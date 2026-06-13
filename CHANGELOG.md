# Changelog

All notable changes to Mythify are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- MCP `execution_probe` for Google Colab CLI availability checks. The probe
  runs only version and help commands, returns explicit non-billable and
  no-remote-execution guard fields, and records no verification evidence.
- `docs/colab-cli-spike-plan.md` to document the safe Colab CLI spike scope
  and the future evidence fields required before any billable remote execution.
- MCP `lifecycle_probe` for Google Agents CLI and ADK CLI availability checks.
  The probe runs only version, help, and eval-help commands, returns explicit
  no-eval and no-deploy guard fields, and records no verification evidence.
- `docs/agents-cli-adk-spike-plan.md` to document the safe lifecycle probe
  scope and the future evidence fields required before eval or deployment
  execution.
- MCP `local_model_run` for role-limited localhost OpenAI-compatible reader and
  triage runs. The output is marked as material, not verification evidence,
  and the tool writes no Mythify state.
- MCP `host_cli_run` for bounded Kimi Code and OpenCode non-interactive worker
  runs. The output is marked as material, not verification evidence, and the
  tool writes no Mythify state.
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

[Unreleased]: https://github.com/aihxp/mythify/compare/v2.5.0...HEAD
[2.5.0]: https://github.com/aihxp/mythify/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/aihxp/mythify/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/aihxp/mythify/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/aihxp/mythify/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/aihxp/mythify/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/aihxp/mythify/releases/tag/v2.0.0
