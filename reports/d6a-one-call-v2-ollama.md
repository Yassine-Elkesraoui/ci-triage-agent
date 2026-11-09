# D6a: one-call LLM baseline v2 on Ollama

| | |
|---|---|
| predictor | `one_call_v2-ollama`: `src/ci_triage/predictors/llm_once.py`, one fixed evidence bundle, one model call, one repair call at most |
| code under test | commit `ecc9452fc558940cfefe4a1ee14d339ae65885e3` (`ecc9452`, "feat: D6a prompt v2 aligned with the lab rulebook (pre-registered, not yet evaluated)"). The runs were made at `b5bfca9`, which only adds `reports/d6a-one-call-v2-gemini.md`. No later commit touches `src/`, `prompts/` or `configs/`, and the tree was clean when this report was written. |
| settings | `configs/one_call_v2.toml` (sha256 `74f39cba5f5eb95e…`), prompt `prompts/one_call_v2.md` (sha256 `a9934294e8caff4f…`), both unchanged since `ecc9452` and the same files as the Gemini runs |
| model | requested `qwen3.5:4b`; `model_version` served on every call: `qwen3.5:4b@d8b0f5e9760c` (60/60 calls in each mode) |
| model digest | `d8b0f5e9760c` is the first 12 hex characters of the digest the Ollama server lists for the tag. The full digest is `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`, read from `/api/tags` on 2026-11-09, prefix matching. Listed as GGUF, family `qwen35`, 4.2B parameters, `Q4_K_M`. |
| lab | `ci-triage-lab` v1.0, submodule at `2b362bc7d69862b534cfd102ed5dc9c9e297dccc`, 60 cases (10 per category) |
| environment | Python 3.11.9, ollama (Python client) 0.6.3, Ollama server 0.40.15 (read on 2026-11-09; the runs do not record the server version), Windows 11, from the repo root in `.venv`. Hardware is not recorded in the run output. |
| date | 2026-11-07. Log-only finished 22:02 UTC; full finished 22:54 UTC. These are the modification times of each `cases.jsonl`; the machine clock is on GMT, which is UTC in November. Summed per-case wall time: 50.6 min for log-only (60 cases), 51.2 min for full. |

**Prompt v2.** Same prompt and config as the Gemini report (`reports/d6a-one-call-v2-gemini.md`). The six
category definitions are copied verbatim from the lab rulebook, and there is one tie-break sentence (the layer
that has to change to fix the failure). Prompt v1 was never run.

## Commands

From the repo root:

```powershell
.\.venv\Scripts\python.exe -m ci_triage.evaluation.llm_run --backend ollama --mode log-only            # inj-001 to inj-004, then stopped
.\.venv\Scripts\python.exe -m ci_triage.evaluation.llm_run --backend ollama --mode log-only --resume   # the other 56
.\.venv\Scripts\python.exe -m ci_triage.evaluation.llm_run --backend ollama                            # full mode, all 60
```

**How log-only was run.** inj-001 to inj-004 ran in a Claude Code background shell, which was then stopped. The
other 56 cases were resumed in PowerShell with the same command (plus `--resume`), config and prompt checksums.
Full mode ran all 60 cases in PowerShell in one go. Console output of the two PowerShell runs is in
`results/ollama-log-only.log` and `results/ollama-full.log`. Both print config sha256 `74f39cba5f5eb95e` and
prompt sha256 `a9934294e8caff4f`. The log-only log ends with "56 cases run, 4 skipped as done"; the full log
ends with "60 cases run, 0 skipped as done". No console log of the background-shell part is in `results/`.
Its four records are complete `ok` records, served by the same `qwen3.5:4b@d8b0f5e9760c`.

**Validity check, done before scoring.** Each `cases.jsonl` has exactly 60 non-empty lines. All are valid JSON,
the 60 `case_id`s are unique, and all 60 have status `ok`. Default config in all three commands; no setting
was changed between them. Log-only ran first, as for Gemini, because it is the headline condition for D6.

## Headline

