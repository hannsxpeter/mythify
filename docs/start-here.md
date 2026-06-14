# Start Here

Mythify is an evidence protocol for AI coding agents. It helps an agent leave
behind a durable answer to four questions:

- What was the goal?
- What changed?
- What was actually verified?
- What remains uncertain or unfinished?

You do not need to learn every command first. Start with one loop.

## One Happy Path

From a Mythify checkout:

```bash
./scripts/install_user.sh --project /path/to/your/project
cd /path/to/your/project
mythify classify "Fix the failing parser test"
mythify plan create "Fix the failing parser test" --steps '[{"title":"Reproduce and fix","success_criteria":"parser tests pass"}]'
mythify step 1 in_progress
```

Then do the normal engineering work. When you have a check:

```bash
mythify report --since last --format chat
mythify verify run "python3 -m unittest discover -s tests" --claim "parser tests pass"
mythify step 1 completed "verify run exit 0: parser tests pass"
mythify report --since last --format chat
mythify summary
```

That is the core product. Everything else is optional.

Use `report` while you work, not only at the end. It turns new Mythify events
into short chat-ready updates, then advances a cursor so repeated calls do not
repeat the same evidence. Use `--peek` when you want to inspect the report
without moving the cursor.

## Three Workflows Worth Learning

### 1. Small Fix

Use this when the task is clear and the verifier is obvious.

```bash
mythify classify "Fix typo in CLI help"
# If classification says fast, do the edit.
mythify verify run "python3 -m unittest discover -s tests -v" --claim "CLI tests pass"
```

The point is not ceremony. The point is that the completion claim has a command
behind it.

### 2. Serious Change

Use this when the work has multiple steps or could regress behavior.

```bash
mythify plan create "Add package installer" --steps '[{"title":"Implement installer","success_criteria":"installer smoke test passes"},{"title":"Document installer","success_criteria":"docs link check passes"}]'
mythify step 1 in_progress
mythify report --since last --format chat
# implement
mythify verify run "rm -rf /tmp/mythify-install /tmp/mythify-project && mkdir -p /tmp/mythify-project && scripts/install_user.sh --prefix /tmp/mythify-install --project /tmp/mythify-project" --claim "installer smoke test passes"
mythify step 1 completed "verify run exit 0: installer smoke test passes"
mythify report --since last --format chat
```

Each completed step gets evidence, not just confidence.

### 3. Release Readiness

Use this before publishing or merging broad changes.

```bash
mythify verify run "python3 -m unittest discover -s tests -v" --claim "Python suite passes"
mythify verify run "npm test --prefix mcp-server" --claim "MCP suite passes"
mythify verify run "python3 scripts/mythify.py readiness --json" --claim "readiness report generated"
mythify readiness
```

`readiness` is a dashboard over recorded evidence. It does not make the release
safe by itself.

## What To Ignore At First

Do not start with fanout, host model switching, provider probes, remote
execution, lifecycle adapters, or every MCP tool. Those are power-user surfaces.
The first habit is simple: plan when useful, run checks, record evidence.

## When To Add MCP

The CLI is enough for shell-capable agents. Add the MCP server when your host
can call tools directly, when you want desktop sessions to share `.mythify/`
state, or when you need MCP-only surfaces such as fanout.

For Codex after running the installer:

```bash
codex mcp add mythify \
  --env MYTHIFY_DIR=/path/to/your/project/.mythify \
  --env MYTHIFY_TRIAGE_ENGINE=codex-cli \
  --env MYTHIFY_FANOUT_ENGINE=codex-cli \
  -- /path/to/prefix/bin/mythify-mcp
```

Use absolute paths in MCP configs.
