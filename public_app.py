"""
Anonimizador Legal Público - App para Render + Blueprint para Replit
=====================================================================
Flujo simplificado: subir → revisar → descargar directo
Sin almacenamiento persistente - todo en memoria/tmp con cleanup inmediato
"""

import os
import uuid
import json
import logging
import tempfile
import traceback
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
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

ALLOWED_EXTENSIONS = {'doc', 'docx', 'pdf', 'txt'}


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


def entity_to_dict(e):
    """Normaliza entidad a dict estándar."""
    if isinstance(e, dict):
        return {
            'type': e.get('type') or e.get('entity_type') or 'UNKNOWN',
            'value': e.get('value') or e.get('text') or '',
            'text': e.get('text') or e.get('value') or '',
            'start': e.get('start', 0),
            'end': e.get('end', 0),
            'confidence': e.get('confidence', 1.0),
            'source': e.get('source', 'unknown')
        }
    
    try:
        text_val = getattr(e, 'value', None) or getattr(e, 'text', None) or str(e)
        return {
            'type': getattr(e, 'type', 'UNKNOWN'),
            'value': text_val,
            'text': text_val,
            'start': getattr(e, 'start', 0),
            'end': getattr(e, 'end', 0),
            'confidence': getattr(e, 'confidence', 1.0),
            'source': getattr(e, 'source', 'unknown')
        }
    except Exception as ex:
        logger.error(f"ENTITY_CONVERT_FAIL | error={ex}")
        return None


def normalize_entities(items):
    """Normaliza lista de entidades a dicts."""
    if not items:
        return []
    result = []
    for e in items:
        d = entity_to_dict(e)
        if d and (d.get('value') or d.get('text')):
            result.append(d)
    return result


def dicts_to_entity_objects(entity_dicts):
    """Convierte dicts a objetos Entity, dividiendo entidades multi-línea."""
    from detector_capas import Entity
    entities = []
    for d in entity_dicts:
        value = d.get('value') or d.get('text', '')
        if not value:
            continue
        
        ent_type = d.get('type', 'UNKNOWN')
        source = d.get('source', 'manual')
        confidence = d.get('confidence', 1.0)
        
        if '\n' in value:
            parts = [p.strip() for p in value.split('\n') if p.strip() and len(p.strip()) > 3]
            for part in parts:
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


