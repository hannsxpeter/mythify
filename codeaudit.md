# Mythify Code Audit

Original read-only audit date: 2026-06-15. This report is self-contained: every
finding cites exact locations, carries its own context, and states how to verify
the fix. Remediation status has since been updated in place through v3.6.41.

## Snapshot

- Project: Mythify, an evidence protocol for AI coding agents (CLI + MCP server + protocol manifests).
- Original audited state: branch `main`, commit `4f0177b` ("fix: harden strict step evidence gate").
- Languages: Python 3.9+ (CLI), JavaScript / Node 18+ (MCP server). JSON manifests; Markdown protocol/docs.
- Size: `scripts/mythify.py` 5,165 lines plus `scripts/mythify_classification.py` 432 lines, `scripts/mythify_host_model.py` 386 lines, `scripts/mythify_io.py` 244 lines, `scripts/mythify_model_policy.py` 1,606 lines, `scripts/mythify_outcomes.py` 412 lines, `scripts/mythify_router.py` 955 lines, `scripts/mythify_trace.py` 883 lines, and `scripts/mythify_workflows.py` 963 lines; `mcp-server/src/index.js` 5,600 lines plus `mcp-server/src/classification.js` 415 lines, `mcp-server/src/host-model.js` 318 lines, `mcp-server/src/model-policy.js` 1,052 lines, `mcp-server/src/model-provider.js` 470 lines, `mcp-server/src/host-cli.js` 514 lines, `mcp-server/src/execution-adapter.js` 460 lines, `mcp-server/src/lifecycle-adapter.js` 333 lines, and `mcp-server/src/provider-defaults.js` 415 lines; `mcp-server/src/fanout.js` 2,494; plus `capability-registry.js` (883) and small registry shims. ~24.0k lines of core code.
- Frameworks/deps: Python zero-dependency (stdlib only). Node: `@modelcontextprotocol/sdk@1.29.0`, `zod@4.4.3` (only two direct deps, pinned via lockfile with SRI).
- Entry points: `scripts/mythify.py` `main()` (argparse dispatch to 61 `cmd_*` handlers, 37 inline, 5 imported from `scripts/mythify_outcomes.py`, 2 imported from `scripts/mythify_router.py`, and 17 imported from `scripts/mythify_workflows.py`); `mcp-server/src/index.js` `main()` registering 40 MCP tools; shared facts in `protocol/{operation-registry,classification-rules,workflow-router,surface-manifest}.json` + `PROTOCOL.md`. As of v3.6.27, `classification-rules.json` carries classification policy, not just keyword rules; as of v3.6.28, deterministic classification lives in direct-import Python and MCP modules; as of v3.6.29, host model switch record helpers also live in direct-import modules; as of v3.6.30, trace analysis and playbook formatting live in a direct-import Python module; as of v3.6.31, MCP provider probing and local model role-runner helpers live in a direct-import MCP module; as of v3.6.32, MCP host CLI probe and worker helpers live in a direct-import MCP module; as of v3.6.33, MCP execution adapter probe and run helpers live in a direct-import MCP module; as of v3.6.34, MCP lifecycle probe helpers live in a direct-import MCP module; as of v3.6.35, MCP provider defaults, adapter contracts, and role assignment metadata live in a direct-import MCP module; as of v3.6.36, MCP model policy construction and model triage runner helpers live in a direct-import MCP module; as of v3.6.37, Python model policy construction, provider-default metadata, and model triage runner helpers live in a direct-import CLI module; as of v3.6.38, Python durable IO helpers live in a direct-import CLI module; as of v3.6.39, Python research and campaign workflow stores live in a direct-import CLI module; as of v3.6.40, Python prompt packet and workflow route helpers live in a direct-import CLI module; as of v3.6.41, Python outcome loop store and command handlers live in a direct-import CLI module.
- Evident maturity: mature, deliberately engineered (v3.6.x, multi-version CI, 276 tests, a strict evidence doctrine). Held to a high bar for a developer tool, especially on correctness of the evidence gate, which is the product's central promise. Not held to a production web-service operability bar.
- Audit coverage: the load-bearing code was read exhaustively (verify run, the strict step gate, outcome loops, atomic IO, state-dir resolution, the fanout spawn path, the protocol-hash check, both runtimes' gate implementations, the drift-guard scripts, the test suites). The remaining bulk of `mythify.py` and `index.js` was sampled (all command/tool registrations enumerated; longest functions inspected). Both test suites were executed by the analysis (Python 154 tests pass, MCP 122 tests pass).
- Exclusions: `node_modules/`, `__pycache__/`, `.mythify/local-*.json` benchmark outputs, `coverage/`, `dist/`.

## Overall score

**77/100 - Grade C (adequate, real gaps).**

Mythify is strong where it counts for credibility: an honest, well-tested evidence gate, a genuinely zero-dependency CLI, accurate documentation, and a real corruption-quarantine and atomic-write discipline. It is dragged down by one architectural decision, two hand-maintained native runtimes over one state directory, whose parity is guarded only mechanically (byte-identical JSON, name counts) and not behaviorally. That gap is not theoretical: it has already produced a confirmed correctness bug in the flagship gate (cross-runtime timestamp formats) and several smaller behavioral divergences. A secondary theme is the absence of concurrency safety despite a design that spawns parallel workers sharing the state dir.

Calibration: graded as a mature developer tool / agent protocol; concurrency and the evidence gate are held to a high bar because the project's value proposition is trustworthy, durable evidence.

| Dimension | Score | Grade | Weight | Verdict |
| :-- | :-- | :-- | :-- | :-- |
| Security | 86 | B | 20% | No critical/remote vulns; shell exec is by-design for a local tool, and the named security findings now have regression coverage. |
| Architecture and Design | 72 | C- | 15% | The two-runtime design remains, but classification policy, gate parity, record shape, and drift-critical contracts now have shared data or conformance coverage. |
| Code Quality and Maintainability | 74 | C | 15% | Consistent, well-named, low dead code, but two ~10k-line god-modules and pervasive cross-runtime duplication. |
| Testing and Verification | 84 | B | 15% | 276 tests, no theater, deterministic, gate edges and extracted helper modules covered. |
| Error Handling and Resilience | 76 | C | 10% | Solid single-process behavior, corruption quarantine, JSONL locking, atomic-write fsync, and fanout process-tree cleanup; broader state concurrency remains. |
| Performance and Efficiency | 86 | B | 8% | Fine for a CLI; strict gates and cursor reports now use bounded tail reads, with full scans reserved for explicit history/readiness surfaces. |
| Dependencies and Supply Chain | 90 | A- | 7% | Zero-dep Python; two pinned, current Node deps with lockfile + SRI + Dependabot. Only gap: no `npm audit` gate in CI. |
| Documentation and Drift | 86 | B | 5% | Verified claims hold (40-tool count, protocol-hash check, variant sync); two small drift items. |
| Observability and Operability | 84 | B | 5% | Clean exit-code discipline, `[OK]`/`[FAIL]`/`[WARN]`, quarantine warnings, log compaction. Minor MCP `isError` nit. |
| **Overall (weighted)** | **81** | **B-** | 100% | Strong engineering with most audit risks closed; remaining drag is the large-module structure. |

Weighting: defaults, unchanged. No Critical findings, so no dimension or overall cap is triggered.

## What to fix first

1. [x] ~~`[ARC-001]` Cross-runtime timestamp format mismatch silently breaks the strict step gate~~ - Completed in v3.6.5.
2. [x] ~~`[ARC-003]` Drift guards verify copies and counts, not behavior~~ - Completed in v3.6.18 with classification, verify record-shape, and strict gate-decision conformance.
3. [x] ~~`[SEC-001]` `outcome check` ignores `MYTHIFY_DISABLE_RUN`~~ - Completed in v3.6.5.
4. [x] ~~`[ERR-004]` Fanout async worker output is accumulated unbounded (no `maxBuffer`)~~ - Completed in v3.6.7.
5. [x] ~~`[ARC-002]` Core logic is hand-duplicated across both runtimes~~ - Completed in v3.6.27 for the shared classification policy contract; remaining large-module structure is tracked by QUAL-001.
6. [x] ~~`[ERR-001]` No file locking; `logs compact` TOCTOU drops concurrent appends~~ - Completed in v3.6.20.
7. [x] ~~`[SEC-002]` Verifier stdout/stderr tails persisted unredacted; `init` writes no `.gitignore`~~ - Completed across v3.6.8 and v3.6.21.

## Remediation status

Last updated: 2026-06-16.

- [x] ~~[ARC-001] Cross-runtime timestamp format mismatch silently breaks the strict step gate~~ - Completed in v3.6.5.
- [x] ~~[ARC-002] Core business logic is hand-duplicated across both runtimes~~ - Completed in v3.6.27 by moving classification thresholds, risk, ceremony, fanout, fanout visibility, execution profile, model triage, next-action, and verification-hint facts into the shared classification policy manifest with CLI/MCP conformance coverage.
- [x] ~~[ARC-003] Drift guards verify copies and counts, not behavior or record shapes~~ - Completed in v3.6.18 with classification, verify record-shape, and strict gate-decision conformance.
- [x] ~~[ARC-004] Additional confirmed behavioral divergences between the two runtimes~~ - Completed in v3.6.19 by aligning verifier output-cap and no-exit evidence semantics.
- [x] ~~[SEC-001] `outcome check` ignores `MYTHIFY_DISABLE_RUN`~~ - Completed in v3.6.5.
- [x] ~~[SEC-002] Verifier output tails are persisted unredacted and `init` writes no `.gitignore`~~ - Completed in v3.6.8 and v3.6.21.
- [x] ~~[ERR-001] No file locking; `logs compact` read-then-rewrite drops concurrent appends~~ - Completed in v3.6.20 with shared JSONL lock directories for appends and compaction.
- [x] ~~[ERR-004] Fanout async worker output is accumulated unbounded (no `maxBuffer`)~~ - Completed in v3.6.7.
- [x] ~~[TEST-001] No cross-runtime behavioral conformance test~~ - Completed in v3.6.18 with classification, verify record-shape, and strict gate-decision conformance.
- [ ] [QUAL-001] Two ~10k-line god-modules - Open. Progress in v3.6.28: deterministic classification was extracted to direct-import Python and MCP modules with module-level tests. Progress in v3.6.29: host model switch record construction, capability enrichment, and formatting were extracted to direct-import Python and MCP modules with module-level tests. Progress in v3.6.30: trace analysis and trace playbook Markdown formatting was extracted to a direct-import Python module with module-level tests. Progress in v3.6.31: MCP provider probing and local model role-runner helpers were extracted to `mcp-server/src/model-provider.js` with module-level tests. Progress in v3.6.32: MCP host CLI probe and worker helpers were extracted to `mcp-server/src/host-cli.js` with module-level tests. Progress in v3.6.33: MCP execution adapter probe and run helpers were extracted to `mcp-server/src/execution-adapter.js` with module-level tests. Progress in v3.6.34: MCP lifecycle probe helpers were extracted to `mcp-server/src/lifecycle-adapter.js` with module-level tests. Progress in v3.6.35: MCP provider defaults, adapter contracts, and role assignment metadata were extracted to `mcp-server/src/provider-defaults.js` with module-level tests. Progress in v3.6.36: MCP model policy construction and model triage runner helpers were extracted to `mcp-server/src/model-policy.js` with module-level tests. Progress in v3.6.37: Python model policy construction, provider-default metadata, and model triage runner helpers were extracted to `scripts/mythify_model_policy.py` with module-level tests. Progress in v3.6.38: Python durable IO helpers were extracted to `scripts/mythify_io.py` with direct module tests. Progress in v3.6.39: Python research and campaign workflow stores were extracted to `scripts/mythify_workflows.py` with direct module tests. Progress in v3.6.40: Python prompt packet and workflow route helpers were extracted to `scripts/mythify_router.py` with direct module tests. Progress in v3.6.41: Python outcome loop store and command handlers were extracted to `scripts/mythify_outcomes.py` with direct module tests.
- [x] ~~[SEC-003] Raw, un-slugified name is used as a filename before `slugify`~~ - Completed in v3.6.15.
- [x] ~~[SEC-004] Fanout `context_paths` are not sandboxed to the project root~~ - Completed in v3.6.22.
- [x] ~~[SEC-005] `host_cli_run` accepts an arbitrary `bin` executable~~ - Completed in v3.6.23.
- [x] ~~[SEC-006] `outcome` `allowed_paths` is advisory-only despite a sandboxing-implying name~~ - Completed in v3.6.16.
- [x] ~~[ERR-002] `append_jsonl` is non-atomic for large records~~ - Completed in v3.6.17 by surfacing malformed JSONL records.
- [x] ~~[ERR-003] No `fsync` before atomic rename~~ - Completed in v3.6.24.
- [x] ~~[ERR-005] Fanout timeout kills only the direct child; shell-engine grandchildren can orphan~~ - Completed in v3.6.25.
- [x] ~~[PERF-001] The evidence ledger is re-read in full on every gate check and report, and grows unbounded~~ - Completed in v3.6.26.
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
The Python CLI and Node MCP server are line-for-line ports over one `.mythify/` state directory. v3.6.27 moves classification policy into shared data, and v3.6.18-v3.6.19 cover classification, verify record shape, gate decisions, and verifier failure semantics with cross-runtime tests. Important behavior is still implemented in both languages, including outcome loops, plan/step mutation, memory, lessons, reports, and persistence helpers.
- Members: ARC-001 (the live bug), ARC-002 (the duplication), ARC-003 (guards miss behavior), ARC-004 (more divergences), TEST-001 (no conformance test), DOC-002 (claim overstates reality), QUAL-002 (version surface asymmetry).
- Root fix: keep expanding shared data contracts and conformance tests while reducing the large modules under QUAL-001. Any future shared behavior change should have one manifest or schema edit plus an interop assertion.

### SP-2: Partial concurrency safety despite a parallel-worker design
State IO still mostly assumes a single writer, but the protocol explicitly spawns fanout workers and sub-agents that share the same state directory. v3.6.20 protects top-level JSONL appends and compaction with a shared lock directory, while broader JSON read-modify-write stores still need a concurrency story.
- Members: ERR-001 (completed JSONL locking), ERR-002 (non-atomic append, mitigated by malformed-line warnings), ERR-003 (no fsync), ERR-004 (completed output cap), PERF-001 (full-ledger re-read).
- Root fix: extend advisory locking to read-modify-write JSON stores where concurrent writers are supported; keep worker output buffers capped; consider an index or tail-read for the growing ledger.

### SP-3: Security controls declared but not fully enforced (paper controls)
Several original controls existed in name or partial form. The concrete SEC-001, SEC-002, SEC-003, SEC-004, SEC-005, and SEC-006 instances are now remediated.
- Members: SEC-001 (completed kill-switch coverage), SEC-002 (completed `.gitignore` and verifier-tail redaction), SEC-003 (completed slugged lookup), SEC-004 (completed fanout context containment), SEC-005 (completed host CLI bin allowlist), and SEC-006 (completed advisory labeling).
- Root fix: keep execution controls enforced on every execution path, keep name-to-path lookups normalized before filesystem access, and keep explicit executable overrides tied to their adapter family.

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
- Status: Completed in v3.6.27 for the shared classification policy contract.
- Evidence: `protocol/classification-rules.json` now has schema version 2 and carries the previously duplicated classification facts: trivial and ambiguity thresholds, question prefixes, vague request terms, risk buckets, ceremony buckets, fanout policy, fanout visibility terms, execution-profile policy, next-action text, model-triage policy, and verification hints. Both `scripts/mythify.py` and `mcp-server/src/index.js` load those fields at runtime instead of carrying local duplicate tables. The package copy is byte-for-byte checked by `scripts/check_classification_rules_manifest.mjs`, and the checker now validates the required policy sections. Regression coverage: `tests.test_mythify.TestClassification.test_classification_policy_manifest_contains_shared_decision_facts`, `mcp-server/test/smoke.test.js` packaged-manifest assertions, and `tests.test_interop.TestCliMcpInterop.test_cli_and_mcp_classification_outputs_match`.
- Impact: The classifier's duplicated policy tables are no longer hand-maintained in two languages. A shared manifest edit now changes both adapters' deterministic classification behavior, while existing interop tests keep the two outputs aligned.
- Recommendation: Complete for ARC-002's shared-policy fix. Continue the broader structural cleanup under QUAL-001.
- Verify the fix: `python3 -m unittest tests.test_mythify.TestClassification -v`; `python3 -m unittest tests.test_interop.TestCliMcpInterop.test_cli_and_mcp_classification_outputs_match -v`; `npm test --prefix mcp-server`; `node scripts/check_classification_rules_manifest.mjs`.
- Related: SP-1; ARC-001, ARC-003, DOC-002.

### [ARC-003] Drift guards verify copies and counts, not behavior or record shapes
- Severity: High | Confidence: Confirmed | Effort: M | Dimension: Architecture and Design
- Location: `scripts/check_surface_manifest.mjs`, `scripts/check_classification_rules_manifest.mjs`, interop tests in `tests/test_interop.py`.
- Evidence: v3.6.18 completes the behavioral conformance coverage by asserting identical classification outputs, matching `verify_run` record shapes, and strict gate decisions in both directions: MCP completion accepts CLI `verify_run` evidence, and CLI completion accepts MCP `verify_run` evidence. The older manifest guards still cover byte-identical JSON copies, tool and command counts, and documentation mentions.
- Impact: The guards give false confidence: the cheap, mechanical part of parity is policed while the expensive, bug-prone part (behavioral equivalence) is left to manual discipline. ARC-001 passed every existing guard.
- Recommendation: Keep extending the interop conformance harness when shared behavior changes, especially for gate decisions, record schemas, and classification rules.
- Verify the fix: `python3 -m unittest tests.test_interop.TestCliMcpInterop.test_cli_and_mcp_gate_decisions_accept_each_others_verify_run_records` passes; full interop tests run in CI.
- Related: SP-1; ARC-001, ARC-002, TEST-001.

### [ARC-004] Additional confirmed behavioral divergences between the two runtimes
- Severity: Medium | Confidence: Confirmed | Effort: M | Dimension: Architecture and Design
- Location: verifier capture paths in `scripts/mythify.py` and `mcp-server/src/index.js`; regression coverage in `tests/test_mythify.py` and `mcp-server/test/smoke.test.js`.
- Evidence: v3.6.19 gives CLI and MCP `verify_run` the same default 16 MiB verifier output cap, the same optional `MYTHIFY_VERIFY_MAX_OUTPUT_BYTES` test/config override, and the same `exit_code:-1, verified:false` record semantics for timeouts, output overflow, and signal-killed commands.
- Impact: The same verifier command can produce different recorded evidence depending on which adapter ran it, weakening the "executed verification is trustworthy" promise.
- Recommendation: Keep verifier failure cases in the cross-runtime contract when future execution lanes are added.
- Verify the fix: `python3 -m unittest tests.test_mythify.TestVerify.test_run_output_limit_records_minus_one_and_exits_two tests.test_mythify.TestVerify.test_run_signal_kill_records_minus_one_and_exits_two` and `npm test --prefix mcp-server -- --test-name-pattern 'verify_run'` pass.
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
- Severity: Medium | Confidence: Fixed | Effort: M | Dimension: Security
- Location: `scripts/mythify.py:126`, `scripts/mythify.py:2955-2991`, and `scripts/mythify.py:8898-8899`; `mcp-server/src/index.js:61`, `mcp-server/src/index.js:303-328`, and `mcp-server/src/index.js:2150-2151`; regression tests in `tests/test_mythify.py:2444` and `tests/test_mythify.py:2657`, plus `mcp-server/test/smoke.test.js:1962`.
- Evidence: v3.6.8 added default `.mythify/` `.gitignore` coverage during init. v3.6.21 adds parallel CLI and MCP redaction helpers that mask common API key, token, password, credential, authorization bearer, GitHub, OpenAI, Anthropic, and npm token shapes before stdout and stderr tails are stored or printed by `verify_run` and `outcome_check`.
- Impact: The original secret-at-rest path for verifier stdout/stderr tails is closed. Residual risk remains if a user embeds a secret directly in the verifier command string, which is still recorded for evidence reproducibility.
- Recommendation: Keep verifier command strings secret-free, pass secrets through environment variables or host secret stores, and extend the redaction patterns when new common token shapes appear.
- Verify the fix: `python3 -m unittest tests.test_mythify.TestVerify.test_run_redacts_secret_patterns_from_output_tails tests.test_mythify.TestOutcome.test_outcome_check_redacts_verifier_and_metric_output_tails` and `npm test --prefix mcp-server -- --test-name-pattern 'redact verifier output tails'` pass.
- Related: SP-3.

### [ERR-001] No file locking; `logs compact` read-then-rewrite drops concurrent appends
- Severity: Medium | Confidence: Confirmed (race) / Likely (occurs in practice) | Effort: M | Dimension: Error Handling and Resilience
- Location: JSONL append and compaction paths in `scripts/mythify.py`; MCP append path in `mcp-server/src/index.js`; regression coverage in `tests/test_mythify.py` and `mcp-server/test/smoke.test.js`.
- Evidence: v3.6.20 wraps CLI JSONL appends, MCP JSONL appends, and CLI log compaction in a shared lock-directory protocol keyed by the resolved log path. The compact operation holds the same lock across read, archive, and rewrite, so a concurrent append waits instead of being overwritten.
- Impact: Silent loss of verification/reflection/memory records under concurrency, directly damaging the evidence ledger the product is built on.
- Recommendation: Extend the same lock discipline to broader JSON read-modify-write stores if concurrent plan or outcome updates become a supported workflow.
- Verify the fix: `python3 -m unittest tests.test_mythify.TestLogsCompact.test_logs_compact_lock_preserves_concurrent_append` and `npm test --prefix mcp-server -- --test-name-pattern 'shared lock'` pass.
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
- Location: `tests/test_interop.py`.
- Evidence: v3.6.18 adds gate-decision conformance to the existing classification and verify record-shape interop tests. The harness now checks that each runtime can consume the other's `verify_run` evidence for strict step completion.
- Impact: The most consequential class of regression (silent semantic divergence between the two implementations) has no coverage.
- Recommendation: Keep this harness mandatory in CI and add cases for future shared state behavior.
- Verify the fix: `python3 -m unittest tests.test_interop.TestCliMcpInterop.test_cli_and_mcp_gate_decisions_accept_each_others_verify_run_records` passes.
- Related: SP-1; ARC-001, ARC-003, ARC-004.

### [QUAL-001] Two ~10k-line god-modules
- Severity: Medium | Confidence: Confirmed | Effort: L | Dimension: Code Quality and Maintainability
- Location: `scripts/mythify.py` (5,165-line entrypoint after v3.6.41; `build_parser` remains one large function; 37 `cmd_*` handlers still live in the file, 5 outcome handlers are imported from `scripts/mythify_outcomes.py`, 2 prompt/router handlers are imported from `scripts/mythify_router.py`, and 17 research/campaign handlers are imported from `scripts/mythify_workflows.py`). `mcp-server/src/index.js` (5,600-line entrypoint after v3.6.41; 37 inline `registerTool` blocks remain).
- Evidence: v3.6.28 extracts deterministic classification to `scripts/mythify_classification.py` and `mcp-server/src/classification.js`, with direct module tests. v3.6.29 extracts host model switch record construction, capability enrichment, and formatting to `scripts/mythify_host_model.py` and `mcp-server/src/host-model.js`, with direct module tests. v3.6.30 extracts trace analysis and trace playbook Markdown formatting to `scripts/mythify_trace.py`, with direct module tests. v3.6.31 extracts MCP provider probing and local model role-runner helpers to `mcp-server/src/model-provider.js`, with direct module tests. v3.6.32 extracts MCP host CLI probe and worker helpers to `mcp-server/src/host-cli.js`, with direct module tests. v3.6.33 extracts MCP execution adapter probe and run helpers to `mcp-server/src/execution-adapter.js`, with direct module tests. v3.6.34 extracts MCP lifecycle probe helpers to `mcp-server/src/lifecycle-adapter.js`, with direct module tests. v3.6.35 extracts MCP provider defaults, adapter contracts, and role assignment metadata to `mcp-server/src/provider-defaults.js`, with direct module tests. v3.6.36 extracts MCP model policy construction and model triage runner helpers to `mcp-server/src/model-policy.js`, with direct module tests. v3.6.37 extracts Python model policy construction, provider-default metadata, and model triage runner helpers to `scripts/mythify_model_policy.py`, with direct module tests. v3.6.38 extracts Python durable IO helpers to `scripts/mythify_io.py`, with direct module tests. v3.6.39 extracts Python research and campaign workflow stores to `scripts/mythify_workflows.py`, with direct module tests. v3.6.40 extracts Python prompt packet and workflow route helpers to `scripts/mythify_router.py`, with direct module tests. v3.6.41 extracts Python outcome loop store and command handlers to `scripts/mythify_outcomes.py`, with direct module tests. The main runtime files remain too large, with very large dispatch/registration functions and most command/tool logic still in the entrypoints.
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
- Severity: Low | Confidence: Fixed | Effort: S | Dimension: Security
- Location: `mcp-server/src/fanout.js:980-1027` (containment check and prompt assembly); schema note `mcp-server/src/fanout.js:2336`; regression test `mcp-server/test/fanout.test.js:371`.
- Evidence: v3.6.22 resolves each `context_paths` entry against the project root and rejects absolute paths, relative `../` escapes, and symlink targets that resolve outside that root before any worker job is started.
- Impact: Fanout no longer inlines out-of-project files into delegated worker prompts by accident. Absolute paths remain accepted when they resolve inside the project root.
- Recommendation: Keep external context opt-in out of the default path; if a future workflow needs cross-project context, add a separate explicit allowlist.
- Verify the fix: `npm test --prefix mcp-server -- --test-name-pattern 'context_paths'` passes, including absolute outside, relative escape, and symlink escape cases.
- Related: SP-3.

### [SEC-005] `host_cli_run` accepts an arbitrary `bin` executable
- Severity: Low | Confidence: Fixed | Effort: S | Dimension: Security
- Location: `mcp-server/src/host-cli.js` `resolveHostCliBinary`; `host_cli_probe` and `host_cli_run` schema descriptions; regression tests in `mcp-server/test/host-cli-run.test.js` and `mcp-server/test/host-cli.test.js`.
- Evidence: v3.6.23 rejects executable explicit `bin` overrides whose basename is not part of the selected host CLI family. Missing paths still report the existing not-executable error, while an executable `custom-runner` is blocked before invocation.
- Impact: The host CLI adapter can no longer be pointed at an arbitrary executable name through the MCP `bin` input. Operator-controlled environment variables still support local installation overrides.
- Recommendation: Keep the host-family basename allowlist in sync with supported Kimi, OpenCode, and Antigravity binary names.
- Verify the fix: `npm test --prefix mcp-server -- --test-name-pattern 'host_cli_run|host_cli_probe reports missing'` passes.
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
- Status: Completed in v3.6.24.
- Evidence: CLI `_write_text_atomic` flushes and fsyncs the temp fd before `os.replace`, then best-effort fsyncs the parent directory. MCP `writeTextAtomic` opens the temp file explicitly, writes, fsyncs, closes, renames, and best-effort fsyncs the parent directory. Regression coverage: `tests.test_mythify.TestDurableIo` checks Python order, and `mcp-server/test/durable-io.test.js` checks the MCP helper sequence.
- Impact: Rare data-loss window on hard crash is reduced for atomic state-file rewrites.
- Recommendation: Complete. Keep directory fsync best effort for platform compatibility.
- Verify the fix: `python3 -m unittest tests.test_mythify.TestDurableIo` and `npm test --prefix mcp-server`.
- Related: SP-2; ERR-001.

### [ERR-005] Fanout timeout kills only the direct child; shell-engine grandchildren can orphan
- Severity: Low | Confidence: Suspected | Effort: S | Dimension: Error Handling and Resilience
- Location: `mcp-server/src/fanout.js:1057-1064` (kills `child.kill("SIGKILL")` only); shell-engine spawn `:1030`.
- Status: Completed in v3.6.25.
- Evidence: `runSubprocess` now starts local worker subprocesses in a fresh POSIX process group and kills that group on timeout or output-cap failure. Regression coverage: `mcp-server/test/fanout.test.js` starts a command worker that spawns a long-lived grandchild and verifies a timed-out worker leaves no marker from the grandchild.
- Impact: Timed-out fanout command workers no longer leave the common shell-engine grandchild process tree alive.
- Recommendation: Complete. Windows keeps direct-child kill semantics because POSIX process groups are unavailable there.
- Verify the fix: `node --test test/fanout.test.js`.
- Related: SP-2.

### [PERF-001] The evidence ledger is re-read in full on every gate check and report, and grows unbounded
- Severity: Low | Confidence: Confirmed | Effort: M | Dimension: Performance and Efficiency
- Location: gate read `scripts/mythify.py:8327` (`read_jsonl(state / "verifications.jsonl")`), report assembly reads all records; `verifications.jsonl` is already 3.2 MB / ~1,533 records in this repo.
- Status: Completed in v3.6.26.
- Evidence: CLI and MCP strict step gates now use `read_jsonl_since` / `readJsonlSince` to read verification records from a timestamp-bounded tail window. `report --since last` and MCP `work_report` use the same bounded reader for verification and reflection logs when the cursor has a lower-bound event. Full scans remain for explicit history, readiness, summary, and `report --since start` surfaces.
- Impact: The hot-path gate and cursor report cost is now bounded by the recent log window in normal use, while preserving correctness by falling back to older chunks until a pre-boundary record is found.
- Recommendation: Complete. Keep `logs compact` for archival control and full-history surfaces.
- Verify the fix: `tests.test_mythify.TestDurableIo` confirms the Python tail reader ignores an old malformed prefix when a valid boundary record is in the tail; `mcp-server/test/durable-io.test.js` checks MCP gate and report call sites use the bounded reader.
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

- **Security (86):** No critical or remotely exploitable issues; shell execution is the tool's stated purpose and inputs are operator-supplied, with good controls (loopback allowlist for local providers, env-var key names not raw secrets, recursive-fanout fork-bomb guards, kill-switch coverage, verifier-tail redaction, fanout context containment, and host CLI bin allowlisting). Score remains below A because command execution is still a core operator-facing capability that needs ongoing review as adapters are added.
- **Architecture (72):** ARC-001, ARC-003, ARC-004, and the shared classification-policy part of ARC-002 are complete. The dual-runtime model remains, but the highest-value parity facts now have executable conformance or shared manifest coverage.
- **Code Quality (74):** Naming, consistency, and dead-code hygiene are good; the drag is the two god-modules (QUAL-001) and the cross-runtime duplication.
- **Testing (84):** A genuine strength in depth and honesty; v3.6.18 closes the strict gate-decision conformance gap, and v3.6.19 adds verifier failure parity regressions.
- **Error Handling (76):** Single-process behavior and corruption quarantine are solid; v3.6.20 closes the JSONL compaction race, v3.6.24 fsyncs atomic state rewrites, and v3.6.25 kills fanout subprocess process groups. Broader SP-2 concurrency risks remain.
- **Performance (86):** Adequate for a CLI; v3.6.26 bounds recent gate and cursor-report ledger reads, while explicit full-history surfaces still scan the full log by design.
- **Dependencies (90):** Best dimension; only DEP-001 (no CI audit gate) keeps it from A.
- **Documentation (86):** Verified accurate on the claims that matter; DOC-001/002 are small drift items.
- **Observability (84):** Good for the tool class; OBS-001 is a minor client-ergonomics nit.

## Remediation plan

- **Quick wins** (highest value per effort; act now): completed SEC-001, SEC-003, SEC-006, ERR-002, and ERR-004.
- **Plan now** (High/Critical and scheduled Medium work, suggested order): QUAL-001 (large-module reduction and direct module tests).
- **Verify first** (Suspected; re-check the cited code before acting): none remaining.
- **Backlog** (Low; batch): none remaining.

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
