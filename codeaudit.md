# Mythify Code Audit

Read-only audit. Date: 2026-06-12. This report is self-contained: every finding
cites exact locations and how to verify a fix, so it can be acted on without
having watched the audit.

## Remediation status

Updated: 2026-06-12. Status: all numbered findings are completed and verified.

| Finding | Status | Evidence |
| :--- | :--- | :--- |
| [QUAL-001] Generated variants stale | Completed | `python3 scripts/build_variants.py && git diff --exit-code CLAUDE.md AGENTS.md .cursorrules` exited 0; stale 15-tool wording is absent from generated variants. |
| [DOC-001] CHANGELOG lagging 2.4.0 | Completed | CHANGELOG now has 2.2.0, 2.3.0, and 2.4.0 sections, current compare links, and no stale 15-tool claim. |
| [DOC-002] Release work uncommitted and without CI provenance | Completed | Release commit `5097767d3bcfaad1d094eedf147e6417c57cb1c7` is pushed to `origin/main`; CI run `27444101777` passed; tags `v2.2.0`, `v2.3.0`, and `v2.4.0` exist on `origin`. |

Note: the 2.2.0 and 2.3.0 tags are backfilled annotated version markers on
the same green release commit as 2.4.0 because the 2.2.0 through 2.4.0 changes
landed together in one commit.

## Snapshot

- Project: Mythify, a portability layer that gives any model Mythos-class
  operational discipline (planning loops, executed verification, persistent
  memory, parallel delegation) via a drop-in protocol, a Python CLI, and a Node
  MCP server.
- State at audit time: branch `main`, HEAD `a5004bf`, with 21 uncommitted
  changes (19 modified, 5 untracked paths). Current remediation state: release
  commit `5097767d3bcfaad1d094eedf147e6417c57cb1c7` is pushed to
  `origin/main`; version tags `v2.0.0` through `v2.4.0` exist on `origin`.
- Languages: Python 3.9+ (CLI, stdlib only), Node 18+ ESM (MCP server, deps
  `@modelcontextprotocol/sdk` + `zod`).
- Size: scripts/mythify.py 3468 LOC, mcp-server/src/index.js 3235 LOC,
  mcp-server/src/fanout.js 2063 LOC, scripts/local_model_eval.py 616 LOC, plus
  ~1600 LOC of tests. ~11K LOC total in load-bearing files.
- Entry points: `scripts/mythify.py` (argparse CLI), `mcp-server/src/index.js`
  (stdio MCP server, 19 core tools), `mcp-server/src/fanout.js` (3 fanout tools).
- Maturity: pre-1.0 developer tooling with unusually strong test discipline
  (100 passing tests, executed-verification eval harness).
- Coverage: near-exhaustive on the contract surface (design.md, both
  implementations, generated variants, tests). Sampled: the deep internals of
  individual tool handlers (read the load-bearing ones: outcome_check,
  host_model_switch, classify_task, fanout engines).
- Exclusions: node_modules, dist, docs/research-report.md (exempt legacy),
  package-lock.json.

## Overall score

83/100 - Grade B (solid, minor issues). Original audit score; all numbered
findings below are now remediated.

Mythify is a genuinely well-built tool: a single-source-of-truth contract
(design.md) with two independent implementations that interoperate, executed
verification enforced over self-report, and 100 passing tests including an
eval harness that grades by re-running the suite rather than trusting model
output. At audit time, the score was held below an A by self-inflicted drift,
not by defects: the project's own anti-drift CI gate was red because the
generated protocol variants were not regenerated after PROTOCOL.md grew to 22
tools, and the CHANGELOG and release markers lagged the code by three minor
versions. Those drift findings are now completed and verified in the
remediation status table above.

Calibration: graded as pre-1.0 developer tooling. The tool executes arbitrary
shell by design, so "runs what you tell it" is the feature, not a vulnerability;
security is judged on whether that power is bounded and documented (it is).

