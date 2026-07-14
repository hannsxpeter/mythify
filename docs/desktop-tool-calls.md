# Desktop Tool Calls

Mythify exposes the same MCP tool surface to Codex Desktop, Claude Desktop,
and Cursor Desktop. The important idea is simple: run one local stdio MCP
server, point it at a project `.mythify/` directory with `MYTHIFY_DIR`, then
let each desktop client call tools such as `classify_task`, `workflow_route`,
`plan_create`, `verify_run`, `fanout_start`, and `fanout_results`.

## Shared Setup

From this repository:

```bash
cd /absolute/path/to/mythify/mcp-server
npm install
```

In each project you want Mythify to manage:

```bash
cd /absolute/path/to/your/project
python3 /absolute/path/to/mythify/scripts/mythify.py init
```

Use absolute paths in desktop configs. Desktop apps often launch MCP servers
with a minimal working directory and a minimal `PATH`, so absolute paths avoid
the usual "works in terminal, not in the app" failure.

## Common Server Block

Claude Desktop and Cursor use JSON-shaped MCP config. Start from:

```json
{
  "mcpServers": {
    "mythify": {
      "command": "/absolute/path/to/node",
      "args": ["/absolute/path/to/mythify/mcp-server/src/index.js"],
      "env": {
        "MYTHIFY_DIR": "/absolute/path/to/your/project/.mythify"
      }
    }
  }
}
```

Optional env entries:

```json
{
  "MYTHIFY_DISABLE_RUN": "1",
  "MYTHIFY_HOST_PLATFORM": "codex-desktop",
  "MYTHIFY_SESSION_MODEL": "gpt-5",
  "MYTHIFY_SPAWN_CEILING": "same_or_lower",
  "MYTHIFY_FANOUT_EFFORT": "medium",
  "MYTHIFY_FANOUT_CODEX_BIN": "/absolute/path/to/codex"
}
```

Use `MYTHIFY_DISABLE_RUN=1` only when you want planning, memory, lessons, and
classification without shell execution. Leave it unset when you want
`verify_run` to run tests and builds.

## Model Policy

Desktop hosts have two different model layers:

- The session model is the model selected in Codex Desktop, Claude Desktop, or
  Cursor Desktop. Mythify cannot and should not silently change it.
- Spawned models are workers launched by Mythify through `classify_task` triage
  or `fanout_start`.

`classify_task` returns `model_policy` to separate those layers. It records the
host platform, the recommended triage engine, the spawned model policy, fanout
worker effort, reviewer effort, and the command-first verifier policy. Pass
`platform: "codex-desktop"`, `platform: "claude-desktop"`, or
`platform: "cursor-desktop"` when the caller knows where it is running.
It also returns `model_policy.session.recommendation`, a prompt-specific host
settings recommendation. `model_policy.model_router` keeps autonomy policy,
execution topology, model profile, reasoning effort, review policy, and the
verification gate separate. Direct prompts such as `what is 1 + 1?` map to
`utility`. Normal implementation and debugging map to `balanced`. Research,
benchmark, release, migration, security, and design map to `strong`. `max`
must be explicit. A supplied executed-verifier failure count can escalate one
profile per failure, but automatic escalation stops at `strong`.

Default profile-to-model mappings are platform-aware:

| Platform | Utility | Balanced | Strong | Max |
| :--- | :--- | :--- | :--- | :--- |
| Codex Desktop or CLI | `gpt-5.6-luna`, low | `gpt-5.6-terra`, medium | `gpt-5.6-sol`, high | `gpt-5.6-sol`, max or pro mode |
| Claude Desktop or Claude Code | `haiku`, low | `sonnet`, high | `opus`, xhigh | `fable`, max |
| Cursor Desktop or cursor-agent | Discover Luna, Haiku, Mini, Flash, or Composer | Discover Terra, Sonnet, Composer, or `auto` | Discover Opus, Sol, or Gemini 3.1 Pro | Discover Fable, Opus, or Sol |

Override recommendation model names with `MYTHIFY_HOST_UTILITY_MODEL`,
`MYTHIFY_HOST_BALANCED_MODEL`, `MYTHIFY_HOST_STRONG_MODEL`, and
`MYTHIFY_HOST_MAX_MODEL` if your local host exposes different ids. The legacy
fast and standard override names remain accepted.

Pass `session_model` when the desktop app or user can name the current chat
model. If the user wants Mythify to remember an intended host model, call
`host_model_switch` first. Mythify writes `.mythify/host-model.json`, returns
the host-specific switch action, and uses that recorded target as the default
session model for later `classify_task` and `fanout_start` calls. The desktop
host still owns the actual current chat model switch.
Default ceiling is `same_or_lower`, so explicit spawned models cannot be
stronger than the initiating model unless the caller passes
`spawn_ceiling: "allow_stronger"`. Unknown local CLI defaults are recorded as
uncheckable rather than guessed.

`fanout_start` accepts `engine`, `model`, `effort`, and `speed` at both the
job level and the per-task level. Per-task values win. Effort and speed are
written to `job.json`, shown in `fanout_status`, and included in the worker
prompt. For Codex workers, `speed: "fast"` enables Codex fast mode where
supported and `speed: "standard"` explicitly disables it for that worker.
For Claude Code workers, Mythify passes resolved effort with `--effort` and
keeps speed as prompt-visible policy. For Cursor workers, Mythify resolves the
requested `model`, `effort`, and `speed` into an encoded model id from
`cursor-agent models` when a matching id is available. When no model is
provided, it selects from the live catalog by capability profile and uses
Cursor `auto` only when the catalog contains it. It never falls back to a
different provider.

