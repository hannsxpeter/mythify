# Start Here

Mythify is an evidence protocol for AI coding agents. Its job is to leave behind
a durable answer to four questions, in language anyone can read:

- What was the goal?
- What changed?
- What was actually verified?
- What is still uncertain or unfinished?

You do not need to learn the whole command surface. Four commands cover the
first week: `route`, `report`, `verify run`, and `status`.

If your host supports local skills, the installer also installs `mythify-work`,
`mythify-route`, and `mythify-verify`. Use `$mythify-work` (Codex) or
`/mythify-work` (Claude Code) when you want the plan, the failed checks, the
verifiers, and the next actions narrated inside the chat as they happen.

## The one habit

If you take nothing else from this page: **a completion claim needs a command
behind it.** Not a stronger model, not a longer explanation. A command, its exit
code, and a record of both.

Everything below is machinery for making that habit cheap.

## One happy path

From a Mythify clone:

```bash
./scripts/install_user.sh --project /path/to/your/project
cd /path/to/your/project
mythify route "Fix the failing parser test"
mythify plan create "Fix the failing parser test" --steps '[{"title":"Reproduce and fix","success_criteria":"parser tests pass"}]'
mythify report --cursor chat --mark
mythify step 1 in_progress
```

Then do the normal engineering work. When you have a check to run:

```bash
mythify report --since last --cursor chat --format chat
mythify verify run "python3 -m unittest discover -s tests" --claim "parser tests pass"
mythify step 1 completed "verify run exit 0: parser tests pass"
mythify report --since last --cursor chat --format chat
mythify summary
```

That is the core product. Everything else is optional.

In chat, the same flow is one line:

```text
$mythify-work Fix the failing parser test
```

The skill tells the host agent to run that same durable loop while surfacing
`report --since last --cursor chat --format chat` after each step and verifier.

## Two commands worth understanding early

**`route`** picks the workflow shape from your prompt and your current durable
state. It can return direct, plan, research, review, outcome, campaign, failure
recovery, handoff, or prompt-packet routing. It only advises. It never executes
the work for you.

**`report`** is for while you work, not only at the end. It turns new Mythify
events into short chat-ready updates, then advances a cursor so repeated calls do
not replay the same evidence. Its `Attention` section pulls failed checks, failed
steps, and attested warnings up to the top, so problems land in the chat instead
of staying buried in logs.

- `--mark` at the start of a task sets the chat cursor without replaying old
  project history.
- `--since last` for every update after that.
- Do not combine `--mark` with `--since`. Mark first, then use `--since last`.
- `--peek` inspects the report without moving the cursor.

## Four workflows worth learning

### 1. Small fix

The task is clear and the check is obvious. Do not build ceremony around it.

```bash
mythify route "Fix typo in CLI help"
# If route says direct or fast, just do the edit.
mythify verify run "python3 -m unittest discover -s tests -v" --claim "CLI tests pass"
```

The point is not the plan. The point is that the completion claim has a command
behind it.

### 2. Serious change

Multiple steps, or a real chance of breaking something.

```bash
mythify plan create "Add package installer" --steps '[{"title":"Implement installer","success_criteria":"installer smoke test passes"},{"title":"Document installer","success_criteria":"docs link check passes"}]'
mythify step 1 in_progress
mythify report --since last --format chat
# implement
mythify verify run "rm -rf /tmp/mythify-install /tmp/mythify-project && mkdir -p /tmp/mythify-project && scripts/install_user.sh --prefix /tmp/mythify-install --project /tmp/mythify-project" --claim "installer smoke test passes"
mythify step 1 completed "verify run exit 0: installer smoke test passes"
mythify report --since last --format chat
```

Each completed step carries evidence, not confidence.

### 3. Foggy work

The effort is too big for one session and you do not yet know the steps, because
the decisions have not been made. Do not write a plan for decisions you have not
taken.

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
close without `--human-input`, because an agent that answers its own question has
proved nothing. `map promote` hands the settled destination, its decisions, and
its scope boundary to a plan, and the loop above takes over.

### 4. Release readiness

Before publishing, or before merging something broad.

```bash
mythify verify run "python3 -m unittest discover -s tests -v" --claim "Python suite passes"
mythify verify run "npm test --prefix mcp-server" --claim "MCP suite passes"
mythify verify run "python3 scripts/mythify.py readiness --json" --claim "readiness report generated"
mythify readiness
```

`readiness` is a dashboard over recorded evidence. It reports what has been
proven. It does not make the release safe by itself.

## What to ignore at first

Skip fanout, host model switching, provider probes, remote execution, lifecycle
adapters, and most of the MCP tool set. Those are power-user surfaces and none of
them are load-bearing on day one.

Skip `classify` too, unless you specifically need classification on its own. For
ordinary chat work, `route` wraps classification with durable state and returns
the next workflow move.

## When to add MCP

The CLI is enough for any shell-capable agent. Add the MCP server when your host
calls tools directly, when you want desktop sessions to share the same
`.mythify/` state, or when you need an MCP-only surface such as fanout.

For Codex, after running the installer:

```bash
codex mcp add mythify \
  --env MYTHIFY_DIR=/path/to/your/project/.mythify \
  --env MYTHIFY_HOST_PLATFORM=codex-desktop \
  -- /path/to/prefix/bin/mythify-mcp
```

Use absolute paths in MCP configs.
