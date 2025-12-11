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
    
    def can_access_tenant(self, tenant_id):
        if self.is_super_admin():
            return True
        return self.tenant_id == tenant_id
    
    def can_manage_tenant(self, tenant_id):
        if self.is_super_admin():
            return True
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
    contenido = db.Column(db.Text, nullable=False)
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
    contenido = db.Column(db.Text, nullable=False)
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
