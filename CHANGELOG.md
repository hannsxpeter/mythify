# Changelog

All notable changes to Mythify are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No changes yet.

## [3.2.1] - 2026-06-14

### Changed

- CLI `report --mark` and MCP `work_report` mark mode now confirm the cursor is
  ready for future updates instead of using absence-focused no-event wording.

## [3.2.0] - 2026-06-14

### Added

- CLI `report --mark` and MCP `work_report` `mark` mode to start a fresh chat
  cursor at the latest event without replaying old project history.

## [3.1.0] - 2026-06-14

### Added

- Start-here guide focused on Mythify's shortest useful loop, three concrete
  workflows, and which advanced surfaces new users can ignore at first.
- User-local installer script for CLI and MCP launchers from a checkout.
- Chat-ready live work reports through the CLI `report` command and MCP
  `work_report` tool, with cursors that suppress repeated progress updates.

### Changed

- README positioning now leads with Mythify as an evidence protocol for AI
  coding agents and places the minimal plan, verify, step, summary loop before
  the full component surface.
- Distribution docs now name the supported checkout-plus-installer path and
  keep npm registry publishing as an explicit future package-identity decision.

## [3.0.1] - 2026-06-14

### Fixed

- The MCP npm tarball now starts from an unpacked install by loading the
  operation registry, classification rules, and surface manifest from
  package-local `mcp-server/protocol/` mirrors.
- Release checks now compare package-local runtime manifests against the root
  manifests before packaging.

## [3.0.0] - 2026-06-14

### Fixed

- CLI and MCP deterministic classification now load task keyword rules from
  `protocol/classification-rules.json`, removing the duplicated review keyword
  table that caused audit follow-up drift.
- CLI and MCP task classification now treat `evaluate` and `assess`
  codebase/product prompts as review work instead of trivial direct work.
- Security guidance now names verification logs and outcome iterations as
  plain-text evidence surfaces that can include command strings plus output
  tails.
- Audit and readiness docs now distinguish historical snapshots from current
  release signals.
- Completed audit, roadmap, and superseded v3 release-decision notes are now
  archived under `docs/archive/`, with current status preserved in root stubs
  and `docs/release.md`.

### Added

- v3.0.0 final release gate report.
  `docs/archive/release/v3-final-release-gate-2026-06-14.md` records that the
  metadata-aligned final gate passed and that creating a `v3.0.0` tag still
  required explicit approval at that time.
- v3.0.0 release metadata alignment.
  `docs/archive/release/v3-release-metadata-alignment-2026-06-14.md` records
  package metadata, lockfile, changelog anchors, and release-facing docs aligned
  to `3.0.0` before any release-candidate tag or publish action.
- Release version alignment decision record.
  `docs/archive/release/release-version-alignment-decision-2026-06-14.md`
  records `3.0.0` as the intended next release version before any
  release-candidate tag.
- v3.0 release-candidate decision record.
  `docs/archive/release/v3-release-candidate-decision-2026-06-14.md` records
  why the v3.0 release-candidate tag waited until metadata, changelog anchors,
  release docs, and final release gates aligned.
- v3.0 release readiness sweep report.
  `docs/archive/release/v3-release-readiness-sweep-2026-06-14.md` records the
  read-only readiness result, executed gates, remaining external-proof waiting
  items, and the next safe release-candidate decision step without tagging or
  publishing.
- Release process guide. `docs/release.md` documents the current `v3.0.0`
  release gate, build artifacts, GitHub release process, and GitHub package
  artifact posture.
- Host apply and confirm proof watchlist.
  `docs/host-apply-confirm-proof-watchlist.md` now defines proof gates for
  current-chat model apply, current-chat model confirm, worker model override,
  and thinking override before host mutation can become actionable.
- CLI-only to model-runtime migration guide. `docs/cli-to-model-runtime-migration.md`
  now documents the opt-in path from the CLI baseline to MCP, host model
  policy, local models, host CLI workers, hosted provider fanout, remote
  execution substrates, and agent lifecycle lanes while preserving explicit
  user control and executable verification.
