"""
Smoke test para el detector NER local.
Activa temporalmente USE_LOCAL_NER=1 y ejecuta un texto de prueba.

Uso:
    python smoke_test_local_ner.py
"""

import os
import sys

os.environ["USE_LOCAL_NER"] = "1"
os.environ.setdefault("LOCAL_NER_MODEL_PATH", "models/ner_v1")

from detector_ner_local import _load_model, detect_with_local_ner

TEST_TEXT = "El perito Carlos Mendoza Ruiz, colegiado ICAC N° 15432, presentó su informe."


def main():
    print("=" * 60)
    print("SMOKE TEST - Detector NER Local")
    print("=" * 60)
    print(f"USE_LOCAL_NER = {os.environ.get('USE_LOCAL_NER')}")
    print(f"LOCAL_NER_MODEL_PATH = {os.environ.get('LOCAL_NER_MODEL_PATH')}")
    print()

    model = _load_model()
    if model is None:
        print("ERROR: No se pudo cargar el modelo.")
        print("Asegúrate de que existe en:", os.environ.get("LOCAL_NER_MODEL_PATH"))
        sys.exit(1)

    print(f"Modelo cargado: {model.pipe_names}")
    print(f"Labels: {model.get_pipe('ner').labels if 'ner' in model.pipe_names else 'N/A'}")
    print()

    print(f"Texto de prueba:\n  \"{TEST_TEXT}\"")
    print()

    entities = detect_with_local_ner(TEST_TEXT)

    if not entities:
        print("No se detectaron entidades.")
        print("(El modelo puede necesitar más datos de entrenamiento)")
    else:
        print(f"Entidades detectadas ({len(entities)}):")
        for e in entities:
            print(f"  [{e['type']}] \"{e['value']}\" (pos {e['start']}-{e['end']}, "
                  f"source={e['source']}, conf={e['confidence']})")

    print()
    print("=" * 60)
    print("Smoke test completado.")


if __name__ == "__main__":
    main()
