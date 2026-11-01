# D5b: rules baseline v1, first official score

| | |
|---|---|
| predictor | `rules` (`src/ci_triage/predictors/rules.py`), rules-only, no LLM |
| code under test | commit `0d806235dfc23b7d72c6d5c35e4c4d74e178f5cb` (`0d80623`, "feat: D5a rules baseline (pre-registered, not yet evaluated)") |
| lab | `ci-triage-lab` v1.0, submodule at `2b362bc7d69862b534cfd102ed5dc9c9e297dccc`, 60 cases (10 per category) |
| date | 2026-11-01, run started 15:37:00 UTC |
| command | `python -m ci_triage.evaluation.run --predictor rules` (from the repo root, in `.venv`) |
| pre-registration | `rules.py`, its tests and the grader were unchanged since `0d80623` and the working tree was clean when the run started. This is the first evaluation of these rules; nothing was tuned on the result. |

**Note on how the rules were written.** The rules were written from general CI knowledge, after reading the
lab app's workflow file `.github/workflows/ci.yml`, to learn its job and step names (`smoke` / "Import & route smoke
tests", `docker` / "Docker image builds", "Install dependencies", `actions/setup-python`, "Build image"). An agent
can fetch that file with `get_file` during triage. No labels, injection records, ground truth or per-case data were
read before the run. The rules match on generic keywords (docker, install, pip, setup-, …), not on those exact
names.

## Rules table (from the `rules.py` docstring at `0d80623`)

```
==  =============================  ===============================================================  =============== ====
#   Rule                           Fires when                                                       Category        Conf
==  =============================  ===============================================================  =============== ====
1   Same code, different result    The eval commit's diff is truly empty (no files, no hidden-file  flaky           0.90
                                   marker) and an earlier run of the same tree passed. The same
                                   tree: the eval commit and the ancestors reached through
                                   truly-empty commits (plus the first non-empty one, whose tree
                                   an empty child shares).
2   Time limit                     The failure block has a "has timed out after" line, or the       timeout         0.85
                                   failing job was cancelled and its duration reaches (>= 95% of)
                                   the job's timeout-minutes in the workflow file at the eval
                                   commit.
3   Docker build                   The failing job is a Docker job (name or a step mentions         build           0.85
                                   docker) and the other jobs, the smoke job among them, passed.
4   Dependency install step        The failing step installs dependencies (install, pip, poetry,    dependency      0.80
                                   requirements, dependencies).
5   Environment setup step         The failing step is setup-python or another environment-setup    environment     0.75
                                   step (actions/setup-*, "Set up ...", checkout, cache).
6a  Changed: requirements          compare(baseline, eval) touches a requirements, constraints,     dependency      0.70
                                   lock or packaging file (pyproject.toml when its changed lines
                                   name dependencies or version pins).
6b  Changed: CI or test config     ... touches a workflow, pytest config (pytest.ini, tox.ini,      environment     0.65
                                   setup.cfg, other pyproject.toml changes) or env settings
                                   (.env*, .python-version, runtime.txt).
6c  Changed: Dockerfile            ... touches a Dockerfile, .dockerignore or compose file.         build           0.70
6d  Changed: nondeterministic      ... touches test code whose added lines use randomness, time,    flaky           0.55
    test                           threads or unordered collections.
6e  Changed: application code      ... touches any other non-doc file.                              real_regression 0.60
7   Fallback                       Nothing above fired.                                             real_regression 0.30
==  =============================  ===============================================================  =============== ====
```

Rule 6 checks 6a to 6e in that order; the first kind present wins. The baseline is the newest successful run
created before the eval run on a commit outside the eval commit's tree.

## Headline

| metric | value |
|---|---|
| category accuracy | **58/60 (96.7%)** |
| macro-F1 | **0.966** |
| failing_job correct | 60/60 (100%)¹ |
| failing_step correct | 60/60 (100%) |
| suspect_file correct | 60/60 (100%) |
| grounding, cases with every quote grounded | 60/60 (100%) |
| grounding, quotes | 180/180 (100%) |
| fix match exact / partial / none | 60 / 0 / 0 (100% exact): 38 "restore" + 22 "remove" cases |
| action consistent with true category | 58/60 (96.7%) |
| tool calls per case | mean 6.18 (min 6, max 10) |
| Brier score (confidence vs category correct) | 0.0804 |
| predictor errors | 0 |
| runtime | 31.5 s wall clock for the whole command (60 cases, including loading lab data and git); ≤ 0.53 s per case |

