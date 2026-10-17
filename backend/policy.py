"""Combine the rule score and the ML score into ALLOW / QUARANTINE / BLOCK."""
from schemas import Decision, RuleHit

ALLOW_BELOW = 0.4
BLOCK_AT = 0.75

# The classifier gives ~0.2-0.4 even on harmless text, so only the part above
# ML_FLOOR counts as evidence of an attack.
ML_FLOOR = 0.5
ML_BLOCK_AT = 0.9


def ml_evidence(ml_score: float) -> float:
    return max(0.0, (ml_score - ML_FLOOR) / (1 - ML_FLOOR))


def combine(rule_score: float, ml_score: float) -> float:
    # Noisy-OR: either signal alone can push risk up, and both together push it further.
    return 1 - (1 - rule_score) * (1 - ml_evidence(ml_score))


def decide(rule_score: float, ml_score: float, hits: list[RuleHit]) -> tuple[Decision, float, list[str]]:
    risk = round(combine(rule_score, ml_score), 3)
    reasons = [h.reason for h in hits]
    if ml_score >= ML_FLOOR:
        reasons.append(f"ML classifier: {ml_score:.0%} likely prompt injection")

    if any(h.hard_block for h in hits):
        return Decision.BLOCK, max(risk, 1.0), reasons
    if ml_score >= ML_BLOCK_AT or risk >= BLOCK_AT:
        return Decision.BLOCK, risk, reasons
    if risk >= ALLOW_BELOW:
        return Decision.QUARANTINE, risk, reasons
    return Decision.ALLOW, risk, reasons
