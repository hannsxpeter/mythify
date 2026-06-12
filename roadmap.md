# Mythify Future Roadmap

This is a memory aid for product direction, not a committed release schedule.
The goal is to keep Mythify focused as it grows beyond CLI-native users.

## Product Thesis

Mythify is bring-your-own-model, bring-your-own-agent discipline.

Models, CLIs, APIs, and local runtimes can change. Mythify should stay focused
on the durable contract:

- What is the task?
- What is the plan?
- What state must survive context loss?
- What counts as evidence?
- What command actually verified the claim?
- What worker output is material, and what did the orchestrator verify?

The core promise stays the same: use the right model for the role, but prove
the result the same way.

## Near Term

### CLI-Native Power Users

Keep the first wedge sharp for people who already use agent CLIs.

- Codex CLI
- Claude Code
- Cursor Agent
- Generic command workers
- MCP clients that already expose tool calls

Priorities:

- Make setup obvious.
- Keep docs practical and copy-pasteable.
- Preserve the strict evidence model.
- Keep worker output clearly separate from verified evidence.
- Improve failure messages and recovery flows.

## Architecture Runway

The local/API roadmap adds more runtimes and therefore more places for drift.
Before adding many providers, introduce a small amount of structure that keeps
the product from becoming a pile of one-off adapters.

Direction:

- Keep `design.md` as the hand-authored normative contract.
- Add an operation and role registry gradually, starting with one or two
  low-risk operations.
- Generate docs tables, schemas, and test fixtures from the registry where it
  clearly removes drift.
- Keep the current Node MCP server until a replacement has proven itself.
- Treat a one-core architecture as a v3 destination, not an immediate rewrite.

Good v2 runway tasks:

- Bind verification records to plan and step ids.
- Add whole-state no-mutation checks for refusal paths.
- Add a deployed-copy version handshake between protocol text and CLI.
- Add log compaction or rotation for long-lived `.mythify` directories.
- Expand CLI/MCP interop from spot checks to every mutating operation.

Completed v2 runway tasks:

- [x] 2026-06-12: add a capability registry for host, provider, execution,
  and lifecycle adapter metadata without changing user behavior.

Next adapter slices:

- Wire registry data into `host_model_switch` status output without claiming
  current-chat switching.
- Add a generic OpenAI-compatible local provider probe for reader or triage
  roles.
- Add a Kimi Code or OpenCode CLI probe after the local provider path proves
  the adapter contract.

Do not let architecture work pause the product path. It should make local
models, API providers, and role-based spawning cheaper to add.

## Model Assignment

Mythify should assign roles, not vibes.

Suggested role model:

- `session`: main host model, controlled by the user or host app
- `triage`: cheap or fast model for task classification
- `reader`: cheap, local, or privacy-preferred model for codebase reading
- `worker`: same-or-lower model for independent subtasks
- `reviewer`: same-or-stronger only when explicitly allowed
- `verifier`: command-first, not model-first

Suggested model tiers:

- `small`
- `fast`
- `standard`
- `strong`
- `frontier`
- `local`
- `unknown`

Default policy:

- Spawned workers should be same-or-lower than the session model.
- Stronger reviewers require explicit opt-in.
- Reading tasks should prefer local or privacy-preserving models when configured.
- Verifiers should run commands first and use model judgment only as supporting material.
- Spawned workers should not recursively fan out by default.
- Workers should be read-only by default unless the user deliberately enables writes.

## Host Model Switching

The current `host-model` idea is worth keeping, but it should graduate from
"record an intent" to "apply when the host exposes a capability, otherwise
return precise manual guidance."

Research notes:

- `docs/host-model-switching-research.md`
- `docs/local-llm-and-new-host-research.md`

Core rule:

- Mythify can recommend or request a host model switch, but it should not
  pretend the switch happened unless the host adapter confirms it.

Switch statuses:

- `applied`: the host adapter changed the model or thinking setting.
- `requested`: Mythify recorded the desired switch and returned host-specific
  instructions.
