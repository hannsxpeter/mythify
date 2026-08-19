# Blast-radius safety cases

Mythify uses blast-radius reviews for changes whose main risk sits outside the
visible diff. A review names one safety fact, records the exact source state,
separates real risks from cleared checks, and stays unproven until a command
runs against that same source state.

## When to use one

`route` selects the review workflow for prompts such as:

- `blast radius of this change`
- `what could this break`
- `review this small diff I do not trust`

The review prompt asks the agent to inspect more than direct callers. Relevant
surfaces include lifecycle timing, teardown, wire formats, database columns,
feature flags, pinned dependency source, local patches, and consumers in other
languages or services.

## CLI workflow

Create the immutable safety case first:

```bash
mythify review blast-radius \
  --status warn \
  --path src/cache.py \
  --safety-fact "eviction removes only expired entries" \
  --proof-depth 2 \
  --risk '{"failure_mode":"live entries are removed","path":"src/cache.py","line":42,"likelihood":"low","impact":"high","disposition":"unproven","check":"python3 -m unittest tests.test_cache"}' \
  --cleared '{"failure_mode":"the wire payload changes","path":"src/cache.py","line":42,"likelihood":"low","impact":"medium","check":"rg -n \"serialize\" src tests"}' \
  --merge-command "python3 -m unittest tests.test_cache" \
  --name cache-eviction
```

Then run the stored merge command and inspect the derived result:

```bash
mythify review prove cache-eviction
mythify review show cache-eviction
mythify review show cache-eviction --json
```

`review prove` accepts `--command` to override the stored command for that run,
`--claim` to label the verification, `--mode executed|runtime`, and `--timeout`.
Runtime mode represents a running-app or integration reproduction and produces
proof depth 5 when it passes. Ordinary executed mode produces depth 4.

Exit codes are:

- `0`: the command ran against the exact reviewed change and passed.
- `1`: the review is missing, has the wrong type, is stale, or has no command.
- `2`: execution was disabled, the command failed, or a passing command changed
  the reviewed source.

## Proof ladder

Every safety fact reports the highest supported depth:

1. The fact was stated.
2. The fact points to concrete source.
3. The failure was traced and shown not to reach.
4. A script or test ran the shipped code and would fail loudly if the fact were
   false.
5. The behavior was reproduced through the running app or an integration path.

Creation accepts only depths 1-3. Depths 4 and 5 come only from linked executed
verification. A failed command still records that execution happened, but the
safety fact remains unproven.

## Risk record

Each risk contains:

```json
{
  "failure_mode": "live entries are removed",
  "path": "src/cache.py",
  "line": 42,
  "likelihood": "low",
  "impact": "high",
  "disposition": "unproven",
  "check": "python3 -m unittest tests.test_cache",
  "evidence_id": null
}
```

`likelihood` and `impact` are `low`, `medium`, or `high`. `disposition` is
`confirmed`, `cleared`, or `unproven`. The CLI `--cleared` option forces the
cleared disposition so a checked non-risk cannot be confused with an open one.

## Exact-change binding

The review stores:

- the current Git commit;
- whether the worktree was clean;
- `worktree_digest`, a SHA-256 fingerprint over the tracked binary diff and
  the paths and Git object hashes of untracked files.

The digest does not persist source file contents. It distinguishes two dirty
worktrees that share the same commit, which the older clean or dirty boolean
could not do.

Proof refuses to start when the current commit or digest differs from the
review. The executed verification stores the same provenance plus typed
`review:<name>` lineage. Status is derived from that append-only evidence at
read time. The original review JSON is never edited to claim success.

## MCP parity

The MCP server exposes the same state contract through:

- `blast_radius_review_create`
- `blast_radius_review_prove`
- `blast_radius_review_status`

CLI-created reviews can be proved and read through MCP, and MCP evidence is
recognized by the CLI. Both runtimes compute the same dirty-worktree digest and
revision lineage. The `quality` tool profile includes all three tools.

## Security and evidence boundary

- The review is material, not verification evidence.
- `MYTHIFY_DISABLE_RUN=1` refuses proof before command execution.
- Proof uses the shared bounded, redacted verification runner.
- Output artifacts stay under `.mythify/verification-artifacts/` and follow the
  existing redaction and size limits.
- A proof command that passes but changes reviewed source returns an unproven
  result.
- A stale review must be recreated for the current change. Proof from another
  review revision or worktree digest does not count.

## Source and license

The workflow selectively adapts ideas from the
[pstack `blast-radius` skill](https://github.com/cursor/plugins/blob/main/pstack/skills/blast-radius/SKILL.md),
which is available in Cursor's MIT-licensed plugins repository. Mythify keeps
its own immutable state, typed lineage, cross-runtime schema, execution kill
switch, redacted artifacts, and release-gate integration.
