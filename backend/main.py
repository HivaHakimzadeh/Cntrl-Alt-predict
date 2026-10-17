import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

import audit
from classifier import InjectionClassifier
from policy import decide
from rules import run_rules
from schemas import Decision, Evaluation, ToolCall

app = FastAPI(title="Agentic Blast-Radius Guardrail")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

classifier = InjectionClassifier()
quarantine = audit.QuarantineQueue()


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/evaluate", response_model=Evaluation)
def evaluate(call: ToolCall) -> Evaluation:
    rule_score, hits = run_rules(call)
    ml_score = classifier.score(call)
    decision, risk, reasons = decide(rule_score, ml_score, hits)

    record = Evaluation(
        id=uuid.uuid4().hex[:12],
        timestamp=datetime.now(timezone.utc).isoformat(),
        decision=decision,
        risk_score=risk,
        rule_score=round(rule_score, 3),
        ml_score=round(ml_score, 3),
        reasons=reasons,
        rule_hits=hits,
        tool_call=call,
        status="pending" if decision == Decision.QUARANTINE else "final",
    )
    if decision == Decision.QUARANTINE:
        quarantine.add(record)
    audit.write(record)
    return record


@app.get("/logs")
def logs(limit: int = 100) -> list[dict]:
    return audit.read(limit)


@app.get("/quarantine", response_model=list[Evaluation])
def list_quarantine() -> list[Evaluation]:
    return quarantine.pending()


def _resolve(record_id: str, approved: bool, reviewer: str) -> Evaluation:
    record = quarantine.resolve(record_id, approved, reviewer)
    if record is None:
        raise HTTPException(status_code=404, detail="No pending quarantined action with that id")
    return record


@app.post("/quarantine/{record_id}/approve", response_model=Evaluation)
def approve(record_id: str, reviewer: str = "human") -> Evaluation:
    return _resolve(record_id, True, reviewer)


@app.post("/quarantine/{record_id}/reject", response_model=Evaluation)
def reject(record_id: str, reviewer: str = "human") -> Evaluation:
    return _resolve(record_id, False, reviewer)
