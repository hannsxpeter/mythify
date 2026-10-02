---
name: mythify-verify
description: |
  Mythify verification front door. Use when the user asks for mythify-verify,
  "mythify verify", "prove this", "did it work", or wants a completion claim
  grounded in an executed check.
---

Invoke this skill the way your host runs skills. Treat any text after the
skill name as the claim to prove.

# mythify-verify

Turn a claim into executed evidence and show the verdict in chat.

Run commands through the host's Mythify MCP tools when they exist, else the
`mythify` launcher, else `python3 scripts/mythify.py`.

## Process

1. Name the claim that needs proof.
2. Pick a check that can fail: a test, build, lint, type-check, curl, file
   check, or targeted script. Never `true`, `exit 0`, or a bare `echo`.
3. Run it. When the claim is an active plan step with a stored
   `verify_command`, run that step's own check instead:

       mythify verify run "COMMAND" --claim "CLAIM"
       mythify plan verify N

4. Report:

       mythify report --since last --cursor chat

5. Bring the verdict into chat:

   - Verified (exit 0): claim, command, exit code, duration.
   - Unverified (exit 2): command, exit code, the relevant output tail, and
     the next fix.
   - Attested only: say it is self-reported and weaker than executed
     evidence.

6. If the check satisfies an active plan step, complete it and report again:

       mythify step N completed "verify run exit 0: CLAIM"
       mythify report --since last --cursor chat

## Rule

Do not say "done", "fixed", "green", or "released" without an executed check
behind it. When nothing can run, record `mythify verify claim CLAIM EVIDENCE`
and label it attested, not verified. Output from a delegated subagent or
worker is material, not evidence: verify the integrated result yourself.
