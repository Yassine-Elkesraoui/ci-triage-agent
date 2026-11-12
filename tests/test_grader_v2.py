"""Grader v2: normalised grounding, the quote-rule metric, remove-type fixes left to a judge, and that it
changes nothing else. The saved-run regression tests re-grade every run under results/ (gitignored run
output, so they are skipped where there is none) by replaying the recorded tool calls; no model is called.
"""

from __future__ import annotations

import pytest
from conftest import CASES, LAB, TRUTH
from oracle import OraclePredictor

from ci_triage.diagnosis import CATEGORIES, Diagnosis, Evidence, EvidenceSource
from ci_triage.evaluation.grader import (QUOTE_RULES, check_quote, fix_match, fix_match_v2, grade_case,
                                         ground_normalise, is_header_line, normalise)
from ci_triage.evaluation.metrics import summarise, to_markdown
from ci_triage.evaluation.regrade import StaleRun, regrade_run, saved_runs
from ci_triage.evaluation.run import evaluate, parse_results_name, results_name
from ci_triage.llm.config import load_config
from ci_triage.predictors import RulesPredictor
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import bundle_sections, gather_bundle
from ci_triage.replay import ReplaySource
from ci_triage.tools import Mode, ToolCall, make_toolkit

LOADED = load_config()

# -- the normalisation ------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["x  =  1\n\tassert   y", "  a\n\n b  \r\n", "E       assert False", "plain"])
def test_ground_normalise_is_fix_match_normalisation_on_plain_text(text):
    assert ground_normalise(text) == normalise(text)


@pytest.mark.parametrize(("text", "expected"), [
    ("+    env:\n+      API_PORT: \"x\"", 'env: API_PORT: "x"'),  # diff gutter
    ("-FROM python:3.11-slim\n+FROM python:3.99-slim", "FROM python:3.11-slim FROM python:3.99-slim"),
    ("- COPY api/ ./api/", "COPY api/ ./api/"),  # a space after the marker is whitespace
    ("     context line", "context line"),
    ("161|     numpy=>2.4.4\n162|          ^ (from line 66)", "numpy=>2.4.4 ^ (from line 66)"),  # line numbers
    ("  7| +    timeout-minutes: 1", "timeout-minutes: 1"),  # a numbered patch line: prefix, then gutter
    ("          timeout-minutes: 3\n        run: pytest", "timeout-minutes: 3 run: pytest"),  # indentation
    (" +  where 0 = len([])", "where 0 = len([])"),  # indentation goes first, then the marker ...
    ("+ where 0 = len([])", "where 0 = len([])"),  # ... so a re-indented copy agrees
])
def test_ground_normalise_strips_gutter_line_numbers_and_whitespace(text, expected):
    assert ground_normalise(text) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("E       +  where False", "E + where False"),  # pytest's E gutter is text
    ("   12 | >>> FROM python:3.99-slim", "12 | >>> FROM python:3.99-slim"),  # docker's numbering is text
    ("--- Dockerfile", "-- Dockerfile"),  # one gutter character only
    ("12|foo", "12|foo"),  # not a line-number prefix
])
def test_ground_normalise_keeps_everything_else(text, expected):
    assert ground_normalise(text) == expected


# -- grounding and quote rules on synthetic sections ------------------------------------------------------

SECTIONS = {
    EvidenceSource.HISTORY: ['Job "Smoke tests" (id 1): conclusion failure, duration 30 s\n'
                             '  Step 2 "Run tests": conclusion failure, duration 20 s'],
    EvidenceSource.LOG: ["[pytest_assertion; step Run tests; lines 651-653]\n"
                         "651| >       assert [c.index for c in chunks] == [0, 1, 2, ...]\n"
                         "652| E       AssertionError: values differ\n"
                         "653| E       assert False\n"
                         "654|     assert 0 > 1\n"
                         "655|      +  where 0 = len([])"],
    EvidenceSource.FILE: ['13|     env:\n14|       API_PORT: "not_a_number"'],
    EvidenceSource.DIFF: ['From abc1234 (last successful run) to def5678 (this run)\n'
                          '--- .github/workflows/ci.yml (modified, +2 -0)\n'
                          '@@ -10,4 +10,6 @@\n+    env:\n+      API_PORT: "not_a_number"'],
}
NAMES = {"smoke tests", "run tests"}


