"""Runs the D6b tool-using agent over the lab cases, one case at a time, safe to stop and resume.

``python -m ci_triage.evaluation.agent_run --backend gemini|ollama --mode log-only|full [--resume]``

Settings come from one pre-registered config (default ``configs/agent_v1.toml``), printed at the start of
every run and verbatim in every summary.md. Like ``llm_run``:

- each case is appended to ``results/agent_v1-<backend>[-<mode>]/cases.jsonl`` as soon as it is done:
  status, every model call (model_version, tokens, latency, retries), every turn, every tool call (name,
  arguments, output size, whether it was shown), the bundle, the environment and, unless api_error, the
  grades; with Ollama, the environment holds the server version and basic hardware facts;
- ``ok`` and ``invalid_output`` cases are scored (invalid_output as wrong); api_error cases are re-run by
  ``--resume``; Gemini attempts are paced and counted against the daily cap, shared with D6a
  (``results/llm_call_ledger.json``), and the run stops cleanly when it is reached.

Grading (grader v2 by default) checks quotes against everything the agent saw: the bundle's sections and
the results of its own tool calls that were shown to it, by evidence source. Results dropped to fit the
context budget were never seen and do not count.

The pre-registered run plan (``[[run]]`` in the config) is enforced: a run starts only when every earlier
run of the same backend in the plan has all its cases scored.
"""

from __future__ import annotations

import argparse
import platform
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field

from ci_triage.agent.config import DEFAULT_AGENT_CONFIG, LoadedAgentConfig, load_agent_config
from ci_triage.agent.loop import AgentToolCall, Status, ToolAgent, TurnRecord
from ci_triage.diagnosis import EvidenceSource
from ci_triage.evaluation.grader import DEFAULT_GRADER, GRADERS, CaseResult, GraderVersion, grade_case, tool_sections
from ci_triage.evaluation.llm_run import LEDGER_FILE, SCORED, EvalCase, lab_cases, make_client
from ci_triage.evaluation.metrics import summarise, to_markdown
from ci_triage.evaluation.run import results_name
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.llm.calling import CallLedger, DailyCapReached, LLMCallRecord, ModelCaller, Pacer, scrub
from ci_triage.llm.clients import LLMClient
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import bundle_sections
from ci_triage.tools import Mode, ToolCall

PACKAGES = ("langgraph", "langchain-core", "google-genai", "ollama", "pydantic")


class AgentCaseRecord(BaseModel):
    case_id: str
    run_id: int
    status: Status
    error: str | None = None
    repaired: bool = False
    llm_calls: list[LLMCallRecord]
    turns: list[TurnRecord]
    turns_used: int
    tool_calls: list[AgentToolCall]
    bundle_tool_calls: int
    context_forced: bool
    max_turns_forced: bool
    prompt_chars: int
    max_prompt_tokens_estimate: int
    diff_included: bool
    wall_time_s: float
    environment: dict[str, Any] = Field(default_factory=dict)
    result: CaseResult | None = None  # None for api_error: not scored
    user_message: str = ""


@dataclass
class AgentRunReport:
    directory: Path
    records: list[AgentCaseRecord]
    ran: int
    skipped: int
    stopped: str | None
    remaining: list[str]

    @property
    def api_errors(self) -> list[str]:
        return [r.case_id for r in self.records if r.status == "api_error"]


def seen_sections(user_message: str, calls: Sequence[ToolCall], bundle_tool_calls: int,
                  agent_calls: Sequence[AgentToolCall]) -> dict[EvidenceSource, list[str]]:
    """Everything the agent saw, by evidence source: the bundle's sections, then the outputs of its own
    tool calls that were shown to it (``calls`` is the toolkit's log; the agent's calls follow the bundle's)."""
    own = list(calls[bundle_tool_calls:])
    if len(own) != len(agent_calls):
        raise ValueError(f"toolkit logged {len(own)} agent calls, the agent recorded {len(agent_calls)}")
    shown = [call for call, agent in zip(own, agent_calls, strict=True) if agent.shown]
    sections: dict[EvidenceSource, list[str]] = {source: [] for source in EvidenceSource}
    for source, texts in bundle_sections(user_message).items():
        sections[EvidenceSource(source)] += texts
    for source, texts in tool_sections(shown).items():
        sections[source] += texts
    return sections


