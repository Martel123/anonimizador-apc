"""
Legal Filters - Anti-sobreanonimización para documentos legales peruanos
=========================================================================
Implementa whitelist, heurísticas y filtros para evitar tokenizar texto jurídico.
"""

import re
from typing import Set, List, Tuple, Optional
from dataclasses import dataclass

LEGAL_WHITELIST_EXACT = {
    "SEÑOR JUEZ",
    "SEÑORA JUEZA", 
    "AL JUZGADO",
    "EXPEDIENTE",
    "CUADERNO PRINCIPAL",
    "CUADERNO CAUTELAR",
    "ESCRITO N",
    "SUMILLA",
    "PETITORIO",
    "FUNDAMENTOS DE HECHO",
    "FUNDAMENTOS DE DERECHO",
    "MEDIOS PROBATORIOS",
    "ANEXOS",
    "OTROSI DIGO",
    "OTROSÍ DIGO",
    "POR LO EXPUESTO",
    "A UD PIDO",
    "A USTED PIDO",
    "PRIMERO",
    "SEGUNDO",
    "TERCERO",
    "CUARTO",
    "QUINTO",
    "SEXTO",
    "SEPTIMO",
    "SÉPTIMO",
    "OCTAVO",
    "NOVENO",
    "DECIMO",
    "DÉCIMO",
    "ANTECEDENTES",
    "PRETENSION",
    "PRETENSIÓN",
    "DEMANDA",
    "CONTESTACION",
    "CONTESTACIÓN",
    "RECONVENCION",
    "RECONVENCIÓN",
    "AMPARO MI DEMANDA",
    "INTERPONGO DEMANDA",
    "FORMULO DEMANDA",
    "DEDUZCO",
    "SOLICITO",
    "DECLARO",
    "MANIFIESTO",
    "EXPONGO",
    "ACREDITO",
    "ADJUNTO",
    "OFREZCO",
    "EN CONSECUENCIA",
    "SIN EMBARGO",
    "POR TANTO",
    "POR ELLO",
    "ASIMISMO",
    "DEL MISMO MODO",
    "EN ESTE SENTIDO",
    "EN TAL SENTIDO",
    "CABE SEÑALAR",
    "ES PRECISO",
    "RESULTA NECESARIO",
    "ES DE VERSE",
    "CONFORME A",
    "DE CONFORMIDAD CON",
    "EN VIRTUD DE",
    "A LA FECHA",
    "AL RESPECTO",
    "POR LO QUE",
    "SIENDO ASI",
    "SIENDO ASÍ",
    "ESTANDO A",
    "EN MERITO A",
    "EN MÉRITO A",
    "POR LOS CONSIDERANDOS",
    "SE RESUELVE",
    "SENTENCIA",
    "AUTO",
    "RESOLUCION",
    "RESOLUCIÓN",
    "DECRETO",
    "SE DISPONE",
    "NOTIFIQUESE",
    "NOTIFÍQUESE",
    "CUMPLASE",
    "CÚMPLASE",
    "HAGASE SABER",
    "HÁGASE SABER",
    "DAR CUENTA",
    "TRASLADO",
    "VISTA",
    "AUDIENCIA",
    "INFORME ORAL",
    "VOTO",
    "VISTO",
    "CONSIDERANDO",
    "FUNDAMENTO JURIDICO",
    "FUNDAMENTO JURÍDICO",
    "BASE LEGAL",
    "MARCO LEGAL",
    "MARCO NORMATIVO",
    "PODER JUDICIAL",
    "CORTE SUPREMA",
    "CORTE SUPERIOR",
    "TRIBUNAL CONSTITUCIONAL",
    "MINISTERIO PUBLICO",
    "MINISTERIO PÚBLICO",
    "FISCALIA",
    "FISCALÍA",
    "DEFENSORIA DEL PUEBLO",
    "DEFENSORÍA DEL PUEBLO",
    "INDECOPI",
    "SUNARP",
    "SUNAT",
    "RENIEC",
    "ESSALUD",
    "ONPE",
    "JNE",
    "SBS",
    "BCR",
    "MEF",
    "MINJUS",
    "PCM",
    "EL DEMANDANTE",
    "LA DEMANDANTE",
    "EL DEMANDADO",
    "LA DEMANDADA",
    "LOS DEMANDANTES",
    "LAS DEMANDANTES",
    "LOS DEMANDADOS",
    "LAS DEMANDADAS",
    "EL RECURRENTE",
    "LA RECURRENTE",
    "EL SOLICITANTE",
    "LA SOLICITANTE",
    "EL ACCIONANTE",
    "LA ACCIONANTE",
    "EL REQUIRENTE",
    "LA REQUIRENTE",
    "PARTE ACTORA",
    "PARTE DEMANDADA",
    "CODIGO CIVIL",
    "CÓDIGO CIVIL",
    "CODIGO PROCESAL CIVIL",
    "CÓDIGO PROCESAL CIVIL",
    "CODIGO PENAL",
    "CÓDIGO PENAL",
    "CODIGO PROCESAL PENAL",
    "CÓDIGO PROCESAL PENAL",
    "CONSTITUCION POLITICA",
    "CONSTITUCIÓN POLÍTICA",
    "LEY ORGANICA",
    "LEY ORGÁNICA",
    "DECRETO SUPREMO",
    "DECRETO LEGISLATIVO",
    "DECRETO DE URGENCIA",
    "LEY GENERAL",
    "TEXTO UNICO ORDENADO",
    "TEXTO ÚNICO ORDENADO",
    "TUO",
    "REGLAMENTO",
    "DIRECTIVA",
    "JURISPRUDENCIA",
    "PRECEDENTE VINCULANTE",
    "ACUERDO PLENARIO",
    "CASACION",
    "CASACIÓN",
    "APELACION",
    "APELACIÓN",
    "QUEJA",
    "RECURSO DE",
    "NULIDAD",
    "IMPUGNACION",
    "IMPUGNACIÓN",
}

