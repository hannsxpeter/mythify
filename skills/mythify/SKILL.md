---
name: mythify
description: Evidence discipline for coding agents on any host. Routes a task, plans it in verifiable steps, runs a real check before any completion claim, plans products before large builds, keeps decisions and lessons on disk, and reports progress and issues in chat. Use for multi-step or multi-session work, when a claim needs executed evidence, for reviews and audits that must surface findings, for product direction, or when the user asks for Mythify or says one shot, in one go, address all, keep going until done, yolo, or full send.
---

Invoke this skill the way your host runs skills. Treat any text after the
skill name as the task.

# Mythify

Mythify changes how reliably you work, not what you can do. Executed checks
replace self-report, written plans replace improvisation, and `.mythify/`
on disk replaces context-window memory. It works the same with any model and
any host.

## How to run it

- If the host exposes Mythify MCP tools, use them. Each tool is a command path
  joined by `_` (`plan create` is `plan_create`, `verify run` is
  `verify_run`); the `mythify` tool runs any other command by argument list.
- Otherwise run the `mythify` launcher, or `python3 scripts/mythify.py` from
  a project that carries a copy.
- Run `mythify init` once per project. Every command takes `--help`.

## Front door

1. `mythify route "TASK"` picks one route: `direct`, `plan`, `map`,
   `product`, `outcome`, `review`, `failure_recovery`, or `handoff`. It reads
   the task and the state on disk and prints the next command. It never acts.
2. `mythify status` reorients: active plan, outcome, map, evidence, and
   attention items with issues first.
3. `mythify verify run "COMMAND" --claim "CLAIM"` proves a claim.
4. `mythify report --since last --cursor chat` shows what changed since the
   last report.

Use a lower-level command only after `route` points at it, or when the user
names it.

## Match ceremony to the task

| Task | What to use |
| :--- | :--- |
| Trivial edit or question | Nothing. Just do it. |
| Focused fix | Do it, then `verify run` before claiming it works. |
| Multi-step work | `plan create` with steps, `step` updates, a check per step. |
| Decisions not made yet | `map create`, settle one ticket at a time, `map promote`. |
| Product direction | `product create`, outcomes, non-goals, bets, human approval, `product promote`. |
| Retry until a check passes | `outcome start` with a verifier and budgets, then `outcome check`. |
| Multi-session work | All of the above, plus memory and lessons. |

## The loop

1. PLAN. `mythify plan create "GOAL" --steps JSON`. Give each step a
   `success_criteria` and, when you can, a `verify_command`.
2. ACT. `mythify step N in_progress`, then do the work.
3. VERIFY. `mythify plan verify N` runs the step's stored `verify_command`;
   otherwise `mythify verify run "COMMAND" --claim "CLAIM"`.
4. REFLECT. After a failure or surprise, `mythify reflect --action ...
   --outcome failure --observation ... --next ...`.
5. CORRECT or ADVANCE. Red: fix the cause and verify again. Green:
   `mythify step N completed "verify run exit 0: CLAIM"`.

For a goal with a verifier and a budget, use `outcome start GOAL --success
TEXT --verify COMMAND`, make one bounded attempt, run `outcome check`, and
continue only while it says the outcome is active and budget remains.

Read `references/autonomy-loop.md` for step statuses, plan management, and
when to escalate ceremony. When the project has `.godplans/PLAN.mdx` or
`.godaudits/AUDIT.mdx`, read `references/godplans-godaudits.mdx` and use
`plan import`.

## Evidence rules

- A completion claim needs an executed check. Exit 0 is verified; exit 2 is
  unverified. If you did not run it, say so.
- `step N completed` refuses without a RESULT and a passing `verify run`
  recorded after the step started. A stored `verify_command` must match the
  recorded command. `MYTHIFY_REQUIRE_VERIFIED_STEP=0` waives this only when
  the user asks, and the waiver is stamped on the step.
- `verify claim CLAIM EVIDENCE` records a statement when nothing can run. It
  never counts as verified.
