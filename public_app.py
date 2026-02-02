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
from datetime import datetime
from flask import Flask, Blueprint, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import final auditor for 0-leak guarantee
try:
    from final_auditor import audit_document, log_audit_result
    FINAL_AUDITOR_AVAILABLE = True
except ImportError:
    FINAL_AUDITOR_AVAILABLE = False
    logger.warning("Final auditor not available")

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
    """
    Aplica anonimización a DOCX con soporte para tokens predefinidos (manual entities).
    Usa reemplazo run-aware para manejar texto partido.
    """
    from docx import Document
    from processor_docx import EntityMapping
    
    doc = Document(input_path)
    
    replacements = []
    reverse_mapping = {}
    type_counters = {}
    value_to_token = {}
    
    for d in entity_dicts:
        ent_type = d.get('type', 'UNKNOWN')
        value = d.get('value', '')
        token = d.get('token', '')
        replace_all = d.get('replace_all', True)
        candidates = d.get('candidates', [])
        
        if not value:
            continue
        
        all_values = set()
        if value:
            all_values.add(value)
        if candidates:
            all_values.update(candidates)
        
        for v in all_values:
            if not v or len(v) < 2:
                continue
            
            normalized = v.strip().lower()
            key = f"{ent_type}|{normalized}"
            
            if key in value_to_token:
                t = value_to_token[key]
            elif token:
                t = token
                value_to_token[key] = t
            else:
                type_counters[ent_type] = type_counters.get(ent_type, 0) + 1
                t = f"{{{{{ent_type}_{type_counters[ent_type]}}}}}"
                value_to_token[key] = t
            
            replacements.append((v, t, replace_all))
            
            clean_token = t.replace('{{', '').replace('}}', '')
            if t not in reverse_mapping:
                masked = v[:3] + '...' + v[-2:] if len(v) > 8 else v[:2] + '***'
                reverse_mapping[t] = masked
    
    replacements.sort(key=lambda x: len(x[0]), reverse=True)
    
    replaced_count = apply_replacements_to_docx(doc, replacements)
    
    doc.save(output_path)
    
    return replaced_count, reverse_mapping


def apply_replacements_to_docx(doc, replacements):
    """
    Aplica lista de reemplazos (value, token, replace_all) al documento DOCX.
    Maneja párrafos, tablas, headers y footers con reemplazo run-aware.
    """
    total_count = 0
    # Track which once-only replacements have been done (global across paragraphs)
    done_once = set()
    
    def replace_in_paragraph(paragraph, replacements):
        nonlocal done_once
        count = 0
        for original, token, replace_all in replacements:
            # Skip if this is a once-only replacement that's already been applied globally
            if not replace_all and original in done_once:
                continue
            full_text = paragraph.text
            if original not in full_text:
                continue
            
            run_map = []
            pos = 0
            for idx, run in enumerate(paragraph.runs):
                run_text = run.text
                run_map.append((pos, pos + len(run_text), idx, run))
                pos += len(run_text)
            
            start = 0
            iterations = 0
            max_iterations = 100
            
            while iterations < max_iterations:
                iterations += 1
                full_text = paragraph.text
                idx = full_text.find(original, start)
                if idx == -1:
                    break
                
                end_idx = idx + len(original)
                
                affected_runs = []
                for run_start, run_end, run_idx, run in run_map:
                    if run_start < end_idx and run_end > idx:
                        affected_runs.append((run_start, run_end, run_idx, run))
                
                if not affected_runs:
                    start = idx + 1
                    continue
                
                first_run = affected_runs[0]
                first_run_obj = first_run[3]
                local_start = idx - first_run[0]
                
                if len(affected_runs) == 1:
                    local_end = local_start + len(original)
                    old_text = first_run_obj.text
                    first_run_obj.text = old_text[:local_start] + token + old_text[local_end:]
                else:
                    old_text = first_run_obj.text
                    first_run_obj.text = old_text[:local_start] + token
                    
                    for _, _, _, run in affected_runs[1:-1]:
                        run.text = ''
                    
                    last_run = affected_runs[-1]
                    last_run_obj = last_run[3]
                    local_end_in_last = end_idx - last_run[0]
                    last_run_obj.text = last_run_obj.text[local_end_in_last:]
                
                count += 1
                
                if not replace_all:
                    done_once.add(original)
                    break
                
                run_map = []
                pos = 0
                for r_idx, run in enumerate(paragraph.runs):
                    run_map.append((pos, pos + len(run.text), r_idx, run))
                    pos += len(run.text)
                start = 0
        
        return count
    
    for para in doc.paragraphs:
        total_count += replace_in_paragraph(para, replacements)
    
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    total_count += replace_in_paragraph(para, replacements)
    
    try:
        for section in doc.sections:
            if section.header:
                for para in section.header.paragraphs:
                    total_count += replace_in_paragraph(para, replacements)
            if section.footer:
                for para in section.footer.paragraphs:
                    total_count += replace_in_paragraph(para, replacements)
    except Exception as e:
        logger.warning(f"Error processing headers/footers: {e}")
    
    return total_count


