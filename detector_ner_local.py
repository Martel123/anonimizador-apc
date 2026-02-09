"""
Detector NER Local - Integración de modelo spaCy entrenado localmente
=====================================================================
Carga un modelo NER entrenado con spaCy desde disco y detecta entidades.
Controlado por variables de entorno:
  - USE_LOCAL_NER: "1" para activar, cualquier otro valor para desactivar (default "0")
  - LOCAL_NER_MODEL_PATH: ruta al modelo (default "models/ner_v1")

Si el modelo no existe o USE_LOCAL_NER != "1", retorna [] sin errores.
"""

import os
import logging
from functools import lru_cache
from typing import List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

USE_LOCAL_NER = os.environ.get("USE_LOCAL_NER", "0") == "1"
LOCAL_NER_MODEL_PATH = os.environ.get("LOCAL_NER_MODEL_PATH", "models/ner_v1")


@lru_cache(maxsize=1)
def _load_model():
    """Carga el modelo spaCy local con cache. Retorna None si no está disponible."""
    model_path = Path(LOCAL_NER_MODEL_PATH)
    if not model_path.exists():
        logger.warning(f"LOCAL_NER | model not found at {model_path}")
        return None

    try:
        import spacy
        nlp = spacy.load(str(model_path))
        logger.info(f"LOCAL_NER | model loaded from {model_path}")
        return nlp
    except Exception as e:
        logger.warning(f"LOCAL_NER | failed to load model: {e}")
        return None


def detect_with_local_ner(text: str) -> list:
    """
    Detecta entidades usando el modelo NER local.

    Retorna lista de dicts compatibles con el pipeline:
        type, value, start, end, source, confidence

    Si USE_LOCAL_NER != "1" o el modelo no existe, retorna [].
    """
    if not USE_LOCAL_NER:
        return []

    if not text or len(text.strip()) < 10:
        return []

    nlp = _load_model()
    if nlp is None:
        return []

    entities = []

    try:
        max_length = 100000
        chunks = [text[i:i + max_length] for i in range(0, len(text), max_length)]
        offset = 0

        for chunk in chunks:
            doc = nlp(chunk)

            for ent in doc.ents:
                value = ent.text.strip()
                if not value or len(value) < 2:
                    continue

                entities.append({
                    "type": ent.label_,
                    "value": value,
                    "start": offset + ent.start_char,
                    "end": offset + ent.end_char,
                    "source": "local_ner",
                    "confidence": 0.85,
                })

            offset += len(chunk)

        logger.info(f"LOCAL_NER | detected {len(entities)} entities")

    except Exception as e:
        logger.warning(f"LOCAL_NER | detection failed: {e}")

    return entities
