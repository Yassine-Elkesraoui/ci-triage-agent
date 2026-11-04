"""A synthetic failing run built in code, unrelated to the lab: a small library-fees project whose
fee rate was halved, so one test fails. Used by the LLM baseline tests and the live smoke test."""

from __future__ import annotations

from ci_triage.evaluation.ground_truth import GroundTruth
from ci_triage.github_source import JSON, NotAvailable
from ci_triage.tools import Mode, Toolkit, make_toolkit

RUN_ID = 900001
GREEN_RUN_ID = 900000
HEAD = "c0ffee" + "1" * 34
BASE = "c0ffee" + "0" * 34
JOB_ID = 77001
JOB_NAME = "unit tests"
STEP_NAME = "Run unit tests"
WORKFLOW_PATH = ".github/workflows/test.yml"

WORKFLOW = """name: test
on: [push]
jobs:
  unit:
    name: unit tests
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install -e .
      - name: Run unit tests
        run: python -m pytest -q
"""

FEES_PY = '''DAILY_FEE = 0.125


def late_fee(days: int) -> float:
    return round(days * DAILY_FEE, 2)
'''

PATCH = """@@ -1,4 +1,4 @@
-DAILY_FEE = 0.25
+DAILY_FEE = 0.125


 def late_fee(days: int) -> float:"""

LOG_LINES = [
    "##[group]Run actions/checkout@v4",
    "Syncing repository: example/shelfwise",
    "##[endgroup]",
    "##[group]Run pip install -e .",
    "Successfully installed shelfwise-0.3.0",
    "##[endgroup]",
    "##[group]Run python -m pytest -q",
    "python -m pytest -q",
    "..F....                                                                  [100%]",
    "=================================== FAILURES ===================================",
    "__________________________ test_late_fee_for_20_days ___________________________",
    "",
    "    def test_late_fee_for_20_days():",
    ">       assert late_fee(days=20) == 5.0",
    "E       assert 2.5 == 5.0",
    "E        +  where 2.5 = late_fee(days=20)",
    "",
    "tests/test_fees.py:7: AssertionError",
    "=========================== short test summary info ============================",
    "FAILED tests/test_fees.py::test_late_fee_for_20_days - assert 2.5 == 5.0",
    "1 failed, 6 passed in 0.12s",
    "##[error]Process completed with exit code 1.",
    "##[endgroup]",
    "Post job cleanup.",
]

VALID_DIAGNOSIS = """{
  "category": "real_regression",
  "confidence": 0.9,
  "failing_job": "unit tests",
  "failing_step": "Run unit tests",
  "suspect_file": "shelfwise/fees.py",
  "evidence": [
    {"quote": "E       assert 2.5 == 5.0", "source": "log"},
    {"quote": "+DAILY_FEE = 0.125", "source": "diff"}
  ],
  "root_cause": "The daily late fee was halved, so late_fee(20) returns 2.5 instead of 5.0.",
  "suggested_fix": "DAILY_FEE = 0.25",
  "recommended_action": "fix_code"
}"""


def _log(lines: list[str]) -> str:
    return "".join(f"2026-10-01T10:00:{min(n, 59):02d}.0000000Z {line}\n" for n, line in enumerate(lines))


