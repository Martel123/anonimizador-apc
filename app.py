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

from models import db, User, DocumentRecord, Plantilla, Modelo, Estilo, CampoPlantilla, Tenant, Case, CaseAssignment, CaseDocument, Task

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
    }
}

CARPETA_MODELOS = "modelos_legales"
CARPETA_ESTILOS = "estilos_estudio"
CARPETA_RESULTADOS = "Resultados"
CARPETA_PLANTILLAS_SUBIDAS = "plantillas_subidas"
CARPETA_ESTILOS_SUBIDOS = "estilos_subidos"

import re

def extract_text_from_docx(file_path):
    """Extract all text from a Word document."""
    doc = Document(file_path)
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                full_text.append(cell.text)
    return '\n'.join(full_text)


def extract_text_from_pdf(file_path):
    """Extract text from PDF file."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        text = []
        for page in reader.pages:
            text.append(page.extract_text() or '')
        return '\n'.join(text)
    except Exception as e:
        logging.error(f"Error extracting PDF: {e}")
        return ""


def extract_text_from_txt(file_path):
    """Extract text from TXT file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logging.error(f"Error reading TXT: {e}")
        return ""


ALLOWED_EXTENSIONS = {'.docx', '.pdf', '.txt'}


def extract_text_from_file(file_path):
    """Extract text from file based on extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.docx':
        return extract_text_from_docx(file_path)
    elif ext == '.pdf':
        return extract_text_from_pdf(file_path)
    elif ext == '.txt':
        return extract_text_from_txt(file_path)
    return ""


def detect_placeholders_from_text(text):
    """
    Detect placeholders in text that represent fields to fill.
    Detects each occurrence of dots or underscores as a separate field.
    """
    campos = []
    campo_counter = {}
    
    pattern_curly_double = re.findall(r'\{\{([^}]+)\}\}', text)
    pattern_curly_single = re.findall(r'\{([^{}]+)\}', text)
    pattern_brackets_double = re.findall(r'\[\[([^\]]+)\]\]', text)
    pattern_brackets_single = re.findall(r'\[([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s_]+)\]', text)
    
    all_patterns = pattern_curly_double + pattern_curly_single + pattern_brackets_double + pattern_brackets_single
    for p in all_patterns:
        cleaned = p.strip().replace('_', ' ')
        if cleaned and len(cleaned) > 1:
            campos.append(cleaned)
    
    dot_pattern = r'([A-Za-zÁÉÍÓÚáéíóúÑñ\s\.\,\-°]+?)(?:N[°º]?\s*)?[\.…]{3,}|_{3,}'
    
    matches = list(re.finditer(dot_pattern, text))
    
    for match in matches:
        full_match = match.group(0)
        context = match.group(1) if match.group(1) else ""
        
        context = context.strip()
        context = re.sub(r'^[,\.\s]+', '', context)
        context = re.sub(r'[,\.\s]+$', '', context)
        
        if len(context) < 3:
            start = max(0, match.start() - 50)
            before_text = text[start:match.start()]
            words = re.findall(r'[A-Za-zÁÉÍÓÚáéíóúÑñ]+(?:\s+[A-Za-zÁÉÍÓÚáéíóúÑñ]+)*', before_text)
            if words:
                context = words[-1] if len(words[-1]) > 2 else ' '.join(words[-2:]) if len(words) > 1 else words[-1]
        
        if 'N°' in full_match or 'N.' in context or 'Nº' in full_match:
            if 'D.N.I' in context or 'DNI' in context:
                context = 'Número de DNI'
            elif 'celular' in context.lower() or 'teléfono' in context.lower():
                context = 'Número de celular'
            elif 'expediente' in context.lower():
                context = 'Número de expediente'
            else:
                context = 'Número'
        
        if context and len(context) >= 2:
            base_name = context[:100]
            if base_name.lower() in campo_counter:
                campo_counter[base_name.lower()] += 1
                campos.append(f"{base_name} {campo_counter[base_name.lower()]}")
            else:
                campo_counter[base_name.lower()] = 1
                campos.append(base_name)
    
    lines = text.split('\n')
    for line in lines:
        if ':' in line and re.search(r':\s*$', line.strip()):
            parts = line.split(':')
            if parts[0].strip() and len(parts[0].strip()) > 2:
                campo_name = parts[0].strip()
                if campo_name.lower() not in campo_counter:
                    campos.append(campo_name)
                    campo_counter[campo_name.lower()] = 1
    
    final_campos = []
    for c in campos:
        if c and len(c.strip()) > 1:
            final_campos.append(c.strip())
    
    return final_campos


def campo_to_key(campo_name):
    """Convert a campo name to a valid key."""
    key = campo_name.lower().strip()
    key = re.sub(r'[áàäâ]', 'a', key)
    key = re.sub(r'[éèëê]', 'e', key)
    key = re.sub(r'[íìïî]', 'i', key)
    key = re.sub(r'[óòöô]', 'o', key)
    key = re.sub(r'[úùüû]', 'u', key)
    key = re.sub(r'[ñ]', 'n', key)
    key = re.sub(r'[^a-z0-9]+', '_', key)
    key = re.sub(r'_+', '_', key)
    key = key.strip('_')
    return key[:50] if key else 'campo'


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
        if not current_user.is_super_admin() and not current_user.is_admin_estudio():
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
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template("landing.html")


@app.route("/dashboard")
@login_required
def dashboard():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    
    from datetime import timedelta
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    total_documentos = 0
    docs_este_mes = 0
    docs_semana = 0
    documentos_recientes = []
    
    if tenant_id:
        total_documentos = DocumentRecord.query.filter_by(tenant_id=tenant_id).count()
        docs_este_mes = DocumentRecord.query.filter(
            DocumentRecord.tenant_id == tenant_id,
            DocumentRecord.fecha >= month_ago
        ).count()
        docs_semana = DocumentRecord.query.filter(
            DocumentRecord.tenant_id == tenant_id,
            DocumentRecord.fecha >= week_ago
        ).count()
        documentos_recientes = DocumentRecord.query.filter_by(tenant_id=tenant_id).order_by(
            DocumentRecord.fecha.desc()
        ).limit(5).all()
    
    total_plantillas = len(MODELOS)
    estilos_disponibles = 0
    if tenant_id:
        total_plantillas += Plantilla.query.filter_by(tenant_id=tenant_id, activa=True).count()
        estilos_disponibles = Estilo.query.filter_by(tenant_id=tenant_id, activo=True).count()
    
    total_usuarios = 0
    usuarios_activos = 0
    if tenant_id:
        total_usuarios = User.query.filter_by(tenant_id=tenant_id).count()
        usuarios_activos = User.query.filter_by(tenant_id=tenant_id, activo=True).count()
    
    promedio_diario = round(docs_semana / 7, 1) if docs_semana > 0 else 0
    
    tipo_mas_usado = "-"
    if tenant_id and total_documentos > 0:
        result = db.session.query(
            DocumentRecord.tipo_documento, 
            db.func.count(DocumentRecord.id).label('count')
        ).filter_by(tenant_id=tenant_id).group_by(
            DocumentRecord.tipo_documento
        ).order_by(db.desc('count')).first()
        if result:
            tipo_mas_usado = result[0][:20] + "..." if len(result[0]) > 20 else result[0]
    
    casos_activos = 0
    casos_pendientes = 0
    tareas_pendientes = 0
    tareas_vencidas = 0
    casos_recientes = []
    tareas_urgentes = []
    
    if tenant_id:
        casos_activos = Case.query.filter(
            Case.tenant_id == tenant_id,
            Case.estado.in_(['en_proceso', 'en_espera'])
        ).count()
        casos_pendientes = Case.query.filter_by(tenant_id=tenant_id, estado='por_comenzar').count()
        tareas_pendientes = Task.query.filter_by(tenant_id=tenant_id, estado='pendiente').count()
        tareas_vencidas = Task.query.filter(
            Task.tenant_id == tenant_id,
            Task.estado.notin_(['completado', 'cancelado']),
            Task.fecha_vencimiento.isnot(None),
            Task.fecha_vencimiento < today
        ).count()
        casos_recientes = Case.query.filter_by(tenant_id=tenant_id).order_by(
            Case.updated_at.desc()
        ).limit(5).all()
        tareas_urgentes = Task.query.filter(
            Task.tenant_id == tenant_id,
            Task.estado.notin_(['completado', 'cancelado'])
        ).order_by(Task.fecha_vencimiento.asc().nullslast()).limit(5).all()
    
    stats = {
        'total_documentos': total_documentos,
        'docs_este_mes': docs_este_mes,
        'docs_semana': docs_semana,
        'casos_activos': casos_activos,
        'casos_pendientes': casos_pendientes,
        'tareas_pendientes': tareas_pendientes,
        'tareas_vencidas': tareas_vencidas,
        'total_plantillas': total_plantillas,
        'estilos_disponibles': estilos_disponibles,
        'total_usuarios': total_usuarios,
        'usuarios_activos': usuarios_activos,
        'promedio_diario': promedio_diario,
        'tipo_mas_usado': tipo_mas_usado
    }
    
    return render_template("dashboard.html", stats=stats, documentos_recientes=documentos_recientes, casos_recientes=casos_recientes, tareas_urgentes=tareas_urgentes)


@app.route("/generador")
@login_required
def generador():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    
    modelos_completos = dict(MODELOS)
    if tenant_id:
        plantillas_db = Plantilla.query.filter_by(tenant_id=tenant_id, activa=True).all()
        for p in plantillas_db:
            if p.key not in modelos_completos:
                modelos_completos[p.key] = {
                    "nombre": p.nombre,
                    "plantilla": f"{p.key}.txt",
                    "carpeta_estilos": p.carpeta_estilos or p.key
                }
    
    return render_template("generador.html", modelos=modelos_completos, tenant=tenant)


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
        
        if User.query.filter_by(username=username).first():
            flash("Ya existe un usuario con este nombre. Por favor elige otro.", "error")
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
    
    plantilla_db = None
    if tenant_id:
        plantilla_db = Plantilla.query.filter_by(key=tipo_documento, tenant_id=tenant_id, activa=True).first()
    
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
    
    if tenant_id:
        campos_dinamicos = CampoPlantilla.query.filter_by(plantilla_key=tipo_documento, tenant_id=tenant_id).order_by(CampoPlantilla.orden).all()
    else:
        campos_dinamicos = []
    
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
    
    plantilla_db = None
    if tenant_id:
        plantilla_db = Plantilla.query.filter_by(key=tipo_documento, tenant_id=tenant_id, activa=True).first()
    
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
    
    if tenant_id:
        campos_dinamicos = CampoPlantilla.query.filter_by(plantilla_key=tipo_documento, tenant_id=tenant_id).order_by(CampoPlantilla.orden).all()
    else:
        campos_dinamicos = []
    
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
    
    plantilla_db = None
    if tenant_id:
        plantilla_db = Plantilla.query.filter_by(key=tipo_documento, tenant_id=tenant_id, activa=True).first()
    
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
    tenant_id = tenant.id if tenant else None
    
    if not tenant_id:
        flash("Necesitas un contexto de estudio para editar documentos.", "error")
        return redirect(url_for("historial"))
    
    if record.tenant_id != tenant_id:
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
    
    if current_user.is_super_admin() and tenant_id:
        record = DocumentRecord.query.filter_by(archivo=safe_filename, tenant_id=tenant_id).first()
    elif current_user.is_super_admin() and not tenant_id:
        record = None
    elif current_user.is_admin and tenant_id:
        record = DocumentRecord.query.filter_by(archivo=safe_filename, tenant_id=tenant_id).first()
    else:
        record = DocumentRecord.query.filter_by(archivo=safe_filename, user_id=current_user.id, tenant_id=tenant_id).first()
    
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
    
    if current_user.is_super_admin() and tenant_id:
        query = DocumentRecord.query.filter_by(tenant_id=tenant_id)
    elif current_user.is_super_admin() and not tenant_id:
        query = DocumentRecord.query.filter(DocumentRecord.id < 0)
    elif current_user.is_admin and tenant_id:
        query = DocumentRecord.query.filter_by(tenant_id=tenant_id)
    else:
        query = DocumentRecord.query.filter_by(user_id=current_user.id, tenant_id=tenant_id)
    
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


@app.route("/admin/modelos")
@admin_estudio_required
def admin_modelos():
    """Admin view to see all models from all users in the tenant."""
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    todos_modelos = Modelo.query.filter_by(tenant_id=tenant.id).all()
    usuarios = {u.id: u for u in User.query.filter_by(tenant_id=tenant.id).all()}
    
    return render_template("admin_modelos.html", 
                          modelos=todos_modelos, 
                          usuarios=usuarios,
                          modelos_sistema=MODELOS,
                          tenant=tenant)


@app.route("/admin/estilos")
@admin_estudio_required
def admin_estilos():
    """Admin view to see all styles from all users in the tenant."""
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    todos_estilos = Estilo.query.filter_by(tenant_id=tenant.id).all()
    usuarios = {u.id: u for u in User.query.filter_by(tenant_id=tenant.id).all()}
    
    return render_template("admin_estilos.html", 
                          estilos=todos_estilos, 
                          usuarios=usuarios,
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


@app.route("/admin/convertir", methods=["GET", "POST"])
@admin_estudio_required
def convertir_documento():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    documento_convertido = None
    campos_detectados = 0
    
    if request.method == "POST":
        archivo = request.files.get('archivo')
        
        if not archivo or not archivo.filename or not archivo.filename.endswith('.docx'):
            flash("Debes subir un archivo Word (.docx).", "error")
            return render_template("convertir_documento.html")
        
        try:
            from docx import Document
            from docx.shared import Pt
            
            doc = Document(archivo)
            campo_num = 0
            
            dot_pattern = re.compile(r'[\.…]{4,}|_{4,}')
            
            for para in doc.paragraphs:
                text = para.text
                if dot_pattern.search(text):
                    new_text = text
                    for match in dot_pattern.finditer(text):
                        campo_num += 1
                        new_text = new_text.replace(match.group(), f'{{{{campo_{campo_num}}}}}', 1)
                    
                    for run in para.runs:
                        run.text = ""
                    if para.runs:
                        para.runs[0].text = new_text
                    else:
                        para.text = new_text
            
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            text = para.text
                            if dot_pattern.search(text):
                                new_text = text
                                for match in dot_pattern.finditer(text):
                                    campo_num += 1
                                    new_text = new_text.replace(match.group(), f'{{{{campo_{campo_num}}}}}', 1)
                                
                                for run in para.runs:
                                    run.text = ""
                                if para.runs:
                                    para.runs[0].text = new_text
                                else:
                                    para.text = new_text
            
            if campo_num == 0:
                flash("No se encontraron espacios con puntos o guiones para convertir.", "error")
                return render_template("convertir_documento.html")
            
            convertidos_folder = os.path.join("documentos_convertidos", f"tenant_{tenant.id}")
            os.makedirs(convertidos_folder, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            original_name = secure_filename(archivo.filename)
            output_name = f"convertido_{timestamp}_{original_name}"
            output_path = os.path.join(convertidos_folder, output_name)
            
            doc.save(output_path)
            
            campos_detectados = campo_num
            documento_convertido = output_name
            
            flash(f"Documento convertido exitosamente. Se reemplazaron {campo_num} campos.", "success")
            
        except Exception as e:
            logging.error(f"Error al convertir documento: {e}")
            flash("Error al procesar el documento. Verifica que sea un archivo Word válido.", "error")
    
    return render_template("convertir_documento.html", 
                         documento_convertido=documento_convertido,
                         campos_detectados=campos_detectados)


@app.route("/admin/convertir/descargar/<nombre_archivo>")
@admin_estudio_required
def descargar_convertido(nombre_archivo):
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    convertidos_folder = os.path.join("documentos_convertidos", f"tenant_{tenant.id}")
    file_path = os.path.join(convertidos_folder, secure_filename(nombre_archivo))
    
    if not os.path.exists(file_path):
        flash("El archivo no existe.", "error")
        return redirect(url_for("convertir_documento"))
    
    return send_file(file_path, as_attachment=True, download_name=nombre_archivo)


@app.route("/admin/plantilla", methods=["GET", "POST"])
@admin_estudio_required
def admin_plantilla():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    plantilla_id = request.args.get('id', type=int)
    plantilla = Plantilla.query.filter_by(id=plantilla_id, tenant_id=tenant.id).first() if plantilla_id else None
    campos_detectados = []
    
    if plantilla_id and not plantilla:
        flash("No tienes permiso para editar esta plantilla.", "error")
        return redirect(url_for("admin"))
    
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        nombre = request.form.get("nombre", "").strip()
        
        archivo = request.files.get('archivo_word')
        contenido = ""
        archivo_path = None
        
        if archivo and archivo.filename and archivo.filename.endswith('.docx'):
            tenant_folder = os.path.join(CARPETA_PLANTILLAS_SUBIDAS, f"tenant_{tenant.id}")
            os.makedirs(tenant_folder, exist_ok=True)
            
            safe_name = secure_filename(archivo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            archivo_name = f"{timestamp}_{safe_name}"
            archivo_path = os.path.join(tenant_folder, archivo_name)
            archivo.save(archivo_path)
            
            contenido = extract_text_from_docx(archivo_path)
            campos_detectados = detect_placeholders_from_text(contenido)
        elif plantilla:
            contenido = plantilla.contenido
        
        if not key or not nombre:
            flash("La clave y el nombre son obligatorios.", "error")
            return render_template("admin_plantilla.html", plantilla=plantilla, campos_detectados=campos_detectados)
        
        if not contenido and not plantilla:
            flash("Debes subir un archivo Word con la plantilla.", "error")
            return render_template("admin_plantilla.html", plantilla=plantilla, campos_detectados=campos_detectados)
        
        if archivo_path and contenido and len(contenido.strip()) < 50:
            flash("El documento Word parece estar vacío o tiene muy poco contenido.", "error")
            if archivo_path and os.path.exists(archivo_path):
                os.remove(archivo_path)
            return render_template("admin_plantilla.html", plantilla=plantilla, campos_detectados=campos_detectados)
        
        if plantilla:
            plantilla.key = key
            plantilla.nombre = nombre
            if contenido:
                plantilla.contenido = contenido
            if archivo_path:
                plantilla.archivo_original = archivo_path
                nuevos_campos = 0
                if campos_detectados:
                    for i, campo_name in enumerate(campos_detectados):
                        campo_key = campo_to_key(campo_name)
                        existing_campo = CampoPlantilla.query.filter_by(
                            plantilla_key=key, 
                            nombre_campo=campo_key, 
                            tenant_id=tenant.id
                        ).first()
                        if not existing_campo:
                            max_orden = db.session.query(db.func.max(CampoPlantilla.orden)).filter_by(
                                plantilla_key=key, tenant_id=tenant.id
                            ).scalar() or 0
                            campo = CampoPlantilla(
                                plantilla_key=key,
                                nombre_campo=campo_key[:100],
                                etiqueta=campo_name[:200] if len(campo_name) <= 200 else campo_name[:197] + "...",
                                tipo='text',
                                requerido=True,
                                orden=max_orden + i + 1,
                                tenant_id=tenant.id
                            )
                            db.session.add(campo)
                            nuevos_campos += 1
                if nuevos_campos > 0:
                    flash(f"Plantilla actualizada. Se detectaron {nuevos_campos} campos nuevos.", "success")
                else:
                    flash("Plantilla actualizada exitosamente.", "success")
            else:
                flash("Plantilla actualizada exitosamente.", "success")
        else:
            existing = Plantilla.query.filter_by(key=key, tenant_id=tenant.id).first()
            if existing:
                flash("Ya existe una plantilla con esta clave.", "error")
                return render_template("admin_plantilla.html", plantilla=plantilla, campos_detectados=campos_detectados)
            
            plantilla = Plantilla(
                key=key, 
                nombre=nombre, 
                contenido=contenido, 
                archivo_original=archivo_path,
                carpeta_estilos=key,
                tenant_id=tenant.id
            )
            db.session.add(plantilla)
            db.session.flush()
            
            if campos_detectados:
                for i, campo_name in enumerate(campos_detectados):
                    campo_key = campo_to_key(campo_name)
                    existing_campo = CampoPlantilla.query.filter_by(
                        plantilla_key=key, 
                        nombre_campo=campo_key, 
                        tenant_id=tenant.id
                    ).first()
                    if not existing_campo:
                        campo = CampoPlantilla(
                            plantilla_key=key,
                            nombre_campo=campo_key[:100],
                            etiqueta=campo_name[:200] if len(campo_name) <= 200 else campo_name[:197] + "...",
                            tipo='text',
                            requerido=True,
                            orden=i,
                            tenant_id=tenant.id
                        )
                        db.session.add(campo)
            
            flash(f"Plantilla creada exitosamente. Se detectaron {len(campos_detectados)} campos.", "success")
        
        db.session.commit()
        
        if campos_detectados and not plantilla_id:
            return redirect(url_for("admin_campos", plantilla_key=key))
        return redirect(url_for("admin"))
    
    return render_template("admin_plantilla.html", plantilla=plantilla, campos_detectados=campos_detectados)


@app.route("/admin/plantilla/eliminar/<int:plantilla_id>", methods=["POST"])
@admin_estudio_required
def eliminar_plantilla(plantilla_id):
    tenant = get_current_tenant()
    plantilla = Plantilla.query.filter_by(id=plantilla_id, tenant_id=tenant.id).first()
    
    if not plantilla:
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
    estilo = Estilo.query.filter_by(id=estilo_id, tenant_id=tenant.id).first() if estilo_id else None
    
    if estilo_id and not estilo:
        flash("No tienes permiso para editar este estilo.", "error")
        return redirect(url_for("admin"))
    
    plantillas_db = Plantilla.query.filter_by(tenant_id=tenant.id).all()
    plantillas_keys = list(MODELOS.keys()) + [p.key for p in plantillas_db]
    plantillas_keys = list(set(plantillas_keys))
    
    if request.method == "POST":
        plantilla_key = request.form.get("plantilla_key", "").strip()
        nombre = request.form.get("nombre", "").strip()
        
        archivo = request.files.get('archivo_word')
        contenido = ""
        archivo_path = None
        
        if archivo and archivo.filename and archivo.filename.endswith('.docx'):
            tenant_folder = os.path.join(CARPETA_ESTILOS_SUBIDOS, f"tenant_{tenant.id}")
            os.makedirs(tenant_folder, exist_ok=True)
            
            safe_name = secure_filename(archivo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            archivo_name = f"{timestamp}_{safe_name}"
            archivo_path = os.path.join(tenant_folder, archivo_name)
            archivo.save(archivo_path)
            
            contenido = extract_text_from_docx(archivo_path)
        elif estilo:
            contenido = estilo.contenido
        
        if not plantilla_key or not nombre:
            flash("La plantilla asociada y el nombre son obligatorios.", "error")
            return render_template("admin_estilo.html", estilo=estilo, plantillas_keys=plantillas_keys)
        
        if not contenido and not estilo:
            flash("Debes subir un archivo Word con el ejemplo de estilo.", "error")
            return render_template("admin_estilo.html", estilo=estilo, plantillas_keys=plantillas_keys)
        
        if archivo_path and contenido and len(contenido.strip()) < 50:
            flash("El documento Word parece estar vacío o tiene muy poco contenido.", "error")
            if archivo_path and os.path.exists(archivo_path):
                os.remove(archivo_path)
            return render_template("admin_estilo.html", estilo=estilo, plantillas_keys=plantillas_keys)
        
        if estilo:
            estilo.plantilla_key = plantilla_key
            estilo.nombre = nombre
            if contenido:
                estilo.contenido = contenido
            if archivo_path:
                estilo.archivo_original = archivo_path
            flash("Estilo actualizado exitosamente.", "success")
        else:
            estilo = Estilo(
                plantilla_key=plantilla_key, 
                nombre=nombre, 
                contenido=contenido,
                archivo_original=archivo_path,
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
    estilo = Estilo.query.filter_by(id=estilo_id, tenant_id=tenant.id).first()
    
    if not estilo:
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
        campos = []
    
    return jsonify([{
        'id': c.id,
        'nombre_campo': c.nombre_campo,
        'etiqueta': c.etiqueta,
        'tipo': c.tipo,
        'requerido': c.requerido,
        'placeholder': c.placeholder or '',
        'opciones': c.opciones.split(',') if c.opciones else []
    } for c in campos])


# ==================== GESTIÓN DE CASOS ====================

def case_access_required(f):
    """Decorator to ensure user can access case management."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        tenant = get_current_tenant()
        if not tenant:
            flash("No tienes un estudio asociado.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


def case_manage_required(f):
    """Decorator to ensure user can manage cases (admin/coordinador)."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.can_manage_cases():
            flash("No tienes permisos para gestionar casos.", "error")
            return redirect(url_for("casos"))
        tenant = get_current_tenant()
        if not tenant:
            flash("No tienes un estudio asociado.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/casos")
@case_access_required
def casos():
    tenant = get_current_tenant()
    
    estado_filter = request.args.get('estado', '')
    prioridad_filter = request.args.get('prioridad', '')
    busqueda = request.args.get('busqueda', '').strip()
    
    query = Case.query.filter_by(tenant_id=tenant.id)
    
    if not current_user.can_manage_cases():
        assigned_case_ids = db.session.query(CaseAssignment.case_id).filter_by(user_id=current_user.id).subquery()
        query = query.filter(
            db.or_(
                Case.created_by_id == current_user.id,
                Case.id.in_(assigned_case_ids)
            )
        )
    
    if estado_filter:
        query = query.filter_by(estado=estado_filter)
    if prioridad_filter:
        query = query.filter_by(prioridad=prioridad_filter)
    if busqueda:
        query = query.filter(
            db.or_(
                Case.titulo.ilike(f'%{busqueda}%'),
                Case.cliente_nombre.ilike(f'%{busqueda}%'),
                Case.numero_expediente.ilike(f'%{busqueda}%')
            )
        )
    
    casos_list = query.order_by(Case.updated_at.desc()).all()
    
    stats = {
        'total': Case.query.filter_by(tenant_id=tenant.id).count(),
        'por_comenzar': Case.query.filter_by(tenant_id=tenant.id, estado='por_comenzar').count(),
        'en_proceso': Case.query.filter_by(tenant_id=tenant.id, estado='en_proceso').count(),
        'en_espera': Case.query.filter_by(tenant_id=tenant.id, estado='en_espera').count(),
        'terminado': Case.query.filter_by(tenant_id=tenant.id, estado='terminado').count(),
    }
    
    return render_template("casos.html", 
                          casos=casos_list,
                          stats=stats,
                          estado_filter=estado_filter,
                          prioridad_filter=prioridad_filter,
                          busqueda=busqueda,
                          estados=Case.ESTADOS,
                          prioridades=Case.PRIORIDADES)


@app.route("/casos/nuevo", methods=["GET", "POST"])
@case_manage_required
def caso_nuevo():
    tenant = get_current_tenant()
    
    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        cliente_nombre = request.form.get("cliente_nombre", "").strip()
        
        if not titulo or not cliente_nombre:
            flash("El título y nombre del cliente son obligatorios.", "error")
            return render_template("caso_form.html", caso=None, usuarios=User.query.filter_by(tenant_id=tenant.id, activo=True).all())
        
        caso = Case(
            tenant_id=tenant.id,
            titulo=titulo,
            descripcion=request.form.get("descripcion", "").strip(),
            numero_expediente=request.form.get("numero_expediente", "").strip(),
            cliente_nombre=cliente_nombre,
            cliente_email=request.form.get("cliente_email", "").strip(),
            cliente_telefono=request.form.get("cliente_telefono", "").strip(),
            contraparte_nombre=request.form.get("contraparte_nombre", "").strip(),
            tipo_caso=request.form.get("tipo_caso", "").strip(),
            juzgado=request.form.get("juzgado", "").strip(),
            estado=request.form.get("estado", "por_comenzar"),
            prioridad=request.form.get("prioridad", "media"),
            notas=request.form.get("notas", "").strip(),
            created_by_id=current_user.id
        )
        
        fecha_limite = request.form.get("fecha_limite", "").strip()
        if fecha_limite:
            try:
                caso.fecha_limite = datetime.strptime(fecha_limite, "%Y-%m-%d")
            except ValueError:
                pass
        
        db.session.add(caso)
        db.session.flush()
        
        responsable_id = request.form.get("responsable_id", type=int)
        if responsable_id:
            assignment = CaseAssignment(
                case_id=caso.id,
                user_id=responsable_id,
                rol_en_caso='abogado',
                es_responsable=True
            )
            db.session.add(assignment)
        
        db.session.commit()
        flash("Caso creado exitosamente.", "success")
        return redirect(url_for("caso_detalle", caso_id=caso.id))
    
    usuarios = User.query.filter_by(tenant_id=tenant.id, activo=True).all()
    return render_template("caso_form.html", caso=None, usuarios=usuarios, estados=Case.ESTADOS, prioridades=Case.PRIORIDADES)


@app.route("/casos/<int:caso_id>")
@case_access_required
def caso_detalle(caso_id):
    tenant = get_current_tenant()
    caso = Case.query.filter_by(id=caso_id, tenant_id=tenant.id).first_or_404()
    
    if not current_user.can_manage_cases():
        is_assigned = CaseAssignment.query.filter_by(case_id=caso_id, user_id=current_user.id).first()
        if caso.created_by_id != current_user.id and not is_assigned:
            flash("No tienes acceso a este caso.", "error")
            return redirect(url_for("casos"))
    
    assignments = CaseAssignment.query.filter_by(case_id=caso_id).all()
    case_documents = CaseDocument.query.filter_by(case_id=caso_id).order_by(CaseDocument.created_at.desc()).all()
    tasks = Task.query.filter_by(case_id=caso_id).order_by(Task.fecha_vencimiento).all()
    
    return render_template("caso_detalle.html",
                          caso=caso,
                          assignments=assignments,
                          case_documents=case_documents,
                          tasks=tasks,
                          estados=Case.ESTADOS,
                          prioridades=Case.PRIORIDADES)


@app.route("/casos/<int:caso_id>/editar", methods=["GET", "POST"])
@case_manage_required
def caso_editar(caso_id):
    tenant = get_current_tenant()
    caso = Case.query.filter_by(id=caso_id, tenant_id=tenant.id).first_or_404()
    
    if request.method == "POST":
        caso.titulo = request.form.get("titulo", "").strip()
        caso.descripcion = request.form.get("descripcion", "").strip()
        caso.numero_expediente = request.form.get("numero_expediente", "").strip()
        caso.cliente_nombre = request.form.get("cliente_nombre", "").strip()
        caso.cliente_email = request.form.get("cliente_email", "").strip()
        caso.cliente_telefono = request.form.get("cliente_telefono", "").strip()
        caso.contraparte_nombre = request.form.get("contraparte_nombre", "").strip()
        caso.tipo_caso = request.form.get("tipo_caso", "").strip()
        caso.juzgado = request.form.get("juzgado", "").strip()
        caso.estado = request.form.get("estado", "por_comenzar")
        caso.prioridad = request.form.get("prioridad", "media")
        caso.notas = request.form.get("notas", "").strip()
        
        fecha_limite = request.form.get("fecha_limite", "").strip()
        if fecha_limite:
            try:
                caso.fecha_limite = datetime.strptime(fecha_limite, "%Y-%m-%d")
            except ValueError:
                pass
        else:
            caso.fecha_limite = None
        
        if caso.estado == 'terminado' and not caso.fecha_cierre:
            caso.fecha_cierre = datetime.utcnow()
        elif caso.estado != 'terminado':
            caso.fecha_cierre = None
        
        db.session.commit()
        flash("Caso actualizado exitosamente.", "success")
        return redirect(url_for("caso_detalle", caso_id=caso.id))
    
    usuarios = User.query.filter_by(tenant_id=tenant.id, activo=True).all()
    return render_template("caso_form.html", caso=caso, usuarios=usuarios, estados=Case.ESTADOS, prioridades=Case.PRIORIDADES)


@app.route("/casos/<int:caso_id>/asignar", methods=["POST"])
@case_manage_required
def caso_asignar(caso_id):
    tenant = get_current_tenant()
    caso = Case.query.filter_by(id=caso_id, tenant_id=tenant.id).first_or_404()
    
    user_id = request.form.get("user_id", type=int)
    rol = request.form.get("rol", "abogado")
    es_responsable = request.form.get("es_responsable") == "on"
    
    if not user_id:
        flash("Selecciona un usuario.", "error")
        return redirect(url_for("caso_detalle", caso_id=caso_id))
    
    existing = CaseAssignment.query.filter_by(case_id=caso_id, user_id=user_id).first()
    if existing:
        flash("Este usuario ya está asignado al caso.", "error")
        return redirect(url_for("caso_detalle", caso_id=caso_id))
    
    if es_responsable:
        CaseAssignment.query.filter_by(case_id=caso_id, es_responsable=True).update({'es_responsable': False})
    
    assignment = CaseAssignment(
        case_id=caso_id,
        user_id=user_id,
        rol_en_caso=rol,
        es_responsable=es_responsable
    )
    db.session.add(assignment)
    db.session.commit()
    
    flash("Usuario asignado exitosamente.", "success")
    return redirect(url_for("caso_detalle", caso_id=caso_id))


@app.route("/casos/<int:caso_id>/desasignar/<int:assignment_id>", methods=["POST"])
@case_manage_required
def caso_desasignar(caso_id, assignment_id):
    tenant = get_current_tenant()
    caso = Case.query.filter_by(id=caso_id, tenant_id=tenant.id).first_or_404()
    
    assignment = CaseAssignment.query.filter_by(id=assignment_id, case_id=caso_id).first_or_404()
    db.session.delete(assignment)
    db.session.commit()
    
    flash("Usuario removido del caso.", "success")
    return redirect(url_for("caso_detalle", caso_id=caso_id))


@app.route("/casos/<int:caso_id>/estado", methods=["POST"])
@case_access_required
def caso_cambiar_estado(caso_id):
    tenant = get_current_tenant()
    caso = Case.query.filter_by(id=caso_id, tenant_id=tenant.id).first_or_404()
    
    if not current_user.can_manage_cases():
        is_assigned = CaseAssignment.query.filter_by(case_id=caso_id, user_id=current_user.id).first()
        if caso.created_by_id != current_user.id and not is_assigned:
            flash("No tienes permiso para modificar este caso.", "error")
            return redirect(url_for("casos"))
    
    nuevo_estado = request.form.get("estado")
    if nuevo_estado in Case.ESTADOS:
        caso.estado = nuevo_estado
        if nuevo_estado == 'terminado':
            caso.fecha_cierre = datetime.utcnow()
        else:
            caso.fecha_cierre = None
        db.session.commit()
        flash(f"Estado actualizado a: {Case.ESTADOS[nuevo_estado]}", "success")
    
    return redirect(url_for("caso_detalle", caso_id=caso_id))


# ==================== GESTIÓN DE TAREAS ====================

@app.route("/tareas")
@case_access_required
def tareas():
    tenant = get_current_tenant()
    
    estado_filter = request.args.get('estado', '')
    tipo_filter = request.args.get('tipo', '')
    mis_tareas = request.args.get('mis_tareas', '')
    
    query = Task.query.filter_by(tenant_id=tenant.id)
    
    if mis_tareas or not current_user.can_manage_cases():
        query = query.filter(
            db.or_(
                Task.assigned_to_id == current_user.id,
                Task.created_by_id == current_user.id
            )
        )
    
    if estado_filter:
        query = query.filter_by(estado=estado_filter)
    if tipo_filter:
        query = query.filter_by(tipo=tipo_filter)
    
    tareas_list = query.order_by(
        db.case(
            (Task.estado == 'pendiente', 1),
            (Task.estado == 'en_curso', 2),
            (Task.estado == 'bloqueado', 3),
            else_=4
        ),
        Task.fecha_vencimiento.asc().nullslast()
    ).all()
    
    tareas_pendientes = Task.query.filter_by(tenant_id=tenant.id, estado='pendiente').count()
    tareas_vencidas = Task.query.filter(
        Task.tenant_id == tenant.id,
        Task.estado.notin_(['completado', 'cancelado']),
        Task.fecha_vencimiento.isnot(None),
        Task.fecha_vencimiento < datetime.utcnow()
    ).count()
    
    return render_template("tareas.html",
                          tareas=tareas_list,
                          tareas_pendientes=tareas_pendientes,
                          tareas_vencidas=tareas_vencidas,
                          estado_filter=estado_filter,
                          tipo_filter=tipo_filter,
                          mis_tareas=mis_tareas,
                          estados=Task.ESTADOS,
                          tipos=Task.TIPOS)


@app.route("/tareas/nueva", methods=["GET", "POST"])
@case_access_required
def tarea_nueva():
    tenant = get_current_tenant()
    
    if request.method == "POST":
        titulo = request.form.get("titulo", "").strip()
        if not titulo:
            flash("El título es obligatorio.", "error")
            return redirect(url_for("tarea_nueva"))
        
        tarea = Task(
            tenant_id=tenant.id,
            titulo=titulo,
            descripcion=request.form.get("descripcion", "").strip(),
            tipo=request.form.get("tipo", "general"),
            prioridad=request.form.get("prioridad", "media"),
            created_by_id=current_user.id
        )
        
        case_id = request.form.get("case_id", type=int)
        if case_id:
            caso = Case.query.filter_by(id=case_id, tenant_id=tenant.id).first()
            if caso:
                tarea.case_id = case_id
        
        assigned_to_id = request.form.get("assigned_to_id", type=int)
        if assigned_to_id:
            user = User.query.filter_by(id=assigned_to_id, tenant_id=tenant.id, activo=True).first()
            if user:
                tarea.assigned_to_id = assigned_to_id
        
        fecha_vencimiento = request.form.get("fecha_vencimiento", "").strip()
        if fecha_vencimiento:
            try:
                tarea.fecha_vencimiento = datetime.strptime(fecha_vencimiento, "%Y-%m-%d")
            except ValueError:
                pass
        
        db.session.add(tarea)
        db.session.commit()
        
        flash("Tarea creada exitosamente.", "success")
        return redirect(url_for("tareas"))
    
    casos = Case.query.filter_by(tenant_id=tenant.id).filter(Case.estado.notin_(['terminado', 'archivado'])).all()
    usuarios = User.query.filter_by(tenant_id=tenant.id, activo=True).all()
    preselected_case = request.args.get('caso_id', type=int)
    
    return render_template("tarea_form.html",
                          tarea=None,
                          casos=casos,
                          usuarios=usuarios,
                          preselected_case=preselected_case,
                          tipos=Task.TIPOS,
                          prioridades=Task.PRIORIDADES)


@app.route("/tareas/<int:tarea_id>/estado", methods=["POST"])
@case_access_required
def tarea_cambiar_estado(tarea_id):
    tenant = get_current_tenant()
    tarea = Task.query.filter_by(id=tarea_id, tenant_id=tenant.id).first_or_404()
    
    if tarea.assigned_to_id != current_user.id and tarea.created_by_id != current_user.id and not current_user.can_manage_cases():
        flash("No tienes permiso para modificar esta tarea.", "error")
        return redirect(url_for("tareas"))
    
    nuevo_estado = request.form.get("estado")
    if nuevo_estado in Task.ESTADOS:
        tarea.estado = nuevo_estado
        if nuevo_estado == 'completado':
            tarea.fecha_completada = datetime.utcnow()
        else:
            tarea.fecha_completada = None
        db.session.commit()
        flash(f"Tarea actualizada: {Task.ESTADOS[nuevo_estado]}", "success")
    
    next_url = request.form.get("next", url_for("tareas"))
    return redirect(next_url)


# ==================== MIS MODELOS (User personal document models) ====================

@app.route("/mis-modelos")
@login_required
def mis_modelos():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    user_id = current_user.id
    
    modelos_usuario = Modelo.query.filter_by(tenant_id=tenant_id, created_by_id=user_id).all()
    estilos_usuario = Estilo.query.filter_by(tenant_id=tenant_id, created_by_id=user_id).all()
    
    return render_template("mis_modelos.html", modelos_usuario=modelos_usuario, estilos_usuario=estilos_usuario, modelos_sistema=MODELOS)


@app.route("/mi-modelo", methods=["GET", "POST"])
@login_required
def mi_modelo():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("dashboard"))
    
    modelo_id = request.args.get('id', type=int)
    modelo = Modelo.query.filter_by(id=modelo_id, tenant_id=tenant.id, created_by_id=current_user.id).first() if modelo_id else None
    campos_detectados = []
    
    if request.method == "POST":
        key = request.form.get("key", "").strip()
        nombre = request.form.get("nombre", "").strip()
        
        archivo = request.files.get('archivo_word')
        contenido = ""
        archivo_path = None
        
        if archivo and archivo.filename:
            ext = os.path.splitext(archivo.filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                flash(f"Formato de archivo no soportado ({ext}). Use .docx, .pdf o .txt", "error")
                return render_template("mi_modelo.html", modelo=modelo, campos_detectados=campos_detectados)
            
            user_folder = os.path.join(CARPETA_PLANTILLAS_SUBIDAS, f"user_{current_user.id}")
            os.makedirs(user_folder, exist_ok=True)
            
            safe_name = secure_filename(archivo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            archivo_name = f"{timestamp}_{safe_name}"
            archivo_path = os.path.join(user_folder, archivo_name)
            archivo.save(archivo_path)
            
            contenido = extract_text_from_file(archivo_path)
            if not contenido:
                flash("No se pudo extraer texto del archivo. Verifique que el archivo contenga texto.", "error")
                return render_template("mi_modelo.html", modelo=modelo, campos_detectados=campos_detectados)
            campos_detectados = detect_placeholders_from_text(contenido)
        if not contenido and modelo:
            contenido = modelo.contenido
        
        if not key or not nombre:
            flash("La clave y el nombre son obligatorios.", "error")
            return render_template("mi_modelo.html", modelo=modelo, campos_detectados=campos_detectados)
        
        if not contenido and not modelo:
            flash("Debes subir un archivo Word con el modelo.", "error")
            return render_template("mi_modelo.html", modelo=modelo, campos_detectados=campos_detectados)
        
        if modelo:
            modelo.key = key
            modelo.nombre = nombre
            if contenido:
                modelo.contenido = contenido
            if archivo_path:
                modelo.archivo_original = archivo_path
            flash("Modelo actualizado exitosamente.", "success")
        else:
            modelo = Modelo(
                key=f"user_{current_user.id}_{key}",
                nombre=nombre,
                contenido=contenido,
                archivo_original=archivo_path,
                carpeta_estilos=key,
                tenant_id=tenant.id,
                created_by_id=current_user.id
            )
            db.session.add(modelo)
            flash("Modelo creado exitosamente.", "success")
        
        db.session.commit()
        return redirect(url_for("mis_modelos"))
    
    return render_template("mi_modelo.html", modelo=modelo, campos_detectados=campos_detectados)


@app.route("/mi-modelo/eliminar/<int:modelo_id>", methods=["POST"])
@login_required
def eliminar_mi_modelo(modelo_id):
    tenant = get_current_tenant()
    modelo = Modelo.query.filter_by(id=modelo_id, tenant_id=tenant.id, created_by_id=current_user.id).first()
    
    if not modelo:
        flash("No tienes permiso para eliminar este modelo.", "error")
        return redirect(url_for("mis_modelos"))
    
    db.session.delete(modelo)
    db.session.commit()
    flash("Modelo eliminado exitosamente.", "success")
    return redirect(url_for("mis_modelos"))


# ==================== MIS ESTILOS (User personal styles) ====================

@app.route("/mis-estilos")
@login_required
def mis_estilos():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    user_id = current_user.id
    
    estilos_usuario = Estilo.query.filter_by(tenant_id=tenant_id, created_by_id=user_id).all()
    modelos_usuario = Modelo.query.filter_by(tenant_id=tenant_id, created_by_id=user_id).all()
    
    return render_template("mis_estilos.html", estilos_usuario=estilos_usuario, modelos_usuario=modelos_usuario)


@app.route("/mi-estilo", methods=["GET", "POST"])
@login_required
def mi_estilo():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("dashboard"))
    
    estilo_id = request.args.get('id', type=int)
    estilo = Estilo.query.filter_by(id=estilo_id, tenant_id=tenant.id, created_by_id=current_user.id).first() if estilo_id else None
    modelos_usuario = Modelo.query.filter_by(tenant_id=tenant.id, created_by_id=current_user.id).all()
    
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        plantilla_key = request.form.get("plantilla_key", "").strip()
        
        archivo = request.files.get('archivo_word')
        contenido = ""
        archivo_path = None
        
        if archivo and archivo.filename:
            ext = os.path.splitext(archivo.filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                flash(f"Formato de archivo no soportado ({ext}). Use .docx, .pdf o .txt", "error")
                return render_template("mi_estilo.html", estilo=estilo, modelos_usuario=modelos_usuario)
            
            user_folder = os.path.join(CARPETA_ESTILOS_SUBIDOS, f"user_{current_user.id}")
            os.makedirs(user_folder, exist_ok=True)
            
            safe_name = secure_filename(archivo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            archivo_name = f"{timestamp}_{safe_name}"
            archivo_path = os.path.join(user_folder, archivo_name)
            archivo.save(archivo_path)
            
            contenido = extract_text_from_file(archivo_path)
            if not contenido:
                flash("No se pudo extraer texto del archivo. Verifique que el archivo contenga texto.", "error")
                return render_template("mi_estilo.html", estilo=estilo, modelos_usuario=modelos_usuario)
        if not contenido and estilo:
            contenido = estilo.contenido
        
        if not nombre:
            flash("El nombre es obligatorio.", "error")
            return render_template("mi_estilo.html", estilo=estilo, modelos_usuario=modelos_usuario)
        
        if not contenido and not estilo:
            flash("Debes subir un archivo Word con el estilo.", "error")
            return render_template("mi_estilo.html", estilo=estilo, modelos_usuario=modelos_usuario)
        
        if estilo:
            estilo.nombre = nombre
            estilo.plantilla_key = plantilla_key
            if contenido:
                estilo.contenido = contenido
            if archivo_path:
                estilo.archivo_original = archivo_path
            flash("Estilo actualizado exitosamente.", "success")
        else:
            estilo = Estilo(
                nombre=nombre,
                plantilla_key=plantilla_key,
                contenido=contenido,
                archivo_original=archivo_path,
                tenant_id=tenant.id,
                created_by_id=current_user.id
            )
            db.session.add(estilo)
            flash("Estilo creado exitosamente.", "success")
        
        db.session.commit()
        return redirect(url_for("mis_estilos"))
    
    return render_template("mi_estilo.html", estilo=estilo, modelos_usuario=modelos_usuario)


@app.route("/mi-estilo/eliminar/<int:estilo_id>", methods=["POST"])
@login_required
def eliminar_mi_estilo(estilo_id):
    tenant = get_current_tenant()
    estilo = Estilo.query.filter_by(id=estilo_id, tenant_id=tenant.id, created_by_id=current_user.id).first()
    
    if not estilo:
        flash("No tienes permiso para eliminar este estilo.", "error")
        return redirect(url_for("mis_estilos"))
    
    db.session.delete(estilo)
    db.session.commit()
    flash("Estilo eliminado exitosamente.", "success")
    return redirect(url_for("mis_estilos"))


# ==================== ESTADISTICAS INDIVIDUALES ====================

@app.route("/mis-estadisticas")
@login_required
def mis_estadisticas():
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    user_id = current_user.id
    
    from datetime import timedelta
    fecha_inicio_str = request.args.get('fecha_inicio')
    fecha_fin_str = request.args.get('fecha_fin')
    today = datetime.now()
    
    if fecha_inicio_str:
        try:
            fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d')
        except:
            fecha_inicio = today - timedelta(days=30)
    else:
        fecha_inicio = today - timedelta(days=30)
    
    if fecha_fin_str:
        try:
            fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d')
        except:
            fecha_fin = today
    else:
        fecha_fin = today
    
    assigned_case_ids = db.session.query(CaseAssignment.case_id).filter_by(user_id=user_id).subquery()
    casos_query = Case.query.filter(Case.id.in_(assigned_case_ids))
    
    casos_por_comenzar = casos_query.filter_by(estado='por_comenzar').count()
    casos_en_proceso = casos_query.filter(Case.estado.in_(['en_proceso', 'en_espera'])).count()
    casos_terminados = casos_query.filter_by(estado='terminado').count()
    total_casos = casos_query.count()
    
    docs_query = DocumentRecord.query.filter_by(user_id=user_id, tenant_id=tenant_id)
    total_docs = docs_query.count()
    docs_periodo = docs_query.filter(DocumentRecord.fecha >= fecha_inicio, DocumentRecord.fecha <= fecha_fin).count()
    
    tareas_completadas = Task.query.filter_by(assigned_to_id=user_id, estado='completado').count()
    tareas_pendientes = Task.query.filter_by(assigned_to_id=user_id, estado='pendiente').count()
    tareas_en_curso = Task.query.filter_by(assigned_to_id=user_id, estado='en_curso').count()
    tareas_vencidas = Task.query.filter(
        Task.assigned_to_id == user_id,
        Task.estado.notin_(['completado', 'cancelado']),
        Task.fecha_vencimiento.isnot(None),
        Task.fecha_vencimiento < today
    ).count()
    
    total_tareas_con_fecha = Task.query.filter(
        Task.assigned_to_id == user_id,
        Task.fecha_vencimiento.isnot(None),
        Task.estado == 'completado'
    ).count()
    tareas_a_tiempo = Task.query.filter(
        Task.assigned_to_id == user_id,
        Task.estado == 'completado',
        Task.fecha_completada.isnot(None),
        Task.fecha_vencimiento.isnot(None),
        Task.fecha_completada <= Task.fecha_vencimiento
    ).count()
    cumplimiento = round((tareas_a_tiempo / total_tareas_con_fecha) * 100, 1) if total_tareas_con_fecha > 0 else 100
    
    docs_recientes = docs_query.order_by(DocumentRecord.fecha.desc()).limit(10).all()
    tareas_recientes = Task.query.filter_by(assigned_to_id=user_id).order_by(Task.updated_at.desc()).limit(10).all()
    
    stats = {
        'casos_por_comenzar': casos_por_comenzar,
        'casos_en_proceso': casos_en_proceso,
        'casos_terminados': casos_terminados,
        'total_casos': total_casos,
        'total_docs': total_docs,
        'docs_periodo': docs_periodo,
        'tareas_completadas': tareas_completadas,
        'tareas_pendientes': tareas_pendientes,
        'tareas_en_curso': tareas_en_curso,
        'tareas_vencidas': tareas_vencidas,
        'cumplimiento': cumplimiento
    }
    
    return render_template("mis_estadisticas.html",
                          stats=stats,
                          docs_recientes=docs_recientes,
                          tareas_recientes=tareas_recientes,
                          fecha_inicio=fecha_inicio.strftime('%Y-%m-%d'),
                          fecha_fin=fecha_fin.strftime('%Y-%m-%d'))


# ==================== ESTADISTICAS EQUIPO (Admin/Coordinador) ====================

@app.route("/estadisticas-equipo")
@login_required
def estadisticas_equipo():
    if not current_user.can_manage_cases() and not current_user.is_admin_estudio():
        flash("No tienes acceso a esta seccion.", "error")
        return redirect(url_for("dashboard"))
    
    tenant = get_current_tenant()
    tenant_id = tenant.id if tenant else None
    
    from datetime import timedelta
    today = datetime.now()
    month_ago = today - timedelta(days=30)
    
    usuarios = User.query.filter_by(tenant_id=tenant_id, activo=True).all()
    
    user_stats = []
    for usuario in usuarios:
        assigned_ids = [a.case_id for a in usuario.case_assignments.all()]
        casos_total = len(assigned_ids)
        casos_activos = Case.query.filter(Case.id.in_(assigned_ids), Case.estado.in_(['en_proceso', 'en_espera'])).count() if assigned_ids else 0
        
        docs_total = DocumentRecord.query.filter_by(user_id=usuario.id, tenant_id=tenant_id).count()
        docs_mes = DocumentRecord.query.filter(
            DocumentRecord.user_id == usuario.id,
            DocumentRecord.tenant_id == tenant_id,
            DocumentRecord.fecha >= month_ago
        ).count()
        
        tareas_completadas = Task.query.filter_by(assigned_to_id=usuario.id, estado='completado').count()
        tareas_pendientes = Task.query.filter_by(assigned_to_id=usuario.id, estado='pendiente').count()
        tareas_vencidas = Task.query.filter(
            Task.assigned_to_id == usuario.id,
            Task.estado.notin_(['completado', 'cancelado']),
            Task.fecha_vencimiento.isnot(None),
            Task.fecha_vencimiento < today
        ).count()
        
        total_con_fecha = Task.query.filter(
            Task.assigned_to_id == usuario.id,
            Task.fecha_vencimiento.isnot(None),
            Task.estado == 'completado'
        ).count()
        a_tiempo = Task.query.filter(
            Task.assigned_to_id == usuario.id,
            Task.estado == 'completado',
            Task.fecha_completada.isnot(None),
            Task.fecha_vencimiento.isnot(None),
            Task.fecha_completada <= Task.fecha_vencimiento
        ).count()
        cumplimiento = round((a_tiempo / total_con_fecha) * 100) if total_con_fecha > 0 else 100
        
        user_stats.append({
            'usuario': usuario,
            'casos_total': casos_total,
            'casos_activos': casos_activos,
            'docs_total': docs_total,
            'docs_mes': docs_mes,
            'tareas_completadas': tareas_completadas,
            'tareas_pendientes': tareas_pendientes,
            'tareas_vencidas': tareas_vencidas,
            'cumplimiento': cumplimiento
        })
    
    totales = {
        'casos': sum(s['casos_total'] for s in user_stats),
        'docs': sum(s['docs_total'] for s in user_stats),
        'docs_mes': sum(s['docs_mes'] for s in user_stats),
        'tareas_completadas': sum(s['tareas_completadas'] for s in user_stats),
        'tareas_pendientes': sum(s['tareas_pendientes'] for s in user_stats)
    }
    
    return render_template("estadisticas_equipo.html", user_stats=user_stats, totales=totales)


# ==================== AI ASSISTANT API ====================

@app.route("/api/ai-assistant", methods=["POST"])
@login_required
def ai_assistant_api():
    data = request.get_json()
    message = data.get('message', '')
    
    if not message:
        return jsonify({'response': 'Por favor escribe una consulta.'})
    
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": """Eres un asistente legal IA especializado en derecho peruano.
                Ayudas a abogados con:
                - Consultas sobre procedimientos legales
                - Estructura de documentos juridicos
                - Plazos procesales
                - Fundamentos juridicos comunes
                - Sugerencias de mejora para escritos
                Responde de forma concisa y profesional en espanol."""},
                {"role": "user", "content": message}
            ],
            temperature=0.7,
            max_tokens=500
        )
        ai_response = response.choices[0].message.content
        return jsonify({'response': ai_response})
    except Exception as e:
        logging.error(f"Error AI Assistant: {e}")
        return jsonify({'response': 'Lo siento, hubo un error al procesar tu consulta. Intenta de nuevo.'})


with app.app_context():
    db.create_all()
    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)
    os.makedirs(CARPETA_PLANTILLAS_SUBIDAS, exist_ok=True)
    os.makedirs(CARPETA_ESTILOS_SUBIDOS, exist_ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
