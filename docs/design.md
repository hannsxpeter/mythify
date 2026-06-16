# Mythify v2 Design Specification

This document is the single source of truth for Mythify's contracts: the CLI command
surface, the MCP tool surface, the on-disk state formats, and the output conventions.
The Python CLI (`scripts/mythify.py`) and the MCP server (`mcp-server/src/index.js`)
are independent implementations of the same contracts and must interoperate on the
same state directory.

## Goals

1. Real verification. Completion claims are checked by executing commands and reading
   exit codes, not by self-report. Self-attested claims are allowed but are recorded
   and displayed as second-class evidence.
2. Per-project state. Each project owns a `.mythify/` directory. The only global state
   is the cross-project lessons store.
3. Proportional ceremony. Protocol overhead scales with task size. Trivial tasks pay
   zero overhead.
4. Durability. Atomic writes, corrupt-file recovery, and no crashes on bad state.

## Writing rules (every file in this repository)

- No emojis. Use ASCII markers: `[OK]`, `[FAIL]`, `[WARN]`.
- No em dashes (U+2014) and no en dashes (U+2013). Use commas, colons, parentheses,
  or plain hyphens instead.
- No pending-work markers, no placeholder content. Every file ships complete.
- Documentation is imperative and concise.
- Exception: `docs/research-report.md` is preserved legacy content, copied verbatim,
  and is exempt from these character rules.

## Repository layout (final)

```
mythify/
|-- README.md
|-- LICENSE                      MIT, holder "Mythify contributors", year 2026
|-- .gitignore
|-- CLAUDE.md                    generated from protocol/PROTOCOL.md
|-- AGENTS.md                    generated from protocol/PROTOCOL.md
|-- .cursorrules                 generated from protocol/PROTOCOL.md
|-- protocol/
|   |-- PROTOCOL.md              canonical protocol source
|   |-- classification-rules.json deterministic classifier keywords
|   |-- operation-registry.json  shared operation metadata
|   |-- workflow-router.json     shared workflow route metadata
|   `-- surface-manifest.json    shared public surface metadata
|-- scripts/
|   |-- mythify.py               zero-dependency CLI orchestrator
|   |-- build_variants.py        generates CLAUDE.md, AGENTS.md, .cursorrules
|   |-- build_registry_docs.mjs  generates registry-backed docs
|   |-- check_surface_manifest.mjs checks public surface metadata drift
|   |-- install_user.sh          user-local CLI and MCP launcher installer
|   |-- local_model_eval.py      local bare-vs-Mythify comparison harness
|   `-- package_skill.py         builds dist/mythify.skill from skills/mythify/
|-- mcp-server/
|   |-- package.json
|   |-- mcp-config.example.json
|   |-- client-configs/
|   |-- src/capability-registry.js
|   |-- src/fanout.js
|   |-- src/index.js
|   |-- src/operation-registry.js
|   |-- src/surface-manifest.js
|   |-- protocol/classification-rules.json package copy of classifier keywords
|   |-- protocol/operation-registry.json package copy of operation metadata
|   |-- protocol/workflow-router.json package copy of route metadata
|   |-- protocol/surface-manifest.json package copy of public surface metadata
|   |-- test/capability-registry.test.js
|   |-- test/execution-probe.test.js
|   |-- test/host-cli-probe.test.js
|   |-- test/host-cli-run.test.js
|   |-- test/lifecycle-probe.test.js
|   |-- test/local-model-run.test.js
|   |-- test/provider-probe.test.js
|   |-- test/smoke.test.js
|   `-- test/fanout.test.js
|-- skills/
|   `-- mythify/
|       |-- SKILL.md
|       `-- references/
|           |-- autonomy-loop.md
|           |-- self-verification.md
|           |-- memory-system.md
|           `-- meta-prompts.md
|-- tests/
|   |-- test_mythify.py          CLI unit and end-to-end tests (stdlib unittest)
|   |-- test_interop.py          CLI and MCP server against the same state dir
|   `-- test_local_model_eval.py offline test for the local comparison harness
`-- docs/
    |-- design.md                this document
    |-- codex-integrations.md    Codex Desktop, CLI, MCP, and benchmark setup
    |-- claude-integrations.md   Claude Desktop and Claude Code guide
    |-- adapter-candidates.md    generated from the capability registry
    |-- antigravity-mcp-setup.md Antigravity CLI probe and MCP setup guide
    |-- agents-cli-adk-spike-plan.md Google Agents CLI and ADK probe plan
    |-- colab-cli-spike-plan.md  Google Colab CLI non-billable spike plan
    `-- research-report.md       preserved research report
