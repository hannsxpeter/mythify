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
| Multi-step, single session | A plan, plus executed verification of every completion claim. |
| Long-horizon or multi-session | Full loop with memory and lessons. |

## The autonomy loop

1. PLAN: `plan create GOAL [--steps JSON] [--name NAME]`, then `status`.
2. ACT: mark the next step `in_progress`, then do the work.
3. VERIFY: `verify run COMMAND [--claim TEXT] [--timeout N]`.
4. REFLECT: record what happened, especially after failures or surprises.
5. CORRECT or ADVANCE: on failure, fix and re-verify; on success, mark the
   step completed with evidence and take the next pending step.

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
| `status` | Orientation: active plan, next step, counts. |
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
| `lesson add TITLE DETAIL [--tags a,b] [--global]` | Record a lesson. |
| `lesson list [--tag TAG] [--scope project\|global\|all]` | List lessons by scope. |
| `verify run COMMAND [--claim TEXT] [--timeout N]` | Execute and record proof. Exit 0 verified, 2 unverified. |
| `verify claim CLAIM EVIDENCE` | Record a self-report. Never counts as verified. |
| `reflect [JSON]` | Record a reflection (flags form also accepted). |
| `summary` | Full session report. |

## MCP clients

Clients wired to the Mythify MCP server instead of the CLI use 15 tools:
memory_store, memory_recall, memory_clear, lesson_record, lesson_recall,
plan_create, plan_add_step, plan_update_step, plan_status, verify_run,
verify_claim, reflect, plus the parallel delegation tools fanout_start,
fanout_status, and fanout_results. Same state directory, same file formats,
full interop with the CLI.
