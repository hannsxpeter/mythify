---
name: mythify
description: Chat-native operational discipline protocol for AI coding agents, including planning loops, campaigns, executed verification, persistent memory, structured reflection, and visible issue reporting. Use when executing multi-step or long-horizon tasks, when work spans sessions, when progress claims need grounding in evidence, when audits or reviews must surface findings in chat, when the user asks for Mythify, mythify, mythos-style autonomous execution, or when the user asks for one shot, in one go, address all, continuous run, keep going until done, yolo, or similar full-send phrasing.
---

# Mythify Protocol

You are operating under the Mythify Protocol: an operational discipline layer.
It changes how reliably you work, not what you can do. It supplies durable
workflow capabilities through one CLI: plans with evidence-gated steps,
research records, campaigns, executed verification, persistent memory, and
structured reflection.

Use Mythify as a skill first and a command surface second. Prefer MCP tools
when the host exposes them. Otherwise use the installed `mythify` launcher when
available, falling back to `python3 scripts/mythify.py` from a Mythify checkout.
State is per-project: each project owns a `.mythify/` directory (run `init`
once). The only global state is the cross-project lessons store in
`~/.mythify/lessons/`.

## Chat trigger phrases

Treat these user phrases as Mythify triggers even when the user does not type
the word Mythify:

- one shot, one-shot, one go, in one go, all in one go
- address all, fix all, do all, do everything, execute all
- continuous run, keep going, keep going until done, until no issues remain
- yolo, full send, ship it, run it through

Interpret these as a request for a durable autonomous work loop, not as
permission to skip safeguards. For small bounded work, use the normal plan or
outcome loop. For long-running project goals, start or resume a campaign:

    mythify campaign start "GOAL" --success "DONE CRITERIA"
    mythify campaign status
    mythify campaign prompt
    mythify campaign advance --result "phase evidence"
    mythify campaign learn "what improves the next task" --apply-next

Use `mythify campaign prompt` when the host needs the next task injected or
displayed inside chat. Use `mythify campaign watch --max-iterations 0` only
when the host is explicitly managing a long-running background watcher. Both
commands are read-only prompt surfaces: the host still performs edits, runs
checks, reports issues in chat, and advances the campaign with evidence.

If the user says yolo or full send, keep the same safety boundaries: do not run
destructive or irreversible actions without explicit permission, and do not
claim completion without executed verification when a check exists.

## Chat contract

The user should experience Mythify as visible disciplined work inside the chat,
not as a hidden log system. When this skill triggers:

1. Say briefly that you are using Mythify and what outcome you are pursuing.
2. For multi-step work, create or resume a plan and set a chat cursor:
   `report --cursor chat --mark` or MCP `work_report` with `mark: true`.
3. After meaningful phases, failures, audit sweeps, and before the final
   response, run `report --since last --cursor chat --format chat` or MCP
   `work_report`.
4. Bring the report into the conversation. Lead with `Attention` items:
   failed checks, failed steps, failure reflections, and attested warnings.
   If there are none, say no new issues were reported in that window.
5. For audits and reviews, list findings in the chat with file and line
   references when applicable. Do not leave findings only in `.mythify/`.

Read `references/chat-experience.md` before running an audit, review, release
gate, or any task where the user asked for play-by-play progress.

## Proportional ceremony

Match protocol overhead to task size. Trivial tasks pay zero overhead.

| Task size | Protocol |
| :--- | :--- |
| Trivial (single edit or question) | No protocol commands. Just do it. |
| Focused low-risk fix or test task | Fast profile: skip plan state, do the work, then `verify run`. |
| Multi-step, single session | A plan, plus executed verification of every completion claim. |
| Long-horizon or multi-session | Full loop with memory and lessons. |

## The autonomy loop

1. PLAN: `plan create GOAL [--steps JSON] [--name NAME]`, then `status`.
2. ACT: mark the next step `in_progress`, then do the work.
3. VERIFY: `verify run COMMAND [--claim TEXT] [--timeout N]`.
4. REFLECT: record what happened, especially after failures or surprises.
5. CORRECT or ADVANCE: on failure, fix and re-verify; on success, mark the
   step completed with evidence and take the next pending step.

For outcome-driven work, start a supervised loop with `outcome start`, make one
bounded attempt, then run `outcome check`. Continue only when Mythify says the
outcome is still active and the budget remains. Use `outcome results` to report
the evidence trail and `outcome stop --reason TEXT` when the host decides to
end the loop.

Read `references/autonomy-loop.md` before starting any multi-step plan, when
deciding how much ceremony a task deserves, or when you need the step
lifecycle rules (statuses, icons, the evidence rule, archiving).

## Verification doctrine

Executed beats attested. A completion claim requires an executed verification:
use `verify run` whenever anything executable exists (tests, builds, linters,
a curl, a file check). Use `verify claim CLAIM EVIDENCE` only when nothing
executable exists; it is recorded as `[WARN] ATTESTED` and never counts as
verified.

Read `references/self-verification.md` before claiming any task or step
complete, when choosing between `verify run` and `verify claim`, or when a
verification fails and you need to interpret the verdict.

## Memory and lessons