def environment(client: LLMClient) -> dict[str, Any]:
    """Versions of Python and the libraries in play; for Ollama, the server version and the hardware."""
    info: dict[str, Any] = {"python": platform.python_version()}
    for package in PACKAGES:
        try:
            info[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            pass
    server_info = getattr(client, "server_info", None)
    if callable(server_info):
        info.update(server_info())
    return info


def read_agent_records(path: Path) -> list[AgentCaseRecord]:
    if not path.is_file():
        return []
    return [AgentCaseRecord.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def run_cases(agent: ToolAgent, cases: Sequence[EvalCase], directory: Path, *, resume: bool = False,
              mode: Mode | str = Mode.FULL, echo: Callable[[str], None] = print,
              grader: GraderVersion = DEFAULT_GRADER, env: dict[str, Any] | None = None) -> AgentRunReport:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "cases.jsonl"
    existing = read_agent_records(path)
    if existing and not resume:
        raise SystemExit(f"{path} already has {len(existing)} cases; pass --resume to continue it, "
                         "or move it away to start over")
    known = {case.case_id for case in cases}
    kept = [r for r in existing if r.status in SCORED and r.case_id in known]  # api_error cases run again
    other = sorted({r.result.grader for r in kept if r.result is not None} - {grader})
    if other:
        raise SystemExit(f"{path} was graded with grader {', '.join(other)}; resume it with --grader {other[0]}")
    secrets = agent.caller.secrets
    line = lambda r: scrub(r.model_dump_json(), secrets) + "\n"  # noqa: E731 - never write a key
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.writelines(line(r) for r in kept)
    done = {r.case_id for r in kept}
    records, ran, stopped = list(kept), 0, None
    for case in cases:
        if case.case_id in done:
            continue
        toolkit = case.toolkit()
        start = time.perf_counter()
        try:
            outcome = agent.run(CaseView(run_id=case.run_id), toolkit)
        except DailyCapReached as exc:
            stopped = str(exc)
            break
        elapsed = time.perf_counter() - start
        case_env = environment(agent.caller.client) if env is None else env  # after the case: the model is loaded
        result = None
        if outcome.status in SCORED:
            sections = seen_sections(outcome.user_message, toolkit.calls, outcome.bundle_tool_calls,
                                     outcome.tool_calls)
            result = grade_case(case.truth, outcome.diagnosis, toolkit.calls, outcome.error, wall_time_s=elapsed,
                                grader=grader, sections=sections)
        record = AgentCaseRecord(
            case_id=case.case_id, run_id=case.run_id, status=outcome.status, error=outcome.error,
            repaired=outcome.repaired, llm_calls=outcome.calls, turns=outcome.turns, turns_used=outcome.turns_used,
            tool_calls=outcome.tool_calls, bundle_tool_calls=outcome.bundle_tool_calls,
            context_forced=outcome.context_forced, max_turns_forced=outcome.max_turns_forced,
            prompt_chars=outcome.prompt_chars, max_prompt_tokens_estimate=outcome.max_prompt_tokens_estimate,
            diff_included=outcome.diff_included, wall_time_s=elapsed, environment=case_env, result=result,
            user_message=outcome.user_message)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line(record))
        records.append(record)
        ran += 1
        echo(f"{case.case_id}: {outcome.status}, {outcome.turns_used} turns, {len(outcome.tool_calls)} tool calls"
             + (" (repaired)" if outcome.repaired else "") + (" (context forced)" if outcome.context_forced else ""))
    finished = {r.case_id for r in records if r.status in SCORED}
    report = AgentRunReport(directory, records, ran, len(done), stopped,
                            [case.case_id for case in cases if case.case_id not in finished])
    summary = scrub(summary_markdown(agent, report, len(cases), Mode(mode)), secrets)
    (directory / "summary.md").write_text(summary, encoding="utf-8", newline="\n")
    return report


def _total(values: Sequence[int | None]) -> int:
    return sum(v for v in values if v is not None)


def summary_markdown(agent: ToolAgent, report: AgentRunReport, n_cases: int, mode: Mode) -> str:
    loaded = agent.loaded
    records = report.records
    scored = [r.result for r in records if r.status in SCORED and r.result is not None]
    calls = [call for r in records for call in r.llm_calls]
    versions = sorted({c.model_version for c in calls if c.model_version})
    latencies = [c.latency_s for c in calls if c.latency_s is not None]
    tool_calls = [t for r in records for t in r.tool_calls]
    turns = [r.turns_used for r in records]
    count = lambda status: sum(r.status == status for r in records)  # noqa: E731
    by_tool = ", ".join(f"{name} {n}" for name, n in Counter(t.name for t in tool_calls).most_common()) or "-"
    by_purpose = ", ".join(f"{p} {n}" for p, n in Counter(c.purpose for c in calls).items()) or "-"
    environments = {tuple(sorted((k, str(v)) for k, v in r.environment.items())) for r in records}
    lines = [
        f"# Agent: {agent.name} ({mode.value} mode)",
        "",
        f"{len(records)} of {n_cases} cases recorded: {count('ok')} ok ({sum(r.repaired for r in records)} after a "
        f"repair call), {count('invalid_output')} invalid_output (scored as wrong), {count('api_error')} api_error "
        "(not scored).",
    ]
    if report.api_errors:
        lines.append(f"api_error, to re-run with `--resume`: {', '.join(report.api_errors)}.")
    if report.stopped:
        lines += ["", f"**Stopped early:** {report.stopped}. Run again with `--resume`."]
    lines += [
        "",
        "## Agent",
        "",
        "| | |",
        "|---|---|",
        f"| model turns per case, mean / max | {mean(turns):.2f} / {max(turns)} |" if turns else "| model turns | - |",
        f"| turns used, distribution | {', '.join(f'{k}: {v}' for k, v in sorted(Counter(turns).items())) or '-'} |",
        f"| tool calls (all cases), per case mean | {len(tool_calls)}, {len(tool_calls) / len(records):.2f} |"
        if records else "| tool calls | - |",
        f"| tool calls by tool | {by_tool} |",
        f"| tool results not available (bad name, arguments or scope) | {sum(not t.available for t in tool_calls)} |",
        f"| answer forced by the turn limit | {sum(r.max_turns_forced for r in records)} |",
        f"| context_forced (answer early or results dropped) | {sum(r.context_forced for r in records)} |",
        f"| tool results dropped, never shown | {sum(not t.shown for t in tool_calls)} |",
        f"| prompt size estimate, max | {max((r.max_prompt_tokens_estimate for r in records), default=0)} tokens |",
        "",
        "## Model calls",
        "",
        "| | |",
        "|---|---|",
        f"| requested model | {agent.caller.client.requested_model} |",
        f"| model_version served | {', '.join(versions) or '-'} |",
        f"| calls by purpose | {by_purpose} |",
        f"| attempts (retries) | {sum(c.attempts for c in calls)} ({sum(c.retries for c in calls)}) |",
        f"| tokens in / out | {_total([c.tokens_in for c in calls])} / {_total([c.tokens_out for c in calls])} |",
        f"| thinking tokens | {_total([c.thoughts_tokens for c in calls])} |",
        f"| latency per call, mean / max | {mean(latencies):.2f} s / {max(latencies):.2f} s |"
        if latencies else "| latency | - |",
        "",
        "## Environment",
        "",
    ]
    for env in sorted(environments):
        lines.append("- " + ", ".join(f"{k} {v}" for k, v in env))
    lines.append("")
    if scored:
        lines += ["## Scores (ok and invalid_output cases)", "",
                  to_markdown(summarise(agent.name, scored, mode.value)).split("\n", 2)[2], ""]
    lines += [
        "## Settings (pre-registered)",
        "",
        f"Config `{loaded.path.name}` (sha256 `{loaded.sha256[:16]}`), prompt `{loaded.config.prompt}` (sha256 "
        f"`{loaded.prompt.sha256[:16]}`), bundle from `{loaded.config.bundle}` (sha256 `{loaded.bundle.sha256[:16]}`).",
        "",
        "```toml",
        loaded.text.rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def make_agent_caller(client: LLMClient, loaded: LoadedAgentConfig, results_root: Path,
                      daily_cap: int | None = None) -> ModelCaller:
    c = loaded.config
    pacer = ledger = None
    if client.backend == "gemini":
        pacer = Pacer(c.gemini.max_requests_per_minute)
        cap = c.gemini.daily_call_cap if daily_cap is None else daily_cap
        ledger = CallLedger(results_root / LEDGER_FILE, client.requested_model, cap)
    elif daily_cap is not None:
        ledger = CallLedger(results_root / LEDGER_FILE, client.requested_model, daily_cap)
    return ModelCaller(client, temperature=c.generation.temperature, max_output_tokens=c.generation.max_output_tokens,
                       retry=c.retry, context_tokens=c.context.context_tokens, pacer=pacer, ledger=ledger)


def run_directory(loaded: LoadedAgentConfig, backend: str, mode: Mode | str, out: Path = RESULTS_DIR) -> Path:
    return Path(out) / results_name(f"{loaded.config.name}-{backend}", mode)


def check_run_plan(loaded: LoadedAgentConfig, backend: str, mode: Mode | str, out: Path, n_cases: int) -> None:
    """Refuse a run the pre-registered plan does not allow yet: an earlier run of the same backend is not done."""
    plan = [(r.backend, r.mode) for r in loaded.config.run]
    if (backend, Mode(mode).value) not in plan:
        raise SystemExit(f"{backend} {Mode(mode).value} is not in the run plan of {loaded.path.name}")
    for earlier in plan[:plan.index((backend, Mode(mode).value))]:
        if earlier[0] != backend:
            continue
        records = read_agent_records(run_directory(loaded, *earlier, out) / "cases.jsonl")
        scored = sum(r.status in SCORED for r in records)
        if scored < n_cases:
            raise SystemExit(f"run plan: finish {earlier[0]} {earlier[1]} first ({scored}/{n_cases} cases scored)")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ci_triage.evaluation.agent_run",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--backend", choices=["gemini", "ollama"], required=True)
    parser.add_argument("--mode", choices=[m.value for m in Mode], required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_AGENT_CONFIG)
    parser.add_argument("--resume", action="store_true", help="skip cases already done; re-run api_error ones")
    parser.add_argument("--daily-cap", type=int, default=None, help="attempts per day (default: from the config)")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="results root (default: results/)")
    parser.add_argument("--grader", choices=GRADERS, default=DEFAULT_GRADER)
    args = parser.parse_args(argv)
    loaded = load_agent_config(args.config)
    mode = Mode(args.mode)
    cases = lab_cases(mode)
    check_run_plan(loaded, args.backend, mode, args.out, len(cases))
    print(f"config {loaded.path} (sha256 {loaded.sha256[:16]}), prompt sha256 {loaded.prompt.sha256[:16]}, "
          f"bundle config sha256 {loaded.bundle.sha256[:16]}")
    print(loaded.text)
    agent = ToolAgent(make_agent_caller(make_client(args.backend, loaded), loaded, args.out, args.daily_cap), loaded)
    report = run_cases(agent, cases, run_directory(loaded, args.backend, mode, args.out), resume=args.resume,
                       mode=mode, grader=args.grader)
    print(f"{report.ran} cases run, {report.skipped} skipped as done; wrote {report.directory}")
    if report.api_errors:
        print(f"api_error, re-run with --resume: {', '.join(report.api_errors)}")
    if report.stopped:
        print(f"stopped: {report.stopped}; {len(report.remaining)} cases left, run again with --resume")


if __name__ == "__main__":
    main()
