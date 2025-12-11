import os
import csv
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from docx import Document
from openai import OpenAI

from models import db, User, DocumentRecord, Plantilla, Estilo

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Por favor inicia sesión para acceder a esta página.'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
    """Lee el archivo .txt de la plantilla o desde la base de datos."""
    plantilla_db = Plantilla.query.filter_by(key=nombre_archivo.replace('.txt', ''), activa=True).first()
    if plantilla_db:
        return plantilla_db.contenido
    
    ruta = os.path.join(CARPETA_MODELOS, nombre_archivo)
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def cargar_estilos(carpeta_estilos):
    """Lee archivos de texto de la carpeta del modelo en /estilos_estudio o base de datos."""
    estilos_db = Estilo.query.filter_by(plantilla_key=carpeta_estilos, activo=True).all()
    if estilos_db:
        return "\n\n---\n\n".join([e.contenido for e in estilos_db])
    
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


def validar_dato(valor):
    """Si un campo llega vacío, reemplazarlo con {{FALTA_DATO}}."""
    if not valor or valor.strip() == "":
        return "{{FALTA_DATO}}"
    return valor.strip()


@app.route("/")
def index():
    """Página principal con el formulario."""
    return render_template("index.html", modelos=MODELOS)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Página de inicio de sesión."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            flash("Sesión iniciada correctamente.", "success")
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash("Email o contraseña incorrectos.", "error")
    
    return render_template("login.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    """Página de registro de usuario."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        
        if not username or not email or not password:
            flash("Todos los campos son obligatorios.", "error")
            return render_template("registro.html")
        
        if password != password_confirm:
            flash("Las contraseñas no coinciden.", "error")
            return render_template("registro.html")
        
        if len(password) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
            return render_template("registro.html")
        
        if User.query.filter_by(email=email).first():
            flash("Ya existe una cuenta con este email.", "error")
            return render_template("registro.html")
        
        if User.query.filter_by(username=username).first():
            flash("Este nombre de usuario ya está en uso.", "error")
            return render_template("registro.html")
        
        user = User(username=username, email=email)
        user.set_password(password)
        
        if User.query.count() == 0:
            user.is_admin = True
        
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        flash("Cuenta creada exitosamente.", "success")
        return redirect(url_for('index'))
    
    return render_template("registro.html")


@app.route("/logout")
@login_required
def logout():
    """Cerrar sesión."""
    logout_user()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for('index'))


@app.route("/procesar_ia", methods=["POST"])
@login_required
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
    nombre_archivo = f"{tipo_documento}_{fecha_actual.strftime('%Y%m%d_%H%M%S')}.docx"
    
    guardar_docx(texto_generado, nombre_archivo)
    
    demandante = datos_caso["demandante1"] if datos_caso["demandante1"] != "{{FALTA_DATO}}" else "Sin nombre"
    
    record = DocumentRecord(
        user_id=current_user.id,
        fecha=fecha_actual,
        tipo_documento=modelo["nombre"],
        tipo_documento_key=tipo_documento,
        demandante=demandante,
        archivo=nombre_archivo,
        texto_generado=texto_generado,
        datos_caso=datos_caso
    )
    db.session.add(record)
    db.session.commit()
    
    flash(f"Documento generado exitosamente: {nombre_archivo}", "success")
    return redirect(url_for("descargar", nombre_archivo=nombre_archivo))


@app.route("/preview", methods=["POST"])
@login_required
def preview():
    """Genera un preview del documento sin guardarlo."""
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
        flash("Error al generar el preview. Verifica tu API key de OpenAI.", "error")
        return redirect(url_for("index"))
    
    return render_template("preview.html", 
                          texto=texto_generado, 
                          datos_caso=datos_caso,
                          tipo_documento=tipo_documento,
                          modelo=modelo)