Store facts, decisions, discoveries, and state as you learn them. Recall at
session start and before architectural decisions. Project lessons stay in the
project; add `--global` only for lessons that apply everywhere.

Read `references/memory-system.md` at the start of any session that resumes
prior work, before recording your first memory entry or lesson, and before
any decision that earlier discoveries might affect.

## Behavioral constraints

Act over ask. Lead with outcome. Ground every claim. Pause only for
destructive or irreversible actions, real scope changes, or input only the
user can provide. Build the simplest thing that meets the requirement.
Persist state outside the context window on long tasks. Keep the user-facing
thread clear and useful: summarize evidence, surface issues, and avoid dumping
raw logs unless they are needed to diagnose a failure.

Read `references/meta-prompts.md` when writing prompts or instructions for
subagents, when unsure whether to pause for the user, or when scoping how
much to build.

## Command quick reference

| Command | Purpose |
| :--- | :--- |
| `init` | Create `./.mythify` workspace. |
| `protocol check [PATH ...] [--json]` | Verify copied protocol files match this CLI. |
| `status` | Orientation: active plan, next step, counts. |
| `classify TASK [--json] [--triage never\|auto\|always]` | Identify task type, risk, execution profile, verification strategy, fanout fit, model policy, and task-based host recommendation. |
| `plan create GOAL [--steps JSON] [--name NAME]` | Create a plan, set it active. |
| `plan add-step TITLE [--criteria TEXT] [--plan NAME]` | Append a step. |
| `plan list` | List plans with progress. |
| `plan show [NAME]` | Full detail of a plan. |
| `plan switch NAME` | Set the active plan. |
| `plan archive [NAME]` | Move a plan to the archive. |
| `step ID STATUS [RESULT] [--plan NAME]` | Update a step. completed and failed require RESULT. |
| `memory set KEY VALUE [--category C]` | Store an entry (fact, decision, discovery, state). |
| `memory get [QUERY] [--category C]` | Substring search over keys and values. |
| `memory clear [KEY] [--all]` | Remove one entry, or everything with `--all`. |
| `host-model switch MODEL [--platform P]` | Record a requested host chat model switch for model policy. |
| `host-model status` | Show the recorded host model switch. |
| `host-model clear` | Clear the recorded host model switch. |
| `research start QUESTION [--name NAME]` | Start source-backed research. |
| `research add-source TITLE [--url URL]` | Add a research source. |
| `research add-claim CLAIM --evidence TEXT` | Add a source-backed claim. |
| `research summary [NAME]` | Show sources, claims, open questions, and decision. |
| `research close [NAME] --decision TEXT` | Close research with a decision. |
| `campaign start GOAL [--tasks JSON]` | Start a long-running task campaign. |
| `campaign status [NAME]` | Show campaign progress and current phase. |
| `campaign prompt [NAME] [--json]` | Render the next host prompt without mutating state. |
| `campaign watch [NAME] [--interval N] [--max-iterations N]` | Poll a campaign and emit refreshed host prompts. |
| `campaign advance [NAME] --result TEXT` | Advance the current task through the loop. |
| `campaign learn LESSON` | Record learning for later campaign tasks. |
| `outcome start GOAL --success TEXT --verify COMMAND [--metric COMMAND]` | Start a supervised outcome loop with verifier, optional metric, and budget. |
| `outcome check [NAME]` | Run the verifier and return success, retry, or budget exhaustion. |
| `outcome status [NAME]` | Show the active or named outcome loop. |
| `outcome results [NAME]` | Show all verifier iterations and final state. |
| `outcome stop [NAME] --reason TEXT` | Stop an outcome loop. |
| `lesson add TITLE DETAIL [--tags a,b] [--global]` | Record a lesson. |
| `lesson list [--tag TAG] [--scope project\|global\|all]` | List lessons by scope. |
| `logs compact [--keep N] [--dry-run] [--json]` | Archive raw verification and reflection logs, then keep recent active records. |
| `verify run COMMAND [--claim TEXT] [--timeout N]` | Execute and record proof. Exit 0 verified, 2 unverified. |
| `verify claim CLAIM EVIDENCE` | Record a self-report. Never counts as verified. |
| `reflect [JSON]` | Record a reflection (flags form also accepted). |
| `report --since last --cursor chat` | Chat-ready progress and issue report. |
| `summary` | Full session report. |

## MCP clients

Clients wired to the Mythify MCP server instead of the CLI should use the
equivalent tools, especially `work_report` for chat narration,
`workflow_status` for orientation, `verification_history` for evidence,
`plan_create`, `plan_add_step`, `plan_update_step`, `verify_run`,
`verify_claim`, `reflect`, and `campaign_next_prompt` for campaign reprompts.
Same state directory, same file formats, full
interop with the CLI.

`classify_task` returns `model_policy.session.recommendation` so hosts can map
the prompt to chat settings before work begins. Direct low-risk prompts use a
fast profile with low thinking and fast speed, ordinary implementation uses a
standard profile with medium thinking, and research or high-risk work uses a
strong profile with high thinking and standard speed.

Fanout visibility defaults to `summary`: show worker titles, status counts,
and notable findings in the main chat. Use quiet, verbose, or threaded only
when the prompt asks for that behavior; threaded still requires host support.
