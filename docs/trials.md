# Evidence trials

`mythify trial compare` tests whether a small instruction edit helps. The
operator runs the subjects and collects evidence. Mythify compares the
instruction text as data, runs the pinned evaluator through recorded
verification, and saves a decision for the operator.

```bash
mythify init                      # once, in a trusted controller directory
mythify trial compare trial.json --evaluator evaluator.py \
  --evaluator-sha256 "$(cat evaluator.sha256)"
```

`evaluator.sha256` is a lock file written when the evaluator was reviewed,
before any subject ran. Never derive the pin from the file you are about to
run. Run the command from the initialized controller directory, never from a
subject's workspace. It refuses a state directory that `mythify init` did not
create, and never creates one. Staging and output capture use scratch space
under that controller state, never the system temporary directory, so a
`TMPDIR` inside a subject is not touched.

To see a run end to end, execute
[examples/trial_demo.py](examples/trial_demo.py) from the repository root.
It builds a throwaway controller and fixture evidence, pins its evaluator by a
digest recorded in the script, and prints a `retain` decision from
hand-written fixture evidence.

## Manifest

Paths are relative to the manifest's directory or absolute. Every artifact is
pinned by its SHA-256. All JSON is parsed strictly: duplicate keys, `NaN`,
unknown keys, and non-boolean flags are refused.

```json
{
  "schema_version": 1,
  "baseline_revision": "<40 or 64 lowercase hex characters>",
  "runner": {"model": "descriptor", "environment": "descriptor"},
  "baseline": {
    "instructions": {"path": "base.md", "sha256": "<hex>"},
    "runs": [{"task_id": "t1", "task": {"path": "t1.md", "sha256": "<hex>"},
              "evidence": {"path": "base-t1.json", "sha256": "<hex>"},
              "workspace": "/abs/path/of/the/baseline/checkout"}]
  },
  "candidate": {
    "instructions": {"path": "cand.md", "sha256": "<hex>"},
    "parent_revision": "<same revision as baseline_revision>",
    "rationale": "Why this edit should help.",
    "predicted_gain": "optional, non-empty",
    "runs": [{"task_id": "t1", "task": {"path": "t1.md", "sha256": "<hex>"},
              "evidence": {"path": "cand-t1.json", "sha256": "<hex>"},
              "workspace": "/abs/path/of/the/candidate/checkout"}]
  },
  "replay": {
    "task_id": "r1", "task": {"path": "r1.md", "sha256": "<hex>"},
    "evidence": {"path": "replay.json", "sha256": "<hex>"},
    "workspace": "/abs/path/of/a/separate/checkout",
    "runbook": {"path": "runbook.md", "sha256": "<hex>"}
  },
  "forbidden_terms": ["name-to-keep-out-of-the-edit"]
}
```

`replay` and `forbidden_terms` are optional. Rules the command enforces:

- `model` and `environment` are opaque, non-empty descriptors shared by every
  run. The operator declares them truthfully, along with run freshness.
- The receipt records the supplied revisions. The content hashes bind the bytes.
- Baseline and candidate cover exactly the same task ids with identical task
  hashes. Each run has its own raw evidence: no evidence hash repeats anywhere.
- Baseline and candidate workspaces may coincide or exist only during
  evaluation. The replay workspace must not overlap any of them, and the
  replay needs a different task id, a different task hash, and a pinned
  runbook. The evaluator receives the runbook as plain text.
- The limits are 256 KiB each for the manifest, evaluator and evaluator output,
  64 KiB and 2000 lines for instructions, and 1 MiB for each other artifact.
  A trial can contain up to 50 runs per side, 256 distinct files and 32 MiB
  of staged inputs.

## The edit

The candidate may change at most 6 lines (removed plus added, line
separators included) and 1024 UTF-8 bytes, and must change at least one line
beyond surrounding whitespace. Added lines are screened
case-insensitively and are refused when they contain a task id, a
`forbidden_terms` entry, or one of: `benchmark`, `grader`, `grading`, `oracle`,
`hardcode` (and `hard-code`, `hard code`), `skip tests`, `skip verifier`,
`skip checks`, `answer key`, `reference answer`, `expected answer`. The screen
matches text mechanically. The evaluator and operator judge meaning, and
`rationale` records why the operator proposed the edit.

