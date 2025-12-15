import os
import csv
import json
import logging
import requests
import resend
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

from models import db, User, DocumentRecord, Plantilla, Modelo, Estilo, CampoPlantilla, Tenant, Case, CaseAssignment, CaseDocument, Task, FinishedDocument, ImagenModelo, CaseAttachment, ModeloTabla, ReviewSession, ReviewIssue
import uuid

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

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
CARPETA_IMAGENES_MODELOS = "static/imagenes_modelos"
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def get_resend_credentials():
    """Get Resend API credentials from Replit connector."""
    try:
        hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
        token = os.environ.get('REPL_IDENTITY')
        if not token:
            token = os.environ.get('WEB_REPL_RENEWAL')
            if token:
                token = 'depl ' + token
        else:
            token = 'repl ' + token
        
        if not hostname or not token:
            return None, None
            
        response = requests.get(
            f'https://{hostname}/api/v2/connection?include_secrets=true&connector_names=resend',
            headers={'Accept': 'application/json', 'X_REPLIT_TOKEN': token}
        )
        data = response.json()
        if data.get('items'):
            settings = data['items'][0].get('settings', {})
            return settings.get('api_key'), settings.get('from_email')
    except Exception as e:
        logging.error(f"Error getting Resend credentials: {e}")
    return None, None


def send_notification_email(to_email, subject, html_content):
    """Send email notification via Resend."""
    try:
        api_key, from_email = get_resend_credentials()
        if not api_key or not from_email:
            logging.warning("Resend not configured")
            return False
        
        resend.api_key = api_key
        resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html_content
        })
        return True
    except Exception as e:
        logging.error(f"Error sending email: {e}")
        return False


def check_and_send_notifications(tenant_id):
    """Check documents and send notifications for urgent/importante deadlines."""
    try:
        docs = FinishedDocument.query.filter(
            FinishedDocument.tenant_id == tenant_id,
            FinishedDocument.plazo_entrega.isnot(None),
            FinishedDocument.case_id.isnot(None)
        ).all()
        
        for doc in docs:
            priority = doc.get_priority_status()
            
            if priority == 'urgente' and not doc.sent_urgente_notification:
                sent_any = False
                if doc.case:
                    for assignment in doc.case.assignments:
                        if assignment.user and assignment.user.email:
                            if send_notification_email(
                                assignment.user.email,
                                f"URGENTE: Documento '{doc.nombre}' vence pronto",
                                f"<h2>Documento Urgente</h2><p>El documento <strong>{doc.nombre}</strong> tiene plazo de entrega en menos de 24 horas.</p><p>Expediente: {doc.numero_expediente or 'N/A'}</p>"
                            ):
                                sent_any = True
                if sent_any:
                    doc.sent_urgente_notification = True
                    db.session.commit()
                
            elif priority == 'importante' and not doc.sent_importante_notification:
                sent_any = False
                if doc.case:
                    for assignment in doc.case.assignments:
                        if assignment.user and assignment.user.email:
                            if send_notification_email(
                                assignment.user.email,
                                f"Importante: Documento '{doc.nombre}' vence en 2 dias",
                                f"<h2>Recordatorio Importante</h2><p>El documento <strong>{doc.nombre}</strong> tiene plazo de entrega en menos de 48 horas.</p><p>Expediente: {doc.numero_expediente or 'N/A'}</p>"
                            ):
                                sent_any = True
                if sent_any:
                    doc.sent_importante_notification = True
                    db.session.commit()
    except Exception as e:
        logging.error(f"Error checking notifications: {e}")


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


def detect_placeholders_with_context(text):
    """
    Detect placeholders in text and return them with position context.
    Returns list of dicts with: nombre, etiqueta, tipo, start, end, contexto, match_text
    """
    campos = []
    campo_counter = {}
    seen_positions = set()
    
    patterns = [
        (r'\{\{([^}]+)\}\}', 'curly_double'),
        (r'\{([^{}]+)\}', 'curly_single'),
        (r'\[\[([^\]]+)\]\]', 'bracket_double'),
        (r'\[([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s_]+)\]', 'bracket_single'),
    ]
    
    for pattern, pattern_type in patterns:
        for match in re.finditer(pattern, text):
            start, end = match.start(), match.end()
            if any(s <= start < e or s < end <= e for s, e in seen_positions):
                continue
            seen_positions.add((start, end))
            
            captured = match.group(1).strip().replace('_', ' ')
            if captured and len(captured) > 1:
                context_start = max(0, start - 30)
                context_end = min(len(text), end + 30)
                contexto = text[context_start:context_end]
                
                campos.append({
                    'nombre': campo_to_key(captured),
                    'etiqueta': captured,
                    'tipo': 'text',
                    'start': start,
                    'end': end,
                    'contexto': contexto,
                    'match_text': match.group(0),
                    'pattern_type': pattern_type
                })
    
    dot_pattern = r'([A-Za-zÁÉÍÓÚáéíóúÑñ\s\.\,\-°]+?)(?:N[°º]?\s*)?([\.…]{3,}|_{3,})'
    
    for match in re.finditer(dot_pattern, text):
        start, end = match.start(), match.end()
        if any(s <= start < e or s < end <= e for s, e in seen_positions):
            continue
        seen_positions.add((start, end))
        
        full_match = match.group(0)
        context = match.group(1) if match.group(1) else ""
        placeholder_chars = match.group(2) if match.group(2) else ""
        
        context = context.strip()
        context = re.sub(r'^[,\.\s]+', '', context)
        context = re.sub(r'[,\.\s]+$', '', context)
        
        if len(context) < 3:
            before_start = max(0, match.start() - 50)
            before_text = text[before_start:match.start()]
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
                display_name = f"{base_name} {campo_counter[base_name.lower()]}"
            else:
                campo_counter[base_name.lower()] = 1
                display_name = base_name
            
            context_start = max(0, start - 30)
            context_end = min(len(text), end + 30)
            contexto = text[context_start:context_end]
            
            campos.append({
                'nombre': campo_to_key(display_name),
                'etiqueta': display_name,
                'tipo': 'text',
                'start': start,
                'end': end,
                'contexto': contexto,
                'match_text': full_match,
                'pattern_type': 'dots_underscores'
            })
    
    lines = text.split('\n')
    current_pos = 0
    for line in lines:
        if ':' in line and re.search(r':\s*$', line.strip()):
            parts = line.split(':')
            if parts[0].strip() and len(parts[0].strip()) > 2:
                campo_name = parts[0].strip()
                if campo_name.lower() not in campo_counter:
                    start = current_pos + line.find(campo_name)
                    end = current_pos + len(line)
                    
                    if not any(s <= start < e or s < end <= e for s, e in seen_positions):
                        seen_positions.add((start, end))
                        context_start = max(0, current_pos - 10)
                        context_end = min(len(text), current_pos + len(line) + 10)
                        
                        campos.append({
                            'nombre': campo_to_key(campo_name),
                            'etiqueta': campo_name,
                            'tipo': 'text',
                            'start': start,
                            'end': end,
                            'contexto': text[context_start:context_end],
                            'match_text': line.strip(),
                            'pattern_type': 'colon_field'
                        })
                        campo_counter[campo_name.lower()] = 1
        current_pos += len(line) + 1
    
    campos.sort(key=lambda x: x['start'])
    return campos


