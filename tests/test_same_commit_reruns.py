"""Phase B change f: list_runs and get_run show earlier runs of the triaged commit, created before the triaged
run, for every predictor (both modes); and the 60 v1.0 cases still get byte-identical bundles and tool outputs."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta

import pytest
import replay_snapshot
from conftest import CASES, LAB, RERUN_CASES

from ci_triage.github_source import NotAvailable
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import gather_bundle
from ci_triage.replay import ReplaySource
from ci_triage.tools import Mode, make_toolkit
from ci_triage.tools.models import NotAvailableResult, RunInfo, RunsResult

SNAPSHOT = json.loads(replay_snapshot.SNAPSHOT.read_text(encoding="utf-8"))


# -- v1.0: nothing changes ------------------------------------------------------------------------------------


def test_snapshot_covers_every_v1_0_case_in_both_modes():
    assert sorted(SNAPSHOT) == sorted(case.case_id for case in CASES) and len(SNAPSHOT) == 60
    assert all(sorted(modes) == sorted(m.value for m in Mode) for modes in SNAPSHOT.values())


@pytest.mark.parametrize("mode", list(Mode))
def test_v1_0_bundles_and_tool_outputs_are_byte_identical(case, mode):
    now = replay_snapshot.case_digests(case, LAB, mode)
    before = SNAPSHOT[case.case_id][Mode(mode).value]
    changed = sorted(k for k in before.keys() & now.keys() if before[k] != now[k])
    gone, new = sorted(before.keys() - now.keys()), sorted(now.keys() - before.keys())
    assert not (changed or gone or new), f"{case.case_id} {mode}: changed {changed}, gone {gone}, new {new}"


def test_v1_0_cases_have_no_same_commit_reruns(case):
    assert ReplaySource(case, LAB).same_commit_reruns == frozenset()


# -- a same-commit rerun, added to a copy of the lab ----------------------------------------------------------


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_like(run: dict, run_id: int, created: datetime, conclusion: str) -> dict:
    return run | {"id": run_id, "run_number": run["run_number"] + 1000, "conclusion": conclusion,
                  "status": "completed", "created_at": _iso(created), "run_started_at": _iso(created),
                  "updated_at": _iso(created + timedelta(minutes=2))}


def _with_runs(case, *runs: tuple[int, float, str]):
    """A copy of the lab with runs of ``case``'s eval commit added: (id, minutes after the triaged run was
    created, conclusion)."""
    triaged = LAB.run(case.eval_run_id)
    created = _ts(triaged["created_at"])
    extra = {run_id: _run_like(triaged, run_id, created + timedelta(minutes=minutes), conclusion)
             for run_id, minutes, conclusion in runs}
    return dataclasses.replace(LAB, listed_runs={**LAB.listed_runs, **extra})


EARLIER_GREEN, EARLIER_RED, LATER = 990000001, 990000002, 990000003
SAMPLE = [CASES[0], CASES[30], RERUN_CASES[0]]  # an injection case, another category, an empty-rerun case


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("sample", SAMPLE, ids=[c.case_id for c in SAMPLE])
def test_earlier_runs_of_the_triaged_commit_are_shown(sample, mode):
    lab = _with_runs(sample, (EARLIER_GREEN, -30, "success"), (EARLIER_RED, -15, "failure"), (LATER, 24 * 60, "success"))
    source = ReplaySource(sample, lab)
    assert source.same_commit_reruns == {EARLIER_GREEN, EARLIER_RED}
    toolkit = make_toolkit(source, sample.eval_commit, mode)

    listing = toolkit.call("list_runs", {"limit": 100})
    assert isinstance(listing, RunsResult)
    shown = {run.id: run for run in listing.runs}
    assert listing.runs[0].id == sample.eval_run_id  # still the newest
    for run_id, conclusion in ((EARLIER_GREEN, "success"), (EARLIER_RED, "failure")):
        assert shown[run_id].head_sha == sample.eval_commit and shown[run_id].conclusion == conclusion
        run = toolkit.call("get_run", {"run_id": run_id})
        assert isinstance(run, RunInfo) and run.head_sha == sample.eval_commit and run.conclusion == conclusion
    assert [r.id for r in listing.runs].index(EARLIER_RED) < [r.id for r in listing.runs].index(EARLIER_GREEN)

    # no time travel: a run of the same commit created after the triaged run finished stays out
    assert LATER not in shown
    later = toolkit.call("get_run", {"run_id": LATER})
    assert isinstance(later, NotAvailableResult) and later.reason == "not available in this case's scope"


def test_a_rerun_started_after_the_triaged_run_is_not_a_same_commit_rerun():
    case = CASES[0]
    source = ReplaySource(case, _with_runs(case, (EARLIER_GREEN, 0, "success"), (LATER, 1, "success")))
    assert source.same_commit_reruns == frozenset()


def test_runs_of_other_commits_are_not_same_commit_reruns():
    case, other = CASES[0], CASES[1]
    triaged = LAB.run(other.eval_run_id)
    created = _ts(LAB.run(case.eval_run_id)["created_at"]) - timedelta(minutes=5)
    run = _run_like(triaged, EARLIER_GREEN, created, "success")  # another case's commit
    source = ReplaySource(case, dataclasses.replace(LAB, listed_runs={**LAB.listed_runs, EARLIER_GREEN: run}))
    assert source.same_commit_reruns == frozenset()
    assert isinstance(source.get_run(EARLIER_GREEN), NotAvailable)


@pytest.mark.parametrize("mode", list(Mode))
def test_the_bundle_does_not_change_when_a_same_commit_rerun_exists(mode):
    """The one-call bundle has no run list; its diff baseline skips runs of the triaged commit."""
    one_call = replay_snapshot.ONE_CALL
    case = CASES[0]
    bundles = []
    for lab in (LAB, _with_runs(case, (EARLIER_GREEN, -30, "success"))):
        toolkit = make_toolkit(ReplaySource(case, lab), case.eval_commit, mode)
        bundles.append(gather_bundle(CaseView(case.eval_run_id), toolkit, one_call.config.budget,
                                     len(one_call.prompt.system),
                                     one_call.config.generation.max_output_tokens).user_message())
    assert bundles[0] == bundles[1]
