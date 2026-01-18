"""
Detector por Capas para Documentos Legales Peruanos
====================================================
Implementa detección de PII en 4 capas con máximo recall:
- CAPA 1: Regex determinístico
- CAPA 2: Heurística legal Perú (contexto)
- CAPA 3: Personas (spaCy + fallback)
- CAPA 4: Merge, deduplicación, resolución de solapamientos
"""

import re
import logging
from typing import Dict, List, Tuple, Set, Any, Optional
from collections import defaultdict
from dataclasses import dataclass, field

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

@dataclass
class Entity:
    """Representa una entidad detectada."""
    type: str
    value: str
    start: int
    end: int
    source: str  # 'regex', 'context', 'spacy', 'heuristic'
    confidence: float = 1.0

# ============================================================================
# CAPA 1: REGEX DETERMINÍSTICO
# ============================================================================

# Anti-falsos positivos para DNI (contextos donde 8 dígitos NO son DNI)
MONEY_CONTEXT_PATTERN = re.compile(
    r'(?:S/\.?\s*|US\$\s*|\$\s*|PEN\s+|USD\s+|soles|dólares|dolares|nuevos soles|%|porcentaje|por\s*ciento)'
    r'\s*[0-9]{1,3}(?:[,\'][0-9]{3})*(?:\.[0-9]{2})?',
    re.IGNORECASE
)

# Patrón para detectar contextos monetarios cerca de números
NEAR_MONEY_CONTEXT = re.compile(
    r'(?:S/\.?|US\$|\$|PEN|USD|soles|dólares|dolares|%|porcentaje)\s*$',
    re.IGNORECASE
)

# DNI: 8 dígitos con validación
DNI_PATTERN = re.compile(r'\b([0-9]{8})\b')

# RUC: 11 dígitos (empieza con 10, 15, 17 o 20)
RUC_PATTERN = re.compile(r'\b((?:10|15|17|20)[0-9]{9})\b')

# Email
EMAIL_PATTERN = re.compile(
    r'\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b',
    re.IGNORECASE
)

# Teléfono (múltiples formatos peruanos)
PHONE_PATTERNS = [
    re.compile(r'(\+51\s*[-\s]?\s*9\d{2}\s*[-\s]?\s*\d{3}\s*[-\s]?\s*\d{3})\b'),
    re.compile(r'(\+51\s*9\d{8})\b'),
    re.compile(r'\b(9\d{2}\s*[-\s]?\s*\d{3}\s*[-\s]?\s*\d{3})\b'),
    re.compile(r'\b(9\d{8})\b'),
    re.compile(r'(\(0?1\)\s*\d{3}\s*[-\s]?\s*\d{4})\b'),
    re.compile(r'\b(01\s*[-\s]?\s*\d{3}\s*[-\s]?\s*\d{4})\b'),
    re.compile(r'\b(0\d{2}\s*[-\s]?\s*\d{6,7})\b'),
    re.compile(r'(\(\d{2,3}\)\s*\d{6,7})\b'),
]

# Expediente judicial peruano
EXPEDIENTE_PATTERNS = [
    re.compile(r'\b(\d{5}-\d{4}-\d+-\d{4}-[A-Z]{2}-[A-Z]{2}-\d{2})\b', re.IGNORECASE),
    re.compile(r'\b(\d{5,6}-\d{4}(?:-\d+)?)\b'),
    re.compile(r'(?:expediente|exp\.?)\s*(?:n[°oº]?\s*)?[:\s]*(\d{2,6}[-/]?\d{0,4}[-/]?\d{0,4})', re.IGNORECASE),
]

# Casilla electrónica
CASILLA_PATTERN = re.compile(
    r'casilla\s+(?:electr[oó]nica\s+)?(?:n[°oº]?\s*)?(\d+)',
    re.IGNORECASE
)

# Acta de conciliación/audiencia
ACTA_PATTERNS = [
    re.compile(r'acta\s+(?:de\s+)?(?:conciliaci[oó]n|audiencia|inspecci[oó]n|constataci[oó]n)?\s*(?:n[°oº]?\s*)?(\d+[-/]?\d*)', re.IGNORECASE),
    re.compile(r'\b(acta\s+n[°oº]?\s*\d+[-/]?\d*)\b', re.IGNORECASE),
]

