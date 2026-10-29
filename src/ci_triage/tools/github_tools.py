"""The read-only tools over a GitHubSource, and their registry.

Each tool takes the source, its validated input and the size cap, and returns
its output model or a ``NotAvailableResult``. Tools only read; they never
raise for missing data (``Toolkit`` also catches anything unexpected).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ci_triage.github_source import GitHubSource, NotAvailable
from ci_triage.tools import failure_block
from ci_triage.tools.models import (
    CommitResult,
    CompareInput,
    CompareResult,
    DirEntry,
    ExtractFailureBlockInput,
    FailureBlockResult,
    FileDiff,
    FileResult,
    GetCommitInput,
    GetFileInput,
    GetJobLogInput,
    GetJobsInput,
    GetRunInput,
    Job,
    JobLog,
    JobsResult,
    ListRunsInput,
    NotAvailableResult,
    RunInfo,
    RunsResult,
    RunSummary,
    Step,
)
from ci_triage.tools.text import MAX_LINE_CHARS, clip, fit, log_lines, numbered, page_note, render, split_lines

_FAILED = ("failure", "cancelled", "timed_out")


def _na(result: NotAvailable) -> NotAvailableResult:
    return NotAvailableResult.of(result.what, result.reason)


def _seconds(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: E731
    return int((parse(end) - parse(start)).total_seconds())


def _line_chars(cap: int) -> int:
    """How long a log, file or patch line may be: short enough that a page always holds at least one."""
    return min(MAX_LINE_CHARS, cap // 4)


def _numbered_page(lines: list[str], first: int, count: int, width: int) -> tuple[str, bool]:
    """Lines ``first..first+count`` numbered, and whether any had to be clipped to ``width``."""
    page = lines[first:first + count]
    return numbered((first + n + 1, clip(line, width)) for n, line in enumerate(page)), any(
        len(line) > width for line in page
    )


def _join_notes(*notes: str | None) -> str | None:
    return "; ".join(note for note in notes if note) or None


# -- runs and jobs --------------------------------------------------------------


def get_run(source: GitHubSource, args: GetRunInput, cap: int) -> RunInfo | NotAvailableResult:
    run = source.get_run(args.run_id)
    if isinstance(run, NotAvailable):
        return _na(run)
    return RunInfo.model_validate(run | {"duration_s": _seconds(run.get("run_started_at"), run.get("updated_at"))})


def _job(job: dict[str, Any], with_steps: bool) -> Job:
    steps = [
        Step(
            number=step["number"],
            name=step["name"],
            status=step.get("status"),
            conclusion=step.get("conclusion"),
            duration_s=_seconds(step.get("started_at"), step.get("completed_at")),
        )
        for step in job.get("steps") or []
    ]
    return Job(
        id=job["id"],
        name=job["name"],
        status=job.get("status"),
        conclusion=job.get("conclusion"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        duration_s=_seconds(job.get("started_at"), job.get("completed_at")),
        runner_labels=job.get("labels") or [],
        steps=steps if with_steps else [],
        steps_omitted=None if with_steps or not steps else len(steps),
    )


def get_jobs(source: GitHubSource, args: GetJobsInput, cap: int) -> JobsResult | NotAvailableResult:
    """All jobs with steps. If that is too big: failed jobs first, with only their steps; then no steps at all;
    then as many jobs as fit."""
    jobs = source.get_jobs(args.run_id)
    if isinstance(jobs, NotAvailable):
        return _na(jobs)
    raw = jobs["jobs"]
    full = JobsResult(run_id=args.run_id, total_count=len(raw), jobs=[_job(job, True) for job in raw])
    if len(render(full)) <= cap:
        return full
    raw = sorted(raw, key=lambda job: job.get("conclusion") not in _FAILED)
    failed_steps = JobsResult(run_id=args.run_id, total_count=len(raw),
                              jobs=[_job(job, job.get("conclusion") in _FAILED) for job in raw],
                              note="failed jobs first; steps of the other jobs are omitted to fit the size cap")
    if len(render(failed_steps)) <= cap:
        return failed_steps
    bare = [_job(job, False) for job in raw]

    def make(n: int) -> JobsResult:
        note = "failed jobs first; steps are omitted to fit the size cap"
        if n < len(raw):
            note += f"; {len(raw) - n} more jobs not shown"
        return JobsResult(run_id=args.run_id, total_count=len(raw), jobs=bare[:n], note=note)

    return fit(make, len(raw), cap)


def list_runs(source: GitHubSource, args: ListRunsInput, cap: int) -> RunsResult | NotAvailableResult:
    listing = source.list_runs(branch=args.branch, limit=args.limit)
    if isinstance(listing, NotAvailable):
        return _na(listing)
    runs = [RunSummary.model_validate(run) for run in listing["workflow_runs"]]
    total = listing["total_count"]

    def make(n: int) -> RunsResult:
        note = None
        if n < len(runs):
            note = f"truncated at the size cap: {n} of {len(runs)} requested runs shown; lower limit or use get_run"
        elif len(runs) < total:
            note = f"{len(runs)} of {total} runs shown; raise limit for more"
        return RunsResult(branch=args.branch, total_count=total, runs=runs[:n], note=note)

    return fit(make, len(runs), cap)


# -- logs -----------------------------------------------------------------------


def get_job_log(source: GitHubSource, args: GetJobLogInput, cap: int) -> JobLog | NotAvailableResult:
    log = source.get_job_log(args.job_id)
    if isinstance(log, NotAvailable):
        return _na(log)
    lines = log_lines(log, timestamps=args.timestamps)
    total = len(lines)
    if args.offset >= max(total, 1):
        return NotAvailableResult.of(what=f"log of job {args.job_id} at offset {args.offset}",
                                  reason=f"offset is past the end of the log ({total} lines)")
    wanted = min(args.max_lines, total - args.offset)

    def make(n: int) -> JobLog:
        content, clipped = _numbered_page(lines, args.offset, n, _line_chars(cap))
        nxt = args.offset + n
        return JobLog(
            job_id=args.job_id,
            total_lines=total,
            offset=args.offset,
            returned_lines=n,
            next_offset=nxt if nxt < total else None,
            content=content,
            note=_join_notes(page_note(args.offset, n, total, n < wanted),
                             f"lines longer than {_line_chars(cap)} chars are clipped" if clipped else None),
        )

    return fit(make, wanted, cap)


def extract_failure_block(
    source: GitHubSource, args: ExtractFailureBlockInput, cap: int
) -> FailureBlockResult | NotAvailableResult:
    log = source.get_job_log(args.job_id)
    if isinstance(log, NotAvailable):
        return _na(log)
    lines = log_lines(log)
    blocks = failure_block.find_evidence(lines)
    base = dict(job_id=args.job_id, log_lines=len(lines), log_chars=len(log))

    def result(rendered, omitted, trimmed) -> FailureBlockResult:
        notes = []
        if any("log_tail" in block.kinds for block in blocks):
            notes.append("No decisive error line found in this log; showing the last lines before job cleanup.")
        if omitted:
            starts = ", ".join(str(b.start + 1) for b in omitted[:10])
            notes.append(f"{len(omitted)} lower-priority blocks omitted at the size cap (they start at lines {starts}); "
                         "read them with get_job_log(offset=line-1)")
        if trimmed:
            notes.append('some blocks were trimmed to fit ("…" marks left-out lines); '
                         "get_job_log(offset=start_line-1) shows them in full")
        return FailureBlockResult(**base, blocks=rendered, omitted_blocks=len(omitted) or None,
                                  note="; ".join(notes) or None)

    # Room for the worst-case note, so adding it afterwards cannot break the cap.
    worst = result([], blocks, True)
    reserve = len(render(worst)) - len(render(FailureBlockResult(**base, blocks=[])))
    fits = lambda rendered: len(render(FailureBlockResult(**base, blocks=rendered))) + reserve <= cap  # noqa: E731
    rendered, omitted, trimmed = failure_block.fit_blocks(blocks, lines, fits)
    return result(rendered, omitted, trimmed)


# -- git ------------------------------------------------------------------------


def _diffs(files: list[dict[str, Any]], path: str | None, offset: int, cap: int,
           make_result: Callable[[list[FileDiff], str | None], BaseModel]) -> BaseModel | NotAvailableResult:
    """Without ``path``: every file, each patch cut to the same number of lines. With ``path``: one patch, paged."""
    patches = {f["filename"]: split_lines(f["patch"]) if f.get("patch") is not None else None for f in files}

    def diff(f: dict[str, Any], first: int, count: int) -> FileDiff:
        lines = patches[f["filename"]]
        shown = None if lines is None else "\n".join(clip(line, _line_chars(cap)) for line in lines[first:first + count])
        nxt = first + count
        return FileDiff(
            filename=f["filename"], status=f["status"], additions=f["additions"], deletions=f["deletions"],
            patch=shown, patch_lines=len(lines or []), patch_offset=first or None,
            next_offset=nxt if lines is not None and nxt < len(lines) else None,
        )

    if path is None:
        longest = max((len(lines) for lines in patches.values() if lines), default=0)

        def make(k: int) -> BaseModel:
            shown = [diff(f, 0, k) for f in files]
            cut = [d.filename for d in shown if d.next_offset is not None]
            note = (f"patches cut to {k} lines to fit the size cap; call again with path=<file> and offset to page "
                    f"through one ({', '.join(cut[:5])})") if cut else None
            return make_result(shown, note)

        return fit(make, longest, cap)

    match = next((f for f in files if f["filename"] == path.replace("\\", "/").strip("/")), None)
    if match is None:
        return NotAvailableResult.of(what=f"patch of {path}", reason="file not changed in this diff")
    lines = patches[match["filename"]] or []
    if lines and offset >= len(lines):
        return NotAvailableResult.of(what=f"patch of {path} at offset {offset}",
                                  reason=f"offset is past the end of the patch ({len(lines)} lines)")

    def make_one(n: int) -> BaseModel:
        shown = diff(match, offset, n)
        note = page_note(offset, n, len(lines), True) if lines else None
        return make_result([shown], note)

    return fit(make_one, len(lines) - offset if lines else 0, cap)


def get_commit(source: GitHubSource, args: GetCommitInput, cap: int) -> CommitResult | NotAvailableResult:
    commit = source.get_commit(args.sha)
    if isinstance(commit, NotAvailable):
        return _na(commit)
    header = dict(sha=commit["sha"], parents=commit["parents"], author_date=commit.get("author_date"),
                  message=commit.get("message"), hidden_files=commit.get("hidden_files"), diff_note=commit.get("diff"))
    if "files" not in commit:
        if args.path is not None:
            return NotAvailableResult.of(what=f"patch of {args.path} in commit {args.sha}", reason=commit.get("diff", ""))
        return CommitResult(**header)
    return _diffs(commit["files"], args.path, args.offset, cap,
                  lambda files, note: CommitResult(**header, files=files, note=note))


def compare(source: GitHubSource, args: CompareInput, cap: int) -> CompareResult | NotAvailableResult:
    diff = source.compare(args.base, args.head)
    if isinstance(diff, NotAvailable):
        return _na(diff)
    header = dict(base_commit=diff["base_commit"], head_commit=diff["head_commit"], hidden_files=diff.get("hidden_files"))
    return _diffs(diff["files"], args.path, args.offset, cap,
                  lambda files, note: CompareResult(**header, files=files, note=note))


def get_file(source: GitHubSource, args: GetFileInput, cap: int) -> FileResult | NotAvailableResult:
    found = source.get_file(args.path, args.ref)
    if isinstance(found, NotAvailable):
        return _na(found)
    base = dict(path=found["path"], ref=args.ref or "HEAD", offset=args.offset)
    if found["type"] == "dir":
        entries = [DirEntry(name=e["name"], type=e["type"]) for e in found["entries"]]
        if args.offset and args.offset >= len(entries):
            return NotAvailableResult.of(what=f"{args.path} at offset {args.offset}",
                                      reason=f"offset is past the end of the listing ({len(entries)} entries)")
        wanted = min(args.max_lines, len(entries) - args.offset)

        def make_dir(n: int) -> FileResult:
            nxt = args.offset + n
            return FileResult(**base, type="dir", entries=entries[args.offset:nxt],
                              next_offset=nxt if nxt < len(entries) else None,
                              note=page_note(args.offset, n, len(entries), n < wanted, unit="entries"))

        return fit(make_dir, wanted, cap)
    if found["encoding"] == "base64":
        return FileResult(**base, type="file", size=found["size"], note="binary file; content not shown")
    lines = split_lines(found["content"])
    if args.offset and args.offset >= len(lines):
        return NotAvailableResult.of(what=f"{args.path} at offset {args.offset}",
                                  reason=f"offset is past the end of the file ({len(lines)} lines)")
    wanted = min(args.max_lines, len(lines) - args.offset)

    def make_file(n: int) -> FileResult:
        content, clipped = _numbered_page(lines, args.offset, n, _line_chars(cap))
        nxt = args.offset + n
        return FileResult(
            **base, type="file", size=found["size"], total_lines=len(lines), content=content,
            next_offset=nxt if nxt < len(lines) else None,
            note=_join_notes(page_note(args.offset, n, len(lines), n < wanted),
                             f"lines longer than {_line_chars(cap)} chars are clipped" if clipped else None),
        )

    return fit(make_file, wanted, cap)


# -- registry -------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[GitHubSource, Any, int], BaseModel]

    def definition(self) -> dict[str, Any]:
        """The tool as an LLM tool definition: name, description, JSON schema of its input."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_model.model_json_schema()}


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool("list_runs", "Recent workflow runs on a branch, newest first, with status and conclusion.",
             ListRunsInput, list_runs),
        Tool("get_run", "One workflow run: status, conclusion, event, commit sha and timing.",
             GetRunInput, get_run),
        Tool("get_jobs", "A run's jobs and their steps, with conclusions and durations in seconds.",
             GetJobsInput, get_jobs),
        Tool("extract_failure_block",
             "The evidence in a failed job's log (test failures, exceptions, errors, timeouts) with context, "
             "most important first.",
             ExtractFailureBlockInput, extract_failure_block),
        Tool("get_job_log", "A page of a job's log with line numbers; page through it with offset and max_lines.",
             GetJobLogInput, get_job_log),
        Tool("get_commit", "A commit's metadata and per-file diff; pass path and offset to page through one patch.",
             GetCommitInput, get_commit),
        Tool("compare", "The diff between two commits, per file; pass path and offset to page through one patch.",
             CompareInput, compare),
        Tool("get_file",
             "A file with line numbers, or a directory listing, at a commit (default: the one under triage); paged.",
             GetFileInput, get_file),
    )
}


def tool_definitions() -> list[dict[str, Any]]:
    return [tool.definition() for tool in TOOLS.values()]
