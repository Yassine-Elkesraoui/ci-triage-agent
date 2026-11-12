"""Grader v2.1: a "history" quote found in list_runs, get_run or get_jobs output the predictor itself saw
breaks no quote rule; one matched only in the one-call bundle's RUN AND JOBS keeps the job-or-step-name
rule; nothing else changes. The saved-run regression test re-grades every saved D5 and D6a run under
results/ (gitignored run output, so it is skipped where there is none); no model is called.
"""

from __future__ import annotations

import pytest
import synthetic as s
from conftest import LAB, TRUTH
from test_agent import answer_quoting, make_agent, quiet, synthetic_cases

from ci_triage.diagnosis import Diagnosis, Evidence, EvidenceSource
from ci_triage.evaluation.agent_run import read_agent_records, run_cases
from ci_triage.evaluation.grader import DEFAULT_GRADER, GRADERS, _strings, grade_case, job_and_step_names, normalise
from ci_triage.evaluation.metrics import summarise, to_markdown
from ci_triage.evaluation.regrade import StaleRun, regrade_run, saved_runs
from ci_triage.llm.clients import FakeClient, FakeTurn
from ci_triage.tools import Mode


def test_v2_1_is_the_default_and_v1_v2_stay_selectable():
    assert DEFAULT_GRADER == "v2.1" and GRADERS == ("v1", "v2", "v2.1")


GREEN_CREATED = "2026-09-30T10:00:00Z"  # the last green run's created_at: in list_runs output, not in the bundle


def _list_runs_quote():
    """A toolkit that ran the bundle's calls and then list_runs, and a string list_runs returned that is
    neither a job nor a step name."""
    toolkit, _ = s.toolkit(Mode.FULL)
    toolkit.call("list_runs", {"branch": "main"})
    listing = toolkit.calls[-1]
    assert GREEN_CREATED in _strings(listing.output)
    assert normalise(GREEN_CREATED).casefold() not in job_and_step_names(toolkit.calls)
    return toolkit, listing, GREEN_CREATED


def _diagnosis(*evidence: tuple[str, str]) -> Diagnosis:
    return Diagnosis(category="real_regression", confidence=0.9, failing_job=s.JOB_NAME, failing_step=s.STEP_NAME,
                     root_cause="x", recommended_action="fix_code",
                     evidence=[Evidence(quote=q, source=src) for q, src in evidence])


def test_history_quote_from_list_runs_output_is_accepted():
    toolkit, listing, quote = _list_runs_quote()
    d = _diagnosis((quote, "history"))
    sections = {EvidenceSource.HISTORY: list(_strings(listing.output))}
    v21 = grade_case(s.truth("x"), d, toolkit.calls, grader="v2.1", sections=sections, seen_calls=[listing])
    v2 = grade_case(s.truth("x"), d, toolkit.calls, grader="v2", sections=sections, seen_calls=[listing])
    assert (v21.grader, v21.quotes_grounded, v21.quote_rules.followed, v21.quote_rules.history_not_name) == \
           ("v2.1", 1, 1, 0)
    assert (v2.grader, v2.quotes_grounded, v2.quote_rules.followed, v2.quote_rules.history_not_name) == ("v2", 1, 0, 1)


def test_history_quote_from_get_run_and_get_jobs_output_is_accepted_and_get_commit_is_not():
    toolkit, _ = s.toolkit(Mode.FULL)
    toolkit.call("get_run", {"run_id": s.RUN_ID})
    toolkit.call("get_jobs", {"run_id": s.RUN_ID})
    get_run, get_jobs = toolkit.calls[-2:]
    assert get_run.available and get_jobs.available
    names = job_and_step_names(toolkit.calls)
    as_commit = get_run.model_copy(update={"tool": "get_commit"})  # the same output, from a tool v2.1 leaves out
    for call in (get_run, get_jobs, as_commit):
        quote = next(t for t in _strings(call.output) if len(t) > 8 and normalise(t).casefold() not in names)
        r = grade_case(s.truth("x"), _diagnosis((quote, "history")), toolkit.calls, grader="v2.1",
                       sections={EvidenceSource.HISTORY: [quote]}, seen_calls=[call])
        assert r.quote_rules.history_not_name == (call.tool == "get_commit"), call.tool


