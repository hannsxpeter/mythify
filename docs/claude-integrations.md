# Using Mythify with Claude Desktop and Claude Code

Mythify's value scales inversely with model strength: the protocol and tools
compensate for exactly the things smaller models lack (sustained multi-step
discipline, grounded progress claims, memory beyond the context window). This
guide covers wiring Mythify into Anthropic's two main clients and running it
deliberately on smaller, cheaper models.

All paths below must be absolute. Neither `.mcp.json` nor
`claude_desktop_config.json` supports relative paths or variable substitution.

## Claude Code

### The protocol: zero setup

Claude Code reads `CLAUDE.md` from the project root at session start. Copy the
protocol and the CLI into any project and it is active immediately:

```bash
cp CLAUDE.md scripts/mythify.py /path/to/your/project/
cd /path/to/your/project && mkdir -p scripts && mv mythify.py scripts/
python3 scripts/mythify.py init
```

Note: Claude Code reads `CLAUDE.md`, not `AGENTS.md`. The `AGENTS.md` variant
in this repo is for tools that follow that convention (Codex CLI, Cursor with
AGENTS.md support, and others). If your project already standardizes on
`AGENTS.md`, create a one-line `CLAUDE.md` containing `@AGENTS.md` so Claude
Code imports it instead of duplicating the content.

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
Desktop delegate parallel subtasks to fresh workers, and with the
`claude-cli` engine those workers bill against your Claude subscription
instead of an API key. Desktop usually needs two extra `env` entries that a
terminal session gets for free:

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

## Running Mythify on smaller models

This is the configuration where Mythify earns its keep. A frontier model has
much of the protocol's discipline trained in; a smaller model does not, and
the protocol plus executed verification recovers a useful share of it at a
fraction of the price. As of mid-2026, `claude-haiku-4-5` costs $1 per million
input tokens and $5 per million output tokens, versus $10 and $50 for the
frontier tier: a 10x difference that buys a lot of verification loops.

Switching models in Claude Code (aliases `haiku`, `sonnet`, `opus`, `fable`,
or full model IDs like `claude-haiku-4-5` and `claude-sonnet-4-6`):

```bash
claude --model haiku                 # one session
ANTHROPIC_MODEL=haiku claude         # via environment
/model                               # switch mid-session
```

Or persistently, in `.claude/settings.json`:

```json
{
  "model": "haiku"
}
```

In Claude Desktop, pick the model from the dropdown next to the send button.

Guidance that matters more as the model gets smaller:

1. Run both layers. Drop in the protocol (`CLAUDE.md`) and keep the tools
   available (CLI or MCP). Small models drop multi-step habits fastest, so the
   externalized plan and the evidence-or-it-did-not-happen rule do more work.
2. Insist on `verify run` for every completion claim. Hallucinated progress is
   the dominant small-model failure mode, and an exit code is the cheapest
   antidote.
3. Re-orient constantly. `status` at session start and after any confusion;
   small context windows (Haiku 4.5 carries 200K tokens) plus small models
   mean state on disk beats state in context even sooner.
4. Expect protocol drift and treat it as normal. When the model stops updating
   steps, a one-line reminder ("follow the Mythify loop") restores it; the
   protocol text is deliberately short so re-reading it is cheap.
5. Keep the honest framing. Mythify closes the discipline gap, not the
   capability gap. A small model with Mythify completes more multi-step work
   than the same model without it; it does not become a frontier model.

Fanout makes the cheap-model strategy concrete. Run the orchestrating session
on a strong model and fan the mechanical subtasks (drafting boilerplate,
summarizing files, generating test cases) to `haiku` workers with
`fanout_start`: the strong model writes the task list once and the server
does the spawning and collecting. Models mix per task, since per-task `model`
overrides the job `model`, which overrides `MYTHIFY_FANOUT_MODEL`, so one job
can run five haiku drafters and one sonnet reviewer. Merge the results
yourself and verify the merged work with `verify_run`; worker output is
material, not evidence.
