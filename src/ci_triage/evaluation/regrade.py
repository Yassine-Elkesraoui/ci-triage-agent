"""Re-grades saved runs, with no predictor or model call: ``python -m ci_triage.evaluation.regrade [DIR ...]``.

DIR is a results directory (``results/<name>[-<mode>]``, from ``evaluation.run`` or ``evaluation.llm_run``);
with none, every directory under results/ that holds a cases.jsonl. For each saved case, the recorded tool
calls are replayed offline on a fresh toolkit for the run's mode (the replay source is deterministic, so
the outputs are the ones the predictor got; a replayed call whose size or availability differs is an
error), and the saved diagnosis is graded again with grader v1, v2 and v2.1. One-call LLM cases are graded
against the sections of their saved bundle. The grader a run was saved with (v1 for every run before
grader v2) must reproduce its saved grades exactly.

Writes ``DIR/grader-v2/cases.jsonl`` and ``summary.md``; the saved files are never touched. Only scored
cases are re-graded (for LLM runs: ``ok`` and ``invalid_output``). A run saved by an older grader, whose
records lack fields grader v1 now writes (``job_correct`` came with D5c), cannot be reproduced and is
skipped with a message (``StaleRun``).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ci_triage.evaluation.grader import CaseResult, grade_case
from ci_triage.evaluation.ground_truth import GroundTruth, load_ground_truth
from ci_triage.evaluation.llm_run import SCORED, LLMCaseRecord
from ci_triage.evaluation.metrics import Summary, summarise, to_markdown
from ci_triage.evaluation.run import parse_results_name
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.predictors.llm_once import bundle_sections
from ci_triage.replay import Case, LabData, ReplaySource, load_cases, load_lab
from ci_triage.tools import Mode, ToolCall, make_toolkit

OUT_DIR = "grader-v2"


class StaleRun(ValueError):
    """The run was saved by an older grader; its records do not parse as today's results."""


@dataclass(frozen=True)
class Regraded:
    directory: Path
    name: str  # the predictor's
    mode: Mode
    stored: list[CaseResult]  # as saved
    v1: list[CaseResult]
    v2: list[CaseResult]
    v2_1: list[CaseResult]

    @property
    def saved_grader(self) -> str:
        """The grader the run was saved with (one per run)."""
        graders = {r.grader for r in self.stored}
        if len(graders) != 1:
            raise ValueError(f"{self.directory.name}: saved cases graded with {sorted(graders) or 'nothing'}")
        return graders.pop()

    @property
    def regraded_as_saved(self) -> list[CaseResult]:
        return {"v1": self.v1, "v2": self.v2, "v2.1": self.v2_1}[self.saved_grader]

    @property
    def reproduces(self) -> bool:
        """The saved grader, run again, gives the saved grades exactly."""
        return [r.model_dump() for r in self.stored] == [r.model_dump() for r in self.regraded_as_saved]

    def summaries(self) -> tuple[Summary, Summary]:
        return summarise(self.name, self.v1, self.mode.value), summarise(self.name, self.v2, self.mode.value)


def saved_runs(root: Path = RESULTS_DIR) -> list[Path]:
    return sorted(path.parent for path in Path(root).glob("*/cases.jsonl"))


def replay_calls(case: Case, lab: LabData, mode: Mode, recorded: Sequence[ToolCall]) -> list[ToolCall]:
    """The recorded calls run again on a fresh toolkit, with their outputs (which saved files leave out)."""
    toolkit = make_toolkit(ReplaySource(case, lab), case.eval_commit, mode)
    for call in recorded:
        toolkit.call(call.tool, call.arguments)
    for old, new in zip(recorded, toolkit.calls, strict=True):
        if (old.output_chars, old.available) != (new.output_chars, new.available):
            raise ValueError(f"{case.case_id}: replayed {new.tool} {new.arguments} differs from the saved call")
    return toolkit.calls


