"""
Detector OpenAI - Detección avanzada de PII usando GPT
=======================================================
Usa la API de OpenAI para detectar entidades en documentos legales peruanos.
Incluye pre-redacción local para privacidad y chunking para documentos largos.
"""

import os
import re
import json
import logging
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

logger = logging.getLogger(__name__)

USE_OPENAI_DETECT = os.environ.get("USE_OPENAI_DETECT", "1") == "1"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT_SECONDS = int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20"))
OPENAI_MAX_RETRIES = int(os.environ.get("OPENAI_MAX_RETRIES", "1"))
OPENAI_CHUNK_TOKENS = int(os.environ.get("OPENAI_CHUNK_TOKENS", "1500"))
OPENAI_CONCURRENCY = int(os.environ.get("OPENAI_CONCURRENCY", "2"))
STRICT_ZERO_LEAKS = os.environ.get("STRICT_ZERO_LEAKS", "1") == "1"

PRE_REDACT_DNI = re.compile(r'\b(\d{8})\b')
PRE_REDACT_RUC = re.compile(r'\b((?:10|15|17|20)\d{9})\b')
PRE_REDACT_EMAIL = re.compile(r'\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b', re.IGNORECASE)
PRE_REDACT_PHONE = re.compile(r'(\+51[\s\-]?9[\d\s\-]{8,12}|\b9\d{8}\b|\b9\d{2}[\s\-]?\d{3}[\s\-]?\d{3}\b)')
PRE_REDACT_CCI = re.compile(r'\b(\d{20})\b')
PRE_REDACT_CUENTA = re.compile(r'(?:cuenta|cta\.?)[\s:]*(?:n[°º]?\s*)?(\d{10,20})', re.IGNORECASE)
PRE_REDACT_PLACA = re.compile(r'\b([A-Z]{3}[-\s]?\d{3})\b', re.IGNORECASE)

OPENAI_ENTITY_TYPES = [
    "PERSONA", "ENTIDAD", "DIRECCION", "COLEGIATURA",
    "EXPEDIENTE", "ACTA", "REGISTRO", "RESOLUCION",
    "PARTIDA", "CASILLA", "JUZGADO", "TRIBUNAL", "SALA"
]

SYSTEM_PROMPT = """Eres un DETECTOR DE PII para documentos legales peruanos.

Devuelve SOLO un JSON con ESTE formato exacto:

{
  "chunk_idx": 0,
  "entities": [
    {
      "type": "PERSONA|DNI|RUC|EMAIL|TELEFONO|DIRECCION|EXPEDIENTE|ACTA|CASILLA|COLEGIATURA|ORG",
      "start": 0,
      "end": 0,
      "value": "",
      "priority": "high|medium",
      "why": ""
    }
  ],
  "residual_check": {
    "possible_remaining_pii": true,
    "notes": []
  }
}

REGLAS DE SALIDA (OBLIGATORIO CUMPLIR):
A) "start" y "end" son índices EXACTOS dentro del CHUNK (Python slicing): chunk[start:end].
B) "value" debe ser EXACTAMENTE chunk[start:end] (idéntico, sin recortar, sin limpiar).
C) "why" debe ser corto (máx 12 palabras).
D) NO reportar NADA dentro de tokens {{...}}.
E) Reporta TODAS las ocurrencias (si se repite 3 veces, reporta 3).
F) Ordena entities por start ascendente.
G) NO devuelvas entidades vacías: si no hay PII, entities debe ser [].

CRITERIOS DE DETECCIÓN (RECALL MÁXIMO):

1) PERSONA (MUY AGRESIVO):
- Marca como PERSONA cualquier secuencia de 2 a 4 palabras SOLO letras:
  a) TODO MAYÚSCULAS: "REINER MARQUEZ ALVAREZ"
  b) Title Case: "Reiner Marquez Alvarez"
- Marca PERSONA con prioridad HIGH si:
  - hay contexto legal cerca (±80 caracteres):
    identificado, identificada, DNI, demandante, demandado, señor, doña, abogado,
    suscrito, suscribo, interpone, contra, madre, padre, menor, hijo, hija
  - O si son 3 o 4 palabras (nombre completo probable), aunque no haya contexto.
- NO marques como PERSONA si coincide con palabras legales comunes (títulos/secciones):
  SEÑOR, SEÑORES, JUEZ, JUZGADO, DEMANDA, SUMILLA, PETITORIO, FUNDAMENTOS,
  MEDIOS, PRUEBAS, ANEXOS, OTROSÍ, EXPEDIENTE, ACTA, CASILLA, MINISTERIO,
  PODER JUDICIAL, FISCALÍA, TRIBUNAL, SALA, ARTÍCULO, CÓDIGO.

2) DNI:
- 8 dígitos consecutivos.
- Prioridad HIGH si cerca aparece "DNI" o "identificado".

3) RUC:
- 11 dígitos que empiecen con 10 o 20. SIEMPRE marcar.

4) EMAIL:
- patrón correo estándar, incluso si está pegado a letras antes/después.
- si encuentras correos concatenados, reporta cada correo.

5) TELEFONO:
- Perú: 9 dígitos (empieza con 9) con o sin +51, con espacios/guiones/paréntesis.

6) DIRECCION:
- Si aparece "domicilio real" o "domicilio procesal":
  captura desde esa frase hasta el siguiente ';' o '.' o salto de línea (lo que ocurra primero).
- Si aparecen disparadores de dirección + número, marca el bloque completo:
  Av/Avenida/Jr/Jirón/Calle/Pasaje/Mz/Manzana/Lt/Lote/Dpto/Departamento/Urb/Urbanización/N°/Nro/Bloque/Piso/Distrito/Provincia/Departamento.

7) EXPEDIENTE / ACTA / CASILLA:
- EXPEDIENTE: "EXPEDIENTE" o "Exp." + identificador con dígitos/guiones.
- ACTA: "ACTA" + identificador.
- CASILLA: "CASILLA" + identificador.
Captura el identificador completo.

8) COLEGIATURA:
- CAL/CMP/CIP (con o sin puntos) + número (4 a 7 dígitos). Ej: "CAL N° 49657"

SEGUNDA PASADA (residual_check):
- Re-escanea mentalmente el chunk.
- Si crees que puede haber PII no capturada (por formatos raros, cortes, pegado), pon:
  possible_remaining_pii=true y notes con máximo 3 patrones cortos.
- Si estás seguro que capturaste todo, possible_remaining_pii=false."""


