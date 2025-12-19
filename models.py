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
    subscription_status = db.Column(db.String(20), default='pending')
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
    
    password_set = db.Column(db.Boolean, default=True)
    first_login_completed = db.Column(db.Boolean, default=True)
    onboarding_completed = db.Column(db.Boolean, default=True)
    
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
    fecha_inicio = db.Column(db.DateTime, nullable=True)
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
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=True)
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
    task = db.relationship('Task', backref=db.backref('direct_documents', lazy='dynamic'))
    
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


class PricingConfig(db.Model):
    """Configuración de precios dinámicos (editable por super_admin)."""
    __tablename__ = 'pricing_config'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(300))
    activo = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    
    updated_by = db.relationship('User', foreign_keys=[updated_by_id])
    
    @classmethod
    def get_value(cls, key, default=None):
        """Obtiene un valor de configuración."""
        config = cls.query.filter_by(key=key, activo=True).first()
        return config.value if config else default
    
    @classmethod
    def set_value(cls, key, value, description=None, user_id=None):
        """Establece o actualiza un valor de configuración."""
        config = cls.query.filter_by(key=key).first()
        if config:
            config.value = value
            config.updated_by_id = user_id
            if description:
                config.description = description
        else:
            config = cls(key=key, value=value, description=description, updated_by_id=user_id)
            db.session.add(config)
        db.session.commit()
        return config
    
    @classmethod
    def get_pricing(cls):
        """Obtiene todos los precios como diccionario."""
        configs = cls.query.filter_by(activo=True).all()
        return {c.key: c.value for c in configs}
    
    @classmethod
    def init_defaults(cls):
        """Inicializa valores por defecto si no existen."""
        defaults = [
            ('price_per_seat', '69.00', 'Precio por usuario/mes en USD'),
            ('currency', 'USD', 'Moneda por defecto'),
            ('currency_symbol', '$', 'Símbolo de moneda'),
            ('min_seats', '1', 'Mínimo de asientos'),
            ('max_seats', '100', 'Máximo de asientos'),
            ('trial_days', '14', 'Días de prueba gratis'),
            ('platform_name', 'LegalDoc Pro', 'Nombre de la plataforma'),
        ]
        for key, value, desc in defaults:
            if not cls.query.filter_by(key=key).first():
                db.session.add(cls(key=key, value=value, description=desc))
        db.session.commit()


class PricingAddon(db.Model):
    """Addons/complementos opcionales."""
    __tablename__ = 'pricing_addons'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.Text)
    precio = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), default='USD')
    tipo = db.Column(db.String(20), default='monthly')  # monthly, one_time
    activo = db.Column(db.Boolean, default=True)
    orden = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CheckoutSession(db.Model):
    """Sesión de checkout/compra pendiente."""
    __tablename__ = 'checkout_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), unique=True, nullable=False)
    
    # Datos del estudio
    nombre_estudio = db.Column(db.String(200), nullable=False)
    admin_nombre = db.Column(db.String(100), nullable=False)
    admin_email = db.Column(db.String(120), nullable=False)
    
    # Configuración de compra
    seats = db.Column(db.Integer, nullable=False)
    addons = db.Column(db.JSON)  # Lista de addon IDs
    subtotal = db.Column(db.Numeric(10, 2))
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), default='USD')
    
    # Estado
    status = db.Column(db.String(20), default='pending')
    # pending, pending_payment, paid, expired, cancelled, failed
    
    # Integración Culqi
    culqi_charge_id = db.Column(db.String(100))
    culqi_token_id = db.Column(db.String(100))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    
    # Resultado
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'))
    error_message = db.Column(db.Text)
    
    tenant = db.relationship('Tenant', backref='checkout_session')
    
    def is_expired(self):
        if self.expires_at:
            return datetime.utcnow() > self.expires_at
        return False


class Subscription(db.Model):
    """Suscripción activa de un tenant."""
    __tablename__ = 'subscriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), unique=True, nullable=False)
    
    # Configuración de asientos
    seats_purchased = db.Column(db.Integer, nullable=False)
    seats_used = db.Column(db.Integer, default=1)
    
    # Estado
    status = db.Column(db.String(20), default='active')
    # active, past_due, suspended, cancelled, trial
    
    # Facturación
    plan_type = db.Column(db.String(20), default='monthly')  # monthly, yearly
    price_per_seat = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(3), default='USD')
    
    # Período actual
    current_period_start = db.Column(db.DateTime)
    current_period_end = db.Column(db.DateTime)
    trial_ends_at = db.Column(db.DateTime)
    
    # Integración Culqi
    culqi_customer_id = db.Column(db.String(100))
    culqi_card_id = db.Column(db.String(100))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at = db.Column(db.DateTime)
    
    tenant = db.relationship('Tenant', backref=db.backref('subscription', uselist=False))
    
    def can_add_user(self):
        """Verifica si se puede agregar un usuario más."""
        return self.seats_used < self.seats_purchased
    
    def add_user(self):
        """Incrementa el contador de usuarios usados."""
        if self.can_add_user():
            self.seats_used += 1
            db.session.commit()
            return True
        return False
    
    def remove_user(self):
        """Decrementa el contador de usuarios usados."""
        if self.seats_used > 0:
            self.seats_used -= 1
            db.session.commit()
            return True
        return False
    
    def is_active(self):
        """Verifica si la suscripción está activa."""
        return self.status in ['active', 'trial']


