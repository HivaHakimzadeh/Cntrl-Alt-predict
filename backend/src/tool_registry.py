"""Tool registry: maps the tool names real agents/benchmarks use onto the guardrail's risk classes.

The scorer was built around five tools (wire_transfer, send_email, delete_file, read_file, share_document).
Real benchmarks use hundreds of others (AgentDojo: send_money; AgentInjectionBench: email_send, shell_exec;
InjecAgent: BankManagerTransferFunds, GmailSendEmail, TerminalExecute...). Without this mapping every unseen tool
fell into the fail-closed bucket (base score 40) and was quarantined, which is the main cause of false positives
on real data.

Two things happen here:
  1. canonical_action(name): fold a real tool name into one of our five core actions when the verb is clear.
  2. EXTRA_BASE: classes that don't fit the five (shell, code, generic API, DB...) get their own base score.
Anything still unrecognised stays "unknown" and keeps the fail-closed default.
Edit the keyword lists below to add your own tools. This table is the "blast-radius" knowledge of the system.
"""
from __future__ import annotations

import re

CORE_ACTIONS = ("read_file", "send_email", "share_document", "delete_file", "wire_transfer")

# classes that do not collapse into the five core actions: name -> base blast radius (0-100 scale, like rules.ACTION_BASE)
EXTRA_BASE = {
    "file_write": 12,        # modify state, partly reversible
    "shell_exec": 25,        # arbitrary command execution
    "code_exec": 25,         # arbitrary code execution
    "api_call": 10,          # generic outbound call
    "mcp_tool": 10,          # opaque third-party tool
    "database_query": 8,
    "web_search": 2,
    "web_fetch": 4,
    "browser_navigate": 3,
    "calendar_access": 3,
}
EXTRA_ACTIONS = tuple(EXTRA_BASE)

# exact names seen in the benchmarks
EXACT = {
    "email_send": "send_email", "messaging": "send_email", "send_message": "send_email",
    "file_read": "read_file", "send_money": "wire_transfer",
    "file_write": "file_write", "shell_exec": "shell_exec", "code_exec": "code_exec", "api_call": "api_call",
    "mcp_tool": "mcp_tool", "database_query": "database_query", "web_search": "web_search",
    "web_fetch": "web_fetch", "browser_navigate": "browser_navigate", "calendar_access": "calendar_access",
}

# keyword (a word inside the tool name) -> action. First matching group wins, so the riskiest verbs come first.
_RULES = (
    ("delete_file", {"delete", "remove", "erase", "wipe", "purge", "destroy"}),
    ("wire_transfer", {"transfer", "pay", "withdraw", "deposit", "money", "payment", "order", "purchase", "buy"}),
    ("share_document", {"share", "grant", "unlock", "disable", "autofill", "permission", "invite"}),
    ("send_email", {"send", "email", "forward", "post", "reply", "message", "notify"}),
    ("shell_exec", {"execute", "terminal", "shell", "exec", "command"}),
    ("file_write", {"update", "create", "manage", "move", "schedule", "edit", "write", "set", "add", "modify",
                    "give", "place", "go", "leave", "join", "upload"}),
    ("read_file", {"get", "view", "read", "search", "list", "find", "fetch", "check", "show", "open", "lookup"}),
)


def _words(name: str) -> list:
    parts = re.split(r"[_\-\s.]+", name)
    out = []
    for p in parts:
        out += re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", p)
    return [w.lower() for w in out if w]


def canonical_action(name: str) -> str:
    """Return one of CORE_ACTIONS / EXTRA_ACTIONS, or the original name if nothing matches (-> fail closed)."""
    if not name or name in CORE_ACTIONS or name in EXTRA_BASE:
        return name
    if name in EXACT:
        return EXACT[name]
    words = set(_words(name))
    for action, keys in _RULES:
        if words & keys:
            return action
    return name
