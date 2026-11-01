"""LEAK TEST for log-only mode: no tool output reveals what changed, on any case.

Log-only switches off get_commit and compare and serves get_file only at the
commit under triage. These tests drive every remaining tool over everything the
case's scope allows, and check that nothing diff-shaped, and none of the lines the
injection removed, comes back.
"""

from __future__ import annotations

import re

import pytest
from conftest import LAB
from tooling import read_all

from ci_triage.github_source import NotAvailable
from ci_triage.replay import ReplaySource
from ci_triage.tools import LOG_ONLY_REASON, TOOLS, LogOnlySource, Mode, make_toolkit, tool_definitions
from ci_triage.tools.models import CommitResult, CompareResult, FileResult, NotAvailableResult

REMOVED = ("get_commit", "compare")
DIFF_KEYS = {"files", "patch", "patch_lines", "patch_offset", "base_commit", "head_commit", "parents", "hidden_files",
             "diff_note"}
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@|^diff --git |^--- a/|^\+\+\+ b/", re.MULTILINE)
MIN_LINE = 8  # shorter removed lines ("]", "env:") occur everywhere


def _toolkit(case):
    return make_toolkit(ReplaySource(case, LAB), case.eval_commit, Mode.LOG_ONLY)


def _walk(value, key=None):
    """(key, string) for every string in a dumped output, keys included."""
    if isinstance(value, dict):
        for k, item in value.items():
            yield None, k
            yield from _walk(item, k)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, key)
    elif isinstance(value, str):
        yield key, value


def exercise_log_only(toolkit, case, truth):
    """Every tool on everything the case's scope allows in log-only mode, every page; (where, output) pairs."""
    out = []

    def call(where, tool, args):
        out.append((where, toolkit.call(tool, args)))
        return out[-1][1]

    def paged(where, tool, args):
        out.extend((f"{where} page {n}", page) for n, page in enumerate(read_all(toolkit, tool, args)))

    runs = call("list_runs", "list_runs", {"limit": 100})
    for run in runs.runs:
        call(f"get_run({run.id})", "get_run", {"run_id": run.id})
        jobs = call(f"get_jobs({run.id})", "get_jobs", {"run_id": run.id})
        if isinstance(jobs, NotAvailableResult):
            continue
        for job in jobs.jobs:
            call(f"extract_failure_block({job.id})", "extract_failure_block", {"job_id": job.id})
            if not isinstance(toolkit.call("get_job_log", {"job_id": job.id}), NotAvailableResult):
                paged(f"get_job_log({job.id})", "get_job_log", {"job_id": job.id})
    for path in ("", ".github/workflows/ci.yml", "requirements.txt", "Dockerfile", "pytest.ini", truth.target_file):
        if not isinstance(toolkit.call("get_file", {"path": path}), NotAvailableResult):
            paged(f"get_file({path!r})", "get_file", {"path": path})
    # what a model might try anyway
    source = toolkit.source._inner
    for sha in ("HEAD", case.eval_commit, source.baseline_commit, case.injection_commit):
        call(f"get_commit({sha[:7]})", "get_commit", {"sha": sha})
        call(f"compare(baseline, {sha[:7]})", "compare", {"base": source.baseline_commit, "head": sha})
        call(f"get_file at {sha[:7]}", "get_file", {"path": truth.target_file, "ref": sha})
    return out


def removed_lines(case, truth) -> list[str]:
    """Lines the injection took out of the target file that are nowhere in its eval-commit version."""
    at_eval = LAB.git.text("show", f"{case.eval_commit}:{truth.target_file}")
    return [line.strip() for line in truth.restored if len(line.strip()) >= MIN_LINE and line.strip() not in at_eval]


# -- the leak test ----------------------------------------------------------------------


def test_no_output_reveals_the_change(case, truth):
    t = truth[case.case_id]
    toolkit = _toolkit(case)
    outputs = exercise_log_only(toolkit, case, t)
    needles = removed_lines(case, t)
    found = []
    for where, output in outputs:
        assert not isinstance(output, CommitResult | CompareResult), where
        for key, text in _walk(output.model_dump(mode="json")):
            if key is None and text in DIFF_KEYS:
                found.append((where, f"diff field {text!r}"))
            elif key is not None and HUNK.search(text):
                found.append((where, f"diff text in {key}: {HUNK.search(text).group()!r}"))
            elif key is not None:
                found += [(where, f"removed line {n!r}") for n in needles if n in text]
    assert not found, "\n".join(f"{where}: {what}" for where, what in found[:20])
    assert all(call.error is None for call in toolkit.calls), [c.error for c in toolkit.calls if c.error]


