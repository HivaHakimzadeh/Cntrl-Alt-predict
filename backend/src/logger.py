"""Structured JSON-lines log of every guardrail decision."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .schema import ScorerResponse, ToolCallRequest

ROOT = Path(__file__).resolve().parent.parent


def log_path() -> Path:
    return Path(os.getenv("GUARDRAIL_LOG", ROOT / "logs" / "decisions.jsonl"))


def log_decision(req: ToolCallRequest, resp: ScorerResponse, breakdown: Optional[dict] = None) -> dict:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "action": req.action,
        "arguments": req.arguments,
        "user_request": req.user_request,
        "source_context": req.source_context,
        "score": resp.score,
        "level": resp.level,
        "decision": resp.decision,
        "reason": resp.reason,
        **(breakdown or {}),  # rule_score, llm_risk, llm_used, signals (explainability extras)
    }
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return entry


def read_log(limit: Optional[int] = None) -> List[dict]:
    path = log_path()
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-limit:] if limit else rows


def clear_log() -> None:
    path = log_path()
    if path.exists():
        path.unlink()
