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
| CLI | `scripts/mythify.py` | Zero-dependency Python 3.9+ orchestrator for plans, memory, lessons, outcome loops, verification, and reflection. |
| MCP server | `mcp-server/` | Node 18+ server exposing the same state directory through 28 MCP tools, including task classification, host model switch state, provider probes, local model runs, host CLI probes, bounded host CLI worker runs, execution probes, lifecycle probes, outcome loops, and parallel delegation (fanout). |
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
mkdir -p /path/to/your/project/protocol
cp scripts/mythify.py /path/to/your/project/scripts/
cp protocol/operation-registry.json /path/to/your/project/protocol/
cd /path/to/your/project
python3 scripts/mythify.py protocol check CLAUDE.md
python3 scripts/mythify.py init
```

The protocol file steers the agent; the CLI gives it durable plans, memory, lessons,
and executed verification. `protocol check` confirms the copied protocol file and
CLI came from the same source protocol before you start relying on them.

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
- `MYTHIFY_DISABLE_RUN=1` makes execution refuse: both the CLI `verify run` command
  and the MCP `verify_run` tool execute nothing and record nothing (the CLI exits 2,
  the unverified code). Use this in environments where shell execution is not allowed.
- `MYTHIFY_REQUIRE_VERIFIED_STEP=1` is an opt-in gate: marking a step `completed`
  (CLI `step` or MCP `plan_update_step`) then requires a recorded passing `verify run`
  since the step started, not just a non-empty result. Default off keeps the existing
  behavior.
- New verification records include active step context when a step is
  `in_progress`: `plan`, `step_id`, `step_title`, and `step_status`. Null
  context and older records remain compatible with existing readers.

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
commands. A focused low-risk bugfix or test task can use the fast profile: skip plan
state, do the focused work, then record `verify run` before claiming completion. A
multi-step single-session task uses a plan plus executed verification of completion
claims. Long-horizon or multi-session work uses the full loop with memory and lessons.

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

## Outcome loops

Use an outcome loop when the user gives a result more than a recipe: "make the
benchmark pass", "produce the best draft under this metric", or "keep trying
until the verifier is green." The host chat stays the cockpit. Mythify records
the target, verifier, optional metric, and iteration budget, then tells the host
whether to retry, stop, or report success after each bounded attempt.

```bash
python3 scripts/mythify.py outcome start \
  "Improve parser reliability" \
  --success "parser regression tests pass" \
  --verify "python3 -m unittest tests/test_parser.py" \
  --metric "python3 scripts/score_parser.py" \
  --max-iterations 3
```

The host then makes one focused attempt and checks the result:

```bash
python3 scripts/mythify.py outcome check --notes "added empty input guard"
```

`outcome check` exits 0 when the verifier and metric pass, and 2 when the loop
needs another attempt or has exhausted the budget. Results are durable in
`.mythify/outcomes/<name>/goal.json` and `iterations.jsonl`, so Codex Desktop,
Claude Desktop, Cursor Desktop, and the CLI can all resume the same loop
through the same MCP state.

## State layout

Each project owns a `.mythify/` directory:

```
.mythify/
|-- memory.json
|-- host-model.json              optional recorded host chat model request
|-- outcomes/
|   |-- active                   text file containing the active outcome slug
|   `-- <slug>/
|       |-- goal.json
|       `-- iterations.jsonl
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

The only global state is the cross-project lessons store in `~/.mythify/lessons/`.
All JSON writes are atomic, and corrupt files are renamed aside with a `[WARN]`
instead of crashing.

`protocol/operation-registry.json` backs the shared memory operation contract.
The Python CLI and MCP server both read it for memory categories, the default
category, and the guarded `memory_clear` refusal text.

Use `logs compact` to keep long-lived workspaces readable. It archives the raw
top-level verification and reflection logs under `.mythify/logs/archive/`, then
keeps the most recent valid records in the active logs. Compaction is
maintenance, not verification evidence.

## CLI command reference

