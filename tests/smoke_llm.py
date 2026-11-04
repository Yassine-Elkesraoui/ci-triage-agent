"""Live smoke test of the one-call baseline, by hand: the synthetic case (tests/synthetic.py), one model
call per backend. Not a lab case, not collected by pytest. Needs ``pip install -e ".[llm]"``, Ollama
running with the model pulled, and GEMINI_API_KEY set.

    python tests/smoke_llm.py [gemini] [ollama] [--mode log-only]
"""

from __future__ import annotations

import argparse

import synthetic as s

from ci_triage.evaluation.grader import grade_case
from ci_triage.evaluation.llm_run import make_caller, make_client
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.llm.config import load_config
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import LLMOncePredictor
from ci_triage.tools import Mode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backends", nargs="*", default=["gemini", "ollama"])
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.FULL.value)
    args = parser.parse_args()
    loaded = load_config()
    print(f"config {loaded.path.name} sha256 {loaded.sha256[:16]}, prompt sha256 {loaded.prompt.sha256[:16]}, "
          f"mode {args.mode}")
    for backend in args.backends:
        predictor = LLMOncePredictor(make_caller(make_client(backend, loaded), loaded, RESULTS_DIR), loaded)
        toolkit, _ = s.toolkit(args.mode)
        outcome = predictor.run(CaseView(s.RUN_ID), toolkit)
        print(f"\n== {backend}: status {outcome.status}" + (" (repaired)" if outcome.repaired else ""))
        print(f"prompt: {outcome.prompt_chars} chars, ~{outcome.prompt_tokens_estimate} tokens estimated")
        for call in outcome.calls:
            print(f"call {call.purpose}: requested {call.requested_model}, served {call.model_version}, "
                  f"tokens in {call.tokens_in} out {call.tokens_out} thinking {call.thoughts_tokens}, "
                  f"latency {call.latency_s and round(call.latency_s, 2)} s, attempts {call.attempts}, "
                  f"retries {call.retries}, finish {call.finish_reason}, context overflow {call.context_overflow}"
                  + (f", errors {call.attempt_errors}" if call.attempt_errors else ""))
        if outcome.diagnosis is not None:
            grade = grade_case(s.truth(), outcome.diagnosis, toolkit.calls)
            print(f"grade: category {grade.category_correct}, job {grade.job_correct}, step {grade.step_correct}, "
                  f"file {grade.file_correct}, quotes grounded {grade.quotes_grounded}/{grade.quotes}, "
                  f"fix {grade.fix_match}, action {grade.action_correct}")
            print(outcome.diagnosis.model_dump_json(indent=1))
        else:
            print(f"error: {outcome.error}")
            for call in outcome.calls:
                print(f"raw output: {(call.output or '')[:600]!r}")


if __name__ == "__main__":
    main()
