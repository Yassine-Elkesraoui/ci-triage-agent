"""The interface a CI-triage agent uses to read GitHub.

``ReplaySource`` implements it offline from lab data; a live GitHub client will
implement it later. Results are JSON-like dicts shaped like the GitHub REST API
responses they stand for, or ``NotAvailable`` when the data does not exist or was
not captured. Methods never raise for missing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

JSON = dict[str, Any]


@dataclass(frozen=True)
class NotAvailable:
    """The requested data does not exist or cannot be served."""

    what: str
    reason: str

    def to_dict(self) -> JSON:
        return {"available": False, "what": self.what, "reason": self.reason}


@dataclass(frozen=True)
class TriageTarget:
    """The failing run to triage and the commit it ran on."""

    run_id: int
    head_sha: str


@runtime_checkable
class GitHubSource(Protocol):
    def get_run(self, run_id: int) -> JSON | NotAvailable:
        """A workflow run (``GET /actions/runs/{run_id}``)."""

    def get_jobs(self, run_id: int) -> JSON | NotAvailable:
        """A run's jobs with their steps (``GET /actions/runs/{run_id}/jobs``)."""

    def get_job_log(self, job_id: int) -> str | NotAvailable:
        """A job's plain-text log (``GET /actions/jobs/{job_id}/logs``)."""

    def get_commit(self, sha: str) -> JSON | NotAvailable:
        """A commit's metadata and its per-file diff against its first parent.

        Keys: sha, tree_sha, author_date, parents, message, files
        (each file: filename, status, additions, deletions, patch), and
        hidden_files when some changed files are withheld.
        """

    def compare(self, base: str, head: str) -> JSON | NotAvailable:
        """The diff from ``base`` to ``head`` (``GET /compare/{base}...{head}``).

        Keys: base_commit, head_commit, files, and hidden_files as in get_commit.
        """

    def get_file(self, path: str, ref: str | None = None) -> JSON | NotAvailable:
        """A file's content, or a directory listing, at ``ref`` (default: the target commit).

        ``path=""`` lists the repository root. Files: type "file", path, sha, size,
        encoding ("utf-8", "utf-16" or "base64"), content. Directories: type "dir",
        path, entries (each: name, path, type "file" or "dir").
        """

    def list_runs(self, branch: str = "main", limit: int = 100) -> JSON | NotAvailable:
        """Workflow runs on ``branch``, newest first (``GET /actions/runs?branch=``)."""
