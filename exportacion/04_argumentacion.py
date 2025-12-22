# ==================== ARGUMENTACIÓN (Sistema completo) ====================
# Líneas 6361-6910 de app.py

CARPETA_ARGUMENTACION = "argumentaciones"

def get_argumentacion_folder(tenant_id):
    folder = os.path.join(CARPETA_ARGUMENTACION, f"tenant_{tenant_id}")
    os.makedirs(folder, exist_ok=True)
    return folder


@app.route("/argumentacion")
@login_required
def argumentacion():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    sesiones = ArgumentationSession.query.filter_by(
        user_id=current_user.id,
        tenant_id=tenant.id,
        activo=True
    ).order_by(ArgumentationSession.updated_at.desc()).limit(10).all()
    
    estilos_personalizados = UserArgumentationStyle.query.filter_by(
        user_id=current_user.id,
        tenant_id=tenant.id,
        activo=True
    ).all()
    
    estilos_predefinidos = UserArgumentationStyle.ESTILOS_PREDEFINIDOS
    
    casos = Case.query.filter_by(tenant_id=tenant.id).order_by(Case.titulo).all()
    
    return render_template("argumentacion.html",
                          sesiones=sesiones,
                          estilos_personalizados=estilos_personalizados,
                          estilos_predefinidos=estilos_predefinidos,
                          casos=casos)