For a router-recommended native dynamic workflow, use
`engine: "claude-ultracode"` with exactly one task. Claude Code 2.1.203 or
newer is required. The adapter fixes effort to `ultracode`, records workflow
mode in durable fanout state, exposes progress through `fanout_status`, and
returns the final response through `fanout_results` as material only.
Platform-specific reasoning or effort flags can still be passed through the
corresponding extra args env variable for that CLI.

`fanout_start` also accepts `purpose` and `visibility`. Visibility modes are
`quiet`, `summary`, `verbose`, and `threaded`, plus `auto`. Omit visibility or
set `auto` to infer from the original user request and task prompts. The
resolved default is `summary`: the main chat should show worker titles, status
counts, and notable findings, but not every worker transcript. Use `quiet`
when the user wants background work, `verbose` when the user asks for full
worker output, and `threaded` when the user asks for visible worker chats.
Threaded mode is only a request unless the host can actually create visible
Codex, Claude, or Cursor threads.

Example host model request:

```json
{
  "action": "switch",
  "platform": "codex-desktop",
  "target_model": "gpt-5.6-terra",
  "current_model": "gpt-5.6-luna",
  "thinking": "high",
  "speed": "fast"
}
```

Expected result:

- `.mythify/host-model.json` records `target_model`
- `classify_task` reports `model_policy.session.model_source` as `host_model_switch`
- The response tells the user or host agent how to perform the actual chat switch

## Outcome Loops In Desktop Hosts

Outcome loops keep the desktop chat as the visible cockpit. Codex, Claude, or
Cursor calls `outcome_start` with the user goal, success criteria, verifier
command, optional metric command, and iteration budget. The host agent then
makes one bounded attempt in the normal chat context and calls `outcome_check`.

`outcome_check` executes the verifier, records the iteration, and returns one
of three decisions: report success, make another bounded attempt, or stop
because the retry budget is exhausted. The user sees a summary by default. If
the user asks for full transcripts or visible worker threads, use the matching
visibility mode, subject to host support.

The same `.mythify/outcomes/` state works across Codex Desktop, Claude Desktop,
Cursor Desktop, and the CLI. That means a loop started in one host can be
inspected or stopped from another host as long as they point at the same
`MYTHIFY_DIR`.

## Codex Desktop

Codex uses the same config layers as the Codex CLI: user config at
`~/.codex/config.toml` and trusted project config at `.codex/config.toml`.
The safest setup path is the CLI registration command:

```bash
codex mcp add mythify \
  --env MYTHIFY_DIR=/absolute/path/to/your/project/.mythify \
  --env MYTHIFY_HOST_PLATFORM=codex-desktop \
  -- node /absolute/path/to/mythify/mcp-server/src/index.js
```

Then open the same project in Codex Desktop. Ask Codex to use the Mythify MCP
tool, for example:

```text
Use mythify classify_task on this request with triage auto, then create a plan.
```

Local Codex triage and fanout workers use the current `codex login` auth. No
Mythify API key is required.

## Claude Desktop

Claude Desktop reads:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Paste the common JSON server block into that file and restart Claude Desktop.
Then ask Claude:

```text
Use the mythify tool to classify this task, then create a plan and verify with the relevant command.
```

For Claude-backed fast triage or fanout from Desktop, run `claude /login` in a
terminal first, or set the token produced by `claude setup-token`:

```json
{
  "MYTHIFY_TRIAGE_ENGINE": "claude-cli",
  "MYTHIFY_FANOUT_ENGINE": "claude-cli",
  "MYTHIFY_FANOUT_CLAUDE_BIN": "/absolute/path/to/claude",
  "CLAUDE_CODE_OAUTH_TOKEN": "<output of: claude setup-token>"
}
```

## Cursor Desktop

Cursor supports MCP from Settings > Tools and MCP. For project-scoped setup,
create `.cursor/mcp.json` in the project root and paste the common JSON server
block. For global setup, use `~/.cursor/mcp.json`.

Then ask Cursor Agent:

```text
Call mythify classify_task with triage auto, then use mythify plan_create for the selected approach.
```

For Cursor-backed fast triage or fanout, run `cursor-agent login` or
`cursor agent login`, then set:

```json
{
  "MYTHIFY_HOST_PLATFORM": "cursor-desktop",
  "MYTHIFY_FANOUT_CURSOR_BIN": "/absolute/path/to/cursor-agent"
}
```

Cursor fanout workers default to ask mode. That fits Mythify's workflow:
workers return material, the orchestrator merges it, and `verify_run`
provides evidence.

## Smoke Test

After configuring a client, ask it to call:

```text
Use mythify classify_task on "make this better" with triage set to never.
```

Expected result:

- `task_type` is `feature`
- `ambiguity` is `high`
- `execution_profile` is `standard`
- `model_triage` is `recommended`
- `model_policy.session.control` is `host_selected`

Then ask:

```text
Use mythify plan_status.
```

If it can call both tools, desktop tool calling is wired correctly.
