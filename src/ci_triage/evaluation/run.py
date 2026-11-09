"""Runs a predictor over every lab case and grades it.

``python -m ci_triage.evaluation.run --predictor NAME`` writes results/NAME/cases.jsonl
(one graded case per line: the diagnosis, the grades, the tool call log and the wall
time) and results/NAME/summary.md; with ``--mode log-only``, results/NAME-log-only/.
NAME is a predictor registered in ``ci_triage.predictors``, or ``module:attribute``
naming a predictor class or instance importable from the current path.

The predictor sees a ``CaseView`` (the run id) and a fresh ``Toolkit`` per case, built for
the evaluation mode (``--mode full|log-only``, see ``ci_triage.tools.modes``); ground truth
is loaded here and only reaches the grader. Each case's wall time is recorded. Cases are graded with
grader v2 (default); ``--grader v1`` reproduces the scores of runs graded before v2.
"""

from __future__ import annotations

import argparse
import importlib
import time
from collections.abc import Sequence
from pathlib import Path

from ci_triage.diagnosis import Diagnosis
from ci_triage.evaluation.grader import DEFAULT_GRADER, GRADERS, CaseResult, GraderVersion, grade_case
from ci_triage.evaluation.ground_truth import load_ground_truth
from ci_triage.evaluation.metrics import Summary, summarise, to_markdown
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.predictors import PREDICTORS, CaseView, Predictor
from ci_triage.replay import LabData, ReplaySource, load_cases, load_lab
from ci_triage.tools import Mode, make_toolkit


def evaluate(predictor: Predictor, lab: LabData | None = None, mode: Mode | str = Mode.FULL,
             grader: GraderVersion = DEFAULT_GRADER) -> list[CaseResult]:
    lab = lab or load_lab()
    truth = load_ground_truth(lab.root)
    results = []
    for case in load_cases(lab):
        toolkit = make_toolkit(ReplaySource(case, lab), case.eval_commit, mode)
        diagnosis, error = None, None
        start = time.perf_counter()
        try:
            diagnosis = Diagnosis.model_validate(predictor.predict(CaseView(run_id=case.eval_run_id), toolkit))
        except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - start
        results.append(grade_case(truth[case.case_id], diagnosis, toolkit.calls, error, wall_time_s=elapsed,
                                  grader=grader))
    return results


def results_name(name: str, mode: Mode | str = Mode.FULL) -> str:
    """The results directory's name: the predictor's, plus the mode unless it is full."""
    return name if Mode(mode) is Mode.FULL else f"{name}-{Mode(mode).value}"


def parse_results_name(directory_name: str) -> tuple[str, Mode]:
    """(predictor name, mode) of a results directory named by ``results_name``."""
    for mode in Mode:
        if mode is not Mode.FULL and directory_name.endswith(f"-{mode.value}"):
            return directory_name.removesuffix(f"-{mode.value}"), mode
    return directory_name, Mode.FULL


def write_results(name: str, results: Sequence[CaseResult], out_dir: Path = RESULTS_DIR,
                  mode: Mode | str = Mode.FULL) -> tuple[Path, Summary]:
    """results/<name>[-<mode>]/cases.jsonl and summary.md; returns the directory and the summary."""
    directory = Path(out_dir) / results_name(name, mode)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for result in results:
            f.write(result.model_dump_json(exclude_none=True) + "\n")
    summary = summarise(name, results, Mode(mode).value)
    (directory / "summary.md").write_text(to_markdown(summary), encoding="utf-8", newline="\n")
    return directory, summary


def load_predictor(spec: str) -> Predictor:
    if spec in PREDICTORS:
        return PREDICTORS[spec]()
    module, sep, attribute = spec.partition(":")
    if not sep:
        raise SystemExit(f"unknown predictor {spec!r}; known: {', '.join(PREDICTORS)}, or module:attribute")
    found = getattr(importlib.import_module(module), attribute)
    return found() if isinstance(found, type) else found


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ci_triage.evaluation.run", description=__doc__.split("\n")[0])
    parser.add_argument("--predictor", required=True, help=f"{', '.join(PREDICTORS)}, or module:attribute")
    parser.add_argument("--mode", choices=[mode.value for mode in Mode], default=Mode.FULL.value,
                        help="full (default): every tool; log-only: no diffs, files only at the eval commit")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="results root (default: results/)")
    parser.add_argument("--grader", choices=GRADERS, default=DEFAULT_GRADER,
                        help=f"grader version (default {DEFAULT_GRADER}); v1 reproduces runs graded before v2")
    args = parser.parse_args(argv)
    predictor = load_predictor(args.predictor)
    mode = Mode(args.mode)
    directory, summary = write_results(predictor.name, evaluate(predictor, mode=mode, grader=args.grader), args.out,
                                       mode)
    label = predictor.name if mode is Mode.FULL else f"{predictor.name} ({mode.value})"
    print(f"{label}: {summary.correct}/{summary.cases} correct, macro-F1 {summary.macro_f1:.3f}; "
          f"wrote {directory}")


if __name__ == "__main__":
    main()