@app.route("/argumentacion/sesion/<int:session_id>")
@login_required
def argumentacion_sesion(session_id):
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    sesion = ArgumentationSession.query.filter_by(
        id=session_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first_or_404()
    
    mensajes = ArgumentationMessage.query.filter_by(session_id=sesion.id).order_by(ArgumentationMessage.created_at.asc()).all()
    
    estilos_personalizados = UserArgumentationStyle.query.filter_by(
        user_id=current_user.id,
        tenant_id=tenant.id,
        activo=True
    ).all()
    
    estilos_predefinidos = UserArgumentationStyle.ESTILOS_PREDEFINIDOS
    
    return render_template("argumentacion_sesion.html",
                          sesion=sesion,
                          mensajes=mensajes,
                          estilos_personalizados=estilos_personalizados,
                          estilos_predefinidos=estilos_predefinidos)


@app.route("/argumentacion/nueva", methods=["POST"])
@login_required
def argumentacion_nueva():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    texto_documento = None
    archivo_nombre = None
    archivo_tipo = None
    case_id = request.form.get("case_id", type=int)
    
    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        filename = secure_filename(archivo.filename)
        ext = os.path.splitext(filename)[1].lower()
        
        if ext not in ['.docx', '.doc', '.txt', '.pdf']:
            flash("Formato no soportado. Use archivos .docx, .txt o .pdf", "error")
            return redirect(url_for('argumentacion'))
        
        archivo_nombre = filename
        archivo_tipo = ext
        
        if ext == '.docx' or ext == '.doc':
            try:
                archivo.seek(0)
                doc = Document(archivo)
                texto_documento = "\n".join([p.text for p in doc.paragraphs])
            except Exception as e:
                logging.error(f"Error leyendo docx: {e}")
                flash("Error al leer el archivo Word.", "error")
                return redirect(url_for('argumentacion'))
        elif ext == '.txt':
            archivo.seek(0)
            texto_documento = archivo.read().decode('utf-8')
        elif ext == '.pdf':
            try:
                from PyPDF2 import PdfReader
                archivo.seek(0)
                reader = PdfReader(archivo)
                texto_documento = "\n".join([page.extract_text() or "" for page in reader.pages])
            except Exception as e:
                logging.error(f"Error leyendo PDF: {e}")
                flash("Error al leer el archivo PDF.", "error")
                return redirect(url_for('argumentacion'))
    
    texto_directo = request.form.get("texto_documento", "").strip()
    if not texto_documento and texto_directo:
        texto_documento = texto_directo
        archivo_nombre = "Texto directo"
        archivo_tipo = "text"
    
    if not texto_documento:
        flash("No se proporcionó ningún documento.", "error")
        return redirect(url_for('argumentacion'))
    
    titulo = archivo_nombre or f"Sesión {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    
    sesion = ArgumentationSession(
        user_id=current_user.id,
        tenant_id=tenant.id,
        case_id=case_id if case_id else None,
        titulo=titulo,
        documento_original=texto_documento,
        archivo_nombre=archivo_nombre,
        archivo_tipo=archivo_tipo
    )
    db.session.add(sesion)
    db.session.commit()
    
    flash("Documento cargado correctamente.", "success")
    return redirect(url_for('argumentacion_sesion', session_id=sesion.id))


@app.route("/argumentacion/mejorar/<int:session_id>", methods=["POST"])
@login_required
def argumentacion_mejorar(session_id):
    tenant = get_current_tenant()
    if not tenant:
        return jsonify({"error": "No autorizado"}), 403
    
    sesion = ArgumentationSession.query.filter_by(
        id=session_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first()
    
    if not sesion:
        return jsonify({"error": "Sesión no encontrada"}), 404
    
    instrucciones = request.form.get("instrucciones", "").strip()
    estilo = request.form.get("estilo", "Formal clásico")
    
    if not instrucciones:
        flash("Por favor, indica qué tipo de mejora deseas.", "error")
        return redirect(url_for('argumentacion_sesion', session_id=session_id))
    
    mensaje_usuario = ArgumentationMessage(
        session_id=sesion.id,
        role="user",
        content=instrucciones,
        estilo_aplicado=estilo
    )
    db.session.add(mensaje_usuario)
    
    documento_actual = sesion.ultima_version_mejorada or sesion.documento_original
    
    estilo_instrucciones = ""
    for e in UserArgumentationStyle.ESTILOS_PREDEFINIDOS:
        if e['nombre'] == estilo:
            estilo_instrucciones = e['instrucciones']
            break
    
    if not estilo_instrucciones:
        estilo_custom = UserArgumentationStyle.query.filter_by(
            user_id=current_user.id,
            nombre=estilo,
            activo=True
        ).first()
        if estilo_custom:
            estilo_instrucciones = estilo_custom.instrucciones
    
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), timeout=150.0)
        
        system_prompt = f"""Actua como un asistente juridico especializado en redaccion y argumentacion.

Tu tarea es modificar directamente el texto del documento juridico, aplicando EXACTAMENTE las instrucciones del usuario, de forma rapida, directa y sin rodeos.

REGLAS ESTRICTAS:
1. Manten intactos todos los datos facticos (nombres, DNIs, fechas, montos, direcciones, numeros de expediente, numeros de cuenta, porcentajes, acuerdos economicos)
2. Si encuentras incoherencias factuales, solo senala el error; no inventes datos nuevos
3. Puedes anadir parrafos completos si el usuario lo pide
4. Puedes eliminar fragmentos si el usuario lo pide
5. Puedes reorganizar la logica argumentativa si ayuda a la claridad
6. Respeta la estructura general (Hechos - Fundamentos - Petitorio)
7. No inventes hechos ni articulos falsos
8. Aplica el estilo solicitado: {estilo}
9. {estilo_instrucciones}

INSTRUCCIONES DEL USUARIO:
{instrucciones}

Devuelve SIEMPRE el documento modificado completo, listo para copiar, sin comentarios meta, solo el contenido final."""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Documento a mejorar:\n\n{documento_actual[:20000]}"}
            ],
            temperature=0.4,
            max_tokens=8000
        )
        
        resultado = response.choices[0].message.content
        
        mensaje_ia = ArgumentationMessage(
            session_id=sesion.id,
            role="assistant",
            content=resultado,
            estilo_aplicado=estilo
        )
        db.session.add(mensaje_ia)
        
        sesion.ultima_version_mejorada = resultado
        sesion.estilo_usado = estilo
        sesion.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash("Argumentación mejorada correctamente.", "success")
        
    except Exception as e:
        logging.error(f"Error mejorando argumentación: {e}")
        error_msg = str(e).lower()
        if 'timeout' in error_msg or 'timed out' in error_msg:
            flash("El documento es muy extenso y la mejora tarda más de lo esperado. Intenta con instrucciones más específicas o un fragmento más corto.", "warning")
        else:
            flash("Error al procesar la mejora. Intenta nuevamente.", "error")
    
    return redirect(url_for('argumentacion_sesion', session_id=session_id))