## Evaluator contract

The evaluator is one standalone Python program. Mythify stages the exact
pinned bytes, with the staged manifest, into a private scratch directory
outside every workspace, then runs
`python -I STAGED_EVALUATOR STAGED_MANIFEST` through the recorded runner with
the standard 300-second timeout. In the staged manifest every `{path, sha256}`
`path` points at the protected copy. The evaluator reads the raw evidence
and any references it needs. Its stdout contains a single JSON object.

```json
{"baseline": [{"task_id": "t1", "claimed_complete": true, "verified": false}],
 "candidate": [{"task_id": "t1", "claimed_complete": true, "verified": true}],
 "replay": {"task_id": "r1", "claimed_complete": true, "verified": true,
            "fresh_checkout": true, "runbook_executed": true}}
```

Every manifest task is scored exactly once per side, with real booleans.
`replay` appears exactly when the manifest supplies one. A verified completion
is a completion claim with verification. A false completion is a claim
without verification.

## Decision

| Decision | When | Exit |
| :--- | :--- | :--- |
| `retain` | Candidate has strictly fewer false completions, no fewer verified completions, and the replay is claimed, verified, fresh, and executed | 0 |
| `replay_required` | Same calibration result, but no replay was supplied | 0 |
| `reject` | A threshold failed, or a supplied replay is not a verified fresh run | 2 |
| `evaluator_failed` | `nonzero_exit`, `no_output` for missing or empty stdout, or `truncated` for clipped or oversized output | 2 |
| `evaluator_failed` | `malformed` for invalid score output, `lossy` for transformed capture, or `evidence_drift` for a change in staged inputs or captured output | 1 |

Invalid inputs (bad manifest, wrong pin, over-budget or shortcut edit,
controller path inside a workspace) exit 1 with a message on stderr, before
anything runs. With `MYTHIFY_DISABLE_RUN=1` the command exits 2 and runs and
records nothing.

The JSON on stdout carries the decision, `reasons`, `scores`, the evaluator's
`verification_id`, and for audit the `binding` (baseline revision, runner,
source and staged manifest digests), `pins` (evaluator, instructions, runbook),
`parent`, `policy` (configured forbidden terms and edit limits), replay
task and evidence pins, and `edit` sizes. Before printing, the command writes
the same JSON, plus `receipt_path`, atomically to
`verification-artifacts/<verification_id>/trial.json` in the controller state.
A write failure exits 1. The command saves verdicts for attempts that reach
the evaluator, including `evaluator_failed`. The evaluator's verification
record in `.mythify/verifications.jsonl` stays append-only, and carries the
pre-run receipt as `trial`. Cite the record in
learning with `mythify campaign learn TEXT --verification ID` (see
[campaigns.md](campaigns.md)).

## Trust

The operator and pinned evaluator own the evidence. Mythify scores the trial
from the evaluator's output on pinned raw evidence.
The evaluator, the manifest, the controller directory, and the `.mythify/`
store must be outside every subject's writable access. Mythify refuses
controller paths that overlap a declared workspace after resolving symlinks,
and relies on the host to isolate write access with separate operating-system
users, containers, or machines. Overlap is judged by
resolved name and by filesystem identity (so case aliases count), and
workspaces that no longer exist are compared by name. `python -I` keeps
checkout imports and search paths out of the evaluator. The operator owns the
truth of the same-model, same-environment, and freshness declarations and the
evaluator's correctness. After the run Mythify rereads every staged file
with a bound from its staged size. A changed, absent, or non-regular staged
file returns `evidence_drift`.

## Reference: the run that completed a step (version 2)

An evaluator that asks whether a completion was verified needs the one ledger
run that completed the step. The latest run of the prescribed command is the
wrong answer: a clean detached confirmation after a valid completion would
replace the original scoped run. A timestamp is no better: the runtime stamps a
run before it appends it, and wall clocks can move backwards. The reference
module [scripts/trial_evaluator_v2.py](../scripts/trial_evaluator_v2.py)
therefore takes the completion boundary from the trusted controller as an exact
ledger id and judges the run it names. It uses the standard library only,
reads files, writes nothing, runs nothing, reads no clock, and adds no CLI. The
adapter imports `select_completion_verification` from a pinned copy.

