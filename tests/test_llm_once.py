"""The one-call LLM baseline (D6a), with FakeClient only: no network, no keys, no model.

Synthetic cases (tests/synthetic.py) drive the predictor and runner. The one lab-wide check builds the
evidence bundle for every case in both modes, to show it fits the token budget; it calls no model.
"""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest
import synthetic as s
from conftest import LAB_DIR, TRUTH

from ci_triage.diagnosis import Category, Diagnosis
from ci_triage.evaluation.grader import grade_case
from ci_triage.evaluation.llm_run import EvalCase, make_caller, read_records, run_cases
from ci_triage.llm.calling import CallLedger, ModelCaller, Pacer
from ci_triage.llm.clients import APICallError, FakeClient, GeminiClient, MissingAPIKey, OllamaClient
from ci_triage.llm.config import load_config
from ci_triage.llm.schema import response_schema
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import (_SHRINK_ORDER, MIN_SECTION_CHARS, LLMOncePredictor, gather_bundle,
                                           parse_diagnosis, truncate)
from ci_triage.replay import ReplaySource
from ci_triage.tools import Mode, make_toolkit

LOADED = load_config()
CONFIG = LOADED.config
BUDGET = CONFIG.budget
MAX_OUT = CONFIG.generation.max_output_tokens
KEY = "AIzaTEST-not-a-real-key-0123456789"


def unavailable(n: int = 1, status: int = 503) -> list[Exception]:
    return [APICallError(status, "service unavailable") for _ in range(n)]


def predictor(client: FakeClient, *, ledger: CallLedger | None = None, sleeps: list | None = None,
              secrets: list[str] | None = None) -> LLMOncePredictor:
    caller = ModelCaller(client, temperature=CONFIG.generation.temperature, max_output_tokens=MAX_OUT,
                         retry=CONFIG.retry, context_tokens=BUDGET.context_tokens, ledger=ledger,
                         sleep=(sleeps.append if sleeps is not None else lambda _: None),
                         secrets=secrets if secrets is not None else [])
    return LLMOncePredictor(caller, LOADED, name="test")


def synthetic_cases(*ids: str, mode: Mode = Mode.FULL) -> list[EvalCase]:
    return [EvalCase(i, s.RUN_ID, s.truth(i), lambda: s.toolkit(mode)[0]) for i in ids]


def run_one(client: FakeClient, mode: Mode = Mode.FULL, **kwargs):
    toolkit, source = s.toolkit(mode)
    return predictor(client, **kwargs).run(CaseView(s.RUN_ID), toolkit), toolkit, source


# -- settings and prompt --------------------------------------------------------------------------------


def test_preregistered_settings():
    assert CONFIG.gemini.model == "gemini-3.5-flash-lite"
    assert (CONFIG.ollama.model, CONFIG.ollama.think, CONFIG.ollama.num_ctx, CONFIG.ollama.seed) == \
        ("qwen3.5:4b", False, 8192, 42)
    assert CONFIG.generation.temperature == 0
    assert CONFIG.gemini.max_requests_per_minute == 10 and CONFIG.gemini.daily_call_cap == 450
    assert CONFIG.retry.max_tries == 5 and set(CONFIG.retry.statuses) == {429, 503, 504}
    assert BUDGET.context_tokens == CONFIG.ollama.num_ctx


def test_prompt_uses_nothing_from_the_lab():
    """No case id, target file, failing test or package name of the lab appears in the prompt.

    One exception: "pytest" may appear inside a category definition copied verbatim from the lab's public
    rulebook (the manifest's definitions, the "Definition" column of lab/README.md), and nowhere else.
    """
    text = (LOADED.path.parents[1] / CONFIG.prompt).read_text(encoding="utf-8")
    rulebook = json.loads((LAB_DIR / "injections" / "manifest.json").read_text(encoding="utf-8"))["categories"]
    for definition in rulebook.values():
        if "pytest" in definition.lower():
            text = text.replace(definition, re.sub("(?i)pytest", "<rulebook>", definition))
    assert not re.search(r"\binj-\d+", text)
    for truth in TRUTH.values():
        assert Path(truth.target_file).name not in text, truth.target_file
        for test in truth.expected_failing_tests:
            assert test.split("::")[-1].split("[")[0] not in text, test
    for name in ("pytest", "pip ", "requirements.txt", "Dockerfile", "fastapi", "pydantic", "uvicorn", "httpx"):
        assert name.lower() not in text.lower(), name
    for category in Category:
        assert f"- {category.value}:" in text


