# Chat Experience

Mythify state is durable, but the user lives in the chat. Treat the chat as the
primary product surface and `.mythify/` as the evidence ledger behind it.

## Start of work

For non-trivial tasks:

1. State the outcome you are pursuing in one sentence.
2. Create or resume the plan.
3. Mark the chat cursor so old project history does not flood the thread:

       mythify report --cursor chat --mark

   MCP equivalent: call `work_report` with `cursor: "chat"` and `mark: true`.

## While working

After a meaningful phase, failed check, audit sweep, or surprise, run:

    mythify report --since last --cursor chat --format chat

MCP equivalent: call `work_report` with `since: "last"` and `cursor: "chat"`.

Then write a short user-facing update:

- Outcome: what just happened.
- Attention: failed checks, failed steps, failure reflections, and attested
  warnings from the report.
- Next: what you are doing next.

Do not paste every event if the report is long. Preserve the important issues
and evidence. Mention omitted routine successes only when they affect the next
decision.

## Audit and review reporting

For audits, reviews, and release gates, always surface findings in the chat.
Use this order:

1. Verified findings, with file and line references when applicable.
2. Warnings, including attested evidence or skipped checks.
3. Open questions or residual risk.
4. Evidence commands that passed or failed.

If no actionable issue was found, say that plainly and cite the checks that
support it. Do not make "no issues" sound verified unless an executable check,
inspection pass, or both actually happened.

## Final response

Before the final response, run a final chat report or equivalent MCP
`work_report`. The final answer should lead with the verified outcome, then
issue status, then evidence. It should not require the user to inspect
`.mythify/` to know what happened.
