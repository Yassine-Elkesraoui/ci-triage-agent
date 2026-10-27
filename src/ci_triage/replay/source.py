"""ReplaySource: GitHub as one case's triage agent may see it, from lab data only."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from ci_triage._git import EMPTY_TREE, Git
from ci_triage.github_source import JSON, NotAvailable
from ci_triage.replay.cases import Case
from ci_triage.replay.labdata import LabData, load_lab
from ci_triage.replay.redaction import (
    HIDDEN_PATHS,
    REDACTED,
    hidden_files_marker,
    is_hidden,
    neutralize,
    neutralize_bytes,
    redact_jobs,
    redact_run,
)

DEFAULT_BRANCH = "main"
OUT_OF_SCOPE = "not available in this case's scope"
_BRANCH_REFS = {"main", "HEAD", "refs/heads/main"}


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode(data: bytes) -> tuple[str, str]:
    """(encoding, content): text where it decodes, base64 otherwise, like the contents API."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return "utf-16", data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return "utf-8", data.decode("utf-8")
    except UnicodeDecodeError:
        return "base64", base64.b64encode(data).decode("ascii")


def diff_payload(git: Git, base: str, head: str) -> JSON:
    """The visible files changed from ``base`` to ``head``, plus a marker if any were hidden."""
    payload: JSON = {"files": [dict(f) for f in git.diff_files(base, head, HIDDEN_PATHS)]}
    if hidden := git.hidden_count(base, head, HIDDEN_PATHS):
        payload["hidden_files"] = hidden_files_marker(hidden)
    return payload


