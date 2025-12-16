import os
import secrets
import hashlib
from datetime import datetime
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy.orm import DeclarativeBase
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
import pyotp
import base64


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class Tenant(db.Model):
    """Estudio jurídico / Organización."""
    __tablename__ = 'tenants'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    logo_path = db.Column(db.String(255))
    color_primario = db.Column(db.String(7), default='#3B82F6')
    color_secundario = db.Column(db.String(7), default='#10B981')
    resolucion_directoral = db.Column(db.String(255))
    direccion = db.Column(db.String(300))
    telefono = db.Column(db.String(100))
    pagina_web = db.Column(db.String(200))
    pais = db.Column(db.String(100), default='Perú')
    ciudad = db.Column(db.String(100))
    areas_practica = db.Column(db.Text)
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    users = db.relationship('User', backref='tenant', lazy='dynamic')
    modelos = db.relationship('Modelo', backref='tenant', lazy='dynamic')
    estilos = db.relationship('Estilo', backref='tenant', lazy='dynamic')
    campos = db.relationship('CampoPlantilla', backref='tenant', lazy='dynamic')
    documents = db.relationship('DocumentRecord', backref='tenant', lazy='dynamic')
    
    def get_logo_url(self):
        if self.logo_path:
            return f"/static/tenants/{self.slug}/{self.logo_path}"
        return None
    
    def get_header_info(self):
        lines = []
        if self.resolucion_directoral:
            lines.append(self.resolucion_directoral)
        if self.direccion:
            lines.append(f"Dirección: {self.direccion}")
        if self.telefono:
            lines.append(f"Teléfono: {self.telefono}")
        if self.pagina_web:
            lines.append(f"Página web: {self.pagina_web}")
        return lines


_cached_encryption_key = None

def get_encryption_key():
    """Get encryption key for 2FA secrets. Key must be set in environment."""
    global _cached_encryption_key
    if _cached_encryption_key is not None:
        return _cached_encryption_key
    
    key = os.environ.get('TWOFA_ENCRYPTION_KEY')
    if not key:
        raise ValueError("TWOFA_ENCRYPTION_KEY environment variable must be set for 2FA functionality")
    
    if isinstance(key, str):
        key = key.encode()
    
    _cached_encryption_key = key
    return key


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='usuario_estudio')
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    tema_preferido = db.Column(db.String(10), default='claro')
    densidad_visual = db.Column(db.String(10), default='normal')
    
    twofa_enabled = db.Column(db.Boolean, default=False)
    twofa_secret_encrypted = db.Column(db.String(500))
    twofa_backup_codes_hashed = db.Column(db.JSON)
    twofa_last_verified_at = db.Column(db.DateTime)
    twofa_required = db.Column(db.Boolean, default=False)
    
    documents = db.relationship('DocumentRecord', backref='user', lazy='dynamic')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def is_super_admin(self):
        return self.role == 'super_admin'
    
    def is_admin_estudio(self):
        return self.role == 'admin_estudio'
    
    def is_usuario_estudio(self):
        return self.role == 'usuario_estudio'
    
    def is_coordinador(self):
        return self.role == 'coordinador'
    
    def can_manage_cases(self):
        return self.role in ['super_admin', 'admin_estudio', 'coordinador']
    
    def can_access_tenant(self, tenant_id, current_tenant_id=None):
        if self.is_super_admin():
            return current_tenant_id is not None and current_tenant_id == tenant_id
        return self.tenant_id == tenant_id
    
    def can_manage_tenant(self, tenant_id, current_tenant_id=None):
        if self.is_super_admin():
            return current_tenant_id is not None and current_tenant_id == tenant_id
        return self.tenant_id == tenant_id and self.is_admin_estudio()
    
    @property
    def is_admin(self):
        return self.role in ['super_admin', 'admin_estudio']
    
    def requires_2fa(self):
        """Check if user role requires 2FA."""
        return self.role in ['super_admin', 'admin_estudio']
    
    def generate_totp_secret(self):
        """Generate and encrypt a new TOTP secret."""
        secret = pyotp.random_base32()
        fernet = Fernet(get_encryption_key())
        self.twofa_secret_encrypted = fernet.encrypt(secret.encode()).decode()
        return secret
    
    def get_totp_secret(self):
        """Decrypt and return the TOTP secret."""
        if not self.twofa_secret_encrypted:
            return None
        try:
            fernet = Fernet(get_encryption_key())
            return fernet.decrypt(self.twofa_secret_encrypted.encode()).decode()
        except Exception:
            return None
    
    def get_totp_uri(self, issuer="Plataforma Legal"):
        """Generate provisioning URI for QR code."""
        secret = self.get_totp_secret()
        if not secret:
            return None
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=self.email, issuer_name=issuer)
    
    def verify_totp(self, code):
        """Verify a TOTP code."""
        secret = self.get_totp_secret()
        if not secret:
            return False
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)
    
    def generate_backup_codes(self, count=8):
        """Generate backup codes and store hashed versions."""
        codes = []
        hashed_codes = []
        for _ in range(count):
            code = f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
            codes.append(code)
            hashed = hashlib.sha256(code.encode()).hexdigest()
            hashed_codes.append(hashed)
        self.twofa_backup_codes_hashed = hashed_codes
        return codes
    
    def verify_backup_code(self, code):
        """Verify and consume a backup code."""
        if not self.twofa_backup_codes_hashed:
            return False
        code_hash = hashlib.sha256(code.strip().upper().encode()).hexdigest()
        if code_hash in self.twofa_backup_codes_hashed:
            self.twofa_backup_codes_hashed.remove(code_hash)
            return True
        return False
    
    def disable_2fa(self):
        """Disable 2FA for this user."""
        self.twofa_enabled = False
        self.twofa_secret_encrypted = None
        self.twofa_backup_codes_hashed = None
        self.twofa_last_verified_at = None