- `manual`: the host does not expose a switch capability, so the user must use
  the host UI or command.
- `blocked`: the requested model, thinking level, or provider is unavailable.

Capability layers:

- Codex thread adapters can use model and thinking overrides where thread tools
  expose them, especially for new threads, follow-up threads, and spawned work.
- CLI adapters can pass model, effort, speed, or thinking flags to worker
  commands when those CLIs support them.
- API adapters can choose the model directly per role.
- Desktop MCP clients usually cannot have their current chat model changed by
  an MCP tool unless the host exposes a native capability.
- Hosts without a switch API should get exact instructions, not fake success.

Use cases:

- If the current session is on a frontier model with max or extra-high
  thinking for a small task, recommend downshifting to a fast model.
- If a task is high-risk, research-heavy, or architecture-heavy, recommend
  keeping or upgrading the session model.
- If the session should stay strong but subtasks are cheap, spawn lower-tier
  readers or workers.
- If a reviewer genuinely needs to be stronger than the session model, require
  explicit opt-in.

Implementation idea:

- Keep `host_model_switch`, but make its output structured:
  `requested_model`, `requested_thinking`, `reason`, `host_capability`,
  `status`, `applied_by`, and `manual_action`.
- Add `host_model_status` fields for current model, desired model, last
  confirmed switch, and whether the value is host-confirmed or self-reported.
- Let hosts declare capabilities: `can_switch_current_thread`,
  `can_set_new_thread_model`, `can_set_worker_model`, and
  `can_set_thinking`.
- Never use a recorded desired model as proof that the host actually switched.

## Local Model Support

Local models are attractive for privacy, cost, and high-volume background work.
They should enter Mythify through adapters rather than special cases.

Candidate adapters:

- Ollama
- LM Studio
- llama.cpp server
- vLLM
- OpenAI-compatible localhost servers
- Generic OpenAI-compatible base URL

Good first local-model roles:

- task classification
- codebase mapping
- summarization
- documentation drafts
- test idea generation
- fanout reading
- cheap review passes

Risk posture:

- Be honest that local model quality varies by task.
- Prefer smaller scopes and stronger verification for weaker models.
- Do not let local worker confidence count as proof.
- Keep executable verification as the final authority.

Recommended order:

1. Generic OpenAI-compatible local adapter with explicit base URL, model id,
   timeout, and key handling.
2. Ollama profile for the easiest first local setup.
3. LM Studio profile for users who prefer local desktop model management.
4. llama.cpp profile for GGUF power users.
5. vLLM profile for workstation, server, and team-local inference.

## API Provider Support

API users need reliable orchestration, cost control, and auditability.

Candidate adapters:

- OpenAI
- Anthropic
- OpenAI-compatible hosted providers
- Custom command or HTTP adapters

Priorities:

- Provider-neutral role assignment.
- Per-role model defaults.
- Cost and timeout controls.
- Clear audit logs for spawned work.
- No surprising cross-provider fallback.
- Explicit user choice before using a stronger or more expensive model.

## Cross-Platform Spawning

The adapter contract should stay small.

Each adapter should answer:

- Is it available?
- Is it authenticated or configured?
- What models are available?
- How do I run one prompt?
- What timeout, cost, and sandbox knobs exist?
- What output did it produce?

Avoid normalizing every platform perfectly. Coarse tiers and clear policy are
better than fragile provider-specific magic.

## Host Adapter Candidates

The host roadmap should prefer adapters that can be probed and verified from a
terminal. A host adapter should not claim model switching, spawning, or MCP
setup unless a local probe or official contract proves the capability.

Near candidates:

- Kimi Code CLI: high priority. It supports `kimi -p` for single
  non-interactive instructions, `/model` for interactive model switching,
  provider and model configuration, MCP, ACP for IDEs, subagents, hooks, and a
  browser UI.
- OpenCode CLI: high priority. The installed CLI supports headless `run`,
  explicit `--model`, provider and model listings, model variants, agent
  selection, MCP management, a headless server, and a web interface.
