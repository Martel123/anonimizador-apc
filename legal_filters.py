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
    # ── Artículos y preposiciones cortas (gap crítico) ───────────────────────
    'DE', 'EL', 'LA', 'LO', 'AL', 'UN', 'UNA', 'LES', 'LE',
    'SU', 'SUS', 'MI', 'MIS', 'TU', 'TUS',
    # ── Meses del año ────────────────────────────────────────────────────────
    'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
    'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE',
    # ── Días de la semana ────────────────────────────────────────────────────
    'LUNES', 'MARTES', 'MIÉRCOLES', 'MIERCOLES', 'JUEVES',
    'VIERNES', 'SÁBADO', 'SABADO', 'DOMINGO',
    # ── Ordinales femeninos (PRIMERO…DÉCIMO ya están, faltan femeninos) ──────
    'PRIMERA', 'SEGUNDA', 'TERCERA', 'CUARTA', 'QUINTA',
    'SEXTA', 'SÉPTIMA', 'SEPTIMA', 'OCTAVA', 'NOVENA', 'DÉCIMA', 'DECIMA',
    # ── Gentilicios y demográficos ───────────────────────────────────────────
    'PERUANO', 'PERUANA', 'PERUANOS', 'PERUANAS',
    # ── Tipos de sala / juzgado ──────────────────────────────────────────────
    'FAMILIA', 'LABORAL', 'AGRARIO', 'ADMINISTRATIVO', 'CORRECCIONAL',
    'ESPECIALIZADO', 'ESPECIALIZADA', 'MIXTO', 'MIXTA',
    'NACIONAL', 'REGIONAL', 'PROVINCIAL', 'DISTRITAL', 'MUNICIPAL',
    # ── Calificativos que aparecen en cargos y títulos ───────────────────────
    'GENERAL', 'ESPECIAL', 'PLENA', 'PLENO', 'ÚNICO', 'UNICO', 'ÚNICA', 'UNICA',
    'PRESENTE', 'PRESENTES', 'VIGENTE', 'VIGENTES', 'ACTUAL', 'ACTUALES',
    'CITADO', 'CITADA', 'MENCIONADO', 'MENCIONADA', 'INDICADO', 'INDICADA',
    'REFERIDO', 'REFERIDA', 'SEÑALADO', 'SEÑALADA',
    # ── Sustantivos jurídicos adicionales ────────────────────────────────────
    'PROCESO', 'ACCIÓN', 'ACCION', 'PRETENSIÓN', 'PRETENSION',
    'DEMANDADO', 'DEMANDADA', 'PARTE', 'PARTES', 'ACTO', 'ACTOS',
    'HECHO', 'DERECHO', 'DEBER', 'FACULTAD', 'CAUSA', 'EFECTOS',
    'RESOLUCIÓN', 'DISPOSITION', 'TÉRMINO', 'TERMINO', 'PLAZO',
    'ALIMENTOS', 'DIVORCIO', 'TENENCIA', 'REGIMEN', 'RÉGIMEN', 'VISITAS',
    'FILIACION', 'FILIACIÓN', 'ADOPCION', 'ADOPCIÓN', 'SUCESION', 'SUCESIÓN',
    'TESTAMENTO', 'HERENCIA', 'ANTICIPO', 'LEGÍTIMA', 'LEGITIMA',
    'OBLIGACION', 'OBLIGACIÓN', 'CONTRATO', 'INCUMPLIMIENTO', 'RESOLUCION',
    'RESCISION', 'RESCISIÓN', 'NULIDAD', 'ANULABILIDAD', 'SIMULACION',
    'INDEMNIZACION', 'INDEMNIZACIÓN', 'DAÑOS', 'DANOS', 'PERJUICIOS',
    'LUCRO', 'CESANTE', 'MORAL', 'EMERGENTE',
    'MENOR', 'MENORES', 'HIJOS', 'HIJAS', 'PADRE', 'MADRE', 'CONYUGE',
    'ESPOSO', 'ESPOSA', 'CONVIVIENTE', 'HEREDERO', 'HEREDEROS',
    # ── Palabras de tiempo y ordinales de uso general ─────────────────────────
    'AÑO', 'AÑOS', 'MES', 'MESES', 'DIA', 'DÍAS', 'DIAS',
    'FINAL', 'INICIAL', 'ANTERIOR', 'POSTERIOR', 'PREVIO', 'PREVIA',
    # ── Sustantivos genéricos frecuentes en encabezados ───────────────────────
    'ACUERDO', 'ACUERDOS', 'RESULTADO', 'RESULTADOS', 'INFORME', 'INFORMES',
    'REPORTE', 'REPORTES', 'NOTA', 'NOTAS', 'OFICIO', 'OFICIOS',
    'CARGO', 'CARGOS', 'NOMBRE', 'NOMBRES', 'APELLIDO', 'APELLIDOS',
    'FECHA', 'FECHAS', 'HORA', 'HORAS',
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


SPANISH_MONTHS = {
    'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
    'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE',
}

