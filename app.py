import os
import csv
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash
from werkzeug.utils import secure_filename
from docx import Document
from openai import OpenAI

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")

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

CARPETA_MODELOS = "modelos_legales"
CARPETA_ESTILOS = "estilos_estudio"
CARPETA_RESULTADOS = "Resultados"
ARCHIVO_HISTORIAL = "historial.csv"


def cargar_plantilla(nombre_archivo):
    """Lee el archivo .txt de la plantilla."""
    ruta = os.path.join(CARPETA_MODELOS, nombre_archivo)
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def cargar_estilos(carpeta_estilos):
    """Lee archivos de texto de la carpeta del modelo en /estilos_estudio."""
    ruta_carpeta = os.path.join(CARPETA_ESTILOS, carpeta_estilos)
    estilos = []
    if os.path.exists(ruta_carpeta):
        for archivo in os.listdir(ruta_carpeta):
            if archivo.endswith(".txt"):
                ruta_archivo = os.path.join(ruta_carpeta, archivo)
                with open(ruta_archivo, "r", encoding="utf-8") as f:
                    estilos.append(f.read())
    return "\n\n---\n\n".join(estilos)


def construir_prompt(plantilla, estilos, datos_caso):
    """Crea el prompt jurídico para OpenAI."""
    prompt = f"""Eres un abogado del estudio jurídico.

ESTILO:
{estilos if estilos else "(No hay ejemplos de estilo disponibles)"}

PLANTILLA BASE:
{plantilla if plantilla else "(No hay plantilla disponible)"}

DATOS DEL CASO:
- Invitado: {datos_caso.get('invitado', '{{FALTA_DATO}}')}
- Demandante: {datos_caso.get('demandante1', '{{FALTA_DATO}}')}
- DNI Demandante: {datos_caso.get('dni_demandante1', '{{FALTA_DATO}}')}
- Argumento 1: {datos_caso.get('argumento1', '{{FALTA_DATO}}')}
- Argumento 2: {datos_caso.get('argumento2', '{{FALTA_DATO}}')}
- Argumento 3: {datos_caso.get('argumento3', '{{FALTA_DATO}}')}
- Conclusión: {datos_caso.get('conclusion', '{{FALTA_DATO}}')}

INSTRUCCIONES:
- Respeta la estructura de la plantilla.
- Adopta el estilo de los ejemplos.
- Si falta un dato, conserva {{{{FALTA_DATO}}}}.
- Redacta el documento final completo.
- No incluyas explicaciones."""
    return prompt


def generar_con_ia(prompt):
    """Función que usa el modelo OpenAI para generar el documento."""
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un abogado experto en redacción de documentos legales."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=4000
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Error al generar con IA: {e}")
        return None


def guardar_docx(texto, nombre_archivo):
    """Convierte texto a .docx y lo guarda."""
    doc = Document()
    for parrafo in texto.split("\n"):
        if parrafo.strip():
            doc.add_paragraph(parrafo)
    ruta = os.path.join(CARPETA_RESULTADOS, nombre_archivo)
    doc.save(ruta)
    return ruta


def registrar_historial(fecha, tipo_documento, demandante, nombre_archivo):
    """Agrega una fila al archivo historial.csv."""
    archivo_existe = os.path.exists(ARCHIVO_HISTORIAL)
    with open(ARCHIVO_HISTORIAL, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not archivo_existe:
            writer.writerow(["fecha", "tipo_documento", "demandante", "archivo"])
        writer.writerow([fecha, tipo_documento, demandante, nombre_archivo])


def validar_dato(valor):
    """Si un campo llega vacío, reemplazarlo con {{FALTA_DATO}}."""
    if not valor or valor.strip() == "":
        return "{{FALTA_DATO}}"
    return valor.strip()


@app.route("/")
def index():
    """Página principal con el formulario."""
    return render_template("index.html", modelos=MODELOS)


@app.route("/procesar_ia", methods=["POST"])
def procesar_ia():
    """Procesa el formulario y genera el documento."""
    tipo_documento = request.form.get("tipo_documento")
    
    if tipo_documento not in MODELOS:
        flash("Tipo de documento no válido.", "error")
        return redirect(url_for("index"))
    
    modelo = MODELOS[tipo_documento]
    
    datos_caso = {
        "invitado": validar_dato(request.form.get("invitado", "")),
        "demandante1": validar_dato(request.form.get("demandante1", "")),
        "dni_demandante1": validar_dato(request.form.get("dni_demandante1", "")),
        "argumento1": validar_dato(request.form.get("argumento1", "")),
        "argumento2": validar_dato(request.form.get("argumento2", "")),
        "argumento3": validar_dato(request.form.get("argumento3", "")),
        "conclusion": validar_dato(request.form.get("conclusion", ""))
    }
    
    plantilla = cargar_plantilla(modelo["plantilla"])
    estilos = cargar_estilos(modelo["carpeta_estilos"])
    prompt = construir_prompt(plantilla, estilos, datos_caso)
    
    texto_generado = generar_con_ia(prompt)
    
    if not texto_generado:
        flash("Error al generar el documento. Verifica tu API key de OpenAI.", "error")
        return redirect(url_for("index"))
    
    fecha_actual = datetime.now()
    fecha_str = fecha_actual.strftime("%Y-%m-%d %H:%M:%S")
    nombre_archivo = f"{tipo_documento}_{fecha_actual.strftime('%Y%m%d_%H%M%S')}.docx"
    
    guardar_docx(texto_generado, nombre_archivo)
    
    demandante = datos_caso["demandante1"] if datos_caso["demandante1"] != "{{FALTA_DATO}}" else "Sin nombre"
    registrar_historial(fecha_str, modelo["nombre"], demandante, nombre_archivo)
    
    flash(f"Documento generado exitosamente: {nombre_archivo}", "success")
    return redirect(url_for("descargar", nombre_archivo=nombre_archivo))


@app.route("/descargar/<nombre_archivo>")
def descargar(nombre_archivo):
    """Descarga el documento generado de forma segura."""
    safe_filename = secure_filename(nombre_archivo)
    if not safe_filename or safe_filename != nombre_archivo:
        flash("Nombre de archivo no válido.", "error")
        return redirect(url_for("index"))
    
    if not safe_filename.endswith(".docx"):
        flash("Tipo de archivo no permitido.", "error")
        return redirect(url_for("index"))
    
    ruta_completa = os.path.join(os.path.abspath(CARPETA_RESULTADOS), safe_filename)
    if not ruta_completa.startswith(os.path.abspath(CARPETA_RESULTADOS)):
        flash("Acceso no permitido.", "error")
        return redirect(url_for("index"))
    
    if not os.path.exists(ruta_completa):
        flash("Archivo no encontrado.", "error")
        return redirect(url_for("index"))
    
    return send_from_directory(
        os.path.abspath(CARPETA_RESULTADOS), 
        safe_filename, 
        as_attachment=True
    )


@app.route("/historial")
def historial():
    """Muestra el historial de documentos generados."""
    documentos = []
    if os.path.exists(ARCHIVO_HISTORIAL):
        with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                documentos.append(row)
    documentos.reverse()
    return render_template("historial.html", documentos=documentos)


if __name__ == "__main__":
    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=True)
