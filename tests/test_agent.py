"""The D6b tool-using agent, with scripted models only (FakeClient, stub SDKs): no network, no key, no lab run.

The synthetic case (tests/synthetic.py) drives the loop; lab cases are used only to check that the first
user turn is exactly the one-call bundle.
"""

from __future__ import annotations

import ast
import json
import re
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest
import synthetic as s
from conftest import CASES, LAB

from ci_triage.agent.config import ContextSettings, LoadedAgentConfig, load_agent_config
from ci_triage.agent.loop import AgentToolCall, ToolAgent, repeat_prefix, tool_specs
from ci_triage.diagnosis import Category, Diagnosis, EvidenceSource
from ci_triage.evaluation.agent_run import (AgentCaseRecord, check_run_plan, make_agent_caller, read_agent_records,
                                            run_cases, run_directory, seen_sections)
from ci_triage.evaluation.llm_run import EvalCase
from ci_triage.labpaths import REPO_ROOT, RESULTS_DIR
from ci_triage.llm.calling import CallLedger, ModelCaller
from ci_triage.llm.clients import (APICallError, FakeClient, FakeTurn, GeminiClient, Message, OllamaClient,
                                   ToolCallRequest, ToolSpec, gemini_contents, ollama_messages)
from ci_triage.llm.schema import response_schema
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import gather_bundle
from ci_triage.replay import ReplaySource
from ci_triage.tools import LOG_ONLY_REASON, TOOLS, Mode, make_toolkit, render

LOADED = load_agent_config()
CONFIG = LOADED.config
MAX_OUT = CONFIG.generation.max_output_tokens
KEY = "AIzaTEST-agent-not-a-real-key-0123456789"
ANSWER = s.VALID_DIAGNOSIS
FEES_LINE = "DAILY_FEE = 0.125"  # in shelfwise/fees.py; not in the log-only bundle


def unavailable(n: int = 1, status: int = 503) -> list[Exception]:
    return [APICallError(status, "service unavailable") for _ in range(n)]


def make_agent(client: FakeClient, *, loaded: LoadedAgentConfig = LOADED, ledger: CallLedger | None = None,
               sleeps: list | None = None, secrets: list[str] | None = None) -> ToolAgent:
    caller = ModelCaller(client, temperature=CONFIG.generation.temperature, max_output_tokens=MAX_OUT,
                         retry=CONFIG.retry, context_tokens=loaded.config.context.context_tokens, ledger=ledger,
                         sleep=(sleeps.append if sleeps is not None else lambda _: None),
                         secrets=secrets if secrets is not None else [])
    return ToolAgent(caller, loaded, name="test")


def run_one(client: FakeClient, mode: Mode = Mode.FULL, big: bool = False, **kwargs):
    toolkit, source = s.toolkit(mode, big=big)
    return make_agent(client, **kwargs).run(CaseView(s.RUN_ID), toolkit), toolkit, source


def synthetic_cases(*ids: str, mode: Mode = Mode.FULL) -> list[EvalCase]:
    return [EvalCase(i, s.RUN_ID, s.truth(i), lambda: s.toolkit(mode)[0]) for i in ids]


def with_context(context_tokens: int) -> LoadedAgentConfig:
    """The pre-registered config with another context budget (tests only)."""
    context = ContextSettings(context_tokens=context_tokens, reserve_tokens=CONFIG.context.reserve_tokens,
                              chars_per_token=CONFIG.context.chars_per_token)
    return LoadedAgentConfig(CONFIG.model_copy(update={"context": context}), LOADED.text, LOADED.path, LOADED.sha256,
                             LOADED.prompt, LOADED.bundle)


def answer_quoting(*quotes: tuple[str, str]) -> str:
    data = json.loads(ANSWER)
    data["evidence"] = [{"quote": q, "source": src} for q, src in quotes]
    return json.dumps(data)


def quiet(_: str) -> None:
    pass


# -- settings, prompt, tools ------------------------------------------------------------------------------


def test_preregistered_settings():
    c = CONFIG
    assert (c.name, c.prompt, c.bundle) == ("agent_v1", "prompts/agent_v1.md", "configs/one_call_v2.toml")
    assert c.agent.max_turns == 8 and c.generation.repair_calls == 1 and c.generation.temperature == 0
    assert c.generation.max_output_tokens == LOADED.bundle.config.generation.max_output_tokens
    assert (c.gemini.model, c.gemini.thinking_level, c.gemini.seed) == ("gemini-3.5-flash-lite", "minimal", 42)
    assert (c.gemini.max_requests_per_minute, c.gemini.daily_call_cap) == (10, 450)
    assert (c.ollama.model, c.ollama.think, c.ollama.num_ctx, c.ollama.seed) == ("qwen3.5:4b", False, 16384, 42)
    assert c.context.context_tokens == 16384 and set(c.retry.statuses) == {429, 500, 503, 504}
    assert c.retry.max_tries == 5
    assert [(r.backend, r.mode) for r in c.run] == [("gemini", "log-only"), ("gemini", "full"),
                                                    ("ollama", "log-only"), ("ollama", "full")]


DROPPED = " You cannot ask\nfor more data: use only what the bundle shows."


