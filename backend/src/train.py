"""Train the predictive layer (XGBoost) on synthetic data and save it to models/.

    python -m src.train
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from sklearn.metrics import accuracy_score, roc_auc_score

from . import ml_model, synth
from .features import DENSE_NAMES, feature_names, featurize, make_vectorizer
from .rules import rule_score
from .schema import ToolCallRequest


def _new_clf(seed: int):
    """Regularised on purpose: ~10% of the data is genuinely ambiguous (random labels), and an unregularised
    booster memorises that noise and becomes overconfident exactly where a human should decide."""
    try:
        from xgboost import XGBClassifier

        return "xgboost", XGBClassifier(n_estimators=400, max_depth=3, learning_rate=0.05, subsample=0.8,
                                        colsample_bytree=0.8, min_child_weight=8, reg_lambda=5.0,
                                        early_stopping_rounds=20, n_jobs=2, random_state=seed, eval_metric="logloss")
    except Exception:  # e.g. no s390x wheel: fall back, the rest of the pipeline is unchanged
        from sklearn.ensemble import GradientBoostingClassifier

        return "sklearn", GradientBoostingClassifier(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8,
                                                     min_samples_leaf=15, n_iter_no_change=20,
                                                     validation_fraction=0.2, random_state=seed)


def _fit(clf, backend: str, X, y, seed: int):
    """Early stopping uses a validation slice of the TRAINING data only; the test split is never touched."""
    if backend == "xgboost":
        rng = np.random.RandomState(seed)
        idx = rng.permutation(X.shape[0]); cut = int(0.8 * len(idx))
        clf.fit(X[idx[:cut]], y[idx[:cut]], eval_set=[(X[idx[cut:]], y[idx[cut:]])], verbose=False)
    else:
        clf.fit(X, y)
    return clf


def _prep(rows):
    reqs = [ToolCallRequest(**r["request"]) for r in rows]
    scored = [rule_score(q) for q in reqs]
    return reqs, [s for _, s in scored], [r for r, _ in scored], np.array([1 if r["label"] == "attack" else 0 for r in rows])


def train(n_train: int = 3000, n_test: int = 1000, seed: int = 7, save: bool = True, verbose: bool = True) -> dict:
    train_rows = synth.generate(n_train, seed=seed, phrasing="train")
    test_rows = synth.generate(n_test, seed=seed + 1, phrasing="novel")
    tr_req, tr_sig, tr_rule, y_tr = _prep(train_rows)
    te_req, te_sig, te_rule, y_te = _prep(test_rows)

    vec = make_vectorizer().fit([q.source_context.lower() for q in tr_req])
    X_tr = featurize(tr_req, tr_sig, tr_rule, vec)
    X_te = featurize(te_req, te_sig, te_rule, vec)

    t0 = time.time()
    backend, clf = _new_clf(seed)
    _fit(clf, backend, X_tr, y_tr, seed)
    train_secs = time.time() - t0

    p_te = clf.predict_proba(X_te)[:, 1]
    metrics = {
        "backend": backend, "n_train": n_train, "n_test": n_test, "train_seconds": round(train_secs, 2),
        "test_auc_ml": round(float(roc_auc_score(y_te, p_te)), 4),
        "test_auc_rules_only": round(float(roc_auc_score(y_te, te_rule)), 4),
        "test_accuracy_at_0.5": round(float(accuracy_score(y_te, p_te >= 0.5)), 4),
        "train_auc_ml": round(float(roc_auc_score(y_tr, clf.predict_proba(X_tr)[:, 1])), 4),
    }
    if backend == "xgboost":
        metrics["trees_kept"] = int(clf.best_iteration) + 1
    amb_tr = np.array([r["category"].startswith("ambiguous") for r in train_rows])
    amb_te = np.array([r["category"].startswith("ambiguous") for r in test_rows])
    p_tr = clf.predict_proba(X_tr)[:, 1]
    conf = lambda p: float(((p > 0.8) | (p < 0.2)).mean())
    metrics["ambiguous_confident_train"] = round(conf(p_tr[amb_tr]), 3)   # honest model: low
    metrics["ambiguous_confident_test"] = round(conf(p_te[amb_te]), 3)
    names = feature_names(vec)
    if save:
        d = ml_model.models_dir()
        d.mkdir(parents=True, exist_ok=True)
        if backend == "xgboost":
            clf.save_model(str(d / "risk_model.json"))
        else:
            import joblib

            joblib.dump(clf, d / "risk_model.joblib")
        (d / "meta.json").write_text(json.dumps({
            "backend": backend, "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seed": seed, "data": "synthetic (src/synth.py) - NOT real benchmark data",
            "vocabulary": {k: int(v) for k, v in vec.vocabulary_.items()},
            "n_features": len(names), "metrics": metrics}, indent=1))
        ml_model.reset()
    if verbose:
        print(json.dumps(metrics, indent=1))
    return {"model": clf, "vec": vec, "metrics": metrics, "feature_names": names, "backend": backend,
            "train_rows": train_rows, "test_rows": test_rows,
            "X_tr": X_tr, "X_te": X_te, "y_tr": y_tr, "y_te": y_te, "seed": seed}


def ablation(art: dict) -> dict:
    """Held-out AUC when the model sees only structure, only untrusted-text words, or both."""
    n_dense = len(DENSE_NAMES)
    views = {"structure only (rule signals + numerics)": slice(0, n_dense),
             "text only (words in untrusted content)": slice(n_dense, None),
             "structure + text (shipped model)": slice(None)}
    out = {}
    for name, cols in views.items():
        be, clf = _new_clf(art["seed"])
        _fit(clf, be, art["X_tr"][:, cols], art["y_tr"], art["seed"])
        out[name] = round(float(roc_auc_score(art["y_te"], clf.predict_proba(art["X_te"][:, cols])[:, 1])), 4)
    return out


if __name__ == "__main__":
    a = train()
    print(json.dumps(ablation(a), indent=1))
