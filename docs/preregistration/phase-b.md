# Phase B pre-registration: holdout, v2 changes, evaluation plan

Sealed by the commit that adds this file. When this was written:
- the holdout did not exist, and no lab file was opened for it;
- no code was changed and no model was called;
- the `lab/` submodule was at ci-triage-lab v1.0 (`2b362bc`).

Phase A's results are frozen at the tags `results-v1` and `results-v1.0.1`. Its 60 cases are spent
(`reports/d7-summary.md`, limitation 13), so phase B is scored on a new holdout.

## 1. Holdout spec (ci-triage-lab v1.1)

**Size and scope**
- 30 new cases, `inj-061` to `inj-090`: 5 per category for real_regression, flaky, dependency,
  environment, timeout and build.
- The same app and the same CI workflow as v1.0. Each case is one injected change.
- The 60 v1.0 cases are untouched: same commits, runs, logs, labels and files.

**Labels and data format**
- Labels are isolated exactly as in v1.0. Each case's injection record, category and revert stay in the
  lab's `injections/` data, which the replay hides from predictors.
- The same index, audit and API-snapshot formats as v1.0. The release is tagged `v1.1`.

**Injection families.** Families are taken from a general taxonomy of CI failures and must differ from the
v1.0 families. Before tagging, the lab builder checks each one against the v1.0 manifest; a family that
turns out to repeat a v1.0 mechanism is replaced from the same category's list, and the swap is reported.
Each category's 5 cases are spread over its families, at most 2 per family. No case-level detail is
fixed here.

| category | holdout families |
|---|---|
| real_regression | boundary or off-by-one change in a loop, slice or range; changed default argument value; inverted comparison or boolean operator; changed error handling (a different exception type, or an error swallowed); renamed or dropped field in a returned object |
| flaky | dependence on the wall-clock date or hour; dependence on filesystem enumeration order; dependence on the completion order of concurrent async tasks; collision on a shared temporary file or other process-wide resource; ordering by generated unique ids |
| dependency | a pin to a release whose `python_requires` excludes the runner's Python; a package installed without an optional extra the code needs; a pin with no wheel for the platform, forcing a source build that fails; a hash mismatch under `--require-hashes`; a new release that changes a default behaviour without removing any API |
| environment | a required environment variable missing entirely (not set to a bad value); a wrong working directory or `PYTHONPATH`; a locale or encoding setting that breaks text handling; a script CI invokes that lacks execute permission; test-discovery configuration that collects no tests or the wrong ones |
| timeout | a deadlock (a lock acquired twice, or two waits on each other); a command blocked waiting on stdin; polling for a local service that never starts; an unbounded retry loop; an algorithmic slowdown that is still correct but far too slow |
| build | `COPY --from` naming a stage that does not exist; an unknown or malformed Dockerfile instruction; a required build argument with no default and none passed; `.dockerignore` excluding a file the build needs; `USER` switching to an account that does not exist, or a permission error in `RUN` |

**Same-commit reruns.** In every category, exactly 2 of the 5 cases (12 of 30) get an earlier run of the
same commit before the triaged run:
- in the 2 flaky cases, that earlier run passed;
- in the other 10 cases, it failed in the same way as the triaged run.

So the presence of a rerun says nothing on its own; only its outcome does. The other 18 cases have no
same-commit rerun.

**Submodule and order of work**
- The agent repo's `lab/` submodule stays at v1.0 until every phase-B predictor (section 2) is sealed.
- The holdout is built after that sealing, from this spec only.
- If the order has to change, the change is reported with the results.

## 2. v2 changes

Each change is general and names no case. They make **agent v2** (new files `configs/agent_v2.toml` and
`prompts/agent_v2.md`, beside the unchanged v1 files). Change f applies to the replay for every predictor.

**a. Answer through a tool.** The agent answers by calling `submit_diagnosis`, whose parameters are the
Diagnosis JSON schema (the same schema the answer turn uses now).
- Turns 1 to 7 offer the mode's tools plus `submit_diagnosis`. The answer turn offers `submit_diagnosis`
  only, or the schema-constrained answer where a backend cannot force a tool call.
- A turn with neither a tool call nor a submission is answered with the repair message once, asking for
  `submit_diagnosis`. A submission that fails validation also gets one repair call, with the errors.
- Still no valid diagnosis: invalid_output, scored as wrong.

**b. One sentence beside the verbatim definitions.** The category definitions stay verbatim. This sentence
is added after them, not inside them: "In real_regression, "code" means the application's own source code;
build files, dependency pins, test-runner configuration and the workflow belong to their own categories."

