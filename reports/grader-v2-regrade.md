# Grader v2: re-grade of the D5 and D6a runs

| | |
|---|---|
| grader | v2, commit `c4adf0ef00d40deccfc5a71a09217cd4dc5a8658` (`c4adf0e`, "feat: grader v2 (normalised grounding, quote-rule metric, remove fixes to judge)"). Grader v1 is unchanged and still selectable: `--grader v1` on both runners. |
| runs re-graded | rules v1 full (D5b) and log-only (D5c); one-call v2 on Gemini and on Ollama, log-only and full (D6a). 60 cases each; every case of every run was re-graded. |
| method | `python -m ci_triage.evaluation.regrade`. Each saved case's recorded tool calls are replayed offline on a fresh toolkit for the run's mode; the replay source is deterministic, and every replayed call matched the saved one in size and availability. The saved diagnosis is then graded with v1 and with v2. One-call cases are graded against the sections of the bundle saved with them. **No predictor and no model was called.** |
| check | Grader v1 reproduces the saved grades of all 360 cases exactly. For the two rules runs, it also reproduces the saved `summary.md` byte for byte. |
| lab | `ci-triage-lab` v1.0, submodule at `2b362bc7d69862b534cfd102ed5dc9c9e297dccc` |
| date | 2026-11-09, re-grade run at 16:30 UTC from the clean tree at `c4adf0e` |

The D5b, D5c and D6a reports stay as published: they are grader v1 scores. This report adds the v2 view
of the same diagnoses.

## What v2 changes

General rules only; no case id appears anywhere in the grader.

1. **Grounding.** Quote and source text both go through the same normalisation before matching. Per line,
   it strips the `<n>| ` line-number prefix (whatever the number), the indentation, and one diff gutter
   marker (`+`/`-`; the context gutter is a leading space). Then it collapses whitespace with fix match's
   `normalise`. A quote is grounded if its normalised text is in the section it cites, or in any section
   (the label is then wrong).
   - Sections are what the predictor showed the model. For one-call, that is the bundle's sections. For
     tool-calling predictors such as rules, it is the outputs of each source's tools.
2. **Quote rules followed**, a new metric that does not affect grounding. It counts, per quote:
   - a wrong source label;
   - a quoted header line the predictor formatted (section, failure-block or diff-file header, or a
     truncation marker);
   - a history (RUN AND JOBS) quote that is more than a job or step name;
   - a kept line-number prefix;
   - an ellipsis the source text does not have.
3. **Fix match.** Unchanged for restore-type cases (38). Remove-type cases (22) get `needs_judge` and are
   left out of the fix-match rates, which are now over the 38 restore-type cases.

## Category metrics are unchanged

Category, confidence, failing job, failing step, suspect file, action and fix kind are identical under v1
and v2 for every case of every run (tested). The same holds for the summary's accuracy, macro-F1,
job/step/localisation rates, Brier score, confusion matrix and per-category precision, recall and F1.

| run | category accuracy (v1 = v2) | macro-F1 (v1 = v2) |
|---|---|---|
| rules v1, full (D5b) | 58/60 (96.7%) | 0.966 |
| rules v1, log-only (D5c) | 39/60 (65.0%) | 0.593 |
| one-call v2 Gemini, log-only | 49/60 (81.7%) | 0.783 |
| one-call v2 Gemini, full | 50/60 (83.3%) | 0.799 |
| one-call v2 Ollama, log-only | 43/60 (71.7%) | 0.683 |
| one-call v2 Ollama, full | 42/60 (70.0%) | 0.664 |

## v1 vs v2, per run

"Fix match" under v1 is over all 60 cases. Under v2 it is over the 38 restore-type cases, and the other 22
need a judge. "No fix" counts the remove-type diagnoses that suggest no fix at all.

**Rules v1, full (D5b)**