- Agent lifecycle lane contract. MCP `lifecycle_probe` now returns
  `lifecycle_lane_contract` with allowed probe commands, disabled lifecycle
  actions, future guarded actions, eval and deployment prerequisites, mutation
  policy, write-state posture, and material-only evidence status. Google
  Agents CLI and ADK registry entries now carry the same probe-only guardrails
  into generated adapter docs.
- Stable cross-platform role assignment metadata. CLI and MCP
  `model_policy.provider_defaults` now expose `role_assignment_contract`,
  mapping session, triage, reader, fanout worker, reviewer, verifier, remote
  execution, and agent lifecycle roles to adapter-interface lanes, provider
  posture, evidence boundaries, state-write posture, and no-hidden-fallback
  guardrails without changing runtime routing.
- Stable adapter interface metadata. CLI and MCP `model_policy.provider_defaults`
  now expose `adapter_interface_contract`, and generated adapter candidate docs
  normalize every registry lane to shared interface, locality, execution,
  state-write, evidence, role, and guardrail fields without changing runtime
  routing.
- Host adapter proof scans. CLI and MCP host-model status now include
  `adapter_proof_scan`, and MCP `host_cli_probe` reports current-chat apply,
  current-chat confirm, worker model override, and thinking override proof
  statuses without mutating host state or recording verification evidence.
- Host-confirmed model fields. CLI and MCP host-model records now include
  `host_confirmation`, separating requested model, user-reported current model,
  confirmation status, confirmation source, timestamps, and unsupported
  reasons without claiming the host current chat changed.
- Hosted provider fanout guardrails. The `anthropic` and `openai` fanout
  engines now require explicit billing, data-transmission, and material-only
  acknowledgements before a job starts; refusal happens before job or audit
  state is written. CLI and MCP `model_policy.provider_defaults` now expose the
  guarded fanout API contract, required acknowledgement fields, audit log path,
  and material-only output status.
- Fanout provider worker audit logs. Each spawned fanout task now appends
  redacted start and finish rows to `.mythify/provider-audit.jsonl`, recording
  provider class, engine, model, role, billing posture, cost metadata fields,
  prompt hash and byte count, output byte count, and the material-only
  verification boundary without storing raw prompts or worker output.
- `scripts/local_model_eval.py` now emits a `fanout_value` report block. It
  records helpful and waste-prone task shapes, per-scenario fanout-fit
  metadata, verifier-backed single-worker sufficiency signals, and a caveat
  that proving real fanout value requires independent worker outputs, a merged
  artifact, and a verifier run after the merge.
- `scripts/local_model_eval.py` now emits a `role_strength` report block. It
  records required stronger roles, scoped reviewer opt-in roles, broader
  stronger-worker opt-in roles, per-role policy rows, observed harness rates,
  and a caveat that stronger-model benefit requires a paired role-isolated run.
- `scripts/local_model_eval.py` now emits a `local_model_benefit` report
  block. It adds scenario task categories, local reader and triage candidate
  roles, fit reasons, observed verifier-backed outcomes, category summaries,
  and a caveat that provider-specific benefit requires a local-model-backed
  run or provider check.
- `scripts/local_model_eval.py` now emits a `profile_overhead` report block.
  It compares measured Mythify profile `model_duration_seconds` against bare
  runs, reporting average duration delta, ratio, lower-duration winner,
  per-profile rows, speed fields, and a local wall-clock smoke-test caveat.
- `scripts/local_model_eval.py` now emits a `false_completion_claims` report
  block. It compares the bounded completion signal of model process exit code
  0 with per-workspace `python3 -m unittest` exit codes, reporting
  verifier-backed claims, false completion claims, rate delta, lower-rate
  winner, conclusion, and a no-tone-scoring caveat.
- `scripts/local_model_eval.py` now emits a `verified_task_success` report
  block. It answers the bare-vs-Mythify verified success question from
  per-workspace `python3 -m unittest` exit codes, including rate delta, winner,
  conclusion, Mythify evidence rate, duration delta, and a local smoke-test
  caveat.
- CLI `readiness` and MCP `release_readiness` read-only release readiness
  views. The view summarizes recorded verification gates, project git state,
  and roadmap state without rerunning gates, mutating state, or declaring the
  release safe.
- CLI `progress` and MCP `outcome_progress` read-only outcome progress views.
  The view shows active and recent outcome loops, iteration budget, verifier
  exit details, metric score when present, and next action without running
  checks, making attempts, stopping loops, or treating notes as verification.
