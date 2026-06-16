# Mythify Code Audit

Read-only audit. Date: 2026-06-15. This report is self-contained: every finding
cites exact locations, carries its own context, and states how to verify the fix.
No source files were modified. The only file created is this one.

## Snapshot

- Project: Mythify, an evidence protocol for AI coding agents (CLI + MCP server + protocol manifests).
- State: branch `main`, commit `4f0177b` ("fix: harden strict step evidence gate").
- Languages: Python 3.9+ (CLI), JavaScript / Node 18+ (MCP server). JSON manifests; Markdown protocol/docs.
- Size: `scripts/mythify.py` 10,283 lines; `mcp-server/src/index.js` 9,051 lines; `mcp-server/src/fanout.js` 2,379; plus `capability-registry.js` (883) and small registry shims. ~22.6k lines of core code.
- Frameworks/deps: Python zero-dependency (stdlib only). Node: `@modelcontextprotocol/sdk@1.29.0`, `zod@4.4.3` (only two direct deps, pinned via lockfile with SRI).
- Entry points: `scripts/mythify.py` `main()` (argparse dispatch to 61 `cmd_*` handlers); `mcp-server/src/index.js` `main()` registering 40 MCP tools; shared facts in `protocol/{operation-registry,classification-rules,workflow-router,surface-manifest}.json` + `PROTOCOL.md`.
- Evident maturity: mature, deliberately engineered (v3.6.x, multi-version CI, 227 tests, a strict evidence doctrine). Held to a high bar for a developer tool, especially on correctness of the evidence gate, which is the product's central promise. Not held to a production web-service operability bar.
- Audit coverage: the load-bearing code was read exhaustively (verify run, the strict step gate, outcome loops, atomic IO, state-dir resolution, the fanout spawn path, the protocol-hash check, both runtimes' gate implementations, the drift-guard scripts, the test suites). The remaining bulk of `mythify.py` and `index.js` was sampled (all command/tool registrations enumerated; longest functions inspected). Both test suites were executed by the analysis (Python 128 tests pass, MCP 99 tests pass).
- Exclusions: `node_modules/`, `__pycache__/`, `.mythify/local-*.json` benchmark outputs, `coverage/`, `dist/`.

## Overall score

**77/100 - Grade C (adequate, real gaps).**

Mythify is strong where it counts for credibility: an honest, well-tested evidence gate, a genuinely zero-dependency CLI, accurate documentation, and a real corruption-quarantine and atomic-write discipline. It is dragged down by one architectural decision, two hand-maintained native runtimes over one state directory, whose parity is guarded only mechanically (byte-identical JSON, name counts) and not behaviorally. That gap is not theoretical: it has already produced a confirmed correctness bug in the flagship gate (cross-runtime timestamp formats) and several smaller behavioral divergences. A secondary theme is the absence of concurrency safety despite a design that spawns parallel workers sharing the state dir.

Calibration: graded as a mature developer tool / agent protocol; concurrency and the evidence gate are held to a high bar because the project's value proposition is trustworthy, durable evidence.

| Dimension | Score | Grade | Weight | Verdict |
| :-- | :-- | :-- | :-- | :-- |
| Security | 78 | C+ | 20% | No critical/remote vulns; shell exec is by-design for a local tool, but the kill-switch is half-enforced and verifier output is persisted unredacted. |
| Architecture and Design | 62 | D | 15% | Two hand-duplicated runtimes; drift guards check copies/counts not behavior; a confirmed correctness divergence already exists. |
| Code Quality and Maintainability | 74 | C | 15% | Consistent, well-named, low dead code, but two ~10k-line god-modules and pervasive cross-runtime duplication. |
| Testing and Verification | 84 | B | 15% | 227 tests, ~2,300 real assertions, no theater, deterministic, gate edges covered; the one gap (no cross-runtime conformance test) is exactly what let the divergences through. |
| Error Handling and Resilience | 68 | D+ | 10% | Solid single-process happy path and corruption quarantine, but no file locking, a compaction TOCTOU, and an unbounded fanout output buffer. |
| Performance and Efficiency | 82 | B | 8% | Fine for a CLI; the evidence ledger is re-read in full on every gate/report and grows unbounded between compactions. |
| Dependencies and Supply Chain | 90 | A- | 7% | Zero-dep Python; two pinned, current Node deps with lockfile + SRI + Dependabot. Only gap: no `npm audit` gate in CI. |
| Documentation and Drift | 86 | B | 5% | Verified claims hold (40-tool count, protocol-hash check, variant sync); two small drift items. |
| Observability and Operability | 84 | B | 5% | Clean exit-code discipline, `[OK]`/`[FAIL]`/`[WARN]`, quarantine warnings, log compaction. Minor MCP `isError` nit. |
| **Overall (weighted)** | **77** | **C** | 100% | Strong engineering taxed by a dual-runtime maintenance model that has begun to bite. |

Weighting: defaults, unchanged. No Critical findings, so no dimension or overall cap is triggered.

## What to fix first

1. [x] ~~`[ARC-001]` Cross-runtime timestamp format mismatch silently breaks the strict step gate~~ - Completed in v3.6.5.
2. [~] `[ARC-003]` Drift guards verify copies and counts, not behavior - Partially addressed in v3.6.6 with classification and verify record-shape conformance; gate-decision conformance remains open.
3. [x] ~~`[SEC-001]` `outcome check` ignores `MYTHIFY_DISABLE_RUN`~~ - Completed in v3.6.5.
4. [x] ~~`[ERR-004]` Fanout async worker output is accumulated unbounded (no `maxBuffer`)~~ - Completed in v3.6.7.
5. [ ] `[ARC-002]` Core logic is hand-duplicated across both runtimes - Open.
6. [ ] `[ERR-001]` No file locking; `logs compact` TOCTOU drops concurrent appends - Open.
7. [~] `[SEC-002]` Verifier stdout/stderr tails persisted unredacted; `init` writes no `.gitignore` - Partially addressed in v3.6.8 by adding default `.mythify/` `.gitignore` coverage; verifier-output redaction remains open.

## Remediation status

Last updated: 2026-06-15.

- [x] ~~[ARC-001] Cross-runtime timestamp format mismatch silently breaks the strict step gate~~ - Completed in v3.6.5.
- [ ] [ARC-002] Core business logic is hand-duplicated across both runtimes - Open.
- [~] [ARC-003] Drift guards verify copies and counts, not behavior or record shapes - Partially addressed in v3.6.6; gate-decision conformance remains open.
- [ ] [ARC-004] Additional confirmed behavioral divergences between the two runtimes - Open.
- [x] ~~[SEC-001] `outcome check` ignores `MYTHIFY_DISABLE_RUN`~~ - Completed in v3.6.5.
- [~] [SEC-002] Verifier output tails are persisted unredacted and `init` writes no `.gitignore` - Partially addressed in v3.6.8; output redaction remains open.
- [ ] [ERR-001] No file locking; `logs compact` read-then-rewrite drops concurrent appends - Open.
- [x] ~~[ERR-004] Fanout async worker output is accumulated unbounded (no `maxBuffer`)~~ - Completed in v3.6.7.
- [~] [TEST-001] No cross-runtime behavioral conformance test - Partially addressed in v3.6.6; gate-decision conformance remains open.
- [ ] [QUAL-001] Two ~10k-line god-modules - Open.
- [x] ~~[SEC-003] Raw, un-slugified name is used as a filename before `slugify`~~ - Completed in v3.6.15.
- [ ] [SEC-004] Fanout `context_paths` are not sandboxed to the project root - Open, needs re-verification before changing behavior.
- [ ] [SEC-005] `host_cli_run` accepts an arbitrary `bin` executable - Open, needs re-verification before changing behavior.
- [x] ~~[SEC-006] `outcome` `allowed_paths` is advisory-only despite a sandboxing-implying name~~ - Completed in v3.6.16.
- [x] ~~[ERR-002] `append_jsonl` is non-atomic for large records~~ - Completed in the post-v3.6.16 resilience slice by surfacing malformed JSONL records.
- [ ] [ERR-003] No `fsync` before atomic rename - Open, needs re-verification before changing behavior.
- [ ] [ERR-005] Fanout timeout kills only the direct child; shell-engine grandchildren can orphan - Open, needs re-verification before changing behavior.
- [ ] [PERF-001] The evidence ledger is re-read in full on every gate check and report, and grows unbounded - Open.
- [x] ~~[DEP-001] No `npm audit` gate in CI~~ - Completed in v3.6.9.
- [x] ~~[TEST-002] Read-only view commands are lightly tested~~ - Completed in v3.6.14.
- [x] ~~[DOC-001] `roadmap.md` references a stale release~~ - Completed in v3.6.12.
- [x] ~~[DOC-002] README "shared contract core" claim overstates current reality~~ - Completed in v3.6.13.
- [x] ~~[QUAL-002] Version-surface asymmetry~~ - Completed in v3.6.11.
- [x] ~~[OBS-001] MCP tool errors do not set the `isError` flag~~ - Completed in v3.6.10.

## Strengths (preserve these)

- **A real, well-tested evidence gate (single-runtime).** The strict gate's logic is sound: attested claims cannot satisfy it (`scripts/mythify.py:8329`), step-binding prevents one step borrowing another's verification (`verification_record_matches_step`, `mythify.py:1386-1390`), and the hardest case is explicitly tested (`tests/test_mythify.py:2081`, "bound verification for one step cannot complete another"). Do not weaken this while fixing the cross-runtime format bug (ARC-001).
- **Test suite quality, not just quantity.** 128 Python + 99 MCP tests, ~2,300 assertions, zero no-assertion/`assertTrue(True)` theater, no mocking (real subprocesses, real loopback HTTP on `127.0.0.1:0`, real exit codes). Deterministic (1s-timeout-vs-5s-sleep style). Preserve this style for any new code.
- **Genuine integrity mechanisms.** `read_json` quarantines corrupt files to `*.corrupt-<stamp>` and returns a default rather than crashing (`mythify.py:817-839`); writes are atomic via mkstemp + `os.replace` (`mythify.py:793-814`).
- **The protocol-hash check is real, not decorative.** `protocol check` compares an embedded `sha256` against both the live `PROTOCOL.md` and each variant's header hash, and drift is CI-guarded (`mythify.py:34`, tests at `test_mythify.py:137-205`).
- **Supply chain.** Zero-dependency Python CLI (stdlib only); two pinned, current Node deps with lockfile, SRI, and Dependabot.
- **Documentation accuracy.** Spot-checked claims hold: the "40 MCP tools" count is exact (37 + 3 fanout) and smoke-test-guarded; the three protocol variants are in sync with their generator; the CLI quick-reference matches the implemented commands 1:1.

## Systemic patterns (root causes)

### SP-1: Two hand-maintained native runtimes, parity guarded only mechanically
The Python CLI and Node MCP server are line-for-line ports over one `.mythify/` state directory, but only four thin manifests are genuinely shared; all behavior (verify record shape, the gate, classification scoring, outcome loops, persistence helpers) is reimplemented twice. The drift guards assert byte-identical JSON and tool/command counts, not behavioral equivalence, so the divergence-prone logic is outside their coverage.
- Members: ARC-001 (the live bug), ARC-002 (the duplication), ARC-003 (guards miss behavior), ARC-004 (more divergences), TEST-001 (no conformance test), DOC-002 (claim overstates reality), QUAL-002 (version surface asymmetry).
- Root fix: add a cross-runtime behavioral conformance harness (identical input through both runtimes must yield identical classification, record shape, and gate decision), and either generate both adapters' shared logic from one source or normalize the shared primitives (timestamps, record schema) into checked contracts. Fix ARC-001's timestamp format as the first concrete instance.

### SP-2: No concurrency safety despite a parallel-worker design
State IO assumes a single writer, but the protocol explicitly spawns fanout workers and sub-agents that share the same state directory. Read-modify-write and log compaction race with no locking.
- Members: ERR-001 (no locking + compaction TOCTOU), ERR-002 (non-atomic append), ERR-003 (no fsync), ERR-004 (unbounded fanout output buffer), PERF-001 (full-ledger re-read).
- Root fix: add advisory file locking (`fcntl.flock` / a lockfile) around read-modify-write and compaction of the shared JSON/JSONL stores; cap worker output buffers; consider an index or tail-read for the growing ledger.

### SP-3: Security controls declared but not fully enforced (paper controls)
Several controls exist in name or partial form but do not fully hold.
- Members: SEC-001 (`MYTHIFY_DISABLE_RUN` skips `outcome check`), SEC-003 (raw name bypasses the `slugify` sanitizer before lookup), SEC-006 (`allowed_paths` is advisory-only despite the sandboxing-implying name), SEC-002 (no auto-`.gitignore` for a dir that stores captured output).
- Root fix: enforce the kill-switch on every execution path, slugify before any name-to-path lookup, and either enforce `allowed_paths` or rename it to signal it is advisory.

## Findings

### [ARC-001] Cross-runtime timestamp format mismatch silently breaks the strict step gate
- Severity: High | Confidence: Confirmed | Effort: M | Dimension: Architecture and Design
- Location: `mcp-server/src/index.js:209` (`isoNow`), `scripts/mythify.py:680-682` (`now_iso`); gate comparisons `mcp-server/src/index.js:8476-8482` and `scripts/mythify.py:8326-8333`.
- Evidence: `isoNow()` returns `new Date().toISOString()` -> millisecond precision with a `Z` suffix (`2026-06-15T18:26:24.862Z`). `now_iso()` returns `isoformat(timespec="seconds")` -> second precision with a `+00:00` suffix (`2026-06-15T18:26:24+00:00`). Both gates decide completion via the lexicographic string comparison `record.timestamp >= lowerBound`, where `lowerBound = step.updated_at || plan.created`. Timestamps are stored raw and never normalized on read. Because `+` (0x2B) sorts before `.` (0x2E), a Python-recorded verify in the same wall-clock second as a Node-written `step.updated_at` compares as earlier (`"...24+00:00" < "...24.000Z"`), so the passing verify is rejected.
- Impact: In the interop mode the project explicitly supports and tests (CLI and MCP over one `.mythify/`), a legitimately passing verification can be silently refused, blocking step completion with a misleading "no passing verify run was recorded since this step started" message. It is intermittent and writer-order-dependent, so it is hard to diagnose. It fails safe (false negative, never a false positive), which is why it is High and not Critical, but it directly undermines the product's flagship guarantee in a supported configuration.
- Recommendation: Normalize timestamps to one canonical format on write in both runtimes (e.g. both emit second-precision UTC with the same suffix), or compare parsed instants rather than strings in both gates. Apply the same fix to the report event sort key and outcome iteration ordering, which use the same comparison.
- Verify the fix: add a test that writes `step.updated_at` in one runtime's format and a verified record in the other's, within the same second, and asserts the step completes; assert `isoNow()` and `now_iso()` produce byte-identical formats for a fixed instant.
- Related: SP-1; ARC-003, ARC-004, TEST-001.

### [ARC-002] Core business logic is hand-duplicated across both runtimes
- Severity: High | Confidence: Confirmed | Effort: L | Dimension: Architecture and Design
- Location: `scripts/mythify.py` and `mcp-server/src/index.js` throughout; e.g. classification `mythify.py:2658-2757` vs `index.js:3951-4069`; verify record `mythify.py:8700-8754` vs `index.js:8869-8953`; persistence helpers `now_iso/write_json_atomic/append_jsonl/read_jsonl` (`mythify.py:680,793,842,854`) vs (`index.js:209,297,1680,1685`).
- Evidence: Only four manifests are genuinely shared at runtime (`classification-rules.json`, `workflow-router.json`, `operation-registry.json`, `surface-manifest.json`). Everything built on them, the classification scoring (the 20-term risk lists, the `<=12` words trivial threshold, ceremony tiers), the verify record schema, the strict gate, outcome loops, plan/step, memory, lessons, the report cursor, and all persistence helpers, is reimplemented in both languages. The JS `readJsonl` even carries the comment "matching the CLI's tolerant reader" (`index.js:1701`), an explicit acknowledgment that parity is a manual obligation.
- Impact: Every behavioral change must be written, reviewed, and tested twice with nothing forcing agreement. Roughly half of ~19k lines of logic is a translation of the other half; divergences (ARC-001, ARC-004) are the inevitable result.
- Recommendation: Move shared behavior toward a single source. Options in increasing order: (a) extend the manifest-driven approach so the risk/ceremony/threshold facts are data both adapters read (today they are hardcoded twice); (b) extract a canonical record-schema and gate-decision contract; (c) longer term, generate one adapter or share a behavior core. At minimum, treat the shared primitives (timestamp format, record shape) as checked contracts.
- Verify the fix: a single edit to a shared classification or record fact changes both runtimes' behavior without editing two files; the conformance harness (ARC-003) stays green.
- Related: SP-1; ARC-001, ARC-003, DOC-002.

### [ARC-003] Drift guards verify copies and counts, not behavior or record shapes
- Severity: High | Confidence: Confirmed | Effort: M | Dimension: Architecture and Design
- Location: `scripts/check_surface_manifest.mjs`, `scripts/check_classification_rules_manifest.mjs`, interop test `tests/test_interop.py:204`.
- Evidence: The guards assert (a) the two JSON manifest copies are byte-identical, (b) tool/command counts match `registerTool` scrapes and `--help`, and (c) docs mention each name. The interop test runs both runtimes against one temp dir but only asserts substring round-trips (e.g. `assertIn("Interop goal", status_text)`). Nothing asserts that `classify_task_text`/`classifyTaskText` return the same classification for the same prompt, that the verify record shapes match field-for-field, or that the gate makes the same decision.
- Impact: The guards give false confidence: the cheap, mechanical part of parity is policed while the expensive, bug-prone part (behavioral equivalence) is left to manual discipline. ARC-001 passed every existing guard.
- Recommendation: Add a cross-runtime conformance harness: feed a fixed corpus of prompts/commands through both runtimes and assert identical classification output, identical record JSON shape, and identical gate decisions. Run it in CI alongside the existing manifest checks.
- Verify the fix: introduce a deliberate one-line divergence (e.g. change one risk term in one runtime) and confirm the harness fails.
- Related: SP-1; ARC-001, ARC-002, TEST-001.

### [ARC-004] Additional confirmed behavioral divergences between the two runtimes
- Severity: Medium | Confidence: Confirmed | Effort: M | Dimension: Architecture and Design
- Location: verify-failure path `mcp-server/src/index.js:8914-8923` vs `scripts/mythify.py:8707-8722`; buffer caps `index.js:8899` (16 MiB) vs `index.js:5295/6130/6448` (1 MiB) vs Python uncapped.
- Evidence: Node records a third outcome for a spawn-failed or signal-killed verifier (`exit_code:-1, verified:false` with a reason note); Python only catches `TimeoutExpired`, so a signal-killed verifier follows a different path and records different (or no) evidence. Node caps captured output at 16 MiB for `verify_run` but 1 MiB elsewhere; Python's `subprocess.run` has no cap, so large-output commands truncate differently across and within runtimes.
- Impact: The same verifier command can produce different recorded evidence depending on which adapter ran it, weakening the "executed verification is trustworthy" promise.
- Recommendation: Unify the failure-classification logic and the output-cap constant across both runtimes (a shared contract per ARC-002).
- Verify the fix: a verifier killed by signal records the same `verified:false` evidence in both runtimes; the output cap is one shared constant.
- Related: SP-1; ARC-002.

### [SEC-001] `outcome check` ignores the `MYTHIFY_DISABLE_RUN` execution kill-switch
- Severity: Medium | Confidence: Confirmed | Effort: S | Dimension: Security
- Location: `scripts/mythify.py:8701` (guard present in `cmd_verify_run`) vs `cmd_outcome_check` running `run_shell_capture(goal["verify_command"], ...)` and the metric command (the two `run_shell_capture` calls inside `cmd_outcome_check`, ~`mythify.py:8547` and `:8552`); `run_shell_capture` (`mythify.py:2416-2426`) runs `subprocess.run(..., shell=True)` unconditionally. `grep` shows `MYTHIFY_DISABLE_RUN` only at `mythify.py:8701`.
- Evidence: `verify run` refuses to execute when `MYTHIFY_DISABLE_RUN=1`, but `outcome check` spawns a shell and runs the stored verifier and metric commands with no such check.
- Impact: An environment that sets the kill-switch to block all shell execution (sandbox, CI dry-run, restricted host) is only half-protected; `outcome check` still executes. A declared security control is incompletely enforced.
- Recommendation: Check `MYTHIFY_DISABLE_RUN` at the top of `cmd_outcome_check` (and ideally inside `run_shell_capture`, the shared chokepoint) and return the same disabled result/exit code as `verify run`.
- Verify the fix: with `MYTHIFY_DISABLE_RUN=1`, `outcome check` records no execution and returns the disabled exit code; add a test mirroring the `verify run` disabled-path test.
- Related: SP-3.

### [SEC-002] Verifier output tails are persisted unredacted and `init` writes no `.gitignore`
- Severity: Medium | Confidence: Likely | Effort: M | Dimension: Security
- Location: `scripts/mythify.py:118` (`TAIL_CHARS = 4000`), record writes in `cmd_verify_run` (`mythify.py:8724-8742`) and `cmd_outcome_check` (`mythify.py:8615-8623`); `cmd_init` (`mythify.py:4114-4135`) creates the layout but writes no `.gitignore`.
- Evidence: Up to 4,000 chars of verifier stdout and stderr are stored verbatim into `verifications.jsonl` and `outcomes/<slug>/iterations.jsonl`. The verifier inherits the full parent environment (no explicit `env=`), so a failing test that prints an auth header, a token in a stack trace, or `env`-style debug output is written to disk. There is no redaction anywhere (no `redact`/`mask`/`scrub`). `init` does not add `.mythify/` to `.gitignore`, so a project initialized elsewhere could commit these tails.
- Impact: Secrets-at-rest in `.mythify/` plus a commit-leak path in repos that do not pre-ignore the directory.
- Recommendation: Have `init` write/append a `.gitignore` entry for `.mythify/` (or at least the `*.jsonl` evidence files); optionally cap/redact obvious secret patterns in stored tails, or make tail length configurable down to 0.
- Verify the fix: after `init`, `.mythify/` is gitignored; a verifier that prints a known token does not leave it committable.
- Related: SP-3.

### [ERR-001] No file locking; `logs compact` read-then-rewrite drops concurrent appends
- Severity: Medium | Confidence: Confirmed (race) / Likely (occurs in practice) | Effort: M | Dimension: Error Handling and Resilience
- Location: `compact_jsonl_log` (`scripts/mythify.py:8845-8883`, reads at `:8860-8861`, rewrites via `write_jsonl_atomic` at `:8880`); no `flock`/lockfile anywhere in the file. Same last-writer-wins pattern on `memory.json`, `outcomes/<slug>/goal.json`, plan files, and the active pointer.
- Evidence: Per-file writes are atomic (`os.replace`), but there is no cross-operation lock. `compact_jsonl_log` reads the whole log, then later rewrites it; any `append_jsonl` landing in between is silently overwritten. The protocol spawns fanout workers and sub-agents that share the state dir, making concurrent writers plausible.
- Impact: Silent loss of verification/reflection/memory records under concurrency, directly damaging the evidence ledger the product is built on.
- Recommendation: Add advisory locking (`fcntl.flock` or a lockfile) around read-modify-write and compaction of shared stores; for append-heavy logs, hold the lock only for the compaction swap.
- Verify the fix: a test that appends to `verifications.jsonl` concurrently with a compaction does not lose the appended record.
- Related: SP-2; ERR-002, ERR-003.

### [ERR-004] Fanout async worker output is accumulated unbounded (no `maxBuffer`)
- Severity: Medium | Confidence: Confirmed | Effort: S | Dimension: Error Handling and Resilience
- Location: `mcp-server/src/fanout.js:1024-1083` (`runSubprocess`, `stdout += chunk` / `stderr += chunk` at `:1067-1072`).
- Evidence: The async `spawn` path that runs all fanout workers accumulates output into strings with no size cap; the kill timer fires only on the per-worker timeout, never on output volume. Every `spawnSync` site elsewhere sets `maxBuffer`, but this async path does not. Truncation happens only later, on read-back.
- Impact: A worker that streams large output within its timeout window grows these buffers unbounded and can OOM the MCP server process.
- Recommendation: Track accumulated byte length and kill the child once a ceiling is exceeded (mirror `maxBuffer`), recording a truncation/over-limit reason.
- Verify the fix: a worker that emits more than the ceiling is killed and recorded as over-limit rather than growing memory.
- Related: SP-2.

### [TEST-001] No cross-runtime behavioral conformance test
- Severity: Medium | Confidence: Confirmed | Effort: M | Dimension: Testing and Verification
- Location: `tests/test_interop.py:204` (substring round-trip only).
- Evidence: The interop test confirms both runtimes can read each other's state and surface expected substrings, but never asserts identical behavior (classification output, record shape, gate decision) for identical inputs. This is the test that would have caught ARC-001 and ARC-004.
- Impact: The most consequential class of regression (silent semantic divergence between the two implementations) has no coverage.
- Recommendation: Implement the conformance harness described in ARC-003; this finding and ARC-003 share a fix.
- Verify the fix: the harness exists, runs in CI, and fails on an injected divergence.
- Related: SP-1; ARC-001, ARC-003, ARC-004.

### [QUAL-001] Two ~10k-line god-modules
- Severity: Medium | Confidence: Confirmed | Effort: L | Dimension: Code Quality and Maintainability
- Location: `scripts/mythify.py` (single 10,283-line module; `build_parser` ~`:8966-10269` is one ~1,303-line function; 61 `cmd_*` handlers + ~358 functions in one file). `mcp-server/src/index.js` (single 9,051-line module; 37 inline `registerTool` blocks across `:7162-8985`).
- Evidence: Both runtimes are single files with very large dispatch/registration functions and no package split (only `fanout.js`/registry shims are extracted on the Node side).
- Impact: High navigation cost, hard to test functions in isolation, wide blast radius for changes; compounds ARC-002 because the duplicated logic is also unmodularized.
- Recommendation: Split each runtime into modules by concern (state IO, verify/gate, classification/routing, outcome, memory/lessons, command/tool registration). Modularizing in parallel makes a future shared-core extraction tractable.
- Verify the fix: no single source file exceeds a chosen ceiling (e.g. 1,500 lines); tests import individual modules directly.
- Related: SP-1; ARC-002.

### [SEC-003] Raw, un-slugified name is used as a filename before `slugify` (constrained path traversal)
- Severity: Low | Confidence: Confirmed | Effort: S | Dimension: Security
- Location: `find_plan_slug` (`scripts/mythify.py:1323-1330`), `find_research_slug` (`:1455-1462`), `find_campaign_slug` (~`:1735`), `find_outcome_slug` (`:2371-2378`); Node mirror `resolvePlan` (`mcp-server/src/index.js:1786-1796`).
- Evidence: The lookup tries `plan_path(state, name)` with the raw `name` before falling back to `slugify(name)`. A `--plan "../../x"` builds `plans/../../x.json`; if it resolves to an existing file, the raw string is returned and flows into read and (on edit) `write_json_atomic`, reading or overwriting a `.json` outside `.mythify/`. Constrained: `.json` suffix always appended, target must already exist, and a write requires valid plan JSON. Present identically in both runtimes (shared design, not a divergence).
- Impact: An operator-supplied name with `../` can read or clobber an existing `.json` outside the state dir. Low for a single-operator local tool, but it bypasses the `slugify` sanitizer that is clearly the intended guard.
- Recommendation: Slugify before any name-to-path lookup, or verify the resolved path stays within the plans/research/outcome directory.
- Verify the fix: a `--plan "../../x"` is treated as the slug `x` (or rejected), never resolving outside the state dir.
- Related: SP-3.

### [SEC-004] Fanout `context_paths` are not sandboxed to the project root
- Severity: Low | Confidence: Suspected | Effort: S | Dimension: Security
- Location: `mcp-server/src/fanout.js:991-1000` (resolve), `:1011` (inlined into worker prompt); schema note `:2232`.
- Evidence: Each `context_paths` entry is resolved as `path.isAbsolute(p) ? p : path.join(projectRoot, p)`; absolute paths and `../` are read as-is and their content is inlined into a worker prompt sent to an external engine. The tool schema states absolute paths are allowed, so this is partly intended, and the caller is normally the trusted orchestrating agent.
- Impact: A `context_paths` value like `../../.ssh/id_rsa` causes the server to read that file and ship it to a third-party model. No containment or opt-in for out-of-project reads.
- Recommendation: Add an opt-in flag or allowlist for out-of-project reads; otherwise resolve within `projectRoot` and reject escapes. Re-verify intended behavior against the documented contract before changing.
- Verify the fix: an out-of-project `context_paths` entry is rejected unless an explicit opt-in is set.
- Related: SP-3.

### [SEC-005] `host_cli_run` accepts an arbitrary `bin` executable
- Severity: Low | Confidence: Suspected | Effort: S | Dimension: Security
- Location: `mcp-server/src/index.js:7539-7542`, `:7569-7581`; `resolveHostCliBinary` (`:6094-6098`); exec `spawnSync(resolved.bin, args, { shell: false })` (`:6443`).
- Evidence: `bin` is a free-form MCP input validated only by `isExecutableFile`, with no allowlist tying it to the known host CLIs (Kimi/OpenCode/Antigravity). A caller can run any on-disk executable with the prompt as argv. `shell: false` prevents metacharacter injection, and the caller is the trusted agent.
- Impact: Broadest "arbitrary executable" surface among the non-shell tools; low risk given the trust model but unconstrained.
- Recommendation: Allowlist `bin` to the supported host CLIs, or require an explicit opt-in for arbitrary executables. Re-verify the intended capability before restricting.
- Verify the fix: a `bin` outside the allowlist is rejected unless opt-in is set.
- Related: SP-3.

### [SEC-006] `outcome` `allowed_paths` is advisory-only despite a sandboxing-implying name
- Severity: Low | Confidence: Confirmed | Effort: S | Dimension: Security
- Location: `mcp-server/src/index.js:2062-2063`, `:8665`; `scripts/mythify.py:2471`, `:5992`.
- Evidence: `allowed_paths` is stored and displayed but never enforced as a write/read boundary, in both runtimes.
- Impact: A reader may assume the outcome loop is path-sandboxed when it is not.
- Recommendation: Either enforce `allowed_paths` or rename/document it as advisory guidance only.
- Verify the fix: behavior matches the name, or docs explicitly state it is advisory.
- Related: SP-3.

### [ERR-002] `append_jsonl` is non-atomic for large records
- Severity: Low | Confidence: Confirmed | Effort: S | Dimension: Error Handling and Resilience
- Location: `scripts/mythify.py:842-846`; tolerant reader `:854-869`.
- Evidence: A single `write()` of `json.dumps(record) + "\n"`; records carry up to two 4,000-char tails, easily exceeding `PIPE_BUF`/page-atomicity. A crash or disk-full mid-write can leave a torn line. `read_jsonl` skips unparseable lines, so the torn (most recent) record is silently dropped rather than crashing.
- Impact: Possible silent loss of the just-written verification/reflection record on crash, which could make a just-passed gate look unsatisfied.
- Recommendation: Write via a temp-append + atomic strategy, or accept the risk and document it; at minimum log when `read_jsonl` skips a malformed trailing line.
- Verify the fix: a truncated trailing line is detected and surfaced, not silently dropped.
- Related: SP-2; ERR-001.

### [ERR-003] No `fsync` before atomic rename
- Severity: Low | Confidence: Suspected | Effort: S | Dimension: Error Handling and Resilience
- Location: `_write_text_atomic` (`scripts/mythify.py:793-808`).
- Evidence: Writes to a temp file then `os.replace` without `os.fsync` on the fd. `os.replace` is atomic for visibility, but a crash before metadata flush could leave a zero-length file on some filesystems.
- Impact: Rare data-loss window on hard crash; acceptable for a local dev tool.
- Recommendation: `fsync` the temp fd before `os.replace` if durability matters; otherwise document the trade-off.
- Verify the fix: the write path fsyncs before rename.
- Related: SP-2; ERR-001.

### [ERR-005] Fanout timeout kills only the direct child; shell-engine grandchildren can orphan
- Severity: Low | Confidence: Suspected | Effort: S | Dimension: Error Handling and Resilience
- Location: `mcp-server/src/fanout.js:1057-1064` (kills `child.kill("SIGKILL")` only); shell-engine spawn `:1030`.
- Evidence: For the `MYTHIFY_FANOUT_COMMAND` engine the child is a shell (not `detached` into its own process group); SIGKILL to the shell PID does not signal its grandchildren. Binary engines spawn the worker directly and are narrower.
- Impact: Leaked worker processes after timeouts for the shell engine.
- Recommendation: Spawn the shell `detached` and kill the process group on timeout, or use the binary engines.
- Verify the fix: a timed-out shell-engine worker leaves no orphaned grandchild process.
- Related: SP-2.

### [PERF-001] The evidence ledger is re-read in full on every gate check and report, and grows unbounded
- Severity: Low | Confidence: Confirmed | Effort: M | Dimension: Performance and Efficiency
- Location: gate read `scripts/mythify.py:8327` (`read_jsonl(state / "verifications.jsonl")`), report assembly reads all records; `verifications.jsonl` is already 3.2 MB / ~1,533 records in this repo.
- Evidence: `cmd_step`, the report, and verify-context all read the entire `verifications.jsonl` each invocation. The file grows without bound between manual `logs compact` runs (and compaction has the ERR-001 TOCTOU risk).
- Impact: O(n) work per operation that grows with project age; currently fast but unbounded. For a tool whose value is a durable ledger, the ledger's read cost is on every hot path.
- Recommendation: Read the tail or maintain a small index for the gate's "since lower_bound" query; encourage/automate compaction.
- Verify the fix: gate-check time stays roughly constant as the ledger grows.
- Related: SP-2.

### [DEP-001] No `npm audit` gate in CI
- Severity: Low | Confidence: Likely | Effort: S | Dimension: Dependencies and Supply Chain
- Location: `.github/workflows/ci.yml` (Node job); `.github/dependabot.yml` (weekly).
- Evidence: Dependabot opens PRs weekly, but CI never runs `npm audit`, so a freshly disclosed transitive CVE would not fail a build between Dependabot runs. No obviously vulnerable pinned versions exist today (reasoned from versions; mark Suspected).
- Impact: A window where a known-vulnerable transitive dependency passes CI.
- Recommendation: Add `npm audit --audit-level=high` to the Node CI job; run `npm audit` locally to confirm the current tree is clean.
- Verify the fix: CI fails on an injected high-severity advisory; `npm audit` reports clean now.
- Related: none.

### [TEST-002] Read-only view commands are lightly tested
- Severity: Low | Confidence: Likely | Effort: S | Dimension: Testing and Verification
- Location: `cmd_dashboard` (`scripts/mythify.py:4399`), `cmd_history` (`:4550`), `cmd_readiness` (`:6443`), `cmd_timeline` (`:6647`); also `background`, `progress`.
- Evidence: These commands have ~3-4 test references each vs 30-77 for core commands. They are read-only, so blast radius is small, but formatting/regression coverage is thin.
- Impact: Formatting or aggregation regressions in the read-only views could ship unnoticed.
- Recommendation: Add output-shape assertions for each read-only view against a seeded state dir.
- Verify the fix: each view command has a test asserting its key output sections.
- Related: none.

### [DOC-001] `roadmap.md` references a stale release
- Severity: Low | Confidence: Confirmed | Effort: S | Dimension: Documentation and Drift
- Location: `roadmap.md` ("Current intended release: v3.0.0"); repo is at v3.6.x (`mcp-server/package.json`, git tags).
- Evidence: The roadmap names v3.0.0 as the current intended release while the project has shipped through v3.6.x.
- Impact: Misleads a reader about the project's current state.
- Recommendation: Update or date-stamp `roadmap.md`, or generate the version line from a single source.
- Verify the fix: `roadmap.md` matches the current version.
- Related: SP-1 (version surface).

### [DOC-002] README "shared contract core" claim overstates current reality
- Severity: Low | Confidence: Confirmed | Effort: S | Dimension: Documentation and Drift
- Location: `README.md` (the "moving toward one shared contract core, native adapters" passage).
- Evidence: The wording is direction-honest ("moving toward", "one surface at a time"), but only the four manifests are shared; the verification engine, classification scoring, gate, outcome loop, and persistence are duplicated (ARC-002). A reader could overestimate how much is shared.
- Impact: Architecture claim outruns implementation.
- Recommendation: Tighten the wording to state precisely what is shared today, or treat the gap as the backlog it implies and track it.
- Verify the fix: the README's shared-surface claim matches what is actually read from manifests at runtime.
- Related: SP-1; ARC-002.

### [QUAL-002] Version-surface asymmetry
- Severity: Low | Confidence: Confirmed | Effort: S | Dimension: Code Quality and Maintainability
- Location: `mcp-server/src/index.js:56` (`VERSION` hardcoded, duplicated from `mcp-server/package.json`); no `--version` flag in `scripts/mythify.py`.
- Evidence: The MCP server hardcodes its version (will drift from `package.json` unless updated together); the Python CLI exposes no version at all.
- Impact: Minor: version drift risk and no CLI way to report the running version.
- Recommendation: Read the version from `package.json` on the Node side; add a `--version` to the CLI sourced from a single constant.
- Verify the fix: both surfaces report the same version from a single source.
- Related: SP-1.

### [OBS-001] MCP tool errors do not set the `isError` flag
- Severity: Low | Confidence: Confirmed | Effort: S | Dimension: Observability and Operability
- Location: `mcp-server/src/index.js` tool handlers (`guarded`, ~`:7140-7149`); failures returned as `[FAIL]`-prefixed text, 0 uses of `isError`.
- Evidence: Failures are surfaced as inline `[FAIL]` text rather than the MCP `isError: true` field (intentional, matches CLI convention and is smoke-tested).
- Impact: Clients that branch on `isError` cannot distinguish failures without string-matching `[FAIL]`.
- Recommendation: Also set `isError: true` on failure results while keeping the `[FAIL]` text, so both programmatic and text clients work.
- Verify the fix: a failing tool call returns `isError: true` and the `[FAIL]` text.
- Related: none.

## Dimension notes

- **Security (78):** No critical or remotely exploitable issues; shell execution is the tool's stated purpose and inputs are operator-supplied, with good controls (loopback allowlist for local providers `index.js:5658-5661`, env-var key names not raw secrets, recursive-fanout fork-bomb guards `fanout.js:265-268`). Score held below B by SEC-001 (half-enforced kill-switch) and SEC-002 (unredacted secret persistence + no auto-gitignore), with several Low sandbox/traversal gaps (SEC-003/004/005/006).
- **Architecture (62):** Three High findings (ARC-001/002/003) plus ARC-004 are all facets of SP-1. The dual-runtime model is deliberate and partly mitigated, but it has produced a live correctness bug and the guards do not cover the bug class. This is the dimension that most needs investment.
- **Code Quality (74):** Naming, consistency, and dead-code hygiene are good; the drag is the two god-modules (QUAL-001) and the cross-runtime duplication.
- **Testing (84):** A genuine strength in depth and honesty; the single structural gap (TEST-001, no conformance test) is precisely what allowed ARC-001/004 to ship.
- **Error Handling (68):** Single-process behavior and corruption quarantine are solid, but SP-2 (no locking, TOCTOU, non-atomic append, unbounded fanout buffer) is a real systemic gap given the parallel-worker design.
- **Performance (82):** Adequate for a CLI; PERF-001 (full-ledger re-read, unbounded growth) is the only structural note.
- **Dependencies (90):** Best dimension; only DEP-001 (no CI audit gate) keeps it from A.
- **Documentation (86):** Verified accurate on the claims that matter; DOC-001/002 are small drift items.
- **Observability (84):** Good for the tool class; OBS-001 is a minor client-ergonomics nit.

## Remediation plan

- **Quick wins** (highest value per effort; act now): completed SEC-001, SEC-003, SEC-006, ERR-002, and ERR-004.
- **Plan now** (High/Critical and scheduled Medium work, suggested order): finish ARC-003 (+TEST-001 gate-decision conformance) -> ARC-004 -> ERR-001 -> SEC-002 redaction -> QUAL-001 -> ARC-002 (long-horizon dedup/generation program).
- **Verify first** (Suspected; re-check the cited code before acting): SEC-004, SEC-005, ERR-003, ERR-005.
- **Backlog** (Low; batch): PERF-001.

## Scope and limitations

- The load-bearing code was read exhaustively; the remaining bulk of the two ~10k-line modules was sampled (all command/tool registrations enumerated, longest functions inspected). If a divergence exists in a sampled-but-unread region, it would not appear here, though SP-1 predicts more will.
- Performance findings are reasoned from code, not a profiler; PERF-001 is grounded in the observed 3.2 MB ledger but real latency was not measured.
- Dependency CVE assessment is reasoned from pinned versions against general knowledge; `npm audit` was not run in the audit sandbox. Confirm with `npm audit` (DEP-001).
- Path-traversal findings (SEC-003) were read-traced, not executed (read-only audit).
- Assumptions that would change conclusions: if Mythify is only ever used single-runtime (never CLI+MCP on one dir), ARC-001's severity drops to Medium; if the state dir is guaranteed single-writer, SP-2's findings drop in severity. Both assumptions contradict the project's stated interop and fanout features, so they are not made here.

## How to use this report (for the acting agent)

1. Triage by severity and confidence. Confirmed Critical and High are safe to act on now, in the order in "What to fix first". Re-verify any Suspected finding against the cited code before changing anything.
2. Fix root causes first; prefer systemic patterns (SP-1, SP-2, SP-3) over individual leaves.
3. Preserve the strengths; do not refactor them away while fixing other issues (especially the single-runtime gate correctness and the test-suite style).
4. Confirm the stated assumption on Likely findings before acting.
5. One finding, one change, verified: after each fix run its "Verify the fix" step; keep changes atomic and traceable to the finding ID.
6. Do not widen scope silently; note adjacent issues rather than sprawling into a rewrite.
7. Re-run the audit to measure progress; confirm findings are resolved, not relocated, and watch for regressions in the strengths.
