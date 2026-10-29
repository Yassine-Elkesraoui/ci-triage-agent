"""The key evidence in each case's failing log, for scoring extract_failure_block.

For the 21 cases with expected failing tests, the key evidence is their test
ids. For the other 39 it is the decisive error line(s), hand-picked from the
saved logs and listed below as substrings that must all appear. Where a log
states the root cause apart from the line that ended the step (an apt or shell
error inside a Docker build, a conflict explanation under pip's error), both
are required.

For scoring only, like ground_truth.
"""

from __future__ import annotations

from ci_triage.evaluation.ground_truth import GroundTruth

_SMOKE_TIMEOUT = "##[error]The action 'Run smoke tests' has timed out after 3 minutes."

DECISIVE_LINES: dict[str, tuple[str, ...]] = {
    # real_regression without a failing test: the app no longer imports
    "inj-010": ("ImportError: cannot import name 'ChatRequst' from 'api.models.schemas'",),
    # dependency
    "inj-011": ("ERROR: No matching distribution found for pydantic==9.9.9",),
    "inj-012": ("ERROR: No matching distribution found for numpy==99.0.0",),
    "inj-013": ("ERROR: No matching distribution found for httpx==99.99.99",),
    "inj-014": ("ERROR: No matching distribution found for fastpi==0.136.1",),
    "inj-015": ("ERROR: No matching distribution found for chromedb==1.5.9",),
    "inj-016": ("ERROR: Cannot install -r requirements.txt (line 12) and pydantic==1.10.13",
                "chromadb 1.5.9 depends on pydantic>=2.0"),
    "inj-017": ("ERROR: Cannot install -r requirements.txt (line 113), -r requirements.txt (line 30), "
                "-r requirements.txt (line 70) and anyio==3.6.1",),
    "inj-018": ("ERROR: Invalid requirement: 'numpy=>2.4.4'",),
    "inj-059": ("AttributeError: `np.float_` was removed in the NumPy 2.0 release",),
    "inj-060": ("ValueError: numpy.dtype size changed, may indicate binary incompatibility",),
    # environment
    "inj-029": ("ValidationError: 1 validation error for Settings", "input_value='not_a_number'"),
    "inj-030": ("ValidationError: 1 validation error for Settings", "input_value='big'"),
    "inj-031": ("ValidationError: 1 validation error for Settings", "input_value='high'"),
    "inj-032": ("ValidationError: 1 validation error for Settings", "input_value='maybe'"),
    "inj-035": ("error: unrecognized arguments: --cov=api",),
    "inj-036": ("'minversion' requires pytest-99.0",),
    "inj-037": ("ERROR: Missing required plugins: pytest-xdist",),
    "inj-038": ("##[error]The version '3.99' with architecture 'x64' was not found",),
    # timeout
    "inj-039": ("##[error]The operation was canceled.",),
    "inj-040": ("##[error]The operation was canceled.",),
    "inj-041": ("##[error]The action 'Install dependencies' has timed out after 1 minutes.",),
    "inj-042": ("##[error]The action 'Build image' has timed out after 1 minutes.",),
    "inj-043": (_SMOKE_TIMEOUT,),
    "inj-044": (_SMOKE_TIMEOUT,),
    "inj-045": (_SMOKE_TIMEOUT,),
    "inj-046": (_SMOKE_TIMEOUT,),
    "inj-047": (_SMOKE_TIMEOUT,),
    "inj-048": (_SMOKE_TIMEOUT,),
    # build
    "inj-019": ("failed to solve: python:3.99-slim: failed to resolve source metadata",),
    "inj-020": ("failed to solve: pythonn:3.11-slim: failed to resolve source metadata",),
    "inj-021": ("failed to solve: invalid containerPort: http-port",),
    "inj-022": ('failed to solve: failed to compute cache key', '"/requirement.txt": not found'),
    "inj-023": ("E: Unable to locate package build-essentiall", "failed to solve: process"),
    "inj-024": ("Could not open requirements file: [Errno 2] No such file or directory: 'requirements.text'",),
    "inj-025": ("RepositoryNotFoundError: 401 Client Error", "failed to solve: process"),
    "inj-026": ('failed to solve: failed to compute cache key', '"/apii": not found'),
    "inj-027": ('failed to solve: failed to compute cache key', '"/evall": not found'),
    "inj-028": ("mkdirr: not found", "failed to solve: process"),
}


def key_evidence(truth: GroundTruth) -> tuple[str, ...]:
    """Substrings a good failure block for this case must contain."""
    return truth.expected_failing_tests or DECISIVE_LINES[truth.case_id]
