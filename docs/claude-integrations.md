# Using Mythify with Claude Desktop and Claude Code

Mythify externalizes multi-step state, completion evidence, and memory outside
the model context. Whether those mechanisms produce a larger benefit on a
smaller Claude model is an unverified hypothesis, not a published product
claim. The committed smoke evidence used an unpinned Codex default model and
does not compare Claude tiers. This guide covers wiring Mythify into
Anthropic's two main clients and cautiously evaluating smaller models.

All paths below must be absolute. Neither `.mcp.json` nor
`claude_desktop_config.json` supports relative paths or variable substitution.
For a cross-client setup covering Codex Desktop, Claude Desktop, and Cursor
Desktop, see [desktop-tool-calls.md](desktop-tool-calls.md).

## Claude Code

### The protocol: zero setup

Claude Code reads `CLAUDE.md` from the project root at session start. Copy the
protocol and the CLI into any project and it is active immediately:

```bash
cp CLAUDE.md /path/to/your/project/
mkdir -p /path/to/your/project/scripts
mkdir -p /path/to/your/project/protocol
cp scripts/mythify.py /path/to/your/project/scripts/
cp scripts/mythify_*.py /path/to/your/project/scripts/
cp protocol/operation-registry.json /path/to/your/project/protocol/
cp protocol/classification-rules.json /path/to/your/project/protocol/
cp protocol/model-capabilities.json /path/to/your/project/protocol/
cp protocol/workflow-router.json /path/to/your/project/protocol/
cd /path/to/your/project
python3 scripts/mythify.py protocol check CLAUDE.md
python3 scripts/mythify.py init
```

Note: Claude Code reads `CLAUDE.md`, not `AGENTS.md`. The `AGENTS.md` variant
in this repo is for tools that follow that convention (Codex CLI, Cursor with
AGENTS.md support, and others). If your project already standardizes on
`AGENTS.md`, create a one-line `CLAUDE.md` containing `@AGENTS.md` so Claude
Code imports it instead of duplicating the content.

### Chat skills: optional

For a chat-native front door, install the focused Mythify skills. The checkout
installer copies them into the Claude Code skills root (`$CLAUDE_HOME/skills`
or `$HOME/.claude/skills`) alongside the Codex copy:

```bash
./scripts/install_user.sh --skip-mcp --project /path/to/your/project
```

This installs `mythify`, `mythify-work`, `mythify-route`, and `mythify-verify`.
Invoke any of them with `/mythify` in Claude Code (the same skill is `$mythify`
in Codex). Pass `--skip-claude-skills` to install only the Codex copy, or
`--claude-skills-root PATH` to override the destination. The skills are
optional sugar over the protocol and CLI; they do not require the MCP server.

### The MCP server: optional in Claude Code

Claude Code has shell access, so the model can run `scripts/mythify.py`
directly; the MCP server is optional here. Add it when you want the same
state tools across clients, or typed tools instead of shell calls.

User scope (available in every project):

```bash
claude mcp add --scope user --transport stdio mythify -- node /absolute/path/to/mythify/mcp-server/src/index.js
```

Project scope (writes a `.mcp.json` at the project root, which teammates are
prompted to approve on first use):

```bash
claude mcp add --scope project --transport stdio mythify -- node /absolute/path/to/mythify/mcp-server/src/index.js
```

The generated `.mcp.json` looks like this; you can also write it by hand:

```json
{
  "mcpServers": {
    "mythify": {
      "type": "stdio",
      "command": "node",
      "args": ["/absolute/path/to/mythify/mcp-server/src/index.js"]
    }
  }
}
```

Do not commit a `.mcp.json` that points at a path other machines will not
have. This repo deliberately ships no `.mcp.json` for that reason; generate
yours locally with the command above.

## Claude Desktop

Claude Desktop has no shell, which makes the MCP server the only way to give
it Mythify's tools, and the most valuable place to run them: `verify_run`
gives a shell-less client real command execution with recorded exit codes
instead of self-reported progress.

Edit the config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

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

Restart Claude Desktop after editing; the config is read at startup.

