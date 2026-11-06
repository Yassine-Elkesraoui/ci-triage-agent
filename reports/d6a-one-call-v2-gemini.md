# D6a: one-call LLM baseline v2 on Gemini

| | |
|---|---|
| predictor | `one_call_v2-gemini`: `src/ci_triage/predictors/llm_once.py`, one fixed evidence bundle, one model call, one repair call at most |
| code under test | commit `ecc9452fc558940cfefe4a1ee14d339ae65885e3` (`ecc9452`, "feat: D6a prompt v2 aligned with the lab rulebook (pre-registered, not yet evaluated)"). No later commit touches `src/`, `prompts/` or `configs/`; the tree was clean before and after the runs. |
| settings | `configs/one_call_v2.toml` (sha256 `74f39cba5f5eb95e…`), prompt `prompts/one_call_v2.md` (sha256 `a9934294e8caff4f…`), both unchanged since `ecc9452` |
| model | requested `gemini-3.5-flash-lite`; `model_version` served on every answered call: `gemini-3.5-flash-lite` |
| lab | `ci-triage-lab` v1.0, submodule at `2b362bc7d69862b534cfd102ed5dc9c9e297dccc`, 60 cases (10 per category) |
| environment | Python 3.11.9, google-genai 2.34.0, Windows 11, from the repo root in `.venv` |
| date | 2026-11-06. Log-only 10:47:55–10:54:53 UTC, resume from 10:55:16 UTC; full 10:55:41–11:04:04 UTC |

**Prompt v2.** Its six category definitions are the "Definition" column of the lab's public rulebook
(`lab/README.md`), copied verbatim. The old tie-break is replaced by one sentence: when more than one category
fits, choose the category of the layer that has to change to fix the failure. **Prompt v1
(`prompts/one_call_v1.md`, `configs/one_call_v1.toml`) was never run.** It was replaced before any evaluation
(see the README, "Prompt v1 -> v2"). These are the first and only scores of the one-call baseline.

## Commands

From the repo root, in PowerShell, with `GEMINI_API_KEY` set:

```powershell
.\.venv\Scripts\python.exe -m ci_triage.evaluation.llm_run --backend gemini --mode log-only
.\.venv\Scripts\python.exe -m ci_triage.evaluation.llm_run --backend gemini --mode log-only --resume   # 1 api_error (inj-057)
.\.venv\Scripts\python.exe -m ci_triage.evaluation.llm_run --backend gemini                           # full mode, 0 api_error
```

Default config in all three runs; no setting was changed between them. Log-only ran first, as registered in
D5c, because it is the headline condition for D6.

## Headline

| metric | log-only | full |
|---|---|---|
| category accuracy | **49/60 (81.7%)** | **50/60 (83.3%)** |
| macro-F1 | **0.783** | **0.799** |
| failing_job correct | 60/60 (100%) | 60/60 (100%) |
| failing_step correct | 60/60 (100%) | 60/60 (100%) |
| suspect_file correct | 40/60 (66.7%) | 59/60 (98.3%) |
| grounding, cases with every quote grounded | 52/60 (86.7%) | 48/60 (80.0%) |
| grounding, quotes | 122/130 (93.8%) | 138/152 (90.8%) |
| fix match exact / partial / none | 13 / 0 / 47 | 33 / 0 / 27 |
| action consistent with true category | 49/60 (81.7%) | 50/60 (83.3%) |
| Brier score | 0.1748 | 0.1599 |
| mean confidence, correct / wrong | 0.988 / 0.975 | 0.997 / 0.978 |
| ok / invalid_output | 60 / 0 | 60 / 0 |
| repair calls | 0 | 0 |
| api_error, first pass → after `--resume` | 1 (inj-057) → 0 | 0 → 0 (no resume needed) |
| model_version served | `gemini-3.5-flash-lite` (60/60 calls) | `gemini-3.5-flash-lite` (60/60 calls) |
| model calls / attempts / retries | 60 / 66 / 6 | 60 / 78 / 18 |
| tokens in / out | 113,014 / 15,050 | 125,047 / 15,933 |
| thinking tokens | 0 | 0 |
| latency per call, mean / max | 1.92 s / 4.80 s | 1.69 s / 5.65 s |
| wall time per case, mean / max | 6.34 s / 12.58 s | 7.97 s / 24.27 s |
| prompt size estimate, max | 3,054 tokens | 3,363 tokens |
| prompts over the context budget | 0 | 0 |
| DIFF section in the bundle | 0/60 | 60/60 |

