"""The lab's cases, reduced to their triage targets.

Only ``id``, ``eval_run_id``, ``eval_commit`` and ``injection_commit`` (which
bounds the case's scope, see ``ReplaySource``) are read from
``injections/index.json``; labels live in ``ci_triage.evaluation``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ci_triage.github_source import TriageTarget
from ci_triage.replay.labdata import LabData, load_lab


@dataclass(frozen=True)
class Case:
    case_id: str
    eval_run_id: int
    eval_commit: str  # full sha
    injection_commit: str  # full sha; the eval commit or an ancestor of it

    @property
    def target(self) -> TriageTarget:
        return TriageTarget(run_id=self.eval_run_id, head_sha=self.eval_commit)


def load_cases(lab: LabData | None = None) -> list[Case]:
    lab = lab or load_lab()
    index = json.loads((lab.root / "injections" / "index.json").read_text(encoding="utf-8"))
    cases = []
    for entry in index["cases"]:
        run_id = int(entry["eval_run_id"])
        head_sha = lab.run(run_id)["head_sha"]
        if not head_sha.startswith(entry["eval_commit"]):
            raise ValueError(f"{entry['id']}: eval run {run_id} ran on {head_sha}, not {entry['eval_commit']}")
        injection = lab.git.resolve_commit(entry["injection_commit"])
        if injection is None or not lab.git.is_ancestor(injection, head_sha):
            raise ValueError(f"{entry['id']}: injection commit {entry['injection_commit']} is not behind the eval commit")
        cases.append(Case(case_id=entry["id"], eval_run_id=run_id, eval_commit=head_sha, injection_commit=injection))
    return cases