def check(quote: str, source: str) -> tuple[bool, frozenset[str]]:
    shown = {s: [ground_normalise(t) for t in texts] for s, texts in SECTIONS.items()}
    return check_quote(Evidence(quote=quote, source=source), shown, NAMES)


@pytest.mark.parametrize(("quote", "source", "grounded", "breaks"), [
    ("E       AssertionError: values differ", "log", True, set()),  # verbatim
    ("AssertionError:   values differ\nE assert False", "log", True, set()),  # whitespace
    ('env:\n  API_PORT: "not_a_number"', "diff", True, set()),  # diff gutter dropped
    ("assert 0 > 1\n+ where 0 = len([])", "log", True, set()),  # re-indented pytest continuation line
    ('    env:\n      API_PORT: "not_a_number"', "file", True, set()),
    ("AssertionError: values differ", "file", True, {"wrong_source"}),  # in another section
    ("652| E       AssertionError: values differ", "log", True, {"line_number_prefix"}),
    ("99| E       AssertionError: values differ", "log", True, {"line_number_prefix"}),  # wrong number too
    ("AssertionError: the values differ", "log", False, set()),  # paraphrase
    ("assert False\nAssertionError: values differ", "log", False, set()),  # lines reordered
    ("assert [c.index for c in chunks] ... assert False", "log", False, {"ellipsis"}),  # elided
    ("assert [c.index for c in chunks] == [0, 1, 2, ...]", "log", True, set()),  # the source's own "..."
    ("[pytest_assertion; step Run tests; lines 651-653]", "log", True, {"section_header"}),
    ("pytest_assertion; step Run tests", "log", True, {"section_header"}),
    ("--- .github/workflows/ci.yml (modified, +2 -0)", "diff", True, {"section_header"}),
    ("Smoke tests", "history", True, set()),  # a job name
    ("Run   tests", "history", True, set()),  # a step name, spacing ignored
    ('Job "Smoke tests" (id 1): conclusion failure, duration 30 s', "history", True, {"history_not_name"}),
    ("13|     env:", "log", True, {"wrong_source", "line_number_prefix"}),
])
def test_grounding_and_quote_rules(quote, source, grounded, breaks):
    assert check(quote, source) == (grounded, frozenset(breaks))


@pytest.mark.parametrize("line", [
    "=== FAILURE BLOCK [log]: job \"Import & route smoke tests\" ===",
    "=== DIFF [diff] ===",
    "[timeout; step python -m pytest tests -v; lines 690-698]",
    "[pytest_summary, pytest_counts, exit_code; step python -m pytest tests -v; lines 704-709]",
    "[timeout; step python -m pytest tests -v]",
    "cancelled; step pip install -r requirements.txt",
    "[exit_code; lines 3-9]",
    "--- Dockerfile (modified, +1 -1)",
    "From 501c3843c4ab8cb03b48393b27cbf8ab4b52088c (last successful run) to 0f7e001 (this run)",
    "[truncated: 409 more characters not shown]",
])
def test_header_lines(line):
    assert is_header_line(line)


@pytest.mark.parametrize("line", [
    "=========================== short test summary info ============================",
    "-FROM python:3.11-slim", "--- a/Dockerfile", "- name: Run smoke tests", "ERROR: x; step y",
    "651| E       AssertionError: assert None == []", "Run smoke tests", "##[error]The operation was canceled.",
])
def test_not_header_lines(line):
    assert not is_header_line(line)


# -- the bundle: sections parse back, headers are exactly the lines the predictor formats ------------------


def test_bundle_sections_round_trip_and_headers_are_never_tool_text(case):
    toolkit = make_toolkit(ReplaySource(case, LAB), case.eval_commit, Mode.FULL)
    bundle = gather_bundle(CaseView(run_id=case.eval_run_id), toolkit, LOADED.config.budget,
                           len(LOADED.prompt.system), LOADED.config.generation.max_output_tokens)
    sections = bundle_sections(bundle.user_message())
    assert sections == {s.source: [s.text] for s in bundle.sections}
    tool_lines = {line for call in toolkit.calls for text in _texts(call) for line in text.split("\n")}
    assert not any(is_header_line(line) for line in tool_lines)
    for section in bundle.sections:
        assert is_header_line(section.render().split("\n", 1)[0])
        if section.title in ("FAILURE BLOCK", "DIFF"):
            formatted = [line for line in section.text.split("\n")  # "Note: <the tool's note>" is tool text
                         if line and line not in tool_lines and line.removeprefix("Note: ") not in tool_lines]
            assert formatted and all(map(is_header_line, formatted)), formatted


