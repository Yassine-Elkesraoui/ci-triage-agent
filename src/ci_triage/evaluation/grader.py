"""Grades one Diagnosis against a case's ground truth and the toolkit calls that produced it.

- category, failing_step (case and spacing ignored), suspect_file (the injection's target file);
- failing_job: the failed job's name as get_jobs shows it (case and spacing ignored);
- evidence grounding: a quote counts only if it appears verbatim in a string the toolkit returned
  for this case, from a tool of its declared source (``SOURCE_TOOLS``). Log, file and patch pages
  number their lines, so each string is also searched with the "<n>| " prefixes removed, which lets
  a multi-line quote match;
- fix match: does suggested_fix contain the text the revert restored, whitespace normalised?
  exact: all of it; partial: one of its lines (8+ characters), or every token the revert brought
  back that the injection did not have; none otherwise. 22 reverts restore nothing, only removing
  what the injection added (a timeout-minutes line, an env block, a pytest.ini option, a sleep);
  for those the removed text is what the fix must name;
- action: the one ``ACTION_FOR`` the true category.

That is grader v1. Grader v2 (the default; ``grader="v1"`` reproduces the v1 scores) changes three things,
after the read-only audit of the D6a runs (``reports/grader-v2-regrade.md``):

- grounding: quote and source text both go through ``ground_normalise`` (per line: a "<n>| " line-number
  prefix, the indentation and one diff gutter marker, "+" or "-", stripped; then whitespace collapsed as
  fix match does). A consequence: v2 cannot tell an added diff line from a removed one. A quote is grounded if that text is in the section it cites, or in any section (the label is
  then wrong). Sections are what the predictor showed the model: the one-call bundle's sections, or for
  tool-calling predictors the outputs of each source's tools (``SOURCE_TOOLS``);
- quote rules followed, a separate metric that does not affect grounding: per quote, a wrong source
  label, a quoted header line the predictor formatted, a history quote that is more than a job or step
  name, a kept line-number prefix, an ellipsis the source text does not have (``QuoteRules``);
- fix match: unchanged for restore-type cases; remove-type cases get "needs_judge" (whether a fix
  removes the right thing is left to a judge) and are left out of the fix-match rates.

Grader v2.1 (the default since D6b; ``grader="v2"`` reproduces the v2 scores) changes one thing: a "history"
quote found, after the v2 normalisation, in the output of a list_runs, get_run or get_jobs call the predictor
itself saw (``HISTORY_TOOLS``, ``seen_calls``) does not break ``history_not_name``. A quote matched only in the
one-call bundle's RUN AND JOBS section keeps the job-or-step-name rule.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from ci_triage.diagnosis import ACTION_FOR, Category, Diagnosis, Evidence, EvidenceSource
from ci_triage.evaluation.ground_truth import GroundTruth
from ci_triage.tools import ToolCall

FixMatch = Literal["exact", "partial", "none", "needs_judge"]  # needs_judge: grader v2, remove-type cases
GraderVersion = Literal["v1", "v2", "v2.1"]
GRADERS: tuple[GraderVersion, ...] = ("v1", "v2", "v2.1")
DEFAULT_GRADER: GraderVersion = "v2.1"
Sections = Mapping[EvidenceSource | str, Sequence[str]]  # evidence source -> the texts shown under it

SOURCE_TOOLS: dict[EvidenceSource, frozenset[str]] = {
    EvidenceSource.LOG: frozenset({"get_job_log", "extract_failure_block"}),
    EvidenceSource.DIFF: frozenset({"get_commit", "compare"}),
    EvidenceSource.FILE: frozenset({"get_file"}),
    EvidenceSource.HISTORY: frozenset({"list_runs", "get_run", "get_jobs", "get_commit"}),
}
HISTORY_TOOLS = frozenset({"list_runs", "get_run", "get_jobs"})  # v2.1: tool output a history quote may copy
MIN_PARTIAL_LINE = 8  # shorter restored lines ("env:", "]") say too little on their own

_LINE_NUMBER = re.compile(r"^\d+\| ", re.MULTILINE)
_TOKEN = re.compile(r"[A-Za-z0-9_]+(?:[.\-][A-Za-z0-9_]+)*")  # keeps 2.13.4 and timeout-minutes whole

# -- grader v2 patterns
_ANY_LINE_NUMBER = re.compile(r"^\s*\d+\|(?= |$) ?")  # "12| " as pages number lines, any number, one space
_MARKER = ("+", "-")  # a diff line's gutter, once the leading space (the context gutter) is gone
_ELLIPSIS = ("...", "…")
# Lines the one-call predictor formats itself (``ci_triage.predictors.llm_once``), never tool output:
_HEADER_LINE = re.compile("|".join((
    r"=== (?:RUN AND JOBS|FAILURE BLOCK|WORKFLOW FILE|DIFF) \[\w+\].*===",  # a section header
    r"\[?[a-z_]+(?:, [a-z_]+)*; (?:step |lines \d).*",  # a failure-block header "[kinds; step S; lines a-b]"
    r"--- \S.* \([a-z]+, \+\d+ -\d+\)",  # a diff file header "--- path (modified, +1 -0)"
    r"From [0-9a-f]{7,40} \(last successful run\) to [0-9a-f]{7,40} \(this run\)",
    r"\[truncated: \d+ more characters not shown\]",
)))
QUOTE_RULES = ("wrong_source", "section_header", "history_not_name", "line_number_prefix", "ellipsis")


class QuoteRules(BaseModel):
    """Grader v2: how many of a case's quotes break each quoting rule (one quote can break several)."""

    followed: int  # quotes that break none
    wrong_source: int = 0  # not in the section it cites, but in another one
    section_header: int = 0  # quotes a header line the predictor formatted
    history_not_name: int = 0  # a history quote that is not just a job or step name (v2.1: nor in history tool output)
    line_number_prefix: int = 0  # keeps a "12| " line-number prefix
    ellipsis: int = 0  # has "..." or "…" and is not in the source text as written