def generate_highlighted_html(text, campos):
    """
    Generate HTML with highlighted placeholders for preview.
    Properly escapes content to prevent XSS attacks.
    Iterates in forward order to preserve campo index synchronization.
    """
    from markupsafe import escape
    
    if not campos:
        escaped = str(escape(text))
        return escaped.replace('\n', '<br>')
    
    sorted_campos = sorted(enumerate(campos), key=lambda x: x[1]['start'])
    
    segments = []
    last_pos = 0
    
    for original_idx, campo in sorted_campos:
        start = campo['start']
        end = campo['end']
        
        if start > last_pos:
            before_text = text[last_pos:start]
            segments.append(str(escape(before_text)))
        
        match_text = text[start:end]
        escaped_match = str(escape(match_text))
        escaped_etiqueta = str(escape(campo['etiqueta']))
        escaped_nombre = str(escape(campo['nombre']))
        
        colors = ['bg-yellow-200', 'bg-green-200', 'bg-blue-200', 'bg-pink-200', 'bg-purple-200', 'bg-orange-200']
        color = colors[original_idx % len(colors)]
        
        highlighted = f'<span class="placeholder-highlight {color} px-1 rounded cursor-pointer hover:ring-2 hover:ring-blue-500" data-campo-index="{original_idx}" data-campo-nombre="{escaped_nombre}" title="{escaped_etiqueta}">{escaped_match}</span>'
        segments.append(highlighted)
        
        last_pos = end
    
    if last_pos < len(text):
        remaining_text = text[last_pos:]
        segments.append(str(escape(remaining_text)))
    
    result = ''.join(segments)
    result = result.replace('\n', '<br>')
    return result


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


def coordinador_or_admin_required(f):
    """Permite acceso a coordinadores, admin_estudio y super_admin."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_super_admin() and not current_user.is_admin_estudio() and not current_user.is_coordinador():
            flash("Acceso restringido a coordinadores y administradores.", "error")
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


def construir_prompt(plantilla, estilos, datos_caso, campos_dinamicos=None, datos_tablas=None):
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
    
    tablas_str = ""
    if datos_tablas:
        for tabla_nombre, tabla_info in datos_tablas.items():
            tablas_str += f"\n\n[TABLA: {tabla_nombre}]\n"
            columnas = tabla_info.get('columnas', [])
            filas = tabla_info.get('filas', [])
            if columnas:
                tablas_str += "| " + " | ".join(columnas) + " |\n"
                tablas_str += "|" + "|".join(["---"] * len(columnas)) + "|\n"
            for fila in filas:
                tablas_str += "| " + " | ".join([str(fila.get(col, '')) for col in columnas]) + " |\n"
            if tabla_info.get('total'):
                tablas_str += f"TOTAL: {tabla_info['total']}\n"
    
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
{datos_str}{tablas_str}

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
8. Redacta el documento completo sin explicaciones adicionales.
9. Si hay tablas de datos (gastos, honorarios, etc.), incluye la tabla formateada en el documento usando el formato:
   [[TABLA:{{'tabla_nombre'}}]]
   La tabla será insertada automáticamente en esa ubicación."""
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


def agregar_tabla_word(doc, tabla_nombre, tabla_data):
    """Add a formatted table to the Word document."""
    from docx.shared import Inches
    from docx.oxml.ns import nsdecls
    from docx.oxml import parse_xml
    
    columnas = tabla_data.get('columnas', [])
    filas = tabla_data.get('filas', [])
    total = tabla_data.get('total')
    mostrar_total = tabla_data.get('mostrar_total', False)
    
    if not columnas or not filas:
        return
    
    p_titulo = doc.add_paragraph()
    run_titulo = p_titulo.add_run(tabla_nombre.upper())
    run_titulo.bold = True
    run_titulo.font.name = 'Times New Roman'
    run_titulo.font.size = Pt(11)
    p_titulo.paragraph_format.space_before = Pt(12)
    p_titulo.paragraph_format.space_after = Pt(6)
    
    num_filas = len(filas) + 1
    if mostrar_total and total:
        num_filas += 1
    
    table = doc.add_table(rows=num_filas, cols=len(columnas))
    table.style = 'Table Grid'
    
    header_row = table.rows[0]
    for i, col in enumerate(columnas):
        cell = header_row.cells[i]
        cell.text = col
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
                run.font.name = 'Times New Roman'
                run.font.size = Pt(10)
        shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="E6E6E6"/>')
        cell._tc.get_or_add_tcPr().append(shading_elm)
    
    for row_idx, fila in enumerate(filas):
        row = table.rows[row_idx + 1]
        for col_idx, col in enumerate(columnas):
            cell = row.cells[col_idx]
            cell.text = str(fila.get(col, ''))
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.name = 'Times New Roman'
                    run.font.size = Pt(10)
    
    if mostrar_total and total:
        total_row = table.rows[-1]
        total_row.cells[0].text = 'TOTAL'
        for paragraph in total_row.cells[0].paragraphs:
            for run in paragraph.runs:
                run.bold = True
                run.font.name = 'Times New Roman'
                run.font.size = Pt(10)
        
        total_row.cells[-1].text = str(total)
        for paragraph in total_row.cells[-1].paragraphs:
            for run in paragraph.runs:
                run.bold = True
                run.font.name = 'Times New Roman'
                run.font.size = Pt(10)
        
        for cell in total_row.cells:
            shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F0F0F0"/>')
            cell._tc.get_or_add_tcPr().append(shading_elm)
    
    doc.add_paragraph()


def guardar_docx(texto, nombre_archivo, tenant=None, datos_tablas=None):
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
    
    import re
    tabla_pattern = re.compile(r'\[\[TABLA:([^\]]+)\]\]')
    imagen_pattern = re.compile(r'\{\{IMAGEN:([^}]+)\}\}')
    tablas_insertadas = set()
    
    for parrafo in texto.split("\n"):
        linea = parrafo.strip()
        if not linea:
            continue
        
        tabla_match = tabla_pattern.search(linea)
        if tabla_match and datos_tablas:
            tabla_ref = tabla_match.group(1).strip()
            for tabla_nombre, tabla_data in datos_tablas.items():
                if tabla_nombre.lower() in tabla_ref.lower() or tabla_ref.lower() in tabla_nombre.lower():
                    if tabla_nombre not in tablas_insertadas:
                        agregar_tabla_word(doc, tabla_nombre, tabla_data)
                        tablas_insertadas.add(tabla_nombre)
                    break
            continue
        
        imagen_match = imagen_pattern.search(linea)
        if imagen_match:
            imagen_key = imagen_match.group(1).strip().lower()
            imagen_key_norm = imagen_key.replace(' ', '_').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u').replace('ñ', 'n')
            
            imagen_path = None
            if tenant:
                campo_imagen = CampoPlantilla.query.filter_by(
                    tenant_id=tenant.id,
                    tipo='file'
                ).filter(CampoPlantilla.nombre_campo.ilike(f'%{imagen_key_norm}%')).first()
                
                if campo_imagen and campo_imagen.archivo_path:
                    full_path = os.path.join(CARPETA_IMAGENES_MODELOS, campo_imagen.archivo_path)
                    if os.path.exists(full_path):
                        imagen_path = full_path
            
            if imagen_path:
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = p.add_run()
                try:
                    run.add_picture(imagen_path, width=Cm(8))
                except Exception as e:
                    logging.error(f"Error insertando imagen {imagen_key}: {e}")
                    p.add_run(f"[Imagen: {imagen_key}]")
            else:
                p = doc.add_paragraph()
                run = p.add_run(f"[Imagen: {imagen_key} no encontrada]")
                run.font.name = 'Times New Roman'
                run.font.size = Pt(10)
                run.italic = True
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
    
    if datos_tablas:
        for tabla_nombre, tabla_data in datos_tablas.items():
            if tabla_nombre not in tablas_insertadas:
                agregar_tabla_word(doc, tabla_nombre, tabla_data)
    
    folder = get_resultados_folder(tenant)
    ruta = os.path.join(folder, nombre_archivo)
    doc.save(ruta)
    return ruta


def validar_dato(valor):
    if not valor or valor.strip() == "":
        return "{{FALTA_DATO}}"
    return valor.strip()