@dataclass
class OpenAIEntity:
    type: str
    value: str
    source: str = "openai"


@lru_cache(maxsize=1)
def is_openai_available() -> bool:
    """Verifica si OpenAI está disponible y configurado."""
    if not USE_OPENAI_DETECT:
        return False
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    return len(key) > 10


def pre_redact_for_privacy(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Pre-redacta información sensible antes de enviar a OpenAI.
    Retorna el texto redactado y un mapeo para restaurar.
    """
    redaction_map = {}
    result = text
    
    def replace_with_counter(pattern, prefix, text_to_process):
        counter = [0]
        local_map = {}
        def replacer(m):
            counter[0] += 1
            placeholder = f"[{prefix}_{counter[0]}]"
            local_map[placeholder] = m.group(0)
            return placeholder
        new_text = pattern.sub(replacer, text_to_process)
        return new_text, local_map
    
    result, email_map = replace_with_counter(PRE_REDACT_EMAIL, "EMAIL_PRE", result)
    redaction_map.update(email_map)
    
    result, ruc_map = replace_with_counter(PRE_REDACT_RUC, "RUC_PRE", result)
    redaction_map.update(ruc_map)
    
    result, phone_map = replace_with_counter(PRE_REDACT_PHONE, "TEL_PRE", result)
    redaction_map.update(phone_map)
    
    result, cci_map = replace_with_counter(PRE_REDACT_CCI, "CCI_PRE", result)
    redaction_map.update(cci_map)
    
    result, placa_map = replace_with_counter(PRE_REDACT_PLACA, "PLACA_PRE", result)
    redaction_map.update(placa_map)
    
    dni_counter = [0]
    def dni_replacer(m):
        val = m.group(0)
        context_start = max(0, m.start() - 20)
        context = result[context_start:m.start()].lower()
        if any(c in context for c in ['s/', 'us$', '$', 'soles', 'artículo', 'art.', 'ley', 'decreto']):
            return val
        dni_counter[0] += 1
        placeholder = f"[DNI_PRE_{dni_counter[0]}]"
        redaction_map[placeholder] = val
        return placeholder
    result = PRE_REDACT_DNI.sub(dni_replacer, result)
    
    return result, redaction_map


def chunk_text(text: str, max_chars: int = 6000) -> List[str]:
    """Divide el texto en chunks respetando límites de párrafo."""
    if len(text) <= max_chars:
        return [text]
    
    chunks = []
    paragraphs = text.split('\n\n')
    current_chunk = ""
    
    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= max_chars:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            if len(para) > max_chars:
                words = para.split()
                current_chunk = ""
                for word in words:
                    if len(current_chunk) + len(word) + 1 <= max_chars:
                        current_chunk += word + " "
                    else:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = word + " "
            else:
                current_chunk = para + "\n\n"
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks if chunks else [text]


def call_openai_api(chunk: str, chunk_idx: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Llama a la API de OpenAI para detectar entidades en un chunk.
    Retorna (entities, residual_check) según el formato del prompt.
    """
    try:
        from openai import OpenAI
        client = OpenAI(timeout=OPENAI_TIMEOUT_SECONDS)
        
        user_message = f"""CHUNK_IDX: {chunk_idx}
CHUNK_TEXT:
<<<
{chunk}
>>>"""
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        
        entities = data.get("entities", [])
        residual_check = data.get("residual_check", {"possible_remaining_pii": False, "notes": []})
        
        if residual_check.get("possible_remaining_pii"):
            logger.warning(f"OPENAI_RESIDUAL | chunk={chunk_idx} | notes={residual_check.get('notes', [])}")
        
        logger.info(f"OPENAI_CHUNK | idx={chunk_idx} | entities={len(entities)} | residual={residual_check.get('possible_remaining_pii', False)}")
        return entities, residual_check
        
    except json.JSONDecodeError as e:
        logger.error(f"OPENAI_JSON_ERROR | chunk={chunk_idx} | error={str(e)}")
        return [], {"possible_remaining_pii": True, "notes": ["json_error"]}
    except Exception as e:
        logger.error(f"OPENAI_API_ERROR | chunk={chunk_idx} | error={str(e)}")
        return [], {"possible_remaining_pii": True, "notes": ["api_error"]}


CATEGORY_MAP = {
    "DNI": "DNI",
    "RUC": "RUC",
    "CE": "DNI",
    "PASAPORTE": "DNI",
    "EMAIL": "EMAIL",
    "TELEFONO": "TELEFONO",
    "DIRECCION": "DIRECCION",
    "PERSONA": "PERSONA",
    "NOMBRE_PERSONA": "PERSONA",
    "NOMBRE_MENOR": "PERSONA",
    "PLACA": "PLACA",
    "CUENTA_BANCARIA": "CUENTA",
    "CCI": "CUENTA",
    "TARJETA": "CUENTA",
    "EXPEDIENTE": "EXPEDIENTE",
    "FECHA_NACIMIENTO": "FECHA_NACIMIENTO",
    "FIRMA": "FIRMA",
    "COLEGIATURA": "COLEGIATURA",
    "CAL": "COLEGIATURA",
    "CMP": "COLEGIATURA",
    "CIP": "COLEGIATURA",
    "ENTIDAD": "ENTIDAD",
    "ORG": "ENTIDAD",
    "ACTA": "ACTA_REGISTRO",
    "REGISTRO": "ACTA_REGISTRO",
    "RESOLUCION": "RESOLUCION",
    "PARTIDA": "PARTIDA",
    "CASILLA": "CASILLA",
    "CASILLA_ELECTRONICA": "CASILLA",
    "JUZGADO": "JUZGADO",
    "TRIBUNAL": "TRIBUNAL",
    "SALA": "SALA",
}


def detect_with_openai(text: str) -> Tuple[List[OpenAIEntity], List[Dict[str, Any]]]:
    """
    Detecta entidades usando OpenAI API.
    Aplica pre-redacción, chunking y procesamiento paralelo.
    Retorna (entities, residual_notes) donde residual_notes son alertas de posibles fugas.
    """
    if not is_openai_available():
        logger.warning("OPENAI_DISABLED | USE_OPENAI_DETECT=0 or no API key")
        return [], []
    
    if not text or len(text.strip()) < 50:
        return [], []
    
    redacted_text, redaction_map = pre_redact_for_privacy(text)
    
    chunks = chunk_text(redacted_text)
    logger.info(f"OPENAI_DETECT | chunks={len(chunks)} | text_len={len(text)}")
    
    all_entities = []
    all_residual_notes = []
    
    with ThreadPoolExecutor(max_workers=OPENAI_CONCURRENCY) as executor:
        futures = {executor.submit(call_openai_api, chunk, i): i for i, chunk in enumerate(chunks)}
        
        for future in as_completed(futures):
            chunk_idx = futures[future]
            try:
                entities, residual_check = future.result()
                
                for ent in entities:
                    ent_type = ent.get("type", "").upper()
                    value = ent.get("value", "").strip()
                    priority = ent.get("priority", "medium")
                    
                    mapped_type = CATEGORY_MAP.get(ent_type, ent_type)
                    
                    if value and not re.match(r'^\{\{.*\}\}$', value):
                        all_entities.append(OpenAIEntity(
                            type=mapped_type,
                            value=value,
                            source="openai"
                        ))
                
                if residual_check.get("possible_remaining_pii"):
                    for note in residual_check.get("notes", []):
                        all_residual_notes.append({
                            "chunk_idx": chunk_idx,
                            "note": note
                        })
                    
            except Exception as e:
                logger.error(f"OPENAI_FUTURE_ERROR | chunk={chunk_idx} | error={str(e)}")
    
    unique_entities = []
    seen = set()
    for ent in all_entities:
        key = (ent.type, ent.value.lower())
        if key not in seen:
            seen.add(key)
            unique_entities.append(ent)
    
    logger.info(f"OPENAI_DETECT_DONE | entities={len(unique_entities)} | residual_notes={len(all_residual_notes)}")
    return unique_entities, all_residual_notes


def merge_openai_with_local(local_entities: List[Dict], openai_entities: List[OpenAIEntity], text: str) -> List[Dict]:
    """
    Combina entidades de OpenAI con las detectadas localmente.
    OpenAI complementa, no reemplaza las detecciones locales.
    """
    merged = list(local_entities)
    local_values = {e.get('value', '').lower() for e in local_entities}
    
    for oai_ent in openai_entities:
        if oai_ent.value.lower() not in local_values:
            start = text.lower().find(oai_ent.value.lower())
            if start >= 0:
                merged.append({
                    'type': oai_ent.type,
                    'value': oai_ent.value,
                    'start': start,
                    'end': start + len(oai_ent.value),
                    'source': 'openai',
                    'confidence': 0.9
                })
    
    return merged


def get_openai_stats() -> Dict[str, Any]:
    """Retorna estadísticas de configuración de OpenAI."""
    # USE_AI_SEMANTIC_FILTER se define más abajo en el módulo;
    # importamos desde el entorno directamente para no causar NameError.
    semantic_active = os.environ.get("USE_AI_SEMANTIC_FILTER", "0") == "1"
    return {
        "enabled": USE_OPENAI_DETECT,
        "available": is_openai_available(),
        "model": OPENAI_MODEL,
        "timeout": OPENAI_TIMEOUT_SECONDS,
        "max_retries": OPENAI_MAX_RETRIES,
        "chunk_tokens": OPENAI_CHUNK_TOKENS,
        "concurrency": OPENAI_CONCURRENCY,
        "strict_zero_leaks": STRICT_ZERO_LEAKS,
        "ai_semantic_filter": semantic_active,
    }


# ============================================================================
# CAPA IA SEMÁNTICA — VALIDADOR DE CANDIDATOS AMBIGUOS
# ============================================================================
#
# NO procesa el documento completo.
# Recibe solo candidatos ya detectados por el pipeline local y clasifica
# los ambiguos con un único llamado a la API.
#
# Activación: USE_AI_SEMANTIC_FILTER=1
# Desactivación: USE_AI_SEMANTIC_FILTER=0  (default)
# ============================================================================

USE_AI_SEMANTIC_FILTER: bool = os.environ.get("USE_AI_SEMANTIC_FILTER", "0") == "1"

# Tipos que SIEMPRE pasan directo al clasificador (sin filtro IA).
# Incluye PII estructurada (regex determinístico) + entidades judiciales
# detectadas con patrones conservadores que siempre van a ALWAYS_REVIEW.
AI_SKIP_TYPES: frozenset = frozenset({
    # PII estructurada
    'DNI', 'RUC', 'EMAIL', 'TELEFONO',
    'CUENTA', 'CCI', 'EXPEDIENTE', 'CASILLA',
    'COLEGIATURA', 'ACTA', 'ACTA_REGISTRO', 'PLACA',
    'PARTIDA', 'RESOLUCION', 'FECHA_NACIMIENTO',
    # Entidades judiciales con patrones conservadores (siempre → ALWAYS_REVIEW)
    'JUZGADO', 'SALA', 'TRIBUNAL',
})

# Tipos con ambigüedad semántica real que se benefician del filtro IA.
# SOLO estas categorías pasan por el llamado a la API.
AI_AMBIGUOUS_TYPES: frozenset = frozenset({
    'PERSONA', 'ENTIDAD', 'DIRECCION',
})

# Máximo de candidatos por llamado a la API (control de costo)
AI_BATCH_SIZE: int = int(os.environ.get("AI_SEMANTIC_BATCH_SIZE", "25"))

# Caracteres de contexto a enviar por candidato (antes y después)
# Debe coincidir con lo indicado en el prompt del validador (120 chars)
AI_CONTEXT_WINDOW: int = int(os.environ.get("AI_SEMANTIC_CONTEXT", "120"))

# Decisiones válidas que la IA puede devolver
AI_KEEP_DECISIONS = frozenset({
    'PERSONA_REAL', 'ENTIDAD_INSTITUCIONAL', 'DIRECCION_FISICA', 'DUDOSO'
})
AI_DROP_DECISIONS = frozenset({
    'TITULO_JURIDICO', 'TEXTO_NO_SENSIBLE'
})

# ---------------------------------------------------------------------------
# PROMPT DEL VALIDADOR SEMÁNTICO
# Diseñado para documentos legales peruanos. Una sola regla por caso,
# con ejemplos concretos para anclar las decisiones del modelo.
# ---------------------------------------------------------------------------
_SEMANTIC_FILTER_SYSTEM = """\
Eres un VALIDADOR SEMÁNTICO DE PRIVACIDAD para documentos legales peruanos.

INPUT: lista JSON de candidatos detectados automáticamente como posibles datos sensibles.
Cada candidato tiene:
  idx         → índice (devuélvelo siempre en tu respuesta)
  text        → texto del span candidato
  label       → tipo propuesto por el detector local
  ctx_before  → hasta 120 chars antes del span en el documento original
  ctx_after   → hasta 120 chars después del span en el documento original

OUTPUT: JSON estricto → {"results": [{"idx": N, "decision": "CLASE"}]}
Sin explicaciones. Sin texto adicional. Solo JSON.

══════════════════════════════════════════════════════
CLASES VÁLIDAS Y SUS CRITERIOS EXACTOS
══════════════════════════════════════════════════════

PERSONA_REAL
  Nombre de una persona física identificable.
  ✓ "Carlos Javier Quispe Mamani"
  ✓ "María del Carmen Flores"
  ✓ "Javier A. Torres" (inicial + apellido)
  ✗ "ACUERDO FINAL PRIMERO"  → no es nombre
  ✗ "Código Civil Peruano"   → título de norma

ENTIDAD_INSTITUCIONAL
  Nombre propio de una organización, empresa, asociación o entidad pública
  con existencia jurídica propia (se puede demandar, contratar, registrar).
  ✓ "Centro de Conciliación Paz y Justicia"
  ✓ "Banco de Crédito del Perú"
  ✓ "Asociación Civil Los Pinos"
  ✓ "SUNAT" / "RENIEC" / "SUNARP"
  ✗ "Primera Sala Civil de Lima"  → órgano jurisdiccional, no entidad
  ✗ "Ministerio de Justicia"  → solo si es referencia genérica institucional
     (si el doc lo menciona como CONTRAPARTE directa → ENTIDAD_INSTITUCIONAL)

DIRECCION_FISICA
  Dirección postal real: calle, avenida, jirón, manzana, lote, etc.
  ✓ "Jr. Las Flores N° 234, Surquillo"
  ✓ "Av. Arequipa 1500 Of. 302"
  ✓ "Mz. C Lt. 12, AA.HH. Villa María"
  ✗ "Lima, Perú"             → demasiado genérico
  ✗ "la dirección indicada"  → referencia sin datos

TITULO_JURIDICO
  Denominación de un órgano jurisdiccional (juzgado, sala, tribunal, fiscalía)
  O título de sección procesal, norma o fórmula de encabezado.
  No es PII; identifica una función o un cargo, no a una persona o empresa.
  ✓ "Primer Juzgado Civil de Lima"
  ✓ "Primera Sala Civil"
  ✓ "Segunda Fiscalía Provincial Penal"
  ✓ "FUNDAMENTOS DE HECHO"
  ✓ "ACUERDO FINAL PRIMERO"
  ✓ "CÓDIGO CIVIL PERUANO"
  ✓ "SEGUNDO OTROSÍ DIGO"
  ✓ "PETITORIO"

TEXTO_NO_SENSIBLE
  Expresión genérica, fórmula procesal, locución técnica o fragmento
  sin contenido de PII. Tampoco es un órgano jurisdiccional.
  ✓ "la demandante"   ✓ "las partes"   ✓ "el suscrito"
  ✓ "de conformidad"  ✓ "según lo expuesto"
  ✓ Un número romano aislado: "III", "IV"
  ✓ Fragmento truncado sin sentido: "Jiménez" (solo apellido, sin contexto)

DUDOSO
  Usa DUDOSO cuando:
  - El span está claramente truncado o malformado (ej. "Jr." sin calle).
  - El contexto es insuficiente para decidir con certeza.
  - La misma cadena puede ser nombre o título según el caso.
  DUDOSO → se conserva y se marca para revisión humana.

══════════════════════════════════════════════════════
REGLAS DE CALIDAD DE SPAN (antes de clasificar)
══════════════════════════════════════════════════════
Si el span tiene menos de 4 caracteres → TEXTO_NO_SENSIBLE
Si el span tiene más de 120 caracteres → DUDOSO (probablemente mal delimitado)
Si el span es solo dígitos o solo símbolos → TEXTO_NO_SENSIBLE
Si el span es una sola palabra muy común (artículo, preposición, etc.) → TEXTO_NO_SENSIBLE

══════════════════════════════════════════════════════
REGLA FINAL
══════════════════════════════════════════════════════
Ante la duda entre PERSONA_REAL y otro → prioriza privacidad → PERSONA_REAL o DUDOSO.
Ante la duda entre ENTIDAD_INSTITUCIONAL y TITULO_JURIDICO → revisa si tiene personería
jurídica propia (empresa/asociación) vs. si es un órgano del Estado sin vida independiente.
"""


def _call_semantic_filter_api(batch: List[Dict[str, Any]]) -> Dict[int, str]:
    """
    Llama a la API con un batch de candidatos ambiguos.
    Retorna dict {idx: decision_string}.
    Si la API falla → retorna {} (fail-safe aplicado en el llamador).
    """
    VALID_DECISIONS = AI_KEEP_DECISIONS | AI_DROP_DECISIONS
    try:
        from openai import OpenAI
        client = OpenAI(timeout=OPENAI_TIMEOUT_SECONDS)

        user_message = json.dumps({"candidates": batch}, ensure_ascii=False)

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SEMANTIC_FILTER_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            # ~30 tokens per candidate; 25 candidates × 35 = 875 → 1024 por margen
            max_tokens=1024,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        results = data.get("results", [])

        decisions: Dict[int, str] = {}
        for item in results:
            idx = item.get("idx")
            dec = item.get("decision", "DUDOSO")
            if idx is None:
                continue
            # Normalizar y validar que la decisión sea conocida
            dec = dec.strip().upper()
            if dec not in VALID_DECISIONS:
                logger.warning(
                    f"AI_SEMANTIC_UNKNOWN_DECISION | idx={idx} | raw_decision={dec!r} → DUDOSO"
                )
                dec = "DUDOSO"
            decisions[idx] = dec

        logger.info(
            f"AI_SEMANTIC_API | batch_size={len(batch)} | parsed={len(decisions)}"
        )
        return decisions

    except json.JSONDecodeError as e:
        logger.error(f"AI_SEMANTIC_JSON_ERROR | parse_failed={e}")
        return {}
    except Exception as e:
        logger.error(f"AI_SEMANTIC_API_ERROR | {type(e).__name__}: {e}")
        return {}


def validate_ambiguous_candidates(
    entities: List[Dict[str, Any]],
    full_text: str,
) -> List[Dict[str, Any]]:
    """
    Capa IA semántica: valida únicamente los candidatos ambiguos.

    FLUJO:
      1. Separación: PII estructurada (SKIP) → pasan directo sin IA.
      2. Filtro pre-IA: spans malformados se rechazan o marcan DUDOSO antes
         de llegar a la API → ahorra tokens y evita legitimación de ruido.
      3. Batches ≤ AI_BATCH_SIZE candidatos → 1 llamada por batch.
      4. Política de decisión por resultado IA:
           PERSONA_REAL          → conservar, conf ≥ 0.82, fuente confirmada
           ENTIDAD_INSTITUCIONAL → conservar, conf ≥ 0.82
           DIRECCION_FISICA      → conservar, conf ≥ 0.82
           DUDOSO                → conservar, conf ↓ 0.65 → siempre needs_review
           TITULO_JURIDICO       → DESCARTAR (falso positivo confirmado)
           TEXTO_NO_SENSIBLE     → DESCARTAR (falso positivo confirmado)
      5. Fail-safe: si la API falla en un batch → todo ese batch → DUDOSO
         (conservar, revisión humana).

    LOGGING:
      DEBUG: un log por candidato con su decisión.
      INFO:  resumen de candidatos enviados, conservados y descartados;
             desglose por decisión.
    """
    if not entities:
        return entities

    # ──────────────────────────────────────────────────────────────
    # 1. SEPARAR en tres grupos
    # ──────────────────────────────────────────────────────────────
    structured: List[Dict] = []
    ambiguous: List[Dict] = []
    other: List[Dict] = []

    for ent in entities:
        etype = ent.get('type', '').upper()
        if etype in AI_SKIP_TYPES:
            structured.append(ent)
        elif etype in AI_AMBIGUOUS_TYPES:
            ambiguous.append(ent)
        else:
            other.append(ent)

    if not ambiguous:
        return entities

    logger.info(
        f"AI_SEMANTIC_START | structured={len(structured)} "
        f"| ambiguous={len(ambiguous)} | other={len(other)}"
    )

    # ──────────────────────────────────────────────────────────────
    # 2. FILTRO PRE-IA: calidad de span
    # Los candidatos que no pasan se clasifican directamente sin API.
    # ──────────────────────────────────────────────────────────────
    _ONLY_DIGITS_OR_SYMBOLS = re.compile(r'^[\d\W_]+$')
    _COMMON_STOPWORDS = frozenset({
        'de', 'la', 'el', 'en', 'y', 'a', 'que', 'por', 'con', 'del',
        'al', 'los', 'las', 'un', 'una', 'su', 'sus', 'es', 'se',
    })

    ready_for_ai: List[Dict] = []
    pre_dropped: List[str] = []
    pre_kept_as_dudoso: List[str] = []

    for ent in ambiguous:
        span = ent.get('value', '').strip()
        span_len = len(span)

        # Span vacío → descartar
        if span_len == 0:
            pre_dropped.append(f"EMPTY|{ent.get('type')}")
            ent['_pre_filter'] = 'EMPTY'
            continue

        # Span demasiado corto (< 4 chars) → descartar
        if span_len < 4:
            pre_dropped.append(f"TOO_SHORT|{span!r}")
            ent['_pre_filter'] = 'TOO_SHORT'
            continue

        # Solo dígitos/símbolos → descartar
        if _ONLY_DIGITS_OR_SYMBOLS.match(span):
            pre_dropped.append(f"DIGITS_ONLY|{span!r}")
            ent['_pre_filter'] = 'DIGITS_ONLY'
            continue

        # Span muy largo (> 120 chars) → probable captura errónea → DUDOSO
        if span_len > 120:
            ent['_pre_filter'] = 'OVERSIZED'
            ent['ai_decision'] = 'DUDOSO'
            ent['confidence'] = min(ent.get('confidence', 0.55), 0.65)
            pre_kept_as_dudoso.append(f"OVERSIZED|{span[:40]!r}")
            ready_for_ai.append(ent)   # igual se envía a IA con flag
            continue

        # Palabra única muy común → descartar
        words = span.lower().split()
        if len(words) == 1 and words[0] in _COMMON_STOPWORDS:
            pre_dropped.append(f"STOPWORD|{span!r}")
            ent['_pre_filter'] = 'STOPWORD'
            continue

        ready_for_ai.append(ent)

    if pre_dropped:
        logger.info(
            f"AI_PRE_FILTER_DROPPED | count={len(pre_dropped)} "
            f"| items={pre_dropped[:10]}"
        )
    if pre_kept_as_dudoso:
        logger.debug(f"AI_PRE_FILTER_DUDOSO | items={pre_kept_as_dudoso}")

    # ──────────────────────────────────────────────────────────────
    # 3. CONSTRUIR CANDIDATOS CON CONTEXTO para la API
    # ──────────────────────────────────────────────────────────────
    CTX = AI_CONTEXT_WINDOW   # 120 chars por defecto

    candidates_for_ai: List[Dict[str, Any]] = []
    for idx, ent in enumerate(ready_for_ai):
        start = ent.get('start', 0)
        end = ent.get('end', start + len(ent.get('value', '')))
        ctx_before = full_text[max(0, start - CTX):start]
        ctx_after = full_text[end:min(len(full_text), end + CTX)]
        candidates_for_ai.append({
            "idx": idx,
            "text": ent.get('value', '')[:200],
            "label": ent.get('type', ''),
            "ctx_before": ctx_before[-CTX:],
            "ctx_after": ctx_after[:CTX],
        })

    # ──────────────────────────────────────────────────────────────
    # 4. LLAMAR A LA API en batches
    # ──────────────────────────────────────────────────────────────
    all_decisions: Dict[int, str] = {}
    for batch_start in range(0, len(candidates_for_ai), AI_BATCH_SIZE):
        batch = candidates_for_ai[batch_start:batch_start + AI_BATCH_SIZE]
        decisions = _call_semantic_filter_api(batch)
        if not decisions:
            # Fail-safe: API falló → todo el batch → DUDOSO (conservar)
            logger.warning(
                f"AI_SEMANTIC_FAILSAFE | batch_offset={batch_start} "
                f"| size={len(batch)} → all DUDOSO"
            )
            for c in batch:
                all_decisions[c["idx"]] = "DUDOSO"
        else:
            all_decisions.update(decisions)

    # ──────────────────────────────────────────────────────────────
    # 5. APLICAR POLÍTICA DE DECISIÓN
    # ──────────────────────────────────────────────────────────────
    # Confianzas ajustadas por decisión IA:
    #   Confirmado sensible → eleva conf a ≥ 0.82 (aún irá a needs_review
    #   si el tipo es ALWAYS_REVIEW, pero con señal fuerte)
    #   DUDOSO              → limita conf a ≤ 0.65 (siempre needs_review)
    # El campo 'ai_decision' permite trazabilidad en pantalla de revisión.
    _CONF_CONFIRMED = 0.82
    _CONF_DUDOSO    = 0.65

    surviving_ambiguous: List[Dict] = []
    decision_counts: Dict[str, int] = {}

    for idx, ent in enumerate(ready_for_ai):
        decision = all_decisions.get(idx, "DUDOSO")
        decision_counts[decision] = decision_counts.get(decision, 0) + 1

        logger.debug(
            f"AI_DECISION | idx={idx} | type={ent.get('type')} "
            f"| value={ent.get('value','')[:50]!r} | decision={decision}"
        )

        if decision in AI_DROP_DECISIONS:
            # Falso positivo confirmado → descartar
            continue

        # Conservar: ajustar confianza y fuente según decisión
        if decision == 'PERSONA_REAL':
            ent['ai_decision'] = decision
            ent['confidence'] = max(ent.get('confidence', 0.75), _CONF_CONFIRMED)
            ent['source'] = ent.get('source', 'ai_semantic') + '+ai_confirmed'
        elif decision == 'ENTIDAD_INSTITUCIONAL':
            ent['ai_decision'] = decision
            ent['confidence'] = max(ent.get('confidence', 0.75), _CONF_CONFIRMED)
            ent['source'] = ent.get('source', 'ai_semantic') + '+ai_confirmed'
        elif decision == 'DIRECCION_FISICA':
            ent['ai_decision'] = decision
            ent['confidence'] = max(ent.get('confidence', 0.75), _CONF_CONFIRMED)
            ent['source'] = ent.get('source', 'ai_semantic') + '+ai_confirmed'
        else:
            # DUDOSO o decisión no reconocida → necesita revisión humana
            ent['ai_decision'] = 'DUDOSO'
            ent['confidence'] = min(ent.get('confidence', 0.60), _CONF_DUDOSO)

        surviving_ambiguous.append(ent)

    # ──────────────────────────────────────────────────────────────
    # 6. LOG RESUMEN
    # ──────────────────────────────────────────────────────────────
    total_sent    = len(candidates_for_ai)
    total_kept    = len(surviving_ambiguous)
    total_dropped = total_sent - total_kept + len(pre_dropped)

    logger.info(
        f"AI_SEMANTIC_RESULT | "
        f"ambiguous_input={len(ambiguous)} | "
        f"pre_dropped={len(pre_dropped)} | "
        f"sent_to_ai={total_sent} | "
        f"kept={total_kept} | "
        f"dropped_by_ai={total_sent - total_kept} | "
        f"decision_breakdown={decision_counts}"
    )

    return structured + other + surviving_ambiguous
