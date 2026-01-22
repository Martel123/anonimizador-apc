# Anonimizador Legal - Despliegue en Render

Herramienta de anonimización automática de documentos legales peruanos. Detecta y reemplaza datos personales (DNI, RUC, emails, teléfonos, direcciones, nombres) usando un sistema de 4 capas de detección.

## Archivos para Render

```
main_render.py           # Aplicación Flask standalone
requirements_render.txt  # Dependencias mínimas
Procfile                 # Comando de inicio
render.yaml             # Configuración de Render
templates/              # Plantillas HTML
  anonymizer_standalone.html
  anonymizer_download_standalone.html
  anonymizer_review_standalone.html
detector_capas.py       # Sistema de detección 4 capas
processor_docx.py       # Procesador de DOCX
processor_pdf.py        # Procesador de PDF
anonymizer.py           # Módulo base de anonimización
anonymizer_robust.py    # Orquestador robusto
```

## Despliegue en Render

### Opción 1: Usando render.yaml (Blueprint)

1. Sube el código a GitHub
2. En Render, crea un nuevo "Blueprint" y conecta tu repositorio
3. Render detectará `render.yaml` y configurará todo automáticamente

### Opción 2: Configuración Manual

1. Crea un nuevo "Web Service" en Render
2. Conecta tu repositorio de GitHub
3. Configura:
   - **Build Command**: `pip install -r requirements_render.txt && python -m spacy download es_core_news_sm`
   - **Start Command**: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 main_render:app`
   - **Environment**: Python 3.11

### Variables de Entorno

| Variable | Descripción | Requerida |
|----------|-------------|-----------|
| `SESSION_SECRET` | Clave secreta para sesiones Flask | Sí (Render puede generarla) |

## Características

- **Sin base de datos**: Solo procesa archivos en memoria/tmp
- **Formatos soportados**: DOCX, PDF (texto)
- **Detección de PII**:
  - Capa 1: Regex determinístico (DNI, RUC, email, teléfono)
  - Capa 2: Heurística legal peruana (domicilios, casillas)
  - Capa 3: Detección de personas (spaCy + heurístico)
  - Capa 4: Merge y deduplicación
- **Post-scan**: Verificación obligatoria del documento final
- **Privacidad**: Archivos eliminados automáticamente después de 30 minutos

## Desarrollo Local

```bash
pip install -r requirements_render.txt
python -m spacy download es_core_news_sm
python main_render.py
```

Acceder a: http://localhost:5000

## Health Check

Render usa `/health` para verificar que la aplicación está funcionando.

## Notas

- El procesamiento de PDFs escaneados (imágenes) no está soportado
- Tamaño máximo de archivo: 16 MB
- Los archivos se almacenan temporalmente en `/tmp` y se eliminan periódicamente
