"""Case scope: each case reaches its baseline and its own commits and runs, nothing else."""

import json

from conftest import CASES, LAB, LAB_DIR, TRUTH

from ci_triage._git import EMPTY_TREE
from ci_triage.github_source import NotAvailable
from ci_triage.replay import ReplaySource
from ci_triage.replay.source import OUT_OF_SCOPE, diff_payload

EXTRA_RUN = 37651788145  # unlabelled first attempt at inj-039, belongs to no case


def _owned(case_id):
    """Runs and commits a case owns, from its record and ground truth (evaluation side)."""
    record = json.loads((LAB_DIR / "injections" / f"{case_id}.json").read_text(encoding="utf-8"))
    runs = {int(record["run_id"])} | {int(r["run_id"]) for r in record["observed"].get("runs") or []}
    commits = {record["commit"]} | {r["commit"] for r in record["observed"].get("runs") or []}
    commits = {LAB.git.resolve_commit(sha) for sha in commits} | {TRUTH[case_id].revert_commit}
    return runs, commits


OWNED = {case.case_id: _owned(case.case_id) for case in CASES}


def _refused(result):
    return isinstance(result, NotAvailable) and result.reason == OUT_OF_SCOPE


def test_nothing_from_other_cases_is_reachable(source, case):
    listed = {run["id"] for run in source.list_runs(limit=10_000)["workflow_runs"]}
    for other, (runs, commits) in OWNED.items():
        if other == case.case_id:
            continue
        assert not listed & runs, other
        for run_id in runs | {EXTRA_RUN}:
            assert _refused(source.get_run(run_id)), (other, run_id)
            assert _refused(source.get_jobs(run_id)), (other, run_id)
            for job in LAB.jobs_snapshots.get(run_id, {"jobs": []})["jobs"]:
                assert _refused(source.get_job_log(job["id"])), (other, job["id"])
        for sha in commits:
            if sha == source.baseline_commit:  # the previous case's revert: metadata only
                assert "files" not in source.get_commit(sha)
                continue
            assert _refused(source.get_commit(sha)), (other, sha)
            assert _refused(source.get_file(".github/workflows/ci.yml", sha)), (other, sha)
            assert _refused(source.compare(sha, "main")), (other, sha)
            assert _refused(source.compare(source.baseline_commit, sha)), (other, sha)


def test_reachable_logs_are_the_cases_own(source, case):
    own_runs, _ = OWNED[case.case_id]
    for run in source.list_runs(limit=10_000)["workflow_runs"]:
        jobs = source.get_jobs(run["id"])
        if isinstance(jobs, NotAvailable):
            continue
        for job in jobs["jobs"]:
            if isinstance(source.get_job_log(job["id"]), str):
                assert run["id"] in own_runs


def test_baseline(source, case):
    runs = source.list_runs(limit=10_000)["workflow_runs"]
    baseline = runs[-1]
    assert baseline["head_sha"] == source.baseline_commit
    assert baseline["conclusion"] == "success"
    assert source.baseline_commit == LAB.git.commit_header(case.injection_commit)[1][0]
    assert all(run["head_sha"] != source.baseline_commit for run in runs[:-1])
    assert isinstance(source.get_jobs(baseline["id"]), NotAvailable)  # the lab captured no jobs for baselines
    commit = source.get_commit(source.baseline_commit)
    assert "files" not in commit and OUT_OF_SCOPE in commit["diff"]
    assert source.get_file("requirements.txt", source.baseline_commit)["type"] == "file"
    assert _refused(source.get_commit(commit["parents"][0]))


def test_compare_is_exactly_the_cases_mutation(source, case):
    truth = TRUTH[case.case_id]
    diff = source.compare(source.baseline_commit, case.eval_commit)
    injection_parent = LAB.git.commit_header(truth.injection_commit)[1][0]
    assert diff["files"] == [dict(f) for f in LAB.git.diff_files(injection_parent, truth.injection_commit)]
    assert {f["filename"] for f in diff["files"]} == {truth.target_file}
    lines = [line for f in diff["files"] for line in f["patch"].splitlines()]
    added = [line[1:] for line in lines if line.startswith("+")]
    removed = [line[1:] for line in lines if line.startswith("-")]
    # find/replace may carry unchanged context lines and are substrings of the lines they edit
    find = set(filter(None, map(str.strip, truth.find.splitlines())))
    replace = set(filter(None, map(str.strip, truth.replace.splitlines())))
    assert find != replace
    for fragment in replace - find:
        assert any(fragment in line for line in added), fragment
    for fragment in find - replace:
        assert any(fragment in line for line in removed), fragment


def test_hidden_files_marker_never_on_eval_commit(source, case):
    assert "hidden_files" not in source.get_commit(case.eval_commit)
    assert "hidden_files" not in source.compare(source.baseline_commit, case.eval_commit)


def _first_parent_payloads():
    for sha in LAB.git.rev_list("HEAD"):
        parents = LAB.git.commit_header(sha)[1]
        yield sha, diff_payload(LAB.git, parents[0] if parents else EMPTY_TREE, sha)


def test_hidden_files_marker_wording():
    payloads = dict(_first_parent_payloads())
    fully_hidden = [p for p in payloads.values() if "hidden_files" in p and not p["files"]]
    partly_hidden = [p for p in payloads.values() if "hidden_files" in p and p["files"]]
    truly_empty = [p for p in payloads.values() if not p["files"] and "hidden_files" not in p]
    assert fully_hidden and partly_hidden and truly_empty
    for payload in fully_hidden + partly_hidden:
        assert payload["hidden_files"].endswith("outside the application under test are hidden in this benchmark") \
            or payload["hidden_files"] == "1 file outside the application under test is hidden in this benchmark"
