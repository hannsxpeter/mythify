import test from "node:test";
import assert from "node:assert/strict";
import {
  OUTCOME_TOOL_NAMES,
  registerOutcomeTools,
} from "../src/outcome-tools.js";

function makeHarness({
  failingCommands = new Map(),
  scopeViolations = (allowedPaths) => allowedPaths.length > 0 ? ["docs/release.md"] : [],
} = {}) {
  const registered = [];
  const outcomes = new Map();
  const iterations = new Map();
  const verifications = [];
  const runs = [];
  let active = null;

  const server = {
    registerTool(name, config, handler) {
      registered.push({ name, config, handler });
    },
  };

  registerOutcomeTools(server, {
    guarded: (handler) => async (args) => handler(args || {}),
    slugify: (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""),
    uniqueOutcomeSlug: (base) => outcomes.has(base) ? `${base}-2` : base,
    isoNow: () => "2026-06-16T00:00:00.000Z",
    saveOutcome: (slug, goal) => {
      goal.updated = "2026-06-16T00:00:00.000Z";
      outcomes.set(slug, goal);
    },
    setActiveOutcomeSlug: (slug) => {
      active = slug;
    },
    resolveOutcome: (name) => {
      const slug = name || active;
      const goal = outcomes.get(slug);
      if (!goal) {
        return { error: "[FAIL] No active outcome. Create one with outcome_start, or pass an outcome name." };
      }
      return { slug, goal };
    },
    readOutcomeIterations: (slug) => iterations.get(slug) || [],
    formatOutcomeStatus: (slug, goal, rows = []) =>
      `[OK] Outcome ${slug}: ${goal.goal}\nstatus: ${goal.status}\niterations: ${rows.length}`,
    runShellCapture: (command, timeoutSeconds) => {
      runs.push({ command, timeoutSeconds });
      const exitCode = failingCommands.get(command) || 0;
      return {
        command,
        exit_code: exitCode,
        duration_seconds: 0.01,
        stdout_tail: command.includes("metric") ? "42" : "verified",
        stderr_tail: "",
        verified: exitCode === 0,
      };
    },
    parseMetricScore: (output) => Number.parseFloat(String(output)),
    appendJsonl: (target, record) => {
      if (target.startsWith("iterations:")) {
        const slug = target.slice("iterations:".length);
        const rows = iterations.get(slug) || [];
        rows.push(record);
        iterations.set(slug, rows);
        return;
      }
      verifications.push(record);
    },
    outcomeIterationsPath: (slug) => `iterations:${slug}`,
    verificationsPath: () => "verifications",
    verificationStepContext: () => ({ plan: "release", step_id: 36 }),
    clearActiveOutcomeSlug: (slug) => {
      if (!slug || active === slug) {
        active = null;
      }
    },
    scopeViolations,
    mcpFrontDoorNote: " Route first.",
  });

  return { registered, outcomes, iterations, verifications, runs, get active() { return active; } };
}

