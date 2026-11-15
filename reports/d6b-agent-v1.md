# D6b: tool-using agent v1, all four runs

| | |
|---|---|
| predictor | `agent_v1-<backend>`: `src/ci_triage/agent/loop.py`. It starts from the D6a one-call bundle, takes up to 8 model turns with the mode's read-only tools, then gives one `Diagnosis`, with one repair call at most |
| code under test | commit `bf06738de7eb7c19b34b5acae5304a334ffedbe7` (`bf06738`, "fix: grader v2.1 accepts history quotes from tool output (before D6b runs)"). No later commit exists; the tree was clean when this report was written. All six console-log sessions print the checksums below. |
| settings | `configs/agent_v1.toml` (sha256 `6923a11809bccb7e…`), prompt `prompts/agent_v1.md` (sha256 `87b633e93edbc9bd…`), bundle from `configs/one_call_v2.toml` (sha256 `74f39cba5f5eb95e…`) |
| models | Gemini: requested `gemini-3.5-flash-lite`, served `gemini-3.5-flash-lite` on every answered call (194 + 116). Ollama: requested `qwen3.5:4b`, served `qwen3.5:4b@d8b0f5e9760c` on every answered call (380 + 379); the only call with no version is inj-024's failed turn 4. |
| grader | v2.1 (the runner's default; saved in every record). D6a and rules v1 were re-graded with v2.1 for this report, in memory and offline (see "Comparison") |
| lab | `ci-triage-lab` v1.0, submodule at `2b362bc7d69862b534cfd102ed5dc9c9e297dccc`, 60 cases (10 per category) |
| environment | Python 3.11.9, langgraph 1.2.17, langchain-core 1.7.6, google-genai 2.34.0, ollama (client) 0.6.3, pydantic 2.14.1. For the Ollama runs, from the records: Ollama server 0.40.16, Windows, Intel CPU with 12 logical cores, 25.4 GB RAM, NVIDIA GeForce MX570 A (2 GiB). The model is 3.67 GB, of which 0.17 GB sat in VRAM. |
| dates (UTC) | Gemini log-only 2026-11-13, about 17:52–18:11. Gemini full 2026-11-13, about 22:41–22:52. Ollama log-only 2026-11-13, about 17:53–20:28. Ollama full 2026-11-13 22:41 to 2026-11-14 01:37, then two `--resume` passes of inj-024 on 2026-11-15 (17:56–18:05 and 18:06–18:13). End times are the `cases.jsonl` modification times; start times come from the summed wall time and the Ollama server log. The Gemini and Ollama runs overlapped in time; Gemini is hosted, so the overlap shared only the local CPU. |

Registered run plan (`[[run]]` in the config), enforced by the runner: Gemini log-only, Gemini full, Ollama
log-only, Ollama full. Each backend ran log-only first, the headline condition.

## Commands

From the repo root, in `.venv`:

```powershell
.\.venv\Scripts\python.exe -m ci_triage.evaluation.agent_run --backend gemini --mode log-only            # 60 cases, one session
.\.venv\Scripts\python.exe -m ci_triage.evaluation.agent_run --backend gemini --mode full                # 60 cases, one session
.\.venv\Scripts\python.exe -m ci_triage.evaluation.agent_run --backend ollama --mode log-only            # 60 cases, one session
.\.venv\Scripts\python.exe -m ci_triage.evaluation.agent_run --backend ollama --mode full                # 60 cases, inj-024 api_error
.\.venv\Scripts\python.exe -m ci_triage.evaluation.agent_run --backend ollama --mode full --resume       # inj-024 only: api_error again
.\.venv\Scripts\python.exe -m ci_triage.evaluation.agent_run --backend ollama --mode full --resume       # inj-024 only: api_error again
```

Default config and grader in every command. Console output is in `results/agent-<backend>-<mode>.log`.

## Validity check (before scoring)

| run | lines | valid JSON | unique case_ids | status | grader | saved grades reproduced offline |
|---|---|---|---|---|---|---|
| Gemini log-only | 60 | 60 | 60 (= the lab's 60) | 60 ok | v2.1 ×60 | 60/60 |
| Gemini full | 60 | 60 | 60 | 60 ok | v2.1 ×60 | 60/60 |
| Ollama log-only | 60 | 60 | 60 | 60 ok | v2.1 ×60 | 60/60 |
| Ollama full | 60 | 60 | 60 | 59 ok, 1 api_error (inj-024) | v2.1 ×59 | 59/59 |

**How the reproduction was done.** Each record's tool calls were replayed offline on a fresh toolkit. The
case was then re-graded with v2.1 against the bundle sections and the shown tool results, as `agent_run`
does. Every saved `CaseResult` came back identical, field for field. No other run has an api_error, and
none has an invalid_output.

## inj-024 (Ollama, full): diagnosis

Read-only. Nothing was re-run, and no code, prompt or config was changed. Sources:
`results/agent_v1-ollama/cases.jsonl`, `results/agent-ollama-full.log`, and the Ollama server logs
(`%LOCALAPPDATA%\Ollama\server-1.log` for the first pass, `server.log` for the two `--resume` passes).
`--resume` replaces the case's record, so `cases.jsonl` holds only the third pass. The server log holds all
three.

**The case.** inj-024 is a build case: the Dockerfile copies a misspelt `requirements.text`.

**Turns 1–3.** These were identical in all three passes: the same prompt sizes on the server (3,749, 4,102
and 5,984 tokens) and the same output sizes (109, 26 and 26 tokens). Each answered HTTP 200.

| turn | model output | tool result shown |
|---|---|---|
| 1 | no text; `compare(base=7d54990…, head=702aa0a…)` | the diff, 728 characters |
| 2 | no text; `get_file(path="requirements.txt")` | the file, 3,441 characters |
| 3 | no text (content `""`); one tool call, `get_file(path="requirements.text")`; 26 tokens out, `finish_reason` stop, 9.96 s | `{"available":false,"what":"requirements.text at main","reason":"not found"}` (75 characters) |

The turn-3 result was rebuilt offline: same size and availability as recorded. The bundle rebuilt
byte-identical to the saved one, with 6 tool calls.

**Turn 4.** Tools were offered, as on turns 1–7. The request was **6,040 tokens**, as counted by the server
(`task.n_tokens = 6040`); our own estimate, at 3 characters per token, was 5,703. With the 1,536-token output
cap, that is 7,576 of the 16,384-token context. All 15 attempts got the same prompt.

**Every failed attempt**, as `[GIN]` lines in the server log:

| pass | attempt time (UTC) and HTTP status, duration |
|---|---|
| 1, first run (2026-11-14) | 01:21:54 **500** 55.8 s · 01:22:55 **500** 55.8 s · 01:24:02 **500** 55.6 s · 01:25:21 **500** 56.1 s · 01:26:57 **500** 54.2 s |
| 2, `--resume` (2026-11-15) | 17:59:25 **500** 1m10s · 18:00:47 **500** 1m10s · 18:02:15 **500** 1m10s · 18:03:47 **500** 1m10s · 18:05:38 **500** 1m0s |
| 3, `--resume` (2026-11-15) | 18:07:56 **500** 1m10s · 18:09:08 **500** 1m0s · 18:10:30 **500** 1m0s · 18:12:03 **500** 1m10s · 18:13:56 **500** 1m10s |

All 15 return the same error text: `XML syntax error on line 4: element <function> closed by </parameter>`.
The client recorded it as `500: XML syntax error on line 4: element <function> closed by </parameter>`, five
times in pass 3's record (the earlier passes' records were replaced). In the server log, each 500 comes
right after two warnings:

```
level=WARN source=qwen3coder.go:87 msg="qwen tool call parsing failed" error="XML syntax error on line 4: element <function> closed by </parameter>"
level=WARN source=qwen35.go:115 msg="qwen3.5 tool call parsing failed" error="XML syntax error on line 4: element <function> closed by </parameter>"
```

On each attempt the model had generated 265 tokens before the parse failed. The slot is released at
6,304 tokens every time, with `truncated = 0`. The retries are on 500, which `retry.statuses` includes. They
repeat identically because decoding is greedy (temperature 0, seed 42).

**Classification: (a), model output Ollama could not parse.** The model produced a qwen3.5 XML tool call
whose tags do not nest: a `<function>` closed by `</parameter>`. Ollama's qwen3.5 tool-call parser rejected
it and the server returned 500. The 265 generated tokens themselves were not saved: a 500 carries no
message, and the server log does not print generated text.

The other three causes are excluded:
- **Not (b), a context or size limit.** 6,040 + 1,536 is under the 16,384 context, and the log shows
  `truncated = 0`.
- **Not (c), a server crash.** The same runner served the next attempt each time, and then inj-025 with 200s.
  No restart is logged.
- **Not (d), our adapter or code.** The request was accepted and the prompt processed. The failure is in the
  server's parsing of the model's own output. The same conversation format had worked on turns 1–3 of this
  case and on every other case. The adapter only passed on the status and text it got.

**Rule applied (fixed before the diagnosis): (a), so inj-024 is scored as invalid_output, wrong.** It counts
as no diagnosis: no category, job, step, file, quotes or fix; confidence 0. The Ollama full run is
therefore scored on all 60 cases. The 59-case figures in `results/agent_v1-ollama/summary.md` (42/59,
macro-F1 0.698, Brier 0.2687) leave inj-024 out. The figures here add it as wrong; the saved files are
unchanged. Under the grader's rule, a missing diagnosis has confidence 0, so inj-024 adds 0 to the Brier
sum. That is why the 60-case Brier (0.2642) is a little lower than the 59-case one.

## Headline

All four runs, graded with v2.1. "Restore-type" fix match is over the 38 cases whose revert restores
lines. The 22 remove-type cases are left to a judge.

| metric | Gemini log-only | Gemini full | Ollama log-only | Ollama full |
|---|---|---|---|---|
| category accuracy | **47/60 (78.3%)** | **41/60 (68.3%)** | **51/60 (85.0%)** | **42/60 (70.0%)** |
| macro-F1 | **0.781** | **0.698** | **0.843** | **0.690** |
| failing_job correct | 60/60 | 60/60 | 60/60 | 59/60 |
| failing_step correct | 60/60 | 60/60 | 60/60 | 59/60 |
| suspect_file correct | 53/60 (88.3%) | 60/60 (100%) | 34/60 (56.7%) | 47/60 (78.3%) |
| grounding, cases with every quote grounded | 59/60 | 59/60 | 54/60 | 53/60 |
| grounding, quotes | 150/151 (99.3%) | 161/162 (99.4%) | 159/166 (95.8%) | 182/188 (96.8%) |
| quote rules followed, quotes / cases | 149/151 / 58/60 | 160/162 / 58/60 | 139/166 / 40/60 | 166/188 / 42/60 |
| quote rule breaks | RUN AND JOBS beyond a name 2 | RUN AND JOBS beyond a name 1, line-number prefix 1 | line-number prefix 15, section header 6, wrong source 5, ellipsis 3 | section header 8, wrong source 7, line-number prefix 6, ellipsis 1 |
| fix match exact / partial / none, restore-type (38) | 25 / 1 / 12 | 36 / 1 / 1 | 14 / 1 / 23 | 30 / 1 / 7 |
| remove-type (22): need a judge (no fix suggested) | 22 (2) | 22 (13) | 22 (16) | 22 (11) |
| action consistent with true category | 47/60 | 41/60 | 51/60 | 42/60 |
| Brier score | 0.2158 | 0.3132 | **0.1379** | 0.2642 |
| mean confidence, correct / wrong | 0.988 / 0.997 | 0.995 / 0.994 | 0.966 / 0.953 | 0.970 / 0.964 |
| ok / invalid_output / api_error | 60 / 0 / 0 | 60 / 0 / 0 | 60 / 0 / 0 | 59 / 1 (inj-024) / 0 |
| repair calls (all repaired) | 6 | 9 | 14 | 8 |

- **Log-only, the headline: Ollama 51/60, Gemini 47/60.** The local model with tools is the best log-only
  predictor so far. Gemini with tools is 2 below its one-call score.
- **Full mode: no gain from tools.** Gemini's agent is 9 below its one-call run (41 vs 50). Ollama's is level
  (42 vs 42). Both are far below rules v1 (58/60).
- **Brier.** Gemini's wrong answers are as confident as its right ones (0.994–0.997), so its Brier is the
  worst of any LLM run in full mode. Ollama log-only has the best Brier of any LLM run so far (0.138), from
  accuracy, not calibration.

## Comparison with D6a one-call v2 and rules v1

**How the baselines were scored.** The D6a and rules runs were saved with grader v1. For this table they
were re-graded with v2.1 by `regrade_run`, offline, with nothing written. Saved v1 grades reproduce
exactly. Category metrics are identical under every grader. The other v2.1 figures equal the published v2
figures (`reports/grader-v2-regrade.md`), because v2.1 changes nothing for predictors that saw no
history-tool output of their own.

**Log-only**

| metric | rules v1 | one-call v2, Gemini | agent v1, Gemini | one-call v2, Ollama | agent v1, Ollama |
|---|---|---|---|---|---|
| category accuracy | 39/60 (65.0%) | 49/60 (81.7%) | 47/60 (78.3%) | 43/60 (71.7%) | **51/60 (85.0%)** |
| macro-F1 | 0.593 | 0.783 | 0.781 | 0.683 | **0.843** |
| suspect_file correct | 0/60 | 40/60 | **53/60** | 18/60 | 34/60 |
| grounding, cases | **60/60** | 58/60 | 59/60 | 52/60 | 54/60 |
| quote rules followed, cases | **60/60** | 54/60 | 58/60 | 36/60 | 40/60 |
| fix exact, restore-type (38) | 0 | 12 | **25** | 5 | 14 |
| Brier score | **0.1270** | 0.1748 | 0.2158 | 0.2579 | 0.1379 |
| real_regression recall | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| flaky recall | 0/10 | 1/10 | 3/10 | 1/10 | **4/10** |
| dependency recall | 8/10 | 9/10 | 9/10 | **10/10** | 9/10 |
| environment recall | 1/10 | **10/10** | 9/10 | 6/10 | **10/10** |
| timeout recall | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| build recall | **10/10** | 9/10 | 6/10 | 6/10 | 8/10 |
| tokens in (all calls) | — | 113,014 | 756,104 | 110,501 | 2,174,304 |
| wall time per case, mean | — | 6.34 s | 19.33 s | 50.57 s | 155.14 s |

**Full**

| metric | rules v1 | one-call v2, Gemini | agent v1, Gemini | one-call v2, Ollama | agent v1, Ollama |
|---|---|---|---|---|---|
| category accuracy | **58/60 (96.7%)** | 50/60 (83.3%) | 41/60 (68.3%) | 42/60 (70.0%) | 42/60 (70.0%) |
| macro-F1 | **0.966** | 0.799 | 0.698 | 0.664 | 0.690 |
| suspect_file correct | **60/60** | 59/60 | **60/60** | 46/60 | 47/60 |
| grounding, cases | **60/60** | 59/60 | 59/60 | 53/60 | 53/60 |
| quote rules followed, cases | **60/60** | 57/60 | 58/60 | 45/60 | 42/60 |
| fix exact, restore-type (38) | **38** | 33 | 36 | 30 | 30 |
| Brier score | **0.0804** | 0.1599 | 0.3132 | 0.2733 | 0.2642 |
| real_regression recall | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| flaky recall | **8/10** | 1/10 | 3/10 | 1/10 | 2/10 |
| dependency recall | **10/10** | **10/10** | 6/10 | **10/10** | **10/10** |
| environment recall | **10/10** | **10/10** | 9/10 | 6/10 | 7/10 |
| timeout recall | **10/10** | **10/10** | 9/10 | **10/10** | 8/10 |
| build recall | **10/10** | 9/10 | 4/10 | 5/10 | 5/10 (+1 invalid_output) |
| tokens in (all calls) | — | 125,047 | 406,146 | 122,016 | 2,110,703 |
| wall time per case, mean | — | 7.97 s | 11.29 s | 51.16 s | 177.38 s |

**Case by case, with exact McNemar tests.** Each pair is compared on category correctness over the same 60
cases. The p-value is exact two-sided: 2 · P(X ≤ min(b, c)) with X ~ Binomial(b + c, ½), capped at 1. Ollama
full counts inj-024 as wrong.

| pair | both right | only the agent right | only the other right | both wrong | same label | exact p |
|---|---|---|---|---|---|---|
| agent vs one-call, Gemini log-only | 43 | 4: inj-022, 052, 053, 059 | 6: inj-018, 021, 023, 025, 028, 037 | 7 | 50/60 | **0.754** |
| agent vs one-call, Gemini full | 39 | 2: inj-052, 054 | 11: inj-012, 013, 016, 018, 019, 020, 022, 026, 027, 032, 044 | 8 | 47/60 | **0.0225** |
| agent vs one-call, Ollama log-only | 42 | 9: inj-019, 020, 033, 034, 035, 037, 053, 055, 056 | 1: inj-059 | 8 | 50/60 | **0.0215** |
| agent vs one-call, Ollama full | 37 | 5: inj-025, 028, 036, 037, 055 | 5: inj-022, 027, 038, 043, 044 | 13 | 48/60 | **1.000** |
| agent Gemini vs rules v1, log-only | 34 | 13: inj-029, 030, 031, 032, 033, 034, 035, 036, 052, 053, 055, 059, 060 | 5: inj-018, 021, 023, 025, 028 | 8 | 41/60 | **0.0963** |
| agent Gemini vs rules v1, full | 41 | 0 | 17: inj-012, 013, 016, 018, 019, 020, 022, 025, 026, 027, 032, 044, 049, 050, 053, 056, 058 | 2 | 43/60 | **1.53 × 10⁻⁵** |
| agent Ollama vs rules v1, log-only | 37 | 14: inj-029, 030, 031, 032, 033, 034, 035, 036, 037, 052, 053, 055, 056, 060 | 2: inj-024, 025 | 7 | 44/60 | **0.00418** |
| agent Ollama vs rules v1, full | 42 | 0 | 16: inj-019, 020, 022, 024, 027, 033, 035, 038, 043, 044, 049, 050, 053, 054, 056, 058 | 2 | 44/60 | **3.05 × 10⁻⁵** |
| agent Gemini vs agent Ollama, log-only | 45 | Gemini only, 2: inj-024, 059 | Ollama only, 6: inj-018, 021, 023, 028, 037, 056 | 7 | 51/60 | **0.289** |
| agent Gemini vs agent Ollama, full | 35 | Gemini only, 6: inj-024, 033, 035, 038, 043, 054 | Ollama only, 7: inj-012, 013, 016, 018, 025, 026, 032 | 12 | 44/60 | **1.000** |

Exact p, unrounded fractions: 772/1024, 184/8192, 22/1024, 1, 25,232/262,144, 2/131,072, 274/65,536,
2/65,536, 74/256, 1.

- **Agent vs one-call.** Two of the four differences reach p < 0.05: Ollama log-only, +8 cases (p = 0.021),
  and Gemini full, −9 cases (p = 0.022). Gemini log-only (−2) and Ollama full (0) are not distinguishable from
  no change.
- **Agent vs rules.** Full mode: rules v1 is right on every case either agent gets right, and on 16–17 more
  (p ≈ 2–3 × 10⁻⁵). Log-only: the Ollama agent beats the rules by 12 (p = 0.004). The Gemini agent's +8 is
  not significant (p = 0.096).
- **Gemini vs Ollama agent.** Not distinguishable in either mode (p = 0.29 and 1.0).
- **Multiple tests.** These ten tests were not pre-registered, and no multiplicity correction was planned.
  With Bonferroni over the ten (α = 0.005), only the three agent-vs-rules results with p < 0.005 survive:
  both full-mode losses and the Ollama log-only win. The two one-call differences do not.

**Label changes without any tool call.** In a case where the agent calls no tool, its input is the one-call
bundle. It differs from D6a in three ways only: the prompt (the Tools section and one dropped sentence), the
tool definitions offered, and an answer turn with no response schema. D6a's one call was
schema-constrained. Labels still change in those cases:

| run | cases with 0 tool calls | correctness vs one-call in those: gains / losses | cases with tool calls: gains / losses |
|---|---|---|---|
| Gemini log-only | 9 | 0 / 1 (inj-025) | 4 / 5 |
| Gemini full | 28 | 0 / **5** (inj-013, 016, 018, 032, 044) | 2 / 6 |
| Ollama log-only | 16 | 3 (inj-020, 035, 037) / 1 (inj-059) | **6 / 0** |
| Ollama full | 11 | 2 (inj-036, 037) / 1 (inj-043) | 3 / 4 |

So the D6b − D6a differences are not all caused by tool output. Gemini full loses 5 of its 11 cases without
reading anything. Ollama log-only's net +8 is +6 from cases where it used tools and +2 from cases where it
did not.

## Per category

| category | cases | Gemini log-only P / R / F1 | Gemini full P / R / F1 | Ollama log-only P / R / F1 | Ollama full P / R / F1 |
|---|---|---|---|---|---|
| real_regression | 10 | 0.45 / 1.00 / 0.62 (22 predicted) | 0.34 / 1.00 / 0.51 (29) | 0.59 / 1.00 / 0.74 (17) | 0.45 / 1.00 / 0.62 (22) |
| flaky | 10 | 1.00 / 0.30 / 0.46 (3) | 1.00 / 0.30 / 0.46 (3) | 1.00 / 0.40 / 0.57 (4) | 1.00 / 0.20 / 0.33 (2) |
| dependency | 10 | 0.90 / 0.90 / 0.90 (10) | 1.00 / 0.60 / 0.75 (6) | 0.82 / 0.90 / 0.86 (11) | 0.67 / 1.00 / 0.80 (15) |
| environment | 10 | 1.00 / 0.90 / 0.95 (9) | 1.00 / 0.90 / 0.95 (9) | 1.00 / 1.00 / 1.00 (10) | 1.00 / 0.70 / 0.82 (7) |
| timeout | 10 | 1.00 / 1.00 / 1.00 (10) | 1.00 / 0.90 / 0.95 (9) | 1.00 / 1.00 / 1.00 (10) | 1.00 / 0.80 / 0.89 (8) |
| build | 10 | 1.00 / 0.60 / 0.75 (6) | 1.00 / 0.40 / 0.57 (4) | 1.00 / 0.80 / 0.89 (8) | 1.00 / 0.50 / 0.67 (5) |

## Confusion matrices

Rows: true category. Columns: predicted. "(none)": no diagnosis (inj-024, invalid_output).

**Gemini, log-only**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 7 | 3 | 0 | 0 | 0 | 0 |
| dependency | 1 | 0 | 9 | 0 | 0 | 0 |
| environment | 0 | 0 | 1 | 9 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 4 | 0 | 0 | 0 | 0 | 6 |

**Gemini, full**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 7 | 3 | 0 | 0 | 0 | 0 |
| dependency | 4 | 0 | 6 | 0 | 0 | 0 |
| environment | 1 | 0 | 0 | 9 | 0 | 0 |
| timeout | 1 | 0 | 0 | 0 | 9 | 0 |
| build | 6 | 0 | 0 | 0 | 0 | 4 |

**Ollama, log-only**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build |
|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 |
| flaky | 6 | 4 | 0 | 0 | 0 | 0 |
| dependency | 1 | 0 | 9 | 0 | 0 | 0 |
| environment | 0 | 0 | 0 | 10 | 0 | 0 |
| timeout | 0 | 0 | 0 | 0 | 10 | 0 |
| build | 0 | 0 | 2 | 0 | 0 | 8 |

**Ollama, full**

| true \ predicted | real_regression | flaky | dependency | environment | timeout | build | (none) |
|---|---|---|---|---|---|---|---|
| real_regression | 10 | 0 | 0 | 0 | 0 | 0 | 0 |
| flaky | 8 | 2 | 0 | 0 | 0 | 0 | 0 |
| dependency | 0 | 0 | 10 | 0 | 0 | 0 | 0 |
| environment | 1 | 0 | 2 | 7 | 0 | 0 | 0 |
| timeout | 2 | 0 | 0 | 0 | 8 | 0 | 0 |
| build | 1 | 0 | 3 | 0 | 0 | 5 | 1 |

## Localisation and fix match by category

Fix exact is over restore-type cases only. Per category: real_regression 10, flaky 7, dependency 10,
environment 1, timeout 0, build 10.

| category | suspect_file GL / GF / OL / OF | fix exact (+partial) GL / GF / OL / OF |
|---|---|---|
| real_regression | 10 / 10 / 9 / 10 | 8 / 10 / 8 / 10 of 10 |
| flaky | 10 / 10 / 6 / 7 | 3 (+1) / 5 (+1) / 0 (+1) / 2 (+1) of 7 |
| dependency | 10 / 10 / 8 / 9 | 3 / 10 / 1 / 8 of 10 |
| environment | 9 / 10 / 3 / 9 | 1 / 1 / 0 / 1 of 1 |
| timeout | 4 / 10 / 0 / 3 | no restore-type case |
| build | 10 / 10 / 8 / 9 | 10 / 10 / 5 / 9 of 10 |
| **total** | **53 / 60 / 34 / 47** | **25 (+1) / 36 (+1) / 14 (+1) / 30 (+1) of 38** |

GL, GF, OL, OF: Gemini log-only, Gemini full, Ollama log-only, Ollama full.

- **Log-only localisation and fixes improve with tools, for both models.** Gemini's suspect file goes from
  40 to 53, its exact fixes from 12 to 25 of 38. Ollama's go from 18 to 34 and from 5 to 14. In log-only
  mode, `get_file` at the eval commit is how the agent reaches the changed application file; the one-call
  bundle shows only the workflow file.
- **Full mode.** Gemini reaches 60/60 files and 36/38 exact fixes (one-call: 59 and 33), level with rules v1
  on files and 2 short on fixes. Ollama is unchanged (47 vs 46; 30 vs 30).
- **Ollama timeout.** It names the workflow file for no timeout case in log-only mode (0/10), the same as
  its one-call run.

## Misclassified cases, across runs

Every case at least one agent or one-call run gets wrong. Columns: agent GL, GF, OL, OF, then one-call
GL, GF, OL, OF. `*` = wrong. Abbreviations: RR real_regression, FL flaky, DEP dependency, ENV environment,
TO timeout, BLD build, `—` no diagnosis.

| case | true | agent GL | agent GF | agent OL | agent OF | 1-call GL | 1-call GF | 1-call OL | 1-call OF |
|---|---|---|---|---|---|---|---|---|---|
| inj-012 | DEP | DEP | RR* | DEP | DEP | DEP | DEP | DEP | DEP |
| inj-013 | DEP | DEP | RR* | DEP | DEP | DEP | DEP | DEP | DEP |
| inj-016 | DEP | DEP | RR* | DEP | DEP | DEP | DEP | DEP | DEP |
| inj-018 | DEP | RR* | RR* | DEP | DEP | DEP | DEP | DEP | DEP |
| inj-059 | DEP | DEP | DEP | RR* | DEP | RR* | DEP | DEP | DEP |
| inj-019 | BLD | BLD | RR* | BLD | RR* | BLD | BLD | DEP* | DEP* |
| inj-020 | BLD | BLD | RR* | BLD | DEP* | BLD | BLD | DEP* | DEP* |
| inj-021 | BLD | RR* | BLD | BLD | BLD | BLD | BLD | BLD | BLD |
| inj-022 | BLD | BLD | RR* | BLD | DEP* | ENV* | BLD | BLD | BLD |
| inj-023 | BLD | RR* | BLD | BLD | BLD | BLD | BLD | BLD | BLD |
| inj-024 | BLD | BLD | BLD | DEP* | —* | BLD | BLD | DEP* | DEP* |
| inj-025 | BLD | RR* | RR* | DEP* | BLD | BLD | RR* | DEP* | DEP* |
| inj-026 | BLD | BLD | RR* | BLD | BLD | BLD | BLD | BLD | BLD |
| inj-027 | BLD | BLD | RR* | BLD | DEP* | BLD | BLD | BLD | BLD |
| inj-028 | BLD | RR* | BLD | BLD | BLD | BLD | BLD | BLD | RR* |
| inj-032 | ENV | ENV | RR* | ENV | ENV | ENV | ENV | ENV | ENV |
| inj-033 | ENV | ENV | ENV | ENV | RR* | ENV | ENV | RR* | RR* |
| inj-034 | ENV | ENV | ENV | ENV | ENV | ENV | ENV | RR* | ENV |
| inj-035 | ENV | ENV | ENV | ENV | DEP* | ENV | ENV | DEP* | DEP* |
| inj-036 | ENV | ENV | ENV | ENV | ENV | ENV | ENV | ENV | DEP* |
| inj-037 | ENV | DEP* | ENV | ENV | ENV | ENV | ENV | DEP* | DEP* |
| inj-038 | ENV | ENV | ENV | ENV | DEP* | ENV | ENV | ENV | ENV |
| inj-043 | TO | TO | TO | TO | RR* | TO | TO | TO | TO |
| inj-044 | TO | TO | RR* | TO | RR* | TO | TO | TO | TO |
| inj-049 | FL | RR* | RR* | RR* | RR* | RR* | RR* | RR* | RR* |
| inj-050 | FL | RR* | RR* | RR* | RR* | RR* | RR* | RR* | RR* |
| inj-051 | FL | RR* | RR* | RR* | RR* | RR* | RR* | RR* | RR* |
| inj-052 | FL | FL | FL | FL | FL | RR* | RR* | FL | FL |
| inj-053 | FL | FL | RR* | FL | RR* | RR* | RR* | RR* | RR* |
| inj-054 | FL | RR* | FL | RR* | RR* | RR* | RR* | RR* | RR* |
| inj-055 | FL | FL | FL | FL | FL | FL | FL | RR* | RR* |
| inj-056 | FL | RR* | RR* | FL | RR* | RR* | RR* | RR* | RR* |
| inj-057 | FL | RR* | RR* | RR* | RR* | RR* | RR* | RR* | RR* |
| inj-058 | FL | RR* | RR* | RR* | RR* | RR* | RR* | RR* | RR* |

**What the wrong answers say.** The root causes were read for every case where the agent and its one-call
baseline differ:

- **Gemini: the right cause, labelled real_regression.** Every one of Gemini full's 11 losses is
  real_regression. Each root cause names the injected change exactly and calls it "a code change" or "a
  commit". Examples: `numpy==99.0.0` (inj-012); `python:3.99-slim` (inj-019); `apii/` and `evall/` in a
  Dockerfile `COPY` (inj-026, 027); `EMBEDDING_NORMALIZE: "maybe"` (inj-032); a 600 s sleep against a
  3-minute limit (inj-044). The suspect file and fix are exact. Gemini log-only's 4 build losses (inj-021,
  023, 025, 028) are the same pattern: it reads the Dockerfile and calls the typo a regression.
