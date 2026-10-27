"""The flaky cases whose eval commit is an empty rerun commit."""

import json

from conftest import LAB, LAB_DIR, RERUN_CASES

from ci_triage.replay import ReplaySource


def test_five_rerun_cases():
    assert sorted(case.case_id for case in RERUN_CASES) == ["inj-050", "inj-052", "inj-054", "inj-055", "inj-058"]


def test_rerun_commit_has_empty_diff(rerun_case, truth):
    source = ReplaySource(rerun_case, LAB)
    commit = source.get_commit(rerun_case.eval_commit)
    assert commit["files"] == []
    injection = source.get_commit(truth[rerun_case.case_id].injection_commit)
    assert injection["files"], "the injection commit itself is not empty"
    assert commit["tree_sha"] == injection["tree_sha"]


def test_earlier_green_run_of_same_code_is_listed(rerun_case):
    record = json.loads((LAB_DIR / "injections" / f"{rerun_case.case_id}.json").read_text(encoding="utf-8"))
    runs = record["observed"]["runs"]
    position = [int(run["run_id"]) for run in runs].index(rerun_case.eval_run_id)
    earlier_green = {int(run["run_id"]) for run in runs[:position] if run["conclusion"] == "success"}
    assert earlier_green, "every rerun case has an earlier green run in the lab"

    source = ReplaySource(rerun_case, LAB)
    tree = source.get_commit(rerun_case.eval_commit)["tree_sha"]
    listed = {run["id"]: run for run in source.list_runs(limit=10_000)["workflow_runs"]}
    for run_id in earlier_green:
        assert run_id in listed
        assert listed[run_id]["conclusion"] == "success"
        assert listed[run_id]["head_commit"]["tree_id"] == tree