- Antigravity CLI and desktop: high potential, but later than OpenCode. It has
  CLI/TUI, desktop, MCP, non-interactive prompts, skills, and settings-based
  model configuration, but the local `agy` shim is currently broken in this
  environment and needs a verified probe before implementation.

Later candidates:

- Kimi Work desktop: track as a local desktop agent for files, browser
  automation, scheduled tasks, Python execution, and office workflows after the
  CLI adapter path is stable.
- OpenCode Desktop: track after the CLI/server adapter proves useful.
- Antigravity desktop MCP setup guide: useful before full worker automation.
- Antigravity worker adapter: only after `agy -p`, model controls, workspace
  trust, and permissions are verified locally.

## Execution and Agent Lifecycle Adapters

Some new tools are useful to Mythify but should not be treated as model
providers.

Google Colab CLI:

- Classify as an `execution_substrate`, not a `model_provider`.
- Useful for GPU, TPU, notebook, training, artifact, and heavy verification
  workflows.
- Track remote command exit, session id, accelerator type, logs, artifacts,
  and teardown status as evidence.
- Require explicit billing, data movement, and cleanup posture before use.

Google Agents CLI and ADK:

- Classify as an `agent_lifecycle` adapter, not a general coding host.
- Useful for ADK scaffolding, eval generation, eval grading, deployment, and
  observability workflows.
- Keep this after local/API provider support unless a user is specifically
  building ADK agents.
- Record `agents-cli eval`, `agents-cli deploy`, or `adk eval` command results
  as verification evidence.

## Workflow Surfaces

Mythify already supports the workflow shape through protocol state, CLI, and
MCP tools. Later, it can make that process more visible.

Possible surfaces:

- status dashboard
- background task view
- phase view for Understand, Design, Build, Judge, Verify
- fanout worker timeline
- verification history
- outcome loop progress
- release readiness view

The UI should reveal evidence, not decorate self-report.

## Evaluation

The local eval harness is a start. Future work should measure the core claim
more deeply.

Questions to answer:

- Does Mythify improve verified task success?
- Does it reduce false completion claims?
- How much overhead does each profile add?
- Which tasks benefit from local models?
- Which roles require stronger models?
- Where does fanout help, and where does it waste tokens?

Evidence should come from rerunning verifiers, not from model self-ratings.

## Product Guardrails

Do not turn Mythify into a generic model router too early.

Avoid:

- automatic global optimization across every provider
- hidden provider fallback
- complex cost prediction before there is enough usage data
- claiming local models are equivalent across tasks
- write-enabled spawned workers by default
- model judgment as final proof

Preserve:

- contract-first design
- executable verification
- durable state
- proportional ceremony
- clear role boundaries
- explicit user control
- honest failure reporting

## Possible Release Themes

### v2.6

- Architecture runway: operation registry prototype for a small surface.
- Step-bound verification records.
- Whole-state refusal no-mutation checks.
- Host model switch capability contract and status model.

### v2.7

- First supported local model backend.
- Local reader or triage role.
- Tests proving local output remains material, not evidence.
- Generic OpenAI-compatible localhost adapter.
- Ollama and LM Studio setup profiles.
- Host adapter proof of concept for model and thinking overrides where the host
  exposes them.

### v2.8

- API provider adapter path.
- Per-role provider defaults.
- Cost and timeout metadata in worker records.
- CLI/MCP interop matrix for every mutating operation.
- Kimi Code CLI adapter proof of concept.
- OpenCode CLI adapter proof of concept.
- Antigravity CLI probe and MCP setup guide.

### v3.0

- Stable cross-platform role assignment.
- Stable adapter interface.
- Desktop local-agent lane for Kimi Work style workflows.
- Execution adapter lane for Colab CLI style remote jobs.
- Agent lifecycle lane for Agents CLI and ADK style workflows.
- One-core architecture decision based on the registry prototype.
- Stronger workflow surfaces.
- Clear migration guide from CLI-only usage to model-runtime orchestration.
