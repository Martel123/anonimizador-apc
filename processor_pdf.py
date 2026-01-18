"""
Procesador PDF Robusto
======================
Extrae texto de PDFs con múltiples fallbacks y detecta PDFs escaneados.
"""

import re
import os
import logging
from typing import Dict, List, Tuple, Optional, Any
from collections import defaultdict

from detector_capas import detect_all_pii, post_scan_final


MAX_PDF_PAGES = 50
MIN_TEXT_LENGTH = 100  # Mínimo caracteres para considerar PDF con texto


def extract_text_pdf_pymupdf(file_path: str) -> Tuple[Optional[str], int]:
    """Extractor primario usando PyMuPDF (fitz)."""
    try:
        import fitz
        doc = fitz.open(file_path)
        page_count = len(doc)
        
        if page_count > MAX_PDF_PAGES:
            doc.close()
            raise ValueError(f"PDF excede el límite de {MAX_PDF_PAGES} páginas")
        
        text_parts = []
        for page in doc:
            text = page.get_text()
            text_parts.append(text)
        doc.close()
        
        return '\n\n'.join(text_parts), page_count
    except ValueError:
        raise
    except Exception as e:
        logging.warning(f"PyMuPDF extraction failed: {e}")
        return None, 0


def extract_text_pdf_pdfplumber(file_path: str) -> Tuple[Optional[str], int]:
    """Extractor fallback usando pdfplumber."""
    try:
        import pdfplumber
        
        with pdfplumber.open(file_path) as pdf:
            page_count = len(pdf.pages)
            
            if page_count > MAX_PDF_PAGES:
                raise ValueError(f"PDF excede el límite de {MAX_PDF_PAGES} páginas")
            
            text_parts = []
            for page in pdf.pages:
                text = page.extract_text() or ''
                text_parts.append(text)
        
        return '\n\n'.join(text_parts), page_count
    except ValueError:
        raise
    except Exception as e:
        logging.warning(f"pdfplumber extraction failed: {e}")
        return None, 0


def extract_text_pdf_pypdf2(file_path: str) -> Tuple[Optional[str], int]:
    """Extractor legacy usando PyPDF2."""
    try:
        from PyPDF2 import PdfReader
        
        reader = PdfReader(file_path)
        page_count = len(reader.pages)
        
        if page_count > MAX_PDF_PAGES:
            raise ValueError(f"PDF excede el límite de {MAX_PDF_PAGES} páginas")
        
        text_parts = []
        for page in reader.pages:
            text = page.extract_text() or ''
            text_parts.append(text)
        
        return '\n\n'.join(text_parts), page_count
    except ValueError:
        raise
    except Exception as e:
        logging.warning(f"PyPDF2 extraction failed: {e}")
        return None, 0


def extract_text_pdf(file_path: str) -> Dict[str, Any]:
    """
    Extrae texto de PDF con múltiples fallbacks.
    
    Returns:
        Dict con: success, text, page_count, extractor_used, is_scanned, error
    """
    result = {
        'success': False,
        'text': None,
        'page_count': 0,
        'extractor_used': None,
        'is_scanned': False,
        'error': None
    }
    
    extractors = [
        ('pymupdf', extract_text_pdf_pymupdf),
        ('pdfplumber', extract_text_pdf_pdfplumber),
        ('pypdf2', extract_text_pdf_pypdf2),
    ]
    
    for name, extractor in extractors:
        try:
            text, page_count = extractor(file_path)
            if text is not None:
                result['text'] = text
                result['page_count'] = page_count
                result['extractor_used'] = name
                
                # Verificar si es PDF escaneado
                if len(text.strip()) < MIN_TEXT_LENGTH:
                    result['is_scanned'] = True
                    result['error'] = 'PDF_SCANNED'
                else:
                    result['success'] = True
                
                return result
        except ValueError as ve:
            result['error'] = str(ve)
            return result
        except Exception as e:
            logging.warning(f"Extractor {name} failed: {e}")
            continue
    
    result['error'] = 'PDF_PARSE_ERROR'
    return result