Two Desktop-specific notes:

- Set `MYTHIFY_DIR` explicitly. Desktop launches the server outside any
  project directory, so without it the server cannot discover which project's
  `.mythify/` you mean. Point it at the project you are currently working on.
- `verify_run` executes shell commands on your machine by design. If you want
  Desktop to have the planning, memory, and reflection tools but not command
  execution, add `"MYTHIFY_DISABLE_RUN": "1"` to the `env` block. See
  [SECURITY.md](../SECURITY.md) for the full risk notes.

### Fanout from Claude Desktop

The fanout tools (`fanout_start`, `fanout_status`, `fanout_results`) let
Desktop delegate parallel subtasks to fresh workers. With the local CLI
engines, those workers use subscriptions you already authenticated in a
terminal instead of API keys:

- `claude-cli`: run `claude /login`, or use `claude setup-token` for Desktop.
- `claude-ultracode`: update to Claude Code 2.1.203 or newer, then use the same
  Claude authentication as `claude-cli`.
- `codex-cli`: run `codex login`.
- `cursor-agent`: run `cursor-agent login`, or `cursor agent login`.

For Claude Desktop with Claude workers, Desktop usually needs two extra `env`
entries that a terminal session gets for free:

```json
{
  "mcpServers": {
    "mythify": {
      "command": "node",
      "args": ["/absolute/path/to/mythify/mcp-server/src/index.js"],
      "env": {
        "MYTHIFY_DIR": "/absolute/path/to/your/project/.mythify",
        "MYTHIFY_FANOUT_CLAUDE_BIN": "/opt/homebrew/bin/claude",
        "CLAUDE_CODE_OAUTH_TOKEN": "<output of: claude setup-token>"
      }
    }
  }
}
```

Why Desktop needs them:

- `MYTHIFY_FANOUT_CLAUDE_BIN`: Desktop launches MCP servers with a minimal
  `PATH`, so `claude` often does not resolve. The server falls back to
  `~/.claude/local/claude`, `/opt/homebrew/bin/claude`, and
  `/usr/local/bin/claude`; set the variable when your binary lives anywhere
  else (find it with `which claude` in a terminal).
- `CLAUDE_CODE_OAUTH_TOKEN`: the server runs outside any terminal login
  session. If you have run `claude /login` once in a terminal, workers
  usually inherit that stored credential through `HOME`; otherwise run
  `claude setup-token` and put the printed token in the `env` block. A worker
failure containing `Not logged in` or `401` means exactly this is missing.

When `workflow_route` returns a recommended `execution_adapter` with engine
`claude-ultracode`, call `fanout_start` with that engine and exactly one
self-contained task. Mythify probes the CLI before creating the job, launches
one native dynamic workflow with `--effort ultracode`, monitors it through
`fanout_status`, and ingests the final response through `fanout_results`.
Workflow output remains material; run `verify_run` on the merged result before
claiming completion. The adapter never passes a permission-bypass flag.

For Codex workers in any MCP client, set the engine and, if needed, the binary
path:

```json
{
  "mcpServers": {
    "mythify": {
      "command": "node",
      "args": ["/absolute/path/to/mythify/mcp-server/src/index.js"],
      "env": {
        "MYTHIFY_DIR": "/absolute/path/to/your/project/.mythify",
        "MYTHIFY_FANOUT_ENGINE": "codex-cli",
        "MYTHIFY_FANOUT_CODEX_BIN": "/opt/homebrew/bin/codex"
      }
    }
  }
}
```

Codex workers default to `MYTHIFY_FANOUT_CODEX_SANDBOX=read-only` so parallel
workers produce material for the orchestrator to merge. Set it to
`workspace-write` only when isolated worker edits are acceptable.

For Cursor workers, use:

```json
{
  "mcpServers": {
    "mythify": {
      "command": "node",
      "args": ["/absolute/path/to/mythify/mcp-server/src/index.js"],
      "env": {
        "MYTHIFY_DIR": "/absolute/path/to/your/project/.mythify",
        "MYTHIFY_FANOUT_ENGINE": "cursor-agent",
        "MYTHIFY_FANOUT_CURSOR_BIN": "/Users/you/.local/bin/cursor-agent"
      }
    }
  }
}
```

