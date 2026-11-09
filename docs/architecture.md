# Mythify architecture

The maintainer contract for Mythify 6.x: runtime layout, on-disk state, the
rules that make evidence count, and the 6.0 decisions. Commands are in the
generated [commands.md](commands.md); MCP host setup is in [mcp.md](mcp.md).
A design change adds a decision log entry here in the same pull request.

## Goals and non-goals

Goals:

- A completion claim costs an executed check: `step ID completed` is refused
  until a passing `verify run` is on record for that step.
- Plans, decisions, memory, and evidence live on disk in the project, so a new
  session resumes from `.mythify/` instead of chat history.
- Python 3.9 or newer, standard library only, any host, any model.
- A protocol short enough for a small model to load whole.

Non-goals: choosing, ranking, routing to, or switching models, providers, or
vendor CLIs; spawning or orchestrating workers (hosts delegate, Mythify
verifies the integrated result); sandboxing (commands run with the caller's
privileges, see [SECURITY.md](../SECURITY.md)); hosted services, accounts, or
telemetry; grading user projects against Mythify's own release gates.
Printing a routing policy file the user owns is not choosing: see the
2026-11-09 decision.

## Runtime layout

`scripts/mythify.py` is the only entry point. It holds `VERSION`, state
directory resolution, plan and step commands, the shared verification runner
(`execute_recorded_verification`, `run_shell_capture`), `summary`, and
`main()`. Modules that need its helpers receive them through `configure_*`
calls at import time, and the parser receives the handlers as a symbol
table, so no module imports the entry point.

| Module | Owns |
| --- | --- |
| `mythify_parser.py` | `build_parser`, `MythifyArgumentParser` (usage errors exit 64), and every command grammar not listed elsewhere |
| `mythify_map_parser.py`, `mythify_product_parser.py` | the `map` and `product` grammars (the `verify`, `reflect`, `review`, and `lineage` grammars sit beside their handlers) |
| `mythify_verification_commands.py` | `verify run`, `verify claim`, `reflect`, display-only test counts |
| `mythify_checks.py` | named checks: `checks.json`, `verify check`, `{files}` expansion, `@NAME` step verifiers |
| `mythify_gate.py` | `verify gate`: the read-only hook check over in-progress steps |
| `mythify_io.py` | atomic writes, JSONL locks, tolerant reads, the chained append, verification anchors |
| `mythify_evidence_guard.py` | no-op verifier detection, zero-test detection, ledger chain checks, legacy opt-out detection |
| `mythify_provenance.py` | git commit, clean flag, and `worktree_digest` for each executed record |
| `mythify_reuse.py` | `environment_digest` and the `--reuse` lookup over the verification ledger |
| `mythify_ratchet.py` | test-count drop detection, the step baseline, and the `status` drop list |
| `mythify_memory.py`, `mythify_log_compaction.py`, `mythify_loopfit.py` | memory and lessons, `logs compact`, the `loop-fit` advisory |
| `mythify_outcomes.py` | outcome loops: start, check, run, status, results, stop, scope and frozen paths |
| `mythify_campaigns.py` | campaign grammar, plan metadata, strict advancement, learning, and read-only prompt watching |
| `mythify_plans.py` | validated plan identifiers, contained paths, active pointers, plan reads and writes |
| `mythify_maps.py` | decision maps, tickets, fog, scope-out, promote |
| `mythify_product.py` | product records, readiness check, approval, verdicts, promote, measure, show |
| `mythify_quality.py` | blast-radius safety cases: create, prove, show |
| `mythify_findings.py` | findings reviews: rounds, findings, resolution, coverage, status attention |
| `mythify_review_cover.py` | `review_covers_current_source`: the review, if any, that covers the source now |
| `mythify_path_risk.py` | the high-risk completion check, the step start stamp, `route` diff risk |
| `mythify_lineage.py` | typed parent references and current, stale, missing, or unknown status |
| `mythify_plan_import.py`, `mythify_godfiles.py` | read-only godplans and godaudits parsing and `plan import` |
| `mythify_classification.py` | deterministic task classification from `protocol/classification-rules.json` |
| `mythify_router.py` | `route` selection and `prompt` packets |
| `mythify_agent_policy.py` | reads the user's routing policy file and builds the `route` delegation block (advisory) |
| `mythify_views.py` | `status`, `history`, `report`, and the attention detectors |
| `mythify_protocol.py` | `protocol check`, the embedded protocol digest, the CLAUDE.md pointer text |
| `mythify_runtime_helpers.py` | timestamps, slugs, output redaction, `ChildTerminationGuard` |
| `mythify_mcp.py` | the MCP stdio server |

Data files in `protocol/`:

