"""Digests of what the replay serves for every v1.0 case, in both modes: the one-call bundle, a fixed battery
of tool calls, and every tool output the rules predictor reads. Not collected by pytest.

``tests/data/replay_v1_0.json`` was written by this script from commit 38a97db, before change f (phase B,
section 2) touched the replay; ``test_same_commit_reruns.py`` recomputes it and requires every digest to be
equal, i.e. byte-identical output.

    python tests/replay_snapshot.py --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ci_triage.llm.config import load_config
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import gather_bundle
from ci_triage.predictors.rules import RulesPredictor
from ci_triage.replay import LabData, ReplaySource
from ci_triage.replay.cases import Case
from ci_triage.tools import Mode, make_toolkit, render
from ci_triage.tools.models import JobsResult

SNAPSHOT = Path(__file__).parent / "data" / "replay_v1_0.json"
ONE_CALL = load_config()  # configs/one_call_v2.toml: the bundle of one-call v2, agent v1 and agent v2


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def case_digests(case: Case, lab: LabData, mode: Mode) -> dict[str, str]:
    """Label -> digest of what the replay served, for one case in one mode."""
    out: dict[str, str] = {}
    toolkit = lambda: make_toolkit(ReplaySource(case, lab), case.eval_commit, mode)  # noqa: E731

    bundle = gather_bundle(CaseView(case.eval_run_id), toolkit(), ONE_CALL.config.budget,
                           len(ONE_CALL.prompt.system), ONE_CALL.config.generation.max_output_tokens)
    out["bundle"] = digest(bundle.user_message())

    kit = toolkit()
    source = ReplaySource(case, lab)

    def call(label: str, tool: str, args: dict) -> object:
        result = kit.call(tool, args)
        out[label] = digest(render(result))
        return result

    call("list_runs()", "list_runs", {})
    listing = call("list_runs(limit=100)", "list_runs", {"limit": 100})
    for run in listing.runs:
        call(f"get_run({run.id})", "get_run", {"run_id": run.id})
        jobs = call(f"get_jobs({run.id})", "get_jobs", {"run_id": run.id})
        if isinstance(jobs, JobsResult):
            for job in jobs.jobs:
                call(f"extract_failure_block({job.id})", "extract_failure_block", {"job_id": job.id})
                call(f"get_job_log({job.id})", "get_job_log", {"job_id": job.id})
    call("get_commit(HEAD)", "get_commit", {"sha": "HEAD"})
    call("get_commit(eval)", "get_commit", {"sha": case.eval_commit})
    call("compare(baseline, eval)", "compare", {"base": source.baseline_commit, "head": case.eval_commit})
    call("get_file(workflow)", "get_file", {"path": ".github/workflows/ci.yml"})
    call("get_file(root)", "get_file", {"path": ""})

    rules_kit = toolkit()
    diagnosis = RulesPredictor().predict(CaseView(case.eval_run_id), rules_kit)
    out["rules diagnosis"] = digest(diagnosis.model_dump_json())
    for n, logged in enumerate(rules_kit.calls):
        out[f"rules call {n}: {logged.tool} {json.dumps(logged.arguments, sort_keys=True)}"] = \
            digest(json.dumps(logged.output, sort_keys=True))
    return out


def snapshot(cases: list[Case], lab: LabData) -> dict[str, dict[str, dict[str, str]]]:
    return {case.case_id: {mode.value: case_digests(case, lab, mode) for mode in Mode} for case in cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help=f"write {SNAPSHOT}")
    args = parser.parse_args()
    from ci_triage.replay import load_cases, load_lab

    lab = load_lab()
    data = snapshot(load_cases(lab), lab)
    n = sum(len(d) for modes in data.values() for d in modes.values())
    print(f"{len(data)} cases, {n} digests")
    if args.write:
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {SNAPSHOT}")


if __name__ == "__main__":
    main()
