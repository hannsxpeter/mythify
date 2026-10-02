# Security Policy

## Supported versions

| Version | Supported |
| :--- | :--- |
| 6.x | Yes |
| 5.x and earlier | No |

Fixes land on `main` and ship in the next 6.x release. Earlier lines get no
backports.

## Reporting a vulnerability

Do not open a public issue for security problems.

Report privately through GitHub Security Advisories:
https://github.com/hannsxpeter/mythify/security/advisories/new

If you cannot use GitHub, email hprincivil@gmail.com with a description, a
reproduction, and the impact you believe it has.

What to expect:

- Acknowledgment within 7 days.
- A fix or documented mitigation within 30 days for confirmed issues, with
  credit to the reporter in the changelog unless you ask otherwise.

## Mythify's execution model (read before reporting)

Some behavior is by design and is not a vulnerability.

These commands execute shell commands, because verification means running
real commands and recording real exit codes:

| Command | What it runs |
| :--- | :--- |
| `verify run COMMAND` | `COMMAND` |
| `plan verify ID` | the step's stored `verify_command` |
| `outcome check` | the outcome's verify command and optional metric command |
| `outcome run` | the outcome's agent command, then its verify and metric commands, in a bounded loop |
| `map verify ID` | the ticket's stored `verify_command` |
| `review prove NAME` | the review's merge-gate command, or `--command` |
| `product measure OUTCOME` | the outcome's stored measure command |

Each runs through the system shell with the privileges of whoever runs the
CLI, in its own process session, with a timeout and an output cap. Mythify
stores redacted stdout and stderr tails under `.mythify/`, and for every
command except the outcome loops, redacted full-output artifacts as well.

The MCP server (`mythify mcp`, launched by `bin/mythify-mcp`) runs the CLI in a
subprocess for every tool call. A tool call can therefore run any of the
commands above, and the typed tools and the `mythify` escape-hatch tool
reach the same CLI. The server adds no execution path of its own.

Mythify is not a sandbox. It does not restrict what an agent asks it to run.
The boundary is the operating-system user that runs the CLI or the MCP
server. Mythify spawns no model, provider, or agent on its own; the only
agent command it runs is the one you pass to `outcome start --agent`.

Hardening guidance for users:

- Keep anything a review or a frozen path must cover out of `.gitignore`.
  The review fingerprint (`worktree_digest`) refuses a worktree with
  assume-unchanged or skip-worktree index entries and covers
  `.git/info/exclude` and the global excludes file, and outcome
  `--frozen-paths` hash the files on disk, but both leave out files that the
  repository's own `.gitignore` files ignore (a frozen path that names a
  file is covered even then).
- Set `MYTHIFY_DISABLE_RUN=1` to turn off command execution. Every command in
  the table above then refuses before running anything, records nothing, and
  exits 2. Any value other than empty, `0`, `false`, `no`, or `off` has the
  same effect. Case and surrounding whitespace are ignored, so `1 `, `TRUE`,
  `yes`, or a typo fails closed, and a value made only of spaces counts as
  empty and leaves execution on. Because MCP tools run the CLI, setting it in
  the MCP server's environment covers every tool.
- Never run the CLI or the MCP server with elevated privileges.
- Do not place secrets in commands, verifier output, memory entries, lessons,
  outcome notes, product records, or reflections. Everything under
  `.mythify/` is plain text on disk. Redaction of common token and password
  patterns in captured output is best effort, not a guarantee.

A report is in scope when Mythify does something other than what this model
describes: for example, executing a command while `MYTHIFY_DISABLE_RUN=1` is
set, running a command no tool call or CLI invocation asked for, writing
state outside the resolved `.mythify/` directory (other than global lessons
under `~/.mythify/lessons` from `lesson add --global` and the `.mythify/` line
that `init` adds to the project `.gitignore`), path traversal in
state-file handling, or a refusal gate (the strict step gate, a human-input
gate) that a crafted argument bypasses.
