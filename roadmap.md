# Mythify Future Roadmap

This is a memory aid for product direction, not a committed release schedule.
The goal is to keep Mythify focused as it grows beyond CLI-native users.

## How To Read This

- Start with the Status Dashboard when deciding what to build next.
- Use Track Backlogs when you need the fuller context for a work area.
- Use Done when checking whether a slice already shipped.
- Done means the work was implemented and verified unless a note says otherwise.

Status markers:

- `[ ]` Not started
- `[>]` In progress
- `[x]` Done
- `[~]` Deferred or waiting on external proof

## Status Dashboard

### In Progress

- [>] First local model backend
  - Current goal: turn the generic OpenAI-compatible probe path into a
    role-limited local reader or triage adapter.
  - Next step: design the execution boundary so local model output remains
    material until an executable verifier checks it.
  - Guardrail: do not let local model output count as proof.

### Next To Do

1. [ ] Add the first local model backend.
   - Start with generic OpenAI-compatible local provider support.
   - Keep local model output material until an executable verifier checks it.
2. [ ] Add real Kimi Code and OpenCode worker adapter spikes.
   - Kimi: verify `kimi -p` behavior locally before claiming worker support.
   - OpenCode: verify `opencode run` behavior locally before claiming worker
     support.
3. [ ] Add step-bound verification records.
   - Bind verification records to plan and step ids.
   - Make completion evidence easier to audit later.
4. [ ] Expand CLI/MCP interop coverage.
   - Move from spot checks to every mutating operation.

### Later

- [ ] Ollama setup profile.
- [ ] LM Studio setup profile.
- [ ] llama.cpp profile for GGUF power users.
- [ ] vLLM profile for workstation, server, and team-local inference.
- [ ] API provider adapter path with cost and timeout metadata.
- [ ] Role defaults per provider.
- [ ] Antigravity worker adapter after local `agy -p`, model controls, workspace
  trust, and permissions are verified.
- [ ] Kimi Work desktop lane after CLI adapters are stable.
- [ ] OpenCode Desktop lane after CLI and server adapters prove useful.
- [ ] Colab remote execution adapter after explicit billing, data movement, and
  cleanup posture are designed.
- [ ] Workflow dashboard or phase view that reveals evidence without decorating
  self-report.
- [ ] One-core architecture decision after the registry prototype proves enough
  value.

### Done

- [x] 2026-06-12: rebuild around contract-first Mythify v2.
- [x] 2026-06-12: add fanout parallel delegation for MCP.
- [x] 2026-06-12: add local subscription-backed fanout engines for Codex and
  Cursor.
- [x] 2026-06-12: add supervised outcome loops.
- [x] 2026-06-12: add task classification.
- [x] 2026-06-12: add fast model triage after classification.
- [x] 2026-06-12: add platform-aware model policy.
- [x] 2026-06-12: add host model switch records and status output.
- [x] 2026-06-12: add initiating-model spawn ceilings.
- [x] 2026-06-12: add fanout visibility, effort, and speed controls.
- [x] 2026-06-12: add the local bare-vs-Mythify evaluation harness.
- [x] 2026-06-12: add fast Mythify profile support.
- [x] 2026-06-12: add the capability registry for host, provider, execution,
  and lifecycle adapter metadata.
- [x] 2026-06-12: add generic OpenAI-compatible provider probe.
- [x] 2026-06-12: add Kimi Code and OpenCode CLI probes without executing
  prompts.
- [x] 2026-06-12: add Antigravity CLI probe and MCP setup guide.
- [x] 2026-06-12: add non-billable Google Colab CLI probe and spike plan.
- [x] 2026-06-12: close the generated-variant and changelog drift found in
  `codeaudit.md`.
- [x] 2026-06-13: refactor the roadmap into a scan-first dashboard with visible
  status lanes.
- [x] 2026-06-13: add non-deploying Google Agents CLI and ADK lifecycle probe
  and spike plan.

## Track Backlogs

### Architecture Runway

