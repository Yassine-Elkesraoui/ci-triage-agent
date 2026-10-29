"""The tools: registry, every tool on every case, pagination, size caps, never raising, call records."""

import json

import pytest
from tooling import exercise_everything, failed_job_id, numbered_lines, read_all

from ci_triage.github_source import NotAvailable
from ci_triage.tools import MAX_OUTPUT_CHARS, TOOLS, Toolkit, render, tool_definitions
from ci_triage.tools.models import NotAvailableResult, ToolOutput
from ci_triage.tools.text import MAX_LINE_CHARS, clip, log_lines, split_lines

TOOL_NAMES = {"get_run", "get_jobs", "get_job_log", "extract_failure_block", "get_commit", "compare", "get_file",
              "list_runs"}


@pytest.fixture
def toolkit(source):
    return Toolkit(source)


def _ok(result):
    assert isinstance(result, ToolOutput), result
    assert result.available is True
    return result


# -- registry -------------------------------------------------------------------


def test_registry_lists_every_tool():
    definitions = tool_definitions()
    assert {d["name"] for d in definitions} == TOOL_NAMES == set(TOOLS)
    for d in definitions:
        assert set(d) == {"name", "description", "input_schema"}
        assert d["description"] and "\n" not in d["description"] and len(d["description"]) <= 140
        schema = d["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        for name, prop in schema["properties"].items():
            assert prop.get("description"), f"{d['name']}.{name} has no description"
        json.dumps(schema)


# -- every tool, every case -----------------------------------------------------


def test_get_run(toolkit, case):
    run = _ok(toolkit.call("get_run", {"run_id": case.eval_run_id}))
    assert run.id == case.eval_run_id and run.head_sha == case.eval_commit
    assert run.status == "completed" and run.conclusion in ("failure", "cancelled")
    assert run.duration_s is not None and run.duration_s > 0
    assert toolkit.call("get_run", {"run_id": str(case.eval_run_id)}).id == case.eval_run_id


def test_get_jobs_with_step_durations(toolkit, case):
    jobs = _ok(toolkit.call("get_jobs", {"run_id": case.eval_run_id}))
    assert jobs.total_count == len(jobs.jobs) > 0 and jobs.note is None
    failed = [job for job in jobs.jobs if job.conclusion in ("failure", "cancelled")]
    assert len(failed) == 1 and failed[0].steps
    assert failed[0].duration_s is not None
    finished = [step for step in failed[0].steps if step.conclusion in ("success", "failure")]
    assert finished and all(step.duration_s is not None and step.duration_s >= 0 for step in finished)


def test_get_job_log_pages_cover_the_whole_log(toolkit, source, case):
    job_id = failed_job_id(toolkit, case.eval_run_id)
    expected = [clip(line, MAX_LINE_CHARS) for line in log_lines(source.get_job_log(job_id))]
    pages = read_all(toolkit, "get_job_log", {"job_id": job_id})
    assert len(pages) > 1, "a ~700-line log should not fit in one page"
    got = [pair for page in pages for pair in numbered_lines(_ok(page).content)]
    assert [n for n, _ in got] == list(range(1, len(expected) + 1))
    assert [text for _, text in got] == expected
    assert all(page.total_lines == len(expected) for page in pages)
    assert pages[-1].next_offset is None and pages[0].note and "use offset=" in pages[0].note


def test_get_job_log_options(toolkit, source, case):
    job_id = failed_job_id(toolkit, case.eval_run_id)
    page = _ok(toolkit.call("get_job_log", {"job_id": job_id, "offset": 5, "max_lines": 3, "timestamps": True}))
    assert page.returned_lines == 3 and page.next_offset == 8 and "more remain" in page.note
    raw = split_lines(source.get_job_log(job_id))
    assert [n for n, _ in numbered_lines(page.content)] == [6, 7, 8]
    assert numbered_lines(page.content)[0][1][:28] == raw[5][:28]  # timestamp kept


def test_extract_failure_block(toolkit, case):
    job_id = failed_job_id(toolkit, case.eval_run_id)
    result = _ok(toolkit.call("extract_failure_block", {"job_id": job_id}))
    assert result.blocks and result.log_lines > 0 and result.log_chars > 0
    for block in result.blocks:
        assert block.kinds and 1 <= block.start_line <= block.end_line <= result.log_lines
        numbers = [n for n, _ in numbered_lines("\n".join(l for l in block.text.split("\n") if l != "…"))]
        assert numbers == sorted(numbers) and numbers[0] == block.start_line and numbers[-1] == block.end_line
    assert render(result) == render(toolkit.call("extract_failure_block", {"job_id": job_id})), "deterministic"


def test_get_commit(toolkit, source, case):
    commit = _ok(toolkit.call("get_commit", {"sha": case.eval_commit}))
    assert commit.sha == case.eval_commit and len(commit.parents) == 1 and commit.message == "<redacted>"
    assert _ok(toolkit.call("get_commit", {"sha": "HEAD"})).sha == case.eval_commit
    baseline = _ok(toolkit.call("get_commit", {"sha": source.baseline_commit}))
    assert baseline.files == [] and baseline.diff_note
    assert isinstance(toolkit.call("get_commit", {"sha": source.baseline_commit, "path": "Dockerfile"}),
                      NotAvailableResult)


def test_compare_and_patch_paging(toolkit, source, case):
    args = {"base": source.baseline_commit, "head": case.eval_commit}
    diff = _ok(toolkit.call("compare", args))
    raw = source.compare(source.baseline_commit, case.eval_commit)
    assert diff.base_commit == source.baseline_commit and diff.head_commit == case.eval_commit
    assert [f.filename for f in diff.files] == [f["filename"] for f in raw["files"]] and diff.files
    for f in raw["files"]:
        pages = read_all(toolkit, "compare", {**args, "path": f["filename"]})
        patch = "\n".join(_ok(page).files[0].patch for page in pages)
        assert patch == "\n".join(clip(line, MAX_LINE_CHARS) for line in split_lines(f["patch"]))


def test_get_file_pages_reconstruct_content(toolkit, source, case):
    for path in [".github/workflows/ci.yml", *(f["filename"] for f in source.compare(source.baseline_commit,
                                                                                     case.eval_commit)["files"])]:
        raw = source.get_file(path)
        if isinstance(raw, NotAvailable):  # e.g. a file the case removed
            continue
        pages = read_all(toolkit, "get_file", {"path": path, "max_lines": 40})
        got = [text for page in pages for _, text in numbered_lines(_ok(page).content)]
        assert got == [clip(line, MAX_LINE_CHARS) for line in split_lines(raw["content"])]
    root = _ok(toolkit.call("get_file", {"path": ""}))
    assert root.type == "dir" and {"api", "tests", "Dockerfile"} <= {e.name for e in root.entries}
    two = _ok(toolkit.call("get_file", {"path": "", "max_lines": 2}))
    assert len(two.entries) == 2 and two.next_offset == 2
    assert _ok(toolkit.call("get_file", {"path": "Dockerfile", "ref": source.baseline_commit})).content


def test_list_runs(toolkit, case):
    runs = _ok(toolkit.call("list_runs", {}))
    assert runs.runs[0].id == case.eval_run_id and runs.total_count >= len(runs.runs)
    assert len(_ok(toolkit.call("list_runs", {"limit": 1})).runs) == 1
    assert _ok(toolkit.call("list_runs", {"branch": "dev"})).runs == []


# -- size caps ------------------------------------------------------------------


@pytest.mark.parametrize("cap", [MAX_OUTPUT_CHARS, 1000])
def test_size_cap_holds_for_every_call(source, case, cap):
    toolkit = Toolkit(source, max_chars=cap)
    exercise_everything(toolkit, source, case)
    toolkit.call("get_job_log", {"job_id": failed_job_id(toolkit, case.eval_run_id), "max_lines": 1000})
    assert toolkit.calls
    for call in toolkit.calls:
        assert call.output_chars <= cap, call
        assert call.error is None, call
    assert max(call.output_chars for call in toolkit.calls) > cap * 0.8, "pages should use the room they have"


def test_output_chars_is_what_the_model_reads(toolkit, case):
    text = toolkit.call_json("get_run", {"run_id": case.eval_run_id})
    assert toolkit.calls[-1].output_chars == len(text) and json.loads(text)["id"] == case.eval_run_id


# -- never raise ----------------------------------------------------------------


def test_bad_and_out_of_scope_calls_are_not_available(toolkit, source, case):
    job_id = failed_job_id(toolkit, case.eval_run_id)
    head = {"base": source.baseline_commit, "head": case.eval_commit}
    bad = [
        ("no_such_tool", {}),
        (None, {}),
        ("get_run", None),
        ("get_run", "run 1"),
        ("get_run", {}),
        ("get_run", {"run_id": "not-a-number"}),
        ("get_run", {"run_id": 1, "verbose": True}),
        ("get_run", {"run_id": 1}),
        ("get_jobs", {"run_id": 1}),
        ("get_job_log", {"job_id": 1}),
        ("get_job_log", {"job_id": job_id, "offset": -1}),
        ("get_job_log", {"job_id": job_id, "offset": 10**6}),
        ("get_job_log", {"job_id": job_id, "max_lines": 0}),
        ("extract_failure_block", {"job_id": 1}),
        ("get_commit", {"sha": "0000000"}),
        ("get_commit", {"sha": case.eval_commit, "path": "not/changed.py"}),
        ("compare", {"base": "0000000", "head": "HEAD"}),
        ("compare", {**head, "path": "not/changed.py"}),
        ("get_file", {"path": "does/not/exist.py"}),
        ("get_file", {"path": "../outside"}),
        ("get_file", {"path": "x" * 10_000}),
        ("get_file", {"path": "injections/index.json"}),
        ("get_file", {"path": "Dockerfile", "ref": "deadbeef"}),
        ("get_file", {"path": "Dockerfile", "offset": 10**6}),
        ("list_runs", {"limit": 0}),
    ]
    for name, args in bad:
        result = toolkit.call(name, args)
        assert isinstance(result, NotAvailableResult), (name, args, result)
        assert len(render(result)) <= 1000
    assert len(toolkit.calls) == len(bad) + 1  # and failed_job_id's get_jobs
    assert all(call.error is None and call.available is False for call in toolkit.calls[1:])


class _ExplodingSource:
    def __getattr__(self, name):
        def explode(*args, **kwargs):
            raise RuntimeError("boom")
        return explode


def test_internal_errors_become_not_available_and_are_recorded():
    toolkit = Toolkit(_ExplodingSource())
    for name in TOOLS:
        args = {"run_id": 1, "job_id": 1, "sha": "abc", "base": "a", "head": "b"}
        allowed = TOOLS[name].input_model.model_fields
        result = toolkit.call(name, {k: v for k, v in args.items() if k in allowed})
        assert isinstance(result, NotAvailableResult) and "internal error" in result.reason
        assert "boom" not in render(result)
    assert all(call.error == "RuntimeError: boom" for call in toolkit.calls)


# -- call records ---------------------------------------------------------------


def test_every_call_is_recorded(source, case):
    toolkit = Toolkit(source)
    toolkit.call("list_runs", {"limit": 5})
    toolkit.call("get_run", {"run_id": case.eval_run_id})
    toolkit.call("get_run", {"run_id": 1})
    toolkit.call("nope")
    assert [c.tool for c in toolkit.calls] == ["list_runs", "get_run", "get_run", "nope"]
    assert toolkit.calls[1].arguments == {"run_id": case.eval_run_id}
    assert [c.available for c in toolkit.calls] == [True, True, False, False]
    assert all(c.output_chars > 0 for c in toolkit.calls)
    assert toolkit.call_counts() == {"list_runs": 1, "get_run": 2, "nope": 1}
