"""Layer 1: deterministic rule engine. Fast, explainable, no ML.

Produces a 0-100 base score plus a list of Signals (each with points and a human-readable detail).
The signals are reused as features by the ML layer (src/features.py) and as queries by the policy RAG.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .schema import ToolCallRequest
from .tool_registry import EXTRA_BASE

# =============================================================================
# TUNABLE CONFIG  (everything a judge might ask "why?" about lives here)
# =============================================================================
ACTION_BASE = {          # inherent blast radius of the action itself
    "read_file": 2,
    "send_email": 8,
    "share_document": 15,   # hard to un-share
    "delete_file": 18,      # irreversible
    "wire_transfer": 20,    # irreversible
}
UNKNOWN_ACTION_BASE = 40    # fail closed: a tool we have never seen is held for review by default

INTERNAL_DOMAINS = {"acme-corp.com"}
KNOWN_PAYEES = {            # accounts the user has paid before
    "ACCT-ELEC-001": "City Power & Light",
    "ACCT-WATER-017": "Metro Water",
    "ACCT-INET-203": "FiberNet",
    "ACCT-RENT-044": "Maple Street Properties",
}
SENSITIVE_KEYWORDS = (
    "password", "passwd", "credential", "api_key", "apikey", "secret", "ssn",
    "social security", "private key", "id_rsa", ".env", "customer list",
    "customer_list", "payroll", "salary", "credit card", "iban",
)
WRITE_PERMISSIONS = {"edit", "write", "owner", "admin", "editor"}

_VERB_SUFFIX = r"(?:s|es|d|ed|ing)?"
ACTION_VERBS = {         # words in the USER's request that justify the action
    "wire_transfer": ["pay", "transfer", "wire", "remit", "settle", "reimburse", "send money"],
    "delete_file": ["delete", "remove", "erase", "clean", "clear", "wipe", "trash", "purge"],
    "send_email": ["send", "sent", "reply", "respond", "forward", "write", "draft", "compose", "notify", "message", "tell"],
    "share_document": ["share", "give", "grant", "collaborate", "forward", "send", "invite"],
    "read_file": ["read", "open", "show", "summarize", "summarise", "check", "review", "look", "view",
                  "print", "display", "analyze", "analyse", "find", "get", "fetch", "load", "parse", "tell"],
}
ACTION_VERB_RX = {
    a: re.compile(r"\b(?:" + "|".join(v) + r")" + _VERB_SUFFIX + r"\b", re.I) for a, v in ACTION_VERBS.items()
}

INJECTION_PATTERNS = [re.compile(p, re.I) for p in (
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|rules|prompts?|directions?)",
    r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)",
    r"(?:new|updated|revised)\s+instructions?\s*:?",
    r"system\s+(?:override|prompt|message|instruction)",
    r"do\s+not\s+(?:tell|inform|notify|alert|mention)\s+(?:this\s+to\s+)?the\s+user",
    r"you\s+(?:must|should|need\s+to)\s+(?:now\s+|immediately\s+)?(?:send|transfer|wire|forward|delete|share|email|grant|remove)",
    r"<\s*/?\s*(?:system|instructions?)\s*>",
    r"(?:note|message)\s+to\s+(?:the\s+)?(?:ai|assistant|agent|model)",
    r"as\s+the\s+(?:ai\s+)?assistant,?\s+(?:please\s+)?(?:you\s+)?(?:send|transfer|wire|forward|delete|share)",
    r"hi\s+ai\s+assistant",
)]

TARGET_KEYS = ("to", "recipient", "path", "doc_id")

# Destroying or overwriting these breaks the machine or its access control, whatever the user asked for.
# Signals listed in HARD_BLOCK_SIGNALS force BLOCK in the scorer, after ML/LLM, so no model can talk them down.
PROTECTED_PATH_RX = [re.compile(p, re.I) for p in (
    r"^[/\\]+\*?$",                                                  # / or /*
    r"^~[/\\]*\*?$",                                                 # ~ or ~/*
    r"^/(?:etc|bin|sbin|boot|usr|lib|lib64|sys|proc|dev|var/lib|system)(?:/|$)",
    r"(?:^|[/\\])\.(?:ssh|gnupg)(?:[/\\]|$)",
    r"^[a-z]:[/\\]*(?:windows|program files)?(?:[/\\]|$)",           # C:\ , C:\Windows
)]
PATH_KEYS = ("path", "file", "filename", "target", "directory", "dir")
DESTRUCTIVE_PATH_ACTIONS = {"delete_file", "file_write"}
DANGEROUS_SHELL_RX = [re.compile(p, re.I) for p in (
    r"\brm\s+(?:-\w+\s+)*-\w*[rf]\w*\s+(?:-\w+\s+)*(?:/|~|/\*|~/\*)(?:\s|$|;)",   # rm -rf / , rm -rf ~
    r"\bmkfs(?:\.\w+)?\b",
    r"\bdd\b[^;|]*\bof=/dev/",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",                      # fork bomb
)]
HARD_BLOCK_SIGNALS = {"protected_path"}


@dataclass
class Signal:
    name: str
    points: int
    detail: str


# =============================================================================
# A. RULE ENGINE
# =============================================================================
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _domain(addr: str) -> Optional[str]:
    return addr.rsplit("@", 1)[1].lower() if "@" in addr else None


def _is_internal(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in INTERNAL_DOMAINS)


def _mentioned(value, text: str) -> bool:
    """Is this target (account / email / path / doc) referenced in `text`?"""
    t, v = _norm(text), str(value)
    cands = {v}
    if "@" in v:
        cands.add(v.split("@")[0])                      # bob@x.com -> bob
    if "/" in v or "\\" in v:
        name = re.split(r"[\\/]", v.rstrip("/\\"))[-1]
        cands.add(name)                                  # /a/b/notes.txt -> notes.txt
        cands.add(name.rsplit(".", 1)[0])                # notes.txt -> notes
    return any(len(_norm(c)) >= 3 and _norm(c) in t for c in cands)


def _targets(req: ToolCallRequest):
    return [(k, str(v)) for k, v in req.arguments.items() if k in TARGET_KEYS and v]


def _num(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _action_implied(req: ToolCallRequest) -> bool:
    """Does the user's request plausibly call for this kind of action?"""
    rx = ACTION_VERB_RX.get(req.action)
    if rx is None or rx.search(req.user_request):
        return True
    if req.action in ("send_email", "share_document"):   # "Email bob ..." names the recipient
        tgt = req.arguments.get("to") or req.arguments.get("recipient")
        return bool(tgt) and _mentioned(tgt, req.user_request)
    return False


