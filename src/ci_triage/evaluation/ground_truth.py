"""Ground truth for each lab case: what was injected, where, and how it was undone.

For scoring only. The replay layer must never import this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ci_triage._git import Git
from ci_triage.labpaths import default_lab_dir


@dataclass(frozen=True)
class GroundTruth:
    case_id: str
    category: str
    target_file: str
    mutation: str  # the spec's description
    find: str
    replace: str
    expected_failing_tests: tuple[str, ...]
    eval_run_id: int
    eval_commit_is_injection: bool
    injection_commit: str  # full sha
    revert_commit: str | None  # full sha of the commit that reverted the injection


def _reverts(git: Git) -> dict[str, str]:
    """Reverted sha -> reverting sha, from "This reverts commit <sha>." trailers."""
    log = git.text("log", "--format=%H%x1f%B%x1e", "HEAD")
    reverts = {}
    for record in filter(str.strip, log.split("\x1e")):
        sha, body = record.strip().split("\x1f", 1)
        for reverted in re.findall(r"This reverts commit ([0-9a-f]{40})", body):
            reverts.setdefault(reverted, sha)
    return reverts


def load_ground_truth(lab_dir: Path | None = None) -> dict[str, GroundTruth]:
    root = Path(lab_dir or default_lab_dir())
    git = Git(root)
    reverts = _reverts(git)
    index = json.loads((root / "injections" / "index.json").read_text(encoding="utf-8"))
    truths = {}
    for entry in index["cases"]:
        spec = json.loads((root / "injections" / "specs" / f"{entry['id']}.json").read_text(encoding="utf-8"))
        injection = git.resolve_commit(entry["injection_commit"])
        truths[entry["id"]] = GroundTruth(
            case_id=entry["id"],
            category=entry["category"],
            target_file=entry["target_file"],
            mutation=spec["description"],
            find=spec["find"],
            replace=spec["replace"],
            expected_failing_tests=tuple(entry["expected_failing_tests"]),
            eval_run_id=int(entry["eval_run_id"]),
            eval_commit_is_injection=entry["eval_commit_is_injection"],
            injection_commit=injection,
            revert_commit=reverts.get(injection),
        )
    return truths
