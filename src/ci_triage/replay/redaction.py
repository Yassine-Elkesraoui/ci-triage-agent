"""What the replay layer hides, so that no output names a case, its label or the lab.

Rules:

1. Runs: ``display_title`` (GitHub copies it from the head commit's message)
   becomes ``<redacted>``. ``head_commit`` keeps only ``id``, ``tree_id`` and
   ``timestamp``; its ``message`` becomes ``<redacted>`` and author/committer
   are dropped.
2. Commits: only sha, tree sha, author date and parents are served; the message
   is ``<redacted>``.
3. Paths under ``HIDDEN_PATHS`` (the lab's own ``injections/`` directory of
   records, specs and logs) are left out of file reads, directory listings and
   diffs. A diff that had files left out says so with ``hidden_files_marker``,
   without saying which.
4. Neutral identity: the lab's owner and repository names become
   ``example-org`` and ``app`` in everything served: JSON, logs, file contents
   and diffs.
5. Belt and braces: after rules 1-2, any string in run or job JSON that still
   matches ``LABEL_MARKERS`` becomes ``<redacted>``.

Apart from rule 4, logs and file contents are served verbatim, since rewriting
them would alter the evidence; the leak tests check them instead.
"""

from __future__ import annotations

import copy
import re
from typing import Any

REDACTED = "<redacted>"
HIDDEN_PATHS = ("injections",)
LABEL_MARKERS = re.compile(r'inj-\d|inject:|Revert "inject', re.IGNORECASE)
IDENTITY = (
    (re.compile(r"Yassine-Elkesraoui", re.IGNORECASE), "example-org"),
    (re.compile(r"ci-triage-lab", re.IGNORECASE), "app"),
)


def is_hidden(path: str) -> bool:
    return any(path == hidden or path.startswith(hidden + "/") for hidden in HIDDEN_PATHS)


def hidden_files_marker(count: int) -> str:
    noun, verb = ("file", "is") if count == 1 else ("files", "are")
    return f"{count} {noun} outside the application under test {verb} hidden in this benchmark"


def scrub(value: Any) -> Any:
    """Replace every string containing a label marker, recursively."""
    if isinstance(value, str):
        return REDACTED if LABEL_MARKERS.search(value) else value
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def neutralize(value: Any) -> Any:
    """Rule 4, recursively over strings, dict keys and lists."""
    if isinstance(value, str):
        for pattern, replacement in IDENTITY:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {neutralize(key): neutralize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [neutralize(item) for item in value]
    return value


def neutralize_bytes(data: bytes) -> bytes:
    for pattern, replacement in IDENTITY:
        data = re.compile(pattern.pattern.encode(), re.IGNORECASE).sub(replacement.encode(), data)
    return data


def redact_run(run: dict[str, Any]) -> dict[str, Any]:
    run = copy.deepcopy(run)
    run["display_title"] = REDACTED
    if head := run.get("head_commit"):
        run["head_commit"] = {
            "id": head["id"],
            "tree_id": head["tree_id"],
            "timestamp": head["timestamp"],
            "message": REDACTED,
        }
    return scrub(run)


def redact_jobs(jobs: dict[str, Any]) -> dict[str, Any]:
    return scrub(jobs)
