# MCP setup

Mythify ships an MCP server written in standard-library Python. It speaks
newline-delimited JSON-RPC 2.0 over stdio and has no dependencies. Every tool
call runs one Mythify CLI command in your project and returns that command's
output and exit code, so the evidence rules over MCP are the CLI's rules.

You do not need MCP if your agent can run shell commands: it can call
`mythify` directly. Add MCP when your host calls tools instead of a shell, or
when you want typed tool schemas in front of the agent.

## The launcher

Use one of these as the server command:

| How Mythify is installed | Server command |
| :--- | :--- |
| `scripts/install_user.sh` | `PREFIX/bin/mythify-mcp` (default `~/.local/bin/mythify-mcp`) |
| A clone or unpacked archive, no install | `python3 /absolute/path/to/mythify/scripts/mythify.py` with the argument `mcp` |

The installer prints the exact command and `MYTHIFY_DIR` value when it
finishes:

```text
[OK] MCP setup: register this stdio server in your host's MCP configuration:
  command: /home/you/.local/bin/mythify-mcp
  env: MYTHIFY_DIR=/path/to/your/project/.mythify
```

## MYTHIFY_DIR

Set `MYTHIFY_DIR` to the absolute path of the project's `.mythify` directory.
The server runs every command with the parent of that directory as its working
directory, so `verify run` commands execute in your project root.

Without `MYTHIFY_DIR`, the server walks up from its own working directory to
the first `.mythify` folder it finds, skipping the global lessons store in
`~/.mythify`, and falls back to its working directory when it finds none.
Desktop hosts often start MCP servers with a minimal working directory and a
minimal `PATH`, so set the variable and use absolute paths for both the command
and `MYTHIFY_DIR`.

Run `mythify init` in the project first.

## Config example

Most hosts read a JSON file with an `mcpServers` object:

```json
{
  "mcpServers": {
    "mythify": {
      "command": "/home/you/.local/bin/mythify-mcp",
      "env": { "MYTHIFY_DIR": "/path/to/your/project/.mythify" }
    }
  }
}
```

Without an install, set `"command"` to the absolute path of your `python3`
(`command -v python3` prints it) and
`"args": ["/absolute/path/to/mythify/scripts/mythify.py", "mcp"]`.

## Where hosts keep MCP config

These locations come from the host setup notes Mythify kept through 5.x.
Hosts move these files between releases; if a path does not exist, check the
host's own documentation.

| Host | Config location | Format |
| :--- | :--- | :--- |
| Claude Code | `.mcp.json` in the project root (project scope); `claude mcp add` writes it | JSON, `mcpServers` |
| Claude Desktop | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`; Windows `%APPDATA%\Claude\claude_desktop_config.json`; read at startup, so restart after editing | JSON, `mcpServers` |
| Codex CLI and Codex Desktop | `~/.codex/config.toml` (user) or `.codex/config.toml` (trusted project); `codex mcp add` writes it | TOML |
| Cursor | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | JSON, `mcpServers` |
| Antigravity | `.agents/mcp_config.json` (project), `~/.gemini/config/mcp_config.json`, or `~/.gemini/antigravity-cli/mcp_config.json` | JSON, `mcpServers` |
| Kimi Code | `.kimi-code/mcp.json` (project) or `~/.kimi-code/mcp.json` (user) | JSON |

Two of these hosts have a registration command that writes the file for you:

```bash
claude mcp add --scope project --transport stdio mythify -- /home/you/.local/bin/mythify-mcp
codex mcp add mythify \
  --env MYTHIFY_DIR=/path/to/your/project/.mythify \
  -- /home/you/.local/bin/mythify-mcp
```

The first command records no environment; add the `env` block from the config
example to the generated `.mcp.json`. Do not commit an MCP config that points
at paths other machines do not have.

## Check it from a terminal

The server answers each request line and exits when stdin closes:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"status","arguments":{"recent":1}}}' \
  | MYTHIFY_DIR=/path/to/your/project/.mythify mythify-mcp
```

Two JSON lines come back: the `initialize` result with `serverInfo`, then the
`status` output ending in `exit_code: 0`.

## Tools

`tools/list` returns 37 typed tools plus one escape-hatch tool. The list is
built from the CLI's argument parser at startup, so `tools/list` is
authoritative and these tables are a snapshot of 6.0.0.

A typed tool's name is the command path joined with `_`, with `-` turned into
`_`:

| Group | Typed tools |
| :--- | :--- |
| Orient | `init`, `status`, `report`, `route` |
| Plans | `plan_create`, `plan_add_step`, `plan_verify`, `plan_show`, `step` |
| Evidence | `verify_run`, `verify_claim`, `reflect` |
| Memory | `memory_set`, `memory_get`, `lesson_add`, `lesson_list` |
| Outcome loops | `outcome_start`, `outcome_check`, `outcome_status`, `outcome_stop` |
| Maps | `map_create`, `map_ticket`, `map_claim`, `map_resolve`, `map_show`, `map_promote` |
| Product | `product_create`, `product_outcome`, `product_non_goal`, `product_bet`, `product_risk`, `product_check`, `product_approve`, `product_decide`, `product_promote`, `product_measure`, `product_show` |

