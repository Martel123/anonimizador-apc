"""
Anonimizador Legal Público - App mínima para Render
====================================================
NO importa models, db, flask_login, sqlalchemy, pyotp.
NO usa flash() ni session.
Confidencialidad total: documentos en /tmp, borrados en finally.
"""

import os
import uuid
import json
import logging
import tempfile
import threading
import time
import zipfile
import subprocess
import shutil
import traceback
from datetime import datetime
from flask import Flask, Blueprint, render_template, request, redirect, url_for, send_file, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

anonymizer_bp = Blueprint("anonymizer", __name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-fallback-secret")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), "anon_uploads")
OUTPUT_FOLDER = os.path.join(tempfile.gettempdir(), "anon_outputs")
ALLOWED_EXTENSIONS = {'doc', 'docx', 'pdf', 'txt'}
JOB_EXPIRY_MINUTES = 30

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def validate_file_format(file_path: str, ext: str) -> tuple:
    """
    Validates that file content matches expected format.
    Returns (is_valid: bool, error_message: str or None)
    """
    try:
        with open(file_path, 'rb') as f:
            head = f.read(8)
        
        if ext == 'docx':
            if not zipfile.is_zipfile(file_path):
                logger.warning(f"FORMAT_ERROR | ext=docx | not_zip | head={head[:4].hex()}")
                return False, "El archivo no es un DOCX válido (no es ZIP). Si es .DOC antiguo, conviértelo a .DOCX."
            return True, None
        
        elif ext == 'pdf':
            if not head.startswith(b'%PDF'):
                logger.warning(f"FORMAT_ERROR | ext=pdf | head={head[:4].hex()}")
                return False, "El archivo no es un PDF válido (no empieza con %PDF)."
            return True, None
        
        elif ext == 'txt':
            return True, None
        
        elif ext == 'doc':
            if head.startswith(b'\xd0\xcf\x11\xe0'):
                return True, None
            if head.startswith(b'PK'):
                return False, "Este archivo parece ser DOCX, no DOC. Cambia la extensión a .docx."
            logger.warning(f"FORMAT_ERROR | ext=doc | head={head[:4].hex()}")
            return False, "El archivo no parece ser un documento Word válido."
        
        return True, None
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return False, f"Error validando archivo: {str(e)[:100]}"


def entity_to_dict(e):
    """
    Normaliza cualquier entidad (objeto Entity o dict) a un dict estándar.
    Soporta múltiples formatos de entrada.
    """
    if isinstance(e, dict):
        return {
            'type': e.get('type') or e.get('entity_type') or e.get('label') or 'UNKNOWN',
            'value': e.get('value') or e.get('text') or e.get('original') or '',
            'text': e.get('text') or e.get('value') or e.get('original') or '',
            'start': e.get('start', 0),
            'end': e.get('end', 0),
            'confidence': e.get('confidence', 1.0),
            'source': e.get('source', 'unknown')
        }
    
    try:
        text_val = (getattr(e, 'value', None) or 
                    getattr(e, 'text', None) or 
                    getattr(e, 'original', None) or 
                    getattr(e, 'span_text', None) or '')
        
        type_val = (getattr(e, 'entity_type', None) or 
                    getattr(e, 'type', None) or 
                    getattr(e, 'label', None) or 
                    getattr(e, 'ent_type', None) or 'UNKNOWN')
        
        start_val = getattr(e, 'start', None) or getattr(e, 'start_char', 0)
        end_val = getattr(e, 'end', None) or getattr(e, 'end_char', 0)
        confidence_val = getattr(e, 'confidence', 1.0)
        source_val = getattr(e, 'source', 'unknown')
        
        return {
            'type': type_val,
            'value': text_val,
            'text': text_val,
            'start': start_val,
            'end': end_val,
            'confidence': confidence_val,
            'source': source_val
        }
    except Exception as ex:
        attrs = [a for a in dir(e) if not a.startswith('_')][:10]
        logger.error(f"ENTITY_CONVERT_FAIL | type={type(e).__name__} | attrs={attrs} | error={ex}")
        raise ValueError(f"Cannot convert entity of type {type(e).__name__}: {attrs}")


def normalize_entities(items):
    """Normaliza una lista de entidades a dicts estándar."""
    if not items:
        return []
    result = []
    for e in items:
        try:
            d = entity_to_dict(e)
            if d.get('value') or d.get('text'):
                result.append(d)
        except Exception as ex:
            logger.warning(f"ENTITY_SKIP | error={ex}")
    return result


def dicts_to_entity_objects(entity_dicts):
    """Convierte lista de dicts a objetos Entity para el motor.
    
    Maneja entidades que cruzan líneas (ej: 'SAN BORJA\\nEDUARDO GAMARRA')
    dividiéndolas en partes separadas para búsqueda en párrafos individuales.
    """
    from detector_capas import Entity
    entities = []
    for d in entity_dicts:
        value = d.get('value') or d.get('text', '')
        if not value:
            continue
        
        ent_type = d.get('type', 'UNKNOWN')
        source = d.get('source', 'manual')
        confidence = d.get('confidence', 1.0)
        
        # Si el valor contiene saltos de línea, dividir en partes
        if '\n' in value:
            parts = [p.strip() for p in value.split('\n') if p.strip()]
            for part in parts:
                # Solo agregar partes significativas (>3 chars para evitar ruido)
                if len(part) > 3:
                    entities.append(Entity(
                        type=ent_type,
                        value=part,
                        start=0,
                        end=len(part),
                        source=source,
                        confidence=confidence
                    ))
        else:
            entities.append(Entity(
                type=ent_type,
                value=value,
                start=d.get('start', 0),
                end=d.get('end', 0),
                source=source,
                confidence=confidence
            ))
    return entities


def apply_entities_to_docx(input_path, output_path, entity_dicts):
    """
    Aplica reemplazos usando entidades específicas (NO re-detecta).
    Motor: processor_docx.process_docx_run_aware + EntityMapping
    """
    from docx import Document
    from processor_docx import EntityMapping, process_docx_run_aware
    
    result = {
        'ok': True,
        'mapping': {},
        'replacement_stats': {},
        'replaced_count': 0,
        'error': None
    }
    
    try:
        doc = Document(input_path)
        entities = dicts_to_entity_objects(entity_dicts)
        
        if not entities:
            doc.save(output_path)
            result['replaced_count'] = 0
            logger.warning(f"APPLY_NO_ENTITIES | No entities to apply")
            return result
        
        mapping = EntityMapping()
        stats = process_docx_run_aware(doc, entities, mapping)
        
        doc.save(output_path)
        
        result['mapping'] = mapping.reverse_mappings
        result['replacement_stats'] = dict(stats)
        result['replacement_stats']['entities_replaced'] = dict(stats.get('entities_replaced', {}))
        result['replaced_count'] = stats.get('replacements', 0)
        
        logger.info(f"APPLY_DOCX_OK | replaced={result['replaced_count']} | types={mapping.get_summary()}")
        
    except Exception as e:
        logger.error(f"APPLY_DOCX_FAIL | error={e}")
        result['ok'] = False
        result['error'] = str(e)
    
    return result


def apply_entities_to_text(input_path, output_path, entity_dicts):
    """
    Aplica reemplazos a texto plano usando entidades específicas.
    """
    from collections import defaultdict
    
    result = {
        'ok': True,
        'mapping': {},
        'replaced_count': 0,
        'error': None
    }
    
    try:
        with open(input_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        
        counters = defaultdict(int)
        mapping = {}
        replaced_count = 0
        
        sorted_entities = sorted(entity_dicts, key=lambda e: len(e.get('value', '')), reverse=True)
        
        for ent in sorted_entities:
            value = ent.get('value') or ent.get('text', '')
            ent_type = ent.get('type', 'UNKNOWN')
            
            if not value or value not in text:
                continue
            
            counters[ent_type] += 1
            token = f"{{{{{ent_type}_{counters[ent_type]}}}}}"
            
            if ent.get('replaceAll', True):
                count = text.count(value)
                text = text.replace(value, token)
                replaced_count += count
            else:
                text = text.replace(value, token, 1)
                replaced_count += 1
            
            mapping[token] = value[:3] + '***'
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
        
        result['mapping'] = mapping
        result['replaced_count'] = replaced_count
        
        logger.info(f"APPLY_TXT_OK | replaced={replaced_count}")
        
    except Exception as e:
        logger.error(f"APPLY_TXT_FAIL | error={e}")
        result['ok'] = False
        result['error'] = str(e)
    
    return result


def apply_entities_to_pdf(input_path, output_path, entity_dicts):
    """
    Extrae texto del PDF, aplica entidades, guarda como .txt
    """
    from collections import defaultdict
    
    result = {
        'ok': True,
        'mapping': {},
        'replaced_count': 0,
        'error': None
    }
    
    try:
        from processor_pdf import extract_text_pdf
        extract_result = extract_text_pdf(input_path)
        
        if not extract_result.get('success'):
            result['ok'] = False
            result['error'] = extract_result.get('error', 'No se pudo extraer texto del PDF')
            return result
        
        text = extract_result.get('text', '')
        
        counters = defaultdict(int)
        mapping = {}
        replaced_count = 0
        
        sorted_entities = sorted(entity_dicts, key=lambda e: len(e.get('value', '')), reverse=True)
        
        for ent in sorted_entities:
            value = ent.get('value') or ent.get('text', '')
            ent_type = ent.get('type', 'UNKNOWN')
            
            if not value or value not in text:
                continue
            
            counters[ent_type] += 1
            token = f"{{{{{ent_type}_{counters[ent_type]}}}}}"
            
            if ent.get('replaceAll', True):
                count = text.count(value)
                text = text.replace(value, token)
                replaced_count += count
            else:
                text = text.replace(value, token, 1)
                replaced_count += 1
            
            mapping[token] = value[:3] + '***'
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
        
        result['mapping'] = mapping
        result['replaced_count'] = replaced_count
        
        logger.info(f"APPLY_PDF_OK | replaced={replaced_count}")
        
    except Exception as e:
        logger.error(f"APPLY_PDF_FAIL | error={e}")
        result['ok'] = False
        result['error'] = str(e)
    
    return result


def convert_doc_to_docx(input_path: str, output_dir: str) -> tuple:
    """
    Attempts to convert .doc to .docx using LibreOffice.
    Returns (success: bool, output_path or error_message: str)
    """
    soffice_paths = [
        'soffice',
        '/usr/bin/soffice',
        '/usr/bin/libreoffice',
        'libreoffice'
    ]
    
    soffice_cmd = None
    for path in soffice_paths:
        if shutil.which(path):
            soffice_cmd = path
            break
    
    if not soffice_cmd:
        logger.info("DOC_CONVERT | LibreOffice not available")
        return False, "Este servidor no puede convertir archivos .DOC automáticamente. Por favor conviértelo a .DOCX usando Word o Google Docs y vuelve a subirlo."
    
    try:
        result = subprocess.run(
            [soffice_cmd, '--headless', '--convert-to', 'docx', '--outdir', output_dir, input_path],
            capture_output=True,
            timeout=60,
            text=True
        )
        
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        expected_output = os.path.join(output_dir, f"{base_name}.docx")
        
        if os.path.exists(expected_output):
            logger.info(f"DOC_CONVERT | success | output={expected_output}")
            return True, expected_output
        else:
            logger.warning(f"DOC_CONVERT | failed | stderr={result.stderr[:200]}")
            return False, "Error al convertir .DOC a .DOCX. Por favor conviértelo manualmente."
            
    except subprocess.TimeoutExpired:
        return False, "Tiempo agotado convirtiendo .DOC. Por favor conviértelo a .DOCX manualmente."
    except Exception as e:
        logger.error(f"DOC conversion error: {e}")
        return False, f"Error de conversión: {str(e)[:100]}"


def process_txt_file(file_path: str, strict_mode: bool = True, generate_mapping: bool = False) -> dict:
    """
    Process a plain text file for anonymization.
    """
    try:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='latin-1', errors='ignore') as f:
                text = f.read()
        
        if not text.strip():
            return {'success': False, 'error': 'El archivo de texto está vacío.'}
        
        from detector_capas import detect_all_pii, post_scan_final
        entities, detect_meta = detect_all_pii(text)
        
        all_entities = []
        for ent in entities:
            if hasattr(ent, '__dict__'):
                all_entities.append({
                    'type': ent.type,
                    'value': ent.value,
                    'start': ent.start,
                    'end': ent.end,
                    'confidence': ent.confidence,
                    'source': ent.source
                })
            else:
                all_entities.append(ent)
        
        confirmed = []
        needs_review = []
        
        for ent in all_entities:
            if ent.get('confidence', 1.0) >= 0.7:
                confirmed.append(ent)
            elif strict_mode:
                needs_review.append(ent)
            else:
                confirmed.append(ent)
        
        return {
            'success': True,
            'ok': True,
            'text': text,
            'confirmed': confirmed,
            'needs_review': needs_review if strict_mode else [],
            'detector_used': 'detector_capas',
            'text_preview': text[:2000],
            'is_txt': True,
            'detect_meta': detect_meta
        }
        
    except Exception as e:
        logger.error(f"TXT processing error: {e}")
        return {'success': False, 'error': f"Error procesando texto: {str(e)[:150]}"}


def anonymize_txt_file(input_path: str, output_path: str, entities: list) -> dict:
    """
    Anonymize a TXT file by replacing detected entities with tokens.
    """
    try:
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except UnicodeDecodeError:
            with open(input_path, 'r', encoding='latin-1', errors='ignore') as f:
                text = f.read()
        
        sorted_entities = sorted(entities, key=lambda e: e.get('start', 0), reverse=True)
        
        mapping = {}
        type_counters = {}
        
        for ent in sorted_entities:
            ent_type = ent.get('type', 'DATO')
            original = ent.get('text', ent.get('value', ''))
            start = ent.get('start', 0)
            end = ent.get('end', start + len(original))
            
            if ent_type not in type_counters:
                type_counters[ent_type] = 1
            else:
                type_counters[ent_type] += 1
            
            token = f"{{{{{ent_type}_{type_counters[ent_type]}}}}}"
            mapping[token] = original
            
            text = text[:start] + token + text[end:]
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
        
        return {
            'ok': True,
            'output_path': output_path,
            'mapping': mapping,
            'entities_replaced': len(entities)
        }
        
    except Exception as e:
        logger.error(f"TXT anonymization error: {e}")
        return {'ok': False, 'error': f"Error anonimizando texto: {str(e)[:150]}"}

jobs_store = {}
jobs_lock = threading.Lock()

JOBS_DIR = os.path.join(tempfile.gettempdir(), "anon_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

def save_job(job_id, job_data):
    """Save job to file for cross-worker persistence"""
    job_path = os.path.join(JOBS_DIR, f"{job_id}.json")
    try:
        with open(job_path, 'w', encoding='utf-8') as f:
            serializable = {}
            for k, v in job_data.items():
                if isinstance(v, datetime):
                    serializable[k] = v.isoformat()
                else:
                    serializable[k] = v
            json.dump(serializable, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"save_job error: {e}")

def load_job(job_id):
    """Load job from file"""
    job_path = os.path.join(JOBS_DIR, f"{job_id}.json")
    if os.path.exists(job_path):
        try:
            with open(job_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if 'created_at' in data and isinstance(data['created_at'], str):
                    data['created_at'] = datetime.fromisoformat(data['created_at'])
                return data
        except Exception as e:
            logger.error(f"load_job error: {e}")
    return None


def allowed_file(filename):
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def cleanup_old_files():
    try:
        now = time.time()
        expiry_seconds = JOB_EXPIRY_MINUTES * 60
        for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
            if os.path.exists(folder):
                for fn in os.listdir(folder):
                    fp = os.path.join(folder, fn)
                    if os.path.isfile(fp):
                        if now - os.path.getmtime(fp) > expiry_seconds:
                            try:
                                os.remove(fp)
                            except:
                                pass
        with jobs_lock:
            expired = [jid for jid, d in jobs_store.items()
                      if 'created_at' in d and (datetime.now() - d['created_at']).total_seconds() > expiry_seconds]
            for jid in expired:
                del jobs_store[jid]
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


def start_cleanup_thread():
    def loop():
        while True:
            time.sleep(300)
            cleanup_old_files()
    t = threading.Thread(target=loop, daemon=True)
    t.start()

start_cleanup_thread()


def safe_remove(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except:
            pass


def check_openai_available():
    return bool(os.environ.get("OPENAI_API_KEY"))


@anonymizer_bp.route("/")
def home():
    cleanup_old_files()
    error = None
    if not check_openai_available():
        error = "Servicio temporalmente no disponible. Intente más tarde."
    return render_template("anonymizer_standalone.html", error=error)


@anonymizer_bp.route("/anonymizer")
def anonymizer_home():
    return render_template("anonymizer_standalone.html")


@anonymizer_bp.route("/health")
def health():
    return "ok"


@anonymizer_bp.route("/anonymizer/process", methods=["POST"])
def anonymizer_process():
    temp_input = None
    temp_output = None
    
    try:
        if not check_openai_available():
            return jsonify({"error": "OPENAI_API_KEY missing"}), 503
        
        file = request.files.get("file")
        if not file or not file.filename:
            return render_template("anonymizer_standalone.html", 
                                   error="Por favor selecciona un documento.")
        
        if not allowed_file(file.filename):
            return render_template("anonymizer_standalone.html",
                                   error="Formato no soportado. Solo DOC, DOCX, PDF o TXT."), 400
        
        job_id = str(uuid.uuid4())
        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[1].lower()
        
        temp_input = os.path.join(UPLOAD_FOLDER, f"{job_id}_in.{ext}")
        file.save(temp_input)
        file_size = os.path.getsize(temp_input)
        
        with open(temp_input, 'rb') as f:
            head16 = f.read(16).hex()
        logger.info(f"UPLOAD | job={job_id} | file={filename} | ext={ext} | size={file_size} | HEAD16={head16}")
        
        if file_size > 10 * 1024 * 1024:
            return render_template("anonymizer_standalone.html",
                                   error="Archivo muy grande. Máximo 10MB."), 400
        
        is_valid, validation_error = validate_file_format(temp_input, ext)
        if not is_valid:
            logger.warning(f"FORMAT_ERROR | job={job_id} | ext={ext} | error={validation_error}")
            safe_remove(temp_input)
            return render_template("anonymizer_standalone.html", error=validation_error), 400
        
        logger.info(f"UPLOAD_OK | job={job_id} | ext={ext} | size={file_size}")
        
        if ext == 'doc':
            success, result_or_error = convert_doc_to_docx(temp_input, UPLOAD_FOLDER)
            if not success:
                safe_remove(temp_input)
                return render_template("anonymizer_standalone.html", error=result_or_error), 400
            safe_remove(temp_input)
            temp_input = result_or_error
            ext = 'docx'
            file_size = os.path.getsize(temp_input)
            logger.info(f"DOC_CONVERTED | job={job_id} | new_path={temp_input}")
        
        strict_mode = request.form.get("strict_mode") is not None
        generate_mapping = request.form.get("generate_mapping") is not None
        
        logger.info(f"PROCESS | job={job_id} | strict_mode={strict_mode} | generate_mapping={generate_mapping}")
        
        try:
            text_content = ""
            if ext == 'docx':
                from docx import Document
                from processor_docx import extract_full_text_docx
                doc = Document(temp_input)
                text_content = extract_full_text_docx(doc)
            elif ext == 'pdf':
                from processor_pdf import extract_text_pdf
                extract_result = extract_text_pdf(temp_input)
                if extract_result.get('success'):
                    text_content = extract_result.get('text', '')
                else:
                    raise ValueError(extract_result.get('error', 'No se pudo extraer texto del PDF'))
            elif ext == 'txt':
                with open(temp_input, 'r', encoding='utf-8', errors='ignore') as f:
                    text_content = f.read()
            
            from detector_capas import detect_all_pii, post_scan_final
            entities, detect_meta = detect_all_pii(text_content)
            
            all_entities = normalize_entities(entities)
            
            confirmed = []
            needs_review = []
            for ent in all_entities:
                if strict_mode:
                    # En strict_mode, todas las entidades van a revisión
                    needs_review.append(ent)
                elif ent.get('confidence', 1.0) >= 0.8:
                    confirmed.append(ent)
                else:
                    needs_review.append(ent)
            
            has_warning, warnings = post_scan_final(text_content)
            text_preview = text_content[:2000] if text_content else ""
            
            sample_keys = list(all_entities[0].keys()) if all_entities else []
            logger.info(f"DETECT_OK | job={job_id} | confirmed={len(confirmed)} | needs_review={len(needs_review)} | has_warning={has_warning} | sample_keys={sample_keys}")
            
        except Exception as detect_err:
            logger.error(f"DETECT_FAIL | job={job_id} | error={str(detect_err)[:200]}")
            logger.error(f"DETECT_TRACE | {traceback.format_exc()}")
            safe_remove(temp_input)
            return render_template("anonymizer_standalone.html",
                error=f"Error detectando entidades: {str(detect_err)[:100]}"), 500
        
        if strict_mode:
            job_data = {
                'created_at': datetime.now(),
                'original_filename': filename,
                'ext': ext,
                'input_path': temp_input,
                'strict_mode': True,
                'result': {
                    'success': True,
                    'confirmed': confirmed,
                    'needs_review': needs_review,
                    'text_preview': text_preview,
                    'detector_used': detect_meta.get('detector', 'hybrid'),
                    'post_scan_warning': has_warning
                }
            }
            with jobs_lock:
                jobs_store[job_id] = job_data
            save_job(job_id, job_data)
            logger.info(f"STRICT_MODE | job={job_id} | redirecting to review")
            return redirect(url_for('.anonymizer_review', job_id=job_id))
        
        output_path = None
        mapping = {}
        all_entities = confirmed + needs_review
        sample_ent = all_entities[0] if all_entities else {}
        sample_keys = list(sample_ent.keys())
        logger.info(f"DIRECT_APPLY | job={job_id} | ext={ext} | confirmed={len(confirmed)} | needs_review={len(needs_review)} | total={len(all_entities)}")
        logger.info(f"DIRECT_ENTITY_SAMPLE | keys={sample_keys} | has_value={'value' in sample_ent} | sample={str(sample_ent)[:150]}")
        
        try:
            if ext == 'docx':
                output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_out.docx")
                anon_result = apply_entities_to_docx(temp_input, output_path, all_entities)
                
                if anon_result.get('ok'):
                    mapping = anon_result.get('mapping', {})
                    replaced = anon_result.get('replaced_count', 0)
                    logger.info(f"DOCX_ANON_OK | job={job_id} | replaced={replaced} | types={len(mapping)}")
                else:
                    error_msg = anon_result.get('error', 'Error anonimizando DOCX')
                    logger.error(f"ANON_FAIL | job={job_id} | ext=docx | error={error_msg}")
                    safe_remove(temp_input)
                    return render_template("anonymizer_standalone.html", error=error_msg), 400
            
            elif ext == 'pdf':
                output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_out.txt")
                anon_result = apply_entities_to_pdf(temp_input, output_path, all_entities)
                
                if anon_result.get('ok'):
                    mapping = anon_result.get('mapping', {})
                    replaced = anon_result.get('replaced_count', 0)
                    logger.info(f"PDF_ANON_OK | job={job_id} | replaced={replaced}")
                else:
                    error_msg = anon_result.get('error', 'Error anonimizando PDF')
                    logger.error(f"ANON_FAIL | job={job_id} | ext=pdf | error={error_msg}")
                    safe_remove(temp_input)
                    return render_template("anonymizer_standalone.html", error=error_msg), 400
            
            elif ext == 'txt':
                output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_out.txt")
                anon_result = apply_entities_to_text(temp_input, output_path, all_entities)
                
                if anon_result.get('ok'):
                    mapping = anon_result.get('mapping', {})
                    replaced = anon_result.get('replaced_count', 0)
                    logger.info(f"TXT_ANON_OK | job={job_id} | replaced={replaced}")
                else:
                    error_msg = anon_result.get('error', 'Error anonimizando TXT')
                    logger.error(f"ANON_FAIL | job={job_id} | ext=txt | error={error_msg}")
                    safe_remove(temp_input)
                    return render_template("anonymizer_standalone.html", error=error_msg), 400
                    
        except Exception as e:
            logger.error(f"ANON_FAIL | job={job_id} | ext={ext} | error={str(e)[:200]}")
            logger.error(f"ANON_TRACE | {traceback.format_exc()}")
            safe_remove(temp_input)
            return render_template("anonymizer_standalone.html",
                error=f"Error procesando documento: {str(e)[:100]}"), 500
        
        job_data = {
            'created_at': datetime.now(),
            'original_filename': filename,
            'ext': ext,
            'input_path': temp_input,
            'output_path': output_path,
            'mapping': mapping,
            'strict_mode': False,
            'result': {'success': True, 'confirmed': all_entities, 'needs_review': [], 'mapping': mapping}
        }
        with jobs_lock:
            jobs_store[job_id] = job_data
        save_job(job_id, job_data)
        
        return redirect(url_for('.anonymizer_download_page', job_id=job_id))
        
    except Exception as e:
        logger.error(f"Process error: {e}")
        return render_template("anonymizer_standalone.html",
                               error=f"Error inesperado: {str(e)[:150]}")


@anonymizer_bp.route("/anonymizer/review/<job_id>")
def anonymizer_review(job_id):
    with jobs_lock:
        job = jobs_store.get(job_id)
    if not job:
        job = load_job(job_id)
    
    if not job:
        return render_template("anonymizer_standalone.html",
                               error="Sesión expirada. Suba el documento nuevamente.")
    
    result = job.get('result', {})
    confirmed = result.get('confirmed', [])
    needs_review = result.get('needs_review', [])
    text_preview = result.get('text_preview', '')
    
    pending_entities = []
    for i, ent in enumerate(needs_review):
        context_start = max(0, ent.get('start', 0) - 30)
        context_end = min(len(text_preview), ent.get('end', 0) + 30)
        context = text_preview[context_start:context_end] if text_preview else ""
        
        pending_entities.append({
            'id': f"pending_{i}",
            'type': ent.get('type', 'UNKNOWN'),
            'value': ent.get('value') or ent.get('text', ''),
            'confidence': ent.get('confidence', 1.0),
            'context': context
        })
    
    entities_summary = {}
    for ent in confirmed:
        ent_type = ent.get('type', 'UNKNOWN')
        entities_summary[ent_type] = entities_summary.get(ent_type, 0) + 1
    
    return render_template("anonymizer_review.html",
        job_id=job_id,
        pending_count=len(pending_entities),
        document_text=text_preview,
        pending_entities=pending_entities,
        confirmed_count=len(confirmed),
        entities_summary=entities_summary,
        original_filename=job.get('original_filename', 'documento')
    )


@anonymizer_bp.route("/anonymizer/review/<job_id>/apply", methods=["POST"])
def anonymizer_apply_review(job_id):
    with jobs_lock:
        job = jobs_store.get(job_id)
    if not job:
        job = load_job(job_id)
    
    if not job:
        if request.is_json:
            return jsonify({'success': False, 'error': 'Sesión expirada'}), 400
        return render_template("anonymizer_standalone.html", error="Sesión expirada. Suba el documento nuevamente.")
    
    try:
        result = job.get('result', {})
        confirmed = result.get('confirmed', [])
        needs_review = result.get('needs_review', [])
        
        final_entities = list(confirmed)
        manual_entities = []
        
        if request.is_json:
            data = request.get_json() or {}
            accepted_indices = data.get('accepted', [])
            for idx in accepted_indices:
                if 0 <= idx < len(needs_review):
                    final_entities.append(needs_review[idx])
        else:
            for i, ent in enumerate(needs_review):
                decision_key = f"decisions[pending_{i}]"
                if request.form.get(decision_key) == 'true':
                    final_entities.append(ent)
            
            manual_json = request.form.get('manual_entities', '[]')
            try:
                manual_entities = json.loads(manual_json)
                for m in manual_entities:
                    final_entities.append({
                        'type': m.get('type', 'CUSTOM'),
                        'value': m.get('value', ''),
                        'text': m.get('value', ''),
                        'start': 0,
                        'end': 0,
                        'confidence': 1.0,
                        'source': 'manual',
                        'token': m.get('token', ''),
                        'replaceAll': m.get('replaceAll', True)
                    })
            except json.JSONDecodeError:
                pass
        
        sample_types = list(set([e.get('type', '?') for e in final_entities[:5]]))
        sample_entity = final_entities[0] if final_entities else {}
        sample_keys = list(sample_entity.keys())
        logger.info(f"APPLY_START | job={job_id} | confirmed={len(confirmed)} | needs_review={len(needs_review)} | final={len(final_entities)} | manual={len(manual_entities)}")
        logger.info(f"APPLY_ENTITY_SAMPLE | keys={sample_keys} | has_value={'value' in sample_entity} | has_start={'start' in sample_entity} | sample={str(sample_entity)[:150]}")
        
        ext = job.get('ext', 'docx')
        input_path = job.get('input_path')
        output_ext = 'txt' if ext == 'pdf' else ext
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_out.{output_ext}")
        
        if ext == 'docx':
            anon_result = apply_entities_to_docx(input_path, output_path, final_entities)
            success = anon_result.get('ok', False)
            replaced = anon_result.get('replaced_count', 0)
            logger.info(f"APPLY_OK | job={job_id} | ext=docx | replaced={replaced} | output={output_path}")
        elif ext == 'txt':
            anon_result = apply_entities_to_text(input_path, output_path, final_entities)
            success = anon_result.get('ok', False)
            replaced = anon_result.get('replaced_count', 0)
            logger.info(f"APPLY_OK | job={job_id} | ext=txt | replaced={replaced}")
        else:
            anon_result = apply_entities_to_pdf(input_path, output_path, final_entities)
            success = anon_result.get('ok', False)
            replaced = anon_result.get('replaced_count', 0)
            logger.info(f"APPLY_OK | job={job_id} | ext=pdf | replaced={replaced}")
        
        if success:
            mapping = anon_result.get('mapping', {})
            job['output_path'] = output_path
            job['mapping'] = mapping
            job['result']['confirmed'] = final_entities
            with jobs_lock:
                jobs_store[job_id] = job
            save_job(job_id, job)
            
            redirect_url = url_for('.anonymizer_download_page', job_id=job_id)
            
            if request.is_json:
                return jsonify({'success': True, 'redirect': redirect_url})
            else:
                return redirect(redirect_url)
        else:
            error_msg = anon_result.get('error', 'Error procesando documento')
            if request.is_json:
                return jsonify({'success': False, 'error': error_msg}), 500
            return render_template("anonymizer_standalone.html", error=error_msg)
            
    except Exception as e:
        logger.error(f"Apply review error: {e}")
        logger.error(f"APPLY_TRACE | {traceback.format_exc()}")
        if request.is_json:
            return jsonify({'success': False, 'error': str(e)}), 500
        return render_template("anonymizer_standalone.html", error=f"Error: {str(e)[:100]}")


@anonymizer_bp.route("/anonymizer/download-page/<job_id>")
def anonymizer_download_page(job_id):
    with jobs_lock:
        job = jobs_store.get(job_id)
    if not job:
        job = load_job(job_id)
    
    if not job:
        return render_template("anonymizer_standalone.html",
                               error="Sesión expirada. Suba el documento nuevamente.")
    
    result = job.get('result', {})
    return render_template("anonymizer_download_standalone.html",
        job_id=job_id,
        original_filename=job.get('original_filename', 'documento'),
        entities_count=len(result.get('confirmed', [])),
        mapping=job.get('mapping', result.get('mapping', {}))
    )


@anonymizer_bp.route("/anonymizer/download/<job_id>/<file_type>")
def anonymizer_download(job_id, file_type):
    with jobs_lock:
        job = jobs_store.get(job_id)
    if not job:
        job = load_job(job_id)
    
    if not job:
        return render_template("anonymizer_standalone.html",
                               error="Sesión expirada. Suba el documento nuevamente.")
    
    try:
        if file_type == 'document':
            output_path = job.get('output_path')
            if not output_path or not os.path.exists(output_path):
                result = job.get('result', {})
                output_path = result.get('output_path')
            
            if not output_path or not os.path.exists(output_path):
                ext = job.get('ext', 'docx')
                input_path = job.get('input_path')
                output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_out.{ext}")
                
                if ext == 'docx':
                    from processor_docx import anonymize_docx_complete
                    anon_result = anonymize_docx_complete(input_path, output_path, strict_mode=True)
                else:
                    from processor_pdf import anonymize_pdf_to_text
                    anon_result = anonymize_pdf_to_text(input_path, strict_mode=True)
                    if anon_result.get('ok') and 'anonymized_text' in anon_result:
                        txt_path = output_path.replace('.pdf', '.txt')
                        with open(txt_path, 'w', encoding='utf-8') as f:
                            f.write(anon_result['anonymized_text'])
                        output_path = txt_path
                
                job['output_path'] = output_path
                job['mapping'] = anon_result.get('mapping', {})
                with jobs_lock:
                    jobs_store[job_id] = job
                save_job(job_id, job)
            
            original = job.get('original_filename', 'documento')
            base = original.rsplit('.', 1)[0] if '.' in original else original
            ext = job.get('ext', 'docx')
            
            return send_file(output_path, as_attachment=True, 
                           download_name=f"{base}_anonimizado.{ext}")
        
        elif file_type == 'report':
            result = job.get('result', {})
            mapping = job.get('mapping', result.get('mapping', {}))
            
            report = {
                'fecha': datetime.now().isoformat(),
                'archivo': job.get('original_filename'),
                'entidades': len(result.get('confirmed', [])),
                'mapeo': mapping
            }
            
            report_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_report.json")
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            
            original = job.get('original_filename', 'doc')
            base = original.rsplit('.', 1)[0] if '.' in original else original
            
            return send_file(report_path, as_attachment=True,
                           download_name=f"{base}_reporte.json")
        
        else:
            return render_template("anonymizer_standalone.html",
                                   error="Tipo de archivo no válido.")
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        return render_template("anonymizer_standalone.html",
                               error=f"Error en descarga: {str(e)[:100]}")


app.register_blueprint(anonymizer_bp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
