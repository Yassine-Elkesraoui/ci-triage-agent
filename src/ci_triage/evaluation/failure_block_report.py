"""How much key evidence extract_failure_block keeps, and how small its output is.

Run ``python -m ci_triage.evaluation.failure_block_report`` for the tables.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ci_triage.evaluation.ground_truth import GroundTruth, load_ground_truth
from ci_triage.evaluation.key_evidence import key_evidence
from ci_triage.replay import LabData, ReplaySource, load_cases, load_lab
from ci_triage.tools import Toolkit, render

CATEGORIES = ("real_regression", "flaky", "dependency", "environment", "timeout", "build")


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    category: str
    by_test_name: bool  # key evidence is the failing test ids, else the decisive error line(s)
    needles: tuple[str, ...]
    missing: tuple[str, ...]
    block_chars: int  # rendered extract_failure_block output
    log_chars: int  # the raw log as the source serves it

    @property
    def covered(self) -> bool:
        return not self.missing


def score_case(source: ReplaySource, truth: GroundTruth, max_chars: int | None = None) -> CaseScore:
    toolkit = Toolkit(source) if max_chars is None else Toolkit(source, max_chars)
    jobs = toolkit.call("get_jobs", {"run_id": source.target.run_id})
    job = next(job for job in jobs.jobs if job.conclusion in ("failure", "cancelled"))
    result = toolkit.call("extract_failure_block", {"job_id": job.id})
    text = "\n".join(block.text for block in result.blocks)
    needles = key_evidence(truth)
    return CaseScore(
        case_id=truth.case_id,
        category=truth.category,
        by_test_name=bool(truth.expected_failing_tests),
        needles=needles,
        missing=tuple(needle for needle in needles if needle not in text),
        block_chars=len(render(result)),
        log_chars=len(source.get_job_log(job.id)),
    )


def score_all(lab: LabData | None = None, max_chars: int | None = None) -> list[CaseScore]:
    lab = lab or load_lab()
    truth = load_ground_truth(lab.root)
    return [score_case(ReplaySource(case, lab), truth[case.case_id], max_chars) for case in load_cases(lab)]


def _fraction(hits: int, total: int) -> str:
    return f"{hits}/{total}" if total else "-"


def coverage_table(scores: list[CaseScore]) -> str:
    by_category: dict[str, list[CaseScore]] = defaultdict(list)
    for score in scores:
        by_category[score.category].append(score)
    rows = ["| category | cases | failing test named | decisive line present | covered |",
            "|---|---|---|---|---|"]
    for category in [*CATEGORIES, "all"]:
        group = scores if category == "all" else by_category[category]
        tests = [s for s in group if s.by_test_name]
        lines = [s for s in group if not s.by_test_name]
        covered = sum(s.covered for s in group)
        rows.append(f"| {category} | {len(group)} | {_fraction(sum(s.covered for s in tests), len(tests))} | "
                    f"{_fraction(sum(s.covered for s in lines), len(lines))} | {covered / len(group):.0%} |")
    return "\n".join(rows)


def size_table(scores: list[CaseScore]) -> str:
    by_category: dict[str, list[CaseScore]] = defaultdict(list)
    for score in scores:
        by_category[score.category].append(score)
    rows = ["| category | avg block chars | max block chars | avg raw log chars | block / log |",
            "|---|---|---|---|---|"]
    for category in [*CATEGORIES, "all"]:
        group = scores if category == "all" else by_category[category]
        block = sum(s.block_chars for s in group) / len(group)
        log = sum(s.log_chars for s in group) / len(group)
        rows.append(f"| {category} | {block:,.0f} | {max(s.block_chars for s in group):,} | {log:,.0f} | "
                    f"{block / log:.1%} |")
    return "\n".join(rows)


def main() -> None:
    scores = score_all()
    print(coverage_table(scores))
    print()
    print(size_table(scores))
    for score in scores:
        if score.missing:
            print(f"MISSING {score.case_id} ({score.category}): {score.missing}")


if __name__ == "__main__":
    main()