```

`dist/` (built skill packages) and `node_modules/` are build outputs, ignored by git.

## One-core architecture decision

Decision: Mythify will move toward one shared contract core, but it will not do a
whole-runtime rewrite yet. The Python CLI and Node MCP server stay as separate
host adapters. Shared behavior moves behind small, checked contract artifacts
only after duplication has produced drift or maintenance pressure.

Evidence for the decision:

- The CLI and MCP already duplicate state I/O, JSONL reads, atomic writes,
  shell verification, plan updates, outcome loops, model policy, host-model
  records, and dashboard formatting. Examples include `build_dashboard` in
  `scripts/mythify.py` and `buildWorkflowDashboard` in
  `mcp-server/src/index.js`, plus parallel `run_shell_capture` and
  `runShellCapture` implementations.
- The shared registries are working where the duplicated facts are narrow:
  `protocol/operation-registry.json` owns memory operation metadata, and
  `protocol/classification-rules.json` owns deterministic classifier keyword
  metadata. `protocol/workflow-router.json` owns route ids, prompt mapping,
  and output field metadata. `mcp-server/src/capability-registry.js` owns host,
  provider, execution, and lifecycle capability metadata.
- Drift is still easy to create in prose and copied surface metadata. The
  dashboard slice raised the MCP tool contract to 30 tools, while the README
  component summary still said 29 until this decision pass.
- `tests/test_interop.py` proves the two runtimes can share one `.mythify`
  state directory for mutating state families, so migration can be incremental
  without breaking existing users.

Policy:

- Keep Python CLI command handling, Node MCP handler wiring, host CLI process
  execution, and MCP fanout runtime code in their native adapters for now.
- Put shared facts into explicit artifacts first: protocol files, operation
  registries, capability registries, generated docs, schemas, or manifests.
- Add or expand a shared artifact only when a focused drift test protects the
  generated or shared output.
- Prefer data contracts and generation over a cross-language runtime dependency
  until at least two more duplicated surfaces demonstrate recurring drift.
- Every migration slice must preserve the on-disk state contract, evidence
  boundaries, no-mutation guarantees, and CLI/MCP interop tests.

Migration guardrails:

- `docs/design.md` leads. The contract changes before implementation.
- One surface per slice. Do not combine a registry move with unrelated feature
  work.
- Each shared artifact needs an executable check, not reviewer memory.
- Generated files must carry either a source hash or a check command.
- Runtime output remains material unless an executed verifier records it.
- Rollback must be simple: adapters can keep their local implementation while
  the shared artifact is corrected.

## Capability registry

The MCP server keeps host, provider, execution, and lifecycle capability metadata in
`mcp-server/src/capability-registry.js`. The registry is a contract boundary, not a
router. Listing a candidate adapter does not make it a supported public input.

Registry rules:

- Existing public enums stay stable until this design document changes.
- Candidate adapters can be tracked before `classify_task`, `host_model_switch`, or
  `fanout_start` accept them.
- A `true` capability means Mythify has a documented or locally probed path for that
  adapter. Unknown capabilities default to `false`.
- Runtime tools must still verify adapter availability before claiming that anything
  was applied.
- Generated docs, schemas, and fixtures may be derived from the registry only after a
  drift test protects the generated output.
- The first generated registry-backed document is `docs/adapter-candidates.md`,
  built from `mcp-server/src/capability-registry.js` by
  `node scripts/build_registry_docs.mjs`.
- The generated adapter document is informational. It must not become a public
  input schema, router, or behavior switch.
- The drift gate is byte-for-byte equality between the generated output and
  `docs/adapter-candidates.md`. The Node registry test also compares the
  generated text against the committed file.

Adapter kinds:

- `host`: host CLI or app-backed coding agents used for bounded worker output.
- `desktop_agent`: local desktop agent surface without a stable automation
  contract.
- `model_provider`: local OpenAI-compatible model backends for reader and
  triage roles.
- `api_provider`: hosted model APIs that need explicit billing and data
  movement posture before execution.
- `custom_adapter`: user-defined command or future HTTP adapters.
- `execution_substrate`: runtime that executes remote or local jobs and returns logs,
  files, or artifacts.
- `agent_lifecycle`: scaffold, test, deploy, or observe tools for agents.

Stable adapter interface v1:

All candidates can be normalized to the same metadata fields: `id`, `kind`,
`status`, `locality`, `openai_compatible`, `probe_supported`,
`run_supported`, `execution_enabled`, `writes_state`, `evidence_status`,
`material_not_evidence`, `billing`, `roles`, and `guardrails`.
This interface is descriptive only. It does not add hidden provider fallback,
does not turn metadata-only candidates into runnable adapters, and does not
grant permission for workers to write state.

The current public host platforms remain `auto`, `unknown`, `codex-desktop`,
`codex-cli`, `claude-desktop`, `claude-code`, `cursor-desktop`, and
`cursor-agent`. Adapter profiles such as generic OpenAI-compatible local
providers, Ollama, LM Studio, llama.cpp, vLLM, Kimi Code, OpenCode,
Antigravity, Kimi Work, OpenCode Desktop, Google Colab CLI, Google Agents CLI,
and Google ADK CLI live in the registry instead of the host platform enum.
Kimi Work and OpenCode Desktop are metadata-only `desktop_agent` candidates
until a documented or locally probeable automation surface exists. New
candidates must enter the registry first, then earn public schema support in a
separate verified slice.

## Operation registry

Shared operation metadata lives in `protocol/operation-registry.json`. This is a
runtime contract for duplicated operation facts that have already caused drift,
not a broad router or code generation layer.

Prototype scope:

- The first registered surface is `memory`.
- The registry owns memory categories, the default category, the memory state
  filename, and the no-target `memory_clear` refusal strings for CLI and MCP.
- The Python CLI and Node MCP server both load the registry at runtime.
- Tests compare runtime behavior against the registry before any generated docs
  or schemas are allowed to depend on it.

Keep new surfaces out of the registry until duplication has been observed and a
focused drift test proves the shared contract reduces maintenance risk.

## Surface manifest

Shared public surface metadata lives in `protocol/surface-manifest.json`. The
manifest owns duplicated metadata that is easy to drift across prose, tests, and
runtime registrations.

Current scope:

- Top-level CLI command names and command count.
- MCP core tool names, fanout tool names, and the 37 core plus 3 fanout count
  split.
- Front door, workflow, advanced, and labs tier membership for the CLI and MCP
  surfaces.

Rules:

- The manifest is not a runtime router. It does not generate tool handlers,
  command parsers, schemas, or behavior.
- `mcp-server/src/surface-manifest.js` exposes the manifest to Node tests.
- Python tests may read the JSON file directly.
- `scripts/check_surface_manifest.mjs` verifies manifest counts, tier
  partitions, runtime MCP registrations, public doc count phrases, documented
  tool names, and CLI `--help` output.
- CI hygiene runs the check so README, design, protocol notes, tests, and
  runtime registrations cannot quietly disagree about public surface metadata.
- Add a new surface only after drift has been observed and the check can prove
  the shared metadata reduces maintenance risk.

## Classification rules manifest

Shared deterministic task-classification keyword rules live in
`protocol/classification-rules.json`. The Python CLI and Node MCP server both
load this manifest at startup, so keyword additions such as review wording only
need one data edit.

Rules:

- The manifest owns keyword matching data only. It does not own classification
  scoring, risk policy, ceremony policy, model policy, fanout policy, or
  verification hints.
- Keep runtime behavior in the native CLI and MCP adapters unless duplication
  has already caused drift.
- Python and Node tests must cover any newly added terms that affect public
  classification behavior.
- `mcp-server/protocol/classification-rules.json`,
  `mcp-server/protocol/operation-registry.json`,
  `mcp-server/protocol/workflow-router.json`, and
  `mcp-server/protocol/surface-manifest.json` are package-local mirrors so the
  npm tarball can run without access to repository-root files. Run
  `node scripts/check_classification_rules_manifest.mjs`, the MCP smoke suite,
  and `node scripts/check_surface_manifest.mjs` to verify the mirrors.

## Workflow router manifest

Shared workflow route metadata lives in `protocol/workflow-router.json`. The
Python CLI `route` command and Node MCP `workflow_route` tool both load this
manifest at startup.

Rules:

- The manifest owns route ids, route priority metadata, prompt-packet mapping,
  and the public output field list.
- The manifest is not an execution engine. It does not run checks, mutate state,
  spawn workers, or complete tasks.
- Route behavior remains native in the CLI and MCP adapters: read active state,
  inspect the latest executed verification, classify the task, and return a
  structured route packet.
- Route output is material for the initiating host chat. It keeps the host as
  executor unless the user explicitly hands work elsewhere.

## Experience surface tiers

The router lets Mythify reduce the surface users have to think about without
removing compatibility for scripts, MCP hosts, or power users.

Default front door:

- CLI `route`, `report`, `verify run`, and `status`.
- MCP `workflow_route`, `work_report`, `verify_run`, and `workflow_status`.

Workflow primitives:

- `plan`, `outcome`, `campaign`, `research`, and `prompt` in the CLI.
- `plan_create`, `plan_add_step`, `plan_update_step`, `outcome_start`,
  `outcome_check`, `campaign_next_prompt`, and `prompt_packet` in MCP.

Advanced surfaces:

- Dashboards, history, background, readiness, timeline, phase, trace,
  memory, lessons, logs, fanout, reflections, summaries, and protocol checks.

Labs surfaces:

- Host-model state, provider probes, local model runs, host CLI workers,
  execution substrate probes/runs, and lifecycle probes. These surfaces are
  explicit, material-only, and adapter-facing. They should not be presented as
  the default product path until they can perform and confirm host actions.

Public help, docs, skills, and MCP tool descriptions should present the default
front door first, then workflow primitives, then advanced surfaces, then labs.
Primitive commands stay available, but broad or ambiguous prompts should route
through `route` or `workflow_route` before selecting a lower-level tool.

## Background task view

The background task view is a read-only orientation surface for durable long
running work:

- CLI command: `background [--recent N] [--json]`.
- MCP tool: `background_status`.
- State sources: `.mythify/outcomes/*/goal.json`,
  `.mythify/outcomes/*/iterations.jsonl`, and
  `.mythify/fanout/<job_id>/job.json`.
- Output: outcome counts, active outcome, recent outcome loops, fanout job
  counts, fanout task counts, recent fanout jobs, and each recent job's task
  statuses.
- Evidence boundary: the view reports recorded verifier iterations and durable
  task statuses. It must not infer progress from model confidence, host UI
  state, or whether an MCP server process is currently alive.
- Mutation boundary: the normal path must not create, edit, interrupt, stop, or
  otherwise mutate outcome or fanout records. It is an orientation view, not a
  control surface.

## Outcome progress view

The outcome progress view is a read-only progress surface for verifier-backed
outcome loops:

- CLI command: `progress [--recent N] [--json]`.
- MCP tool: `outcome_progress`.
- State sources: `.mythify/outcomes/*/goal.json`,
  `.mythify/outcomes/*/iterations.jsonl`, and the active outcome pointer.
- Output: outcome counts, active outcome, recent outcome loops, iteration
  budget, remaining iterations, last verifier exit, last verifier verdict,
  metric exit and score when present, and the recorded next action.
- Evidence boundary: the view reports recorded `outcome check` and
  `outcome_check` verifier iterations. Notes, model prose, and host UI state
  must not become verification evidence by appearing in the progress view.
- Mutation boundary: the normal path must not run checks, make attempts, stop
  loops, change active outcomes, or edit goal or iteration records. It is a
  progress display, not a control surface.

## Release readiness view

The release readiness view is a read-only release-review surface:

- CLI command: `readiness [--json]`.
- MCP tool: `release_readiness`.
- State sources: `.mythify/verifications.jsonl`, `roadmap.md`, and read-only
  git status for the project root.
- Output: release-review status, required gate rows, each gate's latest
  matching executed verifier record, source file references, project git
  status, and active roadmap slice.
- Required gates: Python suite, Node MCP suite, surface manifest check,
  generated registry docs check, protocol variants check, generated variants
  idempotence, whitespace check, forbidden dash scan, and emoji scan.
- Evidence boundary: the view only summarizes recorded executed verifier
  records. Missing rows stay missing, failed rows stay failed, and attested
  claims do not satisfy release gates.
- Mutation boundary: the normal path must not append, edit, compact, or
  remove Mythify state, must not rerun release gates, and must not tag,
  publish, push, or declare the release safe.

## Verification history

The verification history is a read-only evidence surface for recorded checks:

- CLI command: `history [--recent N] [--json]`.
- MCP tool: `verification_history`.
- State source: `.mythify/verifications.jsonl`.
- Record kinds: executed records from `verify run` and `verify_run`, plus
  attested records from `verify claim` and `verify_claim`.
- Output: total counts, executed passed count, executed failed count, attested
  count, recent records, verdicts, command or evidence fields, exit code,
  duration, output-tail byte counts, and plan or step context when present.
- Evidence boundary: executed records are machine-checked evidence. Attested
  records remain self-reported and must not be upgraded by appearing in the
  history view.
- Mutation boundary: the normal path must not append, compact, edit, remove,
  rerun, or reclassify verification records. It is a history view, not a
  verifier or log maintenance command.

## Work report

The work report is a chat-ready progress surface for visible live narration:

- CLI command: `report [--since last|start] [--format chat|json] [--recent N] [--cursor NAME] [--peek] [--mark]`.
- MCP tool: `work_report`.
- State sources: active and inactive plan files, `.mythify/verifications.jsonl`,
  `.mythify/reflections.jsonl`, and `.mythify/reports/<cursor>.json`.
- Output: an `Attention` section for failed verification, failed step, failure
  reflection, and attested warning events, followed by chronological plan
  creation, step updates, verification verdicts, and reflection events. `chat`
  output is intended to be pasted or summarized in the host conversation.
- Cursor behavior: by default the selected cursor advances to the newest known
  event so later `--since last` reports show only new events. `--peek` leaves
  the cursor unchanged. `--mark` advances the cursor to the newest known event
  without showing old events and is incompatible with `--since`.
- Evidence boundary: the report does not rerun checks, does not upgrade
  attested claims, and does not prove work beyond recorded Mythify evidence.
- Mutation boundary: the only normal mutation is the cursor file. It must not
  edit plans, verifications, reflections, memory, lessons, outcomes, fanout
  jobs, git state, or project files.

## Fanout worker timeline

The fanout worker timeline is a read-only orientation surface for delegated
worker history:

- CLI command: `timeline [--recent N] [--json]`.
- MCP tool: `fanout_timeline`.
- State sources: `.mythify/fanout/<job_id>/job.json`, including job `created`,
  job `last_updated`, task `started_at`, task `finished_at`, task
  `duration_seconds`, task `status`, task `error`, and output metadata.
- Output: fanout job counts, fanout task counts, recent job records, and
  chronological events for job creation, task starts, task finishes, failures,
  interruptions, and pending tasks.
- Evidence boundary: the timeline reports durable worker state. Worker output
  remains material, not verification evidence, until the orchestrator verifies
  merged work with an executed check.
- Mutation boundary: the normal path must not create, edit, interrupt, retry,
  stop, or otherwise mutate fanout jobs or worker tasks. It is a timeline, not
  a process-control surface.

## Phase view

The phase view is a read-only orientation surface for the current workflow
shape:

- CLI command: `phase [--recent N] [--json]`.
- MCP tool: `phase_status`.
- Phase buckets: Understand, Design, Build, Judge, and Verify.
- State sources: the active plan in `.mythify/plans/`, recent verification
  records, recent reflection records, durable outcome loop state, durable
  fanout job state, memory counts, and lesson counts.
- Output: active plan, goal, each phase's plan steps, each phase's derived
  step status, evidence count summaries, and the next recorded plan action for
  that phase.
- Evidence boundary: the view summarizes durable state and executed evidence
  counts. It must not grade confidence, infer success from prose, or replace
  `verify run`, `verify_run`, or `outcome_check`.
- Mutation boundary: the normal path must not create, edit, complete, fail,
  archive, interrupt, stop, or otherwise mutate any plan, verification,
  reflection, outcome, fanout, memory, or lesson state.

## State model (shared contract)

### State directory resolution

1. If the `MYTHIFY_DIR` environment variable is set, use that path directly as the
   state directory. Create it (and subdirectories) on demand.
2. Otherwise walk from the current working directory upward; the first directory
   containing a `.mythify/` folder wins, and that `.mythify/` is the state directory.
3. Otherwise:
   - Python CLI: `init` creates `./.mythify` and adds `.mythify/` to the
     project `.gitignore` for the default in-repo state directory. Every other command prints
     `[FAIL] No .mythify workspace found. Run: python3 scripts/mythify.py init`
     and exits 1.
   - MCP server: lazily creates `<cwd>/.mythify` on first write. Reads with no state
     respond gracefully (for example "No memory entries yet."), never with a crash.

Global lessons live in `~/.mythify/lessons/` and are independent of project state.
Both implementations must resolve the home directory through the `HOME` environment
variable when it is set (Python `Path.home()`, Node `os.homedir()` both already do).

### Layout of a state directory

```
.mythify/
|-- memory.json
|-- host-model.json              optional recorded host chat model request
|-- plans/
|   |-- active                   text file containing the slug of the active plan
|   |-- <slug>.json
|   `-- archive/
|       `-- <slug>.json
|-- lessons/
|   `-- <slug>.json
|-- logs/
|   `-- archive/
|       `-- <log-stem>-<YYYYMMDDHHMMSS>.jsonl
|-- verifications.jsonl
`-- reflections.jsonl
```

### File formats (exact field names; both implementations identical)

memory.json:

```json
{
  "entries": [
    {"key": "str", "value": "str", "category": "fact|decision|discovery|state", "timestamp": "ISO-8601"}
  ],
  "metadata": {"created": "ISO-8601", "last_updated": "ISO-8601", "total_entries": 0}
}
```

Keys are unique; `set` on an existing key overwrites the entry.

host-model.json:

```json
{
  "platform": "codex-desktop|codex-cli|claude-desktop|claude-code|cursor-desktop|cursor-agent|unknown",
  "requested_platform": "auto|unknown|codex-desktop|codex-cli|claude-desktop|claude-code|cursor-desktop|cursor-agent",
  "target_model": "str",
  "current_model": "str",
  "target_model_tier": "unknown|small|fast|standard|strong|frontier",
  "thinking": "auto|low|medium|high|xhigh|max",
  "speed": "auto|standard|fast",
  "reason": "str",
  "status": "recorded_requires_host_action",
  "control": "host_selected",
  "can_apply_current_chat": false,
  "host_capability": {
    "kind": "host",
    "status": "supported|unknown|unsupported",
    "can_switch_current_thread": false,
    "can_set_new_thread_model": true,
    "can_set_worker_model": true,
    "can_set_thinking": true,
    "can_list_models": false,
    "can_confirm_current_model": false
  },
  "switch_result": {
    "status": "manual",
    "requested_model": "str",
    "requested_thinking": "auto|low|medium|high|xhigh|max",
    "requested_speed": "auto|standard|fast",
    "current_model": "str",
    "current_thinking": "",
    "current_chat_supported": false,
    "current_chat_confirmed": false,
    "manual_action_required": true,
    "applied_by": "none",
    "reason": "host_current_chat_unconfirmed"
  },
  "host_confirmation": {
    "requested_model": "str",
    "user_reported_current_model": "str",
    "user_reported_current_thinking": "",
    "current_model_confirmed": false,
    "confirmed_current_model": "",
    "confirmed_current_thinking": "",
    "confirmation_status": "unsupported|unconfirmed|confirmed|blocked",
    "confirmation_source": "none|host_adapter",
    "confirmation_checked_at": "ISO-8601",
    "confirmed_at": "",
    "unsupported_reason": "host_capability_cannot_confirm_current_model"
  },
  "adapter_proof_scan": {
    "status": "metadata_only",
    "platform": "codex-desktop",
    "proof_source": "host_capability_registry",
    "checked_at": "ISO-8601",
    "host_state_mutated": false,
    "writes_state": false,
    "verification_recorded": false,
    "material_not_evidence": true,
    "guardrail": "current_chat_apply_or_confirm_requires_executed_host_evidence",
    "paths": {
      "current_chat_model_apply": {"status": "supported|unsupported|unknown"},
      "current_chat_model_confirm": {"status": "supported|unsupported|unknown"},
      "new_thread_model_apply": {"status": "supported|unsupported|unknown"},
      "worker_model_apply": {"status": "supported|unsupported|unknown"},
      "thinking_apply": {"status": "supported|unsupported|unknown"}
    }
  },
  "updated": "ISO-8601",
  "host_actions": ["str"]
}
```

`host-model.json` is optional. Explicit `session_model` and
`MYTHIFY_SESSION_MODEL` beat it; otherwise it supplies the default session model
for `classify_task` and `fanout_start`.

Host model switch status rules:

- `switch_result.status` is `manual` when Mythify recorded a target model but no
  host adapter applied or confirmed the current chat.
- `switch_result.status` is `requested` only when a future host adapter accepts a
  request but cannot yet confirm the current chat.
- `switch_result.status` is `applied` only when a host adapter confirms the
  current chat model or thinking changed.
- `switch_result.status` is `blocked` only when an adapter proves the requested
  change cannot be requested or applied.
- `current_chat_confirmed` must stay `false` unless `host_capability` has
  `can_confirm_current_model: true` and the host returns positive evidence.
- `host_confirmation.current_model_confirmed` must stay `false` unless a host
  adapter returns positive current-chat evidence. User-supplied
  `current_model` is recorded as `user_reported_current_model`, not proof.
- `host_confirmation.confirmation_status` is `unsupported` when the capability
  registry cannot confirm the current model, `unconfirmed` when a future
  adapter can check but has not produced evidence, `confirmed` only after
  positive host evidence, and `blocked` only after adapter evidence proves
  confirmation cannot be performed.
- `adapter_proof_scan` is a non-mutating metadata scan. Its path statuses are
  `supported`, `unsupported`, or `unknown`, and `host_state_mutated` must stay
  `false`. A supported path means the registry or probe found a possible path;
  it is not proof that the host changed.
- CLI and MCP status output must expose `host_capability`, `can_apply_current_chat`,
  `switch_result`, `host_confirmation`, and `adapter_proof_scan` so callers can
  distinguish desired state, user-reported state, host-confirmed state, and
  future apply or confirm paths.
- `docs/host-apply-confirm-proof-watchlist.md` names the proof gates for
  current-chat apply, current-chat confirm, worker model override, and thinking
  override before any host mutation path can be enabled.

outcomes/&lt;slug&gt;/goal.json:

```json
{
  "id": "slug",
  "goal": "str",
  "success_criteria": "str",
  "verify_command": "str",
  "metric_command": "str",
  "max_iterations": 3,
  "iteration_count": 0,
  "allowed_paths": ["str"],
  "visibility": "auto|quiet|summary|verbose|threaded",
  "status": "active|succeeded|failed|stopped",
  "created": "ISO-8601",
  "updated": "ISO-8601",
  "last_verified": true,
  "best_metric_score": 42.5,
  "stop_reason": "str or null"
}
```

`allowed_paths` are advisory host-edit hints recorded for the supervising host;
they are not enforced as a sandbox.

outcomes/&lt;slug&gt;/iterations.jsonl, one JSON object per verifier attempt:

```json
{
  "iteration": 1,
  "timestamp": "ISO-8601",
  "notes": "str",
  "verify": {"command": "str", "exit_code": 0, "duration_seconds": 0.03, "stdout_tail": "str", "stderr_tail": "str", "verified": true},
  "metric": {"command": "str", "exit_code": 0, "duration_seconds": 0.03, "stdout_tail": "str", "stderr_tail": "str", "verified": true, "score": 42.5},
  "verified": true,
  "status_after": "succeeded|active|failed",
  "next_action": "str"
}
```

`outcomes/active` stores the active outcome slug. Outcome loops are supervised:
the host chat acts between `outcome check` calls, while Mythify records the
verifier result, optional metric, iteration budget, and next action. A passing
check also appends an executed verification record tagged with the outcome slug
and iteration number.

plans/&lt;slug&gt;.json:

```json
{
  "name": "slug",
  "goal": "str",
  "steps": [
    {"id": 1, "title": "str", "success_criteria": "str", "status": "pending|in_progress|completed|failed|skipped", "result": null, "updated_at": "ISO-8601 (present once updated)"}
  ],
  "created": "ISO-8601",
  "last_updated": "ISO-8601"
}
```

Step ids are 1-based integers assigned in order. `success_criteria` defaults to an
empty string. `result` is a string or null.

lessons/&lt;slug&gt;.json:

```json
{"title": "str", "detail": "str", "tags": ["str"], "created": "ISO-8601"}
```

Lesson filename: `slugify(title)` truncated to 50 chars, then `-YYYYMMDDHHMMSS`,
then `.json`. This makes same-title lessons collision-free.

verifications.jsonl, one JSON object per line. Two kinds:

```json
{"kind": "executed", "claim": "str or null", "command": "str", "exit_code": 0, "duration_seconds": 0.03, "stdout_tail": "str", "stderr_tail": "str", "verified": true, "timestamp": "ISO-8601", "plan": "slug or null", "step_id": 1, "step_title": "str or null", "step_status": "in_progress or null"}
{"kind": "attested", "claim": "str", "evidence": "str", "verified": null, "timestamp": "ISO-8601", "plan": "slug or null", "step_id": 1, "step_title": "str or null", "step_status": "in_progress or null"}
```

`verified` is a boolean only for executed verifications (true when exit_code == 0).
Attested entries always have `verified: null`: a self-report is never marked verified.
On timeout, record `exit_code: -1`, `verified: false`, and append
`"(timed out after N seconds)"` to `stderr_tail`. Output tails keep the last 4000
characters of each stream.

Every new verification record also captures active step context. If an active
plan exists and exactly the first currently `in_progress` step can be found,
record `plan`, `step_id`, `step_title`, and `step_status`. If no active plan or
in-progress step exists, write those fields with `null`. Readers must tolerate
older verification records that do not contain these fields.

reflections.jsonl, one JSON object per line:

```json
{"action": "str", "outcome": "success|partial|failure", "observation": "str", "root_cause": "str or null", "next": "str", "lesson": "str or null", "timestamp": "ISO-8601"}
```

logs/archive/*.jsonl:

- Raw snapshots created by `logs compact`.
- Names are `<log-stem>-<YYYYMMDDHHMMSS>.jsonl`, with a numeric suffix on
  collision.
- The first compacted logs are the top-level `verifications.jsonl` and
  `reflections.jsonl` files. Outcome iteration logs stay in their outcome
  directories.
- Archives preserve the original bytes of the active log before compaction,
  including unparseable lines. The compacted active log keeps only the most
  recent valid JSONL records.

### Durability rules

- All JSON file writes are atomic: write to a temp file in the same directory, then
  rename over the target (Python `os.replace`, Node `fs.renameSync`).
- Corrupt JSON on read: rename the bad file to `<filename>.corrupt-<YYYYMMDDHHMMSS>`,
  print `[WARN]` to stderr, and continue with a fresh default. Never crash.
- jsonl logs are plain appends.
- `logs compact [--keep N] [--dry-run] [--json]` is maintenance, not
  verification evidence. Default `--keep` is 1000. When a target log has more
  than `N` valid records, write a raw archive first, then atomically replace
  the active log with the most recent `N` valid records. `--dry-run` reports
  candidates and counts without writing files.

### Slugs

`slugify(text)`: lowercase, replace runs of non-alphanumeric characters with `-`,
strip leading and trailing `-`, truncate to 40 characters. For plan slugs, on
collision with an existing plan file append `-2`, `-3`, and so on.

## Output conventions (both implementations)

- Event markers: `[OK]`, `[FAIL]`, `[WARN]`.
- Step status icons: pending `[ ]`, in_progress `[>]`, completed `[x]`,
  failed `[!]`, skipped `[~]`.
- Verification verdict lines:
  - `[OK] VERIFIED: <claim or command> (exit 0, 0.03s)`
  - `[FAIL] UNVERIFIED: <claim or command> (exit 2, 0.10s)` followed by
    `--- stdout (tail) ---` and `--- stderr (tail) ---` blocks when non-empty.
  - `[WARN] ATTESTED: <claim> (self-reported, not machine-checked; prefer verify run)`
- ASCII only in all program output.

## CLI: scripts/mythify.py

Single file, Python 3.9+, standard library only (argparse, json, os, sys, subprocess,
datetime, pathlib, tempfile). Subcommand grammar:

| Command | Behavior | Exit code |
| :--- | :--- | :--- |
| `init` | Create `./.mythify` with subdirectories and empty memory.json, and add `.mythify/` to the project `.gitignore` for the default in-repo state directory. If already inside a workspace, print `[WARN]` and exit 0. | 0 |
| `protocol check [PATH ...] [--json]` | Verify copied protocol files match the CLI's embedded source protocol hash. With no paths, check source protocol when present and local `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` files. | 0 if every checked file matches; 1 on missing metadata or drift |
| `status` | Orientation: active plan with step icons, next pending step and its criteria, one-line counts (memory, lessons, verifications, reflections). | 0; 1 if no workspace |
| `dashboard [--recent N] [--json]` | Read-only workflow dashboard: active plan, current and next step, active outcome, memory and lesson counts, verification totals, recent verification records, and recent reflections. It does not mutate state or report model confidence. | 0; 1 if no workspace |
| `history [--recent N] [--json]` | Read-only verification history: executed and attested records, verdicts, commands, exit codes, duration, and plan or step context from durable state. It does not mutate state, rerun checks, or upgrade attested claims. | 0; 1 if no workspace |
| `report [--since last\|start] [--format chat\|json] [--recent N] [--cursor NAME] [--peek] [--mark]` | Chat-ready live work report over durable plan, step, verification, and reflection events, with an `Attention` section for failed checks, failed steps, failure reflections, and attested warnings. By default it advances a cursor so repeated calls show only new events; `--peek` leaves the cursor unchanged; `--mark` advances the cursor to the latest event without showing old events and cannot be combined with `--since`. | 0; 1 if no workspace, invalid recent value, or incompatible flags |
| `route TASK [--json] [--triage never\|auto\|always] [--platform P] [--effort E] [--speed S] [--session-model M] [--spawn-ceiling C] [--reviewer-strength R]` | Read-only workflow router. It classifies the task, inspects durable state and the latest executed verification, then returns a route, reason, next command, prompt packet, verification strategy, chat policy, pause rules, expected state writes, and evidence. It must not mutate state or move execution out of the initiating host unless the user explicitly asks. | 0; 1 if no workspace |
| `prompt KIND [NAME] [--goal TEXT] [--verify COMMAND] [--json]` | Render a read-only workflow prompt packet. Kinds are `research`, `analysis`, `failure`, `handoff`, `review`, `campaign`, and `next`; packet output is steering material for the host, not verification evidence. `next` selects failure recovery only when the latest executed check is red, then campaign, research, handoff, or analysis based on active state. | 0; 1 if no workspace or named state is missing |
| `background [--recent N] [--json]` | Read-only background task view: outcome loops, fanout jobs, task counts, current statuses, and next actions from durable state. It does not mutate state or report model confidence as progress. | 0; 1 if no workspace |
| `progress [--recent N] [--json]` | Read-only outcome loop progress: active and recent outcomes, iteration budget, verifier exit details, metric score when present, and next action from durable state. It does not mutate state, run checks, stop loops, or treat notes as verification. | 0; 1 if no workspace |
| `readiness [--json]` | Read-only release readiness: recorded verification gates, project git state, roadmap state, and release-review status without rerunning gates or declaring the release safe. | 0; 1 if no workspace |
| `timeline [--recent N] [--json]` | Read-only fanout worker timeline: recent fanout jobs, task start and finish events, duration, status, errors, and output metadata from durable state. It does not mutate state or report worker output as verification evidence. | 0; 1 if no workspace |
| `phase [--recent N] [--json]` | Read-only phase view: active plan steps grouped into Understand, Design, Build, Judge, and Verify, with supporting evidence counts from durable state. It does not mutate state or report model confidence as progress. | 0; 1 if no workspace |
| `outcome start GOAL --success TEXT --verify COMMAND [--metric COMMAND] [--max-iterations N] [--allowed-paths CSV] [--visibility MODE] [--name NAME] [--json]` | Start a supervised outcome loop, set it active, and record the verifier, optional metric, allowed path hints, visibility policy, and iteration budget. | 0; 1 if no workspace or invalid budget |
| `outcome check [NAME] [--notes TEXT] [--timeout N] [--json]` | Run the verifier and optional metric for the active or named outcome, append an iteration record, append executed verification evidence, and return the next action. | 0 if verified, 2 if still unmet or failed, 1 if not found |
| `outcome status [NAME] [--json]` | Show outcome status, verifier, metric, iteration budget, and latest next action. | 0; 1 if not found |
| `outcome results [NAME] [--json]` | Show every recorded verifier iteration plus final state. | 0 if succeeded, 2 otherwise, 1 if not found |
| `outcome stop [NAME] --reason TEXT [--json]` | Mark an active or named outcome stopped and clear the active pointer when it matches. | 0; 1 if not found |
| `plan create GOAL [--steps JSON] [--name NAME]` | Create plan, set it active. `--steps` is a JSON array of `{"title": str, "success_criteria": str (optional)}`. Without `--steps`, create an empty plan and suggest `plan add-step`. Invalid JSON: `[FAIL]`, exit 1. | 0 |
| `plan add-step TITLE [--criteria TEXT] [--plan NAME]` | Append a step (id = max + 1) to the named or active plan. | 0; 1 if plan not found |
| `plan list` | List plans with active marker and per-plan progress, plus archived count. | 0 |
| `plan show [NAME]` | Full detail of the named or active plan. | 0; 1 if not found |
| `plan switch NAME` | Set the active plan pointer. | 0; 1 if not found |
| `plan archive [NAME]` | Move plan file to `plans/archive/`; clear the active pointer if it pointed there. On filename conflict in archive, append a timestamp. | 0; 1 if plan not found |
| `step ID STATUS [RESULT] [--plan NAME]` | Update step status. STATUS must be one of the five enum values, otherwise `[FAIL]`, exit 1. `completed` and `failed` REQUIRE the RESULT argument (evidence or failure description); without it print `[FAIL] Evidence required: pass a RESULT describing what proves this status.` and exit 1. By default, `completed` ALSO requires a recorded passing executed verification (see "Verified-step gate" below), otherwise print `[FAIL] Verified evidence required: strict evidence mode is enabled by default, but no passing 'verify run' was recorded since this step started. Run 'verify run' with a passing check first, or set MYTHIFY_REQUIRE_VERIFIED_STEP=0 to use legacy prose-only completion.` and exit 1 without modifying the plan. Set `MYTHIFY_REQUIRE_VERIFIED_STEP=0` to opt out. After updating, print the next pending step. | 0 |
| `memory set KEY VALUE [--category C]` | Category one of fact, decision, discovery, state; default fact. | 0 |
| `memory get [QUERY] [--category C]` | Case-insensitive substring match over keys and values; optional category filter. | 0 |
| `memory clear [KEY] [--all]` | KEY removes one entry. `--all` clears everything. Neither: `[FAIL]` explaining the guard, exit 1. | 0 |
| `lesson add TITLE DETAIL [--tags a,b] [--global]` | Record a lesson in the project store, or the global store with `--global`. | 0 |
| `lesson list [--tag TAG] [--scope project\|global\|all]` | Default scope all; label each lesson `(project)` or `(global)`; `--tag` filters. | 0 |
| `logs compact [--keep N] [--dry-run] [--json]` | Archive raw top-level verification and reflection logs, then keep the most recent valid records in active logs. Default keep is 1000. `--dry-run` writes nothing. | 0; 1 if keep is invalid |
| `verify run COMMAND [--claim TEXT] [--timeout N]` | Execute COMMAND through the shell, capture exit code, duration, and output tails, append an executed record, print the verdict. Default timeout 300 seconds. If `MYTHIFY_DISABLE_RUN=1`, refuse: execute nothing, record nothing, print `[FAIL] verify run is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was executed and nothing was recorded. Unset it to enable execution, or use verify claim to record a self-reported attestation.` and exit 2 (the unverified code, so callers branching on verify run treat a disabled run as not verified). | 0 if verified, 2 if unverified or disabled |
| `verify claim CLAIM EVIDENCE` | Append an attested record and print the `[WARN] ATTESTED` line. | 0 |
| `reflect [JSON]` or `reflect --action A --outcome O --observation OBS --next N [--root-cause R] [--lesson L]` | Record a structured reflection. Required keys: action, outcome (enum success, partial, failure), observation, next. A provided lesson is auto-recorded as a project lesson tagged `auto-reflected`. JSON positional takes precedence over flags. Missing keys or bad outcome: `[FAIL]`, exit 1. | 0 |
| `classify TASK [--json] [--triage never\|auto\|always] [--platform auto\|codex-desktop\|claude-desktop\|cursor-desktop] [--effort auto\|low\|medium\|high] [--speed auto\|standard\|fast] [--session-model MODEL] [--spawn-ceiling auto\|lower_only\|same_or_lower\|allow_stronger] [--reviewer-strength auto\|same_or_lower\|allow_stronger]` | Classify a task before planning. Returns task type, risk, ambiguity, ceremony level, execution profile, verification strategy, fanout recommendation, fast model triage fit, model policy, task-based host recommendation, signals, and next action. `--triage auto` runs one fast local model only when the gate is recommended or required. Does not require `.mythify` state unless the selected local model command does. | 0 |
| `summary` | Full session report: plans and progress, memory count, project and global lesson counts, verification stats (executed passed, executed failed, attested count), reflection count. | 0 |

Implementation notes:

- `verify run` uses `subprocess.run(command, shell=True, capture_output=True,
  text=True, timeout=N)`. Catch `TimeoutExpired` per the timeout rule above.
- All commands other than `init` and `classify` require a resolvable state
  directory (or `MYTHIFY_DIR`, which is created on demand).
- `--help` output for the top level and each subcommand must be accurate.

## MCP server: mcp-server/

Node 18+, ESM (`"type": "module"`). Dependencies: `@modelcontextprotocol/sdk`
(current 1.x) and `zod` (4.x). package.json: name `mythify-mcp`, version `3.6.15`,
scripts `{"start": "node src/index.js", "test": "node --test test/*.test.js"}`
(the glob form, because modern Node treats a bare directory argument to --test as
a literal file and fails), engines node >= 18. Use the registration API that the
installed SDK version supports (prefer `registerTool`); verify against the
installed package, not from memory.

Exactly 40 tools: the 37 core tools below plus the 3 fanout tools defined in the
"Fanout: parallel delegation" section. Tool descriptions must state what the tool
does AND when to use it, since descriptions drive tool selection.

| Tool | Input schema | Behavior |
| :--- | :--- | :--- |
| `classify_task` | `{task: string, format?: enum(text, json), triage?: enum(never, auto, always), triage_engine?: enum(claude-cli, codex-cli, cursor-agent, command), triage_model?: string, triage_timeout_seconds?: number, platform?: enum(auto, unknown, codex-desktop, codex-cli, claude-desktop, claude-code, cursor-desktop, cursor-agent), effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast), session_model?: string, spawn_ceiling?: enum(auto, lower_only, same_or_lower, allow_stronger), reviewer_strength?: enum(auto, same_or_lower, allow_stronger)}` | Classify a task before planning. Returns task type, risk, ambiguity, ceremony level, execution profile, verification strategy, fanout recommendation, fast model triage fit, model policy, task-based host recommendation, signals, and next action. With `triage: auto`, run one fast local model only when the deterministic gate recommends it. |
| `host_model_switch` | `{action?: enum(switch, status, clear), platform?: enum(auto, unknown, codex-desktop, codex-cli, claude-desktop, claude-code, cursor-desktop, cursor-agent), target_model?: string, current_model?: string, thinking?: enum(auto, low, medium, high, xhigh, max), speed?: enum(auto, standard, fast), reason?: string, format?: enum(text, json)}` | Record, show, or clear a requested host chat model switch. `switch` writes `.mythify/host-model.json`, returns platform-specific switch guidance, registry-backed `host_capability`, `switch_result`, `host_confirmation`, and `adapter_proof_scan`, and makes later `classify_task` and `fanout_start` calls use the recorded target as the session model when no explicit or env session model is supplied. It does not claim to mutate or confirm the current host chat unless a future host integration exposes that capability and confirms the result. |
| `provider_probe` | `{provider?: enum(generic-openai-compatible, ollama, lm-studio, llama-cpp, vllm), base_url?: string, model?: string, check?: enum(models, chat, both), api_key_env?: string, timeout_seconds?: number, prompt?: string, format?: enum(text, json)}` | Probe an OpenAI-compatible provider by calling `/v1/models` and, when requested, `/v1/chat/completions`. Generic defaults: `MYTHIFY_OPENAI_COMPAT_BASE_URL`, `MYTHIFY_OPENAI_COMPAT_MODEL`, and `MYTHIFY_OPENAI_COMPAT_API_KEY`. `provider: "ollama"` defaults to `MYTHIFY_OLLAMA_BASE_URL` or `http://localhost:11434/v1`; `provider: "lm-studio"` defaults to `MYTHIFY_LM_STUDIO_BASE_URL` or `http://localhost:1234/v1`; `provider: "llama-cpp"` defaults to `MYTHIFY_LLAMA_CPP_BASE_URL` or `http://localhost:8080/v1`; `provider: "vllm"` defaults to `MYTHIFY_VLLM_BASE_URL` or `http://localhost:8000/v1`. Local profiles use provider-specific model env vars and no auth header by default. Returns provider availability, model presence, chat response tail, and `material_not_evidence: true`. It does not write state, spawn workers, or count as verification evidence. |
| `local_model_run` | `{provider?: enum(generic-openai-compatible, ollama, lm-studio, llama-cpp, vllm), role?: enum(reader, triage), base_url?: string, model?: string, prompt: string, api_key_env?: string, timeout_seconds?: number, max_tokens?: number, format?: enum(text, json)}` | Run a role-limited prompt against a localhost OpenAI-compatible provider. Generic defaults: `MYTHIFY_OPENAI_COMPAT_BASE_URL`, `MYTHIFY_OPENAI_COMPAT_MODEL`, and `MYTHIFY_OPENAI_COMPAT_API_KEY`. `provider: "ollama"`, `provider: "lm-studio"`, `provider: "llama-cpp"`, and `provider: "vllm"` default to local profiles. The base URL must be `localhost`, `127.0.0.1`, `::1`, or `0.0.0.0`. Returns model output with `material_not_evidence: true`, `evidence_status: "model_output_not_verification"`, `writes_state: false`, and `verification_recorded: false`. It does not edit files, run commands, write state, or count model output as verification evidence. |
| `host_cli_probe` | `{host?: enum(kimi-code, opencode, antigravity), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Kimi Code, OpenCode, or Antigravity CLI availability by running only version and help commands. Defaults to `MYTHIFY_KIMI_BIN`, `MYTHIFY_OPENCODE_BIN`, or `MYTHIFY_ANTIGRAVITY_BIN`, then PATH and common install paths. Returns binary resolution, feature evidence, proof statuses for current-chat apply, current-chat confirm, worker model override, and thinking override, plus `material_not_evidence: true`. It does not execute a prompt, write state, spawn workers, or count as verification evidence. Antigravity MCP setup guidance lives in `docs/antigravity-mcp-setup.md`; the probe does not install or mutate MCP config. |
| `host_cli_run` | `{host?: enum(kimi-code, opencode, antigravity), bin?: string, prompt: string, cwd?: string, timeout_seconds?: number, model?: string, agent?: string, format?: enum(text, json)}` | Run a bounded non-interactive prompt through Kimi Code, OpenCode, or Antigravity. Kimi uses `kimi --print -p PROMPT --final-message-only`. OpenCode uses `opencode run --format json [--model MODEL] [--agent AGENT] PROMPT`. Antigravity uses `agy [--model MODEL] -p PROMPT`, requires explicit `cwd`, and never passes permission-bypass flags. Defaults to `MYTHIFY_KIMI_BIN`, `MYTHIFY_OPENCODE_BIN`, or `MYTHIFY_ANTIGRAVITY_BIN`, then PATH and common install paths. Returns stdout and stderr tails, timeout and exit metadata, `trust_policy`, `permission_policy`, `material_not_evidence: true`, `evidence_status: "worker_output_not_verification"`, `writes_state: false`, and `verification_recorded: false`. It does not edit files directly, write Mythify state, or count worker output as verification evidence; merged work must still be verified with `verify_run`. |
| `execution_probe` | `{adapter?: enum(google-colab-cli), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Google Colab CLI availability by running only version and help commands. Defaults to `MYTHIFY_COLAB_BIN`, then PATH and common install paths. Returns binary resolution, feature evidence, `non_billable: true`, `job_execution_enabled: false`, and `material_not_evidence: true`. It does not provision a runtime, request an accelerator, execute notebooks, upload data, write state, or count as verification evidence. |
| `execution_run` | `{adapter?: enum(google-colab-cli), bin?: string, cwd?: string, script_path: string, script_args?: string[], accelerator_type?: enum(cpu, gpu, tpu), accelerator?: enum(T4, L4, G4, H100, A100, v5e1, v6e1), billing_ack?: boolean, data_movement_ack?: boolean, cleanup_ack?: boolean, timeout_seconds?: number, format?: enum(text, json)}` | Run a guarded Google Colab CLI ephemeral job through `colab run`. Defaults to `MYTHIFY_COLAB_BIN`, then PATH and common install paths. It requires `billing_ack: true`, `data_movement_ack: true`, and `cleanup_ack: true` before invoking the CLI, resolves `script_path` locally, supports CPU by default or explicit GPU/TPU accelerator flags, never passes `--keep`, and returns stdout and stderr tails plus exit metadata. It writes no Mythify state and returns `material_not_evidence: true`, `evidence_status: "remote_output_not_verification"`, and `verification_recorded: false`; remote logs or artifacts must be consumed by a separate verifier before any completion claim is verified. |
| `lifecycle_probe` | `{adapter?: enum(google-agents-cli, google-adk-cli), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Google Agents CLI or ADK CLI availability by running only version, help, and eval-help commands. Defaults to `MYTHIFY_AGENTS_CLI_BIN` or `MYTHIFY_ADK_BIN`, then PATH and common install paths. Returns binary resolution, feature evidence, `can_probe_eval: true`, `eval_execution_enabled: false`, `deployment_enabled: false`, `material_not_evidence: true`, and `lifecycle_lane_contract` with allowed probe commands, disabled lifecycle actions, future guarded actions, eval and deployment prerequisites, mutation policy, and material-only evidence status. It does not scaffold projects, run agents, execute evals, deploy, publish, mutate cloud resources, write project state, or count as verification evidence. |
| `workflow_status` | `{recent?: number, format?: enum(text, json)}` | Show a read-only dashboard of active plan, current step, next step, active outcome, memory and lesson counts, verification totals, recent verification records, and recent reflections. It must not mutate state and must not report model confidence as evidence. |
| `verification_history` | `{recent?: number, format?: enum(text, json)}` | Show a read-only history of executed and attested verification records, including verdict, command or evidence, exit code, duration, and plan or step context. It must not mutate state, rerun checks, or upgrade attested claims. |
| `work_report` | `{since?: enum(last, start), recent?: number, cursor?: string, peek?: boolean, mark?: boolean, format?: enum(chat, json)}` | Show a chat-ready live work report over durable plan, step, verification, and reflection events, with an `Attention` section for failed checks, failed steps, failure reflections, and attested warnings. By default it advances a cursor so repeated calls show only new events; `peek` leaves the cursor unchanged; `mark` advances the cursor to the latest event without showing old events and cannot be combined with `since`. |
| `background_status` | `{recent?: number, format?: enum(text, json)}` | Show a read-only background task view of durable outcome loops and fanout jobs, including task counts, statuses, and next actions. It must not mutate state and must not report model confidence as progress. |
| `outcome_progress` | `{recent?: number, format?: enum(text, json)}` | Show a read-only progress view of active and recent outcome loops, including iteration budget, verifier exit details, metric score when present, and next action. It must not run checks, make attempts, stop loops, or treat notes as verification. |
| `release_readiness` | `{format?: enum(text, json)}` | Show a read-only release readiness view from recorded verification gates, project git state, and roadmap state. It must not rerun gates, mutate state, tag, publish, push, or declare the release safe. |
| `fanout_timeline` | `{recent?: number, format?: enum(text, json)}` | Show a read-only timeline of fanout job creation, task starts, task finishes, duration, status, errors, and output metadata. It must not mutate state and must not treat worker output as verification evidence. |
| `phase_status` | `{recent?: number, format?: enum(text, json)}` | Show a read-only Understand, Design, Build, Judge, Verify phase view of active plan steps and durable evidence counts. It must not mutate state and must not report model confidence as progress. |
| `campaign_next_prompt` | `{name?: string, format?: enum(text, json)}` | Render a chat-ready next prompt for the active or named campaign's current task and phase. It must not mutate state, run checks, advance a phase, or treat prompt output as verification evidence. Hosts may display or inject the returned prompt, then the host agent does the work and advances the campaign with evidence. |
| `prompt_packet` | `{kind?: enum(research, analysis, failure, handoff, review, campaign, next), name?: string, goal?: string, verify_command?: string, format?: enum(text, json)}` | Render a chat-ready prompt packet for research to implementation, analysis to plan, failure recovery, handoff, review, campaign, or the next useful workflow move. It must not mutate state, run checks, advance work, or treat prompt output as verification evidence. Hosts may display or inject the returned prompt, then the host agent does the work and records evidence. |
| `workflow_route` | `{task: string, format?: enum(text, json), triage?: enum(never, auto, always), triage_engine?: enum(claude-cli, codex-cli, cursor-agent, command), triage_model?: string, triage_timeout_seconds?: number, platform?: enum(auto, unknown, codex-desktop, codex-cli, claude-desktop, claude-code, cursor-desktop, cursor-agent), effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast), session_model?: string, spawn_ceiling?: enum(auto, lower_only, same_or_lower, allow_stronger), reviewer_strength?: enum(auto, same_or_lower, allow_stronger)}` | Choose the next workflow route from prompt text and durable state. It returns `route`, `reason`, `next_command`, `prompt_packet`, `verification_strategy`, `chat_policy`, `pause_rules`, `state_writes`, and `evidence`. It must not mutate state, run checks, advance work, or move execution out of the initiating host unless the user explicitly asks. |
| `outcome_start` | `{goal: string, success: string, verify_command: string, metric_command?: string, max_iterations?: number, allowed_paths?: string[], visibility?: enum(auto, quiet, summary, verbose, threaded), name?: string, format?: enum(text, json)}` | Start a supervised outcome loop and set it active. `allowed_paths` are advisory host-edit hints, not a sandbox. The host agent acts between checks; Mythify records the verifier, metric, budget, and visibility policy. |
| `outcome_check` | `{name?: string, notes?: string, timeout_seconds?: number, format?: enum(text, json)}` | Run the verifier and optional metric for the active or named outcome, append an iteration, append executed verification evidence, and return success, retry, or budget-exhausted guidance. If `MYTHIFY_DISABLE_RUN=1`, refuse and record nothing. |
| `outcome_status` | `{name?: string, format?: enum(text, json)}` | Show active or named outcome status, verifier, metric, iteration budget, and next action. |
| `outcome_results` | `{name?: string, format?: enum(text, json)}` | Show all recorded verifier iterations and final state. |
| `outcome_stop` | `{name?: string, reason: string, format?: enum(text, json)}` | Mark an outcome stopped and clear the active pointer when it matches. |
| `memory_store` | `{key: string, value: string, category: enum(fact, decision, discovery, state) = "fact"}` | Upsert by key. Returns `[OK]` summary. |
| `memory_recall` | `{query?: string, category?: enum(fact, decision, discovery, state, all)}` | Substring search as in the CLI. |
| `memory_clear` | `{key?: string, confirm_clear_all?: boolean}` | With key: remove one. Without key and without `confirm_clear_all: true`: refuse with an explanation, do not clear. |
| `lesson_record` | `{title: string, detail: string, tags?: string[], scope: enum(project, global) = "project"}` | Write a lesson file per the format. |
| `lesson_recall` | `{tag?: string, scope: enum(project, global, all) = "all"}` | List lessons, labeled by scope. |
| `plan_create` | `{goal: string, name?: string, steps?: [{title: string, success_criteria?: string}]}` | Ids auto-assigned 1-based. Sets active plan. |
| `plan_add_step` | `{title: string, success_criteria?: string, plan?: string}` | Append to named or active plan. |
| `plan_update_step` | `{step_id: number, status: enum(pending, in_progress, completed, failed, skipped), result?: string, plan?: string}` | Enforce the evidence rule: `completed` or `failed` without `result` returns `[FAIL] Evidence required ...` and does NOT modify the plan. By default, `completed` also requires a recorded passing executed verification (see "Verified-step gate") and otherwise returns the same `[FAIL] Verified evidence required ...` text the CLI uses, without modifying the plan. Set `MYTHIFY_REQUIRE_VERIFIED_STEP=0` to opt out. On success, include the next pending step in the response. |
| `plan_status` | `{plan?: string}` | Goal, progress count, step list with icons. |
| `verify_run` | `{command: string, claim?: string, timeout_seconds?: number = 300}` | Execute through the shell, record an executed verification, return the verdict with output tails. If env `MYTHIFY_DISABLE_RUN=1`, refuse with an explanation and record nothing. |
| `verify_claim` | `{claim: string, evidence: string}` | Record an attested entry, return the `[WARN] ATTESTED` line. |
| `reflect` | `{action_taken: string, outcome: enum(success, partial, failure), observation: string, root_cause?: string, next_action: string, lesson?: string}` | Append reflection; auto-record lesson if provided (project scope, tag `auto-reflected`). Note: jsonl field names follow the file format (`action`, `next`), not the tool parameter names. |

All tool results are text content prefixed with `[OK]`, `[FAIL]`, or `[WARN]`.
Handlers never throw on bad state; they return explanatory text.

mcp-config.example.json: a complete example client configuration using a local
absolute path placeholder like `/absolute/path/to/mythify/mcp-server/src/index.js`
and a `MYTHIFY_DIR` env entry. This is the one allowed "placeholder", since the
install path is genuinely user-specific.

## Classification, execution profiles, and fast model triage

Classification is two-stage:

1. Deterministic gate. `classify` and `classify_task` always compute task type,
   risk, ambiguity, ceremony, execution profile, verification hint, fanout fit,
   and `model_triage`.
2. Optional fast model pass. The caller must opt in with `--triage auto`,
   `--triage always`, or the matching MCP `triage` argument. `auto` runs only
   when `model_triage` is `recommended` or `required`.

`execution_profile` may be `direct`, `fast`, `standard`, or `full`:

- `direct`: answer or make one reversible edit with no protocol state.
- `fast`: focused low-risk work skips plan state but still requires an executed
  `verify run` before completion is claimed.
- `standard`: create a plan with verifiable steps, act step by step, and run
  `verify run` before completion.
- `full`: use plan, memory, step updates, executed verification, reflection on
  failures, and summary.

Classification always returns `model_policy`. It separates:

- `provider_defaults`: advisory provider defaults for each role. These are
  policy metadata only and do not route work by themselves. Precedence is
  future explicit role input, `MYTHIFY_ROLE_<ROLE>_PROVIDER`, then built-in
  defaults. Invalid env values are ignored with `status:
  "invalid_env_ignored"`. Every role uses `fallback_policy:
  "no_implicit_cross_provider_fallback"`. The object also declares
  `timeout_metadata_fields` and `cost_metadata_fields` so hosts know which
  fields are intentionally standardized.
- `provider_defaults.provider_catalog`: provider-specific posture metadata for
  `host`, `host_cli`, `local_openai_compatible`, `api_provider`, `command`,
  and `local_command`. It records allowed roles, default roles, billing
  posture, execution boundary, evidence status, state-write posture, and
  fallback policy. Each resolved role also includes its selected
  `provider_profile`.
- `provider_defaults.adapter_interface_contract`: stable metadata shape shared
  by the registry-backed adapter lanes. It records version, fields, lanes,
  fallback policy, and an execution policy of
  `metadata_shape_only_no_runtime_change`. MCP also includes a normalized
  candidate catalog from the capability registry; CLI exposes the same
  contract fields without using them as a router.
- `provider_defaults.role_assignment_contract`: stable role-to-lane metadata
  for session, triage, reader, fanout worker, reviewer, verifier, remote
  execution, and agent lifecycle roles. It records default and selected
  providers, eligible adapter-interface lanes, evidence boundaries, state-write
  posture, and no-hidden-fallback guardrails. MCP additionally lists eligible
  candidate IDs from the adapter registry. The contract keeps
  `runtime_routing_changed: false`; role metadata never enables hidden
  fallback, remote execution, evals, deployments, or new state writes.
- `provider_defaults.api_provider_contract`: metadata for hosted providers
  before Mythify can spend API credits. It currently covers OpenAI, Anthropic,
  and hosted OpenAI-compatible endpoints. It records auth env names, billing
  posture, timeout metadata fields, cost metadata fields, pricing URLs, and
  `execution_enabled: false` for general provider role routing. The explicit
  fanout API path is recorded separately with `fanout_execution_enabled: true`,
  engines `anthropic` and `openai`, required acknowledgement fields,
  `.mythify/provider-audit.jsonl`, and `fanout_output_material_status:
  "material_not_verification"`.
- `provider_defaults.custom_adapter_contract`: metadata for user-defined
  adapter paths. The `command` adapter is enabled only through
  `MYTHIFY_TRIAGE_COMMAND` and `MYTHIFY_FANOUT_COMMAND`, reads prompts on
  stdin, obeys role timeouts, writes no Mythify state, and returns material,
  not verification evidence. The `http` adapter is metadata-only with
  `execution_enabled: false`; it records env names for a future custom HTTP
  worker and lists the execution blockers that must be solved first.
- Resolved role records include `timeout` and `cost` objects. `timeout`
  records `timeout_seconds`, `timeout_source`, `timeout_enforced_by`, and
  `can_override`. `cost` records billing posture, `cost_estimate_supported:
  false`, `cost_estimate_status: "not_estimated"`,
  `cost_estimate_cents: null`, pricing references, and usage metadata field
  names. Pricing URLs are advisory references only.
- `session`: host-selected current conversation model, model source, rough
  tier, effort policy, spawn ceiling, and `recommendation`.
  `host_model_switch` records intended host model changes in
  `.mythify/host-model.json`; the host still owns the actual current chat
  model switch. The optional `host_confirmation` record separates
  user-reported current model input from host-confirmed current model evidence.
  The `adapter_proof_scan` record reports supported, unsupported, or unknown
  apply and confirm paths without mutating host state.
- `session.recommendation`: task-based host settings with `action`,
  `target_profile`, `target_model`, `target_model_source`,
  `target_model_tier`, `thinking`, `speed`, and `reason`. The action is one
  of `keep`, `downgrade`, `upgrade`, or `recommend_set`.
- `spawn_ceiling`: policy object with `policy`, `source`, `session_model`,
  `session_model_source`, `session_model_tier`, default, and opt-in rule.
- `reader`: optional read-only model role for inspecting supplied material.
  It defaults to the localhost OpenAI-compatible provider path and can use the
  explicit Ollama profile. It returns material, not verification evidence.
- `triage`: spawned problem-framing worker, engine, spawned model policy,
  model tier, relation to the session model, provider default, effort,
  timeout, max turns, and sandbox.
- `fanout_worker`: default policy for independent fanout tasks, including
  chat visibility (`quiet`, `summary`, `verbose`, or `threaded`).
- `reviewer`: whether a separate reviewer worker is useful, its effort, and
  the explicit stronger-model policy. Reviewers default to same-or-lower than
  the initiating session; `reviewer_strength: "allow_stronger"` records
  classifier policy. Actual fanout still requires `role: "reviewer"` plus
  `reviewer_allow_stronger: true` before reviewer fanout may exceed the
  session without the broader `spawn_ceiling: "allow_stronger"` escape hatch.
- `verifier`: command-first verification policy, no model when an executable
  check exists.

Built-in role provider defaults:

| Role | Default provider | Allowed provider values |
| :--- | :--- | :--- |
| `session` | `host` | `host` |
| `triage` | `host_cli` | `host_cli`, `local_openai_compatible`, `command` |
| `reader` | `local_openai_compatible` | `local_openai_compatible`, `host` |
| `fanout_worker` | `host_cli` | `host_cli`, `api_provider`, `command` |
| `reviewer` | `host_cli` | `host_cli`, `api_provider`, `command` |
| `verifier` | `local_command` | `local_command` |

Built-in role provider catalog:

| Provider | Default roles | Allowed roles | Execution boundary | Evidence |
| :--- | :--- | :--- | :--- | :--- |
| `host` | `session` | `session`, `reader` | Host-selected current conversation | Host output is not verification |
| `host_cli` | `triage`, `fanout_worker`, `reviewer` | `triage`, `fanout_worker`, `reviewer` | Bounded local host CLI worker | Worker output is material, not verification |
| `local_openai_compatible` | `reader` | `triage`, `reader` | Localhost OpenAI-compatible model provider | Model output is material, not verification |
| `api_provider` | none | `fanout_worker`, `reviewer` | Guarded fanout API execution with explicit hosted provider acknowledgements | Provider output is material, not verification |
| `command` | none | `triage`, `fanout_worker`, `reviewer` | Explicit user command | Command output is material, not verification |
| `local_command` | `verifier` | `verifier` | Local executed verifier | Exit code is verification evidence |

`--platform` and MCP `platform` may be `auto`, `unknown`, `codex-desktop`,
`codex-cli`, `claude-desktop`, `claude-code`, `cursor-desktop`, or
`cursor-agent`. `--effort` and MCP `effort` may be `auto`, `low`, `medium`,
or `high`. `--speed` and MCP `speed` may be `auto`, `standard`, or `fast`.
Auto speed preserves the host or CLI default; fast maps to Codex fast mode
where supported; standard explicitly disables Codex fast mode for that spawned
worker. `--session-model`, MCP `session_model`, and
`MYTHIFY_SESSION_MODEL` provide the initiating model when the host can name it;
if neither is set, Mythify uses `.mythify/host-model.json` when present.
`--spawn-ceiling`, MCP `spawn_ceiling`, and `MYTHIFY_SPAWN_CEILING` may be
`auto`, `lower_only`, `same_or_lower`, or `allow_stronger`; auto defaults to
`same_or_lower`. `--reviewer-strength`, MCP `reviewer_strength`, and
`MYTHIFY_REVIEWER_STRENGTH` may be `auto`, `same_or_lower`, or
`allow_stronger`; auto defaults to `same_or_lower`. Auto effort keeps triage
cheap and scales fanout or reviewer effort by risk and ceremony.

Host recommendations are profile-based, then mapped to platform model names.
Direct low-risk prompts use profile `fast`, thinking `low`, and speed `fast`.
Research, benchmark, design, security, release, and migration prompts use
profile `strong`, thinking `high`, and speed `standard`. Ambiguous or normal
implementation work uses profile `standard`, thinking `medium`, and speed
`auto`. Defaults are Codex `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`; Claude
`haiku`, `sonnet`, `opus`; and Cursor `gpt-5.3-codex-low-fast`,
`gpt-5.3-codex`, `gpt-5.3-codex-high`. The defaults can be replaced with
`MYTHIFY_HOST_FAST_MODEL`, `MYTHIFY_HOST_STANDARD_MODEL`, and
`MYTHIFY_HOST_STRONG_MODEL`.

The fast model pass is not verification. It returns a problem frame that the
main agent may use before planning. The required JSON shape is:

```json
{
  "primary_type": "string",
  "secondary_types": ["string"],
  "ambiguity": "low|medium|high",
  "hidden_questions": ["string"],
  "likely_files_or_surfaces": ["string"],
  "verification_plan": ["string"],
  "fanout_plan": ["string"],
  "risk_notes": ["string"],
  "recommended_first_step": "string"
}
```

Supported fast triage engines are local-first and API-free:
`claude-cli`, `codex-cli`, `cursor-agent`, and `command`. Selection order is
explicit argument, `MYTHIFY_TRIAGE_ENGINE`, the initiating host from
`MYTHIFY_HOST_PLATFORM` or detected host state when that CLI is available,
local CLI auto-detection, then `MYTHIFY_TRIAGE_COMMAND`. Fanout binary env vars
are accepted as fallbacks for CLI paths. `claude-cli` defaults to model
`haiku`; `codex-cli` and `cursor-agent` use their local defaults unless
`MYTHIFY_TRIAGE_MODEL` or an explicit model is set. The `command` engine reads
the triage prompt on stdin
and must print JSON. It is the custom command adapter path for triage only;
its output is material, not verification evidence.

## Trace analysis

`trace analyze` is a CLI-only read-only surface for turning exported agent
traces into Mythify product and eval signals. It intentionally has no hard
runtime dependency on Hugging Face or `datasets`; callers export bounded JSONL
or JSON slices first, then pass local files to Mythify.

Supported row shapes:

- session traces: rows with `trace`, `messages`, `metadata`, and optional
  `num_tool_calls`
- action rows: rows with `context`, `completion`, `output_type`, and an
  `output` object such as `{ "tool": "Bash", "input": { "command": "..." } }`
- scenario rows: rows with `instruction`, `input`, `output`, and `prompt`

The analyzer records counts for formats, sessions, models, harnesses, output
types, trace event types, tool names, repeated shell commands, verifier-like
command signals, verifier-like text signals, and error or recovery language.
It then emits recommendations such as classifier scenario evals, action-first
runtime behavior, automatic evidence detection, background monitoring, visual
verification, and context-limit recovery.

The trace surface also has a playbook layer:

- `trace distill` filters one model slice and renders a Markdown behavior
  profile.
- `trace compare` filters target and baseline slices, compares visible metrics,
  and renders target-minus-baseline guidance.
- `trace playbook` compresses a target slice into session-start operating
  rules for chat-native agent work.
- `trace install-playbook` installs generated Markdown as a local Code or Codex
  skill with overwrite protection.

The measured signals are intentionally visible and reproducible: tool density,
command density, read/edit rhythm, test/edit rhythm, verify/edit rhythm, top
tools, verifier-like commands, and recovery language. Mythify does not attempt
to extract private reasoning. The output is a practical behavior scaffold for
agents that already run inside a host chat.

Guardrail: trace analysis is material, not verification. A Fable or Mythos
trace can suggest what Mythify should do, but it cannot prove local work is
complete. Completion claims still require `verify run`, `verify_run`, or an
explicit attested warning when no executable check exists.

## Research workflow

`research` is a CLI state surface for source-backed inquiry:

- `research start QUESTION [--name NAME] [--json]`
- `research list [--json]`
- `research add-source TITLE [--url URL] [--note TEXT] [--credibility C]`
- `research add-claim CLAIM --evidence TEXT [--source ID] [--confidence C]`
- `research add-question QUESTION`
- `research summary [NAME] [--json]`
- `research close [NAME] --decision TEXT`

State lives under `.mythify/research/`:

- `active`: current research record pointer.
- `<slug>.json`: question, status, sources, claims, open questions, decision,
  created timestamp, and updated timestamp.

Research records are deliberately material-only. A claim inside a research
record can guide a design or product decision, but it does not prove that local
implementation work is complete. When research turns into code or docs, the
host must move through a plan, outcome, or campaign and record executable
verification where available.

## Prompt packet workflow

`prompt` is a CLI read-only surface for chat-native reprompting:

- `prompt research [NAME] [--goal TEXT] [--verify COMMAND] [--json]`
- `prompt analysis [--goal TEXT] [--verify COMMAND] [--json]`
- `prompt failure [--goal TEXT] [--verify COMMAND] [--json]`
- `prompt handoff [--goal TEXT] [--verify COMMAND] [--json]`
- `prompt review [--goal TEXT] [--verify COMMAND] [--json]`
- `prompt campaign [NAME] [--goal TEXT] [--verify COMMAND] [--json]`
- `prompt next [--goal TEXT] [--verify COMMAND] [--json]`

Each packet returns:

- `kind`: the requested kind.
- `selected_kind`: the packet type actually rendered.
- `title`: human-readable packet label.
- `source`: the durable state source, such as research, campaign, verification,
  workflow state, or git state.
- `context`: structured context for hosts that want JSON.
- `next_prompt`: the chat-ready prompt to display or inject.
- `guardrail`: material-only warning.

`prompt next` chooses the packet from durable state. It routes to failure
recovery only when the latest executed verification is red, then to an active
campaign, active research, active plan handoff, and finally analysis. MCP
clients use `prompt_packet` for the same contract. Both surfaces must be
read-only and must not convert prompt text into verification evidence.

## Workflow router

`route` is the CLI decision-tree surface for choosing the next workflow shape
without performing it:

- `route TASK [--json]`
- MCP clients use `workflow_route` with the same contract.
- For broad or ambiguous prompts, chat hosts should call this before lower-level
  primitives such as `classify`, `plan`, `outcome`, `campaign`, `prompt`, or
  fanout.

The router combines deterministic classification, the active durable state, the
latest executed verification, and `protocol/workflow-router.json`. It returns a
route packet with the route id, reason, suggested next command, prompt packet
kind, verification strategy, chat policy, pause rules, expected state writes,
and evidence. It is read-only: the host chat still executes edits, runs checks,
reports issues, and records evidence.

Priority order favors recovery and durable loops:

1. latest executed verification is red: `failure`;
2. full-send language such as "one shot", "in one go", "address all", or
   "yolo": `campaign`;
3. active campaign or outcome with continue language: `campaign` or `outcome`;
4. explicit research or review language: `research` or `review`;
5. active plan continuation: `handoff`;
6. direct low-risk prompts: `direct`;
7. otherwise: `plan`.

## Campaign workflow

`campaign` is a CLI state surface for long-running "one-shot a project" work:

- `campaign start GOAL [--tasks JSON] [--name NAME] [--success TEXT] [--verify COMMAND]`
- `campaign list [--json]`
- `campaign status [NAME] [--json]`
- `campaign prompt [NAME] [--json]`
- `campaign watch [NAME] [--interval N] [--max-iterations N] [--json]`
- `campaign add-task TITLE [--criteria TEXT]`
- `campaign advance [NAME] --result TEXT`
- `campaign task ID STATUS [RESULT]`
- `campaign learn LESSON [--task ID] [--apply-next]`
- `campaign stop [NAME] --reason TEXT`

State lives under `.mythify/campaigns/`:

- `active`: current campaign pointer.
- `<slug>.json`: goal, success criteria, optional campaign verifier, current
  task id, generated or explicit tasks, phase events, learnings, status,
  created timestamp, and updated timestamp.

Each campaign task moves through the same loop:

1. understand
2. design
3. build
4. judge
5. verify
6. reflect

Advancing from `reflect` completes the current task and moves the frontier to
the next pending task. `campaign learn` records a small improvement that should
shape later tasks. This is the productized version of the long-horizon loop:
durable task frontier, visible phase, verification slot, reflection, and
learning carried forward.

Campaigns do not execute arbitrary project work by themselves. The host agent
does the work, runs checks, and calls `campaign advance` or `campaign task`
with the result. This keeps Mythify as the evidence and control layer rather
than a hidden executor.
`campaign prompt` renders the current task and phase as a host prompt without
mutating state. `campaign watch` repeats that read-only render on an interval so
a host-managed background loop can pick up the next prompt after an external
advance. MCP clients use the read-only `campaign_next_prompt` tool for the same
contract, `workflow_route` when they need Mythify to choose the next workflow
path, or `prompt_packet` when they need the shared packet contract across
research, analysis, failure recovery, handoff, review, campaign, and next.

### Smoke test: mcp-server/test/smoke.test.js

Uses `node:test` and the SDK `Client` with `StdioClientTransport`, spawning the
server with `MYTHIFY_DIR` and `HOME` pointed at fresh temp directories. Assertions:

1. `tools/list` returns exactly the manifest tool names (set equality), the 36
   core tools plus `fanout_start`, `fanout_status`, `fanout_results`.
2. `classify_task` returns a benchmark classification in text form with
   execution profile `full`, a question classification in JSON form with
   execution profile `direct`, and a command-backed fast triage result when
   requested.
3. `memory_store` then `memory_recall` round-trips a value.
4. `plan_create` with one step, then `plan_update_step` to completed WITHOUT result
   returns the evidence refusal and leaves the step pending; with result it succeeds.
5. `verify_run` with `node -e "process.exit(0)"` reports VERIFIED; with
   `node -e "process.exit(3)"` reports UNVERIFIED.
6. `memory_clear` with no arguments refuses.
7. Outcome tools start a loop, run a successful verifier, record iteration
   evidence, and fail cleanly when the retry budget is exhausted.
8. After the calls, read `memory.json` and the plan file from the temp dir and assert
   the exact field names from the format contract (this enforces interop at the byte
   level).

## Protocol: protocol/PROTOCOL.md

The canonical behavioral protocol, under 160 lines, written to steer a model, not to
document the project. Required structure:

1. Title and one-paragraph identity: "You are operating under the Mythify Protocol",
   an operational discipline layer; it changes how reliably the model works, not what
   it can do.
2. Core rules, always active: act don't ask; lead with outcome; ground every claim
   (a completion claim requires an executed verification); bounded autonomy (pause
   only for destructive or irreversible actions, real scope changes, or input only
   the user can provide); anti-overengineering; persist state outside the context
   window on long tasks.
3. Proportional ceremony table: trivial task (single edit or question) uses no
   protocol commands; focused low-risk fix or test tasks use the fast profile
   with `verify run` but no plan state; multi-step single-session task uses a
   plan plus executed verification of completion claims; long-horizon or
   multi-session work uses the full loop with memory and lessons.
4. The autonomy loop: PLAN, ACT, VERIFY, REFLECT, then CORRECT or ADVANCE, with the
   exact CLI commands for each stage.
5. Verification doctrine: executed beats attested; `verify run` whenever anything
   executable exists (tests, builds, linters, a curl, a file check); `verify claim`
   only when nothing executable exists, and it never counts as verified.
6. Memory and lessons: what to store, when to recall (before architectural decisions,
   at session start), project vs global lessons.
7. Command quick reference matching the CLI table exactly.
8. A short MCP note listing the 38 tool names for clients using the server instead
   of the CLI, with delegation discipline for the fanout tools.

### Protocol handshake

The CLI embeds the SHA-256 hash of `protocol/PROTOCOL.md` in
`PROTOCOL_SOURCE_SHA256`. Generated protocol variants include the same hash in a
metadata header:

```
<!-- Mythify protocol-sha256: HASH -->
```

`python3 scripts/mythify.py protocol check [PATH ...] [--json]` compares the
embedded CLI hash with explicit protocol copy paths. With no paths, it checks
the source repo protocol when present and any `CLAUDE.md`, `AGENTS.md`, and
`.cursorrules` files in the current working directory.

Failure modes:

- Missing metadata header: print `[FAIL]`, name the path, and exit 1.
- Hash mismatch: print `[FAIL]`, show the expected and actual short hashes,
  and exit 1.
- Source protocol mismatch in a source checkout: print `[FAIL]`, name
  `protocol/PROTOCOL.md`, and exit 1.

The command reads files only; it does not create `.mythify` state. A copied
install can therefore verify that its protocol file and CLI came from the same
source protocol before an agent trusts either one.

### scripts/build_variants.py

Reads `protocol/PROTOCOL.md`, writes three files at the repo root: `CLAUDE.md`,
`AGENTS.md`, `.cursorrules`. Each begins with the header line:

```
<!-- Generated from protocol/PROTOCOL.md by scripts/build_variants.py. Edit the source, then rebuild. -->
```

followed by the protocol hash metadata header, a blank line, and the protocol body
verbatim. Idempotent. Zero dependencies. Exit 0 on success with an `[OK]` line
listing the files written.

## Skill surfaces

`skills/mythify/` is the Manus-style skill package. `SKILL.md` starts with YAML
frontmatter:

```yaml
---
name: mythify
description: Operational discipline protocol that gives any AI agent Mythos-class autonomy patterns, including planning loops, executed verification, persistent memory, and structured reflection. Use when executing multi-step or long-horizon tasks, when work spans sessions, when progress claims need grounding in evidence, or when the user asks for mythify or mythos-style autonomous execution.
---
```

Body: condensed protocol with pointers describing when to read each reference file.
References, each under 100 lines, v2 semantics throughout:

- `references/autonomy-loop.md`: the loop, proportional ceremony, step lifecycle.
- `references/self-verification.md`: executed vs attested, evidence rule, examples.
- `references/memory-system.md`: memory categories, project vs global lessons,
  read-before-decide discipline.
- `references/meta-prompts.md`: the injectable behavioral constraints (act over ask,
  lead with outcome, grounding, bounded autonomy, anti-overengineering, persistence).

Codex-style chat front doors live beside the package skill:

- `skills/mythify-work/`: visible multi-step work loop. It keeps execution in
  the initiating chat, marks the report cursor, and surfaces a report after
  steps and verifiers.
- `skills/mythify-route/`: visible router. It shows the route decision, reason,
  and next action before mutating state.
- `skills/mythify-verify/`: visible verifier. It turns a claim into executed
  evidence, reports the verdict, and completes the active step when applicable.

These focused skills exist because chat experience is an execution-model
problem, not a Node-versus-Python problem. Godpowers-style visibility comes
from in-band Markdown skills and report cadence. The CLI and MCP server remain
the evidence ledger behind that chat surface.

### scripts/package_skill.py

Zips `skills/mythify/` contents so SKILL.md sits at the zip root with `references/`
beside it. Output: `dist/mythify.skill`. Stdlib `zipfile` only. Prints the entry
list and `[OK]` on success.

### scripts/install_user.sh

Installs user-local launchers and, by default, copies `skills/mythify*`
directories into `$CODEX_HOME/skills` or `$HOME/.codex/skills`. `--skip-skills`
disables that copy, `--skills-root PATH` overrides the destination, and
`--install-chat-hook` installs `scripts/mythify_chat_report_hook.sh` as
`mythify-chat-report-hook.sh` under `$CODEX_HOME/hooks` or `$HOME/.codex/hooks`.
The hook helper only prints `report --since last --cursor chat --format chat`
output. It does not mutate host config.

## README.md

Sections, in order:

1. Title and tagline: give any model Mythos-class operational discipline.
2. Honest framing paragraph: this improves the harness, not the underlying model;
   a weaker model with disciplined planning, executed verification, and persistent
   memory completes more long-horizon work than the same model without them; link
   `docs/research-report.md` and state its own caveat (training beats prompting;
   this closes the discipline gap, not the capability gap).
3. Components table: protocol variants, CLI, user installer, MCP server, skill.
4. Start Here section: position Mythify as an evidence protocol for AI coding
   agents, link `docs/start-here.md`, show `scripts/install_user.sh --project`,
   then show the minimal plan, verify, step, summary loop.
5. Quick start A: drop-in (copy `CLAUDE.md` or `AGENTS.md`, `scripts/mythify.py`,
   `protocol/operation-registry.json`, and
   `protocol/classification-rules.json`, and `protocol/workflow-router.json`
   into a project, run
   `python3 scripts/mythify.py protocol check FILE`, then `init`).
6. Quick start B: MCP server (npm install inside `mcp-server/`, then the example
   client config; note `MYTHIFY_DIR` and `MYTHIFY_DISABLE_RUN`).
7. Quick start C: build the skill (`python3 scripts/package_skill.py`).
8. How it works: proportional ceremony including the fast profile, the autonomy
   loop, then "Verification: evidence over attestation" with a short example
   transcript showing `verify run` on a failing then passing test command.
9. State layout tree.
10. CLI command reference table and MCP tool table (matching this spec exactly).
11. Compatibility table: Claude Code, Cursor, Windsurf, VS Code Copilot,
    Claude Desktop, Manus, any CLI agent, custom MCP clients.
12. Development: `python3 -m unittest discover -s tests -v` and
    `cd mcp-server && npm ci && npm test`.
13. Limitations, honest: no npm registry package yet (`scripts/install_user.sh`
    from a checkout is the supported user-local path), evals not yet run
    (claims are design rationale, not measured results), protocol adherence
    varies by model strength.
14. License: MIT.

The README also links `docs/cli-to-model-runtime-migration.md`, which explains
the opt-in path from CLI-only usage to host, local model, API provider,
execution substrate, and lifecycle lanes without hidden routing, automatic
spending, or deployment.

Document only what exists. No npx instructions, no badges for services not set up.

## Local Evaluation Harness

`scripts/local_model_eval.py` is the built-in bare-vs-Mythify smoke harness.
It creates paired task workspaces, runs the selected local CLI or command
engine, then verifies each workspace with `python3 -m unittest`. The model
output is material; the evaluation metric comes from verifier exit codes.

The JSON report must include:

- `summary`: per-mode attempts, model success, verified success, Mythify
  evidence success, success rates, average model duration, and the winner by
  verified success rate.
- `verified_task_success`: the direct answer to the product question. It
  records `metric: "verified_success_rate"`, `comparison:
  "mythify_vs_bare"`, `evidence_source:
  "per-workspace python3 -m unittest exit code"`, bare and Mythify rates,
  the rate delta, winner, conclusion, Mythify evidence rate, duration delta,
  and `statistical_strength: "local_smoke"`.
- `false_completion_claims`: the direct answer to the false-completion
  question. It records `metric: "false_completion_rate"`, `completion_signal:
  "model_exit_code_0"`, the evidence source, per-mode completion signals,
  verifier-backed claims, false completion claims, false-completion rates,
  rate delta, lower-rate winner, conclusion, and the local smoke-test caveat.
  The harness must not classify tone or confidence in model prose as evidence.
- `profile_overhead`: the direct answer to the profile-overhead question. It
  records `metric: "avg_model_duration_seconds"`, `comparison:
  "mythify_profile_vs_bare"`, measured duration evidence source, bare and
  Mythify average model durations, delta, ratio, lower-duration winner,
  conclusion, per-profile attempts, per-profile duration deltas, speed fields,
  and the local smoke-test caveat. The harness must not estimate timing or use
  model-reported timing as evidence.
- `local_model_benefit`: the direct answer to the local-model task-fit
  question. It records `metric: "local_model_candidate_task_categories"`,
  supported local roles, per-scenario task categories, local reader or triage
  candidate roles, fit reasons, observed verifier-backed success rates,
  Mythify evidence rates, category summaries, and the local smoke-test caveat.
  The harness must not claim provider-specific local model benefit unless the
  report came from a local-model-backed command or provider check.
- `fanout_value`: the direct answer to the fanout-value question. It records
  `metric: "fanout_value_fit"`, helpful and waste-prone task shapes, policy
  rows, per-scenario fanout-fit metadata, observed verifier-backed success
  rates, Mythify evidence rates, single-worker sufficiency counts, and the
  local smoke-test caveat. The harness must not treat parallelism, worker
  enthusiasm, or worker output alone as value; proving real fanout value
  requires independent worker outputs, a merged artifact, and a verifier run
  after the merge.
- `role_strength`: the direct answer to the stronger-model role question. It
  records `metric: "stronger_model_role_requirement"`, default spawn ceiling,
  required stronger roles, scoped reviewer opt-in roles, broad stronger opt-in
  roles, per-role policy rows, observed harness rates, and the local
  smoke-test caveat. The harness must not claim bigger models are generally
  better; stronger-model benefit requires a paired run with the role isolated.
- `runs`: per-workspace model exit details, verifier exit details, output
  tails, and Mythify evidence counts.

The built-in scenarios are a rerunnable smoke signal, not a large benchmark.
Do not upgrade `verified_task_success.conclusion` into a release claim without
recording the exact harness command and its JSON output as evidence.

## Housekeeping

.gitignore:

```
.DS_Store
.mythify/
.mcp.json
__pycache__/
*.pyc
node_modules/
dist/
*.corrupt-*
*.tgz
npm-debug.log*
```

LICENSE: MIT, copyright 2026 Mythify contributors.

## Tests

### tests/test_mythify.py

Stdlib `unittest`. Invoke the CLI as a subprocess with `sys.executable`, a scrubbed
environment (`MYTHIFY_DIR` removed, `HOME` pointed at a per-test temp directory so
the real global lessons store is never touched), and `cwd` inside a temp project
directory. Required coverage:

- init creates the documented layout, adds the default state directory to
  `.gitignore`, preserves existing ignore rules, and re-init warns and exits 0.
- Commands without a workspace fail with exit 1 and the documented message.
- State discovery walks up: a command run from a nested subdirectory finds the
  project `.mythify`.
- `MYTHIFY_DIR` overrides discovery and is created on demand.
- Plan lifecycle: create with steps, create without steps, add-step, list, show,
  switch, archive; slug collision produces `-2`.
- Step updates: valid transitions; invalid status rejected with exit 1; completed
  and failed without RESULT rejected with exit 1 and do not modify the plan;
  completed with RESULT persists result and prints the next pending step.
- Memory: set, overwrite, get with query and category filter, clear KEY, clear
  without args fails with exit 1, clear --all empties.
- Lessons: project add and list; global add and list (under the temp HOME); tag
  filter; scope filter.
- verify run: `true`-like command verified with exit 0; `false`-like command
  unverified with exit 2; timeout case (`--timeout 1` on a 5-second sleep) records
  exit_code -1 and exits 2; the jsonl record matches the executed format.
- verify claim: exits 0, prints ATTESTED warning, jsonl record has verified null.
- reflect: JSON form, flags form, missing required key fails, lesson auto-recording
  creates a project lesson tagged auto-reflected.
- summary and status: run without error and include the expected counts.
- Corrupt recovery: write garbage into memory.json, run `memory get`, expect
  `[WARN]` on stderr, exit 0, and a `memory.json.corrupt-*` file.

### tests/test_local_model_eval.py

Offline command-engine tests verify the local benchmark harness without real
model accounts. The default `--mythify-profile auto` resolves built-in focused
bugfix scenarios to `fast`, requiring executed verification evidence but no
plan record. `--mythify-profile standard` keeps the older plan-plus-verify
behavior and requires both plan and verification evidence.

### tests/test_interop.py

Stdlib only. Skips (unittest skip, not failure) unless `node` is on PATH and
`mcp-server/node_modules` exists. It runs the Python CLI and the Node MCP server
against one temp `.mythify` directory and covers the shared mutating state
surface, not probes or MCP-only fanout.

Coverage matrix:

- CLI writes, MCP reads: `host-model switch`, `plan create`, `step in_progress`,
  `memory set`, `lesson add`, and `outcome start`.
- MCP writes, CLI reads: `host_model_switch`, `plan_add_step`,
  `plan_update_step`, `memory_store`, `memory_clear`, `lesson_record`,
  `outcome_check`, `outcome_start`, `outcome_stop`, `verify_run`,
  `verify_claim`, and `reflect`.
- CLI writes after MCP writes, MCP reads: `host-model clear` is checked so the
  host model state contract is bidirectional.
- Verification records and reflection records are checked on disk because both
  APIs intentionally append logs rather than exposing a read tool for individual
  log entries.

### Whole-state refusal no-mutation checks

Refusal paths that promise "nothing was recorded", "nothing was cleared", or
"the plan was not modified" must be tested with whole-state snapshots. A
snapshot includes every regular file under the active `.mythify` directory,
keyed by relative path and content hash. Representative CLI and MCP refusal
tests must compare the full snapshot before and after the refused operation so
new files, removed files, and unrelated file rewrites are all caught.

Representative refusal paths:

- CLI: `step completed` without RESULT, `step completed` blocked by
  strict step evidence, `memory clear` with no target, and
  `verify run` with `MYTHIFY_DISABLE_RUN=1`.
- MCP: `plan_update_step` without `result`, `memory_clear` with no target, and
  `verify_run` with `MYTHIFY_DISABLE_RUN=1`.

## Fanout: parallel delegation (MCP only)

Fanout gives the orchestrating model parallel sub-workers through one-shot
declarative jobs: the model emits a task list once, and the server does the
spawning, sequencing, and collecting. This deliberately avoids turn-by-turn
orchestration, which weaker models cannot sustain. Fanout is MCP-only; the CLI
does not implement it (a CLI host has shell access and usually its own
parallelism), and `docs/design.md` is explicit about that divergence.

Implementation lives in `mcp-server/src/fanout.js`, wired into the server in
`mcp-server/src/index.js`.

### Engines

A worker is one fresh model invocation with no memory of the conversation.
Six engines, selected by `MYTHIFY_FANOUT_ENGINE` or auto-detected in this
order: explicit env value, else the initiating host CLI from
`MYTHIFY_HOST_PLATFORM` or detected host state when that CLI is available,
else `claude-cli` if a claude binary resolves, else `codex-cli` if a codex
binary resolves, else `cursor-agent` if Cursor Agent resolves, else
`anthropic` if `ANTHROPIC_API_KEY` is set, else `command` if
`MYTHIFY_FANOUT_COMMAND` is set, else `fanout_start` refuses with a message
listing all six options. `openai` is explicit-only because it needs both an
endpoint and a model.

| Engine | Mechanism | Billing | Models |
| :--- | :--- | :--- | :--- |
| `claude-cli` | Spawn `<bin> -p --output-format json --model <model> --max-turns <N>` with the assembled prompt on stdin, cwd = project root (parent of `.mythify/`). Parse the JSON output: `result` is the text, `is_error` true or a non-zero exit means failure. | Claude subscription (or whatever auth the claude CLI resolves) | Aliases `haiku`, `sonnet`, `opus`, `fable`, or any full model ID |
| `codex-cli` | Spawn `<bin> --ask-for-approval never exec --cd <project> --sandbox <mode> --skip-git-repo-check --ephemeral --color never --output-last-message <tmp> [-m <model>] -` with the assembled prompt on stdin. Exit 0 means success; the worker output is the output-last-message file, falling back to stdout. | Codex CLI local login, usually ChatGPT/Codex subscription auth | Any model the local Codex CLI supports; empty model means the CLI default |
| `cursor-agent` | Spawn `cursor-agent --print --output-format text --trust --workspace <project> [--mode <mode>] [--model <model>] <prompt-file-instruction>`, or `cursor agent ...` when the configured binary is `cursor`. The assembled prompt is written to a temporary file under `.mythify/tmp/`; stdout is the worker output. | Cursor Agent local login, usually Cursor subscription auth | Any model Cursor Agent exposes; empty model means the agent default |
| `anthropic` | POST `https://api.anthropic.com/v1/messages` (anthropic-version 2023-06-01) with `max_tokens` from env. Aliases map: haiku to claude-haiku-4-5, sonnet to claude-sonnet-4-6, opus to claude-opus-4-8, fable to claude-fable-5. Join text blocks. | API key (`ANTHROPIC_API_KEY`) | Any Claude model ID |
| `openai` | POST `<MYTHIFY_FANOUT_BASE_URL>/chat/completions` with `MYTHIFY_FANOUT_API_KEY`. | Provider API key | Any model the endpoint serves |
| `command` | Run the `MYTHIFY_FANOUT_COMMAND` shell template; prompt on stdin; stdout is the output; exit 0 is success. | Whatever the command does | Anything (generic CLI agents; also used by CI to test the job machinery with no network) |

