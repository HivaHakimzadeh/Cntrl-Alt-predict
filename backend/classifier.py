"""Local ML injection classifier: TF-IDF + Logistic Regression.

The model is trained from data/training_data.csv and saved to model/. It's not
committed to git: pickled scikit-learn models are tied to the library version,
so each machine (your Mac, the LinuxONE server) trains its own copy. Training
takes well under a second on the starter dataset.
"""
import csv
import json
from pathlib import Path

import pickle
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_union

from schemas import ToolCall

BASE_DIR = Path(__file__).parent
DATA_PATH = BASE_DIR / "data" / "training_data.csv"
MODEL_PATH = BASE_DIR / "model" / "injection_classifier.joblib"


def load_dataset(path: Path = DATA_PATH) -> tuple[list[str], list[int]]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r["text"] for r in rows], [int(r["label"]) for r in rows]


def build_pipeline() -> Pipeline:
    # Word n-grams catch phrases like "ignore previous instructions";
    # character n-grams survive misspellings and obfuscation.
    features = make_union(
        TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, lowercase=True),
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, lowercase=True),
    )
    return Pipeline([
        ("features", features),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", C=4.0)),
    ])


def train(path: Path = DATA_PATH) -> Pipeline:
    texts, labels = load_dataset(path)
    model = build_pipeline().fit(texts, labels)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    return model


def to_text(call: ToolCall) -> str:
    """What the classifier reads: the untrusted context plus the agent's arguments."""
    arg_text = " ".join(str(v) if not isinstance(v, (dict, list)) else json.dumps(v) for v in call.args.values())
    return f"{call.context} {arg_text}".strip()


class InjectionClassifier:
    def __init__(self) -> None:
        try:
            with open(MODEL_PATH, "rb") as f:
                self.model = pickle.load(f)
        except Exception:
            # Missing, or saved by a different scikit-learn version: retrain locally.
            self.model = train()

    def score(self, call: ToolCall) -> float:
        text = to_text(call)
        if not text:
            return 0.0
        return float(self.model.predict_proba([text])[0][1])
