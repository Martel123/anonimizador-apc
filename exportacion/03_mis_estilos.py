# ==================== MIS ESTILOS (User personal styles) ====================
# Líneas 5024-5126 de app.py

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