The `command` engine is the supported custom command adapter path for fanout.
It is bounded by fanout validation, worker timeout, context byte caps, and the
depth guard. Its output is still material for the orchestrator, never final
verification evidence.

The hosted provider engines, `anthropic` and `openai`, require
`hosted_provider_billing_ack: true`, `hosted_provider_data_ack: true`, and
`hosted_provider_material_ack: true` before the job is created. The guard
acknowledges metered external billing, remote prompt and context transmission,
and the material-only status of provider output. Refusal happens before any
fanout job directory or provider audit row is written.

`claude-cli` binary resolution (Claude Desktop launches MCP servers with a
minimal PATH): `MYTHIFY_FANOUT_CLAUDE_BIN` if set, else `claude` on PATH, else
the first existing of `~/.claude/local/claude`, `/opt/homebrew/bin/claude`,
`/usr/local/bin/claude`. Resolution failure names the env var in the error.

`codex-cli` binary resolution: `MYTHIFY_FANOUT_CODEX_BIN` if set, else `codex`
on PATH, else the first existing of `~/.local/bin/codex`,
`/opt/homebrew/bin/codex`, `/usr/local/bin/codex`. Resolution failure names
the env var in the error. Workers run with `HOME`, `TERM=dumb`, an augmented
`PATH`, `CODEX_HOME` when set, `XDG_CONFIG_HOME` when set, and the fanout
guards. They do not inherit `OPENAI_API_KEY`; the intended path is local
`codex login`.

