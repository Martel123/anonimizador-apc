"""
Final Auditor - Auditoría final obligatoria para garantizar 0 fugas de PII
===========================================================================
Escanea el documento final y:
- Detecta cualquier PII residual (email, DNI, teléfono, colegiatura, direcciones)
- Aplica reemplazo automático de emergencia si es necesario
- Marca el documento como NO SEGURO si hay fugas no corregibles
"""

import re
import logging
from typing import Tuple, List, Dict, Any, Optional
from dataclasses import dataclass

DNI_PATTERN = re.compile(r'\b(\d{8})\b')
EMAIL_PATTERN = re.compile(r'\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b', re.IGNORECASE)

PHONE_PATTERNS = [
    re.compile(r'(\+51[\s\-]?9[\d\s\-]{8,12})\b'),
    re.compile(r'\b(9\d{2}[\s\-]?\d{3}[\s\-]?\d{3})\b'),
    re.compile(r'\b(9\d{8})\b'),
    re.compile(r'(\(\s*0?1\s*\)[\s\-]?\d{3}[\s\-]?\d{4})'),
    re.compile(r'\b(0?1[\s\-]?\d{3}[\s\-]?\d{4})\b'),
    re.compile(r'\b(0[1-9]\d?[\s\-]?\d{6,7})\b'),
]

COLEGIATURA_PATTERN = re.compile(
    r'(?:C\.?A\.?L\.?|CAL|CMP|CIP|CAP|CPA|Colegiatura|Colegio de Abogados|N[°º]?\s*(?:de\s+)?Colegiatura)'
    r'[\s:N°º]*(\d{4,6})',
    re.IGNORECASE
)

RUC_PATTERN = re.compile(r'\b((?:10|20)\d{9})\b')

ACTA_REGISTRO_PATTERNS = [
    re.compile(
        r'(?:Acta\s+(?:de\s+)?[Cc]onciliaci[óo]n|N[°º]\s*(?:de\s+)?[Aa]cta|Registro\s+N[°º]?|N[°º]\s*(?:de\s+)?[Rr]egistro|'
        r'Constancia\s+N[°º]?|Documento\s+N[°º]?|Expediente\s+N[°º]?)'
        r'[\s:N°º]*(\d{4,12}(?:[-/]\d{2,6})?)',
        re.IGNORECASE
    ),
]

PLACA_PATTERN = re.compile(
    r'\b([A-Z]{1,3}[-\s]?\d{1,3}[-\s]?[A-Z0-9]{1,3})\b',
    re.IGNORECASE
)

PLACA_POSITIVE_CONTEXTS = [
    'placa', 'vehiculo', 'vehículo', 'automovil', 'automóvil', 'auto', 'camioneta',
    'moto', 'motocicleta', 'camion', 'camión', 'bus', 'ómnibus', 'omnibus'
]

DIRECCION_PATTERNS = [
    re.compile(
        r'(?:Calle|Av\.?|Avenida|Jr\.?|Jirón|Jiron|Psje\.?|Pasaje|Alameda|Malecón|Malecon)'
        r'\s+[A-ZÁÉÍÓÚÑa-záéíóúñ\s]+(?:N[°º]?\s*\d+|Nro\.?\s*\d+|\d+)'
        r'(?:\s*[-,]\s*(?:Dpto\.?|Dep\.?|Oficina|Of\.?|Int\.?|Piso)\s*\d+[A-Z]?)?'
        r'(?:\s*[-,]\s*(?:Mz\.?|Manzana)\s*[A-Z0-9]+)?'
        r'(?:\s*[-,]\s*(?:Lt\.?|Lote)\s*\d+)?'
        r'(?:\s*[-,]\s*(?:Urb\.?|Urbanización|Urbanizacion)\s+[A-ZÁÉÍÓÚÑa-záéíóúñ\s]+)?',
        re.IGNORECASE
    ),
    re.compile(
        r'(?:Mz\.?|Manzana)\s*[A-Z0-9]+\s*[-,]?\s*(?:Lt\.?|Lote)\s*\d+'
        r'(?:\s*[-,]\s*(?:Urb\.?|Urbanización|Urbanizacion)\s+[A-ZÁÉÍÓÚÑa-záéíóúñ\s]+)?',
        re.IGNORECASE
    ),
]

TOKEN_PATTERN = re.compile(r'\{\{[A-Z_]+_\d+\}\}')