- A check that cannot fail proves nothing. Never use `true`, `exit 0`, or a
  bare `echo` as a verifier; Mythify warns on them and `status` flags passes
  that checked nothing or ran zero tests.

Read `references/self-verification.md` before claiming a step or task done.

## Keep the work visible in chat

The user lives in the chat, not in `.mythify/`.

1. Say in one sentence that you are using Mythify and what outcome you
   pursue.
2. For multi-step work, mark a chat cursor first:
   `mythify report --cursor chat --mark`.
3. After each phase, failed check, or surprise, run
   `mythify report --since last --cursor chat` and give a short update:
   outcome, attention items, next action.
4. Lead with failures. If the report shows none, say no new issues were
   reported in that window.
5. For reviews and audits, list findings with file and line in the chat.

Read `references/chat-experience.md` before an audit, review, or release
gate, and `references/communication-quality.md` before a final response.

## Full-send phrases

Treat "one shot", "in one go", "address all", "do everything", "keep going
until done", "continuous run", "yolo", and "full send" as a request for a
durable loop: plan every step, drive each to a passing check, and report as
you go. They never waive safety. Do not run destructive or irreversible
actions without permission, and do not claim completion without a check.

## Product planning

Before a large build, settle why it exists, for whom, and how success is
measured. `route` sends product questions to the `product` route.

1. `mythify product create TITLE --problem TEXT --user TEXT [--stage S]`.
2. `product outcome STATEMENT --metric TEXT --target TEXT [--measure COMMAND]`
   for each measurable outcome (at most five).
3. `product non-goal TEXT --reason TEXT` for what is out.
4. `product bet HYPOTHESIS --outcome O1 --kill TEXT` for each bet, with the
   kill criterion that would stop it.
5. `product risk TEXT --kind value|usability|feasibility|viability`.
6. `product check` exits non-zero until the plan is complete for its stage.
   Fix the gaps it names.
7. A human approves: `product approve --human-input "THEIR WORDS"`.
8. `product promote BET` turns an approved bet into an execution plan.
   `product measure O1` runs the outcome's measure command as evidence.
   `product decide BET --verdict continue|pivot|stop` needs the human's words.

## Delegation is your choice

Mythify never picks a model, provider, or subagent. When `route --json`
reports `parallelism.fit` as `possible` or `strong`, or the work splits into
independent parts, hand parts to any subagent, worker, or model your host
offers, or to none. Write each delegated prompt so it stands alone and names
the files it needs. Delegated output is material, not evidence: merge it into
the deliverable, then `verify run` the integrated result. If a delegation
choice mattered, record why with `reflect`. Read `references/meta-prompts.md`
for constraint blocks to paste into delegated prompts.

## Human decisions stay human

Mythify refuses these without a non-empty `--human-input`:
`map resolve` on a `grilling` or `prototype` ticket (or a task ticket added
with `--mode hitl`), `product approve`, and `product decide`. It records the
words but cannot verify who supplied them. Never write those words yourself.
Ask, wait, then record what the person said.

## Pause rules

Act when the next step is clear and reversible. Pause only for destructive or
irreversible actions, real scope changes, or input only the user can give.
Build the smallest thing that meets the need.

## Memory and lessons

- `memory set KEY VALUE --category fact|decision|discovery|state` when you
  learn something that cost effort. `memory get QUERY` at session start and
  before an architectural decision.
- `lesson add TITLE DETAIL [--global]` when reality surprises you.
  `lesson list` before work in an unfamiliar area.
- Start a resumed session with `status`; end long work with `summary`.

Read `references/memory-system.md` before your first memory entry or lesson.

## References

- `references/autonomy-loop.md`: plan and step lifecycle.
- `references/self-verification.md`: executed versus attested evidence.
- `references/chat-experience.md`: reporting progress and findings in chat.
- `references/communication-quality.md`: the rewrite pass for prose.
- `references/memory-system.md`: memory entries and lessons.
- `references/meta-prompts.md`: constraint blocks for delegated prompts.
- `references/godplans-godaudits.mdx`: importing godplans and godaudits tasks.
