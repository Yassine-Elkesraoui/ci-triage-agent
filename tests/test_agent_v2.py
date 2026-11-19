"""Agent v2 (phase B, changes a to e), with scripted models only (FakeClient, stub SDKs): no network, no key,
no lab run. The synthetic case (tests/synthetic.py) drives the loop."""

from __future__ import annotations

import json
import re
import sys
import types
from dataclasses import replace

import pytest
import synthetic as s

from ci_triage.agent.config import ContextSettings, LoadedAgentConfig, load_agent_config
from ci_triage.agent.loop import ToolAgent, repeat_prefix, tool_specs
from ci_triage.agent.loop_v2 import (NO_SUBMISSION, NOT_RUN, SUBMIT, ToolAgentV2, is_unparseable_tool_call,
                                     make_agent, no_retry_rule, submit_spec)
from ci_triage.diagnosis import Category, Diagnosis
from ci_triage.evaluation.agent_run import (AgentCaseRecord, AgentCaseRecordV2, check_run_plan, make_agent_caller,
                                            read_agent_records, record_type, run_cases)
from ci_triage.evaluation.llm_run import EvalCase
from ci_triage.labpaths import REPO_ROOT, RESULTS_DIR
from ci_triage.llm.calling import ModelCaller
from ci_triage.llm.clients import APICallError, FakeClient, FakeTurn, GeminiClient, LLMResponse, Message, OllamaClient
from ci_triage.llm.schema import response_schema
from ci_triage.predictors.base import CaseView
from ci_triage.tools import TOOLS, Mode

V1 = load_agent_config()
V2 = load_agent_config(REPO_ROOT / "configs" / "agent_v2.toml")
CONFIG = V2.config
MAX_OUT = CONFIG.generation.max_output_tokens
ANSWER = json.loads(s.VALID_DIAGNOSIS)
SUBMIT_OK = FakeTurn.calling((SUBMIT, ANSWER))
FEES = ("get_file", {"path": "shelfwise/fees.py"})
FEES_LINE = "DAILY_FEE = 0.125"
PARSE_ERROR = "XML syntax error on line 4: element <function> closed by </parameter>"  # Ollama 0.40.16, D6b inj-024


def client(*script, can_force: bool = True, **kwargs) -> FakeClient:
    return FakeClient(script=list(script), can_force_tool_call=can_force, **kwargs)


def make_caller(fake: FakeClient, loaded: LoadedAgentConfig = V2, sleeps: list | None = None) -> ModelCaller:
    return ModelCaller(fake, temperature=CONFIG.generation.temperature, max_output_tokens=MAX_OUT,
                       retry=CONFIG.retry, context_tokens=loaded.config.context.context_tokens,
                       sleep=(sleeps.append if sleeps is not None else lambda _: None), secrets=[],
                       no_retry=no_retry_rule(loaded))


def run_one(fake: FakeClient, mode: Mode = Mode.FULL, loaded: LoadedAgentConfig = V2, sleeps: list | None = None):
    toolkit, _ = s.toolkit(mode)
    return ToolAgentV2(make_caller(fake, loaded, sleeps), loaded, name="test").run(CaseView(s.RUN_ID), toolkit), toolkit


def with_context(**update) -> LoadedAgentConfig:
    context = CONFIG.context.model_copy(update=update)
    return LoadedAgentConfig(CONFIG.model_copy(update={"context": context}), V2.text, V2.path, V2.sha256, V2.prompt,
                             V2.bundle)


def quiet(_: str) -> None:
    pass


# -- settings, files, selection -------------------------------------------------------------------------------