# Juzgado
JUZGADO_PATTERN = re.compile(
    r'\b(\d*[°ºo]?\s*juzgado\s+(?:de\s+)?(?:paz\s+letrado|familia|civil|penal|laboral|mixto|comercial|constitucional)'
    r'(?:\s+(?:de|del)\s+[A-Za-záéíóúñÁÉÍÓÚÑ\s]+){0,4})',
    re.IGNORECASE
)

# Cuenta bancaria/CCI
CUENTA_PATTERNS = [
    re.compile(r'(?:cuenta|cta\.?)\s*(?:de\s+ahorros?|corriente)?\s*(?:n[°oº]?\s*)?(\d{10,20})', re.IGNORECASE),
    re.compile(r'(?:CCI|cci)\s*[:.]?\s*(\d{20})'),
    re.compile(r'\b(\d{3}[-\s]?\d{3}[-\s]?\d{10,14})\b'),
]

# Placa vehicular
PLACA_PATTERN = re.compile(r'\b([A-Z]{3}[-\s]?\d{3})\b', re.IGNORECASE)


def is_in_money_context(text: str, start: int, end: int) -> bool:
    """Verifica si un número está en contexto monetario."""
    before_start = max(0, start - 30)
    before_text = text[before_start:start]
    
    after_end = min(len(text), end + 30)
    after_text = text[end:after_end]
    
    money_indicators = ['S/', 'US$', '$', 'PEN', 'USD', 'soles', 'dólares', 'dolares', '%', 
                        'porcentaje', 'por ciento', 'puntos', 'cuotas', 'meses', 'días',
                        'años', 'horas', 'minutos', 'metros', 'kilos', 'gramos']
    
    for indicator in money_indicators:
        if indicator.lower() in before_text.lower() or indicator.lower() in after_text.lower():
            return True
    
    return False


def detect_layer1_regex(text: str) -> List[Entity]:
    """
    CAPA 1: Detección con regex determinístico.
    Incluye anti-falsos positivos para DNI.
    """
    entities = []
    
    # DNI (8 dígitos) con validación anti-falsos positivos
    for match in DNI_PATTERN.finditer(text):
        value = match.group(1)
        start, end = match.start(1), match.end(1)
        
        # Verificar que no sea contexto monetario
        if not is_in_money_context(text, start, end):
            # Verificar que no sea parte de RUC
            extended_start = max(0, start - 3)
            extended_end = min(len(text), end + 3)
            extended_text = text[extended_start:extended_end]
            if not re.search(r'\d{11}', extended_text):
                entities.append(Entity(
                    type='DNI',
                    value=value,
                    start=start,
                    end=end,
                    source='regex'
                ))
    
    # RUC (11 dígitos)
    for match in RUC_PATTERN.finditer(text):
        entities.append(Entity(
            type='RUC',
            value=match.group(1),
            start=match.start(1),
            end=match.end(1),
            source='regex'
        ))
    
    # Email
    for match in EMAIL_PATTERN.finditer(text):
        entities.append(Entity(
            type='EMAIL',
            value=match.group(1),
            start=match.start(1),
            end=match.end(1),
            source='regex'
        ))
    
    # Teléfono
    for pattern in PHONE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1) if match.lastindex else match.group(0)
            # Normalizar: quitar espacios internos para detectar duplicados
            normalized = re.sub(r'\s+', '', value)
            if len(normalized) >= 9:  # Mínimo 9 dígitos
                entities.append(Entity(
                    type='TELEFONO',
                    value=value,
                    start=match.start(),
                    end=match.end(),
                    source='regex'
                ))
    
    # Expediente
    for pattern in EXPEDIENTE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1) if match.lastindex else match.group(0)
            entities.append(Entity(
                type='EXPEDIENTE',
                value=value,
                start=match.start(),
                end=match.end(),
                source='regex'
            ))
    
    # Casilla
    for match in CASILLA_PATTERN.finditer(text):
        full_match = match.group(0)
        entities.append(Entity(
            type='CASILLA',
            value=full_match,
            start=match.start(),
            end=match.end(),
            source='regex'
        ))
    
    # Acta
    for pattern in ACTA_PATTERNS:
        for match in pattern.finditer(text):
            full_match = match.group(0)
            entities.append(Entity(
                type='ACTA',
                value=full_match,
                start=match.start(),
                end=match.end(),
                source='regex'
            ))
    
    # Juzgado
    for match in JUZGADO_PATTERN.finditer(text):
        entities.append(Entity(
            type='JUZGADO',
            value=match.group(1),
            start=match.start(1),
            end=match.end(1),
            source='regex'
        ))
    
    # Cuenta bancaria
    for pattern in CUENTA_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1) if match.lastindex else match.group(0)
            entities.append(Entity(
                type='CUENTA',
                value=value,
                start=match.start(),
                end=match.end(),
                source='regex'
            ))
    
    # Placa
    for match in PLACA_PATTERN.finditer(text):
        entities.append(Entity(
            type='PLACA',
            value=match.group(1),
            start=match.start(1),
            end=match.end(1),
            source='regex'
        ))
    
    return entities


