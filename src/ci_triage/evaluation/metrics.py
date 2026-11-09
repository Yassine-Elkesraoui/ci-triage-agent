"""Summary metrics over graded cases, and their Markdown report."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ci_triage.diagnosis import CATEGORIES
from ci_triage.evaluation.grader import QUOTE_RULES, CaseResult

NO_DIAGNOSIS = "(none)"  # confusion-matrix column for cases where the predictor raised


@dataclass(frozen=True)
class CategoryScore:
    category: str
    support: int  # cases of this category
    predicted: int  # cases predicted as this category
    correct: int
    precision: float
    recall: float
    f1: float
    step_rate: float
    localisation_rate: float
    grounding_rate: float
    fix_exact_rate: float
    action_rate: float
    fix_cases: int = 0  # cases the fix rate is over: all (grader v1), restore-type only (v2)


@dataclass(frozen=True)
class Summary:
    predictor: str
    cases: int
    correct: int
    accuracy: float
    macro_f1: float
    step_rate: float
    localisation_rate: float
    grounding_rate: float  # cases with at least one quote, all grounded
    quote_grounding_rate: float  # grounded quotes / quotes; 0 if there were none
    quotes: int
    fix_exact_rate: float
    fix_partial_rate: float
    fix_none_rate: float
    action_rate: float
    mean_tool_calls: float
    brier: float  # mean (confidence - category correct)^2; 0 is perfect
    errors: int
    per_category: dict[str, CategoryScore]
    confusion: dict[str, dict[str, int]]  # true -> predicted -> count
    job_rate: float = 0.0  # failing_job correct
    mean_wall_time_s: float = 0.0  # per case
    max_wall_time_s: float = 0.0
    mode: str = "full"  # evaluation mode, see ci_triage.tools.modes
    grader: str = "v1"
    fix_cases: int = 0  # cases the fix-match rates are over: all (v1), restore-type only (v2)
    needs_judge: int = 0  # v2: remove-type cases left to a judge
    needs_judge_no_fix: int = 0  # ... of which the diagnosis suggests no fix at all
    quote_rules_followed: int = 0  # v2: quotes that break no quote rule
    cases_quote_rules_followed: int = 0  # v2: cases with at least one quote, every quote following the rules
    quote_rule_breaks: dict[str, int] = field(default_factory=dict)  # v2: quotes breaking each rule


def _rate(group: Sequence[CaseResult], hit: Callable[[CaseResult], bool]) -> float:
    return sum(map(hit, group)) / len(group) if group else 0.0


def _ratio(a: int, b: int) -> float:
    return a / b if b else 0.0


def _grader(results: Sequence[CaseResult]) -> str:
    graders = {r.grader for r in results}
    if len(graders) > 1:
        raise ValueError(f"results graded with different graders: {sorted(graders)}")
    return graders.pop() if graders else "v1"


def summarise(predictor: str, results: Sequence[CaseResult], mode: str = "full") -> Summary:
    grader = _grader(results)
    # v2 leaves remove-type fixes to a judge: the fix-match rates are over restore-type cases only
    fixable = (lambda r: True) if grader == "v1" else (lambda r: r.fix_kind == "restore")  # noqa: E731
    columns = [*CATEGORIES, NO_DIAGNOSIS]
    confusion = {true: dict.fromkeys(columns, 0) for true in CATEGORIES}
    for r in results:
        confusion[r.true_category][r.predicted_category or NO_DIAGNOSIS] += 1

    per_category = {}
    for category in CATEGORIES:
        group = [r for r in results if r.true_category == category]
        predicted = sum(r.predicted_category == category for r in results)
        correct = confusion[category][category]
        precision, recall = _ratio(correct, predicted), _ratio(correct, len(group))
        per_category[category] = CategoryScore(
            category=category, support=len(group), predicted=predicted, correct=correct,
            precision=precision, recall=recall, f1=_ratio(2 * precision * recall, precision + recall),
            step_rate=_rate(group, lambda r: r.step_correct),
            localisation_rate=_rate(group, lambda r: r.file_correct),
            grounding_rate=_rate(group, lambda r: r.evidence_grounded),
            fix_exact_rate=_rate([r for r in group if fixable(r)], lambda r: r.fix_match == "exact"),
            action_rate=_rate(group, lambda r: r.action_correct),
            fix_cases=sum(map(fixable, group)),
        )

    correct = sum(r.category_correct for r in results)
    quotes = sum(r.quotes for r in results)
    fix_group = [r for r in results if fixable(r)]
    rules = [r.quote_rules for r in results if r.quote_rules is not None]
    judged = [r for r in results if r.fix_match == "needs_judge"]
    return Summary(
        predictor=predictor,
        cases=len(results),
        correct=correct,
        accuracy=_ratio(correct, len(results)),
        macro_f1=sum(score.f1 for score in per_category.values()) / len(per_category),
        step_rate=_rate(results, lambda r: r.step_correct),
        localisation_rate=_rate(results, lambda r: r.file_correct),
        grounding_rate=_rate(results, lambda r: r.evidence_grounded),
        quote_grounding_rate=_ratio(sum(r.quotes_grounded for r in results), quotes),
        quotes=quotes,
        fix_exact_rate=_rate(fix_group, lambda r: r.fix_match == "exact"),
        fix_partial_rate=_rate(fix_group, lambda r: r.fix_match == "partial"),
        fix_none_rate=_rate(fix_group, lambda r: r.fix_match == "none"),
        action_rate=_rate(results, lambda r: r.action_correct),
        mean_tool_calls=_ratio(sum(r.tool_calls for r in results), len(results)),
        brier=_ratio(sum((r.confidence - r.category_correct) ** 2 for r in results), len(results)),
        errors=sum(r.error is not None for r in results),
        per_category=per_category,
        confusion=confusion,
        job_rate=_rate(results, lambda r: r.job_correct),
        mean_wall_time_s=_ratio(sum(r.wall_time_s for r in results), len(results)),
        max_wall_time_s=max((r.wall_time_s for r in results), default=0.0),
        mode=str(mode),
        grader=grader,
        fix_cases=len(fix_group),
        needs_judge=len(judged),
        needs_judge_no_fix=sum(not (r.diagnosis.suggested_fix or "").strip() for r in judged if r.diagnosis),
        quote_rules_followed=sum(q.followed for q in rules),
        cases_quote_rules_followed=sum(r.quotes > 0 and r.quote_rules is not None and r.quote_rules.followed == r.quotes
                                       for r in results),
        quote_rule_breaks={rule: sum(getattr(q, rule) for q in rules) for rule in QUOTE_RULES} if rules else {},
    )


def _pct(value: float) -> str:
    return f"{value:.1%}"


RULE_LABELS = {"wrong_source": "wrong source label", "section_header": "section header",
               "history_not_name": "RUN AND JOBS beyond a job or step name", "line_number_prefix": "line-number prefix",
               "ellipsis": "ellipsis"}


def _count(part: int, whole: int) -> str:
    return f"{part}/{whole} ({_pct(_ratio(part, whole))})"


def _v2_rows(s: Summary) -> list[str]:
    breaks = ", ".join(f"{RULE_LABELS[rule]} {n}" for rule, n in s.quote_rule_breaks.items())
    return [
        f"| quote rules followed (quotes) | {_count(s.quote_rules_followed, s.quotes)} |",
        f"| quote rules followed (cases, every quote) | {_count(s.cases_quote_rules_followed, s.cases)} |",
        f"| quote rule breaks (quotes) | {breaks or '-'} |",
        f"| fix match exact / partial / none, restore-type ({s.fix_cases} cases) | {_pct(s.fix_exact_rate)} / "
        f"{_pct(s.fix_partial_rate)} / {_pct(s.fix_none_rate)} |",
        f"| fix match, remove-type | {s.needs_judge} need a judge ({s.needs_judge_no_fix} suggest no fix) |",
    ]


def to_markdown(summary: Summary) -> str:
    s = summary
    v2 = s.grader == "v2"
    fix_rows = _v2_rows(s) if v2 else [
        f"| fix match exact / partial / none | {_pct(s.fix_exact_rate)} / {_pct(s.fix_partial_rate)} / "
        f"{_pct(s.fix_none_rate)} |"]
    lines = [
        f"# Evaluation: {s.predictor}" + (f" ({s.mode} mode)" if s.mode != "full" else ""),
        "",
        f"{s.cases} cases" + (f", {s.errors} with no diagnosis (the predictor raised)" if s.errors else "") + "."
        + (" Graded with grader v2: normalised grounding, quote rules, remove-type fixes left to a judge." if v2
           else ""),
        "",
        "| metric | value |",
        "|---|---|",
        f"| category accuracy | {s.correct}/{s.cases} ({_pct(s.accuracy)}) |",
        f"| macro-F1 | {s.macro_f1:.3f} |",
        f"| failing step correct | {_pct(s.step_rate)} |",
        f"| localisation (suspect file) | {_pct(s.localisation_rate)} |",
        f"| grounding (cases, all quotes grounded) | {_pct(s.grounding_rate)} |",
        f"| grounding (quotes) | {_pct(s.quote_grounding_rate)} of {s.quotes} |",
        *fix_rows,
        f"| action consistent with category | {_pct(s.action_rate)} |",
        f"| mean tool calls | {s.mean_tool_calls:.2f} |",
        f"| Brier score (lower is better) | {s.brier:.4f} |",
        f"| failing job correct | {_pct(s.job_rate)} |",
        f"| wall time per case, mean / max | {s.mean_wall_time_s:.3f} s / {s.max_wall_time_s:.3f} s |",
        "",
        "## Per category",
        "",
        "| category | cases | predicted | precision | recall | F1 | step | localisation | grounded | fix exact | action |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in s.per_category.values():
        lines.append(f"| {c.category} | {c.support} | {c.predicted} | {c.precision:.2f} | {c.recall:.2f} | "
                     f"{c.f1:.2f} | {_pct(c.step_rate)} | {_pct(c.localisation_rate)} | {_pct(c.grounding_rate)} | "
                     f"{_pct(c.fix_exact_rate) if c.fix_cases or not v2 else '-'} | {_pct(c.action_rate)} |")
    columns = [*CATEGORIES] + ([NO_DIAGNOSIS] if s.errors else [])
    lines += [
        "",
        "## Confusion matrix",
        "",
        "Rows: true category. Columns: predicted.",
        "",
        "| true \\ predicted | " + " | ".join(columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    for true, row in s.confusion.items():
        lines.append(f"| {true} | " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(lines) + "\n"
