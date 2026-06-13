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

- [>] LM Studio setup profile.
  - Current goal: make LM Studio the next explicit local desktop model profile.
  - Next step: map the Ollama profile pattern against LM Studio's local
    OpenAI-compatible API shape.
  - Guardrail: setup support must stay local-only, mark model output as
    material not verification evidence, and avoid provisioning or installing
    anything without an explicit user action.

### Next To Do

1. [ ] llama.cpp profile for GGUF power users.
   - Keep an explicit path for users who already manage GGUF models.
2. [ ] vLLM profile for workstation, server, and team-local inference.
   - Keep server-style local inference separate from desktop onboarding.
3. [ ] API provider adapter path with cost and timeout metadata.
   - Bring hosted provider work in after the local profiles prove the adapter
     shape.

### Later

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
- [x] 2026-06-13: add role-limited local model backend for localhost
  OpenAI-compatible reader and triage runs.
- [x] 2026-06-13: add bounded Kimi Code and OpenCode host CLI worker runs.
- [x] 2026-06-13: add step-bound verification records for CLI and MCP evidence.
- [x] 2026-06-13: expand CLI/MCP interop coverage across shared mutating state.
- [x] 2026-06-13: add whole-state no-mutation checks for refusal paths.
- [x] 2026-06-13: add the memory operation registry prototype.
- [x] 2026-06-13: add deployed-copy protocol hash handshake between generated
  protocol files and the CLI.
- [x] 2026-06-13: add host model switch capability status with `switch_result`
  and current-chat confirmation fields.
- [x] 2026-06-13: add CLI log compaction for top-level verification and
  reflection logs, with raw archives under `.mythify/logs/archive/`.
- [x] 2026-06-13: generate adapter candidate docs from the capability registry,
  protected by Node and CI drift checks.
- [x] 2026-06-13: add advisory per-role provider defaults to CLI and MCP
  `model_policy`, including reader role metadata and no implicit fallback.
- [x] 2026-06-13: add stronger reviewer opt-in policy for classifier output
  and fanout tasks, keeping ordinary workers same-or-lower by default.
- [x] 2026-06-13: add Ollama local setup profile for `provider_probe` and
  `local_model_run`, defaulting to the local `/v1` endpoint with material-only
  output.

## Track Backlogs

### Architecture Runway

Why this track exists: local models, API providers, and host CLIs create more
places for drift. Add enough structure to keep adapters honest without pausing
product work.

Open:

- [ ] Expand registry-backed generation only when another duplicated surface
  has a focused drift test.

Done:

- [x] Log compaction archives raw top-level verification and reflection logs
  before trimming active logs to recent valid records.
- [x] Generated adapter candidate docs come from
  `mcp-server/src/capability-registry.js` and are protected by drift checks.
- [x] Capability registry exists in `mcp-server/src/capability-registry.js`.
- [x] Registry data is shown in `host_model_switch` status output.
- [x] Verification records include active plan and in-progress step context.
- [x] Full CLI/MCP interop matrix covers shared mutating operations.
- [x] Refusal paths have whole-state no-mutation snapshot checks.
- [x] Memory operation registry powers shared CLI and MCP memory categories,
  default category, state filename, and no-target clear refusals.
- [x] Generated protocol files carry a source hash, and CLI `protocol check`
  detects copied-file drift before workspace initialization.

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

- [ ] Provider-neutral role assignment.
- [ ] Cost and timeout controls per role.

Done:

- [x] Stronger reviewer opt-in flow requires explicit classifier or fanout
  policy before review tasks can exceed the initiating session model.
- [x] Per-role provider defaults are explicit in CLI and MCP `model_policy`.
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
- [ ] Add adapter execution tests once a host exposes apply or confirm APIs.

Done:

- [x] `host_model_switch` records requested model state.
- [x] Status output includes registry-backed host capability information.
- [x] Recorded desired model is not treated as proof that the host switched.
- [x] `switch_result` separates manual requested state from applied or
  host-confirmed current-chat state.
- [x] CLI and MCP text output show current-chat confirmation, manual-action
  status, and per-host capability fields.
- [x] Focused tests cover current public capability fields for requested host
  switch records.

### Local Model Support

Local models are useful for privacy, cost, and high-volume background work.
They should enter through adapters, not special cases.

Open:

- [ ] LM Studio profile.
- [ ] llama.cpp profile.
- [ ] vLLM profile.

Done:

- [x] Ollama profile defaults to `http://localhost:11434/v1`, uses
  `MYTHIFY_OLLAMA_MODEL`, sends no auth header by default, and refuses
  non-local URLs.
- [x] Generic OpenAI-compatible provider probe can call `/v1/models` and
  `/v1/chat/completions`.
- [x] Generic OpenAI-compatible local adapter can run localhost reader and
  triage prompts through `local_model_run`.
- [x] Local model output is marked material, not verification evidence.
- [x] Focused tests cover reader, triage, non-local refusal, and no verification
  state writes.

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

- [ ] Antigravity worker adapter after local prompt, model, workspace, trust,
  and permission behavior is verified.
- [ ] Kimi Work desktop lane.
- [ ] OpenCode Desktop lane.

Done:

- [x] Kimi Code CLI probe.
- [x] OpenCode CLI probe.
- [x] Kimi Code bounded worker run through `host_cli_run`.
- [x] OpenCode bounded worker run through `host_cli_run`.
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

- [x] Operation registry prototype for a small surface.
- [x] Deployed-copy version handshake between protocol text and CLI.
- [x] Step-bound verification records.
- [x] Whole-state refusal no-mutation checks.
- [x] Host model switch capability contract and status model.
- [x] Agents CLI and ADK lifecycle spike.

### v2.7

- [x] First supported local model backend.
- [x] Local reader and triage roles.
- [x] Tests proving local output remains material, not evidence.
- [x] Generic OpenAI-compatible localhost adapter.
- [x] Ollama setup profile.
- [ ] LM Studio setup profile.
- [ ] Host adapter proof of concept for model and thinking overrides where the
  host exposes them.

### v2.8

- [ ] API provider adapter path.
- [ ] Per-role provider defaults.
- [ ] Cost and timeout metadata in worker records.
- [x] CLI/MCP interop matrix for shared mutating operations.
- [x] Kimi Code CLI adapter proof of concept.
- [x] OpenCode CLI adapter proof of concept.

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
