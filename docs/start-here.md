# Start Here

Mythify is an evidence protocol for AI coding agents. It helps an agent leave
behind a durable answer to four questions:

- What was the goal?
- What changed?
- What was actually verified?
- What remains uncertain or unfinished?

You do not need to learn every command first. Start with the reduced surface:
`route`, `report`, `verify run`, and `status`.

If your host supports local skills, the checkout installer also installs
`mythify-work`, `mythify-route`, and `mythify-verify`. Use `$mythify-work` when
you want the Godpowers-style experience where the plan, failed checks,
verifiers, and next actions are narrated inside the chat.

## One Happy Path

From a Mythify checkout:

```bash
./scripts/install_user.sh --project /path/to/your/project
cd /path/to/your/project
mythify route "Fix the failing parser test"
mythify plan create "Fix the failing parser test" --steps '[{"title":"Reproduce and fix","success_criteria":"parser tests pass"}]'
mythify report --cursor chat --mark
mythify step 1 in_progress
```

Then do the normal engineering work. When you have a check:

```bash
mythify report --since last --cursor chat --format chat
mythify verify run "python3 -m unittest discover -s tests" --claim "parser tests pass"
mythify step 1 completed "verify run exit 0: parser tests pass"
mythify report --since last --cursor chat --format chat
mythify summary
```

That is the core product. Everything else is optional.

In chat, the same flow is shorter:

```text
$mythify-work Fix the failing parser test
```

The skill tells the host agent to run the same durable loop while surfacing
`report --since last --cursor chat --format chat` after steps and verifiers.

Use `route` when you want Mythify to choose the workflow shape from the prompt
and durable state. It may return direct, plan, research, review, outcome,
campaign, failure recovery, handoff, or prompt-packet routing, but it does not
execute the work for you.

Use `report` while you work, not only at the end. It turns new Mythify events
into short chat-ready updates, then advances a cursor so repeated calls do not
repeat the same evidence. Use `--mark` at the start of a task to set a chat
cursor without replaying old project history. Do not combine `--mark` with
`--since`: mark first, then use `--since last` for later updates. Its
`Attention` section calls out failed checks, failed steps, and attested warnings
so issue lists can be copied into the chat instead of staying hidden in logs.
Use `--peek` when you want to inspect the report without moving the cursor.

## Four Workflows Worth Learning

### 1. Small Fix

Use this when the task is clear and the verifier is obvious.

```bash
mythify route "Fix typo in CLI help"
# If route says direct or fast, do the edit.
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

### 3. Foggy Work

Use this when the effort is too big for one session and you do not yet know
what the steps are, because the decisions have not been made.

```bash
mythify map create "Ship a billing revamp spec" --fog "how do existing subscriptions migrate"
mythify map ticket "Pick the proration model" --type grilling
mythify map claim T1
# hold the actual conversation with the human
mythify map resolve T1 --answer "Daily proration with a monthly true-up" \
  --human-input "the human chose daily with a monthly true-up"
mythify map show
# when no ticket and no fog remain:
mythify map promote
```

One decision ticket per session. A `grilling` or `prototype` ticket will not
close without `--human-input`, because an agent that answers its own question
has proved nothing. `map promote` hands the settled destination, its decisions,
and its scope boundary to a plan, and the loop above takes over.

### 4. Release Readiness

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

Do not start with `classify` unless you only need classification. For ordinary
chat work, `route` wraps classification with durable state and returns the next
workflow move.

## When To Add MCP

The CLI is enough for shell-capable agents. Add the MCP server when your host
can call tools directly, when you want desktop sessions to share `.mythify/`
state, or when you need MCP-only surfaces such as fanout.

For Codex after running the installer:

```bash
codex mcp add mythify \
  --env MYTHIFY_DIR=/path/to/your/project/.mythify \
  --env MYTHIFY_HOST_PLATFORM=codex-desktop \
  -- /path/to/prefix/bin/mythify-mcp
```

Use absolute paths in MCP configs.
