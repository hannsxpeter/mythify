# Product planning

`mythify product` keeps a product record above execution plans. A plan says
how to build something; a product record says why it is worth building, for
whom, how anyone will know it worked, what is deliberately out, which bets are
in play, and which human decides each one.

## Who it is for

- A product manager or director who wants an agent to draft the product
  framing, but not to approve it.
- An engineer or agent who is handed "build X" and needs to know which outcome
  X is supposed to move before writing a plan.
- A team that wants its `ROADMAP.md` generated from a record that also
  tracks plans and measurements, so the roadmap cannot quietly drift from the
  work.

## The questions it forces

| Question | Where it lives |
| --- | --- |
| What problem, in the user's words, without naming the solution? | `--problem` on `product create` |
| Who is the one primary user? | `--user` on `product create` |
| What measurable outcomes, from where to where, and who set the target? | `product outcome` (at most five) |
| How will we read the metric without asking anyone? | `--measure` on `product outcome` |
| What are we not doing, why, and what would reopen it? | `product non-goal` |
| Which bets, linked to which outcomes, and what result kills each one? | `product bet` |
| Who decides continue, pivot, or stop, and by when? | `--decider` and `--decide-by` on `product bet` |
| Will they want it, can they use it, can we build it, does it work for the business? | `product risk --kind value, usability, feasibility, viability` |

## Worked example

Create the record. The stage sets how strict the readiness check is.

```bash
mythify product create "Activation onboarding" \
  --problem "New teams sign up but never invite a teammate, so they churn in week one" \
  --user "Team admin setting up a new workspace" \
  --appetite "six weeks" --stage pre-launch --name onboarding
```

Add outcomes, a non-goal, a bet, and a value risk:

```bash
mythify product outcome "More new teams invite a teammate in week one" \
  --metric "week-one invite rate" --baseline "18%" --target "35%" \
  --target-source decided \
  --measure "python3 scripts/metrics/invite_rate.py --at-least 0.35"

mythify product non-goal "Enterprise SSO" \
  --reason "No enterprise buyers in the target segment yet" \
  --revisit-when "the first enterprise deal reaches the pipeline"

mythify product bet "An invite step inside setup lifts week-one invites" \
  --outcome O1 --kill "invite rate under 22% two weeks after launch" \
  --decider "Dana, VP Product" --decide-by 2026-11-15 --priority 1

mythify product risk "Admins skip the invite step to finish setup faster" \
  --kind value --mitigation "Keep the step skippable but visible" \
  --validation "Five admin interviews before launch"
```

Lint readiness. `product check` exits 0 when ready and 2 when gaps remain, and
prints one line per gap naming the field, for example
`O1.target_source: say where the target came from ...`.

```bash
mythify product check
mythify product check --json   # {"ready": ..., "stage": ..., "gaps": [{code, item, message}]}
```

Show the one-pager to the human, then record their approval in their words:

```bash
mythify product show
mythify product approve --human-input "Dana: approved, ship the invite step first"
```

Promote the approved bet. The plan's goal is the bet's hypothesis, it carries
`product_ref {product, bet, outcomes}`, and its lineage parent is
`product:onboarding`. The bet goes in flight.

```bash
mythify product promote B1 --steps '[{"title": "Add the invite step to setup", "verify_command": "python3 -m unittest tests.test_setup"}]'
mythify plan verify 1
mythify step 1 completed "verify run exit 0: setup tests pass"
```

Measure the outcome. `product measure` runs the outcome's measure command
through the same recorded-verification path as `verify run`: hash-chained
ledger, provenance, retained output artifacts, redaction, timeout, and
`MYTHIFY_DISABLE_RUN=1` refusing to run. The record carries the parent
`product:onboarding` and the claim `O1 measured: week-one invite rate`. Exit 0
means the command passed; exit 2 means it ran and failed.

```bash
mythify product measure O1
```

When an in-flight bet's plan is complete and every outcome it targets has a
passing measurement, the bet is marked shipped. Take the evidence to the
decider and record their verdict:

```bash
mythify product decide B1 --verdict continue --human-input "Dana: numbers hold, keep going"
mythify product show --markdown > ROADMAP.md
```

## Human gates

- `product approve` and `product decide` refuse to run without a non-empty
  `--human-input`. The agent cannot approve product direction or set a bet
  verdict from its own words. The refusal exits 1 on the CLI and returns
  `isError` through MCP.
- `product approve` also refuses while `product check` reports gaps.
- Adding an outcome, non-goal, bet, or risk to an approved product returns it
  to draft and moves the old approval into `approval_history`. A bet the human
  never saw cannot be promoted under an older approval.
- `MYTHIFY_REQUIRE_HUMAN_INPUT=0` waives the human-input requirement the same
  way it does for HITL map tickets: the waiver is stamped on the record as
  `human_input_waived`, and `status` lists the opt-out as an attention item.
- `product promote` requires an approved product and a bet that is not stopped,
  and refuses a second promote of a bet while its plan file exists.
- `product measure` refuses an outcome with no measure command and a measure
  command that cannot fail (`true`, `exit 0`, a bare `echo`).

## Stage rules for check

Rules are cumulative: each stage adds to the ones before it.

| Stage | Adds |
| --- | --- |
| prototype | problem, user, at least one outcome, a metric and target on every outcome, at least one non-goal, every bet linked to existing outcomes |
| pre-launch | a target source (user, cited, decided, or hypothesis) on every outcome, at least one value risk |
| growth | an executable measure command on every outcome, a named decider on every bet |
| enterprise | a decide-by date on every bet, a mitigation on every risk, all four risk kinds recorded |

## Traceability flags

`product show` lists traces, not opinions:

- `outcome_without_bet`: an outcome no live bet targets.
- `approved_bet_without_plan`: the product is approved but a bet is still
  proposed with no plan.
- `completed_plan_unmeasured`: a bet's plan is complete, but an outcome it
  targets has no passing measurement.
- `verdict_overdue`: a bet's verdict is still pending and its `decide_by`
  parses as an ISO date earlier than today.

`status` shows the active product with its readiness and flag count;
`summary` lists every product. `--json` on `show` returns the record plus the
computed gaps, flags, measurements, and plan progress.

## How it relates to maps, plans, and godplans

- A map settles open questions when the route is foggy. Use it when you do not
  yet know what the product record should say; resolve the decision tickets,
  then write the product record.
- A product record decides what is worth building and who decides. Each
  approved bet becomes one plan through `product promote`.
- A plan is execution: steps, verify commands, and the strict completion gate.
  The plan keeps `product_ref` and lineage back to the product, so `lineage
  status plan NAME` reports when the product changed after promotion.
- A godplans `PLAN.mdx` with open tasks still imports with `plan import
  --source godplans`. Promote a bet when the work starts from a product
  decision; import when a godplans master plan already holds the tasks.

`mythify route` sends product-planning prompts (product plan, PRD, roadmap,
prioritize, target users, kill criteria, north star, go-to-market, what should
we build) to the `product` route, and `mythify prompt product` renders the
framing packet for the active product.
