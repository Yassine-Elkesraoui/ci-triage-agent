"""extract_failure_block: key-evidence coverage on all 60 cases, size, and behaviour on synthetic logs.

Run with ``-s`` to see the coverage and size tables.
"""

import pytest
from conftest import CASES, LAB, TRUTH

from ci_triage.evaluation.failure_block_report import coverage_table, score_case, size_table
from ci_triage.evaluation.key_evidence import DECISIVE_LINES
from ci_triage.replay import ReplaySource
from ci_triage.tools import Toolkit, render
from ci_triage.tools.failure_block import find_evidence
from ci_triage.tools.github_tools import extract_failure_block
from ci_triage.tools.models import ExtractFailureBlockInput
from ci_triage.tools.text import log_lines


@pytest.fixture(scope="module")
def scores():
    return [score_case(ReplaySource(case, LAB), TRUTH[case.case_id]) for case in CASES]


def test_key_evidence_is_defined_for_every_case():
    with_tests = [t for t in TRUTH.values() if t.expected_failing_tests]
    assert len(with_tests) == 21
    assert set(DECISIVE_LINES) == {t.case_id for t in TRUTH.values() if not t.expected_failing_tests}


def test_key_evidence_is_in_the_raw_log(case):
    """Sanity check of the labels: every needle is in the log the source serves."""
    toolkit = Toolkit(ReplaySource(case, LAB))
    jobs = toolkit.call("get_jobs", {"run_id": case.eval_run_id}).jobs
    job = next(j for j in jobs if j.conclusion in ("failure", "cancelled"))
    text = "\n".join(log_lines(toolkit.source.get_job_log(job.id)))
    truth = TRUTH[case.case_id]
    for needle in truth.expected_failing_tests or DECISIVE_LINES[case.case_id]:
        assert needle in text, needle


def test_every_block_contains_the_key_evidence(scores):
    print("\n" + coverage_table(scores) + "\n\n" + size_table(scores))
    missing = {s.case_id: s.missing for s in scores if s.missing}
    assert not missing


def test_blocks_are_small_next_to_the_log(scores):
    assert sum(s.block_chars for s in scores) / sum(s.log_chars for s in scores) < 0.05
    assert max(s.block_chars for s in scores) <= 4000


# -- synthetic logs -------------------------------------------------------------

TS = "2026-01-01T00:00:00.0000000Z "


class _OneLog:
    """A source with a single job log."""

    def __init__(self, text):
        self.text = text

    def get_job_log(self, job_id):
        return self.text


def _blocks(lines, cap=4000):
    result = extract_failure_block(_OneLog("\n".join(TS + line for line in lines)), ExtractFailureBlockInput(job_id=1), cap)
    assert len(render(result)) <= cap
    return result


PYTEST_LOG = [
    "##[group]Run python -m pytest tests -v",
    "tests/test_a.py::test_ok PASSED [ 50%]",
    "tests/test_a.py::test_bad FAILED [100%]",
    "=================================== FAILURES ===================================",
    "___________________________________ test_bad ___________________________________",
    "    def test_bad():",
    ">       assert 1 == 2",
    "E       assert 1 == 2",
    "",
    "tests/test_a.py:3: AssertionError",
    "------------------------------ Captured log call -------------------------------",
    "INFO     app:config.py:12 settings loaded",
    "=========================== short test summary info ============================",
    "FAILED tests/test_a.py::test_bad - assert 1 == 2",
    "========================= 1 failed, 1 passed in 0.01s ==========================",
    "##[error]Process completed with exit code 1.",
    "Post job cleanup.",
]


def test_pytest_order_and_step():
    result = _blocks(PYTEST_LOG)
    kinds = [b.kinds[0] for b in result.blocks]
    assert kinds == ["pytest_summary", "pytest_assertion", "pytest_failed_test"]
    assert "FAILED tests/test_a.py::test_bad - assert 1 == 2" in result.blocks[0].text
    assert "exit_code" in result.blocks[0].kinds  # merged: adjacent to the summary
    assert "10| tests/test_a.py:3: AssertionError" in result.blocks[1].text
    assert all(b.step == "python -m pytest tests -v" for b in result.blocks)


def test_timestamps_and_ansi_are_stripped():
    result = _blocks(["\x1b[36;1mnoise\x1b[0m", "##[error]Something broke"])
    assert result.blocks[0].text == "1| noise\n2| ##[error]Something broke"


