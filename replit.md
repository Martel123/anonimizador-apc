# Plataforma de Generación de Documentos Jurídicos - Multi-Tenant SaaS

## Overview

Sistema web Flask multi-tenant (SaaS) para generar documentos jurídicos. Múltiples estudios jurídicos pueden registrarse, cada uno con datos aislados (plantillas, usuarios, documentos, estilos). Cada tenant tiene branding personalizable (logo, información de contacto) y el sistema soporta tres roles de usuario.

## Arquitectura Multi-Tenant

### Modelo de Aislamiento
- Cada estudio jurídico (tenant) tiene datos completamente aislados
- Todas las tablas principales incluyen `tenant_id` para filtrado automático
- Los documentos se almacenan en carpetas separadas por tenant

### Roles de Usuario
- **super_admin**: Propietario de la plataforma, acceso a todos los estudios, puede impersonar tenants
- **admin_estudio**: Administrador del estudio, gestiona plantillas/estilos/usuarios de su estudio
- **usuario_estudio**: Abogado/colaborador, puede generar documentos y ver su historial

## Estructura del Proyecto

```
/
├── app.py                    # Aplicación Flask principal con rutas multi-tenant
├── main.py                   # Entry point para gunicorn
├── models.py                 # Modelos SQLAlchemy (Tenant, User, DocumentRecord, Plantilla, Estilo, CampoPlantilla)
├── migrate_to_multitenant.py # Script de migración a multi-tenant
├── templates/
│   ├── index.html           # Formulario principal (tenant-aware)
│   ├── login.html           # Página de inicio de sesión
│   ├── registro.html        # Página de selección de registro
│   ├── registro_estudio.html # Registro de nuevo estudio jurídico
│   ├── historial.html       # Historial de documentos con filtros (por tenant)
│   ├── preview.html         # Preview de documento antes de guardar
│   ├── editar.html          # Edición post-generación
│   ├── admin.html           # Panel de administración del estudio
│   ├── admin_usuarios.html  # Gestión de usuarios del estudio
│   ├── configurar_estudio.html # Configuración del estudio (logo, datos)
│   ├── super_admin.html     # Panel de super administrador
│   ├── admin_plantilla.html # CRUD de plantillas
│   └── admin_estilo.html    # CRUD de estilos
├── modelos_legales/         # Plantillas de documentos predeterminadas
├── estilos_estudio/         # Ejemplos de estilo predeterminados
├── plantillas_subidas/      # Plantillas Word subidas por tenants
│   └── tenant_<id>/         # Carpeta específica por estudio
├── estilos_subidos/         # Estilos Word subidos por tenants
│   └── tenant_<id>/         # Carpeta específica por estudio
├── Resultados/              # Documentos .docx generados (subcarpetas por tenant)
│   └── tenant_<id>/         # Carpeta específica por estudio
├── logos/                   # Logos de estudios
└── design_guidelines.md     # Guías de diseño frontend
```

## Base de Datos PostgreSQL

### Tablas