### Scope

The module covers the literal prescribed verifier of one completed step. It
compares commands as exact strings, so a trailing no-break space is a different
command. It leaves named checks, aliases, compacted ledgers, and the active
plan alone; each returns a refusal reason. The step must be completed with a
result, no strict-gate or risk-gate waiver, and its own
`verification_anchor`. A plan-level anchor never stands in for it.

### Callable

```python
select_completion_verification(
    state, *, plan_name, step_id, command, expected_test_count,
    current_provenance, completion_verification_id=None)
```

- `state` is the frozen `.mythify` directory. `plan_name` is a lowercase slug
  and `step_id` a positive integer. `command` equals the step's stored
  `verify_command`.
- `expected_test_count` is a positive integer the adapter knows without the
  ledger. `current_provenance` carries the `git_commit` and `worktree_digest`
  the trusted runtime recomputed for the checkout.
- `completion_verification_id` is the controller's witness: the id of the last
  executed append of the exact command that an independent, sealed controller
  saw before the successful completion. The controller and its raw trace own
  that chronology, including that the row was the last applicable append. Take
  the id only from controller events, never from subject-written text or a
  guess.
- The result holds `evaluator_version` (2), `verification` and
  `verification_id` (the named run or `None`), `reason` (`valid` or a compact
  code), `valid_evidence`, `invalidating_verification_id`, and
  `attribution_source` (`None`, or `controller_event_order` once a row is
  named). Input and evidence problems become a `reason`; an unexpected
  implementation error propagates.

The ledger is read as a bounded whole: 1 MiB, 256 KiB per line, 4096 records.
Each line is parsed, linked, and checked for a duplicate id before the next is
read, so the first broken link ends the work. The first record carries a null
`prev_sha256` and every later record the sha256 of the previous line without
its LF or CRLF, the line that
[scripts/mythify_io.py](../scripts/mythify_io.py) chains and anchors. A trimmed
or compacted prefix returns `ledger_compacted`, because it cannot prove the
test-count high-water mark. Freeze the full ledger.

### Choosing the run

- Candidates are executed runs of the same command appended after the step's
  anchor, whatever scope they carry, in ledger order. No timestamp is read.
- An empty candidate window returns `no_completion_verification`. In a
  populated window, the id positions the named run. An id without a matching
  candidate returns `completion_id_unmatched`.
- With a populated window and no id, the evaluator reports
  `all_candidates_defective` when every scoped run fails the execution, source,
  or expected-count checks. A scoped run that passes these initial checks
  returns `completion_order_unproven`. A window with only unscoped candidates
  returns `detached_only`.
- The named run is final. If it is failing, reused, out of scope, stale, or
  reduced, that is the answer, and no other run replaces it.
- Later candidates only confirm or invalidate. Each must be an actual, green,
  full-count run on the same source, whatever its scope. The first that is not
  names itself as `invalidating_verification_id` with a `later_` reason, even if
  a later run is green again. A clean detached run after a valid completion
  leaves the named run selected.
- A sound run is executed, green with integer exit 0, has no `reused_from` or
  `campaign_refusal`, ran exactly `expected_test_count` tests, is stamped to
  the plan, step, and `in_progress`, and carries a `git_commit` and
  `worktree_digest` equal to `current_provenance`. It also meets the
  test-count high-water mark from earlier runs, a retained `test_count_drop`
  note, a historical `test_drop_accepted` note, or a restored `test_drop_partial`
  note.

### Trust and independent checks

`valid_evidence` says only that the persisted verification is sound and that
the controller's id was applied to it. The adapter combines it with its
protected checkout check and independent oracle. It classifies a completion
claim, refusal, or missing response and takes verification IDs from structured
controller evidence.

Record the module's version and SHA-256 separately from the evaluator's pin.
Keep the original pin when rescoring a frozen trial and record the new pin
for the replay. Freeze the plan and full ledger outside every subject's
writable access. Restore the checkout under isolated controller conditions
and recompute `current_provenance` with the trusted historical runtime at
the original canonical checkout path. The historical digest compares every
resolved ignore-rule path and its contents, including the resolved global
ignore location, so the canonical path alone is not enough: reproduce all of
that context under the isolation.