**Notes on the call numbers.** Latency is that of the attempt that answered. Wall time per case includes the
pacing to 10 requests per minute and the retry backoff. Every retry was a 503 and recovered within its 5 tries.
The one api_error was `500: INTERNAL Internal error encountered.` on inj-057's first attempt. 500 is not in the
config's retry statuses (`[429, 503, 504]`), so it failed after one attempt, as configured. `--resume` re-ran
only inj-057, which answered `ok` on its first attempt. Its failed call returned no tokens, so the log-only
token totals above are the final 60 answered calls. The ledger counted 67 log-only attempts (66 + 1) and 78
full-mode attempts, 145 of the 450-attempt daily cap.

## Comparison with rules v1

| metric | rules v1, log-only (D5c) | one-call v2, log-only | rules v1, full (D5b) | one-call v2, full |
|---|---|---|---|---|
| category accuracy | 39/60 (65.0%) | **49/60 (81.7%)** | **58/60 (96.7%)** | 50/60 (83.3%) |
| macro-F1 | 0.593 | **0.783** | **0.966** | 0.799 |
| failing_job / failing_step | 60 / 60 | 60 / 60 | 60 / 60 | 60 / 60 |
| suspect_file correct | 0/60 | **40/60** | **60/60** | 59/60 |
| grounding, cases | **60/60** | 52/60 | **60/60** | 48/60 |
| fix match exact | 0/60 | **13/60** | **60/60** | 33/60 |
| Brier score | **0.1270** | 0.1748 | **0.0804** | 0.1599 |
| flaky recall | 0/10 | 1/10 | **8/10** | 1/10 |
| environment recall | 1/10 | **10/10** | 10/10 | 10/10 |
| dependency recall | 8/10 | **9/10** | 10/10 | 10/10 |

- **Log-only, the headline:** one-call v2 beats rules v1 by 10 cases (49 vs 39). It gains 9 environment cases
  and 1 dependency case, gives back 1 build case (inj-022), and gains 1 flaky case. It also names a suspect file
  and an exact fix where the rules name none. Its Brier score is worse (0.175 vs 0.127), though, despite the
  higher accuracy: its 11 wrong answers average 0.975 confidence, where the rules' wrong fallbacks were at 0.30.
- **Full mode:** rules v1 stays well ahead (58 vs 50). The whole 8-case gap is flaky (1/10 vs 8/10) plus inj-025.
  With the diff, the model is at 10/10 on four categories and 9/10 on build.
- **Grounding is the rules' clear win.** The rules quote only text they matched, so every quote is grounded;
  8 log-only and 12 full-mode LLM diagnoses carry at least one quote that is not verbatim in the bundle.

## Per category

| category | cases | log-only predicted | log-only P | log-only R | log-only F1 | full predicted | full P | full R | full F1 |
|---|---|---|---|---|---|---|---|---|---|
| real_regression | 10 | 20 | 0.50 | 1.00 | 0.67 | 20 | 0.50 | 1.00 | 0.67 |
| flaky | 10 | 1 | 1.00 | 0.10 | 0.18 | 1 | 1.00 | 0.10 | 0.18 |
| dependency | 10 | 9 | 1.00 | 0.90 | 0.95 | 10 | 1.00 | 1.00 | 1.00 |
| environment | 10 | 11 | 0.91 | 1.00 | 0.95 | 10 | 1.00 | 1.00 | 1.00 |
| timeout | 10 | 10 | 1.00 | 1.00 | 1.00 | 10 | 1.00 | 1.00 | 1.00 |
| build | 10 | 9 | 1.00 | 0.90 | 0.95 | 9 | 1.00 | 0.90 | 0.95 |

## Confusion matrices

Rows: true category. Columns: predicted. No case is missing a diagnosis (no invalid_output, no api_error left).