def test_the_same_text_never_seen_in_tool_output_still_breaks_the_rule():
    toolkit, listing, quote = _list_runs_quote()
    d = _diagnosis((quote, "history"))
    # only in a RUN AND JOBS-like bundle section: grounded, but the job-or-step-name rule stays
    in_bundle = grade_case(s.truth("x"), d, toolkit.calls, grader="v2.1", sections={EvidenceSource.HISTORY: [quote]})
    assert (in_bundle.quotes_grounded, in_bundle.quote_rules.history_not_name) == (1, 1)
    # list_runs ran (in the bundle's calls, say) but its output was not shown to the model
    unseen = grade_case(s.truth("x"), d, toolkit.calls, grader="v2.1", sections={EvidenceSource.HISTORY: [quote]},
                        seen_calls=[c for c in toolkit.calls if c is not listing])
    assert unseen.quote_rules.history_not_name == 1
    # seen nowhere
    nowhere = grade_case(s.truth("x"), d, toolkit.calls[:-1], grader="v2.1", sections={}, seen_calls=[])
    assert (nowhere.quotes_grounded, nowhere.quote_rules.history_not_name) == (0, 1)
    # labelled "log": the history rule does not apply, the label is wrong
    as_log = grade_case(s.truth("x"), _diagnosis((quote, "log")), toolkit.calls, grader="v2.1",
                        sections={EvidenceSource.HISTORY: [quote]}, seen_calls=[listing])
    assert as_log.quote_rules.model_dump()["wrong_source"] == 1 and as_log.quote_rules.history_not_name == 0


def test_agent_run_accepts_a_history_quote_from_its_own_list_runs_call(tmp_path):
    _, _, quote = _list_runs_quote()
    answer = answer_quoting(("E       assert 2.5 == 5.0", "log"), (quote, "history"))
    looked = FakeClient.of([FakeTurn.calling(("list_runs", {"branch": "main"})), answer])
    never = FakeClient.of([answer])
    run_cases(make_agent(looked), synthetic_cases("looked"), tmp_path / "a", echo=quiet, env={})
    run_cases(make_agent(never), synthetic_cases("never"), tmp_path / "b", echo=quiet, env={})
    (a,) = read_agent_records(tmp_path / "a" / "cases.jsonl")
    (b,) = read_agent_records(tmp_path / "b" / "cases.jsonl")
    assert quote not in a.user_message and a.result.grader == b.result.grader == "v2.1"
    assert (a.result.quotes_grounded, a.result.quote_rules.followed, a.result.quote_rules.history_not_name) == (2, 2, 0)
    assert (b.result.quotes_grounded, b.result.quote_rules.history_not_name) == (1, 1)


def test_summary_names_grader_v2_1():
    r = grade_case(TRUTH["inj-011"], None, [], grader="v2.1")
    md = to_markdown(summarise("x", [r]))
    assert "Graded with grader v2.1:" in md and "| quote rules followed (quotes) |" in md


# -- every saved D5 and D6a run: v2.1 gives exactly the v2 results -----------------------------------------

SAVED = [p for p in saved_runs() if not p.name.startswith("agent_")]  # D6b runs are not D5 or D6a


@pytest.fixture(scope="module", params=SAVED or [None], ids=[p.name for p in SAVED] or ["none"])
def regraded(request):
    if request.param is None:
        pytest.skip("no saved runs under results/")
    try:
        return regrade_run(request.param, LAB, TRUTH)
    except StaleRun as exc:
        pytest.skip(str(exc))


def test_saved_run_v2_1_results_byte_identical_to_v2(regraded):
    assert regraded.v2 and all(r.grader == "v2" for r in regraded.v2) and all(r.grader == "v2.1" for r in regraded.v2_1)
    v2 = [r.model_dump_json(exclude={"grader"}).encode() for r in regraded.v2]
    v21 = [r.model_dump_json(exclude={"grader"}).encode() for r in regraded.v2_1]
    assert v21 == v2
    s2 = to_markdown(summarise(regraded.name, regraded.v2, regraded.mode.value))
    s21 = to_markdown(summarise(regraded.name, regraded.v2_1, regraded.mode.value))
    assert s21.replace("Graded with grader v2.1:", "Graded with grader v2:", 1).encode() == s2.encode()