def test_prompt_is_the_one_call_prompt_plus_a_neutral_tools_section():
    one_call = (REPO_ROOT / "prompts" / "one_call_v2.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    agent = (REPO_ROOT / CONFIG.prompt).read_text(encoding="utf-8").replace("\r\n", "\n")
    head, tools_and_answer = agent.split("## Tools\n", 1)
    tools, answer = tools_and_answer.split("## Answer\n", 1)
    # everything but the dropped sentence, the Tools section and the final-turn message is one_call_v2 verbatim
    rebuilt = head + "## Answer\n" + answer.split("\n<!-- FINAL TURN MESSAGE", 1)[0].rstrip("\n") + "\n"
    assert rebuilt == one_call.replace(DROPPED, "")
    assert LOADED.prompt.repair == LOADED.bundle.prompt.repair
    for category in Category:
        assert f"- {category.value}:" in agent
    neutral = (tools + LOADED.prompt.final_turn).lower()
    for word in (*(c.value for c in Category), "regression", "flaky", "lab", "inj-", "rules", "d5", "d6",
                 "should", "first", "always", "never", "likely", "diff tool", "pytest", "docker"):
        assert not re.search(rf"\b{re.escape(word)}\b", neutral), word
    for name in re.findall(r"\b(?:get_\w+|list_runs|compare|extract_failure_block)\b", tools):
        assert name in TOOLS, name


@pytest.mark.parametrize("mode", list(Mode))
def test_tools_offered_are_the_toolkits_with_neutral_descriptions(mode):
    specs = tool_specs(s.toolkit(mode)[0])
    expected = [n for n in TOOLS if not (Mode(mode) is Mode.LOG_ONLY and n in ("compare", "get_commit"))]
    assert [t.name for t in specs] == expected
    for spec in specs:
        assert spec.description == TOOLS[spec.name].description
        assert "$defs" not in str(spec.parameters) and "title" not in spec.parameters
        assert spec.parameters["type"] == "object" and spec.parameters["properties"]
        text = (spec.description + json.dumps(spec.parameters)).lower()
        # ("the commit under triage" names which commit a default refers to; it is not strategy)
        for word in ("regression", "flaky", "dependency", "diagnos", "category", "should", "likely",
                     "inj-", "ci-triage-lab"):
            assert word not in text, (spec.name, word)


@pytest.mark.parametrize("mode", list(Mode))
def test_first_turn_is_exactly_the_one_call_bundle(mode):
    one_call = LOADED.bundle
    saved = {}
    path = RESULTS_DIR / ("one_call_v2-gemini" + ("-log-only" if Mode(mode) is Mode.LOG_ONLY else "")) / "cases.jsonl"
    if path.is_file():  # the bundles the D6a runs sent, where they are saved (gitignored run output)
        saved = {d["case_id"]: d["user_message"] for d in map(json.loads, path.read_text(encoding="utf-8").splitlines())}
    for case in CASES:
        client = FakeClient.of([ANSWER])
        outcome = make_agent(client).run(CaseView(case.eval_run_id),
                                         make_toolkit(ReplaySource(case, LAB), case.eval_commit, mode))
        expected = gather_bundle(CaseView(case.eval_run_id), make_toolkit(ReplaySource(case, LAB), case.eval_commit,
                                                                          mode), one_call.config.budget,
                                 len(one_call.prompt.system), one_call.config.generation.max_output_tokens)
        assert client.chats[0].messages == [Message("user", expected.user_message())], case.case_id
        if saved:
            assert outcome.user_message == saved[case.case_id], case.case_id


# -- the loop ---------------------------------------------------------------------------------------------


def test_tool_call_then_observation_then_answer():
    client = FakeClient.of([FakeTurn.calling(("get_file", {"path": "shelfwise/fees.py"})), ANSWER])
    outcome, toolkit, _ = run_one(client)
    assert outcome.status == "ok" and outcome.turns_used == 2 and not outcome.repaired
    first, second = client.chats
    assert [t.name for t in first.tools] == list(TOOLS) and first.schema is None
    assert first.temperature == 0 and first.max_output_tokens == MAX_OUT and first.system == LOADED.prompt.system
    asked, result = second.messages[-2:]
    assert asked.role == "assistant" and asked.tool_calls[0].name == "get_file"
    assert result.role == "tool" and result.tool_name == "get_file" and result.tool_call_id == "call-0"
    assert result.content == render(toolkit.call("get_file", {"path": "shelfwise/fees.py"})) and FEES_LINE in result.content
    (call,) = outcome.tool_calls
    logged = toolkit.calls[outcome.bundle_tool_calls]
    assert (call.turn, call.name, call.arguments, call.output_chars, call.available, call.shown) == \
        (1, "get_file", {"path": "shelfwise/fees.py"}, logged.output_chars, True, True)
    assert [c.purpose for c in outcome.calls] == ["turn", "turn"]
    assert [c.tool_calls for c in outcome.calls] == [["get_file"], []] and outcome.calls[0].tools_offered == 8
    assert [t.tool_calls for t in outcome.turns] == [1, 0] and not outcome.max_turns_forced
    assert all(c.model_version == "fake-model-001" and c.tokens_in and c.latency_s is not None for c in outcome.calls)
    assert outcome.diagnosis == Diagnosis.model_validate_json(ANSWER)


def test_several_calls_in_one_turn_all_answer():
    client = FakeClient.of([FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID}), ("get_run", {"run_id": s.RUN_ID})),
                            ANSWER])
    outcome, _, _ = run_one(client)
    assert [c.name for c in outcome.tool_calls] == ["get_jobs", "get_run"]
    assert [m.role for m in client.chats[1].messages] == ["user", "assistant", "tool", "tool"]


