"""The Diagnosis format, the grader and the runner, checked with predictors whose scores are known.

oracle (answers from ground truth): 100% on every metric. constant (always real_regression):
exactly 10/60. random (fixed seed): near 1/6.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import fields

import pytest
from conftest import CASES, LAB, TRUTH
from oracle import OraclePredictor
from pydantic import ValidationError

from ci_triage.diagnosis import ACTION_FOR, CATEGORIES, Category, Diagnosis, Evidence, EvidenceSource, RecommendedAction
from ci_triage.evaluation.grader import fix_match, grade_case, is_grounded, normalise
from ci_triage.evaluation.ground_truth import job_names
from ci_triage.evaluation.metrics import summarise
from ci_triage.evaluation.run import evaluate, load_predictor, main, write_results
from ci_triage.predictors import CaseView, ConstantPredictor, Predictor, RandomPredictor
from ci_triage.replay import ReplaySource
from ci_triage.tools import LOG_ONLY_REASON, Mode, Toolkit

CASE = {case.case_id: case for case in CASES}


def diagnosis(**overrides) -> Diagnosis:
    fields_ = dict(category="dependency", confidence=0.9, failing_job="Import & route smoke tests",
                   failing_step="Install dependencies", root_cause="bad pin", recommended_action="fix_dependency")
    return Diagnosis(**fields_ | overrides)


@pytest.fixture(scope="module")
def oracle_results():
    return evaluate(OraclePredictor(TRUTH), LAB)


@pytest.fixture(scope="module")
def constant_results():
    return evaluate(ConstantPredictor(), LAB)


@pytest.fixture(scope="module")
def random_results():
    return evaluate(RandomPredictor(seed=0), LAB)


# -- the format -----------------------------------------------------------------


def test_categories_are_the_labs():
    assert set(CATEGORIES) == {t.category for t in TRUTH.values()}
    assert set(ACTION_FOR) == set(Category) and len(set(ACTION_FOR.values())) == len(RecommendedAction)


def test_diagnosis_validation():
    assert diagnosis().suspect_file is None and diagnosis().evidence == []
    for bad in ({"category": "network"}, {"confidence": 1.01}, {"confidence": -0.1},
                {"recommended_action": "retry"}, {"surprise": 1},
                {"evidence": [{"quote": "x", "source": "log"}] * 6},
                {"evidence": [{"quote": "", "source": "log"}]},
                {"evidence": [{"quote": "x", "source": "slack"}]}):
        with pytest.raises(ValidationError):
            diagnosis(**bad)


def test_diagnosis_schema_documents_every_field():
    schema = Diagnosis.model_json_schema()
    assert set(schema["required"]) == {"category", "confidence", "failing_job", "failing_step", "root_cause",
                                       "recommended_action"}
    assert all("description" in prop for prop in schema["properties"].values())


def test_case_view_holds_only_the_run_id():
    assert [f.name for f in fields(CaseView)] == ["run_id"]
    assert isinstance(ConstantPredictor(), Predictor) and isinstance(OraclePredictor(TRUTH), Predictor)


# -- ground truth the grader needs --------------------------------------------------


def test_failing_step_is_a_step_of_the_failed_job(case, source, truth):
    jobs = Toolkit(source).call("get_jobs", {"run_id": case.eval_run_id})
    failed = [job for job in jobs.jobs if job.conclusion in ("failure", "cancelled")]
    assert truth[case.case_id].failing_step in {step.name for job in failed for step in job.steps}


def test_failing_job_name_is_the_failed_job_as_get_jobs_shows_it(case, source, truth):
    jobs = Toolkit(source).call("get_jobs", {"run_id": case.eval_run_id})
    failed = [job.name for job in jobs.jobs if job.conclusion in ("failure", "cancelled")]
    assert failed == [truth[case.case_id].failing_job_name]


def test_job_names():
    workflow = ("name: CI\non: push\njobs:\n  smoke:\n    name: Smoke tests\n    steps:\n      - name: x\n"
                "  docker:\n    needs: smoke\n  lint:\n    name: 'Lint (ruff)'\nenv:\n  name: not-a-job\n")
    assert job_names(workflow) == {"smoke": "Smoke tests", "docker": "docker", "lint": "Lint (ruff)"}


def test_every_revert_restores_or_removes_something(truth):
    for t in truth.values():
        assert t.restored or t.removed, t.case_id
        # The revert undoes the spec's find -> replace (one side may be a fragment of the other's lines).
        restored, removed = normalise(" ".join(t.restored)), normalise(" ".join(t.removed))
        assert normalise(t.find) in restored or all(normalise(line) in normalise(t.find) for line in t.restored)
        assert normalise(t.replace) in removed or all(normalise(line) in normalise(t.replace) for line in t.removed)


# -- grounding ------------------------------------------------------------------------


def _toolkit(case_id: str) -> Toolkit:
    """A toolkit that has fetched the case's jobs and its failed job's failure block."""
    case = CASE[case_id]
    toolkit = Toolkit(ReplaySource(case, LAB))
    jobs = toolkit.call("get_jobs", {"run_id": case.eval_run_id})
    job_id = next(job.id for job in jobs.jobs if job.conclusion == "failure")
    toolkit.call("extract_failure_block", {"job_id": job_id})
    return toolkit


def test_grounding_needs_a_returned_output_of_the_declared_source():
    toolkit = _toolkit("inj-011")
    line = "ERROR: No matching distribution found for pydantic==9.9.9"
    assert is_grounded(Evidence(quote=line, source="log"), toolkit.calls)
    assert is_grounded(Evidence(quote=f"  {line}\n", source="log"), toolkit.calls)  # outer whitespace ignored
    assert not is_grounded(Evidence(quote=line, source="file"), toolkit.calls)  # no get_file call returned it
    assert not is_grounded(Evidence(quote=line.replace("9.9.9", "9.9.8"), source="log"), toolkit.calls)
    assert not is_grounded(Evidence(quote=line, source="log"), [])  # quoting what was never fetched
    toolkit.call("get_file", {"path": "requirements.txt"})
    assert is_grounded(Evidence(quote="pydantic==9.9.9", source="file"), toolkit.calls)


def test_grounding_matches_multiline_quotes_without_line_numbers():
    toolkit = _toolkit("inj-011")
    block = next(call.output for call in toolkit.calls if call.tool == "extract_failure_block")["blocks"][0]["text"]
    lines = [line.split("| ", 1)[1] for line in block.split("\n") if "| " in line]
    i = next(n for n, line in enumerate(lines) if "No matching distribution" in line)
    quote = "\n".join(lines[i - 1:i + 1])
    assert "\n" in quote and is_grounded(Evidence(quote=quote, source="log"), toolkit.calls)


def test_grounding_sees_unescaped_text():
    """Quotes are matched against the strings the model read, not their JSON escaping."""
    toolkit = _toolkit("inj-010")
    quote = "ImportError: cannot import name 'ChatRequst' from 'api.models.schemas'"
    assert is_grounded(Evidence(quote=quote, source="log"), toolkit.calls)
    toolkit.call("get_file", {"path": "api/routes/chat.py"})
    assert is_grounded(Evidence(quote="from api.models.schemas import ChatRequst", source=EvidenceSource.FILE),
                       toolkit.calls)


# -- fix match ------------------------------------------------------------------------


@pytest.mark.parametrize(("case_id", "fix", "expected"), [
    ("inj-011", "pydantic==2.13.4", "exact"),
    ("inj-011", "In requirements.txt, change the pin to:\n    pydantic==2.13.4\n", "exact"),
    ("inj-011", "Pin pydantic to 2.13.4", "partial"),
    ("inj-011", "Pin pydantic to a version that exists", "none"),
    ("inj-014", "Rename fastpi to fastapi", "partial"),
    ("inj-001", "char_count=len(content),", "exact"),
    ("inj-001", "char_count=len(content) - 1,", "none"),  # the injected line is not the fix
    ("inj-049", 'request = ChatRequest(question="What is RAG?")\nassert request.question == "What is RAG?"', "exact"),
    ("inj-049", 'request = ChatRequest(question="What is RAG?")', "partial"),
    ("inj-039", "Delete `timeout-minutes: 1` from the smoke job.", "exact"),  # a removal
    ("inj-039", "Raise the timeout.", "none"),
    ("inj-029", 'Remove API_PORT: "not_a_number" from the env block', "partial"),
    ("inj-029", None, "none"),
])
def test_fix_match(case_id, fix, expected):
    assert fix_match(fix, TRUTH[case_id]) == expected


# -- the sanity predictors ------------------------------------------------------------------


def test_oracle_scores_100_percent_on_every_metric(oracle_results):
    s = summarise("oracle", oracle_results)
    assert (s.cases, s.correct, s.errors) == (60, 60, 0)
    for metric in ("accuracy", "macro_f1", "step_rate", "localisation_rate", "grounding_rate",
                   "quote_grounding_rate", "fix_exact_rate", "action_rate"):
        assert getattr(s, metric) == 1.0, metric
    assert s.brier == 0.0 and s.fix_partial_rate == s.fix_none_rate == 0.0
    assert all(c.precision == c.recall == c.f1 == 1.0 for c in s.per_category.values())
    assert all(s.confusion[c][p] == (10 if c == p else 0) for c in CATEGORIES for p in CATEGORIES)
    assert s.mean_tool_calls == 2.0 and all(len(r.calls) == r.tool_calls == 2 for r in oracle_results)
    assert s.job_rate == 1.0 and all(r.job_correct for r in oracle_results)
    assert all(r.wall_time_s > 0 for r in oracle_results)
    assert s.mean_wall_time_s > 0 and s.max_wall_time_s >= s.mean_wall_time_s


def test_constant_scores_exactly_10_of_60(constant_results):
    s = summarise("constant", constant_results)
    assert (s.correct, s.cases, s.errors) == (10, 60, 0)
    assert s.accuracy == pytest.approx(10 / 60)
    assert s.macro_f1 == pytest.approx((2 * (1 / 6) / (1 + 1 / 6)) / 6)  # F1 2/7 on one category, 0 on five
    assert all(row == {**dict.fromkeys(row, 0), "real_regression": 10} for row in s.confusion.values())
    assert s.action_rate == pytest.approx(10 / 60)
    assert s.step_rate == s.localisation_rate == s.grounding_rate == s.fix_exact_rate == s.mean_tool_calls == 0
    assert s.job_rate == 0  # "unknown"
    assert s.brier == pytest.approx((10 * (5 / 6) ** 2 + 50 * (1 / 6) ** 2) / 60)


def test_random_scores_near_one_sixth(random_results):
    s = summarise("random", random_results)
    assert s.errors == 0
    assert 4 <= s.correct <= 16  # binomial(60, 1/6): mean 10, sd 2.9
    assert len({r.predicted_category for r in random_results}) == 6
    assert [r.predicted_category for r in evaluate(RandomPredictor(seed=0), LAB)] == \
           [r.predicted_category for r in random_results]


def test_grader_reads_the_call_log_not_the_predictor():
    """A diagnosis quoting the right line scores no grounding if its own toolkit never returned it."""
    case = CASE["inj-011"]
    quote = Evidence(quote="ERROR: No matching distribution found for pydantic==9.9.9", source="log")
    result = grade_case(TRUTH["inj-011"], diagnosis(evidence=[quote]), Toolkit(ReplaySource(case, LAB)).calls)
    assert result.category_correct and result.quotes == 1 and not result.evidence_grounded


# -- the runner -------------------------------------------------------------------------


class Raises:
    name = "raises"

    def predict(self, case_view, toolkit):
        toolkit.call("get_run", {"run_id": case_view.run_id})
        raise RuntimeError("model said no")


def test_a_failing_prediction_is_graded_wrong_not_fatal():
    results = evaluate(Raises(), LAB)
    s = summarise("raises", results)
    assert s.errors == 60 and s.correct == 0 and s.brier == 0.0 and s.mean_tool_calls == 1
    assert all(r.error == "RuntimeError: model said no" and r.diagnosis is None for r in results)
    assert sum(s.confusion[c]["(none)"] for c in CATEGORIES) == 60


def test_runner_writes_cases_and_summary(tmp_path, constant_results):
    directory, summary = write_results("constant", constant_results, tmp_path)
    rows = [json.loads(line) for line in (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["case_id"] for row in rows] == [case.case_id for case in CASES]
    assert Counter(row["category_correct"] for row in rows) == {False: 50, True: 10}
    assert all(row["diagnosis"]["category"] == "real_regression" for row in rows)
    assert all(row["wall_time_s"] >= 0 and row["job_correct"] is False for row in rows)
    markdown = (directory / "summary.md").read_text(encoding="utf-8")
    assert "| category accuracy | 10/60 (16.7%) |" in markdown and "## Confusion matrix" in markdown
    assert markdown.startswith("# Evaluation: constant\n") and "| failing job correct | 0.0% |" in markdown


def test_runner_cli(tmp_path, capsys):
    main(["--predictor", "constant", "--out", str(tmp_path)])
    assert "constant: 10/60 correct" in capsys.readouterr().out
    assert (tmp_path / "constant" / "summary.md").is_file()
    assert sum(1 for _ in (tmp_path / "constant" / "cases.jsonl").open(encoding="utf-8")) == 60


def test_runner_cli_log_only(tmp_path, capsys):
    main(["--predictor", "constant", "--mode", "log-only", "--out", str(tmp_path)])
    assert "constant (log-only): 10/60 correct" in capsys.readouterr().out
    markdown = (tmp_path / "constant-log-only" / "summary.md").read_text(encoding="utf-8")
    assert markdown.startswith("# Evaluation: constant (log-only mode)\n")
    assert not (tmp_path / "constant").exists()
    with pytest.raises(SystemExit):
        main(["--predictor", "constant", "--mode", "diff-only", "--out", str(tmp_path)])


class Probe:
    """Tries the diff tools and an old file, and records what came back."""

    name = "probe"

    def __init__(self):
        self.seen = []

    def predict(self, case_view, toolkit):
        commit = toolkit.call("get_commit", {"sha": "HEAD"})
        diff = toolkit.call("compare", {"base": "HEAD", "head": "HEAD"})
        old = toolkit.call("get_file", {"path": "", "ref": "HEAD~1"})
        head = toolkit.call("get_file", {"path": ""})
        self.seen.append((commit.available, diff.available, old.available, head.available,
                          getattr(commit, "reason", None)))
        return diagnosis()


@pytest.mark.parametrize(("mode", "expected"), [
    (Mode.FULL, (True, True, False, True, None)),
    (Mode.LOG_ONLY, (False, False, False, True, LOG_ONLY_REASON)),
])
def test_evaluate_builds_each_case_toolkit_for_the_mode(mode, expected):
    probe = Probe()
    results = evaluate(probe, LAB, mode=mode)
    assert len(results) == len(probe.seen) == 60 and set(probe.seen) == {expected}


def test_call_log_outputs_stay_out_of_the_jsonl(tmp_path, oracle_results):
    directory, _ = write_results("oracle", oracle_results[:1], tmp_path)
    row = json.loads((directory / "cases.jsonl").read_text(encoding="utf-8"))
    assert [call["tool"] for call in row["calls"]] == ["get_jobs", "extract_failure_block"]
    assert "output" not in row["calls"][0]


def test_load_predictor():
    assert isinstance(load_predictor("random"), RandomPredictor)
    assert isinstance(load_predictor("oracle:OraclePredictor"), OraclePredictor)
    with pytest.raises(SystemExit):
        load_predictor("nope")
