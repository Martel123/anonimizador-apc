"""
Legal Document Anonymizer Module
Rule-based PII detection for Peruvian legal documents.
No paid services - uses regex patterns and heuristics.
"""

import re
import os
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Any
from collections import defaultdict

ALLOWED_EXTENSIONS_ANON = {'.docx', '.pdf'}
MAX_FILE_SIZE_MB = 10
MAX_PDF_PAGES = 50

DNI_PATTERN = re.compile(r'\b[0-9]{8}\b')
RUC_PATTERN = re.compile(r'\b[12][0-9]{10}\b')
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
PHONE_PATTERNS = [
    re.compile(r'\+51\s?9[0-9]{8}\b'),
    re.compile(r'\b9[0-9]{8}\b'),
    re.compile(r'\b0[1-9][0-9]\s?[0-9]{6,7}\b'),
    re.compile(r'\([0-9]{2,3}\)\s?[0-9]{6,7}\b'),
]
EXPEDIENTE_PATTERN = re.compile(r'\b[0-9]{5}-[0-9]{4}-[0-9]-[0-9]{4}-[A-Z]{2}-[A-Z]{2}-[0-9]{2}\b', re.IGNORECASE)
CASILLA_PATTERN = re.compile(r'\bcasilla\s+(?:electr[oó]nica\s+)?(?:n[°oº]?\s*)?[0-9]+\b', re.IGNORECASE)
JUZGADO_PATTERN = re.compile(r'\b(?:[0-9]+[°ºo]?\s*)?juzgado\s+(?:de\s+)?(?:paz\s+letrado|familia|civil|penal|laboral|mixto|comercial)[^.]*', re.IGNORECASE)

ADDRESS_KEYWORDS = [
    r'\bAv(?:enida)?\.?\s+',
    r'\bJr(?:\.|irón)?\s+',
    r'\bCalle\s+',
    r'\bPsje(?:\.|Pasaje)?\s+',
    r'\bMz(?:\.|anzana)?\s+',
    r'\bLt(?:\.|ote)?\s+',
    r'\bDpto(?:\.|Departamento)?\s+',
    r'\bUrb(?:\.|anizaci[oó]n)?\s+',
    r'\bAA\.?HH\.?\s+',
    r'\bP\.?J\.?\s+',
    r'\bDistrito\s+(?:de\s+)?',
    r'\bProvincia\s+(?:de\s+)?',
]

ADDRESS_PATTERN = re.compile(
    r'(' + '|'.join(ADDRESS_KEYWORDS) + r')[A-Za-záéíóúñÁÉÍÓÚÑ0-9\s,.\-°º#]+(?=[\.\n,;]|$)',
    re.IGNORECASE
)

NAME_CONTEXT_KEYWORDS = [
    r'(?:señor|señora|sr\.|sra\.)\s+',
    r'(?:don|doña)\s+',
    r'(?:el|la)\s+(?:demandante|demandado|demandada)\s+',
    r'(?:el|la)\s+(?:solicitante|invitado|invitada)\s+',
    r'identificad[oa]\s+con\s+(?:DNI|documento)',
    r'(?:abogad[oa]|letrad[oa])\s+',
    r'(?:el|la)\s+(?:menor|menores?)\s+',
    r'(?:madre|padre|hijo|hija)\s+',
    r'(?:cónyuge|esposo|esposa)\s+',
    r'(?:testigo|perito)\s+',
]

NAME_PATTERN = re.compile(
    r'(?:' + '|'.join(NAME_CONTEXT_KEYWORDS) + r')([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,4})',
    re.IGNORECASE
)

UPPERCASE_NAME_PATTERN = re.compile(r'\b([A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,4})\b')

MONEY_PATTERN = re.compile(r'(?:S/\.?\s*|US\$\s*|\$\s*|PEN\s+|USD\s+)[0-9]{1,3}(?:[,\'][0-9]{3})*(?:\.[0-9]{2})?', re.IGNORECASE)


