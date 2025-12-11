"""
Script de migración para convertir la app a multi-tenant.
Ejecutar una sola vez para migrar datos existentes.
"""
import os
import sys
from datetime import datetime

def run_migration():
    from app import app, db
    from models import Tenant, User, DocumentRecord, Plantilla, Estilo, CampoPlantilla
    
    with app.app_context():
        print("=== Iniciando migración a multi-tenant ===\n")
        
        tenant_default = Tenant.query.filter_by(slug='estudio-default').first()
        
        if not tenant_default:
            print("1. Creando tenant por defecto...")
            tenant_default = Tenant(
                nombre='Abogadas Perú',
                slug='estudio-default',
                resolucion_directoral='Autorizado su funcionamiento por Resolución Directoral N.º 3562-2022-JUS/DGDPAJ-DCMA',
                direccion='Av. Javier Prado Este 255, oficina 701. Distrito de San Isidro, Lima-Perú',
                telefono='(01) – 6757575 / 994647890',
                pagina_web='www.abogadasperu.com',
                pais='Perú',
                ciudad='Lima',
                areas_practica='Derecho de Familia, Alimentos, Conciliación',
                activo=True
            )
            db.session.add(tenant_default)
            db.session.commit()
            print(f"   Tenant creado con ID: {tenant_default.id}")
        else:
            print(f"1. Tenant por defecto ya existe (ID: {tenant_default.id})")
        
        print("\n2. Migrando usuarios...")
        users_sin_tenant = User.query.filter(User.tenant_id == None).all()
        primer_usuario = User.query.order_by(User.id).first()
        
        for user in users_sin_tenant:
            user.tenant_id = tenant_default.id
            if user.id == primer_usuario.id if primer_usuario else False:
                user.role = 'super_admin'
                print(f"   Usuario '{user.email}' -> super_admin")
            elif hasattr(user, 'is_admin') and user.is_admin:
                user.role = 'admin_estudio'
                print(f"   Usuario '{user.email}' -> admin_estudio")
            else:
                user.role = 'usuario_estudio'
                print(f"   Usuario '{user.email}' -> usuario_estudio")
        
        print("\n3. Migrando documentos...")
        docs_sin_tenant = DocumentRecord.query.filter(DocumentRecord.tenant_id == None).all()
        for doc in docs_sin_tenant:
            doc.tenant_id = tenant_default.id
        print(f"   {len(docs_sin_tenant)} documentos migrados")
        
        print("\n4. Migrando plantillas...")
        plantillas_sin_tenant = Plantilla.query.filter(Plantilla.tenant_id == None).all()
        for p in plantillas_sin_tenant:
            p.tenant_id = tenant_default.id
        print(f"   {len(plantillas_sin_tenant)} plantillas migradas")
        
        print("\n5. Migrando estilos...")
        estilos_sin_tenant = Estilo.query.filter(Estilo.tenant_id == None).all()
        for e in estilos_sin_tenant:
            e.tenant_id = tenant_default.id
        print(f"   {len(estilos_sin_tenant)} estilos migrados")
        
        print("\n6. Migrando campos de plantilla...")
        campos_sin_tenant = CampoPlantilla.query.filter(CampoPlantilla.tenant_id == None).all()
        for c in campos_sin_tenant:
            c.tenant_id = tenant_default.id
        print(f"   {len(campos_sin_tenant)} campos migrados")
        
        db.session.commit()
        
        print("\n=== Migración completada exitosamente ===")
        print(f"\nResumen:")
        print(f"  - Tenant ID: {tenant_default.id}")
        print(f"  - Usuarios: {User.query.filter_by(tenant_id=tenant_default.id).count()}")
        print(f"  - Documentos: {DocumentRecord.query.filter_by(tenant_id=tenant_default.id).count()}")
        print(f"  - Plantillas: {Plantilla.query.filter_by(tenant_id=tenant_default.id).count()}")
        print(f"  - Estilos: {Estilo.query.filter_by(tenant_id=tenant_default.id).count()}")
        print(f"  - Campos: {CampoPlantilla.query.filter_by(tenant_id=tenant_default.id).count()}")


if __name__ == '__main__':
    run_migration()
