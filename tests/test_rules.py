"""The rules baseline on small synthetic runs (no lab data): each rule fires, and every field is filled."""

from __future__ import annotations

import copy

import pytest

from ci_triage.diagnosis import ACTION_FOR, Category, EvidenceSource
from ci_triage.evaluation.grader import is_grounded
from ci_triage.github_source import NotAvailable
from ci_triage.predictors import PREDICTORS, CaseView
from ci_triage.predictors.rules import CONFIDENCE, RulesPredictor, file_kind, job_timeout_minutes
from ci_triage.tools import Toolkit

WORKFLOW_PATH = ".github/workflows/ci.yml"
WORKFLOW = """name: CI
on: [push]
jobs:
  smoke:
    name: Import & route smoke tests
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v5
      - name: Run smoke tests
        timeout-minutes: 3
        run: python -m pytest tests -v
  docker:
    name: Docker image builds
    needs: smoke
    steps:
      - name: Build image
        run: docker build -t app:ci .
"""
SMOKE_STEPS = ["Set up job", "Run actions/checkout@v5", "Run actions/setup-python@v6", "Install dependencies",
               "Run smoke tests", "Complete job"]
FAILING_TEST_LOG = """##[group]Run python -m pytest tests -v
tests/test_api.py::test_health PASSED [ 50%]
tests/test_api.py::test_chat FAILED [100%]
E       assert 500 == 200
=========================== short test summary info ============================
FAILED tests/test_api.py::test_chat - assert 500 == 200
========================= 1 failed, 1 passed in 0.52s ==========================
##[error]Process completed with exit code 1.
Post job cleanup.
"""
APP_PATCH = "@@ -1,3 +1,3 @@\n def health():\n-    return 200\n+    return 500\n"


def _t(seconds: int) -> str:
    return f"2026-01-01T00:{seconds // 60:02d}:{seconds % 60:02d}Z"


class FakeSource:
    """A GitHubSource from plain dicts: runs, jobs, logs, commits, compares and files."""

    def __init__(self) -> None:
        self.runs: dict[int, dict] = {}
        self.jobs: dict[int, dict] = {}
        self.logs: dict[int, str] = {}
        self.commits: dict[str, dict] = {}
        self.compares: dict[tuple[str, str], list[dict]] = {}
        self.files: dict[tuple[str, str], str] = {}

    def get_run(self, run_id):
        return copy.deepcopy(self.runs.get(run_id)) or NotAvailable(f"run {run_id}", "out of scope")

    def get_jobs(self, run_id):
        return copy.deepcopy(self.jobs.get(run_id)) or NotAvailable(f"jobs of {run_id}", "out of scope")

    def get_job_log(self, job_id):
        return self.logs.get(job_id) or NotAvailable(f"log of {job_id}", "out of scope")

    def get_commit(self, sha):
        return copy.deepcopy(self.commits.get(sha)) or NotAvailable(f"commit {sha}", "out of scope")

    def compare(self, base, head):
        if (base, head) not in self.compares:
            return NotAvailable(f"compare {base}...{head}", "out of scope")
        return {"base_commit": base, "head_commit": head, "files": copy.deepcopy(self.compares[(base, head)])}

    def get_file(self, path, ref=None):
        content = self.files.get((path, ref or "head"))
        if content is None:
            return NotAvailable(path, "not found")
        return {"type": "file", "path": path, "sha": "x", "size": len(content), "encoding": "utf-8", "content": content}

    def list_runs(self, branch="main", limit=100):
        runs = sorted(self.runs.values(), key=lambda run: run["created_at"], reverse=True)
        return {"total_count": len(runs), "workflow_runs": copy.deepcopy(runs[:limit])}


def _diff(filename: str, patch: str) -> dict:
    plus = sum(1 for line in patch.split("\n") if line.startswith("+"))
    minus = sum(1 for line in patch.split("\n") if line.startswith("-"))
    return {"filename": filename, "status": "modified", "additions": plus, "deletions": minus, "patch": patch}


def _step(number: int, name: str, conclusion: str | None, start: int = 0, end: int = 1) -> dict:
    return {"number": number, "name": name, "status": "completed" if conclusion else "pending",
            "conclusion": conclusion, "started_at": _t(start), "completed_at": _t(end)}