class CaseResult(BaseModel):
    case_id: str
    true_category: Category
    predicted_category: Category | None  # None when the predictor raised
    confidence: float  # 0 when the predictor raised
    category_correct: bool
    step_correct: bool
    job_correct: bool
    file_correct: bool
    quotes: int
    quotes_grounded: int
    evidence_grounded: bool  # at least one quote, and every quote grounded
    fix_kind: Literal["restore", "remove"]
    fix_match: FixMatch
    action_correct: bool
    tool_calls: int
    wall_time_s: float = 0.0  # time the predictor took on this case
    error: str | None = None
    diagnosis: Diagnosis | None
    calls: list[ToolCall]
    grader: GraderVersion = "v1"  # results saved before grader v2 were graded with v1
    quote_rules: QuoteRules | None = None  # grader v2 only


# -- evidence -------------------------------------------------------------------


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _searchable(call: ToolCall) -> list[str]:
    texts = []
    for text in _strings(call.output):
        texts.append(text)
        if (stripped := _LINE_NUMBER.sub("", text)) != text:
            texts.append(stripped)
    return texts


def is_grounded(evidence: Evidence, calls: Sequence[ToolCall]) -> bool:
    quote = evidence.quote.strip()
    tools = SOURCE_TOOLS[evidence.source]
    return bool(quote) and any(
        quote in text for call in calls if call.tool in tools and call.available for text in _searchable(call)
    )


# -- fix ------------------------------------------------------------------------


def normalise(text: str) -> str:
    return " ".join(text.split())


def _tokens(lines: Iterable[str]) -> set[str]:
    return {token for line in lines for token in _TOKEN.findall(line)}


def fix_target(truth: GroundTruth) -> tuple[Literal["restore", "remove"], tuple[str, ...], tuple[str, ...]]:
    """(kind, lines the fix must contain, lines it replaces)."""
    if truth.restored:
        return "restore", truth.restored, truth.removed
    return "remove", truth.removed, ()


