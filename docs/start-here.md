# Start here

Mythify makes an AI coding agent back every "done" with a command it actually
ran. This page is the shortest path from nothing to a working loop, then the
four workflows worth learning, then what you can ignore.

## The one habit

**A completion claim needs a command behind it.** Not a stronger model and not
a longer explanation: a command, its exit code, and a record of both.
Everything below makes that habit cheap.

## 1. Install

You need Python 3.9 or newer. Nothing else.

```bash
git clone https://github.com/hannsxpeter/mythify.git
cd mythify
./scripts/install_user.sh --project /path/to/your/project
```

The installer puts `mythify`, `mythify-mcp`, and `mythify-uninstall` in
`~/.local/bin` (change it with `--prefix`), copies a self-contained runtime
under `~/.local/share/mythify/`, and runs `mythify init` in the project you
named. Add `~/.local/bin` to your `PATH` if the installer warns about it.

No install? Run `python3 /path/to/mythify/scripts/mythify.py COMMAND` from your
project directory instead of `mythify COMMAND`.

To have your agent follow the protocol, copy `AGENTS.md` from the Mythify
repository into your project root. If your host reads `CLAUDE.md` instead, copy
that file too: it is a short pointer that imports `AGENTS.md`. Run
`mythify protocol check` in the project to confirm the copies match your
installed CLI.

## 2. Run one loop

From your project directory:

```bash
mythify init      # once per project; skip it if the installer ran with --project

mythify plan create "Fix the failing parser test" \
  --steps '[{"title":"Reproduce and fix","success_criteria":"parser tests pass","verify_command":"python3 -m unittest discover -s tests"}]'

mythify step 1 in_progress
# ... you, or your agent, do the work ...
mythify plan verify 1
mythify step 1 completed "verify run exit 0: parser tests pass"
```

`verify_command` is the step's definition of done, written as a command.
`plan verify 1` runs it and records the exit code against step 1. Only then does
`step 1 completed` succeed. Try the last line before `plan verify` and Mythify
refuses with exit 1:

```text
[FAIL] Verified evidence required: strict evidence mode is enabled by default,
but no passing executed 'verify run' with exit code 0 was recorded since this
step started. ...
```

That refusal is the product.

## 3. Read the state

```bash
mythify status     # active plan, next step, evidence counts, attention items
mythify report     # new events since the last report, as chat-ready lines
mythify summary    # plans, outcomes, maps, products, memory, lessons, evidence
```

`status` lists failed checks, checks that passed without testing anything, and
ledger edits under `Attention`, issues first. `report` advances a cursor so the
next call shows only new events. Run `mythify report --mark` at the start of a
task to skip old history, `--since last` for each update after that, and
`--peek` to look without moving the cursor. For machine-readable output,
`status` and `summary` take `--json` and `report` takes `--format json`.

## 4. Four workflows worth learning

Not sure which one fits? `mythify route "TASK"` reads the request and the state
on disk and recommends one: `direct`, `plan`, `map`, `product`, `outcome`,
`review`, `failure_recovery`, or `handoff`. It advises and never acts.

### Small fix

The task is clear and the check is obvious. Skip the plan.

```bash
mythify route "remove an unused import in utils.py"    # Workflow route: direct
# make the edit
mythify verify run "python3 -m unittest discover -s tests" --claim "tests still pass"
```

`verify run` exits 0 and records `VERIFIED` when the command passes, and exits
2 and records `UNVERIFIED` when it fails. Either way the record stays in
`.mythify/verifications.jsonl`. When `route` answers `plan` but reports
`profile=fast`, the same short path applies.

### Serious change

Several steps, or a real chance of breaking something. Give every step its own
check.

```bash
mythify plan create "Add CSV export" --steps '[
  {"title":"Write the exporter","success_criteria":"exporter tests pass","verify_command":"python3 -m unittest tests.test_export"},
  {"title":"Wire the CLI flag","success_criteria":"CLI tests pass","verify_command":"python3 -m unittest tests.test_cli"}
]'
mythify plan add-step "Document the flag" --criteria "docs build" --verify "python3 scripts/build_docs.py"

mythify step 1 in_progress
# implement
mythify plan verify 1
mythify step 1 completed "verify run exit 0: exporter tests pass"
```

When a check fails, fix the cause and verify again; never advance on red.
Record what you learned so the next session does not repeat it:

