"""Evaluation modes: how much the toolkit may reveal.

- ``full``: every tool, as the replay scope allows (commits, diffs, files at the
  baseline, ...).
- ``log-only``: nothing that shows what changed. ``get_commit`` and ``compare``
  (the commit-diff and patch tools) are switched off, and ``get_file`` reads only
  the commit under triage. Runs, jobs, logs, ``extract_failure_block`` and files at
  the eval commit stay. This is the situation of an alert with no usable diff: a
  large PR, a scheduled run, or an alert-only integration.

Log-only is enforced twice: the toolkit refuses the removed tools by name, and
``LogOnlySource`` refuses the underlying data, so neither the toolkit nor the
source alone has to be right.
"""

from __future__ import annotations

import re
from enum import StrEnum

from ci_triage.github_source import JSON, GitHubSource, NotAvailable
from ci_triage.tools.text import MAX_OUTPUT_CHARS
from ci_triage.tools.toolkit import Toolkit


class Mode(StrEnum):
    FULL = "full"
    LOG_ONLY = "log-only"


LOG_ONLY_REASON = "not available in log-only mode"
LOG_ONLY_REMOVED = ("get_commit", "compare")  # the tools that show commits' diffs and patches
_HEAD_REFS = {"HEAD"}
_SHA_PREFIX = re.compile(r"^[0-9a-fA-F]{4,40}$")


class LogOnlySource:
    """``inner`` without diffs, and with files only at ``head_sha`` (the commit under triage)."""

    def __init__(self, inner: GitHubSource, head_sha: str) -> None:
        self._inner = inner
        self._head = head_sha.lower()

    def is_head(self, ref: str | None) -> bool:
        """``ref`` names the commit under triage: omitted, ``HEAD``, or (a prefix of) its sha."""
        if ref is None or ref in _HEAD_REFS:
            return True
        return isinstance(ref, str) and bool(_SHA_PREFIX.match(ref)) and self._head.startswith(ref.lower())

    def get_run(self, run_id: int) -> JSON | NotAvailable:
        return self._inner.get_run(run_id)

    def get_jobs(self, run_id: int) -> JSON | NotAvailable:
        return self._inner.get_jobs(run_id)

    def get_job_log(self, job_id: int) -> str | NotAvailable:
        return self._inner.get_job_log(job_id)

    def list_runs(self, branch: str = "main", limit: int = 100) -> JSON | NotAvailable:
        return self._inner.list_runs(branch=branch, limit=limit)

    def get_commit(self, sha: str) -> JSON | NotAvailable:
        return NotAvailable(f"commit {sha}", LOG_ONLY_REASON)

    def compare(self, base: str, head: str) -> JSON | NotAvailable:
        return NotAvailable(f"compare {base}...{head}", LOG_ONLY_REASON)

    def get_file(self, path: str, ref: str | None = None) -> JSON | NotAvailable:
        if not self.is_head(ref):
            return NotAvailable(f"{path} at {ref}",
                                f"{LOG_ONLY_REASON}: files can be read only at the commit under triage (omit ref)")
        return self._inner.get_file(path, None)


def make_toolkit(source: GitHubSource, head_sha: str, mode: Mode | str = Mode.FULL,
                 max_chars: int = MAX_OUTPUT_CHARS) -> Toolkit:
    """A toolkit over ``source`` for one case in ``mode``; ``head_sha`` is the commit under triage."""
    mode = Mode(mode)
    if mode is Mode.FULL:
        return Toolkit(source, max_chars)
    return Toolkit(LogOnlySource(source, head_sha), max_chars,
                   unavailable=dict.fromkeys(LOG_ONLY_REMOVED, LOG_ONLY_REASON))