Why this track exists: local models, API providers, and host CLIs create more
places for drift. Add enough structure to keep adapters honest without pausing
product work.

Open:

- [ ] Operation registry prototype for a small surface.
- [ ] Step-bound verification records.
- [ ] Whole-state no-mutation checks for refusal paths.
- [ ] Deployed-copy version handshake between protocol text and CLI.
- [ ] Log compaction or rotation for long-lived `.mythify` directories.
- [ ] Full CLI/MCP interop matrix for mutating operations.
- [ ] Generate docs tables, schemas, or fixtures from the registry only after a
  drift test protects the output.

Done:

- [x] Capability registry exists in `mcp-server/src/capability-registry.js`.
- [x] Registry data is shown in `host_model_switch` status output.

### Model Assignment

Core idea: Mythify should assign roles, not vibes.

Roles:

- `session`: main host model, controlled by the user or host app.
- `triage`: cheap or fast model for task classification.
- `reader`: cheap, local, or privacy-preferred model for codebase reading.
- `worker`: same-or-lower model for independent subtasks.
- `reviewer`: same-or-stronger only when explicitly allowed.
- `verifier`: command-first, not model-first.

Open:

- [ ] Per-role provider defaults.
- [ ] Provider-neutral role assignment.
- [ ] Stronger reviewer opt-in flow.
- [ ] Cost and timeout controls per role.

Done:

- [x] Platform-aware model policy.
- [x] Task-based host model recommendations.
- [x] Same-or-lower default worker spawning.
- [x] Explicit stronger-worker ceiling.

### Host Model Switching

Core rule: Mythify can recommend or request a host model switch, but it should
not pretend the switch happened unless the host adapter confirms it.

Open:

- [ ] Apply model or thinking changes when a host exposes a real capability.
- [ ] Add host-confirmed current model fields where supported.
- [ ] Add exact manual instructions for hosts without a switch API.
- [ ] Add adapter-specific tests for `can_switch_current_thread`,
  `can_set_new_thread_model`, `can_set_worker_model`, and `can_set_thinking`.

Done:

- [x] `host_model_switch` records requested model state.
- [x] Status output includes registry-backed host capability information.
- [x] Recorded desired model is not treated as proof that the host switched.

### Local Model Support

Local models are useful for privacy, cost, and high-volume background work.
They should enter through adapters, not special cases.

Open:

- [ ] Generic OpenAI-compatible local adapter with explicit base URL, model id,
  timeout, and key handling.
- [ ] Ollama profile.
- [ ] LM Studio profile.
- [ ] llama.cpp profile.
- [ ] vLLM profile.
- [ ] Local reader role.
- [ ] Local triage role.
- [ ] Tests proving local output remains material, not evidence.

Done:

- [x] Generic OpenAI-compatible provider probe can call `/v1/models` and
  `/v1/chat/completions`.

### API Provider Support

API users need reliable orchestration, cost control, and auditability.

Open:

- [ ] OpenAI adapter path.
- [ ] Anthropic adapter path.
- [ ] OpenAI-compatible hosted provider path.
- [ ] Custom command or HTTP adapter path.
- [ ] Clear audit logs for spawned provider work.
- [ ] No-surprise cross-provider fallback policy.

Done:

- [x] Generic OpenAI-compatible probe shape exists.

### Host Adapter Candidates

Prefer adapters that can be probed and verified from a terminal. A host adapter
should not claim model switching, spawning, or MCP setup unless a local probe or
official contract proves the capability.

Open:

- [ ] Kimi Code worker adapter proof of concept.
- [ ] OpenCode worker adapter proof of concept.
- [ ] Antigravity worker adapter after local prompt, model, workspace, trust,
  and permission behavior is verified.
- [ ] Kimi Work desktop lane.
- [ ] OpenCode Desktop lane.

Done:

- [x] Kimi Code CLI probe.
- [x] OpenCode CLI probe.
- [x] Antigravity CLI probe.
- [x] Antigravity MCP setup guide.

### Execution and Agent Lifecycle Adapters

Some tools are useful to Mythify but are not model providers.

Open:

