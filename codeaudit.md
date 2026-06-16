# Mythify Code Audit

Original read-only audit date: 2026-06-15. Current tracker updated through
v3.6.52 on 2026-06-16.

This file is now the closed remediation tracker. Completed findings have been
collapsed into compact ledgers, and the original long-form evidence is
preserved in git history.

## Current Status

- Original audit score: 77/100, Grade C.
- Current remediation score: 84/100, Grade B.
- Findings closed: 24 of 24.
- Findings still open: 0.
- Current blocking theme: none. The remaining architectural risk is ongoing
  dual-runtime parity discipline, not an open audit task.

## Snapshot

- Project: Mythify, an evidence protocol for AI coding agents.
- Runtime surfaces: Python 3.9+ CLI, Node 18+ MCP server, JSON protocol
  manifests, and generated Markdown protocol docs.
- Current tests: 294 total, 156 Python and 138 MCP.
- Dependency posture: zero-dependency Python CLI; two direct Node dependencies
  pinned by lockfile with SRI.
- Original audited state: branch `main`, commit `4f0177b` (`fix: harden strict
  step evidence gate`).

| Area | Current shape |
| :-- | :-- |
| Python CLI entrypoint | `scripts/mythify.py`, 1,492 lines, with `main()` plus imported parser, workflow, view, memory, outcome, trace, host-model, and policy helpers. |
| Python modules | Classification, host model, IO, memory, model policy, model triage, outcomes, parser, router, trace, views, status views, and workflows are direct-import modules with tests. |
| MCP entrypoint | `mcp-server/src/index.js`, 1,224 lines, as a composition root for focused registration and view modules. |
| MCP modules | Adapter tools, workflow tools, view tools, view core, status views, memory tools, plan tools, outcome tools, verification tools, classification, host model, model policy, model providers, host CLI, execution, lifecycle, provider defaults, fanout, fanout policy, fanout registration, and registries are separate modules. |
| Largest runtime source files | `scripts/mythify.py` 1,492 lines, `mcp-server/src/fanout.js` 1,473, `mcp-server/src/workflow-tools.js` 1,447, `scripts/mythify_model_policy.py` 1,315, `scripts/mythify_parser.py` 1,311. |
| Shared contracts | Protocol manifests, classification policy, gate parity tests, verify record-shape tests, operation registry, surface manifest, and protocol hash checks. |

## Scorecard

| Dimension | Score | Grade | Weight | Current verdict |
| :-- | :-- | :-- | :-- | :-- |
| Security | 86 | B | 20% | Original concrete security findings are fixed and covered. Shell execution remains core behavior that needs review when adapters are added. |
| Architecture and Design | 74 | C | 15% | Most parity bugs are fixed or tested. The dual-runtime design still creates ongoing maintenance cost. |
| Code Quality and Maintainability | 82 | B | 15% | Runtime source files now sit under the modularity ceiling with direct-import extraction coverage. |
| Testing and Verification | 84 | B | 15% | Strong deterministic tests, useful interop coverage, and direct module tests for extracted areas. |
| Error Handling and Resilience | 76 | C | 10% | JSONL locking, fsync, quarantine, and fanout cleanup are improved. Broader JSON read-modify-write concurrency remains worth watching. |
| Performance and Efficiency | 86 | B | 8% | Hot gate and cursor report paths use bounded tail reads. Full-history views still scan by design. |
| Dependencies and Supply Chain | 90 | A- | 7% | Clean dependency shape with CI audit coverage added. |
| Documentation and Drift | 86 | B | 5% | Protocol, tool counts, roadmap, and README drift findings are fixed. |
| Observability and Operability | 84 | B | 5% | Exit-code and status reporting are good. MCP error flagging is now fixed. |
| **Overall weighted** | **84** | **B** | 100% | All audit tasks are closed. The remaining risk is sustaining parity and modular boundaries during future changes. |

## Active Remediation

No open remediation tasks remain.

### QUAL-001 Detail