def test_preregistered_v2_settings():
    c = CONFIG
    assert (c.name, c.prompt, c.bundle) == ("agent_v2", "prompts/agent_v2.md", "configs/one_call_v2.toml")
    assert (c.agent.loop, c.agent.max_turns, c.agent.repeated_calls_to_answer) == ("v2", 8, 2)
    assert c.generation == V1.config.generation and c.generation.repair_calls == 1 and c.generation.temperature == 0
    assert c.generation.max_output_tokens == 1536
    assert (c.gemini, c.ollama, c.retry, c.run) == (V1.config.gemini, V1.config.ollama, V1.config.retry, V1.config.run)
    assert (c.context.context_tokens, c.context.reserve_tokens) == (16384, 256) and c.context.use_server_count
    assert c.context.chars_per_token == {"gemini": 1.80, "ollama": 1.50}
    assert (c.context.ratio("gemini"), c.context.ratio("ollama"), c.context.ratio("fake")) == (1.8, 1.5, 1.5)


def test_v1_files_are_unchanged_and_v1_is_still_selectable():
    # the sha256 values agent v1's runs and reports/d6b-agent-v1.md recorded
    assert V1.sha256 == "6923a11809bccb7eaad9950e6ff0aca3e9800ed02d0c701a7fdcd918bc3fe70a"
    assert V1.prompt.sha256 == "87b633e93edbc9bd1126f4bcab6ca81e3f163eeda1fba07b39967d034c358c94"
    assert V1.bundle.sha256 == V2.bundle.sha256 == "74f39cba5f5eb95e018f8d272bb003f0e3f781c1c0a1647585220c7cf07d3290"
    assert (V1.config.agent.loop, V1.config.agent.repeated_calls_to_answer) == ("v1", None)
    assert V1.config.context.chars_per_token == 3.0 and not V1.config.context.use_server_count
    assert V1.prompt.tool_call_error is None
    v1 = make_agent(make_caller(client(), V1), V1)
    v2 = make_agent(make_caller(client()), V2)
    assert type(v1) is ToolAgent and type(v2) is ToolAgentV2 and v2.name.startswith("agent_v2-")
    assert no_retry_rule(V1) is None and no_retry_rule(V2) is is_unparseable_tool_call
    assert make_agent_caller(client(), V1, RESULTS_DIR).no_retry is None
    assert make_agent_caller(client(), V2, RESULTS_DIR).no_retry is is_unparseable_tool_call
    assert (record_type(V1), record_type(V2)) == (AgentCaseRecord, AgentCaseRecordV2)
    with pytest.raises(ValueError):
        ToolAgentV2(make_caller(client(), V1), V1)


def test_v1_and_v2_configs_cannot_mix_settings():
    data = V2.config.model_dump()
    with pytest.raises(ValueError, match="repeated_calls_to_answer"):
        type(CONFIG).model_validate({**data, "agent": {**data["agent"], "repeated_calls_to_answer": None}})
    with pytest.raises(ValueError, match="one chars_per_token per backend"):
        type(CONFIG).model_validate({**data, "context": {**data["context"], "chars_per_token": 3.0}})
    v1 = V1.config.model_dump()
    with pytest.raises(ValueError, match="no server count"):
        type(CONFIG).model_validate({**v1, "context": {**v1["context"], "use_server_count": True}})


# -- the prompt: v1 plus the registered changes ----------------------------------------------------------------

SENTENCE = ('In real_regression, "code" means the application\'s own source code; build files, dependency pins, '
            "test-runner configuration and the workflow belong to their own categories.")

# Every difference from prompts/agent_v1.md, as (v2 text, v1 text): b's sentence, and the wording that change a
# (answer through submit_diagnosis, schema answer where it cannot be forced, the error turn) needs.
V2_EDITS = [
    ("and you answer by submitting one diagnosis of why the run failed.",
     "and you answer with one JSON object that diagnoses why the run failed."),
    (f"\n{SENTENCE}\n", ""),
    ("When you have what you need, call submit_diagnosis with your diagnosis. Calling it ends the case.",
     "When you have what you need, answer as described below, without calling a tool."),
    ("Answer by calling submit_diagnosis. Its arguments are exactly these fields:",
     "Return one JSON object with exactly these fields:"),
    ("On a turn where submit_diagnosis is not offered as a tool, answer with these fields as one JSON object only.",
     "Answer with the JSON object only."),
]