def _saved_cases(directory: Path) -> list[tuple[CaseResult, str | None]]:
    """(saved result, the bundle as sent for one-call LLM cases, else None), scored cases only."""
    cases = []
    for line in (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        try:
            if "llm_calls" in data:
                record = LLMCaseRecord.model_validate(data)
                if record.status in SCORED and record.result is not None:
                    cases.append((record.result, record.user_message))
            else:
                cases.append((CaseResult.model_validate(data), None))
        except ValidationError as exc:
            missing = sorted({".".join(map(str, e["loc"])) for e in exc.errors() if e["type"] == "missing"})
            raise StaleRun(f"{directory.name} was saved by an older grader (missing: {', '.join(missing) or '?'}); "
                           "re-run it to grade it") from exc
    return cases


def regrade_run(directory: Path, lab: LabData | None = None,
                truth: dict[str, GroundTruth] | None = None) -> Regraded:
    lab = lab or load_lab()
    truth = truth or load_ground_truth(lab.root)
    directory = Path(directory)
    name, mode = parse_results_name(directory.name)
    lab_cases = {case.case_id: case for case in load_cases(lab)}
    stored, v1, v2, v2_1 = [], [], [], []
    for saved, message in _saved_cases(directory):
        calls = replay_calls(lab_cases[saved.case_id], lab, mode, saved.calls)
        args = (truth[saved.case_id], saved.diagnosis, calls, saved.error)
        sections = None if message is None else bundle_sections(message)
        stored.append(saved)
        v1.append(grade_case(*args, wall_time_s=saved.wall_time_s, grader="v1"))
        v2.append(grade_case(*args, wall_time_s=saved.wall_time_s, grader="v2", sections=sections))
        v2_1.append(grade_case(*args, wall_time_s=saved.wall_time_s, grader="v2.1", sections=sections))
    return Regraded(directory, name, mode, stored, v1, v2, v2_1)


def write_v2(run: Regraded) -> Path:
    out = run.directory / OUT_DIR
    out.mkdir(exist_ok=True)
    with (out / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        f.writelines(result.model_dump_json(exclude_none=True) + "\n" for result in run.v2)
    (out / "summary.md").write_text(to_markdown(run.summaries()[1]), encoding="utf-8", newline="\n")
    return out


def _row(run: Regraded) -> str:
    s1, s2 = run.summaries()
    q1 = sum(r.quotes_grounded for r in run.v1)
    q2 = sum(r.quotes_grounded for r in run.v2)
    exact1 = sum(r.fix_match == "exact" for r in run.v1)
    exact2 = sum(r.fix_match == "exact" for r in run.v2)
    return (f"{run.directory.name}: grounded cases {round(s1.grounding_rate * s1.cases)} -> "
            f"{round(s2.grounding_rate * s2.cases)}/{s2.cases}, quotes {q1} -> {q2}/{s2.quotes}, "
            f"quote rules followed {s2.quote_rules_followed}/{s2.quotes}, fix exact {exact1}/{s1.fix_cases} -> "
            f"{exact2}/{s2.fix_cases} restore-type ({s2.needs_judge} need a judge); "
            f"saved grader {run.saved_grader} reproduces the saved grades: {'yes' if run.reproduces else 'NO'}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ci_triage.evaluation.regrade", description=__doc__.split("\n")[0])
    parser.add_argument("directories", nargs="*", type=Path, help="results directories (default: all under results/)")
    args = parser.parse_args(argv)
    lab = load_lab()
    truth = load_ground_truth(lab.root)
    failed = []
    for directory in args.directories or saved_runs():
        try:
            run = regrade_run(directory, lab, truth)
        except StaleRun as exc:
            print(f"{directory.name}: skipped, {exc}")
            continue
        write_v2(run)
        print(_row(run))
        if not run.reproduces:
            failed.append(directory.name)
    if failed:
        raise SystemExit(f"the saved grader does not reproduce the saved grades of: {', '.join(failed)}")


if __name__ == "__main__":
    main()