| Dimension | Score | Grade | Weight | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| Security | 85 | B | 20% | Arbitrary execution is intended, bounded by kill switches, depth guard, curated worker env; command engine env inheritance is documented. |
| Architecture and Design | 88 | B | 15% | Contract-first, single protocol source, shared state contract across CLI and MCP; drift is process, not structure. |
| Code Quality and Maintainability | 80 | B | 15% | Consistent style and ASCII discipline, but index.js and mythify.py are very large single files under growth pressure. |
| Testing and Verification | 85 | B | 15% | 100 tests (71 Python + 29 Node), interop and stub-binary tests, eval harness grades by execution; CI is now green for release commit `5097767`. |
| Error Handling and Resilience | 85 | B | 10% | Atomic writes, corrupt-file quarantine, graceful no-state, per-worker timeouts. |
| Performance and Efficiency | 82 | B | 8% | Not a hot-path system; fanout has a concurrency cap and per-task byte caps. |
| Dependencies and Supply Chain | 85 | B | 7% | Minimal deps, committed lockfile, CLI is stdlib-only. |
| Documentation and Drift | 72 | C | 5% | Extensive docs. The stale variants and lagging CHANGELOG findings are now fixed. |
| Observability and Operability | 80 | B | 5% | ASCII markers, durable state, verification log; appropriate for a CLI/MCP tool. |
| Overall (weighted) | 83 | B | 100% | Solid; drag is drift and release lag, not correctness. |

Default weights; no re-weighting. No Critical finding, so no dimension or
overall cap is triggered.

## What to fix first

1. [x] [QUAL-001] Generated protocol variants were stale (said "15 tools", source said 22) - High, S - completed and verified.
2. [x] [DOC-001] CHANGELOG stopped at 2.1.0 while everything else was 2.4.0; it also said fanout brought the server to "15 tools" - Medium, S - completed and verified.
3. [x] [DOC-002] Three minor versions of features (outcome loops, classify_task, host_model_switch, codex-cli/cursor-agent engines, eval harness) were uncommitted and unreleased at audit time - Medium, M - completed and verified.

## Strengths (preserve these)

- Executed verification is real and the new code keeps it. `outcome_check`
  runs the verifier itself (`mcp-server/src/index.js:` outcome_check handler,
  `const verify = runShellCapture(goal.verify_command, timeout)`) and only
  reports success when `verify.verified && metricOk`. It does not trust notes.
- The eval harness grades honestly. `scripts/local_model_eval.py` re-runs
  `python3 -m unittest` against the worker's modified workspace and reads the
  exit code (`scripts/local_model_eval.py:196` subprocess.run on the suite),
  and the protocol arm is told "Do not claim completion unless verify run
  records exit 0" (`scripts/local_model_eval.py:301`). The eval applies the
  project's own discipline to itself.
- Single source of truth for the protocol. `protocol/PROTOCOL.md` generates the
  three variant files via `scripts/build_variants.py`; design.md is the one
  contract both implementations follow. This is exactly why QUAL-001 is
  catchable rather than silent.
- Contract stays in sync at the code level. design.md (`docs/design.md:308`,
  `:873`) is at version 2.4.0 and 22 tools, matching package.json (2.4.0),
  index.js VERSION (2.4.0), the 19 registered core tools plus 3 fanout tools,
  and the smoke test's 22-tool assertion.
- Both suites pass: `python3 -m unittest discover -s tests` runs 71 tests OK;
  `npm test` runs 29 tests, 29 pass. The stub-binary fanout test verifies the
  claude-cli engine's argv and curated environment without network.
- Durability discipline: atomic writes, corrupt-file quarantine to
  `<file>.corrupt-<timestamp>`, graceful behavior on missing state, per-worker
  timeouts and a fanout depth guard.

## Systemic patterns (root causes)

- Regeneration and release steps lag manual source edits. The variant drift
  (QUAL-001), the stale CHANGELOG (DOC-001), and the uncommitted feature set
  (DOC-002) are three faces of one root cause: features were added to the
  source files (PROTOCOL.md, code) without running the downstream steps
  (`build_variants.py`, CHANGELOG update, commit, tag, CI). Root fix: run the
  generator, update the CHANGELOG, and commit before considering a feature
  done; a pre-commit hook running `build_variants.py` plus
  `git diff --exit-code` would make QUAL-001 impossible to commit.

## Findings

### [QUAL-001] Generated protocol variants are stale relative to PROTOCOL.md
- Severity: High | Confidence: Confirmed | Effort: S | Dimension: Code Quality and Maintainability
- Status: Completed. Verified with the variant generator, generated-file diff
  check, and stale generated tool-count search.