def _text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_prompt_v2_is_v1_with_the_registered_changes_only():
    v1, v2 = _text("prompts/agent_v1.md"), _text("prompts/agent_v2.md")
    system = v2.split("\n<!-- REPAIR MESSAGE", 1)[0]
    for new, old in V2_EDITS:
        assert system.count(new) == 1, new
        system = system.replace(new, old)
    assert system == v1.split("\n<!-- REPAIR MESSAGE", 1)[0]


def test_sentence_b_follows_the_verbatim_definitions():
    v1, v2 = _text("prompts/agent_v1.md"), _text("prompts/agent_v2.md")
    definitions = v1.split("Choose exactly one.\n\n", 1)[1].split("\n\n", 1)[0]
    assert all(f"- {c.value}:" in definitions for c in Category)
    assert f"Choose exactly one.\n\n{definitions}\n\n{SENTENCE}\n\nWhen more than one category fits" in v2
    assert v2.count(SENTENCE) == 1 and SENTENCE in V2.prompt.system


def test_prompt_v2_turn_messages_are_neutral():
    p = V2.prompt
    assert "{errors}" in p.repair and "{error}" in p.tool_call_error
    assert p.tool_call_error_message("E") == "Your last reply could not be used: the server could not parse its " \
                                             "tool call (E). Continue from here."
    neutral = (V2.prompt.system.split("## Tools\n", 1)[1].split("## Answer\n", 1)[0] + p.final_turn + p.repair
               + p.tool_call_error).lower()
    for word in (*(c.value for c in Category), "regression", "flaky", "lab", "inj-", "rules", "should", "first",
                 "always", "never", "likely", "pytest", "docker"):
        assert not re.search(rf"\b{re.escape(word)}\b", neutral), word


# -- a. answer through submit_diagnosis -----------------------------------------------------------------------


@pytest.mark.parametrize("can_force", [True, False])
@pytest.mark.parametrize("mode", list(Mode))
def test_turns_offer_the_modes_tools_plus_submit_diagnosis(mode, can_force):
    fake = client(SUBMIT_OK, can_force=can_force)
    outcome, toolkit = run_one(fake, mode)
    (chat,) = fake.chats
    assert chat.tools == [*tool_specs(toolkit), submit_spec()] and chat.schema is None and chat.force_tool is None
    assert submit_spec().parameters == response_schema(Diagnosis)  # the schema v1's answer turn uses
    assert outcome.status == "ok" and outcome.answered_by == "submit_diagnosis" and outcome.turns_used == 1
    assert outcome.diagnosis == Diagnosis.model_validate(ANSWER) and not outcome.repaired
    assert outcome.turns[0].submitted and outcome.turns[0].tools_offered and outcome.turns[0].submit_offered


def test_tools_then_a_submission():
    fake = client(FakeTurn.calling(FEES), SUBMIT_OK)
    outcome, toolkit = run_one(fake)
    assert outcome.status == "ok" and outcome.turns_used == 2 and [c.name for c in outcome.tool_calls] == ["get_file"]
    assert len(toolkit.calls) == outcome.bundle_tool_calls + 1
    assert [c.purpose for c in outcome.calls] == ["turn", "turn"] and outcome.calls[1].tool_calls == [SUBMIT]


def test_a_submission_ends_the_case_and_the_turns_other_calls_are_not_run():
    fake = client(FakeTurn.calling(FEES, (SUBMIT, ANSWER), ("get_jobs", {"run_id": s.RUN_ID})))
    outcome, toolkit = run_one(fake)
    assert outcome.status == "ok" and outcome.tool_calls == [] and len(toolkit.calls) == outcome.bundle_tool_calls
    assert outcome.turns[0].tool_calls == 3 and outcome.turns[0].submitted


def test_a_turn_without_a_tool_call_or_submission_gets_the_repair_asking_for_one():
    fake = client(s.VALID_DIAGNOSIS, SUBMIT_OK)  # a JSON answer as text is not a submission
    outcome, _ = run_one(fake)
    assert outcome.status == "ok" and outcome.repaired and [c.purpose for c in outcome.calls] == ["turn", "repair"]
    repair = fake.chats[1]
    assert repair.tools == [submit_spec()] and repair.force_tool == SUBMIT and repair.schema is None
    assert repair.messages[-2] == Message("assistant", s.VALID_DIAGNOSIS)
    assert repair.messages[-1] == Message("user", V2.prompt.repair_message(NO_SUBMISSION))