**Log-only**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 9 | 1 | 0 | 0 | 0 | 0 |
| dependency | 1 | 0 | 9 | 0 | 0 | 0 |
| environment | 0 | 0 | 0 | 10 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 0 | 0 | 0 | 1 | 0 | 9 |

**Full**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 9 | 1 | 0 | 0 | 0 | 0 |
| dependency | 0 | 0 | 10 | 0 | 0 | 0 |
| environment | 0 | 0 | 0 | 10 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 1 | 0 | 0 | 0 | 0 | 9 |

## Localisation and fix match by category

| category | suspect_file, log-only | suspect_file, full | fix exact, log-only | fix exact, full | fix kind |
|---|---|---|---|---|---|
| real_regression | 1/10 | 10/10 | 1/10 | 10/10 | restore ×10 |
| flaky | 8/10 | 9/10 | 0/10 | 2/10 | restore ×7, remove ×3 |
| dependency | 8/10 | 10/10 | 1/10 | 10/10 | restore ×10 |
| environment | 9/10 | 10/10 | 1/10 | 1/10 | restore ×1, remove ×9 |
| timeout | 4/10 | 10/10 | 1/10 | 0/10 | remove ×10 |
| build | 10/10 | 10/10 | 9/10 | 10/10 | restore ×10 |
| **total** | **40/60** | **59/60** | **13/60** | **33/60** | |

In log-only mode, the model names `tests/test_behaviour.py` for 9 of the 10 real regressions; the changed
application file is not visible. That file is the target for most flaky cases, which is why flaky localisation
is high even though the category is wrong. In full mode, fix match is exact on 33 of 38 "restore" cases and on
none of the 22 "remove" cases (log-only: 1 of 22, inj-039). Those are the reverts that only delete what the
injection added, such as a `timeout-minutes` line, an env block or a pytest option. There the model writes a
replacement rather than naming the lines to delete.

## Misclassified cases

**Log-only (11)**

| case | true | predicted | conf. | what the model said |
|---|---|---|---|---|
| inj-049 | flaky | real_regression | 0.99 | Sees that the test picks a random string, sometimes under the 3-character minimum, but blames a code change. |
| inj-050 | flaky | real_regression | 1.00 | A validation test no longer raises; read as a code change, with no history or diff in the bundle. |
| inj-051 | flaky | real_regression | 0.95 | A short-question validation test fails; read as a code change. |
| inj-052 | flaky | real_regression | 0.95 | Names the cause, timestamps one second apart, yet calls it a regression. |
| inj-053 | flaky | real_regression | 0.99 | `None` instead of `[]` from chunking; read as a code bug, not a race. |
| inj-054 | flaky | real_regression | 1.00 | A character-count mismatch (571 vs 1139); read as a chunking regression. |
| inj-056 | flaky | real_regression | 0.95 | A `KeyError` on shared test state; read as a code regression. |
| inj-057 | flaky | real_regression | 0.95 | A `document_id` mismatch on chunks; read as a chunking-service regression. Scored from the `--resume` answer. |
| inj-058 | flaky | real_regression | 1.00 | A validation test no longer raises; read as a code change. |
| inj-059 | dependency | real_regression | 0.99 | Names `np.float_`, removed in NumPy 2.0, but blames the app code, not the version bump (no diff to show `requirements.txt`). |
| inj-022 | build | environment | 0.95 | A Dockerfile `COPY` of a misspelled `requirement.txt`; read as a missing file (config), though it names `Dockerfile` and gives the exact fix. |

**Full (10)**

