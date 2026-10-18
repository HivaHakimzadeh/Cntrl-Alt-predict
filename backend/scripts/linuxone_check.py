"""Preflight for the LinuxONE box. Run from the project root:

    python scripts/linuxone_check.py                       # checks everything it can find
    python scripts/linuxone_check.py --granite /path/model.gguf

Prints a PASS/FAIL line per item and a short "what to do next" list. Safe to run repeatedly.
"""
import argparse, importlib, os, platform, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
results = []


def report(ok, name, detail="", warn=False):
    """warn=True: a missing OPTIONAL item (shown as a warning, not counted as a failure)."""
    results.append((ok or warn, name))
    print(f"{'✅' if ok else '⚠️ ' if warn else '❌'} {name}" + (f"  -  {detail}" if detail else ""))


def find_gguf(budget_s=15):
    roots = [Path.cwd(), Path.home(), Path("/models"), Path("/opt"), Path("/data"), Path("/mnt"), Path("/srv"),
             Path("/workspace"), Path("/home")]
    skip = {"proc", "sys", "dev", "node_modules", ".git", "site-packages"}
    t0, hits = time.time(), []
    for r in roots:
        if not r.exists():
            continue
        for dirpath, dirs, files in os.walk(r):
            dirs[:] = [d for d in dirs if d not in skip and dirpath.count(os.sep) - str(r).count(os.sep) < 5]
            hits += [str(Path(dirpath) / f) for f in files if f.lower().endswith(".gguf")]
            if time.time() - t0 > budget_s:
                return sorted(set(hits)), True
    return sorted(set(hits)), False


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--granite", help="path to the Granite .gguf")
    args = ap.parse_args()
    if args.granite:
        os.environ["GRANITE_GGUF_PATH"] = args.granite
    todo = []

    print("== Platform ==")
    on_z = platform.machine() == "s390x"
    report(on_z, "architecture is s390x (IBM LinuxONE)", platform.machine())
    report(sys.version_info >= (3, 10), "Python >= 3.10", sys.version.split()[0])
    cpus = os.cpu_count() or 1
    mem = next((int(l.split()[1]) / 1e6 for l in open("/proc/meminfo") if l.startswith("MemTotal")), 0) if Path("/proc/meminfo").exists() else 0
    print(f"   vCPUs: {cpus}   RAM: {mem:.1f} GB   -> suggested GRANITE_THREADS={max(1, cpus)}")

    print("\n== Python packages ==")
    for mod, need in [("pydantic", True), ("numpy", True), ("scipy", True), ("sklearn", True), ("pandas", True),
                      ("matplotlib", True), ("xgboost", False), ("llama_cpp", False), ("pytest", False)]:
        try:
            m = importlib.import_module(mod); report(True, mod, getattr(m, "__version__", "ok"))
        except Exception as e:
            report(False, mod + ("" if need else " (optional)"), f"{type(e).__name__}: {str(e)[:60]}", warn=not need)
            todo.append(f"pip install --user {'scikit-learn' if mod == 'sklearn' else 'llama-cpp-python' if mod == 'llama_cpp' else mod}")

    print("\n== Project: rules + ML + RAG (no Granite needed) ==")
    try:
        from src import demo, ml_model, policy_rag
        report(ml_model.available(), "trained XGBoost model loads from models/", "if this fails on s390x: python -m src.train (sklearn fallback)")
        report(len(policy_rag.load_clauses()) == 13, "policy clauses parsed", f"{len(policy_rag.load_clauses())} clauses")
        os.environ.setdefault("GUARDRAIL_LOG", "/tmp/linuxone_check.jsonl")
        res = {r["id"]: r["decision"] for r in (demo.run_scenario(s) for s in demo.load_scenarios())}
        report(res == {"green": "ALLOW", "red": "BLOCK", "yellow": "QUARANTINE"}, "3 demo scenarios give ALLOW/BLOCK/QUARANTINE", str(res))
    except Exception as e:
        report(False, "project import / demo", f"{type(e).__name__}: {e}")

    print("\n== Granite (generative layer) ==")
    from src import llm_local
    gp = llm_local.granite_path()
    if not gp:
        hits, partial = find_gguf()
        print(f"   searched for *.gguf{' (stopped early)' if partial else ''}: {hits[:5] or 'none found'}")
        if hits:
            todo.append(f"export GRANITE_GGUF_PATH={hits[0]}   # then re-run this script")
        else:
            todo.append("find the Granite .gguf: open the ML Lab 02 notebook and look for the model path (search the notebook for 'gguf')")
        report(False, "GRANITE_GGUF_PATH set and file exists")
    else:
        report(True, "GRANITE_GGUF_PATH set and file exists", str(gp))
        try:
            t0 = time.time()
            raw = llm_local._granite_generate("Reply with only the JSON {\"risk\": 5, \"reason\": \"ok\"}", "Return the JSON now.")
            report(True, "Granite loads and generates", f"{time.time() - t0:.1f}s incl. model load")
            print("   raw output:", raw.strip()[:200])
            try:
                llm_local.parse_verdict(raw); report(True, "output parses as a verdict")
            except Exception as e:
                report(False, "output parses as a verdict", str(e)); todo.append("Granite replied but not in JSON: see runbook section 5 (debugging)")
        except Exception as e:
            report(False, "Granite loads and generates", f"{type(e).__name__}: {str(e)[:120]}")

    print("\n== Summary ==")
    bad = [n for ok, n in results if not ok]
    print("All good." if not bad else f"{len(bad)} item(s) need attention:")
    for n in bad:
        print("  -", n)
    for t in dict.fromkeys(todo):
        print("  next:", t)


if __name__ == "__main__":
    main()