SPANISH_WEEKDAYS = {
    'LUNES', 'MARTES', 'MIÉRCOLES', 'MIERCOLES',
    'JUEVES', 'VIERNES', 'SÁBADO', 'SABADO', 'DOMINGO',
}

# Sustantivos comunes que pueden aparecer en Title Case pero NO son nombres propios
COMMON_NOUNS_TITLE = {
    'ACTA', 'ACTAS', 'SALA', 'CORTE', 'JUZGADO', 'JUICIO', 'PROCESO',
    'PARTE', 'PARTES', 'OBJETO', 'MATERIA', 'ASUNTO', 'CAUSA', 'CASO',
    'CÓDIGO', 'CODIGO', 'ARTÍCULO', 'ARTICULO', 'REGLAMENTO', 'DECRETO',
    'RESOLUCIÓN', 'RESOLUCION', 'SENTENCIA', 'AUTO', 'FALLO', 'ESCRITO',
    'RECURSO', 'APELACIÓN', 'APELACION', 'CASACIÓN', 'CASACION',
    'PODER', 'ESTADO', 'NACIÓN', 'NACION', 'REPÚBLICA', 'REPUBLICA',
    'MINISTERIO', 'FISCALÍA', 'FISCALIA', 'DEFENSORÍA', 'DEFENSORIA',
    'SUPERINTENDENCIA', 'DIRECCION', 'DIRECCIÓN', 'GERENCIA', 'OFICINA',
    'NOTARIA', 'NOTARÍA', 'REGISTROS', 'REGISTRO',
    'CONTRATO', 'CONVENIO', 'ACUERDO', 'PACTO',
    'TUTELA', 'AMPARO', 'HABEAS', 'CORPUS', 'HÁBEAS',
    'DEMANDA', 'DENUNCIA', 'QUEJA', 'RECURSO', 'ESCRITO',
    'CONCILIACION', 'CONCILIACIÓN', 'ARBITRAJE', 'MEDIACIÓN', 'MEDIACION',
}


def contains_month(text: str) -> bool:
    """Retorna True si el texto contiene nombre de mes o día de la semana."""
    words_upper = {w.upper().rstrip('.,;:') for w in text.split()}
    return bool(words_upper & (SPANISH_MONTHS | SPANISH_WEEKDAYS))


# Sufijos de sustantivos abstractos y adjetivos relacionales en español
# → no pueden ser nombres propios de persona
_COMMON_NOUN_SUFFIXES = (
    'IDAD', 'IDAD', 'CIÓN', 'CION', 'SIÓN', 'SION', 'MIENTO',
    'ANZA', 'ENCIA', 'ANCIA', 'ISMO', 'TURA', 'DURA', 'URA',
)
_COMMON_ADJ_SUFFIXES = (
    'ENTAL', 'IONAL', 'ONAL', 'INAL', 'ERAL', 'ORAL', 'URAL',
    'IENTE', 'ENTE', 'ANTE', 'ARIO', 'ARIA', 'ARIO', 'TIVO', 'TIVA',
    'TICO', 'TICA', 'OSO', 'OSA', 'ESCO', 'ESCA',
)


def is_common_noun(word: str) -> bool:
    """Retorna True si la palabra es un sustantivo o adjetivo común
    (no puede ser un nombre propio de persona).
    Chequea:
    1. Listas explícitas de exclusión
    2. Sufijos típicos de sustantivos abstractos y adjetivos relacionales
    """
    w = word.upper()
    if w in COMMON_NOUNS_TITLE or w in EXCLUDED_UPPERCASE_WORDS:
        return True
    # Sufijo de sustantivo abstracto (>= 7 chars para evitar falsos como "Rial")
    if len(w) >= 7:
        if w.endswith(_COMMON_NOUN_SUFFIXES) or w.endswith(_COMMON_ADJ_SUFFIXES):
            return True
    return False


def looks_like_proper_name(text: str) -> bool:
    """
    Heurística para detectar nombres propios de personas peruanas.
    Balance precisión/recall:
    - 2 a 6 palabras (cubrir nombres con partículas: "María De Los Ángeles")
    - Máximo 80 caracteres
    - No contiene mes, día de la semana ni expresión de fecha
    - No es whitelist exacta, patrón legal, verbo legal ni conector
    - No es título de sección legal
    - Al menos 2 tokens que sean "nombres propios reales":
        * Forma Title Case >= 3 chars, sin ser sustantivo común
        * O forma ALL CAPS >= 3 chars (apellido en mayúsculas)
    - Las partículas de nombre (de, del, de la…) son toleradas pero
      NO cuentan como tokens válidos.
    """
    words = text.split()

    # Rango razonable de palabras para nombre + apellido(s)
    # 6 cubre: Nombre1 Nombre2 + De La + Apellido1 Apellido2
    if len(words) < 2 or len(words) > 6:
        return False

    if len(text) > 80:
        return False

    # Rechazo por fecha / calendario
    if contains_month(text):
        return False

    # Rechazos por whitelists y filtros legales
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

    # Patrones de forma de nombre propio
    title_case_pat = re.compile(r'^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}$')   # Forma: Title (>= 3 chars)
    uppercase_pat  = re.compile(r'^[A-ZÁÉÍÓÚÑ]{3,}$')               # APELLIDO en mayúsculas

    # Partículas de nombre que se toleran (no cuentan como válidas ni inválidas)
    name_particles = {'DE', 'DEL', 'DE LA', 'DE LOS', 'DE LAS', 'LA', 'EL',
                      'VAN', 'VON', 'MC', 'MAC', 'DA', 'DI'}

    valid_name_tokens = 0
    for word in words:
        word_upper = word.upper()

        # Partícula → tolerar, no contar
        if word_upper in name_particles:
            continue

        # Rechazar si es palabra excluida o sustantivo común
        if word_upper in EXCLUDED_UPPERCASE_WORDS or is_common_noun(word):
            continue

        # Contar si tiene forma de nombre propio real
        if title_case_pat.match(word) or uppercase_pat.match(word):
            valid_name_tokens += 1

    return valid_name_tokens >= 2