¹ The grader does not score `failing_job`. Ground truth stores the job's key in `ci.yml` (`smoke`, `docker`); the
Diagnosis holds the display name `get_jobs` shows. The figure above maps each key to its `name:` in the lab's
`ci.yml` and compares exactly; this was computed after the run from `results/rules/cases.jsonl`.

## Per category

| category | cases | predicted | precision | recall | F1 |
|---|---|---|---|---|---|
| real_regression | 10 | 12 | 0.83 | 1.00 | 0.91 |
| flaky | 10 | 8 | 1.00 | 0.80 | 0.89 |
| dependency | 10 | 10 | 1.00 | 1.00 | 1.00 |
| environment | 10 | 10 | 1.00 | 1.00 | 1.00 |
| timeout | 10 | 10 | 1.00 | 1.00 | 1.00 |
| build | 10 | 10 | 1.00 | 1.00 | 1.00 |

## Confusion matrix

Rows: true category. Columns: predicted.

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 2 | 8 | 0 | 0 | 0 | 0 |
| dependency | 0 | 0 | 10 | 0 | 0 | 0 |
| environment | 0 | 0 | 0 | 10 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 0 | 0 | 0 | 0 | 0 | 10 |

## Which rules fired

| rule | fired | correct | true categories |
|---|---|---|---|
| 1 same code, different result | 5 | 5 | flaky ×5 (the five empty-rerun cases) |
| 2 time limit | 10 | 10 | timeout ×10 (8 by the "has timed out after" line, 2 by cancelled-at-limit; failing steps: smoke tests ×6, install ×2, build ×2) |
| 3 Docker build | 10 | 10 | build ×10 |
| 4 dependency install step | 8 | 8 | dependency ×8 |
| 5 environment setup step | 1 | 1 | environment ×1 |
| 6a requirements changed | 2 | 2 | dependency ×2 |
| 6b CI / test config changed | 9 | 9 | environment ×9 |
| 6c Dockerfile changed | 0 | — | — |
| 6d nondeterministic test changed | 3 | 3 | flaky ×3 |
| 6e application code changed | 11 | 10 | real_regression ×10, flaky ×1 |
| 7 fallback | 1 | 0 | flaky ×1 |

Rule order mattered for timeouts. Four timeouts happened in the install or build step, and rule 2 ran before
rules 3 and 4 claimed them.

## Misclassified cases

| case | true | predicted | rule | why it fired |
|---|---|---|---|---|
| inj-051 | flaky | real_regression | 7 (fallback) | The eval commit is the injection, so rule 1 cannot apply. The only change is in `tests/test_behaviour.py`: a set literal `{"hi", "hello"}` read with `next(iter(...))`, which depends on the hash seed. Rule 6d's unordered-collection patterns match `set(`, not a set literal, so no rule-6 kind matched and the fallback fired. |
| inj-057 | flaky | real_regression | 6e (application code) | The eval commit is the injection. The only change is in `api/services/chunking_service.py`, which masks the random `uuid4`'s bits (`UUID(int=document_id.int & ...)`), so the result depends on the random id. Rule 6d only looks for nondeterminism in test code, so the change counts as application code. |

In both cases every other field was right: failing job and step, suspect file (the target file), grounded
evidence, and an exact fix match. Only the category, and with it the recommended action, was wrong.

## Reading the result

- **The benchmark is easy on localisation and fix.** Every case is a single injection, and
  `compare(baseline, eval)` shows exactly the injected change, with nothing unrelated mixed in. So
  "suspect = the changed file" and "fix = revert the diff" give 100% for suspect_file and fix match. That measures
  the benchmark's structure more than the predictor's skill. A real repository's baseline-to-failure diff often
  touches several unrelated files.
- **Job topology carries much of build.** In the lab's `ci.yml`, `docker` runs only after `smoke` passes, so "the
  Docker job failed" nearly implies `build` once timeouts are excluded. Rule 3 scored 10/10 partly for that reason.
- **Flaky without a rerun is the weak spot.** Rule 1 scored 5/5 on the empty-rerun cases. The other five flaky
  cases depend on spotting nondeterminism in a diff, and that got 3/5.
- **Confidence.** Every rule that fired was right every time, except 6e (10/11) and 7 (0/1). The fixed
  confidences (0.30–0.90) are therefore mostly too low. The Brier score of 0.080 comes mainly from correct cases
  at 0.55–0.65 confidence, not from the two errors.
- **Small sample.** There are 10 cases per category, so each case moves recall by 0.10.

