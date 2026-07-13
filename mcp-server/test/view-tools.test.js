import test from "node:test";
import assert from "node:assert/strict";
import {
  VIEW_TOOL_NAMES,
  registerViewTools,
} from "../src/view-tools.js";
import {
  releaseReadinessStatus,
  summarizeReleaseGate,
} from "../src/view-status-core.js";
import { verificationFreshness } from "../src/verification-provenance.js";

function viewDeps() {
  return {
    guarded: (handler) => async (args) => handler(args || {}),
    buildWorkflowDashboard: (recent) => ({ kind: "dashboard", recent }),
    formatWorkflowDashboard: (view) => `dashboard ${view.recent}`,
    buildVerificationHistoryView: (recent) => ({ kind: "history", recent }),
    formatVerificationHistoryView: (view) => `history ${view.recent}`,
    buildWorkReport: (args) => ({ kind: "report", ...args }),
    formatWorkReport: (view) => `report ${view.recent}`,
    buildBackgroundView: (recent) => ({ kind: "background", recent }),
    formatBackgroundView: (view) => `background ${view.recent}`,
    buildEvidenceHarnessView: (recent) => ({ kind: "harness", recent }),
    formatEvidenceHarnessView: (view) => `harness ${view.recent}`,
    buildOutcomeProgressView: (recent) => ({ kind: "outcome", recent }),
    formatOutcomeProgressView: (view) => `outcome ${view.recent}`,
    buildReleaseReadinessView: () => ({ kind: "readiness" }),
    formatReleaseReadinessView: (view) => view.kind,
    buildFanoutTimelineView: (recent) => ({ kind: "fanout", recent }),
    formatFanoutTimelineView: (view) => `fanout ${view.recent}`,
    buildPhaseView: (recent) => ({ kind: "phase", recent }),
    formatPhaseView: (view) => `phase ${view.recent}`,
  };
}

test("view tool registrar wires stable read-only view tool names", async () => {
  const registered = [];
  const server = {
    registerTool(name, config, handler) {
      registered.push({ name, config, handler });
    },
  };

  registerViewTools(server, viewDeps());

  assert.deepEqual(registered.map((entry) => entry.name), VIEW_TOOL_NAMES);
  const workflowStatus = registered.find((entry) => entry.name === "workflow_status");
  assert.ok(workflowStatus.config.inputSchema.recent);

  const jsonResult = await workflowStatus.handler({ recent: 2, format: "json" });
  assert.match(jsonResult, /^\[OK\] /);
  const payload = JSON.parse(jsonResult.slice("[OK] ".length));
  assert.equal(payload.kind, "dashboard");
  assert.equal(payload.recent, 2);

  const workReport = registered.find((entry) => entry.name === "work_report");
  const refusal = await workReport.handler({ mark: true, since: "last" });
  assert.match(refusal, /^\[FAIL\] mark cannot be combined with since/);

  const evidenceHarness = registered.find((entry) => entry.name === "evidence_harness");
  const harnessJson = await evidenceHarness.handler({ recent: 4, format: "json" });
  assert.match(harnessJson, /^\[OK\] /);
  const harnessPayload = JSON.parse(harnessJson.slice("[OK] ".length));
  assert.equal(harnessPayload.kind, "harness");
  assert.equal(harnessPayload.recent, 4);
});

test("view tool registrar rejects missing required deps", () => {
  assert.throws(
    () => registerViewTools({ registerTool() {} }, {}),
    /requires deps\.guarded/
  );
});

test("P-MUST-02 readiness accepts only fresh passing evidence", () => {
  const gate = {
    id: "tests",
    label: "Tests",
    required: true,
    sources: ["tests/"],
    commands: ["python3 -m unittest discover -s tests -v"],
  };
  const current = { git_commit: "current", worktree_clean: true, mythify_version: "4.3.0" };
  const base = {
    kind: "executed",
    claim: "suite passes",
    command: "python3 -m unittest discover -s tests -v",
    exit_code: 0,
    verified: true,
    timestamp: "2026-07-13T00:00:00Z",
  };

  const legacy = summarizeReleaseGate(gate, [base], current);
  assert.equal(legacy.status, "stale");
  assert.deepEqual(legacy.freshness, {
    status: "legacy",
    reason: "missing_provenance",
  });
  assert.equal(legacy.latest_record.provenance, null);

  const stale = summarizeReleaseGate(
    gate,
    [{ ...base, provenance: { git_commit: "old", worktree_clean: true, mythify_version: "4.3.0" } }],
    current
  );
  assert.equal(stale.status, "stale");
  assert.equal(stale.freshness.reason, "git_commit_mismatch");
  assert.equal(releaseReadinessStatus([stale], { status: "clean" }), "needs_evidence");

  const versionStale = summarizeReleaseGate(
    gate,
    [{ ...base, provenance: { git_commit: "current", worktree_clean: true, mythify_version: "4.2.0" } }],
    current
  );
  assert.equal(versionStale.status, "stale");
  assert.equal(versionStale.freshness.reason, "mythify_version_mismatch");

  const fresh = summarizeReleaseGate(
    gate,
    [{ ...base, provenance: { git_commit: "current", worktree_clean: true, mythify_version: "4.3.0" } }],
    current
  );
  assert.equal(fresh.status, "passed");
  assert.equal(fresh.freshness.status, "fresh");
  assert.equal(
    releaseReadinessStatus([fresh], { status: "clean" }),
    "ready_for_release_review"
  );
  assert.deepEqual(
    verificationFreshness(
      { provenance: { git_commit: "recorded", worktree_clean: true, mythify_version: "4.3.0" } },
      { git_commit: null, worktree_clean: null, mythify_version: "4.3.0" }
    ),
    { status: "stale", reason: "current_git_commit_unavailable" }
  );
  const spoof = summarizeReleaseGate(
    gate,
    [{ ...base, command: "true", claim: "python3 -m unittest discover -s tests -v" }],
    current
  );
  assert.equal(spoof.status, "missing");
  const inconsistent = summarizeReleaseGate(
    gate,
    [{ ...base, exit_code: 9, provenance: { git_commit: "current", worktree_clean: true, mythify_version: "4.3.0" } }],
    current
  );
  assert.equal(inconsistent.status, "failed");
  assert.deepEqual(
    verificationFreshness(
      { provenance: { git_commit: null, worktree_clean: null, mythify_version: "4.3.0" } },
      { git_commit: null, worktree_clean: null, mythify_version: "4.3.0" }
    ),
    { status: "stale", reason: "current_git_commit_unavailable" }
  );
  assert.deepEqual(
    verificationFreshness(
      { provenance: { git_commit: "current", worktree_clean: false, mythify_version: "4.3.0" } },
      current
    ),
    { status: "stale", reason: "recorded_worktree_dirty" }
  );
  assert.deepEqual(
    verificationFreshness({ provenance: [] }, current),
    { status: "legacy", reason: "missing_provenance" }
  );
});
