"""Every ReplaySource method works for every case, and missing data is NotAvailable."""

from conftest import CASES, LAB

from ci_triage.github_source import GitHubSource, NotAvailable
from ci_triage.replay import ReplaySource
from ci_triage.replay.redaction import REDACTED


def _failed_jobs(jobs):
    return [job for job in jobs["jobs"] if job["conclusion"] in ("failure", "cancelled")]


def test_sixty_cases():
    assert len(CASES) == 60
    assert len({case.case_id for case in CASES}) == 60
    assert all(len(case.eval_commit) == 40 for case in CASES)


def test_implements_protocol(source):
    assert isinstance(source, GitHubSource)


def test_get_run(source, case):
    run = source.get_run(case.eval_run_id)
    assert run["id"] == case.eval_run_id
    assert run["head_sha"] == case.eval_commit
    assert run["status"] == "completed"
    assert run["conclusion"] in ("failure", "cancelled")
    assert run["display_title"] == REDACTED
    assert run["head_commit"]["message"] == REDACTED
    assert str(case.eval_run_id) == str(source.get_run(str(case.eval_run_id))["id"])


def test_get_jobs_with_steps(source, case):
    jobs = source.get_jobs(case.eval_run_id)
    assert jobs["jobs"], "no jobs"
    assert all(job["run_id"] == case.eval_run_id for job in jobs["jobs"])
    failed = _failed_jobs(jobs)
    assert len(failed) == 1
    assert failed[0]["steps"], "failed job has no steps"


def test_get_job_log_of_failed_job(source, case):
    job = _failed_jobs(source.get_jobs(case.eval_run_id))[0]
    log = source.get_job_log(job["id"])
    assert isinstance(log, str) and len(log.splitlines()) > 5


def test_other_jobs_have_no_log(source, case):
    for job in source.get_jobs(case.eval_run_id)["jobs"]:
        if job["conclusion"] not in ("failure", "cancelled"):
            result = source.get_job_log(job["id"])
            assert isinstance(result, NotAvailable)
            assert job["conclusion"] in result.reason


def test_get_commit(source, case):
    commit = source.get_commit(case.eval_commit)
    assert set(commit) == {"sha", "tree_sha", "author_date", "parents", "message", "files"}
    assert commit["sha"] == case.eval_commit
    assert commit["message"] == REDACTED
    assert len(commit["parents"]) == 1
    assert source.get_commit(case.eval_commit[:7])["sha"] == case.eval_commit
    parent = source.get_commit(commit["parents"][0])  # the baseline, or an earlier commit of the case
    assert parent["sha"] == commit["parents"][0] and parent["message"] == REDACTED


def test_compare(source, case):
    diff = source.compare(source.baseline_commit, case.eval_commit)
    assert diff["base_commit"] == source.baseline_commit and diff["head_commit"] == case.eval_commit
    assert diff["files"]
    assert source.compare(source.baseline_commit, "main") == diff


def test_get_file(source, case):
    workflow = source.get_file(".github/workflows/ci.yml")
    assert workflow["type"] == "file" and "jobs:" in workflow["content"]
    assert source.get_file(".github/workflows/ci.yml", case.eval_commit) == workflow
    root = source.get_file("")
    assert root["type"] == "dir"
    assert {"api", "tests", "Dockerfile", "requirements.txt"} <= {e["name"] for e in root["entries"]}
    assert source.get_file("api/services")["type"] == "dir"


def test_list_runs(source, case):
    runs = source.list_runs()
    assert runs["workflow_runs"][0]["id"] == case.eval_run_id
    assert runs["total_count"] >= len(runs["workflow_runs"])
    assert len(source.list_runs(limit=3)["workflow_runs"]) == min(3, runs["total_count"])
    assert source.list_runs(branch="dev") == {"total_count": 0, "workflow_runs": []}


def test_missing_data_is_not_available(source, case):
    not_available = [
        source.get_run(1),
        source.get_run("not-a-run"),
        source.get_jobs(1),
        source.get_job_log(1),
        source.get_job_log(None),
        source.get_commit("0000000"),
        source.get_commit("not-a-sha"),
        source.get_commit(None),
        source.compare("0000000", "main"),
        source.compare(case.eval_commit, "not-a-sha"),
        source.get_file("does/not/exist.py"),
        source.get_file("../outside.txt"),
        source.get_file("README.md", ref="deadbeef"),
        source.get_file("README.md", ref="--all"),
        source.get_file("injections"),
        source.get_file("injections/index.json"),
    ]
    for result in not_available:
        assert isinstance(result, NotAvailable), result
        assert result.to_dict()["available"] is False


def test_runs_without_captured_jobs_are_not_available(source):
    uncaptured = [r["id"] for r in source.list_runs(limit=10_000)["workflow_runs"] if r["id"] not in LAB.jobs_snapshots]
    for run_id in uncaptured:
        result = source.get_jobs(run_id)
        assert isinstance(result, NotAvailable) and "not captured" in result.reason


def test_green_runs_have_no_logs():
    """Every job of every green run any case can see: a NotAvailable, never an exception."""
    checked = 0
    for case in CASES:
        source = ReplaySource(case, LAB)
        for run in source.list_runs(limit=10_000)["workflow_runs"]:
            if run["conclusion"] == "success" and run["id"] in LAB.jobs_snapshots:
                for job in source.get_jobs(run["id"])["jobs"]:
                    assert isinstance(source.get_job_log(job["id"]), NotAvailable)
                    checked += 1
    assert checked > 0
