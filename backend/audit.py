"""Append-only JSONL audit log plus the in-memory quarantine queue."""
import json
import threading
from pathlib import Path
from typing import Optional

from schemas import Evaluation

LOG_PATH = Path(__file__).parent / "logs" / "audit.jsonl"

_lock = threading.Lock()


def write(record: Evaluation, event: str = "evaluated") -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    line = json.dumps({"event": event, **record.model_dump(mode="json")})
    with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read(limit: int = 100) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    return [json.loads(line) for line in reversed(lines[-limit:])]


class QuarantineQueue:
    def __init__(self) -> None:
        self._items: dict[str, Evaluation] = {}

    def add(self, record: Evaluation) -> None:
        self._items[record.id] = record

    def pending(self) -> list[Evaluation]:
        return [r for r in self._items.values() if r.status == "pending"]

    def resolve(self, record_id: str, approved: bool, reviewer: str) -> Optional[Evaluation]:
        record = self._items.get(record_id)
        if record is None or record.status != "pending":
            return None
        record.status = "approved" if approved else "rejected"
        record.resolved_by = reviewer
        write(record, event=record.status)
        return record