class DocumentRecord(db.Model):
    __tablename__ = 'document_records'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    tipo_documento = db.Column(db.String(100), nullable=False)
    tipo_documento_key = db.Column(db.String(50), nullable=False)
    demandante = db.Column(db.String(200), nullable=False)
    archivo = db.Column(db.String(255), nullable=False)
    texto_generado = db.Column(db.Text)
    datos_caso = db.Column(db.JSON)
    
    def to_dict(self):
        return {
            'id': self.id,
            'fecha': self.fecha.strftime("%Y-%m-%d %H:%M:%S"),
            'tipo_documento': self.tipo_documento,
            'tipo_documento_key': self.tipo_documento_key,
            'demandante': self.demandante,
            'archivo': self.archivo
        }


class Modelo(db.Model):
    """Modelo de documento legal (antes Plantilla)."""
    __tablename__ = 'plantillas'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    key = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    contenido = db.Column(db.Text, nullable=True, default='')
    archivo_original = db.Column(db.String(255))
    archivo_convertido = db.Column(db.String(255))
    carpeta_estilos = db.Column(db.String(100))
    activa = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    created_by = db.relationship('User', backref=db.backref('modelos', lazy='dynamic'))
    
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'key', name='uq_plantilla_tenant_key'),
    )
    
    def is_owner(self, user):
        """Check if user is the owner of this modelo."""
        return self.created_by_id == user.id
    
    def can_access(self, user):
        """Check if user can access this modelo."""
        if user.is_admin:
            return True
        return self.created_by_id == user.id

Plantilla = Modelo


class Estilo(db.Model):
    """Estilo de redaccion para documentos legales."""
    __tablename__ = 'estilos'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    plantilla_key = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    contenido = db.Column(db.Text, nullable=True, default='')
    archivo_original = db.Column(db.String(255))
    activo = db.Column(db.Boolean, default=True)
    es_predeterminado = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    created_by = db.relationship('User', backref=db.backref('estilos', lazy='dynamic'))
    
    def is_owner(self, user):
        """Check if user is the owner of this estilo."""
        return self.created_by_id == user.id
    
    def can_access(self, user):
        """Check if user can access this estilo."""
        if user.is_admin:
            return True
        return self.created_by_id == user.id