class EntityMapping:
    """Maintains consistent mapping of entities to placeholders."""
    
    def __init__(self):
        self.mappings: Dict[str, Dict[str, str]] = defaultdict(dict)
        self.counters: Dict[str, int] = defaultdict(int)
        self.reverse_mappings: Dict[str, Dict[str, str]] = defaultdict(dict)
    
    def get_placeholder(self, entity_type: str, value: str) -> str:
        """Get or create a placeholder for a value."""
        normalized = value.strip().upper()
        
        if normalized in self.mappings[entity_type]:
            return self.mappings[entity_type][normalized]
        
        self.counters[entity_type] += 1
        placeholder = f"{{{{{entity_type}_{self.counters[entity_type]}}}}}"
        
        self.mappings[entity_type][normalized] = placeholder
        self.reverse_mappings[entity_type][placeholder] = self._mask_value(value, entity_type)
        
        return placeholder
    
    def _mask_value(self, value: str, entity_type: str) -> str:
        """Create a masked version of the value for the report."""
        if len(value) <= 4:
            return '*' * len(value)
        
        if entity_type in ['DNI', 'RUC']:
            return value[:2] + '*' * (len(value) - 4) + value[-2:]
        elif entity_type == 'EMAIL':
            parts = value.split('@')
            if len(parts) == 2:
                user = parts[0][:2] + '***'
                return f"{user}@{parts[1]}"
        elif entity_type == 'TELEFONO':
            return value[:3] + '***' + value[-2:]
        elif entity_type in ['NOMBRE', 'PERSONA']:
            words = value.split()
            if len(words) >= 2:
                return words[0][:2] + '*** ' + words[-1][:2] + '***'
            return value[:2] + '***'
        
        if len(value) > 8:
            return value[:3] + '...' + value[-3:]
        return value[:2] + '***'
    
    def get_summary(self) -> Dict[str, int]:
        """Get count of entities by type."""
        return {k: len(v) for k, v in self.mappings.items() if v}
    
    def get_replacements_for_report(self) -> Dict[str, List[Dict[str, str]]]:
        """Get list of replacements for the report."""
        result = {}
        for entity_type, placeholders in self.reverse_mappings.items():
            if placeholders:
                result[entity_type] = [
                    {"placeholder": ph, "masked_original": masked}
                    for ph, masked in placeholders.items()
                ]
        return result


def detect_entities(text: str) -> Tuple[List[Tuple[str, str, int, int]], EntityMapping]:
    """
    Detect all PII entities in text.
    Returns list of (entity_type, value, start, end) and the mapping object.
    """
    entities = []
    mapping = EntityMapping()
    
    for match in EXPEDIENTE_PATTERN.finditer(text):
        entities.append(('EXPEDIENTE', match.group(), match.start(), match.end()))
    
    for match in CASILLA_PATTERN.finditer(text):
        entities.append(('CASILLA', match.group(), match.start(), match.end()))
    
    for match in JUZGADO_PATTERN.finditer(text):
        entities.append(('JUZGADO', match.group(), match.start(), match.end()))
    
    for match in RUC_PATTERN.finditer(text):
        value = match.group()
        if not _is_money_context(text, match.start(), match.end()):
            entities.append(('RUC', value, match.start(), match.end()))
    
    ruc_positions = {(e[2], e[3]) for e in entities if e[0] == 'RUC'}
    for match in DNI_PATTERN.finditer(text):
        value = match.group()
        if (match.start(), match.end()) not in ruc_positions:
            if not _is_money_context(text, match.start(), match.end()):
                if not _is_date_context(text, match.start(), match.end()):
                    entities.append(('DNI', value, match.start(), match.end()))
    
    for match in EMAIL_PATTERN.finditer(text):
        entities.append(('EMAIL', match.group(), match.start(), match.end()))
    
    for pattern in PHONE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group()
            if not any(e[2] == match.start() for e in entities):
                entities.append(('TELEFONO', value, match.start(), match.end()))
    
    for match in ADDRESS_PATTERN.finditer(text):
        value = match.group().strip()
        if len(value) > 10:
            entities.append(('DIRECCION', value, match.start(), match.end()))
    
    for match in NAME_PATTERN.finditer(text):
        name = match.group(1).strip()
        if len(name) > 5 and ' ' in name:
            full_match_start = match.start()
            name_start = match.start() + match.group().index(name)
            entities.append(('PERSONA', name, name_start, name_start + len(name)))
    
    potential_names = []
    for match in UPPERCASE_NAME_PATTERN.finditer(text):
        value = match.group(1)
        words = value.split()
        if 2 <= len(words) <= 5:
            if not any(w.lower() in ['de', 'la', 'el', 'los', 'las', 'del', 'y'] for w in words):
                if not any(e[2] <= match.start() < e[3] or e[2] < match.end() <= e[3] for e in entities):
                    potential_names.append(('PERSONA', value, match.start(), match.end()))
    
    entities.extend(potential_names[:20])
    
    entities.sort(key=lambda x: (x[2], -(x[3] - x[2])))
    
    return entities, mapping


