# Guía de Despliegue en Render

Este proyecto está preparado para ser desplegado en Render como Web Service.

## Configuración del Proyecto

### Archivo Principal
- **Archivo de entrada:** `main.py`
- El archivo ya está configurado para usar puerto dinámico desde la variable `PORT`

### Build Command (Render)
```
pip install -r requirements_render.txt
```

### Start Command (Render)
```
gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 main:app
```

Alternativa (usando Flask directamente):
```
python main.py
```

## Variables de Entorno Requeridas

Configura estas variables en el panel de Render (Environment):

| Variable | Descripción | Obligatoria |
|----------|-------------|-------------|
| `DATABASE_URL` | URL de conexión PostgreSQL | Sí |
| `SESSION_SECRET` | Clave secreta para sesiones Flask | Sí |
| `OPENAI_API_KEY` | API Key de OpenAI para IA | Sí |
| `RESEND_API_KEY` | API Key de Resend para emails | Sí |
| `PORT` | Puerto (Render lo asigna automáticamente) | Automático |
| `FLASK_DEBUG` | "false" para producción | Opcional |

## Archivo requirements_render.txt

Copia el contenido de `requirements_render.txt` (creado abajo) como `requirements.txt` en tu repositorio de GitHub antes de desplegar.

## Verificaciones de Seguridad

- No hay claves hardcodeadas en el código
- Todas las credenciales se leen desde variables de entorno
- `debug=False` en producción (controlado por `FLASK_DEBUG`)
- Logging configurado para producción (nivel INFO)

## Pasos para Desplegar

1. Sube el proyecto a un repositorio de GitHub
2. En Render, crea un nuevo Web Service
3. Conecta tu repositorio de GitHub
4. Configura:
   - **Build Command:** `pip install -r requirements_render.txt`
   - **Start Command:** `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 main:app`
5. Agrega las variables de entorno requeridas
6. Despliega

## Base de Datos

- El proyecto usa PostgreSQL
- Puedes usar Render PostgreSQL o un servicio externo como Neon
- Asegúrate de que `DATABASE_URL` tenga el formato correcto:
  `postgresql://usuario:password@host:puerto/basededatos`

## Dominio Personalizado

Render permite configurar dominios personalizados desde el panel de control del Web Service. El proyecto ya incluye `ProxyFix` para manejar correctamente los headers de proxy y HTTPS.
