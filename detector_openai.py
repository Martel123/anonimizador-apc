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

# Tipos que SIEMPRE pasan directo (PII estructurada verificada por regex)
AI_SKIP_TYPES: frozenset = frozenset({
    'DNI', 'RUC', 'EMAIL', 'TELEFONO',
    'CUENTA', 'CCI', 'EXPEDIENTE', 'CASILLA',
    'COLEGIATURA', 'ACTA_REGISTRO', 'PLACA',
    'PARTIDA', 'RESOLUCION', 'FECHA_NACIMIENTO',
})

# Tipos ambiguos que se benefician de validación semántica
AI_AMBIGUOUS_TYPES: frozenset = frozenset({
    'PERSONA', 'ENTIDAD', 'DIRECCION',
    'JUZGADO', 'SALA', 'TRIBUNAL',
})

# Máximo de candidatos por llamado a la API (control de costo)
AI_BATCH_SIZE: int = int(os.environ.get("AI_SEMANTIC_BATCH_SIZE", "25"))

# Caracteres de contexto a enviar por candidato (antes y después)
AI_CONTEXT_WINDOW: int = int(os.environ.get("AI_SEMANTIC_CONTEXT", "100"))

# Decisiones válidas que la IA puede devolver
AI_KEEP_DECISIONS = frozenset({
    'PERSONA_REAL', 'ENTIDAD_INSTITUCIONAL', 'DIRECCION_FISICA', 'DUDOSO'
})
AI_DROP_DECISIONS = frozenset({
    'TITULO_JURIDICO', 'TEXTO_NO_SENSIBLE'
})

# Prompt del validador semántico
_SEMANTIC_FILTER_SYSTEM = """\
Eres un VALIDADOR SEMÁNTICO de privacidad para documentos legales peruanos.
Recibirás una lista de candidatos en formato JSON.
Cada candidato tiene:
  - idx:        índice numérico (conservar en la respuesta)
  - text:       texto del candidato detectado
  - label:      tipo propuesto (PERSONA / ENTIDAD / DIRECCION / JUZGADO / SALA / TRIBUNAL)
  - ctx_before: hasta 100 caracteres antes del candidato en el documento
  - ctx_after:  hasta 100 caracteres después del candidato en el documento

Para CADA candidato devuelve UNA de estas decisiones:
  PERSONA_REAL          → nombre de persona física real (persona natural)
  ENTIDAD_INSTITUCIONAL → nombre de organización, empresa o entidad pública
  DIRECCION_FISICA      → dirección postal o domicilio real
  TITULO_JURIDICO       → encabezado de sección, título de ley, denominación de órgano judicial
  TEXTO_NO_SENSIBLE     → expresión genérica, fórmula procesal, no es PII
  DUDOSO                → ambiguo; no tienes suficiente contexto para decidir

REGLAS ESTRICTAS:
- Devuelve SOLO JSON: {"results": [{"idx": 0, "decision": "..."}]}
- Sin explicaciones, sin texto adicional.
- Si el texto es claramente un nombre de persona (2-6 palabras propias) → PERSONA_REAL.
- Si es un juzgado, sala, tribunal o fiscalía → TITULO_JURIDICO.
- Si es una frase jurídica (FUNDAMENTOS DE HECHO, CÓDIGO CIVIL…) → TEXTO_NO_SENSIBLE.
- En caso de duda real → DUDOSO (se marcará para revisión humana).
"""


def _call_semantic_filter_api(batch: List[Dict[str, Any]]) -> Dict[int, str]:
    """
    Llama a la API con un batch de candidatos.
    Retorna dict {idx: decision}.
    """
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
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        data = json.loads(content)
        results = data.get("results", [])

        decisions = {}
        for item in results:
            idx = item.get("idx")
            decision = item.get("decision", "DUDOSO")
            if idx is not None:
                decisions[idx] = decision
        logger.info(f"AI_SEMANTIC_FILTER | batch={len(batch)} | decisions={len(decisions)}")
        return decisions

    except json.JSONDecodeError as e:
        logger.error(f"AI_SEMANTIC_JSON_ERROR | {e}")
        return {}
    except Exception as e:
        logger.error(f"AI_SEMANTIC_API_ERROR | {e}")
        return {}


def validate_ambiguous_candidates(
    entities: List[Dict[str, Any]],
    full_text: str,
) -> List[Dict[str, Any]]:
    """
    Capa IA semántica: valida solo los candidatos ambiguos.

    Flujo:
      1. Separa PII estructurada (pasa directo) de candidatos ambiguos.
      2. Construye batches con contexto corto (~100 chars antes/después).
      3. Un solo llamado a la API por batch (máx AI_BATCH_SIZE candidatos).
      4. Aplica decisiones:
           PERSONA_REAL / ENTIDAD_INSTITUCIONAL / DIRECCION_FISICA → conservar
           DUDOSO → conservar (irá a needs_review)
           TITULO_JURIDICO / TEXTO_NO_SENSIBLE → descartar
      5. Retorna entidades estructuradas + ambiguas supervivientes.

    Si la API falla, retorna la lista original sin cambios (fail-safe).
    """
    if not entities:
        return entities

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

    # Construir candidatos con contexto
    candidates_for_ai: List[Dict[str, Any]] = []
    for idx, ent in enumerate(ambiguous):
        start = ent.get('start', 0)
        end = ent.get('end', len(ent.get('value', '')))
        ctx_before = full_text[max(0, start - AI_CONTEXT_WINDOW):start]
        ctx_after = full_text[end:min(len(full_text), end + AI_CONTEXT_WINDOW)]
        candidates_for_ai.append({
            "idx": idx,
            "text": ent.get('value', '')[:200],
            "label": ent.get('type', ''),
            "ctx_before": ctx_before[-AI_CONTEXT_WINDOW:],
            "ctx_after": ctx_after[:AI_CONTEXT_WINDOW],
        })

    # Procesar en batches
    all_decisions: Dict[int, str] = {}
    for batch_start in range(0, len(candidates_for_ai), AI_BATCH_SIZE):
        batch = candidates_for_ai[batch_start:batch_start + AI_BATCH_SIZE]
        decisions = _call_semantic_filter_api(batch)
        if not decisions:
            # API falló → conservar todo este batch sin cambios
            for c in batch:
                all_decisions[c["idx"]] = "DUDOSO"
        else:
            all_decisions.update(decisions)

    # Aplicar decisiones a los candidatos ambiguos
    surviving_ambiguous: List[Dict] = []
    for idx, ent in enumerate(ambiguous):
        decision = all_decisions.get(idx, "DUDOSO")
        if decision in AI_DROP_DECISIONS:
            logger.debug(
                f"AI_FILTER_DROP | type={ent.get('type')} "
                f"| value={ent.get('value','')[:40]} | decision={decision}"
            )
            continue
        # Conservar: ajustar confianza según decisión IA
        if decision in ('PERSONA_REAL', 'ENTIDAD_INSTITUCIONAL', 'DIRECCION_FISICA'):
            ent['ai_decision'] = decision
            ent['confidence'] = max(ent.get('confidence', 0.75), 0.80)
        else:
            ent['ai_decision'] = 'DUDOSO'
            ent['confidence'] = min(ent.get('confidence', 0.55), 0.70)
        surviving_ambiguous.append(ent)

    logger.info(
        f"AI_SEMANTIC_RESULT | input={len(ambiguous)} ambiguous "
        f"| kept={len(surviving_ambiguous)} "
        f"| dropped={len(ambiguous) - len(surviving_ambiguous)}"
    )

    return structured + other + surviving_ambiguous
