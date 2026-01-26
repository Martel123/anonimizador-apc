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
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
import os
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
        
        from detector_capas import DetectorCapas
        detector = DetectorCapas()
        entities = detector.detect(text)
        
        confirmed = []
        needs_review = []
        
        for ent in entities:
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
            'is_txt': True
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


@app.route("/")
def home():
    cleanup_old_files()
    error = None
    if not check_openai_available():
        error = "Servicio temporalmente no disponible. Intente más tarde."
    return render_template("anonymizer_standalone.html", error=error)


@app.route("/anonymizer")
def anonymizer_home():
    return render_template("anonymizer_standalone.html")


@app.route("/health")
def health():
    return "ok"


@app.route("/anonymizer/process", methods=["POST"])
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
        
        logger.info(f"UPLOAD | job={job_id} | file={filename} | ext={ext} | size={file_size}")
        
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
        
        if ext == 'txt':
            result = process_txt_file(temp_input, strict_mode=strict_mode, generate_mapping=generate_mapping)
        else:
            if ext == 'docx':
                try:
                    from docx import Document
                    Document(temp_input)
                    logger.info(f"DOCX_OPEN_OK | job={job_id}")
                except Exception as docx_err:
                    logger.error(f"DOCX_OPEN_FAIL | job={job_id} | error={str(docx_err)[:200]}")
                    safe_remove(temp_input)
                    return render_template("anonymizer_standalone.html",
                        error=f"El archivo parece DOCX pero no se pudo abrir: {str(docx_err)[:100]}"), 400
            
            import anonymizer_robust as anon_robust
            logger.info(f"ROBUST_PATH | {anon_robust.__file__}")
            
            try:
                result = anon_robust.process_document_robust(
                    temp_input, ext, file_size,
                    strict_mode=strict_mode, generate_mapping=generate_mapping
                )
            except Exception as robust_err:
                logger.error(f"ROBUST_FAIL | job={job_id} | ext={ext} | error={str(robust_err)[:200]}")
                logger.error(f"ROBUST_TRACE | {traceback.format_exc()}")
                safe_remove(temp_input)
                return render_template("anonymizer_standalone.html",
                    error=f"Error procesando {ext.upper()}: {str(robust_err)[:150]}"), 500
        
        with jobs_lock:
            jobs_store[job_id] = {
                'created_at': datetime.now(),
                'original_filename': filename,
                'ext': ext,
                'input_path': temp_input,
                'result': result
            }
        
        if not result.get('success', False):
            error_msg = result.get('error', 'Error procesando documento')
            return render_template("anonymizer_standalone.html", error=error_msg)
        
        if result.get('needs_review'):
            return redirect(url_for('anonymizer_review', job_id=job_id))
        
        return redirect(url_for('anonymizer_download_page', job_id=job_id))
        
    except Exception as e:
        logger.error(f"Process error: {e}")
        return render_template("anonymizer_standalone.html",
                               error=f"Error inesperado: {str(e)[:150]}")


@app.route("/anonymizer/review/<job_id>")
def anonymizer_review(job_id):
    with jobs_lock:
        job = jobs_store.get(job_id)
    
    if not job:
        return render_template("anonymizer_standalone.html",
                               error="Sesión expirada. Suba el documento nuevamente.")
    
    result = job.get('result', {})
    return render_template("anonymizer_review_standalone.html",
        job_id=job_id,
        original_filename=job.get('original_filename', 'documento'),
        confirmed=result.get('confirmed', []),
        needs_review=result.get('needs_review', []),
        text_preview=result.get('text_preview', '')[:2000],
        detector_used=result.get('detector_used', 'unknown'),
        post_scan_warning=result.get('post_scan_warning')
    )


@app.route("/anonymizer/review/<job_id>/apply", methods=["POST"])
def anonymizer_apply_review(job_id):
    with jobs_lock:
        job = jobs_store.get(job_id)
    
    if not job:
        return jsonify({'success': False, 'error': 'Sesión expirada'}), 400
    
    try:
        data = request.get_json() or {}
        accepted_indices = data.get('accepted', [])
        
        result = job.get('result', {})
        confirmed = result.get('confirmed', [])
        needs_review = result.get('needs_review', [])
        
        final_entities = list(confirmed)
        for idx in accepted_indices:
            if 0 <= idx < len(needs_review):
                final_entities.append(needs_review[idx])
        
        ext = job.get('ext', 'docx')
        input_path = job.get('input_path')
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_out.{ext}")
        
        if ext == 'docx':
            from processor_docx import anonymize_docx_complete
            anon_result = anonymize_docx_complete(input_path, output_path, strict_mode=True)
            success = anon_result.get('ok', False)
        elif ext == 'txt':
            anon_result = anonymize_txt_file(input_path, output_path, final_entities)
            success = anon_result.get('ok', False)
        else:
            from processor_pdf import anonymize_pdf_to_text
            anon_result = anonymize_pdf_to_text(input_path, strict_mode=True)
            success = anon_result.get('ok', False)
            if success and 'anonymized_text' in anon_result:
                txt_path = output_path.replace('.pdf', '.txt')
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(anon_result['anonymized_text'])
                output_path = txt_path
        
        if success:
            with jobs_lock:
                jobs_store[job_id]['output_path'] = output_path
                jobs_store[job_id]['mapping'] = anon_result.get('mapping', {})
            return jsonify({'success': True, 'redirect': url_for('anonymizer_download_page', job_id=job_id)})
        else:
            return jsonify({'success': False, 'error': anon_result.get('error', 'Error')}), 500
            
    except Exception as e:
        logger.error(f"Apply review error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/anonymizer/download-page/<job_id>")
def anonymizer_download_page(job_id):
    with jobs_lock:
        job = jobs_store.get(job_id)
    
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


@app.route("/anonymizer/download/<job_id>/<file_type>")
def anonymizer_download(job_id, file_type):
    with jobs_lock:
        job = jobs_store.get(job_id)
    
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
                
                with jobs_lock:
                    jobs_store[job_id]['output_path'] = output_path
                    jobs_store[job_id]['mapping'] = anon_result.get('mapping', {})
            
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
