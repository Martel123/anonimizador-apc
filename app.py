import os
import csv
import json
import logging
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify, session, g
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from docx import Document
from docx.shared import Pt, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.style import WD_STYLE_TYPE
from openai import OpenAI

from models import db, User, DocumentRecord, Plantilla, Estilo, CampoPlantilla, Tenant

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


def super_admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_super_admin():
            flash("Acceso restringido a super administradores.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function


def admin_estudio_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Acceso restringido a administradores.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function


def get_current_tenant():
    if not current_user.is_authenticated:
        return None
    if current_user.is_super_admin() and 'impersonate_tenant_id' in session:
        return Tenant.query.get(session['impersonate_tenant_id'])
    return current_user.tenant


def get_tenant_id():
    tenant = get_current_tenant()
    return tenant.id if tenant else None


def get_resultados_folder(tenant=None):
    if tenant:
        folder = os.path.join(CARPETA_RESULTADOS, f"tenant_{tenant.id}")
    else:
        folder = CARPETA_RESULTADOS
    os.makedirs(folder, exist_ok=True)
    return folder


def cargar_plantilla(nombre_archivo, tenant_id=None):
    key = nombre_archivo.replace('.txt', '')
    if tenant_id:
        plantilla_db = Plantilla.query.filter_by(key=key, tenant_id=tenant_id, activa=True).first()
        if plantilla_db:
            return plantilla_db.contenido
    
    plantilla_db = Plantilla.query.filter_by(key=key, activa=True).first()
    if plantilla_db:
        return plantilla_db.contenido
    
    ruta = os.path.join(CARPETA_MODELOS, nombre_archivo)
    if os.path.exists(ruta):
        with open(ruta, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def cargar_estilos(carpeta_estilos, tenant_id=None):
    if tenant_id:
        estilos_db = Estilo.query.filter_by(plantilla_key=carpeta_estilos, tenant_id=tenant_id, activo=True).all()
        if estilos_db:
            return "\n\n---\n\n".join([e.contenido for e in estilos_db])
    
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


def construir_prompt(plantilla, estilos, datos_caso, campos_dinamicos=None):
    datos_str = ""
    if campos_dinamicos and len(campos_dinamicos) > 0:
        for campo in campos_dinamicos:
            valor = datos_caso.get(campo.nombre_campo, '{{FALTA_DATO}}')
            datos_str += f"- {campo.etiqueta}: {valor}\n"
    else:
        datos_str = f"""- Invitado: {datos_caso.get('invitado', '{{FALTA_DATO}}')}
- Demandante: {datos_caso.get('demandante1', '{{FALTA_DATO}}')}
- DNI Demandante: {datos_caso.get('dni_demandante1', '{{FALTA_DATO}}')}
- Argumento 1: {datos_caso.get('argumento1', '{{FALTA_DATO}}')}
- Argumento 2: {datos_caso.get('argumento2', '{{FALTA_DATO}}')}
- Argumento 3: {datos_caso.get('argumento3', '{{FALTA_DATO}}')}
- Conclusión: {datos_caso.get('conclusion', '{{FALTA_DATO}}')}"""
    
    prompt = f"""Eres un abogado experto del estudio jurídico especializado en derecho de familia.

══════════════════════════════════════════════════════════════
VOCABULARIO Y FRASES FORMALES OBLIGATORIAS:
══════════════════════════════════════════════════════════════
{estilos if estilos else "(No hay ejemplos de estilo disponibles)"}

══════════════════════════════════════════════════════════════
PLANTILLA BASE:
══════════════════════════════════════════════════════════════
{plantilla if plantilla else "(No hay plantilla disponible)"}

══════════════════════════════════════════════════════════════
DATOS DEL CASO:
══════════════════════════════════════════════════════════════
{datos_str}

══════════════════════════════════════════════════════════════
INSTRUCCIONES:
══════════════════════════════════════════════════════════════
1. Usa las frases formales del vocabulario cuando corresponda a cada sección.
2. Si el vocabulario incluye citas legales, incorpóralas en la fundamentación jurídica.
3. Estructura el documento con secciones numeradas si corresponde (PRIMERO, SEGUNDO...).
4. Mantén tono formal y respetuoso.
5. Si falta un dato, conserva {{{{FALTA_DATO}}}}.
6. Montos en números y letras: S/1,000.00 (MIL CON 00/100 SOLES).
7. Usa mayúsculas para énfasis en términos legales.
8. Redacta el documento completo sin explicaciones adicionales."""
    return prompt


def generar_con_ia(prompt):
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


def get_tenant_logo_path(tenant):
    if tenant and tenant.logo_path:
        logo_path = os.path.join("static", "tenants", tenant.slug, tenant.logo_path)
        if os.path.exists(logo_path):
            return logo_path
    default_logo = os.path.join("static", "logo_estudio.png")
    if os.path.exists(default_logo):
        return default_logo
    return None


def guardar_docx(texto, nombre_archivo, tenant=None):
    doc = Document()
    
    sections = doc.sections
    for section in sections:
        section.top_margin = Cm(3.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(3)
        section.right_margin = Cm(2.5)
        
        logo_path = get_tenant_logo_path(tenant)
        if logo_path and os.path.exists(logo_path):
            header = section.header
            header_para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
            header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = header_para.add_run()
            run.add_picture(logo_path, width=Cm(4))
            
            if tenant:
                info_lines = tenant.get_header_info()
            else:
                info_lines = [
                    "Autorizado su funcionamiento por Resolución Directoral N.º 3562-2022-JUS/DGDPAJ-DCMA",
                    "Dirección: Av. Javier Prado Este 255, oficina 701. Distrito de San Isidro, Lima-Perú",
                    "Teléfono (01) – 6757575 / 994647890",
                    "Página web: www.abogadasperu.com"
                ]
            
            for linea in info_lines:
                info_para = header.add_paragraph()
                info_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                info_run = info_para.add_run(linea)
                info_run.font.name = 'Times New Roman'
                info_run.font.size = Pt(9)
                info_para.paragraph_format.space_after = Pt(0)
                info_para.paragraph_format.space_before = Pt(0)
    
    titulos_principales = ['SUMILLA:', 'PETITORIO:', 'HECHOS:', 'FUNDAMENTOS', 'ANEXOS:', 
                          'POR TANTO:', 'VÍA PROCEDIMENTAL:', 'CONTRACAUTELA:',
                          'FUNDAMENTACION JURÍDICA:', 'FUNDAMENTACIÓN JURÍDICA:']
    titulos_secundarios = ['PRIMERO:', 'SEGUNDO:', 'TERCERO:', 'CUARTO:', 'QUINTO:',
                          'SEXTO:', 'SÉPTIMO:', 'OCTAVO:', 'NOVENO:', 'DÉCIMO:',
                          'DATOS DEL SOLICITANTE:', 'DATOS DE LOS SOLICITANTES:',
                          'NOMBRE Y DIRECCIÓN DEL DEMANDADO:', 'NOMBRE DEL INVITADO',
                          'OTRAS PERSONAS CON DERECHO ALIMENTARIO']
    encabezados = ['SEÑOR JUEZ', 'SEÑORA JUEZ', 'SEÑOR:', 'SEÑORA:', 'PRESENTE']
    
    for parrafo in texto.split("\n"):
        linea = parrafo.strip()
        if not linea:
            continue
        
        p = doc.add_paragraph()
        run = p.add_run(linea)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)
        
        es_titulo_principal = any(linea.upper().startswith(t.upper()) for t in titulos_principales)
        es_titulo_secundario = any(linea.upper().startswith(t.upper()) for t in titulos_secundarios)
        es_encabezado = any(linea.upper().startswith(t.upper()) for t in encabezados)
        
        if es_titulo_principal:
            run.bold = True
            run.font.size = Pt(12)
            p.paragraph_format.space_before = Pt(18)
            p.paragraph_format.space_after = Pt(6)
        elif es_titulo_secundario:
            run.bold = True
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(6)
        elif es_encabezado:
            run.bold = True
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(6)
        elif linea.startswith('_____'):
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(24)
        elif linea.upper().startswith('D.N.I'):
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(12)
        else:
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.first_line_indent = Cm(1.25)
        
        p.paragraph_format.line_spacing = 1.5
    
    folder = get_resultados_folder(tenant)
    ruta = os.path.join(folder, nombre_archivo)
    doc.save(ruta)
    return ruta


def validar_dato(valor):
    if not valor or valor.strip() == "":
        return "{{FALTA_DATO}}"
    return valor.strip()


@app.context_processor
def inject_tenant():
    return {
        'current_tenant': get_current_tenant() if current_user.is_authenticated else None,
        'is_impersonating': 'impersonate_tenant_id' in session and current_user.is_authenticated and current_user.is_super_admin()
    }


@app.route("/")
def index():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    
    modelos_completos = dict(MODELOS)
    if tenant_id:
        plantillas_db = Plantilla.query.filter_by(tenant_id=tenant_id, activa=True).all()
    else:
        plantillas_db = Plantilla.query.filter_by(activa=True).all()
    
    for p in plantillas_db:
        if p.key not in modelos_completos:
            modelos_completos[p.key] = {
                "nombre": p.nombre,
                "plantilla": f"{p.key}.txt",
                "carpeta_estilos": p.carpeta_estilos or p.key
            }
    return render_template("index.html", modelos=modelos_completos, tenant=tenant)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        
        user = User.query.filter_by(email=email, activo=True).first()
        if user and user.check_password(password):
            user.last_login = datetime.utcnow()
            db.session.commit()
            login_user(user)
            flash("Sesión iniciada correctamente.", "success")
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash("Email o contraseña incorrectos.", "error")
    
    return render_template("login.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    return render_template("registro.html")


@app.route("/registro_estudio", methods=["GET", "POST"])
def registro_estudio():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == "POST":
        nombre_estudio = request.form.get("nombre_estudio", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        
        if not nombre_estudio or not username or not email or not password:
            flash("Todos los campos son obligatorios.", "error")
            return render_template("registro_estudio.html")
        
        if password != password_confirm:
            flash("Las contraseñas no coinciden.", "error")
            return render_template("registro_estudio.html")
        
        if len(password) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
            return render_template("registro_estudio.html")
        
        if User.query.filter_by(email=email).first():
            flash("Ya existe una cuenta con este email.", "error")
            return render_template("registro_estudio.html")
        
        slug = nombre_estudio.lower().replace(" ", "-").replace(".", "")[:50]
        base_slug = slug
        counter = 1
        while Tenant.query.filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        tenant = Tenant(
            nombre=nombre_estudio,
            slug=slug,
            activo=True
        )
        db.session.add(tenant)
        db.session.flush()
        
        is_first_user = User.query.count() == 0
        user = User(
            username=username,
            email=email,
            tenant_id=tenant.id,
            role='super_admin' if is_first_user else 'admin_estudio',
            activo=True
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        flash(f"Estudio '{nombre_estudio}' creado exitosamente. Eres el administrador.", "success")
        return redirect(url_for('configurar_estudio'))
    
    return render_template("registro_estudio.html")


@app.route("/logout")
@login_required
def logout():
    if 'impersonate_tenant_id' in session:
        del session['impersonate_tenant_id']
    logout_user()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for('index'))


@app.route("/procesar_ia", methods=["POST"])
@login_required
def procesar_ia():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    tipo_documento = request.form.get("tipo_documento")
    
    plantilla_db = Plantilla.query.filter_by(key=tipo_documento, activa=True).first()
    if tipo_documento in MODELOS:
        modelo = MODELOS[tipo_documento]
    elif plantilla_db:
        modelo = {
            "nombre": plantilla_db.nombre,
            "plantilla": f"{tipo_documento}.txt",
            "carpeta_estilos": plantilla_db.carpeta_estilos or tipo_documento
        }
    else:
        flash("Tipo de documento no válido.", "error")
        return redirect(url_for("index"))
    
    campos_dinamicos = CampoPlantilla.query.filter_by(plantilla_key=tipo_documento).order_by(CampoPlantilla.orden).all()
    
    if campos_dinamicos:
        datos_caso = {}
        for campo in campos_dinamicos:
            datos_caso[campo.nombre_campo] = validar_dato(request.form.get(campo.nombre_campo, ""))
    else:
        datos_caso = {
            "invitado": validar_dato(request.form.get("invitado", "")),
            "demandante1": validar_dato(request.form.get("demandante1", "")),
            "dni_demandante1": validar_dato(request.form.get("dni_demandante1", "")),
            "argumento1": validar_dato(request.form.get("argumento1", "")),
            "argumento2": validar_dato(request.form.get("argumento2", "")),
            "argumento3": validar_dato(request.form.get("argumento3", "")),
            "conclusion": validar_dato(request.form.get("conclusion", ""))
        }
    
    plantilla = cargar_plantilla(modelo["plantilla"], tenant_id)
    estilos = cargar_estilos(modelo["carpeta_estilos"], tenant_id)
    prompt = construir_prompt(plantilla, estilos, datos_caso, campos_dinamicos if campos_dinamicos else None)
    
    texto_generado = generar_con_ia(prompt)
    
    if not texto_generado:
        flash("Error al generar el documento. Verifica tu API key de OpenAI.", "error")
        return redirect(url_for("index"))
    
    fecha_actual = datetime.now()
    nombre_archivo = f"{tipo_documento}_{fecha_actual.strftime('%Y%m%d_%H%M%S')}.docx"
    
    guardar_docx(texto_generado, nombre_archivo, tenant)
    
    demandante_campo = datos_caso.get("demandante1") or datos_caso.get("nombre_demandante") or datos_caso.get("demandante") or "Sin nombre"
    if demandante_campo == "{{FALTA_DATO}}":
        demandante_campo = "Sin nombre"
    
    record = DocumentRecord(
        user_id=current_user.id,
        tenant_id=tenant_id,
        fecha=fecha_actual,
        tipo_documento=modelo["nombre"],
        tipo_documento_key=tipo_documento,
        demandante=demandante_campo,
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
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    tipo_documento = request.form.get("tipo_documento")
    
    plantilla_db = Plantilla.query.filter_by(key=tipo_documento, activa=True).first()
    if tipo_documento in MODELOS:
        modelo = MODELOS[tipo_documento]
    elif plantilla_db:
        modelo = {
            "nombre": plantilla_db.nombre,
            "plantilla": f"{tipo_documento}.txt",
            "carpeta_estilos": plantilla_db.carpeta_estilos or tipo_documento
        }
    else:
        flash("Tipo de documento no válido.", "error")
        return redirect(url_for("index"))
    
    campos_dinamicos = CampoPlantilla.query.filter_by(plantilla_key=tipo_documento).order_by(CampoPlantilla.orden).all()
    
    if campos_dinamicos:
        datos_caso = {}
        for campo in campos_dinamicos:
            datos_caso[campo.nombre_campo] = validar_dato(request.form.get(campo.nombre_campo, ""))
    else:
        datos_caso = {
            "invitado": validar_dato(request.form.get("invitado", "")),
            "demandante1": validar_dato(request.form.get("demandante1", "")),
            "dni_demandante1": validar_dato(request.form.get("dni_demandante1", "")),
            "argumento1": validar_dato(request.form.get("argumento1", "")),
            "argumento2": validar_dato(request.form.get("argumento2", "")),
            "argumento3": validar_dato(request.form.get("argumento3", "")),
            "conclusion": validar_dato(request.form.get("conclusion", ""))
        }
    
    plantilla = cargar_plantilla(modelo["plantilla"], tenant_id)
    estilos = cargar_estilos(modelo["carpeta_estilos"], tenant_id)
    prompt = construir_prompt(plantilla, estilos, datos_caso, campos_dinamicos if campos_dinamicos else None)
    
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
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    tipo_documento = request.form.get("tipo_documento")
    texto_editado = request.form.get("texto_editado")
    
    plantilla_db = Plantilla.query.filter_by(key=tipo_documento, activa=True).first()
    if tipo_documento in MODELOS:
        modelo = MODELOS[tipo_documento]
    elif plantilla_db:
        modelo = {
            "nombre": plantilla_db.nombre,
            "plantilla": f"{tipo_documento}.txt",
            "carpeta_estilos": plantilla_db.carpeta_estilos or tipo_documento
        }
    else:
        flash("Tipo de documento no válido.", "error")
        return redirect(url_for("index"))
    
    datos_caso_str = request.form.get("datos_caso", "{}")
    try:
        datos_caso = json.loads(datos_caso_str)
    except:
        datos_caso = {}
    
    fecha_actual = datetime.now()
    nombre_archivo = f"{tipo_documento}_{fecha_actual.strftime('%Y%m%d_%H%M%S')}.docx"
    
    guardar_docx(texto_editado, nombre_archivo, tenant)
    
    demandante_campo = datos_caso.get("demandante1") or datos_caso.get("nombre_demandante") or datos_caso.get("demandante") or "Sin nombre"
    if demandante_campo == "{{FALTA_DATO}}":
        demandante_campo = "Sin nombre"
    
    record = DocumentRecord(
        user_id=current_user.id,
        tenant_id=tenant_id,
        fecha=fecha_actual,
        tipo_documento=modelo["nombre"],
        tipo_documento_key=tipo_documento,
        demandante=demandante_campo,
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
    record = DocumentRecord.query.get_or_404(doc_id)
    tenant = get_current_tenant()
    
    if not current_user.can_access_tenant(record.tenant_id):
        flash("No tienes permiso para editar este documento.", "error")
        return redirect(url_for("historial"))
    
    if request.method == "POST":
        texto_editado = request.form.get("texto_editado")
        
        folder = get_resultados_folder(tenant)
        ruta = os.path.join(folder, record.archivo)
        
        doc = Document()
        for parrafo in texto_editado.split("\n"):
            if parrafo.strip():
                doc.add_paragraph(parrafo)
        doc.save(ruta)
        
        record.texto_generado = texto_editado
        record.fecha = datetime.now()
        db.session.commit()
        
        flash("Documento actualizado exitosamente.", "success")
        return redirect(url_for("historial"))
    
    return render_template("editar.html", record=record)


@app.route("/descargar/<nombre_archivo>")
@login_required
def descargar(nombre_archivo):
    safe_filename = secure_filename(nombre_archivo)
    if not safe_filename or safe_filename != nombre_archivo:
        flash("Nombre de archivo no válido.", "error")
        return redirect(url_for("index"))
    
    if not safe_filename.endswith(".docx"):
        flash("Tipo de archivo no permitido.", "error")
        return redirect(url_for("index"))
    
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    
    if current_user.is_super_admin():
        record = DocumentRecord.query.filter_by(archivo=safe_filename).first()
    elif current_user.is_admin:
        record = DocumentRecord.query.filter_by(archivo=safe_filename, tenant_id=tenant_id).first()
    else:
        record = DocumentRecord.query.filter_by(archivo=safe_filename, user_id=current_user.id).first()
    
    if not record:
        flash("Documento no encontrado o no tienes permiso para accederlo.", "error")
        return redirect(url_for("historial"))
    
    doc_tenant = Tenant.query.get(record.tenant_id) if record.tenant_id else None
    folder = get_resultados_folder(doc_tenant)
    ruta_completa = os.path.join(os.path.abspath(folder), safe_filename)
    
    if not os.path.exists(ruta_completa):
        old_path = os.path.join(os.path.abspath(CARPETA_RESULTADOS), safe_filename)
        if os.path.exists(old_path):
            ruta_completa = old_path
            folder = CARPETA_RESULTADOS
        else:
            flash("Archivo no encontrado.", "error")
            return redirect(url_for("index"))
    
    return send_from_directory(
        os.path.abspath(folder), 
        safe_filename, 
        as_attachment=True
    )


@app.route("/historial")
@login_required
def historial():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    
    search = request.args.get('search', '').strip()
    tipo_filter = request.args.get('tipo', '').strip()
    fecha_desde = request.args.get('fecha_desde', '').strip()
    fecha_hasta = request.args.get('fecha_hasta', '').strip()
    
    if current_user.is_super_admin() and not tenant_id:
        query = DocumentRecord.query
    elif current_user.is_admin and tenant_id:
        query = DocumentRecord.query.filter_by(tenant_id=tenant_id)
    else:
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


@app.route("/super_admin")
@super_admin_required
def super_admin():
    tenants = Tenant.query.order_by(Tenant.created_at.desc()).all()
    
    stats = []
    for t in tenants:
        doc_count = DocumentRecord.query.filter_by(tenant_id=t.id).count()
        user_count = User.query.filter_by(tenant_id=t.id).count()
        last_doc = DocumentRecord.query.filter_by(tenant_id=t.id).order_by(DocumentRecord.fecha.desc()).first()
        stats.append({
            'tenant': t,
            'docs': doc_count,
            'users': user_count,
            'last_activity': last_doc.fecha if last_doc else None
        })
    
    total_docs = DocumentRecord.query.count()
    total_users = User.query.count()
    total_tenants = Tenant.query.count()
    
    return render_template("super_admin.html",
                          stats=stats,
                          total_docs=total_docs,
                          total_users=total_users,
                          total_tenants=total_tenants)


@app.route("/super_admin/impersonate/<int:tenant_id>")
@super_admin_required
def impersonate_tenant(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    session['impersonate_tenant_id'] = tenant_id
    flash(f"Ahora estás viendo como: {tenant.nombre}", "info")
    return redirect(url_for('index'))


@app.route("/super_admin/stop_impersonate")
@super_admin_required
def stop_impersonate():
    if 'impersonate_tenant_id' in session:
        del session['impersonate_tenant_id']
    flash("Volviste a tu vista de super administrador.", "info")
    return redirect(url_for('super_admin'))


@app.route("/admin")
@admin_estudio_required
def admin():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    plantillas = Plantilla.query.filter_by(tenant_id=tenant.id).all()
    estilos = Estilo.query.filter_by(tenant_id=tenant.id).all()
    usuarios = User.query.filter_by(tenant_id=tenant.id).all()
    total_docs = DocumentRecord.query.filter_by(tenant_id=tenant.id).count()
    
    return render_template("admin.html", 
                          plantillas=plantillas, 
                          estilos=estilos,
                          usuarios=usuarios,
                          total_docs=total_docs,
                          modelos=MODELOS,
                          tenant=tenant)


@app.route("/configurar_estudio", methods=["GET", "POST"])
@admin_estudio_required
def configurar_estudio():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    if request.method == "POST":
        tenant.nombre = request.form.get("nombre", "").strip() or tenant.nombre
        tenant.resolucion_directoral = request.form.get("resolucion_directoral", "").strip()
        tenant.direccion = request.form.get("direccion", "").strip()
        tenant.telefono = request.form.get("telefono", "").strip()
        tenant.pagina_web = request.form.get("pagina_web", "").strip()
        tenant.pais = request.form.get("pais", "").strip()
        tenant.ciudad = request.form.get("ciudad", "").strip()
        tenant.areas_practica = request.form.get("areas_practica", "").strip()
        
        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename:
                filename = secure_filename(file.filename)
                tenant_folder = os.path.join("static", "tenants", tenant.slug)
                os.makedirs(tenant_folder, exist_ok=True)
                filepath = os.path.join(tenant_folder, filename)
                file.save(filepath)
                tenant.logo_path = filename
        
        db.session.commit()
        flash("Configuración del estudio actualizada.", "success")
        return redirect(url_for("admin"))
    
    return render_template("configurar_estudio.html", tenant=tenant)


@app.route("/admin/usuarios", methods=["GET", "POST"])
@admin_estudio_required
def admin_usuarios():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "usuario_estudio")
        
        if not username or not email or not password:
            flash("Todos los campos son obligatorios.", "error")
        elif User.query.filter_by(email=email).first():
            flash("Ya existe un usuario con ese email.", "error")
        else:
            if role not in ['admin_estudio', 'usuario_estudio']:
                role = 'usuario_estudio'
            
            user = User(
                username=username,
                email=email,
                tenant_id=tenant.id,
                role=role,
                activo=True
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f"Usuario {username} creado exitosamente.", "success")
    
    usuarios = User.query.filter_by(tenant_id=tenant.id).all()
    return render_template("admin_usuarios.html", usuarios=usuarios, tenant=tenant)


@app.route("/admin/usuario/toggle/<int:user_id>", methods=["POST"])
@admin_estudio_required
def toggle_usuario(user_id):
    tenant = get_current_tenant()
    user = User.query.get_or_404(user_id)
    
    if user.tenant_id != tenant.id:
        flash("No tienes permiso para modificar este usuario.", "error")
        return redirect(url_for("admin_usuarios"))
    
    if user.id == current_user.id:
        flash("No puedes desactivar tu propia cuenta.", "error")
        return redirect(url_for("admin_usuarios"))
    
    user.activo = not user.activo
    db.session.commit()
    status = "activado" if user.activo else "desactivado"
    flash(f"Usuario {user.username} {status}.", "success")
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/plantilla", methods=["GET", "POST"])
@admin_estudio_required
def admin_plantilla():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    plantilla_id = request.args.get('id', type=int)
    plantilla = Plantilla.query.get(plantilla_id) if plantilla_id else None
    
    if plantilla and plantilla.tenant_id != tenant.id:
        flash("No tienes permiso para editar esta plantilla.", "error")
        return redirect(url_for("admin"))
    
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
            existing = Plantilla.query.filter_by(key=key, tenant_id=tenant.id).first()
            if existing:
                flash("Ya existe una plantilla con esta clave.", "error")
                return render_template("admin_plantilla.html", plantilla=plantilla)
            
            plantilla = Plantilla(
                key=key, 
                nombre=nombre, 
                contenido=contenido, 
                carpeta_estilos=key,
                tenant_id=tenant.id
            )
            db.session.add(plantilla)
            flash("Plantilla creada exitosamente.", "success")
        
        db.session.commit()
        return redirect(url_for("admin"))
    
    return render_template("admin_plantilla.html", plantilla=plantilla)


@app.route("/admin/plantilla/eliminar/<int:plantilla_id>", methods=["POST"])
@admin_estudio_required
def eliminar_plantilla(plantilla_id):
    tenant = get_current_tenant()
    plantilla = Plantilla.query.get_or_404(plantilla_id)
    
    if plantilla.tenant_id != tenant.id:
        flash("No tienes permiso para eliminar esta plantilla.", "error")
        return redirect(url_for("admin"))
    
    db.session.delete(plantilla)
    db.session.commit()
    flash("Plantilla eliminada exitosamente.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/estilo", methods=["GET", "POST"])
@admin_estudio_required
def admin_estilo():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    estilo_id = request.args.get('id', type=int)
    estilo = Estilo.query.get(estilo_id) if estilo_id else None
    
    if estilo and estilo.tenant_id != tenant.id:
        flash("No tienes permiso para editar este estilo.", "error")
        return redirect(url_for("admin"))
    
    plantillas_db = Plantilla.query.filter_by(tenant_id=tenant.id).all()
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
            estilo = Estilo(
                plantilla_key=plantilla_key, 
                nombre=nombre, 
                contenido=contenido,
                tenant_id=tenant.id
            )
            db.session.add(estilo)
            flash("Estilo creado exitosamente.", "success")
        
        db.session.commit()
        return redirect(url_for("admin"))
    
    return render_template("admin_estilo.html", estilo=estilo, plantillas_keys=plantillas_keys)


@app.route("/admin/estilo/eliminar/<int:estilo_id>", methods=["POST"])
@admin_estudio_required
def eliminar_estilo(estilo_id):
    tenant = get_current_tenant()
    estilo = Estilo.query.get_or_404(estilo_id)
    
    if estilo.tenant_id != tenant.id:
        flash("No tienes permiso para eliminar este estilo.", "error")
        return redirect(url_for("admin"))
    
    db.session.delete(estilo)
    db.session.commit()
    flash("Estilo eliminado exitosamente.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/campos/<plantilla_key>", methods=["GET", "POST"])
@admin_estudio_required
def admin_campos(plantilla_key):
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    plantilla = Plantilla.query.filter_by(key=plantilla_key, tenant_id=tenant.id).first()
    nombre_plantilla = plantilla.nombre if plantilla else MODELOS.get(plantilla_key, {}).get("nombre", plantilla_key)
    
    if request.method == "POST":
        campo_id = request.form.get("campo_id", type=int)
        nombre_campo = request.form.get("nombre_campo", "").strip()
        etiqueta = request.form.get("etiqueta", "").strip()
        tipo = request.form.get("tipo", "text").strip()
        requerido = request.form.get("requerido") == "on"
        orden = request.form.get("orden", 0, type=int)
        placeholder = request.form.get("placeholder", "").strip()
        opciones = request.form.get("opciones", "").strip()
        
        if not nombre_campo or not etiqueta:
            flash("Nombre del campo y etiqueta son obligatorios.", "error")
        else:
            if campo_id:
                campo = CampoPlantilla.query.get(campo_id)
                if campo and campo.tenant_id == tenant.id:
                    campo.nombre_campo = nombre_campo
                    campo.etiqueta = etiqueta
                    campo.tipo = tipo
                    campo.requerido = requerido
                    campo.orden = orden
                    campo.placeholder = placeholder
                    campo.opciones = opciones
                    flash("Campo actualizado.", "success")
            else:
                campo = CampoPlantilla(
                    plantilla_key=plantilla_key,
                    nombre_campo=nombre_campo,
                    etiqueta=etiqueta,
                    tipo=tipo,
                    requerido=requerido,
                    orden=orden,
                    placeholder=placeholder,
                    opciones=opciones,
                    tenant_id=tenant.id
                )
                db.session.add(campo)
                flash("Campo agregado.", "success")
            db.session.commit()
    
    campos = CampoPlantilla.query.filter_by(plantilla_key=plantilla_key, tenant_id=tenant.id).order_by(CampoPlantilla.orden).all()
    return render_template("admin_campos.html", 
                          plantilla_key=plantilla_key, 
                          nombre_plantilla=nombre_plantilla,
                          campos=campos)


@app.route("/admin/campo/eliminar/<int:campo_id>", methods=["POST"])
@admin_estudio_required
def eliminar_campo(campo_id):
    tenant = get_current_tenant()
    campo = CampoPlantilla.query.get_or_404(campo_id)
    
    if campo.tenant_id != tenant.id:
        flash("No tienes permiso para eliminar este campo.", "error")
        return redirect(url_for("admin"))
    
    plantilla_key = campo.plantilla_key
    db.session.delete(campo)
    db.session.commit()
    flash("Campo eliminado.", "success")
    return redirect(url_for("admin_campos", plantilla_key=plantilla_key))


@app.route("/api/campos/<plantilla_key>")
def get_campos_plantilla(plantilla_key):
    tenant_id = None
    if current_user.is_authenticated:
        tenant = get_current_tenant()
        tenant_id = tenant.id if tenant else None
    
    if tenant_id:
        campos = CampoPlantilla.query.filter_by(plantilla_key=plantilla_key, tenant_id=tenant_id).order_by(CampoPlantilla.orden).all()
    else:
        campos = CampoPlantilla.query.filter_by(plantilla_key=plantilla_key).order_by(CampoPlantilla.orden).all()
    
    return jsonify([{
        'id': c.id,
        'nombre_campo': c.nombre_campo,
        'etiqueta': c.etiqueta,
        'tipo': c.tipo,
        'requerido': c.requerido,
        'placeholder': c.placeholder or '',
        'opciones': c.opciones.split(',') if c.opciones else []
    } for c in campos])


with app.app_context():
    db.create_all()
    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
