"""Retrain the predictive layer on synthetic + REAL benchmark data, with honest out-of-fold evaluation.

    python -m src.train_real            # ~1-3 min; writes models/ and results/real_cv_results.json

Protocol (so the reported numbers are not inflated):
  * AgentInjectionBench (182): 5-fold stratified CV. Every sample is scored by a model that never saw it.
  * InjecAgent (+controls): split 70/30 BY ATTACKER INSTRUCTION (same instruction never on both sides).
    The 30% is a fixed held-out set of unseen attacks.
  * Each fold trains on: synthetic(4000) + InjecAgent-train + AgentInjectionBench-train (weighted: attacks x8, benigns x16).
  * The shipped model is then retrained on everything (so its own scores on real data are in-sample: use the CV
    numbers, not the shipped model, when quoting real-data results).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.model_selection import StratifiedKFold

from . import ml_model, synth
from .evaluate import load_rows
from .features import NGRAM, feature_names, featurize
from .scorer import score_request
from .schema import ToolCallRequest
from .train import _new_clf, _prep

ROOT = Path(__file__).resolve().parent.parent
AIB_ATTACK_WEIGHT = 8.0   # real attacks still matter a lot
AIB_BENIGN_WEIGHT = 16.0  # real hard-benigns are scarce and drive the headline FP number — weight them higher
SEED = 7
# Preferred operating point: keep attack catch >= MIN_CATCH while minimising FP on real benigns
MIN_CATCH = 0.90
TARGET_ALLOW = None  # filled after the sweep; written into meta.json so the shipped defaults can be updated


def _vec(contexts):
    return CountVectorizer(binary=True, ngram_range=NGRAM, min_df=3, max_features=1500).fit([c.lower() for c in contexts])


def _is_ia_test(group: str) -> bool:
    return int(hashlib.md5(group.encode()).hexdigest(), 16) % 10 < 3


def _fit(rows, weights, seed=SEED):
    reqs, sigs, rules_, y = _prep(rows)
    vec = _vec([q.source_context for q in reqs])
    X = featurize(reqs, sigs, rules_, vec)
    backend, clf = _new_clf(seed)
    w = np.asarray(weights, dtype=float)
    if backend == "xgboost":
        rng = np.random.RandomState(seed); idx = rng.permutation(X.shape[0]); cut = int(0.8 * len(idx))
        clf.fit(X[idx[:cut]], y[idx[:cut]], sample_weight=w[idx[:cut]],
                eval_set=[(X[idx[cut:]], y[idx[cut:]])], sample_weight_eval_set=[w[idx[cut:]]], verbose=False)
    else:
        clf.fit(X, y, sample_weight=w)
    return backend, clf, vec


def _install(clf, vec, backend):
    ml_model._state.update(loaded=True, model=clf, vec=vec, meta={"backend": backend})


def _scores(rows):
    return np.array([score_request(ToolCallRequest(**r["request"]), use_ml=True, use_llm=False)[0].score for r in rows])


def _rule_scores(rows):
    return np.array([score_request(ToolCallRequest(**r["request"]), use_ml=False, use_llm=False)[0].score for r in rows])


def rates(scores, labels, allow=30, block=70):
    a, b = scores[labels == 1], scores[labels == 0]
    pct = lambda x: round(float(x), 3)
    return {"n_attack": int(len(a)), "n_benign": int(len(b)),
            "attack_catch": pct((a >= allow).mean()) if len(a) else None,
            "attack_hard_block": pct((a >= block).mean()) if len(a) else None,
            "false_positive": pct((b >= allow).mean()) if len(b) else None,
            "false_hard_block": pct((b >= block).mean()) if len(b) else None}


def _aib_weights(rows):
    """Higher weight on real hard-benigns (they drive the headline FP number)."""
    return [AIB_BENIGN_WEIGHT if r["label"] == "benign" else AIB_ATTACK_WEIGHT for r in rows]


def _pick_threshold(sweep, min_catch=MIN_CATCH):
    """Among points with AIB attack_catch >= min_catch, pick the one with lowest AIB FP.
    Tie-break: higher allow_below (more conservative on benigns)."""
    eligible = [s for s in sweep if (s["AIB"]["attack_catch"] or 0) >= min_catch]
    if not eligible:
        eligible = sweep  # fall back to whole sweep
    return min(eligible, key=lambda s: (s["AIB"]["false_positive"] or 1.0, -s["allow_below"]))


def run(save: bool = True, verbose: bool = True) -> dict:
    real = load_rows(ROOT / "data" / "eval_real.jsonl")
    aib = [r for r in real if r["source"] == "AgentInjectionBench"]
    ia = [r for r in real if r["source"].startswith("InjecAgent")]
    ia_train = [r for r in ia if not _is_ia_test(r["group"])]
    ia_test = [r for r in ia if _is_ia_test(r["group"])]
    # more synthetic data + hard-benigns baked into synth.generate
    syn = synth.generate(4000, seed=SEED, phrasing="train")
    y_aib = np.array([r["label"] == "attack" for r in aib]).astype(int)
    y_ia = np.array([r["label"] == "attack" for r in ia_test]).astype(int)

    oof = np.zeros(len(aib)); ia_fold_scores = []
    for k, (tr, te) in enumerate(StratifiedKFold(5, shuffle=True, random_state=SEED).split(aib, y_aib)):
        aib_tr = [aib[i] for i in tr]
        rows = syn + ia_train + aib_tr
        w = [1.0] * (len(syn) + len(ia_train)) + _aib_weights(aib_tr)
        be, clf, vec = _fit(rows, w)
        _install(clf, vec, be)
        oof[te] = _scores([aib[i] for i in te])
        ia_fold_scores.append(_scores(ia_test))
        if verbose:
            print(f"fold {k + 1}/5 done")
    ia_scores = np.mean(ia_fold_scores, axis=0)

    sweep = []
    for allow in range(10, 71, 5):
        sweep.append({"allow_below": allow, "AIB": rates(oof, y_aib, allow, 70), "IA_heldout": rates(ia_scores, y_ia, allow, 70)})
    base_rules_aib = rates(_rule_scores(aib), y_aib)
    chosen = _pick_threshold(sweep)
    default_30 = next(s for s in sweep if s["allow_below"] == 30)

    # shipped model: train on everything, save, then sanity-check on data it never saw
    rows = syn + ia + aib
    w = [1.0] * (len(syn) + len(ia)) + _aib_weights(aib)
    be, clf, vec = _fit(rows, w)
    _install(clf, vec, be)
    hand = load_rows(ROOT / "data" / "eval_handwritten.jsonl")
    novel = synth.generate(1000, seed=SEED + 1, phrasing="novel")
    sanity = {}
    for name, rs in (("handwritten", hand), ("synthetic_novel", novel)):
        y = np.array([r["label"] == "attack" for r in rs]).astype(int)
        # report at both the old default and the newly chosen threshold
        sanity[name] = {
            "at_30": rates(_scores(rs), y, 30, 70),
            "at_chosen": rates(_scores(rs), y, chosen["allow_below"], 70),
        }
    out = {
        "protocol": "5-fold CV on AgentInjectionBench; InjecAgent held out by attacker instruction (30%)",
        "aib_attack_weight": AIB_ATTACK_WEIGHT,
        "aib_benign_weight": AIB_BENIGN_WEIGHT,
        "min_catch_for_threshold_pick": MIN_CATCH,
        "rules_only_AIB": base_rules_aib,
        "default_thresholds_30_70": default_30,
        "chosen_threshold": chosen,
        "threshold_sweep": sweep,
        "shipped_model_sanity_unseen_data": sanity,
    }
    if save:
        d = ml_model.models_dir(); d.mkdir(parents=True, exist_ok=True)
        if be == "xgboost":
            clf.save_model(str(d / "risk_model.json"))
        else:
            import joblib
            joblib.dump(clf, d / "risk_model.joblib")
        names = feature_names(vec)
        (d / "meta.json").write_text(json.dumps({
            "backend": be, "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seed": SEED,
            "data": "synthetic (incl. hard-benign) + AgentInjectionBench + InjecAgent (src/train_real.py)",
            "vocabulary": {k: int(v) for k, v in vec.vocabulary_.items()}, "n_features": len(names),
            "recommended_allow_below": chosen["allow_below"],
            "recommended_block_at": 70,
            "metrics": {
                "cv_at_30": default_30,
                "cv_at_chosen": chosen,
                "sanity": sanity,
            }}, indent=1))
        ml_model.reset()
        (ROOT / "results").mkdir(exist_ok=True)
        (ROOT / "results" / "real_cv_results.json").write_text(json.dumps(out, indent=2))
    if verbose:
        print(json.dumps({
            "rules_only_AIB": base_rules_aib,
            "at_30": default_30,
            "chosen": chosen,
            "sanity": sanity,
        }, indent=1))
        print("\nallow_below | AIB catch / FP | InjecAgent-heldout catch / control FP")
        for s in sweep:
            mark = "  <-- chosen" if s["allow_below"] == chosen["allow_below"] else ""
            print(f"{s['allow_below']:>11} | {s['AIB']['attack_catch']} / {s['AIB']['false_positive']} | "
                  f"{s['IA_heldout']['attack_catch']} / {s['IA_heldout']['false_positive']}{mark}")
    return out


if __name__ == "__main__":
    run()