def _is_money_context(text: str, start: int, end: int) -> bool:
    """Check if the number is in a money context."""
    context_start = max(0, start - 20)
    context = text[context_start:start].lower()
    money_indicators = ['s/', 's/.', 'us$', '$', 'soles', 'dólares', 'dolares', 'monto', 'suma', 'pago', 'pen', 'usd']
    return any(ind in context for ind in money_indicators)


def _is_date_context(text: str, start: int, end: int) -> bool:
    """Check if the number looks like a date."""
    value = text[start:end]
    context_end = min(len(text), end + 15)
    after = text[end:context_end].lower().strip()
    
    if after.startswith(('/', '-')) and len(after) > 1 and after[1:3].isdigit():
        return True
    
    if int(value) > 31000000:
        return False
    
    return False


def replace_entities(text: str, entities: List[Tuple[str, str, int, int]], mapping: EntityMapping) -> str:
    """Replace all detected entities with their placeholders."""
    non_overlapping = []
    for entity in entities:
        overlaps = False
        for existing in non_overlapping:
            if not (entity[3] <= existing[2] or entity[2] >= existing[3]):
                overlaps = True
                break
        if not overlaps:
            non_overlapping.append(entity)
    
    non_overlapping.sort(key=lambda x: -x[2])
    
    result = text
    for entity_type, value, start, end in non_overlapping:
        placeholder = mapping.get_placeholder(entity_type, value)
        result = result[:start] + placeholder + result[end:]
    
    return result


def anonymize_docx(file_path: str) -> Tuple[str, Dict[str, Any], EntityMapping]:
    """
    Anonymize a DOCX file.
    Returns the anonymized text, summary dict, and entity mapping.
    """
    from docx import Document
    
    doc = Document(file_path)
    
    all_text_parts = []
    
    for para in doc.paragraphs:
        all_text_parts.append(para.text)
    
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    all_text_parts.append(para.text)
    
    full_text = '\n'.join(all_text_parts)
    
    entities, mapping = detect_entities(full_text)
    
    for para in doc.paragraphs:
        para_entities = [(e[0], e[1]) for e in entities if e[1] in para.text]
        for entity_type, value in para_entities:
            placeholder = mapping.get_placeholder(entity_type, value)
            para.text = para.text.replace(value, placeholder)
    
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    para_entities = [(e[0], e[1]) for e in entities if e[1] in para.text]
                    for entity_type, value in para_entities:
                        placeholder = mapping.get_placeholder(entity_type, value)
                        para.text = para.text.replace(value, placeholder)
    
    summary = {
        'entities_found': mapping.get_summary(),
        'total_entities': sum(mapping.get_summary().values()),
        'replacements': mapping.get_replacements_for_report()
    }
    
    return doc, summary, mapping


def anonymize_pdf(file_path: str) -> Tuple[str, Dict[str, Any], EntityMapping, bool]:
    """
    Anonymize a PDF file.
    Returns (text, summary, mapping, is_scanned).
    For scanned PDFs, returns is_scanned=True.
    """
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        raise ImportError("PyPDF2 is required for PDF processing")
    
    reader = PdfReader(file_path)
    
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"El PDF excede el límite de {MAX_PDF_PAGES} páginas")
    
    all_text = []
    for page in reader.pages:
        text = page.extract_text() or ''
        all_text.append(text)
    
    full_text = '\n\n'.join(all_text)
    
    if len(full_text.strip()) < 100:
        return None, {}, EntityMapping(), True
    
    entities, mapping = detect_entities(full_text)
    anonymized_text = replace_entities(full_text, entities, mapping)
    
    summary = {
        'entities_found': mapping.get_summary(),
        'total_entities': sum(mapping.get_summary().values()),
        'replacements': mapping.get_replacements_for_report(),
        'page_count': len(reader.pages)
    }
    
    return anonymized_text, summary, mapping, False


