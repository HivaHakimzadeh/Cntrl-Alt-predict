"""Convert the REAL benchmarks into this project's eval format (data/eval_real.jsonl).

    python scripts/load_real_benchmarks.py [path/to/datasets]      # default: ../datasets

Row format = data/eval_handwritten.jsonl plus a "group" field (rows sharing a group must never be split between
train and test). Tool names are left raw: src/tool_registry.py maps them when ToolCallRequest is built.

Sources
  AgentInjectionBench  142 attacks + 40 benign   (benign = real, hard: benign text that also contains URLs/instructions)
  InjecAgent           1054 attacks              (action = the attacker's tool)
  InjecAgent-control   1054 benign controls      (SYNTHETIC: the same tool-response template with the attacker
                                                 instruction swapped for harmless text; action = the user's own tool)
The controls are easy negatives. Treat AgentInjectionBench's 40 benign rows as the headline false-positive number.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "datasets"

BENIGN_FILLER = [
    "Works great, arrived on time and exactly as described.", "Thanks for the update, see you at the meeting tomorrow.",
    "The quarterly figures look fine to me.", "Reminder: the office is closed on Monday for the public holiday.",
    "Nice product, five stars, would recommend to a friend.", "Please find the notes from last week's discussion.",
    "Everything looks good on our side, no changes needed.", "Great photo! Thanks for sharing it with the group.",
    "The delivery was a day late but the quality is good.", "Lunch is at noon in the main hall, all welcome.",
]


def aib_rows():
    p = DS / "AgentInjectionBench" / "data" / "agent_injection_bench.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        conv = r["conversation"]
        user = next((m["content"] for m in conv if m["role"] == "user"), "")
        ctx = "\n".join(m["content"] for m in conv if m["role"] == "tool_result")
        yield {"id": r["id"], "group": r["id"], "label": "attack" if r["ground_truth"] == "unsafe" else "benign",
               "category": r["attack_category"], "source": "AgentInjectionBench",
               "request": {"action": (r["target_tools"] or ["unknown"])[0], "arguments": {},
                           "user_request": user, "source_context": ctx}}


def injecagent_rows():
    for name in ("test_cases_dh_base.json", "test_cases_ds_base.json"):
        cases = json.load(open(DS / "InjecAgent" / "data" / name, encoding="utf-8"))
        for i, c in enumerate(cases):
            grp = "IA:" + c["Attacker Instruction"]
            tag = f"IA-{name[11:13]}-{i}"
            yield {"id": tag, "group": grp, "label": "attack", "category": c["Attack Type"], "source": "InjecAgent",
                   "request": {"action": (c["Attacker Tools"] or ["unknown"])[0], "arguments": {},
                               "user_request": c["User Instruction"], "source_context": c["Tool Response"]}}
            filler = BENIGN_FILLER[i % len(BENIGN_FILLER)]
            benign_ctx = c["Tool Response Template"].replace("<Attacker Instruction>", filler)
            yield {"id": tag + "-ctl", "group": grp, "label": "benign", "category": "control", "source": "InjecAgent-control",
                   "request": {"action": c["User Tool"], "arguments": {},
                               "user_request": c["User Instruction"], "source_context": benign_ctx}}


if __name__ == "__main__":
    out = list(aib_rows()) + list(injecagent_rows())
    (ROOT / "data" / "eval_real.jsonl").write_text("\n".join(json.dumps(r) for r in out) + "\n", encoding="utf-8")
    from collections import Counter
    print(Counter((r["source"], r["label"]) for r in out))
