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
# ===============================
# COLEGIOS DE ABOGADOS (ENTIDAD)
# ===============================

COLEGIOS_ABOGADOS_ABREV = {
    "CAL":  "Colegio de Abogados de Lima",
    "CALN": "Colegio de Abogados de Lima Norte",
    "CAC":  "Colegio de Abogados del Callao",
    "CAA":  "Colegio de Abogados de Arequipa",
    "ICAP": "Ilustre Colegio de Abogados de Piura",
    "ICAL": "Ilustre Colegio de Abogados de La Libertad",
    "ICAC": "Ilustre Colegio de Abogados del Cusco",
    "ICAA": "Ilustre Colegio de Abogados de Ayacucho",
    "CAH":  "Colegio de Abogados de Huánuco",
    "ICAI": "Ilustre Colegio de Abogados de Ica",
    "CAJ":  "Colegio de Abogados de Junín",
    "ICAT": "Ilustre Colegio de Abogados de Tacna",
}

def normalize_abbrev(s: str) -> str:
    return s.replace(".", "").replace(" ", "").upper()

# Detecta: "CAL: Juan Perez..." => ENTIDAD + ENTIDAD (según tu regla)
PAT_COLEGIO_ENTIDAD = re.compile(
    r"\b([A-Z](?:\.?[A-Z]){1,5}\.?)\s*[:\-]?\s*"
    r"([A-ZÁÉÍÓÚÑ&“”\"().,\-]+(?:\s+[A-ZÁÉÍÓÚÑ&“”\"().,\-]+){1,5})\b"
)

def detectar_colegio_entidad(text: str):
    resultados = []
    for m in PAT_COLEGIO_ENTIDAD.finditer(text):
        abrev = normalize_abbrev(m.group(1))
        if abrev in COLEGIOS_ABOGADOS_ABREV:
            s1, e1 = m.span(1)  # abreviatura
            s2, e2 = m.span(2)  # “nombre” siguiente
            resultados.append((s1, e1, "ENTIDAD", 0.65))
            resultados.append((s2, e2, "ENTIDAD", 0.65))
    return resultados


# =====================================================
# ENTIDADES PÚBLICAS / NOTARIALES -> ENTIDAD + ENTIDAD
# =====================================================

TRIGGERS_ENTIDAD = [
    # Justicia
    r"JUZGADO", r"SALA", r"CORTE\s+SUPERIOR", r"PODER\s+JUDICIAL",
    r"FISCAL[IÍ]A", r"MINISTERIO\s+P[ÚU]BLICO",
    r"DEFENSOR[IÍ]A\s+DEL\s+PUEBLO",

    # Registro / Identidad
    r"SUNARP", r"REGISTROS\s+P[ÚU]BLICOS", r"REGISTRO\s+PERSONAL", r"REGISTRO\s+VEHICULAR",
    r"RENIEC", r"RNP", r"REGISTRO\s+NACIONAL",

    # Gobierno / Municipal
    r"MUNICIPALIDAD", r"GOBIERNO\s+REGIONAL", r"MINISTERIO", r"SUPERINTENDENCIA",
    r"SBS", r"OSIPTEL", r"OSINERGMIN", r"SUNAT", r"INDECOPI", r"MIGRACIONES",

    # Notarial / Conciliación
    r"NOTAR[IÍ]A", r"NOTARIO", r"COLEGIO\s+DE\s+NOTARIOS",
    r"CENTRO\s+DE\s+CONCILIACI[ÓO]N", r"CONCILIADOR",

    # Policía / Militar
    r"PNP", r"POLIC[IÍ]A\s+NACIONAL", r"COMISAR[IÍ]A",
]

ENTIDAD_SIGUIENTE = r"([A-ZÁÉÍÓÚÑ0-9&“”\"().,\-]+(?:\s+[A-ZÁÉÍÓÚÑ0-9&“”\"().,\-]+){1,8})"

PAT_TRIGGER_SEGUIDO = re.compile(
    rf"\b({'|'.join(TRIGGERS_ENTIDAD)})\s+{ENTIDAD_SIGUIENTE}\b",
    flags=re.IGNORECASE
)

PAT_TRIGGER_DOS_PUNTOS = re.compile(
    rf"\b({'|'.join(TRIGGERS_ENTIDAD)})\s*[:\-]\s*{ENTIDAD_SIGUIENTE}\b",
    flags=re.IGNORECASE
)

# Triggers que corresponden a tipos de órgano judicial.
# Para estos se emite UNA entidad combinada (trigger + nombre) → sin texto residual.
# Para otros institucionales (MUNICIPALIDAD, MINISTERIO…) se mantiene el par.
_COURT_TRIGGERS = frozenset({
    'juzgado', 'sala', 'tribunal', 'corte superior', 'corte suprema',
    'poder judicial', 'fiscalía', 'fiscalia',
    'ministerio público', 'ministerio publico', 'defensoría del pueblo',
    'defensoria del pueblo',
})


def detectar_entidad_publica_entidad(text: str):
    """Detecta entidades institucionales.
    Para órganos judiciales/fiscales: emite UNA entidad con el span completo
    (trigger + nombre), evitando texto residual entre ambos tokens.
    Para el resto: emite dos entidades separadas (trigger, nombre).
    """
    resultados = []

    def _is_court_trigger(trigger_text: str) -> bool:
        return trigger_text.strip().lower() in _COURT_TRIGGERS

    for m in PAT_TRIGGER_SEGUIDO.finditer(text):
        s1, e1 = m.span(1)
        s2, e2 = m.span(2)
        trigger = text[s1:e1]
        if _is_court_trigger(trigger):
            # Span unificado: desde inicio del trigger hasta fin del nombre
            resultados.append((s1, e2, "ENTIDAD", 0.65))
        else:
            resultados.append((s1, e1, "ENTIDAD", 0.65))
            resultados.append((s2, e2, "ENTIDAD", 0.65))

    for m in PAT_TRIGGER_DOS_PUNTOS.finditer(text):
        s1, e1 = m.span(1)
        s2, e2 = m.span(2)
        trigger = text[s1:e1]
        if _is_court_trigger(trigger):
            resultados.append((s1, e2, "ENTIDAD", 0.65))
        else:
            resultados.append((s1, e1, "ENTIDAD", 0.65))
            resultados.append((s2, e2, "ENTIDAD", 0.65))

    return sorted(set(resultados), key=lambda x: (x[0], x[1], x[2]))



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

# DNI con trigger explícito: siempre captura independiente del contexto monetario
DNI_EXPLICIT_PATTERN = re.compile(
    r'(?:D\.?N\.?I\.?|documento\s+(?:nacional\s+)?de\s+identidad'
    r'|identificad[oa]\s+con|con\s+D\.?N\.?I\.?)\s*(?:n[°oº]?\s*)?[:\-\s]*'
    r'\b([0-9]{8})\b',
    re.IGNORECASE
)
DNI_EXPLICIT_PATTERN_2 = re.compile(
    r'(?:D\s*\.?\s*N\s*\.?\s*I\s*\.?\s*[:N°ºo-]*\s*)(\d{8})',
    re.IGNORECASE
)

# RUC: 11 dígitos (empieza con 10, 15, 17 o 20)
RUC_PATTERN = re.compile(r'\b((?:10|15|17|20)[0-9]{9})\b')

