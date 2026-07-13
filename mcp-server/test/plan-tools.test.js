import test from "node:test";
import assert from "node:assert/strict";
import {
  PLAN_TOOL_NAMES,
  registerPlanTools,
} from "../src/plan-tools.js";

function makeHarness() {
  const registered = [];
  const plans = new Map();
  const verifications = [];
  let active = null;

  const server = {
    registerTool(name, config, handler) {
      registered.push({ name, config, handler });
    },
  };

  registerPlanTools(server, {
    guarded: (handler) => async (args) => handler(args || {}),
    slugify: (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""),
    uniquePlanSlug: (base) => plans.has(base) ? `${base}-2` : base,
    isoNow: () => "2026-06-16T00:00:00.000Z",
    writeJsonAtomic: (target, plan) => {
      const slug = target.slice("plans:".length);
      plans.set(slug, plan);
    },
    planPath: (slug) => `plans:${slug}`,
    setActiveSlug: (slug) => {
      active = slug;
    },
    stepLine: (step) => {
      let line = `[${step.status}] ${step.id}. ${step.title}`;
      if (step.success_criteria) {
        line += ` (criteria: ${step.success_criteria})`;
      }
      if (step.result) {
        line += `\n    result: ${step.result}`;
      }
      return line;
    },
    resolvePlan: (name) => {
      const slug = name || active;
      const plan = plans.get(slug);
      if (!plan) {
        return { error: "[FAIL] No active plan. Create one with plan_create, or pass a plan name." };
      }
      return { slug, plan };
    },
    savePlan: (slug, plan) => {
      plan.last_updated = "2026-06-16T00:00:00.000Z";
      plans.set(slug, plan);
    },
    strictStepEvidenceEnabled: () => true,
    readJsonlSince: () => verifications,
    readJsonl: () => verifications,
    verificationsPath: () => "verifications",
    verificationRecordMatchesStep: (record, slug, stepId) => {
      const hasPlan = Object.prototype.hasOwnProperty.call(record, "plan");
      const hasStep = Object.prototype.hasOwnProperty.call(record, "step_id");
      return !hasPlan && !hasStep ? true : record.plan === slug && record.step_id === stepId;
    },
    timestampAtOrAfter: () => true,
    verificationRecordHasExplicitStepContext: (record, slug, stepId) =>
      record.plan === slug && record.step_id === stepId,
    nextPendingText: (plan) => {
      const pending = (plan.steps || []).find((step) => step.status === "pending");
      return pending ? `Next pending step: [ ] ${pending.id}. ${pending.title}` : "No pending steps remain.";
    },
    readActiveSlug: () => active,
    mcpFrontDoorNote: " Route first.",
  });

  return { registered, plans, verifications, get active() { return active; } };
}

test("plan tool registrar wires plan handlers and strict evidence", async () => {
  const harness = makeHarness();
  const { registered, plans, verifications } = harness;

  assert.deepEqual(registered.map((entry) => entry.name), PLAN_TOOL_NAMES);
  const planCreate = registered.find((entry) => entry.name === "plan_create");
  assert.ok(planCreate.config.inputSchema.steps);
  assert.ok(planCreate.config.inputSchema.horizon);

  const createResult = await planCreate.handler({
    goal: "Ship plan",
    name: "Release Plan",
    steps: [{ title: "Build", success_criteria: "tests pass" }],
  });
  assert.match(createResult, /^\[OK\] Created plan "release-plan"/);
  assert.equal(harness.active, "release-plan");
  assert.equal(plans.get("release-plan").steps[0].title, "Build");

  const planAddStep = registered.find((entry) => entry.name === "plan_add_step");
  const addResult = await planAddStep.handler({
    title: "Verify",
    success_criteria: "gate passes",
  });
  assert.match(addResult, /^\[OK\] Added step 2/);

  const planUpdateStep = registered.find((entry) => entry.name === "plan_update_step");
  const inProgress = await planUpdateStep.handler({ step_id: 2, status: "in_progress" });
  assert.match(inProgress, /Step 2/);
  assert.equal(plans.get("release-plan").steps[1].status, "in_progress");

  const missingResult = await planUpdateStep.handler({ step_id: 2, status: "completed" });
  assert.match(missingResult, /^\[FAIL\] Evidence required/);

  const missingVerification = await planUpdateStep.handler({
    step_id: 2,
    status: "completed",
    result: "tests pass",
  });
  assert.match(missingVerification, /^\[FAIL\] Verified evidence required/);

  verifications.push({
    kind: "executed",
    verified: true,
    exit_code: 0,
    command: "manual check",
    timestamp: "2026-06-16T00:00:01.000Z",
    plan: "release-plan",
    step_id: 2,
  });
  const completed = await planUpdateStep.handler({
    step_id: 2,
    status: "completed",
    result: "tests pass",
  });
  assert.match(completed, /Next pending step/);
  assert.equal(plans.get("release-plan").steps[1].result, "tests pass");

  const planStatus = registered.find((entry) => entry.name === "plan_status");
  const status = await planStatus.handler({});
  assert.match(status, /Progress: 1\/2 steps completed/);
  assert.match(status, /tests pass/);
});

