"""
Anonimizador Legal - Standalone version for Render deployment.
No database, only file upload → process → download.
"""

import os
import uuid
import json
import logging
import tempfile
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, flash, jsonify, session
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-change-in-production")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

UPLOAD_FOLDER = "/tmp/anonymizer_uploads"
OUTPUT_FOLDER = "/tmp/anonymizer_outputs"
ALLOWED_EXTENSIONS = {'docx', 'pdf'}
JOB_EXPIRY_MINUTES = 30

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

jobs_store = {}
jobs_lock = threading.Lock()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def cleanup_old_files():
    """Remove files older than JOB_EXPIRY_MINUTES."""
    try:
        now = time.time()
        expiry_seconds = JOB_EXPIRY_MINUTES * 60
        
        for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    filepath = os.path.join(folder, filename)
                    if os.path.isfile(filepath):
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > expiry_seconds:
                            os.remove(filepath)
                            logger.info(f"Cleaned up old file: {filename}")
        
        with jobs_lock:
            expired_jobs = []
            for job_id, job_data in jobs_store.items():
                if 'created_at' in job_data:
                    created = job_data['created_at']
                    if (datetime.now() - created).total_seconds() > expiry_seconds:
                        expired_jobs.append(job_id)
            
            for job_id in expired_jobs:
                del jobs_store[job_id]
                logger.info(f"Cleaned up expired job: {job_id}")
                
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


def start_cleanup_thread():
    """Start background cleanup thread."""
    def cleanup_loop():
        while True:
            time.sleep(300)
            cleanup_old_files()
    
    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()


start_cleanup_thread()


@app.route("/")
def home():
    return render_template("anonymizer_standalone.html")


@app.route("/anonymizer")
def anonymizer_home():
    return render_template("anonymizer_standalone.html")


@app.route("/anonymizer/process", methods=["POST"])
def anonymizer_process():
    """Process uploaded document for anonymization."""
    try:
        if 'file' not in request.files:
            flash("No se seleccionó ningún archivo", "error")
            return redirect(url_for('home'))
        
        file = request.files['file']
        if file.filename == '':
            flash("No se seleccionó ningún archivo", "error")
            return redirect(url_for('home'))
        
        if not allowed_file(file.filename):
            flash("Formato no soportado. Use DOCX o PDF.", "error")
            return redirect(url_for('home'))
        
        job_id = str(uuid.uuid4())
        filename = secure_filename(file.filename)
        ext = filename.rsplit('.', 1)[1].lower()
        
        input_path = os.path.join(UPLOAD_FOLDER, f"{job_id}_input.{ext}")
        file.save(input_path)
        
        file_size = os.path.getsize(input_path)
        
        import anonymizer_robust as anon_robust
        result = anon_robust.process_document_robust(
            input_path, 
            ext, 
            file_size,
            strict_mode=True,
            generate_mapping=True
        )
        
        with jobs_lock:
            jobs_store[job_id] = {
                'created_at': datetime.now(),
                'original_filename': filename,
                'ext': ext,
                'input_path': input_path,
                'result': result
            }
        
        if not result.get('success', False):
            error_msg = result.get('error', 'Error desconocido')
            flash(f"Error procesando documento: {error_msg}", "error")
            return redirect(url_for('home'))
        
        if result.get('needs_review'):
            return redirect(url_for('anonymizer_review', job_id=job_id))
        
        return redirect(url_for('anonymizer_download_page', job_id=job_id))
        
    except Exception as e:
        logger.error(f"Process error: {e}")
        flash(f"Error inesperado: {str(e)}", "error")
        return redirect(url_for('home'))