Per-case results are in `results/rules/` (gitignored run output): `cases.jsonl` holds each diagnosis, its grades
and the tool call log; `summary.md` holds the grader's summary.

<details>
<summary>All 60 cases</summary>

| case | true | predicted | rule | confidence | correct | tool calls |
|---|---|---|---|---|---|---|
| inj-001 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-002 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-003 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-004 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-005 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-006 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-007 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-008 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-009 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-010 | real_regression | real_regression | 6e | 0.60 | yes | 6 |
| inj-049 | flaky | flaky | 6d | 0.55 | yes | 6 |
| inj-050 | flaky | flaky | 1 | 0.90 | yes | 7 |
| inj-051 | flaky | real_regression | 7 | 0.30 | **no** | 6 |
| inj-052 | flaky | flaky | 1 | 0.90 | yes | 7 |
| inj-053 | flaky | flaky | 6d | 0.55 | yes | 6 |
| inj-054 | flaky | flaky | 1 | 0.90 | yes | 8 |
| inj-055 | flaky | flaky | 1 | 0.90 | yes | 10 |
| inj-056 | flaky | flaky | 6d | 0.55 | yes | 6 |
| inj-057 | flaky | real_regression | 6e | 0.60 | **no** | 6 |
| inj-058 | flaky | flaky | 1 | 0.90 | yes | 7 |
| inj-011 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-012 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-013 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-014 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-015 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-016 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-017 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-018 | dependency | dependency | 4 | 0.80 | yes | 6 |
| inj-059 | dependency | dependency | 6a | 0.70 | yes | 6 |
| inj-060 | dependency | dependency | 6a | 0.70 | yes | 6 |
| inj-029 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-030 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-031 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-032 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-033 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-034 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-035 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-036 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-037 | environment | environment | 6b | 0.65 | yes | 6 |
| inj-038 | environment | environment | 5 | 0.75 | yes | 6 |
| inj-039 | timeout | timeout | 2 | 0.85 | yes | 7 |
| inj-040 | timeout | timeout | 2 | 0.85 | yes | 7 |
| inj-041 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-042 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-043 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-044 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-045 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-046 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-047 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-048 | timeout | timeout | 2 | 0.85 | yes | 6 |
| inj-019 | build | build | 3 | 0.85 | yes | 6 |
| inj-020 | build | build | 3 | 0.85 | yes | 6 |
| inj-021 | build | build | 3 | 0.85 | yes | 6 |
| inj-022 | build | build | 3 | 0.85 | yes | 6 |
| inj-023 | build | build | 3 | 0.85 | yes | 6 |
| inj-024 | build | build | 3 | 0.85 | yes | 6 |
| inj-025 | build | build | 3 | 0.85 | yes | 6 |
| inj-026 | build | build | 3 | 0.85 | yes | 6 |
| inj-027 | build | build | 3 | 0.85 | yes | 6 |
| inj-028 | build | build | 3 | 0.85 | yes | 6 |

</details>

## Not applied

Ideas for a v2, recorded here and **not applied**. Any of them would be a new, separately pre-registered version
scored as v2. They come from looking at these 60 cases, so a v2 score is no longer a blind first score.

1. **Set literals and iteration order in rule 6d.** Match `{a, b}` literals, `next(iter(...))` and iteration over a
   set or dict, not only `set(`. This would catch inj-051.
2. **Nondeterminism in application code.** Apply the 6d patterns (`uuid4`/`UUID(`, `random`, time, threads) to
   application code too, as flaky with lower confidence when the failing test doesn't consistently point at the
   code. This would catch inj-057, but risks real regressions that touch `uuid`.
3. **Recalibrate confidences.** The fixed confidences are too low for rules 1–6b. Fitting them per rule (on a
   held-out split, or by leave-one-out) would lower the Brier score. With 60 cases this risks overfitting.
4. **Have rule 1 use rerun history beyond empty commits.** For example, the same test failing and passing across
   runs of commits whose tree differs only in hidden files.
5. **Weaken the shortcuts that depend on topology and single-file diffs** before using this as a baseline for the
   LLM agent. For example: report results on a variant where the baseline-to-eval diff includes unrelated
   commits, or where Docker builds don't depend on smoke. That would show how much of the 100% localisation and
   fix match survives.
6. **Score `failing_job` in the grader** by mapping workflow job keys to display names, or by storing display names
   in ground truth, so it's no longer computed post hoc.
7. **Record per-case wall time in `cases.jsonl`**, so runtime is per case rather than one figure for the whole
   command.
