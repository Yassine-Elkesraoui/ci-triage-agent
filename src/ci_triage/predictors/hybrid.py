"""Hybrid v1 (phase B, pre-registered in docs/preregistration/phase-b.md, section 2): rules v1 decide whenever
one of their rules 1 to 6 fires, and their diagnosis is the answer; when they fall back (rule 7), agent v2
decides, and its diagnosis is the answer.

Which rule fired is read from the rules' own diagnosis: rules v1 (unchanged) start every ``root_cause`` with
``Rule <id> (<name>): ...``. ``rules_decide`` is the one place the decision is made; the live predictor below
and the scorer that combines saved runs (``ci_triage.evaluation.hybrid_run``) both call it.

The live predictor runs the rules, then, only on a fallback, agent v2 on the same toolkit. The toolkit only
reads, so the agent's bundle and tool results are those of a run of its own; its repeat guard counts only its
own calls.

Needs ``langgraph`` (agent v2). Must not import ``ci_triage.evaluation``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from ci_triage.agent.loop import AgentError, Status
from ci_triage.agent.loop_v2 import AgentOutcomeV2, ToolAgentV2
from ci_triage.diagnosis import Diagnosis
from ci_triage.predictors.base import CaseView
from ci_triage.predictors.rules import RULE_NAMES, RulesPredictor
from ci_triage.tools import Toolkit

FALLBACK_RULE = "7"
_RULE = re.compile(r"^Rule (?P<rule>\w+) \((?P<name>[^)]*)\)")

DecidedBy = Literal["rules", "agent_v2"]


def fired_rule(diagnosis: Diagnosis | None) -> str | None:
    """The id of the rule that produced a rules v1 diagnosis (``"1"`` ... ``"6e"``, ``"7"``), or None."""
    match = _RULE.match(diagnosis.root_cause) if diagnosis is not None else None
    if match is None or RULE_NAMES.get(match["rule"]) != match["name"]:
        return None
    return match["rule"]


def rules_decide(rules_diagnosis: Diagnosis | None) -> bool:
    """True when one of rules 1 to 6 fired; False on the fallback (rule 7) or with no rules diagnosis."""
    rule = fired_rule(rules_diagnosis)
    return rule is not None and rule != FALLBACK_RULE


class HybridOutcome(BaseModel):
    decided_by: DecidedBy
    rule: str | None  # the rule that fired, as the rules diagnosis says
    status: Status  # the agent's when it decided, else ok
    diagnosis: Diagnosis | None
    rules_diagnosis: Diagnosis
    agent: AgentOutcomeV2 | None = None  # only when the agent decided
    error: str | None = None


class HybridPredictor:
    """Rules v1, or agent v2 on their fallback. ``predict`` raises ``AgentError`` when the agent decided and
    gave no diagnosis (invalid_output or api_error)."""

    name = "hybrid_v1"

    def __init__(self, agent: ToolAgentV2, rules: RulesPredictor | None = None) -> None:
        self.agent = agent
        self.rules = rules or RulesPredictor()
        self.name = f"hybrid_v1-{agent.caller.client.backend}"

    def run(self, case_view: CaseView, toolkit: Toolkit) -> HybridOutcome:
        rules = self.rules.predict(case_view, toolkit)
        rule = fired_rule(rules)
        if rules_decide(rules):
            return HybridOutcome(decided_by="rules", rule=rule, status="ok", diagnosis=rules, rules_diagnosis=rules)
        outcome = self.agent.run(case_view, toolkit)
        return HybridOutcome(decided_by="agent_v2", rule=rule, status=outcome.status, diagnosis=outcome.diagnosis,
                             rules_diagnosis=rules, agent=outcome, error=outcome.error)

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        outcome = self.run(case_view, toolkit)
        if outcome.diagnosis is None:
            raise AgentError(outcome.agent)  # type: ignore[arg-type]
        return outcome.diagnosis