Argument keys are the CLI's long option names with `-` turned into `_`
(`--human-input` becomes `human_input`, `--target-source` becomes
`target_source`), and positionals use their CLI names (`goal`, `id`, `status`,
`command`). Each property description ends with the CLI flag it maps to.
Required CLI arguments are required in the schema, unknown keys are rejected,
and booleans stand for flags such as `json`.

The escape-hatch tool `mythify` takes `{"args": [...]}`, the argument list you
would type after `mythify`. It runs any command without a typed tool, for
example `{"args": ["outcome", "run"]}` or `{"args": ["summary", "--json"]}`.
It refuses `mcp`. In 6.0.0 these commands have no typed tool: `protocol check`,
`history`, `summary`, `loop-fit`, every `prompt` kind, `plan import`,
`plan list`, `plan switch`, `plan archive`, `map list`, `map verify`,
`map fog`, `map scope-out`, `product list`, `outcome run`, `outcome results`,
`review blast-radius`, `review prove`, `review show`, `lineage attach`,
`lineage status`, `memory clear`, and `logs compact`.

### Load the schema before you call

Treat a tool as unavailable until its schema is loaded from `tools/list` or
your host's tool search. Never call a Mythify tool from a guessed or
remembered schema: argument names changed in 6.0.0 (for example
`workflow_route` is now `route` and `memory_store` is now `memory_set`), and a
call with plausible but wrong arguments either fails validation or does
something you did not intend. If a tool does not resolve, say so and use the
`mythify` tool or the CLI instead. A loaded schema is input to your next call,
not evidence that anything worked.

## Results and errors

Every tool result is one text block: the command's stdout, then its stderr
when non-empty, then a final line `exit_code: N`. `isError` follows the exit
code:

| Exit code | Meaning | `isError` |
| :--- | :--- | :--- |
| 0 | Success. | false |
| 2 | A recorded unverified verdict: the check ran and failed, or `product check` found gaps. This is a valid result, not a tool error. | false |
| 1 | Refusal or failure, for example `step` completed without a passing `verify run`, or `product approve` without `human_input`. | true |
| 64 | Usage error from the CLI's argument parser. | true |
| 124 | The call hit `MYTHIFY_MCP_CALL_TIMEOUT` and was stopped. | true |

Arguments the server cannot turn into a command line (an unknown key, a missing
required key, a wrong type) return `isError: true` with a `[FAIL]` line and no
`exit_code`, because no command ran. An unknown tool name is a JSON-RPC error
(`-32602`).

`MYTHIFY_DISABLE_RUN=1` in the server's environment turns off every command
that executes a shell command: `verify run`, `plan verify`, `map verify`,
`outcome check`, `outcome run`, `product measure`, and `review prove`. They
print `[FAIL] ... is disabled`, run nothing, and exit 2.

## Human gates still need the human

MCP changes how the agent calls Mythify, not who may decide. `product_approve`
and `product_decide` are refused without `human_input`, and `map_resolve` on a
`grilling` or `prototype` ticket, or a task ticket added with mode `hitl`, is
refused without `human_input`. The value must be the person's actual words.
An agent that writes its own approval has recorded nothing a reviewer can
trust. Ask, wait for the answer, then pass it through.

## Protocol versions

The server supports two eras of the MCP lifecycle:

- `initialize` era. Supported versions: `2026-07-28`, `2025-11-25`,
  `2025-06-18`, `2025-03-26`, and `2024-11-05`. `initialize` echoes the
  client's `protocolVersion` when it is in that list and answers `2025-11-25`
  otherwise. `notifications/initialized` is accepted and ignored.
- Stateless era. `server/discover` returns `supportedVersions`,
  `capabilities`, and `instructions`. Requests may arrive without
  `initialize`; when a request carries
  `params._meta["io.modelcontextprotocol/protocolVersion"]`, the result
  carries `_meta["io.modelcontextprotocol/serverInfo"]` with the server name
  and version.

Other methods: `ping`, `tools/list`, `tools/call`, and
`notifications/cancelled`. Unknown methods get `-32601`, malformed JSON gets
`-32700`, and batch requests get `-32600`. Notifications get no reply.

## Timeouts and cancellation

Tool calls run one at a time, in arrival order, so writes to `.mythify/` stay
ordered. `ping` and `tools/list` are still answered while a long call runs.

`MYTHIFY_MCP_CALL_TIMEOUT` sets the per-call limit in seconds (default 900).
When a call exceeds it, the server stops the CLI process and the command it
was running, and returns `exit_code: 124`. A stopped `verify_run` records
nothing. To have a slow check recorded as a failed verification instead, pass
the tool's own `timeout` argument with a value below the call limit; the CLI
default for `verify run` is 300 seconds.

`notifications/cancelled` with the request's id stops a queued or running
call the same way, and that request gets no response.