def test_traceback_final_exception_outranks_its_cause():
    lines = [
        "Traceback (most recent call last):",
        '  File "a.py", line 1, in <module>',
        "KeyError: 'x'",
        "",
        "The above exception was the direct cause of the following exception:",
        "",
        "Traceback (most recent call last):",
        '  File "b.py", line 2, in <module>',
        "    load()",
        "RuntimeError: config could not be loaded",
        "##[error]Process completed with exit code 1.",
    ]
    blocks = find_evidence(log_lines("\n".join(lines)))
    assert [b.kinds[0] for b in blocks] == ["python_exception", "python_exception"]
    assert blocks[0].priority == 1 and 9 in blocks[0].keys and blocks[1].priority == 2


def test_timeout_keeps_what_was_running_and_orphans():
    lines = [f"tests/test_a.py::test_{n} PASSED [{n}%]" for n in range(20)] + [
        "##[error]The action 'Run smoke tests' has timed out after 3 minutes.",
        "Post job cleanup.",
        "Cleaning up orphan processes",
        "Terminate orphan process: pid (42) (python)",
    ]
    result = _blocks(lines)
    assert result.blocks[0].kinds == ["timeout"] and "test_19 PASSED" in result.blocks[0].text
    assert result.blocks[1].kinds == ["orphan_process"] and result.blocks[1].step is None


def test_pip_noise_is_context_not_evidence():
    lines = [
        "##[group]Run pip install -r requirements.txt",
        "ERROR: Ignored the following yanked versions: " + "1.0, " * 2000,
        "ERROR: Could not find a version that satisfies the requirement foo==9 (from versions: " + "1.0, " * 2000,
        "ERROR: No matching distribution found for foo==9",
        "##[error]Process completed with exit code 1.",
    ]
    tight = _blocks(lines, cap=700)
    assert "No matching distribution found for foo==9" in tight.blocks[0].text
    assert "Ignored the following" not in tight.blocks[0].text
    assert "… [+" in _blocks(lines).blocks[0].text  # long lines clipped


def test_buildkit_failure_keeps_the_dockerfile_excerpt():
    lines = [
        "##[group]Run docker build -t app:ci .",
        "#14 [9/9] RUN mkdirr -p data",
        "#14 0.105 /bin/sh: 1: mkdirr: not found",
        '#14 ERROR: process "/bin/sh -c mkdirr -p data" did not complete successfully: exit code: 127',
        "------",
        " > [9/9] RUN mkdirr -p data:",
        "0.105 /bin/sh: 1: mkdirr: not found",
        "------",
        "Dockerfile:41",
        "--------------------",
        "  41 | >>> RUN mkdirr -p data",
        "--------------------",
        'ERROR: failed to build: failed to solve: process "/bin/sh -c mkdirr -p data" did not complete successfully',
        "##[error]Process completed with exit code 1.",
    ]
    block = _blocks(lines).blocks[0]
    assert block.kinds[0] == "buildkit_failed_to_solve"
    assert ">>> RUN mkdirr -p data" in block.text and "mkdirr: not found" in block.text
    assert block.step == "docker build -t app:ci ."


def test_nothing_recognisable_falls_back_to_the_log_tail():
    result = _blocks([f"line {n}" for n in range(50)] + ["Post job cleanup.", "cleanup noise"])
    assert result.blocks[0].kinds == ["log_tail"]
    assert "50| line 49" in result.blocks[0].text and "cleanup noise" not in result.blocks[0].text
    assert result.note == "No decisive error line found in this log; showing the last lines before job cleanup."


def test_evidence_found_means_no_fallback_note():
    assert _blocks(PYTEST_LOG).note is None


def test_a_block_too_big_for_the_cap_shows_its_first_key_lines():
    lines = [f"tests/test_a.py::test_{n} FAILED [{n % 100}%]" for n in range(400)]
    lines += [f"Traceback (most recent call last):\n  File \"x.py\", line {n}\nValueError: bad {n}" for n in range(50)]
    result = _blocks("\n".join(lines).split("\n"), cap=1000)
    first = result.blocks[0]
    assert first.kinds[0] == "python_exception"  # everything is adjacent, so it all merges into one block
    assert "ValueError: bad 0" in first.text and "ValueError: bad 49" not in first.text and first.text.endswith("…")
    assert "FAILED" not in first.text  # the exceptions outrank the progress lines merged with them
    assert "trimmed to fit" in result.note


def test_blocks_that_do_not_fit_are_omitted_and_named():
    lines = []
    for n in range(30):
        lines += [f"##[error]Problem number {n} " + "x" * 150, *(f"filler {n}.{k}" for k in range(5))]
    result = _blocks(lines, cap=1500)
    assert result.omitted_blocks and "omitted at the size cap" in result.note
    assert result.blocks[0].text.startswith("1| ##[error]Problem number 0")


def test_empty_log():
    assert _blocks([]).blocks == []
