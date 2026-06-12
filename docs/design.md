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
|   `-- PROTOCOL.md              canonical protocol source
|-- scripts/
|   |-- mythify.py               zero-dependency CLI orchestrator
|   |-- build_variants.py        generates CLAUDE.md, AGENTS.md, .cursorrules
|   `-- package_skill.py         builds dist/mythify.skill from skills/mythify/
|-- mcp-server/
|   |-- package.json
|   |-- mcp-config.example.json
|   |-- src/index.js
|   |-- src/fanout.js
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
|   `-- test_interop.py          CLI and MCP server against the same state dir
`-- docs/
    |-- design.md                this document
    |-- claude-integrations.md   Claude Desktop and Claude Code guide
    `-- research-report.md       preserved research report
```

`dist/` (built skill packages) and `node_modules/` are build outputs, ignored by git.

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
|-- plans/
|   |-- active                   text file containing the slug of the active plan
|   |-- <slug>.json
|   `-- archive/
|       `-- <slug>.json
|-- lessons/
|   `-- <slug>.json
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
{"kind": "executed", "claim": "str or null", "command": "str", "exit_code": 0, "duration_seconds": 0.03, "stdout_tail": "str", "stderr_tail": "str", "verified": true, "timestamp": "ISO-8601"}
{"kind": "attested", "claim": "str", "evidence": "str", "verified": null, "timestamp": "ISO-8601"}
```

`verified` is a boolean only for executed verifications (true when exit_code == 0).
Attested entries always have `verified: null`: a self-report is never marked verified.
On timeout, record `exit_code: -1`, `verified: false`, and append
`"(timed out after N seconds)"` to `stderr_tail`. Output tails keep the last 4000
characters of each stream.

reflections.jsonl, one JSON object per line:

```json
{"action": "str", "outcome": "success|partial|failure", "observation": "str", "root_cause": "str or null", "next": "str", "lesson": "str or null", "timestamp": "ISO-8601"}
```

### Durability rules

- All JSON file writes are atomic: write to a temp file in the same directory, then
  rename over the target (Python `os.replace`, Node `fs.renameSync`).
- Corrupt JSON on read: rename the bad file to `<filename>.corrupt-<YYYYMMDDHHMMSS>`,
  print `[WARN]` to stderr, and continue with a fresh default. Never crash.
- jsonl logs are plain appends.

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
| `status` | Orientation: active plan with step icons, next pending step and its criteria, one-line counts (memory, lessons, verifications, reflections). | 0; 1 if no workspace |
| `plan create GOAL [--steps JSON] [--name NAME]` | Create plan, set it active. `--steps` is a JSON array of `{"title": str, "success_criteria": str (optional)}`. Without `--steps`, create an empty plan and suggest `plan add-step`. Invalid JSON: `[FAIL]`, exit 1. | 0 |
| `plan add-step TITLE [--criteria TEXT] [--plan NAME]` | Append a step (id = max + 1) to the named or active plan. | 0; 1 if plan not found |
| `plan list` | List plans with active marker and per-plan progress, plus archived count. | 0 |
| `plan show [NAME]` | Full detail of the named or active plan. | 0; 1 if not found |
| `plan switch NAME` | Set the active plan pointer. | 0; 1 if not found |
| `plan archive [NAME]` | Move plan file to `plans/archive/`; clear the active pointer if it pointed there. On filename conflict in archive, append a timestamp. | 0; 1 if plan not found |
| `step ID STATUS [RESULT] [--plan NAME]` | Update step status. STATUS must be one of the five enum values, otherwise `[FAIL]`, exit 1. `completed` and `failed` REQUIRE the RESULT argument (evidence or failure description); without it print `[FAIL] Evidence required: pass a RESULT describing what proves this status.` and exit 1. After updating, print the next pending step. | 0 |
| `memory set KEY VALUE [--category C]` | Category one of fact, decision, discovery, state; default fact. | 0 |
| `memory get [QUERY] [--category C]` | Case-insensitive substring match over keys and values; optional category filter. | 0 |
| `memory clear [KEY] [--all]` | KEY removes one entry. `--all` clears everything. Neither: `[FAIL]` explaining the guard, exit 1. | 0 |
| `lesson add TITLE DETAIL [--tags a,b] [--global]` | Record a lesson in the project store, or the global store with `--global`. | 0 |
| `lesson list [--tag TAG] [--scope project\|global\|all]` | Default scope all; label each lesson `(project)` or `(global)`; `--tag` filters. | 0 |
| `verify run COMMAND [--claim TEXT] [--timeout N]` | Execute COMMAND through the shell, capture exit code, duration, and output tails, append an executed record, print the verdict. Default timeout 300 seconds. | 0 if verified, 2 if unverified |
| `verify claim CLAIM EVIDENCE` | Append an attested record and print the `[WARN] ATTESTED` line. | 0 |
| `reflect [JSON]` or `reflect --action A --outcome O --observation OBS --next N [--root-cause R] [--lesson L]` | Record a structured reflection. Required keys: action, outcome (enum success, partial, failure), observation, next. A provided lesson is auto-recorded as a project lesson tagged `auto-reflected`. JSON positional takes precedence over flags. Missing keys or bad outcome: `[FAIL]`, exit 1. | 0 |
| `summary` | Full session report: plans and progress, memory count, project and global lesson counts, verification stats (executed passed, executed failed, attested count), reflection count. | 0 |