```bash
mythify reflect --action "ran exporter tests" --outcome failure \
  --observation "2 of 9 failed on empty rows" --root-cause "no guard for empty input" \
  --next "add the guard, re-run plan verify 1"
mythify memory set export-format "RFC 4180, UTF-8, header row" --category decision
mythify lesson add "Empty rows" "The CSV writer drops empty rows unless quoting is forced"
```

### Foggy work: a map

The effort is too big for one session and the steps are unknown because the
decisions are not made yet. Do not write a plan for decisions nobody has
taken. Chart a map of questions instead.

```bash
mythify map create "A billing revamp spec the team can build from" \
  --fog "how existing subscriptions migrate"
mythify map ticket "Pick the proration model" --type grilling
mythify map ticket "Does the gateway support partial refunds" --type research
mythify map claim T1
# ask the person, wait for the answer
mythify map resolve T1 --answer "Daily proration" \
  --human-input "Dana: prorate daily, true up monthly"
mythify map show
```

The ticket type decides who may answer. `research` and `task` tickets the
agent can close. `grilling` and `prototype` tickets, and task tickets added
with `--mode hitl`, are refused without `--human-input` carrying the person's
words. Fog that sharpens becomes a ticket with
`map ticket TITLE --type TYPE --from-fog F1`, and work past the destination is
ruled out with `map scope-out NOTE --reason TEXT`. When no open
ticket and no fog remain, `mythify map promote` creates a plan that carries
every decision.

### Product direction: a product plan

Before a large build, settle why it exists, for whom, and how anyone will know
it worked.

```bash
mythify product create "Self-serve onboarding" \
  --problem "New teams wait two days for a manual account setup" \
  --user "Team admin signing up without a sales call" --stage pre-launch
mythify product outcome "Admins reach a working workspace the same day" \
  --metric "share of signups with a workspace within 24h" \
  --baseline "12%" --target "60%" --target-source decided \
  --measure "python3 scripts/onboarding_metric.py --min 0.60"
mythify product non-goal "Enterprise SSO" --reason "Needs sales-led contracts"
mythify product bet "A guided setup wizard removes the manual step" \
  --outcome O1 --kill "under 30% same-day setup after 200 signups" \
  --decider "Head of Product"
mythify product risk "Admins would rather wait for a person than set up alone" \
  --kind value --validation "interview five admins who stalled in setup"
mythify product check       # exit 2 lists the gaps; exit 0 when ready
mythify product show        # the one-pager to put in front of the decider
```

Then a person approves it, and only then does a bet become a plan:

```bash
mythify product approve --human-input "Approved. Ship the wizard first."
mythify product promote B1   # creates a plan whose goal is the bet
mythify product measure O1   # after shipping: runs the measure command as evidence
```

`product approve` and `product decide` are refused without the person's words.
The stage (`prototype`, `pre-launch`, `growth`, `enterprise`) sets how strict
`product check` is. [product-planning.md](product-planning.md) covers the rules
for each stage, bet verdicts, and traceability flags.

## 5. What to ignore at first

None of these are needed on day one:

- `outcome start`, `outcome check`, and `outcome run`: verifier-backed retry
  loops with iteration, cost, and scope budgets. Reach for them when one check
  must go green and you want the agent to keep trying inside a fence.
- `review blast-radius` and `review prove`: safety cases for changes whose
  risk sits outside the diff. See [blast-radius.md](blast-radius.md).
- `lineage`, `prompt`, `loop-fit`, `history`, `logs compact`, and
  `plan import`.
- `MYTHIFY_REQUIRE_VERIFIED_STEP=0`. It turns off the evidence gate, and the
  waiver is stamped on every step it touches. Leave it alone unless you know
  exactly what you are giving up.

Every command takes `--help`. The full list is in [commands.md](commands.md).

## 6. Chat skills

The installer also copies four skills into your agent's skills folders:
`mythify`, `mythify-work`, `mythify-route`, and `mythify-verify`. Invoke them
the way your host runs skills. `mythify-work` runs the loop above in chat and
reports each plan, check, and failure as it happens; `mythify-verify` turns a
claim into a recorded check. Use `--skills-root PATH` to choose the folders, or
`--skip-skills` to skip them.

## 7. When to add MCP

The CLI is enough for any agent that can run shell commands. Add the MCP
server when your host calls tools instead of a shell, or when you want typed
tool schemas. The installer already put `mythify-mcp` next to `mythify`;
[mcp.md](mcp.md) shows the config block, where common hosts keep it, and how
exit codes map to tool errors.