class CampoPlantilla(db.Model):
    __tablename__ = 'campos_plantilla'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    plantilla_key = db.Column(db.String(50), nullable=False)
    nombre_campo = db.Column(db.String(100), nullable=False)
    etiqueta = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(50), default='text')
    requerido = db.Column(db.Boolean, default=False)
    orden = db.Column(db.Integer, default=0)
    placeholder = db.Column(db.String(200))
    opciones = db.Column(db.Text)
    archivo_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ImagenModelo(db.Model):
    """Imagen asociada a un modelo de documento."""
    __tablename__ = 'imagenes_modelos'
    
    id = db.Column(db.Integer, primary_key=True)
    modelo_id = db.Column(db.Integer, db.ForeignKey('plantillas.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    nombre = db.Column(db.String(200), nullable=False)
    archivo = db.Column(db.String(255), nullable=False)
    posicion = db.Column(db.String(50), default='inline')
    descripcion = db.Column(db.String(500))
    ancho_cm = db.Column(db.Float, default=5.0)
    orden = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    modelo = db.relationship('Modelo', backref=db.backref('imagenes', lazy='dynamic', cascade='all, delete-orphan'))


class ModeloTabla(db.Model):
    """Tabla/cuadro asociado a un modelo de documento (ej: cuadro de gastos)."""
    __tablename__ = 'modelo_tablas'
    
    id = db.Column(db.Integer, primary_key=True)
    modelo_id = db.Column(db.Integer, db.ForeignKey('plantillas.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    nombre = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.String(500))
    columnas = db.Column(db.JSON, nullable=False)
    num_filas = db.Column(db.Integer, default=5)
    mostrar_total = db.Column(db.Boolean, default=False)
    columna_total = db.Column(db.String(100))
    orden = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    modelo = db.relationship('Modelo', backref=db.backref('tablas', lazy='dynamic', cascade='all, delete-orphan'))
    
    def get_campo_key(self, fila_idx, columna_idx):
        """Genera clave única para un campo de la tabla."""
        col_name = self.columnas[columna_idx] if columna_idx < len(self.columnas) else f"col{columna_idx}"
        safe_name = col_name.lower().replace(' ', '_').replace('.', '')
        tabla_name = self.nombre.lower().replace(' ', '_')
        return f"tabla_{tabla_name}_{fila_idx}_{safe_name}"
    
    def get_all_campos(self):
        """Retorna todos los campos de la tabla como lista de dicts."""
        campos = []
        for fila in range(self.num_filas):
            for col_idx, col_name in enumerate(self.columnas):
                campos.append({
                    'key': self.get_campo_key(fila, col_idx),
                    'fila': fila,
                    'columna': col_idx,
                    'columna_nombre': col_name,
                    'etiqueta': f"{col_name} (Fila {fila + 1})"
                })
        return campos


class Case(db.Model):
    """Caso legal - expediente judicial o extrajudicial."""
    __tablename__ = 'cases'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    numero_expediente = db.Column(db.String(100))
    titulo = db.Column(db.String(300), nullable=False)
    descripcion = db.Column(db.Text)
    cliente_nombre = db.Column(db.String(200), nullable=False)
    cliente_email = db.Column(db.String(120))
    cliente_telefono = db.Column(db.String(50))
    contraparte_nombre = db.Column(db.String(200))
    tipo_caso = db.Column(db.String(100))
    juzgado = db.Column(db.String(200))
    estado = db.Column(db.String(50), default='por_comenzar')
    prioridad = db.Column(db.String(20), default='media')
    fecha_inicio = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_limite = db.Column(db.DateTime)
    fecha_cierre = db.Column(db.DateTime)
    notas = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    tenant = db.relationship('Tenant', backref=db.backref('cases', lazy='dynamic'))
    created_by = db.relationship('User', backref=db.backref('created_cases', lazy='dynamic'))
    assignments = db.relationship('CaseAssignment', backref='case', lazy='dynamic', cascade='all, delete-orphan')
    documents = db.relationship('CaseDocument', backref='case', lazy='dynamic', cascade='all, delete-orphan')
    tasks = db.relationship('Task', backref='case', lazy='dynamic', cascade='all, delete-orphan')
    
    ESTADOS = {
        'por_comenzar': 'Por Comenzar',
        'en_proceso': 'En Proceso',
        'en_espera': 'En Espera',
        'terminado': 'Terminado',
        'archivado': 'Archivado'
    }
    
    PRIORIDADES = {
        'baja': 'Baja',
        'media': 'Media',
        'alta': 'Alta',
        'urgente': 'Urgente'
    }
    
    def get_estado_display(self):
        return self.ESTADOS.get(self.estado, self.estado)
    
    def get_prioridad_display(self):
        return self.PRIORIDADES.get(self.prioridad, self.prioridad)
    
    def get_assigned_users(self):
        return [a.user for a in self.assignments.all()]
    
    def to_dict(self):
        return {
            'id': self.id,
            'numero_expediente': self.numero_expediente,
            'titulo': self.titulo,
            'cliente_nombre': self.cliente_nombre,
            'estado': self.estado,
            'estado_display': self.get_estado_display(),
            'prioridad': self.prioridad,
            'prioridad_display': self.get_prioridad_display(),
            'fecha_inicio': self.fecha_inicio.strftime('%Y-%m-%d') if self.fecha_inicio else None,
            'fecha_limite': self.fecha_limite.strftime('%Y-%m-%d') if self.fecha_limite else None
        }


class CaseAssignment(db.Model):
    """Asignación de usuarios a casos con roles específicos."""
    __tablename__ = 'case_assignments'
    
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    rol_en_caso = db.Column(db.String(50), default='abogado')
    es_responsable = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('case_assignments', lazy='dynamic'))
    
    ROLES_CASO = {
        'abogado': 'Abogado',
        'coordinador': 'Coordinador',
        'asistente': 'Asistente',
        'consultor': 'Consultor'
    }
    
    __table_args__ = (
        db.UniqueConstraint('case_id', 'user_id', name='uq_case_user'),
    )
    
    def get_rol_display(self):
        return self.ROLES_CASO.get(self.rol_en_caso, self.rol_en_caso)


class CaseDocument(db.Model):
    """Vincula documentos a casos con información de versión."""
    __tablename__ = 'case_documents'
    
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('document_records.id'), nullable=False)
    version = db.Column(db.Integer, default=1)
    descripcion = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    added_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    document = db.relationship('DocumentRecord', backref=db.backref('case_links', lazy='dynamic'))
    added_by = db.relationship('User', backref=db.backref('added_case_documents', lazy='dynamic'))


class Task(db.Model):
    """Tareas vinculadas a casos para la bandeja de trabajo."""
    __tablename__ = 'tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'), nullable=True)
    titulo = db.Column(db.String(300), nullable=False)
    descripcion = db.Column(db.Text)
    tipo = db.Column(db.String(50), default='general')
    estado = db.Column(db.String(50), default='pendiente')
    prioridad = db.Column(db.String(20), default='media')
    fecha_vencimiento = db.Column(db.DateTime)
    fecha_completada = db.Column(db.DateTime)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    archivo = db.Column(db.String(500), nullable=True)
    archivo_nombre = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tenant = db.relationship('Tenant', backref=db.backref('tasks', lazy='dynamic'))
    assigned_to = db.relationship('User', foreign_keys=[assigned_to_id], backref=db.backref('assigned_tasks', lazy='dynamic'))
    created_by = db.relationship('User', foreign_keys=[created_by_id], backref=db.backref('created_tasks', lazy='dynamic'))
    
    ESTADOS = {
        'pendiente': 'Pendiente',
        'en_curso': 'En Curso',
        'bloqueado': 'Bloqueado',
        'completado': 'Completado',
        'cancelado': 'Cancelado'
    }
    
    TIPOS = {
        'general': 'General',
        'documento': 'Documento',
        'audiencia': 'Audiencia',
        'revision': 'Revisión',
        'notificacion': 'Notificación'
    }
    
    PRIORIDADES = {
        'baja': 'Baja',
        'media': 'Media',
        'alta': 'Alta',
        'urgente': 'Urgente'
    }
    
    def get_estado_display(self):
        return self.ESTADOS.get(self.estado, self.estado)
    
    def get_tipo_display(self):
        return self.TIPOS.get(self.tipo, self.tipo)
    
    def get_prioridad_display(self):
        return self.PRIORIDADES.get(self.prioridad, self.prioridad)
    
    def is_overdue(self):
        if self.fecha_vencimiento and self.estado not in ['completado', 'cancelado']:
            return datetime.utcnow() > self.fecha_vencimiento
        return False
    
    def to_dict(self):
        return {
            'id': self.id,
            'titulo': self.titulo,
            'estado': self.estado,
            'estado_display': self.get_estado_display(),
            'tipo': self.tipo,
            'tipo_display': self.get_tipo_display(),
            'prioridad': self.prioridad,
            'prioridad_display': self.get_prioridad_display(),
            'fecha_vencimiento': self.fecha_vencimiento.strftime('%Y-%m-%d') if self.fecha_vencimiento else None,
            'is_overdue': self.is_overdue()
        }


