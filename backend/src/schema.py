"""THE CONTRACT. Every component speaks exactly this language (Pydantic v2)."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .tool_registry import canonical_action


class ToolCallRequest(BaseModel):
    action: str = Field(description='Tool name, e.g. "wire_transfer"')
    arguments: dict = Field(description='Tool args, e.g. {"to": "ACCT-9921", "amount": 50000}')
    user_request: str = Field(description="The user's original message (trusted)")
    source_context: str = Field(
        default="",
        description="Text the agent just read: email, doc, webpage... (UNTRUSTED)",
    )
    raw_action: str = Field(default="", description="Original tool name before mapping (filled automatically)")

    @model_validator(mode="after")
    def _canonicalise(self):
        """Real tool names (send_money, GmailSendEmail...) are mapped onto our risk classes; see tool_registry.py."""
        if not self.raw_action:
            self.raw_action = self.action
        self.action = canonical_action(self.action)
        return self


class ScorerResponse(BaseModel):
    score: int = Field(ge=0, le=100)
    level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    decision: Literal["ALLOW", "QUARANTINE", "BLOCK"]
    reason: str = Field(description="One short human-readable sentence (mandatory)")
