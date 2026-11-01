# D5c: rules baseline v1 in log-only mode

| | |
|---|---|
| predictor | `rules` v1 (`src/ci_triage/predictors/rules.py`, unchanged since `0d80623`), rules-only, no LLM |
| code under test | commit `c374412a164a0daf3c9c4659e1efb3d508333d45` (`c374412`, "feat: D5c log-only mode (pre-registered)") |
| lab | `ci-triage-lab` v1.0, submodule at `2b362bc7d69862b534cfd102ed5dc9c9e297dccc`, 60 cases (10 per category) |
| date | 2026-11-01, run started 16:38:15 UTC |
| command | `python -m ci_triage.evaluation.run --predictor rules --mode log-only` (from the repo root, in `.venv`) |
| full-mode reference | `reports/d5b-rules-v1.md` (58/60). After D5c's grader additions, full mode was re-run and gave the same numbers: every D5b field of every case was equal, and `summary.md` only gained the two new rows. |

**Log-only mode** (registered in `c374412`, before this run): `get_commit` and `compare` return "not available
in log-only mode", and `get_file` reads only the eval commit. Runs, jobs, logs, `extract_failure_block`, files at
the eval commit and run history stay available. It is the headline condition for D6. The rules were not changed
for it, so this run measures how v1 degrades when no diff is available.

**Note on how the rules were written** (as in D5b): the rules were written from general CI knowledge, after
reading the lab app's workflow file `.github/workflows/ci.yml`, to learn its job and step names. An agent can
fetch that file with `get_file`, including in log-only mode. No labels, injection records or ground truth were
read before D5b's first score.

## Headline

| metric | log-only | full (D5b) |
|---|---|---|
| category accuracy | **39/60 (65.0%)** | 58/60 (96.7%) |
| macro-F1 | **0.593** | 0.966 |
| failing_job correct | 60/60 (100%) | 60/60 (100%) |
| failing_step correct | 60/60 (100%) | 60/60 (100%) |
| suspect_file correct | 0/60 (0%) | 60/60 (100%) |
| grounding, cases with every quote grounded | 60/60 (100%) | 60/60 (100%) |
| grounding, quotes | 180/180 (100%) | 180/180 (100%) |
| fix match exact / partial / none | 0 / 0 / 60 | 60 / 0 / 0 |
| action consistent with true category | 39/60 (65.0%) | 58/60 (96.7%) |
| tool calls per case | mean 6.03 (min 6, max 7) | mean 6.18 (min 6, max 10) |
| Brier score | 0.1270 | 0.0804 |
| predictor errors | 0 | 0 |
| wall time per case (recorded by the grader) | mean 0.028 s, max 0.133 s | mean 0.286 s, max 1.233 s |
| runtime, whole command | 16.2 s | 31.5 s (D5b) |

Failing job is now scored by the grader. In D5b it was computed after the run, with the same 60/60. Full-mode
wall time comes from the D5c re-run, because D5b had no per-case timing.

In log-only mode each case made one `get_commit` and one `compare` call; all 120 were refused with "not available
in log-only mode", and no tool raised. The two `get_file` calls (rule 2's `timeout-minutes` check, at the eval
commit) were served. Without a diff the rules name no suspect file and suggest no fix, so localisation and fix
match drop to zero by construction.

## Per category

| category | cases | predicted | precision | recall | F1 |
|---|---|---|---|---|---|
| real_regression | 10 | 31 | 0.32 | 1.00 | 0.49 |
| flaky | 10 | 0 | 0.00 | 0.00 | 0.00 |
| dependency | 10 | 8 | 1.00 | 0.80 | 0.89 |
| environment | 10 | 1 | 1.00 | 0.10 | 0.18 |
| timeout | 10 | 10 | 1.00 | 1.00 | 1.00 |
| build | 10 | 10 | 1.00 | 1.00 | 1.00 |

## Confusion matrix

Rows: true category. Columns: predicted.

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 10 | 0 | 0 | 0 | 0 | 0 |
| dependency | 2 | 0 | 8 | 0 | 0 | 0 |
| environment | 9 | 0 | 0 | 1 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 0 | 0 | 0 | 0 | 0 | 10 |

## Which rules fired