def save_anonymized_docx(doc, output_path: str):
    """Save the anonymized DOCX document."""
    doc.save(output_path)


def create_anonymized_pdf(text: str, output_path: str):
    """Create a new PDF with the anonymized text."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    
    doc = SimpleDocTemplate(output_path, pagesize=letter,
                           rightMargin=72, leftMargin=72,
                           topMargin=72, bottomMargin=72)
    
    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=11,
        leading=14,
        spaceAfter=12
    )
    
    story = []
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if para.strip():
            clean_text = para.replace('\n', ' ').strip()
            clean_text = clean_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            story.append(Paragraph(clean_text, normal_style))
            story.append(Spacer(1, 6))
    
    doc.build(story)


def generate_report(summary: Dict[str, Any], original_filename: str, file_type: str) -> Dict[str, Any]:
    """Generate a detailed anonymization report."""
    report = {
        'fecha_procesamiento': datetime.now().isoformat(),
        'archivo_original': original_filename,
        'tipo_archivo': file_type,
        'resumen': {
            'total_entidades_detectadas': summary.get('total_entities', 0),
            'entidades_por_tipo': summary.get('entities_found', {}),
        },
        'reemplazos': summary.get('replacements', {}),
        'advertencias': [],
        'version': '1.0.0'
    }
    
    if 'DIRECCION' in summary.get('entities_found', {}):
        report['advertencias'].append(
            'Las direcciones fueron detectadas mediante heurísticas. '
            'Revise el documento para confirmar la correcta anonimización.'
        )
    
    if 'PERSONA' in summary.get('entities_found', {}):
        report['advertencias'].append(
            'Los nombres fueron detectados mediante patrones. '
            'Pueden existir nombres adicionales no detectados.'
        )
    
    return report


def generate_report_txt(report: Dict[str, Any]) -> str:
    """Generate a text version of the report."""
    lines = [
        "=" * 60,
        "REPORTE DE ANONIMIZACIÓN",
        "=" * 60,
        "",
        f"Fecha de procesamiento: {report['fecha_procesamiento']}",
        f"Archivo original: {report['archivo_original']}",
        f"Tipo de archivo: {report['tipo_archivo']}",
        "",
        "-" * 40,
        "RESUMEN DE ENTIDADES DETECTADAS",
        "-" * 40,
        f"Total de entidades: {report['resumen']['total_entidades_detectadas']}",
        "",
    ]
    
    for entity_type, count in report['resumen']['entidades_por_tipo'].items():
        lines.append(f"  {entity_type}: {count}")
    
    lines.extend(["", "-" * 40, "REEMPLAZOS REALIZADOS", "-" * 40])
    
    for entity_type, replacements in report['reemplazos'].items():
        lines.append(f"\n{entity_type}:")
        for r in replacements:
            lines.append(f"  {r['placeholder']} <- {r['masked_original']}")
    
    if report['advertencias']:
        lines.extend(["", "-" * 40, "ADVERTENCIAS", "-" * 40])
        for adv in report['advertencias']:
            lines.append(f"  * {adv}")
    
    lines.extend(["", "=" * 60])
    
    return '\n'.join(lines)


def cleanup_old_files(directory: str, max_age_minutes: int = 30):
    """Remove files older than max_age_minutes from directory."""
    import time
    
    if not os.path.exists(directory):
        return
    
    current_time = time.time()
    max_age_seconds = max_age_minutes * 60
    
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        if os.path.isfile(filepath):
            file_age = current_time - os.path.getmtime(filepath)
            if file_age > max_age_seconds:
                try:
                    os.remove(filepath)
                    logging.info(f"Cleaned up old file: {filepath}")
                except Exception as e:
                    logging.error(f"Error removing file {filepath}: {e}")