@app.route("/guardar_desde_preview", methods=["POST"])
@login_required
def guardar_desde_preview():
    """Guarda el documento después del preview."""
    tipo_documento = request.form.get("tipo_documento")
    texto_editado = request.form.get("texto_editado")
    
    if tipo_documento not in MODELOS:
        flash("Tipo de documento no válido.", "error")
        return redirect(url_for("index"))
    
    modelo = MODELOS[tipo_documento]
    
    datos_caso = {
        "invitado": request.form.get("invitado", "{{FALTA_DATO}}"),
        "demandante1": request.form.get("demandante1", "{{FALTA_DATO}}"),
        "dni_demandante1": request.form.get("dni_demandante1", "{{FALTA_DATO}}"),
        "argumento1": request.form.get("argumento1", "{{FALTA_DATO}}"),
        "argumento2": request.form.get("argumento2", "{{FALTA_DATO}}"),
        "argumento3": request.form.get("argumento3", "{{FALTA_DATO}}"),
        "conclusion": request.form.get("conclusion", "{{FALTA_DATO}}")
    }
    
    fecha_actual = datetime.now()
    nombre_archivo = f"{tipo_documento}_{fecha_actual.strftime('%Y%m%d_%H%M%S')}.docx"
    
    guardar_docx(texto_editado, nombre_archivo)
    
    demandante = datos_caso["demandante1"] if datos_caso["demandante1"] != "{{FALTA_DATO}}" else "Sin nombre"
    
    record = DocumentRecord(
        user_id=current_user.id,
        fecha=fecha_actual,
        tipo_documento=modelo["nombre"],
        tipo_documento_key=tipo_documento,
        demandante=demandante,
        archivo=nombre_archivo,
        texto_generado=texto_editado,
        datos_caso=datos_caso
    )
    db.session.add(record)
    db.session.commit()
    
    flash(f"Documento guardado exitosamente: {nombre_archivo}", "success")
    return redirect(url_for("descargar", nombre_archivo=nombre_archivo))


@app.route("/editar/<int:doc_id>", methods=["GET", "POST"])
@login_required
def editar_documento(doc_id):
    """Edita un documento existente."""
    record = DocumentRecord.query.get_or_404(doc_id)
    
    if record.user_id != current_user.id and not current_user.is_admin:
        flash("No tienes permiso para editar este documento.", "error")
        return redirect(url_for("historial"))
    
    if request.method == "POST":
        texto_editado = request.form.get("texto_editado")
        
        guardar_docx(texto_editado, record.archivo)
        record.texto_generado = texto_editado
        record.fecha = datetime.now()
        db.session.commit()
        
        flash("Documento actualizado exitosamente.", "success")
        return redirect(url_for("historial"))
    
    return render_template("editar.html", record=record)


@app.route("/descargar/<nombre_archivo>")
@login_required
def descargar(nombre_archivo):
    """Descarga el documento generado de forma segura."""
    safe_filename = secure_filename(nombre_archivo)
    if not safe_filename or safe_filename != nombre_archivo:
        flash("Nombre de archivo no válido.", "error")
        return redirect(url_for("index"))
    
    if not safe_filename.endswith(".docx"):
        flash("Tipo de archivo no permitido.", "error")
        return redirect(url_for("index"))
    
    if current_user.is_admin:
        record = DocumentRecord.query.filter_by(archivo=safe_filename).first()
    else:
        record = DocumentRecord.query.filter_by(archivo=safe_filename, user_id=current_user.id).first()
    
    if not record:
        flash("Documento no encontrado o no tienes permiso para accederlo.", "error")
        return redirect(url_for("historial"))
    
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
@login_required
def historial():
    """Muestra el historial de documentos generados."""
    search = request.args.get('search', '').strip()
    tipo_filter = request.args.get('tipo', '').strip()
    fecha_desde = request.args.get('fecha_desde', '').strip()
    fecha_hasta = request.args.get('fecha_hasta', '').strip()
    
    query = DocumentRecord.query.filter_by(user_id=current_user.id)
    
    if search:
        query = query.filter(
            db.or_(
                DocumentRecord.demandante.ilike(f'%{search}%'),
                DocumentRecord.tipo_documento.ilike(f'%{search}%')
            )
        )
    
    if tipo_filter:
        query = query.filter(DocumentRecord.tipo_documento_key == tipo_filter)
    
    if fecha_desde:
        try:
            fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d')
            query = query.filter(DocumentRecord.fecha >= fecha_desde_dt)
        except ValueError:
            pass
    
    if fecha_hasta:
        try:
            fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d')
            fecha_hasta_dt = fecha_hasta_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(DocumentRecord.fecha <= fecha_hasta_dt)
        except ValueError:
            pass
    
    documentos = query.order_by(DocumentRecord.fecha.desc()).all()
    
    return render_template("historial.html", 
                          documentos=documentos, 
                          modelos=MODELOS,
                          search=search,
                          tipo_filter=tipo_filter,
                          fecha_desde=fecha_desde,
                          fecha_hasta=fecha_hasta)


