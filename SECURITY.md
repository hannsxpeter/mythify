# Security Policy

## Supported versions

| Version | Supported |
| :--- | :--- |
| 2.x | Yes |
| Anything earlier | No (unreleased prototypes) |

## Reporting a vulnerability

Do not open a public issue for security problems.

Report privately through GitHub Security Advisories:
https://github.com/aihxp/mythify/security/advisories/new

If you cannot use GitHub, email hprincivil@gmail.com with a description, a
reproduction, and the impact you believe it has.

What to expect:

- Acknowledgment within 7 days.
- A fix or documented mitigation within 30 days for confirmed issues, with
  credit to the reporter in the changelog unless you ask otherwise.

## Mythify's execution model (read before reporting)

Some behavior is by design and is not a vulnerability:

- `verify run` (CLI) and `verify_run` (MCP tool) execute arbitrary shell
  commands. That is the feature: verification means running real commands and
  recording real exit codes. The commands run with the privileges of whoever
  runs the CLI or the MCP server.
- `fanout_start` (MCP tool) spawns local worker processes (the `claude-cli`,
  `codex-cli`, `cursor-agent`, and `command` engines) or makes outbound API
  calls (the `anthropic` and `openai` engines). That is the
  parallel-delegation feature. `claude-cli` workers get a curated environment
  (`HOME`, `TERM`, an augmented `PATH`, and `CLAUDE_CODE_OAUTH_TOKEN` when
  set), never the rest of the server's environment. `codex-cli` and
  `cursor-agent` workers get a local-login environment (`HOME`, `TERM`, an
  augmented `PATH`, `CODEX_HOME` or `XDG_CONFIG_HOME` when set, and the fanout
  guards); API-key env vars are not passed through by default. `command`
  engine workers run a command you configured yourself and inherit the
  server's environment, so what they can see is up to you. All local workers
  carry the depth-guard variables that prevent nested fanout.
- Mythify is not a sandbox and does not try to be one. It does not restrict
  what a model asks it to run. The boundary is the operating-system user the
  server runs as.

Hardening guidance for users:

- Set `MYTHIFY_DISABLE_RUN=1` in the MCP server environment to disable
  `verify_run` entirely (the tool refuses and records nothing).
- Set `MYTHIFY_DISABLE_FANOUT=1` to disable all three fanout tools
  (`fanout_start`, `fanout_status`, `fanout_results`); they refuse with an
  explanation.
- Never run the MCP server with elevated privileges.
- Do not store secrets in memory entries or lessons. Everything under
  `.mythify/` is plain text on disk.

A report is in scope when Mythify does something other than what this model
describes: for example, executing commands while `MYTHIFY_DISABLE_RUN=1` is
set, spawning workers while `MYTHIFY_DISABLE_FANOUT=1` is set, leaking
undocumented server environment variables into worker processes, writing
state outside the resolved `.mythify/` directory, or any path traversal in
state-file handling.