- **Ollama full: read as dependency.** Two build cases, inj-022 and inj-027 (a misspelt `COPY` source), and
  one environment case, inj-038 (`python-version` 3.99), are called dependency. Each came after 7 tool calls
  that ran to the turn limit.
- **Flaky: the right mechanism, labelled real_regression.** As in D6a, several wrong flaky answers describe
  the nondeterminism exactly. Gemini log-only: `random.choice` that can pick "AI" (inj-049), a
  `random.randint(1900, 2100)` length (inj-050), `next(iter({"hi", "hello"}))` (inj-051), set-ordered
  parametrize (inj-056). Each is still labelled real_regression at 0.99–1.00.

## Agent behaviour

| | Gemini log-only | Gemini full | Ollama log-only | Ollama full |
|---|---|---|---|---|
| model turns per case, mean / median / max | 3.13 / 2.5 / 8 | 1.78 / 2 / 6 | 6.10 / 8 / 8 | 6.20 / 8 / 8 |
| turns used, distribution | 1: 9, 2: 21, 3: 8, 4: 9, 5: 5, 6: 5, 7: 2, 8: 1 | 1: 28, 2: 24, 3: 5, 4: 1, 6: 2 | 1: 16, 6: 1, 8: 43 | 1: 11, 3: 2, 4: 2, 5: 2, 6: 3, 7: 1, 8: 39 |
| tool calls, total (run / repeats) | 128 (128 / 0) | 47 (47 / 0) | 315 (225 / 90) | 313 (265 / 48) |
| tool calls per case, mean / median | 2.13 / 1.5 | 0.78 / 1 | 5.25 / 7 | 5.22 / 7 |
| tool calls per case, distribution | 0: 9, 1: 21, 2: 8, 3: 9, 4: 5, 5: 5, 6: 2, 7: 1 | 0: 28, 1: 24, 2: 5, 3: 1, 5: 2 | 0: 16, 5: 1, 7: 34, 8: 9 | 0: 11, 2: 2, 3: 2, 4: 2, 5: 3, 6: 1, 7: 38, 8: 1 |
| tool calls by tool | get_file 90, list_runs 21, get_jobs 7, get_job_log 4, extract_failure_block 3, get_run 3 | get_file 22, get_commit 21, compare 3, get_jobs 1 | get_file 202, get_job_log 61, extract_failure_block 23, get_run 22, get_jobs 4, list_runs 3 | get_file 198, compare 41, get_commit 29, get_job_log 17, extract_failure_block 13, get_run 11, get_jobs 3, list_runs 1 |
| results "not available" | 4 (get_file 4) | 6 (get_commit 6) | 37 (get_job_log 18, get_file 16, extract_failure_block 3) | 21 (get_job_log 10, get_file 8, compare, get_run, get_commit 1 each) |
| answer forced by the turn limit (turn 8) | 1 (inj-003) | 0 | **43** | **39** |
| accuracy, forced / not forced | 1/1 / 46/59 | — / 41/60 | 35/43 / 16/17 | 25/39 / 17/21 |
| repeated_calls (cases with any) | 0 | 0 | 90 (29) | 48 (22) |
| context_forced / results dropped | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| model calls: turn / answer / repair | 187 / 1 / 6 | 107 / 0 / 9 | 323 / 43 / 14 | 333 / 39 / 8 |
| attempts (retries) | 195 (1: one 503) | 116 (0) | 380 (0) | 384 (4: inj-024's 500s) |
| tokens in / out | 756,104 / 23,446 | 406,146 / 21,110 | 2,174,304 / 36,391 | 2,110,703 / 43,210 |
| tokens in, max per call / tokens out, max per call | 8,267 / 465 | 5,102 / 444 | 15,321 / 1,536 | 13,903 / 587 |
| thinking tokens | 0 | 0 | 0 | 0 |
| latency per call, mean / median / max | 1.62 / 1.23 / 7.01 s | 1.11 / 1.14 / 1.94 s | 24.43 / 11.66 / 380.69 s | 26.84 / 17.97 / 153.97 s |
| wall time per case, mean / max | 19.33 / 62.68 s | 11.29 / 42.00 s | 155.14 / 492.17 s | 177.38 / 478.08 s |
| wall time, whole run | 19.3 min | 11.3 min | 155.1 min | 177.4 min (+ the earlier inj-024 passes) |
| prompt size estimate, max (3 chars/token) | 8,883 | 5,638 | 10,606 | 10,380 |
| Gemini attempts against the daily cap | 195 | 116 | — | — |

**Notes on the behaviour figures**
- **Gemini daily cap.** Both Gemini runs fall on Pacific-time day 2026-11-13. `results/llm_call_ledger.json`
  records 311 attempts for that day, exactly 195 + 116: 311 of the 450 cap, with no other Gemini use. The one
  retry was a 503 that answered on the next try.
- **Tool calls.** "Repeats" are calls with the same name and arguments as an earlier call in the case. The
  guard answers them from the earlier result and does not run them again. Tool-call counts here are the
  agent's own. The graded `tool_calls` field also counts the bundle's 5–6 calls.
- **Ollama turn limit.** Ollama uses its 7 tool turns in most cases (43 and 39 runs to the limit), largely by
  re-reading files. In Ollama log-only, inj-019 calls `get_file Dockerfile` seven times. The answer is then
  forced on turn 8 with no tools and the response schema. Forced cases are 35/43 right in log-only mode and
  25/39 in full.
- **Ollama token counts.** Ollama's `tokens_in` equals the server's full prompt length (`task.n_tokens` in
  the server log; inj-024: 3,749, 4,102, 5,984). That means the config comment that Ollama's counts "skip its
  cached prompt" does not hold for this server version. The 2.1 million tokens in are 17–20× the one-call
  runs, mostly re-sent context.
- **Context.** No case was `context_forced`, and no result was dropped. But in 5 Ollama log-only answer calls
  (inj-011, 014, 015, 016, 017), the real prompt (14,968–15,321 tokens) plus the 1,536 output cap exceeds
  16,384, so the record flags `context_overflow`. The 3-chars-per-token estimate had put them at
  10,263–10,550. The server log shows `truncated = 0` on every request, and KV-cache shifting was disabled
  by the server. Answers of 177–392 tokens fit in the room left, and all 5 are right.
- **Ollama, longest call.** inj-003 log-only, turn 6: 1,536 tokens out (`finish_reason` length) in 381 s. It
  wrote prose ("Looking at t…") instead of a tool call or JSON, and the repair call fixed it.

## Flaky cases and list_runs

Flaky cases are inj-049 to inj-058.

| run | flaky cases that called list_runs | list_runs turn | agent category | D6a one-call category | changed |
|---|---|---|---|---|---|
| Gemini log-only | inj-049 | 1 | RR | RR | no |
| | inj-050 | 1 | RR | RR | no |
| | inj-051 | 3 | RR | RR | no |
| | **inj-053** | 3 (its last tool call) | **FL** | RR | **yes** |
| | inj-056 | 1 | RR | RR | no |
| | inj-057 | 1 | RR | RR | no |
| Gemini full | none (list_runs never called) | | | | |
| Ollama log-only | inj-052 | 7 (its last tool call) | FL | FL | no |
| Ollama full | none (its one list_runs call is inj-030, an environment case) | | | | |

- **What list_runs showed.** Every flaky call above asked for `{branch: main, limit: 5}`. Each got the failed
  run plus one or two earlier green runs, **each on a different head_sha** from the failed run. None showed
  the same commit passing.
- **No agent quoted history** in any flaky diagnosis.
- **inj-053 (Gemini log-only), the one change after a list_runs call.** It read `tests/test_behaviour.py` and
  `api/services/chunking_service.py` on turns 1–2, then called list_runs on turn 3. The root cause and all
  three quotes come from the log and the test file: a worker thread with `time.sleep(random.uniform(0.0,
  0.4))`, against a fixed `time.sleep(0.2)` before the assertion. The record gives no sign that list_runs
  caused the change, and a single run cannot rule out a contribution.
- **So no category change from D6a can be traced to list_runs.** The 8 flaky cases that did change, all
  real_regression → flaky, read the test file with `get_file`, and most never called list_runs:

| run | flaky cases that changed to flaky |
|---|---|
| Gemini log-only | inj-052, 053 |
| Gemini full | inj-052, 054 |
| Ollama log-only | inj-053, 055, 056 |
| Ollama full | inj-055 |

  No flaky case that D6a got right went wrong with the agent: inj-052 (Ollama) and inj-055 (Gemini) stay
  flaky in both modes.
- **list_runs use overall.** Gemini log-only made 21 calls in 20 cases: 7 real_regression, 6 flaky,
  2 dependency, 3 environment, 2 timeout. Gemini full made none. Ollama made 3 calls in log-only (inj-052,
  inj-027) and 1 in full (inj-030).

## Ollama "1 turn, 0 tool calls, repaired" cases

There are 21 such cases: 13 in log-only mode and 8 in full. In each, the first answer was free text on turn
1, with tools offered and no schema. It failed validation (`Invalid JSON: invalid number at line 1
column 2`) and was fixed by the one schema-constrained repair call.

**Every first answer is a full diagnosis written as the prompt's field list**, not as JSON:

```
- category: dependency
- confidence: 0.98
- failing_job: Import & route smoke tests
- failing_step: App imports on a clean machine
- suspect_file: null
- evidence: [{"quote": "ValueError: numpy.dtype size changed, ...", "source": "log"}, ...]
- root_cause: The sklearn library's murmurhash module has a binary incompatibility ...
- suggested_fix: null
- recommended_action: fix_dependency
```

That is Ollama log-only inj-060, abridged. All 21 begin `- category: <label>`.

**Were any of them tool calls?** No. The adapter takes tool calls only from Ollama's structured
`message.tool_calls`. Each raw answer was checked for tool-call text: `<tool_call>`, `<function`,
`"name":`, `"arguments":`, or any of the eight tool names. None matched. In each case the model answered on
the spot, and the answer was taken as its answer, which is how the loop is registered to work.

| run | cases | first-answer label → final label |
|---|---|---|
| Ollama log-only | inj-020, 021, 022, 028, 029, 030, 031, 035, 036, 037, 041, 059, 060 | unchanged in all 13; 12 right, inj-059 wrong (RR) |
| Ollama full | inj-015, 018, 035, 036, 037, 042, 043, 047 | unchanged in 7; **inj-035: environment → dependency** after the repair (right → wrong); inj-043 wrong (RR) in both |

**Other repairs.** The 22nd Ollama repair is inj-003 (log-only): turn 6 was 1,536 tokens of prose, as noted
above. Gemini's 15 repairs (6 log-only, 9 full) are free-form answers too:
- 13 misspell a field name: `suspend_file`, `suggested_file`, `suggested_gif`, `suggested_llm_fix`,
  `root__cause`, `fail_job`, `evidence[].ico`, `evidence[].Quote`;
- 2 are invalid JSON (inj-045, inj-051, full).

All repairs kept the first answer's label, except Ollama full inj-035.

## Reading the result

- **Log-only, the headline: tools help the small local model, not the hosted one.**
  - Ollama gains 8 cases over its one-call run (51 vs 43, p = 0.021) and beats rules v1 by 12 (p = 0.004).
    Its gains are environment (+4), build (+2) and flaky (+3).
  - Gemini with tools is 2 below its one-call run (47 vs 49, p = 0.75). It gains 2 flaky cases, but loses
    build cases it now explains correctly and labels real_regression.
- **Full mode: the agent does not help.** Gemini loses 9 cases (41 vs 50, p = 0.022), all to real_regression.
  Ollama is level (42 vs 42). Rules v1 stays far ahead (58).
- **Gemini's failure mode is labelling, not evidence.** With tools it localises better than ever: suspect
  file 60/60, exact fix 36/38 in full mode. But it calls any change it can see "a code change", so
  real_regression takes 29 of 60 full-mode labels. Five of its full-mode losses had no tool call at all.
- **Part of the D6b − D6a difference is not the tools.** In zero-tool cases the agent sees exactly the
  one-call bundle. Labels still change there: 13 cases across the four runs, 5 gains and 8 losses. The
  prompt, the tool definitions and the unconstrained answer turn all differ from D6a, so D6b vs D6a does
  not measure tools alone.
- **Flaky improves a little**, from 1/10 to 2–4/10, by reading the test file. Run history did not help
  (previous section).
- **Calibration has not improved.** Gemini's mean confidence on wrong answers (0.994–0.997) is as high as on
  right ones.
- **Cost.**
  - Gemini: 3–7× the tokens of one-call. Both Gemini runs together used 311 of the 450-attempt daily cap.
  - Ollama: about 18× the tokens, and 155–177 s per case against about 51 s.
  - Ollama ran to the turn limit in 39–43 of 60 cases, with 48–90 repeated calls.
- **Small sample, single runs.** There are 10 cases per category, so each case moves recall by 0.10. These
  are single runs at temperature 0, seed 42; run-to-run variance was not measured. The McNemar tests were
  not pre-registered.

Per-case results are in `results/agent_v1-<backend>[-log-only]/cases.jsonl` (gitignored run output). Each
record holds:
- status, every model call (model_version, tokens, latency, retries, raw output) and every turn;
- every tool call (name, arguments, output size, shown, repeat_of);
- the bundle, the environment and the v2.1 grades.

`summary.md` is the runner's own summary. For Ollama full it scores 59 cases, without inj-024.

## Settings (pre-registered, verbatim)

```toml
# D6b: the tool-using agent. Pre-registered settings, committed before any evaluation run.
# Every report of this agent prints this file verbatim. Changing a value makes a new version
# (agent_v2.toml, ...); never edit this file after its first evaluation.

name = "agent_v1"
prompt = "prompts/agent_v1.md"     # prompts/one_call_v2.md, one sentence dropped, plus a Tools section
bundle = "configs/one_call_v2.toml"  # the first user turn is exactly this config's bundle for the mode

[agent]
max_turns = 8               # model turns per case, the answer turn included; the repair call is extra
# Turns 1 to max_turns - 1 offer the mode's tools and leave the answer unconstrained; a turn that calls
# no tool is the answer. Turn max_turns offers no tools and constrains the answer to the Diagnosis
# schema. So does any earlier turn when the conversation plus one more answer would not fit the context
# budget (recorded as context_forced); tool results that still do not fit are dropped, never shown,
# and so never count as seen for grounding.

[generation]                # both backends
temperature = 0.0
max_output_tokens = 1536
repair_calls = 1            # one follow-up call if the answer is not a valid Diagnosis; still invalid: invalid_output

[context]                   # both backends, so the two see the same conversations
context_tokens = 16384      # system prompt, tool definitions, every turn so far and the answer must fit
reserve_tokens = 256        # chat template and slack, on top of max_output_tokens
chars_per_token = 3.0       # the estimate the check uses (Ollama's own counts skip its cached prompt)

[gemini]
model = "gemini-3.5-flash-lite"
thinking_level = "minimal"
seed = 42
max_requests_per_minute = 10
daily_call_cap = 450        # attempts per Pacific-time day, retries and repairs included, shared with D6a
timeout_s = 120

[ollama]
model = "qwen3.5:4b"
think = false
num_ctx = 16384             # = context_tokens
seed = 42
host = "http://localhost:11434"
timeout_s = 600

[retry]
max_tries = 5               # attempts per model call, the first one included
statuses = [429, 500, 503, 504]  # a client-side timeout counts as 504
backoff_initial_s = 5.0
backoff_factor = 2.0
backoff_max_s = 60.0

# The registered run plan, in order. Log-only runs first for each backend: it is the headline condition.
# The runner refuses a run until every earlier run of the same backend has all 60 cases scored.
[[run]]
backend = "gemini"
mode = "log-only"

[[run]]
backend = "gemini"
mode = "full"

[[run]]
backend = "ollama"
mode = "log-only"

[[run]]
backend = "ollama"
mode = "full"
```

## Not applied

These are ideas for a later, separately pre-registered version. **Nothing here was applied**: agent v1
stays as scored, and no code, prompt or config was changed for this report.

1. **A tool call the server cannot parse is model output, not an api_error.** Ollama's
   `qwen3.5 tool call parsing failed` 500 (inj-024) is deterministic at temperature 0, so the 5 retries ×
   3 passes only repeated it.
   - Map that error to invalid_output, or to a repair-style turn that shows the model the parse error.
   - Do not retry it.
   - Both are code changes.
2. **Constrain or structure the answer turn.** The free-form answer turn caused all 37 repairs:
   - 21 Ollama field-list answers;
   - 13 Gemini misspelt field names and 2 invalid JSON;
   - 1 Ollama prose answer cut off at the output limit.

   It may also cause some zero-tool label changes from D6a: 13 cases, 8 of them losses. Options: a
   `submit_diagnosis` tool whose parameters are the Diagnosis schema, or a JSON example in the Answer
   section.
3. **Repeated reads.** Ollama ran to the turn limit in 82 of 120 cases, with 138 repeated calls. Options:
   force the answer turn after N repeats, or make the repeat note say that the result is already in the
   conversation.
4. **Recalibrate the context estimate.** At 3 characters per token, it underestimated Ollama's real prompt
   by up to about 47% (10,439 vs 15,321 tokens). That makes 5 answer calls over the budget the check thinks
   it enforces. Options: measure characters per token on tool JSON, or use the server's own count once one
   is known. Also correct the config comment about cached prompt counts.
5. **Labels for visible changes.** Gemini labels a correctly explained Dockerfile, requirements or workflow
   change real_regression ("a code change"). This extends D6a items 3 and 4: say beside the verbatim
   rulebook definitions that "code" in real_regression means application code. Docker build failures are
   build; dependency pins are dependency.
6. **Flaky history.** In this scope, list_runs never shows the same commit passing, so history cannot give
   the flaky signal. Either expose same-sha reruns if the lab has them, or drop the expectation that history
   helps flaky.
7. **Keep every pass of a resumed case.** `--resume` replaces an api_error record, so inj-024's first two
   passes survive only in the Ollama server log. An append-only attempt log would keep them. It is a code
   change.
8. **Brier for a missing diagnosis.** The grader gives an invalid_output confidence 0, so it adds 0 to the
   Brier sum. Scoring it as a uniform 1/6, or reporting Brier over answered cases only, would avoid a missing
   answer looking well calibrated. It is a grader change.
9. **Unchanged from D6a:** calibration (item 5) and a judge for the 22 remove-type fixes.
