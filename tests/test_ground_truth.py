"""Ground truth loads for every case and stays out of the replay layer."""

import ast
from collections import Counter
from pathlib import Path

from conftest import CASES

import ci_triage

SRC = Path(ci_triage.__file__).parent


def test_every_case_has_ground_truth(truth):
    assert set(truth) == {case.case_id for case in CASES}
    assert Counter(t.category for t in truth.values()) == {
        "real_regression": 10, "flaky": 10, "dependency": 10, "environment": 10, "timeout": 10, "build": 10,
    }


def test_revert_commits(truth, lab):
    for t in truth.values():
        assert t.revert_commit, t.case_id
        assert lab.git.is_ancestor(t.injection_commit, t.revert_commit)
        assert t.revert_commit != t.injection_commit


def test_target_file_is_what_the_injection_changed(truth, lab):
    for t in truth.values():
        parent = lab.git.commit_header(t.injection_commit)[1][0]
        changed = {f["filename"] for f in lab.git.diff_files(parent, t.injection_commit)}
        assert t.target_file in changed, t.case_id


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_replay_never_imports_ground_truth():
    replay_side = [*(SRC / "replay").glob("*.py"), *(SRC / "tools").glob("*.py"),
                   SRC / "github_source.py", SRC / "_git.py", SRC / "labpaths.py"]
    for path in replay_side:
        offending = {name for name in _imports(path) if name.startswith("ci_triage.evaluation")}
        assert not offending, f"{path.name} imports {offending}"
