# Runbook: set up and run the guardrail on LinuxONE Community Cloud (L1CC)

**Read this first: what I know vs. what I don't.**
I only saw two of IBM's slides (the 6-step workflow and the ML Lab 02 slide). The exact portal screens, the name of the pre-built Jupyter image,
whether you get a terminal or SSH, whether the instance can reach the internet, and *where the Granite `.gguf` file lives* are things **I cannot see**.
Steps marked **[CHECK]** depend on that. Where a step fails, copy the error and the output of `python scripts/linuxone_check.py` to me.

What the slides/portal indicate: you register at the L1CC portal with a university email (step 1); IBM provides a pre-built, s390x-compiled Jupyter Docker image with
TensorFlow, PyTorch and scikit-learn (step 2); Labs 01/02 validate your kernel and packages (step 3). ML Lab 02 runs a quantized Granite GGUF with `llama-cpp-python` and uses XGBoost, so
**those two libraries and a model file should already exist in the lab environment [CHECK]**.

---

## 0. Your three scenarios for how you get in. Pick the one that matches what you see

| You have... | Use |
|---|---|
| **A) A Jupyter URL in the browser** (JupyterLab/Notebook) | Sections 1A to 7. Most likely for the datathon. |
| **B) An SSH-able Linux VM + Docker** | Section 1B, then 2 onward (inside the container). |
| **C) Only a VM, no Jupyter image** | `pip install --user jupyterlab`, run it, and tunnel the port (see 1B). Ask the organizers first; A or B is easier. |

## 1A. Browser Jupyter: get the code onto the box
1. In JupyterLab's file browser, click **Upload** and select `blast-radius-guardrail.zip`.
2. Open a terminal: **File → New → Terminal** (if there is no terminal option, use a notebook cell starting with `!` for each command below).
3. Unzip (use Python in case `unzip` is missing):
   ```bash
   python -m zipfile -e blast-radius-guardrail.zip .
   cd blast-radius-guardrail
   ```

## 1B. SSH + Docker (only if you have a VM)
```bash
# from your laptop
scp blast-radius-guardrail.zip <user>@<host>:~/
ssh -L 8888:localhost:8888 <user>@<host>      # the -L forwards Jupyter's port to your laptop

# on the VM
python3 -m zipfile -e blast-radius-guardrail.zip .
docker pull <IBM Jupyter image name from the portal / Lab instructions>    # [CHECK] I don't know the image name
docker run -it --rm -p 8888:8888 \
  -v $PWD/blast-radius-guardrail:/home/jovyan/work/blast-radius-guardrail \
  <that image>
# open the http://127.0.0.1:8888/?token=... URL it prints.  (/home/jovyan/work is the usual Jupyter-stack path;
# if the image differs, the mount target may need changing: check with `docker run --rm <image> pwd`.)
# To use a Granite file that lives on the VM, add another mount:  -v /path/on/vm/models:/models
```
Docker images for this platform must be **s390x**. A plain `x86` image will not run.

## 2. Preflight: one command that checks everything
```bash
cd blast-radius-guardrail
python scripts/linuxone_check.py
```
It reports architecture (**must say `s390x`**), Python, vCPUs/RAM, each package, whether the XGBoost model loads, whether the 3 demo scenarios give
ALLOW / BLOCK / QUARANTINE, whether it can find a Granite `.gguf`, and, if one is set, whether Granite actually loads and returns parseable JSON.
Items marked ⚠️ are optional; ❌ are things to fix. The bottom prints suggested next commands.

## 3. Fix what the preflight flags
| Preflight says | Do this |
|---|---|
| `pydantic` missing | `pip install --user pydantic`. **If this fails** (no wheel for s390x, or no internet), tell me: I can swap the schema to plain dataclasses. |
| `pytest` missing | `pip install --user pytest` |
| `xgboost` missing / won't import | Nothing: run `python -m src.train`; it automatically falls back to scikit-learn. Everything else is unchanged. |
| XGBoost model "fails to load" | `python -m src.train` (retrains in a few seconds on the box and overwrites `models/`) |
| `pip` cannot reach the internet | Ask the organizers whether a package mirror/offline wheels are provided; **don't** assume. |
| `llama_cpp` missing | It should be in the lab image (ML Lab 02 uses it). Try the lab's own kernel/environment. Without it the pipeline still runs (rules + ML + policy RAG); only the Granite layer is skipped. |

## 4. Run the tests and the CLI demo
```bash
pip install --user pytest      # if needed
pytest -q                      # expect: 34 passed
python -m src.demo             # prints the three scenarios; expect PASS x3
```