test("outcome tool registrar wires outcome loop handlers", async () => {
  const harness = makeHarness();
  const { registered, outcomes, iterations, verifications, runs } = harness;

  assert.deepEqual(registered.map((entry) => entry.name), OUTCOME_TOOL_NAMES);
  const outcomeStart = registered.find((entry) => entry.name === "outcome_start");
  assert.ok(outcomeStart.config.inputSchema.visibility);

  const startResult = await outcomeStart.handler({
    goal: "Ship outcome",
    success: "Verifier passes",
    verify_command: "verify command",
    metric_command: "metric command",
    max_iterations: 2,
    allowed_paths: ["src"],
    visibility: "summary",
  });
  assert.match(startResult, /^\[OK\] Outcome started: ship-outcome/);
  assert.equal(harness.active, "ship-outcome");
  assert.equal(outcomes.get("ship-outcome").allowed_paths[0], "src");
  assert.equal(outcomes.get("ship-outcome").agent_command, "");
  assert.equal(outcomes.get("ship-outcome").max_cost, null);
  assert.equal(outcomes.get("ship-outcome").cost_spent, 0.0);
  assert.equal(outcomes.get("ship-outcome").escalate_after, null);

  const outcomeCheck = registered.find((entry) => entry.name === "outcome_check");
  const checkResult = await outcomeCheck.handler({
    timeout_seconds: 9,
    notes: "first pass",
  });
  assert.match(checkResult, /succeeded/);
  assert.deepEqual(runs.map((run) => run.command), ["verify command", "metric command"]);
  assert.equal(iterations.get("ship-outcome")[0].metric.score, 42);
  assert.equal(iterations.get("ship-outcome")[0].agent, null);
  assert.equal(iterations.get("ship-outcome")[0].cost, 0.0);
  assert.equal(iterations.get("ship-outcome")[0].cost_spent, 0.0);
  assert.deepEqual(
    iterations.get("ship-outcome")[0].scope_violations,
    ["docs/release.md"]
  );
  assert.match(iterations.get("ship-outcome")[0].next_action, /Scope note:/);
  assert.equal(verifications[0].claim, "Outcome ship-outcome: Verifier passes");
  assert.equal(verifications[0].step_id, 36);
  assert.deepEqual(Object.keys(verifications[0].provenance).sort(), [
    "git_commit",
    "mythify_version",
    "worktree_clean",
  ]);
  assert.match(verifications[0].provenance.mythify_version, /^\d+\.\d+\.\d+$/);
  assert.ok(
    verifications[0].provenance.git_commit === null ||
      typeof verifications[0].provenance.git_commit === "string"
  );

  const outcomeStatus = registered.find((entry) => entry.name === "outcome_status");
  const statusResult = await outcomeStatus.handler({ name: "ship-outcome" });
  assert.match(statusResult, /status: succeeded/);

  const outcomeResults = registered.find((entry) => entry.name === "outcome_results");
  const results = await outcomeResults.handler({ name: "ship-outcome" });
  assert.match(results, /iteration 1: verified=true/);
  assert.match(results, /metric score: 42/);

  const outcomeStop = registered.find((entry) => entry.name === "outcome_stop");
  const stopResult = await outcomeStop.handler({ name: "ship-outcome", reason: "done" });
  assert.match(stopResult, /^\[OK\] Outcome ship-outcome stopped: done/);
  assert.equal(outcomes.get("ship-outcome").status, "stopped");
  assert.equal(harness.active, null);
});

test("outcome_check kill switch refuses execution", async () => {
  const harness = makeHarness();
  const outcomeStart = harness.registered.find((entry) => entry.name === "outcome_start");
  const outcomeCheck = harness.registered.find((entry) => entry.name === "outcome_check");

  await outcomeStart.handler({
    goal: "No run",
    success: "No command",
    verify_command: "verify command",
  });

  const previous = process.env.MYTHIFY_DISABLE_RUN;
  process.env.MYTHIFY_DISABLE_RUN = "1";
  try {
    const result = await outcomeCheck.handler({});
    assert.match(result, /^\[FAIL\] outcome_check is disabled/);
    assert.equal(harness.runs.length, 0);
  } finally {
    if (previous === undefined) {
      delete process.env.MYTHIFY_DISABLE_RUN;
    } else {
      process.env.MYTHIFY_DISABLE_RUN = previous;
    }
  }
});

test("outcome metric failure records combined unverified evidence", async () => {
  const harness = makeHarness({
    failingCommands: new Map([["metric command", 9]]),
  });
  const outcomeStart = harness.registered.find((entry) => entry.name === "outcome_start");
  const outcomeCheck = harness.registered.find((entry) => entry.name === "outcome_check");

  await outcomeStart.handler({
    goal: "Metric contract",
    success: "verifier and metric pass",
    verify_command: "verify command",
    metric_command: "metric command",
    max_iterations: 1,
  });
  const result = await outcomeCheck.handler({ format: "json" });

  assert.match(result, /^\[FAIL\]/);
  assert.equal(harness.iterations.get("metric-contract")[0].verify.verified, true);
  assert.equal(harness.iterations.get("metric-contract")[0].metric.verified, false);
  assert.equal(harness.iterations.get("metric-contract")[0].verified, false);
  assert.equal(harness.verifications[0].verified, false);
  assert.equal(harness.verifications[0].exit_code, 9);
  assert.equal(harness.verifications[0].outcome_verify.verified, true);
  assert.equal(harness.verifications[0].outcome_metric.verified, false);
  assert.equal(harness.verifications[0].outcome_metric.exit_code, 9);
});

test("outcome tool registrar rejects missing required deps", () => {
  assert.throws(
    () => registerOutcomeTools({ registerTool() {} }, {}),
    /requires deps\.guarded/
  );
});
