# Host Model Switching Research

Date: 2026-06-12

This note records current research for Mythify's future host model switching,
role assignment, and spawning roadmap. It distinguishes what Mythify can
actually apply from what it should only request or guide manually.

## Summary

Model control is real, but it lives at different layers.

- API providers and local OpenAI-compatible servers can choose a model per
  request.
- CLI workers can often choose a model at process start or through interactive
  commands.
- Some host thread tools can set model and thinking on new or follow-up
  threads.
- MCP itself does not define a standard "change the current chat model"
  primitive for servers.
- Desktop current-session switching should be treated as host-specific until a
  host adapter confirms it.

The right Mythify design is capability-based:

- `applied`: an adapter confirms the switch happened.
- `requested`: Mythify recorded the desired switch and returned instructions.
- `manual`: the user must change the host UI or command.
- `blocked`: the model, provider, tier, or thinking level is unavailable.

Implementation status:

- First slice landed in `mcp-server/src/capability-registry.js`: existing host
  platforms now have explicit capability flags.
- Current-chat switching remains conservative. A `true` worker or new-thread
  capability does not mean Mythify can mutate the active host chat.

## Findings

### Codex

Official Codex docs say the CLI and IDE extension share `config.toml` for
default model configuration, and that the Codex CLI can use `/model` during an
active thread. They also document `--model` or `-m` for starting a new CLI
thread or `codex exec` run with a specific model.

The Codex config docs also expose `model_reasoning_effort` as a supported
configuration field. Advanced configuration supports custom model providers,
including local or proxy providers through `base_url`.

Local observation in this Codex environment: thread tools expose `model` and
`thinking` overrides for creating a thread and for sending a follow-up message
to another thread. That supports model choice for spawned or separate Codex
work. It does not prove Mythify can mutate the current chat model from inside
an MCP server.

Implication for Mythify:

- Current Codex CLI thread: likely `manual` through `/model`.
- New Codex CLI or `codex exec` worker: `applied` with `--model`.
- Codex thread tool workers: `applied` when the host tool schema accepts
  `model` and `thinking`.
- Codex cloud tasks: `blocked` for default model changes if the host does not
  expose one.

Sources:

