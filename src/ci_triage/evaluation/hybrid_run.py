"""Hybrid v1, scored from saved runs: no model call and no tool call of its own (phase B, section 2).

``python -m ci_triage.evaluation.hybrid_run --backend gemini|ollama --mode log-only|full [--out results/]``

Case by case, it combines the rules v1 run of the mode (``results/rules[-log-only]/cases.jsonl``, written by
``ci_triage.evaluation.run``) with the agent v2 run on the same backend and mode
(``results/agent_v2-<backend>[-log-only]/cases.jsonl``): where one of rules 1 to 6 fired, the rules' graded case
is the hybrid's; where they fell back (rule 7), the agent's is. ``predictors.hybrid.rules_decide`` makes that
choice here and in the live ``HybridPredictor``.

Both runs must cover the same cases and be graded by the same grader. An agent case that is api_error (or
absent) is not scored and is listed, as for the agent's own run. It writes
``results/hybrid_v1-<backend>[-log-only]/cases.jsonl`` (per case: who decided, the rule, the status and the graded
result) and ``summary.md``, which names both inputs with their sha256.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ci_triage.evaluation.agent_run import AgentCaseRecord, AgentCaseRecordV2, read_agent_records
from ci_triage.evaluation.grader import CaseResult
from ci_triage.evaluation.metrics import summarise, to_markdown
from ci_triage.evaluation.run import results_name
from ci_triage.labpaths import RESULTS_DIR
from ci_triage.predictors.hybrid import DecidedBy, fired_rule, rules_decide
from ci_triage.tools import Mode

NAME = "hybrid_v1"
RULES_NAME = "rules"
AGENT_NAME = "agent_v2"
SCORED = ("ok", "invalid_output")


class HybridCaseRecord(BaseModel):
    case_id: str
    decided_by: DecidedBy
    rule: str | None  # the rule that fired, as the rules diagnosis says
    status: Literal["ok", "invalid_output", "api_error", "missing"]  # the agent's when it decided, else ok
    result: CaseResult | None  # the graded case of whoever decided; None when not scored


def read_rules_results(path: Path) -> list[CaseResult]:
    return [CaseResult.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def combine(rules: Sequence[CaseResult], agent: Sequence[AgentCaseRecord]) -> list[HybridCaseRecord]:
    """The hybrid's cases, in the rules run's order."""
    by_case = {r.case_id: r for r in agent}
    if len(by_case) != len(agent) or len({r.case_id for r in rules}) != len(rules):
        raise ValueError("a run lists a case twice")
    extra = sorted(set(by_case) - {r.case_id for r in rules})
    if extra:
        raise ValueError(f"the agent run has cases the rules run has not: {extra}")
    graders = {r.grader for r in rules} | {r.result.grader for r in agent if r.result is not None}
    if len(graders) > 1:
        raise ValueError(f"the runs were graded by different graders: {', '.join(sorted(graders))}")
    out = []
    for result in rules:
        rule = fired_rule(result.diagnosis)
        if rules_decide(result.diagnosis):
            out.append(HybridCaseRecord(case_id=result.case_id, decided_by="rules", rule=rule, status="ok",
                                        result=result))
            continue
        record = by_case.get(result.case_id)
        out.append(HybridCaseRecord(case_id=result.case_id, decided_by="agent_v2", rule=rule,
                                    status=record.status if record is not None else "missing",
                                    result=record.result if record is not None and record.status in SCORED else None))
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summary_markdown(name: str, records: Sequence[HybridCaseRecord], mode: Mode, rules_path: Path,
                     agent_path: Path) -> str:
    scored = [r.result for r in records if r.result is not None]
    unscored = [r for r in records if r.result is None]
    lines = [f"# Hybrid: {name} ({mode.value} mode)", "",
             f"{len(records)} cases: {sum(r.decided_by == 'rules' for r in records)} decided by rules v1 (rules 1-6), "
             f"{sum(r.decided_by == 'agent_v2' for r in records)} by agent v2 (rule 7, the fallback); "
             f"{len(scored)} scored."]
    if unscored:
        lines.append(f"Not scored (the agent's case is api_error or missing): "
                     f"{', '.join(f'{r.case_id} ({r.status})' for r in unscored)}.")
    lines += ["", "| decided by | cases | category correct |", "|---|---|---|"]
    for who in ("rules", "agent_v2"):
        mine = [r for r in records if r.decided_by == who]
        right = sum(r.result.category_correct for r in mine if r.result is not None)
        lines.append(f"| {who} | {len(mine)} | {right}/{sum(r.result is not None for r in mine)} |")
    rules_fired = Counter(r.rule or "none" for r in records)
    lines += ["", "Rules fired: " + ", ".join(f"{rule} {n}" for rule, n in sorted(rules_fired.items())) + ".", ""]
    if scored:
        lines += ["## Scores", "", to_markdown(summarise(name, scored, mode.value)).split("\n", 2)[2], ""]
    lines += ["## Inputs (no model or tool calls of its own)", "",
              f"- rules v1: `{rules_path.as_posix()}` (sha256 `{_sha256(rules_path)[:16]}`)",
              f"- agent v2: `{agent_path.as_posix()}` (sha256 `{_sha256(agent_path)[:16]}`)", ""]
    return "\n".join(lines)


def score(backend: str, mode: Mode | str, out: Path = RESULTS_DIR, results: Path | None = None) -> Path:
    """Combine the saved runs under ``results`` (default ``out``); write the hybrid's run under ``out``."""
    mode, results = Mode(mode), Path(results or out)
    rules_path = results / results_name(RULES_NAME, mode) / "cases.jsonl"
    agent_path = results / results_name(f"{AGENT_NAME}-{backend}", mode) / "cases.jsonl"
    for path in (rules_path, agent_path):
        if not path.is_file():
            raise SystemExit(f"{path} not found: run it first")
    records = combine(read_rules_results(rules_path), read_agent_records(agent_path, AgentCaseRecordV2))
    name = f"{NAME}-{backend}"
    directory = Path(out) / results_name(name, mode)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "cases.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        f.writelines(record.model_dump_json() + "\n" for record in records)
    (directory / "summary.md").write_text(summary_markdown(name, records, mode, rules_path, agent_path),
                                          encoding="utf-8", newline="\n")
    return directory


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ci_triage.evaluation.hybrid_run",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--backend", choices=["gemini", "ollama"], required=True)
    parser.add_argument("--mode", choices=[m.value for m in Mode], required=True)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="results root (default: results/)")
    args = parser.parse_args(argv)
    print(f"wrote {score(args.backend, args.mode, args.out)}")


if __name__ == "__main__":
    main()
