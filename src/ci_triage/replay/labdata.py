"""The raw lab data the replay layer serves: API snapshots, saved logs and git.

Only plumbing is read from the injection records here (which run and job each
saved log belongs to); labels such as category or mutation are never loaded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ci_triage._git import Git
from ci_triage.labpaths import default_lab_dir


@dataclass
class LabData:
    root: Path
    git: Git
    listed_runs: dict[int, dict[str, Any]]   # runs-main.json entries
    run_snapshots: dict[int, dict[str, Any]]  # api/<run_id>/run.json
    jobs_snapshots: dict[int, dict[str, Any]]  # api/<run_id>/jobs.json
    run_of_job: dict[int, int]
    log_paths: dict[int, Path]  # job id -> saved log
    _log_cache: dict[int, str] = field(default_factory=dict, repr=False)

    def run(self, run_id: int) -> dict[str, Any] | None:
        """The run's own snapshot if there is one, else its entry in the main-branch listing."""
        return self.run_snapshots.get(run_id) or self.listed_runs.get(run_id)

    def log_text(self, job_id: int) -> str | None:
        if job_id not in self.log_paths:
            return None
        if job_id not in self._log_cache:
            self._log_cache[job_id] = self.log_paths[job_id].read_text(encoding="utf-8-sig")
        return self._log_cache[job_id]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _failed_job(jobs: dict[str, Any]) -> int:
    """The single job of a run that did not succeed or get skipped."""
    failed = [job["id"] for job in jobs["jobs"] if job["conclusion"] not in ("success", "skipped", None)]
    if len(failed) != 1:
        raise ValueError(f"run {jobs['jobs'][0]['run_id']}: expected one failed job, found {failed}")
    return failed[0]


def _saved_logs(root: Path, jobs_snapshots: dict[int, dict[str, Any]]) -> dict[int, Path]:
    injections = root / "injections"
    logs: dict[int, Path] = {}
    for record_path in sorted(injections.glob("inj-*.json")):
        record = _read_json(record_path)
        logs[int(record["job_id"])] = root / record["log_file"]
        for run in record["observed"].get("runs") or []:
            if run["log_file"]:
                logs.setdefault(_failed_job(jobs_snapshots[int(run["run_id"])]), root / run["log_file"])
    for path in (injections / "logs").glob("extra-*.log"):
        run_id = int(re.search(r"-run(\d+)\.log$", path.name)[1])
        logs[_failed_job(jobs_snapshots[run_id])] = path
    return logs


@lru_cache(maxsize=None)
def _load(root: Path) -> LabData:
    api = root / "injections" / "api"
    listing = _read_json(api / "runs-main.json")
    listed_runs = {run["id"]: run for page in listing["pages"] for run in page["workflow_runs"]}
    run_snapshots = {int(p.parent.name): _read_json(p) for p in api.glob("*/run.json")}
    jobs_snapshots = {int(p.parent.name): _read_json(p) for p in api.glob("*/jobs.json")}
    run_of_job = {job["id"]: run_id for run_id, jobs in jobs_snapshots.items() for job in jobs["jobs"]}
    return LabData(
        root=root,
        git=Git(root),
        listed_runs=listed_runs,
        run_snapshots=run_snapshots,
        jobs_snapshots=jobs_snapshots,
        run_of_job=run_of_job,
        log_paths=_saved_logs(root, jobs_snapshots),
    )


def load_lab(lab_dir: Path | None = None) -> LabData:
    """Load (once per directory) the lab at ``lab_dir``, default the ``lab/`` submodule."""
    return _load(Path(lab_dir or default_lab_dir()).resolve())