test("stored step verifier rejects unrelated and inconsistent evidence", async () => {
  const harness = makeHarness();
  const planCreate = harness.registered.find((entry) => entry.name === "plan_create");
  const planUpdateStep = harness.registered.find((entry) => entry.name === "plan_update_step");
  await planCreate.handler({
    goal: "Bound verifier",
    steps: [{ title: "Bound", verify_command: "expected command" }],
  });
  await planUpdateStep.handler({ step_id: 1, status: "in_progress" });
  harness.verifications.push({
    kind: "executed", verified: true, exit_code: 0, command: "wrong command",
    timestamp: "2026-06-16T00:00:01.000Z", plan: "bound-verifier", step_id: 1,
  });
  const wrong = await planUpdateStep.handler({ step_id: 1, status: "completed", result: "done" });
  assert.match(wrong, /^\[FAIL\] Verified evidence required/);
  harness.verifications.push({
    kind: "executed", verified: true, exit_code: 9, command: "expected command",
    timestamp: "2026-06-16T00:00:02.000Z", plan: "bound-verifier", step_id: 1,
  });
  const inconsistent = await planUpdateStep.handler({ step_id: 1, status: "completed", result: "done" });
  assert.match(inconsistent, /^\[FAIL\] Verified evidence required/);
  harness.verifications.push({
    kind: "executed", verified: true, exit_code: 0, command: "expected command",
    timestamp: "2026-06-16T00:00:03.000Z", plan: "bound-verifier", step_id: 1,
  });
  const completed = await planUpdateStep.handler({ step_id: 1, status: "completed", result: "done" });
  assert.match(completed, /^\[OK\]/);
});

test("restarting a step invalidates earlier same-second evidence", async () => {
  const harness = makeHarness();
  const planCreate = harness.registered.find((entry) => entry.name === "plan_create");
  const update = harness.registered.find((entry) => entry.name === "plan_update_step");
  await planCreate.handler({
    goal: "Restart cursor",
    steps: [{ title: "Restarted", verify_command: "true" }],
  });
  await update.handler({ step_id: 1, status: "in_progress" });
  harness.verifications.push({
    kind: "executed", verified: true, exit_code: 0, command: "true",
    timestamp: "2026-06-16T00:00:00.000Z", plan: "restart-cursor", step_id: 1,
  });
  await update.handler({ step_id: 1, status: "pending" });
  await update.handler({ step_id: 1, status: "in_progress" });
  const refused = await update.handler({ step_id: 1, status: "completed", result: "old" });
  assert.match(refused, /^\[FAIL\] Verified evidence required/);
  harness.verifications.push({
    kind: "executed", verified: true, exit_code: 0, command: "true",
    timestamp: "2026-06-16T00:00:00.000Z", plan: "restart-cursor", step_id: 1,
  });
  const completed = await update.handler({ step_id: 1, status: "completed", result: "fresh" });
  assert.match(completed, /^\[OK\]/);
});

test("imported strict-context plan rejects legacy context-free evidence", async () => {
  const harness = makeHarness();
  harness.plans.set("imported-plan", {
    name: "imported-plan",
    goal: "Imported",
    strict_context: true,
    created: "2026-06-16T00:00:00.000Z",
    last_updated: "2026-06-16T00:00:00.000Z",
    steps: [{
      id: 1,
      title: "Imported step",
      success_criteria: "passes",
      status: "in_progress",
      result: null,
      verification_cursor: 0,
      updated_at: "2026-06-16T00:00:00.000Z",
    }],
  });
  harness.verifications.push({
    kind: "executed",
    verified: true,
    exit_code: 0,
    command: "true",
    timestamp: "2026-06-16T00:00:01.000Z",
  });
  const update = harness.registered.find((entry) => entry.name === "plan_update_step");
  const legacy = await update.handler({
    plan: "imported-plan",
    step_id: 1,
    status: "completed",
    result: "legacy context-free evidence",
  });
  assert.match(legacy, /^\[FAIL\] Verified evidence required/);
  harness.verifications.push({
    kind: "executed",
    verified: true,
    exit_code: 0,
    command: "true",
    timestamp: "2026-06-16T00:00:02.000Z",
    plan: "imported-plan",
    step_id: 1,
  });
  const scoped = await update.handler({
    plan: "imported-plan",
    step_id: 1,
    status: "completed",
    result: "scoped evidence",
  });
  assert.match(scoped, /^\[OK\]/);
});

test("plan_create can generate default horizon steps", async () => {
  const harness = makeHarness();
  const { registered, plans } = harness;
  const planCreate = registered.find((entry) => entry.name === "plan_create");

  const createResult = await planCreate.handler({
    goal: "Horizon plan",
    horizon: 3,
  });

  assert.match(createResult, /with 3 steps/);
  assert.equal(plans.get("horizon-plan").steps.length, 3);
  assert.equal(
    plans.get("horizon-plan").steps[0].title,
    "Confirm goal, done criteria, and non-goals"
  );
});

test("plan_create rejects horizon with explicit steps", async () => {
  const harness = makeHarness();
  const planCreate = harness.registered.find((entry) => entry.name === "plan_create");

  const createResult = await planCreate.handler({
    goal: "Mixed plan",
    steps: [{ title: "Explicit" }],
    horizon: 20,
  });

  assert.match(createResult, /^\[FAIL\] horizon can only be used/);
});

test("plan_status reports no active plan without mutating state", async () => {
  const harness = makeHarness();
  const planStatus = harness.registered.find((entry) => entry.name === "plan_status");
  const status = await planStatus.handler({});
  assert.equal(status, "[OK] No active plan yet. Create one with plan_create.");
  assert.equal(harness.active, null);
});

test("plan tool registrar rejects missing required deps", () => {
  assert.throws(
    () => registerPlanTools({ registerTool() {} }, {}),
    /requires deps\.guarded/
  );
});