class CaseAttachment(db.Model):
    """Archivos adjuntos de casos."""
    __tablename__ = 'case_attachments'
    
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'), nullable=False)
    nombre = db.Column(db.String(255), nullable=False)
    archivo = db.Column(db.String(500), nullable=False)
    tipo_archivo = db.Column(db.String(50))
    descripcion = db.Column(db.Text)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    case = db.relationship('Case', backref=db.backref('attachments', lazy='dynamic'))
    uploaded_by = db.relationship('User', backref=db.backref('case_attachments', lazy='dynamic'))
    
    def get_extension(self):
        if self.archivo:
            return os.path.splitext(self.archivo)[1].lower()
        return ''


class FinishedDocument(db.Model):
    """Documentos terminados subidos por usuarios."""
    __tablename__ = 'finished_documents'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'), nullable=True)
    nombre = db.Column(db.String(255), nullable=False)
    archivo = db.Column(db.String(500), nullable=False)
    descripcion = db.Column(db.Text)
    tipo_documento = db.Column(db.String(100))
    numero_expediente = db.Column(db.String(100))
    plazo_entrega = db.Column(db.DateTime)
    sent_importante_notification = db.Column(db.Boolean, default=False)
    sent_urgente_notification = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tenant = db.relationship('Tenant', backref=db.backref('finished_documents', lazy='dynamic'))
    user = db.relationship('User', backref=db.backref('finished_documents', lazy='dynamic'))
    case = db.relationship('Case', backref=db.backref('finished_documents', lazy='dynamic'))
    
    def get_filename(self):
        return os.path.basename(self.archivo) if self.archivo else None
    
    def get_priority_status(self):
        """Returns priority status based on plazo_entrega deadline.
        Returns: 'urgente' (<=24 hours), 'importante' (24-48 hours), 'vencido' (past), or None
        """
        if not self.plazo_entrega:
            return None
        
        now = datetime.utcnow()
        if self.plazo_entrega < now:
            return 'vencido'
        
        delta = self.plazo_entrega - now
        hours_remaining = delta.total_seconds() / 3600
        
        if hours_remaining <= 24:
            return 'urgente'
        elif hours_remaining <= 48:
            return 'importante'
        return None
    
    def get_days_remaining(self):
        """Returns number of days until deadline (rounded up for display)."""
        if not self.plazo_entrega:
            return None
        
        now = datetime.utcnow()
        delta = self.plazo_entrega - now
        hours = delta.total_seconds() / 3600
        if hours < 0:
            return 0
        return max(1, int(hours / 24) + (1 if hours % 24 > 0 else 0))