def test_ollama_options_and_schema():
    client = OllamaClient.__new__(OllamaClient)  # no connection needed to check the request options
    client.seed, client.num_ctx = CONFIG.ollama.seed, CONFIG.ollama.num_ctx
    assert client.options(0.0, MAX_OUT) == {"temperature": 0.0, "seed": 42, "num_ctx": 8192, "num_predict": MAX_OUT}
    schema = response_schema(Diagnosis)
    assert "$defs" not in str(schema) and "anyOf" not in str(schema)
    assert set(schema["required"]) == set(Diagnosis.model_fields)


# -- one call -------------------------------------------------------------------------------------------


def test_valid_json_takes_one_call_and_is_grounded():
    client = FakeClient.of([s.VALID_DIAGNOSIS])
    outcome, toolkit, _ = run_one(client)
    assert outcome.status == "ok" and not outcome.repaired
    assert [c.purpose for c in outcome.calls] == ["diagnose"]
    call = outcome.calls[0]
    assert (call.requested_model, call.model_version, call.retries, call.attempts) == \
        ("fake-model", "fake-model-001", 0, 1)
    assert call.tokens_in and call.tokens_out and call.latency_s is not None
    sent = client.calls[0]
    assert sent.system == LOADED.prompt.system and sent.temperature == 0 and sent.max_output_tokens == MAX_OUT
    assert sent.schema == response_schema(Diagnosis) and len(sent.messages) == 1
    grade = grade_case(s.truth(), outcome.diagnosis, toolkit.calls)
    assert grade.category_correct and grade.step_correct and grade.job_correct and grade.file_correct
    assert grade.evidence_grounded and grade.fix_match == "exact"


def test_invalid_then_repaired():
    client = FakeClient.of(["The run failed because of a test.", s.VALID_DIAGNOSIS])
    outcome, _, _ = run_one(client)
    assert outcome.status == "ok" and outcome.repaired
    assert [c.purpose for c in outcome.calls] == ["diagnose", "repair"]
    repair = client.calls[1].messages
    assert [m.role for m in repair] == ["user", "assistant", "user"]
    assert repair[1].content == "The run failed because of a test."
    assert "not a valid diagnosis" in repair[2].content and "json" in repair[2].content.lower()


def test_invalid_twice_is_invalid_output_and_scored_wrong(tmp_path):
    client = FakeClient.of(['{"category": "kaput"}', '{"category": "real_regression"}'])
    report = run_cases(predictor(client), synthetic_cases("a"), tmp_path, echo=lambda _: None)
    (record,) = read_records(tmp_path / "cases.jsonl")
    assert record.status == "invalid_output" and len(client.calls) == 2  # one repair, no more
    assert record.result is not None and not record.result.category_correct
    assert record.result.predicted_category is None and record.result.confidence == 0
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "1 invalid_output (scored as wrong)" in summary and "| category accuracy | 0/1 (0.0%) |" in summary
    assert report.remaining == [] and not report.api_errors


def test_parse_accepts_a_fenced_answer_and_rejects_extra_fields():
    assert parse_diagnosis(f"```json\n{s.VALID_DIAGNOSIS}\n```")[0] is not None
    diagnosis, error = parse_diagnosis(s.VALID_DIAGNOSIS.replace('"confidence"', '"mood": 1, "confidence"'))
    assert diagnosis is None and "mood" in error


# -- API errors and retries ------------------------------------------------------------------------------


def test_retryable_error_then_success():
    sleeps: list[float] = []
    outcome, _, _ = run_one(FakeClient.of([*unavailable(1, 429), s.VALID_DIAGNOSIS]), sleeps=sleeps)
    assert outcome.status == "ok" and outcome.calls[0].retries == 1 and sleeps == [5.0]
    assert outcome.calls[0].attempt_errors == ["429: service unavailable"]


def test_api_error_after_five_tries_is_not_scored(tmp_path):
    sleeps: list[float] = []
    client = FakeClient.of([s.VALID_DIAGNOSIS, *unavailable(5, 504)])
    report = run_cases(predictor(client, sleeps=sleeps), synthetic_cases("ok-case", "flaky-api"), tmp_path,
                       echo=lambda _: None)
    ok, failed = read_records(tmp_path / "cases.jsonl")
    assert failed.status == "api_error" and failed.result is None
    assert failed.llm_calls[0].attempts == 5 and failed.llm_calls[0].retries == 4
    assert sleeps == [5.0, 10.0, 20.0, 40.0]
    assert report.api_errors == ["flaky-api"] and report.remaining == ["flaky-api"]
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "**To re-run (api_error):** flaky-api" in summary
    assert "| category accuracy | 1/1 (100.0%) |" in summary  # the api_error case is left out