# Email
# Email: lookbehind basado en chars válidos de email al INICIO +  \b al FINAL.
# Lookbehind: impide que una captura parcial ocurra cuando el local-part
# del email está precedido por un '.' (no-\w), evitando que 'name@domain.com'
# sea capturado en lugar del full 'user.name@domain.com'.
# \b al final: maneja correctamente el punto de cierre de frase (ej. '...gob.pe.')
EMAIL_PATTERN = re.compile(
    r'(?<![A-Za-z0-9._%+\-])([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b',
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
    # Formato largo completo (00001-2024-1-1801-JR-CI-01)
    re.compile(r'\b(EXP[-_][A-Z0-9][A-Z0-9\-]{5,30})\b', re.IGNORECASE),
    re.compile(
        r'(?:expediente\s+judicial|expediente|exp\.?)\s*(?:n[°oº]?\s*)?[-:\s]*'
        r'((?:EXP[-_])?[A-Z0-9][A-Z0-9\-]{5,30})',
        re.IGNORECASE
    ),
    re.compile(r'\b(\d{5}-\d{4}-\d+-\d{4}-[A-Z]{2}-[A-Z]{2}-\d{2})\b', re.IGNORECASE),
    # Formato EXP-NNNN... con guion (EXP-987654321, EXP-01234-2024)
    re.compile(r'\bEXP[-_]([A-Z0-9][\dA-Z\-]{3,18})\b', re.IGNORECASE),
    # Formato corto con guion (00001-2024, 00001-2024-01)
    re.compile(r'\b(\d{5,6}-\d{4}(?:-\d+)?)\b'),
    # "expediente / exp." seguido de número (permite separador : - espacio o guion)
    re.compile(
        r'(?:expediente|exp\.?)\s*[-:\s]*(?:n[°oº]?\s*)?[-:\s]*'
        r'(\d{2,8}(?:[-/]\d{2,8}){0,3})',
        re.IGNORECASE
    ),
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

# Juzgado - pattern limitado a una línea, sin capturar nombres de personas
# JUZGADO_PATTERN - captura nombre completo del juzgado incluyendo:
# - ordinal textual o numérico previo (Primer, Segundo, 1er, 2do…)
# - tipo de especialidad (civil, familia, laboral, comercial, etc.)
# - conjunción "Y" para especializaciones compuestas
# - 0–7 palabras de ubicación adicionales (San Borja, Los Olivos, Lima Norte…)
JUZGADO_PATTERN = re.compile(
    r'\b('
    # Ordinal textual opcional: Primer, Segunda, 3er, 4to, etc.
    r'(?:primer[oa]?\s+|segundo[a]?\s+|tercer[oa]?\s+|cuart[oa]?\s+|quint[oa]?\s+|sext[oa]?\s+|'
    r'\d{1,2}\s*[°ºo]?\s+)?'
    # Palabra clave JUZGADO
    r'juzgado\s+'
    # "de " opcional
    r'(?:de\s+)?'
    # Tipo de especialidad (primera parte)
    r'(?:paz\s+letrado|familia|civil|penal|laboral|mixto|comercial|constitucional|'
    r'especializado|unipersonal|permanente|supraprovincial|corporativo|transitorio)'
    # Extensión con "Y" para especializaciones compuestas (violencia, comercial, etc.)
    r'(?:\s+y\s+[A-Za-záéíóúñÁÉÍÓÚÑ]+(?:\s+[A-Za-záéíóúñÁÉÍÓÚÑ]+){0,4})?'
    # Sufijo de ubicación: 0–7 palabras en Title Case (San Borja, Los Olivos, Lima Norte…)
    r'(?:\s+[A-ZÁÉÍÓÚÑA-Za-záéíóúñ][A-Za-záéíóúñÁÉÍÓÚÑ]*){0,7}'
    r')',
    re.IGNORECASE
)

# Cuenta bancaria/CCI
CUENTA_PATTERNS = [
    # "cuenta / cuentas" bancaria(s) con número (cubre plural y contexto "bajo el número")
    re.compile(
        r'(?:cuentas?\s*(?:bancarias?|de\s+ahorros?|corrientes?)?\s*'
        r'(?:registrad[ao]s?\s+)?(?:bajo\s+(?:el|la)\s+)?n[°uú]mero\s+|'
        r'cuentas?\s*(?:de\s+ahorros?|corriente)?\s*(?:n[°oº]?\s*)?|'
        r'cta\.?\s*(?:n[°oº]?\s*)?)'
        r'(\d{10,20})',
        re.IGNORECASE
    ),
    re.compile(r'(?:CCI|cci)\s*[:.]?\s*(\d{20})'),
    re.compile(r'\b(\d{3}[-\s]?\d{3}[-\s]?\d{10,14})\b'),
]

# Historia Clínica (HC-123456 o "historia clínica N° 123456")
HISTORIA_CLINICA_PATTERN = re.compile(
    r'\b(HC[-_]\d{5,10}|historia\s+cl[ií]nica\s*(?:n[°oº]?|nro\.?)?\s*[:\-]?\s*\d{5,10})\b',
    re.IGNORECASE
)

# Código de cliente (CLI-12345 o "código de cliente: 12345")
CODIGO_CLIENTE_PATTERN = re.compile(
    r'\b(CLI[-_]\d{4,10}|c[oó]digo\s+de\s+cliente\s*(?:n[°oº]?|nro\.?)?\s*[:\-]?\s*\d{4,10})\b',
    re.IGNORECASE
)

# Licencia de conducir (LIC-A12345 o "licencia de conducir: A12345")
LICENCIA_PATTERN = re.compile(
    r'\b(LIC[-_][A-Z0-9]{5,12}|licencia(?:\s+de\s+conducir)?\s*(?:n[°oº]?|nro\.?)?\s*[:\-]?\s*[A-Z0-9]{5,12})\b',
    re.IGNORECASE
)

# Póliza de seguro (POL-12345 o "póliza N° 12345")
POLIZA_PATTERN = re.compile(
    r'\b(POL[-_]\d{5,12}|p[oó]liza\s*(?:n[°oº]?|nro\.?)?\s*[:\-]?\s*\d{5,12})\b',
    re.IGNORECASE
)

# Colegiatura profesional (abogados, médicos, ingenieros, etc.)
# Requiere trigger explícito para evitar falsos positivos.
COLEGIATURA_PATTERN = re.compile(
    r'(?:C\.?A\.?L\.?|C\.?A\.?C\.?|C\.?A\.?A\.?|CMP|CIP|CAP|CPA|CPP|CNP'
    r'|[Cc]olegiatura|[Cc]olegio\s+de\s+[Aa]bogados)'
    r'(?:\s+(?:N[°oº]?|n[°oº]?|n[uú]mero|numero|no\.?|es|de|:))?\s*'
    r'(?:N[°oº]?\s*)?[:\-]?\s*(\d{3,6})',
    re.IGNORECASE
)

# Sala jurisdiccional (Primera Sala Civil, Segunda Sala Penal, etc.)
SALA_PATTERN = re.compile(
    r'\b('
    r'(?:primer[oa]?\s+|segundo[a]?\s+|tercer[oa]?\s+|cuart[oa]?\s+|quint[oa]?\s+|sext[oa]?\s+|'
    r'\d{1,2}\s*[°ºo]?\s+)?'
    r'sala\s+'
    r'(?:de\s+)?'
    r'(?:civil|penal|laboral|familia|mixta?|comercial|constitucional|suprema?|superior|'
    r'apelaciones?|audiencia|litigios|conflictos)'
    r'(?:\s+[A-Za-záéíóúñÁÉÍÓÚÑ]+){0,5}'
    r')',
    re.IGNORECASE
)

# Tribunal (Tribunal Constitucional, Tribunal Arbitral, etc.)
TRIBUNAL_PATTERN = re.compile(
    r'\b('
    r'tribunal\s+'
    r'(?:constitucional|supremo?|arbitral|de\s+justicia|de\s+honor|militar|administrativo|'
    r'fiscal|de\s+apelaci[oó]n|de\s+garant[ií]as?|de\s+contrataciones?)'
    r'(?:\s+[A-Za-záéíóúñÁÉÍÓÚÑ]+){0,4}'
    r')',
    re.IGNORECASE
)

# Partida electrónica/registral (SUNARP)
PARTIDA_PATTERN = re.compile(
    r'(?:partida\s+(?:electr[oó]nica|registral|sunarp)?'
    r'|asiento\s+registral'
    r'|tomo\s+registral'
    r'|(?:SUNARP|sunarp)\s*[:,]?)'
    r'\s*(?:n[°oº]?\s*)?[:\s]*(\d{4,12}(?:[-/]\d{2,6})?)',
    re.IGNORECASE
)

# Resolución/Auto/Decreto con número (requiere "n°" para evitar "auto" falso)
RESOLUCION_PATTERN = re.compile(
    r'(?:resoluci[oó]n|auto|decreto|acuerdo|oficio|informe)\s+'
    r'(?:[a-záéíóúñ]+\s+)*'          # palabras intermedias opcionales (directoral, gerencial…)
    r'n[°oº][.:/ ]*'                   # obligatorio "n°" para reducir falsos positivos
    r'(\d{2,6}(?:[-/]\d{2,6})?)',
    re.IGNORECASE
)


PLACA_VEHICLE_CONTEXTS = [
    'placa', 'vehículo', 'vehiculo', 'auto', 'camioneta', 'moto',
    'motocicleta', 'bus', 'camión', 'camion', 'automóvil', 'automovil',
    'unidad', 'circulación', 'circulacion',
]
PLACA_NEGATIVE_CONTEXTS = [
    'expediente', 'interno', 'caso', 'registro', 'código', 'codigo',
    'cliente', 'poliza', 'póliza', 'licencia', 'historia clínica',
    'historia clinica', 'hc-', 'cli-', 'lic-', 'pol-',
    'documento', 'archivo', 'referencia', 'número de caso', 'numero de caso'
]
# Placa vehicular
PLACA_PATTERN = re.compile(r'\b([A-Z]{3}[-\s]?\d{3})\b', re.IGNORECASE)

# Firma, Sello, Huella - heurísticas de texto
FIRMA_PATTERNS = [
    re.compile(r'(firma\s*:?\s*[^\n]{0,50})', re.IGNORECASE),
    re.compile(r'(_____+)', re.IGNORECASE),  # Líneas de firma
    re.compile(r'(FIRMADO\s*(?:DIGITAL(?:MENTE)?)?)', re.IGNORECASE),
    re.compile(r'(/s/\s*[^\n]{0,30})', re.IGNORECASE),
    re.compile(r'(firmante\s*:?\s*[^\n]{0,50})', re.IGNORECASE),
    re.compile(r'(suscribe\s*:?\s*[^\n]{0,50})', re.IGNORECASE),
]

SELLO_PATTERNS = [
    re.compile(r'(sello\s*:?\s*[^\n]{0,50})', re.IGNORECASE),
    re.compile(r'(\[sello\])', re.IGNORECASE),
    re.compile(r'(sellado\s+por\s*:?\s*[^\n]{0,50})', re.IGNORECASE),
]

HUELLA_PATTERNS = [
    re.compile(r'(huella\s*(?:digital|dactilar)?\s*:?\s*[^\n]{0,30})', re.IGNORECASE),
    re.compile(r'(\[huella\])', re.IGNORECASE),
    re.compile(r'(impresión\s+dactilar\s*:?\s*[^\n]{0,30})', re.IGNORECASE),
]


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
    
    # Rastrear posiciones ya ocupadas por DNI explícito para no duplicar
    _dni_explicit_positions: set = set()

    # DNI con trigger explícito: prioridad máxima, bypass de money_context
    for match in DNI_EXPLICIT_PATTERN.finditer(text):
        value = match.group(1)
        start, end = match.start(1), match.end(1)
        extended_start = max(0, start - 3)
        extended_end = min(len(text), end + 3)
        if not re.search(r'\d{11}', text[extended_start:extended_end]):
            entities.append(Entity(
                type='DNI', value=value, start=start, end=end,
                source='regex', confidence=1.0
            ))
            _dni_explicit_positions.add(start)
            # DNI explícito con formato flexible D . N . I .
            for match in DNI_EXPLICIT_PATTERN_2.finditer(text):
                value = match.group(1)
                start, end = match.start(1), match.end(1)

                if (start, end) not in _dni_explicit_positions:
                    _dni_explicit_positions.add((start, end))
                    entities.append(Entity(
                        type='DNI',
                        value=value,
                        start=start,
                        end=end,
                        source='regex',
                        confidence=1.0
                    ))
    # DNI (8 dígitos) con validación anti-falsos positivos
    for match in DNI_PATTERN.finditer(text):
        value = match.group(1)
        start, end = match.start(1), match.end(1)
        if start in _dni_explicit_positions:
            continue  # ya capturado por DNI_EXPLICIT
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
    
    # ── Expediente: PRIMERO para calcular spans y bloquear TELEFONO ─────────
    # Recopilar spans de EXPEDIENTE para filtrar TELEFONO solapado
    _expediente_spans: List[Tuple[int, int]] = []
    for pattern in EXPEDIENTE_PATTERNS:
        for match in pattern.finditer(text):
            _expediente_spans.append((match.start(), match.end()))
            value = match.group(1) if match.lastindex else match.group(0)
            entities.append(Entity(
                type='EXPEDIENTE',
                value=value,
                start=match.start(),
                end=match.end(),
                source='regex'
            ))

    def _in_expediente_span(m_start: int, m_end: int) -> bool:
        """True si el match solapa con algún span de EXPEDIENTE ya detectado."""
        return any(
            exp_s <= m_start and m_end <= exp_e + 5
            for exp_s, exp_e in _expediente_spans
        )

    # Teléfono (se filtra si cae dentro de un span de EXPEDIENTE
    # o si aparece en contexto textual de expediente)
    _EXPEDIENTE_CONTEXT_MARKERS = [
        "EXP-",
        "EXP_",
        "EXPEDIENTE",
        "EXP.",
        "JUDICIAL",
        "N° DE EXPEDIENTE",
        "NRO DE EXPEDIENTE",
        "EXPEDIENTE JUDICIAL",
    ]

    for pattern in PHONE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1) if match.lastindex else match.group(0)
            normalized = re.sub(r'\s+', '', value)

            if len(normalized) < 9:
                continue

            m_start, m_end = match.start(), match.end()

            # 1) Si el número ya cae dentro de un span de expediente detectado, descartar
            if _in_expediente_span(m_start, m_end):
                continue

            # 2) Filtro extra por contexto textual de expediente
            context_before = text[max(0, m_start - 40):m_start].upper()
            context_after = text[m_end:min(len(text), m_end + 20)].upper()
            context_window = context_before + " " + context_after

            if any(marker in context_window for marker in _EXPEDIENTE_CONTEXT_MARKERS):
                continue

            entities.append(Entity(
                type='TELEFONO',
                value=value,
                start=m_start,
                end=m_end,
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
    
    # Colegiatura profesional
    for match in COLEGIATURA_PATTERN.finditer(text):
        entities.append(Entity(
            type='COLEGIATURA',
            value=match.group(0),
            start=match.start(),
            end=match.end(),
            source='regex'
        ))

    # Sala jurisdiccional
    for match in SALA_PATTERN.finditer(text):
        entities.append(Entity(
            type='SALA',
            value=match.group(1),
            start=match.start(1),
            end=match.end(1),
            source='regex'
        ))

    # Tribunal
    for match in TRIBUNAL_PATTERN.finditer(text):
        entities.append(Entity(
            type='TRIBUNAL',
            value=match.group(1),
            start=match.start(1),
            end=match.end(1),
            source='regex'
        ))

    # Partida electrónica/registral
    for match in PARTIDA_PATTERN.finditer(text):
        entities.append(Entity(
            type='PARTIDA',
            value=match.group(0),
            start=match.start(),
            end=match.end(),
            source='regex'
        ))

    # Resolución/Auto/Decreto con número
    for match in RESOLUCION_PATTERN.finditer(text):
        entities.append(Entity(
            type='RESOLUCION',
            value=match.group(0),
            start=match.start(),
            end=match.end(),
            source='regex'
        ))

    # Historia Clínica
    for match in HISTORIA_CLINICA_PATTERN.finditer(text):
        full_value = match.group(0).strip()
        entities.append(Entity(
            type='HISTORIA_CLINICA',
            value=full_value,
            start=match.start(),
            end=match.end(),
            source='regex'
        ))

        # Código de cliente
        for match in CODIGO_CLIENTE_PATTERN.finditer(text):
            full_value = match.group(0).strip()
            if full_value:
                entities.append(Entity(
                    type='CODIGO_CLIENTE',
                    value=full_value,
                    start=match.start(),
                    end=match.end(),
                    source='regex'
                ))

    # Licencia de conducir
    for match in LICENCIA_PATTERN.finditer(text):
        full_value = match.group(0).strip()
        if full_value and len(full_value) >= 5:
            entities.append(Entity(
                type='LICENCIA',
                value=full_value,
                start=match.start(),
                end=match.end(),
                source='regex'
            ))

    # Póliza de seguro
    for match in POLIZA_PATTERN.finditer(text):
        full_value = match.group(0).strip()
        entities.append(Entity(
            type='POLIZA',
            value=full_value,
            start=match.start(),
            end=match.end(),
            source='regex'
        ))

    # Placa vehicular - solo si hay contexto vehicular claro
    for match in PLACA_PATTERN.finditer(text):
        start, end = match.start(1), match.end(1)
        ctx_window = text[max(0, start - 120):min(len(text), end + 120)].lower()
        
        has_vehicle_ctx = any(c in ctx_window for c in PLACA_VEHICLE_CONTEXTS)
        has_negative_ctx = any(c in ctx_window for c in PLACA_NEGATIVE_CONTEXTS)

        # Si parece identificador interno/documental y no hay contexto vehicular, descartar
        if has_negative_ctx and not has_vehicle_ctx:
            continue

        # Si no hay contexto vehicular real, no lo agregues
        if not has_vehicle_ctx:
            continue

        entities.append(Entity(
            type='PLACA',
            value=match.group(1),
            start=start,
            end=end,
            source='regex',
            confidence=1.0
        ))
    
    # Firma
    for pattern in FIRMA_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if len(value) >= 5:  # Mínimo longitud
                entities.append(Entity(
                    type='FIRMA',
                    value=value,
                    start=match.start(1),
                    end=match.end(1),
                    source='regex',
                    confidence=0.7
                ))
    
    # Sello
    for pattern in SELLO_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if len(value) >= 4:
                entities.append(Entity(
                    type='SELLO',
                    value=value,
                    start=match.start(1),
                    end=match.end(1),
                    source='regex',
                    confidence=0.7
                ))
    
    # Huella
    for pattern in HUELLA_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if len(value) >= 5:
                entities.append(Entity(
                    type='HUELLA',
                    value=value,
                    start=match.start(1),
                    end=match.end(1),
                    source='regex',
                    confidence=0.7
                ))
    
    return entities


# ============================================================================
# CAPA 2: HEURÍSTICA LEGAL PERÚ (CONTEXTO ESTRUCTURAL)
# ============================================================================

# Secciones obligatorias donde SIEMPRE hay PII
PII_SECTIONS = [
    'DATOS DEL DEMANDANTE',
    'DATOS DE LA DEMANDANTE',
    'DATOS DEL DEMANDADO',
    'DATOS DE LA DEMANDADA',
    'DATOS DE LOS SOLICITANTES',
    'DATOS DE LAS SOLICITANTES',
    'DATOS DEL SOLICITANTE',
    'DATOS DE LA SOLICITANTE',
    'DATOS DEL INVITADO',
    'DATOS DE LA INVITADA',
    'DATOS PERSONALES',
    'DATOS GENERALES',
    'I. DATOS DEL DEMANDANTE',
    'II. DATOS DEL DEMANDADO',
]

TRIGGER_WORDS_PERSONA = [
    'doña', 'don', 'señor', 'señora', 'sr.', 'sra.',
    'identificado', 'identificada', 'identificados', 'identificadas',
    'el demandante', 'la demandante', 'el demandado', 'la demandada',
    'el solicitante', 'la solicitante', 'el invitado', 'la invitada',
    'menor de edad', 'los menores', 'las menores',
    'el padre', 'la madre', 'el cónyuge', 'la cónyuge',
    'el abogado', 'la abogada', 'el letrado', 'la letrada',
]


def detect_pii_in_sections(text: str) -> List['Entity']:
    """
    Detección forzada de PII en secciones obligatorias.
    Cuando encontramos una sección como "DATOS DEL DEMANDANTE",
    extraemos agresivamente nombres, DNI, direcciones.
    """
    entities = []
    
    for section in PII_SECTIONS:
        pattern = re.compile(
            re.escape(section) + r'[:\s]*(.{50,500}?)(?=\n\n|\n[A-Z]{2,}|\nI{1,3}\.|\n\d+[.)-])',
            re.IGNORECASE | re.DOTALL
        )
        
        for match in pattern.finditer(text):
            section_text = match.group(1)
            section_start = match.start(1)
            
            from legal_filters import looks_like_proper_name as _looks_like_name
            name_in_section = re.compile(
                r'([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){1,3})',
                re.UNICODE
            )
            for name_match in name_in_section.finditer(section_text):
                value = name_match.group(1)
                if (not is_excluded_word(value)
                        and len(value.split()) >= 2
                        and _looks_like_name(value)):
                    entities.append(Entity(
                        type='PERSONA',
                        value=value,
                        start=section_start + name_match.start(1),
                        end=section_start + name_match.end(1),
                        source='section',
                        confidence=0.65
                    ))
            
            dni_in_section = re.compile(r'\b(\d{8})\b')
            for dni_match in dni_in_section.finditer(section_text):
                entities.append(Entity(
                    type='DNI',
                    value=dni_match.group(1),
                    start=section_start + dni_match.start(1),
                    end=section_start + dni_match.end(1),
                    source='section',
                    confidence=1.0
                ))
    
    return entities


# Patrones de domicilio
# DOMICILIO_PATTERNS: el grupo de captura usa [^;\n] (no [^;.\n]).
# Excluir '.' truncaba las direcciones en la primera abreviatura
# (Av., Jr., Nro., Dpto., etc.), dejando solo 2-3 chars → no match.
# Se usa una longitud máxima conservadora (120) y post-trim de
# \. seguido de espacio+mayúscula para no cruzar oraciones completas.
DOMICILIO_PATTERNS = [
    re.compile(r'domicilio\s+(?:real|procesal|legal|fiscal|actual|particular)\s*[:\s]+([^;\n]{5,120})', re.IGNORECASE),
    re.compile(r'(?:con\s+)?domicilio\s+(?:en|sito\s+en|ubicado\s+en)\s*[:\s]*([^;\n]{5,120})', re.IGNORECASE),
    re.compile(r'(?:reside|vive|habita)\s+en\s*[:\s]*([^;\n]{5,120})', re.IGNORECASE),
    re.compile(r'direcci[oó]n\s*[:\s]+([^;\n]{5,120})', re.IGNORECASE),
    re.compile(r'ubicad[oa]\s+en\s*[:\s]*([^;\n]{5,120})', re.IGNORECASE),
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
# El nombre requiere forma Title Case estricta (sin IGNORECASE en ese grupo)
# para evitar capturar cláusulas tipo "La demandada es Fulano".
IDENTIFICADO_PATTERN = re.compile(
    # Nombre: Title Case o ALL CAPS, 2-6 tokens, sin IGNORECASE
    r'('
    r'(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]{2,})'           # Token 1
    r'(?:\s+(?:de\s+(?:la\s+|los\s+|las\s+)?|del\s+)?'
    r'(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]{2,})){1,5}'     # Tokens 2-6
    r')'
    r'\s*,?\s*'
    r'(?i:identificad[oa]\s+con\s+(?:DNI|D\.?N\.?I\.?|documento)\s*(?:n[°oº]?\s*)?[:\s]*)'
    r'(\d{8})'
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
    # ENTIDADES PÚBLICAS / NOTARIALES -> ENTIDAD + ENTIDAD
    for s, e, label, conf in detectar_entidad_publica_entidad(text):
        entities.append(Entity(type=label, value=text[s:e], start=s, end=e, source="context_entidad_publica", confidence=conf))

    # COLEGIO DE ABOGADOS -> ENTIDAD + ENTIDAD
    for s, e, label, conf in detectar_colegio_entidad(text):
        entities.append(Entity(type=label, value=text[s:e], start=s, end=e, source="context_colegio", confidence=conf))


    # ── helpers para trim de dirección ─────────────────────────────────────────
    _SENTENCE_BREAK = re.compile(r'\.\s+[A-ZÁÉÍÓÚÑ]')

    def _trim_address(raw: str) -> str:
        """Recorta una captura de dirección al primer límite de oración
        ('. Mayúscula') para no cruzar frases completas.
        También descarta trailing comas/puntos/espacios."""
        raw = re.sub(r'\s+', ' ', raw).strip()
        m = _SENTENCE_BREAK.search(raw)
        if m:
            raw = raw[:m.start()].rstrip(' ,;')
        raw = raw.rstrip(' .,;')
        return raw

    # Domicilio (real/procesal/legal)
    for pattern in DOMICILIO_PATTERNS:
        for match in pattern.finditer(text):
            raw_addr = match.group(1)
            address = _trim_address(raw_addr)
            if len(address) >= 8:
                entities.append(Entity(
                    type='DIRECCION',
                    value=address,
                    start=match.start(1),
                    end=match.start(1) + len(raw_addr),
                    source='context',
                    confidence=0.75   # siempre goes to needs_review
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
    'AMPARO', 'INTERPONGO', 'FORMULO', 'DEDUZCO', 'SOLICITO', 'DECLARO',
    'MANIFIESTO', 'EXPONGO', 'ACREDITO', 'ADJUNTO', 'OFREZCO', 'PRESENTO',
    'ANTECEDENTES', 'CONSIDERANDO', 'RESUELVE', 'FALLO', 'DISPONE', 'ORDENA',
    'ALIMENTOS', 'DIVORCIO', 'TENENCIA', 'REGIMEN', 'RÉGIMEN', 'VISITAS',
    'FILIACION', 'FILIACIÓN', 'ADOPCION', 'ADOPCIÓN', 'SUCESION', 'SUCESIÓN',
    'OBLIGACION', 'OBLIGACIÓN', 'CONTRATO', 'INCUMPLIMIENTO',
    'INDEMNIZACION', 'INDEMNIZACIÓN', 'DAÑOS', 'PERJUICIOS', 'EMERGENTE',
    'LUCRO', 'CESANTE', 'MORAL', 'FECHA', 'VIGENTE', 'CONFORME', 'VIRTUD',
    'CONSECUENCIA', 'EMBARGO', 'TANTO', 'ELLO', 'ASIMISMO', 'ADEMÁS', 'ADEMAS',
    'SEXTO', 'SEPTIMO', 'SÉPTIMO', 'OCTAVO', 'NOVENO', 'DECIMO', 'DÉCIMO',
    'NOTIFIQUESE', 'NOTIFÍQUESE', 'CUMPLASE', 'CÚMPLASE', 'HAGASE', 'HÁGASE',
    'AREQUIPA', 'TRUJILLO', 'CHICLAYO', 'PIURA', 'CUSCO', 'TACNA', 'ICA',
    'HUANCAYO', 'PUNO', 'IQUITOS', 'MOQUEGUA', 'TUMBES', 'ANCASH', 'JUNIN',
    'LAMBAYEQUE', 'LORETO', 'PASCO', 'UCAYALI', 'AMAZONAS', 'AYACUCHO',
    'CAJAMARCA', 'HUANUCO', 'HUANCAVELICA', 'APURIMAC', 'MADRE', 'DIOS',
    'MARTIN', 'SAN', 'DOCTOR', 'DOCTORA', 'ABOGADO', 'ABOGADA',
    'MENOR', 'MENORES', 'HIJOS', 'HIJAS', 'PADRE', 'MADRE', 'CONYUGE',
    'ESPOSO', 'ESPOSA', 'CONVIVIENTE', 'HEREDERO', 'HEREDEROS', 'PETITORIO','SUMARIO', 'RESUMEN', 'OBJETO', 'OBJETO DE LA DEMANDA', 'OBJETO DEL PROCESO',
    'HECHOS QUE MOTIVAN', 'HECHOS MATERIA', 'FUNDAMENTOS FÁCTICOS', 'FUNDAMENTOS FACTICOS',
    'FUNDAMENTOS JURIDICOS', 'JURÍDICOS',
    'BASE LEGAL', 'MARCO NORMATIVO', 'SUSTENTO', 'SUSTENTO LEGAL',
    'PUNTOS', 'CONTROVERTIDOS', 'PUNTOS CONTROVERTIDOS',
    'MEDIOS', 'PRUEBA', 'PRUEBAS', 'PROBATORIA', 'PROBATORIOS',
    'OFRECIMIENTO', 'OFRECIMIENTO DE PRUEBAS', 'OFRECIMIENTO DE MEDIOS',
    'ANEXO', 'ANEXOS', 'DOCUMENTOS', 'DOCUMENTAL', 'DOCUMENTALES','ADMISIÓN', 'ADMISION', 'ADMITA', 'ADMITIR', 'ADMITIRLA', 'ADMÍTASE', 'ADMITASE',
    'TRASLADO', 'CORRER', 'CORRA', 'CÓRRASE', 'CORRASE', 'CÓRRASE TRASLADO', 'CORRASE TRASLADO',
    'NOTIFICAR', 'NOTIFICACIÓN', 'NOTIFICACION', 'SEÑÁLESE', 'SEÑALESE', 'SE SIRVA',
    'PROVEER', 'PROVÉASE', 'PROVEASE','CONTESTACIÓN', 'CONTESTACION', 'CONTESTAR', 'ABSOLUCIÓN', 'ABSOLUCION',
    'EXCEPCIÓN', 'EXCEPCION', 'OPOSICIÓN', 'OPOSICION', 'TACHA', 'TACHAS',
    'ACLARACIÓN', 'ACLARACION', 'INTEGRACIÓN', 'INTEGRACION',
    'SUBSANACIÓN', 'SUBSANACION', 'PRECISIÓN', 'PRECISION', 'AMPLIACIÓN', 'AMPLIACION',
    'CORRECCIÓN', 'CORRECCION', 'RECTIFICACIÓN', 'RECTIFICACION', 'RESUELVE', 'SE RESUELVE', 'SE DISPONE', 'SE ORDENA',
    'AUTO', 'AUTO FINAL', 'AUTO ADMISORIO', 'AUTO DE SANEAMIENTO',
    'SENTENCIA', 'SENTENCIA FINAL', 'SENTENCIA DE VISTA','FISCALÍA', 'FISCALIA', 'UNIDAD', 'ÁREA', 'AREA', 'OFICINA',
    'SECRETARÍA', 'SECRETARIA', 'MESA', 'PARTES', 'MESA DE PARTES',
    'JUZGADO', 'SALA', 'DESPACHO','ASUNTO', 'MATERIA', 'REFERENCIA', 'REFIERE', 'DICE', 'DIGO',
    'SEGUIDAMENTE', 'CONSIDERACIONES', 'CONCLUSIONES'
}

EXCLUDED_PHRASES = {
    'FUNDAMENTOS DE HECHO', 'FUNDAMENTOS DE DERECHO', 'MEDIOS PROBATORIOS',
    'AMPARO MI DEMANDA', 'INTERPONGO DEMANDA', 'FORMULO DEMANDA',
    'POR LO EXPUESTO', 'A USTED PIDO', 'A UD PIDO', 'EN CONSECUENCIA',
    'SIN EMBARGO', 'NO OBSTANTE', 'POR TANTO', 'POR ELLO', 'ASIMISMO',
    'DEL MISMO MODO', 'EN ESTE SENTIDO', 'EN TAL SENTIDO', 'CABE SEÑALAR',
    'ES PRECISO', 'RESULTA NECESARIO', 'ES DE VERSE', 'A LA FECHA',
    'AL RESPECTO', 'POR LO QUE', 'SIENDO ASI', 'SIENDO ASÍ', 'ESTANDO A',
    'EN MERITO A', 'EN MÉRITO A', 'CONFORME A', 'DE CONFORMIDAD CON',
    'EN VIRTUD DE', 'SEÑOR JUEZ', 'SEÑORA JUEZA', 'CUADERNO PRINCIPAL',
    'CUADERNO CAUTELAR', 'OTROSI DIGO', 'OTROSÍ DIGO', 'PODER JUDICIAL',
    'CORTE SUPREMA', 'CORTE SUPERIOR', 'TRIBUNAL CONSTITUCIONAL',
    'MINISTERIO PUBLICO', 'MINISTERIO PÚBLICO', 'DEFENSORIA DEL PUEBLO',
    'DEFENSORÍA DEL PUEBLO', 'CODIGO CIVIL', 'CÓDIGO CIVIL',
    'CODIGO PROCESAL CIVIL', 'CÓDIGO PROCESAL CIVIL', 'CODIGO PENAL',
    'CÓDIGO PENAL', 'CONSTITUCION POLITICA', 'CONSTITUCIÓN POLÍTICA',
    'DECRETO SUPREMO', 'DECRETO LEGISLATIVO', 'DECRETO DE URGENCIA',
    'LEY ORGANICA', 'LEY ORGÁNICA', 'LEY GENERAL', 'TEXTO UNICO ORDENADO',
    'TEXTO ÚNICO ORDENADO', 'PRECEDENTE VINCULANTE', 'ACUERDO PLENARIO',
    'EL DEMANDANTE', 'LA DEMANDANTE', 'EL DEMANDADO', 'LA DEMANDADA',
    'LOS DEMANDANTES', 'LAS DEMANDANTES', 'LOS DEMANDADOS', 'LAS DEMANDADAS',
    'EL RECURRENTE', 'LA RECURRENTE', 'EL SOLICITANTE', 'LA SOLICITANTE',
    'PARTE ACTORA', 'PARTE DEMANDADA', 'INVOCANDO INTERÉS', 'INVOCANDO INTERES',
    'DATOS DEL DEMANDADO', 'DATOS DEL DEMANDANTE', 'DATOS DE LA DEMANDADA',
    'DATOS Y DOMICILIO', 'DOMICILIO PROCESAL', 'DOMICILIO REAL','INTERPONGO LA PRESENTE DEMANDA',
    'INTERPONGO LA PRESENTE DEMANDA DE',
    'INTERPONGO LA PRESENTE DENUNCIA',
    'INTERPONGO LA PRESENTE DENUNCIA POR',
    'FORMULO LA PRESENTE DEMANDA',
    'FORMULO LA PRESENTE DEMANDA DE',
    'PRESENTO LA PRESENTE DEMANDA',
    'PRESENTO LA PRESENTE DEMANDA DE',
    'PRESENTO LA PRESENTE DENUNCIA',
    'PRESENTO LA PRESENTE DENUNCIA POR',
    'DEDUZCO DEMANDA',
    'DEDUZCO DEMANDA DE',
    'PROMUEVO DEMANDA',
    'PROMUEVO DEMANDA DE',
    'PLANTEO DEMANDA',
    'PLANTEO DEMANDA DE','INTERPONGO RECURSO DE APELACION',
    'INTERPONGO RECURSO DE APELACIÓN',
    'INTERPONGO RECURSO DE NULIDAD',
    'INTERPONGO RECURSO DE CASACION',
    'INTERPONGO RECURSO DE CASACIÓN',
    'RECURSO DE APELACION',
    'RECURSO DE APELACIÓN',
    'RECURSO DE NULIDAD',
    'RECURSO DE CASACION',
    'RECURSO DE CASACIÓN','FUNDAMENTOS DE HECHOS',
    'FUNDAMENTOS DE DERECHO Y HECHO',
    'FUNDAMENTOS DE HECHO Y DERECHO',
    'HECHOS Y FUNDAMENTOS DE DERECHO',
    'OFRECIMIENTO DE MEDIOS PROBATORIOS',
    'OFRECIMIENTO DE PRUEBAS',
    'MEDIOS DE PRUEBA',
    'MEDIOS PROBATORIOS OFRECIDOS',
    'ANEXOS QUE SE ACOMPAÑAN',
    'ANEXOS QUE ADJUNTO',
    'DOCUMENTOS QUE ADJUNTO'
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
    'agraviado', 'agraviada', 'víctima', 'victima','actor', 'actora', 'demandado(a)', 'demandante(s)',
    'patrocinante', 'patrocinado', 'patrocinada',
    'defendido', 'defendida',
    'denunciante', 'denunciado', 'denunciada',
    'imputado', 'imputada', 'investigado', 'investigada',
    'agraviado', 'agraviada',
    'hermano', 'hermana',
    'suscrito por', 'suscrita por'
}


def is_excluded_word(value: str) -> bool:
    """
    True => NO se debe tratar como PERSONA (ni como "nombre")
    Maneja palabras sueltas y también FRASES (encabezados legales).
    """
    if not value:
        return True

    v = re.sub(r"\s+", " ", value.strip())
    v_up = v.upper()

    # 1) Si es exactamente una palabra prohibida
    if v_up in EXCLUDED_WORDS:
        return True

    # 2) Si es una FRASE: si la mayoría de palabras son legales -> excluir
    words = re.findall(r"[A-ZÁÉÍÓÚÑ]+", v_up)
    if len(words) >= 2:
        hits = sum(1 for w in words if w in EXCLUDED_WORDS)
        ratio = hits / max(1, len(words))

        # Si 60% o más son palabras “legales/encabezado”, NO es nombre
        if ratio >= 0.60:
            return True

        # Reglas duras: si contiene estas palabras, casi seguro es encabezado, no persona
        HARD = {
            "PENSION", "PENSIÓN", "ALIMENTOS", "REDUCCION", "REDUCCIÓN",
            "MONTO", "SITUACION", "SITUACIÓN", "ECONOMICA", "ECONÓMICA",
            "PETITORIO", "FUNDAMENTOS", "HECHOS", "ANEXOS", "PRUEBAS",
            "PRETENSION", "PRETENSIÓN"
        }
        if any(w in HARD for w in words):
            return True

    return False



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
            # Intentar sm primero (disponible en Render), luego md
            try:
                NLP_MODEL = spacy.load("es_core_news_sm")
                logging.info("spaCy model es_core_news_sm loaded")
            except:
                NLP_MODEL = spacy.load("es_core_news_md")
                logging.info("spaCy model es_core_news_md loaded")
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
    Heurístico expandido para detección de personas con contexto.
    Detecta nombres en MAYÚSCULAS/Title Case con triggers fuertes o débiles.
    Usa source='strong_context_persona' para saltar looks_like_proper_name.
    """
    entities = []

    # ── Patrón de nombre (reutilizado) ───────────────────────────────────────
    # Nombre: 1-6 tokens de letras (incluyendo partículas De/Del/De La…)
    _NAME_TC = (
        r'[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+'          # Token Title Case
        r'(?:\s+(?:de\s+(?:la\s+|los\s+|las\s+)?|del\s+)?'
        r'[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,5}'    # + hasta 5 tokens adicionales c/partículas
    )
    _NAME_UC = (
        r'[A-ZÁÉÍÓÚÑ]{2,}'
        r'(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){0,5}'
    )
    _NAME_MIX = rf'(?:{_NAME_TC}|{_NAME_UC})'

    # ── 1. Nombres en MAYÚSCULAS ─────────────────────────────────────────────
    uppercase_pattern = re.compile(r'\b([A-ZÁÉÍÓÚÑ]{2,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,5})\b')

    for match in uppercase_pattern.finditer(text):
        value = match.group(1)
        start = match.start(1)

        if is_excluded_word(value):
            continue

        words = value.split()
        trigger_close = has_trigger_nearby(text, start)
        if trigger_close:
            # Contexto fuerte → strong_context_persona
            entities.append(Entity(
                type='PERSONA', value=value,
                start=start, end=match.end(1),
                source='strong_context_persona', confidence=0.80
            ))
        elif len(words) >= 3:
            # Sin trigger pero 3+ palabras → heurístico normal
            entities.append(Entity(
                type='PERSONA', value=value,
                start=start, end=match.end(1),
                source='heuristic', confidence=0.70
            ))

    # ── 2. Triggers de Title Case expandidos ─────────────────────────────────
    _TITLE_TRIGGERS = (
        r'se[ñn]or[a]?|sr\.?|sra\.?|don|do[ñn]a'
        r'|abogad[oa]|letrad[oa]|dr\.?|dra\.?'
        r'|el\s+demandante|la\s+demandante'
        r'|el\s+demandado|la\s+demandada'
        r'|el\s+codemandado|la\s+codemandada'
        r'|el\s+recurrente|la\s+recurrente'
        r'|el\s+apelante|la\s+apelante'
        r'|el\s+agraviado|la\s+agraviada'
        r'|el\s+imputado|la\s+imputada'
        r'|el\s+investigado|la\s+investigada'
        r'|el\s+procesado|la\s+procesada'
        r'|el\s+actor|la\s+actora'
        r'|el\s+solicitante|la\s+solicitante'
        r'|el\s+apoderado|la\s+apoderada'
        r'|representante\s+legal'
    )
    # (?i:trigger) aplica IGNORECASE solo al trigger, no al nombre capturado
    # Así "compareció" (minúscula inicial) no se captura como token de nombre.
    titlecase_pattern = re.compile(
        rf'(?i:(?:{_TITLE_TRIGGERS}))\s+({_NAME_TC})'
    )
    for match in titlecase_pattern.finditer(text):
        value = match.group(1).strip()
        if not is_excluded_word(value):
            entities.append(Entity(
                type='PERSONA', value=value,
                start=match.start(1), end=match.start(1) + len(value),
                source='strong_context_persona', confidence=0.82
            ))

    # ── Patrón de nombre estricto (solo mayúsculas iniciales, sin newline) ─────
    # Usando [ \t] en lugar de \s para no cruzar líneas.
    _NAME_STRICT = (
        r'(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]{2,})'           # Token 1
        r'(?:[ \t]+(?:de(?:[ \t]+(?:la|los|las))?[ \t]+|del[ \t]+)?'
        r'(?:[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]{2,})){0,5}'     # Tokens 2-6
    )

    # ── 3. Procurador / Procuradores Judiciales ───────────────────────────────
    procurador_pattern = re.compile(
        r'(?i:procurador(?:es)?[ \t]+(?:p[uú]blic[oa]s?[ \t]+|judiciales?[ \t]+)?'
        r'|procurador(?:es)?[ \t]+a[ \t]+cargo[ \t]+)'
        + '(' + _NAME_STRICT + ')',
    )
    for match in procurador_pattern.finditer(text):
        value = match.group(1).strip()
        if not is_excluded_word(value) and len(value) > 3:
            entities.append(Entity(
                type='PERSONA', value=value,
                start=match.start(1), end=match.start(1) + len(value),
                source='strong_context_persona', confidence=0.82
            ))

    # ── 4. Firmantes y suscriptores ──────────────────────────────────────────
    # Usa [ \t] para no cruzar saltos de línea dentro del nombre.
    firmante_pattern = re.compile(
        r'(?i:atentamente[,\t ]+|firma[ \t]*:[ \t]*|firman?[ \t]*:[ \t]*'
        r'|suscrit[oa][ \t]+por[ \t]+|suscrib[eo][ \t]+(?:la[ \t]+)?(?:presente[ \t]+)?(?:el[ \t]+)?'
        r'|abogado[ \t]+defensor[ \t]*:[ \t]*|abogad[oa][ \t]+patrocinante[ \t]*:[ \t]*'
        r'|nombre[ \t]+del[ \t]+firmante[ \t]*:[ \t]*)'
        + '(' + _NAME_STRICT + ')',
    )
    for match in firmante_pattern.finditer(text):
        value = match.group(1).strip().rstrip(',.:;')
        if not is_excluded_word(value) and len(value) > 4:
            entities.append(Entity(
                type='PERSONA', value=value,
                start=match.start(1), end=match.start(1) + len(value),
                source='strong_context_persona', confidence=0.80
            ))

    # ── 5. Remitente / Destinatario ───────────────────────────────────────────
    remitente_pattern = re.compile(
        r'(?i:de[ \t]*:[ \t]*|a[ \t]*:[ \t]*|para[ \t]*:[ \t]*|remitente[ \t]*:[ \t]*'
        r'|destinatario[ \t]*:[ \t]*|dirigido[ \t]+a[ \t]*:[ \t]*|emisor[ \t]*:[ \t]*)'
        + '(' + _NAME_STRICT + ')',
    )
    for match in remitente_pattern.finditer(text):
        value = match.group(1).strip()
        if not is_excluded_word(value) and len(value) > 4:
            entities.append(Entity(
                type='PERSONA', value=value,
                start=match.start(1), end=match.start(1) + len(value),
                source='strong_context_persona', confidence=0.80
            ))

    # ── 6. Menores / Hijos / Filiación ───────────────────────────────────────
    menor_pattern = re.compile(
        r'(?i:menor[ \t]+(?:de[ \t]+nombre|llamad[oa]|de[ \t]+edad[ \t]+llamad[oa])[ \t]+'
        r'|hij[oa][ \t]+(?:de[ \t]+nombre[ \t]+|llamad[oa][ \t]+|menor[ \t]+)?'
        r'|hijos?[ \t]+menores?[ \t]+(?:de[ \t]+nombre[ \t]+)?'
        r'|(?:la|el)[ \t]+menor[ \t]+)'
        + '(' + _NAME_STRICT + ')',
    )
    for match in menor_pattern.finditer(text):
        value = match.group(1).strip()
        if not is_excluded_word(value) and len(value) > 4:
            entities.append(Entity(
                type='PERSONA', value=value,
                start=match.start(1), end=match.start(1) + len(value),
                source='strong_context_persona', confidence=0.80
            ))

    # ── 7. "de apellido(s)" / "apellidado(a)" / "de nombre(s)" ─────────────
    apellido_pattern = re.compile(
        r'(?i:de[ \t]+apellidos?[ \t]+|apellidad[oa][ \t]+|cuyo[ \t]+apellido[ \t]+es[ \t]+'
        r'|cuyos?[ \t]+nombres?[ \t]+(?:son[ \t]+|es[ \t]+)?'
        r'|de[ \t]+nombres?[ \t]+(?:y[ \t]+apellidos?[ \t]+)?)'
        + '(' + _NAME_STRICT + ')',
    )
    for match in apellido_pattern.finditer(text):
        value = match.group(1).strip()
        if not is_excluded_word(value) and len(value) > 4:
            entities.append(Entity(
                type='PERSONA', value=value,
                start=match.start(1), end=match.start(1) + len(value),
                source='strong_context_persona', confidence=0.82
            ))

    # ── 8. "Demandante: NAME" / "Demandado(a): NAME" / "ciudadano/a NAME" ──
    # Captura nombres directamente después de etiquetas de parte procesal o de
    # menciones de ciudadanía, formas que no cubrían los triggers anteriores.
    partes_pattern = re.compile(
        r'(?i:'
        r'demandante[a]?\s*[:(]\s*'
        r'|demandad[oa]\s*[:(]\s*'
        r'|codemandad[oa]\s*[:(]\s*'
        r'|solicitante[a]?\s*[:(]\s*'
        r'|invitad[oa]\s*[:(]\s*'
        r'|(?:el|la)[ \t]+ciudadan[oa][ \t]+'
        r'|ciudadan[oa][ \t]+'
        r')'
        + '(' + _NAME_STRICT + ')',
    )
    for match in partes_pattern.finditer(text):
        value = match.group(1).strip()
        if not is_excluded_word(value) and len(value) > 4:
            entities.append(Entity(
                type='PERSONA', value=value,
                start=match.start(1), end=match.start(1) + len(value),
                source='strong_context_persona', confidence=0.82
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

def apply_legal_filters(entities: List[Entity]) -> Tuple[List[Entity], Dict[str, Any]]:
    """
    CAPA 5: Aplica filtros anti-sobreanonimización.
    Filtra entidades que son texto jurídico, no PII real.
    """
    try:
        from legal_filters import should_anonymize_span, FilterResult
    except ImportError:
        logging.warning("legal_filters module not available, skipping filtering")
        return entities, {'filter_available': False}
    
    filtered = []
    filter_stats = {
        'filter_available': True,
        'total_input': len(entities),
        'accepted': 0,
        'rejected': 0,
        'rejected_reasons': {}
    }
    
    # Fuentes que implican contexto fuerte ya validado
    STRONG_CTX_SOURCES = frozenset({
        'strong_context_persona', 'context', 'context_identificado',
    })

    for entity in entities:
        use_strong = (
            entity.type in ('PERSONA', 'PER')
            and entity.source in STRONG_CTX_SOURCES
        )
        should_keep, reason = should_anonymize_span(
            entity.value, entity.type,
            strong_context=use_strong
        )
        
        if should_keep:
            if reason == "context_persona":
                # Contexto fuerte → needs_review garantizado (PERSONA siempre en ALWAYS_REVIEW)
                entity.confidence = min(entity.confidence, 0.78)
            elif reason == "possible_name":
                entity.confidence = min(entity.confidence, 0.55)
            elif reason == "proper_name":
                entity.confidence = min(entity.confidence, 0.75)
            filtered.append(entity)
            filter_stats['accepted'] += 1
        else:
            filter_stats['rejected'] += 1
            filter_stats['rejected_reasons'][reason] = filter_stats['rejected_reasons'].get(reason, 0) + 1
            logging.debug(f"Filtered out: '{entity.value[:40]}' ({entity.type}) - {reason}")
    
    return filtered, filter_stats


def detect_all_pii(text: str, apply_filters: bool = True) -> Tuple[List[Entity], Dict[str, Any]]:
    """
    Pipeline completo de detección de PII (8 etapas).
    
    ETAPA 1: Preprocesamiento (implícito)
    ETAPA 2: Regex determinístico (email, DNI, teléfono, direcciones)
    ETAPA 3: Contexto estructural (secciones DATOS DEL DEMANDANTE, etc.)
    ETAPA 4: Contexto legal (palabras gatillo)
    ETAPA 5: NER (apoyo para recall)
    ETAPA 6: Filtro anti-sobreanonimización
    ETAPA 7: Merge y consistencia
    ETAPA 8: Auditor final (en public_app.py)
    
    Args:
        text: Texto a analizar
        apply_filters: Si True, aplica filtros anti-sobreanonimización
    
    Returns:
        Tuple de (lista de entidades, metadata del proceso)
    """
    metadata = {
        'layer1_regex_count': 0,
        'layer2_sections_count': 0,
        'layer2_context_count': 0,
        'layer3_personas_count': 0,
        'total_before_merge': 0,
        'total_after_merge': 0,
        'total_after_filter': 0,
        'spacy_used': False,
        'fallback_used': False,
        'filter_stats': {}
    }
    
    all_entities = []
    
    # ETAPA 2: Regex determinístico (PRIORIDAD MÁXIMA - no puede fallar)
    try:
        layer1 = detect_layer1_regex(text)
        metadata['layer1_regex_count'] = len(layer1)
        all_entities.extend(layer1)
    except Exception as e:
        logging.warning(f"Layer 1 (regex) failed: {e}")
    
    # ETAPA 3: Detección en secciones obligatorias (DATOS DEL DEMANDANTE, etc.)
    try:
        section_entities = detect_pii_in_sections(text)
        metadata['layer2_sections_count'] = len(section_entities)
        all_entities.extend(section_entities)
    except Exception as e:
        logging.warning(f"Section detection failed: {e}")
    
    # ETAPA 4: Heurística legal (contexto con palabras gatillo)
    try:
        layer2 = detect_layer2_context(text)
        metadata['layer2_context_count'] = len(layer2)
        all_entities.extend(layer2)
    except Exception as e:
        logging.warning(f"Layer 2 (context) failed: {e}")
    
    # ETAPA 5: NER para personas (SOLO PARA RECALL, no es autoritativo)
    try:
        layer3 = detect_layer3_personas(text)
        metadata['layer3_personas_count'] = len(layer3)
        all_entities.extend(layer3)
        
        spacy_used = any(e.source == 'spacy' for e in layer3)
        heuristic_used = any(e.source == 'heuristic' for e in layer3)
        metadata['spacy_used'] = spacy_used
        metadata['fallback_used'] = heuristic_used and not spacy_used
    except Exception as e:
        logging.warning(f"Layer 3 failed: {e}")
    
    # ETAPA 5b: NER local entrenado (opcional, controlado por USE_LOCAL_NER)
    try:
        from detector_ner_local import detect_with_local_ner
        local_ner_results = detect_with_local_ner(text)
        if local_ner_results:
            for item in local_ner_results:
                all_entities.append(Entity(
                    type=item.get('type', 'PERSONA'),
                    value=item.get('value', ''),
                    start=item.get('start', 0),
                    end=item.get('end', 0),
                    source='local_ner',
                    confidence=item.get('confidence', 0.85)
                ))
            metadata['local_ner_count'] = len(local_ner_results)
            metadata['local_ner_used'] = True
    except Exception as e:
        logging.warning(f"Local NER failed (non-critical): {e}")
    
    metadata['total_before_merge'] = len(all_entities)

    # CAPA 7: Merge
    merged = merge_entities(all_entities)
    metadata['total_after_merge'] = len(merged)

    # CAPA 6: filtros legales
    if apply_filters:
        filtered, filter_stats = apply_legal_filters(merged)

        # FILTRO FINAL IGNORE
        filtered = [e for e in filtered if not is_excluded_word(e.value)]

        metadata['filter_stats'] = filter_stats
        metadata['total_after_filter'] = len(filtered)
        return filtered, metadata

    # Sin filtros
    merged = [e for e in merged if not is_excluded_word(e.value)]
    metadata['total_after_filter'] = len(merged)
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
    