class PDFEntityMapping:
    """Mantiene mapeo consistente de valores a tokens para PDF."""
    
    def __init__(self):
        self.mappings: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.counters: Dict[str, int] = defaultdict(int)
        self.reverse_mappings: Dict[str, str] = {}
    
    def get_token(self, entity_type: str, value: str) -> str:
        """Obtiene o crea un token para un valor."""
        normalized = value.strip().upper()
        
        if normalized in self.mappings[entity_type]:
            return self.mappings[entity_type][normalized]
        
        self.counters[entity_type] += 1
        token = f"{{{{{entity_type}_{self.counters[entity_type]}}}}}"
        
        self.mappings[entity_type][normalized] = token
        self.reverse_mappings[token] = self._mask_value(value, entity_type)
        
        return token
    
    def _mask_value(self, value: str, entity_type: str) -> str:
        """Crea versión enmascarada del valor."""
        if len(value) <= 4:
            return '*' * len(value)
        
        if entity_type in ['DNI', 'RUC']:
            return value[:2] + '*' * (len(value) - 4) + value[-2:]
        elif entity_type == 'EMAIL':
            parts = value.split('@')
            if len(parts) == 2:
                return f"{parts[0][:2]}***@{parts[1]}"
        elif entity_type == 'TELEFONO':
            return value[:3] + '***' + value[-2:]
        elif entity_type == 'PERSONA':
            words = value.split()
            if len(words) >= 2:
                return words[0][:2] + '*** ' + words[-1][:2] + '***'
            return value[:2] + '***'
        
        if len(value) > 10:
            return value[:3] + '...' + value[-3:]
        return value[:2] + '***'
    
    def get_summary(self) -> Dict[str, int]:
        return {t: len(m) for t, m in self.mappings.items() if m}


def anonymize_text(text: str, entities, mapping: PDFEntityMapping) -> str:
    """
    Anonimiza texto reemplazando entidades con tokens.
    Ordena por longitud descendente para evitar conflictos.
    """
    # Crear lista de reemplazos ordenada por longitud
    replacements = []
    for entity in sorted(entities, key=lambda e: len(e.value), reverse=True):
        token = mapping.get_token(entity.type, entity.value)
        replacements.append((entity.value, token))
    
    # Aplicar reemplazos
    result = text
    for original, token in replacements:
        result = result.replace(original, token)
    
    return result


def anonymize_pdf_to_text(file_path: str, strict_mode: bool = True) -> Dict[str, Any]:
    """
    Anonimiza un PDF y devuelve texto anonimizado.
    
    Args:
        file_path: Ruta al archivo PDF
        strict_mode: Si True, ejecuta post-scan
    
    Returns:
        Dict con resultado completo
    """
    result = {
        'ok': True,
        'needs_review': False,
        'text': None,
        'anonymized_text': None,
        'entities': [],
        'detection_metadata': {},
        'mapping': {},
        'post_scan_results': [],
        'page_count': 0,
        'extractor_used': None,
        'is_scanned': False,
        'error': None
    }
    
    try:
        # Extraer texto
        extraction = extract_text_pdf(file_path)
        
        if not extraction['success']:
            result['ok'] = False
            result['error'] = extraction['error']
            result['is_scanned'] = extraction.get('is_scanned', False)
            return result
        
        text = extraction['text']
        result['text'] = text
        result['page_count'] = extraction['page_count']
        result['extractor_used'] = extraction['extractor_used']
        
        # Detectar PII
        entities, metadata = detect_all_pii(text)
        result['detection_metadata'] = metadata
        result['entities'] = [
            {'type': e.type, 'start': e.start, 'end': e.end, 'source': e.source}
            for e in entities
        ]
        
        # Anonimizar
        mapping = PDFEntityMapping()
        anonymized = anonymize_text(text, entities, mapping)
        result['anonymized_text'] = anonymized
        result['mapping'] = mapping.reverse_mappings
        
        # POST-SCAN
        if strict_mode:
            needs_review, detected = post_scan_final(anonymized)
            result['needs_review'] = needs_review
            result['post_scan_results'] = detected
        
    except Exception as e:
        logging.error(f"Error in anonymize_pdf_to_text: {e}")
        result['ok'] = False
        result['error'] = str(e)
    
    return result