def fix_match(suggested_fix: str | None, truth: GroundTruth) -> FixMatch:
    _, needle, replaced = fix_target(truth)
    lines = [line for line in map(normalise, needle) if line]
    if not suggested_fix or not lines:
        return "none"
    fix = normalise(suggested_fix)
    if " ".join(lines) in fix:
        return "exact"
    if any(len(line) >= MIN_PARTIAL_LINE and line in fix for line in lines):
        return "partial"
    brought_back = _tokens(lines) - _tokens(replaced)
    if brought_back and brought_back <= _tokens([fix]):
        return "partial"
    return "none"


# -- grader v2 --------------------------------------------------------------------


def ground_normalise(text: str) -> str:
    """The text v2 grounding compares: per line, a line-number prefix off, the leading whitespace off
    (indentation, and a context line's " " gutter), then one "+" or "-" diff marker off; then whitespace
    collapsed exactly as fix match does (``normalise``). Leading whitespace goes before the marker, so a
    re-indented " +  where" and "+ where" agree."""
    lines = []
    for line in text.split("\n"):
        line = _ANY_LINE_NUMBER.sub("", line, count=1).lstrip()
        lines.append(line[1:] if line[:1] in _MARKER else line)
    return normalise("\n".join(lines))


def tool_sections(calls: Sequence[ToolCall]) -> dict[EvidenceSource, list[str]]:
    """Sections for a predictor that reads tool outputs itself: each source's tools' returned strings."""
    return {source: [text for call in calls if call.tool in tools and call.available for text in _strings(call.output)]
            for source, tools in SOURCE_TOOLS.items()}


def job_and_step_names(calls: Sequence[ToolCall]) -> set[str]:
    """Every job and step name a ``get_jobs`` call returned, normalised and case-folded."""
    names = set()
    for call in calls:
        if call.tool == "get_jobs" and call.available:
            for job in call.output.get("jobs", []):
                names.add(job.get("name"))
                names.update(step.get("name") for step in job.get("steps", []))
    return {normalise(name).casefold() for name in names if name}


def is_header_line(line: str) -> bool:
    """A line the one-call predictor formats itself (section, failure-block, diff-file header), not tool text."""
    return bool(_HEADER_LINE.fullmatch(_ANY_LINE_NUMBER.sub("", line, count=1).strip()))


def history_tool_texts(calls: Sequence[ToolCall]) -> list[str]:
    """v2.1: the strings the ``HISTORY_TOOLS`` calls among ``calls`` returned, ``ground_normalise``d."""
    return [ground_normalise(text) for call in calls if call.tool in HISTORY_TOOLS and call.available
            for text in _strings(call.output)]


def check_quote(evidence: Evidence, sections: Mapping[EvidenceSource, Sequence[str]],
                names: set[str], history_seen: Sequence[str] = ()) -> tuple[bool, frozenset[str]]:
    """(grounded, the quote rules it breaks). ``sections`` and ``history_seen`` (v2.1: ``history_tool_texts``
    of the calls the predictor saw; empty for v2) must already be ``ground_normalise``d."""
    quote = ground_normalise(evidence.quote)
    in_cited = bool(quote) and any(quote in text for text in sections.get(evidence.source, ()))
    grounded = in_cited or (bool(quote) and any(
        quote in text for source, texts in sections.items() if source != evidence.source for text in texts))
    lines = [line for line in evidence.quote.split("\n") if line.strip()]
    breaks = set()
    if grounded and not in_cited:
        breaks.add("wrong_source")
    if any(map(is_header_line, lines)):
        breaks.add("section_header")
    if (evidence.source == EvidenceSource.HISTORY and normalise(evidence.quote).casefold() not in names
            and not (quote and any(quote in text for text in history_seen))):
        breaks.add("history_not_name")
    if any(_ANY_LINE_NUMBER.match(line) for line in lines):
        breaks.add("line_number_prefix")
    if not grounded and any(mark in evidence.quote for mark in _ELLIPSIS):
        breaks.add("ellipsis")
    return grounded, frozenset(breaks)


def fix_match_v2(suggested_fix: str | None, truth: GroundTruth) -> FixMatch:
    """v1 fix match for restore-type cases; "needs_judge" for remove-type ones."""
    return "needs_judge" if fix_target(truth)[0] == "remove" else fix_match(suggested_fix, truth)


