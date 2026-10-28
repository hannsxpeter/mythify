# Campaigns

A campaign keeps a long-running goal and learning beside an execution plan.
The plan remains the only progress record. The host does the work in chat,
and the existing verification gate decides whether a step can complete.

## Continue an existing plan

From a project that already has a Mythify plan:

```bash
mythify plan list
mythify campaign start "Finish the existing work" --plan existing-plan \
  --success "Every planned outcome passes its check"
mythify campaign status existing-plan
mythify campaign prompt existing-plan
```

Replace `existing-plan` with the plan name from `plan list`. Starting the
campaign adds metadata and enables strict step context. It preserves the
plan's goal, steps, results, verification anchors, and evidence ledger.
Repeating the same start resumes the campaign without rewriting state.
A different campaign goal or success criterion is refused once it exists.

Without `--plan` or `--name`, start uses the active plan. With no active
plan, it creates one. An explicit `--name` creates or resumes that named
plan, selecting it as active when newly created. Supplying `--tasks JSON` or
`--verify COMMAND` configures a new plan
only; these flags refuse to replace an existing plan's work.

## Complete one step

The prompt identifies the current step and its check. For a stored verifier:

```bash
mythify step 2 in_progress --plan existing-plan
# Perform the step's work.
mythify plan verify 2 --plan existing-plan
mythify campaign advance existing-plan --result "Stored verifier passed"
```

Use the actual step id shown by the prompt. Advance requires exactly one
in-progress step and passing executed evidence scoped to that step. It uses
the plan's command matching, latest-failure rejection, and source freshness
checks. A prose-only waiver is refused. If verification fails, fix the cause
and run the verifier again before advancing.

For a step without a stored verifier, select its plan, start the step, and
run `mythify verify run "COMMAND" --claim "STEP OUTCOME"` before advancing.
Choose a check that can fail when the outcome is wrong.

Status reflects plan progress. A non-empty plan is complete when every step
is completed; skipped steps do not satisfy the campaign. The campaign's
success text is advisory, so the plan's checks must cover that outcome.

## Resume and retain learning

```bash
mythify campaign learn "Keep the current compatibility check" \
  --plan existing-plan --apply-next
mythify campaign prompt existing-plan
mythify prompt campaign existing-plan
```

Learning appears in subsequent campaign prompts. `--apply-next` marks the
entry for subsequent work; it never edits the implementation automatically.
Named operations on existing plans leave the active plan pointer unchanged. Their output gives
the named continuation command so another active plan is not selected by
accident.

For the active campaign plan, `mythify prompt next` selects its campaign
prompt unless an unresolved verification failure or active map takes
priority. Resume routing also returns the campaign prompt when the plan
steers the route.

## Watch prompts

```bash
mythify campaign watch existing-plan --interval 5 --max-iterations 12
```

Watch reads the named plan again each time and emits only changed prompts.
It never edits files, starts agents, runs checks, or advances steps. The
default is one poll. `--max-iterations 0` watches until interrupted; use it
only when the host explicitly manages a background watcher.
If a plan contains corrupt JSON, campaign inspection refuses and leaves the
file untouched. Unsafe plan identifiers and paths that resolve outside the
plan store are refused before they can be read or written.

## Upgrade and check the installed copy

Re-run `scripts/install_user.sh` from the updated checkout or unpacked
6.1.0 CLI archive. It installs the runtime and chat skills together while
preserving project `.mythify` state. Confirm the launcher you actually use:

```bash
command -v mythify
mythify --version
mythify campaign --help
```

The version should be 6.1.0 or later with campaign support. A stale launcher,
workspace copy, or skill from another release can still recommend commands
the runtime does not have. Use the same release for the CLI and skills, and
check the saved MCP command if its tools still resolve to an older path.

Legacy v5 files under `.mythify/campaigns/` remain untouched and unused.
Attach the existing execution plan with `--plan`; do not create a second
executor or reset progress to obtain campaign behavior.

To roll the runtime back, run the previous release's installer against the
same prefix and keep the project state. Version 6.0 retains the normal plan
loop and ignores campaign metadata. Reinstalling 6.1 restores the campaign
interface over the preserved plan. The installer's ownership checks and
transactional rollback also protect an installation that fails mid-update.

See [MCP setup](mcp.md) for the equivalent typed tools and
[the command reference](commands.md) for every flag.