class ReplaySource:
    """A GitHubSource for one lab case, frozen at the moment its eval run finished.

    Scope (a deliberate benchmark choice). The lab ran its 60 cases one after
    another on one branch, grouped by category, so the real history around any
    case is mostly other cases, often of the same category. A real repository
    has no such neighbours, so serving them would hand the agent a category
    prior it could not have in practice. Each case therefore reaches only its
    own context:

    - the baseline: the most recent green run on main before the injection
      commit, as run status and jobs (no logs). ``get_commit`` on its commit
      gives metadata without a diff, and ``get_file`` may read files there;
    - the case's own commits, from the injection commit up to the eval commit
      (for flaky cases, the empty rerun commits too), and their runs up to the
      eval run, including earlier green reruns of the same code;
    - ``compare(baseline, eval)``, the diff across the case.

    Anything else, including other cases' runs and commits, later runs (no time
    travel: nothing created after the eval run finished) and unknown ids,
    returns ``NotAvailable`` with the same reason, so the refusal does not
    reveal what exists. ``main`` and ``HEAD`` mean the eval commit. The source
    keeps only the triage target and its scope, never the case id or a label.
    """

    def __init__(self, case: Case, lab: LabData | None = None) -> None:
        self._lab = lab or load_lab()
        git = self._lab.git
        self.target = case.target
        self._head = case.eval_commit

        eval_run = self._lab.run(case.eval_run_id)
        jobs = self._lab.jobs_snapshots.get(case.eval_run_id, {"jobs": []})["jobs"]
        self.cutoff = max([_ts(eval_run["updated_at"])] + [_ts(j["completed_at"]) for j in jobs if j["completed_at"]])

        parent = git.commit_header(case.injection_commit)[1][0]
        self._own_commits = frozenset(git.rev_list(f"{parent}..{case.eval_commit}"))
        before = set(git.rev_list(parent))
        green = [run for run in self._lab.listed_runs.values()
                 if run["conclusion"] == "success" and run["head_sha"] in before and self._before_cutoff(run)]
        baseline_run = max(green, key=lambda run: (_ts(run["created_at"]), run["id"]))
        self.baseline_commit: str = baseline_run["head_sha"]
        self._baseline_run_id: int = baseline_run["id"]
        self._run_ids = {self._baseline_run_id} | {
            run["id"] for run in self._lab.listed_runs.values()
            if run["head_sha"] in self._own_commits and self._before_cutoff(run)
        }

    # -- scope ----------------------------------------------------------------

    def _before_cutoff(self, run: dict[str, Any]) -> bool:
        return _ts(run["created_at"]) <= self.cutoff

    def _finished(self, run: dict[str, Any]) -> bool:
        return _ts(run["updated_at"]) <= self.cutoff

    def _scoped_run(self, run_id: Any) -> dict[str, Any] | None:
        run_id = _as_id(run_id)
        return self._lab.run(run_id) if run_id in self._run_ids else None

    def _scoped_commit(self, ref: Any) -> str | None:
        """Full sha for ``ref`` if it is the baseline or one of the case's own commits."""
        if ref is None or ref in _BRANCH_REFS:
            return self._head
        sha = self._lab.git.resolve_commit(str(ref))
        return sha if sha in self._own_commits or sha == self.baseline_commit else None

    # -- runs -----------------------------------------------------------------

    def _present_run(self, run: dict[str, Any]) -> JSON:
        run = redact_run(run)
        if not self._finished(run):
            run.update(status="in_progress", conclusion=None, updated_at=self.cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return neutralize(run)

    def get_run(self, run_id: int) -> JSON | NotAvailable:
        run = self._scoped_run(run_id)
        if run is None:
            return NotAvailable(f"run {run_id}", OUT_OF_SCOPE)
        return self._present_run(run)

    def get_jobs(self, run_id: int) -> JSON | NotAvailable:
        run = self._scoped_run(run_id)
        if run is None:
            return NotAvailable(f"jobs of run {run_id}", OUT_OF_SCOPE)
        if not self._finished(run):
            return NotAvailable(f"jobs of run {run_id}", "run still in progress")
        if run["id"] not in self._lab.jobs_snapshots:
            return NotAvailable(f"jobs of run {run_id}", "jobs were not captured for this run")
        return neutralize(redact_jobs(self._lab.jobs_snapshots[run["id"]]))

    def get_job_log(self, job_id: int) -> str | NotAvailable:
        job_id = _as_id(job_id)
        run_id = self._lab.run_of_job.get(job_id)
        run = self._scoped_run(run_id)
        if run is None or run_id == self._baseline_run_id or not self._finished(run):
            return NotAvailable(f"log of job {job_id}", OUT_OF_SCOPE)
        log = self._lab.log_text(job_id)
        if log is None:
            job = next(j for j in self._lab.jobs_snapshots[run_id]["jobs"] if j["id"] == job_id)
            return NotAvailable(
                f"log of job {job_id}",
                f"no log was saved for this job (conclusion: {job['conclusion']}); "
                "logs exist only for failed or cancelled jobs",
            )
        return neutralize(log)

    def list_runs(self, branch: str = DEFAULT_BRANCH, limit: int = 100) -> JSON | NotAvailable:
        if branch != DEFAULT_BRANCH:
            return {"total_count": 0, "workflow_runs": []}
        runs = sorted((self._lab.run(run_id) for run_id in self._run_ids),
                      key=lambda run: (_ts(run["created_at"]), run["id"]), reverse=True)
        return {"total_count": len(runs), "workflow_runs": [self._present_run(run) for run in runs[: max(limit, 0)]]}

    # -- git ------------------------------------------------------------------

    def get_commit(self, sha: str) -> JSON | NotAvailable:
        full = self._scoped_commit(sha) if sha is not None else None
        if full is None:
            return NotAvailable(f"commit {sha}", OUT_OF_SCOPE)
        tree, parents, author_date = self._lab.git.commit_header(full)
        commit: JSON = {
            "sha": full,
            "tree_sha": tree,
            "author_date": author_date,
            "parents": list(parents),
            "message": REDACTED,
        }
        if full not in self._own_commits:
            commit["diff"] = f"the baseline commit's own diff is {OUT_OF_SCOPE}"
            return commit
        return neutralize(commit | diff_payload(self._lab.git, parents[0] if parents else EMPTY_TREE, full))

    def compare(self, base: str, head: str) -> JSON | NotAvailable:
        base_sha, head_sha = self._scoped_commit(base), self._scoped_commit(head)
        if base_sha is None or head_sha is None:
            return NotAvailable(f"compare {base}...{head}", OUT_OF_SCOPE)
        return neutralize({"base_commit": base_sha, "head_commit": head_sha} | diff_payload(self._lab.git, base_sha, head_sha))

    def get_file(self, path: str, ref: str | None = None) -> JSON | NotAvailable:
        what = f"{path} at {ref or DEFAULT_BRANCH}"
        sha = self._scoped_commit(ref)
        if sha is None:
            return NotAvailable(what, OUT_OF_SCOPE)
        clean = str(path).replace("\\", "/").strip("/")
        if clean and any(part in ("", ".", "..") for part in clean.split("/")):
            return NotAvailable(what, "invalid path")
        if is_hidden(clean):
            return NotAvailable(what, "not found")
        if clean:
            entries = self._lab.git.ls_tree(sha, clean)
            entry = next((e for e in entries if e[3] == clean), None)
            if entry is None:
                return NotAvailable(what, "not found")
            kind, oid = entry[1], entry[2]
        else:
            kind, oid = "tree", None
        if kind == "blob":
            data = neutralize_bytes(self._lab.git.blob(oid))
            encoding, content = _decode(data)
            return neutralize(
                {"type": "file", "path": clean, "sha": oid, "size": len(data), "encoding": encoding, "content": content}
            )
        if kind == "tree":
            listing = self._lab.git.ls_tree(sha, clean + "/" if clean else "")
            return {
                "type": "dir",
                "path": clean,
                "entries": [
                    {"name": name.rsplit("/", 1)[-1], "path": name, "type": "dir" if k == "tree" else "file"}
                    for _, k, _, name in listing
                    if k in ("blob", "tree") and not is_hidden(name)
                ],
            }
        return NotAvailable(what, f"unsupported entry type {kind}")