| metric | log-only | full |
|---|---|---|
| category accuracy | **43/60 (71.7%)** | **42/60 (70.0%)** |
| macro-F1 | **0.683** | **0.664** |
| failing_job correct | 60/60 (100%) | 60/60 (100%) |
| failing_step correct | 60/60 (100%) | 60/60 (100%) |
| suspect_file correct | 18/60 (30.0%) | 46/60 (76.7%) |
| grounding, cases with every quote grounded | 33/60 (55.0%) | 36/60 (60.0%) |
| grounding, quotes | 145/179 (81.0%) | 158/185 (85.4%) |
| fix match exact / partial / none | 5 / 1 / 54 | 32 / 3 / 25 |
| action consistent with true category | 43/60 (71.7%) | 42/60 (70.0%) |
| Brier score | 0.2579 | 0.2733 |
| mean confidence, correct / wrong | 0.968 / 0.952 | 0.981 / 0.953 |
| ok / invalid_output | 60 / 0 | 60 / 0 |
| repair calls | 0 | 0 |
| api_error | 0 | 0 |
| model_version served | `qwen3.5:4b@d8b0f5e9760c` (60/60 calls) | `qwen3.5:4b@d8b0f5e9760c` (60/60 calls) |
| model calls / attempts / retries | 60 / 60 / 0 | 60 / 60 / 0 |
| finish_reason | `stop` 60/60 | `stop` 60/60 |
| tokens in / out | 110,501 / 17,054 | 122,016 / 17,080 |
| tokens in, max per call | 2,769 | 3,047 |
| tokens out, max per call | 466 | 489 |
| thinking tokens | 0 (`think = false`; none reported) | 0 (`think = false`; none reported) |
| latency per call, mean / median / max | 50.46 s / 48.67 s / 84.79 s | 50.83 s / 49.05 s / 84.87 s |
| latency per call, min | 35.12 s | 37.45 s |
| wall time per case, mean / max | 50.57 s / 84.91 s | 51.16 s / 85.19 s |
| prompt size estimate, max | 3,054 tokens | 3,363 tokens |
| context_overflow (prompts over the context budget) | **0** | **0** |
| largest tokens in + max_output_tokens | 4,305 of 8,192 | 4,583 of 8,192 |
| DIFF section in the bundle | 0/60 | 60/60 |

**Notes on the call numbers.**
- **Context.** `context_overflow` is set when `tokens_in + max_output_tokens` exceeds `num_ctx` (8,192). No call
  came close; the largest was 4,583. The maximum prompt size estimates match the Gemini runs exactly, since both build
  the same bundle; the token counts differ because the tokenizers differ.
- **Latency** is the time of the one attempt per call; there were no retries. With no pacing, wall time per case
  is latency plus a few tenths of a second. The local model is about 26–30× slower per call than Gemini
  (1.92 s / 1.69 s mean).
- **inj-001.** The slowest call in log-only was inj-001, the first call of the background-shell run: 84.8 s
  for 234 output tokens, 2.8 tokens/s. The other 59 log-only calls ran at 5.2–6.2 tokens/s. That pattern fits a
  cold model load, but the record does not separate load time. In full mode, inj-001 took 47.7 s.
- **Budget.** The Ollama backend has no daily cap, and `results/llm_call_ledger.json` has no Ollama entries.

## Comparison with Gemini and rules v1

**Log-only**

| metric | rules v1 (D5c) | one-call v2, Gemini | one-call v2, Ollama |
|---|---|---|---|
| category accuracy | 39/60 (65.0%) | **49/60 (81.7%)** | 43/60 (71.7%) |
| macro-F1 | 0.593 | **0.783** | 0.683 |
| failing_job / failing_step | 60 / 60 | 60 / 60 | 60 / 60 |
| suspect_file correct | 0/60 | **40/60** | 18/60 |
| grounding, cases | **60/60** | 52/60 | 33/60 |
| fix match exact | 0/60 | **13/60** | 5/60 |
| Brier score | **0.1270** | 0.1748 | 0.2579 |
| real_regression recall | 10/10 | 10/10 | 10/10 |
| flaky recall | 0/10 | 1/10 | 1/10 |
| dependency recall | 8/10 | 9/10 | **10/10** |
| environment recall | 1/10 | **10/10** | 6/10 |
| timeout recall | 10/10 | 10/10 | 10/10 |
| build recall | **10/10** | 9/10 | 6/10 |
| latency per call, mean | — (no model) | 1.92 s | 50.46 s |

**Full**

