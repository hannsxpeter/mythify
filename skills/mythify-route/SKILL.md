---
name: mythify-route
description: |
  Chat-native Mythify router front door. Use when the user asks for
  /mythify-route, "mythify route", "what should Mythify do next", or wants a
  visible workflow decision before execution. Invoke with /mythify-route in
  Claude Code or $mythify-route in Codex.
---

> Invocation: type `/mythify-route` in Claude Code or `$mythify-route` in Codex to run this skill. Treat any text after it as the task to route.

# /mythify-route

Choose the next Mythify workflow path in chat without hiding the decision in
the ledger.

## Process

1. Restate the user's requested outcome in one sentence.
2. Run the router:

       mythify route "TASK"

   Prefer MCP `workflow_route` when available.
3. Run orientation:

       mythify status

   Prefer MCP `workflow_status` when available.
4. Report the route decision in chat:

   - Recommended path: direct, plan, research, review, outcome, campaign,
     failure recovery, handoff, or prompt packet.
   - Why: the risk, ambiguity, active state, or failed evidence that drove it.
   - Next: the exact first action you will take.

   The router is godplans and godaudits aware: when `.godplans/PLAN.mdx` or
   `.godaudits/AUDIT.mdx` exists with open tasks, the route reason names the
   artifact and the next command becomes `plan import` so the artifact's own
   tasks and verify commands drive the work instead of a freshly drafted plan.

5. If the route selects multi-step work, mark the chat cursor before mutating
   state:

       mythify report --cursor chat --mark

6. Continue only when the next step is clear and reversible. Ask only when the
   route reveals destructive work, a real scope change, or missing user-only
   input.

## Output Rule

The user should not have to inspect `.mythify/` to know the route. Bring the
router's decision and next action into the transcript.
