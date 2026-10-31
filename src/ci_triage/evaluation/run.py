"""Runs a predictor over every lab case and grades it.

``python -m ci_triage.evaluation.run --predictor NAME`` writes results/NAME/cases.jsonl
(one graded case per line: the diagnosis, the grades and the tool call log) and
results/NAME/summary.md. NAME is a predictor registered in ``ci_triage.predictors``,
or ``module:attribute`` naming a predictor class or instance importable from the
current path.

The predictor sees a ``CaseView`` (the run id) and a fresh ``Toolkit`` per case;
ground truth is loaded here and only reaches the grader.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence
from pathlib import Path

from ci_triage.diagnosis import Diagnosis
from ci_triage.evaluation.grader import CaseResult, grade_case
from ci_triage.evaluation.ground_truth import load_ground_truth
from ci_triage.evaluation.metrics import Summary, summarise, to_markdown
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.predictors import PREDICTORS, CaseView, Predictor
from ci_triage.replay import LabData, ReplaySource, load_cases, load_lab
from ci_triage.tools import Toolkit


def evaluate(predictor: Predictor, lab: LabData | None = None) -> list[CaseResult]:
    lab = lab or load_lab()
    truth = load_ground_truth(lab.root)
    results = []
    for case in load_cases(lab):
        toolkit = Toolkit(ReplaySource(case, lab))
        diagnosis, error = None, None
        try:
            diagnosis = Diagnosis.model_validate(predictor.predict(CaseView(run_id=case.eval_run_id), toolkit))
        except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
            error = f"{type(exc).__name__}: {exc}"
        results.append(grade_case(truth[case.case_id], diagnosis, toolkit.calls, error))
    return results


def write_results(name: str, results: Sequence[CaseResult], out_dir: Path = RESULTS_DIR) -> tuple[Path, Summary]:
    """results/<name>/cases.jsonl and summary.md; returns the directory and the summary."""
    directory = Path(out_dir) / name
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for result in results:
            f.write(result.model_dump_json(exclude_none=True) + "\n")
    summary = summarise(name, results)
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
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="results root (default: results/)")
    args = parser.parse_args(argv)
    predictor = load_predictor(args.predictor)
    directory, summary = write_results(predictor.name, evaluate(predictor), args.out)
    print(f"{predictor.name}: {summary.correct}/{summary.cases} correct, macro-F1 {summary.macro_f1:.3f}; "
          f"wrote {directory}")


if __name__ == "__main__":
    main()