def scenario(*, failing_step: str = "Run smoke tests", files: list[dict] | None = None, log: str = FAILING_TEST_LOG,
             docker_fails: bool = False) -> FakeSource:
    """Run 1 (green, commit base) then run 2 (red, commit head = base + ``files``)."""
    files = [_diff("api/main.py", APP_PATCH)] if files is None else files
    src = FakeSource()
    for run_id, sha, conclusion, created in ((1, "base", "success", 0), (2, "head", "failure", 600)):
        src.runs[run_id] = {"id": run_id, "name": "CI", "path": WORKFLOW_PATH, "head_sha": sha, "head_branch": "main",
                            "status": "completed", "conclusion": conclusion, "event": "push", "run_number": run_id,
                            "run_attempt": 1, "created_at": _t(created), "run_started_at": _t(created),
                            "updated_at": _t(created + 120)}
    src.jobs[1] = {"jobs": []}
    smoke_steps = [_step(n, name, "success") for n, name in enumerate(SMOKE_STEPS, 1)]
    if not docker_fails:
        at = SMOKE_STEPS.index(failing_step)
        smoke_steps[at]["conclusion"] = "failure"
        for step in smoke_steps[at + 1:-1]:
            step.update(status="completed", conclusion="skipped")
    src.jobs[2] = {"jobs": [
        {"id": 21, "name": "Import & route smoke tests", "status": "completed",
         "conclusion": "success" if docker_fails else "failure", "started_at": _t(0), "completed_at": _t(60),
         "labels": ["ubuntu-latest"], "steps": smoke_steps},
        {"id": 22, "name": "Docker image builds", "status": "completed",
         "conclusion": "failure" if docker_fails else "skipped", "started_at": _t(60), "completed_at": _t(90),
         "labels": ["ubuntu-latest"],
         "steps": [_step(1, "Set up job", "success"), _step(2, "Build image", "failure" if docker_fails else None)]},
    ]}
    src.logs[22 if docker_fails else 21] = log
    src.commits["base"] = {"sha": "base", "tree_sha": "t0", "parents": ["root"], "message": "<redacted>",
                           "diff": "the baseline commit's own diff is not available in this case's scope"}
    src.commits["head"] = {"sha": "head", "tree_sha": "t1", "parents": ["base"], "message": "<redacted>",
                           "files": files}
    src.compares[("base", "head")] = files
    src.files[(WORKFLOW_PATH, "head")] = WORKFLOW
    return src


def diagnose(src: FakeSource):
    toolkit = Toolkit(src)
    diagnosis = RulesPredictor().predict(CaseView(run_id=2), toolkit)
    return diagnosis, toolkit


def _rule(diagnosis) -> str:
    return diagnosis.root_cause.split(" ", 2)[1]


# -- the rules, one by one ------------------------------------------------------------


def test_rule1_same_tree_passed_is_flaky():
    patch = "@@ -1,2 +1,3 @@\n def test_order():\n+    random.shuffle(items)\n     assert items == sorted(items)\n"
    src = scenario(files=[_diff("tests/test_order.py", patch)])
    # inj (the change) ran green as run 1b; head is an empty rerun commit on top of it
    src.commits["inj"] = {**src.commits["head"], "sha": "inj"}
    src.commits["head"] = {"sha": "head", "tree_sha": "t1", "parents": ["inj"], "message": "<redacted>", "files": []}
    src.runs[3] = {**src.runs[1], "id": 3, "head_sha": "inj", "created_at": _t(300)}
    diagnosis, _ = diagnose(src)
    assert diagnosis.category == Category.FLAKY and _rule(diagnosis) == "1"
    assert diagnosis.confidence == CONFIDENCE["1"]
    assert "run 3" in diagnosis.root_cause
    assert diagnosis.suspect_file == "tests/test_order.py"  # the baseline is run 1, not the green run 3 of inj
    assert diagnosis.recommended_action == ACTION_FOR[Category.FLAKY]


def test_rule1_needs_a_truly_empty_diff():
    src = scenario()
    src.runs[3] = {**src.runs[1], "id": 3, "head_sha": "inj", "created_at": _t(300)}
    src.commits["inj"] = {**src.commits["head"], "sha": "inj"}
    # head's diff only touched hidden files: not the same tree, so not rule 1
    src.commits["head"] = {"sha": "head", "tree_sha": "t2", "parents": ["inj"], "message": "<redacted>", "files": [],
                           "hidden_files": "1 file outside the application under test is hidden in this benchmark"}
    diagnosis, _ = diagnose(src)
    assert _rule(diagnosis) != "1" and diagnosis.category == Category.REAL_REGRESSION


def test_rule1_needs_an_earlier_green_run_of_the_same_tree():
    src = scenario()
    src.commits["head"]["files"] = []  # same tree as base, but base's run failed too: nothing of this tree passed
    src.runs[1]["conclusion"] = "failure"
    diagnosis, _ = diagnose(src)
    assert _rule(diagnosis) != "1"


