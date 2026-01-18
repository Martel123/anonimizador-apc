# Anonimizador Legal

Herramienta web para anonimizar documentos legales peruanos. Detecta y reemplaza automáticamente información personal identificable (PII) en documentos DOCX y PDF.

## Características

- **Detección automática de PII**: DNI, RUC, emails, teléfonos, direcciones, nombres de personas
- **Formatos soportados**: DOCX y PDF (con texto extraíble)
- **Placeholders consistentes**: Mismo dato = mismo placeholder en todo el documento
- **Reporte detallado**: JSON y TXT con resumen de cambios realizados
- **Privacidad**: Archivos procesados en memoria, eliminación automática después de 30 minutos

## Tipos de datos detectados

| Tipo | Placeholder | Descripción |
|------|-------------|-------------|
| DNI | `{{DNI_1}}` | Documentos de identidad (8 dígitos) |
| RUC | `{{RUC_1}}` | Registro Único de Contribuyentes (11 dígitos) |
| Email | `{{EMAIL_1}}` | Direcciones de correo electrónico |
| Teléfono | `{{TELEFONO_1}}` | Números telefónicos (9 dígitos, +51) |
| Persona | `{{PERSONA_1}}` | Nombres de personas |
| Dirección | `{{DIRECCION_1}}` | Direcciones físicas (Av., Jr., Calle, etc.) |
| Expediente | `{{EXPEDIENTE_1}}` | Números de expediente judicial |
| Casilla | `{{CASILLA_1}}` | Casillas electrónicas |
| Juzgado | `{{JUZGADO_1}}` | Referencias a juzgados |

## Límites

- Tamaño máximo de archivo: 10 MB
- Máximo de páginas PDF: 50
- PDFs escaneados (sin texto): No soportados actualmente

## Cómo usar

1. Accede a la página principal
2. Arrastra o selecciona un archivo DOCX o PDF
3. Haz clic en "Anonimizar documento"
4. Descarga el documento anonimizado y el reporte

## Privacidad y Seguridad

- Los documentos NO se almacenan en base de datos
- Procesamiento en memoria
- Archivos temporales eliminados automáticamente después de 30 minutos
- No se guarda información de los datos detectados

## Stack técnico

- **Backend**: Python/Flask
- **Procesamiento DOCX**: python-docx
- **Procesamiento PDF**: PyPDF2, ReportLab
- **Frontend**: HTML5, Tailwind CSS
- **No requiere servicios pagos**: Detección por reglas y expresiones regulares

## Ejecución

El proyecto se ejecuta automáticamente al iniciar. La aplicación estará disponible en:

```
http://localhost:5000
```

## Estructura de archivos

```
├── anonymizer.py          # Módulo principal de anonimización
├── app.py                 # Aplicación Flask
├── main.py                # Punto de entrada
├── templates/
│   ├── base_anonymizer.html      # Layout base
│   ├── anonymizer_home.html      # Página principal
│   └── anonymizer_result.html    # Página de resultados
├── temp_anonymizer/       # Archivos temporales (auto-limpieza)
└── anonymizer_output/     # Archivos de salida (auto-limpieza)
```

## Licencia

Uso interno. No distribuir sin autorización.
