"""Policy: turns a 0-100 blast-radius score into a level and a decision.

Defaults (tunable via env vars):
    score <  40        -> ALLOW
    40 <= score < 70   -> QUARANTINE
    score >= 70        -> BLOCK

Default raised 30→40 after hard-benign retraining (AIB FP 15%→5%, catch still ≥90%).
Override with GUARDRAIL_ALLOW_BELOW / GUARDRAIL_BLOCK_AT.
"""
import os
from typing import Tuple


def thresholds() -> dict:
    allow_below = int(os.getenv("GUARDRAIL_ALLOW_BELOW", "40"))
    block_at = int(os.getenv("GUARDRAIL_BLOCK_AT", "70"))
    return {"allow_below": allow_below, "block_at": block_at, "high_at": (allow_below + block_at) // 2}


def decide(score: int) -> str:
    t = thresholds()
    if score < t["allow_below"]:
        return "ALLOW"
    if score < t["block_at"]:
        return "QUARANTINE"
    return "BLOCK"


def level_for(score: int) -> str:
    t = thresholds()
    if score >= t["block_at"]:
        return "CRITICAL"
    if score >= t["high_at"]:
        return "HIGH"
    if score >= t["allow_below"]:
        return "MEDIUM"
    return "LOW"


def apply(score: int) -> Tuple[str, str]:
    """Return (level, decision)."""
    return level_for(score), decide(score)