- [Codex models](https://developers.openai.com/codex/models)
- [Codex config basics](https://developers.openai.com/codex/config-basic)
- [Codex advanced configuration](https://developers.openai.com/codex/config-advanced)

### Claude Code

Claude Code docs show model selection at multiple surfaces:

- `claude --model opus` at startup.
- `/model sonnet` during a session.
- a settings file `model` value.
- `ANTHROPIC_MODEL`.
- subagent model controls through frontmatter, the Agent tool model parameter,
  `/agents`, and `CLAUDE_CODE_SUBAGENT_MODEL`.

The docs also describe `availableModels` allowlists that can reject or ignore
blocked model selections depending on the surface.

Implication for Mythify:

- Current Claude Code session: likely `manual` through `/model` unless running
  through a controllable CLI process.
- New Claude CLI worker: `applied` with `--model` or environment.
- Claude subagents: `applied` where the host supports subagent model fields.
- Blocked model names must return `blocked` rather than pretending success.

Source:

- [Claude Code model configuration](https://code.claude.com/docs/en/model-config)

### Cursor

Cursor's official CLI page shows model switching through `/model` in the
terminal agent UI, including model names with speed and thinking labels.
Cursor also advertises headless CLI use for scripts and automation, but the
public pages surfaced in this pass did not provide enough primary-source
detail to treat a headless model flag as confirmed.

Implication for Mythify:

- Current interactive Cursor Agent: `manual` through `/model`.
- Cursor workers: `applied` only after the adapter confirms a model flag,
  model id encoding, or API endpoint works in the installed CLI.
- Until then, Cursor model selection should be probed locally and marked
  `blocked` or `manual` when unsupported.

Sources:

- [Cursor CLI](https://cursor.com/cli)
- [Cursor headless CLI docs](https://cursor.com/docs/cli/headless)

### Kimi Code

Kimi Code CLI docs show model control at multiple surfaces:

- `/model` switches the model in an interactive TUI session.
- `~/.kimi-code/config.toml` stores `default_model`, `default_thinking`,
  providers, models, permission rules, loop control, background settings, and
  hooks.
- `kimi -p` can run a single instruction without entering the interactive UI.
- The `kimi provider` command can list, add, and remove providers and import
  providers from the models.dev catalog.
- Kimi Code can use Kimi OAuth, Kimi Platform API keys, and other configured
  providers such as Anthropic, OpenAI, and Google.

Kimi Work is separate. It is a desktop local-agent product for files, browser
automation, scheduled tasks, Python execution, and office workflows. It should
not be treated as the same adapter as Kimi Code CLI.

Implication for Mythify:

- Current Kimi TUI session: likely `manual` through `/model` unless Mythify is
  directly driving the running process.
- New Kimi CLI worker: likely `applied` through `kimi -p` plus config or a
  confirmed launch-time override.
- Kimi provider configuration: `applied` only after a local probe confirms the
  installed version and selected model/provider config.
- Kimi Work desktop: `manual` until a documented or locally probeable
  automation capability exists.

Sources:

- [Kimi Code CLI getting started](https://moonshotai.github.io/kimi-code/en/guides/getting-started)
- [Kimi Code configuration files](https://moonshotai.github.io/kimi-code/en/configuration/config-files.html)
- [Kimi command reference](https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html)
- [Kimi Code overview](https://www.kimi.com/code/docs/en/)
- [Kimi Work](https://www.kimi.com/products/kimi-work)

### MCP Clients

The MCP client concepts documentation lists client-provided features such as
elicitation, roots, and sampling. Sampling lets a server request LLM completions
through the client, but the client stays in control of permissions and security
measures.

This does not define a standard server-to-client operation for changing the
current chat model or thinking level.

Implication for Mythify:

- MCP `host_model_switch` should not claim it changed the active host model
  unless a specific host adapter confirms that capability.
- Plain MCP clients should receive `requested` or `manual`, plus exact user
  guidance.
- MCP sampling can be useful for a client-controlled model call, but it is not
  proof of current-session model switching.

Source:

- [MCP client concepts](https://modelcontextprotocol.io/docs/learn/client-concepts)

### API Providers

OpenAI's Responses API accepts a `model` field per request. OpenAI reasoning
models also accept a `reasoning.effort` setting where supported.

Anthropic's Messages API examples include a `model` field for requests, and
Claude Code's configuration docs show model allowlists and aliases around the
same model-selection concept.

Implication for Mythify:

- API workers can be `applied` per request.
- Thinking or reasoning settings should be explicit adapter fields, not prose.
- Unsupported effort values should be `blocked` or downgraded with a recorded
  reason.

Sources:

- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create/)
- [OpenAI reasoning models](https://developers.openai.com/api/docs/guides/reasoning)
- [Anthropic Messages streaming examples](https://docs.anthropic.com/en/api/messages-streaming)

### Local Models

Ollama supports OpenAI-compatible APIs, including `/v1/responses` with a
supported `model` field, but notes that Responses support is non-stateful.

LM Studio can run a local API server on localhost and offers REST,
OpenAI-compatible, and Anthropic-compatible endpoints. Its OpenAI-compatible
docs show reusing OpenAI clients by changing the base URL to the local server.

Implication for Mythify:

- Local model workers can be `applied` per request when the local server is
  running and the model is available.
- Local providers should be role-first: reader, triage, summarizer, and cheap
  reviewer before high-risk implementation.
- Local model output remains material, not evidence.

Sources:

- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [LM Studio OpenAI-compatible endpoints](https://lmstudio.ai/docs/developer/openai-compat)
- [LM Studio local server](https://lmstudio.ai/docs/developer/core/server)

## Recommended Mythify Design

### Capability Declaration

Each host or provider adapter should declare:

- `can_switch_current_thread`
- `can_set_new_thread_model`
- `can_set_worker_model`
- `can_set_thinking`
- `can_list_models`
- `can_confirm_current_model`

The default for unknown adapters should be false.

### Structured Switch Result

`host_model_switch` should return:

- `status`: `applied`, `requested`, `manual`, or `blocked`
- `requested_model`
- `requested_thinking`
- `current_model`, when confirmed
- `current_thinking`, when confirmed
- `host_capability`
- `applied_by`
- `manual_action`
- `reason`

Recorded desired model state must be marked as unconfirmed unless the host
adapter proves it.

### Practical Policy

- Downshift recommendations are useful when a small task is running on max or
  extra-high thinking.
- Strong-session plus cheap workers is often better than downshifting the
  current session.
- Stronger reviewers should require explicit opt-in.
- API and local workers can use direct per-request model controls.
- Desktop current-session switching remains host-specific.

## Next Experiments

1. Add a host capability registry with no behavior change.
2. Add adapter probes for installed CLIs:
   - Codex: model flag, config, and reasoning effort.
   - Claude: `--model`, `/model` guidance, and available model constraints.
   - Cursor: installed CLI model list and headless model selection, if exposed.
3. Change `host_model_switch` output to include status and confirmation.
4. Add tests where fake adapters prove `applied`, `manual`, and `blocked`
   states.
5. Keep MCP current-session switching as `manual` until a host-specific adapter
   exists.
