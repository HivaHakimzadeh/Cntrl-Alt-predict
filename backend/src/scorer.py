"""Hybrid blast-radius scorer: four layers, one decision.

    1. RULES        deterministic, explainable base score + signals          (src/rules.py)
    2. PREDICTIVE   XGBoost risk model over signals + untrusted-text words   (src/ml_model.py)
    3. RETRIEVAL    BM25 over the written action policy -> cited clauses     (src/policy_rag.py)
    4. GENERATIVE   local IBM Granite judge, ONLY in the gray zone           (src/llm_local.py)

Layers 2 and 4 use the same asymmetric fold: they may RAISE the score freely, but can only SOFTEN it a little,
so a fooled model cannot talk a rule-flagged action down to ALLOW.
"""
from __future__ import annotations

import os
from dataclasses import asdict
from typing import Optional, Tuple

from . import llm_local, ml_model, policy, policy_rag
from .llm_local import cache_key, cache_path, llm_enabled, store_verdict  # noqa: F401  (re-exported)
from .rules import Signal, rule_score  # noqa: F401  (re-exported)
from .schema import ScorerResponse, ToolCallRequest


def ml_enabled() -> bool:
    return os.getenv("GUARDRAIL_ML", "on").lower() != "off"


def fold(base: int, other: int) -> int:
    """`other` (ML or LLM risk) may RAISE the score freely but can only SOFTEN it a little."""
    return round(0.4 * base + 0.6 * other) if other >= base else round(0.75 * base + 0.25 * other)


def in_gray_zone(score: int) -> bool:
    t = policy.thresholds()
    return t["allow_below"] <= score < t["block_at"]


def _rule_reason(signals: list, final: int) -> str:
    risky = sorted((s for s in signals if s.points > 0), key=lambda s: -s.points)
    if final < policy.thresholds()["allow_below"] or not risky:
        return "Routine action consistent with the user's request."
    text = "; ".join(s.detail for s in risky[:3])
    return text[0].upper() + text[1:] + "."


def score_request(req: ToolCallRequest, use_ml: Optional[bool] = None,
                  use_llm: Optional[bool] = None) -> Tuple[ScorerResponse, dict]:
    """Return (ScorerResponse, breakdown). use_ml / use_llm override the env switches (used by evaluation)."""
    use_ml = ml_enabled() if use_ml is None else use_ml
    use_llm = llm_enabled() if use_llm is None else use_llm
    t = policy.thresholds()

    rule, signals = rule_score(req)                                             # 1. rules
    ml_risk = ml_model.predict_risk(req, signals, rule) if use_ml else None     # 2. predictive
    stage1 = fold(rule, ml_risk) if ml_risk is not None else rule
    clauses = policy_rag.retrieve(req, signals) if stage1 >= t["allow_below"] else []   # 3. retrieval
    verdict = (llm_local.judge(req, signals, clauses, stage1)                  # 4. generative (gray zone only)
               if (use_llm and in_gray_zone(stage1)) else None)
    final = max(0, min(100, fold(stage1, verdict["risk"]) if verdict else stage1))

    level, decision = policy.apply(final)
    if verdict and verdict.get("reason"):
        reason = verdict["reason"]
    else:
        reason = _rule_reason(signals, final)
        if clauses:
            reason = reason.rstrip(".") + f" (policy {', '.join(c['id'] for c in clauses)})."
    resp = ScorerResponse(score=final, level=level, decision=decision, reason=reason)
    breakdown = {
        "rule_score": rule,
        "ml_used": ml_risk is not None, "ml_risk": ml_risk, "stage1_score": stage1,
        "policy_clauses": [c["id"] for c in clauses],
        "llm_used": verdict is not None, "llm_risk": verdict["risk"] if verdict else None,
        "llm_cached": bool(verdict and verdict.get("cached")), "llm_source": verdict.get("source") if verdict else None,
        "signals": [asdict(s) for s in signals if s.points > 0],
    }
    return resp, breakdown


def score(req: ToolCallRequest) -> ScorerResponse:
    return score_request(req)[0]
