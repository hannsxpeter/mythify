# Mythify

[![CI](https://github.com/aihxp/mythify/actions/workflows/ci.yml/badge.svg)](https://github.com/aihxp/mythify/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Give any model Mythos-class operational discipline.

Mythify improves the harness, not the underlying model. A weaker model with
disciplined planning, executed verification, and persistent memory completes more
long-horizon work than the same model without them. The patterns are distilled from
the research in [docs/research-report.md](docs/research-report.md), which carries its
own caveat: training beats prompting. Mythify closes the discipline gap, not the
capability gap.

## Components

| Component | Path | Purpose |
| :--- | :--- | :--- |
| Protocol variants | `CLAUDE.md`, `AGENTS.md`, `.cursorrules` | Drop-in rules files, generated from `protocol/PROTOCOL.md` by `scripts/build_variants.py`. |
| CLI | `scripts/mythify.py` | Zero-dependency Python 3.9+ orchestrator for plans, memory, lessons, verification, and reflection. |
| MCP server | `mcp-server/` | Node 18+ server exposing the same state directory through 15 MCP tools, including parallel delegation (fanout). |
| Skill | `skills/mythify/` | Manus-style skill package; `scripts/package_skill.py` builds `dist/mythify.skill`. |

All components read and write the same per-project `.mythify/` state directory, so
they interoperate: a plan created by the CLI is visible to the MCP server and vice
versa.

Each quick start begins from a local clone:

```bash
git clone https://github.com/aihxp/mythify.git
cd mythify
```

## Quick start A: drop-in

Copy a protocol variant and the CLI into your project, then initialize a workspace:

```bash
cp CLAUDE.md /path/to/your/project/          # or AGENTS.md, or .cursorrules
mkdir -p /path/to/your/project/scripts
cp scripts/mythify.py /path/to/your/project/scripts/
cd /path/to/your/project
python3 scripts/mythify.py init
```

The protocol file steers the agent; the CLI gives it durable plans, memory, lessons,
and executed verification.

## Quick start B: MCP server

Install dependencies, then point your MCP client at the server:

```bash
cd mcp-server
npm install
```

Adapt `mcp-server/mcp-config.example.json` for your client:

```json
{
  "mcpServers": {
    "mythify": {
      "command": "node",
      "args": ["/absolute/path/to/mythify/mcp-server/src/index.js"],
      "env": {
        "MYTHIFY_DIR": "/absolute/path/to/your/project/.mythify"
      }
    }
  }
}
```

Environment variables:

- `MYTHIFY_DIR` pins the state directory (created on demand). Without it, the server
  walks up from its working directory to find a `.mythify/` folder, or lazily creates
  `<cwd>/.mythify` on first write.
- `MYTHIFY_DISABLE_RUN=1` makes the `verify_run` tool refuse to execute commands (it
  records nothing). Use this in environments where shell execution is not allowed.

## Quick start C: build the skill

```bash
python3 scripts/package_skill.py
```

This zips `skills/mythify/` into `dist/mythify.skill` with `SKILL.md` at the zip root
and `references/` beside it, ready to import into Manus or any skill-compatible
agent. If you would rather not build it yourself, a prebuilt `mythify.skill` is
attached to each GitHub release at
[https://github.com/aihxp/mythify/releases](https://github.com/aihxp/mythify/releases).

## How it works

### The autonomy loop

For any non-trivial task the protocol runs a disciplined loop:

```
PLAN -> ACT -> VERIFY -> REFLECT -> (CORRECT or ADVANCE)
```

- PLAN: decompose the goal into verifiable steps (`plan create`, `plan add-step`).
- ACT: execute the current step with normal tools, one step at a time.
- VERIFY: run an executable check (`verify run`); fall back to `verify claim` only
  when nothing executable exists.
- REFLECT: record what happened and what comes next (`reflect`).
- CORRECT or ADVANCE: mark the step `failed` with evidence and retry, or mark it
  `completed` with evidence and move to the next pending step (`step`).

Ceremony is proportional. A trivial task (single edit or question) uses no protocol
commands. A multi-step single-session task uses a plan plus executed verification of
completion claims. Long-horizon or multi-session work uses the full loop with memory
and lessons.

### Verification: evidence over attestation

Completion claims are checked by executing commands and reading exit codes, not by
self-report. A failing check, a fix, and a passing re-run look like this:

```
$ python3 scripts/mythify.py verify run "python3 -m unittest discover -s tests" --claim "All tests pass"
[FAIL] UNVERIFIED: All tests pass (exit 1, 0.84s)
--- stderr (tail) ---
FAIL: test_step_requires_evidence (test_mythify.StepTests)
AssertionError: 1 != 0

$ # fix the bug, then re-run
$ python3 scripts/mythify.py verify run "python3 -m unittest discover -s tests" --claim "All tests pass"
[OK] VERIFIED: All tests pass (exit 0, 0.79s)
```

`verify run` exits 0 when the command verified and 2 when it did not, so the agent
(and your scripts) can branch on the result. Self-reported claims go through
`verify claim`; they are recorded and displayed as second-class evidence:

```
[WARN] ATTESTED: <claim> (self-reported, not machine-checked; prefer verify run)
```

An attested entry is never marked verified.

## State layout

Each project owns a `.mythify/` directory:

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

The only global state is the cross-project lessons store in `~/.mythify/lessons/`.
All JSON writes are atomic, and corrupt files are renamed aside with a `[WARN]`
instead of crashing.

## CLI command reference

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

## MCP tool reference

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
| `fanout_start` | `{tasks: [{title: string, prompt: string, context_paths?: string[], model?: string, engine?: string}], model?: string, engine?: string, timeout_seconds?: number}` | Validate the job (1 to `MYTHIFY_FANOUT_MAX_TASKS` tasks, non-empty prompts, engine resolvable, kill switch and depth guard, context files readable), create `.mythify/fanout/<job_id>/job.json`, return the job id immediately, and run the workers in the background with a concurrency pool. Tasks must be fully independent; each task is a fresh model call that costs real money or subscription quota. |
| `fanout_status` | `{job_id?: string}` | Default: most recent job. Per-task lines with the step icon convention plus counts, engine, model, elapsed. Tasks left running by a restarted server are marked `interrupted` and reported as such. |
| `fanout_results` | `{job_id?: string, task_id?: number}` | Return outputs of completed and failed tasks (failures include the error and remediation). Per-task text is capped at 20000 characters with a note pointing at the full output file. Warns when tasks are still running. |

## Parallel delegation (fanout)

The MCP server can fan work out to parallel sub-workers. The orchestrating
model declares a one-shot task list with `fanout_start`, and the server does
the orchestration: spawning, sequencing, and collecting. This deliberately
avoids turn-by-turn coordination, which weaker models cannot sustain. Each
worker is one fresh model invocation with no memory of the conversation, so
every task prompt must stand alone, with files supplied through
`context_paths`. Worker outputs are material, not verification: merge them,
then verify the merged work with `verify_run`. Fanout is MCP-only; the CLI
does not implement it (a CLI host has shell access and usually its own
parallelism).

### Engines

Four engines run the workers. The headline option needs no API key: workers
run through the `claude` CLI and bill against your existing Claude
subscription.

| Engine | Mechanism | Billing | Models |
| :--- | :--- | :--- | :--- |
| `claude-cli` | Spawns the `claude` binary in print mode, one process per task, with the assembled prompt on stdin. | Claude subscription (or whatever auth the claude CLI resolves) | Aliases `haiku`, `sonnet`, `opus`, `fable`, or any full model ID |
| `anthropic` | POST `https://api.anthropic.com/v1/messages`. | API key (`ANTHROPIC_API_KEY`) | Any Claude model ID |
| `openai` | POST `<MYTHIFY_FANOUT_BASE_URL>/chat/completions`. | Provider API key (`MYTHIFY_FANOUT_API_KEY`) | Any model the endpoint serves |
| `command` | Runs the `MYTHIFY_FANOUT_COMMAND` shell template; prompt on stdin, stdout is the output, exit 0 is success. | Whatever the command does | Anything (generic CLI agents) |

The engine is set by `MYTHIFY_FANOUT_ENGINE`, or auto-detected in this order:
`claude-cli` if a claude binary resolves, else `anthropic` if
`ANTHROPIC_API_KEY` is set, else `command` if `MYTHIFY_FANOUT_COMMAND` is set,
else `fanout_start` refuses with a message listing all four options.

### Model selection

Three levels, most specific wins: per-task `model` overrides per-job `model`
overrides `MYTHIFY_FANOUT_MODEL` overrides the engine default (`haiku` for
`claude-cli`, `claude-haiku-4-5` for `anthropic`). The same precedence applies
to `engine`, so one job can mix models and engines across tasks. A typical mix
is cheap haiku drafters plus a sonnet reviewer; the reviewer is still an
independent task that reviews material supplied in its own prompt, not the
other tasks' outputs:

```json
{
  "model": "haiku",
  "tasks": [
    {"title": "Draft install section", "prompt": "Write the install section of the user guide. Use only the outline below.", "context_paths": ["docs/outline.md"]},
    {"title": "Draft config section", "prompt": "Write the configuration section of the user guide. Use only the outline below.", "context_paths": ["docs/outline.md"]},
    {"title": "Review outline for gaps", "prompt": "List every topic the outline below misses that a user guide must cover, ranked by importance.", "model": "sonnet", "context_paths": ["docs/outline.md"]}
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

### Subscription auth for claude-cli workers

Workers spawned by the server need credentials the same way your terminal
does. Either:

- run `claude /login` once in a terminal (workers inherit the stored
  credential through `HOME`), or
- run `claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN` in the MCP
  client's `env` block.

A worker failure whose output contains `Not logged in` or `401` is reported
with exactly that remediation.

### Caveats

- Workers on the API engines (`anthropic`, `openai`) are text-only: one prompt
  in, one completion out. They cannot run tools or read files beyond the
  context inlined into their prompt.
- `claude-cli` workers run Claude Code non-interactively and get its default
  tool sandbox; grant more with `MYTHIFY_FANOUT_CLAUDE_ARGS`, for example
  `--allowedTools "Bash"`.
- Background workers live in the MCP server process. If the client disconnects
  or the server dies, running tasks die with it, and `fanout_status` reports
  them as interrupted afterward.
- Depth limit of one: workers are spawned with `MYTHIFY_FANOUT_DEPTH=1` and
  `MYTHIFY_DISABLE_FANOUT=1` in their environment, so a worker cannot fan out
  again.

## Compatibility

| Environment | Integration |
| :--- | :--- |
| Claude Code | Copy `CLAUDE.md` to the project root, or register the MCP server. |
| Cursor | Copy `.cursorrules` to the project root. |
| Windsurf | Copy `AGENTS.md` to the project root. |
| VS Code Copilot | Copy `AGENTS.md` to the project root. |
| Claude Desktop | Register the MCP server via `mcp-server/mcp-config.example.json`. |
| Manus | Import `dist/mythify.skill` (see quick start C). |
| Any CLI agent | Use `scripts/mythify.py` directly, paired with any protocol variant. |
| Custom MCP clients | Connect to `mcp-server/src/index.js` over stdio. |

For step-by-step Claude Desktop and Claude Code setup, including running Mythify
on smaller models like Haiku, see
[docs/claude-integrations.md](docs/claude-integrations.md).

## Development

Run the Python test suite (stdlib unittest, no dependencies):

```bash
python3 -m unittest discover -s tests -v
```

Run the MCP server smoke test:

```bash
cd mcp-server && npm ci && npm test
```

`tests/test_interop.py` exercises the CLI and the MCP server against the same state
directory; it skips automatically when `node` is not on PATH or
`mcp-server/node_modules` is missing.

## Limitations

- No published npm package yet. You can get the code by cloning the repository or
  downloading a GitHub release, but the MCP server is still configured by local
  absolute path; there are no `npx` instructions because nothing is published to npm.
- Evals have not been run. The claims in this README and in the protocol are design
  rationale, not measured results.
- Protocol adherence varies by model strength. Weaker models follow the discipline
  less reliably, and the gains shrink accordingly.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the
development workflow, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community
expectations, and [SECURITY.md](SECURITY.md) for how to report vulnerabilities. Two
repository rules are non-negotiable:

1. [docs/design.md](docs/design.md) is the contract for all CLI, MCP, and on-disk
   interfaces. Behavior changes start there.
2. The generated protocol variants (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`) are
   never edited by hand. Edit `protocol/PROTOCOL.md` and regenerate them with
   `scripts/build_variants.py`.

## License

MIT. See [LICENSE](LICENSE).
