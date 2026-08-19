import test from "node:test";
import assert from "node:assert/strict";
import {
  OUTCOME_TOOL_NAMES,
  registerOutcomeTools,
} from "../src/outcome-tools.js";

function makeHarness({
  failingCommands = new Map(),
  scopeViolations = (allowedPaths) => allowedPaths.length > 0 ? ["docs/release.md"] : [],
  changedPaths = () => [],
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
    changedPaths,
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
    "worktree_digest",
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

function findTool(harness, name) {
  return harness.registered.find((entry) => entry.name === name);
}

test("a second active outcome requires an explicit supersession", async () => {
  const harness = makeHarness();
  const outcomeStart = findTool(harness, "outcome_start");
  await outcomeStart.handler({
    goal: "Loop A", success: "s", verify_command: "true", name: "loop-a",
  });
  const refused = await outcomeStart.handler({
    goal: "Loop B", success: "s", verify_command: "true", name: "loop-b",
  });
  assert.match(refused, /still active/);
  assert.equal(harness.outcomes.has("loop-b"), false);

  const superseding = await outcomeStart.handler({
    goal: "Loop B", success: "s", verify_command: "true", name: "loop-b",
    supersede: "loop-a targeted the wrong module",
  });
  assert.match(superseding, /superseded: loop-a/);
  const old = harness.outcomes.get("loop-a");
  assert.equal(old.status, "stopped");
  assert.equal(old.superseded_by, "loop-b");
  assert.match(old.stop_reason, /superseded by loop-b/);
  assert.equal(harness.outcomes.get("loop-b").supersedes, "loop-a");
});

test("the metric floor gates success and requires a metric command", async () => {
  const harness = makeHarness();
  const outcomeStart = findTool(harness, "outcome_start");
  const outcomeCheck = findTool(harness, "outcome_check");
  const missing = await outcomeStart.handler({
    goal: "Floored", success: "s", verify_command: "true", metric_floor: 50,
  });
  assert.match(missing, /requires metric_command/);

  await outcomeStart.handler({
    goal: "Floored", success: "s", verify_command: "true",
    metric_command: "metric", metric_floor: 50, max_iterations: 3, name: "floored",
  });
  const low = await outcomeCheck.handler({ name: "floored" });
  assert.match(low, /^\[FAIL\]/);
  assert.match(low, /Metric floor not met \(score 42, floor 50\)/);
  assert.equal(harness.outcomes.get("floored").status, "active");
  assert.equal(harness.iterations.get("floored")[0].metric_floor_unmet, true);
});

test("frozen paths are enforced in supervised checks", async () => {
  const harness = makeHarness({ changedPaths: () => ["tests/test_x.py", "src/ok.js"] });
  const outcomeStart = findTool(harness, "outcome_start");
  const outcomeCheck = findTool(harness, "outcome_check");
  await outcomeStart.handler({
    goal: "Frozen", success: "s", verify_command: "true",
    frozen_paths: ["tests"], name: "frozen",
  });
  const checked = await outcomeCheck.handler({ name: "frozen" });
  assert.match(checked, /Frozen-path violation detected/);
  const goal = harness.outcomes.get("frozen");
  assert.equal(goal.status, "stopped");
  assert.match(goal.stop_reason, /frozen-path violation: tests\/test_x\.py/);
  const verification = harness.verifications[harness.verifications.length - 1];
  assert.equal(verification.exit_code, -1);
  assert.equal(verification.verified, false);
});

test("a first-pass success carries a vacuity caution", async () => {
  const harness = makeHarness();
  await findTool(harness, "outcome_start").handler({
    goal: "Instant", success: "s", verify_command: "true", name: "instant",
  });
  const checked = await findTool(harness, "outcome_check").handler({ name: "instant" });
  assert.match(checked, /confirm the verifier can fail/);
});

test("audit rechecks a finished outcome without mutating its history", async () => {
  const failingCommands = new Map();
  const harness = makeHarness({ failingCommands });
  const outcomeCheck = findTool(harness, "outcome_check");
  await findTool(harness, "outcome_start").handler({
    goal: "Audited", success: "s", verify_command: "check-flag", name: "audited",
  });
  const activeRefused = await outcomeCheck.handler({ name: "audited", audit: true });
  assert.match(activeRefused, /still active/);
  await outcomeCheck.handler({ name: "audited" });
  assert.equal(harness.outcomes.get("audited").status, "succeeded");

  failingCommands.set("check-flag", 1);
  const red = await outcomeCheck.handler({ name: "audited", audit: true });
  assert.match(red, /^\[FAIL\]/);
  assert.match(red, /Audit red/);
  const goal = harness.outcomes.get("audited");
  assert.equal(goal.evidence_stale, true);
  assert.equal(goal.status, "succeeded");
  assert.equal(goal.iteration_count, 1);
  const rows = harness.iterations.get("audited");
  assert.equal(rows[rows.length - 1].audit, true);
  assert.equal(rows[rows.length - 1].verified, false);

  failingCommands.delete("check-flag");
  const green = await outcomeCheck.handler({ name: "audited", audit: true });
  assert.match(green, /Audit green/);
  assert.equal(harness.outcomes.get("audited").evidence_stale, false);
});
