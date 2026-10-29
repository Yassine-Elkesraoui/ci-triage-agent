"""Typed inputs and outputs of the tools.

The input models' JSON schemas are the tool definitions an LLM will see, so
every field carries a description. Inputs forbid unknown fields. Every output
has ``available``: True for results, False for ``NotAvailableResult``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# -- common -------------------------------------------------------------------


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    available: Literal[True] = True


class NotAvailableResult(BaseModel):
    """The data does not exist, is out of scope, or the call was invalid."""

    available: Literal[False] = False
    what: str
    reason: str

    @classmethod
    def of(cls, what: str, reason: str, limit: int = 300) -> NotAvailableResult:
        """``what`` and ``reason`` often echo the caller's input, so they are clipped."""
        clip = lambda text: text if len(text) <= limit else f"{text[:limit]}… [+{len(text) - limit} chars]"  # noqa: E731
        return cls(what=clip(str(what)), reason=clip(str(reason)))


class ToolCall(BaseModel):
    """One recorded call: what was asked and how much came back."""

    tool: str
    arguments: dict[str, Any]
    output_chars: int
    available: bool
    error: str | None = None  # internal exception, for debugging; never shown to the model


_OFFSET = "0-based index of the first line to return (lines to skip)."

# -- inputs -------------------------------------------------------------------


class GetRunInput(ToolInput):
    run_id: int = Field(description="Workflow run id.")


class GetJobsInput(ToolInput):
    run_id: int = Field(description="Workflow run id.")


class GetJobLogInput(ToolInput):
    job_id: int = Field(description="Job id, from get_jobs.")
    offset: int = Field(0, ge=0, description=_OFFSET)
    max_lines: int = Field(200, ge=1, le=1000, description="Maximum number of lines to return.")
    timestamps: bool = Field(False, description="Keep each line's timestamp prefix.")


class ExtractFailureBlockInput(ToolInput):
    job_id: int = Field(description="Job id of a failed or cancelled job, from get_jobs.")


class GetCommitInput(ToolInput):
    sha: str = Field(description="Commit sha (full or abbreviated), or 'HEAD' for the commit under triage.")
    path: str | None = Field(None, description="Only this file's patch, paged with offset.")
    offset: int = Field(0, ge=0, description="With path: 0-based index of the first patch line to return.")


class CompareInput(ToolInput):
    base: str = Field(description="Base commit sha.")
    head: str = Field(description="Head commit sha, or 'HEAD' for the commit under triage.")
    path: str | None = Field(None, description="Only this file's patch, paged with offset.")
    offset: int = Field(0, ge=0, description="With path: 0-based index of the first patch line to return.")


class GetFileInput(ToolInput):
    path: str = Field("", description="Repository-relative path of a file or directory; '' lists the root.")
    ref: str | None = Field(None, description="Commit sha to read at; default: the commit under triage.")
    offset: int = Field(0, ge=0, description="0-based index of the first line (or directory entry) to return.")
    max_lines: int = Field(200, ge=1, le=1000, description="Maximum number of lines (or entries) to return.")


class ListRunsInput(ToolInput):
    branch: str = Field("main", description="Branch name.")
    limit: int = Field(20, ge=1, le=100, description="Maximum number of runs to return, newest first.")


# -- outputs ------------------------------------------------------------------


class RunInfo(ToolOutput):
    id: int
    name: str | None = None
    path: str | None = None
    display_title: str | None = None
    run_number: int | None = None
    run_attempt: int | None = None
    event: str | None = None
    status: str | None = None
    conclusion: str | None = None
    head_branch: str | None = None
    head_sha: str
    created_at: str | None = None
    run_started_at: str | None = None
    updated_at: str | None = None
    duration_s: int | None = None


class Step(BaseModel):
    number: int
    name: str
    status: str | None = None
    conclusion: str | None = None
    duration_s: int | None = None


class Job(BaseModel):
    id: int
    name: str
    status: str | None = None
    conclusion: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_s: int | None = None
    runner_labels: list[str] = []
    steps: list[Step] = []
    steps_omitted: int | None = None


class JobsResult(ToolOutput):
    run_id: int
    total_count: int
    jobs: list[Job]
    note: str | None = None


class JobLog(ToolOutput):
    job_id: int
    total_lines: int
    offset: int
    returned_lines: int
    next_offset: int | None = None
    content: str  # "<line number>| <text>" per line
    note: str | None = None


class EvidenceBlock(BaseModel):
    kinds: list[str]
    step: str | None = None  # the "Run ..." step the evidence sits in
    start_line: int
    end_line: int
    text: str  # "<line number>| <text>" per line, "…" where lines were left out


class FailureBlockResult(ToolOutput):
    job_id: int
    log_lines: int
    log_chars: int
    blocks: list[EvidenceBlock]
    omitted_blocks: int | None = None
    note: str | None = None


class FileDiff(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None = None  # None for binary files
    patch_lines: int = 0
    patch_offset: int | None = None  # set when the page does not start at line 0
    next_offset: int | None = None  # set when more patch lines remain


class CommitResult(ToolOutput):
    sha: str
    parents: list[str]
    author_date: str | None = None
    message: str | None = None
    files: list[FileDiff] = []
    hidden_files: str | None = None
    diff_note: str | None = None
    note: str | None = None


class CompareResult(ToolOutput):
    base_commit: str
    head_commit: str
    files: list[FileDiff]
    hidden_files: str | None = None
    note: str | None = None


class DirEntry(BaseModel):
    name: str
    type: str


class FileResult(ToolOutput):
    path: str
    ref: str
    type: Literal["file", "dir"]
    size: int | None = None
    total_lines: int | None = None
    offset: int = 0
    next_offset: int | None = None
    content: str | None = None  # files: "<line number>| <text>" per line
    entries: list[DirEntry] | None = None  # directories
    note: str | None = None


class RunSummary(BaseModel):
    id: int
    run_number: int | None = None
    run_attempt: int | None = None
    event: str | None = None
    status: str | None = None
    conclusion: str | None = None
    head_sha: str
    created_at: str | None = None


class RunsResult(ToolOutput):
    branch: str
    total_count: int
    runs: list[RunSummary]
    note: str | None = None
