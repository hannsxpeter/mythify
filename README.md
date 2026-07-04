# Mythify

[![CI](https://github.com/aihxp/mythify/actions/workflows/ci.yml/badge.svg)](https://github.com/aihxp/mythify/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**An evidence protocol for AI coding agents.** Mythify makes an AI agent plan
its work, keep notes outside the chat, and prove each claim by running a real
command, not by saying "done." It is the difference between an agent that
*tells* you the tests pass and one that *shows* you the green exit code.

Mythify does not make the model smarter. It makes the model honest and
organized: it improves the harness around the agent, not the agent itself.

New here? This page is written for you. Read it top to bottom and you will be
running Mythify in a few minutes.

---

## Why Mythify exists

Left alone, coding agents drift into two bad habits:

1. **They declare victory too early.** "I fixed the bug" with no test run behind
   it. Mythify refuses to mark work complete until a command actually passes.
2. **They forget.** Long tasks blow past the chat's memory and lose the plan,
   the goal, the lessons learned. Mythify writes all of that to disk so any
   later session can pick up exactly where the last one stopped.

Everything Mythify does serves one rule: **executed evidence beats confident
prose.** A passing exit code is proof; an agent's optimism is not.

## The core idea in 30 seconds

Mythify runs a small loop:

```
PLAN  ->  ACT  ->  VERIFY  ->  (fix and repeat, or move on)
```

- **PLAN**: break the goal into steps, each with a way to check it is done.
- **ACT**: do one step.
- **VERIFY**: run a real command (tests, a build, a lint, a file check). The
  exit code decides whether the step is really done.
- Everything, including the plan and the evidence, lives in a `.mythify/`
  folder in your project, so it survives across sessions.

That is the whole product. The rest is convenience around that loop.

## Install (2 minutes)

Mythify is a checkout you install locally. You need Python 3.9+ (for the CLI)
and, optionally, Node 18+ (for the MCP server that plugs into agent tools).

```bash
git clone https://github.com/aihxp/mythify.git
cd mythify
./scripts/install_user.sh --project /path/to/your/project
```

That installs a `mythify` command and sets up your project. If you would rather
not install anything, you can run the CLI straight from the checkout with
`python3 scripts/mythify.py ...` from your project directory.

There is no `npm install mythify` and no account to create. Mythify is
zero-dependency Python plus a small optional Node server.

## Your first loop

From inside your project:

```bash
mythify init                       # create the .mythify/ state folder (once)

# Make a plan whose one step knows how to prove itself:
mythify plan create "Fix the failing parser test" \
  --steps '[{"title":"Reproduce and fix","success_criteria":"parser tests pass","verify_command":"python3 -m unittest discover -s tests"}]'

mythify step 1 in_progress          # start the step
# ... you (or your agent) do the actual work ...

mythify plan verify 1               # runs the step's verify command for you
mythify step 1 completed "verify run exit 0: parser tests pass"
```

That last `step ... completed` only succeeds if a real verification passed
first. Try to complete a step with nothing but a sentence and Mythify says no.
That refusal is the entire point.

At any time:

```bash
mythify status      # where am I, what is next
mythify report      # a chat-friendly play-by-play of recent progress
mythify summary     # the full session: plans, evidence, lessons
```

## The pieces, gently

You do not need all of these on day one. Reach for them as tasks get bigger.

### Plans and steps

A **plan** is a goal plus ordered **steps**. Each step can carry a
`verify_command`: the exact command that proves it is done. `plan verify ID`
runs that command and records the result against the step; then
`step ID completed` passes because the evidence exists. This is the "definition
of done is a check" idea, made concrete.

### Verification: proof, not promises

- `verify run "COMMAND"` runs a command and records the exit code as evidence.
- `verify claim "..."` records a plain-English claim when nothing is runnable.
  It is always marked second-class and never counts as real proof.

By default, completing a step requires a real `verify run` that passed after the
step started. Set `MYTHIFY_REQUIRE_VERIFIED_STEP=0` only if you knowingly want
the old prose-only behavior.

### Memory and lessons

`memory set` / `memory get` store facts, decisions, and discoveries.
`lesson add` records something you learned the hard way. Both persist on disk,
so a fresh session starts informed instead of blank.

### Routing: "what should I even do?"

Not sure which tool fits? `mythify route "your task"` reads your request and
your current state and recommends the next move (just answer, make a plan,
start a loop, review, and so on). It only advises; it never acts on its own.

## Autonomous loops (new in 4.0)

Sometimes you want the agent to keep trying on its own until a check passes.
Mythify can do that, **safely and with a leash**:

```bash
mythify outcome start "make the suite green" \
  --success "all tests pass" \
  --verify "python3 -m unittest discover -s tests" \
  --agent "your-agent-cli --do-the-work" \
  --max-iterations 5 \
  --max-cost 100 \
  --escalate-after 3 \
  --allowed-paths "src,tests"

mythify outcome run                 # drives the loop by itself
```

Each round the loop fires your `--agent` command, runs the verifier, records the
evidence, and repeats. It stops the moment any of these happens:

- **Success**: the verifier passes.
- **Iteration budget**: it hit `--max-iterations`.
- **Cost budget**: cumulative cost reached `--max-cost` (your agent reports cost
  with a `MYTHIFY_COST=<n>` line; otherwise each round costs one unit).
- **Scope violation**: the agent changed files outside `--allowed-paths`
  (enforced for real, via git).
- **Escalation**: it failed the verifier `--escalate-after` times in a row, so
  it hands back to you.

The loop never declares success without the verifier, and it can never run
unbounded. Autonomy, but on Mythify's terms.

## Working from existing plans and audits

If you use [godplans](https://github.com/aihxp/godplans) or
[godaudits](https://github.com/aihxp/godaudits), Mythify reads their
`.godplans/PLAN.mdx` and `.godaudits/AUDIT.mdx` files directly:

```bash
mythify plan import --source godplans   # turn the plan's tasks into a Mythify plan
```

Each imported task keeps its exact verify command, so executing the plan is the
same verify-gated loop as everything else. Mythify never edits those files; it
just reads them and holds the evidence trail.

## Running many agents at once (MCP server)

The optional Node MCP server exposes Mythify's state to agent tools and adds
**fanout**: run several independent agent tasks in parallel. Writing tasks can
use `isolation: "worktree"` so each runs in its own git worktree on a fresh
branch and cannot collide with the others; you merge the branches you want.

The MCP server shares the exact same `.mythify/` folder as the CLI, so a plan
made in one is visible in the other. It exposes Mythify through 41 MCP tools;
the full list is in [docs/design.md](docs/design.md).

## Feeling native in chat

Three chat skills make Mythify feel like a built-in command in your agent:

- `/mythify-work` (Claude Code) or `$mythify-work` (Codex): a visible
  step-by-step work loop.
- `/mythify-route`: show the recommended next move.
- `/mythify-verify`: turn a claim into real evidence and report the verdict.

## Command reference

The everyday commands:

| Command | What it does |
| :--- | :--- |
| `init` | Create the `.mythify/` folder (run once per project). |
| `route "TASK"` | Recommend the next workflow move (read-only). |
| `plan create GOAL [--steps JSON]` | Create a plan; steps may include `verify_command`. |
| `plan add-step TITLE [--verify CMD]` | Add a step, optionally with its check. |
| `plan verify ID` | Run a step's own check and record scoped evidence. |
| `plan import [--source godplans\|godaudits]` | Import a PLAN.mdx / AUDIT.mdx as a plan. |
| `step ID STATUS [RESULT]` | Update a step; `completed` needs a passing verify. |
| `verify run "CMD" [--claim ...]` | Run a command and record the exit code as evidence. |
| `outcome start GOAL --success ... --verify ...` | Start a verifier-backed loop (add `--agent` to self-drive). |
| `outcome run` | Drive a self-driving loop to success or a bounded stop. |
| `memory set/get`, `lesson add/list` | Persist facts, decisions, and lessons. |
| `status`, `report`, `summary` | Orient, narrate progress, and wrap up. |

There are more surfaces (campaigns, research, dashboards, model policy, trace
analysis, and the full MCP tool set). The complete, exhaustive reference lives
in [docs/design.md](docs/design.md); a quick tour is in
[docs/start-here.md](docs/start-here.md).

## How it is built

Two runtimes over one state folder:

- **CLI** (`scripts/mythify.py` and friends): zero-dependency Python 3.9+.
- **MCP server** (`mcp-server/`): Node 18+, exposes the same state as MCP tools
  plus fanout.

Both read and write the same `.mythify/` directory, so they always agree. The
protocol text itself (`protocol/PROTOCOL.md`) is the source for the drop-in
rules files `CLAUDE.md`, `AGENTS.md`, and `.cursorrules`.

## Learn more

- [docs/start-here.md](docs/start-here.md) - the shortest path to using Mythify.
- [docs/design.md](docs/design.md) - the complete design and command reference.
- [CHANGELOG.md](CHANGELOG.md) - what changed in each release.
- [CONTRIBUTING.md](CONTRIBUTING.md) - how to contribute.

## License

MIT. See [LICENSE](LICENSE).
