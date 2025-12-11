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


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    documents = db.relationship('DocumentRecord', backref='user', lazy='dynamic')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class DocumentRecord(db.Model):
    __tablename__ = 'document_records'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
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
    key = db.Column(db.String(50), unique=True, nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    contenido = db.Column(db.Text, nullable=False)
    carpeta_estilos = db.Column(db.String(100))
    activa = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Estilo(db.Model):
    __tablename__ = 'estilos'
    
    id = db.Column(db.Integer, primary_key=True)
    plantilla_key = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    contenido = db.Column(db.Text, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CampoPlantilla(db.Model):
    __tablename__ = 'campos_plantilla'
    
    id = db.Column(db.Integer, primary_key=True)
    plantilla_key = db.Column(db.String(50), nullable=False)
    nombre_campo = db.Column(db.String(100), nullable=False)
    etiqueta = db.Column(db.String(200), nullable=False)
    tipo = db.Column(db.String(50), default='text')
    requerido = db.Column(db.Boolean, default=False)
    orden = db.Column(db.Integer, default=0)
    placeholder = db.Column(db.String(200))
    opciones = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
