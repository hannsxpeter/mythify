# Host Apply And Confirm Proof Watchlist

This watchlist names the proof Mythify needs before host model switching can
move from advisory state to actual host mutation.

Current policy is conservative:

- `host_model_switch` records desired host model state.
- `adapter_proof_scan` reports capability metadata.
- Host state is not mutated.
- User-reported current model is not host proof.
- A supported worker or new-thread path is not proof that Mythify can mutate
  the active current chat.

## Current Surfaces

| Surface | Current status | Evidence source |
| :--- | :--- | :--- |
| Current-chat model apply | unsupported or unknown | `adapter_proof_scan.paths.current_chat_model_apply` |
| Current-chat model confirm | unsupported or unknown | `adapter_proof_scan.paths.current_chat_model_confirm` |
| New-thread model apply | supported for several host platforms | `host_capability.can_set_new_thread_model` |
| Worker model apply | supported for several host platforms and some host CLI workers | `host_capability.can_set_worker_model`, host CLI probe metadata |
| Thinking apply | supported for Codex and Cursor host platforms | `host_capability.can_set_thinking` |

The watchlist does not change those statuses. It defines what would be required
before a status can safely become actionable.

## Proof Gate: Current-Chat Model Apply

Current-chat apply may become `supported` only when a host adapter can provide
all of these:

- A documented or locally probeable API, tool, command, or protocol operation
  that targets the active current chat.
- An explicit argument or field for the requested model.
- A bounded call path that does not depend on hidden UI automation.
- A returned status that distinguishes accepted, rejected, blocked, and
  unsupported requests.
- No silent fallback to a different model.
- A no-mutation dry-run or probe mode when available.
- Tests proving unsupported hosts remain unsupported.

An apply request may become `applied` only when the adapter also returns
positive current-chat evidence after the request.

## Proof Gate: Current-Chat Model Confirm

Current-chat confirm may become `supported` only when a host adapter can read
the active current chat model from the host itself.

Required evidence:

- Host-sourced current model id or name.
- Host-sourced timestamp, generation id, session id, or equivalent freshness
  signal when available.
- Clear distinction between host-confirmed state and user-reported state.
- A blocked or unsupported result when the host refuses or cannot expose the
  current model.
- Tests proving `host_confirmation.current_model_confirmed` stays `false`
  without host evidence.

Confirmation cannot come from the requested target model, `.mythify/host-model.json`,
or a user-provided `current_model` field.

## Proof Gate: Worker Model Override

Worker model override may be actionable when the spawned process or worker tool
accepts model selection at launch or task creation time.

Required evidence:

- The exact argument, field, environment variable, or config entry used.
- The adapter or process receiving the requested model.
- A bounded worker invocation that treats output as material, not verification.
- A status that distinguishes accepted, unsupported, and blocked.
- Tests proving the selected model is passed to the worker command or request.

This does not imply current-chat switching. Worker model selection is a separate
path.

## Proof Gate: Thinking Or Effort Override

Thinking or effort override may be actionable when the host or worker surface
has a documented or probeable setting for the spawned request or supported
thread type.

Required evidence:

- The exact field, flag, or config key.
- Allowed values and blocked values.
- Whether the setting applies to current chat, new thread, worker, or all of
  them.
- A status that distinguishes accepted, downgraded, unsupported, and blocked.
- Tests proving invalid values do not silently pass as successful.

Thinking override must not be inferred from model name alone.

## Watchlist Rows

| Host or adapter | Current-chat apply | Current-chat confirm | Worker model override | Thinking override | Next proof to seek |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Codex Desktop | unsupported | unsupported | supported for workers | supported | Host API or tool result that confirms active chat model. |
| Codex CLI | unsupported | unsupported | supported for workers | supported | Current-thread command or API result that can be executed and confirmed. |
| Claude Desktop | unsupported | unsupported | supported for workers | unsupported | Host-provided current chat model readback. |
| Claude Code | unsupported | unsupported | supported for workers | unsupported | Controllable process proof beyond manual `/model` guidance. |
| Cursor Desktop | unsupported | unsupported | supported for workers | supported | Host or agent API that reports active chat model. |
| Cursor Agent | unsupported | unsupported | supported for workers | supported | Headless command proof that model and effort were accepted. |
| Kimi Code | unsupported | unsupported | unsupported | unsupported | Confirmed launch-time model override for non-interactive prompts. |
| OpenCode | unsupported | unsupported | supported | unsupported | Result metadata proving `opencode run --model` accepted the model. |
| Antigravity | unsupported | unsupported | supported | unsupported | Result metadata proving `agy --model` accepted the model. |

## Required Test Shapes

When a host path becomes actionable, add tests before enabling it:

- Fake supported adapter applies a current-chat model and returns confirmed
  evidence.
- Fake blocked adapter refuses an unavailable model and records blocked status.
- Fake unsupported adapter leaves current-chat fields unchanged.
- User-reported `current_model` does not set confirmed state.
- Worker model override passes a model field or flag to the spawned worker.
- Thinking override accepts valid values and blocks invalid values.
- No path writes verification evidence unless an executed verifier runs.

## Status Rules

Use these statuses consistently:

- `unsupported`: the host has no known or probeable path.
- `unknown`: the installed host or adapter cannot be inspected.
- `requested`: the adapter accepted a request but cannot confirm current state.
- `applied`: the adapter performed the action and returned positive host
  evidence.
- `confirmed`: the adapter read the current host state and it matches the
  request.
- `blocked`: the adapter proved the requested value cannot be used.

Until those gates exist, roadmap items that apply model or thinking changes in
the current host stay waiting on external proof.

