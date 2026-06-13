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
- No TODO markers, no placeholder content. Every file ships complete.
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
|   `-- operation-registry.json  shared operation metadata
|-- scripts/
|   |-- mythify.py               zero-dependency CLI orchestrator
|   |-- build_variants.py        generates CLAUDE.md, AGENTS.md, .cursorrules
|   |-- build_registry_docs.mjs  generates registry-backed docs
|   |-- local_model_eval.py      local bare-vs-Mythify comparison harness
|   `-- package_skill.py         builds dist/mythify.skill from skills/mythify/
|-- mcp-server/
|   |-- package.json
|   |-- mcp-config.example.json
|   |-- client-configs/
|   |-- src/capability-registry.js
|   |-- src/fanout.js
|   |-- src/index.js
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

- `host`: coding host, desktop app, or agent CLI.
- `model_provider`: API or local model endpoint.
- `execution_substrate`: runtime that executes remote or local jobs and returns logs,
  files, or artifacts.
- `agent_lifecycle`: scaffold, test, deploy, or observe tools for agents.

The current public host platforms remain `auto`, `unknown`, `codex-desktop`,
`codex-cli`, `claude-desktop`, `claude-code`, `cursor-desktop`, and
`cursor-agent`. Adapter profiles such as generic OpenAI-compatible local
providers, Ollama, LM Studio, llama.cpp, Kimi Code, OpenCode, Antigravity,
Google Colab CLI, Google Agents CLI, and Google ADK CLI live in the registry
instead of the host platform enum. Future candidates such as vLLM must enter
the registry first, then earn public schema support in a separate verified
slice.

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

## State model (shared contract)

### State directory resolution

1. If the `MYTHIFY_DIR` environment variable is set, use that path directly as the
   state directory. Create it (and subdirectories) on demand.
2. Otherwise walk from the current working directory upward; the first directory
   containing a `.mythify/` folder wins, and that `.mythify/` is the state directory.
3. Otherwise:
   - Python CLI: `init` creates `./.mythify`. Every other command prints
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
- CLI and MCP status output must expose `host_capability`, `can_apply_current_chat`,
  and `switch_result` so callers can distinguish desired state from host-confirmed
  state.

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
| `init` | Create `./.mythify` with subdirectories and empty memory.json. If already inside a workspace, print `[WARN]` and exit 0. | 0 |
| `protocol check [PATH ...] [--json]` | Verify copied protocol files match the CLI's embedded source protocol hash. With no paths, check source protocol when present and local `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` files. | 0 if every checked file matches; 1 on missing metadata or drift |
| `status` | Orientation: active plan with step icons, next pending step and its criteria, one-line counts (memory, lessons, verifications, reflections). | 0; 1 if no workspace |
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
| `step ID STATUS [RESULT] [--plan NAME]` | Update step status. STATUS must be one of the five enum values, otherwise `[FAIL]`, exit 1. `completed` and `failed` REQUIRE the RESULT argument (evidence or failure description); without it print `[FAIL] Evidence required: pass a RESULT describing what proves this status.` and exit 1. When `MYTHIFY_REQUIRE_VERIFIED_STEP=1`, `completed` ALSO requires a recorded passing executed verification (see "Verified-step gate" below), otherwise print `[FAIL] Verified evidence required: MYTHIFY_REQUIRE_VERIFIED_STEP=1 but no passing 'verify run' was recorded since this step started. Run 'verify run' with a passing check first.` and exit 1 without modifying the plan. After updating, print the next pending step. | 0 |
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
(current 1.x) and `zod` (3.x). package.json: name `mythify-mcp`, version `2.5.0`,
scripts `{"start": "node src/index.js", "test": "node --test test/*.test.js"}`
(the glob form, because modern Node treats a bare directory argument to --test as
a literal file and fails), engines node >= 18. Use the registration API that the
installed SDK version supports (prefer `registerTool`); verify against the
installed package, not from memory.

Exactly 28 tools: the 25 core tools below plus the 3 fanout tools defined in the
"Fanout: parallel delegation" section. Tool descriptions must state what the tool
does AND when to use it, since descriptions drive tool selection.

