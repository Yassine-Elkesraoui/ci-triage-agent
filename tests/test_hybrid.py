"""Hybrid v1: scored by combining saved runs (``evaluation.hybrid_run``) and live (``predictors.hybrid``), the same
rule both ways, and the same answer on synthetic and saved cases. Models are scripted (no network, no key)."""

from __future__ import annotations

import ast
import hashlib
import json

import pytest
import synthetic as s
from conftest import CASES, LAB

from ci_triage.agent.config import load_agent_config
from ci_triage.agent.loop_v2 import SUBMIT, ToolAgentV2, no_retry_rule
from ci_triage.diagnosis import ACTION_FOR, Category, Diagnosis
from ci_triage.evaluation.agent_run import AgentCaseRecordV2, run_cases
from ci_triage.evaluation.grader import CaseResult
from ci_triage.evaluation.hybrid_run import HybridCaseRecord, combine, read_rules_results, score
from ci_triage.evaluation.llm_run import EvalCase, lab_cases
from ci_triage.evaluation.run import evaluate, results_name, write_results
from ci_triage.labpaths import REPO_ROOT, RESULTS_DIR
from ci_triage.llm.calling import ModelCaller
from ci_triage.llm.clients import FakeClient, FakeTurn, LLMResponse
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.hybrid import FALLBACK_RULE, HybridPredictor, fired_rule, rules_decide
from ci_triage.predictors.rules import RULE_NAMES, RulesPredictor
from ci_triage.replay import ReplaySource
from ci_triage.tools import Mode, make_toolkit

V2 = load_agent_config(REPO_ROOT / "configs" / "agent_v2.toml")
ANSWER = json.loads(s.VALID_DIAGNOSIS)
CATEGORIES = list(Category)


class EchoClient(FakeClient):
    """Submits, on its first turn, a diagnosis that depends on the bundle it was shown (its sha256), so two runs
    give the same answer only if the agent saw the same bundle."""

    def chat(self, system, messages, **kwargs) -> LLMResponse:
        digest = hashlib.sha256(messages[0].content.encode("utf-8")).hexdigest()
        category = CATEGORIES[int(digest[:2], 16) % len(CATEGORIES)]
        answer = {**ANSWER, "category": category.value, "recommended_action": ACTION_FOR[category].value,
                  "root_cause": f"saw bundle {digest[:16]}", "evidence": [], "confidence": 0.5}
        self.script = [FakeTurn.calling((SUBMIT, answer))]
        return super().chat(system, messages, **kwargs)


def agent_v2() -> ToolAgentV2:
    client = EchoClient(script=[])
    caller = ModelCaller(client, temperature=0.0, max_output_tokens=V2.config.generation.max_output_tokens,
                         retry=V2.config.retry, context_tokens=V2.config.context.context_tokens, sleep=lambda _: None,
                         secrets=[], no_retry=no_retry_rule(V2))
    return ToolAgentV2(caller, V2, name="agent_v2-fake")


def quiet(_: str) -> None:
    pass


# -- the rule ---------------------------------------------------------------------------------------------------


def _rules_diagnosis(rule: str) -> Diagnosis:
    return Diagnosis.model_validate({**ANSWER, "root_cause": f"Rule {rule} ({RULE_NAMES[rule]}): detail."})


@pytest.mark.parametrize("rule", list(RULE_NAMES))
def test_rules_1_to_6_decide_and_rule_7_falls_back(rule):
    diagnosis = _rules_diagnosis(rule)
    assert fired_rule(diagnosis) == rule
    assert rules_decide(diagnosis) is (rule != FALLBACK_RULE)


def test_anything_else_is_not_a_rules_decision():
    for root_cause in ("Rule 6e (some other name): x.", "Rule 9 (made up): x.", "The fee was halved.", ""):
        diagnosis = Diagnosis.model_validate({**ANSWER, "root_cause": root_cause})
        assert fired_rule(diagnosis) is None and not rules_decide(diagnosis)
    assert fired_rule(None) is None and not rules_decide(None)


def test_rules_v1_still_name_their_rule_in_root_cause(case):
    """The rule's id is read from the rules' own root_cause; every v1.0 case, log-only, carries one."""
    diagnosis = RulesPredictor().predict(CaseView(case.eval_run_id),
                                         make_toolkit(ReplaySource(case, LAB), case.eval_commit, Mode.LOG_ONLY))
    assert fired_rule(diagnosis) in RULE_NAMES


# -- combining saved runs vs the live predictor -----------------------------------------------------------------


