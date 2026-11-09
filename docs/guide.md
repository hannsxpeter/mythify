# Feature guide

Mythify is built around one loop: plan, act, verify. This guide covers each
part in the order you tend to need it. You do not need all of it on day one.
The shortest path to a working loop is [start-here.md](start-here.md), and
every command and flag is in [commands.md](commands.md).

## Plans and steps

A **plan** is a goal plus ordered **steps**. Each step can carry a
`verify_command`: the exact command that proves it is done. `plan verify ID`
runs that command and files the result against the step, which is what lets
`step ID completed` succeed. Definition of done is a check you can run.

`mythify step ID completed` needs a RESULT and a passing `verify run` recorded
after the step started. If the step stores a `verify_command`, the recorded
command must match it. `MYTHIFY_REQUIRE_VERIFIED_STEP=0` waives the gate, and
the waiver is stamped on the step.

For work that spans sessions, `mythify campaign start "GOAL" --plan NAME`
attaches to an existing plan, and `mythify campaign prompt NAME` resumes it.
Campaigns reuse the plan's steps and verification records and complete steps
only through the strict evidence gate. See [campaigns.md](campaigns.md).

## Verification: proof, not promises

`verify run "COMMAND"` runs a command and records the exit code as evidence,
with the output, the git commit and a fingerprint of uncommitted changes, and a
hash chain that makes later edits to the ledger visible in `status`. Exit 0
records `VERIFIED`; a failing command exits 2 and records `UNVERIFIED`.

`verify claim "CLAIM" "EVIDENCE"` records a statement when nothing can run. It
is marked second-class and never counts as proof.

### Reuse

`--reuse` records an earlier passing run instead of running the command again,
but only when the command, git commit, worktree fingerprint, and environment
all match. It does not see ignored files, services, or the network. `plan
verify --reuse` works the same way.

### Named checks and the verify gate

`verify check NAME` runs a named command from `.mythify/checks.json` (define it
with `--set`), optionally on only the changed files through a `{files}`
placeholder. A step can use `@NAME` as its `verify_command`. `verify gate` is a
read-only check for a host's stop or pre-commit hook: it runs nothing and exits
2 while an in-progress step lacks current passing evidence. See
[checks-and-gate.md](checks-and-gate.md).

### Checks that cannot fail

Mythify warns when a step or ticket stores `true`, `exit 0`, or a bare `echo`
as its verifier, and `status` flags passing runs that checked nothing or ran
zero tests.

### Test-count drops

A run that executes fewer tests than the last passing run is flagged. A plan
started by `campaign start` refuses to complete on it unless you pass
`--accept-test-drop REASON`; other plans warn.

## Maps: deciding before planning