LEGAL_WHITELIST_PATTERNS = [
    re.compile(r'^ARTICULO\s+\d+', re.IGNORECASE),
    re.compile(r'^ARTÍCULO\s+\d+', re.IGNORECASE),
    re.compile(r'^ART\.?\s*\d+', re.IGNORECASE),
    re.compile(r'^NUMERAL\s+\d+', re.IGNORECASE),
    re.compile(r'^INCISO\s+\d+', re.IGNORECASE),
    re.compile(r'^LITERAL\s+[A-Z]', re.IGNORECASE),
    re.compile(r'^LEY\s+N[°oº]?\s*\d+', re.IGNORECASE),
    re.compile(r'^D\.?S\.?\s*N[°oº]?\s*\d+', re.IGNORECASE),
    re.compile(r'^D\.?L\.?\s*N[°oº]?\s*\d+', re.IGNORECASE),
    re.compile(r'^\d+[°ºo]?\s*JUZGADO', re.IGNORECASE),
    re.compile(r'^SALA\s+\w+', re.IGNORECASE),
    re.compile(r'^FOJAS?\s+\d+', re.IGNORECASE),
    re.compile(r'^FOLIOS?\s+\d+', re.IGNORECASE),
]

LEGAL_VERBS = {
    'interpongo', 'amparo', 'solicito', 'declaro', 'digo', 'expongo',
    'manifiesto', 'acredito', 'adjunto', 'ofrezco', 'presento', 'formulo',
    'deduzco', 'planteo', 'propongo', 'invoco', 'fundamento', 'sustento',
    'alego', 'argumento', 'contradigo', 'impugno', 'apelo', 'recurro',
    'subsano', 'aclaro', 'preciso', 'ratifico', 'confirmo', 'desisto',
    'renuncio', 'acepto', 'niego', 'reconozco', 'admito', 'rechazo',
    'requiero', 'exijo', 'pido', 'ruego', 'suplico',
}

LEGAL_CONNECTORS = {
    'en consecuencia', 'sin embargo', 'no obstante', 'por tanto', 'por ello',
    'por lo que', 'asimismo', 'además', 'igualmente', 'de igual forma',
    'del mismo modo', 'en este sentido', 'en tal sentido', 'cabe señalar',
    'es preciso', 'resulta necesario', 'es de verse', 'siendo así',
    'estando a', 'conforme a', 'de conformidad con', 'en virtud de',
    'a la fecha', 'al respecto', 'por los fundamentos', 'por lo expuesto',
    'en mérito a', 'en atención a', 'a fin de', 'con el objeto de',
    'toda vez que', 'dado que', 'puesto que', 'ya que', 'debido a',
    'por cuanto', 'en tanto', 'mientras que', 'aun cuando', 'si bien',
    'a pesar de', 'pese a', 'salvo que', 'excepto que', 'siempre que',
    'en caso de', 'de lo contrario', 'en su defecto', 'subsidiariamente',
}

