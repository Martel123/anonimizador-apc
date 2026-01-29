"""
Final Auditor - Auditoría final obligatoria para garantizar 0 fugas de PII
===========================================================================
Escanea el documento final y:
- Detecta cualquier PII residual (email, DNI, teléfono)
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
    re.compile(r'(\+51\s*9\d{8})\b'),
    re.compile(r'\b(9\d{8})\b'),
    re.compile(r'\b(9\d{2}\s*\d{3}\s*\d{3})\b'),
]

TOKEN_PATTERN = re.compile(r'\{\{[A-Z_]+_\d+\}\}')

MONEY_INDICATORS = [
    'S/', 'S/.', 'US$', '$', 'PEN', 'USD', 'soles', 'dólares', 'dolares',
    '%', 'porcentaje', 'por ciento', 'puntos', 'cuotas', 'meses', 'días',
    'años', 'metros', 'kilos', 'gramos', 'horas', 'minutos'
]

LEGAL_NUMBER_CONTEXTS = [
    'artículo', 'articulo', 'art.', 'inciso', 'numeral', 'literal',
    'ley', 'decreto', 'expediente', 'exp.', 'casilla', 'resolución',
    'folio', 'página', 'cuaderno', 'tomo', 'legajo'
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


def find_dni_leaks(text: str) -> List[Dict[str, Any]]:
    """Encuentra posibles DNIs que quedaron sin anonimizar."""
    leaks = []
    
    for match in DNI_PATTERN.finditer(text):
        value = match.group(1)
        start, end = match.start(1), match.end(1)
        
        if TOKEN_PATTERN.search(text[max(0, start-20):end+20]):
            continue
        
        if is_money_context(text, start, end):
            continue
        
        if is_legal_number_context(text, start, end):
            continue
        
        if is_date_context(text, start, end):
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
    all_leaks = []
    warnings = []
    
    dni_leaks = find_dni_leaks(text)
    all_leaks.extend(dni_leaks)
    if dni_leaks:
        logging.warning(f"AUDIT: Found {len(dni_leaks)} potential DNI leaks")
    
    email_leaks = find_email_leaks(text)
    all_leaks.extend(email_leaks)
    if email_leaks:
        logging.warning(f"AUDIT: Found {len(email_leaks)} potential email leaks")
    
    phone_leaks = find_phone_leaks(text)
    all_leaks.extend(phone_leaks)
    if phone_leaks:
        logging.warning(f"AUDIT: Found {len(phone_leaks)} potential phone leaks")
    
    fixed_text = text
    leaks_auto_fixed = 0
    replacements = []
    
    if auto_fix and all_leaks:
        fixed_text, leaks_auto_fixed, replacements = auto_fix_leaks(text, all_leaks, existing_counters)
        
        # Re-auditar el texto corregido para verificar que no quedan fugas
        remaining = []
        remaining.extend(find_dni_leaks(fixed_text))
        remaining.extend(find_email_leaks(fixed_text))
        remaining.extend(find_phone_leaks(fixed_text))
        
        if remaining:
            warnings.append(f"CRITICAL: {len(remaining)} leaks could not be auto-fixed")
            leaks_auto_fixed = leaks_auto_fixed - len(remaining)  # Ajustar conteo
    
    # Calcular fugas restantes basándose en la re-auditoría
    remaining_leaks = 0
    if auto_fix and all_leaks:
        remaining_leaks = len(remaining) if 'remaining' in dir() else 0
    else:
        remaining_leaks = len(all_leaks)
    
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
