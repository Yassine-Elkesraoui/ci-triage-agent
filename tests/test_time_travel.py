"""No time travel: nothing created after the eval run finished is visible."""

import json

from conftest import LAB, LAB_DIR

from ci_triage.github_source import NotAvailable


def test_no_run_newer_than_eval_run(source, case):
    eval_created = LAB.run(case.eval_run_id)["created_at"]
    runs = source.list_runs(limit=10_000)["workflow_runs"]
    assert runs[0]["id"] == case.eval_run_id
    newer = [run["id"] for run in runs if run["created_at"] > eval_created]
    assert not newer
    assert all(run["status"] == "completed" and run["updated_at"] <= LAB.run(case.eval_run_id)["updated_at"]
               for run in runs)


def test_later_runs_do_not_exist(source, case):
    eval_created = LAB.run(case.eval_run_id)["created_at"]
    later = [run for run in LAB.listed_runs.values() if run["created_at"] > eval_created]
    for run in later:
        assert isinstance(source.get_run(run["id"]), NotAvailable)
        assert isinstance(source.get_jobs(run["id"]), NotAvailable)
        for job in LAB.jobs_snapshots.get(run["id"], {"jobs": []})["jobs"]:
            assert isinstance(source.get_job_log(job["id"]), NotAvailable)


def test_revert_is_invisible(source, case, truth):
    revert = truth[case.case_id].revert_commit
    assert revert
    assert isinstance(source.get_commit(revert), NotAvailable)
    assert isinstance(source.get_file(truth[case.case_id].target_file, revert), NotAvailable)
    assert revert not in {run["head_sha"] for run in source.list_runs(limit=10_000)["workflow_runs"]}


def test_later_reruns_are_invisible(source, case):
    record = json.loads((LAB_DIR / "injections" / f"{case.case_id}.json").read_text(encoding="utf-8"))
    run_ids = [int(run["run_id"]) for run in record["observed"].get("runs") or []]
    if case.eval_run_id not in run_ids:
        return
    later = run_ids[run_ids.index(case.eval_run_id) + 1:]
    listed = {run["id"] for run in source.list_runs(limit=10_000)["workflow_runs"]}
    assert not listed & set(later)
    for run_id in later:
        assert isinstance(source.get_run(run_id), NotAvailable)