def _quotes_v2(evidence: Sequence[Evidence], calls: Sequence[ToolCall], sections: Sections | None,
               history_seen: Sequence[str] = ()) -> tuple[int, QuoteRules]:
    shown = tool_sections(calls) if sections is None else sections
    normalised = {EvidenceSource(source): [ground_normalise(text) for text in texts] for source, texts in shown.items()}
    names = job_and_step_names(calls)
    checks = [check_quote(e, normalised, names, history_seen) for e in evidence]
    broken = Counter(rule for _, breaks in checks for rule in breaks)
    return (sum(grounded for grounded, _ in checks),
            QuoteRules(followed=sum(not breaks for _, breaks in checks), **{rule: broken[rule] for rule in QUOTE_RULES}))


# -- the case -------------------------------------------------------------------


def _same_path(a: str | None, b: str) -> bool:
    return a is not None and a.replace("\\", "/").removeprefix("./").strip("/") == b


def _same_name(a: str, b: str) -> bool:
    return normalise(a).casefold() == normalise(b).casefold()


def grade_case(truth: GroundTruth, diagnosis: Diagnosis | None, calls: Sequence[ToolCall],
               error: str | None = None, wall_time_s: float = 0.0, *, grader: GraderVersion = DEFAULT_GRADER,
               sections: Sections | None = None, seen_calls: Sequence[ToolCall] | None = None) -> CaseResult:
    """Grade one case. ``sections``: what the predictor showed the model, by evidence source (grader v2;
    the one-call bundle's sections). Left out, v2 uses the outputs of the calls (``tool_sections``).
    ``seen_calls`` (grader v2.1): the calls whose outputs the model itself saw, not through the bundle. Left
    out, all of ``calls`` when ``sections`` is left out too (the predictor read them itself), else none."""
    if grader not in GRADERS:
        raise ValueError(f"unknown grader {grader!r}; graders are {', '.join(GRADERS)}")
    true_category = Category(truth.category)
    kind = fix_target(truth)[0]
    if diagnosis is None:
        return CaseResult(case_id=truth.case_id, true_category=true_category, predicted_category=None, confidence=0.0,
                          category_correct=False, step_correct=False, job_correct=False, file_correct=False,
                          quotes=0, quotes_grounded=0, evidence_grounded=False, fix_kind=kind, fix_match="none",
                          action_correct=False, tool_calls=len(calls), wall_time_s=wall_time_s, error=error,
                          diagnosis=None, calls=list(calls), grader=grader,
                          quote_rules=QuoteRules(followed=0) if grader != "v1" else None)
    if grader == "v1":
        grounded = sum(is_grounded(evidence, calls) for evidence in diagnosis.evidence)
        rules, fix = None, fix_match(diagnosis.suggested_fix, truth)
    else:
        if seen_calls is None:
            seen_calls = calls if sections is None else ()
        history_seen = history_tool_texts(seen_calls) if grader == "v2.1" else ()
        grounded, rules = _quotes_v2(diagnosis.evidence, calls, sections, history_seen)
        fix = fix_match_v2(diagnosis.suggested_fix, truth)
    return CaseResult(
        case_id=truth.case_id,
        true_category=true_category,
        predicted_category=diagnosis.category,
        confidence=diagnosis.confidence,
        category_correct=diagnosis.category == true_category,
        step_correct=_same_name(diagnosis.failing_step, truth.failing_step),
        job_correct=_same_name(diagnosis.failing_job, truth.failing_job_name),
        file_correct=_same_path(diagnosis.suspect_file, truth.target_file),
        quotes=len(diagnosis.evidence),
        quotes_grounded=grounded,
        evidence_grounded=bool(diagnosis.evidence) and grounded == len(diagnosis.evidence),
        fix_kind=kind,
        fix_match=fix,
        action_correct=diagnosis.recommended_action == ACTION_FOR[true_category],
        tool_calls=len(calls),
        wall_time_s=wall_time_s,
        error=error,
        diagnosis=diagnosis,
        calls=list(calls),
        grader=grader,
        quote_rules=rules,
    )
