"""Run the demo scenarios through the API.

    pytest -v
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

SCENARIOS = json.loads((Path(__file__).parent / "data" / "scenarios.json").read_text())

client = TestClient(app)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_scenario(scenario):
    result = client.post("/evaluate", json=scenario["call"]).json()
    assert result["decision"] == scenario["expected"], result["reasons"]


def test_quarantine_approval_flow():
    quarantined = next(s for s in SCENARIOS if s["expected"] == "QUARANTINE")
    record = client.post("/evaluate", json=quarantined["call"]).json()

    pending_ids = [r["id"] for r in client.get("/quarantine").json()]
    assert record["id"] in pending_ids

    approved = client.post(f"/quarantine/{record['id']}/approve", params={"reviewer": "tester"}).json()
    assert approved["status"] == "approved"
    assert record["id"] not in [r["id"] for r in client.get("/quarantine").json()]
    assert client.post(f"/quarantine/{record['id']}/reject").status_code == 404