def _texts(call: ToolCall):
    from ci_triage.evaluation.grader import _strings
    return _strings(call.output)


# -- fix match ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("case_id", "fix"), [
    ("inj-011", "pydantic==2.13.4"), ("inj-011", "Pin pydantic to 2.13.4"), ("inj-001", "char_count=len(content) - 1,"),
    ("inj-049", 'request = ChatRequest(question="What is RAG?")'), ("inj-001", None),
])
def test_fix_match_v2_is_v1_on_restore_cases(case_id, fix):
    assert TRUTH[case_id].restored and fix_match_v2(fix, TRUTH[case_id]) == fix_match(fix, TRUTH[case_id])


@pytest.mark.parametrize("fix", ["Delete `timeout-minutes: 1` from the smoke job.", "Raise the timeout.", "", None])
def test_fix_match_v2_leaves_remove_cases_to_a_judge(fix):
    for case_id in ("inj-039", "inj-029", "inj-055"):
        assert not TRUTH[case_id].restored and fix_match_v2(fix, TRUTH[case_id]) == "needs_judge"


# -- grade_case and the summary -----------------------------------------------------------------------


def _diagnosis(evidence, fix=None) -> Diagnosis:
    return Diagnosis(category="timeout", confidence=0.9, failing_job="Smoke tests", failing_step="Run tests",
                     root_cause="x", recommended_action="investigate_timeout", suggested_fix=fix,
                     evidence=[Evidence(quote=q, source=s) for q, s in evidence])


def test_grade_case_v2_counts_quote_rules_and_they_do_not_change_grounding():
    d = _diagnosis([("E       AssertionError: values differ", "log"),  # follows the rules
                    ("AssertionError: values differ", "file"),  # wrong label
                    ("652| E       assert False", "log"),  # line-number prefix
                    ("the values differ", "log")])  # not grounded, breaks no rule
    r = grade_case(TRUTH["inj-039"], d, [], grader="v2", sections=SECTIONS)
    assert (r.grader, r.quotes, r.quotes_grounded, r.evidence_grounded, r.fix_match) == ("v2", 4, 3, False, "needs_judge")
    assert r.quote_rules.model_dump() == {"followed": 2, "wrong_source": 1, "section_header": 0, "history_not_name": 0,
                                          "line_number_prefix": 1, "ellipsis": 0}
    s = summarise("x", [r])
    assert (s.grader, s.quote_rules_followed, s.cases_quote_rules_followed, s.needs_judge, s.needs_judge_no_fix,
            s.fix_cases) == ("v2", 2, 0, 1, 1, 0)
    assert s.quote_rule_breaks == {"wrong_source": 1, "section_header": 0, "history_not_name": 0,
                                   "line_number_prefix": 1, "ellipsis": 0}
    md = to_markdown(s)
    assert "| quote rules followed (quotes) | 2/4 (50.0%) |" in md and "wrong source label 1" in md
    assert "| fix match, remove-type | 1 need a judge (1 suggest no fix) |" in md


def test_grade_case_v2_without_sections_reads_the_tool_outputs():
    toolkit = make_toolkit(ReplaySource(next(c for c in CASES if c.case_id == "inj-011"), LAB), "", Mode.FULL)
    jobs = toolkit.call("get_jobs", {"run_id": next(c for c in CASES if c.case_id == "inj-011").eval_run_id})
    failed = next(job for job in jobs.jobs if job.conclusion == "failure")
    toolkit.call("extract_failure_block", {"job_id": failed.id})
    d = _diagnosis([("ERROR:   No matching distribution found for pydantic==9.9.9", "log"), (failed.name, "history")])
    r = grade_case(TRUTH["inj-011"], d, toolkit.calls, grader="v2")
    assert r.grader == "v2" and r.evidence_grounded and r.quote_rules.followed == 2
    assert not grade_case(TRUTH["inj-011"], d, [], grader="v2").evidence_grounded  # nothing fetched, nothing grounded


def test_unknown_grader_and_mixed_graders_are_refused():
    with pytest.raises(ValueError, match="unknown grader"):
        grade_case(TRUTH["inj-011"], None, [], grader="v3")
    mixed = [grade_case(TRUTH["inj-011"], None, [], grader=g) for g in ("v1", "v2")]
    with pytest.raises(ValueError, match="different graders"):
        summarise("x", mixed)