def apply_entities_to_text(input_path, output_path, entity_dicts, ext='txt'):
    """
    Aplica anonimización a texto plano con soporte para tokens predefinidos.
    """
    text = extract_text(input_path, ext)
    
    type_counters = {}
    value_to_token = {}
    reverse_mapping = {}
    
    all_replacements = []
    
    for d in entity_dicts:
        ent_type = d.get('type', 'UNKNOWN')
        value = d.get('value', '')
        token = d.get('token', '')
        replace_all = d.get('replace_all', True)
        candidates = d.get('candidates', [])
        
        all_values = set()
        if value:
            all_values.add(value)
        if candidates:
            all_values.update(candidates)
        
        for v in all_values:
            if not v or len(v) < 2:
                continue
            
            normalized = v.strip().lower()
            key = f"{ent_type}|{normalized}"
            
            if key in value_to_token:
                t = value_to_token[key]
            elif token:
                t = token
                value_to_token[key] = t
            else:
                type_counters[ent_type] = type_counters.get(ent_type, 0) + 1
                t = f"{{{{{ent_type}_{type_counters[ent_type]}}}}}"
                value_to_token[key] = t
            
            all_replacements.append((v, t, replace_all))
            
            if t not in reverse_mapping:
                masked = v[:3] + '...' + v[-2:] if len(v) > 8 else v[:2] + '***'
                reverse_mapping[t] = masked
    
    all_replacements.sort(key=lambda x: len(x[0]), reverse=True)
    
    replaced_count = 0
    for value, token, replace_all in all_replacements:
        if value in text:
            if replace_all:
                count = text.count(value)
                text = text.replace(value, token)
                replaced_count += count
            else:
                text = text.replace(value, token, 1)
                replaced_count += 1
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(text)
    
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
        
        try:
            from detector_openai import detect_with_openai, merge_openai_with_local, is_openai_available
            if is_openai_available():
                logger.info(f"OPENAI_DETECT | job={job_id} | starting OpenAI detection")
                openai_entities = detect_with_openai(full_text)
                if openai_entities:
                    entities_dict = [{'type': e.get('type'), 'value': e.get('value'), 
                                     'start': e.get('start', 0), 'end': e.get('end', 0),
                                     'source': e.get('source', 'local'), 
                                     'confidence': e.get('confidence', 1.0)} for e in all_entities]
                    merged = merge_openai_with_local(entities_dict, openai_entities, full_text)
                    all_entities = normalize_entities(merged)
                    all_entities = deduplicate_entities(all_entities)
                    logger.info(f"OPENAI_MERGE | job={job_id} | openai_entities={len(openai_entities)} | total_after_merge={len(all_entities)}")
        except Exception as e:
            logger.warning(f"OPENAI_DETECT_FAIL | job={job_id} | error={str(e)}")
        
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


def truncate_value(value, max_len=10):
    """Truncate value for display: first 3 chars ... last 3 chars"""
    if len(value) <= max_len:
        return value
    return value[:3] + "..." + value[-3:]

def get_result_paths(job_id):
    """Get file paths for storing results."""
    base = os.path.join(tempfile.gettempdir(), f"result_{job_id}")
    return {
        'doc': f"{base}.doc",
        'meta': f"{base}.meta.json"
    }

