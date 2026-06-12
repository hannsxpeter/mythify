# The Autonomy Loop

The loop turns a goal into verified progress: PLAN, ACT, VERIFY, REFLECT,
then CORRECT or ADVANCE. Every stage maps to a CLI command; run them through
`python3 scripts/mythify.py`.

## Proportional ceremony

Protocol overhead must scale with the task. Paying ceremony a task does not
need is itself a failure mode.

| Task size | What to use |
| :--- | :--- |
| Trivial: single edit, single question | Nothing. No plan, no records. |
| Focused low-risk fix or test task | Fast profile: no plan record, but still run `verify run`. |
| Multi-step, single session | One plan; `verify run` for each completion claim. |
| Long-horizon or multi-session | Full loop, plus memory entries and lessons. |

When in doubt, start light and escalate the moment a task grows a second step
or a second session.

Use `classify` before non-trivial work when available. If it returns
`execution_profile: fast`, act directly on the focused task and record
verification. If the task expands into multiple dependent steps, escalate to a
plan.

## Stage 1: PLAN

Create the plan with steps up front when you can:

    plan create GOAL [--steps JSON] [--name NAME]

`--steps` is a JSON array of `{"title": str, "success_criteria": str}`
(success_criteria optional). Without `--steps`, an empty plan is created;
grow it with:

    plan add-step TITLE [--criteria TEXT] [--plan NAME]

Write success criteria as checkable outcomes ("tests pass", "endpoint
returns 200"), not activities ("work on tests"). Orient with `status`: it
shows the active plan, step icons, and the next pending step with its
criteria.

## Stage 2: ACT

Take exactly one step at a time:

    step ID in_progress

Then do the work. Keep the step's success criteria in front of you; they
define done.

## Stage 3: VERIFY

Prove the outcome before claiming it:

    verify run COMMAND [--claim TEXT] [--timeout N]

Exit 0 means verified, exit 2 means unverified. Details and the executed
versus attested distinction are in `self-verification.md`.

## Stage 4: REFLECT

After a failure, a surprise, or a completed milestone, record a structured
reflection: what you did, the outcome (success, partial, failure), what you
observed, the root cause if known, and what you will do next. A lesson passed
to `reflect` is auto-recorded as a project lesson tagged `auto-reflected`.

## Stage 5: CORRECT or ADVANCE

On failure: fix, then re-verify. Do not mark the step completed until a
verification passes. On success, close the step with evidence and move on;
the CLI prints the next pending step after every update.

## Step lifecycle

    step ID STATUS [RESULT] [--plan NAME]

Five statuses, with display icons:

| Status | Icon | Meaning |
| :--- | :--- | :--- |
| pending | `[ ]` | Not started. |
| in_progress | `[>]` | Being worked now. |
| completed | `[x]` | Done, with evidence in RESULT. |
| failed | `[!]` | Did not work; RESULT describes the failure. |
| skipped | `[~]` | Deliberately not done. |

The evidence rule: `completed` and `failed` REQUIRE the RESULT argument.
Without it the CLI prints
`[FAIL] Evidence required: pass a RESULT describing what proves this status.`
and exits 1, leaving the plan unmodified. RESULT should cite proof: the
verify command that passed, the file created, the observed output.

## Managing plans

- `plan list`: all plans with the active marker, progress, archived count.
- `plan show [NAME]`: full detail of the named or active plan.
- `plan switch NAME`: change the active plan pointer.
- `plan archive [NAME]`: move a finished plan to `plans/archive/` and clear
  the active pointer if it pointed there.

Archive plans when their goal is verified done; a clean `status` is part of
orienting the next session.
