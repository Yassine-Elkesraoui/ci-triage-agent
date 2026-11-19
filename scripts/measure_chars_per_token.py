"""Characters per token on tool JSON, per backend, from the saved agent v1 runs on the v1.0 cases (phase B,
change e). Reads results/agent_v1-<backend>[-log-only]/cases.jsonl only; calls no model and runs no tool.

For each pair of consecutive model calls in a case that both offered the same tools (turns 1 to 7), the
server-counted prompt grew by the new messages between them: the assistant turn (its text and the
arguments of its tool calls) and one tool message per call, whose content is the tool's JSON result. The
characters are counted exactly as the agent's context estimate counts them (``loop.ToolAgent._estimate``),
so the ratio is the one the estimate needs:

    chars_per_token = (characters added) / (server prompt tokens added)

Per backend, it prints the ratio pooled over each case's growth, its minimum and percentiles, and the
per-step distribution, and takes the low (conservative) end: the minimum per-case pooled ratio, rounded
down to 0.05. The server counts: Gemini's ``prompt_token_count``; Ollama's ``prompt_eval_count``, which on
this server version is the full prompt (reports/d6b-agent-v1.md, "Ollama token counts").

    python scripts/measure_chars_per_token.py
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"
BACKENDS = ("gemini", "ollama")
MODES = ("log-only", "full")


def run_dir(backend: str, mode: str) -> Path:
    return RESULTS / (f"agent_v1-{backend}" + ("-log-only" if mode == "log-only" else ""))


def step_chars(text: str, calls: list[dict]) -> int:
    """Characters the estimate adds for one assistant turn with tool calls and its tool messages."""
    assistant = len(text) + len(json.dumps([c["arguments"] for c in calls], default=str))
    tools = sum(c["output_chars"] + len(json.dumps([])) for c in calls)
    return assistant + tools


def samples(backend: str) -> tuple[list[tuple[int, int]], dict[str, tuple[int, int]]]:
    """(chars added, tokens added) per step, and per case (run, case id) the totals."""
    steps: list[tuple[int, int]] = []
    cases: dict[str, tuple[int, int]] = {}
    for mode in MODES:
        path = run_dir(backend, mode) / "cases.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["status"] == "api_error" or record["context_forced"]:
                continue
            calls = record["llm_calls"]
            total_chars = total_tokens = 0
            for n in range(len(calls) - 1):
                now, nxt = calls[n], calls[n + 1]
                if not (now["purpose"] == nxt["purpose"] == "turn" and now["tools_offered"] == nxt["tools_offered"]):
                    continue
                if now["tokens_in"] is None or nxt["tokens_in"] is None or not now["tool_calls"]:
                    continue
                turn = n + 1
                made = [c for c in record["tool_calls"] if c["turn"] == turn]
                chars = step_chars(now["output"] or "", made)
                tokens = nxt["tokens_in"] - now["tokens_in"]
                steps.append((chars, tokens))
                total_chars += chars
                total_tokens += tokens
            if total_tokens > 0:
                cases[f"{mode} {record['case_id']}"] = (total_chars, total_tokens)
    return steps, cases


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def main() -> None:
    for backend in BACKENDS:
        steps, cases = samples(backend)
        per_step = [c / t for c, t in steps if t > 0]
        per_case = {k: c / t for k, (c, t) in cases.items()}
        pooled = sum(c for c, _ in steps) / sum(t for _, t in steps)
        low_case = min(per_case, key=per_case.get)
        chosen = math.floor(per_case[low_case] * 20) / 20
        big = [c / t for c, t in steps if c >= 1000]
        print(f"== {backend}: {len(steps)} steps in {len(cases)} cases (both modes)")
        print(f"   pooled over all steps: {sum(c for c, _ in steps):,} chars / {sum(t for _, t in steps):,} tokens"
              f" = {pooled:.3f}")
        print(f"   per case (pooled growth): min {per_case[low_case]:.3f} ({low_case}), "
              f"p5 {percentile(list(per_case.values()), 0.05):.3f}, median {statistics.median(per_case.values()):.3f}, "
              f"max {max(per_case.values()):.3f}")
        print(f"   per step: min {min(per_step):.3f}, p5 {percentile(per_step, 0.05):.3f}, "
              f"median {statistics.median(per_step):.3f}; steps adding >= 1000 chars: n {len(big)}, "
              f"min {min(big):.3f}, p5 {percentile(big, 0.05):.3f}, median {statistics.median(big):.3f}")
        print(f"   chosen (minimum per-case ratio, rounded down to 0.05): {chosen:.2f}")


if __name__ == "__main__":
    main()
