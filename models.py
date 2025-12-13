import os
from datetime import datetime
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy.orm import DeclarativeBase
from werkzeug.security import generate_password_hash, check_password_hash


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
    plantillas = db.relationship('Plantilla', backref='tenant', lazy='dynamic')
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


class Plantilla(db.Model):
    __tablename__ = 'plantillas'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    key = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    contenido = db.Column(db.Text, nullable=True, default='')
    archivo_original = db.Column(db.String(255))
    carpeta_estilos = db.Column(db.String(100))
    activa = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'key', name='uq_plantilla_tenant_key'),
    )


class Estilo(db.Model):
    __tablename__ = 'estilos'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    plantilla_key = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    contenido = db.Column(db.Text, nullable=True, default='')
    archivo_original = db.Column(db.String(255))
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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
