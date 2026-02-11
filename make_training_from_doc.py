from docx import Document
import json
from pathlib import Path
import re

DOC = Path("training_data/raw_docs/CARTA NOTARIAL - SRA. ALINA MORA.docx")
OUT = Path("training_data/train_append.jsonl")

def read_docx(path):
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text)

text = read_docx(DOC)

entities = []

def add(label, pattern):
    for m in re.finditer(pattern, text, re.I):
        entities.append([m.start(), m.end(), label])

# EMAIL
add("EMAIL", r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}")

# DNI
add("DNI", r"\b\d{8}\b")

# MONTO
add("MONTO", r"S\/\s?\d+(?:\.\d{2})?")

# FECHA
add("FECHA", r"\d{1,2}\s+DE\s+[A-ZÁÉÍÓÚÑ]+\s+(?:DEL\s+)?\d{4}")

# PERSONA (líneas en mayúsculas)
for m in re.finditer(r"(?m)^[A-ZÁÉÍÓÚÑ\s]{10,}$", text):
    entities.append([m.start(), m.end(), "PERSONA"])

data = {
    "text": text,
    "entities": sorted(entities)
}

OUT.write_text(json.dumps(data, ensure_ascii=False) + "\n")

print("Archivo listo:", OUT)
