"""Grades one Diagnosis against a case's ground truth and the toolkit calls that produced it.

- category, failing_step (case and spacing ignored), suspect_file (the injection's target file);
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
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from ci_triage.diagnosis import ACTION_FOR, Category, Diagnosis, Evidence, EvidenceSource
from ci_triage.evaluation.ground_truth import GroundTruth
from ci_triage.tools import ToolCall

FixMatch = Literal["exact", "partial", "none"]

SOURCE_TOOLS: dict[EvidenceSource, frozenset[str]] = {
    EvidenceSource.LOG: frozenset({"get_job_log", "extract_failure_block"}),
    EvidenceSource.DIFF: frozenset({"get_commit", "compare"}),
    EvidenceSource.FILE: frozenset({"get_file"}),
    EvidenceSource.HISTORY: frozenset({"list_runs", "get_run", "get_jobs", "get_commit"}),
}
MIN_PARTIAL_LINE = 8  # shorter restored lines ("env:", "]") say too little on their own

_LINE_NUMBER = re.compile(r"^\d+\| ", re.MULTILINE)
_TOKEN = re.compile(r"[A-Za-z0-9_]+(?:[.\-][A-Za-z0-9_]+)*")  # keeps 2.13.4 and timeout-minutes whole


class CaseResult(BaseModel):
    case_id: str
    true_category: Category
    predicted_category: Category | None  # None when the predictor raised
    confidence: float  # 0 when the predictor raised
    category_correct: bool
    step_correct: bool
    file_correct: bool
    quotes: int
    quotes_grounded: int
    evidence_grounded: bool  # at least one quote, and every quote grounded
    fix_kind: Literal["restore", "remove"]
    fix_match: FixMatch
    action_correct: bool
    tool_calls: int
    error: str | None = None
    diagnosis: Diagnosis | None
    calls: list[ToolCall]


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


# -- the case -------------------------------------------------------------------


def _same_path(a: str | None, b: str) -> bool:
    return a is not None and a.replace("\\", "/").removeprefix("./").strip("/") == b


def _same_name(a: str, b: str) -> bool:
    return normalise(a).casefold() == normalise(b).casefold()


def grade_case(truth: GroundTruth, diagnosis: Diagnosis | None, calls: Sequence[ToolCall],
               error: str | None = None) -> CaseResult:
    true_category = Category(truth.category)
    kind = fix_target(truth)[0]
    if diagnosis is None:
        return CaseResult(case_id=truth.case_id, true_category=true_category, predicted_category=None, confidence=0.0,
                          category_correct=False, step_correct=False, file_correct=False, quotes=0,
                          quotes_grounded=0, evidence_grounded=False, fix_kind=kind, fix_match="none",
                          action_correct=False, tool_calls=len(calls), error=error, diagnosis=None, calls=list(calls))
    grounded = sum(is_grounded(evidence, calls) for evidence in diagnosis.evidence)
    return CaseResult(
        case_id=truth.case_id,
        true_category=true_category,
        predicted_category=diagnosis.category,
        confidence=diagnosis.confidence,
        category_correct=diagnosis.category == true_category,
        step_correct=_same_name(diagnosis.failing_step, truth.failing_step),
        file_correct=_same_path(diagnosis.suspect_file, truth.target_file),
        quotes=len(diagnosis.evidence),
        quotes_grounded=grounded,
        evidence_grounded=bool(diagnosis.evidence) and grounded == len(diagnosis.evidence),
        fix_kind=kind,
        fix_match=fix_match(diagnosis.suggested_fix, truth),
        action_correct=diagnosis.recommended_action == ACTION_FOR[true_category],
        tool_calls=len(calls),
        error=error,
        diagnosis=diagnosis,
        calls=list(calls),
    )