**c. Repeated calls end the search.** A repeated call is the same tool with the same arguments as an
earlier call in the case. It is answered from the earlier result as in v1. After the second repeated call
in a case, the next model turn is the answer turn, recorded as `repeat_forced`.

**d. Unparseable tool calls.** A server error that says the server could not parse the model's tool call
is not retried. The detection rule matches the server's error text, is fixed in code and is covered by a
test.
- The first time it happens in a case, the error is shown to the model once, as an error turn that counts
  as a turn.
- A second time in the same case: invalid_output.
- Other errors keep the v1 retry rules.

**e. Context estimate.** The context check uses characters-per-token measured on tool JSON, per backend
tokenizer, from the v1.0 development data: server-counted prompt tokens against prompt characters, taking
the low (conservative) end. When a server count is available, the check uses the server's own count for
the conversation so far, plus the estimate for the new content only.

**f. Replay shows same-commit reruns.** For every predictor, `list_runs` and `get_run` show earlier runs of
the triaged commit that were created before the triaged run. A test checks that all 60 v1.0 cases still
give byte-identical bundles and tool outputs in both modes.

**New predictor: hybrid v1.** Rules v1 decide whenever their rules 1–6 fire, and their diagnosis is the
answer. When they fall back (rule 7), agent v2 decides, and its diagnosis is the answer. Hybrid v1 is scored
by combining, case by case, the rules v1 run with the agent v2 run on the same backend and mode, so it makes
no model calls of its own. The live predictor implements the same rule and must give the same answer on
every saved case.

**Unchanged:**
- rules v1, one-call v2 and agent v1 (code paths, configs, prompts);
- grader v2.1;
- models, temperature 0, seed 42, 1,536 output tokens, 8 turns and one repair call.

Tests keep the v1 paths selectable and unchanged. Development of v2 uses the v1.0 cases only.

## 3. Evaluation plan on the holdout

**Runs**
- Five predictors: rules v1, one-call v2, agent v1, agent v2 and hybrid v1.
- Each LLM predictor runs on both backends (`gemini-3.5-flash-lite`, `qwen3.5:4b`), in both modes.
- For each backend and predictor, log-only runs before full; log-only is the headline condition.
- Rules v1 have no backend: they run once per mode.
- Hybrid v1 is computed from the rules and agent v2 runs.

**Scoring**
- Grader v2.1, over all 30 holdout cases. Category accuracy is the primary metric.
- invalid_output counts as wrong.
- An api_error is re-run with `--resume` at most twice. If it persists, it is diagnosed as in D6b:
  - if the cause is model output the server cannot parse, the case counts as invalid_output;
  - otherwise the run is reported on n − k cases, plus a conservative 30-case figure counting those cases
    as wrong.

**Primary tests: exactly 4.** Each is an exact two-sided McNemar test on category correctness, log-only mode,
Bonferroni over the 4 (α = 0.0125 each):

| # | comparison | backend |
|---|---|---|
| P1 | agent v2 vs agent v1 | Gemini |
| P2 | agent v2 vs agent v1 | Ollama |
| P3 | hybrid v1 vs rules v1 | Gemini |
| P4 | hybrid v1 vs rules v1 | Ollama |

**Secondary.** Everything else is secondary and exploratory, reported without significance claims:
- full mode;
- every other pair;
- per-category recall, localisation, fix match, grounding, quote rules and Brier;
- tokens, time, turns and tool use;
- the effect of same-commit reruns on flaky recall.

**Power.** 30 cases give low statistical power. To reach p < 0.0125 the discordant split must be at least:

| losses | wins needed | net gain |
|---|---|---|
| 0 | 8 | 8 cases (27 points) |
| 1 | 10 | 9 cases |
| 2 | 13 | 11 cases |
| 3 | 15 | 12 cases |

For comparison, phase A's largest agent gain (Ollama log-only, 9 wins to 1 loss) has p = 0.021, so it would
not pass this threshold. A real improvement of a few cases will usually not be significant here, and a
non-significant primary test is not evidence of no effect.

## 4. Frozen after sealing

The holdout spec (section 1), the v2 change list (section 2) and the evaluation plan (section 3) are frozen
when this file is committed.
- Any later change to them, or to a phase-B predictor after it is sealed, is called **v3** and reported as
  such, alongside the results it affects.
- A family swap under section 1's rule is reported, but it is not a v3 change.
- Phase-B predictors are sealed one commit at a time, each "(pre-registered, not yet evaluated)", before
  the submodule moves to v1.1.
