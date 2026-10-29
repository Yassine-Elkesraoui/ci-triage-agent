"""Helpers for driving the tools in tests: follow pagination, read numbered content, call everything."""

from __future__ import annotations

from conftest import LAB

from ci_triage.tools import Toolkit
from ci_triage.tools.models import NotAvailableResult


def failed_job_id(toolkit: Toolkit, run_id: int) -> int:
    jobs = toolkit.call("get_jobs", {"run_id": run_id})
    return next(job.id for job in jobs.jobs if job.conclusion in ("failure", "cancelled"))


def _next_offset(result):
    if isinstance(result, NotAvailableResult):
        return None
    if hasattr(result, "next_offset"):
        return result.next_offset
    return result.files[0].next_offset if result.files else None


def read_all(toolkit: Toolkit, tool: str, args: dict) -> list:
    """Every page of a paginated call, following next_offset until it runs out."""
    pages, previous = [toolkit.call(tool, {**args, "offset": 0})], 0
    while (offset := _next_offset(pages[-1])) is not None:
        assert offset > previous, "pagination must move forward"
        pages.append(toolkit.call(tool, {**args, "offset": offset}))
        previous = offset
    return pages


def numbered_lines(content: str) -> list[tuple[int, str]]:
    """``"<n>| <text>"`` lines back to (n, text)."""
    out = []
    for line in content.split("\n"):
        number, text = line.split("| ", 1)
        out.append((int(number), text))
    return out


def exercise_everything(toolkit: Toolkit, source, case) -> list[tuple[str, object]]:
    """Every tool on everything the case's scope allows, every page of it; (where, result) pairs."""
    out = []

    def call(where, tool, args):
        out.append((where, toolkit.call(tool, args)))
        return out[-1][1]

    def paged(where, tool, args):
        for n, page in enumerate(read_all(toolkit, tool, args)):
            out.append((f"{where} page {n}", page))

    runs = call("list_runs", "list_runs", {"limit": 100})
    for run in runs.runs:
        call(f"get_run({run.id})", "get_run", {"run_id": run.id})
        jobs = call(f"get_jobs({run.id})", "get_jobs", {"run_id": run.id})
        if isinstance(jobs, NotAvailableResult):
            continue
        for job in jobs.jobs:
            log = toolkit.call("get_job_log", {"job_id": job.id})
            out.append((f"get_job_log({job.id})", log))
            call(f"extract_failure_block({job.id})", "extract_failure_block", {"job_id": job.id})
            if not isinstance(log, NotAvailableResult):
                paged(f"get_job_log({job.id})", "get_job_log", {"job_id": job.id})

    commits = LAB.git.rev_list(f"{source.baseline_commit}..{case.eval_commit}") + (source.baseline_commit,)
    for sha in commits:
        commit = call(f"get_commit({sha[:7]})", "get_commit", {"sha": sha})
        for f in commit.files:
            paged(f"get_commit({sha[:7]}, {f.filename})", "get_commit", {"sha": sha, "path": f.filename})

    diff = call("compare(baseline, eval)", "compare", {"base": source.baseline_commit, "head": case.eval_commit})
    for f in diff.files:
        paged(f"compare(..., {f.filename})", "compare",
              {"base": source.baseline_commit, "head": case.eval_commit, "path": f.filename})

    paged("get_file(workflow)", "get_file", {"path": ".github/workflows/ci.yml"})
    paged("get_file(root)", "get_file", {"path": ""})
    for f in diff.files:
        paged(f"get_file({f.filename})", "get_file", {"path": f.filename})
        paged(f"get_file({f.filename}, baseline)", "get_file", {"path": f.filename, "ref": source.baseline_commit})
    return out
