"""Layer 3 (retrieval): BM25 over the organization's action-policy clauses.

Same idea as the reference lab's "Policy RAG": retrieval keeps every explanation grounded in written
rules. Pure-python BM25 (no external dependency). Works with or without an LLM: retrieved clause ids
are cited in the template reason, and also passed to the local Granite judge as context.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

from .schema import ToolCallRequest

ROOT = Path(__file__).resolve().parent.parent


def policy_path() -> Path:
    return Path(os.getenv("GUARDRAIL_POLICY_DOC", ROOT / "data" / "policy_docs" / "action_policy.md"))


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75)."""

    def __init__(self, docs: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.dl = [len(d) for d in docs]
        self.avgdl = sum(self.dl) / max(1, len(docs))
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: List[str]) -> List[float]:
        out = []
        for tf, dl in zip(self.tf, self.dl):
            s = 0.0
            for t in query:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out


def load_clauses(path: Path | None = None) -> List[Dict[str, str]]:
    """Parse '## P-<n> <title>' sections into clause dicts."""
    text = Path(path or policy_path()).read_text(encoding="utf-8")
    clauses = []
    for m in re.finditer(r"^##\s+(P-\d+)\s+(.+?)\n(.*?)(?=^##\s|\Z)", text, re.S | re.M):
        clauses.append({"id": m.group(1), "title": m.group(2).strip(), "text": " ".join(m.group(3).split())})
    return clauses


_cache: Dict[str, tuple] = {}


def _index():
    key = str(policy_path()) + str(policy_path().stat().st_mtime)
    if key not in _cache:
        _cache.clear()
        clauses = load_clauses()
        _cache[key] = (clauses, BM25([_tokens(c["title"] + " " + c["text"]) for c in clauses]))
    return next(iter(_cache.values()))


# which words each rule signal should pull toward in the policy text
SIGNAL_QUERY = {
    "unknown_account": "wire transfer unapproved payee account approval",
    "large_amount": "large payments payment amount human approval",
    "sizeable_amount": "payment amount human approval",
    "amount_mismatch": "payment amount differs stated user request integrity",
    "external_destination": "external recipient outside organization sharing email",
    "write_access": "external sharing edit owner admin access",
    "sensitive_data": "sensitive data credentials passwords payroll customer lists",
    "action_not_requested": "unrequested actions unrelated user request",
    "target_not_requested": "unrequested targets recipient account path untrusted content confirmation",
    "injection_phrase": "prompt injection ignore previous instructions override hide untrusted",
    "target_from_untrusted": "untrusted content instructions recipient originates confirmation",
}
ACTION_QUERY = {
    "delete_file": "destructive actions delete files irreversible backups",
    "wire_transfer": "payments wire transfer",
    "send_email": "outbound email external recipients",
    "share_document": "external document sharing recipients",
    "read_file": "sensitive data read files",
}


def retrieve(req: ToolCallRequest, signals: list, k: int = 3) -> List[Dict[str, str]]:
    """Top-k policy clauses relevant to this tool call (BM25 over a query built from action + signals)."""
    q = ACTION_QUERY.get(req.action, "unknown tools fail closed review")
    fired = [s.name for s in signals if s.points > 0 and s.name in SIGNAL_QUERY]
    q += " " + " ".join(SIGNAL_QUERY[n] for n in fired)
    if req.action not in ACTION_QUERY:
        q += " unknown tools fail closed"
    clauses, bm25 = _index()
    ranked = sorted(zip(bm25.scores(_tokens(q)), clauses), key=lambda x: -x[0])
    return [c for s, c in ranked[:k] if s > 0]