When the route to the finish is not visible yet, a plan is the wrong tool. A
**map** holds the questions to settle instead. Each ticket is a question with a
type that decides who may answer it: `research` and `task` tickets the agent
can close; `grilling` and `prototype` tickets, and task tickets added with
`--mode hitl`, need `--human-input` with the person's words. When no open
ticket and no fog remain, `map promote` hands the settled destination, its
decisions, and its out-of-scope list to a plan.
The design is adapted from Matt Pocock's
[wayfinder skill](https://github.com/mattpocock/skills/blob/main/skills/engineering/wayfinder/SKILL.md).

## Outcome loops: autonomy on a leash

```bash
mythify outcome start "make the suite green" \
  --success "all tests pass" \
  --verify "python3 -m unittest discover -s tests" \
  --max-iterations 5 --escalate-after 3 --allowed-paths "src,tests"
mythify outcome check               # after each attempt
```

Each `outcome check` runs the verifier and records the attempt. The loop ends
at success, at the iteration budget, or at a change inside `--frozen-paths`
(checked by hashing the files on disk). A change outside `--allowed-paths` is
named in the next action.

Start the loop with `--agent "COMMAND"` and run `mythify outcome run` to let
Mythify fire that command each round; it can be any program, an agent CLI or a
plain script. `outcome run` also stops on a change outside `--allowed-paths`,
after `--escalate-after` failures in a row (handing back to you), and when the
total cost the command reports with `MYTHIFY_COST=<n>` reaches `--max-cost`.
Each round's iteration and a default cost of 1 are charged before the command
starts, so a run that is killed or times out still spends its round.

## Blast-radius safety cases

When the dangerous behavior sits outside the visible diff, `review blast-radius`
records one safety fact, an exact source fingerprint, the risks, and the
cheapest command that would catch the real failure. `review prove` runs that
command and links the evidence only while the reviewed tree is unchanged. See
[blast-radius.md](blast-radius.md).

## Findings reviews and path risk

A findings review records an independent reviewer's rounds: `review round`
opens one stamped with the source it saw, `review finding` and `review resolve`
track what was raised and settled (a dismissed finding cannot be raised
again), and a fourth round needs `--rethink TEXT`.

A step that touches high-risk paths (auth, secrets, migrations, schema, deploy
and CI config, payments) needs a review that covers the current source and the
person's own words (`--human-input`) before it completes. Like the test-count
drop, this check warns by default and refuses on a campaign plan;
`MYTHIFY_RISK_GATE=0` waives it with a stamp. See [reviews.md](reviews.md).

## Memory and lessons

`memory set KEY VALUE --category fact|decision|discovery|state` and `memory get
QUERY` hold what cost effort to learn. `lesson add TITLE DETAIL` records
something learned the hard way; `--global` makes it apply to every project. The
protocol tells the agent to read both at the start of a session, so a fresh
session starts informed instead of blank.

## Routing: "what should I even do here?"

`mythify route "your task"` reads your request alongside your current state and
recommends one move: just do it, plan, map, outcome loop, review, recover from
a failure, or resume. It advises only. `mythify loop-fit` answers a narrower
question: should this run hands-off, supervised, or by hand? `route --json`
also rates the paths you have already changed (`classification.diff_risk`) and
asks for an independent review when they are riskier than your wording.

When `route` sees work that splits into independent parts, it says so
(`parallelism.fit`). Your agent decides whether to use subagents, which ones,
and on which models. Whatever comes back is material, not proof: it gets
merged, and the merged result is verified like anything else.

### The agent policy block

If you keep a routing policy file of your own, `route` shows what it says about
the task. Set `MYTHIFY_AGENT_POLICY` to its path, or leave it at
`~/.agents/policy.json`. With a file present, the text output ends with a
`Delegation` section and `--json` gains a `delegation` object: the policy step
the task falls under (production, question, design, image, bulk,
browser_check, or build), that step's action and role, the options and effort
the file lists for the role, and the review tier for the task's risk.

It is a copy of your file, not a choice. Options your own banned-model list
names are left out, an effort above your ceiling (or one your file forbids) is
lowered to the highest allowed level, and any banned model name or forbidden
effort word in the text it prints is replaced with `[withheld]` (a name
containing a banned model or forbidden effort word is partly withheld).

Step 1 (production, deploys, servers, or money) prefers recall: a clause whose
main verb is an operation verb (deploy, ship, release, publish, restart, roll
back, and the like), ssh, scp, or rsync as the main verb (whatever follows), or
an explicit money phrase is step 1, with or without an environment word. Only
clear code work on a component stays out ("write a deploy script", "add a
--deploy flag"), and an ambiguous prompt goes to step 1. Step 1 and wording
that names risky code (auth, billing, checkout, migration, secrets, and
similar) raise the printed review tier to at least high, and the block says
why.

A missing file changes nothing. An unreadable or invalid file prints one
`[WARN]` line and is ignored. A minimal file, with invented names:

```json
{
  "version": 1,
  "routing": [
    {"step": 1, "id": "production", "when": "Deploys or money?", "role": null, "action": "stop_and_ask"},
    {"step": 7, "id": "build", "when": "Anything that writes code?", "role": "builder", "action": "delegate"}
  ],
  "roles": {
    "builder": {
      "options": [{"tool": "Appone", "model": "Modelone"}],
      "effort": {"default": "medium", "tricky": "high"}
    }
  },
  "review_tiers": [
    {"id": "low", "reviewer_effort": null, "user_review": false},
    {"id": "normal", "reviewer_effort": "high", "user_review": false},
    {"id": "high", "reviewer_effort": "xhigh", "user_review": true}
  ],
  "review_rounds_max": 3,
  "hard_rules": {"banned_models": ["Modelzero"], "effort_ceiling": "xhigh", "never_efforts": ["max"]}
}
```

## Drop-in protocol files

`AGENTS.md` is the protocol your agent reads: a short spine of rules, the loop,
and the command list. Copy it into your project root. `CLAUDE.md` is a short
pointer that imports it with an `@AGENTS.md` line, for hosts that look for that
name; copy it too if your host reads it. If your project already has a
`CLAUDE.md`, do not overwrite it. Add an `@AGENTS.md` line to it if you want it
to load the protocol, and check with `mythify protocol check AGENTS.md`,
because a bare `protocol check` also checks `CLAUDE.md` and fails on any file
that is not the generated pointer. Both files are generated from
`protocol/PROTOCOL.md`, and `mythify protocol check`, run in your project,
exits 1 when a copy no longer matches your installed CLI.

## Agent tooling (MCP)

`mythify mcp`, installed as `mythify-mcp`, serves Mythify as MCP tools over
stdio. Every tool runs the CLI underneath, so the evidence rules are identical.
See [mcp.md](mcp.md) for the config block, where common hosts keep it, the tool
list, and how exit codes map to tool errors.

## Standalone archive, uninstalling, and skills

Download `mythify-cli-VERSION.tar.gz` from a
[release](https://github.com/hannsxpeter/mythify/releases/latest), unpack it,
and run its `scripts/install_user.sh`. The archive is deterministic: the same
source tree always produces the same bytes (`python3 scripts/package_cli.py`).

`mythify-uninstall` removes what the installer added and nothing else. An
ownership manifest records a content hash for each launcher and a per-install
token in each directory the installer owns (the runtime and each skill
folder). If a launcher's hash or a directory's token is missing or changed,
uninstall stops without deleting anything. Files you edit inside an owned
directory are not hashed: uninstall removes them with the directory, so copy
out any skill you customized first. Project `.mythify` folders are never
touched.

The installer also copies four chat skills (`mythify`, `mythify-work`,
`mythify-route`, `mythify-verify`) into the `skills` folder of each agent
config directory it finds in your home directory, or into `~/.agents/skills`
when it finds none. Name the folders yourself with `--skills-root PATH`, or
skip them with `--skip-skills`. Invoke the skills the way your host runs
skills.

`mythify report --metrics` adds read-only flow numbers (runs, failures, reused
runs, seconds saved, time to first failure and first pass, findings rounds)
without moving the report cursor; `--json` and `--plan NAME` are available.
