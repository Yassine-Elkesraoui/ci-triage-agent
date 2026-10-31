"""Predictors: given a failing run and a toolkit, return a Diagnosis. They never see ground truth."""

from ci_triage.predictors.base import CaseView, Predictor
from ci_triage.predictors.baselines import ConstantPredictor, RandomPredictor

# Predictors the runner knows by name; others load as "module:attribute".
PREDICTORS = {"constant": ConstantPredictor, "random": RandomPredictor}

__all__ = ["PREDICTORS", "CaseView", "ConstantPredictor", "Predictor", "RandomPredictor"]
