"""Tests for the ML, retrieval and generative layers, and for the synthetic data generator."""
import json

import pytest

from src import demo, interceptor, llm_local, logger, ml_model, policy_rag, synth
from src.features import DENSE_NAMES, featurize, make_vectorizer
from src.rules import rule_score
from src.schema import ToolCallRequest
from src.scorer import fold, score_request

HAVE_MODEL = (ml_model.models_dir() / "meta.json").exists()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GUARDRAIL_LOG", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("GUARDRAIL_LLM_CACHE", str(tmp_path / "cache.json"))
    # Pin thresholds so a user session env (e.g. ALLOW_BELOW=50) cannot break gray-zone tests
    monkeypatch.setenv("GUARDRAIL_ALLOW_BELOW", "40")
    monkeypatch.setenv("GUARDRAIL_BLOCK_AT", "70")
    monkeypatch.delenv("GUARDRAIL_ML", raising=False)
    monkeypatch.delenv("GUARDRAIL_LLM", raising=False)
    monkeypatch.delenv("GRANITE_GGUF_PATH", raising=False)
    monkeypatch.delenv("GUARDRAIL_ALLOW_EXTERNAL_LLM", raising=False)
    ml_model.reset()
    llm_local.set_generator(None)
    interceptor.QUARANTINE_QUEUE.clear()
    yield
    llm_local.set_generator(None)
    ml_model.reset()


def req(action, args, ur, ctx=""):
    return ToolCallRequest(action=action, arguments=args, user_request=ur, source_context=ctx)


def scenario(sid):
    return next(s for s in demo.load_scenarios() if s["id"] == sid)


# ---- synthetic data ------------------------------------------------------
def test_synth_is_deterministic_and_balanced():
    a, b = synth.generate(200, seed=3), synth.generate(200, seed=3)
    assert a == b
    assert sum(r["label"] == "attack" for r in a) == 100


def test_train_and_novel_phrasings_differ():
    tr = {r["request"]["source_context"] for r in synth.generate(400, seed=1, phrasing="train")}
    no = {r["request"]["source_context"] for r in synth.generate(400, seed=1, phrasing="novel")}
    assert any("Per the compliance team" in c for c in no) and not any("Per the compliance team" in c for c in tr)


# ---- features / ML -------------------------------------------------------
def test_feature_vector_shape_matches_names():
    vec = make_vectorizer().fit([r["request"]["source_context"] for r in synth.generate(300, seed=2)])
    r = req("wire_transfer", {"to": "ACCT-1", "amount": 5}, "Pay my bill.", "hello world")
    rs, sig = rule_score(r)
    X = featurize([r], [sig], [rs], vec)
    assert X.shape[1] == len(DENSE_NAMES) + len(vec.vocabulary_)


@pytest.mark.skipif(not HAVE_MODEL, reason="models/ not trained")
def test_model_separates_obvious_cases():
    green, red = scenario("green"), scenario("red")
    def risk(sc):
        c = sc["scripted_calls"][0]
        q = req(c["name"], {"currency": "USD", **c["arguments"]}, sc["user_request"], sc["source_context"])
        rs, sig = rule_score(q)
        return ml_model.predict_risk(q, sig, rs)
    assert risk(green) < 30 and risk(red) > 70


def test_missing_model_falls_back_to_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("GUARDRAIL_MODELS", str(tmp_path / "nope"))
    ml_model.reset()
    resp, bd = score_request(req("delete_file", {"path": "/x"}, "hello"))
    assert bd["ml_used"] is False and resp.reason


# ---- fold rule -----------------------------------------------------------
def test_fold_asymmetry():
    assert fold(40, 100) == 76 and fold(100, 0) == 75


# ---- policy RAG ----------------------------------------------------------
def test_bm25_retrieves_relevant_clauses():
    sc = scenario("red"); c = sc["scripted_calls"][0]
    q = req(c["name"], c["arguments"], sc["user_request"], sc["source_context"])
    _, sig = rule_score(q)
    ids = [x["id"] for x in policy_rag.retrieve(q, sig)]
    assert {"P-1", "P-9"} & set(ids)
    q2 = req("delete_file", {"path": "/srv/backups/x.sql"}, "Clean up my downloads.")
    assert "P-7" in [x["id"] for x in policy_rag.retrieve(q2, rule_score(q2)[1])]


# ---- generative layer ----------------------------------------------------
def test_parse_verdict_handles_wrapped_json_and_clamps():
    v = llm_local.parse_verdict('Sure! {"risk": 140, "reason": "bad", "injection_evidence": true} Hope that helps')
    assert v["risk"] == 100 and v["injection_evidence"] is True


def test_judge_only_runs_in_gray_zone_and_uses_fold(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_ML", "off")
    calls = []
    llm_local.set_generator(lambda s, u: calls.append(u) or '{"risk": 90, "matches_user_request": false, '
                            '"injection_evidence": true, "reason": "External edit access conflicts with P-4."}')
    sc = scenario("yellow"); c = sc["scripted_calls"][0]
    resp, bd = score_request(req(c["name"], c["arguments"], sc["user_request"], sc["source_context"]))
    assert bd["llm_used"] and resp.score == fold(bd["rule_score"], 90) and "P-4" in resp.reason and "<policy_clauses>" in calls[0]
    calls.clear()                                  # clearly-low and clearly-high calls never reach the judge
    score_request(req("read_file", {"path": "/home/user/notes.txt"}, "Read notes.txt."))
    sc = scenario("red"); c = sc["scripted_calls"][0]
    score_request(req(c["name"], c["arguments"], sc["user_request"], sc["source_context"]))
    assert calls == []


def test_external_llm_is_off_by_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm_local.backend() is None             # a key alone must NOT send data off the machine


# ---- end to end with every layer on -------------------------------------
@pytest.mark.skipif(not HAVE_MODEL, reason="models/ not trained")
@pytest.mark.parametrize("sid", ["green", "yellow", "red"])
def test_full_pipeline_scenarios(sid):
    demo.seed_llm_cache()                          # writes the hand-authored yellow verdict into the tmp cache
    assert demo.run_scenario(scenario(sid))["decision"] == scenario(sid)["expected"]
    row = json.loads(logger.log_path().read_text().splitlines()[-1])
    assert row["ml_used"] and row["reason"]