| File | Read by | Content |
| --- | --- | --- |
| `PROTOCOL.md` | `build_variants.py`, `protocol check` | the protocol spine, the source of AGENTS.md |
| `classification-rules.json` | `mythify_classification.py` | schema 6: task types, risk terms, `path_risk` globs, framing, parallelism, review advisories, and `policy_steps` (the terms behind the delegation block) |
| `workflow-router.json` | `mythify_router.py` | version 3: route ids, descriptions, and their prompt packet kinds |
| `prose-quality.json` | `check_prose_quality.py` | scanned paths, forbidden characters, forbidden phrases |

Generated files are never edited by hand. `scripts/build_variants.py` reads
`protocol/PROTOCOL.md` and writes `AGENTS.md` (a generated header line, a
`<!-- Mythify protocol-sha256: ... -->` line, a blank line, the full body),
`CLAUDE.md` (the header, `@AGENTS.md`, one sentence, from
`mythify_protocol.pointer_copy()`, the text `protocol check` compares
against), and the `PROTOCOL_SOURCE_SHA256` constant in
`scripts/mythify_protocol.py`; `--check` writes nothing and exits 1 on drift.
`scripts/build_commands_doc.py` writes [commands.md](commands.md) from the
argparse tree. Maintainer tooling is described in
[MAINTAINING.md](../MAINTAINING.md).

## State model

### Resolution

1. `MYTHIFY_DIR`, when set, is the state directory. It is created on demand.
2. Otherwise the CLI walks up from the working directory and takes the first
   `.mythify/` directory it finds, skipping `~/.mythify`.
3. Otherwise every command except `init`, `route`, `loop-fit`, `protocol
   check`, and `mcp` exits 1 with `[FAIL] No .mythify workspace found`.

`init` creates `./.mythify` and adds `.mythify/` to the project `.gitignore`.
Run in `$HOME` itself, `init` exits 1, because `~/.mythify` holds global
lessons and is never a project workspace.

### Layout