def test_max_turns_forces_the_answer():
    client = FakeClient.of([FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID}))] * 7 + [ANSWER])
    outcome, _, _ = run_one(client)
    assert outcome.status == "ok" and outcome.turns_used == CONFIG.agent.max_turns == len(client.chats) == 8
    assert all(c.tools is not None and c.schema is None for c in client.chats[:7])
    last = client.chats[7]
    assert last.tools is None and last.schema == response_schema(Diagnosis)
    assert last.messages[-1] == Message("user", LOADED.prompt.final_turn)
    assert outcome.max_turns_forced and not outcome.context_forced and len(outcome.tool_calls) == 7
    assert [c.purpose for c in outcome.calls] == ["turn"] * 7 + ["answer"]
    assert outcome.turns[-1].forced == "max_turns" and not outcome.turns[-1].tools_offered


def test_a_tool_call_on_the_last_turn_is_not_run_and_gets_a_repair():
    # distinct calls (offsets differ), so the repeated-call guard plays no part
    script = [FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "offset": i})) for i in range(8)] + [ANSWER]
    outcome, toolkit, _ = run_one(FakeClient.of(script))
    assert outcome.repeated_calls == 0 and len(outcome.tool_calls) == 7 and len(toolkit.calls) == outcome.bundle_tool_calls + 7
    assert outcome.status == "ok" and outcome.repaired and [c.purpose for c in outcome.calls][-2:] == ["answer", "repair"]


def _turn_estimates() -> tuple[int, int, ToolAgent, list[Message]]:
    """Prompt estimates of turns 1 and 2 when turn 1 reads a big log page, and turn 2's messages."""
    client = FakeClient.of([FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "max_lines": 1000})), ANSWER])
    outcome, _, _ = run_one(client, big=True)
    agent = make_agent(FakeClient.of([]))
    return outcome.turns[0].prompt_tokens_estimate, outcome.turns[1].prompt_tokens_estimate, agent, \
        client.chats[1].messages


def _context_for_room(room: int) -> int:
    return room + CONFIG.context.reserve_tokens + MAX_OUT


def test_context_budget_forces_an_early_answer():
    _, second, _, _ = _turn_estimates()
    loaded = with_context(_context_for_room(second - 1))  # turn 2 with the tools offered does not fit
    client = FakeClient.of([FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "max_lines": 1000})), ANSWER])
    outcome, _, _ = run_one(client, big=True, loaded=loaded)
    assert outcome.status == "ok" and outcome.context_forced and not outcome.max_turns_forced
    assert outcome.turns_used == 2 and outcome.turns[1].forced == "context" and outcome.turns[1].dropped_tool_calls == 0
    assert client.chats[1].tools is None and client.chats[1].messages[-1].content == LOADED.prompt.final_turn
    assert outcome.tool_calls[0].shown and client.chats[1].messages[-2].role == "tool"  # the result still fits


def test_context_budget_drops_results_that_do_not_fit_and_they_are_never_shown():
    _, _, agent, messages = _turn_estimates()
    without_tools = agent._estimate([*messages, Message("user", LOADED.prompt.final_turn)], None)
    loaded = with_context(_context_for_room(without_tools - 1))  # even without the tools, the log page is too much
    client = FakeClient.of([FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "max_lines": 1000})), ANSWER])
    outcome, _, _ = run_one(client, big=True, loaded=loaded)
    assert outcome.status == "ok" and outcome.context_forced and outcome.turns[1].dropped_tool_calls == 1
    assert not outcome.tool_calls[0].shown
    sent = client.chats[1].messages
    assert [m.role for m in sent] == ["user", "user"] and sent[-1].content == LOADED.prompt.final_turn
    assert all(m.role != "tool" for m in sent)


def test_unknown_tool_and_bad_arguments_come_back_as_results_not_crashes():
    client = FakeClient.of([FakeTurn.calling(("rm_rf", {"path": "/"}), ("get_jobs", {"run_id": "not-a-number"}),
                                             ("get_file", "not an object"), ("get_jobs", {"run_id": 1, "x": 2})),
                            ANSWER])
    outcome, _, _ = run_one(client)
    assert outcome.status == "ok" and [c.available for c in outcome.tool_calls] == [False] * 4
    results = [m.content for m in client.chats[1].messages if m.role == "tool"]
    assert "unknown tool" in results[0] and all("invalid arguments" in r for r in results[1:])


def test_log_only_offers_no_diff_tool_and_refuses_one_called_anyway():
    client = FakeClient.of([FakeTurn.calling(("compare", {"base": s.BASE, "head": s.HEAD}),
                                             ("get_commit", {"sha": s.HEAD}),
                                             ("get_file", {"path": "shelfwise/fees.py", "ref": s.BASE})), ANSWER])
    outcome, _, source = run_one(client, Mode.LOG_ONLY)
    offered = [t.name for t in client.chats[0].tools]
    assert "compare" not in offered and "get_commit" not in offered and "=== DIFF" not in outcome.user_message
    assert [c.available for c in outcome.tool_calls] == [False, False, False]
    assert all(LOG_ONLY_REASON in m.content for m in client.chats[1].messages if m.role == "tool")
    assert "compare" not in source.calls and "get_commit" not in source.calls