MONEY_INDICATORS = [
    'S/', 'S/.', 'US$', '$', 'PEN', 'USD', 'soles', 'dólares', 'dolares',
    '%', 'porcentaje', 'por ciento', 'puntos', 'cuotas', 'meses', 'días',
    'años', 'metros', 'kilos', 'gramos', 'horas', 'minutos'
]

LEGAL_NUMBER_CONTEXTS = [
    'artículo', 'articulo', 'art.', 'inciso', 'numeral', 'literal',
    'ley', 'decreto', 'resolución', 'folio', 'página', 'cuaderno', 'tomo', 'legajo'
]

DNI_POSITIVE_CONTEXTS = [
    'dni', 'd.n.i', 'documento de identidad', 'doc. identidad', 'identificado con',
    'identificada con', 'n°', 'nº', 'numero', 'número'
]


@dataclass
class AuditResult:
    """Resultado de la auditoría final."""
    is_safe: bool
    leaks_found: List[Dict[str, Any]]
    leaks_auto_fixed: int
    remaining_leaks: int
    fixed_text: Optional[str]
    warnings: List[str]
    replacements: List[Tuple[str, str]] = None  # Lista de (valor_original, token)
    
    def __post_init__(self):
        if self.replacements is None:
            self.replacements = []


def is_money_context(text: str, start: int, end: int) -> bool:
    """Verifica si un número está en contexto monetario."""
    window = 40
    before = text[max(0, start - window):start].lower()
    after = text[end:min(len(text), end + window)].lower()
    
    for indicator in MONEY_INDICATORS:
        if indicator.lower() in before or indicator.lower() in after:
            return True
    return False


def is_legal_number_context(text: str, start: int, end: int) -> bool:
    """Verifica si un número está en contexto legal (artículo, ley, etc.)."""
    window = 30
    before = text[max(0, start - window):start].lower()
    
    for indicator in LEGAL_NUMBER_CONTEXTS:
        if indicator in before:
            return True
    return False


def is_date_context(text: str, start: int, end: int) -> bool:
    """Verifica si 8 dígitos son una fecha (ej: 20240115)."""
    value = text[start:end]
    if len(value) == 8:
        try:
            year = int(value[:4])
            month = int(value[4:6])
            day = int(value[6:8])
            if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                return True
        except:
            pass
    return False


def has_dni_positive_context(text: str, start: int, end: int) -> bool:
    """Verifica si hay contexto que indica que es un DNI real."""
    window = 50
    before = text[max(0, start - window):start].lower()
    for ctx in DNI_POSITIVE_CONTEXTS:
        if ctx in before:
            return True
    return False


def find_dni_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra posibles DNIs que quedaron sin anonimizar."""
    leaks = []
    
    for match in DNI_PATTERN.finditer(text):
        value = match.group(1)
        start, end = match.start(1), match.end(1)
        
        if TOKEN_PATTERN.search(text[max(0, start-20):end+20]):
            continue
        
        if is_date_context(text, start, end):
            continue
        
        has_positive_context = has_dni_positive_context(text, start, end)
        
        if not has_positive_context:
            if is_money_context(text, start, end):
                continue
            if is_legal_number_context(text, start, end):
                continue
        
        leaks.append({
            'type': 'DNI',
            'value': value,
            'start': start,
            'end': end,
            'context': text[max(0, start-30):min(len(text), end+30)],
            'fixable': True
        })
    
    return leaks


def find_colegiatura_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra números de colegiatura que quedaron sin anonimizar."""
    leaks = []
    
    for match in COLEGIATURA_PATTERN.finditer(text):
        full_match = match.group(0)
        value = match.group(1) if match.lastindex else full_match
        start, end = match.start(), match.end()
        
        if TOKEN_PATTERN.search(text[max(0, start-20):end+20]):
            continue
        
        leaks.append({
            'type': 'COLEGIATURA',
            'value': full_match,
            'start': start,
            'end': end,
            'context': text[max(0, start-20):min(len(text), end+20)],
            'fixable': True
        })
    
    return leaks