- CLI `history` and MCP `verification_history` read-only verification history
  views. The view shows executed and attested records, verdicts, exit codes,
  duration, and plan or step context without rerunning checks or upgrading
  attested claims.
- CLI `timeline` and MCP `fanout_timeline` read-only fanout worker timeline
  views. The view shows durable job creation, task starts, task finishes,
  duration, status, errors, and output metadata without mutating state or
  treating worker output as verification evidence.
- CLI `phase` and MCP `phase_status` read-only phase views. The view groups
  active plan steps into Understand, Design, Build, Judge, and Verify, adds
  durable evidence counts from memory, lessons, verifications, reflections,
  outcomes, and fanout jobs, and does not mutate state or treat model
  confidence as progress.
- CLI `background` and MCP `background_status` read-only background task
  views. The view summarizes durable outcome loops and fanout jobs with
  statuses, task counts, recent jobs, and next actions without mutating state
  or treating model confidence as progress.
- `protocol/surface-manifest.json` plus `scripts/check_surface_manifest.mjs`
  for duplicated public surface metadata. The manifest owns top-level CLI
  command names and MCP tool names/counts, and CI hygiene now checks it
  against runtime registrations, public docs, and CLI help output.
- CLI `dashboard` and MCP `workflow_status` read-only workflow view. The view
  shows active plan, current and next step, active outcome, evidence counts,
  recent verification records, and recent reflections without mutating state
  or reporting model confidence as evidence.
- Custom adapter contract in CLI and MCP `model_policy.provider_defaults`.
  The contract marks `custom-command` as the bounded user-defined adapter path
  through `MYTHIFY_TRIAGE_COMMAND` and `MYTHIFY_FANOUT_COMMAND`, and records
  `custom-http` as metadata-only with execution disabled until its HTTP
  method, auth, timeout, request, response, cost, and evidence boundaries are
  explicit.
- Cost and timeout metadata for role policy and fanout workers. CLI and MCP
  `model_policy` roles now expose `timeout` and `cost` objects, and fanout
  job and task records store billing, timeout source, pricing reference, and
  explicit `not_estimated` cost status without computing dollar estimates.
- MCP `execution_probe` for Google Colab CLI availability checks. The probe
  runs only version and help commands, returns explicit non-billable and
  no-remote-execution guard fields, and records no verification evidence.
- MCP `execution_run` for guarded Google Colab CLI ephemeral jobs. The tool
  runs `colab run` only after explicit billing, data movement, and cleanup
  acknowledgements, never passes `--keep`, writes no Mythify state, and treats
  remote output as material rather than verification evidence.
- `docs/colab-cli-spike-plan.md` to document the safe Colab CLI spike scope
  and the remote execution guard fields required before billable Colab work.
- MCP `lifecycle_probe` for Google Agents CLI and ADK CLI availability checks.
  The probe runs only version, help, and eval-help commands, returns explicit
  no-eval and no-deploy guard fields, and records no verification evidence.
- `docs/agents-cli-adk-spike-plan.md` to document the safe lifecycle probe
  scope and the future evidence fields required before eval or deployment
  execution.
- MCP `local_model_run` for role-limited localhost OpenAI-compatible reader and
  triage runs. The output is marked as material, not verification evidence,
  and the tool writes no Mythify state.
- MCP `host_cli_run` for bounded Kimi Code, OpenCode, and Antigravity
  non-interactive worker runs. The output is marked as material, not
  verification evidence, and the tool writes no Mythify state. Antigravity
  requires explicit `cwd` and does not pass permission-bypass flags.
- Step-bound verification record fields. CLI and MCP verification entries now
  include active plan and in-progress step context when available, while older
  logs and null-context records remain compatible.
- Expanded CLI/MCP interop coverage for shared mutating state: host-model
  records, memory, lessons, plans and steps, outcomes, verification records,
  and reflections.
- Whole-state no-mutation tests for representative CLI and MCP refusal paths,
  comparing every file under `.mythify` before and after guarded failures.
- `protocol/operation-registry.json` as the first operation registry prototype.
  The Python CLI and MCP server now load the shared memory categories, default
  category, state filename, and no-target `memory_clear` refusal contracts.
