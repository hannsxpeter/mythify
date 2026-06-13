# Mythify Future Roadmap

This is a memory aid for product direction, not a committed release schedule.
The goal is to keep Mythify focused as it grows beyond CLI-native users.

## How To Read This

Start here:

- `Active Now` is the one slice currently being worked.
- `Next Queue` is the ordered short list to pull from next.
- `Open Work By Track` is everything not done yet.
- `Shipped Work` is the archive of completed slices.

Status markers:

- `[ ]` Not started
- `[>]` In progress
- `[x]` Done
- `[~]` Deferred or waiting on external proof

## Active Now

- [>] Host-confirmed model fields.
  - Current goal: record host-confirmed current model information only where a
    host can actually provide it.
  - Next step: map current `host_model_switch` status records, capability
    registry fields, and host adapter candidates before adding any
    confirmation fields.
  - Guardrail: Mythify may record requested model changes, but it must not
    claim the current host chat changed unless the host confirms it.

## Next Queue

Nothing queued right now.

## Open Work By Track

### Host Adapter Candidates

What remains:

Nothing open right now.

Decision needed:

- Is each desktop product a host lane, a probe-only adapter candidate, or a
  backlog-only research item?
- What can be proven from local commands or official docs?
- What should remain manual until a host exposes a real automation API?

Already shipped in this track:

- [x] OpenCode Desktop lane mapped as metadata-only `desktop_agent`.
- [x] Kimi Work desktop lane mapped as metadata-only `desktop_agent`.
- [x] Kimi Code CLI probe.
- [x] OpenCode CLI probe.
- [x] Kimi Code bounded worker run through `host_cli_run`.
- [x] OpenCode bounded worker run through `host_cli_run`.
- [x] Antigravity CLI probe.
- [x] Antigravity MCP setup guide.
- [x] Antigravity bounded worker run through `host_cli_run`.

### Model Assignment

What remains:

Nothing open right now.

Already shipped in this track:

- [x] Platform-aware model policy.
- [x] Task-based host model recommendations.
- [x] Same-or-lower default worker spawning.
- [x] Explicit stronger-worker ceiling.
- [x] Stronger reviewer opt-in flow.
- [x] Provider-specific role defaults in CLI and MCP `model_policy`.
- [x] API provider metadata exposed before hosted execution exists.
- [x] Cost and timeout metadata per role and fanout worker records.

Role model:

- `session`: main host model, controlled by the user or host app.
- `triage`: cheap or fast model for task classification.
- `reader`: cheap, local, or privacy-preferred model for codebase reading.
- `worker`: same-or-lower model for independent subtasks.
- `reviewer`: same-or-stronger only when explicitly allowed.
- `verifier`: command-first, not model-first.

### Host Model Switching

What remains:

- [ ] Apply model or thinking changes when a host exposes a real capability.
- [>] Add host-confirmed current model fields where supported.
- [ ] Add adapter execution tests once a host exposes apply or confirm APIs.

Already shipped in this track:

- [x] `host_model_switch` records requested model state.
- [x] Status output includes registry-backed host capability information.
- [x] Recorded desired model is not treated as proof that the host switched.
- [x] `switch_result` separates manual requested state from applied or
  host-confirmed current-chat state.
- [x] CLI and MCP text output show current-chat confirmation, manual-action
  status, and per-host capability fields.
- [x] Focused tests cover current public capability fields for requested host
  switch records.

Core rule: Mythify can recommend or request a host model switch, but it should
not pretend the switch happened unless the host adapter confirms it.

### Local Model Support

What remains:

Nothing open right now.

Already shipped in this track:

- [x] Generic OpenAI-compatible provider probe can call `/v1/models` and
  `/v1/chat/completions`.
- [x] Generic OpenAI-compatible local adapter can run localhost reader and
  triage prompts through `local_model_run`.
- [x] Ollama profile defaults to `http://localhost:11434/v1`.
- [x] LM Studio profile defaults to `http://localhost:1234/v1`.
- [x] llama.cpp profile defaults to `http://localhost:8080/v1`.
- [x] vLLM profile defaults to `http://localhost:8000/v1`.
- [x] Local profiles send no auth header by default where appropriate.
- [x] Local profiles refuse non-local URLs.
- [x] Local model output is marked material, not verification evidence.
- [x] Focused tests cover reader, triage, non-local refusal, and no
  verification state writes.

### API Provider Support

What remains:

Nothing open right now.

Already shipped in this track:

- [x] Hosted execution for OpenAI, Anthropic, and OpenAI-compatible APIs.
- [x] Clear audit logs for spawned provider work.
- [x] Custom adapter contract separates bounded custom command execution from
  metadata-only custom HTTP.