def find_ruc_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra RUCs que quedaron sin anonimizar."""
    leaks = []
    
    for match in RUC_PATTERN.finditer(text):
        value = match.group(1)
        start, end = match.start(1), match.end(1)
        
        if TOKEN_PATTERN.search(text[max(0, start-20):end+20]):
            continue
        
        before = text[max(0, start-30):start].lower()
        if 'ruc' not in before:
            continue
        
        leaks.append({
            'type': 'RUC',
            'value': value,
            'start': start,
            'end': end,
            'context': text[max(0, start-20):min(len(text), end+20)],
            'fixable': True
        })
    
    return leaks


def find_acta_registro_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra números de acta, registro, expediente, constancia que quedaron sin anonimizar."""
    leaks = []
    
    for pattern in ACTA_REGISTRO_PATTERNS:
        for match in pattern.finditer(text):
            full_match = match.group(0)
            value = match.group(1) if match.lastindex else full_match
            start, end = match.start(), match.end()
            
            if TOKEN_PATTERN.search(text[max(0, start-20):end+20]):
                continue
            
            if '{{' in full_match:
                continue
            
            leaks.append({
                'type': 'ACTA_REGISTRO',
                'value': full_match,
                'start': start,
                'end': end,
                'context': text[max(0, start-20):min(len(text), end+20)],
                'fixable': True
            })
    
    seen = set()
    unique_leaks = []
    for leak in leaks:
        if leak['value'] not in seen:
            seen.add(leak['value'])
            unique_leaks.append(leak)
    
    return unique_leaks


def has_placa_positive_context(text: str, start: int, end: int) -> bool:
    """Verifica si hay contexto que indica que es una placa vehicular."""
    window = 50
    before = text[max(0, start - window):start].lower()
    after = text[end:min(len(text), end + window)].lower()
    for ctx in PLACA_POSITIVE_CONTEXTS:
        if ctx in before or ctx in after:
            return True
    return False


def find_placa_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra placas vehiculares que quedaron sin anonimizar."""
    leaks = []
    
    for match in PLACA_PATTERN.finditer(text):
        value = match.group(1)
        start, end = match.start(1), match.end(1)
        
        if TOKEN_PATTERN.search(text[max(0, start-20):end+20]):
            continue
        
        if not has_placa_positive_context(text, start, end):
            continue
        
        if len(value.replace('-', '').replace(' ', '')) < 5:
            continue
        
        leaks.append({
            'type': 'PLACA',
            'value': value,
            'start': start,
            'end': end,
            'context': text[max(0, start-20):min(len(text), end+20)],
            'fixable': True
        })
    
    return leaks


def find_direccion_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra direcciones que quedaron sin anonimizar."""
    leaks = []
    
    for pattern in DIRECCION_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            start, end = match.start(), match.end()
            
            if TOKEN_PATTERN.search(text[max(0, start-10):end+10]):
                continue
            
            if '{{' in value:
                continue
            
            leaks.append({
                'type': 'DIRECCION',
                'value': value,
                'start': start,
                'end': end,
                'context': text[max(0, start-10):min(len(text), end+10)],
                'fixable': True
            })
    
    seen = set()
    unique_leaks = []
    for leak in leaks:
        if leak['value'] not in seen:
            seen.add(leak['value'])
            unique_leaks.append(leak)
    
    return unique_leaks


def find_email_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra emails que quedaron sin anonimizar."""
    leaks = []
    
    for match in EMAIL_PATTERN.finditer(text):
        value = match.group(1)
        
        if TOKEN_PATTERN.search(value):
            continue
        
        if 'example.com' in value.lower() or 'test.com' in value.lower():
            continue
        
        leaks.append({
            'type': 'EMAIL',
            'value': value,
            'start': match.start(1),
            'end': match.end(1),
            'context': text[max(0, match.start()-20):min(len(text), match.end()+20)],
            'fixable': True
        })
    
    return leaks


def find_phone_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra teléfonos que quedaron sin anonimizar."""
    leaks = []
    
    for pattern in PHONE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1)
            start = match.start(1)
            end = match.end(1)
            
            if TOKEN_PATTERN.search(text[max(0, start-20):end+20]):
                continue
            
            if is_money_context(text, start, end):
                continue
            
            leaks.append({
                'type': 'TELEFONO',
                'value': value,
                'start': start,
                'end': end,
                'context': text[max(0, start-20):min(len(text), end+20)],
                'fixable': True
            })
    
    seen = set()
    unique_leaks = []
    for leak in leaks:
        key = (leak['start'], leak['end'])
        if key not in seen:
            seen.add(key)
            unique_leaks.append(leak)
    
    return unique_leaks