def test_no_submission_after_the_repair_is_invalid_output():
    fake = client("I think it is a regression.", "Still prose.")
    outcome, _ = run_one(fake)
    assert outcome.status == "invalid_output" and NO_SUBMISSION in outcome.error and len(fake.chats) == 2


def test_an_invalid_submission_gets_its_errors_and_one_repair():
    bad = {**ANSWER, "category": "kaput"}
    fake = client(FakeTurn.calling((SUBMIT, bad), FEES), SUBMIT_OK)
    outcome, toolkit = run_one(fake)
    assert outcome.status == "ok" and outcome.repaired and outcome.answered_by == "submit_diagnosis"
    assert len(toolkit.calls) == outcome.bundle_tool_calls  # get_file in the submitting turn was not run
    asked, errors, not_run = fake.chats[1].messages[-3:]
    assert asked.role == "assistant" and [c.name for c in asked.tool_calls] == [SUBMIT, "get_file"]
    assert errors.role == "tool" and errors.tool_name == SUBMIT and errors.tool_call_id == "call-0"
    assert errors.content.startswith("Your diagnosis was not accepted: category:")
    assert not_run.tool_name == "get_file" and NOT_RUN in not_run.content


def test_invalid_twice_is_invalid_output():
    fake = client(FakeTurn.calling((SUBMIT, {"category": "kaput"})), FakeTurn.calling((SUBMIT, "not an object")))
    outcome, _ = run_one(fake)
    assert outcome.status == "invalid_output" and "not a JSON object" in outcome.error


def test_the_answer_turn_forces_submit_diagnosis_where_the_client_can():
    fake = client(*[FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "offset": i})) for i in range(7)], SUBMIT_OK)
    outcome, _ = run_one(fake)
    assert outcome.status == "ok" and outcome.turns_used == 8 and outcome.max_turns_forced
    last = fake.chats[7]
    assert last.tools == [submit_spec()] and last.force_tool == SUBMIT and last.schema is None
    assert last.messages[-1] == Message("user", V2.prompt.final_turn)
    assert outcome.turns[7].forced == "max_turns" and outcome.turns[7].submit_offered
    assert not outcome.turns[7].tools_offered and outcome.calls[7].purpose == "answer"


def test_the_answer_turn_is_the_schema_answer_where_the_client_cannot_force_a_call():
    turns = [FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "offset": i})) for i in range(7)]
    fake = client(*turns, s.VALID_DIAGNOSIS, can_force=False)
    outcome, _ = run_one(fake)
    assert all(c.tools is not None and submit_spec() in c.tools and c.force_tool is None for c in fake.chats[:7])
    last = fake.chats[7]
    assert last.tools is None and last.schema == response_schema(Diagnosis) and last.force_tool is None
    assert outcome.status == "ok" and outcome.answered_by == "schema" and not outcome.turns[7].submit_offered


def test_the_schema_answer_gets_a_schema_repair():
    fake = client(*[FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID, "x": i})) for i in range(7)],
                  '{"category": "kaput"}', s.VALID_DIAGNOSIS, can_force=False)
    outcome, _ = run_one(fake)
    assert outcome.status == "ok" and outcome.repaired and outcome.answered_by == "schema"
    repair = fake.chats[8]
    assert repair.tools is None and repair.schema == response_schema(Diagnosis)
    assert repair.messages[-1].content.startswith("Your diagnosis was not accepted:")


# -- c. repeated calls end the search --------------------------------------------------------------------------


