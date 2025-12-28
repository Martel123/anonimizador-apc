#!/usr/bin/env python3
"""
Script para crear o actualizar el super_admin del sistema.
Este script debe ejecutarse manualmente y nunca exponerse en la UI.
"""

import os
import sys

os.environ.setdefault('DATABASE_URL', os.environ.get('DATABASE_URL', ''))

from app import app, db
from models import User

SUPER_ADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD")
SUPER_ADMIN_USERNAME = os.environ.get("SUPERADMIN_USERNAME", "Super Admin")

if not SUPER_ADMIN_EMAIL or not SUPER_ADMIN_PASSWORD:
    raise RuntimeError("Faltan variables de entorno para crear el super admin")


def seed_super_admin():
    with app.app_context():
        existing = User.query.filter_by(email=SUPER_ADMIN_EMAIL).first()
        
        if existing:
            existing.set_password(SUPER_ADMIN_PASSWORD)
            existing.role = 'super_admin'
            existing.activo = True
            db.session.commit()
            print(f"Super admin actualizado: {SUPER_ADMIN_EMAIL}")
        else:
            user = User(
                username=SUPER_ADMIN_USERNAME,
                email=SUPER_ADMIN_EMAIL,
                role='super_admin',
                activo=True,
                tenant_id=None
            )
            user.set_password(SUPER_ADMIN_PASSWORD)
            db.session.add(user)
            db.session.commit()
            print(f"Super admin creado: {SUPER_ADMIN_EMAIL}")

if __name__ == "__main__":
    seed_super_admin()