Implementation notes:

- `verify run` uses `subprocess.run(command, shell=True, capture_output=True,
  text=True, timeout=N)`. Catch `TimeoutExpired` per the timeout rule above.
- All commands other than `init` require a resolvable state directory (or
  `MYTHIFY_DIR`, which is created on demand).
- `--help` output for the top level and each subcommand must be accurate.

## MCP server: mcp-server/

Node 18+, ESM (`"type": "module"`). Dependencies: `@modelcontextprotocol/sdk`
(current 1.x) and `zod` (3.x). package.json: name `mythify-mcp`, version `2.1.0`,
scripts `{"start": "node src/index.js", "test": "node --test test/*.test.js"}`
(the glob form, because modern Node treats a bare directory argument to --test as
a literal file and fails), engines node >= 18. Use the registration API that the
installed SDK version supports (prefer `registerTool`); verify against the
installed package, not from memory.

Exactly 15 tools: the 12 core tools below plus the 3 fanout tools defined in the
"Fanout: parallel delegation" section. Tool descriptions must state what the tool
does AND when to use it, since descriptions drive tool selection.

| Tool | Input schema | Behavior |
| :--- | :--- | :--- |
| `memory_store` | `{key: string, value: string, category: enum(fact, decision, discovery, state) = "fact"}` | Upsert by key. Returns `[OK]` summary. |
| `memory_recall` | `{query?: string, category?: enum(fact, decision, discovery, state, all)}` | Substring search as in the CLI. |
| `memory_clear` | `{key?: string, confirm_clear_all?: boolean}` | With key: remove one. Without key and without `confirm_clear_all: true`: refuse with an explanation, do not clear. |
| `lesson_record` | `{title: string, detail: string, tags?: string[], scope: enum(project, global) = "project"}` | Write a lesson file per the format. |
| `lesson_recall` | `{tag?: string, scope: enum(project, global, all) = "all"}` | List lessons, labeled by scope. |
| `plan_create` | `{goal: string, name?: string, steps?: [{title: string, success_criteria?: string}]}` | Ids auto-assigned 1-based. Sets active plan. |
| `plan_add_step` | `{title: string, success_criteria?: string, plan?: string}` | Append to named or active plan. |
| `plan_update_step` | `{step_id: number, status: enum(pending, in_progress, completed, failed, skipped), result?: string, plan?: string}` | Enforce the evidence rule: `completed` or `failed` without `result` returns `[FAIL] Evidence required ...` and does NOT modify the plan. On success, include the next pending step in the response. |
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

### Smoke test: mcp-server/test/smoke.test.js

Uses `node:test` and the SDK `Client` with `StdioClientTransport`, spawning the
server with `MYTHIFY_DIR` and `HOME` pointed at fresh temp directories. Assertions:

1. `tools/list` returns exactly the 15 tool names above (set equality), the 12
   core tools plus `fanout_start`, `fanout_status`, `fanout_results`.
2. `memory_store` then `memory_recall` round-trips a value.
3. `plan_create` with one step, then `plan_update_step` to completed WITHOUT result
   returns the evidence refusal and leaves the step pending; with result it succeeds.
4. `verify_run` with `node -e "process.exit(0)"` reports VERIFIED; with
   `node -e "process.exit(3)"` reports UNVERIFIED.
5. `memory_clear` with no arguments refuses.
6. After the calls, read `memory.json` and the plan file from the temp dir and assert
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
   protocol commands; multi-step single-session task uses a plan plus executed
   verification of completion claims; long-horizon or multi-session work uses the
   full loop with memory and lessons.
4. The autonomy loop: PLAN, ACT, VERIFY, REFLECT, then CORRECT or ADVANCE, with the
   exact CLI commands for each stage.
5. Verification doctrine: executed beats attested; `verify run` whenever anything
   executable exists (tests, builds, linters, a curl, a file check); `verify claim`
   only when nothing executable exists, and it never counts as verified.
6. Memory and lessons: what to store, when to recall (before architectural decisions,
   at session start), project vs global lessons.