def test_the_second_repeated_call_makes_the_next_turn_the_answer_turn():
    jobs = FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID}))
    fake = client(jobs, jobs, jobs, SUBMIT_OK)
    outcome, toolkit = run_one(fake)
    assert outcome.repeated_calls == 2 and outcome.turns_used == 4 and outcome.repeat_forced
    assert [t.forced for t in outcome.turns] == [None, None, None, "repeat"]
    assert not outcome.max_turns_forced and not outcome.context_forced
    assert fake.chats[3].force_tool == SUBMIT and fake.chats[3].messages[-1].content == V2.prompt.final_turn
    results = [m.content for m in fake.chats[3].messages if m.role == "tool"]
    assert results[1:] == [f"{repeat_prefix(1)}\n{results[0]}"] * 2  # answered from the earlier result, as in v1
    assert len(toolkit.calls) == outcome.bundle_tool_calls + 1


def test_one_repeated_call_does_not_force_the_answer():
    jobs = FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID}))
    fake = client(jobs, jobs, FakeTurn.calling(FEES), SUBMIT_OK)
    outcome, _ = run_one(fake)
    assert outcome.repeated_calls == 1 and not outcome.repeat_forced and outcome.turns_used == 4
    assert all(t.forced is None for t in outcome.turns)


def test_two_repeats_in_one_turn_force_the_next_turn():
    jobs = ("get_jobs", {"run_id": s.RUN_ID})
    fake = client(FakeTurn.calling(jobs, jobs, jobs), SUBMIT_OK)
    outcome, _ = run_one(fake)
    assert outcome.repeated_calls == 2 and outcome.turns[1].forced == "repeat" and outcome.repeat_forced


# -- d. unparseable tool calls ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("status", "message", "matches"), [
    (500, PARSE_ERROR, True),  # the D6b text, as OllamaClient raised it
    (500, "error parsing tool call: raw='{\"name\": \"get_file\"', err=unexpected end of JSON input", True),
    (500, "qwen3.5 tool call parsing failed", True),
    (500, "xml syntax error on line 1: unexpected EOF", True),
    (None, PARSE_ERROR, False),  # not a server answer
    (500, "Internal Server Error", False),
    (503, "service unavailable", False),
    (500, "model requires more system memory (5.6 GiB) than is available (3.1 GiB)", False),
    (400, "INVALID_ARGUMENT Request contains an invalid argument.", False),
    (504, "client-side timeout (ReadTimeout)", False),
])
def test_the_unparseable_tool_call_rule(status, message, matches):
    assert is_unparseable_tool_call(APICallError(status, message)) is matches


def test_an_unparseable_tool_call_is_not_retried_and_becomes_an_error_turn():
    sleeps: list[float] = []
    fake = client(APICallError(500, PARSE_ERROR), SUBMIT_OK)
    outcome, _ = run_one(fake, sleeps=sleeps)
    assert sleeps == [] and outcome.calls[0].attempts == 1 and outcome.calls[0].retries == 0
    assert outcome.status == "ok" and outcome.turns_used == 2 and outcome.unparseable_tool_calls == 1
    assert outcome.turns[0].unparseable_tool_call == PARSE_ERROR and outcome.turns[1].forced is None
    shown = fake.chats[1].messages
    assert shown[-1] == Message("user", V2.prompt.tool_call_error_message(PARSE_ERROR))
    assert [m.role for m in shown] == ["user", "user"]


def test_a_second_unparseable_tool_call_is_invalid_output():
    fake = client(APICallError(500, PARSE_ERROR), FakeTurn.calling(FEES), APICallError(500, PARSE_ERROR))
    outcome, _ = run_one(fake)
    assert outcome.status == "invalid_output" and outcome.unparseable_tool_calls == 2 and outcome.turns_used == 3
    assert "could not parse a tool call twice" in outcome.error and outcome.diagnosis is None


def test_an_unparseable_tool_call_on_the_answer_turn_goes_to_the_repair_call():
    turns = [FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "offset": i})) for i in range(7)]
    fake = client(*turns, APICallError(500, PARSE_ERROR), SUBMIT_OK)
    outcome, _ = run_one(fake)
    assert outcome.status == "ok" and outcome.repaired and outcome.turns_used == 8
    assert PARSE_ERROR in fake.chats[8].messages[-1].content and fake.chats[8].force_tool == SUBMIT