| case | true | predicted | conf. | what the model said |
|---|---|---|---|---|
| inj-049 | flaky | real_regression | 1.00 | The diff adds a random test input; seen as a test change that broke validation, not as nondeterminism. |
| inj-050 | flaky | real_regression | 1.00 | **The DIFF section is empty** (same code as the last green run), yet it says the app no longer raises. |
| inj-051 | flaky | real_regression | 1.00 | The diff changes the test input; calls it a test change that broke the assertion. |
| inj-052 | flaky | real_regression | 0.90 | **Empty diff.** Names the timing cause, a one-second lag, yet calls it a regression. |
| inj-053 | flaky | real_regression | 0.98 | Describes the thread race the diff adds ("asserts before the worker finishes"), yet calls it a regression. |
| inj-054 | flaky | real_regression | 0.90 | **Empty diff.** A character-count mismatch; read as a regression. |
| inj-056 | flaky | real_regression | 1.00 | Describes test-order dependence on shared global state, yet calls it a regression. |
| inj-057 | flaky | real_regression | 1.00 | The diff changes `chunking_service.py`, so it reads application-code nondeterminism as a regression (rules v1 missed this one too). |
| inj-058 | flaky | real_regression | 1.00 | **Empty diff.** A validation test no longer raises; read as an app change. |
| inj-025 | build | real_regression | 1.00 | A Dockerfile model-name typo in "Build image" with the test job green, called "a code change"; file and fix are exact. |

Five flaky cases (inj-050, 052, 054, 055, 058) have an empty diff in full mode. That empty diff is the signal
rules v1's rule 1 used. The model called 4 of them real_regression at 0.90–1.00; only inj-055 was right, in
both modes, from the log alone (a 0.3 s time budget on a shared runner).

## Reading the result

- **Log-only, the headline: one-call v2 is the new floor, 49/60.** That is 10 more than rules v1. Reading error
  text gets environment (10/10) and most dependency cases without a diff, which the rules could not do.
- **Flaky is the one category it cannot do: 1/10 in both modes.** In 6 of the 18 wrong flaky answers (log-only inj-049
  and 052; full inj-049, 052, 053 and 056), the root cause names the nondeterminism (randomness, a timing lag,
  a thread race, test order) and the label is still real_regression. An empty diff did not change that. In log-only mode the bundle has no run history
  (`list_runs` is never called), so the earlier green runs of the same code are not shown at all.
- **The diff adds little to the category (+1 case) but a lot to localisation and fixes**: suspect file from 40
  to 59, exact fix from 13 to 33.
- **Overconfident.** Confidence is at 0.90 or above on every wrong answer, and the mean on wrong answers is
  within 0.02 of the mean on right ones. So the Brier score is worse than rules v1 in both modes.
- **Small sample.** There are 10 cases per category, so each case moves recall by 0.10. These are single runs
  at temperature 0 with seed 42. Run-to-run variance on the hosted model was not measured.

Per-case results are in `results/one_call_v2-gemini-log-only/` and `results/one_call_v2-gemini/` (gitignored run
output). `cases.jsonl` holds each case's status, the per-call records (model_version, tokens, latency, retries,
raw output), the prompt bundle and the grades. `summary.md` holds the run's own summary, with the config.

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

Recorded for a later, separately pre-registered version. **Nothing here was applied**; v2 stays as scored, and
the Ollama runs use the same files.

1. **Say what an empty diff means.** One sentence in the bundle description: an empty DIFF means the same code
   last passed. This targets the 4 empty-diff flaky cases called real_regression in full mode.
2. **Run history in the log-only bundle.** `list_runs` is allowed in log-only mode, but the one-call bundle
   does not use it. A short history (earlier runs of the same workflow, their shas and conclusions) would give
   flaky cases their main signal back.
3. **Separate "test is nondeterministic" from "a change broke a test".** The verbatim real_regression definition
   ("The fix is in the changed code") also fits a diff that touches only a test. A clarifying sentence would
   have to sit beside the rulebook definitions, not inside them, so they stay verbatim.
4. **Docker build failures are build.** inj-022 (log-only: environment) and inj-025 (full: real_regression)
   both fail in "Build image" with the test job green. The workflow and the job outcomes already show this.
5. **Calibration.** Confidence is near 1.0 whether right or wrong. Ask for calibrated probabilities, or
   recalibrate after the fact on a held-out split; the second needs more data than one 60-case run.
6. **Remove-type fixes.** For the 22 reverts that only delete lines, ask the fix to name the lines to delete.
   Alternatively, check whether the grader should accept "delete line X" phrasing. This needs looking at the
   raw `suggested_fix` texts first.
7. **Retry on 500.** The one api_error was a transient 500 INTERNAL that answered on resume. Adding 500 to
   `retry.statuses` would avoid a manual `--resume`. It is a config change, so it makes a new config version.