# ============================================================================
# CAPA 2: HEURÍSTICA LEGAL PERÚ (CONTEXTO)
# ============================================================================

# Patrones de domicilio
DOMICILIO_PATTERNS = [
    re.compile(r'domicilio\s+(?:real|procesal|legal|fiscal|actual|particular)\s*[:\s]+([^;.\n]{10,150})', re.IGNORECASE),
    re.compile(r'(?:con\s+)?domicilio\s+(?:en|sito\s+en|ubicado\s+en)\s*[:\s]*([^;.\n]{10,150})', re.IGNORECASE),
    re.compile(r'(?:reside|vive|habita)\s+en\s*[:\s]*([^;.\n]{10,150})', re.IGNORECASE),
    re.compile(r'direcci[oó]n\s*[:\s]+([^;.\n]{10,150})', re.IGNORECASE),
    re.compile(r'ubicad[oa]\s+en\s*[:\s]*([^;.\n]{10,150})', re.IGNORECASE),
]

# Indicadores de dirección
ADDRESS_INDICATORS = [
    r'\bAv(?:enida)?\.?\s+',
    r'\bJr(?:\.|\.|irón)?\s+',
    r'\bCalle\s+',
    r'\bPsje(?:\.|Pasaje)?\s+',
    r'\bMz(?:\.|anzana)?\s+',
    r'\bLt(?:\.|ote)?\s+',
    r'\bUrb(?:\.|anizaci[oó]n)?\s+',
    r'\bAA\.?HH\.?\s+',
    r'\bP\.?J\.?\s+',
    r'\bN[°oº]?\s*\d+',
    r'\bBloque\s+',
    r'\bPiso\s+',
    r'\bInt(?:erior)?\.?\s*',
    r'\bDpto\.?\s*',
]

ADDRESS_STANDALONE_PATTERN = re.compile(
    r'(' + '|'.join(ADDRESS_INDICATORS) + r')[A-Za-záéíóúñÁÉÍÓÚÑ0-9\s,.\-°º#]+(?=[\.\n,;]|$)',
    re.IGNORECASE
)

# Patrón para "identificado con DNI" -> captura nombre + DNI
IDENTIFICADO_PATTERN = re.compile(
    r'([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñÁÉÍÓÚÑ\s]+?)\s*,?\s*'
    r'identificad[oa]\s+con\s+(?:DNI|D\.?N\.?I\.?|documento)\s*(?:n[°oº]?\s*)?[:\s]*'
    r'(\d{8})',
    re.IGNORECASE
)

# Patrón para contexto de teléfono
TELEFONO_CONTEXT_PATTERN = re.compile(
    r'(?:teléfono|telefono|celular|cel\.?|móvil|movil|whatsapp|fono|contacto)\s*[:\s]?\s*'
    r'(\+?\d[\d\s\-\(\)]{7,18})',
    re.IGNORECASE
)

# Patrón para contexto de email
EMAIL_CONTEXT_PATTERN = re.compile(
    r'(?:correo\s+electr[oó]nico|e-?mail|correo)\s*[:\s]?\s*'
    r'([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})',
    re.IGNORECASE
)