@app.route("/anonymizer/review/<job_id>")
def anonymizer_review(job_id):
    """Show review page for detected entities."""
    with jobs_lock:
        job = jobs_store.get(job_id)
    
    if not job:
        flash("Sesión expirada. Por favor, suba el documento nuevamente.", "error")
        return redirect(url_for('home'))
    
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
    """Apply review decisions and generate final document."""
    with jobs_lock:
        job = jobs_store.get(job_id)
    
    if not job:
        return jsonify({'success': False, 'error': 'Sesión expirada'}), 400
    
    try:
        data = request.get_json()
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
        output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_output.{ext}")
        
        import anonymizer_robust as anon_robust
        
        if ext == 'docx':
            from processor_docx import anonymize_docx_complete
            anon_result = anonymize_docx_complete(input_path, output_path, strict_mode=True)
            success = anon_result.get('ok', False)
        else:
            from processor_pdf import anonymize_pdf_to_text
            anon_result = anonymize_pdf_to_text(input_path, strict_mode=True)
            success = anon_result.get('ok', False)
            if success and 'anonymized_text' in anon_result:
                with open(output_path.replace('.pdf', '.txt'), 'w', encoding='utf-8') as f:
                    f.write(anon_result['anonymized_text'])
                anon_result['output_path'] = output_path.replace('.pdf', '.txt')
        
        if success:
            with jobs_lock:
                jobs_store[job_id]['output_path'] = output_path
                jobs_store[job_id]['mapping'] = anon_result.get('mapping', {})
            
            return jsonify({
                'success': True,
                'redirect': url_for('anonymizer_download_page', job_id=job_id)
            })
        else:
            return jsonify({
                'success': False,
                'error': anon_result.get('error', 'Error generando documento')
            }), 500
            
    except Exception as e:
        logger.error(f"Apply review error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/anonymizer/download-page/<job_id>")
def anonymizer_download_page(job_id):
    """Show download page."""
    with jobs_lock:
        job = jobs_store.get(job_id)
    
    if not job:
        flash("Sesión expirada. Por favor, suba el documento nuevamente.", "error")
        return redirect(url_for('home'))
    
    result = job.get('result', {})
    
    return render_template("anonymizer_download_standalone.html",
        job_id=job_id,
        original_filename=job.get('original_filename', 'documento'),
        entities_count=len(result.get('confirmed', [])),
        mapping=job.get('mapping', result.get('mapping', {}))
    )


@app.route("/anonymizer/download/<job_id>/<file_type>")
def anonymizer_download(job_id, file_type):
    """Download anonymized document or report."""
    with jobs_lock:
        job = jobs_store.get(job_id)
    
    if not job:
        flash("Sesión expirada", "error")
        return redirect(url_for('home'))
    
    try:
        if file_type == 'document':
            output_path = job.get('output_path')
            if not output_path or not os.path.exists(output_path):
                result = job.get('result', {})
                output_path = result.get('output_path')
            
            if not output_path or not os.path.exists(output_path):
                ext = job.get('ext', 'docx')
                input_path = job.get('input_path')
                output_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_output.{ext}")
                
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
            base_name = original.rsplit('.', 1)[0] if '.' in original else original
            ext = job.get('ext', 'docx')
            download_name = f"{base_name}_anonimizado.{ext}"
            
            return send_file(
                output_path,
                as_attachment=True,
                download_name=download_name
            )
        
        elif file_type == 'report':
            result = job.get('result', {})
            mapping = job.get('mapping', result.get('mapping', {}))
            
            report = {
                'fecha': datetime.now().isoformat(),
                'archivo_original': job.get('original_filename'),
                'entidades_detectadas': len(result.get('confirmed', [])),
                'mapeo': mapping,
                'detector': result.get('detector_used', 'unknown')
            }
            
            report_path = os.path.join(OUTPUT_FOLDER, f"{job_id}_report.json")
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            
            original = job.get('original_filename', 'documento')
            base_name = original.rsplit('.', 1)[0] if '.' in original else original
            
            return send_file(
                report_path,
                as_attachment=True,
                download_name=f"{base_name}_reporte.json"
            )
        
        else:
            flash("Tipo de archivo no válido", "error")
            return redirect(url_for('home'))
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        flash(f"Error en descarga: {str(e)}", "error")
        return redirect(url_for('home'))


@app.route("/health")
def health():
    """Health check endpoint for Render."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
