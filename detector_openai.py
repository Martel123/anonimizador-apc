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

SYSTEM_PROMPT = """Eres un DETECTOR DE PII (datos sensibles) para documentos legales peruanos. Tu única función es identificar PII en el texto EXACTO que te envío (un chunk).

REGLAS ABSOLUTAS (NO NEGOCIABLES):
1) PROHIBIDO reescribir, resumir, reformular, traducir, corregir estilo o inventar contenido.
2) PROHIBIDO devolver el texto completo o grandes fragmentos.
3) SOLO devolverás un JSON VÁLIDO (sin markdown, sin explicación fuera del JSON).
4) No incluyas texto adicional, encabezados, comillas decorativas, ni comentarios.
5) NUNCA inventes PII. SOLO marca lo que está en el chunk.
6) Si tienes duda entre "PII" y "no PII", marca como PII (prioridad: recall máximo / cero fugas).
7) IGNORA tokens ya anonimizados con llaves dobles {{...}}: NO los reportes como fugas y NO los modifiques.
8) Debes detectar PII aunque esté:
   - pegada a otras palabras (ej: gmail.comreyna.abogadasperu@gmail.com)
   - separada por espacios raros / saltos de línea / guiones
   - en MAYÚSCULAS o Title Case
9) Debes reportar TODAS las ocurrencias dentro del chunk.

TIPOS DE PII A DETECTAR (OBLIGATORIO):
- PERSONA: nombres y apellidos de personas (2 a 4 palabras) en MAYÚSCULAS o Title Case.
- DIRECCION: direcciones completas y "domicilio real/procesal" (capturar bloque completo).
- EXPEDIENTE: códigos/identificadores de expediente (incluye formatos con guiones).
- ACTA: "ACTA" + identificador.
- CASILLA: "CASILLA" + identificador.
- COLEGIATURA: CAL/CMP/CIP + número.
- ORG: entidades/empresas/organismos (si aparecen) — ejemplo: S.A.C., E.I.R.L., Ministerio, Poder Judicial, Juzgado, Fiscalía.

NOTA IMPORTANTE:
- DNI/RUC/EMAIL/TELEFONO pueden haber sido pre-redactados localmente antes de enviarte el chunk.
  Si NO aparecen en el chunk, NO los inventes. Si aparecen, sí debes reportarlos.

SALIDA OBLIGATORIA (JSON ESTRICTO):
{
  "entities": [
    {"type": "PERSONA|DIRECCION|EXPEDIENTE|ACTA|CASILLA|COLEGIATURA|ORG|DNI|RUC|EMAIL|TELEFONO", "value": "texto exacto del chunk"}
  ]
}

Si no hay PII: {"entities":[]}"""


@dataclass
class OpenAIEntity:
    type: str
    value: str
    source: str = "openai"


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


def call_openai_api(chunk: str, chunk_idx: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Llama a la API de OpenAI para detectar entidades en un chunk.
    Retorna (matches, review) según el nuevo formato del prompt maestro.
    """
    try:
        from openai import OpenAI
        client = OpenAI(timeout=OPENAI_TIMEOUT_SECONDS)
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Analiza el siguiente texto legal y detecta entidades sensibles:\n\n{chunk}"}
            ],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        
        entities = data.get("entities", data.get("matches", []))
        review = data.get("review", [])
        warnings = data.get("warnings", [])
        
        if warnings:
            logger.warning(f"OPENAI_WARNINGS | chunk={chunk_idx} | warnings={warnings}")
        
        logger.info(f"OPENAI_CHUNK | idx={chunk_idx} | entities={len(entities)} | review={len(review)}")
        return entities, review
        
    except json.JSONDecodeError as e:
        logger.error(f"OPENAI_JSON_ERROR | chunk={chunk_idx} | error={str(e)}")
        return [], []
    except Exception as e:
        logger.error(f"OPENAI_API_ERROR | chunk={chunk_idx} | error={str(e)}")
        return [], []


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
    Retorna (entities, review_items) donde review_items son casos dudosos.
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
    all_review = []
    
    with ThreadPoolExecutor(max_workers=OPENAI_CONCURRENCY) as executor:
        futures = {executor.submit(call_openai_api, chunk, i): i for i, chunk in enumerate(chunks)}
        
        for future in as_completed(futures):
            chunk_idx = futures[future]
            try:
                matches, review = future.result()
                
                for ent in matches:
                    category = ent.get("category", ent.get("type", "")).upper()
                    match_text = ent.get("match_text", ent.get("value", "")).strip()
                    confidence = ent.get("confidence", 0.9)
                    
                    mapped_type = CATEGORY_MAP.get(category, category)
                    
                    if match_text and confidence >= 0.5:
                        if not re.match(r'^\{\{.*\}\}$', match_text):
                            all_entities.append(OpenAIEntity(
                                type=mapped_type,
                                value=match_text,
                                source="openai"
                            ))
                
                for item in review:
                    all_review.append({
                        "category": item.get("type_suspected", item.get("category_suspected", "UNKNOWN")),
                        "match_text": item.get("value", item.get("match_text", "")),
                        "reason": item.get("reason", ""),
                        "confidence": item.get("confidence", 0.5)
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
    
    logger.info(f"OPENAI_DETECT_DONE | entities={len(unique_entities)} | review={len(all_review)}")
    return unique_entities, all_review


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
    return {
        "enabled": USE_OPENAI_DETECT,
        "available": is_openai_available(),
        "model": OPENAI_MODEL,
        "timeout": OPENAI_TIMEOUT_SECONDS,
        "max_retries": OPENAI_MAX_RETRIES,
        "chunk_tokens": OPENAI_CHUNK_TOKENS,
        "concurrency": OPENAI_CONCURRENCY,
        "strict_zero_leaks": STRICT_ZERO_LEAKS
    }