@app.route("/argumentacion/start", methods=["POST"])
@login_required
def argumentacion_start_job():
    """Inicia un job asíncrono de argumentación."""
    start_argumentation_worker()
    
    tenant = get_current_tenant()
    if not tenant:
        return jsonify({"success": False, "error": "No autorizado"}), 403
    
    data = request.get_json() or {}
    session_id = data.get('session_id')
    instrucciones = data.get('instrucciones', '').strip()
    estilo = data.get('estilo', 'Formal clásico')
    section = data.get('section', 'full')
    
    if not session_id:
        return jsonify({"success": False, "error": "Sesión no especificada"}), 400
    
    if not instrucciones:
        return jsonify({"success": False, "error": "Instrucciones requeridas"}), 400
    
    sesion = ArgumentationSession.query.filter_by(
        id=session_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first()
    
    if not sesion:
        return jsonify({"success": False, "error": "Sesión no encontrada"}), 404
    
    job_type = detect_intent(instrucciones)
    
    job = ArgumentationJob(
        session_id=sesion.id,
        user_id=current_user.id,
        tenant_id=tenant.id,
        section=section,
        job_type=job_type,
        instructions=instrucciones,
        estilo=estilo,
        status='queued'
    )
    db.session.add(job)
    db.session.commit()
    
    argumentation_job_queue.put(job.id)
    
    return jsonify({
        "success": True,
        "job_id": job.id,
        "job_type": job_type,
        "status": "queued"
    })


@app.route("/argumentacion/jobs/<int:job_id>")
@login_required
def argumentacion_job_status(job_id):
    """Consulta el estado de un job de argumentación."""
    tenant = get_current_tenant()
    if not tenant:
        return jsonify({"success": False, "error": "No autorizado"}), 403
    
    job = ArgumentationJob.query.filter_by(
        id=job_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first()
    
    if not job:
        return jsonify({"success": False, "error": "Job no encontrado"}), 404
    
    return jsonify({
        "success": True,
        "job": job.to_dict()
    })


@app.route("/argumentacion/descargar/<int:session_id>")
@login_required
def argumentacion_descargar(session_id):
    tenant = get_current_tenant()
    if not tenant:
        flash("No autorizado", "error")
        return redirect(url_for('argumentacion'))
    
    sesion = ArgumentationSession.query.filter_by(
        id=session_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first_or_404()
    
    texto = sesion.ultima_version_mejorada or sesion.documento_original
    
    doc = Document()
    
    estilo_doc = EstiloDocumento.query.filter_by(tenant_id=tenant.id).first()
    font_name = estilo_doc.fuente if estilo_doc else 'Times New Roman'
    font_size = estilo_doc.tamano_base if estilo_doc else 12
    line_spacing = estilo_doc.interlineado if estilo_doc else 1.5
    
    sections = doc.sections
    for section in sections:
        if estilo_doc:
            section.top_margin = Cm(estilo_doc.margen_superior)
            section.bottom_margin = Cm(estilo_doc.margen_inferior)
            section.left_margin = Cm(estilo_doc.margen_izquierdo)
            section.right_margin = Cm(estilo_doc.margen_derecho)
        else:
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
            try:
                run.add_picture(logo_path, width=Cm(4))
            except Exception as e:
                logging.error(f"Error adding logo to argumentation doc: {e}")
            
            info_lines = tenant.get_header_info()
            for linea in info_lines:
                info_para = header.add_paragraph()
                info_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                info_run = info_para.add_run(linea)
                info_run.font.name = font_name
                info_run.font.size = Pt(9)
                info_para.paragraph_format.space_after = Pt(0)
                info_para.paragraph_format.space_before = Pt(0)
    
    titulos_principales = ['SUMILLA:', 'PETITORIO:', 'HECHOS:', 'FUNDAMENTOS', 'ANEXOS:', 
                          'POR TANTO:', 'VÍA PROCEDIMENTAL:', 'CONTRACAUTELA:',
                          'FUNDAMENTACION JURÍDICA:', 'FUNDAMENTACIÓN JURÍDICA:']
    titulos_secundarios = ['PRIMERO:', 'SEGUNDO:', 'TERCERO:', 'CUARTO:', 'QUINTO:',
                          'SEXTO:', 'SÉPTIMO:', 'OCTAVO:', 'NOVENO:', 'DÉCIMO:']
    
    for parrafo in texto.split('\n'):
        linea = parrafo.strip()
        if not linea:
            continue
        
        p = doc.add_paragraph()
        run = p.add_run(linea)
        run.font.name = font_name
        run.font.size = Pt(font_size)
        
        es_titulo_principal = any(linea.upper().startswith(t.upper()) for t in titulos_principales)
        es_titulo_secundario = any(linea.upper().startswith(t.upper()) for t in titulos_secundarios)
        
        if es_titulo_principal:
            run.bold = True
            p.paragraph_format.space_before = Pt(18)
            p.paragraph_format.space_after = Pt(6)
        elif es_titulo_secundario:
            run.bold = True
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(6)
        
        p.paragraph_format.line_spacing = line_spacing
    
    folder = get_argumentacion_folder(tenant.id)
    nombre_archivo = f"argumentacion_{sesion.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    ruta = os.path.join(folder, nombre_archivo)
    doc.save(ruta)
    
    return send_file(ruta, as_attachment=True, download_name=nombre_archivo)


@app.route("/argumentacion/copiar/<int:session_id>")
@login_required
def argumentacion_copiar_texto(session_id):
    tenant = get_current_tenant()
    if not tenant:
        return jsonify({"error": "No autorizado"}), 403
    
    sesion = ArgumentationSession.query.filter_by(
        id=session_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first()
    
    if not sesion:
        return jsonify({"error": "Sesión no encontrada"}), 404
    
    texto = sesion.ultima_version_mejorada or sesion.documento_original
    return jsonify({"texto": texto})


@app.route("/argumentacion/estilo/nuevo", methods=["POST"])
@login_required
def argumentacion_estilo_nuevo():
    tenant = get_current_tenant()
    if not tenant:
        flash("No autorizado", "error")
        return redirect(url_for('argumentacion'))
    
    nombre = request.form.get("nombre", "").strip()
    descripcion = request.form.get("descripcion", "").strip()
    instrucciones = request.form.get("instrucciones", "").strip()
    
    if not nombre or not instrucciones:
        flash("Nombre e instrucciones son requeridos.", "error")
        return redirect(url_for('argumentacion'))
    
    existe = UserArgumentationStyle.query.filter_by(
        user_id=current_user.id,
        nombre=nombre,
        activo=True
    ).first()
    
    if existe:
        flash("Ya tienes un estilo con ese nombre.", "error")
        return redirect(url_for('argumentacion'))
    
    estilo = UserArgumentationStyle(
        user_id=current_user.id,
        tenant_id=tenant.id,
        nombre=nombre,
        descripcion=descripcion,
        instrucciones=instrucciones
    )
    db.session.add(estilo)
    db.session.commit()
    
    flash("Estilo guardado correctamente.", "success")
    return redirect(url_for('argumentacion'))


@app.route("/argumentacion/estilo/eliminar/<int:estilo_id>", methods=["POST"])
@login_required
def argumentacion_estilo_eliminar(estilo_id):
    tenant = get_current_tenant()
    if not tenant:
        flash("No autorizado", "error")
        return redirect(url_for('argumentacion'))
    
    estilo = UserArgumentationStyle.query.filter_by(
        id=estilo_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first()
    
    if estilo:
        estilo.activo = False
        db.session.commit()
        flash("Estilo eliminado.", "success")
    
    return redirect(url_for('argumentacion'))


@app.route("/argumentacion/eliminar/<int:session_id>", methods=["POST"])
@login_required
def argumentacion_eliminar(session_id):
    tenant = get_current_tenant()
    if not tenant:
        flash("No autorizado", "error")
        return redirect(url_for('argumentacion'))
    
    sesion = ArgumentationSession.query.filter_by(
        id=session_id,
        user_id=current_user.id,
        tenant_id=tenant.id
    ).first()
    
    if sesion:
        sesion.activo = False
        db.session.commit()
        flash("Sesión eliminada.", "success")
    
    return redirect(url_for('argumentacion'))


@app.route("/argumentacion/historial")
@login_required
def argumentacion_historial():
    tenant = get_current_tenant()
    if not tenant:
        flash("No tienes acceso a esta función.", "error")
        return redirect(url_for('index'))
    
    sesiones = ArgumentationSession.query.filter_by(
        user_id=current_user.id,
        tenant_id=tenant.id,
        activo=True
    ).order_by(ArgumentationSession.updated_at.desc()).all()
    
    return render_template("argumentacion_historial.html", sesiones=sesiones)