# -- repeated calls -------------------------------------------------------------------------------------------


def _tool_results(chat) -> list[str]:
    return [m.content for m in chat.messages if m.role == "tool"]


def test_a_repeated_call_is_not_run_again_and_returns_the_earlier_output():
    fees = ("get_file", {"path": "shelfwise/fees.py"})
    client = FakeClient.of([FakeTurn.calling(fees), FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID})),
                            FakeTurn.calling(fees), ANSWER])
    outcome, toolkit, _ = run_one(client)
    assert outcome.status == "ok" and outcome.turns_used == 4 and [t.tool_calls for t in outcome.turns] == [1, 1, 1, 0]
    assert len(toolkit.calls) == outcome.bundle_tool_calls + 2  # get_file ran once
    assert [t for t in toolkit.calls[outcome.bundle_tool_calls:] if t.tool == "get_file"] == \
        [toolkit.calls[outcome.bundle_tool_calls]]
    first, _, again = _tool_results(client.chats[3])
    assert first == render(toolkit.call("get_file", {"path": "shelfwise/fees.py"})) and FEES_LINE in first
    assert again == "Repeated call: same result as turn 1.\n" + first == f"{repeat_prefix(1)}\n{first}"
    assert outcome.repeated_calls == 1 and [c.repeat_of for c in outcome.tool_calls] == [None, None, 0]
    call = outcome.tool_calls[2]
    assert (call.turn, call.name, call.arguments, call.available, call.shown, call.output_chars) == \
        (3, "get_file", {"path": "shelfwise/fees.py"}, True, True, len(again))


def test_a_repeat_in_the_same_turn_and_argument_order_do_not_run_it_twice():
    client = FakeClient.of([FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "offset": 5}),
                                             ("get_job_log", {"offset": 5, "job_id": s.JOB_ID})), ANSWER])
    outcome, toolkit, _ = run_one(client)
    assert len(toolkit.calls) == outcome.bundle_tool_calls + 1 and outcome.repeated_calls == 1
    first, again = _tool_results(client.chats[1])
    assert again == f"{repeat_prefix(1)}\n{first}"


def test_different_arguments_or_names_are_not_repeats():
    client = FakeClient.of([FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID})),
                            FakeTurn.calling(("get_job_log", {"job_id": s.JOB_ID, "offset": 0}),  # same defaults
                                             ("get_job_log", {"job_id": str(s.JOB_ID)}),
                                             ("extract_failure_block", {"job_id": s.JOB_ID})), ANSWER])
    outcome, toolkit, _ = run_one(client)
    assert outcome.repeated_calls == 0 and len(toolkit.calls) == outcome.bundle_tool_calls + 4
    assert not any(r.startswith("Repeated call") for r in _tool_results(client.chats[2]))


def test_a_call_the_bundle_made_is_not_a_repeat_for_the_agent():
    toolkit, _ = s.toolkit(Mode.FULL)
    gather_bundle(CaseView(s.RUN_ID), toolkit, LOADED.bundle.config.budget, len(LOADED.bundle.prompt.system),
                  MAX_OUT)
    bundle_call = toolkit.calls[0]
    client = FakeClient.of([FakeTurn.calling((bundle_call.tool, bundle_call.arguments)), ANSWER])
    outcome, toolkit, _ = run_one(client)
    assert toolkit.calls[0].tool == bundle_call.tool and toolkit.calls[0].arguments == bundle_call.arguments
    assert outcome.repeated_calls == 0 and len(toolkit.calls) == outcome.bundle_tool_calls + 1


def test_a_repeated_unavailable_call_carries_the_earlier_result():
    bad = ("get_jobs", {"run_id": "not-a-number"})
    client = FakeClient.of([FakeTurn.calling(bad, ("rm_rf", {})), FakeTurn.calling(bad, ("rm_rf", {})), ANSWER])
    outcome, toolkit, _ = run_one(client)
    assert outcome.repeated_calls == 2 and len(toolkit.calls) == outcome.bundle_tool_calls + 2
    assert [c.available for c in outcome.tool_calls] == [False] * 4
    assert [c.repeat_of for c in outcome.tool_calls] == [None, None, 0, 1]
    results = _tool_results(client.chats[2])
    assert results[2:] == [f"{repeat_prefix(1)}\n{results[0]}", f"{repeat_prefix(1)}\n{results[1]}"]


def test_repeats_up_to_the_turn_limit_still_count_as_turns():
    client = FakeClient.of([FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID}))] * 7 + [ANSWER])
    outcome, toolkit, _ = run_one(client)
    assert outcome.turns_used == 8 and outcome.max_turns_forced and outcome.repeated_calls == 6
    assert len(toolkit.calls) == outcome.bundle_tool_calls + 1
    assert [c.repeat_of for c in outcome.tool_calls] == [None] + [0] * 6
    assert all(r == f"{repeat_prefix(1)}\n{_tool_results(client.chats[7])[0]}"
               for r in _tool_results(client.chats[7])[1:])


