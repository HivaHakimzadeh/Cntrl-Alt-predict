"""Run the three scripted demo scenarios (works offline thanks to the LLM cache).

    python -m src.demo --seed            # write the hand-authored LLM verdicts into data/llm_cache.json
    python -m src.demo                   # run all three scenarios
    python -m src.demo --scenario red    # run one
"""
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import List

from . import logger, scorer
from .agent import run_agent
from .schema import ToolCallRequest
from .tools import TOOLS

SCENARIOS_PATH = Path(__file__).resolve().parent.parent / "data" / "scenarios.json"


def load_scenarios() -> List[dict]:
    return json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))


def run_scenario(sc: dict, verbose: bool = False) -> dict:
    steps = run_agent(sc["user_request"], sc["source_context"], scripted_calls=sc["scripted_calls"], verbose=verbose)
    decision = steps[-1]["decision"]
    return {"id": sc["id"], "title": sc["title"], "expected": sc["expected"],
            "decision": decision, "passed": decision == sc["expected"], "steps": steps}


def seed_llm_cache() -> int:
    """Pre-load HAND-AUTHORED placeholder verdicts (labelled source=hand-authored) so the demo never waits on a model.
    On LinuxONE, delete data/llm_cache.json to let the local Granite model generate real ones."""
    n = 0
    for sc in load_scenarios():
        if not sc.get("llm_verdict"):
            continue
        for call in sc["scripted_calls"]:
            b = inspect.signature(TOOLS[call["name"]]).bind(**call["arguments"])
            b.apply_defaults()  # identical normalisation to the interceptor -> identical cache key
            req = ToolCallRequest(action=call["name"], arguments=dict(b.arguments),
                                  user_request=sc["user_request"], source_context=sc["source_context"])
            scorer.store_verdict(req, {**sc["llm_verdict"], "source": "hand-authored"})
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--scenario", default="all")
    ap.add_argument("--clear-log", action="store_true")
    args = ap.parse_args()
    if args.seed:
        print(f"Seeded {seed_llm_cache()} cached LLM verdicts -> {scorer.cache_path()}")
        return
    if args.clear_log:
        logger.clear_log()
    results = []
    for sc in load_scenarios():
        if args.scenario in ("all", sc["id"]):
            print("\n" + "=" * 72 + f"\n{sc['title']}  (expected: {sc['expected']})")
            results.append(run_scenario(sc, verbose=True))
    print("\n" + "-" * 72)
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:7s} expected {r['expected']:10s} got {r['decision']}")


if __name__ == "__main__":
    main()