- [ ] Colab remote execution adapter, only after explicit billing and data
  movement controls exist.

Done:

- [x] Google Colab CLI is classified as an `execution_substrate`.
- [x] `execution_probe` checks Google Colab CLI availability with version and
  help commands only.
- [x] `docs/colab-cli-spike-plan.md` records the non-billable Colab scope.
- [x] Google Agents CLI and ADK CLI are classified as `agent_lifecycle`
  adapters.
- [x] `lifecycle_probe` checks Google Agents CLI and ADK CLI availability with
  version, help, and eval-help commands only.
- [x] `docs/agents-cli-adk-spike-plan.md` records the non-deploying lifecycle
  scope.

Guardrails:

- Colab CLI stays outside model assignment.
- Agents CLI and ADK stay in `agent_lifecycle`, not `coding_host`.
- Deployment commands are not enabled by default.
- Remote execution requires explicit billing, data movement, and cleanup
  posture before use.

### Workflow Surfaces

Mythify already supports the workflow shape through protocol state, CLI, and
MCP tools. Later, it can make the process more visible.

Open:

- [ ] Status dashboard.
- [ ] Background task view.
- [ ] Phase view for Understand, Design, Build, Judge, Verify.
- [ ] Fanout worker timeline.
- [ ] Verification history.
- [ ] Outcome loop progress.
- [ ] Release readiness view.

Principle: reveal evidence, do not decorate self-report.

### Evaluation

The local eval harness is a start. Future work should measure the core claim
more deeply.

Open questions:

- [ ] Does Mythify improve verified task success?
- [ ] Does it reduce false completion claims?
- [ ] How much overhead does each profile add?
- [ ] Which tasks benefit from local models?
- [ ] Which roles require stronger models?
- [ ] Where does fanout help, and where does it waste tokens?

Evidence should come from rerunning verifiers, not from model self-ratings.

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

## Product Guardrails

Do not turn Mythify into a generic model router too early.

Avoid:

- Automatic global optimization across every provider.
- Hidden provider fallback.
- Complex cost prediction before there is enough usage data.
- Claims that local models are equivalent across tasks.
- Write-enabled spawned workers by default.
- Model judgment as final proof.

Preserve:

- Contract-first design.
- Executable verification.
- Durable state.
- Proportional ceremony.
- Clear role boundaries.
- Explicit user control.
- Honest failure reporting.

## Possible Release Themes

### v2.6

- [ ] Operation registry prototype for a small surface.
- [ ] Step-bound verification records.
- [ ] Whole-state refusal no-mutation checks.
- [ ] Host model switch capability contract and status model.
- [x] Agents CLI and ADK lifecycle spike.

### v2.7

- [>] First supported local model backend.
- [ ] Local reader or triage role.
- [ ] Tests proving local output remains material, not evidence.
- [ ] Generic OpenAI-compatible localhost adapter.
- [ ] Ollama and LM Studio setup profiles.
- [ ] Host adapter proof of concept for model and thinking overrides where the
  host exposes them.

### v2.8

- [ ] API provider adapter path.
- [ ] Per-role provider defaults.
- [ ] Cost and timeout metadata in worker records.
- [ ] CLI/MCP interop matrix for every mutating operation.
- [ ] Kimi Code CLI adapter proof of concept.
- [ ] OpenCode CLI adapter proof of concept.

### v3.0

- [ ] Stable cross-platform role assignment.
- [ ] Stable adapter interface.
- [ ] Desktop local-agent lane for Kimi Work style workflows.
- [ ] Execution adapter lane for Colab CLI style remote jobs.
- [ ] Agent lifecycle lane for Agents CLI and ADK style workflows.
- [ ] One-core architecture decision based on the registry prototype.
- [ ] Stronger workflow surfaces.
- [ ] Clear migration guide from CLI-only usage to model-runtime orchestration.

## References

- `docs/host-model-switching-research.md`
- `docs/local-llm-and-new-host-research.md`
- `docs/colab-cli-spike-plan.md`
- `docs/antigravity-mcp-setup.md`