| metric | rules v1 (D5b) | one-call v2, Gemini | one-call v2, Ollama |
|---|---|---|---|
| category accuracy | **58/60 (96.7%)** | 50/60 (83.3%) | 42/60 (70.0%) |
| macro-F1 | **0.966** | 0.799 | 0.664 |
| failing_job / failing_step | 60 / 60 | 60 / 60 | 60 / 60 |
| suspect_file correct | **60/60** | 59/60 | 46/60 |
| grounding, cases | **60/60** | 48/60 | 36/60 |
| fix match exact | **60/60** | 33/60 | 32/60 |
| Brier score | **0.0804** | 0.1599 | 0.2733 |
| real_regression recall | 10/10 | 10/10 | 10/10 |
| flaky recall | **8/10** | 1/10 | 1/10 |
| dependency recall | 10/10 | 10/10 | 10/10 |
| environment recall | **10/10** | **10/10** | 6/10 |
| timeout recall | 10/10 | 10/10 | 10/10 |
| build recall | **10/10** | 9/10 | 5/10 |
| latency per call, mean | — (no model) | 1.69 s | 50.83 s |

**Case by case** (category correct or not, on the same 60 cases):

| pair | both right | only the first right | only the second right | same label |
|---|---|---|---|---|
| Ollama vs Gemini, log-only | 40 | 3: inj-022, inj-052, inj-059 | 9: inj-019, 020, 024, 025, 033, 034, 035, 037, 055 | 48/60 |
| Ollama vs Gemini, full | 41 | 1: inj-052 | 9: inj-019, 020, 024, 028, 033, 035, 036, 037, 055 | 49/60 |
| Ollama vs rules v1, log-only | 35 | 8: inj-029, 030, 031, 032, 036, 052, 059, 060 | 4: inj-019, 020, 024, 025 | 46/60 |
| Ollama vs rules v1, full | 42 | 0 | 16: inj-019, 020, 024, 025, 028, 033, 035, 036, 037, 049, 050, 053, 054, 055, 056, 058 | 44/60 |
| Ollama log-only vs Ollama full | 41 | 2: inj-028, inj-036 | 1: inj-034 | 57/60 |

- **Log-only, the headline: Ollama is between the rules and Gemini.** It is 4 cases above rules v1 (43 vs 39)
  and 6 below Gemini (43 vs 49). Against the rules, it gains 5 environment cases, 2 dependency cases and 1 flaky
  case, and loses 4 build cases. Against Gemini, it loses 4 build cases (all called dependency), 4 environment
  cases and inj-055. It gains inj-022, inj-052 and inj-059.
- **Full mode: below both.** Every case Ollama gets right, rules v1 gets right too (42 of 42). Gemini is
  8 cases ahead (50 vs 42); 8 of its 9 extra cases are build or environment.
- **The diff does not help the category** (43 → 42; 57 of 60 labels unchanged), but it helps localisation
  (18 → 46) and exact fixes (5 → 32). Gemini showed the same pattern.
- **Calibration is the weakest of the three.** The Brier score is 0.258 / 0.273, against Gemini's
  0.175 / 0.160 and the rules' 0.127 / 0.080.
- **Latency.** About 50 s per call locally, against about 2 s hosted. Both are far from the rules' mean of under
  0.3 s per case. All 120 calls answered on the first attempt, with no transport errors.

## Per category

| category | cases | log-only predicted | log-only P | log-only R | log-only F1 | full predicted | full P | full R | full F1 |
|---|---|---|---|---|---|---|---|---|---|
| real_regression | 10 | 21 | 0.48 | 1.00 | 0.65 | 21 | 0.48 | 1.00 | 0.65 |
| flaky | 10 | 1 | 1.00 | 0.10 | 0.18 | 1 | 1.00 | 0.10 | 0.18 |
| dependency | 10 | 16 | 0.62 | 1.00 | 0.77 | 17 | 0.59 | 1.00 | 0.74 |
| environment | 10 | 6 | 1.00 | 0.60 | 0.75 | 6 | 1.00 | 0.60 | 0.75 |
| timeout | 10 | 10 | 1.00 | 1.00 | 1.00 | 10 | 1.00 | 1.00 | 1.00 |
| build | 10 | 6 | 1.00 | 0.60 | 0.75 | 5 | 1.00 | 0.50 | 0.67 |

## Confusion matrices

Rows: true category. Columns: predicted. No case is missing a diagnosis (no invalid_output, no api_error).

**Log-only**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 9 | 1 | 0 | 0 | 0 | 0 |
| dependency | 0 | 0 | 10 | 0 | 0 | 0 |
| environment | 2 | 0 | 2 | 6 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 0 | 0 | 4 | 0 | 0 | 6 |

**Full**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 9 | 1 | 0 | 0 | 0 | 0 |
| dependency | 0 | 0 | 10 | 0 | 0 | 0 |
| environment | 1 | 0 | 3 | 6 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 1 | 0 | 4 | 0 | 0 | 5 |

