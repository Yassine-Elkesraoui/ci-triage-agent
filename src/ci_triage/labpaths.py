"""Where the ci-triage-lab submodule lives."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def default_lab_dir() -> Path:
    """``$CI_TRIAGE_LAB`` if set, else the ``lab/`` submodule of this repository."""
    return Path(os.environ.get("CI_TRIAGE_LAB") or REPO_ROOT / "lab")