- Severity: Medium.
- Confidence: Confirmed.
- Effort: Large.
- Dimension: Code Quality and Maintainability.
- Status: completed in v3.6.52.
- Closed by:
  - Python parser construction extracted to `scripts/mythify_parser.py`.
  - Python model triage execution extracted to `scripts/mythify_model_triage.py`.
  - Python release readiness, fanout timeline, and phase views extracted to
    `scripts/mythify_views_status.py`.
  - MCP workflow route, prompt packet, classification, campaign, and related
    registrations extracted to `mcp-server/src/workflow-tools.js`.
  - MCP read-only view builders extracted to `mcp-server/src/view-core.js` and
    status-specific view builders extracted to
    `mcp-server/src/view-status-core.js`.
  - MCP fanout policy and registration schemas extracted to
    `mcp-server/src/fanout-policy.js` and
    `mcp-server/src/fanout-registration.js`.
- Verification: `python3 scripts/mythify.py verify run ... --claim 'runtime
  source files are at or below 1500 lines'` passed with exit 0 on 2026-06-16.
  The checked runtime files were `scripts/*.py` and `mcp-server/src/*.js`.
- Current note: large test suites remain above 1,500 lines, but they are not
  runtime source files and were not part of this modularity ceiling.

## Completed Findings

| Status | ID | Original issue | Fixed in |
| :-- | :-- | :-- | :-- |
| [x] | ARC-001 | Cross-runtime timestamp format mismatch broke the strict step gate. | v3.6.5 |
| [x] | ARC-002 | Core business logic was hand-duplicated across both runtimes. | v3.6.27 for shared classification policy; remaining size work moved to QUAL-001 |
| [x] | ARC-003 | Drift guards checked copies and counts, not behavior or record shapes. | v3.6.18 |
| [x] | ARC-004 | Additional verifier behavior diverged between runtimes. | v3.6.19 |
| [x] | SEC-001 | `outcome check` ignored `MYTHIFY_DISABLE_RUN`. | v3.6.5 |
| [x] | SEC-002 | Verifier output tails were persisted unredacted and `init` wrote no `.gitignore`. | v3.6.8 and v3.6.21 |
| [x] | SEC-003 | Raw, un-slugified names could be used as filenames. | v3.6.15 |
| [x] | SEC-004 | Fanout `context_paths` were not sandboxed to the project root. | v3.6.22 |
| [x] | SEC-005 | `host_cli_run` accepted an arbitrary executable name. | v3.6.23 |
| [x] | SEC-006 | `outcome allowed_paths` implied sandboxing despite being advisory only. | v3.6.16 |
| [x] | ERR-001 | `logs compact` could drop concurrent JSONL appends. | v3.6.20 |
| [x] | ERR-002 | Malformed trailing JSONL records were silently skipped. | v3.6.17 |
| [x] | ERR-003 | Atomic state rewrites lacked fsync before rename. | v3.6.24 |
| [x] | ERR-004 | Fanout worker output accumulated without a size cap. | v3.6.7 |
| [x] | ERR-005 | Fanout timeouts killed only the direct child process. | v3.6.25 |
| [x] | PERF-001 | Gate and report paths re-read the full evidence ledger. | v3.6.26 |
| [x] | DEP-001 | CI had no `npm audit` gate. | v3.6.9 |
| [x] | TEST-001 | There was no cross-runtime behavioral conformance test. | v3.6.18 |
| [x] | TEST-002 | Read-only view commands were lightly tested. | v3.6.14 |
| [x] | DOC-001 | `roadmap.md` referenced a stale release. | v3.6.12 |
| [x] | DOC-002 | README overstated the shared contract core. | v3.6.13 |
| [x] | QUAL-001 | Source files exceeded the modularity ceiling. | v3.6.52 |
| [x] | QUAL-002 | Version surface was asymmetric. | v3.6.11 |
| [x] | OBS-001 | MCP tool errors did not set `isError`. | v3.6.10 |

## Module Extraction Ledger

