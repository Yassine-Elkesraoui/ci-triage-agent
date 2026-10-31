"""The predictor interface: one failing run in, one Diagnosis out, every read through the toolkit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ci_triage.diagnosis import Diagnosis
from ci_triage.tools import Toolkit


@dataclass(frozen=True)
class CaseView:
    """What a triager is told about a case: the run to triage. Never a case id or a label."""

    run_id: int


@runtime_checkable
class Predictor(Protocol):
    name: str  # results go to results/<name>/

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        """Diagnose ``case_view.run_id``, reading GitHub only through ``toolkit`` (its call log is graded)."""