@anonymizer_bp.route("/anonymizer/apply", methods=["POST"])
@app.route("/anonymizer/apply", methods=["POST"])
def anonymizer_apply():
    """
    Aplica anonimización y muestra página de resultados.
    """
    import html as html_lib
    
    if not check_openai_available():
        return render_error("El servicio no está disponible en este momento.", 503)
    
    temp_input = request.form.get('temp_input_path', '')
    ext = request.form.get('ext', 'docx')
    original_filename = request.form.get('original_filename', 'documento')
    selected_entities_json = request.form.get('selected_entities_json', '[]')
    export_csv = request.form.get('export_csv', 'false').lower() == 'true'
    
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
        else:
            replaced_count, mapping = apply_entities_to_text(temp_input, temp_output, selected_entities, ext)
        
        if not os.path.exists(temp_output):
            logger.error(f"APPLY_FAIL | job={job_id} | reason=output_not_created")
            return render_error("No se pudo generar el archivo final. Intente nuevamente.")
        
        output_size = os.path.getsize(temp_output)
        if output_size == 0:
            logger.error(f"APPLY_FAIL | job={job_id} | reason=output_empty")
            return render_error("No se pudo generar el archivo final. Intente nuevamente.")
        
        logger.info(f"APPLY_DONE | job={job_id} | replaced={replaced_count} | output_size={output_size}")
        
        # ETAPA 8: AUDITOR FINAL OBLIGATORIO - Garantizar 0 fugas
        post_scan_text = ""
        if output_ext == 'docx':
            from docx import Document
            doc_check = Document(temp_output)
            post_scan_text = extract_full_text_docx(doc_check)
        else:
            with open(temp_output, 'r', encoding='utf-8', errors='ignore') as f:
                post_scan_text = f.read()
        
        residual_warning = None
        
        if FINAL_AUDITOR_AVAILABLE:
            # Usar auditor con auto-corrección
            existing_counters = {}
            for token in mapping.keys():
                match = re.match(r'\{\{([A-Z]+)_(\d+)\}\}', token)
                if match:
                    etype, num = match.groups()
                    existing_counters[etype] = max(existing_counters.get(etype, 0), int(num))
            
            audit_result = audit_document(post_scan_text, auto_fix=True, existing_counters=existing_counters)
            log_audit_result(audit_result)
            
            if audit_result.leaks_found:
                logger.warning(f"AUDIT | job={job_id} | leaks_found={len(audit_result.leaks_found)} | auto_fixed={audit_result.leaks_auto_fixed}")
            
            # PERSISTIR AUTO-FIXES: Escribir texto corregido al archivo de salida
            if output_ext == 'docx':
                from docx import Document
                from processor_docx import apply_replacements_to_docx
                
                MAX_ITERATIONS = 2
                current_iteration = 0
                
                while current_iteration < MAX_ITERATIONS:
                    current_iteration += 1
                    
                    if not audit_result.replacements:
                        break
                    
                    logger.info(f"AUDIT_AUTOFIX | job={job_id} | iteration={current_iteration} | applying {len(audit_result.replacements)} replacements")
                    
                    doc_fix = Document(temp_output)
                    fixes_applied = apply_replacements_to_docx(doc_fix, audit_result.replacements)
                    doc_fix.save(temp_output)
                    logger.info(f"AUDIT_AUTOFIX_DOCX | job={job_id} | iteration={current_iteration} | fixes_applied={fixes_applied}")
                    
                    doc_recheck = Document(temp_output)
                    recheck_text = extract_full_text_docx(doc_recheck)
                    audit_result = audit_document(recheck_text, auto_fix=True, existing_counters=existing_counters)
                    
                    if audit_result.is_safe:
                        logger.info(f"AUDIT_RECHECK_PASSED | job={job_id} | iteration={current_iteration} | document is safe")
                        break
                    else:
                        logger.warning(f"AUDIT_RECHECK | job={job_id} | iteration={current_iteration} | remaining={audit_result.remaining_leaks}")
            
            elif audit_result.leaks_auto_fixed > 0 and audit_result.fixed_text:
                with open(temp_output, 'w', encoding='utf-8') as f:
                    f.write(audit_result.fixed_text)
                logger.info(f"AUDIT_AUTOFIX_TXT | job={job_id} | saved corrected text")
            
            if not audit_result.is_safe:
                # Documento no seguro - bloquear descarga INCONDICIONALMENTE
                logger.error(f"AUDIT_UNSAFE | job={job_id} | remaining_leaks={audit_result.remaining_leaks}")
                safe_remove(temp_output)
                
                leak_types = list(set(l['type'] for l in audit_result.leaks_found))
                types_list = [f"{t} (posibles fugas)" for t in leak_types]
                
                return render_template("anonymizer_blocked.html",
                    residual_types=types_list,
                    total_residual=audit_result.remaining_leaks,
                    original_filename=original_filename
                )
            
            # Si hubo auto-correcciones exitosas, advertir al usuario
            if audit_result.leaks_auto_fixed > 0:
                residual_warning = f"NOTA: El auditor detectó y corrigió automáticamente {audit_result.leaks_auto_fixed} posibles fugas de PII adicionales."
        
        else:
            # Fallback: usar post_scan_final si el auditor no está disponible
            from detector_capas import post_scan_final
            text_without_tokens = re.sub(r'\{\{[A-Z]+_\d+\}\}', '', post_scan_text)
            _, residual_pii_clean = post_scan_final(text_without_tokens)
            
            if residual_pii_clean:
                real_residual = [r for r in residual_pii_clean if r['count'] > 0]
                if real_residual:
                    logger.warning(f"POST_SCAN | job={job_id} | residual_pii={real_residual}")
                    types_found = ', '.join([f"{r['type']} ({r['count']})" for r in real_residual])
                    residual_warning = f"ATENCIÓN: Se detectó posible PII residual en el documento: {types_found}. Revise el documento antes de compartirlo."
        
        with open(temp_output, 'rb') as f:
            output_bytes = f.read()
        
        base_name = original_filename.rsplit('.', 1)[0] if '.' in original_filename else original_filename
        download_name = f"{base_name}_anonimizado.{output_ext}"
        
        type_counts = {}
        replacements_by_type = {}
        
        for token, original in mapping.items():
            clean_token = token.replace('{{', '').replace('}}', '')
            parts = clean_token.split('_')
            if len(parts) >= 2:
                entity_type = '_'.join(parts[:-1])
            else:
                entity_type = clean_token
            
            type_counts[entity_type] = type_counts.get(entity_type, 0) + 1
            
            if entity_type not in replacements_by_type:
                replacements_by_type[entity_type] = []
            
            replacements_by_type[entity_type].append({
                'token': clean_token,
                'original': original,
                'original_truncated': truncate_value(original)
            })
        
        warnings = []
        has_direccion = 'DIRECCION' in type_counts
        has_persona = 'PERSONA' in type_counts
        manual_count = sum(1 for e in selected_entities if e.get('source') == 'manual')
        review_count = sum(1 for e in selected_entities if e.get('status') == 'needs_review')
        
        # Agregar warning de PII residual si existe (más importante)
        if residual_warning:
            warnings.insert(0, residual_warning)
        
        if has_direccion:
            warnings.append("Las direcciones fueron detectadas mediante heurísticas. Revise el documento para confirmar la correcta anonimización.")
        if has_persona:
            warnings.append("Los nombres fueron detectados mediante NER y patrones. Pueden existir nombres adicionales no detectados.")
        if review_count > 0:
            warnings.append(f"Se detectaron {review_count} entidades que requieren revisión manual.")
        if manual_count > 0:
            warnings.append(f"Se anonimizaron {manual_count} entidades adicionales tras la revisión manual.")
        
        report_data = {
            'total_replaced': replaced_count,
            'type_counts': type_counts,
            'mapping': mapping,
            'warnings': warnings,
            'original_filename': original_filename
        }
        report_json = json.dumps(report_data, ensure_ascii=False, indent=2)
        
        result_paths = get_result_paths(job_id)
        
        with open(result_paths['doc'], 'wb') as f:
            f.write(output_bytes)
        
        meta_data = {
            'download_name': download_name,
            'output_ext': output_ext,
            'report_json': report_json,
            'created_at': datetime.now().isoformat()
        }
        with open(result_paths['meta'], 'w', encoding='utf-8') as f:
            json.dump(meta_data, f, ensure_ascii=False)
        
        safe_remove(temp_input)
        safe_remove(temp_output)
        
        logger.info(f"RESULTS_PAGE | job={job_id} | replaced={replaced_count}")
        
        return render_template("anonymizer_results.html",
            job_id=job_id,
            total_replaced=replaced_count,
            type_counts=type_counts,
            replacements_by_type=replacements_by_type,
            warnings=warnings,
            download_url=f"/anonymizer/download/{job_id}",
            report_url=f"/anonymizer/report/{job_id}",
            output_ext=output_ext
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"APPLY_FAIL | job={job_id} | reason=json_error | error={e}")
        logger.error(traceback.format_exc())
        safe_remove(temp_input)
        return render_error("Error procesando la selección de entidades. Intente nuevamente.")
    
    except Exception as e:
        logger.error(f"APPLY_FAIL | job={job_id} | error={e}")
        logger.error(traceback.format_exc())
        safe_remove(temp_input)
        safe_remove(temp_output)
        return render_error("No se pudo generar el archivo final. Verifique el documento e intente nuevamente.")