def test_records_and_grounding_with_a_repeated_call(tmp_path):
    fees = ("get_file", {"path": "shelfwise/fees.py"})
    seen = answer_quoting(("E       assert 2.5 == 5.0", "log"), (FEES_LINE, "file"))
    client = FakeClient.of([FakeTurn.calling(fees), FakeTurn.calling(fees), seen])
    run_cases(make_agent(client), synthetic_cases("a", mode=Mode.LOG_ONLY), tmp_path, mode=Mode.LOG_ONLY,
              echo=quiet, env={})
    (r,) = read_agent_records(tmp_path / "cases.jsonl")
    assert r.repeated_calls == 1 and [t.repeat_of for t in r.tool_calls] == [None, 0]
    assert (r.result.quotes, r.result.quotes_grounded) == (2, 2)
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "| repeated tool calls (same name and arguments, not run again) | 1 |" in summary


def test_seen_sections_pair_the_toolkit_log_with_the_calls_that_ran():
    toolkit, _ = s.toolkit(Mode.LOG_ONLY)
    bundle_calls = len(toolkit.calls)
    toolkit.call("get_file", {"path": "shelfwise/fees.py"})
    ran = AgentToolCall(turn=1, name="get_file", arguments={}, output_chars=1, available=True)
    repeat = ran.model_copy(update={"turn": 2, "repeat_of": 0})
    sections = seen_sections("", toolkit.calls, bundle_calls, [ran, repeat])
    assert any(FEES_LINE in t for t in sections[EvidenceSource.FILE])
    with pytest.raises(ValueError):
        seen_sections("", toolkit.calls, bundle_calls, [ran, ran])


# -- repair, invalid output, API errors ---------------------------------------------------------------------


def test_invalid_answer_then_repaired():
    client = FakeClient.of(["The run failed because of a test.", ANSWER])
    outcome, _, _ = run_one(client)
    assert outcome.status == "ok" and outcome.repaired and [c.purpose for c in outcome.calls] == ["turn", "repair"]
    repair = client.chats[1]
    assert repair.tools is None and repair.schema == response_schema(Diagnosis)
    assert [m.role for m in repair.messages[-2:]] == ["assistant", "user"]
    assert repair.messages[-1].content.startswith("Your answer is not a valid diagnosis:")


def test_invalid_twice_is_invalid_output_and_scored_wrong(tmp_path):
    client = FakeClient.of(['{"category": "kaput"}', '{"category": "real_regression"}'])
    run_cases(make_agent(client), synthetic_cases("a"), tmp_path, echo=quiet, env={})
    (record,) = read_agent_records(tmp_path / "cases.jsonl")
    assert record.status == "invalid_output" and len(client.chats) == 2
    assert record.result is not None and not record.result.category_correct and record.result.confidence == 0


def test_500_is_retried_and_an_api_error_is_not_scored(tmp_path):
    sleeps: list[float] = []
    client = FakeClient.of([*unavailable(1, 500), ANSWER, *unavailable(5, 503)])
    report = run_cases(make_agent(client, sleeps=sleeps), synthetic_cases("ok", "api"), tmp_path, echo=quiet, env={})
    ok, failed = read_agent_records(tmp_path / "cases.jsonl")
    assert ok.status == "ok" and ok.llm_calls[0].retries == 1 and ok.llm_calls[0].attempt_errors == [
        "500: service unavailable"]
    assert failed.status == "api_error" and failed.result is None and failed.llm_calls[0].attempts == 5
    assert report.api_errors == ["api"] and sleeps == [5.0, 5.0, 10.0, 20.0, 40.0]


# -- grounding: what the agent saw ----------------------------------------------------------------------------


@pytest.mark.parametrize("grader", ["v1", "v2"])
def test_grounding_accepts_quotes_from_tool_results_the_agent_saw(tmp_path, grader):
    seen = answer_quoting(("E       assert 2.5 == 5.0", "log"), (FEES_LINE, "file"))
    looked = FakeClient.of([FakeTurn.calling(("get_file", {"path": "shelfwise/fees.py"})), seen])
    never = FakeClient.of([seen])  # the same answer, without having read the file
    run_cases(make_agent(looked), synthetic_cases("looked", mode=Mode.LOG_ONLY), tmp_path / "a", mode=Mode.LOG_ONLY,
              echo=quiet, grader=grader, env={})
    run_cases(make_agent(never), synthetic_cases("never", mode=Mode.LOG_ONLY), tmp_path / "b", mode=Mode.LOG_ONLY,
              echo=quiet, grader=grader, env={})
    (a,) = read_agent_records(tmp_path / "a" / "cases.jsonl")
    (b,) = read_agent_records(tmp_path / "b" / "cases.jsonl")
    assert FEES_LINE not in a.user_message
    assert (a.result.quotes, a.result.quotes_grounded, a.result.evidence_grounded) == (2, 2, True)
    assert (b.result.quotes, b.result.quotes_grounded, b.result.evidence_grounded) == (2, 1, False)