- [x] OpenAI, Anthropic, and hosted OpenAI-compatible provider metadata includes
  auth env names, billing posture, timeout defaults, cost metadata fields, and
  pricing references.
- [x] API provider metadata keeps general provider role routing disabled unless
  execution is explicitly guarded.
- [x] No-surprise cross-provider fallback policy is recorded in API provider
  metadata.
- [x] Generic OpenAI-compatible probe shape exists.

### Execution and Agent Lifecycle Adapters

What remains:

Nothing open right now.

Already shipped in this track:

- [x] `execution_run` runs guarded Google Colab CLI ephemeral jobs through
  `colab run` only after explicit billing, data movement, and cleanup
  acknowledgements.
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

### Architecture Runway

What remains:

Nothing open right now.

Already shipped in this track:

- [x] Surface manifest owns top-level CLI commands and MCP tool names/counts,
  with CI checks against runtime registrations, public docs, and CLI help.
- [x] One-core architecture decision keeps CLI and MCP as native adapters while
  moving duplicated facts into checked contract artifacts one surface at a time.
- [x] Read-only workflow dashboard exposes plan, outcome, verification, and
  reflection state without model-confidence fields.
- [x] Capability registry exists in `mcp-server/src/capability-registry.js`.
- [x] Registry data is shown in `host_model_switch` status output.
- [x] Generated adapter candidate docs come from the capability registry and
  are protected by drift checks.
- [x] Generated protocol files carry a source hash.
- [x] CLI `protocol check` detects copied-file drift before workspace
  initialization.
- [x] Operation registry powers shared memory operation categories, default
  category, state filename, and no-target clear refusals.
- [x] Verification records include active plan and in-progress step context.
- [x] Full CLI/MCP interop matrix covers shared mutating operations.
- [x] Refusal paths have whole-state no-mutation snapshot checks.
- [x] Log compaction archives raw top-level verification and reflection logs
  before trimming active logs to recent valid records.

### Workflow Surfaces

What remains:

Nothing open right now.

Principle: reveal evidence, do not decorate self-report.

Already shipped in this track:

- [x] Release readiness through CLI `readiness` and MCP
  `release_readiness`.
- [x] Outcome loop progress through CLI `progress` and MCP
  `outcome_progress`.
- [x] Verification history through CLI `history` and MCP
  `verification_history`.
- [x] Fanout worker timeline through CLI `timeline` and MCP
  `fanout_timeline`.
- [x] Phase view for Understand, Design, Build, Judge, Verify through CLI
  `phase` and MCP `phase_status`.
- [x] Background task view through CLI `background` and MCP
  `background_status`.
- [x] Status dashboard through CLI `dashboard` and MCP `workflow_status`.

### Evaluation

What remains:

Nothing open right now.

Already shipped in this track:

- [x] Fanout value is reported through the local eval harness
  `fanout_value` JSON block, showing helpful and waste-prone task shapes,
  per-scenario fanout-fit metadata, verifier-backed single-worker sufficiency
  for built-in smoke scenarios, and a caveat that real fanout value requires
  independent worker outputs, a merged artifact, and a verifier run after the
  merge.
- [x] Stronger-model role requirements are reported through the local eval
  harness `role_strength` JSON block, showing that no role requires stronger
  models by default, reviewers have a scoped stronger-model opt-in, broader
  stronger workers require `spawn_ceiling: "allow_stronger"`, and verifiers
  remain command-first.
- [x] Local model task benefit is reported through the local eval harness
  `local_model_benefit` JSON block, identifying scenario task categories that
  fit local reader and triage roles, observed verifier-backed outcomes,
  Mythify evidence rates, category summaries, and a caveat that
  provider-specific benefit requires a local-model-backed run or provider
  check.
- [x] Profile overhead is reported through the local eval harness
  `profile_overhead` JSON block, comparing measured Mythify profile
  `model_duration_seconds` against bare runs with average duration delta,
  ratio, lower-duration winner, per-profile rows, speed fields, and a local
  wall-clock smoke-test caveat.
- [x] False completion claims are reported through the local eval harness
  `false_completion_claims` JSON block, comparing model process exit code 0
  with executed verifier results and reporting verifier-backed claims, false
  completion claims, rate delta, lower-rate winner, conclusion, and a
  no-tone-scoring caveat.
- [x] Verified task success is reported through the local eval harness
  `verified_task_success` JSON block, with rate delta, winner, conclusion,
  Mythify evidence rate, duration delta, and a local smoke-test caveat derived
  from executed verifier exit codes.
- [x] Local bare-vs-Mythify evaluation harness.
- [x] Fast Mythify profile support.

Evidence should come from rerunning verifiers, not from model self-ratings.

## Shipped Work

### Recent Completed Slices

