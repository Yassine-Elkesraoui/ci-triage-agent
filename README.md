# ci-triage-agent

An agent that triages failing GitHub Actions runs. It is developed and evaluated
offline against [ci-triage-lab](https://github.com/Yassine-Elkesraoui/ci-triage-lab),
pinned at v1.0 as the `lab/` submodule: 60 labelled CI failures in 6 categories.

Status: milestones D1 (scaffold), D2 (replay layer), D3 (tools), D4 (diagnosis format and evaluation
harness), D5a (a rules-only baseline, committed before its first evaluation), D5b (its first score, in
`reports/`) and D5c (the log-only evaluation mode). No LLM, no agent, no network.

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
  diagnosis.py          Diagnosis: what a predictor returns (category, step, file, evidence, fix, action)
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
    toolkit.py          Toolkit: runs tools by name, never raises, records every call and its output
    modes.py            evaluation modes: full, and log-only (no diffs, files only at the eval commit)
  predictors/           predict(CaseView, Toolkit) -> Diagnosis; constant and random baselines
    rules.py            rules-only baseline (no LLM): its decision table is in the module docstring
  evaluation/           the only code that reads ground truth
    ground_truth.py     category, target file, mutation, revert commit (evaluation only)
    key_evidence.py     the evidence each case's failure block must contain
    failure_block_report.py  coverage and size of extract_failure_block per category
    grader.py           grades one Diagnosis against ground truth and the case's tool call log
    metrics.py          summary metrics and the Markdown report
    run.py              runs a predictor over the 60 cases in a mode, writes results/<name>[-log-only]/
reports/                official scores, committed (results/ is gitignored run output)
tests/
  oracle.py             the oracle predictor: reads ground truth, so it lives here only
```

Neither `ReplaySource`, the tools nor the predictors import `ci_triage.evaluation`; a test enforces it.

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

## Evaluation

```bash
python -m ci_triage.evaluation.run --predictor constant      # or random, rules
python -m ci_triage.evaluation.run --predictor rules --mode log-only
PYTHONPATH=tests python -m ci_triage.evaluation.run --predictor oracle:OraclePredictor
```

writes `results/<name>/cases.jsonl` (per case: the diagnosis, its grades, the tool call log and the
predictor's wall time) and `results/<name>/summary.md`; `--mode log-only` writes to
`results/<name>-log-only/`. A predictor gets a `CaseView` (the run id only) and a fresh `Toolkit` per
case, built for the mode. Per case the grader checks:

- **category**, **failing job** and **failing step** (as `get_jobs` names them, case and spacing ignored)
  and **suspect file** (the injection's target file);
- **grounding**: each evidence quote must appear verbatim in a result the toolkit returned for that case,
  from a tool of the quote's declared source (log: `get_job_log`, `extract_failure_block`; diff: `get_commit`,
  `compare`; file: `get_file`; history: `list_runs`, `get_run`, `get_jobs`, `get_commit`). Line-number
  prefixes are ignored, so a multi-line quote can match;
- **fix match**: whether `suggested_fix` contains the lines the revert restored, whitespace normalised:
  exact (all of them), partial (one line of 8+ characters, or every token the revert brought back), none.
  22 reverts only delete what the injection added (a `timeout-minutes` line, an env block, a sleep); there
  the deleted lines are what the fix must name;
- **action**: the one recommended action that fits the true category (`ACTION_FOR` in `diagnosis.py`);
- **tool calls** made.

The summary adds accuracy, macro-F1, per-category scores, the confusion matrix, the Brier score of
`confidence` against category correctness, and mean and max wall time per case. Sanity predictors pin
the grader: the oracle scores 100% on every metric, constant (always `real_regression`) exactly 10/60,
random (seed 0) 9/60.

### Evaluation modes

- **full** (default): every tool, within the case scope above.
- **log-only**: the toolkit does not reveal what changed. `get_commit` and `compare`, the commit-diff
  and patch tools, return `"not available in log-only mode"` and are left out of `tool_definitions()`.
  `get_file` reads only the commit under triage: `ref` omitted, `HEAD`, or its sha; any other ref,
  including the baseline and parents, is refused. Runs, jobs, logs, `extract_failure_block`, files at the
  eval commit and run history stay. It is enforced twice, by the toolkit and by `LogOnlySource`
  (`tools/modes.py`). `tests/test_log_only.py` drives every remaining tool over every case, and checks
  that no output holds a diff field, diff text or a line the injection removed.

Why log-only exists: real triage often has no usable diff. The PR is large and touches many files, the
failure is on a scheduled run, or the alert arrives with no access to the change. In full mode, the
lab's `compare(baseline, eval)` shows exactly one injected change, which makes the suspect file and the
fix nearly free. It was designed after D5b showed full mode is near ceiling: the rules baseline scores
58/60, with 100% localisation and exact fix match (`reports/d5b-rules-v1.md`). So **log-only is the
headline comparison for D6**, the LLM agent. That choice is registered here, in D5c, before any LLM
run. Full-mode results are still reported alongside.