def test_rule2_timed_out_line():
    log = ("##[group]Run python -m pytest tests -v\ntests/test_api.py::test_slow \n"
           "##[error]The action 'Run smoke tests' has timed out after 3 minutes.\nPost job cleanup.\n")
    diagnosis, _ = diagnose(scenario(log=log))
    assert diagnosis.category == Category.TIMEOUT and _rule(diagnosis) == "2"
    assert diagnosis.evidence[0].quote == "##[error]The action 'Run smoke tests' has timed out after 3 minutes."


@pytest.mark.parametrize(("duration", "expected"), [(300, Category.TIMEOUT), (290, Category.TIMEOUT),
                                                    (100, Category.REAL_REGRESSION)])
def test_rule2_cancelled_at_the_job_timeout(duration, expected):
    src = scenario(log="##[group]Run python -m pytest tests -v\n##[error]The operation was canceled.\n")
    job = src.jobs[2]["jobs"][0]
    job.update(conclusion="cancelled", completed_at=_t(duration))
    step = next(s for s in job["steps"] if s["name"] == "Run smoke tests")
    step.update(conclusion="cancelled")
    diagnosis, _ = diagnose(src)
    assert diagnosis.category == expected
    assert diagnosis.failing_step == "Run smoke tests"


def test_rule3_docker_job_failed_after_smoke_passed():
    log = ("##[group]Run docker build -t app:ci .\n#8 ERROR: process \"/bin/sh -c pip install -r requirements.txt\" "
           "did not complete successfully: exit code: 1\nERROR: failed to solve: process did not complete\n")
    files = [_diff("Dockerfile", "@@ -1,2 +1,2 @@\n-FROM python:3.11-slim\n+FROM python:3.11-slimm\n")]
    diagnosis, _ = diagnose(scenario(docker_fails=True, log=log, files=files))
    assert diagnosis.category == Category.BUILD and _rule(diagnosis) == "3"
    assert (diagnosis.failing_job, diagnosis.failing_step) == ("Docker image builds", "Build image")
    assert diagnosis.suspect_file == "Dockerfile"
    assert "FROM python:3.11-slim" in diagnosis.suggested_fix


def test_rule4_dependency_install_step():
    log = "##[group]Run pip install -r requirements.txt\nERROR: No matching distribution found for fastapi==99.0\n"
    files = [_diff("requirements.txt", "@@ -1 +1 @@\n-fastapi==0.110.0\n+fastapi==99.0\n")]
    diagnosis, _ = diagnose(scenario(failing_step="Install dependencies", log=log, files=files))
    assert diagnosis.category == Category.DEPENDENCY and _rule(diagnosis) == "4"
    assert diagnosis.suspect_file == "requirements.txt"
    assert diagnosis.suggested_fix.endswith("fastapi==0.110.0")


def test_rule5_setup_python_step():
    log = "##[group]Run actions/setup-python@v6\n##[error]Version 3.99 was not found in the local cache\n"
    diagnosis, _ = diagnose(scenario(failing_step="Run actions/setup-python@v6", log=log))
    assert diagnosis.category == Category.ENVIRONMENT and _rule(diagnosis) == "5"


@pytest.mark.parametrize(("filename", "patch", "rule", "category"), [
    ("requirements.txt", "@@ -1 +1 @@\n-numpy==1.26.4\n+numpy==2.0.0\n", "6a", Category.DEPENDENCY),
    (".github/workflows/ci.yml", "@@ -1 +1,3 @@\n+    env:\n+      APP_MODE: broken\n", "6b", Category.ENVIRONMENT),
    ("pytest.ini", "@@ -1 +1,2 @@\n [pytest]\n+addopts = --strict-markers -p no:cacheprovider\n", "6b",
     Category.ENVIRONMENT),
    ("Dockerfile", "@@ -1 +1 @@\n-WORKDIR /app\n+WORKDIR /ap\n", "6c", Category.BUILD),
    ("tests/test_rag.py", "@@ -1 +1,2 @@\n+    if random.random() < 0.3:\n+        assert False\n", "6d",
     Category.FLAKY),
    ("tests/test_rag.py", "@@ -1 +1 @@\n-    assert x == 1\n+    assert x == 2\n", "7", Category.REAL_REGRESSION),
    ("api/main.py", APP_PATCH, "6e", Category.REAL_REGRESSION),
    ("README.md", "@@ -1 +1 @@\n-# App\n+# The app\n", "7", Category.REAL_REGRESSION),
])
def test_rule6_what_changed(filename, patch, rule, category):
    diagnosis, _ = diagnose(scenario(files=[_diff(filename, patch)]))
    assert (_rule(diagnosis), diagnosis.category) == (rule, category)
    assert diagnosis.confidence == CONFIDENCE[rule]
    assert diagnosis.suspect_file == filename
    assert diagnosis.recommended_action == ACTION_FOR[category]


