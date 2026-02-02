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

SYSTEM_PROMPT = """Eres un motor de DETECCIÓN de datos sensibles (PII) para anonimización legal en Perú.

OBJETIVO:
- Detectar PII explícita con alta precisión.
- Minimizar falsos positivos (sobre-identificación).
- Minimizar falsos negativos (fugas).
- NO reescribir el documento.

PROHIBICIONES ABSOLUTAS:
1) PROHIBIDO devolver el texto modificado, tokenizado o reescrito.
2) PROHIBIDO resumir, explicar o comentar el documento.
3) PROHIBIDO inventar datos no presentes en el texto.
4) PROHIBIDO "agrupar" frases completas como si fueran PERSONA. SOLO spans exactos.

SALIDA OBLIGATORIA (JSON ESTRICTO):
Devuelve SOLO JSON válido, sin markdown, sin texto extra, con esta estructura EXACTA:

{
  "entities": [
    {
      "type": "DNI|CE|PASAPORTE|RUC|EMAIL|TELEFONO|DIRECCION|CAL|COLEGIATURA|EXPEDIENTE|CASILLA_ELECTRONICA|PARTIDA|CUENTA_BANCARIA|CCI|TARJETA|PLACA|NOMBRE_PERSONA|NOMBRE_MENOR|FECHA_NACIMIENTO|FIRMA",
      "value": "substring exacto tal como aparece en el texto",
      "start": 0,
      "end": 0,
      "confidence": 0.00,
      "reason": "breve razón"
    }
  ],
  "review": [
    {
      "type_suspected": "NOMBRE_PERSONA|DIRECCION|etc",
      "value": "substring exacto",
      "start": 0,
      "end": 0,
      "confidence": 0.00,
      "reason": "por qué es dudoso"
    }
  ],
  "warnings": []
}

REGLAS DE EXACTITUD:
- "value" DEBE aparecer literalmente en el texto. Sin reconstruir, sin limpiar.
- "start" y "end" son índices de caracteres del texto completo (end exclusivo).
- Si no puedes calcular start/end con seguridad, NO incluyas esa entidad (ponla en review o omítela).
- NO devuelvas duplicados: si el mismo "value" exacto aparece varias veces, devuelve solo el primero y agrega en warnings: "duplicate:<value>".

CATEGORÍAS (QUÉ ES PII):
A) Identificadores
- DNI: 8 dígitos (aunque esté separado por espacios o guiones).
- RUC: 11 dígitos.
- CE/PASAPORTE: si está rotulado o formato claro.
B) Contacto
- EMAIL: cualquier correo válido (aunque esté pegado a tokens o texto).
- TELEFONO: celulares / teléfonos (incluye +51, espacios, guiones).
C) Dirección
- DIRECCION: cuando haya marcadores (Av., Jr., Calle, Psje, Mz, Lt, N°, Dpto, Urb, etc.) o una dirección clara.
D) Profesional
- CAL / COLEGIATURA: "CAL", "C.A.L.", "registro CAL", "CAL nro.", "C.A.L. N°", etc. + número.
E) Bancario / financiero
- CUENTA_BANCARIA / CCI / TARJETA: si está rotulado o patrón típico.
F) Proceso
- EXPEDIENTE: "Exp." "Expediente" + número/código.
- CASILLA_ELECTRONICA: si está rotulado.
- PARTIDA: "Partida electrónica N° …" (NO es DNI).
G) Personas
- NOMBRE_PERSONA: nombres y apellidos de persona natural.
- NOMBRE_MENOR: si el texto indica menor (niño/niña/menor, iniciales con contexto, etc.).
- FIRMA: secciones de firma al final (nombre + DNI/huella/firma).

LISTA NEGRA (NUNCA MARCAR COMO NOMBRE_PERSONA):
NO marcar como NOMBRE_PERSONA (ni nada) lo siguiente:
- Encabezados y fórmulas: "VISTOS", "CONSIDERANDO", "RESUELVE", "SEÑOR JUEZ", "SUMILLA", "OTROSÍ DIGO", "POR TANTO".
- Instituciones/entidades: "PODER JUDICIAL", "MINISTERIO PÚBLICO", "SUNAT", "RENIEC", "INDECOPI", "PNP", "INTERBANK", "BBVA", "BCP", "SCOTIABANK", "MUNICIPALIDAD", "MINISTERIO".
- Cargos genéricos: "JUEZ", "FISCAL", "DEMANDANTE", "DEMANDADO", "ABOGADO", "SECRETARIO", "CONCILIADOR".
- Distritos/ciudades: "LIMA", "MIRAFLORES", etc. (salvo que formen parte de una DIRECCION completa).
- Leyes/normas/artículos: "CÓDIGO CIVIL", "ARTÍCULO", "LEY", "DECRETO".
Si aparece algo de la lista negra dentro de tu supuesto nombre, NO es NOMBRE_PERSONA.

REGLAS ESTRICTAS PARA NOMBRE_PERSONA (ANTI-SOBREIDENTIFICACIÓN):
Solo marcar NOMBRE_PERSONA cuando se cumpla AL MENOS UNA:
1) Formato "Sr./Sra./Señor/Doña/Don + Nombre + Apellido" (mínimo 2 palabras de nombre real).
2) Nombre y apellidos (2 o más componentes) claramente de persona natural, NO institución.
3) Aparece en bloque de firma o identificación (cerca de DNI/CE/PASAPORTE).
4) Está introducido por "identificado como", "con DNI", "de nombre", "suscrito por".

NO marcar como NOMBRE_PERSONA:
- Una sola palabra suelta (ej. "Reiner") SIN evidencia.
- Frases completas o cláusulas. (Si detectas más de 6 palabras seguidas, DESCARTA: eso NO es un nombre.)
- Textos en mayúsculas que parezcan institución o encabezado.
- Cualquier cosa con números dentro (salvo firmas muy específicas; en general, nombres no llevan números).

REGLAS PARA EMAIL (CASO DE TUS ERRORES REALES):
Si ves algo como "{{EMAIL_6}}consultas@abogadasperu.com" o "correo:reyna.abogadasperu@gmail.com",
DEBES extraer el email REAL exacto como EMAIL.
No importa si está pegado a tokens o texto: igual es fuga y debe detectarse.

REGLA CLAVE PARA FIRMAS (CASO DE TUS ERRORES REALES):
Busca al final del documento líneas que contengan:
- Nombre completo + "DNI" o "D.N.I." o "N°"
Eso DEBE detectarse como NOMBRE_PERSONA y DNI.
Ejemplo de patrón textual típico:
"REINER MARQUEZ ALVAREZ" + "D.N.I N° 48819526"
Si lo dejas pasar, tu salida es inválida.

CONFIANZA:
- 0.95–1.00: patrones inequívocos (DNI, RUC, EMAIL, CAL+num, CCI rotulada).
- 0.75–0.94: nombre completo con contexto.
- 0.50–0.74: dudoso -> enviar a review, NO a entities.
- <0.50: no incluir.

CHEQUEO FINAL OBLIGATORIO (SI FALLAS, DEVUELVE VACÍO):
Antes de responder, verifica:
A) JSON válido.
B) Cada entity.value aparece literalmente en el texto.
C) Ninguna entity.value es una frase larga (más de 6 palabras).
D) No incluiste lista negra como nombre.
Si fallas A/B/C/D: responde
{"entities":[],"review":[],"warnings":["output_invalid"]}"""


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
    "ENTIDAD": "ENTIDAD",
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
