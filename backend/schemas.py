from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Decision(str, Enum):
    ALLOW = "ALLOW"
    QUARANTINE = "QUARANTINE"
    BLOCK = "BLOCK"


class ToolCall(BaseModel):
    tool: str = Field(..., description="Name of the tool the agent wants to call, e.g. 'send_email'")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments the agent passed to the tool")
    user_request: str = Field("", description="What the human originally asked the agent to do")
    context: str = Field("", description="Untrusted content the agent read before acting (web page, email body, file)")
    agent_id: str = "default"


class RuleHit(BaseModel):
    rule: str
    weight: float
    reason: str
    hard_block: bool = False


class Evaluation(BaseModel):
    id: str
    timestamp: str
    decision: Decision
    risk_score: float
    rule_score: float
    ml_score: float
    reasons: list[str]
    rule_hits: list[RuleHit]
    tool_call: ToolCall
    status: str = Field("final", description="'pending' while quarantined, then 'approved' or 'rejected'")
    resolved_by: Optional[str] = None