LEGAL_TITLES = {
    'FUNDAMENTOS DE HECHO', 'FUNDAMENTOS DE DERECHO', 'HECHOS',
    'MEDIOS PROBATORIOS', 'ANEXOS', 'OTROSI', 'OTROSÍ', 'PETITORIO',
    'PRETENSION', 'PRETENSIÓN', 'ANTECEDENTES', 'CONSIDERANDOS',
    'RESUELVE', 'FALLO', 'DECISION', 'DECISIÓN', 'PARTE RESOLUTIVA',
    'PARTE EXPOSITIVA', 'PARTE CONSIDERATIVA', 'SUMILLA', 'CUADERNO',
    'ESCRITO', 'DEMANDA', 'CONTESTACION', 'CONTESTACIÓN', 'RECONVENCION',
    'EXCEPCION', 'EXCEPCIÓN', 'TACHAS', 'OPOSICIONES', 'ALEGATOS',
    'CONCLUSIONES', 'INFORME ESCRITO', 'RECURSO', 'APELACION', 'APELACIÓN',
}

EXCLUDED_UPPERCASE_WORDS = {
    'SEÑOR', 'SEÑORA', 'JUEZ', 'JUEZA', 'FISCAL', 'DOCTOR', 'DOCTORA',
    'ABOGADO', 'ABOGADA', 'DEMANDA', 'DEMANDANTE', 'DEMANDADO', 'DEMANDADA',
    'CÓDIGO', 'CODIGO', 'CIVIL', 'PENAL', 'PROCESAL', 'CONSTITUCIONAL',
    'ARTÍCULO', 'ARTICULO', 'INCISO', 'NUMERAL', 'RESOLUCIÓN', 'RESOLUCION',
    'DECRETO', 'LEY', 'REGLAMENTO', 'EXPEDIENTE', 'JUZGADO', 'SALA', 'CORTE',
    'SUPREMA', 'SUPERIOR', 'PODER', 'JUDICIAL', 'REPÚBLICA', 'REPUBLICA',
    'PERÚ', 'PERU', 'ESTADO', 'CONSTITUCIÓN', 'CONSTITUCION', 'LIMA', 'CALLAO',
    'AUTO', 'SENTENCIA', 'APELACIÓN', 'APELACION', 'CASACIÓN', 'CASACION',
    'RECURSO', 'ESCRITO', 'RAZÓN', 'RAZON', 'SOCIAL', 'DENUNCIA',
    'MINISTERIO', 'PÚBLICO', 'PUBLICO', 'DEFENSORÍA', 'DEFENSORIA', 'PUEBLO',
    'QUE', 'DEL', 'LOS', 'LAS', 'POR', 'CON', 'SIN', 'PARA', 'DESDE', 'HASTA',
    'SOBRE', 'ANTE', 'CONTRA', 'ENTRE', 'MEDIANTE', 'SEGÚN', 'SEGUN',
    'SUMILLA', 'PETITORIO', 'FUNDAMENTOS', 'PRUEBAS', 'ANEXOS', 'HECHOS',
    'PRETENSION', 'PRETENSIÓN', 'MEDIOS', 'PROBATORIOS', 'CUADERNO',
    'PRINCIPAL', 'CAUTELAR', 'TUTELA', 'URGENTE', 'MEDIDA', 'EMBARGO',
    'SECUESTRO', 'INSCRIPCIÓN', 'INSCRIPCION', 'REGISTRAL', 'SUNARP',
    'RENIEC', 'SUNAT', 'INDECOPI', 'OSCE', 'ESSALUD', 'ONPE', 'JNE',
    'TITULO', 'TÍTULO', 'PRIMERO', 'SEGUNDO', 'TERCERO', 'CUARTO', 'QUINTO',
    'OTROSÍ', 'OTROSI', 'LEGISLATIVO', 'SUPREMO', 'URGENCIA',
    'ANTECEDENTES', 'CONSIDERANDO', 'RESUELVE', 'FALLO', 'DECLARA',
    'DISPONE', 'ORDENA', 'NOTIFÍQUESE', 'NOTIFIQUESE', 'CÚMPLASE', 'CUMPLASE',
    'INTERLOCUTORIA', 'DEFINITIVA', 'FIRME', 'CONSENTIDA', 'EJECUTORIADA',
    'NULIDAD', 'APERCIBIMIENTO', 'BAJO', 'MULTA', 'COSTAS', 'COSTOS',
    'AREQUIPA', 'TRUJILLO', 'CHICLAYO', 'PIURA', 'CUSCO', 'TACNA', 'ICA',
    'HUANCAYO', 'PUNO', 'IQUITOS', 'MOQUEGUA', 'TUMBES', 'ANCASH', 'JUNIN',
    'LAMBAYEQUE', 'LORETO', 'MADRE', 'DIOS', 'PASCO', 'SAN', 'MARTIN',
    'UCAYALI', 'AMAZONAS', 'APURIMAC', 'AYACUCHO', 'CAJAMARCA', 'HUANUCO',
    'HUANCAVELICA',
    'ALIMENTOS', 'DIVORCIO', 'TENENCIA', 'REGIMEN', 'RÉGIMEN', 'VISITAS',
    'FILIACION', 'FILIACIÓN', 'ADOPCION', 'ADOPCIÓN', 'SUCESION', 'SUCESIÓN',
    'TESTAMENTO', 'HERENCIA', 'ANTICIPO', 'LEGÍTIMA', 'LEGITIMA',
    'OBLIGACION', 'OBLIGACIÓN', 'CONTRATO', 'INCUMPLIMIENTO', 'RESOLUCION',
    'RESCISION', 'RESCISIÓN', 'NULIDAD', 'ANULABILIDAD', 'SIMULACION',
    'INDEMNIZACION', 'INDEMNIZACIÓN', 'DAÑOS', 'DANOS', 'PERJUICIOS',
    'LUCRO', 'CESANTE', 'MORAL', 'EMERGENTE',
    'MENOR', 'MENORES', 'HIJOS', 'HIJAS', 'PADRE', 'MADRE', 'CONYUGE',
    'ESPOSO', 'ESPOSA', 'CONVIVIENTE', 'HEREDERO', 'HEREDEROS',
}


