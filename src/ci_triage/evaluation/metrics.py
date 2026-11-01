"""Summary metrics over graded cases, and their Markdown report."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ci_triage.diagnosis import CATEGORIES
from ci_triage.evaluation.grader import CaseResult

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


def _rate(group: Sequence[CaseResult], hit: Callable[[CaseResult], bool]) -> float:
    return sum(map(hit, group)) / len(group) if group else 0.0


def _ratio(a: int, b: int) -> float:
    return a / b if b else 0.0


def summarise(predictor: str, results: Sequence[CaseResult], mode: str = "full") -> Summary:
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
            fix_exact_rate=_rate(group, lambda r: r.fix_match == "exact"),
            action_rate=_rate(group, lambda r: r.action_correct),
        )

    correct = sum(r.category_correct for r in results)
    quotes = sum(r.quotes for r in results)
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
        fix_exact_rate=_rate(results, lambda r: r.fix_match == "exact"),
        fix_partial_rate=_rate(results, lambda r: r.fix_match == "partial"),
        fix_none_rate=_rate(results, lambda r: r.fix_match == "none"),
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
    )


def _pct(value: float) -> str:
    return f"{value:.1%}"


def to_markdown(summary: Summary) -> str:
    s = summary
    lines = [
        f"# Evaluation: {s.predictor}" + (f" ({s.mode} mode)" if s.mode != "full" else ""),
        "",
        f"{s.cases} cases" + (f", {s.errors} with no diagnosis (the predictor raised)" if s.errors else "") + ".",
        "",
        "| metric | value |",
        "|---|---|",
        f"| category accuracy | {s.correct}/{s.cases} ({_pct(s.accuracy)}) |",
        f"| macro-F1 | {s.macro_f1:.3f} |",
        f"| failing step correct | {_pct(s.step_rate)} |",
        f"| localisation (suspect file) | {_pct(s.localisation_rate)} |",
        f"| grounding (cases, all quotes grounded) | {_pct(s.grounding_rate)} |",
        f"| grounding (quotes) | {_pct(s.quote_grounding_rate)} of {s.quotes} |",
        f"| fix match exact / partial / none | {_pct(s.fix_exact_rate)} / {_pct(s.fix_partial_rate)} / "
        f"{_pct(s.fix_none_rate)} |",
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
                     f"{_pct(c.fix_exact_rate)} | {_pct(c.action_rate)} |")
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