class ActivationToken(db.Model):
    """Tokens para activar cuenta / establecer contraseña."""
    __tablename__ = 'activation_tokens'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    tipo = db.Column(db.String(20), default='set_password')
    # set_password, magic_link, password_reset
    
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='activation_tokens')
    
    def is_valid(self):
        """Verifica si el token es válido."""
        if self.used:
            return False
        if datetime.utcnow() > self.expires_at:
            return False
        return True
    
    def mark_used(self):
        """Marca el token como usado."""
        self.used = True
        self.used_at = datetime.utcnow()
        db.session.commit()
    
    @classmethod
    def create_token(cls, user_id, tipo='set_password', hours=48):
        """Crea un nuevo token de activación."""
        from datetime import timedelta
        token = secrets.token_urlsafe(32)
        activation = cls(
            user_id=user_id,
            token=token,
            tipo=tipo,
            expires_at=datetime.utcnow() + timedelta(hours=hours)
        )
        db.session.add(activation)
        db.session.commit()
        return activation


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


class TaskDocument(db.Model):
    """Tabla puente para vincular documentos terminados a tareas."""
    __tablename__ = 'task_documents'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('finished_documents.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    task = db.relationship('Task', backref=db.backref('task_documents', lazy='dynamic', cascade='all, delete-orphan'))
    document = db.relationship('FinishedDocument', backref=db.backref('task_links', lazy='dynamic'))
    tenant = db.relationship('Tenant', backref=db.backref('task_documents', lazy='dynamic'))
    
    __table_args__ = (
        db.UniqueConstraint('task_id', 'document_id', name='uq_task_document'),
    )


class TaskReminder(db.Model):
    """Registro de recordatorios enviados para evitar duplicados."""
    __tablename__ = 'task_reminders'
    
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    reminder_type = db.Column(db.String(10), nullable=False)  # '3d', '2d', '1d'
    recipient_email = db.Column(db.String(255), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    task = db.relationship('Task', backref=db.backref('reminders', lazy='dynamic', cascade='all, delete-orphan'))
    tenant = db.relationship('Tenant', backref=db.backref('task_reminders', lazy='dynamic'))
    
    REMINDER_TYPES = {
        '3d': 'Faltan 3 días',
        '2d': 'Faltan 2 días',
        '1d': 'Falta 1 día'
    }
    
    @classmethod
    def was_sent(cls, task_id, reminder_type):
        """Verifica si ya se envió este tipo de recordatorio para la tarea."""
        return cls.query.filter_by(task_id=task_id, reminder_type=reminder_type).first() is not None


class CalendarEvent(db.Model):
    """Evento de calendario (reuniones, audiencias, citas)."""
    __tablename__ = 'calendar_events'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text)
    tipo = db.Column(db.String(50), default='reunion')
    ubicacion = db.Column(db.String(300))
    
    fecha_inicio = db.Column(db.DateTime, nullable=False)
    fecha_fin = db.Column(db.DateTime)
    todo_el_dia = db.Column(db.Boolean, default=False)
    link = db.Column(db.String(500))
    
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'))
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    color = db.Column(db.String(7), default='#3b82f6')
    recordatorio_minutos = db.Column(db.Integer, default=30)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tenant = db.relationship('Tenant', backref=db.backref('calendar_events', lazy='dynamic'))
    case = db.relationship('Case', backref=db.backref('calendar_events', lazy='dynamic'))
    created_by = db.relationship('User', backref=db.backref('created_events', lazy='dynamic'))
    attendees = db.relationship('EventAttendee', backref='event', lazy='dynamic', cascade='all, delete-orphan')
    
    TIPOS = {
        'reunion': 'Reunión',
        'audiencia': 'Audiencia',
        'cita': 'Cita con cliente',
        'conferencia': 'Videoconferencia',
        'otro': 'Otro'
    }
    
    def get_tipo_display(self):
        return self.TIPOS.get(self.tipo, self.tipo)
    
    def get_attendees_list(self):
        return [a.user for a in self.attendees.all()]


class EventAttendee(db.Model):
    """Asistentes a un evento de calendario."""
    __tablename__ = 'event_attendees'
    
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('calendar_events.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    estado = db.Column(db.String(20), default='pendiente')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('event_invitations', lazy='dynamic'))
    
    ESTADOS = {
        'pendiente': 'Pendiente',
        'aceptado': 'Aceptado',
        'rechazado': 'Rechazado'
    }
    
    def get_estado_display(self):
        return self.ESTADOS.get(self.estado, self.estado)


class UserArgumentationStyle(db.Model):
    """Estilos de argumentación personalizados por usuario (privados)."""
    __tablename__ = 'user_argumentation_styles'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.Text)
    instrucciones = db.Column(db.Text, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('argumentation_styles', lazy='dynamic'))
    tenant = db.relationship('Tenant', backref=db.backref('user_argumentation_styles', lazy='dynamic'))
    
    ESTILOS_PREDEFINIDOS = [
        {'nombre': 'Formal clásico', 'instrucciones': 'Redacción formal, tradicional, con lenguaje solemne y estructura clásica.'},
        {'nombre': 'Agresivo / Combativo', 'instrucciones': 'Tono firme y directo, enfatizando las violaciones legales y exigiendo reparación contundente.'},
        {'nombre': 'Conciliador', 'instrucciones': 'Tono diplomático, buscando soluciones y evitando confrontación innecesaria.'},
        {'nombre': 'Técnico / Doctrinal', 'instrucciones': 'Cargado de citas doctrinales, jurisprudencia y análisis técnico-jurídico profundo.'},
        {'nombre': 'Pedagógico', 'instrucciones': 'Explicativo y didáctico, ideal para jueces que no son especialistas en la materia.'}
    ]


class ArgumentationSession(db.Model):
    """Sesión de argumentación - contiene documento y conversación."""
    __tablename__ = 'argumentation_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    case_id = db.Column(db.Integer, db.ForeignKey('cases.id'), nullable=True)
    
    titulo = db.Column(db.String(200))
    documento_original = db.Column(db.Text)
    archivo_nombre = db.Column(db.String(255))
    archivo_tipo = db.Column(db.String(50))
    
    ultima_version_mejorada = db.Column(db.Text)
    estilo_usado = db.Column(db.String(100))
    
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('argumentation_sessions', lazy='dynamic'))
    tenant = db.relationship('Tenant', backref=db.backref('argumentation_sessions', lazy='dynamic'))
    case = db.relationship('Case', backref=db.backref('argumentation_sessions', lazy='dynamic'))
    messages = db.relationship('ArgumentationMessage', backref='session', lazy='dynamic', cascade='all, delete-orphan', order_by='ArgumentationMessage.created_at')