- CLI `protocol check` plus generated protocol hash headers, allowing copied
  protocol files to prove they came from the same source protocol as the CLI.
- Host model switch capability status. CLI and MCP host-model records now
  include `host_capability` and `switch_result`, keeping requested model state
  separate from host-confirmed current-chat changes.
- CLI `logs compact` maintenance command for top-level verification and
  reflection logs. It archives raw originals, keeps recent valid active
  records, supports dry runs, and records no verification evidence.
- Generated `docs/adapter-candidates.md`, built from the capability registry
  by `node scripts/build_registry_docs.mjs` and protected by Node and CI drift
  checks.
- Per-role provider defaults in CLI and MCP `model_policy`, covering session,
  triage, reader, fanout worker, reviewer, and verifier roles with explicit
  no-implicit-fallback metadata.
- Stronger reviewer opt-in policy. CLI and MCP classification now expose
  reviewer strength defaults, and `fanout_start` allows stronger reviewer
  models only when the task is marked `role: "reviewer"` and the job sets
  `reviewer_allow_stronger: true`, unless the broader spawn ceiling opt-in is
  used.
- Ollama local setup profile. `provider_probe` and `local_model_run` now accept
  `provider: "ollama"`, default to the local Ollama OpenAI-compatible `/v1`
  endpoint, use `MYTHIFY_OLLAMA_MODEL`, send no auth header by default, and keep
  output marked as material rather than verification evidence.
- LM Studio local setup profile. `provider_probe` and `local_model_run` now
  accept `provider: "lm-studio"`, default to the local LM Studio
  OpenAI-compatible `/v1` endpoint, use `MYTHIFY_LM_STUDIO_MODEL`, send no auth
  header by default, and keep output marked as material rather than
  verification evidence.
- llama.cpp local setup profile. `provider_probe` and `local_model_run` now
  accept `provider: "llama-cpp"`, default to `http://localhost:8080/v1`, use
  `MYTHIFY_LLAMA_CPP_MODEL`, send no auth header by default, and keep output
  marked as material rather than verification evidence.
- vLLM local setup profile. `provider_probe` and `local_model_run` now accept
  `provider: "vllm"`, default to `http://localhost:8000/v1`, use
  `MYTHIFY_VLLM_MODEL`, send no auth header by default, and keep output marked
  as material rather than verification evidence.
- API provider adapter metadata path. CLI and MCP `model_policy` now expose
  hosted provider metadata for OpenAI, Anthropic, and hosted
  OpenAI-compatible endpoints, including auth env names, timeout defaults, cost
  metadata fields, pricing references, explicit billing posture, and
  `execution_enabled: false`.
- Role defaults per provider. CLI and MCP `model_policy.provider_defaults` now
  include a provider catalog for `host`, `host_cli`, `local_openai_compatible`,
  `api_provider`, `command`, and `local_command`, plus each resolved role's
  selected provider profile.
- Kimi Work desktop metadata. The capability registry now tracks Kimi Work as
  a metadata-only `desktop_agent` candidate with manual model-switching and
  spawning posture until a documented or locally probeable automation contract
  exists.
- OpenCode Desktop metadata. The capability registry now tracks OpenCode
  Desktop as a metadata-only `desktop_agent` candidate and points automation
  toward the existing OpenCode CLI worker plus future server or SDK slices
  instead of driving the desktop app directly.

### Changed

- One-core architecture direction is now explicit: keep the Python CLI and
  Node MCP server as native adapters while moving duplicated facts into checked
  protocol files, registries, generated docs, schemas, or manifests one surface
  at a time.

## [2.5.0] - 2026-06-12

### Added

- Opt-in `MYTHIFY_REQUIRE_VERIFIED_STEP` gate. When set to `1`, marking a step
  `completed` requires a recorded passing executed verification (a `verify run`
  with exit 0) since the step started, not just a non-empty RESULT string. The
  gate applies to both the CLI `step` command and the MCP `plan_update_step`
  tool. Default off preserves the existing behavior exactly.

### Fixed

- CLI `verify run` now honors `MYTHIFY_DISABLE_RUN` for parity with the MCP
  server. Previously the CLI ignored it and executed the command regardless;
  it now executes nothing, records nothing, prints the disabled message, and
  exits 2.