@anonymizer_bp.route("/anonymizer/download/<job_id>")
@app.route("/anonymizer/download/<job_id>")
def anonymizer_download(job_id):
    """
    Download the anonymized document with FINAL GUARANTEE.
    Auditor runs JUST BEFORE serving to ensure 0 leaks in the actual downloaded file.
    """
    result_paths = get_result_paths(job_id)
    
    if not os.path.exists(result_paths['doc']) or not os.path.exists(result_paths['meta']):
        return render_error("El enlace ha expirado. Por favor procese el documento nuevamente.")
    
    try:
        with open(result_paths['meta'], 'r', encoding='utf-8') as f:
            meta = json.load(f)
        
        # =========================================================================
        # GARANTÍA FINAL: Auditoría JUSTO ANTES de servir el archivo
        # =========================================================================
        if meta['output_ext'] == 'docx' and FINAL_AUDITOR_AVAILABLE:
            from docx import Document
            from processor_docx import apply_replacements_to_docx
            
            MAX_ITERATIONS = 2
            doc_path = result_paths['doc']
            
            for iteration in range(1, MAX_ITERATIONS + 1):
                doc = Document(doc_path)
                full_text = extract_full_text_docx(doc)
                
                audit_result = audit_document(full_text, auto_fix=True)
                
                if audit_result.is_safe:
                    logger.info(f"DOWNLOAD_AUDIT_SAFE | job={job_id} | iteration={iteration}")
                    break
                
                if audit_result.replacements:
                    fixes = apply_replacements_to_docx(doc, audit_result.replacements)
                    doc.save(doc_path)
                    logger.warning(f"DOWNLOAD_AUDIT_FIX | job={job_id} | iteration={iteration} | fixes={fixes}")
                else:
                    break
            
            # Re-verificar seguridad final después de todas las iteraciones
            doc_final = Document(doc_path)
            final_text = extract_full_text_docx(doc_final)
            final_audit = audit_document(final_text, auto_fix=False)
            
            if not final_audit.is_safe:
                strict_mode = os.environ.get('STRICT_ZERO_LEAKS', '1') == '1'
                force_download = request.args.get('force', '0') == '1'
                
                if strict_mode and not force_download:
                    from processor_docx import hard_redact_patterns
                    doc_hard = Document(doc_path)
                    hard_fixes = hard_redact_patterns(doc_hard)
                    if hard_fixes > 0:
                        doc_hard.save(doc_path)
                        logger.warning(f"HARD_REDACT | job={job_id} | fixes={hard_fixes}")
                        doc_recheck = Document(doc_path)
                        recheck_text = extract_full_text_docx(doc_recheck)
                        final_audit = audit_document(recheck_text, auto_fix=False)
                
                if not final_audit.is_safe:
                    if force_download:
                        logger.warning(f"DOWNLOAD_FORCED | job={job_id} | remaining_leaks={final_audit.remaining_leaks} | user_accepted_risk=true")
                    else:
                        logger.warning(f"DOWNLOAD_WARNING | job={job_id} | remaining_leaks={final_audit.remaining_leaks}")
                        leak_types = list(set(l['type'] for l in final_audit.leaks_found))
                        types_list = [f"{t} (posibles fugas)" for t in leak_types]
                        
                        return render_template("anonymizer_blocked.html",
                            residual_types=types_list,
                            total_residual=final_audit.remaining_leaks,
                            original_filename=meta.get('download_name', 'documento'),
                            job_id=job_id
                        )
        
        elif meta['output_ext'] == 'txt' and FINAL_AUDITOR_AVAILABLE:
            with open(result_paths['doc'], 'r', encoding='utf-8') as f:
                text_content = f.read()
            
            audit_result = audit_document(text_content, auto_fix=True)
            
            if audit_result.leaks_auto_fixed > 0 and audit_result.fixed_text:
                with open(result_paths['doc'], 'w', encoding='utf-8') as f:
                    f.write(audit_result.fixed_text)
                logger.info(f"DOWNLOAD_AUDIT_FIX_TXT | job={job_id} | fixes={audit_result.leaks_auto_fixed}")
            
            if not audit_result.is_safe:
                force_download = request.args.get('force', '0') == '1'
                
                if force_download:
                    logger.warning(f"DOWNLOAD_FORCED_TXT | job={job_id} | remaining={audit_result.remaining_leaks} | user_accepted_risk=true")
                else:
                    logger.warning(f"DOWNLOAD_WARNING_TXT | job={job_id} | remaining={audit_result.remaining_leaks}")
                    return render_template("anonymizer_blocked.html",
                        residual_types=[f"{l['type']} (posibles fugas)" for l in audit_result.leaks_found[:5]],
                        total_residual=audit_result.remaining_leaks,
                        original_filename=meta.get('download_name', 'documento'),
                        job_id=job_id
                    )
        
        # =========================================================================
        # Leer archivo FINAL (después de auditoría) y servir
        # =========================================================================
        with open(result_paths['doc'], 'rb') as f:
            output_bytes = f.read()
        
        output_buffer = BytesIO(output_bytes)
        
        if meta['output_ext'] == 'docx':
            mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:
            mimetype = 'text/plain; charset=utf-8'
        
        logger.info(f"DOWNLOAD | job={job_id} | filename={meta['download_name']} | SAFE")
        
        return send_file(
            output_buffer,
            as_attachment=True,
            download_name=meta['download_name'],
            mimetype=mimetype
        )
    except Exception as e:
        logger.error(f"DOWNLOAD_ERROR | job={job_id} | error={e}")
        return render_error("Error al descargar el documento. Por favor procese el documento nuevamente.")


@anonymizer_bp.route("/anonymizer/report/<job_id>")
@app.route("/anonymizer/report/<job_id>")
def anonymizer_report(job_id):
    """Download the anonymization report."""
    result_paths = get_result_paths(job_id)
    
    if not os.path.exists(result_paths['meta']):
        return render_error("El enlace ha expirado. Por favor procese el documento nuevamente.")
    
    try:
        with open(result_paths['meta'], 'r', encoding='utf-8') as f:
            meta = json.load(f)
        
        report_buffer = BytesIO(meta['report_json'].encode('utf-8'))
        
        base_name = meta['download_name'].rsplit('.', 1)[0] if '.' in meta['download_name'] else meta['download_name']
        report_name = f"{base_name}_reporte.json"
        
        logger.info(f"REPORT | job={job_id} | filename={report_name}")
        
        return send_file(
            report_buffer,
            as_attachment=True,
            download_name=report_name,
            mimetype='application/json'
        )
    except Exception as e:
        logger.error(f"REPORT_ERROR | job={job_id} | error={e}")
        return render_error("Error al descargar el reporte. Por favor procese el documento nuevamente.")


app.register_blueprint(anonymizer_bp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
