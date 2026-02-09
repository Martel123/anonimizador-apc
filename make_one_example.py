import json

text = "El perito Carlos Mendoza Ruiz, colegiado ICAC N° 15432, presentó su informe ante el juzgado."

# Substrings EXACTOS que quieres etiquetar:
persona = "Carlos Mendoza Ruiz"
coleg = "ICAC N° 15432"

p_start = text.index(persona)
p_end = p_start + len(persona)

c_start = text.index(coleg)
c_end = c_start + len(coleg)

record = {
    "text": text,
    "entities": [
        [p_start, p_end, "PERSONA"],
        [c_start, c_end, "COLEGIATURA"]
    ]
}

print("Offsets calculados:", record["entities"])

with open("training_data/train.jsonl", "w", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")

print("Escrito en training_data/train.jsonl")
