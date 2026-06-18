---
name: mythify-work
description: |
  Chat-native Mythify work loop. Use when the user asks for /mythify-work,
  "mythify work", "use Mythify to do this", "one shot", "in one go",
  "address all", "continuous run", or wants Godpowers-style visibility.
---

# /mythify-work

Run the requested work in this chat with Mythify as the durable evidence
ledger. The user should see the work unfold here: what is happening, why it is
happening, what passed, what failed, and what comes next.

## Contract

1. Keep execution in the initiating chat unless the user explicitly asks for a
   handoff, background run, or external agent.
2. Prefer MCP tools when available. Otherwise use the installed `mythify`
   launcher, falling back to `python3 scripts/mythify.py` from a Mythify
   checkout.
3. Start with a one-sentence visible outcome.
4. Run `mythify route "TASK"` to choose the workflow unless the user already
   named a specific Mythify primitive.
5. For multi-step work, create or resume a plan. New plans should use a
   20-step lookahead by default: follow the `mythify route "TASK"` next
   command, or call `mythify plan create "TASK" --horizon 20` when creating
   the plan directly. Then mark the chat cursor:

       mythify report --cursor chat --mark

6. Before each step, say the step name and success criterion in chat.
7. Mark the step in progress, do the work, then run a chat report:

       mythify step ID in_progress
       mythify report --since last --cursor chat --format chat

8. Run an executable verifier before claiming completion:

       mythify verify run "COMMAND" --claim "CLAIM"
       mythify report --since last --cursor chat --format chat

9. If verification fails, surface the failure in chat, record a reflection,
   fix the root cause, and re-verify. Do not advance on red.
10. When verification passes, complete the step with the verifier evidence and
    run another report:

       mythify step ID completed "verify run exit 0: CLAIM"
       mythify report --since last --cursor chat --format chat

11. Before the final answer, run one final report. Lead with Attention items.
    If there are none, say no new issues were reported in the final window.

## Visible Update Shape

Use short updates while working:

- Outcome: what just happened.
- Attention: failed checks, failed steps, failure reflections, and attested
  warnings from the report.
- Next: what you will do next.

Do not dump long raw logs unless needed to diagnose a failure. Do not leave
findings only in `.mythify/`.

## Boundaries

Strict step evidence is the default. A completed step requires a non-empty
RESULT and a passing executed `verify run` since the step started. Use
`MYTHIFY_REQUIRE_VERIFIED_STEP=0` only when the user explicitly asks for legacy
prose-only completion.

Pause only for destructive or irreversible actions, real scope changes, or
input only the user can provide.