class SyntheticSource:
    """A GitHubSource for one failing run. ``big`` inflates the log, workflow and diff far past every cap."""

    def __init__(self, big: bool = False) -> None:
        self.big = big
        noise = [f"tests/test_shelf_{i:04d}.py::test_case_{i} PASSED" + " ." * 40 for i in range(3000)] if big else []
        self.log = _log(LOG_LINES[:8] + noise + LOG_LINES[8:])
        self.workflow = WORKFLOW + ("".join(f"      - name: Extra step {i}\n        run: echo step {i} " + "x" * 60
                                            + "\n" for i in range(400)) if big else "")
        self.patch = PATCH + ("".join(f"\n+# padding line {i} " + "y" * 80 for i in range(600)) if big else "")
        self.calls: list[str] = []

    def _run(self, run_id: int, sha: str, conclusion: str, created: str) -> JSON:
        return {"id": run_id, "name": "test", "path": WORKFLOW_PATH, "display_title": "<redacted>",
                "run_number": run_id - 899000, "run_attempt": 1, "event": "push", "status": "completed",
                "conclusion": conclusion, "head_branch": "main", "head_sha": sha, "created_at": created,
                "run_started_at": created, "updated_at": created.replace(":00:00", ":01:30")}

    def get_run(self, run_id: int) -> JSON | NotAvailable:
        self.calls.append("get_run")
        if run_id == RUN_ID:
            return self._run(RUN_ID, HEAD, "failure", "2026-10-01T10:00:00Z")
        if run_id == GREEN_RUN_ID:
            return self._run(GREEN_RUN_ID, BASE, "success", "2026-09-30T10:00:00Z")
        return NotAvailable(f"run {run_id}", "no such run")

    def get_jobs(self, run_id: int) -> JSON | NotAvailable:
        self.calls.append("get_jobs")
        if run_id != RUN_ID:
            return NotAvailable(f"jobs of run {run_id}", "not captured")
        t = lambda s: f"2026-10-01T10:00:{s:02d}Z"  # noqa: E731
        steps = [(1, "Set up job", "success", 0, 2), (2, "Run actions/checkout@v4", "success", 2, 4),
                 (3, "Run actions/setup-python@v5", "success", 4, 9), (4, "Install", "success", 9, 20),
                 (5, STEP_NAME, "failure", 20, 26), (6, "Complete job", "success", 26, 27)]
        return {"total_count": 1, "jobs": [{
            "id": JOB_ID, "name": JOB_NAME, "status": "completed", "conclusion": "failure",
            "started_at": t(0), "completed_at": t(27), "labels": ["ubuntu-latest"],
            "steps": [{"number": n, "name": name, "status": "completed", "conclusion": c, "started_at": t(a),
                       "completed_at": t(b)} for n, name, c, a, b in steps]}]}

    def get_job_log(self, job_id: int) -> str | NotAvailable:
        self.calls.append("get_job_log")
        return self.log if job_id == JOB_ID else NotAvailable(f"log of job {job_id}", "no such job")

    def get_commit(self, sha: str) -> JSON | NotAvailable:
        self.calls.append("get_commit")
        return NotAvailable(f"commit {sha}", "not part of the synthetic case")

    def compare(self, base: str, head: str) -> JSON | NotAvailable:
        self.calls.append("compare")
        if base != BASE or head not in (HEAD, "HEAD"):
            return NotAvailable(f"compare {base}...{head}", "out of scope")
        return {"base_commit": BASE, "head_commit": HEAD, "files": [
            {"filename": "shelfwise/fees.py", "status": "modified", "additions": 1, "deletions": 1,
             "patch": self.patch}]}

    def get_file(self, path: str, ref: str | None = None) -> JSON | NotAvailable:
        self.calls.append("get_file")
        files = {WORKFLOW_PATH: self.workflow, "shelfwise/fees.py": FEES_PY}
        if path not in files:
            return NotAvailable(f"{path} at {ref or 'HEAD'}", "no such file")
        content = files[path]
        return {"type": "file", "path": path, "sha": "f" * 40, "size": len(content), "encoding": "utf-8",
                "content": content}

    def list_runs(self, branch: str = "main", limit: int = 100) -> JSON | NotAvailable:
        self.calls.append("list_runs")
        runs = [self._run(RUN_ID, HEAD, "failure", "2026-10-01T10:00:00Z"),
                self._run(GREEN_RUN_ID, BASE, "success", "2026-09-30T10:00:00Z")]
        return {"total_count": len(runs), "workflow_runs": runs[:limit]}


def toolkit(mode: Mode | str = Mode.FULL, big: bool = False) -> tuple[Toolkit, SyntheticSource]:
    source = SyntheticSource(big=big)
    return make_toolkit(source, HEAD, mode), source


def truth(case_id: str = "synthetic-1") -> GroundTruth:
    return GroundTruth(
        case_id=case_id, category="real_regression", target_file="shelfwise/fees.py",
        mutation="halve the daily fee", find="DAILY_FEE = 0.25", replace="DAILY_FEE = 0.125",
        expected_failing_tests=("tests/test_fees.py::test_late_fee_for_20_days",), failing_job="unit",
        failing_job_name=JOB_NAME, failing_step=STEP_NAME, eval_run_id=RUN_ID, eval_commit_is_injection=True,
        injection_commit=HEAD, revert_commit=None, restored=("DAILY_FEE = 0.25",), removed=("DAILY_FEE = 0.125",))