def test_rule6_order_requirements_before_application_code():
    files = [_diff("api/main.py", APP_PATCH), _diff("requirements.txt", "@@ -1 +1 @@\n-a==1\n+a==2\n")]
    diagnosis, _ = diagnose(scenario(files=files))
    assert diagnosis.category == Category.DEPENDENCY and diagnosis.suspect_file == "requirements.txt"


def test_rule7_fallback_when_nothing_is_readable():
    diagnosis, toolkit = diagnose(FakeSource())
    assert diagnosis.category == Category.REAL_REGRESSION and _rule(diagnosis) == "7"
    assert diagnosis.confidence == CONFIDENCE["7"]
    assert (diagnosis.failing_job, diagnosis.failing_step, diagnosis.suspect_file) == ("unknown", "unknown", None)
    assert diagnosis.evidence == [] and diagnosis.suggested_fix is None
    assert {call.tool for call in toolkit.calls} >= {"get_run", "get_jobs", "get_commit", "list_runs"}


# -- the fields --------------------------------------------------------------------------


def test_fields_of_a_regression():
    diagnosis, toolkit = diagnose(scenario())
    assert diagnosis.category == Category.REAL_REGRESSION and _rule(diagnosis) == "6e"
    assert (diagnosis.failing_job, diagnosis.failing_step) == ("Import & route smoke tests", "Run smoke tests")
    assert diagnosis.suspect_file == "api/main.py"
    assert diagnosis.root_cause.startswith("Rule 6e (application code changed)")
    assert diagnosis.suggested_fix == "Restore the lines the change removed from api/main.py:\n    return 200"
    assert 1 <= len(diagnosis.evidence) <= 3
    assert all(e.source == EvidenceSource.LOG and is_grounded(e, toolkit.calls) for e in diagnosis.evidence)
    assert diagnosis.evidence[0].quote == "tests/test_api.py::test_chat FAILED [100%]"


def test_fix_names_added_lines_when_nothing_was_removed():
    files = [_diff(".github/workflows/ci.yml", "@@ -5 +5,2 @@\n     runs-on: ubuntu-latest\n+    timeout-minutes: 1\n")]
    diagnosis, _ = diagnose(scenario(files=files))
    assert diagnosis.suggested_fix == ("Remove the lines the change added to .github/workflows/ci.yml:\n"
                                       "    timeout-minutes: 1")


def test_long_patches_are_read_in_full():
    lines = [f"+    value_{n} = compute({n})" for n in range(400)] + ["-    return 200"]
    diagnosis, toolkit = diagnose(scenario(files=[_diff("api/main.py", "@@ -1 +1,400 @@\n" + "\n".join(lines))]))
    assert diagnosis.suggested_fix.endswith("    return 200")
    assert any(call.tool == "compare" and "path" in call.arguments for call in toolkit.calls)


def test_registered_by_name():
    assert PREDICTORS["rules"] is RulesPredictor and RulesPredictor.name == "rules"


# -- helpers -----------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "kind"), [
    ("requirements.txt", "requirements"), ("requirements-eval.txt", "requirements"), ("poetry.lock", "requirements"),
    (".github/workflows/ci.yml", "workflow"), ("pytest.ini", "config"), (".env.example", "env"),
    ("Dockerfile", "docker"), (".dockerignore", "docker"), ("tests/test_api.py", "test"),
    ("tests/conftest.py", "test"), ("api/services/rag_service.py", "app"), ("docs/README.md", None),
])
def test_file_kind(path, kind):
    assert file_kind(path) == kind


def test_pyproject_kind_depends_on_what_changed():
    assert file_kind("pyproject.toml", '@@\n-dependencies = ["a==1"]\n+dependencies = ["a==2"]\n') == "requirements"
    assert file_kind("pyproject.toml", "@@\n+addopts = -x\n") == "config"


def test_job_timeout_minutes():
    lines = WORKFLOW.split("\n")
    assert job_timeout_minutes(lines, "Import & route smoke tests") == 5  # job level, not the step's 3
    assert job_timeout_minutes(lines, "smoke") == 5
    assert job_timeout_minutes(lines, "Docker image builds") is None
    assert job_timeout_minutes(lines, "no such job") is None
    quoted = ["jobs:", "  build:", "    name: 'Build (3.11)'", '    timeout-minutes: "10"  # minutes']
    assert job_timeout_minutes(quoted, "Build (3.11)") == 10
    assert job_timeout_minutes(["on: push"], "x") is None