`cursor-agent` binary resolution: `MYTHIFY_FANOUT_CURSOR_BIN` if set, else
`MYTHIFY_FANOUT_CURSOR_AGENT_BIN`, else `cursor-agent` on PATH and common
locations, else `cursor` on PATH and common locations. When the resolved
binary name is `cursor`, Mythify prepends the `agent` subcommand. Workers run
with `HOME`, `TERM=dumb`, an augmented `PATH`, `XDG_CONFIG_HOME` when set, and
the fanout guards. They do not inherit `CURSOR_API_KEY`; the intended path is
local `cursor-agent login` or `cursor agent login`.

`claude-cli` worker environment is curated, not inherited: `HOME`, `TERM=dumb`,
`PATH` (server PATH augmented with `~/.local/bin`, `/opt/homebrew/bin`, and
`/usr/local/bin`), plus `CLAUDE_CODE_OAUTH_TOKEN` when present in the server
environment, plus the guards below. Harness variables (`CLAUDECODE`,
`CLAUDE_CODE_*`,
`ANTHROPIC_BASE_URL`) are NOT passed through: a server spawned by Claude Code
inherits harness routing that breaks nested workers. Subscription auth setup
is documented as: run `claude /login` once in a terminal, or run
`claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN` in the MCP client's
`env` block. A worker failure whose output contains `Not logged in` or
`401` is reported with exactly that remediation.