- Location: `CLAUDE.md:109`, `AGENTS.md:109`, `.cursorrules` (MCP note section); source `protocol/PROTOCOL.md:130`
- Original evidence: `protocol/PROTOCOL.md:130` listed "exactly 22 tools: `classify_task`, `host_model_switch`, ...". At audit time, the three generated variants still said "through exactly 15 tools: `memory_store`, ..." and omitted the new tools. Running `python3 scripts/build_variants.py` rewrote all three (102-line diff), and a byte comparison of each variant's body against PROTOCOL.md returned False for all three. The repo's own `.github/workflows/ci.yml` hygiene job runs `build_variants.py` then `git diff --exit-code CLAUDE.md AGENTS.md .cursorrules`, which would have failed on that tree.
- Impact at audit time: The CI hygiene gate was red on the working tree; any push or PR would have failed. Agents reading CLAUDE.md or AGENTS.md (the primary on-ramp for Claude Code and AGENTS.md-convention tools) were told the server had 15 tools and were not informed of classify_task, host_model_switch, the five outcome tools, or the fanout tools.
- Recommendation: Run `python3 scripts/build_variants.py` and commit the regenerated `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` alongside the PROTOCOL.md change.
- Verify the fix: `python3 scripts/build_variants.py && git diff --exit-code CLAUDE.md AGENTS.md .cursorrules` exits 0.
- Related: systemic pattern (regeneration lag).

### [DOC-001] CHANGELOG is three minor versions behind and reports a stale tool count
- Severity: Medium | Confidence: Confirmed | Effort: S | Dimension: Documentation and Drift
- Status: Completed. Verified against `mcp-server/package.json`,
  `CHANGELOG.md`, current compare links, and stale tool-count search.
- Location: `CHANGELOG.md:86` (last entry is `## [2.1.0]`), `CHANGELOG.md:8` ("bring the server to 15 tools")
- Original evidence: `mcp-server/package.json` version was `2.4.0`, `mcp-server/src/index.js:20` had `const VERSION = "2.4.0"`, and `docs/design.md:873` said "This is Mythify v2.4.0. Fanout was added in 2.1.0; 2.2.0 added local ...". At audit time, CHANGELOG.md had no entries for 2.2.0, 2.3.0, or 2.4.0. Its newest entry (`CHANGELOG.md:8`) still stated fanout brought the server to 15 tools, but the server exposed 22.
- Impact at audit time: The CHANGELOG no longer described the shipped product. A user reading it saw neither outcome loops, the model-policy tools, the additional fanout engines, nor the eval harness, and was told the wrong tool count.
- Recommendation: Add `## [2.2.0]`, `## [2.3.0]`, and `## [2.4.0]` sections covering the features now in the tree (outcome loops; classify_task and host_model_switch; codex-cli and cursor-agent fanout engines; local_model_eval.py); correct the 2.1.0 entry's "15 tools" to reflect that fanout itself added 3 tools. Update the compare links at the bottom.
- Verify the fix: CHANGELOG's top version section matches `package.json` version; `grep -n "tools" CHANGELOG.md` shows no count that contradicts the registered 22.
- Related: systemic pattern (regeneration lag); QUAL-001.

### [DOC-002] Three minor versions of features are uncommitted and have never run through CI
- Severity: Medium | Confidence: Confirmed | Effort: M | Dimension: Documentation and Drift
- Status: Completed. Release commit `5097767d3bcfaad1d094eedf147e6417c57cb1c7`
  is pushed to `origin/main`, CI run `27444101777` passed, and tags `v2.2.0`,
  `v2.3.0`, and `v2.4.0` exist on `origin`.
- Location: working tree (`git status`): 19 modified files plus untracked `docs/codex-integrations.md`, `docs/desktop-tool-calls.md`, `mcp-server/client-configs/`, `scripts/local_model_eval.py`, `tests/test_local_model_eval.py`
- Original evidence: HEAD was `a5004bf` (the 2.1.0-era fanout commit). The tags `v2.0.0` and `v2.1.0` existed; there was no commit or tag for the 2.2.0 through 2.4.0 work. Everything from outcome loops onward sat uncommitted. The GitHub Actions CI that gates the published releases had therefore not executed against this code; the only verification it had was the local run in this audit.
- Impact at audit time: The known-green provenance (CI on every push) that the project established for 2.0.0 and 2.1.0 did not cover the majority of the current feature set. A clone of the public repo got none of these features; a loss of this working tree would have lost them entirely.
- Recommendation: After QUAL-001 and DOC-001, commit the feature set in coherent chunks with Conventional Commit messages, push so CI runs, and tag the releases the version numbers already claim (2.2.0 through 2.4.0) once green.
- Verify the fix: `git status --porcelain` is empty; `gh run list` shows a green CI run for the new HEAD; `git tag -l` includes the versions design.md references.
- Related: systemic pattern (regeneration lag).