#### tenants (Estudios Jurídicos)
- `id`: ID único
- `nombre`: Nombre del estudio
- `slug`: Identificador URL único
- `logo_path`: Ruta al logo
- `color_primario`: Color primario del estudio (hex, ej: #3B82F6)
- `color_secundario`: Color secundario del estudio (hex, ej: #10B981)
- `resolucion_directoral`: Número de autorización
- `direccion`: Dirección física
- `telefono`: Número de teléfono
- `pagina_web`: Sitio web
- `pais`, `ciudad`: Ubicación
- `areas_practica`: Áreas de especialización
- `activo`: Estado del tenant
- `created_at`, `updated_at`: Timestamps

#### users (Usuarios)
- `id`, `username`, `email`, `password_hash`
- `role`: 'super_admin', 'admin_estudio', 'usuario_estudio'
- `tenant_id`: FK a tenants (null para super_admin)
- `activo`: Estado del usuario
- `tema_preferido`: Tema visual ('claro' o 'oscuro')
- `densidad_visual`: Densidad de la interfaz ('normal' o 'compacta')
- `twofa_enabled`, `twofa_secret_encrypted`: Campos para 2FA
- `created_at`, `last_login`

#### document_records (Documentos)
- `id`, `user_id`, `tenant_id`
- `fecha`, `tipo_documento`, `demandante`
- `archivo`, `texto_generado`, `datos_caso`

#### plantillas
- `id`, `tenant_id`, `key`, `nombre`
- `contenido`: Texto extraído del documento Word
- `archivo_original`: Ruta al archivo Word subido
- `carpeta_estilos`, `activa`, timestamps

#### estilos
- `id`, `tenant_id`, `plantilla_key`, `nombre`
- `contenido`: Texto extraído del documento Word de ejemplo
- `archivo_original`: Ruta al archivo Word subido
- `activo`, timestamps

#### campos_plantilla
- Campos dinámicos detectados automáticamente o agregados manualmente
- `tenant_id` para aislamiento

#### estilos_documento
- `id`, `tenant_id`: FK a tenants
- `fuente`: Fuente para documentos (Times New Roman, Arial, Calibri)
- `tamano_base`: Tamaño de fuente en puntos (10, 11, 12, 14)
- `interlineado`: Espaciado entre líneas (1.0, 1.15, 1.5, 2.0)
- `margen_superior`, `margen_inferior`, `margen_izquierdo`, `margen_derecho`: Márgenes en cm

## Sistema de Roles y Permisos

### Decoradores de Acceso
- `@require_super_admin`: Solo super admins
- `@require_admin`: Super admin o admin_estudio del tenant
- `@require_tenant_user`: Cualquier usuario autenticado del tenant

### Funciones de Tenant
- `get_current_tenant()`: Obtiene el tenant del usuario actual
- `get_tenant_query(model)`: Filtra queries por tenant_id automáticamente

## Rutas

### Públicas
- `/` - Formulario principal
- `/login` - Inicio de sesión
- `/registro` - Selección de tipo de registro
- `/registro_estudio` - Registro de nuevo estudio jurídico

### Protegidas (requieren login)
- `/procesar_ia` - POST para generar documento
- `/preview` - POST para ver preview
- `/historial` - Ver historial (filtrado por tenant)
- `/editar/<doc_id>` - Editar documento
- `/descargar/<nombre_archivo>` - Descargar documento
- `/logout` - Cerrar sesión

### Admin Estudio (requieren admin_estudio o super_admin)
- `/admin` - Panel de administración del estudio
- `/admin/configurar` - Configurar datos del estudio
- `/admin/usuarios` - Gestionar usuarios del estudio
- `/admin/plantilla` - Crear/editar plantilla
- `/admin/estilo` - Crear/editar estilo
- `/admin/campos/<plantilla_key>` - Gestionar campos dinámicos

### Super Admin (solo super_admin)
- `/super-admin` - Panel de super administración
- `/super-admin/impersonate/<tenant_id>` - Ver como otro estudio
- `/super-admin/stop-impersonate` - Salir del modo impersonar

## Flujos Principales

### Registro de Nuevo Estudio
1. Usuario accede a `/registro_estudio`
2. Completa nombre del estudio, email, contraseña
3. Sistema crea nuevo tenant y usuario como `admin_estudio`
4. Usuario puede configurar logo y datos en `/admin/configurar`

### Agregar Usuario al Estudio
1. Admin del estudio accede a `/admin/usuarios`
2. Completa formulario con nombre, email, contraseña, rol
3. Nuevo usuario puede generar documentos del estudio

### Generación de Documento
1. Usuario selecciona plantilla (filtrada por tenant)
2. Completa campos dinámicos
3. Sistema genera documento con encabezado del estudio (logo, datos)
4. Documento se guarda en `/Resultados/tenant_<id>/`

### Impersonar Estudio (Super Admin)
1. Super admin accede a `/super-admin`
2. Hace clic en "Ver como" en un estudio
3. Navega la plataforma como si fuera ese estudio
4. Puede salir con "Salir Vista" en la navegación

## Módulo de Argumentación

### Descripción
Sistema de mejora de documentos legales con IA, procesamiento asíncrono y formato profesional.

### Modelos
- **ArgumentationSession**: Sesión con documento original, versiones mejoradas, contador de interacciones
- **ArgumentationJob**: Jobs de procesamiento con estados (queued/processing/done/failed), secciones objetivo, métricas de tiempo

### Rutas
- `/argumentacion` - Vista principal con listado de sesiones del usuario
- `/argumentacion/nueva` - Iniciar nueva sesión de argumentación
- `/argumentacion/sesion/<id>` - Chat-like interface para mejorar documento
- `/argumentacion/start` (POST) - Crear job de procesamiento asíncrono
- `/argumentacion/jobs/<id>` (GET) - Polling de estado del job
- `/argumentacion/descargar/<id>` - Descarga documento con formato del estudio
- `/argumentacion/historial` - Historial de sesiones del usuario

### Características
- **Procesamiento asíncrono**: Worker de fondo con threading para evitar timeouts
- **Secciones específicas**: Mejorar solo Hechos, Fundamentos o Petitorio
- **Detección de intención**: Distingue preguntas (explicación) de modificaciones (reescritura)
- **Formato profesional**: Documentos descargados incluyen logo, encabezado y estilos del tenant
- **Seguridad multi-tenant**: Filtrado por user_id y tenant_id en todas las consultas

## Generación de Documentos

### Encabezado del Documento
Cada documento incluye encabezado con:
- Logo del estudio (si está configurado)
- Resolución directoral
- Dirección
- Teléfono
- Página web

### Formato
- Archivo .docx con formato profesional
- Fuente Times New Roman, 12pt
- Márgenes estándar legales
- Nomenclatura: `TIPO_DEMANDANTE_FECHA.docx`

## Variables de Entorno

- `OPENAI_API_KEY`: API key de OpenAI
- `SESSION_SECRET`: Clave secreta para sesiones
- `DATABASE_URL`: URL de conexión a PostgreSQL

## Tecnologías

- **Backend**: Flask, Python 3.11, Flask-Login, Flask-SQLAlchemy
- **Base de datos**: PostgreSQL (Neon)
- **Frontend**: HTML5, Tailwind CSS, Roboto font
- **Generación de documentos**: python-docx
- **IA**: OpenAI API (modelo gpt-4o)
- **Servidor**: Gunicorn

## Migración a Multi-Tenant

Para migrar una instalación existente:
```bash
python migrate_to_multitenant.py
```

Esto:
1. Crea un tenant por defecto
2. Migra usuarios existentes al tenant
3. Asigna el primer usuario como `super_admin`
4. Migra documentos, plantillas, estilos y campos

## Sistema de Campos Dinámicos

Las plantillas pueden tener campos personalizados por tenant:

### Tipos de campo
- **text**: Campo de texto simple
- **textarea**: Área de texto multilínea
- **date**: Selector de fecha
- **number**: Campo numérico
- **email**: Campo de email con validación
- **select**: Menú desplegable (opciones separadas por coma)

### Flujo
1. Admin crea plantilla en `/admin/plantilla`
2. Admin define campos en `/admin/campos/<plantilla_key>`
3. Usuario ve campos personalizados en formulario
4. Los datos se envían a OpenAI para generación

## APC IA - Agente Legal Inteligente

### Descripción
APC IA es un agente jurídico que utiliza OpenAI function calling para ejecutar herramientas especializadas. Proporciona una interfaz de chat tipo ChatGPT para abogados.

### Modelos
- **AgentSession**: Sesión de conversación con el agente
- **AgentMessage**: Mensajes de la conversación (role: user/assistant/tool)
- **LegalStrategy**: Estrategias legales generadas por el agente
- **CostEstimate**: Estimaciones de costos generadas

### Herramientas del Agente (7 funciones)
1. `obtener_info_caso`: Obtiene información del caso (partes, materia, juzgado, etc.)
2. `listar_documentos_del_caso`: Lista documentos adjuntos al caso
3. `leer_documento`: Lee el contenido de un documento específico
4. `generar_documento_desde_plantilla`: Genera demandas, contestaciones, escritos
5. `guardar_borrador_estrategia`: Guarda estrategia legal como borrador
6. `crear_tarea`: Crea tareas y recordatorios ligados al caso
7. `calcular_costos_estimados`: Calcula honorarios, tasas y otros gastos

### Rutas
- `/apc-ia` - Vista principal del agente con lista de conversaciones
- `/apc-ia/sesion/<id>` - Chat con el agente en una sesión específica
- `/api/apc/agent` (POST) - Procesa mensajes del usuario
- `/api/apc/sessions` (GET/POST) - Gestión de sesiones
- `/api/apc/sessions/<id>/messages` (GET) - Obtiene mensajes de sesión
- `/api/apc/sessions/<id>` (DELETE) - Elimina sesión

### Características
- **Function calling**: OpenAI GPT-4o con herramientas tipadas
- **Multi-tenant aislamiento**: Todas las consultas filtran por tenant_id y user_id
- **Latencia tracking**: Se registra tiempo de respuesta en cada mensaje
- **Chips de acciones rápidas**: Botones para generar demanda, estrategia, costos, etc.
- **Selector de caso**: Vincula la conversación a un expediente específico