| rule | fired | correct | full mode (D5b) |
|---|---|---|---|
| 1 same code, different result | 0 | — | 5 / 5 |
| 2 time limit | 10 | 10 | 10 / 10 |
| 3 Docker build | 10 | 10 | 10 / 10 |
| 4 dependency install step | 8 | 8 | 8 / 8 |
| 5 environment setup step | 1 | 1 | 1 / 1 |
| 6a–6e what changed | 0 | — | 25 / 26 |
| 7 fallback (real_regression, 0.30) | 31 | 10 | 0 / 1 |

Rules 2–5 read only jobs, steps and logs, and fired exactly as in full mode. Rule 1 needs `get_commit` to prove
the eval commit's diff is empty, and rule 6 needs `compare`; neither fired. Every case those two rules handled in
full mode fell through to the fallback. The fallback's `real_regression` is right for the 10 real regressions,
but only at 0.30 confidence.

## Misclassified cases (21)

All 21 went to rule 7. The reason is always the same: none of rules 1–5 matched, and rule 6 had no changed files,
because `compare` was refused. The "why" column says what would have decided the case.

| case | true | predicted | rule | why it fired | full-mode rule |
|---|---|---|---|---|---|
| inj-049 | flaky | real_regression | 7 | A test fails in "Run smoke tests". The nondeterministic test change that rule 6d needs is visible only in a diff. | 6d |
| inj-050 | flaky | real_regression | 7 | An empty rerun commit. An earlier green run is listed, but `get_commit` is refused, so the rules can't tell it is the same code. | 1 |
| inj-051 | flaky | real_regression | 7 | A test fails in "Run smoke tests". No diff to inspect; this case was wrong in full mode too. | 7 |
| inj-052 | flaky | real_regression | 7 | An empty rerun commit; rule 1 needs `get_commit`, which is refused. | 1 |
| inj-053 | flaky | real_regression | 7 | A test fails in "Run smoke tests". The nondeterministic test change is visible only in a diff. | 6d |
| inj-054 | flaky | real_regression | 7 | An empty rerun commit; rule 1 needs `get_commit`, which is refused. | 1 |
| inj-055 | flaky | real_regression | 7 | An empty rerun commit; rule 1 needs `get_commit`, which is refused. | 1 |
| inj-056 | flaky | real_regression | 7 | A test fails in "Run smoke tests". The nondeterministic test change is visible only in a diff. | 6d |
| inj-057 | flaky | real_regression | 7 | A test fails in "Run smoke tests". The nondeterminism is in application code, unseen without a diff; wrong in full mode too. | 6e |
| inj-058 | flaky | real_regression | 7 | An empty rerun commit; rule 1 needs `get_commit`, which is refused. | 1 |
| inj-059 | dependency | real_regression | 7 | The install succeeds and "App imports on a clean machine" fails, which isn't an install step (rule 4). The `requirements.txt` change is visible only in a diff. | 6a |
| inj-060 | dependency | real_regression | 7 | As inj-059: the import step fails after a clean install, and the `requirements.txt` change is unseen. | 6a |
| inj-029 | environment | real_regression | 7 | "App imports on a clean machine" fails, which isn't a setup step (rule 5). The `ci.yml` change is visible only in a diff. | 6b |
| inj-030 | environment | real_regression | 7 | As inj-029: the import step fails, and the `ci.yml` change is unseen. | 6b |
| inj-031 | environment | real_regression | 7 | As inj-029: the import step fails, and the `ci.yml` change is unseen. | 6b |
| inj-032 | environment | real_regression | 7 | As inj-029: the import step fails, and the `ci.yml` change is unseen. | 6b |
| inj-033 | environment | real_regression | 7 | "Run smoke tests" fails, and the `ci.yml` change is visible only in a diff. | 6b |
| inj-034 | environment | real_regression | 7 | "Run smoke tests" fails, and the `ci.yml` change is visible only in a diff. | 6b |
| inj-035 | environment | real_regression | 7 | "Run smoke tests" fails, and the `pytest.ini` change is visible only in a diff. | 6b |
| inj-036 | environment | real_regression | 7 | "Run smoke tests" fails, and the `pytest.ini` change is visible only in a diff. | 6b |
| inj-037 | environment | real_regression | 7 | "Run smoke tests" fails, and the `pytest.ini` change is visible only in a diff. | 6b |

## Reading the result