| Command | Behavior | Exit code |
| :--- | :--- | :--- |
| `init` | Create `./.mythify` with subdirectories and empty memory.json. If already inside a workspace, print `[WARN]` and exit 0. | 0 |
| `protocol check [PATH ...] [--json]` | Verify copied protocol files match the CLI's embedded source protocol hash. With no paths, check source repo protocol when present and local `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` files. | 0 if every checked file matches; 1 on missing metadata or drift |
| `status` | Orientation: active plan with step icons, next pending step and its criteria, one-line counts (memory, lessons, verifications, reflections). | 0; 1 if no workspace |
| `classify TASK [--json] [--triage never\|auto\|always] [--platform auto\|codex-desktop\|claude-desktop\|cursor-desktop] [--effort auto\|low\|medium\|high] [--speed auto\|standard\|fast] [--session-model MODEL] [--spawn-ceiling auto\|lower_only\|same_or_lower\|allow_stronger] [--reviewer-strength auto\|same_or_lower\|allow_stronger]` | Classify a task before planning. Returns task type, risk, ambiguity, ceremony level, execution profile, verification strategy, fanout recommendation, fast model triage fit, model policy, and task-based host recommendation. `--triage auto` runs a local fast model only when the gate is recommended or required. | 0 |
| `host-model switch MODEL [--platform P] [--current-model M] [--thinking E] [--speed S] [--reason TEXT] [--json]` | Record a requested host chat model switch in `.mythify/host-model.json`, including `host_capability` and `switch_result`. This updates Mythify session model policy; the host still owns the actual current chat model unless a future adapter confirms it. | 0; 1 if no workspace |
| `host-model status [--json]` | Show the recorded host model switch, capability fields, and current-chat confirmation status, if any. | 0; 1 if no workspace |
| `host-model clear [--json]` | Remove the recorded host model switch. | 0; 1 if no workspace |
| `outcome start GOAL --success TEXT --verify COMMAND [--metric COMMAND] [--max-iterations N] [--allowed-paths CSV] [--visibility MODE] [--name NAME] [--json]` | Start a supervised outcome loop, set it active, and record the verifier, optional metric, scope hints, visibility policy, and retry budget. | 0; 1 if no workspace or invalid budget |
| `outcome check [NAME] [--notes TEXT] [--timeout N] [--json]` | Run the verifier and optional metric for the active or named outcome, append an iteration record, and return the next action. | 0 if verified, 2 if still unmet or failed, 1 if not found |
| `outcome status [NAME] [--json]` | Show outcome status, verifier, metric, iteration budget, and latest next action. | 0; 1 if not found |
| `outcome results [NAME] [--json]` | Show every recorded verifier iteration plus final status. | 0 if succeeded, 2 otherwise, 1 if not found |
| `outcome stop [NAME] --reason TEXT [--json]` | Mark an active or named outcome stopped and clear the active pointer when it matches. | 0; 1 if not found |
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
| `logs compact [--keep N] [--dry-run] [--json]` | Archive raw top-level verification and reflection logs, then keep the most recent valid records in active logs. Default keep is 1000. `--dry-run` writes nothing. | 0; 1 if keep is invalid |
| `verify run COMMAND [--claim TEXT] [--timeout N]` | Execute COMMAND through the shell, capture exit code, duration, and output tails, append an executed record, print the verdict. Default timeout 300 seconds. | 0 if verified, 2 if unverified |
| `verify claim CLAIM EVIDENCE` | Append an attested record and print the `[WARN] ATTESTED` line. | 0 |
| `reflect [JSON]` or `reflect --action A --outcome O --observation OBS --next N [--root-cause R] [--lesson L]` | Record a structured reflection. Required keys: action, outcome (enum success, partial, failure), observation, next. A provided lesson is auto-recorded as a project lesson tagged `auto-reflected`. JSON positional takes precedence over flags. Missing keys or bad outcome: `[FAIL]`, exit 1. | 0 |
| `summary` | Full session report: plans and progress, memory count, project and global lesson counts, verification stats (executed passed, executed failed, attested count), reflection count. | 0 |

## MCP tool reference

