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
    failing_job: str  # the job's key in ci.yml ("smoke" or "docker")
    failing_job_name: str  # its name as get_jobs shows it: ci.yml's name: at the eval commit, else the key
    failing_step: str  # the step's name as get_jobs shows it
    eval_run_id: int
    eval_commit_is_injection: bool
    injection_commit: str  # full sha
    revert_commit: str | None  # full sha of the commit that reverted the injection
    restored: tuple[str, ...]  # lines the revert put back in target_file
    removed: tuple[str, ...]  # lines the revert took out of target_file


def _reverts(git: Git) -> dict[str, str]:
    """Reverted sha -> reverting sha, from "This reverts commit <sha>." trailers."""
    log = git.text("log", "--format=%H%x1f%B%x1e", "HEAD")
    reverts = {}
    for record in filter(str.strip, log.split("\x1e")):
        sha, body = record.strip().split("\x1f", 1)
        for reverted in re.findall(r"This reverts commit ([0-9a-f]{40})", body):
            reverts.setdefault(reverted, sha)
    return reverts


def _revert_lines(git: Git, revert: str | None, path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(added, removed) lines of ``path`` in the revert commit, leaving out lines on both sides
    (inj-001's revert re-adds an unchanged line to restore the newline at the end of the file)."""
    if revert is None:
        return (), ()
    diff = git.text("show", "--format=", "--unified=0", "--no-color", revert, "--", path).splitlines()
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]
    both = set(added) & set(removed)
    return tuple(line for line in added if line not in both), tuple(line for line in removed if line not in both)


def job_names(workflow: str) -> dict[str, str]:
    """Job key -> display name (its ``name:``, else the key) in a workflow file indented by two spaces,
    like the lab's ci.yml; no YAML library needed."""
    names: dict[str, str] = {}
    in_jobs, key = False, None
    for line in workflow.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_jobs = line.split("#", 1)[0].strip() == "jobs:"
            key = None
        elif in_jobs and (match := re.match(r"^  ([\w-]+):\s*$", line)):
            key = match[1]
            names[key] = key
        elif in_jobs and key and (match := re.match(r"^    name:\s*(.+?)\s*$", line)):
            value = match[1]
            quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""
            names[key] = value[1:-1] if quoted else value
    return names


def load_ground_truth(lab_dir: Path | None = None) -> dict[str, GroundTruth]:
    root = Path(lab_dir or default_lab_dir())
    git = Git(root)
    reverts = _reverts(git)
    index = json.loads((root / "injections" / "index.json").read_text(encoding="utf-8"))
    truths = {}
    for entry in index["cases"]:
        spec = json.loads((root / "injections" / "specs" / f"{entry['id']}.json").read_text(encoding="utf-8"))
        injection = git.resolve_commit(entry["injection_commit"])
        revert = reverts.get(injection)
        restored, removed = _revert_lines(git, revert, entry["target_file"])
        eval_commit = git.resolve_commit(entry["eval_commit"])
        jobs = job_names(git.text("show", f"{eval_commit}:.github/workflows/ci.yml"))
        truths[entry["id"]] = GroundTruth(
            case_id=entry["id"],
            category=entry["category"],
            target_file=entry["target_file"],
            mutation=spec["description"],
            find=spec["find"],
            replace=spec["replace"],
            expected_failing_tests=tuple(entry["expected_failing_tests"]),
            failing_job=entry["observed_failing_job"],
            failing_job_name=jobs[entry["observed_failing_job"]],
            failing_step=entry["failing_step"],
            eval_run_id=int(entry["eval_run_id"]),
            eval_commit_is_injection=entry["eval_commit_is_injection"],
            injection_commit=injection,
            revert_commit=revert,
            restored=restored,
            removed=removed,
        )
    return truths