def test_other_server_errors_keep_the_v1_retry_rules():
    sleeps: list[float] = []
    fake = client(APICallError(500, "Internal Server Error"), SUBMIT_OK)
    outcome, _ = run_one(fake, sleeps=sleeps)
    assert outcome.status == "ok" and sleeps == [5.0] and outcome.calls[0].retries == 1
    fake = client(*[APICallError(503, "service unavailable")] * 5)
    outcome, _ = run_one(fake)
    assert outcome.status == "api_error" and outcome.calls[0].attempts == 5


# -- e. the context estimate ------------------------------------------------------------------------------------


def _chars(messages) -> int:
    return sum(len(m.content) + len(json.dumps([c.arguments for c in m.tool_calls], default=str)) for m in messages)


def test_the_first_turn_is_estimated_and_later_turns_start_from_the_servers_count():
    fake = client(FakeTurn.calling(FEES), SUBMIT_OK)
    outcome, toolkit = run_one(fake)
    tools = [*tool_specs(toolkit), submit_spec()]
    first, second = outcome.turns
    whole = len(V2.prompt.system) + len(json.dumps([t.__dict__ for t in tools])) + _chars(fake.chats[0].messages)
    assert not first.server_count_used and first.prompt_tokens_estimate == CONFIG.context.tokens_for(whole, "fake")
    new = fake.chats[1].messages[len(fake.chats[0].messages):]
    assert second.server_count_used
    assert second.prompt_tokens_estimate == outcome.calls[0].tokens_in + CONFIG.context.tokens_for(_chars(new), "fake")


class _Counting(FakeClient):
    """A FakeClient whose server counts are ``count`` (its own counts are a quarter of the characters)."""

    count: int = 0

    def chat(self, *args, **kwargs) -> LLMResponse:
        return replace(super().chat(*args, **kwargs), tokens_in=self.count)


def test_the_servers_count_can_force_the_answer_when_the_estimate_alone_would_not():
    room = CONFIG.context.room(MAX_OUT)
    fake = _Counting(script=[FakeTurn.calling(FEES), SUBMIT_OK])
    fake.count = room - 10  # the server says the conversation is nearly full
    outcome, _ = run_one(fake)
    assert outcome.turns[1].forced == "context" and outcome.context_forced and outcome.turns[1].server_count_used
    small = _Counting(script=[FakeTurn.calling(FEES), FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID})), SUBMIT_OK])
    small.count = 1000
    outcome, _ = run_one(small)
    assert [t.forced for t in outcome.turns] == [None, None, None]


class _Counts(FakeClient):
    """A FakeClient whose server counts are the given ones, call by call."""

    counts: list[int] = []

    def chat(self, *args, **kwargs) -> LLMResponse:
        return replace(super().chat(*args, **kwargs), tokens_in=self.counts.pop(0))


def _big_log_run(loaded: LoadedAgentConfig, counts: list[int] | None, *script):
    fake = _Counts(script=list(script)) if counts is not None else client(*script)
    if counts is not None:
        fake.counts = list(counts)
    toolkit, _ = s.toolkit(Mode.FULL, big=True)
    return ToolAgentV2(make_caller(fake, loaded), loaded).run(CaseView(s.RUN_ID), toolkit), fake


BIG_PAGE = FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "max_lines": 1000}))


def _first_estimate() -> int:
    return _big_log_run(V2, None, BIG_PAGE, SUBMIT_OK)[0].turns[0].prompt_tokens_estimate


def _room(room: int) -> LoadedAgentConfig:
    return with_context(context_tokens=room + CONFIG.context.reserve_tokens + MAX_OUT)


def test_dropped_results_and_the_servers_count():
    first = _first_estimate()
    loaded = _room(first + 200)  # turn 1 fits; the big log page does not
    outcome, fake = _big_log_run(loaded, [first, first], BIG_PAGE, SUBMIT_OK)
    second = outcome.turns[1]
    assert second.forced == "context" and second.dropped_tool_calls == 1 and not outcome.tool_calls[0].shown
    # what is left extends the prompt the server counted on turn 1, so its count still applies
    sent = fake.chats[1].messages
    assert [m.role for m in sent] == ["user", "user"] and sent[-1].content == V2.prompt.final_turn
    assert second.server_count_used
    assert second.prompt_tokens_estimate == outcome.calls[0].tokens_in + CONFIG.context.tokens_for(
        _chars(sent[-1:]), "fake")


