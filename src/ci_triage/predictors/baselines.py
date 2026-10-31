"""Predictors that read nothing, to check the grader: what knowing nothing scores."""

from __future__ import annotations

import random

from ci_triage.diagnosis import ACTION_FOR, CATEGORIES, Category, Diagnosis
from ci_triage.predictors.base import CaseView
from ci_triage.tools import Toolkit

UNKNOWN = "unknown"


class ConstantPredictor:
    """Always real_regression, with the uniform prior as its confidence."""

    name = "constant"

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        category = Category.REAL_REGRESSION
        return Diagnosis(category=category, confidence=1 / len(CATEGORIES), failing_job=UNKNOWN,
                         failing_step=UNKNOWN, root_cause="always guesses a code regression",
                         recommended_action=ACTION_FOR[category])


class RandomPredictor:
    """A uniformly random category, the same for a given seed and run whatever the order of cases."""

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        category = Category(random.Random(f"{self.seed}:{case_view.run_id}").choice(CATEGORIES))
        return Diagnosis(category=category, confidence=1 / len(CATEGORIES), failing_job=UNKNOWN,
                         failing_step=UNKNOWN, root_cause="a random guess",
                         recommended_action=ACTION_FOR[category])