def detect_layer2_context(text: str) -> List[Entity]:
    """
    CAPA 2: Detección por contexto legal peruano.
    Detecta entidades basándose en palabras clave de contexto.
    """
    entities = []
    
    # Domicilio (real/procesal/legal)
    for pattern in DOMICILIO_PATTERNS:
        for match in pattern.finditer(text):
            address = match.group(1).strip()
            # Limpiar la dirección
            address = re.sub(r'\s+', ' ', address)
            if len(address) > 10:
                entities.append(Entity(
                    type='DIRECCION',
                    value=address,
                    start=match.start(1),
                    end=match.end(1),
                    source='context'
                ))
    
    # Direcciones standalone con indicadores
    for match in ADDRESS_STANDALONE_PATTERN.finditer(text):
        address = match.group(0).strip()
        if len(address) > 8:
            entities.append(Entity(
                type='DIRECCION',
                value=address,
                start=match.start(),
                end=match.end(),
                source='context'
            ))
    
    # "identificado con DNI" -> extraer nombre y DNI
    for match in IDENTIFICADO_PATTERN.finditer(text):
        nombre = match.group(1).strip()
        dni = match.group(2)
        
        # Validar que el nombre no sea una palabra legal común
        if not is_excluded_word(nombre):
            entities.append(Entity(
                type='PERSONA',
                value=nombre,
                start=match.start(1),
                end=match.end(1),
                source='context'
            ))
        
        entities.append(Entity(
            type='DNI',
            value=dni,
            start=match.start(2),
            end=match.end(2),
            source='context'
        ))
    
    # Teléfono con contexto
    for match in TELEFONO_CONTEXT_PATTERN.finditer(text):
        phone = match.group(1).strip()
        normalized = re.sub(r'[\s\-\(\)]', '', phone)
        if len(normalized) >= 9:
            entities.append(Entity(
                type='TELEFONO',
                value=phone,
                start=match.start(1),
                end=match.end(1),
                source='context'
            ))
    
    # Email con contexto
    for match in EMAIL_CONTEXT_PATTERN.finditer(text):
        email = match.group(1)
        entities.append(Entity(
            type='EMAIL',
            value=email,
            start=match.start(1),
            end=match.end(1),
            source='context'
        ))
    
    return entities


# ============================================================================
# CAPA 3: PERSONAS (spaCy + fallback heurístico)
# ============================================================================

EXCLUDED_WORDS = {
    'SEÑOR', 'SEÑORA', 'JUEZ', 'JUEZA', 'DEMANDA', 'DEMANDANTE', 'DEMANDADO', 'DEMANDADA',
    'FISCAL', 'CÓDIGO', 'CIVIL', 'PENAL', 'PROCESAL', 'CONSTITUCIONAL',
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
    'OTROSÍ', 'OTROSI', 'DECRETO', 'LEGISLATIVO', 'SUPREMO', 'URGENCIA',
    'DATOS', 'DOMICILIO', 'GENERALES', 'PERSONALES', 'PETITORIO', 'DEMANDA',
    'INVOCANDO', 'INTERÉS', 'INTERES', 'LEGITIMIDAD', 'OBRAR',
}

# Disparadores de contexto para personas
PERSON_TRIGGERS = {
    'demandante', 'demandado', 'demandada', 'codemandado', 'codemandada',
    'señor', 'señora', 'sr.', 'sra.', 'don', 'doña',
    'abogado', 'abogada', 'letrado', 'letrada',
    'menor', 'menores', 'hijo', 'hija', 'madre', 'padre',
    'identificado', 'identificada', 'suscrito', 'suscrita',
    'interpone', 'interpongo', 'contra', 'recurrente',
    'testigo', 'perito', 'perita', 'declarante',
    'cónyuge', 'esposo', 'esposa', 'conviviente',
    'representante', 'apoderado', 'apoderada',
    'solicitante', 'invitado', 'invitada',
    'acreedor', 'acreedora', 'deudor', 'deudora',
    'denunciante', 'denunciado', 'denunciada',
    'imputado', 'imputada', 'procesado', 'procesada',
    'agraviado', 'agraviada', 'víctima', 'victima',
}


def is_excluded_word(word: str) -> bool:
    """Verifica si una palabra está en la lista de exclusión."""
    normalized = word.upper().strip()
    words = normalized.split()
    
    # Si es una sola palabra, verificar directamente
    if len(words) == 1:
        return words[0] in EXCLUDED_WORDS
    
    # Si son múltiples palabras, verificar si todas son excluidas
    excluded_count = sum(1 for w in words if w in EXCLUDED_WORDS)
    return excluded_count == len(words)