def test_a_conversation_shorter_than_the_counted_one_is_estimated_whole():
    # two exchanges, then both dropped: the conversation left is shorter than the one counted on turn 2
    first = _first_estimate()
    room = first + 1000
    small = FakeTurn.calling(("get_run", {"run_id": s.RUN_ID}))
    outcome, fake = _big_log_run(_room(room), [first, room + 100, 0], small, BIG_PAGE, SUBMIT_OK)
    third = outcome.turns[2]
    assert third.forced == "context" and third.dropped_tool_calls == 2 and not third.server_count_used
    whole = len(V2.prompt.system) + len(json.dumps([submit_spec().__dict__])) + _chars(fake.chats[2].messages)
    assert third.prompt_tokens_estimate == CONFIG.context.tokens_for(whole, "fake")


def test_per_backend_ratios():
    settings = ContextSettings(context_tokens=10, reserve_tokens=0, chars_per_token={"gemini": 2.0, "ollama": 1.5})
    assert (settings.estimate_tokens("x" * 10, "gemini"), settings.estimate_tokens("x" * 10, "ollama")) == (5, 7)
    assert settings.estimate_tokens("x" * 10) == 7  # unknown backend: the lowest ratio
    assert ContextSettings(context_tokens=10, reserve_tokens=0, chars_per_token=3.0).estimate_tokens("x" * 10) == 4


def test_the_chars_per_token_reproduce_from_the_saved_v1_runs():
    if not all((RESULTS_DIR / f"agent_v1-{b}{m}" / "cases.jsonl").is_file() for b in ("gemini", "ollama")
               for m in ("", "-log-only")):
        pytest.skip("the agent v1 runs are not saved here (results/ is gitignored)")
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import measure_chars_per_token as m

    for backend, value in CONFIG.context.chars_per_token.items():
        _, cases = m.samples(backend)
        assert int(min(c / t for c, t in cases.values()) * 20) / 20 == value


# -- the runner: v2 records and summary ---------------------------------------------------------------------------


def synthetic_cases(*ids: str, mode: Mode = Mode.FULL) -> list[EvalCase]:
    return [EvalCase(i, s.RUN_ID, s.truth(i), lambda: s.toolkit(mode)[0]) for i in ids]


def test_records_and_summary_of_a_v2_run(tmp_path):
    seen = {**ANSWER, "evidence": [{"quote": "E       assert 2.5 == 5.0", "source": "log"},
                                    {"quote": FEES_LINE, "source": "file"}]}
    jobs = FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID}))
    fake = client(FakeTurn.calling(FEES), jobs, jobs, jobs, FakeTurn.calling((SUBMIT, seen)),
                  APICallError(500, PARSE_ERROR), SUBMIT_OK)
    agent = ToolAgentV2(make_caller(fake), V2, name="t")
    run_cases(agent, synthetic_cases("a", "b", mode=Mode.LOG_ONLY), tmp_path, mode=Mode.LOG_ONLY, echo=quiet, env={})
    a, b = read_agent_records(tmp_path / "cases.jsonl", AgentCaseRecordV2)
    assert (a.repeat_forced, a.answered_by, a.turns[-1].forced, a.unparseable_tool_calls) == \
        (True, "submit_diagnosis", "repeat", 0)
    assert (a.result.quotes, a.result.quotes_grounded) == (2, 2)  # the file quote came from get_file
    assert (b.status, b.unparseable_tool_calls, b.turns[0].unparseable_tool_call) == ("ok", 1, PARSE_ERROR)
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    for row in ("| answered by submit_diagnosis / by the schema answer | 2 / 0 |",
                "| answer forced by repeated calls (repeat_forced) | 1 |",
                "| tool calls the server could not parse (cases) | 1 (1) |", "agent_v2.toml"):
        assert row in summary, row
    # resume reads the v2 records back
    report = run_cases(ToolAgentV2(make_caller(client()), V2), synthetic_cases("a", "b", mode=Mode.LOG_ONLY),
                       tmp_path, resume=True, mode=Mode.LOG_ONLY, echo=quiet, env={})
    assert report.skipped == 2 and report.ran == 0