- [x] 2026-06-13: add hosted provider fanout guardrails.
  The `anthropic` and `openai` fanout engines now require explicit billing,
  data-transmission, and material-only acknowledgements before a job starts,
  refuse before writing job or audit state when acknowledgements are missing,
  and expose the guarded fanout API contract through CLI and MCP
  `model_policy.provider_defaults`.
- [x] 2026-06-13: add provider worker audit logs.
  Fanout worker task start and finish events now append to
  `.mythify/provider-audit.jsonl`, recording provider class, engine, model,
  role, billing posture, cost metadata fields, redacted prompt hash and byte
  count, redacted output byte count, and the material-only verification
  boundary without storing raw prompts, context, API keys, authorization
  headers, or worker output.
- [x] 2026-06-13: add fanout value evaluation reporting.
  `scripts/local_model_eval.py` now emits `fanout_value`, a policy and
  harness-evidence report showing helpful and waste-prone task shapes,
  per-scenario fanout-fit metadata, verifier-backed single-worker sufficiency
  counts for built-in smoke scenarios, observed harness rates, and a caveat
  that proving real fanout value requires independent worker outputs, a merged
  artifact, and a verifier run after the merge.
- [x] 2026-06-13: add stronger-model role evaluation reporting.
  `scripts/local_model_eval.py` now emits `role_strength`, a policy and
  harness-evidence report showing required stronger roles, scoped reviewer
  opt-in, broader stronger-worker opt-in, per-role boundaries, observed
  harness rates, and a caveat that stronger-model benefit requires a paired
  role-isolated run.
- [x] 2026-06-13: add local model benefit evaluation reporting.
  `scripts/local_model_eval.py` now emits `local_model_benefit`, mapping
  built-in scenario categories to local reader and triage candidate roles and
  pairing that metadata with observed verifier-backed outcomes. The report
  keeps provider-specific benefit as a caveated claim until a local-model-backed
  command or provider check supplies evidence.
- [x] 2026-06-13: add profile overhead evaluation reporting.
  `scripts/local_model_eval.py` now emits `profile_overhead`, a
  bare-vs-Mythify smoke comparison based on measured model subprocess
  durations. It reports average duration delta, ratio, lower-duration winner,
  per-profile rows, speed fields, and a local wall-clock caveat.
- [x] 2026-06-13: add false completion claim evaluation reporting.
  `scripts/local_model_eval.py` now emits `false_completion_claims`, a
  bare-vs-Mythify smoke comparison that counts model process exit code 0 as a
  bounded completion signal, then checks it against per-workspace unittest
  exits to report verifier-backed claims, false completion claims, rate delta,
  lower-rate winner, conclusion, and a no-tone-scoring caveat.
- [x] 2026-06-13: add verified task success evaluation reporting.
  `scripts/local_model_eval.py` now emits `verified_task_success`, a direct
  bare-vs-Mythify smoke comparison based on per-workspace unittest exit codes,
  with delta, winner, conclusion, Mythify evidence rate, duration delta, and
  an explicit smoke-test caveat.
- [x] 2026-06-13: add read-only release readiness. CLI `readiness` and MCP
  `release_readiness` summarize recorded verification gates, project git state,
  and roadmap state without rerunning gates, mutating state, tagging,
  publishing, pushing, or declaring the release safe.
- [x] 2026-06-13: add read-only outcome progress. CLI `progress` and MCP
  `outcome_progress` show active and recent outcome loops, iteration budget,
  last verifier exit details, metric score when present, and next action
  without running checks, making attempts, stopping loops, or treating notes as
  verification.
- [x] 2026-06-13: add read-only verification history. CLI `history` and MCP
  `verification_history` show executed and attested records, verdicts, exit
  codes, duration, and plan or step context without rerunning checks or
  upgrading attested claims.
- [x] 2026-06-13: add read-only fanout worker timeline. CLI `timeline` and MCP
  `fanout_timeline` show durable job creation, task starts, task finishes,
  duration, status, errors, and output metadata without mutating state or
  treating worker output as verification evidence.
- [x] 2026-06-13: add read-only phase view. CLI `phase` and MCP
  `phase_status` group active plan steps into Understand, Design, Build,
  Judge, and Verify with durable evidence counts, without mutating state or
  treating model confidence as progress.
- [x] 2026-06-13: add read-only background task view. CLI `background` and MCP
  `background_status` summarize durable outcome loops and fanout jobs with
  statuses, task counts, recent jobs, and next actions without mutating state
  or treating model confidence as progress.
- [x] 2026-06-13: add registry-backed surface manifest. The manifest owns
  top-level CLI commands and MCP tool names/counts, while
  `scripts/check_surface_manifest.mjs` verifies runtime registrations, public
  docs, and CLI help without generating runtime handlers or schemas.
- [x] 2026-06-13: decide one-core architecture direction. Mythify keeps the
  Python CLI and Node MCP server as native adapters while moving duplicated
  facts into checked protocol files, registries, generated docs, schemas, or
  manifests one surface at a time.
