# Plataforma de Generación de Documentos Jurídicos

## Overview

Sistema web Flask para generar documentos jurídicos utilizando plantillas internas del estudio y OpenAI. El usuario selecciona un tipo de documento, completa un formulario con los datos del caso, y el sistema genera un documento Word (.docx) personalizado.

## Estructura del Proyecto

```
/
├── app.py                    # Aplicación Flask principal
├── main.py                   # Entry point para gunicorn
├── templates/
│   ├── index.html           # Formulario principal
│   └── historial.html       # Historial de documentos
├── modelos_legales/         # Plantillas de documentos (subir .txt)
│   └── (aumento_alimentos.txt, pension_mutuo.txt, etc.)
├── estilos_estudio/         # Ejemplos de estilo por tipo de documento
│   ├── aumento_alimentos/   # Subcarpeta con archivos .txt de estilo
│   └── pension_mutuo/       # Subcarpeta con archivos .txt de estilo
├── Resultados/              # Documentos .docx generados
├── historial.csv            # Registro de documentos generados
└── design_guidelines.md     # Guías de diseño frontend
```

## Flujo del Sistema

1. Usuario accede a la página principal (/)
2. Selecciona tipo de documento del dropdown
3. Completa el formulario (invitado, demandante, DNI, argumentos, conclusión)
4. Presiona "Generar Documento"
5. Backend:
   - Carga plantilla desde /modelos_legales
   - Carga estilos desde /estilos_estudio
   - Construye prompt jurídico
   - Llama a OpenAI API
   - Convierte respuesta a .docx
   - Guarda en /Resultados
   - Registra en historial.csv
6. Usuario descarga el documento Word

## Diccionario MODELOS

El sistema usa un diccionario para configurar tipos de documentos:

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

Para agregar nuevos tipos de documentos:
1. Añadir entrada al diccionario MODELOS en app.py
2. Crear archivo .txt de plantilla en /modelos_legales
3. Crear carpeta con ejemplos de estilo en /estilos_estudio

## Variables de Entorno Requeridas

- `OPENAI_API_KEY`: API key de OpenAI para generación de documentos
- `SESSION_SECRET`: Clave secreta para sesiones Flask

## Tecnologías

- **Backend**: Flask, Python 3.11
- **Frontend**: HTML5, Tailwind CSS, Roboto font
- **Generación de documentos**: python-docx
- **IA**: OpenAI API (modelo gpt-4o)
- **Servidor**: Gunicorn

## Rutas

- `/` - Formulario principal
- `/procesar_ia` - POST para procesar formulario y generar documento
- `/historial` - Ver historial de documentos generados
- `/descargar/<nombre_archivo>` - Descargar documento específico

## Notas para el Administrador

- **Plantillas**: Subir archivos .txt a /modelos_legales con la estructura deseada
- **Estilos**: Subir archivos .txt de ejemplo a subcarpetas en /estilos_estudio
- Los campos vacíos se reemplazan con `{{FALTA_DATO}}` en el documento generado