- `docs/design.md` tool-count self-contradiction: the fanout tools subsection
  said "total 17" while the rest of the document said 22. It now reads
  "total 22".

## [2.4.0] - 2026-06-12

### Added

- Optional fast model triage after classification. `classify --triage auto`
  and MCP `classify_task` with `triage: "auto"` run one local fast model only
  when the deterministic gate recommends it, returning a JSON problem frame
  without treating model output as verification.
- Local fast triage engine configuration:
  `MYTHIFY_TRIAGE_ENGINE`, `MYTHIFY_TRIAGE_MODEL`,
  `MYTHIFY_TRIAGE_COMMAND`, `MYTHIFY_TRIAGE_CLAUDE_BIN`,
  `MYTHIFY_TRIAGE_CODEX_BIN`, and `MYTHIFY_TRIAGE_CURSOR_BIN`.
- Platform-aware model policy from CLI `classify` and MCP `classify_task`,
  separating host-selected session model, spawned triage worker, fanout
  worker, reviewer, verifier, spawned model policy, and effort.
- Task-based host chat recommendations in `model_policy.session.recommendation`,
  including keep, downgrade, upgrade, or set actions plus target profile,
  target model, thinking, and speed.
- Fanout visibility modes for spawned workers: summary by default, with quiet,
  verbose, threaded, and auto prompt inference through `fanout_start`.
- Host chat model switch state through CLI `host-model` and MCP
  `host_model_switch`, recording `.mythify/host-model.json` for later
  `classify_task` and `fanout_start` session model defaults while returning
  host-specific switch guidance.
- Initiating-model awareness through `session_model`,
  `MYTHIFY_SESSION_MODEL`, `spawn_ceiling`, and `MYTHIFY_SPAWN_CEILING`, with
  same-or-lower spawning by default and explicit opt-in for stronger workers.
- Fanout effort selection through job-level and per-task `effort`, plus
  `MYTHIFY_FANOUT_EFFORT`, with resolved effort recorded in `job.json` and
  inserted into worker prompts.
- Fanout speed selection through job-level and per-task `speed`, plus
  `MYTHIFY_FANOUT_SPEED`, with Codex `fast` and `standard` mapped to Codex
  fast-mode config overrides.
- Platform-specific fanout mapping for Claude and Cursor: Claude receives
  resolved effort through `--effort`, and Cursor resolves `model`, `effort`,
  and `speed` to encoded model ids from `cursor-agent models` when available.
- Execution profiles from CLI `classify` and MCP `classify_task`, including a
  fast profile for focused low-risk work that skips plan state but still
  requires executed `verify run` evidence.
- `scripts/local_model_eval.py --mythify-profile` with `auto`, `fast`, and
  `standard` modes, so local benchmarks can measure fast Mythify runs against
  the older plan-plus-verify behavior.

### Changed

- Fanout job metadata now records model, effort, and speed sources for both
  the job and each task.
- Fanout job metadata now records session model, session tier, spawn ceiling,
  model tier, and ceiling status for jobs and tasks.
- MCP package metadata now reports version 2.4.0.

## [2.3.0] - 2026-06-12

### Added

- Supervised outcome loops through CLI `outcome` and MCP outcome tools, with a
  verifier command, optional metric command, iteration budget, durable
  `.mythify/outcomes` state, and explicit success, retry, stop decisions.
- Problem classification through the CLI (`classify`) and MCP
  (`classify_task`) so agents can identify task type, risk, ceremony level,
  verification strategy, and fanout fit before planning.
- Codex integration guide covering Codex Desktop, Codex MCP registration,
  local Codex fanout workers, and the local benchmark workflow.
- Cross-desktop MCP tool-call guide and example configs for Codex Desktop,
  Claude Desktop, and Cursor Desktop.

## [2.2.0] - 2026-06-12

### Added

- Local subscription-backed fanout engines for `codex-cli` and
  `cursor-agent`, so users can run parallel workers through existing Codex or
  Cursor CLI logins without configuring API keys.