- [x] 2026-06-13: add read-only workflow dashboard surfaces. CLI `dashboard`
  and MCP `workflow_status` now show active plan, current and next step,
  active outcome, evidence counts, recent verification records, and recent
  reflections without mutating state or reporting model confidence as
  evidence.
- [x] 2026-06-13: add custom adapter contract to CLI and MCP
  `model_policy.provider_defaults`. Mythify now names `custom-command` as the
  bounded user-defined path through triage and fanout command engines, while
  keeping `custom-http` metadata-only until HTTP method, auth, timeout,
  request, response, cost, and evidence boundaries are explicit.
- [x] 2026-06-13: add cost and timeout metadata to role policy and fanout
  worker records. Mythify records timeout source, billing posture, pricing
  references, and explicit not-estimated cost status without guessing dollar
  costs.
- [x] 2026-06-13: add guarded Google Colab CLI remote execution through
  `execution_run`. The adapter wraps the official `colab run` ephemeral path,
  requires billing, data movement, and cleanup acknowledgements, writes no
  Mythify state, and keeps remote output as material rather than verification
  evidence.
- [x] 2026-06-13: map OpenCode Desktop as a metadata-only `desktop_agent`
  candidate in the capability registry. Automation stays on the existing
  OpenCode CLI worker and future server or SDK slices until a desktop
  automation contract exists.
- [x] 2026-06-13: map Kimi Work desktop as a metadata-only `desktop_agent`
  candidate in the capability registry. Kimi Work remains manual for
  model-switching and spawning until a documented or locally probeable
  automation surface exists.
- [x] 2026-06-13: refactor roadmap navigation around `Active Now`,
  `Next Queue`, `Open Work By Track`, and `Shipped Work`, so unfinished items
  appear before completed history in each track.
- [x] 2026-06-13: add bounded Antigravity worker runs through `host_cli_run`,
  with explicit workspace `cwd`, optional model flag forwarding, native
  permission handling, and material-only output. The local `agy` shim on this
  host was broken, so the live prompt path is covered by deterministic MCP
  tests and the official CLI contract instead of a real Antigravity run.
- [x] 2026-06-13: add provider-specific role defaults to `model_policy`,
  including allowed roles, default roles, billing posture, execution boundary,
  evidence status, state-write posture, and selected role provider profiles.
- [x] 2026-06-13: add hosted API provider metadata for OpenAI, Anthropic, and
  hosted OpenAI-compatible endpoints, including cost fields, timeout defaults,
  explicit billing posture, and no hidden provider fallback.
- [x] 2026-06-13: add vLLM local setup profile for `provider_probe` and
  `local_model_run`, defaulting to `http://localhost:8000/v1` with
  material-only output.
- [x] 2026-06-13: add llama.cpp local setup profile for `provider_probe` and
  `local_model_run`, defaulting to `http://localhost:8080/v1` with
  material-only output.
- [x] 2026-06-13: add LM Studio local setup profile for `provider_probe` and
  `local_model_run`, defaulting to the local `/v1` endpoint with material-only
  output.
- [x] 2026-06-13: add Ollama local setup profile for `provider_probe` and
  `local_model_run`, defaulting to the local `/v1` endpoint with material-only
  output.
- [x] 2026-06-13: add stronger reviewer opt-in policy for classifier output
  and fanout tasks, keeping ordinary workers same-or-lower by default.

### Foundation Completed Slices

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
- [x] LM Studio setup profile.
- [ ] Host adapter proof of concept for model and thinking overrides where the
  host exposes them.

### v2.8

- [x] API provider adapter path.
- [x] Per-role provider defaults.
- [x] Cost and timeout metadata in worker records.
- [x] Custom command and HTTP adapter contract.
- [x] CLI/MCP interop matrix for shared mutating operations.
- [x] Kimi Code CLI adapter proof of concept.
- [x] OpenCode CLI adapter proof of concept.
- [x] Antigravity CLI adapter proof of concept.

### v3.0

- [ ] Stable cross-platform role assignment.
- [ ] Stable adapter interface.
- [x] Desktop local-agent lane for Kimi Work and OpenCode Desktop style
  workflows.
- [x] Execution adapter lane for Colab CLI style remote jobs.
- [ ] Agent lifecycle lane for Agents CLI and ADK style workflows.
- [x] One-core architecture decision based on the registry prototype.
- [x] Stronger workflow surfaces.
- [ ] Clear migration guide from CLI-only usage to model-runtime orchestration.

## References

- `docs/host-model-switching-research.md`
- `docs/local-llm-and-new-host-research.md`
- `docs/colab-cli-spike-plan.md`
- `docs/antigravity-mcp-setup.md`