### Model, ceiling, and effort selection

Most specific wins: per-task `model` overrides per-job `model` overrides
`MYTHIFY_FANOUT_MODEL` overrides the engine default (`haiku` for `claude-cli`,
`claude-haiku-4-5` for `anthropic`, empty string for `codex-cli` and
`cursor-agent`, which means each local CLI uses its configured default). The
same precedence applies to `engine`, so one job may mix engines and models
across tasks (for example five haiku drafters and one sonnet reviewer; the
reviewer task is still independent and reviews material supplied in its
prompt, not other tasks' outputs).

Spawn ceiling is checked after model resolution. `session_model` comes from the
tool call, `MYTHIFY_SESSION_MODEL`, or `.mythify/host-model.json`; `spawn_ceiling`
comes from the tool call or `MYTHIFY_SPAWN_CEILING`, defaulting to
`same_or_lower`. Mythify classifies known model names into rough tiers:
`small`, `fast`, `standard`, `strong`, `frontier`, or `unknown`. If both the
session model and spawned model have known tiers, `fanout_start` refuses
stronger spawned models unless the ceiling is `allow_stronger`. A safer narrow
path exists for review: a task with `role: "reviewer"` may exceed the session
under `same_or_lower` only when the job also sets
`reviewer_allow_stronger: true`. That reviewer opt-in does not affect worker
tasks and does not override `lower_only`. Unknown tiers are recorded as
`uncheckable`; Mythify does not guess blank local CLI defaults.

Effort is a separate field with the same precedence: per-task `effort`
overrides per-job `effort`, which overrides `MYTHIFY_FANOUT_EFFORT`, which
falls back to a model-derived default. The resolved `effort` and
`effort_source` are stored on both the job and task records, shown in status
and result output, and included in the assembled worker prompt as
`Requested effort: <level>`.

Speed is tracked separately from effort. Per-task `speed` overrides per-job
`speed`, which overrides `MYTHIFY_FANOUT_SPEED`, which otherwise stays `auto`.
`auto` preserves the platform default.

Platform mapping:

- `codex-cli`: `fast` adds `service_tier = "fast"` and
  `features.fast_mode = true`; `standard` adds `features.fast_mode = false`.
- `claude-cli`: resolved `effort` is passed as `--effort`; `speed` is recorded
  and included in the worker prompt because Claude Code exposes no separate
  speed flag.
- `cursor-agent`: `model`, `effort`, and `speed` are resolved against the local
  `cursor-agent models` list. For example, `model: "gpt-5.3-codex"`,
  `effort: "high"`, and `speed: "fast"` resolves to
  `gpt-5.3-codex-high-fast` when that id is available. If no matching encoded
  id is found, Mythify leaves the requested model unchanged.

### Tools (3, total 29)

| Tool | Input schema | Behavior |
| :--- | :--- | :--- |
| `fanout_start` | `{tasks: [{title: string, prompt: string, context_paths?: string[], role?: enum(worker, reviewer), model?: string, engine?: string, effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast)}], purpose?: string, model?: string, engine?: string, effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast), visibility?: enum(auto, quiet, summary, verbose, threaded), session_model?: string, spawn_ceiling?: enum(auto, lower_only, same_or_lower, allow_stronger), reviewer_allow_stronger?: boolean, hosted_provider_billing_ack?: boolean, hosted_provider_data_ack?: boolean, hosted_provider_material_ack?: boolean, timeout_seconds?: number}` | Validate (1 to `MYTHIFY_FANOUT_MAX_TASKS` tasks, non-empty prompts, engine resolvable, kill switch and depth guard, context files readable, spawned model does not exceed the ceiling unless a reviewer-specific opt-in applies, hosted provider API engines require billing, data, and material-only acknowledgements). Create `.mythify/fanout/<job_id>/job.json`, return the job id IMMEDIATELY, run workers in the background with a concurrency pool. Tasks must be fully independent; the description says so and says each task is a fresh model call that costs real money, subscription quota, or local compute. Visibility defaults to summary unless `visibility`, `purpose`, or task prompts request quiet, verbose, or threaded reporting. |
| `fanout_status` | `{job_id?: string}` | Default: most recent job. Per-task lines with the step icon convention plus counts, engine, model, model tier, effort, speed, visibility, and elapsed. Quiet jobs show aggregate progress and failures only. If the job is marked running on disk but unknown to the in-memory registry (server restarted), mark its running tasks `interrupted` and say so. |
| `fanout_results` | `{job_id?: string, task_id?: number}` | Return outputs of completed and failed tasks (failures include the error and remediation). Per-task text in the tool result is capped at 20000 characters with a note pointing at the task output file. Warns when tasks are still running. |

Job ids: `fo-<YYYYMMDDHHMMSS>-<4 random hex>`. Worker prompt assembly:
fixed preamble (you are a delegated worker; the task is self-contained; do not
ask questions; return only the deliverable), then each context file as a
labeled fenced block, then the task prompt. `context_paths` resolve relative
to the project root (absolute allowed); total inlined context per task is
capped at `MYTHIFY_FANOUT_CONTEXT_BYTES` with an explicit truncation marker;
an unreadable path fails the task at validation time with a clear error.

Fanout visibility controls what the host should surface in the main chat.
Modes are `quiet`, `summary`, `verbose`, and `threaded`; `auto` is accepted
on input only. `summary` is the resolved default and should show worker titles,
status counts, and notable findings. `quiet` suppresses per-task status lines
except failures. `verbose` permits detailed worker output in the chat.
`threaded` asks the host to create visible worker chats only when the host has
native thread support; otherwise hosts should fall back to summary. Auto
visibility infers from `purpose` and task prompts, then defaults to summary.

### On-disk format

```
.mythify/fanout/<job_id>/
|-- job.json
`-- task-<id>-output.md
.mythify/provider-audit.jsonl
```

job.json (atomic writes on every transition):

```json
{
  "id": "fo-...", "created": "ISO-8601", "engine": "str", "model": "str",
  "billing": "str", "cost_tracking": "metadata_only_no_estimate",
  "cost_estimate_status": "not_estimated", "cost_estimate_cents": null,
  "pricing_url": "str",
  "model_source": "str", "model_tier": "str", "model_ceiling_status": "str",
  "session_model": "str", "session_model_source": "str",
  "session_model_tier": "str", "spawn_ceiling": "str",
  "spawn_ceiling_source": "str", "reviewer_allow_stronger": false,
  "hosted_provider_engines": ["anthropic|openai"],
  "hosted_provider_billing_acknowledged": false,
  "hosted_provider_data_acknowledged": false,
  "hosted_provider_material_acknowledged": false,
  "effort": "low|medium|high",
  "effort_source": "str", "speed": "auto|standard|fast",
  "speed_source": "str", "visibility": "quiet|summary|verbose|threaded",
  "visibility_source": "explicit|env|prompt|default",
  "visibility_requested": "auto|quiet|summary|verbose|threaded",
  "visibility_reason": "str", "purpose": "str",
  "timeout_seconds": 600,
  "timeout_source": "explicit|env:MYTHIFY_FANOUT_TIMEOUT_SECONDS|default|default_invalid_env_ignored",
  "last_updated": "ISO-8601",
  "tasks": [
    {"id": 1, "title": "str", "status": "pending|running|completed|failed|interrupted",
     "role": "worker|reviewer", "engine": "str", "model": "str", "model_source": "str",
     "billing": "str", "cost_tracking": "metadata_only_no_estimate",
     "cost_estimate_status": "not_estimated", "cost_estimate_cents": null,
     "pricing_url": "str",
     "model_tier": "str", "model_ceiling_status": "str",
     "stronger_reviewer_opt_in": false,
     "effort": "low|medium|high", "effort_source": "str",
     "speed": "auto|standard|fast", "speed_source": "str",
     "timeout_seconds": 600,
     "timeout_source": "explicit|env:MYTHIFY_FANOUT_TIMEOUT_SECONDS|default|default_invalid_env_ignored",
     "started_at": "ISO-8601 or null",
     "finished_at": "ISO-8601 or null", "duration_seconds": 0.0,
     "error": "str or null", "output_file": "task-1-output.md", "output_bytes": 0}
  ]
}
```

`provider-audit.jsonl` is append-only and receives one start event and one
finish event per spawned fanout task. Each row records:

- `surface: "fanout_worker"`, provider class, engine, model, role, effort,
  speed, job id, task id, and task title.
- Billing and cost metadata fields: billing posture, `cost_tracking`,
  `cost_estimate_status`, `cost_estimate_cents`, and `pricing_url`.
- Redacted request metadata: prompt SHA-256, prompt byte count, timeout
  seconds, timeout source, and `prompt_redacted: true`.
- Hosted provider acknowledgement metadata: whether the task required hosted
  provider acknowledgements, the required acknowledgement fields, and whether
  billing, data transmission, and material-only output were acknowledged.
- Redacted output metadata on finish: output file, output byte count,
  `output_redacted: true`, and whether an error was present.
- `output_material_status: "material_not_verification"`,
  `records_verification_evidence: false`, and the verifier boundary:
  worker output must be merged by the orchestrator and verified with
  `verify_run` or `outcome_check`.

The audit log must never store raw prompts, raw context blocks, API keys,
authorization headers, or worker output. It audits existing fanout worker
execution only; it does not enable any separate hosted provider execution path
or upgrade provider output into evidence.

### Configuration

| Env | Default | Meaning |
| :--- | :--- | :--- |
| `MYTHIFY_DISABLE_FANOUT` | unset | `1` disables all three tools (they refuse with an explanation). |
| `MYTHIFY_HOST_PLATFORM` | auto | Declares the initiating host and makes matching local CLIs the default worker choice. |
| `MYTHIFY_FANOUT_ENGINE` | auto | `claude-cli`, `codex-cli`, `cursor-agent`, `anthropic`, `openai`, `command`. |
| `MYTHIFY_FANOUT_MODEL` | engine default | Default worker model. |
| `MYTHIFY_SESSION_MODEL` | recorded host model or unknown | Current host session model used for spawn ceiling checks. Beats `.mythify/host-model.json` when set. |
| `MYTHIFY_SPAWN_CEILING` | `same_or_lower` | Spawn ceiling: `auto`, `lower_only`, `same_or_lower`, or `allow_stronger`. |
| `MYTHIFY_REVIEWER_STRENGTH` | `same_or_lower` | Reviewer strength policy: `auto`, `same_or_lower`, or `allow_stronger`. |
| `MYTHIFY_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Local Ollama OpenAI-compatible `/v1` endpoint for `provider: "ollama"`. |
| `MYTHIFY_OLLAMA_MODEL` | unset | Ollama model id for probe chat checks and local reader or triage runs. |
| `MYTHIFY_LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` | Local LM Studio OpenAI-compatible `/v1` endpoint for `provider: "lm-studio"`. |
| `MYTHIFY_LM_STUDIO_MODEL` | unset | LM Studio model id for probe chat checks and local reader or triage runs. |
| `MYTHIFY_LLAMA_CPP_BASE_URL` | `http://localhost:8080/v1` | Local llama.cpp OpenAI-compatible `/v1` endpoint for `provider: "llama-cpp"`. |
| `MYTHIFY_LLAMA_CPP_MODEL` | unset | llama.cpp model id for probe chat checks and local reader or triage runs. |
| `MYTHIFY_VLLM_BASE_URL` | `http://localhost:8000/v1` | Local vLLM OpenAI-compatible `/v1` endpoint for `provider: "vllm"`. |
| `MYTHIFY_VLLM_MODEL` | unset | vLLM model id for probe chat checks and local reader or triage runs. |
| `OPENAI_API_KEY` | unset | OpenAI API key env name recorded in hosted provider metadata. Fanout's OpenAI-compatible engine uses `MYTHIFY_FANOUT_API_KEY` instead. |
| `MYTHIFY_OPENAI_API_MODEL` | unset | OpenAI API model id env name recorded in hosted provider metadata. |
| `ANTHROPIC_API_KEY` | unset | Anthropic API key env name recorded in hosted provider metadata and used by the guarded `anthropic` fanout engine after hosted provider acknowledgements. |
| `MYTHIFY_ANTHROPIC_API_MODEL` | unset | Anthropic API model id env name recorded in hosted provider metadata. |
| `MYTHIFY_HOSTED_OPENAI_COMPAT_BASE_URL` | unset | Hosted OpenAI-compatible `/v1` endpoint env name recorded in provider metadata. |
| `MYTHIFY_HOSTED_OPENAI_COMPAT_API_KEY` | unset | Hosted OpenAI-compatible API key env name recorded in provider metadata. |
| `MYTHIFY_HOSTED_OPENAI_COMPAT_MODEL` | unset | Hosted OpenAI-compatible model id env name recorded in provider metadata. |
| `MYTHIFY_HOSTED_OPENAI_COMPAT_PROVIDER` | unset | Optional hosted OpenAI-compatible provider label env name recorded in provider metadata. |
| `MYTHIFY_HOSTED_OPENAI_COMPAT_PRICING_URL` | unset | Optional hosted OpenAI-compatible pricing URL env name recorded in provider metadata. |
| `MYTHIFY_HOST_FAST_MODEL` | platform default | Host recommendation model for direct, trivial, or focused low-risk prompts. |
| `MYTHIFY_HOST_STANDARD_MODEL` | platform default | Host recommendation model for balanced implementation, debugging, review, and docs prompts. |
| `MYTHIFY_HOST_STRONG_MODEL` | platform default | Host recommendation model for research, benchmarks, design, release, migration, and security prompts. |
| `MYTHIFY_ROLE_SESSION_PROVIDER` | `host` | Advisory provider default for the session role. Invalid values are ignored. |
| `MYTHIFY_ROLE_TRIAGE_PROVIDER` | `host_cli` | Advisory provider default for the triage role. Invalid values are ignored. |
| `MYTHIFY_ROLE_READER_PROVIDER` | `local_openai_compatible` | Advisory provider default for the reader role. Invalid values are ignored. |
| `MYTHIFY_ROLE_WORKER_PROVIDER` | `host_cli` | Advisory provider default for the fanout worker role. Invalid values are ignored. |
| `MYTHIFY_ROLE_REVIEWER_PROVIDER` | `host_cli` | Advisory provider default for the reviewer role. Invalid values are ignored. |
| `MYTHIFY_ROLE_VERIFIER_PROVIDER` | `local_command` | Advisory provider default for the verifier role. Invalid values are ignored. |
| `MYTHIFY_FANOUT_EFFORT` | model-derived | Default worker effort: `auto`, `low`, `medium`, or `high`. |
| `MYTHIFY_FANOUT_SPEED` | auto | Default worker speed: `auto`, `standard`, or `fast`. Auto preserves platform defaults; fast enables Codex fast mode where supported. |
| `MYTHIFY_FANOUT_VISIBILITY` | auto | Worker visibility mode: `auto`, `quiet`, `summary`, `verbose`, or `threaded`. Auto infers from `purpose` and task prompts, then defaults to summary. |
| `MYTHIFY_FANOUT_CONCURRENCY` | 3 | Parallel workers per job. |
| `MYTHIFY_FANOUT_MAX_TASKS` | 16 | Max tasks per job. |
| `MYTHIFY_FANOUT_MAX_TOKENS` | 8000 | API engines' max_tokens. |
| `MYTHIFY_FANOUT_MAX_TURNS` | 25 | claude-cli `--max-turns`. |
| `MYTHIFY_FANOUT_TIMEOUT_SECONDS` | 600 | Per-worker timeout; on expiry the worker is killed and the task fails with a timeout error. |
| `MYTHIFY_FANOUT_PRICING_URL` | unset | Optional pricing reference recorded for `openai` fanout engine cost metadata. No estimates are computed. |
| `MYTHIFY_FANOUT_CONTEXT_BYTES` | 200000 | Total inlined context per task. |
| `MYTHIFY_FANOUT_OUTPUT_BYTES` | 1048576 | Total captured stdout plus stderr per local CLI or command worker; on overflow the worker is killed and the task fails with retained diagnostic output. |
| `MYTHIFY_FANOUT_CLAUDE_BIN` | resolved | Path to the claude binary. |
| `MYTHIFY_FANOUT_CLAUDE_ARGS` | empty | Extra claude args, for example `--allowedTools "Bash"`. |
| `MYTHIFY_FANOUT_CODEX_BIN` | resolved | Path to the codex binary. |
| `MYTHIFY_FANOUT_CODEX_SANDBOX` | `read-only` | Codex worker sandbox mode. |
| `MYTHIFY_FANOUT_CODEX_ARGS` | empty | Extra codex exec args. |
| `MYTHIFY_FANOUT_CURSOR_BIN` | resolved | Path to `cursor-agent` or `cursor`. |
| `MYTHIFY_FANOUT_CURSOR_AGENT_BIN` | resolved | Path to `cursor-agent`, used only when `MYTHIFY_FANOUT_CURSOR_BIN` is not set. |
| `MYTHIFY_FANOUT_CURSOR_MODELS` | auto-list | Optional whitespace or comma-separated Cursor model id list. When unset, Mythify runs `cursor-agent models` or `cursor agent models` to resolve encoded model ids. |
| `MYTHIFY_FANOUT_CURSOR_MODE` | `ask` | Cursor Agent worker mode. Empty string omits `--mode`. |
| `MYTHIFY_FANOUT_CURSOR_FORCE` | unset | `1` adds `--force` to Cursor Agent workers. |
| `MYTHIFY_FANOUT_CURSOR_ARGS` | empty | Extra Cursor Agent args. |
| `MYTHIFY_FANOUT_BASE_URL`, `MYTHIFY_FANOUT_API_KEY` | unset | openai engine endpoint and key. |
| `MYTHIFY_FANOUT_COMMAND` | unset | command engine shell template. |

### Guards

- Depth limit of one: workers are spawned with `MYTHIFY_FANOUT_DEPTH=1` and
  `MYTHIFY_DISABLE_FANOUT=1` in their environment, and `fanout_start` refuses
  when `MYTHIFY_FANOUT_DEPTH` is already set in the server's own environment.
- Fanout results are material, not verification: the orchestrator merges them
  and then verifies the merged work with `verify_run`. The protocol text says
  this explicitly.
- Server lifetime caveat (documented): background workers live in the MCP
  server process; if the client disconnects or the server dies, running tasks
  die with it, and `fanout_status` reports them as interrupted afterward.

### Smoke coverage (mcp-server/test/, runs in CI with no network)

Using the `command` engine with a deterministic local template and stub local
CLI binaries: 16-tool set equality; a 3-task command job runs to completion
and `fanout_results` returns the outputs; `context_paths` content demonstrably
reaches the worker prompt; the kill switch refuses; the depth guard refuses; a
failing command produces a failed task with captured stderr; job.json matches
the format contract field by field; stub `claude-cli`, `codex-cli`, and
`cursor-agent` workers prove argv, prompt delivery, environment guards, and
auth remediation behavior without network access.

## Verified-step gate

Strict step evidence is enabled by default. Marking a step `completed` requires
both a non-empty RESULT string and evidence of a passing executed verification,
so that a "completed" step is backed by a real exit code rather than only a
prose claim. `MYTHIFY_REQUIRE_VERIFIED_STEP=0` is the legacy opt-out; values
`0`, `false`, `no`, and `off` disable the gate for compatibility.

The rule, identical in the CLI `step` command and the MCP `plan_update_step`
tool:

- The gate applies ONLY to status `completed`. `failed`, `in_progress`,
  `skipped`, and `pending` are never blocked by it (you must always be able to
  record a failure or a state change).
- The RESULT argument is still required first; the verified-step check runs
  after the non-empty-RESULT check.
- Evidence is satisfied when `verifications.jsonl` contains at least one record
  with `kind == "executed"` and `verified == true` whose `timestamp` is greater
  than or equal to the lower bound below. New records with non-null `plan` or
  `step_id` fields must match the target plan slug and step id. Older records
  without step-bound fields, and new records with null step context, keep the
  previous timestamp-only behavior for compatibility. Attested records
  (`kind == "attested"`) never satisfy the gate.
- Lower bound: the step's `updated_at` if the step has one (it was previously
  touched, for example set to `in_progress`); otherwise the parent plan's
  `created` timestamp. Comparison is string comparison of ISO-8601 timestamps,
  which is correct because the format is fixed-width and lexicographically
  ordered.
