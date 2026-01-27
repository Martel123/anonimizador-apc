/* APC Jurídica - Legal Document Anonymizer JS */

document.addEventListener('DOMContentLoaded', function() {
  // ============== UPLOAD PAGE ==============
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const filePreview = document.getElementById('filePreview');
  const fileName = document.getElementById('fileName');
  const fileSize = document.getElementById('fileSize');
  const fileRemove = document.getElementById('fileRemove');
  const uploadForm = document.getElementById('uploadForm');
  const submitBtn = document.getElementById('submitBtn');
  const submitText = document.getElementById('submitText');
  const submitSpinner = document.getElementById('submitSpinner');

  if (dropzone && fileInput) {
    dropzone.addEventListener('click', function() {
      fileInput.click();
    });

    ['dragenter', 'dragover'].forEach(eventName => {
      dropzone.addEventListener(eventName, function(e) {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach(eventName => {
      dropzone.addEventListener(eventName, function(e) {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove('dragover');
      });
    });

    dropzone.addEventListener('drop', function(e) {
      const files = e.dataTransfer.files;
      if (files.length > 0) {
        handleFile(files[0]);
      }
    });

    fileInput.addEventListener('change', function() {
      if (fileInput.files.length > 0) {
        handleFile(fileInput.files[0]);
      }
    });

    if (fileRemove) {
      fileRemove.addEventListener('click', function(e) {
        e.stopPropagation();
        clearFile();
      });
    }

    if (uploadForm) {
      uploadForm.addEventListener('submit', function(e) {
        if (!fileInput.files.length) {
          e.preventDefault();
          alert('Por favor seleccione un archivo');
          return;
        }
        if (submitBtn) {
          submitBtn.disabled = true;
          if (submitText) submitText.textContent = 'Analizando documento...';
          if (submitSpinner) submitSpinner.style.display = 'inline-block';
        }
      });
    }
  }

  function handleFile(file) {
    const allowedExtensions = ['doc', 'docx', 'pdf', 'txt'];
    const maxSize = 10 * 1024 * 1024;
    const ext = file.name.split('.').pop().toLowerCase();
    
    if (!allowedExtensions.includes(ext)) {
      alert('Formato no soportado. Use archivos DOC, DOCX, PDF o TXT.');
      return;
    }
    if (file.size > maxSize) {
      alert('El archivo excede el tamaño máximo de 10MB.');
      return;
    }

    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;

    if (filePreview) {
      filePreview.classList.add('visible');
      if (fileName) fileName.textContent = file.name;
      if (fileSize) fileSize.textContent = formatFileSize(file.size);
    }
    if (submitBtn) {
      submitBtn.disabled = false;
    }
  }

  function clearFile() {
    fileInput.value = '';
    if (filePreview) filePreview.classList.remove('visible');
    if (submitBtn) submitBtn.disabled = true;
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  // ============== REVIEW PAGE ==============
  const selectAllBtn = document.getElementById('selectAllBtn');
  const deselectAllBtn = document.getElementById('deselectAllBtn');
  const selectionCount = document.getElementById('selectionCount');
  const applyForm = document.getElementById('applyForm');
  const applyBtn = document.getElementById('applyBtn');
  const applyText = document.getElementById('applyText');
  const applySpinner = document.getElementById('applySpinner');
  const processingOverlay = document.getElementById('processingOverlay');
  const selectedEntitiesInput = document.getElementById('selectedEntitiesJson');
  const documentText = document.getElementById('documentText');
  const selectionToolbar = document.getElementById('selectionToolbar');
  const manualSection = document.getElementById('manualSection');
  const manualList = document.getElementById('manualList');
  const manualCountEl = document.getElementById('manualCount');

  const manualEntities = [];
  let selectedText = '';

  function getAllCheckboxes() {
    return document.querySelectorAll('.entity-checkbox');
  }

  function updateCounter() {
    const checkboxes = getAllCheckboxes();
    const checked = Array.from(checkboxes).filter(cb => cb.checked).length;
    const total = checkboxes.length + manualEntities.length;
    const totalChecked = checked + manualEntities.length;
    
    if (selectionCount) {
      selectionCount.innerHTML = 'Seleccionadas: <strong>' + totalChecked + '</strong> de ' + total;
    }
    if (applyBtn) {
      applyBtn.disabled = totalChecked === 0;
    }
  }

  if (selectAllBtn) {
    selectAllBtn.addEventListener('click', function() {
      getAllCheckboxes().forEach(cb => cb.checked = true);
      updateCounter();
    });
  }

  if (deselectAllBtn) {
    deselectAllBtn.addEventListener('click', function() {
      getAllCheckboxes().forEach(cb => cb.checked = false);
      updateCounter();
    });
  }

  getAllCheckboxes().forEach(cb => {
    cb.addEventListener('change', updateCounter);
  });

  updateCounter();

  // Manual entity marking
  if (documentText && selectionToolbar) {
    documentText.addEventListener('mouseup', function(e) {
      const selection = window.getSelection();
      selectedText = selection.toString().trim();
      
      if (selectedText.length >= 3) {
        const range = selection.getRangeAt(0);
        const rect = range.getBoundingClientRect();
        
        let top = rect.bottom + window.scrollY + 8;
        let left = rect.left + window.scrollX;
        
        if (top + 150 > window.innerHeight + window.scrollY) {
          top = rect.top + window.scrollY - 150;
        }
        if (left + 380 > window.innerWidth) {
          left = window.innerWidth - 390;
        }
        if (left < 10) left = 10;
        
        selectionToolbar.style.top = top + 'px';
        selectionToolbar.style.left = left + 'px';
        selectionToolbar.style.display = 'block';
      } else {
        selectionToolbar.style.display = 'none';
      }
    });

    document.addEventListener('click', function(e) {
      if (!selectionToolbar.contains(e.target) && !documentText.contains(e.target)) {
        selectionToolbar.style.display = 'none';
      }
    });

    document.querySelectorAll('.mark-type-btn').forEach(btn => {
      btn.addEventListener('click', function() {
        const type = this.dataset.type;
        if (selectedText) {
          addManualEntity(selectedText, type);
          selectionToolbar.style.display = 'none';
          window.getSelection().removeAllRanges();
        }
      });
    });
  }

  function addManualEntity(value, type) {
    const existing = manualEntities.find(e => e.value === value);
    if (existing) return;
    
    manualEntities.push({
      type: type,
      value: value,
      candidates: [value],
      confidence: 1.0,
      source: 'manual'
    });
    
    updateManualList();
    updateCounter();
  }

  function updateManualList() {
    if (!manualCountEl || !manualList || !manualSection) return;
    
    manualCountEl.textContent = manualEntities.length;
    
    if (manualEntities.length > 0) {
      manualSection.style.display = 'block';
      manualList.innerHTML = manualEntities.map((e, idx) => `
        <div class="manual-item">
          <div class="manual-item-content">
            <span class="type-badge type-${e.type}">${e.type}</span>
            <span class="manual-item-value">${e.value}</span>
          </div>
          <button type="button" class="manual-remove" data-idx="${idx}" title="Eliminar">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      `).join('');
      
      document.querySelectorAll('.manual-remove').forEach(btn => {
        btn.addEventListener('click', function() {
          const idx = parseInt(this.dataset.idx);
          manualEntities.splice(idx, 1);
          updateManualList();
          updateCounter();
        });
      });
    } else {
      manualSection.style.display = 'none';
    }
  }

  // Form submit
  if (applyForm) {
    applyForm.addEventListener('submit', function(e) {
      const checkboxes = getAllCheckboxes();
      const selectedEntities = [];

      checkboxes.forEach(cb => {
        if (cb.checked) {
          try {
            const entityData = JSON.parse(cb.dataset.entity);
            selectedEntities.push(entityData);
          } catch (err) {
            console.error('Error parsing entity data:', err);
          }
        }
      });

      // Add manual entities
      manualEntities.forEach(e => selectedEntities.push(e));

      if (selectedEntities.length === 0) {
        e.preventDefault();
        alert('Seleccione al menos una entidad para anonimizar.');
        return;
      }

      if (selectedEntitiesInput) {
        selectedEntitiesInput.value = JSON.stringify(selectedEntities);
      }

      if (applyBtn) {
        applyBtn.disabled = true;
        if (applyText) applyText.innerHTML = '<span class="spinner"></span> Anonimizando...';
        if (applySpinner) applySpinner.style.display = 'inline-block';
      }

      if (processingOverlay) {
        processingOverlay.classList.add('visible');
      }
    });
  }
});
