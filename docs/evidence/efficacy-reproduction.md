# Reproducing the bare versus Mythify comparison

## What this evidence shows

The sanitized July 13, 2026 run in `codex-word-count-2026-07-13.json` contains two paired trials of one small Python bug-fix scenario. Each pair used the same Codex CLI installation and the same external verifier.

- Bare control: the model received only the task, scenario, verifier command, and reporting request. Mythify and its protocol files were absent from the workspace.
- Mythify treatment: the model received the same task and verifier plus the installed Mythify protocol and fast-profile steering.
- Pair order: bare first, Mythify second.
- External verifier: `python3 -m unittest` in each isolated task workspace.
- Result: bare and Mythify both passed 2 of 2 trials. The measured task-success effect was a tie.
- Evidence recording: both Mythify trials recorded one executed, passing
  verification for the exact expected command, `python3 -m unittest`. The
  harness parsed the JSONL records; attested, failed, malformed, or
  different-command records do not count. Bare trials had no Mythify evidence
  store by design.
- Billing posture: authenticated subscription access was used. Monetary dollars and subscription quota consumption were not measured, so no cost value is claimed.

This is a reproducible smoke comparison, not proof that Mythify improves task success. The evidence-recording difference confirms treatment behavior, but it is not an independent efficacy outcome. The observed duration difference is also not a reliable speed claim because the sample is tiny, the order was fixed, and service load can vary.

## Reproduction command

Prerequisites are an authenticated `codex` CLI and Python 3. Run from the repository root:

```sh
python3 scripts/local_model_eval.py \
  --engine codex-cli \
  --scenario word_count_bugfix \
  --repeat 2 \
  --timeout 120 \
  --require-pass \
  --billing-posture subscription_included_authentication \
  --monetary-cost-status not_measured \
  --subscription-quota-status not_measured \
  --summary-output /tmp/mythify-efficacy-summary.json
```

The harness runs each bare and Mythify condition in a fresh temporary workspace. It deletes those workspaces unless `--keep-workspaces` is supplied. The summary is machine-readable JSON with a versioned schema and an explicit evidence status.

Do not commit the full console report or a file written with `--json-output`. Full reports contain temporary paths and bounded model and verifier output tails. Only review or publish `--summary-output` after checking that it contains no paths, output tails, prompts, or credentials.

To reproduce the published sanitized bytes from a retained local raw report without rerunning any model, annotate it in memory with the explicit billing posture:

```sh
python3 scripts/local_model_eval.py \
  --sanitize-existing-report /tmp/mythify-efficacy-full-local.json \
  --summary-output docs/evidence/codex-word-count-2026-07-13.json \
  --billing-posture subscription_included_authentication \
  --monetary-cost-status not_measured \
  --subscription-quota-status not_measured
```

This path reads the raw report, adds the supplied cost metadata in memory, and writes only the sanitized summary. It never modifies the raw report and never starts a model process.

## Published run context

- Date: July 13, 2026
- Engine: Codex CLI 0.144.1 using the account's default model selection
- Scenario: `word_count_bugfix`
- Repeats: 2 paired trials
- Mythify profile: `auto`, resolved to `fast` for this focused scenario
- Sanitized artifact status: `available_repeated_trials`
- Monetary cost: not measured, with no dollar value recorded
- Subscription quota consumption: not measured
- Raw report and temporary workspaces: not retained in the repository

The model identifier was not pinned, so a future account default may select a different model. CLI version, model alias resolution, service load, account settings, and platform can all change the result. Two pairs on one scenario are not statistically powered and do not establish generality. A stronger efficacy claim requires more scenarios, randomized or counterbalanced order, more repetitions, pinned model metadata where available, and analysis planned before the run.

## Runtime modularity guard

The first-party runtime guard recursively counts every non-whitespace physical line, including comments and docstrings, in Python descendants under `scripts/` and JavaScript descendants under `mcp-server/src/`. It excludes `.venv`, `__pycache__`, and `node_modules` directories. The ceiling is 1,500 lines per file.

```sh
python3 scripts/check_runtime_source_size.py
```

Use `--json` for a stable machine-readable report. The guard covers `scripts/mythify.py`, `mcp-server/src/fanout.js`, and `mcp-server/src/workflow-tools.js` explicitly through repository tests.