def test_grounding_rejects_text_the_agent_never_saw(tmp_path):
    answer = answer_quoting(("E       assert 2.5 == 5.0", "log"), ("+DAILY_FEE = 0.125", "diff"),
                            ("DAILY_FEE = 0.25 was halved", "file"))
    client = FakeClient.of([FakeTurn.calling(("compare", {"base": s.BASE, "head": s.HEAD})), answer])
    run_cases(make_agent(client), synthetic_cases("x", mode=Mode.LOG_ONLY), tmp_path, mode=Mode.LOG_ONLY,
              echo=quiet, env={})
    (record,) = read_agent_records(tmp_path / "cases.jsonl")
    assert record.result.quotes_grounded == 1  # only the log line: the diff was refused, the third is invented


def test_seen_sections_leave_out_results_never_shown():
    toolkit, _ = s.toolkit(Mode.LOG_ONLY)
    bundle_calls = len(toolkit.calls)
    toolkit.call("get_file", {"path": "shelfwise/fees.py"})
    shown = AgentToolCall(turn=1, name="get_file", arguments={}, output_chars=1, available=True, shown=True)
    dropped = shown.model_copy(update={"shown": False})
    with_it = seen_sections("", toolkit.calls, bundle_calls, [shown])
    without = seen_sections("", toolkit.calls, bundle_calls, [dropped])
    assert any(FEES_LINE in t for t in with_it[EvidenceSource.FILE])
    assert not any(FEES_LINE in t for texts in without.values() for t in texts)
    with pytest.raises(ValueError):
        seen_sections("", toolkit.calls, bundle_calls, [])


# -- the runner: records, resume, the cap, the plan, the key ----------------------------------------------------


def test_records_hold_turns_tool_calls_tokens_latency_and_environment(tmp_path):
    client = FakeClient.of([FakeTurn.calling(("get_file", {"path": "shelfwise/fees.py"})), ANSWER])
    env = {"ollama_server_version": "0.0-test", "cpu_count": 8}
    run_cases(make_agent(client), synthetic_cases("a"), tmp_path, echo=quiet, env=env)
    (r,) = read_agent_records(tmp_path / "cases.jsonl")
    assert r.turns_used == 2 and [t.name for t in r.tool_calls] == ["get_file"] and r.tool_calls[0].output_chars > 0
    assert r.environment == env and all(c.model_version and c.tokens_in and c.latency_s is not None for c in r.llm_calls)
    assert r.bundle_tool_calls > 0 and r.result.grader == "v2" and r.user_message.startswith("Failed workflow run")
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    for row in ("| model turns per case, mean / max | 2.00 / 2 |", "| tool calls by tool | get_file 1 |",
                "ollama_server_version 0.0-test", "| category accuracy | 1/1 (100.0%) |", "agent_v1.toml"):
        assert row in summary, row


def test_environment_records_ollama_server_and_hardware():
    from ci_triage.evaluation.agent_run import environment

    stub = types.SimpleNamespace(server_info=lambda: {"ollama_server_version": "0.40.15", "cpu_count": 4})
    info = environment(stub)
    assert info["ollama_server_version"] == "0.40.15" and info["python"] and "langgraph" in info
    assert "ollama_server_version" not in environment(FakeClient.of([]))


def test_resume_skips_done_cases_and_reruns_api_errors(tmp_path):
    cases = synthetic_cases("a", "b", "c")
    run_cases(make_agent(FakeClient.of([ANSWER, *unavailable(5), ANSWER])), cases, tmp_path, echo=quiet, env={})
    assert [r.status for r in read_agent_records(tmp_path / "cases.jsonl")] == ["ok", "api_error", "ok"]
    with pytest.raises(SystemExit, match="--resume"):
        run_cases(make_agent(FakeClient.of([])), cases, tmp_path, echo=quiet, env={})
    second = FakeClient.of([ANSWER])
    report = run_cases(make_agent(second), cases, tmp_path, resume=True, echo=quiet, env={})
    assert len(second.chats) == 1 and report.ran == 1 and report.skipped == 2 and report.remaining == []


def test_daily_cap_stops_mid_case_cleanly_and_holds_across_runs(tmp_path):
    day = ["2026-11-04"]
    ledger = lambda: CallLedger(tmp_path / "ledger.json", "fake-model", cap=3, today=lambda: day[0])  # noqa: E731
    turn = FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID}))
    cases, out = synthetic_cases("a", "b"), tmp_path / "run"
    report = run_cases(make_agent(FakeClient.of([turn, ANSWER, turn, ANSWER]), ledger=ledger()), cases, out,
                       echo=quiet, env={})
    assert report.ran == 1 and report.remaining == ["b"] and "daily cap of 3" in report.stopped
    assert [r.case_id for r in read_agent_records(out / "cases.jsonl")] == ["a"]  # b, cut short, is not recorded
    client = FakeClient.of([turn, ANSWER])
    again = run_cases(make_agent(client, ledger=ledger()), cases, out, resume=True, echo=quiet, env={})
    assert again.ran == 0 and again.stopped and client.chats == []
    day[0] = "2026-11-05"
    final = run_cases(make_agent(client, ledger=ledger()), cases, out, resume=True, echo=quiet, env={})
    assert final.ran == 1 and final.remaining == [] and final.stopped is None and ledger().used() == 2


