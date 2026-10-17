# Validation and threshold selection

`backend/data/eval_validation.jsonl` is a fixed, seeded, synthetic validation set. It is separate from `eval_real.jsonl` and the synthetic novel test split. Recreate it only when intentionally changing the validation set:

```sh
cd backend
python3 - <<'PY'
import json
from pathlib import Path
from src import synth
rows = synth.generate(1000, seed=19017, phrasing="novel")
Path("data/eval_validation.jsonl").write_text(
    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
PY
```

Compare ALLOW cutoffs without loading the untouched test split:

```sh
GUARDRAIL_ML=on GUARDRAIL_LLM=off python3 -m src.evaluate --threshold-scan
```

`GUARDRAIL_ALLOW_BELOW` means scores *below* the value are allowed. Raising it creates more holds. `GUARDRAIL_BLOCK_AT` remains 70 during the scan. Mandatory policy decisions (unknown payees, external shares, amount mismatches, user-unrequested actions, and unknown tools) stay enforced separately from the score thresholds.

The validation distribution intentionally includes explicitly requested external shares and user-named unapproved payment accounts as benign intent labels. Those still require review under policy P-4/P-1, so they count as operational interruptions in the false-positive rate. At the current 40 cutoff, inspect the per-category output before treating that rate as a model error. Select a threshold on validation first; only then run evaluation on an untouched test set.