| Tool | Input schema | Behavior |
| :--- | :--- | :--- |
| `classify_task` | `{task: string, format?: enum(text, json), triage?: enum(never, auto, always), triage_engine?: enum(claude-cli, codex-cli, cursor-agent, command), triage_model?: string, triage_timeout_seconds?: number, platform?: enum(auto, unknown, codex-desktop, codex-cli, claude-desktop, claude-code, cursor-desktop, cursor-agent), effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast), session_model?: string, spawn_ceiling?: enum(auto, lower_only, same_or_lower, allow_stronger), reviewer_strength?: enum(auto, same_or_lower, allow_stronger)}` | Classify a task before planning. With `triage: auto`, runs one fast local model pass only when the gate is recommended or required. The JSON result includes `execution_profile` for protocol overhead and `model_policy` for host model, task-based host recommendation, spawned engine, spawned model, per-role effort, speed, spawn ceiling, and reviewer strength opt-in. |
| `host_model_switch` | `{action?: enum(switch, status, clear), platform?: enum(auto, unknown, codex-desktop, codex-cli, claude-desktop, claude-code, cursor-desktop, cursor-agent), target_model?: string, current_model?: string, thinking?: enum(auto, low, medium, high, xhigh, max), speed?: enum(auto, standard, fast), reason?: string, format?: enum(text, json)}` | Record, show, or clear a requested host chat model switch. `switch` writes `.mythify/host-model.json`, returns host-specific switch guidance, `host_capability`, and `switch_result`, and makes later `classify_task` and `fanout_start` calls use the recorded model as the session model when no explicit or env session model is supplied. |
| `provider_probe` | `{provider?: enum(generic-openai-compatible, ollama, lm-studio, llama-cpp, vllm), base_url?: string, model?: string, check?: enum(models, chat, both), api_key_env?: string, timeout_seconds?: number, prompt?: string, format?: enum(text, json)}` | Probe an OpenAI-compatible provider through `/v1/models` and optionally `/v1/chat/completions`. Generic defaults to `MYTHIFY_OPENAI_COMPAT_BASE_URL`, `MYTHIFY_OPENAI_COMPAT_MODEL`, and `MYTHIFY_OPENAI_COMPAT_API_KEY`; `provider: "ollama"` defaults to `MYTHIFY_OLLAMA_BASE_URL` or `http://localhost:11434/v1`; `provider: "lm-studio"` defaults to `MYTHIFY_LM_STUDIO_BASE_URL` or `http://localhost:1234/v1`; `provider: "llama-cpp"` defaults to `MYTHIFY_LLAMA_CPP_BASE_URL` or `http://localhost:8080/v1`; `provider: "vllm"` defaults to `MYTHIFY_VLLM_BASE_URL` or `http://localhost:8000/v1`. Local profiles use their provider-specific model env vars and send no auth header by default. Probe output is material, not verification evidence. |
| `local_model_run` | `{provider?: enum(generic-openai-compatible, ollama, lm-studio, llama-cpp, vllm), role?: enum(reader, triage), base_url?: string, model?: string, prompt: string, api_key_env?: string, timeout_seconds?: number, max_tokens?: number, format?: enum(text, json)}` | Run a reader or triage prompt against a localhost OpenAI-compatible provider. `provider: "ollama"`, `provider: "lm-studio"`, `provider: "llama-cpp"`, and `provider: "vllm"` use local profile defaults. Model output is material, not verification evidence, and the tool writes no Mythify state. |
| `host_cli_probe` | `{host?: enum(kimi-code, opencode, antigravity), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Kimi Code, OpenCode, or Antigravity CLI availability by running version and help commands only. Probe output is material, not verification evidence, and does not execute a prompt or start workers. |
| `host_cli_run` | `{host?: enum(kimi-code, opencode), bin?: string, prompt: string, cwd?: string, timeout_seconds?: number, model?: string, agent?: string, format?: enum(text, json)}` | Run a bounded Kimi Code or OpenCode non-interactive prompt. Worker output is material, not verification evidence, and the tool writes no Mythify state. |
| `execution_probe` | `{adapter?: enum(google-colab-cli), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Google Colab CLI availability by running version and help commands only. Probe output is material, not verification evidence, and does not provision runtimes, request accelerators, or execute jobs. |
| `lifecycle_probe` | `{adapter?: enum(google-agents-cli, google-adk-cli), bin?: string, timeout_seconds?: number, format?: enum(text, json)}` | Probe Google Agents CLI or ADK CLI availability by running version, help, and eval-help commands only. Probe output is material, not verification evidence, and does not scaffold, run evals, deploy, publish, mutate cloud resources, or write project state. |
| `outcome_start` | `{goal: string, success: string, verify_command: string, metric_command?: string, max_iterations?: number, allowed_paths?: string[], visibility?: enum(auto, quiet, summary, verbose, threaded), name?: string, format?: enum(text, json)}` | Start a supervised outcome loop and set it active. The host agent makes bounded attempts between checks; Mythify records evidence and next action. |
| `outcome_check` | `{name?: string, notes?: string, timeout_seconds?: number, format?: enum(text, json)}` | Run the verifier and optional metric, append an iteration, record executed verification evidence, and return success, retry, or budget-exhausted guidance. Refuses when `MYTHIFY_DISABLE_RUN=1`. |
| `outcome_status` | `{name?: string, format?: enum(text, json)}` | Show active or named outcome status, verifier, metric, iteration budget, and next action. |
| `outcome_results` | `{name?: string, format?: enum(text, json)}` | Show all recorded verifier iterations and final outcome state. |
| `outcome_stop` | `{name?: string, reason: string, format?: enum(text, json)}` | Mark an outcome stopped and clear the active pointer when it matches. |
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
| `fanout_start` | `{tasks: [{title: string, prompt: string, context_paths?: string[], role?: enum(worker, reviewer), model?: string, engine?: string, effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast)}], purpose?: string, model?: string, engine?: string, effort?: enum(auto, low, medium, high), speed?: enum(auto, standard, fast), visibility?: enum(auto, quiet, summary, verbose, threaded), session_model?: string, spawn_ceiling?: enum(auto, lower_only, same_or_lower, allow_stronger), reviewer_allow_stronger?: boolean, timeout_seconds?: number}` | Validate the job (1 to `MYTHIFY_FANOUT_MAX_TASKS` tasks, non-empty prompts, engine resolvable, kill switch and depth guard, context files readable, spawned model does not exceed the ceiling unless a reviewer-specific opt-in applies), create `.mythify/fanout/<job_id>/job.json`, return the job id immediately, and run the workers in the background with a concurrency pool. Tasks must be fully independent; each task is a fresh model call that costs real money or subscription quota. Visibility defaults to summary unless `visibility`, `purpose`, or task prompts request quiet, verbose, or threaded reporting. |
| `fanout_status` | `{job_id?: string}` | Default: most recent job. Per-task lines with the step icon convention plus counts, engine, model, effort, speed, elapsed, and visibility. Quiet jobs show aggregate progress and failures only. Tasks left running by a restarted server are marked `interrupted` and reported as such. |
| `fanout_results` | `{job_id?: string, task_id?: number}` | Return outputs of completed and failed tasks (failures include the error and remediation). Per-task text is capped at 20000 characters with a note pointing at the full output file. Warns when tasks are still running. |

## Fast model triage

`classify` always runs a deterministic classifier first. That output includes
`execution_profile: direct | fast | standard | full` and
`model_triage: skip | optional | recommended | required`. The default is to
stop at deterministic classification, so it is fast and free:

```bash
python3 scripts/mythify.py classify "make this better" --json
```

When you want a cheap second read, opt in:

```bash
python3 scripts/mythify.py classify "make this better" --json --triage auto
```

`--triage auto` runs one fast model only when the gate is `recommended` or
`required`; `--triage always` forces it. MCP clients use the same options on
`classify_task`.

Execution profiles choose protocol overhead. `direct` means answer or make the
single reversible edit with no protocol state. `fast` means skip plan state for
focused low-risk work, but still use `verify run`. `standard` means create a plan
with verifiable steps. `full` means use plan, memory, step updates, verification,
reflection, and summary.

Classification also returns a `model_policy` object so Desktop clients do not
have to guess which model setting applies where:

- `provider_defaults`: advisory provider defaults for session, triage, reader,
  fanout worker, reviewer, and verifier roles. These fields do not route work
  by themselves. They make the default provider posture explicit and use
  `fallback_policy: "no_implicit_cross_provider_fallback"`.
  `provider_defaults.api_provider_contract` lists metadata-supported hosted
  API providers, currently OpenAI, Anthropic, and hosted OpenAI-compatible
  endpoints. It records auth env names, billing posture, timeout fields, cost
  metadata fields, pricing URLs, and `execution_enabled: false` until a later
  slice adds explicit hosted execution.
- `session`: the active conversation model is host-selected. Codex Desktop,
  Claude Desktop, Cursor Desktop, and CLI hosts own that dropdown or command
  flag. `host_model_switch` and `host-model switch` can record the intended
  model in Mythify state, but the host still applies the current chat switch.
  `session.recommendation` maps the classified prompt to host settings:
  target profile, target model, thinking, speed, and whether to keep,
  downgrade, upgrade, or set the host model.
- `triage`: the optional fast problem-framing worker, including spawned engine,
  spawned model policy, effort, timeout, and sandbox.
- `reader`: optional read-only model role for inspecting supplied material.
  It defaults to the localhost OpenAI-compatible provider path and can use the
  explicit Ollama profile. It returns material, not verification evidence.
- `fanout_worker`: the default policy for independent spawned workers.
  Includes the recommended chat visibility mode, defaulting to `summary`
  unless the prompt asks for quiet, verbose, or threaded reporting.
- `reviewer`: whether a separate review worker is useful and what effort it
  should use. Reviewers default to same-or-lower than the initiating session;
  `--reviewer-strength allow_stronger` and MCP
  `reviewer_strength: "allow_stronger"` record classifier policy. Actual
  fanout still requires `role: "reviewer"` plus per-job
  `reviewer_allow_stronger: true` before a reviewer can exceed the session
  model without the broader `spawn_ceiling: "allow_stronger"` escape hatch.
- `verifier`: always command-first. Executable `verify_run` evidence beats
  model judgment.

Use `--platform codex-desktop`, `--platform claude-desktop`, or
`--platform cursor-desktop` when the host is known. Use `--effort` and
`--speed` only as policy preferences for spawned roles; they do not override
the current desktop chat model.

Task-based host recommendations keep cheap prompts cheap. For example,
`classify "what is 1 + 1?" --json --platform codex-desktop --session-model gpt-5.5`
returns a `session.recommendation` to downgrade to the fast profile,
`gpt-5.4-mini`, low thinking, and fast speed. A research prompt such as
`make me a research paper about memory consolidation in LLM agents` returns
the strong profile, `gpt-5.5`, high thinking, and standard speed on Codex
Desktop. Claude maps the same profiles to `haiku`, `sonnet`, and `opus`;
Cursor maps them to the local `gpt-5.3-codex` variants.

Use `--session-model` or `MYTHIFY_SESSION_MODEL` when the host can tell
Mythify the initiating model. If neither is set, Mythify falls back to the
recorded `.mythify/host-model.json` target from `host_model_switch` or
`host-model switch`. Mythify classifies that name into a rough tier
(`small`, `fast`, `standard`, `strong`, `frontier`, or `unknown`) and applies
`--spawn-ceiling` or `MYTHIFY_SPAWN_CEILING`. The default ceiling is
`same_or_lower`: spawned workers should be equivalent to or lower than the
session model. `allow_stronger` is the broad global opt-in. Prefer the
reviewer-scoped opt-ins when only an independent reviewer should be stronger.

Provider defaults can be made explicit with environment variables:
`MYTHIFY_ROLE_SESSION_PROVIDER`, `MYTHIFY_ROLE_TRIAGE_PROVIDER`,
`MYTHIFY_ROLE_READER_PROVIDER`, `MYTHIFY_ROLE_WORKER_PROVIDER`,
`MYTHIFY_ROLE_REVIEWER_PROVIDER`, and `MYTHIFY_ROLE_VERIFIER_PROVIDER`.
Invalid values are ignored and reported in `model_policy.provider_defaults`.

Fast triage engines are local-first and do not require API keys:

| Env or option | Default | Meaning |
| :--- | :--- | :--- |
| `--triage-engine`, `MYTHIFY_TRIAGE_ENGINE` | auto | `claude-cli`, `codex-cli`, `cursor-agent`, or `command`. |
| `--triage-model`, `MYTHIFY_TRIAGE_MODEL` | engine default | Fast model. `claude-cli` defaults to `haiku`; Codex and Cursor use their local defaults unless set. |
| `MYTHIFY_TRIAGE_COMMAND` | unset | Shell command for the `command` engine. Reads the triage prompt on stdin and must print JSON. |
| `MYTHIFY_TRIAGE_CLAUDE_BIN`, `MYTHIFY_TRIAGE_CODEX_BIN`, `MYTHIFY_TRIAGE_CURSOR_BIN` | resolved | Override local CLI binary paths. Fanout binary env vars are used as fallbacks. |

The triage model must return JSON with fields such as `primary_type`,
`secondary_types`, `hidden_questions`, `verification_plan`, `fanout_plan`, and
`recommended_first_step`. Mythify records the parsed JSON in the classification
result; it does not treat that model output as verification.

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

Subagent visibility defaults to `summary`. In the main chat, the host should
show worker titles, status counts, and notable findings without dumping every
worker transcript. Use `visibility: "quiet"` for background-only work,
`visibility: "verbose"` when the user asks to see detailed worker output, and
`visibility: "threaded"` when the user asks for visible worker chats. Threaded
mode is a host request: Mythify records it, but visible Codex, Claude, or
Cursor threads require native host support. `visibility: "auto"` or an omitted
visibility field infers the mode from `purpose` and task prompts, falling back
to `summary`.

### Engines

Six engines run the workers. The headline options need no API keys: workers
run through local CLIs that use the subscription login you already have in
your terminal.

| Engine | Mechanism | Billing | Models |
| :--- | :--- | :--- | :--- |
| `claude-cli` | Spawns the `claude` binary in print mode, one process per task, with the assembled prompt on stdin. | Claude subscription (or whatever auth the claude CLI resolves) | Aliases `haiku`, `sonnet`, `opus`, `fable`, or any full model ID |
| `codex-cli` | Spawns `codex exec` non-interactively, with the assembled prompt on stdin and the final message captured from `--output-last-message`. | Codex CLI login, usually your ChatGPT/Codex subscription or configured Codex auth | Any model your Codex CLI supports; omit the model to use your Codex default |
| `cursor-agent` | Spawns `cursor-agent --print` (or `cursor agent --print`) against a temporary prompt file under `.mythify/`. | Cursor Agent login, usually your Cursor subscription or configured Cursor auth | Any model your Cursor Agent account exposes; omit the model to use your Cursor default |
| `anthropic` | POST `https://api.anthropic.com/v1/messages`. | API key (`ANTHROPIC_API_KEY`) | Any Claude model ID |
| `openai` | POST `<MYTHIFY_FANOUT_BASE_URL>/chat/completions`. | Provider API key (`MYTHIFY_FANOUT_API_KEY`) | Any model the endpoint serves |
| `command` | Runs the `MYTHIFY_FANOUT_COMMAND` shell template; prompt on stdin, stdout is the output, exit 0 is success. | Whatever the command does | Anything (generic CLI agents) |

The engine is set by `MYTHIFY_FANOUT_ENGINE`, or auto-detected in this order:
`claude-cli` if a claude binary resolves, else `codex-cli` if a codex binary
resolves, else `cursor-agent` if Cursor Agent resolves, else `anthropic` if
`ANTHROPIC_API_KEY` is set, else `command` if `MYTHIFY_FANOUT_COMMAND` is set,
else `fanout_start` refuses with a message listing all six options. `openai`
is available when selected explicitly with `MYTHIFY_FANOUT_ENGINE=openai`.

### Model selection

Three levels, most specific wins: per-task `model` overrides per-job `model`
overrides `MYTHIFY_FANOUT_MODEL` overrides the engine default (`haiku` for
`claude-cli`, `claude-haiku-4-5` for `anthropic`, and your local CLI default
for `codex-cli` and `cursor-agent`). The same precedence applies to `engine`,
so one job can mix models and engines across tasks. A typical mix is cheap
haiku drafters plus a sonnet reviewer; the reviewer is still an independent
task that reviews material supplied in its own prompt, not the other tasks'
outputs:

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

### Spawn ceiling

Fanout also accepts `session_model` and `spawn_ceiling`. The session model can
come from the tool call, `MYTHIFY_SESSION_MODEL`, or the recorded
`.mythify/host-model.json` target; the ceiling can come from the tool call or
`MYTHIFY_SPAWN_CEILING`. The default ceiling is `same_or_lower`.

When Mythify can classify both the initiating model and the spawned model into
tiers, `fanout_start` refuses a stronger spawned model unless the ceiling is
`allow_stronger`. A narrower path exists for review tasks: a task with
`role: "reviewer"` may exceed the session under `same_or_lower` only when the
job also sets `reviewer_allow_stronger: true`. This keeps cheap triage and
mechanical fanout from accidentally exceeding the orchestrating model. Unknown
local CLI defaults are recorded as `uncheckable`; Mythify does not guess what
Codex Desktop, Claude, or Cursor currently maps a blank model setting to.

```json
{
  "session_model": "haiku",
  "spawn_ceiling": "same_or_lower",
  "tasks": [
    {"title": "Scan docs", "prompt": "Find missing sections.", "model": "haiku"}
  ]
}
```

To deliberately use a stronger reviewer:

```json
{
  "session_model": "haiku",
  "reviewer_allow_stronger": true,
  "tasks": [
    {"title": "High-risk review", "role": "reviewer", "prompt": "Review this patch for bugs.", "model": "sonnet"}
  ]
}
```

That reviewer-scoped opt-in applies only to tasks with `role: "reviewer"` and
does not weaken the same-or-lower default for ordinary worker tasks.

### Effort selection

Effort is tracked separately from model. Per-task `effort` overrides per-job
`effort`, which overrides `MYTHIFY_FANOUT_EFFORT`, which falls back to a
model-derived default (`low` for small or fast model names, `high` for large
or heavy model names, otherwise `medium`). The resolved effort and its source
are stored in `job.json`, shown in `fanout_status`, and inserted into each
worker prompt as `Requested effort: <level>`.

This is intentionally platform-aware but conservative. Codex, Claude, and
Cursor expose model and reasoning controls differently, so Mythify records the
policy and passes effort through the worker prompt. Platform-specific flags can
still be supplied through `MYTHIFY_FANOUT_CODEX_ARGS`,
`MYTHIFY_FANOUT_CLAUDE_ARGS`, or `MYTHIFY_FANOUT_CURSOR_ARGS`.

### Speed selection

Speed is tracked separately from effort. Per-task `speed` overrides per-job
`speed`, which overrides `MYTHIFY_FANOUT_SPEED`, which otherwise stays `auto`.
`auto` preserves the platform default.

Platform mapping:

- `codex-cli`: `speed: "fast"` adds `service_tier = "fast"` and
  `features.fast_mode = true`; `speed: "standard"` adds
  `features.fast_mode = false`.
- `claude-cli`: `effort` is passed as `--effort`; `speed` is recorded and
  included in the worker prompt because Claude Code does not expose a separate
  speed flag.
- `cursor-agent`: Mythify resolves encoded model ids from the local
  `cursor-agent models` list. For example, `model: "gpt-5.3-codex"`,
  `effort: "high"`, and `speed: "fast"` resolves to
  `gpt-5.3-codex-high-fast` when that model is available.

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
| `MYTHIFY_VLLM_BASE_URL` | `http://localhost:8000/v1` | Local vLLM OpenAI-compatible `/v1` endpoint for `provider: "vllm"`. |
| `MYTHIFY_VLLM_MODEL` | unset | vLLM model id for probe chat checks and local reader or triage runs. |
| `OPENAI_API_KEY` | unset | OpenAI API key env name recorded in hosted provider metadata. Not used for execution in the metadata-only slice. |
| `MYTHIFY_OPENAI_API_MODEL` | unset | OpenAI API model id env name recorded in hosted provider metadata. |
| `ANTHROPIC_API_KEY` | unset | Anthropic API key env name recorded in hosted provider metadata. Not used for execution in the metadata-only slice. |
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
| `MYTHIFY_FANOUT_CONTEXT_BYTES` | 200000 | Total inlined context per task. |
| `MYTHIFY_FANOUT_CLAUDE_BIN` | resolved | Path to the claude binary. |
| `MYTHIFY_FANOUT_CLAUDE_ARGS` | empty | Extra claude args, for example `--allowedTools "Bash"`. |
| `MYTHIFY_FANOUT_CODEX_BIN` | resolved | Path to the codex binary. |
| `MYTHIFY_FANOUT_CODEX_SANDBOX` | `read-only` | Codex sandbox mode for workers. Set `workspace-write` only when workers may edit. |
| `MYTHIFY_FANOUT_CODEX_ARGS` | empty | Extra codex exec args. |
| `MYTHIFY_FANOUT_CURSOR_BIN` | resolved | Path to `cursor-agent` or `cursor`; if it points at `cursor`, Mythify runs `cursor agent`. |
| `MYTHIFY_FANOUT_CURSOR_AGENT_BIN` | resolved | Path to `cursor-agent`, used only when `MYTHIFY_FANOUT_CURSOR_BIN` is not set. |
| `MYTHIFY_FANOUT_CURSOR_MODELS` | auto-list | Optional whitespace or comma-separated Cursor model id list. When unset, Mythify runs `cursor-agent models` or `cursor agent models` to resolve encoded model ids. |
| `MYTHIFY_FANOUT_CURSOR_MODE` | `ask` | Cursor Agent mode for workers. Empty string omits `--mode`. |
| `MYTHIFY_FANOUT_CURSOR_FORCE` | unset | `1` adds `--force` for Cursor Agent workers. |
| `MYTHIFY_FANOUT_CURSOR_ARGS` | empty | Extra Cursor Agent args. |
| `MYTHIFY_FANOUT_BASE_URL`, `MYTHIFY_FANOUT_API_KEY` | unset | openai engine endpoint and key. |
| `MYTHIFY_FANOUT_COMMAND` | unset | command engine shell template. |

### Subscription auth for local CLI workers

Workers spawned by the server need credentials the same way your terminal
does. API keys are not required for the local engines.

For Claude workers, either:

- run `claude /login` once in a terminal (workers inherit the stored
  credential through `HOME`), or
- run `claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN` in the MCP
  client's `env` block.

A worker failure whose output contains `Not logged in` or `401` is reported
with exactly that remediation.

For Codex workers, run:

```bash
codex login
```

Mythify then runs `codex exec` with `HOME` and `CODEX_HOME` (when set), so the
worker uses your existing local Codex authentication. The default worker
sandbox is `read-only`; set `MYTHIFY_FANOUT_CODEX_SANDBOX=workspace-write` if
you intentionally want Codex workers to edit files.

For Cursor workers, run:

```bash
cursor-agent login
```

or, if your install exposes only the `cursor` binary:

```bash
cursor agent login
```

Mythify runs Cursor Agent in print mode with `--trust --workspace <project>`.
By default it uses `--mode ask`, which is read-only and fits fanout's "return
material, then merge and verify" workflow. Set `MYTHIFY_FANOUT_CURSOR_MODE=`
to omit the mode, or `MYTHIFY_FANOUT_CURSOR_FORCE=1` when you deliberately
want force-approved commands.

### Caveats

- Workers on the API engines (`anthropic`, `openai`) are text-only: one prompt
  in, one completion out. They cannot run tools or read files beyond the
  context inlined into their prompt.
- `claude-cli` workers run Claude Code non-interactively and get its default
  tool sandbox; grant more with `MYTHIFY_FANOUT_CLAUDE_ARGS`, for example
  `--allowedTools "Bash"`.
- `codex-cli` workers default to a read-only Codex sandbox. Raise that only
  when isolated worker edits are acceptable.
- `cursor-agent` workers default to ask mode. Remove that mode only when you
  want the full agent behavior.
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
| Codex CLI | Copy `AGENTS.md` to the project root, or register the MCP server and select `codex-cli` fanout workers. |
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

## Local comparison harness

`scripts/local_model_eval.py` runs a local before/after comparison with no API
keys. It creates two temporary workspaces for each selected Python bug-fix
task:

- `bare`: the model gets only the task prompt.
- `mythify`: the model gets `AGENTS.md`, `scripts/mythify.py`,
  `protocol/operation-registry.json`, and an initialized `.mythify/` workspace,
  then is told to use the selected Mythify profile and record `verify run`
  evidence.

The harness verifies both workspaces with `python3 -m unittest` and reports
pass rate, Mythify evidence rate, average duration, and per-run output tails.
The default `--mythify-profile auto` uses the fast profile for the built-in
focused bugfix scenarios. Use `--mythify-profile standard` when you want the
older plan-plus-verify benchmark behavior.

Run it with an existing local subscription login:

```bash
python3 scripts/local_model_eval.py --engine codex-cli --model gpt-5 --require-pass
python3 scripts/local_model_eval.py --engine claude-cli --model haiku --require-pass
python3 scripts/local_model_eval.py --engine cursor-agent --model gpt-5 --require-pass
python3 scripts/local_model_eval.py --engine codex-cli --mythify-profile standard --require-pass
```

Run the built-in mini benchmark set:

```bash
python3 scripts/local_model_eval.py --engine codex-cli --scenario all --json-output .mythify/local-codex-benchmark.json
```

Compare Codex speed modes:

```bash
python3 scripts/local_model_eval.py --engine codex-cli --scenario all --speed fast --json-output .mythify/local-codex-fast-mode-benchmark.json
python3 scripts/local_model_eval.py --engine codex-cli --scenario all --speed standard --json-output .mythify/local-codex-standard-mode-benchmark.json
```

In fast profile runs, Mythify evidence means at least one executed verification
record. In standard profile runs, evidence requires both a plan record and an
executed verification.

List the built-in scenarios:

```bash
python3 scripts/local_model_eval.py --list-scenarios
```

For deterministic offline testing, set `MYTHIFY_LOCAL_EVAL_COMMAND` and use
the `command` engine. The unit test in `tests/test_local_model_eval.py` does
exactly that, so CI never needs real model accounts.

For Codex Desktop and Codex CLI setup, see
[docs/codex-integrations.md](docs/codex-integrations.md).

For one Mythify MCP server across Codex Desktop, Claude Desktop, and Cursor
Desktop, see [docs/desktop-tool-calls.md](docs/desktop-tool-calls.md). Example
client configs live in [mcp-server/client-configs](mcp-server/client-configs).

## Development

Run the Python test suite (stdlib unittest, no dependencies):

```bash
python3 -m unittest discover -s tests -v
```

Run the MCP server smoke test:

```bash
cd mcp-server && npm ci && npm test
```

`tests/test_interop.py` exercises the CLI and the MCP server against the same
state directory across host-model state, memory, lessons, plans and steps,
outcomes, verification records, and reflections. It skips automatically when
`node` is not on PATH or `mcp-server/node_modules` is missing.

Whole-state no-mutation tests snapshot every file under `.mythify` before and
after representative refusal paths, proving guarded failures do not create,
delete, or rewrite unrelated state.

Operation registry tests compare the memory CLI and MCP behavior with
`protocol/operation-registry.json` so duplicated operation contracts cannot
quietly drift.

`docs/adapter-candidates.md` is generated from
`mcp-server/src/capability-registry.js` by `node scripts/build_registry_docs.mjs`.
The Node suite and CI hygiene job compare the committed file with fresh
registry output, so adapter docs cannot quietly drift from the registry.

## Limitations

- No published npm package yet. You can get the code by cloning the repository or
  downloading a GitHub release, but the MCP server is still configured by local
  absolute path; there are no `npx` instructions because nothing is published to npm.
- No large benchmark eval has been run yet. The local comparison harness is a
  smoke test for the Mythify effect, not statistically meaningful evidence by
  itself.
- Protocol adherence varies by model strength. Weaker models follow the discipline
  less reliably, and the gains shrink accordingly.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the
development workflow, [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community
expectations, and [SECURITY.md](SECURITY.md) for how to report vulnerabilities. Two
repository rules are non-negotiable:

1. [docs/design.md](docs/design.md) is the contract for all CLI, MCP, and on-disk
   interfaces. Behavior changes start there.
2. Generated files are never edited by hand. Edit `protocol/PROTOCOL.md` and
   regenerate protocol variants with `scripts/build_variants.py`; edit
   `mcp-server/src/capability-registry.js` and regenerate
   `docs/adapter-candidates.md` with `node scripts/build_registry_docs.mjs`.

## License

MIT. See [LICENSE](LICENSE).