- On failure the plan is NOT modified and the command prints
  `[FAIL] Verified evidence required: strict evidence mode is enabled by default, but no passing 'verify run' was recorded since this step started. Run 'verify run' with a passing check first, or set MYTHIFY_REQUIRE_VERIFIED_STEP=0 to use legacy prose-only completion.`
  The CLI exits 1; the MCP tool returns that text.

This is the honest-evidence upgrade: with the gate on, the autonomy loop's ACT
step (`step ID in_progress`) sets the lower bound, the VERIFY step
(`verify run`) records the passing check, and only then does
`step ID completed` succeed.

## Versioning

This is Mythify v3.6.15. Fanout was added in 2.1.0; 2.2.0 added local
subscription-backed `codex-cli` and `cursor-agent` engines; 2.3.0 added
task classification; 2.4.0 added optional fast model triage after
classification, execution profiles, platform-aware model policy,
initiating-model awareness, spawn ceiling checks, and additive fanout model and
effort metadata; 2.5.0 makes the CLI `verify run` honor `MYTHIFY_DISABLE_RUN`
for parity with the MCP server, and adds the `MYTHIFY_REQUIRE_VERIFIED_STEP`
gate to both the CLI `step` command and the MCP `plan_update_step` tool; 3.0.0
aligns the model-runtime orchestration surface, local model lane, host CLI
worker lane, hosted provider fanout guardrails, execution substrate lane, agent
lifecycle lane, registry-generated adapter docs, and release-readiness surfaces
under the v3 roadmap; 3.0.1 fixes standalone MCP tarball startup by packaging
all runtime manifests under `mcp-server/protocol/`; 3.1.0 adds quick-start
installation and live work reports; 3.2.0 and 3.2.1 refine report mark mode;
3.2.2 rejects mark-plus-since report calls that would otherwise hide expected
events; 3.2.3 releases the follow-up documentation hygiene fixes from the
continuous audit loop; 3.3.0 adds chat-visible report attention summaries for
failures and warnings plus packaged skill guidance for chat-first Mythify use;
3.4.0 adds trace analysis and playbook generation; 3.5.0 adds research records,
campaigns, and campaign reprompt surfaces; 3.6.0 adds workflow prompt packets
for research, analysis, failure recovery, handoff, review, campaign, and
next-prompt routing, plus read-only workflow route surfaces for CLI and MCP
hosts; 3.6.1 makes the router the default front door in CLI help, docs, skill
instructions, and MCP descriptions while keeping primitive commands available;
3.6.2 makes strict step evidence the default and divides non-core surfaces into
workflow, advanced, and labs tiers backed by the checked surface manifest;
3.6.3 adds Codex-style chat front-door skills and installer support for a
Godpowers-style visible Mythify experience; 3.6.4 fixes strict step evidence
so explicit null-context verification records cannot complete steps while
older records without context keys remain compatible; 3.6.5 fixes
cross-runtime timestamp comparison in the strict evidence gate and makes CLI
`outcome check` honor the execution kill-switch; 3.6.6 adds cross-runtime
classification and verification record-shape conformance coverage; 3.6.7 caps
captured subprocess output for local CLI and command fanout workers; 3.6.8
adds default `.mythify/` `.gitignore` coverage during CLI init; 3.6.9 adds the
MCP server dependency audit gate to the Node CI matrix; 3.6.10 marks MCP
`[FAIL]` tool results with `isError: true`; 3.6.11 adds CLI `--version` and
makes the MCP server read its reported version from `package.json`; 3.6.12
refreshes the roadmap release status after the DOC-001 audit slice; 3.6.13
clarifies README architecture wording for the DOC-002 audit slice; 3.6.14
adds stable empty-state coverage for the read-only workflow views; 3.6.15
slugifies explicit state lookup names before filesystem access.
The CLI reports 3.6.15 through `--version`; the MCP server reads `package.json`
and reports the package version through server info.
