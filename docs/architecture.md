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

## Runtime layout

`scripts/mythify.py` is the only entry point. It holds `VERSION`, state
directory resolution, the plan and step store, the shared verification runner
(`execute_recorded_verification`, `run_shell_capture`), `summary`, and
`main()`. Modules that need its helpers receive them through `configure_*`
calls at import time, and the parser receives the handlers as a symbol
table, so no module imports the entry point.

| Module | Owns |
| --- | --- |
| `mythify_parser.py` | `build_parser`, `MythifyArgumentParser` (usage errors exit 64), and every command grammar not listed elsewhere |
| `mythify_map_parser.py`, `mythify_product_parser.py` | the `map` and `product` grammars (the `verify`, `reflect`, `review`, and `lineage` grammars sit beside their handlers) |
| `mythify_verification_commands.py` | `verify run`, `verify claim`, `reflect`, display-only test counts |
| `mythify_io.py` | atomic writes, JSONL locks, tolerant reads, the chained append, verification anchors |
| `mythify_evidence_guard.py` | no-op verifier detection, zero-test detection, ledger chain checks, legacy opt-out detection |
| `mythify_provenance.py` | git commit, clean flag, and `worktree_digest` for each executed record |
| `mythify_memory.py`, `mythify_log_compaction.py`, `mythify_loopfit.py` | memory and lessons, `logs compact`, the `loop-fit` advisory |
| `mythify_outcomes.py` | outcome loops: start, check, run, status, results, stop, scope and frozen paths |
| `mythify_maps.py` | decision maps, tickets, fog, scope-out, promote |
| `mythify_product.py` | product records, readiness check, approval, verdicts, promote, measure, show |
| `mythify_quality.py` | blast-radius safety cases: create, prove, show |
| `mythify_lineage.py` | typed parent references and current, stale, missing, or unknown status |
| `mythify_plan_import.py`, `mythify_godfiles.py` | read-only godplans and godaudits parsing and `plan import` |
| `mythify_classification.py` | deterministic task classification from `protocol/classification-rules.json` |
| `mythify_router.py` | `route` selection and `prompt` packets |
| `mythify_views.py` | `status`, `history`, `report`, and the attention detectors |
| `mythify_protocol.py` | `protocol check`, the embedded protocol digest, the CLAUDE.md pointer text |
| `mythify_runtime_helpers.py` | timestamps, slugs, output redaction, `ChildTerminationGuard` |
| `mythify_mcp.py` | the MCP stdio server |

Data files in `protocol/`:

| File | Read by | Content |
| --- | --- | --- |
| `PROTOCOL.md` | `build_variants.py`, `protocol check` | the protocol spine, the source of AGENTS.md |
| `classification-rules.json` | `mythify_classification.py` | schema 4: task types, risk terms, framing, parallelism, and review advisories |
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
`last_updated`, and when present `lineage`, `source` (map promote: `kind`
`map`, `map`, `destination`, `decisions`, `out_of_scope`; plan import: `kind`,
`path`, `version`, `imported_at`), `product_ref` (`product`, `bet`,
`outcomes`), and `strict_context: true` for imported plans.

Step: `id` (integer), `title`, `success_criteria`, `status` (`pending`,
`in_progress`, `completed`, `failed`, `skipped`), `result`, and when set
`verify_command`, `verification_anchor` (`{"after_sha256": <hex or null>}`),
`updated_at`, and `strict_gate_waived`. Imported steps add `source_id`,
`wave`, `phase`, and `artifact_checked: true` when the task's box was checked
in the artifact; such a step still imports as `pending`. A step written before 6.0 may carry an integer
`verification_cursor`; it is still read and is dropped when the step restarts.

Verification record (`verifications.jsonl`, one JSON object per line):

- executed: `id` (`v-<stamp>-<12 hex>`), `kind: "executed"`, `claim`,
  `command`, `exit_code`, `duration_seconds`, `stdout_tail`, `stderr_tail`
  (redacted, last 4000 characters), `verified`, `timestamp`, `provenance`
  (`git_commit`, `worktree_clean`, `worktree_digest`, `mythify_version`),
  `artifacts` (`stdout` and `stderr`, each `path`, `bytes`, `source_bytes`,
  `sha256`, `truncated`, `redacted`), optional `test_count` (display only),
  optional `lineage`, then context: `plan`, `step_id`, `step_title`,
  `step_status` for `verify run` and `plan verify`; `map`, `ticket_id`,
  `ticket_title`, `ticket_type` for `map verify`; `review` and `proof_mode`
  for `review prove`; `product` and `outcome_id` for `product measure`.
  Outcome iterations append an executed record without `id` that carries
  `outcome`, `iteration`, `outcome_verify`, and `outcome_metric`.
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
covered path mapped to the sha256 of its bytes at start), `scope_baseline`
(`git_commit` and `ignore_rules` when `outcome run` first watches paths),
`superseded_by`, `evidence_stale`, `last_audit`. Each
`iterations.jsonl` line holds the iteration number, the `verify` and `metric`
runs, cost, `scope_violations`, `frozen_violations`, `status_after`, and
`next_action`.

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
  `verdict_human_input`, `plan`, and `promoted_at`, `decided_at`,
  `shipped_at`, and `human_input_waived` once set.
