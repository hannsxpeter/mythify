---
name: mythify-route
description: |
  Mythify router front door. Use when the user asks for mythify-route,
  "mythify route", "what should Mythify do next", or wants a visible workflow
  decision before any work starts.
---

Invoke this skill the way your host runs skills. Treat any text after the
skill name as the task to route.

# mythify-route

Choose the next Mythify workflow in chat, and show the decision instead of
hiding it in the ledger.

Run commands through the host's Mythify MCP tools when they exist, else the
`mythify` launcher, else `python3 scripts/mythify.py`.

## Process

1. Restate the requested outcome in one sentence.
2. Route and orient. Both are read-only:

       mythify route "TASK"
       mythify status

   Add `--json` to `route` when you need the classification: risk,
   ambiguity, the `parallelism` and `review` advisories, and the loop-fit
   assessment.
3. Report the decision in chat:

   - Route: one of the ids below.
   - Why: the risk, ambiguity, active state, or failed check that drove it.
   - Next: the exact first command.

   | Route | Meaning |
   | :--- | :--- |
   | `direct` | Answer or make one reversible edit; verify if it changes code. |
   | `plan` | Multi-step work: `plan create` with verifiable steps. |
   | `map` | Decisions are not settled yet: `map create`, one ticket at a time. |
   | `product` | Why, for whom, and what outcome are open: `product create`. |
   | `outcome` | A verifier and a budget exist: `outcome start`, then `outcome check`. |
   | `review` | Findings, risks, or an audit are wanted. |
   | `failure_recovery` | The latest executed check failed; fix it first. |
   | `handoff` | Resume active state without assuming hidden chat context. |

   When `.godplans/PLAN.mdx` or `.godaudits/AUDIT.mdx` has open tasks, the
   next command becomes `plan import`, so the artifact's own tasks and verify
   commands drive the work.
4. If the route starts multi-step work, mark the chat cursor before changing
   state:

       mythify report --cursor chat --mark

5. Continue only when the next step is clear and reversible. Ask only when
   the route reveals destructive work, a real scope change, or input only the
   user can give.

## Delegation

`parallelism.fit` of `possible` or `strong` means independent parts exist.
Whether to delegate, and to which subagent or model, is the host's choice;
Mythify never picks one.

## Output rule

The user should not need to open `.mythify/` to know the route. Name the
constraint that drove it and keep exact command names.