def extraer_datos_tablas(form_data, tipo_documento, tenant_id):
    """Extract table data from form submission based on model tables."""
    datos_tablas = {}
    
    if not tenant_id:
        return datos_tablas
    
    modelo_db = Modelo.query.filter_by(key=tipo_documento, tenant_id=tenant_id).first()
    if not modelo_db:
        return datos_tablas
    
    tablas = ModeloTabla.query.filter_by(modelo_id=modelo_db.id, tenant_id=tenant_id).all()
    
    def normalize_field_name(text):
        text = text.lower().replace(' ', '_')
        text = text.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
        text = text.replace('ñ', 'n')
        text = ''.join(c for c in text if c.isalnum() or c == '_')
        text = text.replace('.', '')
        return text
    
    for tabla in tablas:
        tabla_nombre = normalize_field_name(tabla.nombre)
        
        columnas = tabla.columnas if tabla.columnas else []
        filas = []
        
        for fila_idx in range(tabla.num_filas):
            fila_data = {}
            fila_has_data = False
            
            for col in columnas:
                col_nombre = normalize_field_name(col)
                
                campo_key = f"tabla_{tabla_nombre}_{fila_idx}_{col_nombre}"
                valor = form_data.get(campo_key, '').strip()
                fila_data[col] = valor
                if valor:
                    fila_has_data = True
            
            if fila_has_data:
                filas.append(fila_data)
        
        total_valor = None
        if tabla.mostrar_total and tabla.columna_total:
            col_total_nombre = normalize_field_name(tabla.columna_total)
            campo_total_key = f"tabla_{tabla_nombre}_total_{col_total_nombre}"
            total_valor = form_data.get(campo_total_key, '').strip()
        
        if filas:
            datos_tablas[tabla.nombre] = {
                'columnas': columnas,
                'filas': filas,
                'total': total_valor,
                'mostrar_total': tabla.mostrar_total
            }
    
    return datos_tablas


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
        archivos_subidos = {}
        for campo in campos_dinamicos:
            if campo.tipo == 'file':
                archivo = request.files.get(campo.nombre_campo)
                if archivo and archivo.filename:
                    from werkzeug.utils import secure_filename
                    import uuid
                    filename = secure_filename(archivo.filename)
                    unique_filename = f"{uuid.uuid4().hex}_{filename}"
                    upload_folder = os.path.join('archivos_campos', f'tenant_{tenant_id}')
                    os.makedirs(upload_folder, exist_ok=True)
                    filepath = os.path.join(upload_folder, unique_filename)
                    archivo.save(filepath)
                    datos_caso[campo.nombre_campo] = f"[Archivo: {filename}]"
                    archivos_subidos[campo.nombre_campo] = filepath
                else:
                    datos_caso[campo.nombre_campo] = "[Sin archivo]"
            else:
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
    
    datos_tablas = extraer_datos_tablas(request.form, tipo_documento, tenant_id)
    
    plantilla = cargar_plantilla(modelo["plantilla"], tenant_id)
    estilos = cargar_estilos(modelo["carpeta_estilos"], tenant_id)
    prompt = construir_prompt(plantilla, estilos, datos_caso, campos_dinamicos if campos_dinamicos else None, datos_tablas)
    
    texto_generado = generar_con_ia(prompt)
    
    if not texto_generado:
        flash("Error al generar el documento. Verifica tu API key de OpenAI.", "error")
        return redirect(url_for("index"))
    
    fecha_actual = datetime.now()
    nombre_archivo = f"{tipo_documento}_{fecha_actual.strftime('%Y%m%d_%H%M%S')}.docx"
    
    guardar_docx(texto_generado, nombre_archivo, tenant, datos_tablas)
    
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
        archivos_subidos = {}
        for campo in campos_dinamicos:
            if campo.tipo == 'file':
                archivo = request.files.get(campo.nombre_campo)
                if archivo and archivo.filename:
                    from werkzeug.utils import secure_filename
                    import uuid
                    filename = secure_filename(archivo.filename)
                    unique_filename = f"{uuid.uuid4().hex}_{filename}"
                    upload_folder = os.path.join('archivos_campos', f'tenant_{tenant_id}')
                    os.makedirs(upload_folder, exist_ok=True)
                    filepath = os.path.join(upload_folder, unique_filename)
                    archivo.save(filepath)
                    datos_caso[campo.nombre_campo] = f"[Archivo: {filename}]"
                    archivos_subidos[campo.nombre_campo] = filepath
                else:
                    datos_caso[campo.nombre_campo] = "[Sin archivo]"
            else:
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
    
    datos_tablas = extraer_datos_tablas(request.form, tipo_documento, tenant_id)
    
    plantilla = cargar_plantilla(modelo["plantilla"], tenant_id)
    estilos = cargar_estilos(modelo["carpeta_estilos"], tenant_id)
    prompt = construir_prompt(plantilla, estilos, datos_caso, campos_dinamicos if campos_dinamicos else None, datos_tablas)
    
    texto_generado = generar_con_ia(prompt)
    
    if not texto_generado:
        flash("Error al generar el preview. Verifica tu API key de OpenAI.", "error")
        return redirect(url_for("index"))
    
    return render_template("preview.html", 
                          texto=texto_generado, 
                          datos_caso=datos_caso,
                          datos_tablas=datos_tablas,
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
    
    datos_tablas_str = request.form.get("datos_tablas", "{}")
    try:
        datos_tablas = json.loads(datos_tablas_str)
    except:
        datos_tablas = {}
    
    fecha_actual = datetime.now()
    nombre_archivo = f"{tipo_documento}_{fecha_actual.strftime('%Y%m%d_%H%M%S')}.docx"
    
    guardar_docx(texto_editado, nombre_archivo, tenant, datos_tablas if datos_tablas else None)
    
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
def admin_usuarios():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("index"))
    
    is_admin = current_user.role in ['super_admin', 'admin_estudio']
    
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
            allowed_roles = ['admin_estudio', 'coordinador', 'usuario_estudio'] if is_admin else ['coordinador', 'usuario_estudio']
            if role not in allowed_roles:
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
    return render_template("admin_usuarios.html", usuarios=usuarios, tenant=tenant, is_admin=is_admin)


