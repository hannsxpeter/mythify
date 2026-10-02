# Efficacy smoke run, July 13, 2026 (historical record)

This page records one small comparison run on July 13, 2026, against Mythify
5.x. It is kept as history. It is not a benchmark, and the harness that
produced it no longer ships.

The sanitized result is [efficacy-smoke-2026-07-13.json](efficacy-smoke-2026-07-13.json).
Its field values are unchanged from the original run; only the file name
changed in 6.0.0 (it was `codex-word-count-2026-07-13.json`).

## What was run

- Agent: Codex CLI 0.144.1, using the account's default model. The model
  identifier was not pinned or recorded.
- Task: one scenario, `word_count_bugfix`, a small Python function with a
  whitespace bug and local unit tests.
- Design: two paired trials. Each pair ran a bare condition, then a Mythify
  condition, each in a fresh temporary workspace.
  - Bare: the agent received the task, the scenario, the verifier command, and
    a reporting request. No Mythify files were present.
  - Mythify: the agent received the same task and verifier, plus the 5.x
    protocol file, the CLI, and its `protocol/*.json` manifests. The 5.x
    `auto` profile resolved to `fast` for this scenario.
- External verifier: `python3 -m unittest` in each workspace. Its exit code,
  not the agent's report, decided task success.
- Billing: subscription login. Neither dollar cost nor subscription quota was
  measured.

## Result

| Measure | Bare | Mythify |
| :--- | :--- | :--- |
| Trials | 2 | 2 |
| External verifier passed | 2 of 2 | 2 of 2 |
| False completion claims | 0 | 0 |
| Passing `verify run` recorded for `python3 -m unittest` | 0 (no Mythify store) | 2 of 2 |
| Mean agent run time | 99.7 s | 37.1 s |

Task success was a tie. In both Mythify trials the ledger held one executed,
passing verification for the exact expected command. The harness counted only
that record shape; attested, failed, malformed, or different-command records
would not have counted.

## What it does not show

- It does not show that Mythify improves task success. Both conditions passed
  every trial.
- It does not show a speed effect. Two pairs, a fixed bare-then-Mythify order,
  and variable service load make the time difference descriptive only.
- The evidence-recording difference confirms that the agent followed the
  protocol in that run. It is not an independent efficacy outcome, because the
  Mythify condition was told to record evidence.
- It says nothing about other agents, other models, other tasks, or cost.
- One scenario and two pairs are not statistically powered and do not
  establish generality.

## The harness is gone

The 5.x harness, `scripts/local_model_eval.py`, drove named agent CLIs and
carried model-specific code. Mythify 6.0.0 removed it together with the rest of
the model, provider, and host routing, so this run cannot be reproduced from
the current tree. The commands that produced it live in the 5.x history only.

The JSON stays as data. Its sanitization block records that it holds no raw
agent output, verifier output, temporary paths, or prompts.

## What a real study needs

A pre-registered study that works with any agent is a bet on the
[roadmap](../../ROADMAP.md): more scenarios, counterbalanced or randomized
order, more repetitions, agent and model metadata recorded where the host
exposes it, cost measured, and the analysis fixed before the first run. Until
that exists, the honest claim is the one above: the evidence gate recorded what
it was built to record, and nothing more was measured.
