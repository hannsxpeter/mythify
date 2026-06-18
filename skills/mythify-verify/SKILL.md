---
name: mythify-verify
description: |
  Chat-native Mythify verification front door. Use when the user asks for
  /mythify-verify, "mythify verify", "prove this", "did it work", or wants a
  completion claim grounded in executed evidence. Invoke with /mythify-verify
  in Claude Code or $mythify-verify in Codex.
---

> Invocation: type `/mythify-verify` in Claude Code or `$mythify-verify` in Codex to run this skill. Treat any text after it as the claim to prove.

# /mythify-verify

Turn a claim into executed evidence and show the result in chat.

## Process

1. Identify the claim that needs proof.
2. Choose an executable verifier whenever one exists: test, build, lint,
   type-check, curl, file check, package check, install check, or a targeted
   script.
3. Run:

       mythify verify run "COMMAND" --claim "CLAIM"

   Prefer MCP `verify_run` when available.
4. Run:

       mythify report --since last --cursor chat --format chat

   Prefer MCP `work_report` when available.
5. Bring the verdict into chat:

   - Verified: claim, command, exit code, and duration.
   - Unverified: failed command, exit code, relevant output tail, and next fix.
   - Attested only: say it is self-reported and weaker than executed evidence.

6. If this verifier satisfies an active plan step, complete that step with the
   verifier evidence and report again:

       mythify step ID completed "verify run exit 0: CLAIM"
       mythify report --since last --cursor chat --format chat

## Rule

Do not say "done", "fixed", "green", or "released" unless an executed
verification supports the claim. If no executable verifier exists, use
`mythify verify claim` and label it as attested, not verified.