## Dimension notes

- Security (85): The tool executes arbitrary shell by design; this is bounded
  by `MYTHIFY_DISABLE_RUN` and `MYTHIFY_DISABLE_FANOUT` kill switches, a
  fanout depth guard, and curated worker environments for the claude-cli,
  codex-cli, and cursor-agent engines (SECURITY.md:34-45). The command engine
  inherits the server environment, which SECURITY.md states plainly. No
  undocumented exposure found.
- Architecture (88): Contract-first with a single protocol source and two
  interoperating implementations. The drift findings are process failures, not
  architectural ones; the architecture is in fact what makes the drift
  detectable.
- Code Quality (80): Style and ASCII rules hold across all changed files (scan
  clean). The pressure point is file size: index.js (3235) and mythify.py
  (3468) are large enough that future contributors will struggle to hold them
  in head; consider splitting tool handlers into modules as fanout.js already
  demonstrates.
- Testing (85): 100 passing tests with meaningful assertions, an interop test
  across CLI and MCP, a stub-binary test for the claude-cli engine, and an
  offline test for the eval harness. The original deduction was the red
  hygiene gate from QUAL-001; CI is now green for release commit `5097767`.
- Error Handling (85): atomic writes, corrupt-file quarantine, graceful
  no-state reads, worker timeouts. Consistent with the 2.0.0 baseline.
- Performance (82): concurrency cap and per-task byte caps on fanout; no hot
  path. Suspected-free; nothing to flag.
- Dependencies (85): zod + MCP SDK only, lockfile committed, CLI stdlib-only.
- Documentation (72): the lowest dimension, entirely due to the two drift
  findings; the prose docs themselves (design.md, the integration guides) are
  thorough and in sync with the code.
- Observability (80): ASCII markers, durable per-project state, an append-only
  verification log; appropriate to the project's maturity.

## Remediation plan

- Quick wins (High/Critical, Confirmed, S): QUAL-001 completed.
- Plan now (High/Critical, M or L): none Critical. DOC-002 completed after
  QUAL-001 and DOC-001.
- Verify first (Suspected): none; all findings are Confirmed.
- Backlog (Low): file-size refactor of index.js and mythify.py (noted under
  Code Quality, not raised to a numbered finding).

## Scope and limitations

- Examined: git state, design.md contract, both implementations' tool surface
  and registration, generated-variant sync (by running the generator and
  restoring it afterward, since the audit is read-only), the ASCII house-rule
  scan over all changed and untracked files, both test suites (executed), the
  outcome_check and eval-harness internals, and SECURITY.md claims against code.
- Not exhaustively read: every individual tool handler body in index.js, and
  the full text of the untracked integration docs (scanned for ASCII only).
- Assumption that would change conclusions if untrue: that the remote `v2.1.0`
  tag matches the local HEAD `a5004bf` (the diff-against-tag confirmed the new
  work is post-tag). If the remote has diverged, DOC-002's provenance claim
  should be re-checked against the actual remote.

## How to use this report (for the acting agent)

1. Triage by severity and confidence. Confirmed Critical and High are safe to
   act on now, in the order in "What to fix first". Re-verify any Suspected
   finding against the cited code before changing anything. (There are no
   Suspected findings here.)
2. Fix root causes first; prefer systemic patterns over individual leaves.
3. Preserve the strengths; do not refactor them away while fixing other issues.
4. Confirm the stated assumption on Likely findings before acting. (None here.)
5. One finding, one change, verified: after each fix run its "Verify the fix"
   step; keep changes atomic and traceable to the finding ID.
6. Do not widen scope silently; note adjacent issues rather than sprawling into
   a rewrite.
7. Re-run the audit to measure progress; confirm findings are resolved, not
   relocated, and watch for regressions in the strengths.