def test_the_leak_test_has_teeth(case, truth):
    """In full mode, compare(baseline, eval) shows those removed lines, so their absence above means something."""
    t = truth[case.case_id]
    needles = removed_lines(case, t)
    if not needles:
        # 22 reverts only remove lines; in inj-007 and inj-008 the restored line also occurs elsewhere at eval
        pytest.skip("no removed line of 8+ characters is absent from the eval-commit version of the file")
    source = ReplaySource(case, LAB)
    full = make_toolkit(source, case.eval_commit, Mode.FULL)
    pages = read_all(full, "compare", {"base": source.baseline_commit, "head": case.eval_commit,
                                       "path": t.target_file})
    patch = "\n".join(page.files[0].patch for page in pages)
    assert HUNK.search(patch) and all(n in patch for n in needles)


def test_tool_list_has_no_diff_tools():
    definitions = make_toolkit(None, "0" * 40, Mode.LOG_ONLY).tool_definitions()
    assert {d["name"] for d in definitions} == set(TOOLS) - set(REMOVED)
    assert len(make_toolkit(None, "0" * 40, Mode.FULL).tool_definitions()) == len(tool_definitions()) == len(TOOLS)


# -- get_file only at the eval commit ----------------------------------------------------


def test_get_file_rejects_other_refs(case):
    toolkit = _toolkit(case)
    source = toolkit.source._inner
    parent = LAB.git.commit_header(case.eval_commit)[1][0]
    others = {"baseline": source.baseline_commit, "parent": parent, "parent, short": parent[:7],
              "injection parent": LAB.git.commit_header(case.injection_commit)[1][0],
              "HEAD~1": "HEAD~1", "HEAD^": "HEAD^", "main": "main", "main~1": "main~1", "empty": "",
              "not a sha": "zzzz", "too short": case.eval_commit[:3]}
    if case.injection_commit != case.eval_commit:
        others["injection"] = case.injection_commit  # same tree for rerun cases, but still another commit
    for name, ref in others.items():
        result = toolkit.call("get_file", {"path": ".github/workflows/ci.yml", "ref": ref})
        assert isinstance(result, NotAvailableResult), name
        assert result.reason.startswith(LOG_ONLY_REASON), (name, result.reason)
    for ref in (None, "HEAD", case.eval_commit, case.eval_commit[:7], case.eval_commit[:12].upper()):
        args = {"path": ".github/workflows/ci.yml"} | ({"ref": ref} if ref is not None else {})
        result = toolkit.call("get_file", args)
        assert isinstance(result, FileResult) and result.type == "file", ref


def test_log_only_source_refuses_diffs_itself(case):
    """Even called directly, past the toolkit, the source serves no diff."""
    source = ReplaySource(case, LAB)
    log_only = LogOnlySource(source, case.eval_commit)
    for result in (log_only.get_commit(case.eval_commit), log_only.get_commit("HEAD"),
                   log_only.compare(source.baseline_commit, case.eval_commit),
                   log_only.get_file("", source.baseline_commit)):
        assert isinstance(result, NotAvailable) and result.reason.startswith(LOG_ONLY_REASON)
    assert log_only.get_file("")["type"] == "dir"
    assert log_only.list_runs() == source.list_runs()
    assert log_only.get_jobs(case.eval_run_id) == source.get_jobs(case.eval_run_id)


# -- removed tools ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", REMOVED)
@pytest.mark.parametrize("arguments", [
    {"sha": "HEAD"}, {"base": "HEAD", "head": "HEAD"}, {}, None, {"path": "x", "offset": -1}, ["not", "a", "dict"],
    "garbage",
])
def test_removed_tools_say_why_and_never_raise(tool, arguments):
    from conftest import CASES

    toolkit = _toolkit(CASES[0])
    result = toolkit.call(tool, arguments)
    assert isinstance(result, NotAvailableResult)
    assert result.reason == LOG_ONLY_REASON and tool in result.what
    call = toolkit.calls[-1]
    assert (call.tool, call.available, call.error) == (tool, False, None)
    assert LOG_ONLY_REASON in toolkit.call_json(tool, arguments)


def test_full_mode_is_unchanged(case):
    source = ReplaySource(case, LAB)
    toolkit = make_toolkit(source, case.eval_commit, Mode.FULL)
    assert toolkit.unavailable == {} and toolkit.source is source
    assert isinstance(toolkit.call("compare", {"base": source.baseline_commit, "head": "HEAD"}), CompareResult)
