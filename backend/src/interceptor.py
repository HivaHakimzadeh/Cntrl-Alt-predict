"""Interceptor: every tool call passes through here BEFORE any side-effect.

Adding a new tool to the guardrail = one `@guarded` decorator. Nothing else.
"""
from __future__ import annotations

import functools
import inspect
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, List, Optional

from .logger import log_decision
from .schema import ScorerResponse, ToolCallRequest
from .scorer import score_request


class ActionBlocked(Exception):
    """Raised when the guardrail BLOCKs a tool call. The tool never ran."""

    def __init__(self, response: ScorerResponse):
        super().__init__(f"BLOCKED (score {response.score}): {response.reason}")
        self.response = response


@dataclass
class GuardContext:
    user_request: str = ""      # trusted
    source_context: str = ""    # untrusted text the agent just read


_context: ContextVar[GuardContext] = ContextVar("guard_context", default=GuardContext())
_last: ContextVar[Optional[ScorerResponse]] = ContextVar("guard_last", default=None)
QUARANTINE_QUEUE: List[dict] = []  # actions held for human review


def set_context(user_request: str, source_context: str = "") -> None:
    _context.set(GuardContext(user_request, source_context))


def last_response() -> Optional[ScorerResponse]:
    return _last.get()


def intercept(req: ToolCallRequest) -> ScorerResponse:
    """Score -> policy -> log. Returns the decision; caller decides whether to execute."""
    resp, breakdown = score_request(req)
    log_decision(req, resp, breakdown)
    _last.set(resp)
    if resp.decision == "QUARANTINE":
        QUARANTINE_QUEUE.append({"request": req.model_dump(), "response": resp.model_dump()})
    return resp


def guarded(fn: Callable) -> Callable:
    """Decorator: route every call of `fn` through the guardrail first."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        ctx = _context.get()
        req = ToolCallRequest(
            action=fn.__name__,
            arguments=dict(bound.arguments),
            user_request=ctx.user_request,
            source_context=ctx.source_context,
        )
        resp = intercept(req)
        if resp.decision == "BLOCK":
            raise ActionBlocked(resp)
        if resp.decision == "QUARANTINE":
            return f"QUARANTINED (score {resp.score}): held for human review. {resp.reason}"
        return fn(*bound.args, **bound.kwargs)

    return wrapper