## 5. Hook up Granite (the generative layer)
**Find the model file [CHECK].** The preflight searches common folders. You can also search yourself:
```bash
find / -name "*.gguf" 2>/dev/null | head
grep -rl "gguf" ~ --include=*.ipynb 2>/dev/null | head        # which lab notebook mentions it; open it and copy the path it uses
```
**Point the project at it** (pick one):
```bash
export GRANITE_GGUF_PATH=/full/path/to/granite.gguf           # in the terminal BEFORE launching Jupyter, or
```
```python
import os; os.environ["GRANITE_GGUF_PATH"] = "/full/path/to/granite.gguf"      # first cell of the notebook, any time before section 6
os.environ["GRANITE_THREADS"] = str(os.cpu_count())
```
Then re-run `python scripts/linuxone_check.py`. You want: ✅ Granite loads and generates, ✅ output parses as a verdict.

**Replace the placeholder verdict.** The yellow scenario currently uses a **hand-authored placeholder** (`data/llm_cache.json`, `source: hand-authored`):
```bash
rm data/llm_cache.json
python -m src.demo --scenario yellow
```
The yellow decision should now cite Granite (`source: granite` in `logs/decisions.jsonl`). The result may differ from QUARANTINE: report what Granite actually says.

**Debugging Granite if the preflight says it can't parse output**
```python
from src import llm_local
raw = llm_local._granite_generate(llm_local.JUDGE_SYSTEM, "<user_request>Share the report</user_request>")
print(raw)                                       # does it return JSON at all?
llm_local.parse_verdict(raw)                     # raises with the reason if not
```
The judge fails *soft*: if Granite errors or returns junk, the system silently uses the rules + ML decision. **So a "working" demo does not prove Granite ran.** Check `llm_source` in the log or the notebook's decision table.
Common causes: the GGUF has no chat template (the model rambles), `n_ctx` too small for long source text (`n_ctx=2048` in `src/llm_local.py`), or the model is a *base*, not *instruct*, version.

## 6. Run the notebook
1. Open `notebooks/guardrail_demo.ipynb` and pick the **Python 3** kernel of the lab image.
2. **Run → Run All Cells.** Takes about a minute. Training is ~2 s. (If a cell errors, stop and send me the traceback.)
3. The first cell must print `machine: s390x` and **"✅ Running on IBM LinuxONE"**. If it says NOT s390x, you are not on the right machine.
4. Section 6 should now say Granite is available (if you set the path). Section 7's table has an `llm` column showing which verdicts were Granite vs placeholder.

## 7. Using the machine's resources (honestly)
* **See what you have:** `nproc`, `free -h`, `lscpu | head -25` (screenshot `lscpu` as platform evidence).
* **Granite speed** scales with threads: set `GRANITE_THREADS` to `nproc`. Time one call in the preflight output. Granite is only called in the 30 to 69 "gray zone", so a handful of calls per demo.
* **XGBoost** is set to 2 threads (`n_jobs=2` in `src/train.py`). Raise it to `nproc` if training matters (it takes ~2 s, so it doesn't).
* **Latency:** notebook section 9 measures decision time on **this** machine. That table is your "real-time on LinuxONE" number *only if* the banner says s390x.
* **Do not claim** Telum/accelerator speed-ups unless you actually measure with and without. Our code doesn't explicitly use them; a given llama.cpp build might, but you'd need to verify.
* **Memory:** the pipeline is small (<~500 MB); Granite's GGUF is the big item. If it fails to load with a memory error, use a smaller quantization.

## 8. Slide step 6: what to screenshot (taken **on LinuxONE**)
1. Section "environment" cell → shows `s390x` and the ✅ banner (+ `lscpu` terminal screenshot).
2. Layer 2 training cell → AUC lines + the ablation conclusion.
3. Section 7 → the three colored decisions (green / red / yellow) and the decision table with the `llm` column.
4. Section 8 → results table and the 3-panel figure.
5. Section 9 → latency table with the machine banner.
6. (If Granite runs) a Granite-sourced yellow decision with its policy-cited reason.
The notebook inside the zip was executed on x86; **its outputs are not LinuxONE evidence.** Re-run it there.

## 9. Troubleshooting
| Symptom | Likely cause / fix |
|---|---|
| `ModuleNotFoundError: src` in the notebook | The first cell sets the path assuming the notebook is in `notebooks/`. Open it from inside the project folder. |
| Kernel dies loading Granite | Out of memory or incompatible GGUF. Try a smaller quantization; check `free -h`. |
| Notebook is slow in section 8 | It scores ~2,000 rows; should take seconds. If minutes, check the box isn't oversubscribed (`top`). |
| `pytest` fails only on s390x | Send me the first failure. Don't edit tests to pass. |
| Results differ slightly from the README | Expected: different library versions/hardware. XGBoost JSON loads across architectures but floating-point details can differ. Quote **your** numbers. |
| Instance gone/reset | Community instances can be time-limited. **Download your work (zip) regularly**, and any results CSVs and screenshots. |

## 10. Before you submit
- [ ] Confirm with the organizers that the code satisfies the "from scratch during the hackathon period" rule.
- [ ] Granite working, `llm_cache.json` regenerated, README status table updated.
- [ ] Screenshots taken on LinuxONE; results quoted from the LinuxONE run, not from the README.
- [ ] No benchmark claim until real AgentDojo/AgentInjectionBench samples are imported.