@pytest.fixture(scope="module")
def oracle_by_grader():
    return {g: evaluate(OraclePredictor(TRUTH), LAB, grader=g) for g in ("v1", "v2")}


def test_oracle_under_both_graders(oracle_by_grader):
    s1, s2 = (summarise("oracle", oracle_by_grader[g]) for g in ("v1", "v2"))
    assert (s1.grader, s1.fix_cases, s1.fix_exact_rate, s1.needs_judge) == ("v1", 60, 1.0, 0)
    assert (s2.grader, s2.fix_cases, s2.fix_exact_rate, s2.needs_judge) == ("v2", 38, 1.0, 22)
    assert s2.grounding_rate == s1.grounding_rate == 1.0 and s2.quote_rules_followed == s2.quotes
    assert s2.per_category["timeout"].fix_cases == 0 and "| timeout | 10 | 10 |" in to_markdown(s2)
    v1_md = to_markdown(s1)
    assert "quote rules" not in v1_md and "| fix match exact / partial / none | 100.0% / 0.0% / 0.0% |" in v1_md


@pytest.mark.parametrize("mode", list(Mode))
def test_rules_v1_stays_fully_grounded_under_v2(mode):
    s = summarise("rules", evaluate(RulesPredictor(), LAB, mode, grader="v2"), Mode(mode).value)
    assert s.grounding_rate == s.quote_grounding_rate == 1.0
    assert s.quote_rules_followed == s.quotes and set(s.quote_rule_breaks) == set(QUOTE_RULES)


@pytest.mark.parametrize("name", ["rules", "one_call_v2-gemini"])
@pytest.mark.parametrize("mode", list(Mode))
def test_results_names_parse_back(name, mode):
    assert parse_results_name(results_name(name, mode)) == (name, mode)


# -- every saved run: its grader reproduces it, v2 changes only grounding, quote rules and remove-type fixes ---

SAVED = saved_runs()


@pytest.fixture(scope="module", params=SAVED or [None], ids=[p.name for p in SAVED] or ["none"])
def regraded(request):
    if request.param is None:
        pytest.skip("no saved runs under results/")
    try:
        return regrade_run(request.param, LAB, TRUTH)
    except StaleRun as exc:
        pytest.skip(str(exc))


def test_saved_run_its_grader_reproduces_the_saved_grades(regraded):
    assert regraded.stored and regraded.reproduces


def test_saved_run_its_grader_reproduces_the_saved_summary_byte_for_byte(regraded):
    if regraded.name.startswith("one_call"):
        pytest.skip("an LLM run's summary.md also holds the model-call figures")
    saved = (regraded.directory / "summary.md").read_text(encoding="utf-8")
    summary = regraded.summaries()[0 if regraded.saved_grader == "v1" else 1]
    assert to_markdown(summary) == saved


def test_saved_run_category_job_step_file_identical_under_v1_and_v2(regraded):
    same = ("case_id", "predicted_category", "confidence", "category_correct", "job_correct", "step_correct",
            "file_correct", "action_correct", "fix_kind", "tool_calls", "error")
    for a, b in zip(regraded.v1, regraded.v2, strict=True):
        assert [getattr(a, f) for f in same] == [getattr(b, f) for f in same]
        assert b.fix_match == (a.fix_match if a.fix_kind == "restore" or a.diagnosis is None else "needs_judge")
    s1, s2 = regraded.summaries()
    for metric in ("cases", "correct", "accuracy", "macro_f1", "job_rate", "step_rate", "localisation_rate",
                   "action_rate", "brier", "errors", "confusion"):
        assert getattr(s1, metric) == getattr(s2, metric), metric
    for c in CATEGORIES:
        a, b = s1.per_category[c], s2.per_category[c]
        assert (a.precision, a.recall, a.f1, a.step_rate, a.localisation_rate) == \
               (b.precision, b.recall, b.f1, b.step_rate, b.localisation_rate)


def test_saved_run_v2_grounds_every_quote_v1_grounded(regraded):
    assert all(b.quotes_grounded >= a.quotes_grounded for a, b in zip(regraded.v1, regraded.v2, strict=True))


def test_saved_rules_runs_stay_fully_grounded(regraded):
    if regraded.name != "rules":
        pytest.skip("not a rules run")
    assert all(r.evidence_grounded and r.quote_rules.followed == r.quotes for r in regraded.v2)