| metric | v1 | v2 |
|---|---|---|
| grounding, cases | 60/60 (100%) | 60/60 (100%) |
| grounding, quotes | 180/180 (100%) | 180/180 (100%) |
| quote rules followed, quotes / cases | — | 180/180 (100%) / 60/60 |
| fix match exact / partial / none | 60 / 0 / 0 of 60 (100% exact) | 38 / 0 / 0 of 38 (100% exact) |
| remove-type | scored (22 exact) | 22 need a judge (0 no fix) |

**Rules v1, log-only (D5c)**

| metric | v1 | v2 |
|---|---|---|
| grounding, cases | 60/60 (100%) | 60/60 (100%) |
| grounding, quotes | 180/180 (100%) | 180/180 (100%) |
| quote rules followed, quotes / cases | — | 180/180 (100%) / 60/60 |
| fix match exact / partial / none | 0 / 0 / 60 of 60 (0%) | 0 / 0 / 38 of 38 (0%) |
| remove-type | scored (22 none) | 22 need a judge (22 no fix) |

**One-call v2, Gemini, log-only**

| metric | v1 | v2 |
|---|---|---|
| grounding, cases | 52/60 (86.7%) | **58/60 (96.7%)** |
| grounding, quotes | 122/130 (93.8%) | **128/130 (98.5%)** |
| quote rules followed, quotes / cases | — | 124/130 (95.4%) / 54/60 |
| quote rule breaks | — | wrong source label 3, RUN AND JOBS beyond a name 3 |
| fix match exact / partial / none | 13 / 0 / 47 of 60 (21.7% exact) | 12 / 0 / 26 of 38 (31.6% exact) |
| remove-type | scored (1 exact, 21 none) | 22 need a judge (2 no fix) |

**One-call v2, Gemini, full**

| metric | v1 | v2 |
|---|---|---|
| grounding, cases | 48/60 (80.0%) | **59/60 (98.3%)** |
| grounding, quotes | 138/152 (90.8%) | **151/152 (99.3%)** |
| quote rules followed, quotes / cases | — | 149/152 (98.0%) / 57/60 |
| quote rule breaks | — | wrong source label 1, RUN AND JOBS beyond a name 2 |
| fix match exact / partial / none | 33 / 0 / 27 of 60 (55.0% exact) | 33 / 0 / 5 of 38 (86.8% exact) |
| remove-type | scored (22 none) | 22 need a judge (13 no fix) |

**One-call v2, Ollama, log-only**

| metric | v1 | v2 |
|---|---|---|
| grounding, cases | 33/60 (55.0%) | **52/60 (86.7%)** |
| grounding, quotes | 145/179 (81.0%) | **171/179 (95.5%)** |
| quote rules followed, quotes / cases | — | 152/179 (84.9%) / 36/60 |
| quote rule breaks | — | section header 10, line-number prefix 8, RUN AND JOBS beyond a name 5, wrong source label 4, ellipsis 2 (2 quotes break two rules) |
| fix match exact / partial / none | 5 / 1 / 54 of 60 (8.3% exact) | 5 / 1 / 32 of 38 (13.2% exact) |
| remove-type | scored (22 none) | 22 need a judge (17 no fix) |

**One-call v2, Ollama, full**

| metric | v1 | v2 |
|---|---|---|
| grounding, cases | 36/60 (60.0%) | **53/60 (88.3%)** |
| grounding, quotes | 158/185 (85.4%) | **178/185 (96.2%)** |
| quote rules followed, quotes / cases | — | 166/185 (89.7%) / 45/60 |
| quote rule breaks | — | line-number prefix 9, section header 8, ellipsis 2 |
| fix match exact / partial / none | 32 / 3 / 25 of 60 (53.3% exact) | 30 / 0 / 8 of 38 (78.9% exact) |
| remove-type | scored (2 exact, 3 partial, 17 none) | 22 need a judge (13 no fix) |

**Reading the tables**
- **Grounding gap.** Under v2 the gap between rules (60/60) and the LLM runs narrows to 1–2 cases for Gemini
  and 7–8 for Ollama.
