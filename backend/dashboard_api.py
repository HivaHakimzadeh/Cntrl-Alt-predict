"""Small UI adapter around the team's guardrail contract.

The team's /evaluate endpoint remains unchanged. Review decisions here record
human approval/rejection; they never execute a tool call.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.logger import log_decision
from src.scorer import score_request
from src.schema import ToolCallRequest

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
_records: list[dict] = []


class DashboardToolCall(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    user_request: str = ""
    context: str = ""
    agent_id: str = "dashboard-demo"


def _copy(record: dict) -> dict:
    return {**record, "tool_call": dict(record["tool_call"]), "reasons": list(record["reasons"])}


@router.post("/evaluate")
def evaluate(call: DashboardToolCall):
    req = ToolCallRequest(
        action=call.tool,
        arguments=call.args,
        user_request=call.user_request,
        source_context=call.context,
    )
    response, breakdown = score_request(req)
    log_decision(req, response, breakdown)
    record = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": response.decision,
        "risk_score": response.score / 100,
        "rule_score": breakdown["rule_score"] / 100,
        "ml_score": breakdown["ml_risk"] / 100 if breakdown["ml_risk"] is not None else None,
        "reasons": [response.reason],
        "tool_call": call.model_dump(),
        "status": "pending" if response.decision == "QUARANTINE" else "final",
        "resolved_by": None,
    }
    _records.append(record)
    return _copy(record)


@router.get("/logs")
def logs(limit: int = 100):
    return [_copy(record) for record in reversed(_records[-max(1, min(limit, 500)):])]


@router.get("/quarantine")
def quarantine():
    return [_copy(record) for record in _records if record["status"] == "pending"]


@router.post("/quarantine/{record_id}/{action}")
def review(record_id: str, action: str, reviewer: str = "human"):
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Action must be approve or reject")
    for record in _records:
        if record["id"] == record_id and record["status"] == "pending":
            record["status"] = "approved" if action == "approve" else "rejected"
            record["resolved_by"] = reviewer
            return _copy(record)
    raise HTTPException(status_code=404, detail="No pending action with that id")