def extract_text(file_path, ext):
    """Extrae texto de un archivo."""
    if ext == 'docx':
        from docx import Document
        doc = Document(file_path)
        return '\n'.join([p.text for p in doc.paragraphs])
    
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
        import subprocess
        try:
            result = subprocess.run(
                ['antiword', file_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return result.stdout
        except:
            pass
        raise ValueError("No se pudo procesar archivo DOC. Conviértalo a DOCX.")
    
    return ''


def apply_anonymization(input_path, ext, entities):
    """Aplica anonimización y retorna (output_bytes, output_ext, mapping)."""
    from processor_docx import EntityMapping, process_docx_run_aware
    from collections import defaultdict
    
    entity_objects = dicts_to_entity_objects(entities)
    
    if ext == 'docx':
        from docx import Document
        doc = Document(input_path)
        mapping = EntityMapping()
        stats = process_docx_run_aware(doc, entity_objects, mapping)
        
        output_buffer = BytesIO()
        doc.save(output_buffer)
        output_buffer.seek(0)
        
        return output_buffer.getvalue(), 'docx', mapping.reverse_mappings
    
    elif ext in ('pdf', 'txt', 'doc'):
        text = extract_text(input_path, ext)
        
        counters = defaultdict(int)
        mapping = {}
        
        sorted_entities = sorted(entities, key=lambda e: len(e.get('value', '')), reverse=True)
        
        for ent in sorted_entities:
            value = ent.get('value') or ent.get('text', '')
            ent_type = ent.get('type', 'UNKNOWN')
            
            if not value or value not in text:
                continue
            
            if value not in [m.get('original') for m in mapping.values()]:
                counters[ent_type] += 1
                token = f"{{{{{ent_type}_{counters[ent_type]}}}}}"
                mapping[token] = {'original': value, 'type': ent_type}
                text = text.replace(value, token)
        
        reverse_mapping = {k: v['original'][:20] + '...' if len(v['original']) > 20 else v['original'] 
                          for k, v in mapping.items()}
        
        return text.encode('utf-8'), 'txt', reverse_mapping
    
    raise ValueError(f"Extensión no soportada: {ext}")


@anonymizer_bp.route("/health")
@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@anonymizer_bp.route("/")
@app.route("/")
def index():
    return render_template("anonymizer_standalone.html")


@anonymizer_bp.route("/anonymizer")
@app.route("/anonymizer")
def anonymizer_home():
    return render_template("anonymizer_standalone.html")


@anonymizer_bp.route("/anonymizer/process", methods=["POST"])
@app.route("/anonymizer/process", methods=["POST"])
def anonymizer_process():
    """Procesa archivo y muestra página de revisión."""
    if 'file' not in request.files:
        return render_template("anonymizer_standalone.html", error="No se seleccionó ningún archivo")
    
    file = request.files['file']
    if not file or not file.filename:
        return render_template("anonymizer_standalone.html", error="Archivo vacío")
    
    filename = secure_filename(file.filename)
    ext = get_extension(filename)
    
    if not allowed_file(filename):
        return render_template("anonymizer_standalone.html", 
                               error=f"Formato no soportado: .{ext}. Use DOC, DOCX, PDF o TXT")
    
    job_id = str(uuid.uuid4())
    temp_input = os.path.join(tempfile.gettempdir(), f"in_{job_id}_{filename}")
    
    try:
        file.save(temp_input)
        logger.info(f"UPLOAD | job={job_id} | file={filename} | ext={ext}")
        
        text_content = extract_text(temp_input, ext)
        
        if not text_content or len(text_content.strip()) < 10:
            safe_remove(temp_input)
            return render_template("anonymizer_standalone.html", 
                                   error="No se pudo extraer texto del documento")
        
        from detector_capas import detect_all_pii, post_scan_final
        entities, detect_meta = detect_all_pii(text_content)
        all_entities = normalize_entities(entities)
        
        has_warning, warnings = post_scan_final(text_content)
        text_preview = text_content[:3000] if text_content else ""
        
        logger.info(f"DETECT_OK | job={job_id} | entities={len(all_entities)}")
        
        pending_entities = []
        for i, ent in enumerate(all_entities):
            start = ent.get('start', 0)
            end = ent.get('end', 0)
            context_start = max(0, start - 30)
            context_end = min(len(text_content), end + 30)
            context = text_content[context_start:context_end]
            
            pending_entities.append({
                'index': i,
                'type': ent.get('type', 'UNKNOWN'),
                'value': ent.get('value', ''),
                'confidence': ent.get('confidence', 1.0),
                'context': context
            })
        
        return render_template("anonymizer_review.html",
            temp_input_path=temp_input,
            ext=ext,
            original_filename=filename,
            job_id=job_id,
            pending_count=len(pending_entities),
            document_text=text_preview,
            pending_entities=pending_entities,
            confirmed_count=0,
            entities_summary={},
            all_entities_json=json.dumps(all_entities, ensure_ascii=False)
        )
        
    except Exception as e:
        safe_remove(temp_input)
        logger.error(f"PROCESS_ERROR | job={job_id} | error={e}")
        logger.error(traceback.format_exc())
        return render_template("anonymizer_standalone.html", 
                               error=f"Error procesando documento: {str(e)[:100]}")


@anonymizer_bp.route("/anonymizer/apply", methods=["POST"])
@app.route("/anonymizer/apply", methods=["POST"])
def anonymizer_apply():
    """Aplica anonimización y devuelve archivo directamente."""
    import html
    
    temp_input = request.form.get('temp_input_path', '')
    ext = request.form.get('ext', 'docx')
    original_filename = request.form.get('original_filename', 'documento')
    all_entities_json = request.form.get('all_entities_json', '[]')
    
    if not temp_input or not os.path.exists(temp_input):
        return render_template("anonymizer_standalone.html", 
                               error="Sesión expirada. Suba el documento nuevamente.")
    
    job_id = str(uuid.uuid4())
    
    try:
        all_entities_json = html.unescape(all_entities_json)
        all_entities = json.loads(all_entities_json)
        
        final_entities = []
        for i, ent in enumerate(all_entities):
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
                    'source': 'manual'
                })
        except json.JSONDecodeError:
            pass
        
        logger.info(f"APPLY | job={job_id} | entities={len(final_entities)}")
        
        output_bytes, output_ext, mapping = apply_anonymization(temp_input, ext, final_entities)
        
        base_name = original_filename.rsplit('.', 1)[0] if '.' in original_filename else original_filename
        download_name = f"{base_name}_anonimizado.{output_ext}"
        
        if output_ext == 'docx':
            mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:
            mimetype = 'text/plain; charset=utf-8'
        
        output_buffer = BytesIO(output_bytes)
        
        logger.info(f"APPLY_OK | job={job_id} | size={len(output_bytes)} | entities={len(final_entities)}")
        
        return send_file(
            output_buffer,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype
        )
        
    except Exception as e:
        logger.error(f"APPLY_ERROR | job={job_id} | error={e}")
        logger.error(traceback.format_exc())
        return render_template("anonymizer_standalone.html", 
                               error=f"Error aplicando anonimización: {str(e)[:100]}")
    
    finally:
        safe_remove(temp_input)


app.register_blueprint(anonymizer_bp)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
