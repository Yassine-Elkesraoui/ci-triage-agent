"""Runs an LLM baseline over the lab cases, one case at a time, safe to stop and resume.

``python -m ci_triage.evaluation.llm_run --backend gemini|ollama [--mode log-only] [--resume] [--grader v1|v2]``

Settings come from one pre-registered config file (default ``configs/one_call_v2.toml``), printed at
the start of every run and verbatim in every summary.md.

- Each case is appended to ``results/<name>[-<mode>]/cases.jsonl`` as soon as it is done, with its
  status, the per-call records (requested model, model_version served, tokens in and out, latency,
  retries) and, unless the status is ``api_error``, its grades.
- ``ok`` and ``invalid_output`` cases are scored; ``invalid_output`` counts as wrong. ``api_error`` cases
  (a call that still failed after its retries) are excluded from scoring, listed in summary.md, and
  re-run by ``--resume``, which skips every other case already in cases.jsonl.
- Gemini attempts are paced to ``max_requests_per_minute`` and counted against a daily cap
  (``results/llm_call_ledger.json``); when the cap is reached the run stops cleanly before the next
  attempt, keeps what is done, and tells you to ``--resume`` later.
- Cases are graded with grader v2 (default), against the bundle sections the model was shown;
  ``--grader v1`` reproduces the scores of runs graded before v2.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from ci_triage.evaluation.grader import DEFAULT_GRADER, GRADERS, CaseResult, GraderVersion, grade_case
from ci_triage.evaluation.ground_truth import GroundTruth, load_ground_truth
from ci_triage.evaluation.metrics import summarise, to_markdown
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.llm.calling import CallLedger, DailyCapReached, LLMCallRecord, ModelCaller, Pacer, scrub
from ci_triage.llm.clients import GeminiClient, LLMClient, OllamaClient
from ci_triage.llm.config import DEFAULT_CONFIG, LoadedConfig, load_config
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.llm_once import LLMOncePredictor, Status, bundle_sections
from ci_triage.tools import Mode, Toolkit, make_toolkit

LEDGER_FILE = "llm_call_ledger.json"
SCORED: tuple[Status, ...] = ("ok", "invalid_output")


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    run_id: int
    truth: GroundTruth
    toolkit: Callable[[], Toolkit]  # a fresh toolkit for this case, built for the mode


class LLMCaseRecord(BaseModel):
    case_id: str
    run_id: int
    status: Status
    error: str | None = None
    repaired: bool = False
    llm_calls: list[LLMCallRecord]
    prompt_chars: int
    prompt_tokens_estimate: int
    diff_included: bool
    wall_time_s: float
    result: CaseResult | None = None  # None for api_error: not scored
    user_message: str = ""


@dataclass
class RunReport:
    directory: Path
    records: list[LLMCaseRecord]
    ran: int  # cases run in this invocation
    skipped: int  # cases already done (--resume)
    stopped: str | None  # why the run stopped early, if it did
    remaining: list[str]  # case ids not done yet (api_error ones included)

    @property
    def api_errors(self) -> list[str]:
        return [r.case_id for r in self.records if r.status == "api_error"]


def lab_cases(mode: Mode | str) -> list[EvalCase]:
    from ci_triage.replay import ReplaySource, load_cases, load_lab

    lab = load_lab()
    truth = load_ground_truth(lab.root)
    return [EvalCase(case.case_id, case.eval_run_id, truth[case.case_id],
                     lambda case=case: make_toolkit(ReplaySource(case, lab), case.eval_commit, mode))
            for case in load_cases(lab)]


def read_records(path: Path) -> list[LLMCaseRecord]:
    if not path.is_file():
        return []
    return [LLMCaseRecord.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def run_cases(predictor: LLMOncePredictor, cases: Sequence[EvalCase], directory: Path, *, resume: bool = False,
              mode: Mode | str = Mode.FULL, echo: Callable[[str], None] = print,
              grader: GraderVersion = DEFAULT_GRADER) -> RunReport:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "cases.jsonl"
    existing = read_records(path)
    if existing and not resume:
        raise SystemExit(f"{path} already has {len(existing)} cases; pass --resume to continue it, "
                         "or move it away to start over")
    known = {case.case_id for case in cases}
    kept = [r for r in existing if r.status in SCORED and r.case_id in known]  # api_error cases run again
    other = sorted({r.result.grader for r in kept if r.result is not None} - {grader})
    if other:
        raise SystemExit(f"{path} was graded with grader {', '.join(other)}; resume it with --grader {other[0]}")
    secrets = predictor.caller.secrets
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
            outcome = predictor.run(CaseView(run_id=case.run_id), toolkit)
        except DailyCapReached as exc:
            stopped = str(exc)
            break
        elapsed = time.perf_counter() - start
        result = None
        if outcome.status in SCORED:
            result = grade_case(case.truth, outcome.diagnosis, toolkit.calls, outcome.error, wall_time_s=elapsed,
                                grader=grader, sections=bundle_sections(outcome.user_message))
        record = LLMCaseRecord(case_id=case.case_id, run_id=case.run_id, status=outcome.status, error=outcome.error,
                               repaired=outcome.repaired, llm_calls=outcome.calls, prompt_chars=outcome.prompt_chars,
                               prompt_tokens_estimate=outcome.prompt_tokens_estimate,
                               diff_included=outcome.diff_included, wall_time_s=elapsed, result=result,
                               user_message=outcome.user_message)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line(record))
        records.append(record)
        ran += 1
        echo(f"{case.case_id}: {outcome.status}" + (" (repaired)" if outcome.repaired else ""))
    finished = {r.case_id for r in records if r.status in SCORED}
    report = RunReport(directory, records, ran, len(done), stopped,
                       [case.case_id for case in cases if case.case_id not in finished])
    summary = scrub(summary_markdown(predictor, report, len(cases), Mode(mode)), secrets)
    (directory / "summary.md").write_text(summary, encoding="utf-8", newline="\n")
    return report


def _total(values: Sequence[int | None]) -> int:
    return sum(v for v in values if v is not None)


def summary_markdown(predictor: LLMOncePredictor, report: RunReport, n_cases: int, mode: Mode) -> str:
    loaded = predictor.loaded
    records = report.records
    scored = [r.result for r in records if r.status in SCORED and r.result is not None]
    calls = [call for r in records for call in r.llm_calls]
    versions = sorted({c.model_version for c in calls if c.model_version})
    latencies = [c.latency_s for c in calls if c.latency_s is not None]
    count = lambda status: sum(r.status == status for r in records)  # noqa: E731
    lines = [
        f"# LLM baseline: {predictor.name} ({mode.value} mode)",
        "",
        f"{len(records)} of {n_cases} cases recorded: {count('ok')} ok ({sum(r.repaired for r in records)} after a "
        f"repair call), {count('invalid_output')} invalid_output (scored as wrong), {count('api_error')} api_error "
        "(not scored).",
    ]
    if report.stopped:
        lines += ["", f"**Stopped early:** {report.stopped}. Run again with `--resume`."]
    if report.api_errors:
        lines += ["", "**To re-run (api_error):** " + ", ".join(report.api_errors)]
    if report.remaining and len(report.remaining) != len(report.api_errors):
        lines += ["", f"**Not done yet:** {len(report.remaining)} cases."]
    lines += [
        "",
        "## Model calls",
        "",
        "| | |",
        "|---|---|",
        f"| requested model | {predictor.caller.client.requested_model} |",
        f"| model_version served | {', '.join(versions) or '-'} |",
        f"| calls (repairs) | {len(calls)} ({sum(c.purpose == 'repair' for c in calls)}) |",
        f"| attempts (retries) | {sum(c.attempts for c in calls)} ({sum(c.retries for c in calls)}) |",
        f"| tokens in / out | {_total([c.tokens_in for c in calls])} / {_total([c.tokens_out for c in calls])} |",
        f"| thinking tokens | {_total([c.thoughts_tokens for c in calls])} |",
        f"| latency mean / max | {sum(latencies) / len(latencies) if latencies else 0:.2f} s / "
        f"{max(latencies, default=0):.2f} s |",
        f"| prompts over the context budget | {sum(c.context_overflow for c in calls)} |",
        "",
    ]
    if scored:
        lines += ["## Scores (ok and invalid_output cases)", "",
                  to_markdown(summarise(predictor.name, scored, mode.value)).split("\n", 2)[2], ""]
    lines += [
        "## Settings (pre-registered)",
        "",
        f"Config `{loaded.path.name}` (sha256 `{loaded.sha256[:16]}`), prompt `{loaded.config.prompt}` "
        f"(sha256 `{loaded.prompt.sha256[:16]}`).",
        "",
        "```toml",
        loaded.text.rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def make_client(backend: str, loaded: LoadedConfig) -> LLMClient:
    c = loaded.config
    if backend == "gemini":
        g = c.gemini
        return GeminiClient(g.model, thinking_level=g.thinking_level, seed=g.seed, timeout_s=g.timeout_s)
    if backend == "ollama":
        o = c.ollama
        return OllamaClient(o.model, think=o.think, num_ctx=o.num_ctx, seed=o.seed, host=o.host, timeout_s=o.timeout_s)
    raise ValueError(f"unknown backend {backend!r}")


def make_caller(client: LLMClient, loaded: LoadedConfig, results_root: Path,
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
                       retry=c.retry, context_tokens=c.budget.context_tokens, pacer=pacer, ledger=ledger)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ci_triage.evaluation.llm_run", description=__doc__.split("\n")[0])
    parser.add_argument("--backend", choices=["gemini", "ollama"], required=True)
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.FULL.value)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--resume", action="store_true", help="skip cases already done; re-run api_error ones")
    parser.add_argument("--daily-cap", type=int, default=None, help="attempts per day (default: from the config)")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="results root (default: results/)")
    parser.add_argument("--grader", choices=GRADERS, default=DEFAULT_GRADER,
                        help=f"grader version (default {DEFAULT_GRADER}); v1 reproduces runs graded before v2")
    args = parser.parse_args(argv)
    loaded = load_config(args.config)
    mode = Mode(args.mode)
    print(f"config {loaded.path} (sha256 {loaded.sha256[:16]}), prompt sha256 {loaded.prompt.sha256[:16]}")
    print(loaded.text)
    predictor = LLMOncePredictor(make_caller(make_client(args.backend, loaded), loaded, args.out, args.daily_cap),
                                 loaded)
    name = predictor.name if mode is Mode.FULL else f"{predictor.name}-{mode.value}"
    report = run_cases(predictor, lab_cases(mode), args.out / name, resume=args.resume, mode=mode,
                       grader=args.grader)
    print(f"{report.ran} cases run, {report.skipped} skipped as done; wrote {report.directory}")
    if report.api_errors:
        print(f"api_error, re-run with --resume: {', '.join(report.api_errors)}")
    if report.stopped:
        print(f"stopped: {report.stopped}; {len(report.remaining)} cases left, run again with --resume")


if __name__ == "__main__":
    main()