Cursor workers default to `MYTHIFY_FANOUT_CURSOR_MODE=ask`, matching fanout's
"return material, then merge and verify" shape. Set
`MYTHIFY_FANOUT_CURSOR_MODE=` to omit the mode, and set
`MYTHIFY_FANOUT_CURSOR_FORCE=1` only when you deliberately want force-approved
commands.

## Evaluating Mythify on smaller models

Treat lower-cost model use as an experiment for bounded tasks with executable
verifiers. Mythify has no paired Claude-tier evidence showing recovered
capability, higher task success, or lower total cost. Check Anthropic's current
model availability and pricing before making a cost decision.

Switching models in Claude Code (aliases `haiku`, `sonnet`, `opus`, `fable`,
or full model IDs like `claude-haiku-4-5` and `claude-sonnet-5`):

```bash
claude --model haiku                 # one session
ANTHROPIC_MODEL=haiku claude         # via environment
/model                               # switch mid-session
```

In Mythify MCP, call `host_model_switch` with `platform: "claude-code"` or
`platform: "claude-desktop"` to record the intended host model for future
model policy and spawn ceiling checks. Claude Code can apply the returned
`/model <target>` action; Claude Desktop still requires its model picker.
`classify_task` also returns `model_policy.session.recommendation`: direct
low-risk prompts map to `utility` with `haiku` and low effort; ordinary
implementation maps to `balanced` with `sonnet` and high effort; research or
high-risk prompts map to `strong` with `opus` and xhigh effort. Explicit
`max` maps to `fable` and max effort. Override these names with
`MYTHIFY_HOST_UTILITY_MODEL`, `MYTHIFY_HOST_BALANCED_MODEL`,
`MYTHIFY_HOST_STRONG_MODEL`, and `MYTHIFY_HOST_MAX_MODEL` when needed. Legacy
fast and standard override names remain accepted.

Or persistently, in `.claude/settings.json`:

```json
{
  "model": "haiku"
}
```

In Claude Desktop, pick the model from the dropdown next to the send button.

Conservative evaluation guidance:

1. Run both layers. Drop in the protocol (`CLAUDE.md`) and keep the tools
   available (CLI or MCP), then compare results with and without Mythify on the
   same verifier-backed task.
2. Insist on `verify run` for every completion claim. This guards every model
   against prose that is not backed by an executed command.
3. Re-orient with `status` at session start and after confusion. Durable state
   reduces reliance on any model's context window.
4. Record protocol drift as an observed failure instead of assuming it is a
   small-model trait.
5. Keep the honest framing. Mythify changes the workflow around a model; it
   does not establish that a smaller model becomes more capable.

Fanout makes the cheap-model strategy concrete. Run the orchestrating session
on a strong model and fan the mechanical subtasks (drafting boilerplate,
summarizing files, generating test cases) to `haiku` workers with
`fanout_start`: the strong model writes the task list once and the server
does the spawning and collecting. Models mix per task, since per-task `model`
overrides the job `model`, which overrides `MYTHIFY_FANOUT_MODEL`, so one job
can run five haiku drafters and one sonnet reviewer. Effort and speed mix the
same way: per-task `effort` or `speed` overrides the matching job setting,
which overrides `MYTHIFY_FANOUT_EFFORT` or `MYTHIFY_FANOUT_SPEED`, and the
resolved values are written to `job.json`.
For Claude Code workers, Mythify also passes the resolved effort to the
Claude CLI with `--effort`. Speed remains prompt-visible policy because Claude
Code does not expose a separate speed flag.
Spawn ceiling is separate: pass `session_model` or set
`MYTHIFY_SESSION_MODEL`, and Mythify defaults spawned workers to
same-or-lower unless `spawn_ceiling` is `allow_stronger`.
Merge the results yourself and verify the merged work with `verify_run`;
worker output is material, not evidence.