@app.route("/admin/usuario/toggle/<int:user_id>", methods=["POST"])
@coordinador_or_admin_required
def toggle_usuario(user_id):
    tenant = get_current_tenant()
    user = User.query.get_or_404(user_id)
    
    if user.tenant_id != tenant.id:
        flash("No tienes permiso para modificar este usuario.", "error")
        return redirect(url_for("admin_usuarios"))
    
    if user.id == current_user.id:
        flash("No puedes desactivar tu propia cuenta.", "error")
        return redirect(url_for("admin_usuarios"))
    
    is_admin = current_user.role in ['super_admin', 'admin_estudio']
    if not is_admin and user.role == 'admin_estudio':
        flash("No puedes modificar usuarios administradores.", "error")
        return redirect(url_for("admin_usuarios"))
    
    user.activo = not user.activo
    db.session.commit()
    status = "activado" if user.activo else "desactivado"
    flash(f"Usuario {user.username} {status}.", "success")
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/convertir", methods=["GET", "POST"])
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
@coordinador_or_admin_required
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
    
    campos_data = [{
        'id': c.id,
        'nombre_campo': c.nombre_campo,
        'etiqueta': c.etiqueta,
        'tipo': c.tipo,
        'requerido': c.requerido,
        'placeholder': c.placeholder or '',
        'opciones': c.opciones.split(',') if c.opciones else []
    } for c in campos]
    
    tablas_data = []
    if tenant_id:
        modelo = Modelo.query.filter_by(key=plantilla_key, tenant_id=tenant_id).first()
        if modelo:
            tablas = ModeloTabla.query.filter_by(modelo_id=modelo.id, tenant_id=tenant_id).order_by(ModeloTabla.orden).all()
            for tabla in tablas:
                tablas_data.append({
                    'id': tabla.id,
                    'nombre': tabla.nombre,
                    'columnas': tabla.columnas,
                    'num_filas': tabla.num_filas,
                    'mostrar_total': tabla.mostrar_total,
                    'columna_total': tabla.columna_total
                })
    
    return jsonify({
        'campos': campos_data,
        'tablas': tablas_data
    })


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
                          prioridades=Case.PRIORIDADES,
                          now=datetime.now())


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
        
        colaboradores_ids = request.form.getlist("colaboradores")
        responsable_id = request.form.get("responsable_id", type=int)
        
        for colab_id in colaboradores_ids:
            try:
                user_id = int(colab_id)
                es_responsable = (user_id == responsable_id)
                assignment = CaseAssignment(
                    case_id=caso.id,
                    user_id=user_id,
                    rol_en_caso='abogado',
                    es_responsable=es_responsable
                )
                db.session.add(assignment)
            except ValueError:
                pass
        
        if responsable_id and str(responsable_id) not in colaboradores_ids:
            assignment = CaseAssignment(
                case_id=caso.id,
                user_id=responsable_id,
                rol_en_caso='abogado',
                es_responsable=True
            )
            db.session.add(assignment)
        
        archivos = request.files.getlist("archivos")
        if archivos:
            attachments_dir = os.path.join("case_attachments", f"tenant_{tenant.id}", f"case_{caso.id}")
            os.makedirs(attachments_dir, exist_ok=True)
            
            for archivo in archivos:
                if archivo and archivo.filename:
                    filename = secure_filename(archivo.filename)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    unique_filename = f"{timestamp}_{filename}"
                    filepath = os.path.join(attachments_dir, unique_filename)
                    archivo.save(filepath)
                    
                    ext = os.path.splitext(filename)[1].lower()
                    attachment = CaseAttachment(
                        case_id=caso.id,
                        nombre=filename,
                        archivo=filepath,
                        tipo_archivo=ext,
                        uploaded_by_id=current_user.id
                    )
                    db.session.add(attachment)
        
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
                          prioridades=Case.PRIORIDADES,
                          current_tenant=tenant,
                          now=datetime.now())


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
        
        archivo = request.files.get("archivo")
        if archivo and archivo.filename:
            attachments_dir = os.path.join("task_attachments", f"tenant_{tenant.id}")
            os.makedirs(attachments_dir, exist_ok=True)
            
            filename = secure_filename(archivo.filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{filename}"
            filepath = os.path.join(attachments_dir, unique_filename)
            archivo.save(filepath)
            
            tarea.archivo = filepath
            tarea.archivo_nombre = filename
        
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


@app.route("/tareas/<int:tarea_id>/archivo")
@case_access_required
def descargar_archivo_tarea(tarea_id):
    """Descargar archivo adjunto de una tarea."""
    tenant = get_current_tenant()
    tarea = Task.query.filter_by(id=tarea_id, tenant_id=tenant.id).first_or_404()
    
    if not tarea.archivo or not os.path.exists(tarea.archivo):
        flash("Archivo no encontrado.", "error")
        return redirect(url_for("tareas"))
    
    return send_file(
        tarea.archivo,
        as_attachment=True,
        download_name=tarea.archivo_nombre or os.path.basename(tarea.archivo)
    )


@app.route("/casos/<int:caso_id>/adjunto/<int:attachment_id>")
@case_access_required
def descargar_adjunto_caso(caso_id, attachment_id):
    """Descargar archivo adjunto de un caso."""
    tenant = get_current_tenant()
    caso = Case.query.filter_by(id=caso_id, tenant_id=tenant.id).first_or_404()
    attachment = CaseAttachment.query.filter_by(id=attachment_id, case_id=caso_id).first_or_404()
    
    if not attachment.archivo or not os.path.exists(attachment.archivo):
        flash("Archivo no encontrado.", "error")
        return redirect(url_for("caso_detalle", caso_id=caso_id))
    
    return send_file(
        attachment.archivo,
        as_attachment=True,
        download_name=attachment.nombre
    )


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


@app.route("/api/detect-campos", methods=["POST"])
@login_required
def api_detect_campos():
    """AJAX endpoint to detect fields from uploaded file with document preview."""
    archivo = request.files.get('archivo')
    
    if not archivo or not archivo.filename:
        return jsonify({'success': False, 'error': 'No se proporcionó archivo'}), 400
    
    ext = os.path.splitext(archivo.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'success': False, 'error': f'Formato no soportado: {ext}'}), 400
    
    import tempfile
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, secure_filename(archivo.filename))
    
    try:
        archivo.save(temp_path)
        contenido = extract_text_from_file(temp_path)
        
        if not contenido:
            return jsonify({'success': False, 'error': 'No se pudo extraer texto del archivo'}), 400
        
        campos_detectados = detect_placeholders_with_context(contenido)
        
        highlighted_html = generate_highlighted_html(contenido, campos_detectados)
        
        campos_result = []
        for i, campo in enumerate(campos_detectados):
            campos_result.append({
                'nombre': campo['nombre'],
                'etiqueta': campo['etiqueta'],
                'tipo': campo['tipo'],
                'index': i,
                'contexto': campo['contexto'],
                'match_text': campo['match_text'],
                'pattern_type': campo['pattern_type']
            })
        
        return jsonify({
            'success': True, 
            'campos': campos_result,
            'contenido_html': highlighted_html,
            'contenido_raw': contenido[:5000] if len(contenido) > 5000 else contenido
        })
    except Exception as e:
        logging.error(f"Error detecting campos: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


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
        
        campo_nombres = request.form.getlist('campo_nombre[]')
        campo_etiquetas = request.form.getlist('campo_etiqueta[]')
        campo_tipos = request.form.getlist('campo_tipo[]')
        
        if campo_nombres:
            campos_anteriores = {c.nombre_campo: c for c in CampoPlantilla.query.filter_by(tenant_id=tenant.id, plantilla_key=modelo.key).all()}
            CampoPlantilla.query.filter_by(tenant_id=tenant.id, plantilla_key=modelo.key).delete()
            
            for i, nombre_campo in enumerate(campo_nombres):
                if nombre_campo.strip():
                    etiqueta = campo_etiquetas[i] if i < len(campo_etiquetas) else nombre_campo
                    tipo = campo_tipos[i] if i < len(campo_tipos) else 'text'
                    
                    archivo_path_campo = None
                    if tipo == 'file':
                        campo_archivo = request.files.get(f'campo_archivo_{i}')
                        if campo_archivo and campo_archivo.filename:
                            img_ext = os.path.splitext(campo_archivo.filename)[1].lower()
                            if img_ext in ALLOWED_IMAGE_EXTENSIONS:
                                campo_folder = os.path.join(CARPETA_IMAGENES_MODELOS, f"campos_{tenant.id}")
                                os.makedirs(campo_folder, exist_ok=True)
                                
                                safe_img_name = secure_filename(campo_archivo.filename)
                                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                                img_filename = f"{timestamp}_{safe_img_name}"
                                img_full_path = os.path.join(campo_folder, img_filename)
                                campo_archivo.save(img_full_path)
                                archivo_path_campo = f"campos_{tenant.id}/{img_filename}"
                        elif campo_to_key(nombre_campo) in campos_anteriores:
                            archivo_path_campo = campos_anteriores[campo_to_key(nombre_campo)].archivo_path
                    
                    campo = CampoPlantilla(
                        tenant_id=tenant.id,
                        plantilla_key=modelo.key,
                        nombre_campo=campo_to_key(nombre_campo),
                        etiqueta=etiqueta.strip(),
                        tipo=tipo,
                        orden=i,
                        archivo_path=archivo_path_campo
                    )
                    db.session.add(campo)
            
            db.session.commit()
        
        imagen_archivo = request.files.get('imagen_archivo')
        if imagen_archivo and imagen_archivo.filename:
            img_ext = os.path.splitext(imagen_archivo.filename)[1].lower()
            if img_ext in ALLOWED_IMAGE_EXTENSIONS:
                tenant_folder = os.path.join(CARPETA_IMAGENES_MODELOS, str(tenant.id))
                os.makedirs(tenant_folder, exist_ok=True)
                
                safe_img_name = secure_filename(imagen_archivo.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                img_filename = f"{timestamp}_{safe_img_name}"
                img_path = os.path.join(tenant_folder, img_filename)
                imagen_archivo.save(img_path)
                
                imagen_nombre = request.form.get('imagen_nombre', safe_img_name).strip()
                imagen_posicion = request.form.get('imagen_posicion', 'inline')
                try:
                    imagen_ancho = float(request.form.get('imagen_ancho') or 5.0)
                    if imagen_ancho < 1 or imagen_ancho > 18:
                        imagen_ancho = 5.0
                except (ValueError, TypeError):
                    imagen_ancho = 5.0
                
                nueva_imagen = ImagenModelo(
                    modelo_id=modelo.id,
                    tenant_id=tenant.id,
                    nombre=imagen_nombre,
                    archivo=img_path,
                    posicion=imagen_posicion,
                    ancho_cm=imagen_ancho,
                    orden=modelo.imagenes.count()
                )
                db.session.add(nueva_imagen)
                db.session.commit()
                flash("Imagen agregada al modelo.", "success")
                return redirect(url_for("mi_modelo", id=modelo.id))
            else:
                flash("Formato de imagen no soportado. Use JPG, PNG, GIF o WebP.", "error")
        
        tabla_nombre = request.form.get('tabla_nombre', '').strip()
        tabla_columnas = request.form.get('tabla_columnas', '').strip()
        if tabla_nombre and tabla_columnas:
            columnas_list = [c.strip() for c in tabla_columnas.split(',') if c.strip()]
            if columnas_list:
                try:
                    num_filas = int(request.form.get('tabla_filas', 5))
                    if num_filas < 1:
                        num_filas = 1
                    if num_filas > 50:
                        num_filas = 50
                except ValueError:
                    num_filas = 5
                
                mostrar_total = request.form.get('tabla_mostrar_total') == 'on'
                
                nueva_tabla = ModeloTabla(
                    modelo_id=modelo.id,
                    tenant_id=tenant.id,
                    nombre=tabla_nombre,
                    columnas=columnas_list,
                    num_filas=num_filas,
                    mostrar_total=mostrar_total,
                    columna_total=columnas_list[-1] if mostrar_total and columnas_list else None,
                    orden=modelo.tablas.count() if modelo.tablas else 0
                )
                db.session.add(nueva_tabla)
                db.session.commit()
                flash(f"Cuadro '{tabla_nombre}' agregado al modelo.", "success")
                return redirect(url_for("mi_modelo", id=modelo.id))
        
        return redirect(url_for("mis_modelos"))
    
    campos_guardados = []
    if modelo:
        campos_guardados = CampoPlantilla.query.filter_by(
            tenant_id=tenant.id, 
            plantilla_key=modelo.key
        ).order_by(CampoPlantilla.orden).all()
    
    return render_template("mi_modelo.html", modelo=modelo, campos_detectados=campos_detectados, campos_guardados=campos_guardados)


@app.route("/api/imagen-modelo/<int:imagen_id>", methods=["DELETE"])
@login_required
def eliminar_imagen_modelo(imagen_id):
    tenant = get_current_tenant()
    imagen = ImagenModelo.query.filter_by(id=imagen_id, tenant_id=tenant.id).first()
    
    if not imagen:
        return jsonify({"success": False, "error": "Imagen no encontrada"}), 404
    
    modelo = Modelo.query.get(imagen.modelo_id)
    if not modelo or modelo.created_by_id != current_user.id:
        return jsonify({"success": False, "error": "No tienes permiso"}), 403
    
    if imagen.archivo and os.path.exists(imagen.archivo):
        try:
            os.remove(imagen.archivo)
        except Exception as e:
            logging.error(f"Error deleting image file: {e}")
    
    db.session.delete(imagen)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/modelo-tabla/<int:tabla_id>", methods=["DELETE"])
@login_required
def eliminar_tabla_modelo(tabla_id):
    tenant = get_current_tenant()
    tabla = ModeloTabla.query.filter_by(id=tabla_id, tenant_id=tenant.id).first()
    
    if not tabla:
        return jsonify({"success": False, "error": "Cuadro no encontrado"}), 404
    
    modelo = Modelo.query.get(tabla.modelo_id)
    if not modelo or modelo.created_by_id != current_user.id:
        return jsonify({"success": False, "error": "No tienes permiso"}), 403
    
    db.session.delete(tabla)
    db.session.commit()
    return jsonify({"success": True})


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


# ==================== DOCUMENTOS TERMINADOS ====================

CARPETA_DOCUMENTOS_TERMINADOS = "documentos_terminados"

@app.route("/documentos-terminados")
@login_required
def documentos_terminados():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("dashboard"))
    
    check_and_send_notifications(tenant.id)
    
    documentos = FinishedDocument.query.filter_by(
        tenant_id=tenant.id, 
        user_id=current_user.id
    ).order_by(FinishedDocument.created_at.desc()).all()
    
    casos = Case.query.filter_by(tenant_id=tenant.id).order_by(Case.titulo).all()
    
    return render_template("documentos_terminados.html", documentos=documentos, casos=casos)


@app.route("/documentos-terminados/subir", methods=["POST"])
@login_required
def subir_documento_terminado():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes un estudio asociado.", "error")
        return redirect(url_for("documentos_terminados"))
    
    archivo = request.files.get('archivo')
    nombre = request.form.get('nombre', '').strip()
    case_id = request.form.get('case_id', type=int)
    descripcion = request.form.get('descripcion', '').strip()
    tipo_documento = request.form.get('tipo_documento', '').strip()
    numero_expediente = request.form.get('numero_expediente', '').strip()
    plazo_entrega_str = request.form.get('plazo_entrega', '').strip()
    
    plazo_entrega = None
    if plazo_entrega_str:
        try:
            plazo_entrega = datetime.strptime(plazo_entrega_str, '%Y-%m-%d')
        except ValueError:
            pass
    
    if not archivo or not archivo.filename:
        flash("Debes seleccionar un archivo.", "error")
        return redirect(url_for("documentos_terminados"))
    
    ext = os.path.splitext(archivo.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        flash(f"Formato de archivo no soportado ({ext}). Use .docx, .pdf o .txt", "error")
        return redirect(url_for("documentos_terminados"))
    
    if not nombre:
        nombre = os.path.splitext(archivo.filename)[0]
    
    tenant_folder = os.path.join(CARPETA_DOCUMENTOS_TERMINADOS, f"tenant_{tenant.id}")
    os.makedirs(tenant_folder, exist_ok=True)
    
    safe_name = secure_filename(archivo.filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    archivo_name = f"{timestamp}_{safe_name}"
    archivo_path = os.path.join(tenant_folder, archivo_name)
    archivo.save(archivo_path)
    
    documento = FinishedDocument(
        tenant_id=tenant.id,
        user_id=current_user.id,
        case_id=case_id if case_id else None,
        nombre=nombre,
        archivo=archivo_path,
        descripcion=descripcion,
        tipo_documento=tipo_documento,
        numero_expediente=numero_expediente if numero_expediente else None,
        plazo_entrega=plazo_entrega
    )
    db.session.add(documento)
    db.session.commit()
    
    flash("Documento subido exitosamente.", "success")
    return redirect(url_for("documentos_terminados"))


@app.route("/documentos-terminados/eliminar/<int:doc_id>", methods=["POST"])
@login_required
def eliminar_documento_terminado(doc_id):
    tenant = get_current_tenant()
    documento = FinishedDocument.query.filter_by(
        id=doc_id, 
        tenant_id=tenant.id, 
        user_id=current_user.id
    ).first()
    
    if not documento:
        flash("No tienes permiso para eliminar este documento.", "error")
        return redirect(url_for("documentos_terminados"))
    
    if documento.archivo and os.path.exists(documento.archivo):
        os.remove(documento.archivo)
    
    db.session.delete(documento)
    db.session.commit()
    flash("Documento eliminado exitosamente.", "success")
    return redirect(url_for("documentos_terminados"))


@app.route("/documentos-terminados/descargar/<int:doc_id>")
@login_required
def descargar_documento_terminado(doc_id):
    tenant = get_current_tenant()
    documento = FinishedDocument.query.filter_by(
        id=doc_id, 
        tenant_id=tenant.id,
        user_id=current_user.id
    ).first()
    
    if not documento or not documento.archivo:
        flash("Documento no encontrado.", "error")
        return redirect(url_for("documentos_terminados"))
    
    if not os.path.exists(documento.archivo):
        flash("El archivo no existe en el servidor.", "error")
        return redirect(url_for("documentos_terminados"))
    
    directory = os.path.dirname(documento.archivo)
    filename = os.path.basename(documento.archivo)
    return send_from_directory(directory, filename, as_attachment=True)


@app.route("/documentos-terminados/editar/<int:doc_id>", methods=["GET", "POST"])
@login_required
def editar_documento_terminado(doc_id):
    tenant = get_current_tenant()
    documento = FinishedDocument.query.filter_by(
        id=doc_id, 
        tenant_id=tenant.id,
        user_id=current_user.id
    ).first()
    
    if not documento:
        flash("No tienes permiso para editar este documento.", "error")
        return redirect(url_for("documentos_terminados"))
    
    casos = Case.query.filter_by(tenant_id=tenant.id).order_by(Case.titulo).all()
    
    contenido_texto = ""
    campos_detectados = []
    if documento.archivo and os.path.exists(documento.archivo):
        contenido_texto = extract_text_from_file(documento.archivo)
        campos_detectados = detect_placeholders_with_context(contenido_texto)
    
    if request.method == "POST":
        action = request.form.get('action', 'save_metadata')
        
        if action == 'save_metadata':
            documento.nombre = request.form.get('nombre', documento.nombre).strip()
            documento.descripcion = request.form.get('descripcion', '').strip()
            documento.tipo_documento = request.form.get('tipo_documento', '').strip()
            documento.numero_expediente = request.form.get('numero_expediente', '').strip() or None
            plazo_str = request.form.get('plazo_entrega', '').strip()
            if plazo_str:
                try:
                    documento.plazo_entrega = datetime.strptime(plazo_str, '%Y-%m-%d')
                except ValueError:
                    pass
            else:
                documento.plazo_entrega = None
            case_id = request.form.get('case_id', type=int)
            documento.case_id = case_id if case_id else None
            db.session.commit()
            flash("Documento actualizado exitosamente.", "success")
            return redirect(url_for("documentos_terminados"))
        
        elif action == 'update_fields':
            field_values = {}
            for key in request.form:
                if key.startswith('field_'):
                    field_name = key.replace('field_', '')
                    field_values[field_name] = request.form[key]
            
            if field_values and documento.archivo and os.path.exists(documento.archivo):
                ext = os.path.splitext(documento.archivo)[1].lower()
                if ext == '.docx':
                    try:
                        doc = Document(documento.archivo)
                        
                        def replace_in_runs(para, search_text, replace_text):
                            """Replace text in runs while preserving formatting."""
                            full_text = para.text
                            if search_text not in full_text:
                                return False
                            
                            runs_text = [(run, run.text) for run in para.runs]
                            combined = ''.join([t for _, t in runs_text])
                            
                            if search_text in combined:
                                start_idx = combined.find(search_text)
                                end_idx = start_idx + len(search_text)
                                
                                char_idx = 0
                                first_run_idx = None
                                first_run_char = None
                                last_run_idx = None
                                last_run_char = None
                                
                                for i, (run, text) in enumerate(runs_text):
                                    run_start = char_idx
                                    run_end = char_idx + len(text)
                                    
                                    if first_run_idx is None and run_end > start_idx:
                                        first_run_idx = i
                                        first_run_char = start_idx - run_start
                                    
                                    if run_end >= end_idx:
                                        last_run_idx = i
                                        last_run_char = end_idx - run_start
                                        break
                                    
                                    char_idx = run_end
                                
                                if first_run_idx is not None and last_run_idx is not None:
                                    if first_run_idx == last_run_idx:
                                        run = para.runs[first_run_idx]
                                        original = run.text
                                        run.text = original[:first_run_char] + replace_text + original[last_run_char:]
                                    else:
                                        first_run = para.runs[first_run_idx]
                                        first_run.text = first_run.text[:first_run_char] + replace_text
                                        
                                        for i in range(first_run_idx + 1, last_run_idx):
                                            para.runs[i].text = ''
                                        
                                        last_run = para.runs[last_run_idx]
                                        last_run.text = last_run.text[last_run_char:]
                                    return True
                            return False
                        
                        for para in doc.paragraphs:
                            for field_name, field_value in field_values.items():
                                if field_value:
                                    for campo in campos_detectados:
                                        if campo['nombre'] == field_name and campo['match_text'] in para.text:
                                            replace_in_runs(para, campo['match_text'], field_value)
                        
                        for table in doc.tables:
                            for row in table.rows:
                                for cell in row.cells:
                                    for para in cell.paragraphs:
                                        for field_name, field_value in field_values.items():
                                            if field_value:
                                                for campo in campos_detectados:
                                                    if campo['nombre'] == field_name and campo['match_text'] in para.text:
                                                        replace_in_runs(para, campo['match_text'], field_value)
                        
                        doc.save(documento.archivo)
                        flash("Campos actualizados en el documento.", "success")
                    except Exception as e:
                        logging.error(f"Error updating document fields: {e}")
                        flash("Error al actualizar los campos del documento.", "error")
                elif ext == '.txt':
                    try:
                        with open(documento.archivo, 'r', encoding='utf-8') as f:
                            content = f.read()
                        for field_name, field_value in field_values.items():
                            if field_value:
                                for campo in campos_detectados:
                                    if campo['nombre'] == field_name:
                                        content = content.replace(campo['match_text'], field_value)
                        with open(documento.archivo, 'w', encoding='utf-8') as f:
                            f.write(content)
                        flash("Campos actualizados en el documento.", "success")
                    except Exception as e:
                        logging.error(f"Error updating text document fields: {e}")
                        flash("Error al actualizar los campos del documento.", "error")
                else:
                    flash("La edicion de campos solo esta disponible para archivos .docx y .txt", "warning")
            
            contenido_texto = extract_text_from_file(documento.archivo)
            campos_detectados = detect_placeholders_with_context(contenido_texto)
            contenido_html = ""
            if contenido_texto and campos_detectados:
                contenido_html = generate_highlighted_html(contenido_texto, campos_detectados)
            elif contenido_texto:
                from markupsafe import escape
                contenido_html = str(escape(contenido_texto)).replace('\n', '<br>')
            
            return render_template("editar_documento_terminado.html", 
                                 documento=documento, 
                                 casos=casos,
                                 contenido_texto=contenido_texto,
                                 contenido_html=contenido_html,
                                 campos_detectados=campos_detectados)
    
    contenido_html = ""
    if contenido_texto and campos_detectados:
        contenido_html = generate_highlighted_html(contenido_texto, campos_detectados)
    elif contenido_texto:
        from markupsafe import escape
        contenido_html = str(escape(contenido_texto)).replace('\n', '<br>')
    
    return render_template("editar_documento_terminado.html", 
                         documento=documento, 
                         casos=casos,
                         contenido_texto=contenido_texto,
                         contenido_html=contenido_html,
                         campos_detectados=campos_detectados)


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
        return jsonify({'response': 'Por favor escribe tu pregunta.'})
    
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": """Eres una guia de ayuda amigable para una plataforma de gestion legal. Tu trabajo es explicar paso a paso como usar la plataforma.

ESTILO DE COMUNICACION:
- Habla de forma muy simple y amigable, como si fueras un amigo explicando
- Usa emojis ocasionalmente para ser mas cercano
- Da instrucciones paso a paso muy claras
- Menciona exactamente donde hacer clic y que botones presionar
- Da recomendaciones utiles

FUNCIONES DE LA PLATAFORMA QUE CONOCES:

1. CASOS:
   - Para crear un caso: Menu lateral izquierdo > "Casos" > boton "Nuevo Caso"
   - Llenar: nombre del caso, cliente, descripcion, fecha
   - Los casos agrupan todos los documentos y tareas de un cliente

2. DOCUMENTOS:
   - Para generar documento: Menu "Generar Documento" o desde un caso
   - Seleccionar modelo/plantilla, llenar los campos, presionar "Generar"
   - Los documentos se guardan automaticamente

3. MIS MODELOS:
   - Crear modelos personalizados en "Mis Modelos" > "Nuevo Modelo"
   - Subir un documento Word como plantilla
   - Agregar campos dinamicos que se llenaran automaticamente

4. ANONIMIZAR DOCUMENTOS:
   - En "Mis Modelos" > boton "Anonimiza tu documento"
   - Subir un documento y el sistema quita datos sensibles automaticamente

5. HISTORIAL:
   - Ver todos los documentos generados en "Historial"
   - Filtrar por fecha, tipo de documento, etc.

6. ADMINISTRACION (solo admins):
   - Agregar usuarios: "Admin" > "Usuarios" > "Nuevo Usuario"
   - Configurar estudio: "Admin" > "Configurar Estudio"

7. TAREAS:
   - Crear tareas desde un caso o menu "Tareas"
   - Asignar a usuarios, poner fechas limite

Siempre responde en espanol de forma clara y amigable."""},
                {"role": "user", "content": message}
            ],
            temperature=0.7,
            max_tokens=600
        )
        ai_response = response.choices[0].message.content
        return jsonify({'response': ai_response})
    except Exception as e:
        logging.error(f"Error AI Assistant: {e}")
        return jsonify({'response': 'Ups! Algo salio mal. Intenta de nuevo por favor.'})


# ==================== DOCUMENT ANONYMIZER ====================

import uuid

CARPETA_ANONIMIZADOS = "documentos_anonimizados"


def get_anonimizados_folder(tenant_id, user_id):
    """Get tenant-scoped and user-scoped folder for anonymized documents."""
    folder = os.path.join(CARPETA_ANONIMIZADOS, f"tenant_{tenant_id}", f"user_{user_id}")
    os.makedirs(folder, exist_ok=True)
    return folder


def anonimizar_con_ia(texto):
    """Use OpenAI to identify and anonymize sensitive information in legal documents."""
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        prompt = """Analiza el siguiente documento legal y devuelve un JSON con dos claves:
        
1. "categorias": Un diccionario donde cada clave es una categoria de informacion sensible y el valor es una lista de los elementos encontrados. Las categorias deben ser:
   - "Nombres de personas": nombres completos de personas
   - "DNI/Documentos de identidad": numeros de DNI, RUC, pasaportes
   - "Direcciones": direcciones fisicas, domicilios
   - "Telefonos": numeros de telefono
   - "Montos de dinero": cantidades monetarias (S/, USD, etc)
   - "Fechas especificas": fechas exactas mencionadas
   - "Numeros de expediente": numeros de casos o expedientes
   - "Correos electronicos": direcciones de email
   - "Entidades/Instituciones": nombres de empresas, juzgados, etc.
   - "Otros datos sensibles": cualquier otra informacion identificable

2. "texto_anonimizado": El texto completo del documento con toda la informacion sensible reemplazada por "........................" (24 puntos).

IMPORTANTE:
- Solo incluye categorias que tengan elementos encontrados
- Mantiene la estructura y formato del documento original
- Reemplaza CADA ocurrencia de informacion sensible

DOCUMENTO A ANALIZAR:
"""
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Eres un experto en proteccion de datos personales y anonimizacion de documentos legales. Respondes SOLO con JSON valido, sin texto adicional."},
                {"role": "user", "content": prompt + texto}
            ],
            temperature=0.3,
            max_tokens=4000,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        logging.error(f"Error anonimizando documento: {e}")
        return None


def guardar_docx_anonimizado(texto, nombre_archivo, tenant_id, user_id):
    """Save anonymized text as Word document in tenant-scoped folder."""
    folder = get_anonimizados_folder(tenant_id, user_id)
    
    doc = Document()
    
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)
    
    for para_text in texto.split('\n'):
        if para_text.strip():
            p = doc.add_paragraph(para_text)
            p.paragraph_format.line_spacing = 1.5
        else:
            doc.add_paragraph()
    
    file_path = os.path.join(folder, nombre_archivo)
    doc.save(file_path)
    return file_path


@app.route("/anonimizar-documento", methods=["GET", "POST"])
@login_required
def anonimizar_documento():
    if request.method == "GET":
        return render_template("anonimizar_documento.html")
    
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta funcion.", "error")
        return redirect(url_for('index'))
    
    if 'documento' not in request.files:
        flash("Por favor selecciona un documento.", "error")
        return redirect(url_for('anonimizar_documento'))
    
    archivo = request.files['documento']
    if archivo.filename == '':
        flash("No se selecciono ningun archivo.", "error")
        return redirect(url_for('anonimizar_documento'))
    
    ext = os.path.splitext(archivo.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        flash("Formato no permitido. Usa .docx, .pdf o .txt", "error")
        return redirect(url_for('anonimizar_documento'))
    
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex[:8]
    filename = secure_filename(archivo.filename)
    temp_filename = f"{unique_id}_{filename}"
    temp_path = os.path.join(temp_dir, temp_filename)
    archivo.save(temp_path)
    
    try:
        texto_original = extract_text_from_file(temp_path)
        
        if not texto_original or len(texto_original.strip()) < 50:
            flash("El documento esta vacio o no se pudo extraer el texto.", "error")
            os.remove(temp_path)
            return redirect(url_for('anonimizar_documento'))
        
        resultado = anonimizar_con_ia(texto_original)
        
        if not resultado:
            flash("Error al procesar el documento. Intenta de nuevo.", "error")
            os.remove(temp_path)
            return redirect(url_for('anonimizar_documento'))
        
        categorias = resultado.get('categorias', {})
        texto_anonimizado = resultado.get('texto_anonimizado', texto_original)
        
        categorias_no_vacias = {k: v for k, v in categorias.items() if v}
        
        total_anonimizado = sum(len(items) for items in categorias_no_vacias.values())
        
        random_suffix = uuid.uuid4().hex[:12]
        nombre_base = os.path.splitext(filename)[0][:20]
        nombre_archivo = f"ANON_{random_suffix}.docx"
        
        guardar_docx_anonimizado(texto_anonimizado, nombre_archivo, tenant.id, current_user.id)
        
        os.remove(temp_path)
        
        return render_template("anonimizar_documento.html",
                             resultado=True,
                             categorias=categorias_no_vacias,
                             texto_anonimizado=texto_anonimizado,
                             total_anonimizado=total_anonimizado,
                             nombre_archivo=nombre_archivo)
    
    except Exception as e:
        logging.error(f"Error en anonimizacion: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        flash("Error al procesar el documento.", "error")
        return redirect(url_for('anonimizar_documento'))


@app.route("/descargar-anonimizado/<nombre>")
@login_required
def descargar_anonimizado(nombre):
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a este archivo.", "error")
        return redirect(url_for('index'))
    
    folder = get_anonimizados_folder(tenant.id, current_user.id)
    file_path = os.path.join(folder, nombre)
    
    if not os.path.exists(file_path):
        flash("Archivo no encontrado.", "error")
        return redirect(url_for('anonimizar_documento'))
    
    return send_from_directory(folder, nombre, as_attachment=True)


# ==================== REVISOR IA ====================

CARPETA_REVISIONES = "revisiones_temp"

def get_revisiones_folder(tenant_id):
    folder = os.path.join(CARPETA_REVISIONES, f"tenant_{tenant_id}")
    os.makedirs(folder, exist_ok=True)
    return folder


def revisar_documento_con_ia(texto_documento, nombre_documento):
    """Analiza un documento legal con IA para detectar errores y problemas."""
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        system_prompt = """Eres un revisor legal experto peruano especializado en documentos jurídicos. Tu tarea es revisar documentos legales y detectar problemas en las siguientes categorías:

1. COHERENCIA JURÍDICA: Verifica que los fundamentos legales sean correctos, que las normas citadas existan y apliquen al caso, y que los argumentos sean lógicamente consistentes.

2. CONTRADICCIONES: Detecta inconsistencias en los datos (nombres, fechas, montos, DNI que no coinciden a lo largo del documento), afirmaciones contradictorias, o hechos que se contradicen entre sí.

3. ESTRUCTURA: Verifica que el documento tenga la estructura correcta para su tipo (demanda, contestación, recurso, etc.), que contenga todas las secciones necesarias y en el orden correcto.

4. CAMPOS INCOMPLETOS: Identifica campos vacíos, placeholders no reemplazados (como {{nombre}}, [COMPLETAR], XXX), datos faltantes obligatorios, o información incompleta.

Responde ÚNICAMENTE en formato JSON válido con la siguiente estructura:
{
    "evaluacion_general": "Breve resumen del estado general del documento (1-2 oraciones)",
    "issues": [
        {
            "severidad": "error|advertencia|sugerencia",
            "tipo": "coherencia|contradiccion|estructura|campo_incompleto",
            "ubicacion": "Sección o parte del documento donde se encuentra el problema",
            "fragmento": "Texto exacto donde se detectó el problema (máximo 100 caracteres)",
            "descripcion": "Descripción clara del problema encontrado",
            "recomendacion": "Cómo corregir el problema"
        }
    ]
}

Severidades:
- "error": Problemas graves que deben corregirse obligatoriamente (contradicciones de datos, campos vacíos críticos)
- "advertencia": Problemas importantes que deberían revisarse (posibles inconsistencias, estructura mejorable)
- "sugerencia": Mejoras opcionales o recomendaciones de estilo

Si el documento está bien, devuelve issues como array vacío."""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Revisa el siguiente documento legal llamado '{nombre_documento}':\n\n{texto_documento[:15000]}"}
            ],
            temperature=0.3,
            max_tokens=4000,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result
        
    except Exception as e:
        logging.error(f"Error en revisión IA: {e}")
        return None


@app.route("/revisor-ia")
@login_required
def revisor_ia():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    # Obtener revisiones anteriores del usuario
    revisiones = ReviewSession.query.filter_by(
        tenant_id=tenant.id,
        user_id=current_user.id
    ).order_by(ReviewSession.created_at.desc()).limit(10).all()
    
    # Obtener documentos recientes para seleccionar
    documentos_recientes = FinishedDocument.query.filter_by(
        tenant_id=tenant.id,
        user_id=current_user.id
    ).order_by(FinishedDocument.created_at.desc()).limit(20).all()
    
    return render_template("revisor_ia.html",
                          revisiones=revisiones,
                          documentos_recientes=documentos_recientes)


@app.route("/revisor-ia/analizar", methods=["POST"])
@login_required
def revisor_ia_analizar():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    texto_documento = None
    nombre_documento = "Documento"
    archivo_path = None
    
    # Opción 1: Subir archivo
    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        filename = secure_filename(archivo.filename)
        ext = os.path.splitext(filename)[1].lower()
        
        if ext not in ['.docx', '.txt', '.pdf']:
            flash("Formato no soportado. Use archivos .docx, .txt o .pdf", "error")
            return redirect(url_for('revisor_ia'))
        
        folder = get_revisiones_folder(tenant.id)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        temp_path = os.path.join(folder, unique_name)
        archivo.save(temp_path)
        archivo_path = temp_path
        nombre_documento = filename
        
        # Extraer texto
        if ext == '.docx':
            try:
                doc = Document(temp_path)
                texto_documento = "\n".join([p.text for p in doc.paragraphs])
            except Exception as e:
                logging.error(f"Error leyendo docx: {e}")
                flash("Error al leer el archivo Word.", "error")
                return redirect(url_for('revisor_ia'))
        elif ext == '.txt':
            with open(temp_path, 'r', encoding='utf-8') as f:
                texto_documento = f.read()
        elif ext == '.pdf':
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(temp_path)
                texto_documento = "\n".join([page.extract_text() or "" for page in reader.pages])
            except Exception as e:
                logging.error(f"Error leyendo PDF: {e}")
                flash("Error al leer el archivo PDF.", "error")
                return redirect(url_for('revisor_ia'))
    
    # Opción 2: Seleccionar documento existente
    documento_id = request.form.get("documento_id", type=int)
    if not texto_documento and documento_id:
        doc = FinishedDocument.query.filter_by(
            id=documento_id,
            tenant_id=tenant.id
        ).first()
        if doc:
            nombre_documento = doc.nombre_documento
            # Leer el contenido del archivo
            if doc.archivo_path and os.path.exists(doc.archivo_path):
                try:
                    docx = Document(doc.archivo_path)
                    texto_documento = "\n".join([p.text for p in docx.paragraphs])
                except:
                    pass
            if not texto_documento and doc.contenido_texto:
                texto_documento = doc.contenido_texto
    
    # Opción 3: Texto pegado directamente
    texto_directo = request.form.get("texto_documento", "").strip()
    if not texto_documento and texto_directo:
        texto_documento = texto_directo
        nombre_documento = "Texto pegado"
    
    if not texto_documento:
        flash("No se proporcionó ningún documento para revisar.", "error")
        return redirect(url_for('revisor_ia'))
    
    # Crear sesión de revisión
    review = ReviewSession(
        tenant_id=tenant.id,
        user_id=current_user.id,
        nombre_documento=nombre_documento,
        archivo_path=archivo_path,
        contenido_texto=texto_documento[:50000],
        estado='procesando'
    )
    db.session.add(review)
    db.session.commit()
    
    # Analizar con IA
    resultado = revisar_documento_con_ia(texto_documento, nombre_documento)
    
    if not resultado:
        review.estado = 'error'
        db.session.commit()
        flash("Error al analizar el documento. Intenta de nuevo.", "error")
        return redirect(url_for('revisor_ia'))
    
    # Guardar resultados
    review.evaluacion_general = resultado.get('evaluacion_general', '')
    review.estado = 'completado'
    review.completed_at = datetime.utcnow()
    
    errores = 0
    advertencias = 0
    sugerencias = 0
    
    for i, issue in enumerate(resultado.get('issues', [])):
        severidad = issue.get('severidad', 'sugerencia')
        if severidad == 'error':
            errores += 1
        elif severidad == 'advertencia':
            advertencias += 1
        else:
            sugerencias += 1
        
        review_issue = ReviewIssue(
            session_id=review.id,
            severidad=severidad,
            tipo=issue.get('tipo', 'otro'),
            ubicacion=issue.get('ubicacion', ''),
            fragmento=issue.get('fragmento', ''),
            descripcion=issue.get('descripcion', ''),
            recomendacion=issue.get('recomendacion', ''),
            orden=i
        )
        db.session.add(review_issue)
    
    review.total_errores = errores
    review.total_advertencias = advertencias
    review.total_sugerencias = sugerencias
    db.session.commit()
    
    return redirect(url_for('revisor_ia_resultado', review_id=review.id))


@app.route("/revisor-ia/resultado/<int:review_id>")
@login_required
def revisor_ia_resultado(review_id):
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    review = ReviewSession.query.filter_by(
        id=review_id,
        tenant_id=tenant.id
    ).first_or_404()
    
    issues = ReviewIssue.query.filter_by(session_id=review.id).order_by(
        db.case(
            (ReviewIssue.severidad == 'error', 1),
            (ReviewIssue.severidad == 'advertencia', 2),
            else_=3
        ),
        ReviewIssue.orden
    ).all()
    
    return render_template("revisor_ia_resultado.html",
                          review=review,
                          issues=issues)


@app.route("/revisor-ia/historial")
@login_required
def revisor_ia_historial():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    revisiones = ReviewSession.query.filter_by(
        tenant_id=tenant.id,
        user_id=current_user.id
    ).order_by(ReviewSession.created_at.desc()).all()
    
    return render_template("revisor_ia_historial.html", revisiones=revisiones)


with app.app_context():
    db.create_all()
    os.makedirs(CARPETA_RESULTADOS, exist_ok=True)
    os.makedirs(CARPETA_PLANTILLAS_SUBIDAS, exist_ok=True)
    os.makedirs(CARPETA_ESTILOS_SUBIDOS, exist_ok=True)
    os.makedirs(CARPETA_ANONIMIZADOS, exist_ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