- **Where the quote-rule breaks are.** Gemini's breaks are labels and RUN AND JOBS lines. Ollama's are mostly
  failure-block headers and line-number prefixes. Ollama's 8–9 prefixes include quotes v1 already grounded,
  because their numbers happened to be right. The prompt forbids prefixes either way.
- **Exact-fix rates.** Over restore-type cases they rise for both models, since all remove-type cases are left
  out. The counts change only where v1 had credited a remove-type fix: Gemini log-only 13 → 12, Ollama
  full 32 → 30.

## Where the v1-ungrounded LLM quotes went

The read-only audit (below) classified all 83 quotes that v1 left ungrounded in the four one-call runs.
Under v2, 65 are grounded and 18 are still ungrounded.

| audit class | quotes | grounded by v2 | still ungrounded |
|---|---|---|---|
| (a) invented or paraphrased | 4 | 0 | 4 |
| (b) multi-line, lines dropped or joined | 2 | 0 | 2 |
| (c) whitespace, line-number prefix or `...` only | 29 | 27 | 2 (the `...` elisions) |
| (d) other | 48 | 38 | 10 |
| **total** | **83** | **65** | **18** |

The 10 class-(d) quotes still ungrounded are:

| quotes | what they are |
|---|---|
| 4 | one-character copy slips: a dropped `)`, `"` written as `'`, `-` written as `--- ` |
| 2 | `...` over a line the tool had cut, plus a `)` the model could not see |
| 1 | the middle of a long line dropped without `...` |
| 1 | Dockerfile lines copied without docker's own `NN \| >>>` numbering (cited as `file`) |
| 1 | two RUN AND JOBS lines with the steps between them skipped |
| 1 | a shortened failure-block header (also counted as a section header) |

**The 38 class-(d) quotes v2 now grounds:**
- **24 predictor-formatted lines.** These are 16 failure-block headers, 7 RUN AND JOBS lines and 1 diff file
  header. They are grounded because they are in the section, and each is counted by the quote-rule metric.
- **8 wrong source labels.** Grounded in another section and counted as wrong labels.
- **6 dropped diff gutters.**

Every (a) and (b) quote stays ungrounded: v2 accepts none of them.

## Audit findings (read-only, before v2)

The audit replayed every case's tool calls offline. This reproduced all 240 one-call records: same bundle,
same tool calls, and the same per-case grounded counts as saved. It then read each ungrounded quote line by
line against its source, correcting the automatic class by hand in 15 cases.

**Ungrounded quotes under v1, by run**

| run | ungrounded | (a) | (b) | (c) | (d) |
|---|---|---|---|---|---|
| Gemini log-only | 8 | 1 | 0 | 0 | 7 |
| Gemini full | 14 | 0 | 0 | 6 | 8 |
| Ollama log-only | 34 | 1 | 1 | 12 | 20 |
| Ollama full | 27 | 2 | 1 | 11 | 13 |
| **total** | **83** | **4** | **2** | **29** | **48** |

- **(c): 29 quotes.** 20 re-indented, 3 with a space added after a diff marker, 4 with a line-number prefix off
  by one, and 2 `...` elisions.
- **(d): 48 quotes.**
  - 26 lines the predictor formatted into the bundle, which no tool returned: 17 failure-block headers,
    8 RUN AND JOBS lines and 1 diff file header.
  - 9 wrong source labels.
  - 6 dropped diff gutters.
  - 4 one-character copy slips.
  - 2 `...` over a tool-cut line.
  - 1 mid-line drop.

**Grounding verdict.**
- **Too strict for about a third of failures (29 of 83).** These are re-indentation, an added space after a
  marker, and dropped gutters, all of which are the source text. v1 compared raw characters for grounding but
  normalised whitespace for fix match.
- **Right on 10.** The (a) and (b) quotes and the copy slips really changed the text; one (a) quote states
  something false.