@pytest.mark.parametrize("status", [400, 401, 500, None])
def test_other_errors_are_not_retried(status):
    outcome, _, _ = run_one(FakeClient.of([APICallError(status, "nope")]))
    assert outcome.status == "api_error" and outcome.calls[0].attempts == 1


def test_api_error_in_the_repair_call():
    outcome, _, _ = run_one(FakeClient.of(["{}", *unavailable(5)]))
    assert outcome.status == "api_error" and [c.purpose for c in outcome.calls] == ["diagnose", "repair"]


# -- resume, pacing and the daily cap --------------------------------------------------------------------


def test_resume_skips_done_cases_and_reruns_api_errors(tmp_path):
    cases = synthetic_cases("a", "b", "c")
    first = FakeClient.of([s.VALID_DIAGNOSIS, *unavailable(5), s.VALID_DIAGNOSIS])
    run_cases(predictor(first), cases, tmp_path, echo=lambda _: None)
    assert [r.status for r in read_records(tmp_path / "cases.jsonl")] == ["ok", "api_error", "ok"]
    with pytest.raises(SystemExit, match="--resume"):
        run_cases(predictor(FakeClient.of([])), cases, tmp_path, echo=lambda _: None)
    second = FakeClient.of([s.VALID_DIAGNOSIS])
    report = run_cases(predictor(second), cases, tmp_path, resume=True, echo=lambda _: None)
    assert len(second.calls) == 1 and report.ran == 1 and report.skipped == 2
    records = read_records(tmp_path / "cases.jsonl")
    assert sorted(r.case_id for r in records) == ["a", "b", "c"] and {r.status for r in records} == {"ok"}
    assert report.remaining == []


def test_daily_cap_stops_cleanly_and_holds_across_runs(tmp_path):
    day = ["2026-11-04"]
    ledger = lambda: CallLedger(tmp_path / "ledger.json", "fake-model", cap=2, today=lambda: day[0])  # noqa: E731
    cases = synthetic_cases("a", "b", "c")
    out = tmp_path / "run"
    report = run_cases(predictor(FakeClient.of([s.VALID_DIAGNOSIS] * 3), ledger=ledger()), cases, out,
                       echo=lambda _: None)
    assert report.ran == 2 and report.remaining == ["c"] and "daily cap of 2" in report.stopped
    assert ledger().used() == 2
    assert "Stopped early" in (out / "summary.md").read_text(encoding="utf-8")
    client = FakeClient.of([s.VALID_DIAGNOSIS])
    again = run_cases(predictor(client, ledger=ledger()), cases, out, resume=True, echo=lambda _: None)
    assert again.ran == 0 and again.stopped and client.calls == []  # same day: no call at all
    day[0] = "2026-11-05"
    final = run_cases(predictor(client, ledger=ledger()), cases, out, resume=True, echo=lambda _: None)
    assert final.ran == 1 and final.remaining == [] and final.stopped is None


def test_cap_counts_retries_and_repairs(tmp_path):
    ledger = CallLedger(tmp_path / "ledger.json", "fake-model", cap=100, today=lambda: "2026-11-04")
    run_one(FakeClient.of([*unavailable(2), "{}", s.VALID_DIAGNOSIS]), ledger=ledger)
    assert ledger.used() == 4


