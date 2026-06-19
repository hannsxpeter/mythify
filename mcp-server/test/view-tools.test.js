import test from "node:test";
import assert from "node:assert/strict";
import {
  VIEW_TOOL_NAMES,
  registerViewTools,
} from "../src/view-tools.js";

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