- **Right by the prompt's rules, though not fabrication, on the rest.** The prompt requires quotes "copied
  character for character… without the line-number prefix", forbids `...`, and limits RUN AND JOBS quotes to a
  job or step name. The wrong labels and quoted headers, however, were text the model was shown under the
  stated header.
- **Not too loose.** Only 5 of 646 quotes are under 15 characters, and each is a decisive token or an allowed
  step name. Whether grounded quotes support the stated root cause was not measured.

**Remove-type fixes (full mode).**
- **Gemini** received credit on 0 of its 22. All 9 of its non-empty fixes there replace the injected value with
  a valid one, or rewrite the code as it was. The 5 reviewed by hand would all be accepted by a reviewer:
  - inj-029 and inj-034: a valid env value.
  - inj-036: `minversion = 9.0` against pytest 9.0.3.
  - inj-041: `timeout-minutes: 5` for a step that takes 93–150 s in every bundle that shows it.
  - inj-044: the test function exactly as before the injection.
- **Ollama:** 2 exact and 3 partial. In the 5 reviewed:
  - inj-033 is correct but was not credited.
  - inj-037 (add pytest-xdist) is debatable.
  - inj-043 is credited `exact`, although its fix field is just the culprit line, with no verb and no
    suspect file.
- **Verdict.** v1 was too strict on fixes that replace rather than delete. It was loose in one case, because
  "the fix contains the removed text" cannot tell "delete X" from restating X.

## What v2 does not do

These are evidence for reading the v2 numbers; nothing here was applied.
- **No added/removed distinction.** Stripping the gutter means v2 cannot tell an added diff line from a removed
  one.
- **Other gutters stay.** pytest's `E`/`>` gutter and docker's `NN | >>>` numbering are text, not stripped.
- **Ellipses are not normalised.** An elided quote stays ungrounded and is counted as an ellipsis break.
- **Any-section grounding.** A quote in any section is grounded. A wrong label shows only in the quote-rule
  metric.
- **Remove-type fixes are not scored** until a judge is added: 22 of 60 cases in every run.

## Not re-graded

`results/constant`, `results/oracle` and `results/random` were saved on 2026-10-31, before the grader gained
`job_correct` (D5c). Their records do not parse as today's results, so v1 cannot reproduce them. The re-grade
and the regression tests skip them with that reason. They are sanity runs outside D5 and D6a. Re-running them
(`python -m ci_triage.evaluation.run --predictor constant`, and so on) would grade them with v2.

**Update, 2026-11-09:** they have since been re-run with grader v2, in both modes; see the next section.

## Sanity check under grader v2

The three sanity predictors were re-run on 2026-11-09 with grader v2, in both modes, from `3f4b83a`. None of
them uses an LLM.
- **oracle** answers from ground truth and quotes the failure block it fetched. It is test-only, so it ran
  with `PYTHONPATH=tests`.
- **constant** always answers real_regression.
- **random** picks a uniformly random category, seed 0.

The runs overwrote the stale 2026-10-31 results. Their predicted categories are identical to those, case by
case.

```powershell
$env:PYTHONPATH = "tests"
.\.venv\Scripts\python.exe -m ci_triage.evaluation.run --predictor oracle:OraclePredictor --mode full --grader v2   # and --mode log-only
.\.venv\Scripts\python.exe -m ci_triage.evaluation.run --predictor constant --mode full --grader v2                # and --mode log-only
.\.venv\Scripts\python.exe -m ci_triage.evaluation.run --predictor random --mode full --grader v2                  # and --mode log-only
```