def _saved_runs(tmp_path, mode: Mode, rules_results: list[CaseResult], agent_cases: list[EvalCase]):
    write_results("rules", rules_results, tmp_path, mode)
    run_cases(agent_v2(), agent_cases, tmp_path / results_name("agent_v2-fake", mode), mode=mode, echo=quiet, env={})
    directory = score("fake", mode, out=tmp_path)
    lines = (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    return {r.case_id: r for r in map(HybridCaseRecord.model_validate_json, lines)}, directory


def _synthetic_rules(mode: Mode, case_id: str) -> CaseResult:
    from ci_triage.evaluation.grader import grade_case

    toolkit = s.toolkit(mode)[0]
    return grade_case(s.truth(case_id), RulesPredictor().predict(CaseView(s.RUN_ID), toolkit), toolkit.calls)


@pytest.mark.parametrize("mode", list(Mode))
def test_synthetic_case_same_answer_saved_and_live(tmp_path, mode):
    mode = Mode(mode)
    cases = [EvalCase("synthetic-1", s.RUN_ID, s.truth("synthetic-1"), lambda: s.toolkit(mode)[0])]
    saved, directory = _saved_runs(tmp_path, mode, [_synthetic_rules(mode, "synthetic-1")], cases)
    live = HybridPredictor(agent_v2()).run(CaseView(s.RUN_ID), s.toolkit(mode)[0])
    record = saved["synthetic-1"]
    # full mode: the diff shows application code (rule 6e) and the rules decide; log-only: rule 7, the agent
    assert (record.decided_by, record.rule) == (("rules", "6e") if mode is Mode.FULL else ("agent_v2", "7"))
    assert (live.decided_by, live.rule, live.status) == (record.decided_by, record.rule, record.status)
    assert live.diagnosis == record.result.diagnosis
    assert (live.agent is None) is (live.decided_by == "rules")
    summary = (directory / "summary.md").read_text(encoding="utf-8")
    assert "no model or tool calls of its own" in summary and "sha256" in summary


RULES_RESULTS: dict[Mode, list[CaseResult]] = {}


def _lab_rules(mode: Mode) -> list[CaseResult]:
    if mode not in RULES_RESULTS:
        RULES_RESULTS[mode] = evaluate(RulesPredictor(), LAB, mode)  # offline replay, as a saved rules run
    return RULES_RESULTS[mode]


@pytest.mark.parametrize("mode", list(Mode))
def test_every_v1_0_case_same_answer_saved_and_live(tmp_path, mode):
    mode = Mode(mode)
    saved, _ = _saved_runs(tmp_path, mode, _lab_rules(mode), lab_cases(mode))
    assert sorted(saved) == sorted(case.case_id for case in CASES)
    decided = {"rules": 0, "agent_v2": 0}
    for case in CASES:
        record = saved[case.case_id]
        live = HybridPredictor(agent_v2()).run(CaseView(case.eval_run_id),
                                               make_toolkit(ReplaySource(case, LAB), case.eval_commit, mode))
        assert (live.decided_by, live.rule, live.status) == (record.decided_by, record.rule, record.status), \
            case.case_id
        assert live.diagnosis == record.result.diagnosis, case.case_id
        decided[record.decided_by] += 1
    assert decided["rules"] and decided["agent_v2"] if mode is Mode.LOG_ONLY else decided["rules"]


@pytest.mark.parametrize("mode", list(Mode))
def test_saved_v1_0_rules_runs_make_the_same_decisions(mode):
    """The rules runs saved in results/ (gitignored; scored before phase B) decide the same cases as today's."""
    path = RESULTS_DIR / results_name("rules", mode) / "cases.jsonl"
    if not path.is_file():
        pytest.skip("no saved rules run here (results/ is gitignored)")
    saved = {r.case_id: (fired_rule(r.diagnosis), rules_decide(r.diagnosis)) for r in read_rules_results(path)}
    now = {r.case_id: (fired_rule(r.diagnosis), rules_decide(r.diagnosis)) for r in _lab_rules(Mode(mode))}
    assert saved == now


# -- the scorer's checks ------------------------------------------------------------------------------------------


def _agent_record(case_id: str, status: str = "ok", result: CaseResult | None = None) -> AgentCaseRecordV2:
    return AgentCaseRecordV2(case_id=case_id, run_id=1, status=status, llm_calls=[], turns=[], turns_used=1,
                             tool_calls=[], bundle_tool_calls=0, context_forced=False, max_turns_forced=False,
                             prompt_chars=0, max_prompt_tokens_estimate=0, diff_included=False, wall_time_s=0,
                             result=result)


def _rules_result(case_id: str, rule: str, grader: str = "v2.1") -> CaseResult:
    result = _synthetic_rules(Mode.LOG_ONLY, case_id)
    return result.model_copy(update={"diagnosis": _rules_diagnosis(rule), "grader": grader})


def test_combine_takes_the_rules_case_or_the_agents():
    agent_result = _rules_result("b", "7").model_copy(update={"diagnosis": Diagnosis.model_validate(ANSWER)})
    records = combine([_rules_result("a", "4"), _rules_result("b", "7"), _rules_result("c", "7")],
                      [_agent_record("a"), _agent_record("b", result=agent_result), _agent_record("c", "api_error")])
    assert [(r.case_id, r.decided_by, r.rule, r.status) for r in records] == [
        ("a", "rules", "4", "ok"), ("b", "agent_v2", "7", "ok"), ("c", "agent_v2", "7", "api_error")]
    assert records[0].result.diagnosis.root_cause.startswith("Rule 4") and records[1].result is agent_result
    assert records[2].result is None
    (missing,) = combine([_rules_result("d", "7")], [])
    assert (missing.status, missing.result) == ("missing", None)


def test_combine_refuses_mismatched_runs():
    with pytest.raises(ValueError, match="different graders"):
        combine([_rules_result("a", "4", "v2")], [_agent_record("a", result=_rules_result("a", "7"))])
    with pytest.raises(ValueError, match="cases the rules run has not"):
        combine([_rules_result("a", "4")], [_agent_record("z")])
    with pytest.raises(ValueError, match="twice"):
        combine([_rules_result("a", "4"), _rules_result("a", "4")], [])


def test_hybrid_modules_do_not_import_evaluation():
    path = REPO_ROOT / "src" / "ci_triage" / "predictors" / "hybrid.py"
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("ci_triage.evaluation"), node.module
