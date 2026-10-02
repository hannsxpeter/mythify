---
name: mythify-work
description: |
  Visible Mythify work loop in chat. Use when the user asks for mythify-work,
  "mythify work", "use Mythify to do this", "one shot", "in one go",
  "address all", "keep going until done", or wants to watch multi-step work
  move from plan to passing checks.
---

Invoke this skill the way your host runs skills. Treat any text after the
skill name as the work request.

# mythify-work

Do the requested work in this chat with Mythify as the evidence ledger. The
user should see what is happening, what passed, what failed, and what comes
next without opening `.mythify/`.

Run commands through the host's Mythify MCP tools when they exist, else the
`mythify` launcher, else `python3 scripts/mythify.py`.

## Loop

1. State the outcome you pursue in one sentence.
2. Route unless the user named a Mythify command already:

       mythify route "TASK"

   Follow its route: `direct`, `plan`, `map`, `product`, `outcome`, `review`,
   `failure_recovery`, or `handoff`. For `failure_recovery`, fix the latest
   failed check before anything else.
3. For multi-step work, create or resume a plan whose steps carry a success
   criterion and, when possible, a `verify_command`, then mark the chat
   cursor:

       mythify plan create "GOAL" --steps JSON
       mythify report --cursor chat --mark

4. Before each step, say its name and success criterion. Then:

       mythify step N in_progress

5. Do the work. Verify with the step's own check, or a direct one:

       mythify plan verify N
       mythify verify run "COMMAND" --claim "CLAIM"

6. Red: surface the failure in chat, record it, fix the root cause, and
   verify again. Never advance on red.

       mythify reflect --action "..." --outcome failure --observation "..." --next "..."

7. Green: complete the step with the evidence, then report:

       mythify step N completed "verify run exit 0: CLAIM"
       mythify report --since last --cursor chat

8. Before the final answer, run one last report. Lead with attention items;
   if there are none, say no new issues were reported in that window.

## Update shape

- Outcome: what just happened.
- Attention: failed checks, failed steps, failure reflections, attested
  warnings.
- Next: what you do next.

Keep raw logs out of the chat unless they diagnose a failure.

## Delegation

When parts of the work are independent, you may hand them to any subagent or
worker your host offers, or to none. Each delegated prompt must stand alone.
Delegated output is material: merge it, then `verify run` the integrated
result before completing the step.

## Boundaries

- `step N completed` needs a RESULT and a passing `verify run` recorded after
  the step started. Never use a check that cannot fail, such as `true`.
- Decisions that need a human (`grilling` and `prototype` map tickets,
  `product approve`, `product decide`) wait for the person's words.
- Pause only for destructive or irreversible actions, real scope changes, or
  input only the user can give.