- **A floor for D6.** Log-only removes what made full mode near ceiling: suspect file and fix drop from 100% to
  0%, and accuracy from 96.7% to 65.0%. The categories the rules still get right (timeout, build, 8 of 10
  dependency) are decided by the failing job, step and log alone. Those are the ones a log-only agent should
  match, at least.
- **The gap is flaky (0/10) and environment (1/10).** Both are decidable in principle from what log-only still
  shows, but not by v1's rules:
  - for flaky, the run history: rerun cases have an earlier green run of the same code, on a different sha;
  - for environment, the logs and the eval-commit files: the error text, and the eval commit's `ci.yml` and
    `pytest.ini`, which `get_file` serves.
- **Fallback confidence.** All 31 fallback predictions carry 0.30 confidence. Each of the 21 wrong ones costs
  only 0.09, so the Brier score rises only to 0.127 while accuracy falls by 32 points. Most of that 0.127 comes
  from the 10 correct fallbacks: each costs 0.49 for being right at 0.30.
- **Small sample.** There are 10 cases per category, so each case moves recall by 0.10.

Per-case results are in `results/rules-log-only/` (gitignored run output): `cases.jsonl` holds each diagnosis,
its grades, the tool call log and the wall time; `summary.md` holds the grader's summary.

<details>
<summary>All 60 cases</summary>

| case | true | predicted | rule | confidence | correct | tool calls | full-mode rule |
|---|---|---|---|---|---|---|---|
| inj-001 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-002 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-003 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-004 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-005 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-006 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-007 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-008 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-009 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-010 | real_regression | real_regression | 7 | 0.30 | yes | 6 | 6e |
| inj-049 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 6d |
| inj-050 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 1 |
| inj-051 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 7 |
| inj-052 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 1 |
| inj-053 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 6d |
| inj-054 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 1 |
| inj-055 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 1 |
| inj-056 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 6d |
| inj-057 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 6e |
| inj-058 | flaky | real_regression | 7 | 0.30 | **no** | 6 | 1 |
| inj-011 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-012 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-013 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-014 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-015 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-016 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-017 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-018 | dependency | dependency | 4 | 0.80 | yes | 6 | 4 |
| inj-059 | dependency | real_regression | 7 | 0.30 | **no** | 6 | 6a |
| inj-060 | dependency | real_regression | 7 | 0.30 | **no** | 6 | 6a |
| inj-029 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-030 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-031 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-032 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-033 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-034 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-035 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-036 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-037 | environment | real_regression | 7 | 0.30 | **no** | 6 | 6b |
| inj-038 | environment | environment | 5 | 0.75 | yes | 6 | 5 |
| inj-039 | timeout | timeout | 2 | 0.85 | yes | 7 | 2 |
| inj-040 | timeout | timeout | 2 | 0.85 | yes | 7 | 2 |
| inj-041 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-042 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-043 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-044 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-045 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-046 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-047 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-048 | timeout | timeout | 2 | 0.85 | yes | 6 | 2 |
| inj-019 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-020 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-021 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-022 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-023 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-024 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-025 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-026 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-027 | build | build | 3 | 0.85 | yes | 6 | 3 |
| inj-028 | build | build | 3 | 0.85 | yes | 6 | 3 |

</details>

## Not applied

Recorded for a later, separately pre-registered version. **Nothing here was applied**, and v1 stays as scored.

1. **A log-only flaky rule from run history.** If an earlier run of the same workflow passed and nothing in the
   failure says otherwise, lean flaky. Run trees aren't exposed, so this is a weaker signal than rule 1's.
2. **Read the eval commit's config in log-only mode.** `get_file` serves `ci.yml` and `pytest.ini` at the eval
   commit. Rules could flag known-bad settings (an unparsable env value, an unknown pytest option) without a
   diff.
3. **Classify failures in the import step by the error text.** For example, `ModuleNotFoundError` or an
   `ImportError` naming a third-party package → dependency, and a config/env parse error → environment. This
   targets the 6 cases failing at "App imports on a clean machine".
4. **Lower confidence for a mode-blind fallback.** With no diff, rule 7 is right 10 of 31 times (≈0.32), close to
   its fixed 0.30, so v1's fallback confidence happens to be about calibrated here. A per-mode confidence would
   make that deliberate.
