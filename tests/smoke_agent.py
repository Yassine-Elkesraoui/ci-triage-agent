"""Live smoke test of the D6b agent, by hand: the synthetic case (tests/synthetic.py), not a lab case; not
collected by pytest. Needs ``pip install -e ".[llm,agent]"``, Ollama running with the model pulled, and
GEMINI_API_KEY set. Gemini attempts count against the daily cap in results/llm_call_ledger.json.

    python tests/smoke_agent.py [gemini] [ollama] [--mode log-only]
"""

from __future__ import annotations

import argparse
import json
import time

import synthetic as s

from ci_triage.agent.config import load_agent_config
from ci_triage.agent.loop import ToolAgent
from ci_triage.evaluation.agent_run import environment, make_agent_caller, seen_sections
from ci_triage.evaluation.grader import grade_case
from ci_triage.evaluation.llm_run import make_client
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.predictors.base import CaseView
from ci_triage.tools import Mode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("backends", nargs="*", default=["gemini", "ollama"])
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.LOG_ONLY.value)
    args = parser.parse_args()
    loaded = load_agent_config()
    print(f"config {loaded.path.name} sha256 {loaded.sha256[:16]}, prompt sha256 {loaded.prompt.sha256[:16]}, "
          f"bundle config {loaded.config.bundle} sha256 {loaded.bundle.sha256[:16]}, mode {args.mode}, "
          f"synthetic case (run {s.RUN_ID}, not a lab case)")
    for backend in args.backends:
        client = make_client(backend, loaded)
        agent = ToolAgent(make_agent_caller(client, loaded, RESULTS_DIR), loaded)
        toolkit, _ = s.toolkit(args.mode)
        start = time.perf_counter()
        outcome = agent.run(CaseView(s.RUN_ID), toolkit)
        wall = time.perf_counter() - start
        print(f"\n== {backend}: status {outcome.status}" + (" (repaired)" if outcome.repaired else "")
              + f", {outcome.turns_used} turns, {len(outcome.tool_calls)} tool calls, wall {wall:.1f} s"
              + (", context forced" if outcome.context_forced else "") + (", turn limit" if outcome.max_turns_forced else ""))
        print(f"bundle: {outcome.prompt_chars} chars with the system prompt, {outcome.bundle_tool_calls} toolkit calls")
        for turn in outcome.turns:
            print(f"turn {turn.turn}: tools offered {turn.tools_offered}, forced {turn.forced}, "
                  f"~{turn.prompt_tokens_estimate} prompt tokens estimated, {turn.tool_calls} tool calls")
            for call in (c for c in outcome.tool_calls if c.turn == turn.turn):
                print(f"   -> {call.name}({json.dumps(call.arguments)}) -> {call.output_chars} chars, "
                      f"available {call.available}, shown {call.shown}")
        for call in outcome.calls:
            print(f"call {call.purpose}: served {call.model_version}, tokens in {call.tokens_in} out {call.tokens_out}"
                  f" thinking {call.thoughts_tokens}, latency {call.latency_s and round(call.latency_s, 2)} s, "
                  f"attempts {call.attempts}, finish {call.finish_reason}, tools offered {call.tools_offered}, "
                  f"called {call.tool_calls}" + (f", errors {call.attempt_errors}" if call.attempt_errors else ""))
        tokens_in = sum(c.tokens_in or 0 for c in outcome.calls)
        tokens_out = sum(c.tokens_out or 0 for c in outcome.calls)
        latency = sum(c.latency_s or 0 for c in outcome.calls)
        print(f"total: tokens in {tokens_in} out {tokens_out}, model latency {latency:.1f} s")
        print(f"environment: {environment(client)}")
        if outcome.diagnosis is not None:
            sections = seen_sections(outcome.user_message, toolkit.calls, outcome.bundle_tool_calls,
                                     outcome.tool_calls)
            grade = grade_case(s.truth(), outcome.diagnosis, toolkit.calls, sections=sections)
            print(f"grade (v2): category {grade.category_correct}, job {grade.job_correct}, step {grade.step_correct}, "
                  f"file {grade.file_correct}, quotes grounded {grade.quotes_grounded}/{grade.quotes}, "
                  f"quote rules followed {grade.quote_rules.followed}/{grade.quotes}, fix {grade.fix_match}")
            print(outcome.diagnosis.model_dump_json(indent=1))
        else:
            print(f"error: {outcome.error}")
            for call in outcome.calls:
                print(f"raw output: {(call.output or '')[:600]!r}")


if __name__ == "__main__":
    main()