## Localisation and fix match by category

| category | suspect_file, log-only | suspect_file, full | fix exact, log-only | fix exact, full | fix kind |
|---|---|---|---|---|---|
| real_regression | 0/10 | 10/10 | 1/10 | 10/10 | restore ×10 |
| flaky | 7/10 | 7/10 | 0/10 | 0/10 | restore ×7, remove ×3 |
| dependency | 7/10 | 10/10 | 0/10 | 9/10 | restore ×10 |
| environment | 0/10 | 9/10 | 0/10 | 2/10 (+3 partial) | restore ×1, remove ×9 |
| timeout | 0/10 | 0/10 | 0/10 | 1/10 | remove ×10 |
| build | 4/10 | 10/10 | 4/10 (+1 partial) | 10/10 | restore ×10 |
| **total** | **18/60** | **46/60** | **5/60** | **32/60** | |

- **Restore vs remove fixes.** In full mode, fix match is exact on 30 of the 38 "restore" cases. On the 22
  "remove" cases it is exact on 2 (inj-036, inj-043) and partial on 3 (inj-029, inj-030, inj-034). In log-only
  mode: 5 of 38 restore cases exact, plus inj-023 partial, and none of the 22 remove cases.
- **Empty fields.** The model often leaves the suspect file empty, or the fix empty, in log-only mode. Log-only
  has no suspect file in 29 of 60 diagnoses (Gemini: 2) and an empty fix in 31 (Gemini: 22). Full mode: 13 and 15
  (Gemini: 0 and 16).
- **Timeout.** No suspect file is named for any of the 10 timeout cases, in either mode. Yet several diagnoses
  quote `timeout-minutes: 1` from the workflow file, which is in the bundle.
- **real_regression in log-only.** The model names `tests/test_behaviour.py` for 8 of the 10 real regressions,
  as Gemini did for 9; the changed application file is not visible without the diff.

## Misclassified cases

**Log-only (17)**

| case | true | predicted | conf. | what the model said |
|---|---|---|---|---|
| inj-049 | flaky | real_regression | 0.95 | Sees that the test picks a random question that can be "AI" (2 characters, under the 3-character minimum), but says the model "now requires" 3. |
| inj-050 | flaky | real_regression | 0.95 | A validation test no longer raises; "the code change likely altered the validation logic", with no diff in the bundle. |
| inj-051 | flaky | real_regression | 0.95 | A short-question validation test fails; read as an application change. |
| inj-053 | flaky | real_regression | 0.95 | `None` instead of `[]` for `chunks`; read as a regression. |
| inj-054 | flaky | real_regression | 0.95 | A character-count mismatch (571 vs 1139); read as a regression. |
| inj-055 | flaky | real_regression | 0.95 | Names the 0.3 s budget and the 0.578 s measured, and calls it a performance regression. Gemini got this one right. |
| inj-056 | flaky | real_regression | 0.95 | A `KeyError` on `_SPLIT_RESULTS`; read as code that no longer produces the key. |
| inj-057 | flaky | real_regression | 0.95 | A `document_id` mismatch on chunks; read as a chunking regression. No suspect file. |
| inj-058 | flaky | real_regression | 0.95 | As inj-050. |
| inj-033 | environment | real_regression | 0.95 | Quotes `CHUNK_OVERLAP` set to 5000 in the workflow and says it overrides the default, yet calls it a regression. |
| inj-034 | environment | real_regression | 0.95 | Names `RETRIEVAL_MIN_SCORE` set to 1.5 in the workflow, yet calls it a regression; suspect file wrong. |
| inj-035 | environment | dependency | 0.95 | A `--cov=api` option fails; blamed on the installed pytest version. |
| inj-037 | environment | dependency | 0.95 | A pytest option that needs pytest-xdist; read as a missing package in `requirements.txt`. |
| inj-019 | build | dependency | 0.98 | Names the non-existent base image `python:3.99-slim`, but labels it dependency; no file, no fix. |
| inj-020 | build | dependency | 0.95 | The base-image typo `pythonn:3.11-slim`; `Dockerfile` and the fix are exact, the label is not. |
| inj-024 | build | dependency | 0.95 | The `requirements.text` typo in the Dockerfile; file and fix exact, label dependency. |
| inj-025 | build | dependency | 0.95 | A Hugging Face 401 during the Docker build; its own root cause says "an environment configuration issue", and the label is dependency. |

**Full (18)**