def test_pacer_spaces_attempts_six_seconds_apart():
    now, sleeps = [0.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    pacer = Pacer(10, clock=lambda: now[0], sleep=sleep)
    pacer.wait()
    pacer.wait()
    now[0] += 2.5
    pacer.wait()
    now[0] += 30
    pacer.wait()
    assert sleeps == [6.0, 3.5]


def test_gemini_caller_is_paced_and_capped(tmp_path):
    caller = make_caller(FakeClient.of([], backend="gemini"), LOADED, tmp_path)
    assert caller.pacer is not None and caller.pacer.interval == 6.0
    assert caller.ledger is not None and caller.ledger.cap == 450
    local = make_caller(FakeClient.of([], backend="ollama"), LOADED, tmp_path)
    assert local.pacer is None and local.ledger is None


# -- the bundle ---------------------------------------------------------------------------------------------


def test_log_only_leaves_the_diff_out():
    full, _, full_source = run_one(FakeClient.of([s.VALID_DIAGNOSIS]))
    assert full.diff_included and "=== DIFF [diff]" in full.user_message and "-DAILY_FEE = 0.25" in full.user_message
    assert "compare" in full_source.calls

    outcome, toolkit, source = run_one(FakeClient.of([s.VALID_DIAGNOSIS]), Mode.LOG_ONLY)
    assert not outcome.diff_included and "DIFF" not in outcome.user_message
    assert "DAILY_FEE = 0.25" not in outcome.user_message  # the removed line
    assert not {"compare", "get_commit", "list_runs"} & set(source.calls)
    assert not {"compare", "get_commit", "list_runs"} & {call.tool for call in toolkit.calls}


@pytest.mark.parametrize("mode", list(Mode))
def test_bundle_under_the_token_cap(mode):
    toolkit, _ = s.toolkit(mode, big=True)
    bundle = gather_bundle(CaseView(s.RUN_ID), toolkit, BUDGET, len(LOADED.prompt.system), MAX_OUT)
    prompt = LOADED.prompt.system + bundle.user_message()
    assert BUDGET.estimate_tokens(prompt) + MAX_OUT + BUDGET.reserve_tokens <= BUDGET.context_tokens
    caps = {"RUN AND JOBS": BUDGET.jobs_chars, "FAILURE BLOCK": BUDGET.failure_block_chars,
            "WORKFLOW FILE": BUDGET.workflow_chars, "DIFF": BUDGET.diff_chars}
    for section in bundle.sections:
        assert len(section.text) <= caps[section.title], section.title
    assert "[truncated:" in bundle.section("WORKFLOW FILE").text


@pytest.mark.parametrize("context_tokens", [5000, 4000])  # 5000 cuts the diff only, 4000 the workflow file too
def test_total_is_cut_to_fit_a_smaller_context(context_tokens):
    """Fitting cuts sections in _SHRINK_ORDER: the diff first, and a section only once every section before
    it is down to MIN_SECTION_CHARS. Compared with the same bundle gathered with no room limit."""
    system = len(LOADED.prompt.system)
    roomy = BUDGET.model_copy(update={"context_tokens": 10**6})
    tight = BUDGET.model_copy(update={"context_tokens": context_tokens})
    before = gather_bundle(CaseView(s.RUN_ID), s.toolkit(Mode.FULL, big=True)[0], roomy, system, MAX_OUT)
    after = gather_bundle(CaseView(s.RUN_ID), s.toolkit(Mode.FULL, big=True)[0], tight, system, MAX_OUT)
    assert system + len(after.user_message()) <= tight.prompt_chars(MAX_OUT)
    cut = [len(after.section(t).text) < len(before.section(t).text) for t in _SHRINK_ORDER]
    assert cut[0]  # the diff goes first
    assert cut == sorted(cut, reverse=True)  # the cut sections are a prefix of the order
    for title, later_cut in zip(_SHRINK_ORDER, [any(cut[i + 1:]) for i in range(len(cut))]):
        if later_cut:
            assert len(after.section(title).text) <= MIN_SECTION_CHARS, title


def test_truncate_keeps_whole_lines_within_the_limit():
    text = "\n".join(f"line {i}" for i in range(500))
    cut = truncate(text, 300)
    assert len(cut) <= 300 and cut.endswith("more characters not shown]")
    assert all(line in text.split("\n") for line in cut.split("\n")[:-1])


@pytest.mark.parametrize("mode", list(Mode))
def test_lab_bundles_fit_the_token_cap(case, lab, mode):
    """Every lab case's bundle, in both modes, fits (gathering only: no model is called)."""
    toolkit = make_toolkit(ReplaySource(case, lab), case.eval_commit, mode)
    bundle = gather_bundle(CaseView(case.eval_run_id), toolkit, BUDGET, len(LOADED.prompt.system), MAX_OUT)
    prompt = LOADED.prompt.system + bundle.user_message()
    assert BUDGET.estimate_tokens(prompt) + MAX_OUT + BUDGET.reserve_tokens <= BUDGET.context_tokens
    assert bundle.diff_included == (mode is Mode.FULL)
    assert bundle.section("FAILURE BLOCK") is not None and bundle.section("WORKFLOW FILE") is not None


# -- the API key ------------------------------------------------------------------------------------------


def test_key_never_appears_in_any_output_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    leaky = s.VALID_DIAGNOSIS.replace("The daily late fee", f"Key {KEY}: the daily late fee")
    client = FakeClient.of([f"oops {KEY}", leaky, *[APICallError(403, f"API key {KEY} rejected")]],
                           model_version=f"v-{KEY}")
    caller = make_caller(client, LOADED, tmp_path, daily_cap=50)  # secrets read from the environment
    assert caller.secrets == [KEY]
    run_cases(LLMOncePredictor(caller, LOADED, name="t"), synthetic_cases("a", "b"), tmp_path / "out",
              echo=lambda _: None)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert {p.name for p in files} >= {"cases.jsonl", "summary.md", "llm_call_ledger.json"}
    for path in files:
        assert KEY not in path.read_text(encoding="utf-8"), path
    assert "<redacted>" in (tmp_path / "out" / "cases.jsonl").read_text(encoding="utf-8")


# -- backend adapters, with stub SDK modules ------------------------------------------------------------------


def _stub_genai(monkeypatch, response=None, error=None):
    seen = {}

    class Obj:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class APIError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code, self.status, self.message = code, "UNAVAILABLE", message

    class Models:
        def generate_content(self, **kwargs):
            seen["request"] = kwargs
            if error:
                raise APIError(*error)
            return response

    class Client:
        def __init__(self, api_key, http_options):
            seen["api_key"], seen["http_options"] = api_key, http_options
            self.models = Models()

    gtypes = types.SimpleNamespace(**{name: Obj for name in (
        "HttpOptions", "HttpRetryOptions", "GenerateContentConfig", "ThinkingConfig", "Content", "Part")})
    genai = types.ModuleType("google.genai")
    genai.Client, genai.types, genai.errors = Client, gtypes, types.SimpleNamespace(APIError=APIError)
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return seen, Obj


def test_gemini_adapter_reads_the_key_from_the_environment_only(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    _stub_genai(monkeypatch)
    with pytest.raises(MissingAPIKey):
        GeminiClient("gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    seen, Obj = _stub_genai(monkeypatch, response=types.SimpleNamespace(
        text=s.VALID_DIAGNOSIS, model_version="gemini-3.5-flash-lite-001",
        usage_metadata=types.SimpleNamespace(prompt_token_count=1200, candidates_token_count=180,
                                             thoughts_token_count=0),
        candidates=[types.SimpleNamespace(finish_reason=types.SimpleNamespace(value="STOP"))]))
    client = GeminiClient("gemini-3.5-flash-lite", thinking_level="minimal", seed=42)
    assert seen["api_key"] == KEY and seen["http_options"].retry_options.attempts == 1
    assert KEY not in repr(client) and KEY not in str(vars(client).get("requested_model"))
    out = client.generate("sys", [s_msg("hi")], {"type": "object"}, temperature=0.0, max_output_tokens=MAX_OUT)
    assert (out.model_version, out.tokens_in, out.tokens_out, out.finish_reason) == \
        ("gemini-3.5-flash-lite-001", 1200, 180, "STOP")
    config = seen["request"]["config"]
    assert config.response_mime_type == "application/json" and config.response_json_schema == {"type": "object"}
    assert config.temperature == 0.0 and config.max_output_tokens == MAX_OUT and config.seed == 42


def test_gemini_adapter_maps_errors(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    _stub_genai(monkeypatch, error=(429, "quota"))
    with pytest.raises(APICallError) as caught:
        GeminiClient("m").generate("sys", [s_msg("hi")], {}, temperature=0.0, max_output_tokens=10)
    assert caught.value.status == 429


def test_ollama_adapter(monkeypatch):
    seen = {}

    class ResponseError(Exception):
        def __init__(self, error, status_code=-1):
            super().__init__(error)
            self.error, self.status_code = error, status_code

    class Client:
        def __init__(self, host=None, timeout=None):
            pass

        def chat(self, **kwargs):
            seen.update(kwargs)
            if kwargs["messages"][-1]["content"] == "fail":
                raise ResponseError("model is loading", 503)
            return types.SimpleNamespace(model="qwen3.5:4b", message=types.SimpleNamespace(content="{}"),
                                         prompt_eval_count=2100, eval_count=250, done_reason="stop")

        def list(self):
            return types.SimpleNamespace(models=[types.SimpleNamespace(model="qwen3.5:4b", digest="d8b0f5e9760c99")])

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=Client, ResponseError=ResponseError))
    client = OllamaClient("qwen3.5:4b", think=False, num_ctx=8192, seed=42)
    out = client.generate("sys", [s_msg("hi")], {"type": "object"}, temperature=0.0, max_output_tokens=MAX_OUT)
    assert (out.model_version, out.tokens_in, out.tokens_out) == ("qwen3.5:4b@d8b0f5e9760c", 2100, 250)
    assert seen["think"] is False and seen["format"] == {"type": "object"} and seen["stream"] is False
    assert seen["options"] == {"temperature": 0.0, "seed": 42, "num_ctx": 8192, "num_predict": MAX_OUT}
    assert seen["messages"][0] == {"role": "system", "content": "sys"}
    with pytest.raises(APICallError) as caught:
        client.generate("sys", [s_msg("fail")], {}, temperature=0.0, max_output_tokens=10)
    assert caught.value.status == 503


def s_msg(text: str):
    from ci_triage.llm.clients import Message

    return Message("user", text)