def rule_score(req: ToolCallRequest) -> Tuple[int, list]:
    a, ur, ctx = req.arguments, req.user_request, req.source_context
    sig: list = []
    label = req.action.replace("_", " ")

    # 1. inherent blast radius of the action
    base = ACTION_BASE.get(req.action, EXTRA_BASE.get(req.action, UNKNOWN_ACTION_BASE))
    sig.append(Signal("action_type", base, f"{label} is a {'high-impact' if base >= 15 else 'low-impact'} action"))

    # 2. external / unknown destination
    if req.action in ("send_email", "share_document"):
        addr = str(a.get("to") or a.get("recipient") or "")
        d = _domain(addr)
        if d and not _is_internal(d):
            sig.append(Signal("external_destination", 20, f"recipient {addr} is outside the organization"))
    elif req.action == "wire_transfer":
        acct = str(a.get("to", ""))
        if acct not in KNOWN_PAYEES:
            sig.append(Signal("unknown_account", 25, f"{acct or 'destination'} is not a known payee"))

    # 3. amounts: size, and mismatch with what the user said
    amount = _num(a.get("amount"))
    if amount is not None:
        stated = [float(x.replace(",", "")) for x in re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", ur) if x.strip(",")]
        authorized = any(abs(amount - s) <= max(0.01, 0.01 * s) for s in stated)
        if stated and not authorized:
            sig.append(Signal("amount_mismatch", 25, f"amount ${amount:,.0f} differs from the amount in the user's request"))
        if not authorized:
            if amount >= 10_000:
                sig.append(Signal("large_amount", 25, f"large amount (${amount:,.0f})"))
            elif amount >= 1_000:
                sig.append(Signal("sizeable_amount", 10, f"sizeable amount (${amount:,.0f})"))

    # 4. sensitive keywords in the arguments
    blob = json.dumps(a, default=str).lower()
    hit = next((k for k in SENSITIVE_KEYWORDS if k in blob), None)
    if hit:
        sig.append(Signal("sensitive_data", 20, f"touches sensitive data ('{hit}')"))

    if req.action in DESTRUCTIVE_PATH_ACTIONS:
        hit_path = next((str(a[k]).strip() for k in PATH_KEYS
                         if a.get(k) and any(p.search(str(a[k]).strip()) for p in PROTECTED_PATH_RX)), None)
        if hit_path:
            sig.append(Signal("protected_path", 50, f"{label} targets protected system path {hit_path}"))
    elif req.action in ("shell_exec", "code_exec"):
        cmd = str(a.get("command") or a.get("cmd") or a.get("code") or "")
        if any(p.search(cmd) for p in DANGEROUS_SHELL_RX):
            sig.append(Signal("protected_path", 50, f"command can destroy the system ({cmd.strip()[:40]})"))

    if req.action == "share_document" and str(a.get("permission", "")).lower() in WRITE_PERMISSIONS:
        sig.append(Signal("write_access", 20, f"grants {a.get('permission')} access"))

    # 5. does the action contradict / exceed the user's request?
    if not _action_implied(req):
        # A read the user didn't ask for is odd but does no damage on its own; the sensitive-data and
        # untrusted-target signals catch the dangerous reads.
        pts = 10 if req.action == "read_file" else 25
        sig.append(Signal("action_not_requested", pts, f"user's request does not call for a {label}"))
    unmentioned = [
        v for k, v in _targets(req)
        if not (req.action == "wire_transfer" and k == "to" and v in KNOWN_PAYEES) and not _mentioned(v, ur)
    ]
    if unmentioned:
        sig.append(Signal("target_not_requested", 15, f"target {', '.join(unmentioned)} is not mentioned in the user's request"))

    # 6. prompt-injection evidence in the untrusted context
    hits = [m.group(0) for p in INJECTION_PATTERNS if (m := p.search(ctx or ""))]
    if hits:
        sig.append(Signal("injection_phrase", 25, f"injection-style instruction in source content (\"{hits[0].strip()[:40]}\")"))
    # A sensitive target picked up from untrusted content is suspicious even when the wording dodges the regexes.
    if hits or hit:
        taken = [v for _, v in _targets(req) if _mentioned(v, ctx) and not _mentioned(v, ur)]
        if taken:
            sig.append(Signal("target_from_untrusted", 20, f"target {', '.join(taken)} came from untrusted content, not the user"))

    return min(100, sum(s.points for s in sig)), sig


