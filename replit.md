# Plataforma de Generación de Documentos Jurídicos

## Overview

Sistema web Flask para generar documentos jurídicos utilizando plantillas internas del estudio y OpenAI. El usuario selecciona un tipo de documento, completa un formulario con los datos del caso, y el sistema genera un documento Word (.docx) personalizado.

## Estructura del Proyecto

```
/
├── app.py                    # Aplicación Flask principal
├── main.py                   # Entry point para gunicorn
├── models.py                 # Modelos SQLAlchemy (User, DocumentRecord, Plantilla, Estilo)
├── templates/
│   ├── index.html           # Formulario principal
│   ├── login.html           # Página de inicio de sesión
│   ├── registro.html        # Página de registro
│   ├── historial.html       # Historial de documentos con filtros
│   ├── preview.html         # Preview de documento antes de guardar
│   ├── editar.html          # Edición post-generación
│   ├── admin.html           # Panel de administración
│   ├── admin_plantilla.html # CRUD de plantillas
│   └── admin_estilo.html    # CRUD de estilos
├── modelos_legales/         # Plantillas de documentos (subir .txt)
│   └── (aumento_alimentos.txt, pension_mutuo.txt, etc.)
├── estilos_estudio/         # Ejemplos de estilo por tipo de documento
│   ├── aumento_alimentos/   # Subcarpeta con archivos .txt de estilo
│   └── pension_mutuo/       # Subcarpeta con archivos .txt de estilo
├── Resultados/              # Documentos .docx generados
├── historial.csv            # Registro legacy (migrado a PostgreSQL)
└── design_guidelines.md     # Guías de diseño frontend
```

## Sistema de Usuarios

### Autenticación
- Registro de usuarios con validación de email único
- Login con email y contraseña
- El primer usuario registrado es automáticamente administrador
- Protección de rutas con @login_required

### Roles
- **Usuario**: Puede generar documentos, ver su historial, editar sus documentos
- **Admin**: Todo lo anterior + acceso al panel de administración

## Funcionalidades

### Generación de Documentos
1. Usuario accede a la página principal (/)
2. Selecciona tipo de documento del dropdown
3. Completa el formulario (invitado, demandante, DNI, argumentos, conclusión)
4. Opción 1: "Ver Preview" - genera preview editable antes de guardar
5. Opción 2: "Generar y Descargar" - genera y descarga directamente
6. El documento se guarda en /Resultados y se registra en la base de datos

### Preview y Edición
- Preview muestra el documento generado antes de guardar
- Permite editar el texto antes de crear el archivo final
- Edición post-generación desde el historial

### Historial con Filtros
- Búsqueda por demandante o tipo de documento
- Filtro por tipo de documento
- Filtro por rango de fechas
- Cada usuario solo ve sus propios documentos

### Panel de Administración
- Gestión de plantillas en base de datos
- Gestión de estilos de redacción
- Vista de usuarios registrados
- Estadísticas de documentos generados

## Base de Datos PostgreSQL

### Tablas
- **users**: Usuarios del sistema (id, username, email, password_hash, is_admin, created_at)
- **document_records**: Historial de documentos (id, user_id, fecha, tipo_documento, demandante, archivo, texto_generado, datos_caso)
- **plantillas**: Plantillas adicionales (id, key, nombre, contenido, carpeta_estilos, activa)
- **estilos**: Estilos de redacción (id, plantilla_key, nombre, contenido, activo)
- **campo_plantilla**: Campos dinámicos por plantilla (id, plantilla_key, nombre_campo, etiqueta, tipo, requerido, orden, placeholder, opciones)

## Diccionario MODELOS

El sistema usa un diccionario para configurar tipos de documentos base:

```python
MODELOS = {
    "aumento_alimentos": {
        "nombre": "Aumento de alimentos",
        "plantilla": "aumento_alimentos.txt",
        "carpeta_estilos": "aumento_alimentos"
    },
    "pension_mutuo": {
        "nombre": "Pensión de alimentos – mutuo acuerdo",
        "plantilla": "pension_mutuo.txt",
        "carpeta_estilos": "pension_mutuo"
    }
}
```

Las plantillas en base de datos tienen prioridad sobre las de archivos.

## Variables de Entorno Requeridas

- `OPENAI_API_KEY`: API key de OpenAI para generación de documentos
- `SESSION_SECRET`: Clave secreta para sesiones Flask
- `DATABASE_URL`: URL de conexión a PostgreSQL

## Tecnologías

- **Backend**: Flask, Python 3.11, Flask-Login, Flask-SQLAlchemy
- **Base de datos**: PostgreSQL
- **Frontend**: HTML5, Tailwind CSS, Roboto font
- **Generación de documentos**: python-docx
- **IA**: OpenAI API (modelo gpt-4o)
- **Servidor**: Gunicorn

## Rutas

### Públicas
- `/` - Formulario principal (requiere login para generar)
- `/login` - Inicio de sesión
- `/registro` - Registro de usuarios
- `/descargar/<nombre_archivo>` - Descargar documento

### Protegidas (requieren login)
- `/procesar_ia` - POST para generar documento
- `/preview` - POST para ver preview
- `/guardar_desde_preview` - POST para guardar desde preview
- `/historial` - Ver historial con filtros
- `/editar/<doc_id>` - Editar documento existente
- `/logout` - Cerrar sesión

### Admin (requieren login + is_admin)
- `/admin` - Panel de administración
- `/admin/plantilla` - Crear/editar plantilla
- `/admin/plantilla/eliminar/<id>` - Eliminar plantilla
- `/admin/estilo` - Crear/editar estilo
- `/admin/estilo/eliminar/<id>` - Eliminar estilo
- `/admin/campos/<plantilla_key>` - Gestionar campos dinámicos de una plantilla
- `/admin/campo` - Crear/editar campo dinámico
- `/admin/campo/eliminar/<id>` - Eliminar campo dinámico

### API (requieren login)
- `/api/campos/<plantilla_key>` - GET JSON con campos dinámicos de una plantilla

## Notas para el Administrador

- **Plantillas**: Se pueden subir archivos .txt a /modelos_legales O crear desde el panel admin
- **Estilos**: Se pueden subir archivos .txt a subcarpetas en /estilos_estudio O crear desde el panel admin
- Las plantillas/estilos en base de datos tienen prioridad sobre archivos
- Los campos vacíos se reemplazan con `{{FALTA_DATO}}` en el documento generado
- El primer usuario registrado se convierte automáticamente en administrador

## Sistema de Campos Dinámicos

Las plantillas pueden tener campos personalizados definidos desde el panel de administración:

### Tipos de campo soportados
- **text**: Campo de texto simple
- **textarea**: Área de texto multilínea
- **date**: Selector de fecha
- **number**: Campo numérico
- **email**: Campo de email con validación
- **select**: Menú desplegable (opciones separadas por coma)

### Flujo
1. Admin crea plantilla en `/admin/plantilla`
2. Admin define campos personalizados en `/admin/campos/<plantilla_key>`
3. Usuario selecciona plantilla en formulario principal
4. El formulario carga dinámicamente los campos definidos vía JavaScript
5. Los datos se envían a OpenAI con etiquetas descriptivas para cada campo