| case | true | predicted | conf. | what the model said |
|---|---|---|---|---|
| inj-049 | flaky | real_regression | 0.95 | "The new randomized selection can pick invalid values": names the nondeterminism, labels it real_regression. |
| inj-050 | flaky | real_regression | 0.95 | **Empty diff**, yet "the code change in this commit likely altered the validation logic". |
| inj-051 | flaky | real_regression | 0.95 | The diff picks a short question "randomly via `next(iter(short_questions))`"; read as a test change that broke the assertion. |
| inj-053 | flaky | real_regression | 0.95 | Describes the background thread the diff adds, which "may not have finished" before the assertion, yet calls it a regression. |
| inj-054 | flaky | real_regression | 0.95 | **Empty diff.** A character-count mismatch; read as a regression. |
| inj-055 | flaky | real_regression | 0.95 | **Empty diff.** 0.578 s against a 0.3 s budget; "a regression in performance caused by a code change, not an environment or flaky issue". |
| inj-056 | flaky | real_regression | 0.95 | Describes the order dependence on a global dict ("when run in isolation or if the order changes"), yet calls it a regression. |
| inj-057 | flaky | real_regression | 0.95 | The diff changes `chunking_service.py`; read as a deliberate change to `document_id`. Suspect file correct. |
| inj-058 | flaky | real_regression | 0.95 | **Empty diff**, yet "the code change in this commit likely altered the validation logic". |
| inj-033 | environment | real_regression | 0.95 | Names the `CHUNK_OVERLAP` change in `ci.yml` (suspect file correct) and labels it real_regression. |
| inj-035 | environment | dependency | 0.95 | The `--cov` flag added to the pytest config; blamed on the pytest-cov version. |
| inj-036 | environment | dependency | 0.95 | `minversion = 99.0` added to `pytest.ini` against pytest 9.0.3; file and fix exact. Right in log-only mode. |
| inj-037 | environment | dependency | 0.95 | `pytest.ini` now needs pytest-xdist; read as a missing package. |
| inj-019 | build | dependency | 0.95 | `python:3.99-slim`; file and fix exact, label dependency. |
| inj-020 | build | dependency | 0.95 | `pythonn:3.11-slim`; file and fix exact, label dependency. |
| inj-024 | build | dependency | 0.95 | `requirements.text`; file and fix exact, label dependency. |
| inj-025 | build | dependency | 0.95 | A model-version change in the Docker build with a 401; file and fix exact, label dependency. |
| inj-028 | build | real_regression | 1.00 | The `mkdir` → `mkdirr` typo in the Dockerfile; file and fix exact, label real_regression. Right in log-only mode. |

**The one flaky case called flaky.** inj-052 is right in both modes, at 0.85, the lowest confidence in each run.
The model reads it as "a timing-dependent flakiness rather than a code regression": timestamps one second apart.
Gemini called it real_regression in both modes.

**Empty diffs.** Five flaky cases (inj-050, 052, 054, 055, 058) have an empty diff in full mode, as in the Gemini
run. Ollama got inj-052 right and called the other 4 real_regression at 0.95. Gemini got only inj-055 right.

## Reading the result

- **Log-only, the headline: 43/60.** That is 4 above rules v1 and 6 below Gemini. Reading error text lifts
  environment (6/10, against the rules' 1/10) and dependency (10/10) without a diff, but less than Gemini did on
  environment (10/10).
- **Build vs dependency is the main new confusion.** 4 of 10 build cases (inj-019, 020, 024, 025) are called
  dependency in both modes. These are a bad base image tag, a misspelt image name, a misspelt requirements file
  and a gated model download, all in the Docker build step. In 2 of them in log-only mode (inj-020, 024), and
  all 4 in full mode, the suspect file and the fix are exact; only the label is wrong. The same happens with pytest options
  that need a plugin (inj-035, 037, and inj-036 in full mode): environment cases called dependency.
- **Flaky is 1/10 in both modes, as for Gemini.** Several wrong flaky answers name the cause in their root
  cause: random input, set order, a thread race, test-order dependence or a time budget. That is 2 of 9 in
  log-only mode (inj-049, 055) and 5 of 9 in full mode (inj-049, 051, 053, 055, 056). The label is still
  real_regression.
- **The diff adds nothing to the category (−1 case) but a lot to localisation and fixes**: suspect file
  from 18 to 46, exact fix from 5 to 32. In full mode, the exact-fix count (32) is close to Gemini's (33).
- **Overconfident.** Every wrong answer is at 0.95–1.00. 59 of 60 confidences in each run are 0.95, 0.98 or
  1.00. So the Brier score is the worst of the three predictors in both modes.
- **Grounding is lower than Gemini's** (33 vs 52 cases in log-only, 36 vs 48 in full). Looking at the raw quotes,
  many of the ungrounded ones are multi-line pytest excerpts with lines dropped or joined. In log-only mode, no
  timeout case is fully grounded (0/10); the grader's per-quote reasons were not examined for this report.
- **Small sample, single runs.** There are 10 cases per category, so each case moves recall by 0.10. These are
  single runs at temperature 0 with seed 42 on one local model build. Run-to-run variance was not measured.

Per-case results are in `results/one_call_v2-ollama-log-only/` and `results/one_call_v2-ollama/` (gitignored run
output). `cases.jsonl` holds each case's status, the per-call records (model_version, tokens, latency, retries,
context_overflow, raw output), the prompt bundle and the grades. `summary.md` holds the run's own summary, with
the config. The console logs are `results/ollama-log-only.log` (the 56 resumed cases) and
`results/ollama-full.log`.

