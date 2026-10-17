"""Layer 2 (predictive AI): load the trained risk model and score a tool call.

Model: XGBoost gradient-boosted trees (GBTree), the same family as the reference lab. Saved as native
XGBoost JSON (text format, so it is byte-order independent: trained on x86, loads fine on s390x).
If xgboost cannot be imported (e.g. no s390x wheel), training falls back to scikit-learn's
GradientBoostingClassifier; the rest of the pipeline is unchanged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .features import featurize, make_vectorizer
from .schema import ToolCallRequest

ROOT = Path(__file__).resolve().parent.parent
_state: dict = {"loaded": False, "model": None, "vec": None, "meta": None}


def models_dir() -> Path:
    return Path(os.getenv("GUARDRAIL_MODELS", ROOT / "models"))


def reset() -> None:
    _state.update(loaded=False, model=None, vec=None, meta=None)


def load() -> bool:
    if _state["loaded"]:
        return _state["model"] is not None
    _state["loaded"] = True
    meta_p = models_dir() / "meta.json"
    if not meta_p.exists():
        return False
    try:
        meta = json.loads(meta_p.read_text())
        if meta["backend"] == "xgboost":
            from xgboost import XGBClassifier

            model = XGBClassifier()
            model.load_model(str(models_dir() / "risk_model.json"))
        else:
            import joblib

            model = joblib.load(models_dir() / "risk_model.joblib")
        _state.update(model=model, vec=make_vectorizer(meta["vocabulary"]), meta=meta)
        return True
    except Exception:
        return False


def meta() -> Optional[dict]:
    load()
    return _state["meta"]


def available() -> bool:
    return load()


def predict_risk(req: ToolCallRequest, signals: list, rule: int) -> Optional[int]:
    """Risk 0-100 = round(100 * P(attack)), or None if no trained model is available."""
    if not load():
        return None
    X = featurize([req], [signals], [rule], _state["vec"])
    return int(round(100 * float(_state["model"].predict_proba(X)[0, 1])))
