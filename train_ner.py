import json
import random
import spacy
from spacy.training import Example
from pathlib import Path

DATA_PATH = Path("training_data/train.jsonl")
MODEL_OUTPUT = Path("models/ner_v1")

def load_data():
    data = []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            text = record["text"]
            ents = record["entities"]
            annotations = {"entities": ents}
            data.append((text, annotations))
    return data

def train():
    print("Cargando datos...")
    TRAIN_DATA = load_data()

    if len(TRAIN_DATA) == 0:
        print("No hay datos en train.jsonl")
        return

    print("Creando modelo en blanco...")
    nlp = spacy.blank("es")

    if "ner" not in nlp.pipe_names:
        ner = nlp.add_pipe("ner")
    else:
        ner = nlp.get_pipe("ner")

    for _, annotations in TRAIN_DATA:
        for ent in annotations["entities"]:
            ner.add_label(ent[2])

    optimizer = nlp.begin_training()

    print("Entrenando modelo...")

    for iteration in range(15):
        random.shuffle(TRAIN_DATA)
        losses = {}

        for text, annotations in TRAIN_DATA:
            doc = nlp.make_doc(text)
            example = Example.from_dict(doc, annotations)
            nlp.update([example], sgd=optimizer, losses=losses)

        print(f"Iteración {iteration+1} - Losses: {losses}")

    MODEL_OUTPUT.mkdir(parents=True, exist_ok=True)
    nlp.to_disk(MODEL_OUTPUT)

    print("Modelo guardado en:", MODEL_OUTPUT)

if __name__ == "__main__":
    train()
