"""Deterministic blast-radius rules.

Each rule looks at a tool call and returns zero or more RuleHits. Weights add up
(capped at 1.0) into the rule score. A hit with hard_block=True forces BLOCK
regardless of the score.
"""
import json
import re
from typing import Any
from urllib.parse import urlparse

from schemas import RuleHit, ToolCall

# Tools whose effects can't be undone once they run.
IRREVERSIBLE_TOOLS = {
    "transfer_funds": 0.3,
    "delete_file": 0.3,
    "delete_records": 0.35,
    "drop_table": 0.45,
    "execute_shell": 0.35,
    "send_email": 0.15,
    "post_message": 0.1,
    "change_permissions": 0.3,
}

# Read-only tools are safe by construction.
READ_ONLY_TOOLS = {"read_file", "search_web", "get_weather", "list_files", "read_email", "get_balance"}

AMOUNT_MEDIUM = 1_000
AMOUNT_HIGH = 10_000

TRUSTED_EMAIL_DOMAINS = {"company.com"}
TRUSTED_URL_DOMAINS = {"api.company.com", "docs.company.com"}
KNOWN_ACCOUNTS = {"ACC-1001", "ACC-1002", "ACC-2001"}

# Words in the user's request that justify each tool. If the user asked for none
# of them, the agent is doing something nobody requested.
TOOL_INTENT_KEYWORDS = {
    "transfer_funds": ["pay", "transfer", "send money", "wire", "invoice", "reimburse"],
    "delete_file": ["delete", "remove", "clean", "clear"],
    "delete_records": ["delete", "remove", "purge", "clean"],
    "drop_table": ["drop", "delete table"],
    "execute_shell": ["run", "execute", "command", "script", "install", "build"],
    "send_email": ["email", "mail", "send", "reply", "notify", "message"],
    "post_message": ["post", "message", "notify", "announce", "slack"],
    "change_permissions": ["permission", "access", "share", "grant"],
}

DANGEROUS_SHELL = [
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "Recursive force delete (rm -rf)"),
    (r"curl[^|]*\|\s*(ba)?sh|wget[^|]*\|\s*(ba)?sh", "Pipes a remote script straight into a shell"),
    (r"\bmkfs\b|\bdd\s+if=", "Disk format or raw disk write"),
    (r"chmod\s+-R\s+777", "Makes everything world-writable"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "Fork bomb"),
]

SENSITIVE_PATHS = [r"/etc/(passwd|shadow)", r"\.ssh/", r"\.env\b", r"^/$", r"^~/?$", r"credentials", r"\*"]

INJECTION_PHRASES = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts)",
    r"disregard (your|the|all) (instructions|rules|guidelines)",
    r"you are now (in )?(developer|admin|dan) mode",
    r"system override",
    r"do not (tell|inform|notify) the user",
]

SECRET_PATTERNS = [
    (r"\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]", "Looks like it contains a credential"),
    (r"\bAKIA[0-9A-Z]{16}\b", "Contains an AWS access key"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "Contains a private key"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "Contains something shaped like an SSN"),
]


def _flatten(args: dict[str, Any]) -> str:
    return json.dumps(args, default=str)


def _domain_of(value: str) -> str:
    if "@" in value and "://" not in value:
        return value.rsplit("@", 1)[-1].lower().strip()
    return (urlparse(value).hostname or "").lower()


def check_irreversible(call: ToolCall) -> list[RuleHit]:
    weight = IRREVERSIBLE_TOOLS.get(call.tool)
    if weight is None:
        return []
    return [RuleHit(rule="irreversible", weight=weight, reason=f"'{call.tool}' can't be undone")]


def check_amount(call: ToolCall) -> list[RuleHit]:
    raw = call.args.get("amount")
    if raw is None:
        return []
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return [RuleHit(rule="amount", weight=0.3, reason=f"Amount '{raw}' isn't a number")]
    if amount >= AMOUNT_HIGH:
        return [RuleHit(rule="amount", weight=0.45, reason=f"Large amount: {amount:,.2f}")]
    if amount >= AMOUNT_MEDIUM:
        return [RuleHit(rule="amount", weight=0.2, reason=f"Moderate amount: {amount:,.2f}")]
    return []


def check_destination(call: ToolCall) -> list[RuleHit]:
    hits = []
    for key in ("to", "recipient", "cc", "bcc"):
        value = call.args.get(key)
        for addr in value if isinstance(value, list) else [value] if value else []:
            domain = _domain_of(str(addr))
            if domain and domain not in TRUSTED_EMAIL_DOMAINS:
                hits.append(RuleHit(rule="unknown_destination", weight=0.3, reason=f"External recipient: {addr}"))
    for key in ("url", "endpoint", "webhook"):
        value = call.args.get(key)
        if value:
            domain = _domain_of(str(value))
            if domain not in TRUSTED_URL_DOMAINS:
                hits.append(RuleHit(rule="unknown_destination", weight=0.25, reason=f"Untrusted URL: {value}"))
    for key in ("to_account", "account", "destination_account"):
        value = call.args.get(key)
        if value and str(value) not in KNOWN_ACCOUNTS:
            hits.append(RuleHit(rule="unknown_destination", weight=0.3, reason=f"Unknown account: {value}"))
    return hits


def check_intent_mismatch(call: ToolCall) -> list[RuleHit]:
    keywords = TOOL_INTENT_KEYWORDS.get(call.tool)
    if not keywords or not call.user_request:
        return []
    request = call.user_request.lower()
    if any(k in request for k in keywords):
        return []
    return [RuleHit(
        rule="intent_mismatch",
        weight=0.35,
        reason=f"User asked '{call.user_request[:80]}' but the agent is calling '{call.tool}'",
    )]


def check_shell(call: ToolCall) -> list[RuleHit]:
    if call.tool != "execute_shell":
        return []
    command = str(call.args.get("command", ""))
    return [
        RuleHit(rule="dangerous_shell", weight=1.0, reason=reason, hard_block=True)
        for pattern, reason in DANGEROUS_SHELL
        if re.search(pattern, command, re.IGNORECASE)
    ]


def check_sensitive_path(call: ToolCall) -> list[RuleHit]:
    if call.tool not in {"delete_file", "change_permissions", "write_file"}:
        return []
    path = str(call.args.get("path", ""))
    if any(re.search(p, path) for p in SENSITIVE_PATHS):
        return [RuleHit(rule="sensitive_path", weight=0.6, reason=f"Touches a sensitive path: {path}", hard_block=True)]
    return []


def check_injection_phrases(call: ToolCall) -> list[RuleHit]:
    text = f"{_flatten(call.args)} {call.context}".lower()
    for pattern in INJECTION_PHRASES:
        if re.search(pattern, text):
            return [RuleHit(rule="injection_phrase", weight=0.6, reason="Known prompt-injection phrase in the input")]
    return []


def check_secrets(call: ToolCall) -> list[RuleHit]:
    if call.tool in READ_ONLY_TOOLS:
        return []
    text = _flatten(call.args)
    return [
        RuleHit(rule="secret_exfiltration", weight=0.5, reason=reason)
        for pattern, reason in SECRET_PATTERNS
        if re.search(pattern, text, re.IGNORECASE)
    ]


RULES = [
    check_irreversible,
    check_amount,
    check_destination,
    check_intent_mismatch,
    check_shell,
    check_sensitive_path,
    check_injection_phrases,
    check_secrets,
]


def run_rules(call: ToolCall) -> tuple[float, list[RuleHit]]:
    hits = [hit for rule in RULES for hit in rule(call)]
    score = min(1.0, sum(h.weight for h in hits))
    return score, hits
