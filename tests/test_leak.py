"""LEAK TEST: nothing ReplaySource serves, directly or through the tools, names a case, its label or the lab.

Strings are checked one by one rather than in a json.dumps() of the output,
because JSON escaping would turn 'Revert "inject' into 'Revert \\"inject'.
Matching ignores case.
"""

import json
import re
from functools import lru_cache

from conftest import CASES, LAB, LAB_DIR
from tooling import exercise_everything

from ci_triage.github_source import NotAvailable
from ci_triage.replay import ReplaySource
from ci_triage.replay.redaction import LABEL_MARKERS
from ci_triage.tools import TOOLS, Toolkit

DESCRIPTIONS = sorted(
    json.loads(path.read_text(encoding="utf-8"))["description"]
    for path in (LAB_DIR / "injections" / "specs").glob("inj-*.json")
)
IDENTITY = ("Yassine-Elkesraoui", "ci-triage-lab")
FORBIDDEN = ("inj-0", "inject:", 'Revert "inject', *IDENTITY, *DESCRIPTIONS)
_FORBIDDEN_RE = re.compile("|".join(map(re.escape, sorted(FORBIDDEN, key=len, reverse=True))), re.IGNORECASE)


@lru_cache(maxsize=None)
def _leaks_in(text: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (m.group(), text[max(0, m.start() - 60): m.end() + 60]) for m in _FORBIDDEN_RE.finditer(text)
    )


def _strings(value):
    if isinstance(value, NotAvailable):
        value = value.to_dict()
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def leaks(outputs):
    return [(where, needle, snippet) for where, value in outputs for s in _strings(value) for needle, snippet in _leaks_in(s)]


def _report(found):
    return "\n".join(f"{where}: {needle!r} in ...{snippet!r}..." for where, needle, snippet in found[:30])


def test_forbidden_strings_cover_every_case():
    assert len(DESCRIPTIONS) == 60


def everything_the_target_allows(source, case):
    """Runs, jobs, logs, every in-scope commit, compare, workflow file, touched files, root listing."""
    out = []
    history = source.list_runs(limit=10_000)
    out.append(("list_runs", history))
    for run in history["workflow_runs"]:
        out.append((f"get_run({run['id']})", source.get_run(run["id"])))
        jobs = source.get_jobs(run["id"])
        out.append((f"get_jobs({run['id']})", jobs))
        if not isinstance(jobs, NotAvailable):
            for job in jobs["jobs"]:
                out.append((f"get_job_log({job['id']}) [run {run['id']}]", source.get_job_log(job["id"])))
    for sha in LAB.git.rev_list(f"{source.baseline_commit}..{case.eval_commit}") + (source.baseline_commit,):
        out.append((f"get_commit({sha[:7]})", source.get_commit(sha)))
    diff = source.compare(source.baseline_commit, case.eval_commit)
    out.append(("compare(baseline, eval)", diff))
    out.append(("get_file(workflow)", source.get_file(".github/workflows/ci.yml")))
    out.append(("get_file(root)", source.get_file("")))
    for f in diff["files"]:
        out.append((f"get_file({f['filename']})", source.get_file(f["filename"])))
        out.append((f"get_file({f['filename']}, baseline)", source.get_file(f["filename"], source.baseline_commit)))
    return out


def test_no_label_leaks(source, case):
    found = leaks(everything_the_target_allows(source, case))
    assert not found, f"{len(found)} leak(s):\n{_report(found)}"


def test_every_visible_file_is_clean():
    """get_file on every file in every eval commit's tree (each distinct blob once)."""
    seen, outputs = set(), []
    for case in CASES:
        source = ReplaySource(case, LAB)
        for record in filter(None, LAB.git.text("ls-tree", "-r", "-z", case.eval_commit).split("\0")):
            meta, path = record.split("\t", 1)
            oid = meta.split()[2]
            if path.startswith("injections/"):
                assert isinstance(source.get_file(path), NotAvailable), path
            elif oid not in seen:
                seen.add(oid)
                outputs.append((f"get_file({path}) @ {case.eval_commit[:7]}", source.get_file(path)))
    found = leaks(outputs)
    assert not found, f"{len(found)} leak(s):\n{_report(found)}"


def test_redaction_is_load_bearing(case):
    """The raw lab data does leak, and the detector sees it, so the tests above are not passing vacuously."""
    raw_run = LAB.run(case.eval_run_id)
    assert LABEL_MARKERS.search(raw_run["display_title"])
    found = {needle.lower() for _, needle, _ in leaks([("raw run", raw_run)])}
    assert {"yassine-elkesraoui", "ci-triage-lab"} <= found
    raw_log = LAB.log_text(next(iter(j for j in LAB.log_paths if LAB.run_of_job[j] == case.eval_run_id)))
    assert leaks([("raw log", raw_log)])
    assert not leaks([("redacted run", ReplaySource(case, LAB).get_run(case.eval_run_id))])


def test_no_label_leaks_through_the_tools(source, case):
    """The same sweep through the Toolkit, every page of every log, patch and file included."""
    toolkit = Toolkit(source)
    outputs = [(where, result.model_dump()) for where, result in exercise_everything(toolkit, source, case)]
    assert set(toolkit.call_counts()) == set(TOOLS)
    assert not [call for call in toolkit.calls if call.error]
    found = leaks(outputs)
    assert not found, f"{len(found)} leak(s):\n{_report(found)}"