| Tool | Input schema | Behavior |
| :--- | :--- | :--- |
| `classify_task` | `{task: string, format?: enum(text, json), triage?: enum(never, auto, always), triage_engine?: enum(claude-cli, codex-cli, cursor-agent, command), triage_model?: string, triage_timeout_seconds?: number, platform?: enum(auto, unknown, codex-desktop, codex-cli, claude-desktop, claude-code, cursor-desktop, cursor-agent), effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast), session_model?: string, spawn_ceiling?: enum(auto, lower_only, same_or_lower, allow_stronger), reviewer_strength?: enum(auto, same_or_lower, allow_stronger)}` | Classify a task before planning. Returns task type, risk, ambiguity, ceremony level, execution profile, verification strategy, fanout recommendation, fast model triage fit, model policy, task-based host recommendation, signals, and next action. With `triage: auto`, run one fast local model only when the deterministic gate recommends it. |
| `host_model_switch` | `{action?: enum(switch, status, clear), platform?: enum(auto, unknown, codex-desktop, codex-cli, claude-desktop, claude-code, cursor-desktop, cursor-agent), target_model?: string, current_model?: string, thinking?: enum(auto, low, medium, high, xhigh, max), speed?: enum(auto, standard, fast), reason?: string, format?: enum(text, json)}` | Record, show, or clear a requested host chat model switch. `switch` writes `.mythify/host-model.json`, returns platform-specific switch guidance, registry-backed `host_capability`, and `switch_result`, and makes later `classify_task` and `fanout_start` calls use the recorded target as the session model when no explicit or env session model is supplied. It does not claim to mutate the current host chat unless a future host integration exposes that capability and confirms the result. |
| `provider_probe` | `{provider?: enum(generic-openai-compatible, ollama, lm-studio, llama-cpp), base_url?: string, model?: string, check?: enum(models, chat, both), api_key_env?: string, timeout_seconds?: number, prompt?: string, format?: enum(text, json)}` | Probe an OpenAI-compatible provider by calling `/v1/models` and, when requested, `/v1/chat/completions`. Generic defaults: `MYTHIFY_OPENAI_COMPAT_BASE_URL`, `MYTHIFY_OPENAI_COMPAT_MODEL`, and `MYTHIFY_OPENAI_COMPAT_API_KEY`. `provider: "ollama"` defaults to `MYTHIFY_OLLAMA_BASE_URL` or `http://localhost:11434/v1`; `provider: "lm-studio"` defaults to `MYTHIFY_LM_STUDIO_BASE_URL` or `http://localhost:1234/v1`; `provider: "llama-cpp"` defaults to `MYTHIFY_LLAMA_CPP_BASE_URL` or `http://localhost:8080/v1`. Local profiles use provider-specific model env vars and no auth header by default. Returns provider availability, model presence, chat response tail, and `material_not_evidence: true`. It does not write state, spawn workers, or count as verification evidence. |
| `local_model_run` | `{provider?: enum(generic-openai-compatible, ollama, lm-studio, llama-cpp), role?: enum(reader, triage), base_url?: string, model?: string, prompt: string, api_key_env?: string, timeout_seconds?: number, max_tokens?: number, format?: enum(text, json)}` | Run a role-limited prompt against a localhost OpenAI-compatible provider. Generic defaults: `MYTHIFY_OPENAI_COMPAT_BASE_URL`, `MYTHIFY_OPENAI_COMPAT_MODEL`, and `MYTHIFY_OPENAI_COMPAT_API_KEY`. `provider: "ollama"`, `provider: "lm-studio"`, and `provider: "llama-cpp"` default to local profiles. The base URL must be `localhost`, `127.0.0.1`, `::1`, or `0.0.0.0`. Returns model output with `material_not_evidence: true`, `evidence_status: "model_output_not_verification"`, `writes_state: false`, and `verification_recorded: false`. It does not edit files, run commands, write state, or count model output as verification evidence. |
| `host_cli_probe` | `{host?: enum(kimi-code, opencode, antigravity), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Kimi Code, OpenCode, or Antigravity CLI availability by running only version and help commands. Defaults to `MYTHIFY_KIMI_BIN`, `MYTHIFY_OPENCODE_BIN`, or `MYTHIFY_ANTIGRAVITY_BIN`, then PATH and common install paths. Returns binary resolution, feature evidence, `can_run_noninteractive_prompt`, and `material_not_evidence: true`. It does not execute a prompt, write state, spawn workers, or count as verification evidence. Antigravity MCP setup guidance lives in `docs/antigravity-mcp-setup.md`; the probe does not install or mutate MCP config. |
| `host_cli_run` | `{host?: enum(kimi-code, opencode), bin?: string, prompt: string, cwd?: string, timeout_seconds?: number, model?: string, agent?: string, format?: enum(text, json)}` | Run a bounded non-interactive prompt through Kimi Code or OpenCode. Kimi uses `kimi --print -p PROMPT --final-message-only`. OpenCode uses `opencode run --format json [--model MODEL] [--agent AGENT] PROMPT`. Defaults to `MYTHIFY_KIMI_BIN` or `MYTHIFY_OPENCODE_BIN`, then PATH and common install paths. Returns stdout and stderr tails, timeout and exit metadata, `material_not_evidence: true`, `evidence_status: "worker_output_not_verification"`, `writes_state: false`, and `verification_recorded: false`. It does not edit files directly, write Mythify state, or count worker output as verification evidence; merged work must still be verified with `verify_run`. |
| `execution_probe` | `{adapter?: enum(google-colab-cli), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Google Colab CLI availability by running only version and help commands. Defaults to `MYTHIFY_COLAB_BIN`, then PATH and common install paths. Returns binary resolution, feature evidence, `non_billable: true`, `job_execution_enabled: false`, and `material_not_evidence: true`. It does not provision a runtime, request an accelerator, execute notebooks, upload data, write state, or count as verification evidence. |
| `lifecycle_probe` | `{adapter?: enum(google-agents-cli, google-adk-cli), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Google Agents CLI or ADK CLI availability by running only version, help, and eval-help commands. Defaults to `MYTHIFY_AGENTS_CLI_BIN` or `MYTHIFY_ADK_BIN`, then PATH and common install paths. Returns binary resolution, feature evidence, `can_probe_eval: true`, `eval_execution_enabled: false`, `deployment_enabled: false`, and `material_not_evidence: true`. It does not scaffold projects, run agents, execute evals, deploy, publish, mutate cloud resources, write project state, or count as verification evidence. |
| `outcome_start` | `{goal: string, success: string, verify_command: string, metric_command?: string, max_iterations?: number, allowed_paths?: string[], visibility?: enum(auto, quiet, summary, verbose, threaded), name?: string, format?: enum(text, json)}` | Start a supervised outcome loop and set it active. The host agent acts between checks; Mythify records the verifier, metric, budget, and visibility policy. |
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
| `plan_update_step` | `{step_id: number, status: enum(pending, in_progress, completed, failed, skipped), result?: string, plan?: string}` | Enforce the evidence rule: `completed` or `failed` without `result` returns `[FAIL] Evidence required ...` and does NOT modify the plan. When `MYTHIFY_REQUIRE_VERIFIED_STEP=1`, `completed` also requires a recorded passing executed verification (see "Verified-step gate") and otherwise returns the same `[FAIL] Verified evidence required ...` text the CLI uses, without modifying the plan. On success, include the next pending step in the response. |
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
  "no_implicit_cross_provider_fallback"`.
- `session`: host-selected current conversation model, model source, rough
  tier, effort policy, spawn ceiling, and `recommendation`.
  `host_model_switch` records intended host model changes in
  `.mythify/host-model.json`; the host still owns the actual current chat
  model switch.
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
explicit argument, `MYTHIFY_TRIAGE_ENGINE`, local CLI auto-detection, then
`MYTHIFY_TRIAGE_COMMAND`. Fanout binary env vars are accepted as fallbacks for
CLI paths. `claude-cli` defaults to model `haiku`; `codex-cli` and
`cursor-agent` use their local defaults unless `MYTHIFY_TRIAGE_MODEL` or an
explicit model is set. The `command` engine reads the triage prompt on stdin
and must print JSON.

### Smoke test: mcp-server/test/smoke.test.js

Uses `node:test` and the SDK `Client` with `StdioClientTransport`, spawning the
server with `MYTHIFY_DIR` and `HOME` pointed at fresh temp directories. Assertions:

1. `tools/list` returns exactly the 28 tool names above (set equality), the 25
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
8. A short MCP note listing the 28 tool names for clients using the server instead
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

## Skill: skills/mythify/

Manus-style skill package. `SKILL.md` under 120 lines with YAML frontmatter:

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

### scripts/package_skill.py

Zips `skills/mythify/` contents so SKILL.md sits at the zip root with `references/`
beside it. Output: `dist/mythify.skill`. Stdlib `zipfile` only. Prints the entry
list and `[OK]` on success.

## README.md

Sections, in order:

1. Title and tagline: give any model Mythos-class operational discipline.
2. Honest framing paragraph: this improves the harness, not the underlying model;
   a weaker model with disciplined planning, executed verification, and persistent
   memory completes more long-horizon work than the same model without them; link
   `docs/research-report.md` and state its own caveat (training beats prompting;
   this closes the discipline gap, not the capability gap).
3. Components table: protocol variants, CLI, MCP server, skill.
4. Quick start A: drop-in (copy `CLAUDE.md` or `AGENTS.md`, `scripts/mythify.py`,
   and `protocol/operation-registry.json` into a project, run
   `python3 scripts/mythify.py protocol check FILE`, then `init`).
5. Quick start B: MCP server (npm install inside `mcp-server/`, then the example
   client config; note `MYTHIFY_DIR` and `MYTHIFY_DISABLE_RUN`).
6. Quick start C: build the skill (`python3 scripts/package_skill.py`).
7. How it works: proportional ceremony including the fast profile, the autonomy
   loop, then "Verification: evidence over attestation" with a short example
   transcript showing `verify run` on a failing then passing test command.
8. State layout tree.
9. CLI command reference table and MCP tool table (matching this spec exactly).
10. Compatibility table: Claude Code, Cursor, Windsurf, VS Code Copilot,
    Claude Desktop, Manus, any CLI agent, custom MCP clients.
11. Development: `python3 -m unittest discover -s tests -v` and
    `cd mcp-server && npm ci && npm test`.
12. Limitations, honest: no published npm package yet (local path config), evals not
    yet run (claims are design rationale, not measured results), protocol adherence
    varies by model strength.
13. License: MIT.

Document only what exists. No npx instructions, no badges for services not set up.

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

- init creates the documented layout; re-init warns and exits 0.
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
  `MYTHIFY_REQUIRE_VERIFIED_STEP=1`, `memory clear` with no target, and
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
order: explicit env value, else `claude-cli` if a claude binary resolves, else
`codex-cli` if a codex binary resolves, else `cursor-agent` if Cursor Agent
resolves, else `anthropic` if `ANTHROPIC_API_KEY` is set, else `command` if
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

### Tools (3, total 28)

| Tool | Input schema | Behavior |
| :--- | :--- | :--- |
| `fanout_start` | `{tasks: [{title: string, prompt: string, context_paths?: string[], role?: enum(worker, reviewer), model?: string, engine?: string, effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast)}], purpose?: string, model?: string, engine?: string, effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast), visibility?: enum(auto, quiet, summary, verbose, threaded), session_model?: string, spawn_ceiling?: enum(auto, lower_only, same_or_lower, allow_stronger), reviewer_allow_stronger?: boolean, timeout_seconds?: number}` | Validate (1 to `MYTHIFY_FANOUT_MAX_TASKS` tasks, non-empty prompts, engine resolvable, kill switch and depth guard, context files readable, spawned model does not exceed the ceiling unless a reviewer-specific opt-in applies). Create `.mythify/fanout/<job_id>/job.json`, return the job id IMMEDIATELY, run workers in the background with a concurrency pool. Tasks must be fully independent; the description says so and says each task is a fresh model call that costs real money, subscription quota, or local compute. Visibility defaults to summary unless `visibility`, `purpose`, or task prompts request quiet, verbose, or threaded reporting. |
| `fanout_status` | `{job_id?: string}` | Default: most recent job. Per-task lines with the step icon convention plus counts, engine, model, model tier, effort, speed, visibility, and elapsed. Quiet jobs show aggregate progress and failures only. If the job is marked running on disk but unknown to the in-memory registry (server restarted), mark its running tasks `interrupted` and say so. |
| `fanout_results` | `{job_id?: string, task_id?: number}` | Return outputs of completed and failed tasks (failures include the error and remediation). Per-task text in the tool result is capped at 20000 characters with a note pointing at the full output file. Warns when tasks are still running. |

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
```

job.json (atomic writes on every transition):

```json
{
  "id": "fo-...", "created": "ISO-8601", "engine": "str", "model": "str",
  "model_source": "str", "model_tier": "str", "model_ceiling_status": "str",
  "session_model": "str", "session_model_source": "str",
  "session_model_tier": "str", "spawn_ceiling": "str",
  "spawn_ceiling_source": "str", "reviewer_allow_stronger": false,
  "effort": "low|medium|high",
  "effort_source": "str", "speed": "auto|standard|fast",
  "speed_source": "str", "visibility": "quiet|summary|verbose|threaded",
  "visibility_source": "explicit|env|prompt|default",
  "visibility_requested": "auto|quiet|summary|verbose|threaded",
  "visibility_reason": "str", "purpose": "str",
  "timeout_seconds": 600, "last_updated": "ISO-8601",
  "tasks": [
    {"id": 1, "title": "str", "status": "pending|running|completed|failed|interrupted",
     "role": "worker|reviewer", "engine": "str", "model": "str", "model_source": "str",
     "model_tier": "str", "model_ceiling_status": "str",
     "stronger_reviewer_opt_in": false,
     "effort": "low|medium|high", "effort_source": "str",
     "speed": "auto|standard|fast", "speed_source": "str",
     "started_at": "ISO-8601 or null",
     "finished_at": "ISO-8601 or null", "duration_seconds": 0.0,
     "error": "str or null", "output_file": "task-1-output.md", "output_bytes": 0}
  ]
}
```

### Configuration

| Env | Default | Meaning |
| :--- | :--- | :--- |
| `MYTHIFY_DISABLE_FANOUT` | unset | `1` disables all three tools (they refuse with an explanation). |
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
| `MYTHIFY_FANOUT_CONTEXT_BYTES` | 200000 | Total inlined context per task. |
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

## Verified-step gate (opt-in)

`MYTHIFY_REQUIRE_VERIFIED_STEP` is unset by default, which preserves the
existing behavior exactly: a non-empty RESULT string is sufficient to mark a
step `completed`. When it is set to `1`, marking a step `completed`
additionally requires evidence of a passing executed verification, so that a
"completed" step is backed by a real exit code rather than only a prose claim.

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
  `[FAIL] Verified evidence required: MYTHIFY_REQUIRE_VERIFIED_STEP=1 but no passing 'verify run' was recorded since this step started. Run 'verify run' with a passing check first.`
  The CLI exits 1; the MCP tool returns that text.

This is the honest-evidence upgrade: with the gate on, the autonomy loop's ACT
step (`step ID in_progress`) sets the lower bound, the VERIFY step
(`verify run`) records the passing check, and only then does
`step ID completed` succeed.

## Versioning

This is Mythify v2.5.0. Fanout was added in 2.1.0; 2.2.0 added local
subscription-backed `codex-cli` and `cursor-agent` engines; 2.3.0 added
task classification; 2.4.0 added optional fast model triage after
classification, execution profiles, platform-aware model policy,
initiating-model awareness, spawn ceiling checks, and additive fanout model and
effort metadata; 2.5.0 makes the CLI `verify run` honor `MYTHIFY_DISABLE_RUN`
for parity with the MCP server, and adds the opt-in `MYTHIFY_REQUIRE_VERIFIED_STEP`
gate to both the CLI `step` command and the MCP `plan_update_step` tool. The CLI
prints no version banner; the MCP server reports 2.5.0 through its server info.
