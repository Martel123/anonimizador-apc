"""
Anonimizador Legal Público - App para Render + Blueprint para Replit
=====================================================================
Flujo: SUBIR → PREVIEW COMPLETO → LISTA (seleccionar/deseleccionar) → APLICAR → DESCARGAR
Confidencialidad real: solo archivos temporales en /tmp, borrados siempre en finally
"""

import os
import re
import uuid
import json
import logging
import tempfile
import traceback
import zipfile
from io import BytesIO
from flask import Flask, Blueprint, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

anonymizer_bp = Blueprint("anonymizer", __name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB

ALLOWED_EXTENSIONS = {'doc', 'docx', 'pdf', 'txt'}

# ============================================================================
# UTILIDADES
# ============================================================================

def check_openai_available():
    """Verifica que OPENAI_API_KEY esté configurada. OBLIGATORIO por negocio."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return bool(key and len(key) > 10)


def safe_remove(path):
    """Elimina archivo de forma segura."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
            logger.info(f"CLEANUP | removed={path}")
        except Exception as e:
            logger.warning(f"CLEANUP_FAIL | path={path} | error={e}")


def allowed_file(filename):
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def get_extension(filename):
    if '.' in filename:
        return filename.rsplit('.', 1)[1].lower()
    return ''


def get_output_extension(input_ext):
    """
    Normaliza extensión de salida:
    - DOCX → DOCX
    - TXT → TXT
    - PDF → TXT (texto plano)
    - DOC → TXT (texto plano)
    """
    if input_ext == 'docx':
        return 'docx'
    elif input_ext == 'txt':
        return 'txt'
    else:
        return 'txt'


def validate_file_format(file_path, ext):
    """Valida formato real del archivo."""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(16)
        
        if ext == 'pdf':
            if not header.startswith(b'%PDF'):
                return False, "El archivo no parece ser un PDF válido"
        
        if ext == 'docx':
            if not zipfile.is_zipfile(file_path):
                return False, "El archivo no parece ser un DOCX válido"
        
        return True, None
    except Exception as e:
        return False, f"Error validando archivo: {str(e)}"


def render_error(message, status_code=400):
    """Renderiza página de error limpia sin stacktrace."""
    return render_template("anonymizer_standalone.html",
                           error=message,
                           openai_available=check_openai_available()), status_code


# ============================================================================
# NORMALIZACIÓN DE ENTIDADES CON CANDIDATES
# ============================================================================

def normalize_entity(ent_dict):
    """
    Normaliza una entidad y genera candidates.
    Returns dict con: value, candidates, type, confidence, source
    """
    value_base = ent_dict.get('value') or ent_dict.get('text') or ''
    if not value_base:
        return None
    
    original = value_base
    
    normalized = value_base.strip()
    normalized = re.sub(r'[\r\n\t]+', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized)
    
    no_newlines = re.sub(r'\n+', ' ', original)
    no_newlines = re.sub(r'\s+', ' ', no_newlines).strip()
    
    no_spaces = normalized.replace(' ', '') if len(normalized) >= 4 else None
    
    candidates = []
    seen_lower = set()
    
    for c in [original, normalized, no_newlines, no_spaces]:
        if c and len(c) >= 4:
            c_lower = c.lower()
            if c_lower not in seen_lower:
                seen_lower.add(c_lower)
                candidates.append(c)
    
    if not candidates:
        return None
    
    return {
        'value': normalized,
        'text': normalized,
        'original': original,
        'candidates': candidates,
        'type': ent_dict.get('type') or ent_dict.get('entity_type') or 'UNKNOWN',
        'confidence': float(ent_dict.get('confidence', 1.0)),
        'source': ent_dict.get('source', 'detector'),
        'start': ent_dict.get('start', 0),
        'end': ent_dict.get('end', 0)
    }


def normalize_entities(items):
    """Normaliza lista de entidades, agrega candidates."""
    if not items:
        return []
    result = []
    for e in items:
        if isinstance(e, dict):
            normalized = normalize_entity(e)
        else:
            d = {
                'type': getattr(e, 'type', 'UNKNOWN'),
                'value': getattr(e, 'value', ''),
                'start': getattr(e, 'start', 0),
                'end': getattr(e, 'end', 0),
                'confidence': getattr(e, 'confidence', 1.0),
                'source': getattr(e, 'source', 'detector')
            }
            normalized = normalize_entity(d)
        
        if normalized:
            result.append(normalized)
    return result


def deduplicate_entities(entities):
    """Deduplica entidades por (type, value.lower())."""
    seen = set()
    result = []
    for e in entities:
        key = (e['type'].upper(), e['value'].lower())
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


# ============================================================================
# EXTRACCIÓN DE TEXTO
# ============================================================================

def extract_full_text_docx(doc):
    """Extrae todo el texto del documento DOCX incluyendo tablas, headers, footers."""
    text_parts = []
    
    for para in doc.paragraphs:
        text_parts.append(para.text)
    
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    text_parts.append(para.text)
    
    try:
        for section in doc.sections:
            if section.header:
                for para in section.header.paragraphs:
                    text_parts.append(para.text)
            if section.footer:
                for para in section.footer.paragraphs:
                    text_parts.append(para.text)
    except:
        pass
    
    return '\n'.join(text_parts)


def extract_text(file_path, ext):
    """Extrae texto de un archivo."""
    if ext == 'docx':
        from docx import Document
        doc = Document(file_path)
        return extract_full_text_docx(doc)
    
    elif ext == 'pdf':
        from processor_pdf import extract_text_pdf
        result = extract_text_pdf(file_path)
        if result.get('success'):
            return result.get('text', '')
        raise ValueError(result.get('error', 'Error extrayendo texto del PDF'))
    
    elif ext == 'txt':
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    
    elif ext == 'doc':
        raise ValueError("Formato DOC no soportado directamente. Por favor convierta a DOCX.")
    
    return ''


# ============================================================================
# APLICACIÓN DE ANONIMIZACIÓN
# ============================================================================

def expand_entities_with_candidates(entity_dicts):
    """
    Expande entidades con todos sus candidates para reemplazo MUY ALTO.
    Cada candidate genera una Entity separada.
    """
    from detector_capas import Entity
    
    entities = []
    seen = set()
    
    for d in entity_dicts:
        ent_type = d.get('type', 'UNKNOWN')
        confidence = d.get('confidence', 1.0)
        source = d.get('source', 'detector')
        
        candidates = d.get('candidates', [])
        value = d.get('value', '')
        
        all_values = set(candidates) if candidates else set()
        if value and len(value) >= 4:
            all_values.add(value)
        
        for candidate in all_values:
            if not candidate or len(candidate) < 4:
                continue
            
            key = (ent_type.lower(), candidate.lower())
            if key in seen:
                continue
            seen.add(key)
            
            entities.append(Entity(
                type=ent_type,
                value=candidate,
                start=0,
                end=len(candidate),
                source=source,
                confidence=confidence
            ))
    
    return entities


def apply_entities_to_docx(input_path, output_path, entity_dicts):
    """Aplica anonimización a DOCX con candidates expandidos."""
    from docx import Document
    from processor_docx import EntityMapping, process_docx_run_aware
    
    doc = Document(input_path)
    mapping = EntityMapping()
    
    expanded_entities = expand_entities_with_candidates(entity_dicts)
    
    stats = process_docx_run_aware(doc, expanded_entities, mapping)
    doc.save(output_path)
    
    return stats['replacements'], mapping.reverse_mappings


def apply_entities_to_text(input_path, output_path, entity_dicts, ext='txt'):
    """Aplica anonimización a texto plano."""
    from collections import defaultdict
    
    text = extract_text(input_path, ext)
    
    counters = defaultdict(int)
    mapping = {}
    
    all_replacements = []
    for d in entity_dicts:
        ent_type = d.get('type', 'UNKNOWN')
        candidates = d.get('candidates', [])
        value = d.get('value', '')
        
        values = set(candidates) if candidates else set()
        if value:
            values.add(value)
        
        for v in values:
            if v and len(v) >= 4:
                all_replacements.append((v, ent_type))
    
    all_replacements.sort(key=lambda x: len(x[0]), reverse=True)
    
    replaced_count = 0
    for value, ent_type in all_replacements:
        if value in text:
            if value not in [m.get('original') for m in mapping.values()]:
                counters[ent_type] += 1
                token = f"{{{{{ent_type}_{counters[ent_type]}}}}}"
                mapping[token] = {'original': value, 'type': ent_type}
                count = text.count(value)
                text = text.replace(value, token)
                replaced_count += count
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(text)
    
    reverse_mapping = {k: v['original'][:20] + '...' if len(v['original']) > 20 else v['original'] 
                       for k, v in mapping.items()}
    
    return replaced_count, reverse_mapping


# ============================================================================
# ENDPOINTS
# ============================================================================

@anonymizer_bp.route("/health")
@app.route("/health")
def health():
    """Health check endpoint."""
    return "ok"


@anonymizer_bp.route("/")
@app.route("/")
def index():
    """Página principal de subida."""
    openai_available = check_openai_available()
    return render_template("anonymizer_standalone.html", 
                           openai_available=openai_available)


@anonymizer_bp.route("/anonymizer")
@app.route("/anonymizer")
def anonymizer_home():
    """Alias para página principal."""
    openai_available = check_openai_available()
    return render_template("anonymizer_standalone.html",
                           openai_available=openai_available)


@anonymizer_bp.route("/anonymizer/process", methods=["POST"])
@app.route("/anonymizer/process", methods=["POST"])
def anonymizer_process():
    """
    Procesa archivo y muestra página de revisión.
    Guarda archivo temporal que será borrado en /apply.
    """
    if not check_openai_available():
        return render_error("El servicio no está disponible en este momento. Contacte al administrador.", 503)
    
    if 'file' not in request.files:
        return render_error("No se seleccionó ningún archivo")
    
    file = request.files['file']
    if not file or not file.filename:
        return render_error("El archivo está vacío")
    
    filename = secure_filename(file.filename)
    ext = get_extension(filename)
    
    if not allowed_file(filename):
        return render_error(f"Formato no soportado: .{ext}. Use DOCX, PDF o TXT")
    
    # Get options from form
    strict_mode = request.form.get('strict_mode', 'true').lower() == 'true'
    export_csv = request.form.get('export_csv', 'false').lower() == 'true'
    
    job_id = str(uuid.uuid4())
    temp_input = os.path.join(tempfile.gettempdir(), f"in_{job_id}_{filename}")
    
    try:
        file.save(temp_input)
        file_size = os.path.getsize(temp_input)
        logger.info(f"UPLOAD | job={job_id} | file={filename} | ext={ext} | size={file_size}")
        
        valid, error_msg = validate_file_format(temp_input, ext)
        if not valid:
            safe_remove(temp_input)
            return render_error(error_msg)
        
        full_text = extract_text(temp_input, ext)
        
        if not full_text or len(full_text.strip()) < 10:
            safe_remove(temp_input)
            return render_error("No se pudo leer el contenido del documento")
        
        from detector_capas import detect_all_pii
        entities, detect_meta = detect_all_pii(full_text)
        all_entities = normalize_entities(entities)
        all_entities = deduplicate_entities(all_entities)
        
        confirmed = []
        needs_review = []
        
        for i, ent in enumerate(all_entities):
            ent['index'] = i
            conf = ent.get('confidence', 1.0)
            
            if conf >= 0.80:
                ent['status'] = 'confirmed'
                confirmed.append(ent)
            elif conf >= 0.50:
                ent['status'] = 'needs_review'
                needs_review.append(ent)
        
        entities_all = confirmed + needs_review
        
        confirmed_by_type = {}
        needs_review_by_type = {}
        
        for e in confirmed:
            t = e['type']
            if t not in confirmed_by_type:
                confirmed_by_type[t] = []
            confirmed_by_type[t].append(e)
        
        for e in needs_review:
            t = e['type']
            if t not in needs_review_by_type:
                needs_review_by_type[t] = []
            needs_review_by_type[t].append(e)
        
        logger.info(f"DETECT_OK | job={job_id} | confirmed={len(confirmed)} | needs_review={len(needs_review)}")
        
        return render_template("anonymizer_review.html",
            temp_input_path=temp_input,
            ext=ext,
            original_filename=filename,
            job_id=job_id,
            full_text=full_text,
            preview_text=full_text,
            confirmed=confirmed,
            needs_review=needs_review,
            confirmed_by_type=confirmed_by_type,
            needs_review_by_type=needs_review_by_type,
            entities_all=entities_all,
            confirmed_count=len(confirmed),
            needs_review_count=len(needs_review),
            strict_mode=strict_mode,
            export_csv=export_csv
        )
        
    except Exception as e:
        safe_remove(temp_input)
        logger.error(f"PROCESS_ERROR | job={job_id} | error={e}")
        logger.error(traceback.format_exc())
        return render_error("No se pudo procesar el documento. Verifique el formato e intente nuevamente.")


@anonymizer_bp.route("/anonymizer/apply", methods=["POST"])
@app.route("/anonymizer/apply", methods=["POST"])
def anonymizer_apply():
    """
    Aplica anonimización y devuelve archivo directamente.
    
    REGLA DE ORO:
    - Siempre genera archivo o falla claro
    - Siempre descarga directamente (sin redirección)
    - Siempre borra temporales en finally
    - Verifica que archivo final existe y tamaño > 0
    """
    import html as html_lib
    
    if not check_openai_available():
        return render_error("El servicio no está disponible en este momento.", 503)
    
    temp_input = request.form.get('temp_input_path', '')
    ext = request.form.get('ext', 'docx')
    original_filename = request.form.get('original_filename', 'documento')
    selected_entities_json = request.form.get('selected_entities_json', '[]')
    
    if not temp_input or not os.path.exists(temp_input):
        return render_error("Sesión expirada. Suba el documento nuevamente.")
    
    job_id = str(uuid.uuid4())
    output_ext = get_output_extension(ext)
    temp_output = os.path.join(tempfile.gettempdir(), f"out_{job_id}.{output_ext}")
    
    logger.info(f"APPLY_START | job={job_id} | ext={ext} | output_ext={output_ext}")
    
    try:
        selected_entities_json = html_lib.unescape(selected_entities_json)
        selected_entities = json.loads(selected_entities_json)
        
        entity_count = len(selected_entities) if selected_entities else 0
        logger.info(f"APPLY_START | job={job_id} | entities={entity_count}")
        
        if not selected_entities:
            safe_remove(temp_input)
            return render_error("No se seleccionaron entidades para anonimizar.")
        
        if ext == 'docx':
            replaced_count, mapping = apply_entities_to_docx(temp_input, temp_output, selected_entities)
            mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:
            replaced_count, mapping = apply_entities_to_text(temp_input, temp_output, selected_entities, ext)
            mimetype = 'text/plain; charset=utf-8'
        
        if not os.path.exists(temp_output):
            logger.error(f"APPLY_FAIL | job={job_id} | reason=output_not_created")
            return render_error("No se pudo generar el archivo final. Intente nuevamente.")
        
        output_size = os.path.getsize(temp_output)
        if output_size == 0:
            logger.error(f"APPLY_FAIL | job={job_id} | reason=output_empty")
            return render_error("No se pudo generar el archivo final. Intente nuevamente.")
        
        logger.info(f"APPLY_DONE | job={job_id} | replaced={replaced_count} | output_size={output_size}")
        
        with open(temp_output, 'rb') as f:
            output_bytes = f.read()
        
        base_name = original_filename.rsplit('.', 1)[0] if '.' in original_filename else original_filename
        download_name = f"{base_name}_anonimizado.{output_ext}"
        
        output_buffer = BytesIO(output_bytes)
        
        logger.info(f"DOWNLOAD_OK | job={job_id} | filename={download_name}")
        
        return send_file(
            output_buffer,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"APPLY_FAIL | job={job_id} | reason=json_error | error={e}")
        logger.error(traceback.format_exc())
        return render_error("Error procesando la selección de entidades. Intente nuevamente.")
    
    except Exception as e:
        logger.error(f"APPLY_FAIL | job={job_id} | error={e}")
        logger.error(traceback.format_exc())
        return render_error("No se pudo generar el archivo final. Verifique el documento e intente nuevamente.")
    
    finally:
        safe_remove(temp_input)
        safe_remove(temp_output)


app.register_blueprint(anonymizer_bp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
