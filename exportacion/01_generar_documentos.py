# ==================== GENERAR DOCUMENTOS (Procesar con IA) ====================
# Líneas 2269-2650 de app.py
# Incluye: procesar_ia, preview, guardar_desde_preview, editar_documento, descargar, historial

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
