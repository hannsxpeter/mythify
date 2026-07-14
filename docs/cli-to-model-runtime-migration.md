# CLI-Only To Model-Runtime Migration Guide

This guide shows how to grow from Mythify's original CLI-first workflow into
optional model-runtime orchestration without changing the core discipline.

The migration is opt-in. Mythify should not route work to another provider,
spend API credits, run remote jobs, execute evals, deploy, or mutate project
state unless the user selects that path and the adapter contract allows it.

## The Stable Baseline

Start here and keep this path working forever:

1. Copy a generated protocol file into a project.
2. Copy `scripts/mythify.py`, adjacent `scripts/mythify_*.py` helpers,
   `protocol/operation-registry.json`, `protocol/classification-rules.json`,
   `protocol/model-capabilities.json`,
   and `protocol/workflow-router.json`.
3. Run `python3 scripts/mythify.py protocol check CLAUDE.md`.
4. Run `python3 scripts/mythify.py init`.
5. Use `classify`, `plan`, `step`, `verify run`, `reflect`, and `summary`.

This mode has no provider setup. Verification comes from executed commands, not
model self-report. It is the fallback when every optional runtime lane is
unavailable.

## Optional MCP Runtime

Add the MCP server when a client needs tool calls instead of shell commands.
The MCP server and CLI share the same `.mythify/` state directory, so a plan
created in one surface is visible in the other.

Use MCP for:

- Desktop clients that can call tools directly.
- Fanout workers through `fanout_start`, `fanout_status`, and
  `fanout_results`.
- Read-only workflow surfaces like `workflow_status`, `verification_history`,
  `background_status`, `evidence_harness`, `outcome_progress`, `release_readiness`,
  `fanout_timeline`, and `phase_status`.

Do not treat MCP as a separate source of truth. It is another adapter over the
same state contract.

## Host Model Lane

Host model policy is advisory unless a host exposes a real apply or confirm
capability.

Use:

- `classify` or `classify_task` for `model_policy`.
- `host-model switch` or `host_model_switch` to record a requested host model.
- `host-model status` or `host_model_switch action=status` to read the recorded
  request, host capability metadata, host confirmation fields, and adapter proof
  scan.

Guardrails:

- A recorded request is not proof that the current host chat changed.
- Stronger worker spawning stays same-or-lower unless explicitly allowed.
- Reviewer stronger-model use is a scoped opt-in.
- Model output is material, not verification evidence.

## Local Model Lane

Add local models when privacy, cost, speed, or offline work matters.

Supported profiles:

- Generic OpenAI-compatible localhost endpoint.
- Ollama.
- LM Studio.
- llama.cpp.
- vLLM.

Use:

- `provider_probe` to check `/v1/models` or `/v1/chat/completions`.
- `local_model_run` for reader or triage prompts only.

Guardrails:

- Local model URLs must resolve to localhost-style addresses.
- Local model output is material, not verification evidence.
- Local model tools do not edit files, run commands, or write Mythify state.
- Verification still happens through `verify run` or `verify_run`.

## Host CLI Worker Lane

Use host CLI workers for bounded non-interactive prompts when a task can be
split into independent material-gathering work.

Supported host CLI candidates:

- Kimi Code.
- OpenCode.
- Antigravity.

Use:

- `host_cli_probe` to check version, help, and proof metadata.
- `host_cli_run` for one bounded prompt.
- MCP fanout tools for independent worker batches.

Guardrails:

- Worker output is material, not verification evidence.
- Antigravity requires explicit `cwd` and uses native permissions.
- Workers write no Mythify state.
- Merged work must be checked by an executed verifier.

## API Provider Lane

Use hosted providers only when the user intentionally opts into external
accounts, billing, and data transmission.

Supported fanout provider engines:

- `anthropic`.
- `openai`.

Metadata-supported provider records:

- OpenAI API.
- Anthropic API.
- Hosted OpenAI-compatible endpoints.

Guardrails:

- General API provider role routing remains disabled unless a guarded execution
  path exists.
- Hosted fanout requires billing, data, and material-only acknowledgements.
- Provider audit logs record redacted metadata, not raw prompts or outputs.
- Cost fields are metadata-only. Mythify does not invent token or dollar
  estimates.
- API provider output is material, not verification evidence.

## Execution Substrate Lane

Use execution substrates when the work is a remote job, not a model role.

Current supported adapter:

- Google Colab CLI through `execution_probe` and guarded `execution_run`.

Guardrails:

- `execution_probe` runs version and help only.
- `execution_run` requires billing, data movement, and cleanup
  acknowledgements.
- Remote jobs write no Mythify state.
- Remote output is material until a separate verifier consumes logs,
  artifacts, or reports.

## Agent Lifecycle Lane

Use lifecycle adapters to inspect agent tooling availability before any
scaffold, eval, deploy, publish, or cloud mutation path exists.

Current supported adapters:

- Google Agents CLI.
- Google ADK CLI.

Use:

- `lifecycle_probe` to run version, help, and eval-help probes only.
- `lifecycle_lane_contract` to inspect allowed probe commands, disabled
  lifecycle actions, future guarded actions, eval prerequisites, deployment
  prerequisites, mutation policy, and material-only evidence status.

Guardrails:

- No scaffold, agent run, eval execution, deploy, publish, project mutation, or
  cloud mutation is enabled by default.
- Future eval or deployment work needs a separate guarded contract first.
- Lifecycle output is material, not verification evidence.

## Migration Order

Use this order when adding runtime support to a project:

1. Keep the CLI-only baseline working.
2. Add MCP if the host client benefits from tool calls.
3. Add host model policy for advisory model recommendations.
4. Add local model reader or triage roles when a localhost backend is present.
5. Add host CLI workers for independent material-gathering tasks.
6. Add hosted API fanout only with explicit acknowledgements.
7. Add remote execution only for guarded jobs with cleanup posture.
8. Add lifecycle adapters only as probe-only capability checks until a future
   contract enables guarded actions.

At every step, keep verification separate from model or worker output.

## Do Not Migrate By

- Replacing `verify run` with model judgment.
- Turning provider defaults into hidden routing.
- Falling back across providers when one fails.
- Treating a requested host model switch as a confirmed host switch.
- Running hosted API workers without billing and data acknowledgements.
- Running remote jobs without cleanup posture.
- Running lifecycle evals or deployments from `lifecycle_probe`.
- Letting spawned workers write Mythify state by default.

## Quick Decision Table

| Need | Use | Verification rule |
| :--- | :--- | :--- |
| Durable task discipline only | CLI | Run `verify run` before completion claims. |
| Tool-call access from a desktop client | MCP server | Same `.mythify/` state, same evidence rules. |
| Advisory host model choice | `classify`, `host_model_switch` | Host switch is not confirmed unless the host proves it. |
| Local reader or triage model | `provider_probe`, `local_model_run` | Local output is material, then execute a verifier. |
| Independent worker material | `host_cli_run` or fanout | Merge results, then execute a verifier. |
| Hosted provider worker | Guarded fanout API engine | Require acknowledgements, then execute a verifier. |
| Remote compute job | `execution_run` | Verify artifacts or logs separately. |
| Agent lifecycle capability check | `lifecycle_probe` | Probe output is material; no action commands run. |