class ArgumentationMessage(db.Model):
    """Mensajes dentro de una sesión de argumentación."""
    __tablename__ = 'argumentation_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('argumentation_sessions.id'), nullable=False)
    
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    estilo_aplicado = db.Column(db.String(100))
    message_type = db.Column(db.String(20), default='rewrite')
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    ROLES = {
        'user': 'Usuario',
        'assistant': 'Asistente IA'
    }
    
    MESSAGE_TYPES = {
        'rewrite': 'Documento Mejorado',
        'explanation': 'Respuesta del Asistente'
    }


class ArgumentationJob(db.Model):
    """Job asíncrono para procesamiento de argumentación."""
    __tablename__ = 'argumentation_jobs'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('argumentation_sessions.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    
    section = db.Column(db.String(50), default='full')
    job_type = db.Column(db.String(20), default='rewrite')
    instructions = db.Column(db.Text)
    estilo = db.Column(db.String(100))
    
    status = db.Column(db.String(20), default='queued')
    result_text = db.Column(db.Text)
    error_message = db.Column(db.Text)
    
    extraction_ms = db.Column(db.Integer)
    ia_ms = db.Column(db.Integer)
    docx_render_ms = db.Column(db.Integer)
    total_ms = db.Column(db.Integer)
    
    result_file = db.Column(db.String(500))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    
    session = db.relationship('ArgumentationSession', backref=db.backref('jobs', lazy='dynamic'))
    user = db.relationship('User', backref=db.backref('argumentation_jobs', lazy='dynamic'))
    tenant = db.relationship('Tenant', backref=db.backref('argumentation_jobs', lazy='dynamic'))
    
    STATUSES = {
        'queued': 'En cola',
        'processing': 'Procesando',
        'done': 'Completado',
        'failed': 'Error'
    }
    
    SECTIONS = {
        'full': 'Documento completo',
        'fundamentos': 'Fundamentos de Derecho',
        'petitorio': 'Petitorio',
        'hechos': 'Hechos'
    }
    
    JOB_TYPES = {
        'rewrite': 'Reescritura',
        'explanation': 'Explicación'
    }
    
    def get_status_display(self):
        return self.STATUSES.get(self.status, self.status)
    
    def get_section_display(self):
        return self.SECTIONS.get(self.section, self.section)
    
    def to_dict(self):
        return {
            'id': self.id,
            'session_id': self.session_id,
            'section': self.section,
            'section_display': self.get_section_display(),
            'job_type': self.job_type,
            'status': self.status,
            'status_display': self.get_status_display(),
            'result_text': self.result_text,
            'error_message': self.error_message,
            'ia_ms': self.ia_ms,
            'total_ms': self.total_ms,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None
        }
