"""What a predictor returns for one failing run: the Diagnosis model.

Its JSON schema is the output format an LLM will be asked for, so every field
carries a description. Unknown fields are rejected.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MAX_EVIDENCE = 5


class Category(StrEnum):
    """The six failure categories of ci-triage-lab."""

    REAL_REGRESSION = "real_regression"  # the application code is wrong
    FLAKY = "flaky"  # the same code passes and fails from run to run
    DEPENDENCY = "dependency"  # a package pin or version conflict
    ENVIRONMENT = "environment"  # CI or test configuration: env vars, pytest.ini, Python version
    TIMEOUT = "timeout"  # a step or job ran out of time
    BUILD = "build"  # the Docker image does not build


CATEGORIES: tuple[str, ...] = tuple(category.value for category in Category)


class EvidenceSource(StrEnum):
    LOG = "log"  # get_job_log, extract_failure_block
    DIFF = "diff"  # get_commit, compare
    FILE = "file"  # get_file
    HISTORY = "history"  # list_runs, get_run, get_jobs, get_commit metadata


class RecommendedAction(StrEnum):
    FIX_CODE = "fix_code"
    FIX_DEPENDENCY = "fix_dependency"
    FIX_BUILD = "fix_build"
    FIX_CONFIG = "fix_config"
    INVESTIGATE_TIMEOUT = "investigate_timeout"
    RERUN_AND_QUARANTINE = "rerun_and_quarantine"


# The action each category calls for; the grader checks a diagnosis's action against its true category.
ACTION_FOR: dict[Category, RecommendedAction] = {
    Category.REAL_REGRESSION: RecommendedAction.FIX_CODE,
    Category.FLAKY: RecommendedAction.RERUN_AND_QUARANTINE,
    Category.DEPENDENCY: RecommendedAction.FIX_DEPENDENCY,
    Category.ENVIRONMENT: RecommendedAction.FIX_CONFIG,
    Category.TIMEOUT: RecommendedAction.INVESTIGATE_TIMEOUT,
    Category.BUILD: RecommendedAction.FIX_BUILD,
}


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1, max_length=1000,
                       description="Text copied verbatim from a tool result, without the '<n>| ' line-number prefixes.")
    source: EvidenceSource = Field(description="Where the quote comes from: log, diff, file or history.")


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category = Field(description="The failure category.")
    confidence: float = Field(ge=0, le=1, description="Probability that the category is right, from 0 to 1.")
    failing_job: str = Field(description="Name of the job that failed, as get_jobs shows it.")
    failing_step: str = Field(description="Name of the step that failed, as get_jobs shows it.")
    suspect_file: str | None = Field(None, description="Repository-relative path of the file to change to fix it.")
    evidence: list[Evidence] = Field(default_factory=list, max_length=MAX_EVIDENCE,
                                     description=f"Up to {MAX_EVIDENCE} verbatim quotes supporting the diagnosis.")
    root_cause: str = Field(max_length=500, description="What went wrong, in a sentence or two.")
    suggested_fix: str | None = Field(None, max_length=2000,
                                      description="The change that fixes it, quoting the corrected code or config.")
    recommended_action: RecommendedAction = Field(description="What to do next.")