def normalize_for_comparison(text: str) -> str:
    """Normaliza texto para comparación (upper, sin acentos extra)."""
    return ' '.join(text.upper().split())


def is_in_exact_whitelist(text: str) -> bool:
    """Verifica si el texto está en la whitelist exacta."""
    normalized = normalize_for_comparison(text)
    return normalized in LEGAL_WHITELIST_EXACT


def matches_whitelist_pattern(text: str) -> bool:
    """Verifica si el texto coincide con un patrón de whitelist."""
    normalized = text.strip()
    for pattern in LEGAL_WHITELIST_PATTERNS:
        if pattern.match(normalized):
            return True
    return False


def contains_legal_verb(text: str) -> bool:
    """Verifica si el texto contiene un verbo legal común."""
    text_lower = text.lower()
    words = text_lower.split()
    for word in words:
        clean_word = re.sub(r'[^\w]', '', word)
        if clean_word in LEGAL_VERBS:
            return True
    return False


def is_legal_connector(text: str) -> bool:
    """Verifica si el texto es un conector legal."""
    normalized = ' '.join(text.lower().split())
    return normalized in LEGAL_CONNECTORS


def is_legal_title(text: str) -> bool:
    """Verifica si el texto es un título legal."""
    normalized = normalize_for_comparison(text)
    for title in LEGAL_TITLES:
        if normalized == title or normalized.startswith(title + ' '):
            return True
    return False


def is_all_excluded_words(text: str) -> bool:
    """Verifica si todas las palabras del texto están en la lista de exclusión."""
    words = text.upper().split()
    if len(words) == 0:
        return True
    excluded_count = sum(1 for w in words if w in EXCLUDED_UPPERCASE_WORDS)
    return excluded_count == len(words)


def looks_like_proper_name(text: str) -> bool:
    """
    Heurística para determinar si el texto parece un nombre propio.
    Los nombres propios típicamente:
    - Tienen 2-4 palabras
    - Cada palabra empieza con mayúscula
    - No contienen verbos ni conectores
    - No son frases jurídicas conocidas
    """
    words = text.split()
    
    if len(words) < 2 or len(words) > 5:
        return False
    
    if len(text) > 60:
        return False
    
    if is_in_exact_whitelist(text):
        return False
    
    if matches_whitelist_pattern(text):
        return False
    
    if contains_legal_verb(text):
        return False
    
    if is_legal_connector(text):
        return False
    
    if is_legal_title(text):
        return False
    
    if is_all_excluded_words(text):
        return False
    
    proper_name_pattern = re.compile(r'^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]*$')
    uppercase_word_pattern = re.compile(r'^[A-ZÁÉÍÓÚÑ]+$')
    
    valid_words = 0
    for word in words:
        if proper_name_pattern.match(word) or uppercase_word_pattern.match(word):
            if word.upper() not in EXCLUDED_UPPERCASE_WORDS:
                valid_words += 1
    
    return valid_words >= 2