| run | category | macro-F1 | job / step / file | grounded cases | grounded quotes | quote rules followed | fix exact, restore-type | remove-type | Brier |
|---|---|---|---|---|---|---|---|---|---|
| oracle, full | **60/60** | **1.000** | 60 / 60 / 60 | **60/60** | 96/96 | **96/96** (60/60 cases) | **38/38** | 22 needs_judge (0 no fix) | 0.0000 |
| oracle, log-only | **60/60** | **1.000** | 60 / 60 / 60 | **60/60** | 96/96 | **96/96** (60/60 cases) | **38/38** | 22 needs_judge (0 no fix) | 0.0000 |
| constant, full | 10/60 | 0.048 | 0 / 0 / 0 | 0/60 | no quotes | no quotes | 0/38 | 22 needs_judge (22 no fix) | 0.1389 |
| constant, log-only | 10/60 | 0.048 | 0 / 0 / 0 | 0/60 | no quotes | no quotes | 0/38 | 22 needs_judge (22 no fix) | 0.1389 |
| random, full | 9/60 | 0.147 | 0 / 0 / 0 | 0/60 | no quotes | no quotes | 0/38 | 22 needs_judge (22 no fix) | 0.1278 |
| random, log-only | 9/60 | 0.147 | 0 / 0 / 0 | 0/60 | no quotes | no quotes | 0/38 | 22 needs_judge (22 no fix) | 0.1278 |

- **oracle** is perfect on every v2 metric in both modes. Every case was checked, not only the summary: category,
  job, step, file, action, grounding, all five quote rules (0 breaks), exact fix on every restore-type case,
  needs_judge on every remove-type one, Brier 0.
- **constant** is exactly 10/60 (the 10 real regressions). Its macro-F1 is (2/7)/6 = 0.048, the value the
  tests expect.
- **random** is 9/60, near the expected 10 (binomial(60, 1/6): mean 10, sd 2.9). Its seed depends only on the
  run id, so it makes the same 9 hits in both modes: inj-003, 007, 008, 026, 027, 028, 033, 044 and 050.
- **No grounding for constant or random.** They make no tool calls and quote nothing, so they have no grounded
  cases and no quote-rule figures.
- **Reproduction.** `python -m ci_triage.evaluation.regrade` re-grades all six runs. Since they were saved with
  grader v2, grader v2 is the one that must reproduce them, and it does exactly. That includes each
  `summary.md`, byte for byte.

**Why this needed a fix.** These are the first saved runs graded with v2. The re-grade check and the saved-run
regression tests in `c4adf0e` assumed every saved run had been graded with v1, so all six "failed" the
reproduction check: 12 test failures. Commit `3f4b83a` reproduces each saved run with the grader its records
name. No grading rule changed.

**Skipped tests.** The full suite went from 24 skips before grader v2 to 47 at `c4adf0e`. The 23 extra skips
were all in `tests/test_grader_v2.py`:

| skips | test | reason |
|---|---|---|
| 15 | the 5 saved-run tests × 3 stale runs (constant, oracle, random) | saved by an older grader (missing `job_correct`) |
| 4 | the byte-for-byte `summary.md` check × 4 one-call runs | an LLM run's summary also holds the model-call figures |
| 4 | the rules-only grounding check × 4 one-call runs | not a rules run |

So 15 of the 23 were the old sanity runs; the other 8 are skips by design. With the sanity runs re-run, the
suite at `3f4b83a` has **3,127 passed, 38 skipped, 0 failed**:
- 24 skips from before grader v2 (`test_log_only.py`: no removed line of 8+ characters is absent from the
  eval-commit file).
- 10 for the rules-only check on the 4 one-call runs and the 6 sanity runs.
- 4 for the byte-for-byte check on the 4 one-call runs.

The stale-run skips are gone. Those 15 tests now run on the 6 sanity runs: 24 pass, and 6 are rules-only skips.

## Reproduce

From the repo root, in `.venv`, with the saved runs under `results/`:

```powershell
.\.venv\Scripts\python.exe -m ci_triage.evaluation.regrade        # every saved run; writes results/<run>/grader-v2/
.\.venv\Scripts\python.exe -m pytest tests/test_grader_v2.py      # unit tests + v1-vs-v2 regression over saved runs
```

Each run's v2 grades are in `results/<run>/grader-v2/cases.jsonl` and `summary.md` (gitignored run output). The
saved v1 files are not modified.