def auto_fix_leaks(text: str, leaks: List[Dict[str, Any]], 
                   existing_counters: Optional[Dict[str, int]] = None) -> Tuple[str, int, List[Tuple[str, str]]]:
    """
    Aplica reemplazo automático de emergencia a las fugas detectadas.
    Retorna (texto_corregido, cantidad_correcciones, lista_de_reemplazos).
    
    La lista de reemplazos contiene tuplas (valor_original, token) que pueden
    ser aplicadas directamente a un documento DOCX.
    """
    if not leaks:
        return text, 0, []
    
    counters = existing_counters.copy() if existing_counters else {}
    
    sorted_leaks = sorted(leaks, key=lambda x: -x['start'])
    
    fixes = 0
    result = text
    replacements = []  # Lista de (valor_original, token)
    
    for leak in sorted_leaks:
        if not leak.get('fixable', True):
            continue
        
        entity_type = leak['type']
        value = leak['value']
        
        # Evitar duplicados (mismo valor ya asignado)
        existing_replacement = next((r for r in replacements if r[0] == value), None)
        if existing_replacement:
            token = existing_replacement[1]
        else:
            if entity_type not in counters:
                counters[entity_type] = 99
            counters[entity_type] += 1
            token = f"{{{{{entity_type}_{counters[entity_type]}}}}}"
            replacements.append((value, token))
        
        result = result[:leak['start']] + token + result[leak['end']:]
        fixes += 1
        
        logging.warning(f"AUTO-FIX: Replaced leaked {entity_type} '{value[:20]}...' with {token}")
    
    return result, fixes, replacements


def _find_all_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra todas las fugas de PII en el texto."""
    all_leaks = []
    
    all_leaks.extend(find_dni_leaks(text))
    all_leaks.extend(find_email_leaks(text))
    all_leaks.extend(find_phone_leaks(text))
    all_leaks.extend(find_colegiatura_leaks(text))
    all_leaks.extend(find_ruc_leaks(text))
    all_leaks.extend(find_direccion_leaks(text))
    all_leaks.extend(find_acta_registro_leaks(text))
    all_leaks.extend(find_placa_leaks(text))
    
    return all_leaks


def audit_document(text: str, auto_fix: bool = True,
                   existing_counters: Optional[Dict[str, int]] = None) -> AuditResult:
    """
    AUDITOR FINAL OBLIGATORIO.
    Escanea el documento y garantiza 0 fugas de PII.
    
    Args:
        text: Texto del documento ya anonimizado
        auto_fix: Si True, aplica correcciones automáticas
        existing_counters: Contadores existentes para mantener consistencia de tokens
    
    Returns:
        AuditResult con el estado de seguridad del documento
    """
    warnings = []
    
    all_leaks = _find_all_leaks(text)
    
    if all_leaks:
        by_type = {}
        for leak in all_leaks:
            by_type[leak['type']] = by_type.get(leak['type'], 0) + 1
        for t, c in by_type.items():
            logging.warning(f"AUDIT: Found {c} potential {t} leaks")
    
    fixed_text = text
    leaks_auto_fixed = 0
    replacements = []
    remaining = []
    
    if auto_fix and all_leaks:
        fixed_text, leaks_auto_fixed, replacements = auto_fix_leaks(text, all_leaks, existing_counters)
        
        remaining = _find_all_leaks(fixed_text)
        
        if remaining:
            warnings.append(f"CRITICAL: {len(remaining)} leaks could not be auto-fixed")
    
    remaining_leaks = len(remaining) if auto_fix else len(all_leaks)
    is_safe = remaining_leaks == 0
    
    if not is_safe:
        warnings.append("DOCUMENT MARKED AS NOT SAFE - manual review required")
    
    if leaks_auto_fixed > 0:
        warnings.append(f"Applied {leaks_auto_fixed} emergency auto-fixes")
    
    return AuditResult(
        is_safe=is_safe,
        replacements=replacements,
        leaks_found=all_leaks,
        leaks_auto_fixed=leaks_auto_fixed,
        remaining_leaks=remaining_leaks,
        fixed_text=fixed_text if auto_fix else None,
        warnings=warnings
    )


def log_audit_result(result: AuditResult):
    """Registra el resultado de la auditoría."""
    logging.info("=" * 50)
    logging.info("FINAL AUDIT RESULT")
    logging.info("=" * 50)
    logging.info(f"Safe: {result.is_safe}")
    logging.info(f"Leaks found: {len(result.leaks_found)}")
    logging.info(f"Auto-fixed: {result.leaks_auto_fixed}")
    logging.info(f"Remaining: {result.remaining_leaks}")
    
    for warning in result.warnings:
        logging.warning(f"AUDIT WARNING: {warning}")
    
    if result.leaks_found:
        for leak in result.leaks_found[:5]:
            logging.info(f"  - {leak['type']}: '{leak['value']}' at {leak['start']}")
    
    logging.info("=" * 50)
