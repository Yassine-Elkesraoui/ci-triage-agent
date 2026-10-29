# ci-triage-agent

An agent that triages failing GitHub Actions runs. It is developed and evaluated
offline against [ci-triage-lab](https://github.com/Yassine-Elkesraoui/ci-triage-lab),
pinned at v1.0 as the `lab/` submodule: 60 labelled CI failures in 6 categories.

Status: milestones D1 (scaffold), D2 (replay layer) and D3 (tools). No LLM, no agent, no network.

## Setup

```bash
git submodule update --init          # the replay layer needs lab/'s full git history
python -m venv .venv
.venv/Scripts/activate               # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Layout

```
src/ci_triage/
  github_source.py      GitHubSource protocol, NotAvailable, TriageTarget
  replay/               ReplaySource: offline GitHub served from lab/ data, labels hidden
    cases.py            the 60 cases from lab/injections/index.json (triage targets only)
    labdata.py          API snapshots, saved logs, job -> log index
    redaction.py        what is hidden or rewritten, and why
    source.py           ReplaySource
  tools/                read-only tools over any GitHubSource, typed and size-capped
    models.py           Pydantic inputs (the LLM's tool schemas) and outputs
    github_tools.py     the eight tools and their registry
    failure_block.py    deterministic evidence extraction from a failed job's log
    text.py             log cleaning, line numbering, fitting output into the cap
    toolkit.py          Toolkit: runs tools by name, never raises, records every call
  evaluation/
    ground_truth.py     category, target file, mutation, revert commit (evaluation only)
    key_evidence.py     the evidence each case's failure block must contain
    failure_block_report.py  coverage and size of extract_failure_block per category
tests/
```

Neither `ReplaySource` nor the tools import `ci_triage.evaluation`; a test enforces it.

## Benchmark choices in the replay layer

- **Case scope.** The lab ran its cases back to back on one branch, grouped by
  category, so the real history around a case is mostly other cases of the same
  kind, a category prior no real repository offers. Each case therefore sees only
  its own context: the last green run on main before its injection commit (run and
  jobs; that commit's metadata and files but not its diff), its own commits and
  runs up to the eval run (for flaky cases, the earlier reruns of the same code),
  and `compare(baseline, eval)`. Everything else is "not available in this case's
  scope".
- **No time travel.** Nothing created after the eval run finished is visible.
- **Redaction.** Commit messages and run titles are `<redacted>`; the lab's own
  `injections/` directory is hidden, and a diff that lost files to it says
  "N files outside the application under test are hidden in this benchmark".
- **Neutral identity.** The lab's owner and repository appear as `example-org/app`.

## Tools

`Toolkit(source).call(name, arguments)` runs one of eight read-only tools:
`list_runs`, `get_run`, `get_jobs`, `extract_failure_block`, `get_job_log`,
`get_commit`, `compare`, `get_file`. `tool_definitions()` gives each one's name,
one-line description and input JSON schema.

- **Typed.** Inputs and outputs are Pydantic models; inputs reject unknown fields.
- **Capped.** Every result, rendered as the JSON a model reads, is at most 4,000
  characters. Logs, files and patches come in numbered pages with
  "use offset=N to continue"; very long lines are clipped.
- **Never raises.** Invalid arguments, out-of-scope ids and internal errors all
  return `{"available": false, "what": ..., "reason": ...}`.
- **Recorded.** `toolkit.calls` holds every call's tool, arguments and output size.

`extract_failure_block` finds pytest summaries and assertions, Python
tracebacks' final exceptions, `##[error]` lines (timeouts, cancellations,
setup-python), pip and other `ERROR:` lines, and BuildKit's `failed to solve`
with its Dockerfile excerpt, then returns them most important first with context
trimmed to fit. `python -m ci_triage.evaluation.failure_block_report` prints how
often its output holds each case's key evidence (the failing test, else the
decisive error line).
