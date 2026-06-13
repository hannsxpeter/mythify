---
name: mythify
description: Operational discipline protocol that gives any AI agent Mythos-class autonomy patterns, including planning loops, executed verification, persistent memory, and structured reflection. Use when executing multi-step or long-horizon tasks, when work spans sessions, when progress claims need grounding in evidence, or when the user asks for mythify or mythos-style autonomous execution.
---

# Mythify Protocol (v2)

You are operating under the Mythify Protocol: an operational discipline layer.
It changes how reliably you work, not what you can do. It supplies four
capabilities through one CLI: plans with evidence-gated steps, executed
verification, persistent memory, and structured reflection.

Run every command through `python3 scripts/mythify.py`. State is per-project:
each project owns a `.mythify/` directory (run `init` once). The only global
state is the cross-project lessons store in `~/.mythify/lessons/`.

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
Persist state outside the context window on long tasks.

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
| `outcome start GOAL --success TEXT --verify COMMAND [--metric COMMAND]` | Start a supervised outcome loop with verifier, optional metric, and budget. |
| `outcome check [NAME]` | Run the verifier and return success, retry, or budget exhaustion. |
| `outcome status [NAME]` | Show the active or named outcome loop. |
| `outcome results [NAME]` | Show all verifier iterations and final state. |
| `outcome stop [NAME] --reason TEXT` | Stop an outcome loop. |
| `lesson add TITLE DETAIL [--tags a,b] [--global]` | Record a lesson. |
| `lesson list [--tag TAG] [--scope project\|global\|all]` | List lessons by scope. |
| `verify run COMMAND [--claim TEXT] [--timeout N]` | Execute and record proof. Exit 0 verified, 2 unverified. |
| `verify claim CLAIM EVIDENCE` | Record a self-report. Never counts as verified. |
| `reflect [JSON]` | Record a reflection (flags form also accepted). |
| `summary` | Full session report. |

## MCP clients

Clients wired to the Mythify MCP server instead of the CLI use 28 tools:
classify_task, host_model_switch, provider_probe, local_model_run,
host_cli_probe, host_cli_run, execution_probe, lifecycle_probe, outcome_start,
outcome_check, outcome_status, outcome_results, outcome_stop, memory_store,
memory_recall, memory_clear, lesson_record, lesson_recall, plan_create,
plan_add_step, plan_update_step, plan_status, verify_run, verify_claim,
reflect, plus fanout_start, fanout_status, and fanout_results. Same state
directory, same file formats, full interop with the CLI.

`classify_task` returns `model_policy.session.recommendation` so hosts can map
the prompt to chat settings before work begins. Direct low-risk prompts use a
fast profile with low thinking and fast speed, ordinary implementation uses a
standard profile with medium thinking, and research or high-risk work uses a
strong profile with high thinking and standard speed.

Fanout visibility defaults to `summary`: show worker titles, status counts,
and notable findings in the main chat. Use quiet, verbose, or threaded only
when the prompt asks for that behavior; threaded still requires host support.