def _record(case_id: str) -> str:
    return AgentCaseRecord(case_id=case_id, run_id=1, status="ok", llm_calls=[], turns=[], turns_used=1, tool_calls=[],
                           bundle_tool_calls=0, context_forced=False, max_turns_forced=False, prompt_chars=0,
                           max_prompt_tokens_estimate=0, diff_included=False, wall_time_s=0).model_dump_json() + "\n"


def test_run_plan_puts_log_only_first_for_each_backend(tmp_path):
    check_run_plan(LOADED, "gemini", "log-only", tmp_path, 1)
    check_run_plan(LOADED, "ollama", "log-only", tmp_path, 1)  # the other backend's runs do not block it
    for backend in ("gemini", "ollama"):
        with pytest.raises(SystemExit, match=f"finish {backend} log-only first"):
            check_run_plan(LOADED, backend, "full", tmp_path, 1)
        directory = run_directory(LOADED, backend, "log-only", tmp_path)
        directory.mkdir(parents=True)
        (directory / "cases.jsonl").write_text(_record("a"), encoding="utf-8")
        check_run_plan(LOADED, backend, "full", tmp_path, 1)
    with pytest.raises(SystemExit, match="finish gemini log-only first"):
        check_run_plan(LOADED, "gemini", "full", tmp_path, 2)  # 1 of 2 cases scored


def test_key_never_appears_in_any_output_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    leaky = ANSWER.replace("The daily late fee", f"Key {KEY}: the daily late fee")
    client = FakeClient.of([FakeTurn.calling(("get_file", {"path": f"leak/{KEY}.py"}), text=f"thinking {KEY}"),
                            f"oops {KEY}", leaky, APICallError(403, f"API key {KEY} rejected")],
                           model_version=f"v-{KEY}")
    caller = make_agent_caller(client, LOADED, tmp_path, daily_cap=50)  # secrets read from the environment
    assert caller.secrets == [KEY]
    run_cases(ToolAgent(caller, LOADED, name="t"), synthetic_cases("a", "b"), tmp_path / "out", echo=quiet, env={})
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert {p.name for p in files} >= {"cases.jsonl", "summary.md", "llm_call_ledger.json"}
    for path in files:
        assert KEY not in path.read_text(encoding="utf-8"), path
    assert "<redacted>" in (tmp_path / "out" / "cases.jsonl").read_text(encoding="utf-8")