def has_trigger_nearby(text: str, start: int, window: int = 100) -> bool:
    """Verifica si hay un disparador de contexto cerca del texto."""
    before_start = max(0, start - window)
    context = text[before_start:start].lower()
    
    for trigger in PERSON_TRIGGERS:
        if trigger in context:
            return True
    return False


NLP_MODEL = None
NLP_FAILED = False

def get_nlp():
    """Carga lazy del modelo spaCy con fallback."""
    global NLP_MODEL, NLP_FAILED
    
    if NLP_FAILED:
        return None
    
    if NLP_MODEL is None:
        try:
            import spacy
            try:
                NLP_MODEL = spacy.load("es_core_news_md")
            except:
                NLP_MODEL = spacy.load("es_core_news_sm")
            logging.info("spaCy model loaded successfully")
        except Exception as e:
            logging.warning(f"spaCy not available: {e}")
            NLP_FAILED = True
            return None
    
    return NLP_MODEL


def detect_layer3_spacy(text: str) -> List[Entity]:
    """
    Detección de personas con spaCy.
    """
    entities = []
    nlp = get_nlp()
    
    if nlp is None:
        return entities
    
    try:
        # Procesar texto en chunks para documentos largos
        max_length = 100000
        chunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]
        offset = 0
        
        for chunk in chunks:
            doc = nlp(chunk)
            
            for ent in doc.ents:
                if ent.label_ in ('PER', 'PERSON'):
                    value = ent.text.strip()
                    if not is_excluded_word(value) and len(value) > 2:
                        entities.append(Entity(
                            type='PERSONA',
                            value=value,
                            start=offset + ent.start_char,
                            end=offset + ent.end_char,
                            source='spacy'
                        ))
                elif ent.label_ in ('ORG',):
                    value = ent.text.strip()
                    if not is_excluded_word(value) and len(value) > 3:
                        entities.append(Entity(
                            type='ENTIDAD',
                            value=value,
                            start=offset + ent.start_char,
                            end=offset + ent.end_char,
                            source='spacy'
                        ))
                elif ent.label_ in ('LOC', 'GPE'):
                    value = ent.text.strip()
                    if not is_excluded_word(value) and len(value) > 3:
                        # Solo si parece dirección
                        if any(ind.lower() in value.lower() for ind in ['av', 'jr', 'calle', 'urb', 'mz', 'lt']):
                            entities.append(Entity(
                                type='DIRECCION',
                                value=value,
                                start=offset + ent.start_char,
                                end=offset + ent.end_char,
                                source='spacy'
                            ))
            
            offset += len(chunk)
    
    except Exception as e:
        logging.warning(f"spaCy processing failed: {e}")
    
    return entities


def detect_layer3_heuristic(text: str) -> List[Entity]:
    """
    Fallback heurístico para detección de personas.
    Detecta nombres en MAYÚSCULAS o Title Case con contexto.
    """
    entities = []
    
    # Patrón para nombres en MAYÚSCULAS (2-4 palabras)
    uppercase_pattern = re.compile(r'\b([A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,4})\b')
    
    for match in uppercase_pattern.finditer(text):
        value = match.group(1)
        start = match.start(1)
        
        # Verificar que no sea palabra excluida
        if is_excluded_word(value):
            continue
        
        # Verificar contexto o al menos 3 palabras
        words = value.split()
        if len(words) >= 3 or has_trigger_nearby(text, start):
            entities.append(Entity(
                type='PERSONA',
                value=value,
                start=start,
                end=match.end(1),
                source='heuristic',
                confidence=0.8
            ))
    
    # Patrón para nombres en Title Case con contexto
    titlecase_pattern = re.compile(
        r'(?:señor|señora|sr\.?|sra\.?|don|doña|abogad[oa]|el\s+demandante|la\s+demandada?|el\s+demandado)\s+'
        r'([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,4})',
        re.IGNORECASE
    )
    
    for match in titlecase_pattern.finditer(text):
        value = match.group(1)
        if not is_excluded_word(value):
            entities.append(Entity(
                type='PERSONA',
                value=value,
                start=match.start(1),
                end=match.end(1),
                source='heuristic'
            ))
    
    return entities


