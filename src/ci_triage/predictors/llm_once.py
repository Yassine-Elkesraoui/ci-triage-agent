"""The one-call LLM baseline (D6a): one fixed evidence bundle, one model call, no tools, no agent loop.

The bundle is gathered through the toolkit only, in this order, each part capped (``[budget]`` in
``configs/one_call_v1.toml``):

1. RUN AND JOBS [history]: ``get_run`` and ``get_jobs``: the run, every job and step, conclusions and
   durations.
2. FAILURE BLOCK [log]: ``extract_failure_block`` for the failing job (the first job whose conclusion is
   failure, cancelled or timed_out).
3. WORKFLOW FILE [file]: ``get_file`` of the run's workflow file at the commit under triage, paged.
4. DIFF [diff], only when the mode offers ``compare``: ``list_runs`` finds the newest successful run
   created before this one on another commit, then ``compare(that commit, this one)``, paged per file.
   In log-only mode none of these three tools is called.

If the prompt (system plus user turn) would exceed the character budget derived from the context
window, sections are cut, the diff first, then the workflow file, the jobs and the failure block last.

The model is asked for a ``Diagnosis`` as JSON, constrained by its schema. The answer is validated
with Pydantic; if it is invalid, one repair call shows the model its answer and the validation errors.
Still invalid: ``invalid_output`` (scored as wrong). A model call that fails after its retries:
``api_error`` (not scored; the case is re-run later).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ci_triage.diagnosis import Diagnosis
from ci_triage.llm.calling import APIFailure, LLMCallRecord, ModelCaller
from ci_triage.llm.clients import Message
from ci_triage.llm.config import Budget, LoadedConfig
from ci_triage.llm.schema import response_schema
from ci_triage.predictors.base import CaseView
from ci_triage.tools import Toolkit
from ci_triage.tools.models import CompareResult, FailureBlockResult, FileResult, JobsResult, RunInfo, RunsResult

_FAILED = ("failure", "cancelled", "timed_out")
MIN_SECTION_CHARS = 400  # a section is never cut below this when fitting the total
_SHRINK_ORDER = ("DIFF", "WORKFLOW FILE", "RUN AND JOBS", "FAILURE BLOCK")
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

Status = Literal["ok", "invalid_output", "api_error"]


def truncate(text: str, limit: int) -> str:
    """``text`` cut at a line boundary to at most ``limit`` characters, ending with a truncation marker."""
    if len(text) <= limit:
        return text
    marker = lambda n: f"\n[truncated: {n} more characters not shown]"  # noqa: E731
    room = max(0, limit - len(marker(len(text))))
    cut = text.rfind("\n", 0, room + 1)
    head = text[:cut if cut > 0 else room]
    return head + marker(len(text) - len(head))


@dataclass
class Section:
    title: str  # as the prompt names it
    source: str  # the evidence source to quote it as
    text: str
    label: str = ""  # e.g. the file's path

    def render(self) -> str:
        label = f": {self.label}" if self.label else ""
        return f"=== {self.title} [{self.source}]{label} ===\n{self.text}"


@dataclass
class Bundle:
    run_id: int
    sections: list[Section] = field(default_factory=list)
    diff_included: bool = False

    def user_message(self) -> str:
        parts = [f"Failed workflow run to triage: run id {self.run_id}."]
        parts += [section.render() for section in self.sections]
        return "\n\n".join(parts) + "\n"

    def section(self, title: str) -> Section | None:
        return next((s for s in self.sections if s.title == title), None)


# -- gathering ----------------------------------------------------------------------------------------


def _reason(result: Any) -> str:
    return f"not available: {getattr(result, 'reason', 'unexpected result')}"


def _jobs_text(run: Any, jobs: Any, run_id: int) -> str:
    lines = []
    if isinstance(run, RunInfo):
        lines.append(f"Run {run.id}: workflow {run.path or run.name}, event {run.event}, branch {run.head_branch}, "
                     f"attempt {run.run_attempt}, conclusion {run.conclusion}, duration {run.duration_s} s")
    else:
        lines.append(f"Run {run_id}: {_reason(run)}")
    if not isinstance(jobs, JobsResult):
        lines.append(f"Jobs: {_reason(jobs)}")
        return "\n".join(lines)
    for job in jobs.jobs:
        lines.append(f'Job "{job.name}" (id {job.id}): conclusion {job.conclusion}, duration {job.duration_s} s')
        lines += [f'  Step {step.number} "{step.name}": conclusion {step.conclusion}, duration {step.duration_s} s'
                  for step in job.steps]
        if job.steps_omitted:
            lines.append(f"  ({job.steps_omitted} steps not shown)")
    if jobs.note:
        lines.append(f"Note: {jobs.note}")
    return "\n".join(lines)


def _failure_text(block: Any) -> str:
    if not isinstance(block, FailureBlockResult):
        return _reason(block)
    parts = []
    for b in block.blocks:
        where = f"; step {b.step}" if b.step else ""
        parts.append(f"[{', '.join(b.kinds)}{where}; lines {b.start_line}-{b.end_line}]\n{b.text}")
    if block.note:
        parts.append(f"Note: {block.note}")
    return "\n".join(parts) if parts else "no evidence found in the log"


def _workflow_text(toolkit: Toolkit, path: str, cap: int) -> str:
    pages, offset = [], 0
    while True:
        page = toolkit.call("get_file", {"path": path, "offset": offset, "max_lines": 1000})
        if not isinstance(page, FileResult) or page.type != "file":
            return "\n".join(pages) if pages else _reason(page)
        pages.append(page.content or "")
        if page.next_offset is None or sum(map(len, pages)) >= cap:
            return "\n".join(pages)
        offset = page.next_offset


def _baseline(toolkit: Toolkit, run: RunInfo) -> str | None:
    """The commit of the newest successful run created before ``run`` on another commit."""
    runs = toolkit.call("list_runs", {"branch": run.head_branch or "main", "limit": 100})
    if not isinstance(runs, RunsResult):
        return None
    green = [r for r in runs.runs if r.id != run.id and r.conclusion == "success" and r.head_sha != run.head_sha
             and (not run.created_at or not r.created_at or r.created_at < run.created_at)]
    return max(green, key=lambda r: (r.created_at or "", r.id)).head_sha if green else None


def _diff_text(toolkit: Toolkit, run: RunInfo, cap: int) -> str:
    base = _baseline(toolkit, run)
    if base is None:
        return "no earlier successful run found, so there is no diff"
    diff = toolkit.call("compare", {"base": base, "head": run.head_sha})
    if not isinstance(diff, CompareResult):
        return _reason(diff)
    parts = [f"From {base} (last successful run) to {run.head_sha} (this run)"]
    used = len(parts[0])
    for f in diff.files:
        parts.append(f"--- {f.filename} ({f.status}, +{f.additions} -{f.deletions})")
        patch, nxt = f.patch or "", f.next_offset
        while nxt is not None and used + len(patch) < cap:
            page = toolkit.call("compare", {"base": base, "head": run.head_sha, "path": f.filename, "offset": nxt})
            if not isinstance(page, CompareResult) or not page.files:
                break
            patch += "\n" + (page.files[0].patch or "")
            nxt = page.files[0].next_offset
        if f.patch is None:
            patch = "(binary file)"
        parts.append(patch)
        used += len(parts[-2]) + len(patch)
    if diff.hidden_files:
        parts.append(f"Note: {diff.hidden_files}")
    return "\n".join(parts)


def gather_bundle(case_view: CaseView, toolkit: Toolkit, budget: Budget, system_chars: int,
                  max_output_tokens: int) -> Bundle:
    run_id = case_view.run_id
    run = toolkit.call("get_run", {"run_id": run_id})
    jobs = toolkit.call("get_jobs", {"run_id": run_id})
    bundle = Bundle(run_id=run_id)
    bundle.sections.append(Section("RUN AND JOBS", "history", truncate(_jobs_text(run, jobs, run_id),
                                                                        budget.jobs_chars)))

    failed = next((j for j in jobs.jobs if j.conclusion in _FAILED), None) if isinstance(jobs, JobsResult) else None
    if failed is not None:
        block = _failure_text(toolkit.call("extract_failure_block", {"job_id": failed.id}))
        bundle.sections.append(Section("FAILURE BLOCK", "log", truncate(block, budget.failure_block_chars),
                                       f'job "{failed.name}"'))
    else:
        bundle.sections.append(Section("FAILURE BLOCK", "log", "no failed job found"))

    workflow = (run.path or "").split("@", 1)[0] if isinstance(run, RunInfo) else ""
    if workflow:
        bundle.sections.append(Section("WORKFLOW FILE", "file",
                                       truncate(_workflow_text(toolkit, workflow, budget.workflow_chars),
                                                budget.workflow_chars), workflow))

    offered = {definition["name"] for definition in toolkit.tool_definitions()}
    if "compare" in offered and isinstance(run, RunInfo):
        bundle.sections.append(Section("DIFF", "diff", truncate(_diff_text(toolkit, run, budget.diff_chars),
                                                                budget.diff_chars)))
        bundle.diff_included = True

    _fit(bundle, budget.prompt_chars(max_output_tokens) - system_chars)
    return bundle


def _fit(bundle: Bundle, limit: int) -> None:
    """Cut sections, least important first, until the user turn is at most ``limit`` characters."""
    for title in _SHRINK_ORDER:
        over = len(bundle.user_message()) - limit
        section = bundle.section(title)
        if over <= 0:
            return
        if section is not None and len(section.text) > MIN_SECTION_CHARS:
            section.text = truncate(section.text, max(MIN_SECTION_CHARS, len(section.text) - over))


# -- the model call ---------------------------------------------------------------------------------------


def parse_diagnosis(text: str) -> tuple[Diagnosis | None, str | None]:
    """The diagnosis in ``text``, or None and a short summary of what is wrong with it."""
    cleaned = text.strip()
    if match := _FENCE.match(cleaned):
        cleaned = match[1]
    if not cleaned:
        return None, "the answer is empty"
    try:
        return Diagnosis.model_validate_json(cleaned), None
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}" for e in exc.errors()[:8]]
        more = f" (+{len(exc.errors()) - 8} more)" if len(exc.errors()) > 8 else ""
        return None, ("; ".join(errors) + more)[:1000]


class OneCallOutcome(BaseModel):
    status: Status
    diagnosis: Diagnosis | None = None
    error: str | None = None
    repaired: bool = False
    calls: list[LLMCallRecord] = Field(default_factory=list)
    prompt_chars: int = 0  # system + user turn
    prompt_tokens_estimate: int = 0
    diff_included: bool = False
    user_message: str = ""  # the bundle as sent


class OneCallError(Exception):
    def __init__(self, outcome: OneCallOutcome) -> None:
        super().__init__(f"{outcome.status}: {outcome.error}")
        self.outcome = outcome


class LLMOncePredictor:
    """One bundle, one model call (plus at most ``repair_calls`` repairs). Use ``run`` for the full outcome;
    ``predict`` (the Predictor protocol) raises ``OneCallError`` unless the status is ok."""

    def __init__(self, caller: ModelCaller, loaded: LoadedConfig, name: str | None = None) -> None:
        self.caller = caller
        self.loaded = loaded
        self.name = name or f"{loaded.config.name}-{caller.client.backend}"
        self.schema = response_schema(Diagnosis)

    def run(self, case_view: CaseView, toolkit: Toolkit) -> OneCallOutcome:
        config, prompt = self.loaded.config, self.loaded.prompt
        bundle = gather_bundle(case_view, toolkit, config.budget, len(prompt.system),
                               config.generation.max_output_tokens)
        user = bundle.user_message()
        outcome = OneCallOutcome(status="ok", prompt_chars=len(prompt.system) + len(user),
                                 prompt_tokens_estimate=config.budget.estimate_tokens(prompt.system + user),
                                 diff_included=bundle.diff_included, user_message=user)
        messages = [Message("user", user)]
        try:
            response, record = self.caller.call("diagnose", prompt.system, messages, self.schema)
            outcome.calls.append(record)
            diagnosis, error = parse_diagnosis(response.text)
            for _ in range(config.generation.repair_calls):
                if diagnosis is not None:
                    break
                messages = [*messages, Message("assistant", response.text or "(empty answer)"),
                            Message("user", prompt.repair_message(error or "invalid"))]
                response, record = self.caller.call("repair", prompt.system, messages, self.schema)
                outcome.calls.append(record)
                diagnosis, error = parse_diagnosis(response.text)
                outcome.repaired = diagnosis is not None
        except APIFailure as failure:
            outcome.calls.append(failure.record)
            outcome.status, outcome.error = "api_error", f"api_error: {failure}"
            return outcome
        if diagnosis is None:
            outcome.status, outcome.error = "invalid_output", f"invalid_output: {error}"
            return outcome
        outcome.diagnosis = diagnosis
        return outcome

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        outcome = self.run(case_view, toolkit)
        if outcome.diagnosis is None:
            raise OneCallError(outcome)
        return outcome.diagnosis