def test_an_agent_run_opens_no_network_connection(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    client = FakeClient.of([FakeTurn.calling(("get_jobs", {"run_id": s.RUN_ID})), ANSWER])
    report = run_cases(make_agent(client), synthetic_cases("a"), tmp_path, echo=quiet, env={})
    assert [r.status for r in report.records] == ["ok"]


# -- no agent module imports ci_triage.evaluation -------------------------------------------------------------


def test_no_agent_module_imports_evaluation():
    package = REPO_ROOT / "src" / "ci_triage" / "agent"
    modules = sorted(package.glob("*.py"))
    assert {p.name for p in modules} >= {"__init__.py", "config.py", "loop.py"}
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n.startswith("ci_triage.evaluation") or n == "evaluation" for n in names), (path, names)
            if isinstance(node, ast.ImportFrom) and node.level:
                assert not (node.module or "").startswith("evaluation"), path
    code = ("import sys, ci_triage.agent, ci_triage.agent.config, ci_triage.agent.loop; "
            "print([m for m in sys.modules if m.startswith('ci_triage.evaluation')])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO_ROOT)
    assert out.stdout.strip() == "[]"


# -- backend adapters, with stub SDKs or offline types --------------------------------------------------------


def test_gemini_contents_carry_calls_signatures_and_grouped_results():
    gtypes = pytest.importorskip("google.genai.types")
    signature = b"\x01sig"
    messages = [Message("user", "bundle"),
                Message("assistant", "", tool_calls=(ToolCallRequest("get_jobs", {"run_id": 1}, id="c1",
                                                                     signature=signature),
                                                     ToolCallRequest("get_run", {"run_id": 1}, id="c2"))),
                Message("tool", '{"available":true,"jobs":[]}', tool_name="get_jobs", tool_call_id="c1"),
                Message("tool", '{"available":true,"id":1}', tool_name="get_run", tool_call_id="c2"),
                Message("user", "last turn")]
    contents = gemini_contents(gtypes, messages)
    assert [c.role for c in contents] == ["user", "model", "user", "user"]
    calls = contents[1].parts
    assert [(p.function_call.name, p.function_call.id, p.thought_signature) for p in calls] == \
        [("get_jobs", "c1", signature), ("get_run", "c2", None)]
    results = contents[2].parts
    assert [(p.function_response.name, p.function_response.id, p.function_response.response) for p in results] == \
        [("get_jobs", "c1", {"available": True, "jobs": []}), ("get_run", "c2", {"available": True, "id": 1})]


def _stub_genai(monkeypatch, response):
    seen = {}

    class Obj:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Models:
        def generate_content(self, **kwargs):
            seen["request"] = kwargs
            return response

    class Client:
        def __init__(self, api_key, http_options):
            self.models = Models()

    names = ("HttpOptions", "HttpRetryOptions", "GenerateContentConfig", "ThinkingConfig", "Content", "Part", "Tool",
             "FunctionDeclaration", "AutomaticFunctionCallingConfig", "FunctionCall", "FunctionResponse")
    genai = types.ModuleType("google.genai")
    genai.Client, genai.types = Client, types.SimpleNamespace(**{n: Obj for n in names})
    genai.errors = types.SimpleNamespace(APIError=type("APIError", (Exception,), {}))
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return seen


def test_gemini_chat_offers_tools_and_reads_function_calls(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    part = lambda **kw: types.SimpleNamespace(**{"text": None, "thought": None, "function_call": None,  # noqa: E731
                                                 "thought_signature": None, **kw})
    call = types.SimpleNamespace(name="get_jobs", args={"run_id": 7}, id="c9")
    response = types.SimpleNamespace(
        model_version="gemini-3.5-flash-lite", candidates=[types.SimpleNamespace(
            finish_reason=types.SimpleNamespace(value="STOP"), content=types.SimpleNamespace(parts=[
                part(text="thinking", thought=True), part(text="Let me look."),
                part(function_call=call, thought_signature=b"s")]))],
        usage_metadata=types.SimpleNamespace(prompt_token_count=900, candidates_token_count=20, thoughts_token_count=0))
    seen = _stub_genai(monkeypatch, response)
    client = GeminiClient("gemini-3.5-flash-lite", thinking_level="minimal", seed=42)
    spec = ToolSpec("get_jobs", "A run's jobs.", {"type": "object", "properties": {"run_id": {"type": "integer"}}})
    out = client.chat("sys", [Message("user", "bundle")], tools=[spec], schema=None, temperature=0.0,
                      max_output_tokens=MAX_OUT)
    assert out.text == "Let me look." and out.tool_calls == (ToolCallRequest("get_jobs", {"run_id": 7}, id="c9",
                                                                              signature=b"s"),)
    assert (out.tokens_in, out.tokens_out, out.finish_reason) == (900, 20, "STOP")
    config = seen["request"]["config"]
    (declared,) = config.tools[0].function_declarations
    assert (declared.name, declared.description, declared.parameters_json_schema) == \
        ("get_jobs", "A run's jobs.", spec.parameters)
    assert config.automatic_function_calling.disable is True and not hasattr(config, "response_json_schema")
    assert config.temperature == 0.0 and config.seed == 42 and config.system_instruction == "sys"
    client.chat("sys", [Message("user", "bundle")], tools=None, schema={"type": "object"}, temperature=0.0,
                max_output_tokens=MAX_OUT)
    config = seen["request"]["config"]
    assert not hasattr(config, "tools") and config.response_json_schema == {"type": "object"}


def test_ollama_chat_offers_tools_reads_calls_and_reports_the_server(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, host=None, timeout=None):
            self._client = types.SimpleNamespace(get=lambda path: types.SimpleNamespace(
                json=lambda: {"version": "0.40.15"} if path == "/api/version" else {}))

        def chat(self, **kwargs):
            seen.update(kwargs)
            calls = [types.SimpleNamespace(function=types.SimpleNamespace(name="get_jobs", arguments={"run_id": 7}))]
            return types.SimpleNamespace(model="qwen3.5:4b", message=types.SimpleNamespace(content="", tool_calls=calls),
                                         prompt_eval_count=300, eval_count=25, done_reason="stop")

        def list(self):
            return types.SimpleNamespace(models=[types.SimpleNamespace(model="qwen3.5:4b", digest="d8b0f5e9760c99")])

        def ps(self):
            return types.SimpleNamespace(models=[types.SimpleNamespace(model="qwen3.5:4b", size=10, size_vram=4)])

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=Client, ResponseError=RuntimeError))
    client = OllamaClient("qwen3.5:4b", think=False, num_ctx=16384, seed=42)
    spec = ToolSpec("get_jobs", "A run's jobs.", {"type": "object", "properties": {"run_id": {"type": "integer"}}})
    history = [Message("user", "bundle"), Message("assistant", "", tool_calls=(ToolCallRequest("get_run", {"run_id": 7}),)),
               Message("tool", '{"available":true}', tool_name="get_run")]
    out = client.chat("sys", history, tools=[spec], schema=None, temperature=0.0, max_output_tokens=MAX_OUT)
    assert out.tool_calls == (ToolCallRequest("get_jobs", {"run_id": 7}),) and out.model_version == "qwen3.5:4b@d8b0f5e9760c"
    assert seen["tools"] == [{"type": "function", "function": {"name": "get_jobs", "description": "A run's jobs.",
                                                               "parameters": spec.parameters}}]
    assert "format" not in seen and seen["think"] is False and seen["options"]["num_ctx"] == 16384
    assert seen["messages"] == ollama_messages("sys", history) == [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "bundle"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_run", "arguments": {"run_id": 7}}}]},
        {"role": "tool", "content": '{"available":true}', "tool_name": "get_run"}]
    seen.clear()
    client.chat("sys", history, tools=None, schema={"type": "object"}, temperature=0.0, max_output_tokens=MAX_OUT)
    assert seen["format"] == {"type": "object"} and "tools" not in seen
    info = client.server_info()
    assert info["ollama_server_version"] == "0.40.15" and (info["model_size_bytes"], info["model_vram_bytes"]) == (10, 4)
    assert info["cpu_count"] and "os" in info