def detect_layer3_personas(text: str) -> List[Entity]:
    """
    CAPA 3: Detección de personas con spaCy + fallback heurístico.
    """
    entities = []
    
    # Intentar spaCy primero
    spacy_entities = detect_layer3_spacy(text)
    entities.extend(spacy_entities)
    
    # Siempre agregar heurístico para mayor recall
    heuristic_entities = detect_layer3_heuristic(text)
    entities.extend(heuristic_entities)
    
    return entities


# ============================================================================
# CAPA 4: MERGE, DEDUPLICACIÓN, RESOLUCIÓN DE SOLAPAMIENTOS
# ============================================================================

def merge_entities(all_entities: List[Entity]) -> List[Entity]:
    """
    CAPA 4: Merge de entidades de todas las capas.
    - Elimina duplicados
    - Resuelve solapamientos (prioriza spans más largos)
    - Ordena por posición
    """
    if not all_entities:
        return []
    
    # Eliminar duplicados exactos
    seen = set()
    unique_entities = []
    for e in all_entities:
        key = (e.type, e.start, e.end)
        if key not in seen:
            seen.add(key)
            unique_entities.append(e)
    
    # Ordenar por posición de inicio, luego por longitud (mayor primero)
    unique_entities.sort(key=lambda e: (e.start, -(e.end - e.start)))
    
    # Resolver solapamientos
    merged = []
    for entity in unique_entities:
        # Verificar si se solapa con alguna entidad ya aceptada
        overlaps = False
        for existing in merged:
            if entity.start < existing.end and entity.end > existing.start:
                # Hay solapamiento
                overlaps = True
                # Si la nueva es más larga, reemplazar
                if (entity.end - entity.start) > (existing.end - existing.start):
                    merged.remove(existing)
                    merged.append(entity)
                break
        
        if not overlaps:
            merged.append(entity)
    
    # Ordenar por posición final
    merged.sort(key=lambda e: e.start)
    
    return merged


# ============================================================================
# PIPELINE PRINCIPAL
# ============================================================================

def detect_all_pii(text: str) -> Tuple[List[Entity], Dict[str, Any]]:
    """
    Pipeline completo de detección de PII.
    Ejecuta las 4 capas en orden y devuelve entidades mergeadas.
    
    Returns:
        Tuple de (lista de entidades, metadata del proceso)
    """
    metadata = {
        'layer1_count': 0,
        'layer2_count': 0,
        'layer3_count': 0,
        'total_before_merge': 0,
        'total_after_merge': 0,
        'spacy_used': False,
        'fallback_used': False,
    }
    
    all_entities = []
    
    # CAPA 1: Regex determinístico
    try:
        layer1 = detect_layer1_regex(text)
        metadata['layer1_count'] = len(layer1)
        all_entities.extend(layer1)
    except Exception as e:
        logging.warning(f"Layer 1 failed: {e}")
    
    # CAPA 2: Heurística legal
    try:
        layer2 = detect_layer2_context(text)
        metadata['layer2_count'] = len(layer2)
        all_entities.extend(layer2)
    except Exception as e:
        logging.warning(f"Layer 2 failed: {e}")
    
    # CAPA 3: Personas
    try:
        layer3 = detect_layer3_personas(text)
        metadata['layer3_count'] = len(layer3)
        all_entities.extend(layer3)
        
        # Determinar si se usó spaCy o fallback
        spacy_used = any(e.source == 'spacy' for e in layer3)
        heuristic_used = any(e.source == 'heuristic' for e in layer3)
        metadata['spacy_used'] = spacy_used
        metadata['fallback_used'] = heuristic_used and not spacy_used
    except Exception as e:
        logging.warning(f"Layer 3 failed: {e}")
    
    metadata['total_before_merge'] = len(all_entities)
    
    # CAPA 4: Merge
    merged = merge_entities(all_entities)
    metadata['total_after_merge'] = len(merged)
    
    return merged, metadata


def post_scan_final(text: str) -> Tuple[bool, List[Dict]]:
    """
    POST-SCAN OBLIGATORIO: Verifica que el texto final no contenga PII residual.
    
    Returns:
        Tuple de (needs_review, lista de tipos detectados con conteos)
    """
    entities, _ = detect_all_pii(text)
    
    if not entities:
        return False, []
    
    # Agrupar por tipo
    type_counts = defaultdict(int)
    for e in entities:
        type_counts[e.type] += 1
    
    detected = [{'type': t, 'count': c} for t, c in type_counts.items()]
    
    return True, detected
