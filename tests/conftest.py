import pytest

from ci_triage.evaluation.ground_truth import load_ground_truth
from ci_triage.labpaths import default_lab_dir
from ci_triage.replay import ReplaySource, load_cases, load_lab

LAB_DIR = default_lab_dir()
if not (LAB_DIR / "injections" / "index.json").is_file():
    raise RuntimeError(f"lab submodule missing at {LAB_DIR}; run: git submodule update --init")

LAB = load_lab(LAB_DIR)
if LAB.git.is_shallow():
    raise RuntimeError("lab/ is a shallow clone; the replay layer needs its full git history")

CASES = load_cases(LAB)
TRUTH = load_ground_truth(LAB_DIR)
RERUN_CASES = [case for case in CASES if not TRUTH[case.case_id].eval_commit_is_injection]
LATEST_CASE = max(CASES, key=lambda case: LAB.run(case.eval_run_id)["created_at"])  # sees the most history


def pytest_generate_tests(metafunc):
    if "case" in metafunc.fixturenames:
        metafunc.parametrize("case", CASES, ids=[c.case_id for c in CASES])
    if "rerun_case" in metafunc.fixturenames:
        metafunc.parametrize("rerun_case", RERUN_CASES, ids=[c.case_id for c in RERUN_CASES])


@pytest.fixture(scope="session")
def lab():
    return LAB


@pytest.fixture(scope="session")
def truth():
    return TRUTH


@pytest.fixture
def source(case, lab):
    return ReplaySource(case, lab)