7. Command quick reference matching the CLI table exactly.
8. A short MCP note listing the 15 tool names for clients using the server instead
   of the CLI, with delegation discipline for the fanout tools.

### scripts/build_variants.py

Reads `protocol/PROTOCOL.md`, writes three files at the repo root: `CLAUDE.md`,
`AGENTS.md`, `.cursorrules`. Each begins with the header line:

```
<!-- Generated from protocol/PROTOCOL.md by scripts/build_variants.py. Edit the source, then rebuild. -->
```

followed by a blank line and the protocol body verbatim. Idempotent. Zero
dependencies. Exit 0 on success with an `[OK]` line listing the files written.

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
4. Quick start A: drop-in (copy `CLAUDE.md` or `AGENTS.md` plus `scripts/mythify.py`
   into a project, run `init`).
5. Quick start B: MCP server (npm install inside `mcp-server/`, then the example
   client config; note `MYTHIFY_DIR` and `MYTHIFY_DISABLE_RUN`).
6. Quick start C: build the skill (`python3 scripts/package_skill.py`).
7. How it works: the autonomy loop, then "Verification: evidence over attestation"
   with a short example transcript showing `verify run` on a failing then passing
   test command.
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

### tests/test_interop.py

Stdlib only. Skips (unittest skip, not failure) unless `node` is on PATH and
`mcp-server/node_modules` exists. Flow:

1. Temp project dir and temp HOME. Via the CLI: `init`, `plan create "Interop goal"
   --steps '[{"title": "A", "success_criteria": "x"}]'`, `memory set color blue`.
2. Spawn `node mcp-server/src/index.js` with `MYTHIFY_DIR` pointing at the project's
   `.mythify`. Speak newline-delimited JSON-RPC 2.0 over stdio: `initialize` (accept
   whatever protocolVersion the server negotiates), `notifications/initialized`,
   then `tools/call`:
   - `plan_status` result text contains "Interop goal".
   - `memory_recall` with query "blue" finds the key color.
   - `memory_store` writes key `from_mcp`.
3. Terminate the server. Via the CLI: `memory get from_mcp` finds the entry.

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
Four engines, selected by `MYTHIFY_FANOUT_ENGINE` or auto-detected in this
order: explicit env value, else `claude-cli` if a claude binary resolves, else
`anthropic` if `ANTHROPIC_API_KEY` is set, else `command` if
`MYTHIFY_FANOUT_COMMAND` is set, else `fanout_start` refuses with a message
listing all four options.

| Engine | Mechanism | Billing | Models |
| :--- | :--- | :--- | :--- |
| `claude-cli` | Spawn `<bin> -p --output-format json --model <model> --max-turns <N>` with the assembled prompt on stdin, cwd = project root (parent of `.mythify/`). Parse the JSON output: `result` is the text, `is_error` true or a non-zero exit means failure. | Claude subscription (or whatever auth the claude CLI resolves) | Aliases `haiku`, `sonnet`, `opus`, `fable`, or any full model ID |
| `anthropic` | POST `https://api.anthropic.com/v1/messages` (anthropic-version 2023-06-01) with `max_tokens` from env. Aliases map: haiku to claude-haiku-4-5, sonnet to claude-sonnet-4-6, opus to claude-opus-4-8, fable to claude-fable-5. Join text blocks. | API key (`ANTHROPIC_API_KEY`) | Any Claude model ID |
| `openai` | POST `<MYTHIFY_FANOUT_BASE_URL>/chat/completions` with `MYTHIFY_FANOUT_API_KEY`. | Provider API key | Any model the endpoint serves |
| `command` | Run the `MYTHIFY_FANOUT_COMMAND` shell template; prompt on stdin; stdout is the output; exit 0 is success. | Whatever the command does | Anything (generic CLI agents; also used by CI to test the job machinery with no network) |

`claude-cli` binary resolution (Claude Desktop launches MCP servers with a
minimal PATH): `MYTHIFY_FANOUT_CLAUDE_BIN` if set, else `claude` on PATH, else
the first existing of `~/.claude/local/claude`, `/opt/homebrew/bin/claude`,
`/usr/local/bin/claude`. Resolution failure names the env var in the error.

`claude-cli` worker environment is curated, not inherited: `HOME`, `TERM=dumb`,
`PATH` (server PATH augmented with `/opt/homebrew/bin:/usr/local/bin`), plus
`CLAUDE_CODE_OAUTH_TOKEN` when present in the server environment, plus the
guards below. Harness variables (`CLAUDECODE`, `CLAUDE_CODE_*`,
`ANTHROPIC_BASE_URL`) are NOT passed through: a server spawned by Claude Code
inherits harness routing that breaks nested workers. Subscription auth setup
is documented as: run `claude /login` once in a terminal, or run
`claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN` in the MCP client's
`env` block. A worker failure whose output contains `Not logged in` or
`401` is reported with exactly that remediation.