## Settings (pre-registered, verbatim)

```toml
# D6a: the one-call LLM baseline. Pre-registered settings, committed before any evaluation run.
# Every report of this baseline prints this file verbatim. Changing a value makes a new version
# (one_call_v2.toml, ...); never edit this file after its first evaluation.

name = "one_call_v2"
prompt = "prompts/one_call_v2.md"

[generation]                # both backends
temperature = 0.0
max_output_tokens = 1536    # a full Diagnosis (5 quotes, root cause, fix) is well under this
repair_calls = 1            # one follow-up call if the first answer is not a valid Diagnosis

[budget]                    # the prompt must fit the local model's context with room for the answer
context_tokens = 8192
reserve_tokens = 256        # chat template and slack, on top of max_output_tokens
chars_per_token = 3.0       # conservative estimate (logs and code run at ~3.5-4 chars per token)
jobs_chars = 2500           # run, jobs and steps summary
failure_block_chars = 4000  # extract_failure_block for the failing job (the tool's own cap)
workflow_chars = 3500       # the workflow file at the commit under triage
diff_chars = 4000           # compare(last green run, run under triage); full mode only

[gemini]
model = "gemini-3.5-flash-lite"
thinking_level = "minimal"  # closest to the local model's think=false
seed = 42
max_requests_per_minute = 10
daily_call_cap = 450        # attempts per Pacific-time day, retries and repairs included
timeout_s = 120

[ollama]
model = "qwen3.5:4b"
think = false
num_ctx = 8192
seed = 42
host = "http://localhost:11434"
timeout_s = 600

[retry]
max_tries = 5               # attempts per model call, the first one included
statuses = [429, 503, 504]  # a client-side timeout counts as 504
backoff_initial_s = 5.0
backoff_factor = 2.0
backoff_max_s = 60.0
```

## Not applied

Recorded for a later, separately pre-registered version. **Nothing here was applied**; v2 stays as scored. The
seven items in the Gemini report still stand. Items 1–3 (empty diff, run history, nondeterministic test vs
broken test) and 5 (calibration) apply here unchanged. The Ollama runs add these:

1. **Docker build failures are build, not dependency.** This extends Gemini item 4. A missing base image, a
   misspelt file in a `COPY`/`RUN` and a failed download inside the Docker build all fail in "Build image" with
   the test job green. 4 build cases were called dependency in each mode, and inj-028 real_regression in full.
2. **Test-runner options are environment.** A `pytest.ini` or workflow option that needs a plugin or a newer
   pytest (inj-035, 036, 037) was read as a missing dependency. The verbatim rulebook definitions stay as they
   are; any clarifying sentence would sit beside them.
3. **Ask for the suspect file even without a diff.** In log-only mode, 29 of 60 diagnoses name no suspect file.
   No timeout diagnosis names one in either mode, even when the `timeout-minutes` line from the workflow is
   quoted.
4. **Look at the ungrounded quotes.** Before any prompt change, check the grader's reasons per quote,
   especially for the 10 log-only timeout cases. Grounding may need a "quote one line at a time" instruction,
   or the grader may be stricter than intended on multi-line excerpts.
5. **Record the Ollama server version and hardware in the run output.** The digest pins the weights, but the
   server version (0.40.15 here, read afterwards) and the hardware affect latency and possibly the output.
   It is a code change, so it goes with a later version.