| Version | Area extracted | Module or surface |
| :-- | :-- | :-- |
| v3.6.28 | Deterministic classification | `scripts/mythify_classification.py`, `mcp-server/src/classification.js` |
| v3.6.29 | Host model record construction, capability enrichment, and formatting | `scripts/mythify_host_model.py`, `mcp-server/src/host-model.js` |
| v3.6.30 | Trace analysis and trace playbook formatting | `scripts/mythify_trace.py` |
| v3.6.31 | MCP provider probing and local model role-runner helpers | `mcp-server/src/model-provider.js` |
| v3.6.32 | MCP host CLI probe and worker helpers | `mcp-server/src/host-cli.js` |
| v3.6.33 | MCP execution adapter probe and run helpers | `mcp-server/src/execution-adapter.js` |
| v3.6.34 | MCP lifecycle probe helpers | `mcp-server/src/lifecycle-adapter.js` |
| v3.6.35 | MCP provider defaults, adapter contracts, and role metadata | `mcp-server/src/provider-defaults.js` |
| v3.6.36 | MCP model policy and model triage runner helpers | `mcp-server/src/model-policy.js` |
| v3.6.37 | Python model policy, provider metadata, and model triage helpers | `scripts/mythify_model_policy.py` |
| v3.6.38 | Python durable IO helpers | `scripts/mythify_io.py` |
| v3.6.39 | Python research and campaign workflow stores | `scripts/mythify_workflows.py` |
| v3.6.40 | Python prompt packet and workflow route helpers | `scripts/mythify_router.py` |
| v3.6.41 | Python outcome loop store and command handlers | `scripts/mythify_outcomes.py` |
| v3.6.42 | Python read-only views and command handlers | `scripts/mythify_views.py` |
| v3.6.43 | Python trace command handlers | `scripts/mythify_trace.py` |
| v3.6.44 | Python memory and lesson stores and command handlers | `scripts/mythify_memory.py` |
| v3.6.45 | Python host-model state helpers and command handlers | `scripts/mythify_host_model.py` |
| v3.6.46 | MCP adapter and host integration registrations | `mcp-server/src/adapter-tools.js` |
| v3.6.47 | MCP read-only view registrations | `mcp-server/src/view-tools.js` |
| v3.6.48 | MCP memory and lesson registrations | `mcp-server/src/memory-tools.js` |
| v3.6.49 | MCP outcome loop registrations | `mcp-server/src/outcome-tools.js` |
| v3.6.50 | MCP plan registrations | `mcp-server/src/plan-tools.js` |
| v3.6.51 | MCP verification and reflection registrations | `mcp-server/src/verification-tools.js` |
| v3.6.52 | Python parser construction | `scripts/mythify_parser.py` |
| v3.6.52 | Python model triage execution | `scripts/mythify_model_triage.py` |
| v3.6.52 | Python status view builders | `scripts/mythify_views_status.py` |
| v3.6.52 | MCP workflow tools | `mcp-server/src/workflow-tools.js` |
| v3.6.52 | MCP read-only view builders and status views | `mcp-server/src/view-core.js`, `mcp-server/src/view-status-core.js` |
| v3.6.52 | MCP fanout policy and registration helpers | `mcp-server/src/fanout-policy.js`, `mcp-server/src/fanout-registration.js` |

## Systemic Patterns

### SP-1: Dual runtimes still need behavioral guardrails

The worst parity bugs have been fixed and covered: timestamp formats, verifier
record shapes, strict gate decisions, classification policy, and verifier
failure semantics. The remaining rule is simple: any shared behavior change must
ship with a shared manifest or an interop assertion.

### SP-2: Concurrency safety is improved but not universal

Top-level JSONL appends and compaction now share lock directories, fanout output
is capped, process groups are cleaned up on timeout, and atomic writes fsync.
Broader JSON read-modify-write stores should still get explicit locking before
multi-writer plan or outcome mutation becomes a supported workflow.

### SP-3: Paper security controls were converted into enforced controls

The concrete original control gaps are closed: execution kill-switch coverage,
verifier-tail redaction, `.gitignore`, path normalization, fanout context
containment, host CLI executable allowlisting, and advisory `allowed_paths`
wording.

## Preserved Strengths

- Strict evidence gate with regression coverage.
- Deterministic test suite with real subprocesses and real exit codes.
- Corrupt-state quarantine and durable atomic writes.
- Protocol hash checks and generated protocol variants.
- Minimal supply chain: stdlib Python plus two direct Node dependencies.
- Accurate MCP tool count and protocol surface checks.

## Verification Checklist

- `rg -n "^- \\[ \\]" codeaudit.md` should return no results.
- `for f in scripts/*.py mcp-server/src/*.js; do wc -l "$f"; done | awk '$1 > 1500 {print}'` should return no results.
- `git diff --check -- codeaudit.md` should pass.
- `rg -n -P "\\x{2013}|\\x{2014}" codeaudit.md` should return no results.