### Model selection (all models, three levels)

Most specific wins: per-task `model` overrides per-job `model` overrides
`MYTHIFY_FANOUT_MODEL` overrides the engine default (`haiku` for `claude-cli`,
`claude-haiku-4-5` for `anthropic`). The same precedence applies to `engine`,
so one job may mix engines and models across tasks (for example five haiku
drafters and one sonnet reviewer; the reviewer task is still independent and
reviews material supplied in its prompt, not other tasks' outputs).

### Tools (3, total 15)

| Tool | Input schema | Behavior |
| :--- | :--- | :--- |
| `fanout_start` | `{tasks: [{title: string, prompt: string, context_paths?: string[], model?: string, engine?: string}], model?: string, engine?: string, timeout_seconds?: number}` | Validate (1 to `MYTHIFY_FANOUT_MAX_TASKS` tasks, non-empty prompts, engine resolvable, kill switch and depth guard, context files readable). Create `.mythify/fanout/<job_id>/job.json`, return the job id IMMEDIATELY, run workers in the background with a concurrency pool. Tasks must be fully independent; the description says so and says each task is a fresh model call that costs real money or subscription quota. |
| `fanout_status` | `{job_id?: string}` | Default: most recent job. Per-task lines with the step icon convention plus counts, engine, model, elapsed. If the job is marked running on disk but unknown to the in-memory registry (server restarted), mark its running tasks `interrupted` and say so. |
| `fanout_results` | `{job_id?: string, task_id?: number}` | Return outputs of completed and failed tasks (failures include the error and remediation). Per-task text in the tool result is capped at 20000 characters with a note pointing at the full output file. Warns when tasks are still running. |

Job ids: `fo-<YYYYMMDDHHMMSS>-<4 random hex>`. Worker prompt assembly:
fixed preamble (you are a delegated worker; the task is self-contained; do not
ask questions; return only the deliverable), then each context file as a
labeled fenced block, then the task prompt. `context_paths` resolve relative
to the project root (absolute allowed); total inlined context per task is
capped at `MYTHIFY_FANOUT_CONTEXT_BYTES` with an explicit truncation marker;
an unreadable path fails the task at validation time with a clear error.

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
  "timeout_seconds": 600, "last_updated": "ISO-8601",
  "tasks": [
    {"id": 1, "title": "str", "status": "pending|running|completed|failed|interrupted",
     "engine": "str", "model": "str", "started_at": "ISO-8601 or null",
     "finished_at": "ISO-8601 or null", "duration_seconds": 0.0,
     "error": "str or null", "output_file": "task-1-output.md", "output_bytes": 0}
  ]
}
```

### Configuration

| Env | Default | Meaning |
| :--- | :--- | :--- |
| `MYTHIFY_DISABLE_FANOUT` | unset | `1` disables all three tools (they refuse with an explanation). |
| `MYTHIFY_FANOUT_ENGINE` | auto | `claude-cli`, `anthropic`, `openai`, `command`. |
| `MYTHIFY_FANOUT_MODEL` | engine default | Default worker model. |
| `MYTHIFY_FANOUT_CONCURRENCY` | 3 | Parallel workers per job. |
| `MYTHIFY_FANOUT_MAX_TASKS` | 16 | Max tasks per job. |
| `MYTHIFY_FANOUT_MAX_TOKENS` | 8000 | API engines' max_tokens. |
| `MYTHIFY_FANOUT_MAX_TURNS` | 25 | claude-cli `--max-turns`. |
| `MYTHIFY_FANOUT_TIMEOUT_SECONDS` | 600 | Per-worker timeout; on expiry the worker is killed and the task fails with a timeout error. |
| `MYTHIFY_FANOUT_CONTEXT_BYTES` | 200000 | Total inlined context per task. |
| `MYTHIFY_FANOUT_CLAUDE_BIN` | resolved | Path to the claude binary. |
| `MYTHIFY_FANOUT_CLAUDE_ARGS` | empty | Extra claude args, for example `--allowedTools "Bash"`. |
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

Using the `command` engine with a deterministic local template: 15-tool set
equality; a 3-task job runs to completion and `fanout_results` returns the
outputs; `context_paths` content demonstrably reaches the worker prompt; the
kill switch refuses; the depth guard refuses; a failing command produces a
failed task with captured stderr; job.json matches the format contract field
by field.

## Versioning

This is Mythify v2.1.0 (fanout added on top of the 2.0.0 contract; the 12 core
tools and all 2.0.0 formats are unchanged). The CLI prints no version banner;
the MCP server reports 2.1.0 through its server info.
