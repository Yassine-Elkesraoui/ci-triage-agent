"""A rules-only baseline: hand-written CI triage heuristics, no LLM.

Written from general CI knowledge before it was ever scored (pre-registered):
the rules below were committed first, and only then evaluated. It reads GitHub
only through the toolkit, never ground truth or lab files.

Decision rules, checked in order; the first that fires decides:

==  =============================  ===============================================================  =============== ====
#   Rule                           Fires when                                                       Category        Conf
==  =============================  ===============================================================  =============== ====
1   Same code, different result    The eval commit's diff is truly empty (no files, no hidden-file  flaky           0.90
                                   marker) and an earlier run of the same tree passed. The same
                                   tree: the eval commit and the ancestors reached through
                                   truly-empty commits (plus the first non-empty one, whose tree
                                   an empty child shares).
2   Time limit                     The failure block has a "has timed out after" line, or the       timeout         0.85
                                   failing job was cancelled and its duration reaches (>= 95% of)
                                   the job's timeout-minutes in the workflow file at the eval
                                   commit.
3   Docker build                   The failing job is a Docker job (name or a step mentions         build           0.85
                                   docker) and the other jobs, the smoke job among them, passed.
4   Dependency install step        The failing step installs dependencies (install, pip, poetry,    dependency      0.80
                                   requirements, dependencies).
5   Environment setup step         The failing step is setup-python or another environment-setup    environment     0.75
                                   step (actions/setup-*, "Set up ...", checkout, cache).
6a  Changed: requirements          compare(baseline, eval) touches a requirements, constraints,     dependency      0.70
                                   lock or packaging file (pyproject.toml when its changed lines
                                   name dependencies or version pins).
6b  Changed: CI or test config     ... touches a workflow, pytest config (pytest.ini, tox.ini,      environment     0.65
                                   setup.cfg, other pyproject.toml changes) or env settings
                                   (.env*, .python-version, runtime.txt).
6c  Changed: Dockerfile            ... touches a Dockerfile, .dockerignore or compose file.         build           0.70
6d  Changed: nondeterministic      ... touches test code whose added lines use randomness, time,    flaky           0.55
    test                           threads or unordered collections.
6e  Changed: application code      ... touches any other non-doc file.                              real_regression 0.60
7   Fallback                       Nothing above fired.                                             real_regression 0.30
==  =============================  ===============================================================  =============== ====

Rule 6 checks 6a to 6e in that order over all changed files; the first kind present
wins. The baseline is the newest successful run created before the eval run on a
commit outside the eval commit's tree (rule 1's set), i.e. the last green run of
different code.

Other fields:

- failing_job, failing_step: from get_jobs; the first job that failed (else timed
  out, else was cancelled), and in it the first step that failed (else timed out,
  else was cancelled).
- suspect_file: among the changed files, the first of the kinds the category
  points to (dependency: requirements; environment: workflow, config, env;
  build: Docker; flaky: test, app; timeout: workflow, test, app; real_regression:
  app, test); else the first changed file; None when nothing changed.
- evidence: up to 3 lines copied verbatim from extract_failure_block, error-looking
  lines first, in block order.
- root_cause: a template naming the rule that fired and what it saw.
- suggested_fix: restore the suspect file's change: its removed lines when there
  are any, else the lines it added, to be removed; None when it has neither.
- recommended_action: ``ACTION_FOR[category]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from ci_triage.diagnosis import ACTION_FOR, MAX_EVIDENCE, Category, Diagnosis, Evidence, EvidenceSource
from ci_triage.predictors.base import CaseView
from ci_triage.tools import Toolkit
from ci_triage.tools.models import (
    CommitResult,
    CompareResult,
    FailureBlockResult,
    FileResult,
    Job,
    JobsResult,
    RunInfo,
    RunsResult,
    Step,
)

UNKNOWN = "unknown"
MAX_QUOTES = min(3, MAX_EVIDENCE)
MAX_FIX_LINES = 40
TIMEOUT_REACHED = 0.95  # a cancelled job "reaches" its timeout at 95% of it (timestamps are not exact)
_MAX_PAGES = 50  # pagination guard

CONFIDENCE = {"1": 0.90, "2": 0.85, "3": 0.85, "4": 0.80, "5": 0.75,
              "6a": 0.70, "6b": 0.65, "6c": 0.70, "6d": 0.55, "6e": 0.60, "7": 0.30}

RULE_NAMES = {
    "1": "same code, different result",
    "2": "time limit",
    "3": "Docker job failed while the other jobs passed",
    "4": "the failing step installs dependencies",
    "5": "the failing step sets up the environment",
    "6a": "a requirements file changed",
    "6b": "CI, test or environment configuration changed",
    "6c": "the Dockerfile changed",
    "6d": "test code using randomness, time, threads or unordered collections changed",
    "6e": "application code changed",
    "7": "fallback, nothing more specific matched",
}

_FAILED_ORDER = ("failure", "timed_out", "cancelled")

# -- step and job patterns ----------------------------------------------------------

_TIMED_OUT_LINE = "has timed out after"
_DOCKER = re.compile(r"(?i)docker")
_INSTALL_STEP = re.compile(r"(?i)\binstall|\bpip\b|\bpoetry\b|\brequirements\b|\bdependenc")
_SETUP_STEP = re.compile(r"(?i)setup-|\bset up\b|\bsetup\b|\bcheckout\b|\bcache\b")

# -- file kinds -----------------------------------------------------------------------

REQUIREMENTS, WORKFLOW, CONFIG, ENV = "requirements", "workflow", "config", "env"
DOCKER, TEST, APP = "docker", "test", "app"

_REQUIREMENTS_NAME = re.compile(
    r"(?i)^(?:.*requirements.*\.(?:txt|in)|constraints.*\.txt|pipfile(?:\.lock)?|poetry\.lock|uv\.lock|setup\.py)$"
)
_CONFIG_NAME = re.compile(r"(?i)^(?:pytest\.ini|tox\.ini|setup\.cfg|\.coveragerc)$")
_ENV_NAME = re.compile(r"(?i)^(?:\.env(?:\..+)?|.+\.env|\.python-version|runtime\.txt|\.tool-versions)$")
_DOCKER_NAME = re.compile(
    r"(?i)^(?:dockerfile(?:\..+)?|.+\.dockerfile|\.dockerignore|(?:docker-)?compose.*\.ya?ml)$"
)
_TEST_NAME = re.compile(r"^(?:test_.+\.py|.+_test\.py|conftest\.py)$")
_DOC_NAME = re.compile(r"(?i)\.(?:md|rst)$|^(?:license|changelog|authors)(?:\..+)?$")
_DEPENDENCY_LINE = re.compile(r"(?i)dependencies|requires|[=~!<>]=|\bversion\b")

# Nondeterminism in test code: randomness, time, threads, unordered collections.
_FLAKY_PATTERN = re.compile(
    r"\brandom\b|\brandint\b|\bshuffle\b|\buuid\d?\b|\bsecrets\.|np\.random"
    r"|\btime\.(?:time|sleep|monotonic|perf_counter)\b|\bdatetime\.(?:now|utcnow|today)\b|\bdate\.today\b"
    r"|\bthreading\b|\bThread\(|\bThreadPool|\bconcurrent\.futures\b|\bmultiprocessing\b"
    r"|\basyncio\.(?:gather|wait|sleep)\b"
    r"|\bset\(|\bfrozenset\(|\.popitem\(|\bos\.listdir\(|\bglob\.glob\(|\bhash\("
)

# Which file kinds the suspect is picked from, per category, in order.
SUSPECT_KINDS: dict[Category, tuple[str, ...]] = {
    Category.DEPENDENCY: (REQUIREMENTS,),
    Category.ENVIRONMENT: (WORKFLOW, CONFIG, ENV),
    Category.BUILD: (DOCKER,),
    Category.FLAKY: (TEST, APP),
    Category.TIMEOUT: (WORKFLOW, TEST, APP),
    Category.REAL_REGRESSION: (APP, TEST),
}

# Evidence lines that say what went wrong, preferred over context lines.
_ERRORISH = re.compile(
    r"(?i)^##\[error\]|^E\s|^(?:FAILED|ERROR)\b|\b(?:error|exception|failed|failure|timed out|not found|"
    r"conflict|cancell?ed|denied|unable to)\b|^[A-Za-z_][\w.]*(?:Error|Exception):"
)
_NUMBER_PREFIX = re.compile(r"^\d+\| ")


def file_kind(path: str, patch: str | None = None) -> str | None:
    """The kind of a changed file for rule 6; None for docs (they decide nothing)."""
    clean = path.replace("\\", "/").strip("/")
    parts = PurePosixPath(clean).parts
    name = parts[-1] if parts else clean
    if clean.startswith(".github/workflows/") or clean.startswith(".github/actions/"):
        return WORKFLOW
    if name == "pyproject.toml":
        changed = "\n".join(_changed_lines(patch or "", "+") + _changed_lines(patch or "", "-"))
        return REQUIREMENTS if _DEPENDENCY_LINE.search(changed) else CONFIG
    if _REQUIREMENTS_NAME.match(name):
        return REQUIREMENTS
    if _CONFIG_NAME.match(name):
        return CONFIG
    if _ENV_NAME.match(name):
        return ENV
    if _DOCKER_NAME.match(name):
        return DOCKER
    if _TEST_NAME.match(name) or any(part in ("tests", "test") for part in parts[:-1]):
        return TEST
    if _DOC_NAME.search(name):
        return None
    return APP


def _changed_lines(patch: str, sign: str) -> list[str]:
    """The added (``sign="+"``) or removed (``"-"``) lines of a unified diff, without the sign."""
    header = sign * 3
    return [line[1:] for line in patch.split("\n") if line.startswith(sign) and not line.startswith(header)]


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


# -- reading through the toolkit ----------------------------------------------------


@dataclass
class _Changed:
    filename: str
    kind: str | None
    patch: str


class _Reader:
    def __init__(self, toolkit: Toolkit) -> None:
        self.toolkit = toolkit

    def call(self, name: str, args: dict, expected: type):
        result = self.toolkit.call(name, args)
        return result if isinstance(result, expected) else None

    def same_tree(self, head: str) -> tuple[set[str], bool]:
        """Commits with ``head``'s tree, and whether ``head``'s own diff is truly empty."""
        same, sha, head_empty = {head}, head, None
        for _ in range(_MAX_PAGES):
            commit = self.call("get_commit", {"sha": sha}, CommitResult)
            empty = (commit is not None and not commit.files and commit.hidden_files is None
                     and commit.diff_note is None and bool(commit.parents))
            if head_empty is None:
                head_empty = empty
            if not empty:
                break
            sha = commit.parents[0]
            same.add(sha)  # an empty commit has its parent's tree
        return same, bool(head_empty)

    def patch(self, base: str, head: str, path: str) -> str:
        """The whole patch of one file of ``compare(base, head)``, following pages."""
        lines, offset = [], 0
        for _ in range(_MAX_PAGES):
            diff = self.call("compare", {"base": base, "head": head, "path": path, "offset": offset}, CompareResult)
            if diff is None or not diff.files:
                break
            page = diff.files[0]
            if page.patch:
                lines.append(page.patch)
            if page.next_offset is None:
                break
            offset = page.next_offset
        return "\n".join(lines)

    def changed(self, base: str | None, head: str) -> list[_Changed]:
        if base is None:
            return []
        diff = self.call("compare", {"base": base, "head": head}, CompareResult)
        if diff is None:
            return []
        out = []
        for f in diff.files:
            patch = f.patch or ""
            if f.next_offset is not None:
                patch = self.patch(base, head, f.filename)
            out.append(_Changed(f.filename, file_kind(f.filename, patch), patch))
        return out

    def file_lines(self, path: str, ref: str) -> list[str]:
        lines, offset = [], 0
        for _ in range(_MAX_PAGES):
            page = self.call("get_file", {"path": path, "ref": ref, "offset": offset, "max_lines": 1000}, FileResult)
            if page is None or page.content is None:
                break
            lines += [_NUMBER_PREFIX.sub("", line, count=1) for line in page.content.split("\n")]
            if page.next_offset is None:
                break
            offset = page.next_offset
        return lines


# -- the workflow's timeout-minutes ---------------------------------------------------


def _scalar(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"" else value


def job_timeout_minutes(workflow: list[str], job_name: str) -> float | None:
    """The job-level ``timeout-minutes`` of the job ``job_name`` (its ``name:``, else its key); no YAML library."""
    jobs_at = next((i for i, line in enumerate(workflow) if re.match(r"^jobs:\s*(?:#.*)?$", line)), None)
    if jobs_at is None:
        return None
    jobs: list[dict[str, str]] = []
    key_indent = child_indent = None
    for line in workflow[jobs_at + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        match = re.match(r"^\s*([\w.-]+|'[^']*'|\"[^\"]*\"):(.*)$", line)
        if key_indent is None:
            key_indent = indent
        if indent == key_indent and match:
            jobs.append({"key": _scalar(match[1])})
            child_indent = None
        elif jobs and indent > key_indent:
            child_indent = child_indent or indent
            if indent == child_indent and match and match[1] in ("name", "timeout-minutes"):
                jobs[-1][match[1]] = _scalar(match[2])
    wanted = " ".join(job_name.split()).casefold()
    for job in jobs:
        names = {" ".join(job.get("name", job["key"]).split()).casefold(), job["key"].casefold()}
        if wanted in names or any(wanted.startswith(name + " (") for name in names):
            try:
                return float(job["timeout-minutes"])
            except (KeyError, ValueError):
                return None
    return None


# -- the predictor --------------------------------------------------------------------


def _failing_job(jobs: list[Job]) -> Job | None:
    for conclusion in _FAILED_ORDER:
        job = next((job for job in jobs if job.conclusion == conclusion), None)
        if job is not None:
            return job
    return None


def _failing_step(job: Job) -> Step | None:
    for conclusion in _FAILED_ORDER:
        step = next((step for step in job.steps if step.conclusion == conclusion), None)
        if step is not None:
            return step
    return next((step for step in job.steps if step.status == "in_progress"), None)


def _evidence(block: FailureBlockResult | None) -> list[Evidence]:
    if block is None:
        return []
    lines = []
    for b in block.blocks:
        for line in b.text.split("\n"):
            text = _NUMBER_PREFIX.sub("", line, count=1).strip()
            if text and text != "…" and text not in lines:
                lines.append(text)
    ranked = [line for line in lines if _ERRORISH.search(line)] + [line for line in lines if not _ERRORISH.search(line)]
    return [Evidence(quote=line[:1000], source=EvidenceSource.LOG) for line in ranked[:MAX_QUOTES]]


def _suspect(category: Category, changed: list[_Changed]) -> _Changed | None:
    for kind in SUSPECT_KINDS[category]:
        found = next((c for c in changed if c.kind == kind), None)
        if found is not None:
            return found
    return changed[0] if changed else None


def _fix(suspect: _Changed | None) -> str | None:
    if suspect is None:
        return None
    removed = [line for line in _changed_lines(suspect.patch, "-") if line.strip()]
    if removed:
        return f"Restore the lines the change removed from {suspect.filename}:\n" + "\n".join(removed[:MAX_FIX_LINES])
    added = [line for line in _changed_lines(suspect.patch, "+") if line.strip()]
    if added:
        return f"Remove the lines the change added to {suspect.filename}:\n" + "\n".join(added[:MAX_FIX_LINES])
    return None


def _diff_rule(changed: list[_Changed]) -> tuple[str, Category, str] | None:
    """Rule 6: (rule id, category, detail) from the kinds of the changed files."""
    kinds = {c.kind for c in changed}
    named = lambda *ks: ", ".join(c.filename for c in changed if c.kind in ks)  # noqa: E731
    if REQUIREMENTS in kinds:
        return "6a", Category.DEPENDENCY, named(REQUIREMENTS)
    if kinds & {WORKFLOW, CONFIG, ENV}:
        return "6b", Category.ENVIRONMENT, named(WORKFLOW, CONFIG, ENV)
    if DOCKER in kinds:
        return "6c", Category.BUILD, named(DOCKER)
    flaky = [c for c in changed if c.kind == TEST and _FLAKY_PATTERN.search("\n".join(_changed_lines(c.patch, "+")))]
    if flaky:
        return "6d", Category.FLAKY, ", ".join(c.filename for c in flaky)
    if APP in kinds:
        return "6e", Category.REAL_REGRESSION, named(APP)
    return None


class RulesPredictor:
    """The decision rules of this module's docstring, in order."""

    name = "rules"

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        read = _Reader(toolkit)
        run = read.call("get_run", {"run_id": case_view.run_id}, RunInfo)
        jobs_result = read.call("get_jobs", {"run_id": case_view.run_id}, JobsResult)
        jobs = jobs_result.jobs if jobs_result else []
        job = _failing_job(jobs)
        step = _failing_step(job) if job else None
        block = read.call("extract_failure_block", {"job_id": job.id}, FailureBlockResult) if job else None

        head = run.head_sha if run else "HEAD"
        same_tree, head_empty = read.same_tree(head)
        runs = read.call("list_runs", {"branch": (run.head_branch if run else None) or "main", "limit": 100},
                         RunsResult)
        eval_created = _ts(run.created_at) if run else None
        earlier = [r for r in (runs.runs if runs else []) if r.id != case_view.run_id
                   and (eval_created is None or (_ts(r.created_at) or eval_created) < eval_created)]
        green_same = [r for r in earlier if r.conclusion == "success" and r.head_sha in same_tree]
        green_other = [r for r in earlier if r.conclusion == "success" and r.head_sha not in same_tree]
        baseline = max(green_other, key=lambda r: (r.created_at or "", r.id)).head_sha if green_other else None
        changed = read.changed(baseline, head)

        rule, category, detail = self._decide(read, run, jobs, job, step, block, head, head_empty, green_same,
                                              changed)
        suspect = _suspect(category, changed)
        cause = f"Rule {rule} ({RULE_NAMES[rule]}): {detail}."
        return Diagnosis(
            category=category,
            confidence=CONFIDENCE[rule],
            failing_job=job.name if job else UNKNOWN,
            failing_step=step.name if step else UNKNOWN,
            suspect_file=suspect.filename if suspect else None,
            evidence=_evidence(block),
            root_cause=cause if len(cause) <= 500 else cause[:499] + "…",
            suggested_fix=_fix(suspect),
            recommended_action=ACTION_FOR[category],
        )

    def _decide(self, read: _Reader, run: RunInfo | None, jobs: list[Job], job: Job | None, step: Step | None,
                block: FailureBlockResult | None, head: str, head_empty: bool, green_same: list,
                changed: list[_Changed]) -> tuple[str, Category, str]:
        # 1. same code, different result
        if head_empty and green_same:
            return "1", Category.FLAKY, (f"commit {head[:7]} changes nothing, and run {green_same[0].id} "
                                         "of the same code passed")
        # 2. time limit
        if block is not None and any(_TIMED_OUT_LINE in b.text for b in block.blocks):
            return "2", Category.TIMEOUT, f"the log of job '{job.name}' has a '{_TIMED_OUT_LINE}' line"
        if job is not None and job.conclusion == "cancelled" and job.duration_s is not None:
            workflow = (run.path if run and run.path else ".github/workflows/ci.yml").split("@", 1)[0]
            limit = job_timeout_minutes(read.file_lines(workflow, head), job.name)
            if limit is not None and job.duration_s >= TIMEOUT_REACHED * limit * 60:
                return "2", Category.TIMEOUT, (f"job '{job.name}' was cancelled after {job.duration_s} s, "
                                               f"at its timeout-minutes of {limit:g}")
        # 3. Docker job failed while the smoke job passed
        if job is not None and job.conclusion == "failure":
            others = [other for other in jobs if other.id != job.id]
            is_docker = _DOCKER.search(job.name) or any(_DOCKER.search(s.name) for s in job.steps)
            if is_docker and others and all(other.conclusion == "success" for other in others):
                return "3", Category.BUILD, (f"job '{job.name}' failed while "
                                             f"{', '.join(repr(o.name) for o in others)} passed")
        # 4. and 5. the failing step
        if step is not None and _INSTALL_STEP.search(step.name):
            return "4", Category.DEPENDENCY, f"step '{step.name}' failed"
        if step is not None and _SETUP_STEP.search(step.name):
            return "5", Category.ENVIRONMENT, f"step '{step.name}' failed"
        # 6. what changed
        if found := _diff_rule(changed):
            rule, category, files = found
            return rule, category, f"changed since the last green run: {files}"
        # 7. fallback
        return "7", Category.REAL_REGRESSION, (f"step '{step.name}' failed" if step else "the run failed")


_DEFAULT = RulesPredictor()


def predict(case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
    """``RulesPredictor().predict``."""
    return _DEFAULT.predict(case_view, toolkit)