```
.mythify/
|-- memory.json
|-- verifications.jsonl            hash-chained evidence ledger
|-- reflections.jsonl
|-- plans/active                   slug of the active plan
|-- plans/<slug>.json
|-- plans/archive/<slug>.json
|-- lessons/<slug>-<stamp>.json
|-- outcomes/active
|-- outcomes/<slug>/goal.json
|-- outcomes/<slug>/iterations.jsonl
|-- maps/active
|-- maps/<slug>.json
|-- active_product                 slug of the active product
|-- products/<slug>.json
|-- reviews/<slug>.json
|-- checks.json                    named checks for verify check
|-- reports/<cursor>.json
|-- verification-artifacts/<verification id>/stdout.txt, stderr.txt
|-- logs/archive/<log stem>-<YYYYMMDDHHMMSS>.jsonl
`-- locks/jsonl-<digest>.lock/     transient JSONL write lock
```

Global lessons live in `~/.mythify/lessons/`. Folders and files written by
5.x features that 6.0 removed (`research/`, `campaigns/`, `designs/`,
`evals/`, `fanout/`, `workspace.json`, and the host model switch record) are
never created, never read, and never deleted.

### Records

Plan (`plans/<slug>.json`): `name`, `goal`, `steps`, `created`,
`last_updated`, `verification_anchor` (the ledger position when the plan was
created; plans written before this field existed have none), and when present
`lineage`, `source` (map promote: `kind`
`map`, `map`, `destination`, `decisions`, `out_of_scope`; plan import: `kind`,
`path`, `version`, `imported_at`), `product_ref` (`product`, `bet`,
`outcomes`), `strict_context: true` for imported or campaign plans, and
optional `campaign` metadata.

Campaign metadata: `schema_version: 1`, `goal`, `success`, `created`, and
`lessons` (entries with `text`, `apply_next`, and `timestamp`). The `success`
text describes the goal; step verifiers must cover it. Campaign progress is
derived from the plan's steps, with no campaign pointer or separate task
store. `campaign start` preserves existing steps, results, and verification
anchors. `campaign advance` calls the same strict completion handler as
`step`, refuses a prose-only waiver, and requires exactly one in-progress
step. A campaign is complete only when its non-empty plan has every step
completed; skipped steps do not count. Named commands leave the active plan
pointer unchanged when operating on an existing plan. Creating a new campaign
plan selects it as active. Prompt and watch operations only read state.
Campaign reads preserve corrupt JSON in place and refuse to proceed. Plan
paths reject traversal identifiers and symlinks that resolve outside their
store, including the active pointer, plan directory, and archive paths.

Step: `id` (integer), `title`, `success_criteria`, `status` (`pending`,
`in_progress`, `completed`, `failed`, `skipped`), `result`, and when set
`verify_command`, `verification_anchor` (`{"after_sha256": <hex or null>}`),
`updated_at`, `started_commit` (the git commit when the step went in progress,
null outside git), `started_at`, `strict_gate_waived`, `test_drop_accepted`
(`reason`, `baseline`, `current`, stamped when `--accept-test-drop` let a lower
test count through), `risk` (`level: "high"`, `paths`, `review`, `human_input`,
stamped when the step's changed paths were high risk), `human_input` (the
person's words from `--human-input`), and `risk_gate_waived` (`true` when
`MYTHIFY_RISK_GATE=0` let a high-risk completion through). A step already in
progress keeps its first `started_commit` and `started_at`. Imported steps add `source_id`,
`wave`, `phase`, and `artifact_checked: true` when the task's box was checked
in the artifact; such a step still imports as `pending`. A step written before 6.0 may carry an integer
`verification_cursor`; it is still read and is dropped when the step restarts.

Verification record (`verifications.jsonl`, one JSON object per line):

- executed: `id` (`v-<stamp>-<12 hex>`), `kind: "executed"`, `claim`,
  `command`, `exit_code`, `duration_seconds`, `stdout_tail`, `stderr_tail`
  (redacted, last 4000 characters), `verified`, `timestamp`, `provenance`
  (`git_commit`, `worktree_clean`, `worktree_digest`, `mythify_version`,
  `environment_digest`),
  `artifacts` (`stdout` and `stderr`, each `path`, `bytes`, `source_bytes`,
  `sha256`, `truncated`, `redacted`), optional `test_count` (display only),
  optional `test_count_drop` (`previous`, `previous_id`, `current`; set when a
  passing run executed fewer tests than the previous passing run of the same
  command), optional `reused_from` (the original run's id) with
  `reused_duration_seconds` (a reused record copies `exit_code`, `verified`,
  `test_count`, and the output tails, has `duration_seconds: 0`, and no
  artifacts), optional `check` (the name of the named check that produced the
  run) with `check_template` (the stored command before `{files}` expanded),
  optional `lineage`, then context: `plan`, `step_id`, `step_title`,
  `step_status` for `verify run` and `plan verify`; `map`, `ticket_id`,
  `ticket_title`, `ticket_type` for `map verify`; `review` and `proof_mode`
  for `review prove`; `product` and `outcome_id` for `product measure`.
  Outcome iterations append an executed record without `id` that carries
  `outcome`, `iteration`, `outcome_verify`, and `outcome_metric`.
  `plan verify` stamps its own step. `verify run` and outcome records stamp
  the in-progress step whose stored `verify_command` equals the command (a
  `verify check NAME` record also matches a step whose `verify_command` is
  `@NAME`),
  searching the active plan first and then every other plan, and otherwise
  the first in-progress step of the active plan.
- attested: `kind: "attested"`, `claim`, `evidence`, `verified: null`,
  `timestamp`, and the step context.

Every appended record carries `prev_sha256`, the SHA-256 of the previous raw
line (`null` for the first; older records without it are not judged). An edited, inserted, or deleted line breaks
the next link, and `status` reports the break as an issue. The chain is tamper
evidence, not cryptography.

Memory (`memory.json`): `entries` (`key`, `value`, `category` of `fact`,
`decision`, `discovery`, or `state`, `timestamp`) and `metadata` (`created`,
`last_updated`, `total_entries`). Keys are unique; `memory set` overwrites.

Lesson: `title`, `detail`, `tags`, `created`. Reflection: `action`, `outcome`
(`success`, `partial`, `failure`), `observation`, `root_cause`, `next`,
`lesson` (also saved as a lesson tagged `auto-reflected`), `timestamp`.

Outcome (`outcomes/<slug>/goal.json`): `id`, `goal`, `success_criteria`,
`verify_command`, `metric_command`, `metric_floor`, `agent_command`,
`max_iterations`, `iteration_count`, `max_cost`, `cost_spent`,
`escalate_after`, `allowed_paths`, `frozen_paths`, `status` (`active`,
`succeeded`, `failed`, `stopped`), `created`, `updated`, `last_verified`,
`best_metric_score`, `stop_reason`, `supersedes`, and when set
`frozen_baseline` (written only with `--frozen-paths`: `manifest`, each
covered path mapped to the sha256 of its bytes at start; `outcome start`
stores frozen paths relative to the project root and refuses one outside the
root or naming no existing file or directory), `scope_baseline`
(`git_commit` and `ignore_rules` when `outcome run` first watches paths),
`superseded_by`, `evidence_stale`, `last_audit`, and `attempt_started`
(`iteration`, `started`, `prior_cost_spent`). `outcome run` writes
`attempt_started` and charges the iteration and a default cost of 1.0 before
the agent runs, then reconciles the cost with the reported one; a check or run
that finds the marker left by a process that ended early logs that iteration
with `interrupted: true` and keeps it counted. Each `iterations.jsonl` line
holds the iteration number, the `verify` and `metric` runs, cost,
`cost_unbounded` (the agent reported a cost too large for a float, which
spends the whole `max_cost`), `scope_violations`, `frozen_violations`,
`status_after`, and `next_action`. A metric number too large for a float is
recorded as no score.

Map (`maps/<slug>.json`): `id`, `destination`, `notes`, `status` (`charting`,
`clear`, `promoted`), `tickets`, `fog`, `out_of_scope`, `decisions`,
`created`, `updated`. A ticket holds `id` (`T1`...), `title`, `question`,
`type` (`research`, `prototype`, `grilling`, `task`), `mode` (`afk`, `hitl`),
`status` (`open`, `closed`, `out_of_scope`), `blocked_by`, `claimed_by`,
`claimed_at`, `resolution`, `human_input`, `resolved_at`, `created`,
`verification_anchor`, and optional `verify_command` and
`human_input_waived`. A decision or out-of-scope entry made from a waived
ticket also carries `human_input_waived`, and `map promote` copies it into
the plan's `source`.

Product (`products/<slug>.json`, `schema_version` 1): `name`, `title`,
`status` (`draft`, `approved`, `closed`), `stage` (`prototype`, `pre-launch`,
`growth`, `enterprise`), `problem`, `user`, `appetite`, `created`, `updated`,
`approval` (`human_input`, `at`, `stage`, and `human_input_waived` when
waived) or null, `approval_history`, and:

- `outcomes`: `id` (`O1`...), `statement`, `metric`, `baseline`, `target`,
  `target_source` (`user`, `cited`, `decided`, `hypothesis`), `measure`.
- `non_goals`: `id` (`N1`...), `text`, `reason`, `revisit_when`.
- `bets`: `id` (`B1`...), `hypothesis`, `outcomes`, `kill`, `decider`,
  `decide_by`, `priority`, `status` (`proposed`, `in_flight`, `shipped`,
  `stopped`), `verdict` (`pending`, `continue`, `pivot`, `stop`),
  `verdict_human_input`, `plan`, and `promoted_at`, `promoted_anchor` (the
  `verifications.jsonl` position at promotion; only measurements after it
  can ship the bet), `decided_at`, `shipped_at`, and `human_input_waived`
  once set.
- `risks`: `id` (`R1`...), `text`, `kind` (`value`, `usability`,
  `feasibility`, `viability`), `mitigation`, `validation`.

Every item also has `created`. Workflow is in
[product-planning.md](product-planning.md).

Review (`reviews/<slug>.json`, `schema_version` 1, `kind`
`blast_radius_review`): `name`, `status`, `changed_paths`,
`change_fingerprint`, `safety_fact`, `risks`, `merge_gate`, `created`,
`updated`, `evidence_status`. Never rewritten; proof status is derived from
linked records at read time ([blast-radius.md](blast-radius.md)).

Findings review (`reviews/<slug>.json`, `schema_version` 1, `kind`
`findings_review`, `review_type` `findings`): `name`, `rounds`, `created`,
`updated`, `evidence_status`. Each round has `number`, `opened_at`,
`provenance` (`git_commit`, `worktree_digest` at the round), `clean`,
`closed_at` (clean rounds only), optional `note` and `rethink`, and
`findings`. Each finding has `id` (`F1`..., counted across the whole review),
`summary`, `path`, `severity`, `status` (`open`, `fixed`, `dismissed`),
`reason`, `created_at`, `resolved_at`, and `repeat_of` when it repeats a fixed
finding. Unlike a blast-radius review it is edited by `review round|finding|resolve`;
it is material and never verification evidence ([reviews.md](reviews.md)).

Lineage block (on plans, maps, outcomes, products, and executed records):
`captured_at` and `parents`, each `kind` (`map`, `plan`, `outcome`, `review`,
`product`, `verification`), `id`, `revision` (SHA-256 of the parent's
canonical JSON), `observed_updated`. `lineage attach` also stamps
`lineage_updated`, and refuses kinds `review` and `verification`.

Report cursor (`reports/<cursor>.json`): `cursor`, `updated_at`,
`last_event`, and `seen_keys`, the event keys recorded at the cursor's last
timestamp. Event keys are record ids or content digests, so two events in the
same second are neither dropped nor replayed.

### Durability

JSON files are written to a temp file and renamed into place. JSONL appends
take a directory lock under `locks/` whose owner file names the writing
process; a lock left by a dead process is removed. `outcome check`, `outcome
run`, and `outcome check --audit` take the same kind of lock on the outcome's
`goal.json` for the whole call and re-read the goal under it, so a concurrent
call on the same outcome exits 1 without running anything and cannot spend an
iteration twice. Standard plan reads preserve unreadable JSON in place and
warn without using the record. Other JSON readers quarantine a file that
fails to parse as `<name>.corrupt-<stamp>` with a warning, and a malformed
JSONL line is skipped with a warning. `logs compact` copies the whole raw log
to `logs/archive/` before trimming, keeps retained lines byte for byte so the
hash chain still links, and leaves every verification artifact in place.

## Evidence rules

- High-risk paths. `step ID completed` lists the paths changed since the
  step's `started_commit` (working tree against that commit, plus untracked
  files, minus `.mythify/`) and rates them with `path_risk` from
  `protocol/classification-rules.json`. High risk wants a review that covers
  the current source ([reviews.md](reviews.md)) and `--human-input TEXT`. A
  missing piece warns; on a `strict_context` plan it refuses (exit 1).
  With no commit to compare against (an unborn repository, or a step that
  started without one) every tracked and untracked file counts as changed.
  Outside git the check is skipped. `MYTHIFY_RISK_GATE=0` waives it.
- Strict gate. `step ID completed RESULT` needs a non-empty RESULT and, unless
  `MYTHIFY_REQUIRE_VERIFIED_STEP` is `0`, `false`, `no`, or `off`, an executed
  record with `verified: true` and exit 0, recorded after the step's anchor,
  carrying this plan and step id (context-free records from older versions
  still match on a non-imported plan), and running the stored
  `verify_command` exactly. A waived completion is stamped
  `strict_gate_waived: true` with a warning. Only the latest run of each
  command after the anchor counts, whatever plan, step, ticket, review, or
  outcome it is stamped to, so a pass followed by a failing run of the same
  command is refused until the command passes again. When the commit or
  worktree digest moved after the passing run, completion warns, and on an
  imported (`strict_context`) plan it refuses. The digest leaves the resolved
  state directory out, so ledger writes never move it. Under the same
  baseline rule, a satisfying run whose `test_count` is below the baseline (the
  latest passing run of that command before the step's evidence window, else
  the first passing run inside it) warns, and on a `strict_context` plan
  refuses unless the caller passes `--accept-test-drop REASON`.
- Named checks. `.mythify/checks.json` is `{"schema_version": 1, "checks":
  {NAME: {"command", "description", "updated"}}}` with names matching
  `[a-z0-9-]+`. `verify check NAME` runs the command through the same runner as
  `verify run` and stamps `check` and `check_template` on the record. A
  `{files}` placeholder expands to the changed files, bound as positional
  parameters in a `set -- ...;` prefix with each `{files}` replaced by `"$@"`, so
  no file name is ever parsed as shell text (`--files`,
  else working-tree changes against `HEAD` plus untracked files, minus deleted
  files and `.mythify/`); with none, nothing runs and nothing is recorded. A
  step whose `verify_command` is `@NAME` is run by `plan verify` as that check,
  and the step gate counts a passing record whose `check` equals NAME and keys
  "the latest run" by check name, because the expanded text varies per run.
  `--set` refuses a command `noop_verifier_reason` flags, and a template that
  puts `{files}` inside single quotes or after a backslash, where `"$@"` would be
  literal (refused again at run time).
  A record satisfies an `@NAME` step only when its `check_template` equals the
  check's current command, so redefining a check retires its earlier evidence.
- Gate command. `verify gate [--plan NAME] [--json]` runs
  `evaluate_step_evidence` for each in-progress step, with no test-drop reason
  and no human input, and never executes a command or writes state. It exits 0
  when nothing is in flight or every in-progress step is accepted (a stale
  pass, a strict-context test-count drop, and a strict-context high-risk step
  without its review or human input all count as not accepted), and 2
  otherwise, with a failure line and a next-command line per step on stderr.
  It ignores `MYTHIFY_REQUIRE_VERIFIED_STEP=0`: a hook asks whether evidence
  exists, not whether the session waived the rule. With no active plan pointer
  it exits 0; a `--plan` or active pointer that names a missing or unreadable
  plan exits 1. It resolves the state directory without creating anything
  (`needs_state: "readonly"`), reads review files without quarantining a
  malformed one, and treats that file as covering nothing.
- Reuse. `verify run --reuse` and `plan verify --reuse` look for the newest
  executed record with the identical command, `git_commit`, `worktree_digest`
  (both non-null), `mythify_version`, and `environment_digest`. They reuse it
  only when it passed, is not a no-op or zero-test pass, and, if it is itself
  a reused record, only through its original; otherwise the command runs and
  one `[INFO]` line says why. A newer run of the command at the same commit
  and worktree digest that did not pass cancels the reuse, whatever its
  environment digest, and a record with `test_count` 0 is never reused. The environment digest hashes the platform,
  `PATH`, the command's first program (resolved path, size, mtime), and the
  variables named in `MYTHIFY_REUSE_ENV`. It does not cover files ignored by
  `.gitignore`, services, or the network, so reuse is opt-in.
- Anchors. `step ID in_progress`, `plan verify`, and `map claim` store
  `verification_anchor`, the hash of the ledger's last line; only later
  records count. Every new plan stores one too, and a step completed straight
  from `pending` uses it. A plan slug is never reused while a live or
  archived plan holds it, so records keyed by slug belong to one plan. Compaction keeps lines byte for byte, and an anchor found
  only in an archive means every live record is newer, so anchors survive
  `logs compact`. With no placeable marker, only records strictly after the
  step or claim timestamp count.
- Executed versus attested. `verify claim` records a self-report with
  `verified: null`. It never satisfies a gate.
- No-op warnings. A verify command whose exit status is 0 on every path draws
  a `[WARN]` when stored on a step and an attention item in `status` when it
  passes. `noop_verifier_reason` reads the command's `;`, newline, `&&`, and
  `||` list without running it and recognizes `true`, `/usr/bin/true`, `:`,
  `exit 0`, a bare `exit`, and `echo` or `printf` without a pipe or
  redirection, also behind `env`, `command`, or `sh -c`, so `true;`,
  `pytest || true`, and `pytest; echo done` are flagged. It is a heuristic
  that catches common forms, not a guarantee: a group that does not wrap the
  whole command (a wrapping `( ... )` or `{ ...; }` is unwrapped first), a
  conditional, a background job, `set -e`, or a backslash outside single
  quotes makes it answer "not a no-op". A pass whose output says no tests
  ran is flagged the same way. These are advisory for steps; `review prove`
  and `product measure` refuse a command it flags.
- Human gates. `map resolve` on a `hitl` ticket (`grilling`, `prototype`, or
  `--mode hitl`), `product approve`, and `product decide` refuse without a
  non-empty `--human-input`. Mythify records the words but cannot verify who
  supplied them. `MYTHIFY_REQUIRE_HUMAN_INPUT=0` waives this and
  stamps `human_input_waived` on the ticket and its map decision, the
  approval, or the bet. `status` warns about the opt-out while the variable
  is set in its environment, and about each waived decision in every later
  session while the work it governs is live: a ticket until its map is
  promoted, a decision the active plan carried from its map, a product's
  current approval, and a bet verdict until the bet stops or a verdict with
  the human's words replaces it. `map show`, `plan show`, and `product show`
  mark waived decisions.
- Fail closed. With `MYTHIFY_DISABLE_RUN=1`, `verify run`, `verify check NAME`, `plan verify`,
  `map verify`, `outcome check`, `outcome run`, `review prove`, and `product
  measure` execute nothing, record nothing, and exit 2.
- Material. Route output, prompt packets, maps, product records, reviews,
  lineage, and anything a delegated worker returns are material. Only an
  executed record is proof.

`status` runs the attention detectors over durable state: failed checks,
no-op or zero-test passes, ledger chain breaks, legacy opt-outs, waived
strict and human gates, stale or
drifted outcome evidence, open Critical godaudits findings, and godplans or
godaudits counter drift. Issues sort before warnings, then the list is
truncated to `--recent` with the omitted count.

## MCP adapter

`mythify mcp` (and the installed `bin/mythify-mcp` launcher, which runs it)
speaks newline-delimited JSON-RPC 2.0 over stdio: `initialize`,
`notifications/initialized`, `notifications/cancelled`, `ping`,
`tools/list`, `tools/call`, and `server/discover`. Supported protocol
versions are `2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`, and
`2024-11-05`; `initialize` echoes a supported client version, else
`2025-11-25`.

- Delegation. Each call runs `sys.executable scripts/mythify.py <argv>` with
  stdin closed, in the project root: the parent of `MYTHIFY_DIR` when set
  (passed on as an absolute path), else the nearest ancestor of the server's
  working directory holding `.mythify/` (skipping `~/.mythify`), else the
  server's working directory. The CLI enforces every gate.
- Typed tools. `TOOL_ALLOWLIST` names the typed command paths; each tool is the path
  joined with `_`, `-` mapped to `_` (`plan add-step` is `plan_add_step`).
  Schemas come from the argparse actions: positionals are required unless
  `nargs` is `?` or `*`, `store_true` is a boolean, `append` a string array,
  `type=int` an integer, `choices` an enum, and keys are the long flag names
  (`human_input`, `json`). `build_argv` emits options, `--`, positionals.
- Escape hatch. The `mythify` tool takes `{"args": [...]}` and runs any
  command except `mcp`; its description lists every command without a typed
  tool, generated from the parser.
- Results. One text block: stdout, stderr when non-empty, `exit_code: N`.
  Exit 0 and 2 are results; every other code sets `isError`.
- Timeouts. `MYTHIFY_MCP_CALL_TIMEOUT` (default 900 seconds) bounds a call;
  on timeout the server kills the child's process group and returns exit 124;
  on `notifications/cancelled` it kills the group the same way and sends no
  response for that request. The CLI forwards SIGTERM and SIGINT to the verify child's tree and
  exits 128 plus the signal number. Calls run one at a time on a worker
  thread, so `ping` answers during a long call.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success, or a verified check |
| 1 | refusal or failure: no workspace, bad input, a gate refused, a record not found |
| 2 | unverified: a check ran and failed, `product check` found gaps, an outcome is not met, `verify gate` found an in-progress step without accepted evidence, or `MYTHIFY_DISABLE_RUN=1` refused to run |
| 64 | usage error from the argument parser |
| 124 | MCP only: the call timed out and was terminated |
| 130, 143 | the CLI received SIGINT or SIGTERM while a verify child ran |

## Environment variables

| Variable | Effect |
| --- | --- |
| `MYTHIFY_DIR` | state directory; skips upward discovery |
| `MYTHIFY_REQUIRE_VERIFIED_STEP` | `0`, `false`, `no`, or `off` waives the strict gate (stamped, warned) |
| `MYTHIFY_RISK_GATE` | `0`, `false`, `no`, or `off` waives the high-risk path check at step completion, even under strict context (stamped `risk_gate_waived`, warned) |
| `MYTHIFY_REQUIRE_HUMAN_INPUT` | `0`, `false`, `no`, or `off` waives human gates (stamped, warned) |
| `MYTHIFY_DISABLE_RUN` | `1` makes every command runner refuse and exit 2; so does any value other than empty, `0`, `false`, `no`, or `off` (case and surrounding space ignored), so a malformed value fails closed |
| `MYTHIFY_REUSE_ENV` | comma-separated environment variable names whose values join `provenance.environment_digest`, so changing one stops `--reuse` from matching |
| `MYTHIFY_VERIFY_MAX_OUTPUT_BYTES` | output cap per run, default 16 MiB; a run over the cap fails |
| `MYTHIFY_MAP_CLAIMANT` | default claimant for `map claim` |
| `MYTHIFY_AGENT_POLICY` | path to the user's routing policy file for `route`; overrides `~/.agents/policy.json`; a path that does not exist means no policy |
| `MYTHIFY_MCP_CALL_TIMEOUT` | MCP per-call timeout in seconds, default 900 |
| `HOME` | locates `~/.mythify/lessons` |

`MYTHIFY_COST=<n>` is not an environment variable: an `outcome run` agent
prints it on stdout to report the cost of an iteration. A negative value
counts as 0, a missing one as 1.0, and a value too large for a float spends
the rest of `--max-cost`.

## Decision log

### 2026-10-01: one runtime, in Python

Context: the 5.x "one-core architecture decision" in the former
`docs/design.md` kept the Python CLI and a Node MCP server as separate
adapters, shared facts through mirrored manifests and parity checkers, and
deferred unification until at least two more duplicated surfaces showed
recurring drift. By 5.8 the Node server was about 20,000 source lines with
npm runtime dependencies fetched at install time, the default installer
required `node`, `npm`, and `tar` while the README called Node optional, the
mirrors and their checkers drifted, and MCP `verify_run` ran commands in the
server's working directory instead of the project root.

Decision: the owner asked for the dependencies to go, which a second runtime
with npm packages cannot meet, and the 5.x trigger for unification had
already fired. 6.0 deletes the Node server and its mirrors. MCP is a thin
standard-library adapter that runs the CLI for every call, with tools
generated from the argparse tree. This supersedes the 5.x one-core decision.
Consequences: one implementation of every gate, no parity layer, no
install-time network fetch; each MCP call pays a Python process start, and
calls run serially.

### 2026-10-01: model agnostic, host-chosen delegation

Mythify names, ranks, routes to, switches, and spawns no model, provider, or
vendor CLI. `classify`, the host model switch, model policy, triage, model
profiles, and fanout are removed. `route` returns three neutral advisories in
`classification`: `framing`, `parallelism` (with `chooser: "host"`), and
`review`. The host may delegate to any subagent or to none; delegated output
is material until a `verify run` on the integrated result passes. Why: model
tables went stale between releases, made output depend on which binaries were
on `PATH`, and tied a protocol about evidence to vendors.

### 2026-10-01: product planning replaces design records

`design` and `review create` are removed. `product` records the problem, one
primary user, at most five measured outcomes, non-goals, bets with kill
criteria and deciders, and four risk kinds. Approval and bet verdicts need the
human's words, `product measure` is executable evidence, and `product
promote` turns a bet into a plan with lineage. Why: design approval was stored
and never read, and `plan create --design` kept a bare string. Product
records connect the reason for the work to its plans and measurements.

### 2026-10-01: downsize to the evidence loop

Update, 2026-10-28: campaign commands are available again as a plan-backed
interface. Campaign goals and learning live in plan metadata; the existing
plan steps and evidence gate own progress. No separate campaign task store
or executor is restored. Legacy v5 campaign files remain untouched. The
removal rationale below records the earlier implementation's weaknesses.

`trace`, `artifact`, `eval`, `campaign`, `research`, and `workspace` are
removed, with their modules, parsers, manifests, docs, and tests. Why, per
command:

- `trace` counted tool calls in exported agent traces, sliced them by exact
  model name, and installed per-model playbooks into a host skill directory.
  It recorded no evidence, nothing read its output, and comparing named
  models contradicts the model-agnostic decision above.
- `eval` grew scenarios for a local benchmark harness that drove named agent
  CLIs, and its scenario schema carried model and fanout fields. No install
  shipped the harness, so `eval adopt` printed a command for a file the user
  did not have.
- `artifact` was an HTTP client for an optional external service that strips
  provenance signals from files. By its own contract its output was never
  evidence. A project that needs that check can run the service's own CLI
  under `verify run`.
- `campaign` was a third loop family beside plans and outcome loops, with
  weaker gates: a task could complete on prose, and without a campaign verify
  command the verify phase advanced on prose.
- `research` was a source and claim ledger with no gate and no executed
  evidence. Map `research` tickets cover the same work.
- `workspace` validated a shared and a local config file that nothing outside
  its own module read. Outcome frozen paths come from CLI flags.

The `prompt research`, `prompt analysis`, and `prompt campaign` packets went
with the research and campaign routes. `route` sends a research-like prompt to
`map` when it asks several open questions, and to `direct` otherwise.
[DRIFT.md](DRIFT.md) section 8 lists the drift these features carried in 5.8.

### 2026-10-01: status replaces seven views

`dashboard`, `harness`, `background`, `progress`, `readiness`, `timeline`, and
`phase` are removed. `status` gains `--json`, `--recent`, the evidence
breakdown, recent records, and the attention detectors, issues first.
`outcome status` with no name and no active outcome lists every outcome. Why:
the views overlapped, and `readiness` graded user projects against Mythify's
own release gates, so every user project read as blocked. Maintainers use
[RELEASE-CHECKLIST.md](../RELEASE-CHECKLIST.md) and `scripts/lint.py`.

### 2026-10-01: usage errors exit 64

`MythifyArgumentParser` exits 64 (`EX_USAGE`) on usage errors. Why: argparse
exits 2, the code of an unverified verdict, so a typo read as a failed check
and the MCP server needed a stderr heuristic to tell the two apart.

### 2026-10-01: protocol spine with AGENTS.md canonical

`protocol/PROTOCOL.md` is a short spine with a 12,000-byte budget; the
generated `AGENTS.md` fell from 36,510 bytes in 5.8 to about 7,800. `AGENTS.md`
is the only full copy. `CLAUDE.md` is a generated `@AGENTS.md` pointer that
`protocol check` compares exactly, so no rule can sit beside the import
unchecked. `.cursorrules` and the thin loading profiles are gone. Why: a small
model loads the whole spine, one copy cannot drift from another, and detail
lives in docs read on demand.

### 2026-10-01: a typed MCP core plus one escape hatch

37 typed tools cover the daily loop (orientation, plans, steps, evidence,
memory, lessons, outcomes, maps, products); every other command runs through
the `mythify` tool, whose description lists them. Why: the 5.8 listing of 63
tools cost about 68 KB of host context each session, mostly for tools a
session never called. The 6.0 listing of 38 tools is about 30 KB.

### 2026-11-08: fast, selective verification

`verify run --reuse` stands in an earlier passing run, and it is opt-in: the
environment digest covers the command, commit, worktree digest, Mythify
version, platform, `PATH`, and the executable, but not files ignored by
`.gitignore`, services, or the network, so the caller decides when that is
enough (`MYTHIFY_REUSE_ENV` adds inputs). The test-count drop and high-risk
path checks warn by default and refuse only under strict context, the same
precedent as stale evidence, which keeps 6.2 a minor release.
`verify gate` is read-only so a host stop or pre-commit hook can call it
without changing state. Findings review rounds are capped at three, and a
fourth needs `--rethink TEXT`, because a loop that does not converge in three
rounds usually has the wrong approach, not too few rounds. Why: verification
cost grew with the suite, and the fix had to speed up honest runs without
letting a self-report stand in for one.

### 2026-11-09: route reports a user-owned policy, advisory only

`mythify route` shows what the user's routing policy file says about the task:
the matched step, role, options, effort, and review tier. The file is found
through `MYTHIFY_AGENT_POLICY`, else `~/.agents/policy.json`, and its schema
(version 1) is owned outside Mythify, which reads only the keys it prints.
Mythify never picks, ranks, or switches a model here: it copies the user's own
words, drops options the file bans, lowers efforts the file caps or forbids to
the highest allowed level, replaces banned names and forbidden efforts in every
printed string with `[withheld]` in one pass (so text and JSON agree), and it
contains no model or vendor name. Without a file, or with an invalid one, the
output is byte-for-byte what it was. The task-to-step match uses a separate
`policy_steps` section of `protocol/classification-rules.json` (production,
image, bulk, browser_check) so the `classification` block never changes (step 1
prefers recall: a clause whose operative verb is an operation verb such as
deploy, ship, or release, ssh, rsync, or scp as the main verb, or a money phrase;
only a coding verb whose object is a code noun that the operation word modifies
stays build, and an ambiguous prompt goes to step 1; step 1 and any
`high_tier_terms` word such as auth, billing, or migration raise the delegation
review tier to at least high, in the block only); the
other steps come from the route (direct is question only for a question or
research task with no file changes and build otherwise, map and product are
design, the rest are build, and review uses the reviewer role). Why: users who
already decide who does what want the answer next to the evidence loop, and
printing their own file is the neutral way to do it. This is the case the
clarified product non-goal N1 allows: printing a policy file the user owns is
not choosing a model.