- Configuration for local worker binaries and safety defaults:
  `MYTHIFY_FANOUT_CODEX_BIN`, `MYTHIFY_FANOUT_CODEX_SANDBOX`,
  `MYTHIFY_FANOUT_CODEX_ARGS`, `MYTHIFY_FANOUT_CURSOR_BIN`,
  `MYTHIFY_FANOUT_CURSOR_MODE`, `MYTHIFY_FANOUT_CURSOR_FORCE`, and
  `MYTHIFY_FANOUT_CURSOR_ARGS`.
- Local bare-vs-Mythify comparison harness (`scripts/local_model_eval.py`)
  with an offline unit test and opt-in real local CLI runs for Claude, Codex,
  Cursor, or a generic command worker.
- Built-in local benchmark scenarios for the comparison harness, with JSON
  summary metrics for verified success rate, Mythify evidence rate, and
  average model duration.

### Changed

- Fanout auto-detection now prefers local subscription CLIs before API
  engines: `claude-cli`, `codex-cli`, `cursor-agent`, then API or command
  fallbacks.

## [2.1.0] - 2026-06-12

### Added

- Fanout parallel delegation (MCP only): the orchestrating model declares a
  one-shot task list and the server spawns, sequences, and collects parallel
  sub-workers through `fanout_start`, `fanout_status`, and `fanout_results`.
- Four worker engines: subscription-billed `claude-cli` workers (no API key
  needed), the `anthropic` and `openai` HTTP APIs, and a generic `command`
  engine that runs any local CLI agent through a shell template.
- Model selection at three levels, most specific wins: per-task `model`
  overrides per-job `model` overrides `MYTHIFY_FANOUT_MODEL`. The same
  precedence applies to engines, so one job can mix models and engines
  across tasks.

## [2.0.0] - 2026-06-12

First published release. Mythify 1.x was an unreleased prototype; 2.0.0 is a
ground-up rebuild around the contracts in [docs/design.md](docs/design.md).

### Added

- Executed verification: `verify run` (CLI) and `verify_run` (MCP) execute a
  command and record the real exit code, duration, and output tails. An exit
  code is evidence; self-report is not.
- Per-project state: each project owns a `.mythify/` directory with named
  plans, key-value memory, lessons, and append-only verification and
  reflection logs. Discovery walks upward from the working directory;
  `MYTHIFY_DIR` overrides.
- Node MCP server (`mcp-server/`) exposing the contract as 12 tools for
  clients without shell access (Claude Desktop and any MCP client).
- Generated protocol variants: `protocol/PROTOCOL.md` is the single source;
  `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` are built by
  `scripts/build_variants.py`, and CI fails on drift.
- Manus-style skill package (`skills/mythify/`), built into
  `dist/mythify.skill` by `scripts/package_skill.py`.
- Test suites: 47 Python tests (including a CLI-to-MCP interop test against
  one shared state directory) and an MCP smoke test that asserts the on-disk
  formats field by field.
- Durability: atomic writes, corrupt-state quarantine
  (`<file>.corrupt-<timestamp>`), and graceful behavior on missing state.
- Claude Desktop and Claude Code integration guide
  ([docs/claude-integrations.md](docs/claude-integrations.md)), including
  running Mythify on smaller models.

### Changed

- State moved from a single shared `~/.mythify/` to per-project `.mythify/`
  directories; only cross-project lessons remain global (`~/.mythify/lessons`).
- Self-attested claims (`verify claim` / `verify_claim`) are now recorded with
  `verified: null` and never count as verified; completion and failure step
  updates require evidence.
- Protocol ceremony is proportional to task size; trivial tasks pay zero
  protocol overhead.

### Removed

- The legacy prototype files (Manus research dumps, the old single-file
  orchestrator, and prebuilt `.skill` archives). The source research report is
  preserved verbatim at [docs/research-report.md](docs/research-report.md).

[Unreleased]: https://github.com/aihxp/mythify/compare/v3.2.1...HEAD
[3.2.1]: https://github.com/aihxp/mythify/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/aihxp/mythify/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/aihxp/mythify/compare/v3.0.1...v3.1.0
[3.0.1]: https://github.com/aihxp/mythify/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/aihxp/mythify/compare/v2.5.0...v3.0.0
[2.5.0]: https://github.com/aihxp/mythify/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/aihxp/mythify/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/aihxp/mythify/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/aihxp/mythify/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/aihxp/mythify/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/aihxp/mythify/releases/tag/v2.0.0