def test_a_v1_summary_has_no_v2_rows(tmp_path):
    agent = ToolAgent(make_caller(client(s.VALID_DIAGNOSIS), V1), V1, name="t")
    run_cases(agent, synthetic_cases("a"), tmp_path, echo=quiet, env={})
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "submit_diagnosis" not in summary and "repeat_forced" not in summary
    assert '"answered_by"' not in (tmp_path / "cases.jsonl").read_text(encoding="utf-8")


def test_the_v2_run_plan_is_enforced(tmp_path):
    check_run_plan(V2, "gemini", "log-only", tmp_path, 1)
    with pytest.raises(SystemExit, match="finish gemini log-only first"):
        check_run_plan(V2, "gemini", "full", tmp_path, 1)


# -- backend adapters ---------------------------------------------------------------------------------------------


def test_gemini_forces_submit_diagnosis_with_function_calling_mode_any(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTEST-v2-not-a-real-key-0123456789")
    seen = {}

    class Obj:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Models:
        def generate_content(self, **kwargs):
            seen["request"] = kwargs
            call = types.SimpleNamespace(name=SUBMIT, args=ANSWER, id="c1")
            part = types.SimpleNamespace(text=None, thought=None, function_call=call, thought_signature=None)
            return types.SimpleNamespace(model_version="m", usage_metadata=None, candidates=[types.SimpleNamespace(
                finish_reason=None, content=types.SimpleNamespace(parts=[part]))])

    names = ("HttpOptions", "HttpRetryOptions", "GenerateContentConfig", "ThinkingConfig", "Content", "Part", "Tool",
             "FunctionDeclaration", "AutomaticFunctionCallingConfig", "FunctionCall", "FunctionResponse",
             "ToolConfig", "FunctionCallingConfig")
    genai = types.ModuleType("google.genai")
    genai.Client = lambda api_key, http_options: types.SimpleNamespace(models=Models())
    genai.types = types.SimpleNamespace(**{n: Obj for n in names})
    genai.errors = types.SimpleNamespace(APIError=type("APIError", (Exception,), {}))
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    gemini = GeminiClient("gemini-3.5-flash-lite", thinking_level="minimal", seed=42)
    assert gemini.can_force_tool_call
    out = gemini.chat("sys", [Message("user", "bundle")], tools=[submit_spec()], schema=None, temperature=0.0,
                      max_output_tokens=MAX_OUT, force_tool=SUBMIT)
    config = seen["request"]["config"]
    calling = config.tool_config.function_calling_config
    assert (calling.mode, calling.allowed_function_names) == ("ANY", [SUBMIT])
    assert config.tools[0].function_declarations[0].parameters_json_schema == response_schema(Diagnosis)
    assert out.tool_calls[0].name == SUBMIT and out.tool_calls[0].arguments == ANSWER
    gemini.chat("sys", [Message("user", "bundle")], tools=[submit_spec()], schema=None, temperature=0.0,
                max_output_tokens=MAX_OUT)
    assert not hasattr(seen["request"]["config"], "tool_config")  # v1's requests are unchanged


def test_ollama_cannot_force_a_tool_call(monkeypatch):
    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=lambda host=None, timeout=None: None,
                                                                     ResponseError=RuntimeError))
    ollama = OllamaClient("qwen3.5:4b")
    assert not ollama.can_force_tool_call
    with pytest.raises(ValueError):
        ollama.chat("sys", [], tools=None, schema=None, temperature=0.0, max_output_tokens=1, force_tool=SUBMIT)


def test_submit_diagnosis_is_not_a_toolkit_tool():
    assert SUBMIT not in TOOLS and submit_spec().name == SUBMIT
