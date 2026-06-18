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
    verificationsPath: () => "verifications",
    verificationRecordMatchesStep: (record, slug, stepId) =>
      record.plan === slug && record.step_id === stepId,
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
