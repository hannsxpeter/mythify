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

## Require step order or restrict file changes

Start with `--ordered` to make the first unfinished step the only eligible
step. A skipped or failed step stays first until you reopen and complete it.
An ordered campaign can attach to a plan whose completed steps form a
prefix and whose steps after the first unfinished one are all pending.
Repeating `step ID in_progress` or `plan verify ID` on an open step keeps
the original evidence window.

```bash
mythify campaign start "Update parser and help text" --name parser \
  --allowed-paths "src,docs,tests" --frozen-paths "tests" \
  --tasks '[{"title":"Update parser","allowed_paths":["src"],"verify_command":"python3 -m unittest discover -s tests"},{"title":"Update help text","allowed_paths":["docs"]}]'
mythify step 1 in_progress
mythify plan verify 1
mythify campaign advance --result "Parser tests passed"
```

Either path flag enables scope and implies `--ordered`. The flags take
comma-separated file or directory prefixes relative to the Git worktree
root. A frozen path must name a tracked file or a directory containing
tracked files. Frozen directories cover tracked and Git-visible untracked
files. Keep trusted verifier inputs tracked. Frozen paths take precedence
over allowed paths. A task's
optional `allowed_paths` array also enables scope and narrows that step to
the intersection with any campaign allowlist. An empty step allowlist
(`"allowed_paths":[]`) permits zero file changes for that step.

Start at the root of a committed, clean Git worktree. The raw checkout bytes
and executable modes must agree with HEAD, so checkouts that transform
tracked files can be refused. Start each step before making edits. Mythify
compares the current HEAD tree, Git index, and on-disk files with the original
scope and with the last accepted checkpoint. It includes Git-visible
untracked files and checks both names of a rename. A commit is optional at
a step boundary. Staging or committing an accepted deletion or rename
preserves scope when the worktree paths stay as accepted. The same applies
to staging or committing the exact accepted bytes and mode of an unchanged
file, even during the next step. A different staged or committed blob or mode
counts as a change.

`plan verify` checks scope before and after running its verifier.
Scoped verification requires matching source snapshots before and after
the run.
`campaign advance` and direct `step ID completed RESULT` refuse scope
violations even after a passing check. Only accepted completion advances
the checkpoint. `verify gate` checks for scope violations in flight and
for any path changes when no step is open. It exits 2 on a confirmed
violation and 1 if the baseline or worktree cannot be inspected. Resume
with the same scope and correct the changes before retrying.

The comparison covers filesystem checkpoints. Keep the worktree isolated
while working. It hashes symlink targets as link text, excludes Mythify
state and ordinary Git-ignored files, and refuses changes to Git's external
ignore files. It refuses inspections that exceed 100,000 filesystem
entries or 512 MiB of file contents, as well as submodules and nested Git
worktrees.

## Resume and retain learning

```bash
mythify campaign learn "Keep the current compatibility check" \
  --plan existing-plan --apply-next
mythify campaign prompt existing-plan
mythify prompt campaign existing-plan
```

Learning appears in subsequent campaign prompts. `--apply-next` marks the
entry for subsequent work; it never edits the implementation automatically.

`--verification ID` (repeatable, at most 32) cites executed records, passing
or failing, from the local ledger, including records from another plan or a
detached [trial](trials.md) run. Each ID must match a single executed record
with a status that agrees with its exit code, a valid timestamp, and provenance
with `mythify_version` and `environment_digest`. Each cited ID must be unique.
Citations require a ledger of valid UTF-8 JSON records with unique keys. The
lesson stores `verification_refs`, a list of `{id, revision}` pins that appear
in prompt JSON. The revision is the record's digest at the time of citation.
Campaign completion still requires passing executed evidence for the step.
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
CLI archive. It installs the runtime and chat skills together while
preserving project `.mythify` state. Confirm the launcher you actually use:

```bash
command -v mythify
mythify --version
mythify campaign --help
```

The version should be 6.1.0 or later, the first release with campaign support. A stale launcher,
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
