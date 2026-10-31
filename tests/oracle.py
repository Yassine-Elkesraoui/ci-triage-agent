"""The oracle: answers from ground truth, quoting the failure block it fetched. It must score 100%.

Test-only: no predictor in src/ may read ground truth. To write its results with the runner:
``PYTHONPATH=tests python -m ci_triage.evaluation.run --predictor oracle:OraclePredictor``.
"""

from __future__ import annotations

import re

from ci_triage.diagnosis import ACTION_FOR, Category, Diagnosis, Evidence, EvidenceSource
from ci_triage.evaluation.ground_truth import GroundTruth, load_ground_truth
from ci_triage.predictors import CaseView
from ci_triage.tools import Toolkit

_NUMBERED = re.compile(r"^\d+\| (.*\S.*)$", re.MULTILINE)


class OraclePredictor:
    name = "oracle"

    def __init__(self, truth: dict[str, GroundTruth] | None = None) -> None:
        truth = truth or load_ground_truth()
        self._by_run = {t.eval_run_id: t for t in truth.values()}
        assert len(self._by_run) == len(truth), "eval runs must identify cases"

    def predict(self, case_view: CaseView, toolkit: Toolkit) -> Diagnosis:
        truth = self._by_run[case_view.run_id]
        category = Category(truth.category)
        jobs = toolkit.call("get_jobs", {"run_id": case_view.run_id})
        job = next(job for job in jobs.jobs if job.conclusion in ("failure", "cancelled"))
        block = toolkit.call("extract_failure_block", {"job_id": job.id})
        quotes = [_NUMBERED.search(b.text).group(1)[:300] for b in block.blocks[:2]]
        fix = "\n".join(truth.restored) if truth.restored else "Delete these lines:\n" + "\n".join(truth.removed)
        return Diagnosis(
            category=category,
            confidence=1.0,
            failing_job=job.name,
            failing_step=truth.failing_step,
            suspect_file=truth.target_file,
            evidence=[Evidence(quote=quote, source=EvidenceSource.LOG) for quote in quotes],
            root_cause=truth.mutation,
            suggested_fix=fix,
            recommended_action=ACTION_FOR[category],
        )