class ReviewSession(db.Model):
    """Sesión de revisión de documento por IA."""
    __tablename__ = 'review_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    nombre_documento = db.Column(db.String(255), nullable=False)
    archivo_path = db.Column(db.String(500))
    contenido_texto = db.Column(db.Text)
    estado = db.Column(db.String(50), default='pendiente')  # pendiente, procesando, completado, error
    evaluacion_general = db.Column(db.Text)
    total_errores = db.Column(db.Integer, default=0)
    total_advertencias = db.Column(db.Integer, default=0)
    total_sugerencias = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    
    tenant = db.relationship('Tenant', backref=db.backref('reviews', lazy='dynamic'))
    user = db.relationship('User', backref=db.backref('reviews', lazy='dynamic'))
    issues = db.relationship('ReviewIssue', backref='session', lazy='dynamic', cascade='all, delete-orphan')


class ReviewIssue(db.Model):
    """Problema detectado en una revisión de documento."""
    __tablename__ = 'review_issues'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('review_sessions.id'), nullable=False)
    severidad = db.Column(db.String(20), nullable=False)  # error, advertencia, sugerencia
    tipo = db.Column(db.String(100), nullable=False)  # coherencia, contradiccion, estructura, campo_incompleto
    ubicacion = db.Column(db.String(500))
    fragmento = db.Column(db.Text)
    descripcion = db.Column(db.Text, nullable=False)
    recomendacion = db.Column(db.Text)
    orden = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TwoFALog(db.Model):
    """Log de intentos y eventos de 2FA para auditoría y rate limiting."""
    __tablename__ = 'twofa_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)  # verify_attempt, setup, disable, reset, backup_used
    success = db.Column(db.Boolean, default=False)
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(500))
    reset_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('twofa_logs', lazy='dynamic'))
    reset_by = db.relationship('User', foreign_keys=[reset_by_id])
    
    @classmethod
    def count_recent_failures(cls, user_id, minutes=15):
        """Count recent failed 2FA attempts for rate limiting."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        return cls.query.filter(
            cls.user_id == user_id,
            cls.event_type == 'verify_attempt',
            cls.success == False,
            cls.created_at >= cutoff
        ).count()
    
    @classmethod
    def log_event(cls, user_id, event_type, success=True, ip=None, user_agent=None, reset_by_id=None, details=None):
        """Log a 2FA event."""
        log = cls(
            user_id=user_id,
            event_type=event_type,
            success=success,
            ip_address=ip,
            user_agent=user_agent,
            reset_by_id=reset_by_id,
            details=details
        )
        db.session.add(log)
        return log


class EstiloDocumento(db.Model):
    """Configuración de estilo de documentos por tenant."""
    __tablename__ = 'estilos_documento'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False, unique=True)
    fuente = db.Column(db.String(50), default='Times New Roman')
    tamano_base = db.Column(db.Integer, default=12)
    interlineado = db.Column(db.Float, default=1.5)
    margen_superior = db.Column(db.Float, default=2.5)
    margen_inferior = db.Column(db.Float, default=2.5)
    margen_izquierdo = db.Column(db.Float, default=3.0)
    margen_derecho = db.Column(db.Float, default=2.5)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tenant = db.relationship('Tenant', backref=db.backref('estilo_documento', uselist=False))
    
    FUENTES_PERMITIDAS = [
        ('Times New Roman', 'Times New Roman'),
        ('Arial', 'Arial'),
        ('Calibri', 'Calibri')
    ]
    
    @classmethod
    def get_or_create(cls, tenant_id):
        """Obtiene el estilo del tenant o crea uno con valores por defecto."""
        estilo = cls.query.filter_by(tenant_id=tenant_id).first()
        if not estilo:
            estilo = cls(tenant_id=tenant_id)
            db.session.add(estilo)
            db.session.commit()
        return estilo