@app.route("/admin")
@login_required
def admin():
    """Panel de administración."""
    if not current_user.is_admin:
        flash("No tienes permisos de administrador.", "error")
        return redirect(url_for("index"))
    
    plantillas = Plantilla.query.all()
    estilos = Estilo.query.all()
    usuarios = User.query.all()
    total_docs = DocumentRecord.query.count()
    
    return render_template("admin.html", 
                          plantillas=plantillas, 
                          estilos=estilos,
                          usuarios=usuarios,
                          total_docs=total_docs,
                          modelos=MODELOS)


@app.route("/admin/plantilla", methods=["GET", "POST"])
@login_required
def admin_plantilla():
    """Crear o editar plantilla."""
    if not current_user.is_admin:
        flash("No tienes permisos de administrador.", "error")
        return redirect(url_for("index"))
    
    plantilla_id = request.args.get('id', type=int)
    plantilla = Plantilla.query.get(plantilla_id) if plantilla_id else None
    
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        nombre = request.form.get("nombre", "").strip()
        contenido = request.form.get("contenido", "").strip()
        
        if not key or not nombre or not contenido:
            flash("Todos los campos son obligatorios.", "error")
            return render_template("admin_plantilla.html", plantilla=plantilla)
        
        if plantilla:
            plantilla.key = key
            plantilla.nombre = nombre
            plantilla.contenido = contenido
            flash("Plantilla actualizada exitosamente.", "success")
        else:
            if Plantilla.query.filter_by(key=key).first():
                flash("Ya existe una plantilla con esta clave.", "error")
                return render_template("admin_plantilla.html", plantilla=plantilla)
            
            plantilla = Plantilla(key=key, nombre=nombre, contenido=contenido, carpeta_estilos=key)
            db.session.add(plantilla)
            flash("Plantilla creada exitosamente.", "success")
        
        db.session.commit()
        return redirect(url_for("admin"))
    
    return render_template("admin_plantilla.html", plantilla=plantilla)


@app.route("/admin/plantilla/eliminar/<int:plantilla_id>", methods=["POST"])
@login_required
def eliminar_plantilla(plantilla_id):
    """Eliminar plantilla."""
    if not current_user.is_admin:
        flash("No tienes permisos de administrador.", "error")
        return redirect(url_for("index"))
    
    plantilla = Plantilla.query.get_or_404(plantilla_id)
    db.session.delete(plantilla)
    db.session.commit()
    flash("Plantilla eliminada exitosamente.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/estilo", methods=["GET", "POST"])
@login_required
def admin_estilo():
    """Crear o editar estilo."""
    if not current_user.is_admin:
        flash("No tienes permisos de administrador.", "error")
        return redirect(url_for("index"))
    
    estilo_id = request.args.get('id', type=int)
    estilo = Estilo.query.get(estilo_id) if estilo_id else None
    
    plantillas_db = Plantilla.query.all()
    plantillas_keys = list(MODELOS.keys()) + [p.key for p in plantillas_db]
    plantillas_keys = list(set(plantillas_keys))
    
    if request.method == "POST":
        plantilla_key = request.form.get("plantilla_key", "").strip()
        nombre = request.form.get("nombre", "").strip()
        contenido = request.form.get("contenido", "").strip()
        
        if not plantilla_key or not nombre or not contenido:
            flash("Todos los campos son obligatorios.", "error")
            return render_template("admin_estilo.html", estilo=estilo, plantillas_keys=plantillas_keys)
        
        if estilo:
            estilo.plantilla_key = plantilla_key
            estilo.nombre = nombre
            estilo.contenido = contenido
            flash("Estilo actualizado exitosamente.", "success")
        else:
            estilo = Estilo(plantilla_key=plantilla_key, nombre=nombre, contenido=contenido)
            db.session.add(estilo)
            flash("Estilo creado exitosamente.", "success")
        
        db.session.commit()
        return redirect(url_for("admin"))
    
    return render_template("admin_estilo.html", estilo=estilo, plantillas_keys=plantillas_keys)


@app.route("/admin/estilo/eliminar/<int:estilo_id>", methods=["POST"])
@login_required
def eliminar_estilo(estilo_id):
    """Eliminar estilo."""
    if not current_user.is_admin:
        flash("No tienes permisos de administrador.", "error")
        return redirect(url_for("index"))
    
    estilo = Estilo.query.get_or_404(estilo_id)
    db.session.delete(estilo)
    db.session.commit()
    flash("Estilo eliminado exitosamente.", "success")
    return redirect(url_for("admin"))


with app.app_context():
    db.create_all()
    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