def should_anonymize_span(text: str, entity_type: str,
                          strong_context: bool = False) -> Tuple[bool, str]:
    """
    Determina si un span debe ser anonimizado.
    Retorna (should_anonymize, reason).

    strong_context=True: el span vino de un contexto fuerte (señor/doña/DNI/
    procurador/firmante/…). Se aplican solo exclusiones básicas; se omite
    looks_like_proper_name para maximizar recall en esos casos.
    """
    if entity_type in ('DNI', 'RUC', 'EMAIL', 'TELEFONO', 'CUENTA', 'CCI',
                       'EXPEDIENTE', 'COLEGIATURA', 'CASILLA'):
        return True, "structured_pii"
    
    if not text or len(text.strip()) < 2:
        return False, "too_short"
    
    if entity_type in ('PERSONA', 'PER'):
        # Valores que cruzan saltos de línea nunca son un nombre limpio
        if '\n' in text or '\r' in text:
            return False, "contains_newline"

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

        # ── Ruta rápida para contexto fuerte ─────────────────────────────────
        # El trigger ya garantiza que es una persona; solo rechazamos si el
        # texto es claramente una frase jurídica o no tiene ningún token nominal.
        if strong_context:
            words = text.split()
            # Rechazar cadenas excesivamente largas (>7 palabras) o sin letras
            if len(words) > 7:
                return False, "too_long_strong_ctx"
            if not re.search(r'[A-Za-záéíóúñÁÉÍÓÚÑ]', text):
                return False, "no_alpha_strong_ctx"
            # Rechazar si solo contiene meses/fechas
            if contains_month(text) and len(words) <= 2:
                return False, "contains_month_or_date"
            return True, "context_persona"
        
        words = text.split()
        if len(words) > 7:
            if not looks_like_proper_name(text):
                return False, "too_long_not_name"
        
        if looks_like_proper_name(text):
            return True, "proper_name"
        
        # ── possible_name: fallback conservador ──────────────────────────────
        if contains_month(text):
            return False, "contains_month_or_date"

        has_name_chars = bool(re.match(r'^[A-Za-záéíóúñÁÉÍÓÚÑ\s]+$', text))
        if has_name_chars and 2 <= len(words) <= 5:
            non_excluded = [
                w for w in words
                if w.upper() not in EXCLUDED_UPPERCASE_WORDS
                and not is_common_noun(w)
            ]
            name_form_pat_tc = re.compile(r'^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{3,}$')
            name_form_pat_uc = re.compile(r'^[A-ZÁÉÍÓÚÑ]{3,}$')
            proper_tokens = [
                w for w in non_excluded
                if name_form_pat_tc.match(w) or name_form_pat_uc.match(w)
            ]
            if len(non_excluded) >= 2 and len(proper_tokens) >= 1:
                return True, "possible_name"

        return False, "default_reject_persona"
    
    if entity_type == 'DIRECCION':
        text_lower = text.lower()
        via_indicators = ['avenida', ' av.', ' av ', 'jirón', 'jiron', ' jr.', ' jr ',
                          'calle ', 'pasaje', ' psje', 'alameda', 'malecón', 'malecon']
        structural_indicators = ['manzana', ' mz.', ' mz ', ' lt.', ' lt ', 'lote ',
                                  'urbanización', 'urbanizacion', ' urb.', ' urb ',
                                  ' dpto.', ' dpto ', 'departamento', ' piso ', ' piso.',
                                  ' int.', ' int ', 'interior ', 'bloque ', ' block ']
        has_via = any(ind in text_lower for ind in via_indicators)
        has_structural = any(ind in text_lower for ind in structural_indicators)
        has_number = bool(re.search(r'\d', text))
        if (has_via and has_number) or has_structural:
            return True, "address_structure"
        return False, "no_address_structure"
    
    if entity_type in ('JUZGADO', 'SALA', 'TRIBUNAL', 'CASILLA', 'ACTA',
                       'PARTIDA', 'RESOLUCION'):
        return True, "legal_entity"

    if entity_type == 'ENTIDAD':
        if is_in_exact_whitelist(text):
            return False, "whitelist_exact"
        if is_all_excluded_words(text):
            return False, "all_excluded_words"
        return True, "entity_needs_review"

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