def should_anonymize_span(text: str, entity_type: str) -> Tuple[bool, str]:
    """
    Determina si un span debe ser anonimizado.
    Retorna (should_anonymize, reason).
    """
    if entity_type in ('DNI', 'RUC', 'EMAIL', 'TELEFONO', 'CUENTA', 'CCI', 'EXPEDIENTE'):
        return True, "structured_pii"
    
    if not text or len(text.strip()) < 2:
        return False, "too_short"
    
    if entity_type in ('PERSONA', 'PER'):
        if is_in_exact_whitelist(text):
            return False, "whitelist_exact"
        
        if matches_whitelist_pattern(text):
            return False, "whitelist_pattern"
        
        if contains_legal_verb(text):
            return False, "contains_legal_verb"
        
        if is_legal_connector(text):
            return False, "legal_connector"
        
        if is_legal_title(text):
            return False, "legal_title"
        
        if is_all_excluded_words(text):
            return False, "all_excluded_words"
        
        words = text.split()
        if len(words) > 5:
            if not looks_like_proper_name(text):
                return False, "too_long_not_name"
        
        if looks_like_proper_name(text):
            return True, "proper_name"
        
        has_uppercase = bool(re.search(r'[A-ZÁÉÍÓÚÑ]', text))
        has_name_chars = bool(re.match(r'^[A-Za-záéíóúñÁÉÍÓÚÑ\s]+$', text))
        if has_uppercase and has_name_chars and len(words) >= 2 and len(words) <= 4:
            non_excluded = [w for w in words if w.upper() not in EXCLUDED_UPPERCASE_WORDS]
            if len(non_excluded) >= 2:
                return True, "possible_name"
        
        return False, "default_reject_persona"
    
    if entity_type == 'DIRECCION':
        address_indicators = ['av', 'avenida', 'jr', 'jiron', 'jirón', 'calle', 
                             'mz', 'manzana', 'lt', 'lote', 'urb', 'urbanización',
                             'dpto', 'departamento', 'piso', 'int', 'interior',
                             'km', 'kilómetro', 'bloque', 'block']
        text_lower = text.lower()
        has_indicator = any(ind in text_lower for ind in address_indicators)
        has_number = bool(re.search(r'\d', text))
        
        if has_indicator or has_number:
            return True, "address_pattern"
        return False, "no_address_pattern"
    
    if entity_type in ('JUZGADO', 'CASILLA', 'ACTA', 'ENTIDAD'):
        return True, "legal_entity"
    
    if entity_type in ('FIRMA', 'SELLO', 'HUELLA'):
        return True, "signature_mark"
    
    if entity_type == 'PLACA':
        return True, "vehicle_plate"
    
    return True, "default_accept"


@dataclass
class FilterResult:
    """Resultado del filtrado de una entidad."""
    entity_type: str
    value: str
    accepted: bool
    reason: str
    original_confidence: float
    adjusted_confidence: float


def filter_entities(entities: List[Tuple[str, str, int, int, float]]) -> Tuple[List[Tuple[str, str, int, int, float]], List[FilterResult]]:
    """
    Filtra entidades aplicando reglas anti-sobreanonimización.
    Retorna (entities_filtered, filter_results).
    """
    filtered = []
    results = []
    
    for entity in entities:
        entity_type, value, start, end, confidence = entity
        
        should_keep, reason = should_anonymize_span(value, entity_type)
        
        adjusted_confidence = confidence
        if reason == "possible_name":
            adjusted_confidence = min(confidence, 0.7)
        elif reason == "proper_name":
            adjusted_confidence = max(confidence, 0.9)
        
        result = FilterResult(
            entity_type=entity_type,
            value=value,
            accepted=should_keep,
            reason=reason,
            original_confidence=confidence,
            adjusted_confidence=adjusted_confidence
        )
        results.append(result)
        
        if should_keep:
            filtered.append((entity_type, value, start, end, adjusted_confidence))
    
    return filtered, results


def generate_filter_report(results: List[FilterResult]) -> dict:
    """Genera un reporte del filtrado."""
    report = {
        'total_entities': len(results),
        'accepted': sum(1 for r in results if r.accepted),
        'rejected': sum(1 for r in results if not r.accepted),
        'by_reason': {},
        'rejected_details': []
    }
    
    for result in results:
        reason = result.reason
        if reason not in report['by_reason']:
            report['by_reason'][reason] = {'count': 0, 'accepted': 0, 'rejected': 0}
        report['by_reason'][reason]['count'] += 1
        if result.accepted:
            report['by_reason'][reason]['accepted'] += 1
        else:
            report['by_reason'][reason]['rejected'] += 1
            report['rejected_details'].append({
                'value': result.value[:50],
                'type': result.entity_type,
                'reason': reason
            })
    
    return report
