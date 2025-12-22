# ==================== MIS MODELOS (User personal document models) ====================
# Líneas 4698-5019 de app.py

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