- `risks`: `id` (`R1`...), `text`, `kind` (`value`, `usability`,
  `feasibility`, `viability`), `mitigation`, `validation`.

Every item also has `created`. Workflow is in
[product-planning.md](product-planning.md).

Review (`reviews/<slug>.json`, `schema_version` 1, `kind`
`blast_radius_review`): `name`, `status`, `changed_paths`,
`change_fingerprint`, `safety_fact`, `risks`, `merge_gate`, `created`,
`updated`, `evidence_status`. Never rewritten; proof status is derived from
linked records at read time ([blast-radius.md](blast-radius.md)).

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
iteration twice. A JSON file that fails to
parse is moved to `<name>.corrupt-<stamp>` with a warning, and a malformed
JSONL line is skipped with a warning. `logs compact` copies the whole raw log
to `logs/archive/` before trimming, keeps retained lines byte for byte so the
hash chain still links, and leaves every verification artifact in place.

## Evidence rules

- Strict gate. `step ID completed RESULT` needs a non-empty RESULT and, unless
  `MYTHIFY_REQUIRE_VERIFIED_STEP` is `0`, `false`, `no`, or `off`, an executed
  record with `verified: true` and exit 0, recorded after the step's anchor,
  carrying this plan and step id (context-free records from older versions
  still match on a non-imported plan), and running the stored
  `verify_command` exactly. A waived completion is stamped
  `strict_gate_waived: true` with a warning. When the commit or worktree
  digest moved after the passing run, completion warns, and on an imported
  (`strict_context`) plan it refuses.
- Anchors. `step ID in_progress`, `plan verify`, and `map claim` store
  `verification_anchor`, the hash of the ledger's last line; only later
  records count. Compaction keeps lines byte for byte, and an anchor found
  only in an archive means every live record is newer, so anchors survive
  `logs compact`. With no placeable marker, only records strictly after the
  step or claim timestamp count.
- Executed versus attested. `verify claim` records a self-report with
  `verified: null`. It never satisfies a gate.
- No-op warnings. A verify command such as `true`, `/usr/bin/true`, `:`,
  `exit 0`, or a bare `echo` or `printf` with no shell operator draws a
  `[WARN]` when stored on a step and an attention item in `status` when it
  passes. A pass whose output says no tests ran is flagged the same way. These
  are advisory for steps; `review prove` and `product measure` refuse them.
- Human gates. `map resolve` on a `hitl` ticket (`grilling`, `prototype`, or
  `--mode hitl`), `product approve`, and `product decide` refuse without a
  non-empty `--human-input`. `MYTHIFY_REQUIRE_HUMAN_INPUT=0` waives this and
  stamps `human_input_waived` on the ticket and its map decision, the
  approval, or the bet. `status` warns about the opt-out while the variable
  is set in its environment, and about each waived decision in every later
  session while the work it governs is live: a ticket until its map is
  promoted, a decision the active plan carried from its map, a product's
  current approval, and a bet verdict until the bet stops or a verdict with
  the human's words replaces it. `map show`, `plan show`, and `product show`
  mark waived decisions.
- Fail closed. With `MYTHIFY_DISABLE_RUN=1`, `verify run`, `plan verify`,
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
- Typed tools. `TOOL_ALLOWLIST` names 37 command paths; each tool is the path
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
  on timeout or cancel the server kills the child's process group and reports
  exit 124. The CLI forwards SIGTERM and SIGINT to the verify child's tree and
  exits 128 plus the signal number. Calls run one at a time on a worker
  thread, so `ping` answers during a long call.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success, or a verified check |
| 1 | refusal or failure: no workspace, bad input, a gate refused, a record not found |
| 2 | unverified: a check ran and failed, `product check` found gaps, an outcome is not met, or `MYTHIFY_DISABLE_RUN=1` refused to run |
| 64 | usage error from the argument parser |
| 124 | MCP only: the call timed out and was terminated |
| 130, 143 | the CLI received SIGINT or SIGTERM while a verify child ran |

## Environment variables

| Variable | Effect |
| --- | --- |
| `MYTHIFY_DIR` | state directory; skips upward discovery |
| `MYTHIFY_REQUIRE_VERIFIED_STEP` | `0`, `false`, `no`, or `off` waives the strict gate (stamped, warned) |
| `MYTHIFY_REQUIRE_HUMAN_INPUT` | `0`, `false`, `no`, or `off` waives human gates (stamped, warned) |
| `MYTHIFY_DISABLE_RUN` | `1` makes every command runner refuse and exit 2; so does any value other than empty, `0`, `false`, `no`, or `off` (case and surrounding space ignored), so a malformed value fails closed |
| `MYTHIFY_VERIFY_MAX_OUTPUT_BYTES` | output cap per run, default 16 MiB; a run over the cap fails |
| `MYTHIFY_MAP_CLAIMANT` | default claimant for `map claim` |
| `MYTHIFY_MCP_CALL_TIMEOUT` | MCP per-call timeout in seconds, default 900 |
| `HOME` | locates `~/.mythify/lessons` |

`MYTHIFY_COST=<n>` is not an environment variable: an `outcome run` agent
prints it on stdout to report the cost of an iteration.

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
