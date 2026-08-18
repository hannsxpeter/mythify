import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { registerDesignTools } from "../src/design-tools.js";
import { registerPlanTools } from "../src/plan-tools.js";


function planHarness() {
  const registered = [];
  const plans = new Map();
  let active = null;
  registerPlanTools(
    { registerTool(name, config, handler) { registered.push({ name, config, handler }); } },
    {
      guarded: (handler) => async (args) => handler(args || {}),
      slugify: (value) => String(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""),
      uniquePlanSlug: (base) => base,
      isoNow: () => "2026-08-17T00:00:00.000Z",
      writeJsonAtomic: (target, record) => plans.set(target.slice(6), record),
      planPath: (slug) => `plans:${slug}`,
      setActiveSlug: (slug) => { active = slug; },
      stepLine: (step) => `${step.id}. ${step.title}`,
      resolvePlan: (name) => ({ slug: name || active, plan: plans.get(name || active) }),
      savePlan: (slug, plan) => plans.set(slug, plan),
      strictStepEvidenceEnabled: () => false,
      readJsonlSince: () => [],
      readJsonl: () => [],
      verificationsPath: () => "verifications",
      verificationRecordMatchesStep: () => false,
      timestampAtOrAfter: () => true,
      verificationRecordHasExplicitStepContext: () => false,
      nextPendingText: () => "none",
      readActiveSlug: () => active,
      evidenceMovedSinceRun: () => null,
      currentProvenance: () => ({}),
      captureLineage: () => null,
      resolveStateDir: () => "/state",
    }
  );
  return { registered, plans };
}


test("MCP plan_create enforces design-heavy vertical slices", async () => {
  const harness = planHarness();
  const create = harness.registered.find((entry) => entry.name === "plan_create");
  const rejected = await create.handler({
    goal: "Missing",
    archetype: "design-heavy",
    steps: [{ title: "Build", phase: "build" }],
  });
  assert.match(rejected, /^\[FAIL\].*vertical_slice/);
  const accepted = await create.handler({
    goal: "Vertical",
    archetype: "design-heavy",
    design: "architecture",
    steps: [{
      title: "Build",
      phase: "build",
      vertical_slice: {
        result: "Runnable command",
        files: ["src/command.js"],
        automated_checks: ["node --test"],
        manual_checks: [],
      },
    }],
  });
  assert.match(accepted, /^\[OK\]/);
  const plan = harness.plans.get("vertical");
  assert.equal(plan.archetype, "design-heavy");
  assert.equal(plan.steps[0].vertical_slice.result, "Runnable command");
});


test("MCP design tools create alternatives and keep approval material-only", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-design-tools-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const registered = [];
  const state = path.join(root, ".mythify");
  const writeJsonAtomic = (target, record) => {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(record, null, 2) + "\n");
  };
  registerDesignTools(
    { registerTool(name, config, handler) { registered.push({ name, config, handler }); } },
    {
      guarded: (handler) => async (args) => handler(args || {}),
      resolveStateDir: () => state,
      slugify: (value) => String(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""),
      isoNow: () => "2026-08-17T00:00:00.000Z",
      writeJsonAtomic,
      writeTextAtomic: (target, text) => {
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, text);
      },
      readJsonRecover: (target, fallback) => {
        try { return JSON.parse(fs.readFileSync(target, "utf8")); } catch { return fallback(); }
      },
      captureLineage: () => null,
    }
  );
  const call = async (name, args) => registered.find((entry) => entry.name === name).handler(args);
  assert.match(await call("design_create", { title: "Boundary", problem: "Wide seam" }), /^\[OK\]/);
  const alternativeFields = {
    call_sites: "src/caller.js", locality: "one module", migration_cost: "low",
    deletion_cost: "one file", reversal_evidence: "second consumer",
  };
  assert.match(await call("design_add_alternative", {
    title: "Deep module", interface: "one command", select: true, ...alternativeFields,
  }), /^\[OK\]/);
  assert.match(await call("design_add_alternative", {
    title: "Adapter", interface: "adapter object", ...alternativeFields,
  }), /^\[OK\]/);
  assert.match(await call("design_approve", { note: "Reviewed" }), /^\[OK\]/);
  const record = JSON.parse(await call("design_status", { format: "json" }));
  assert.equal(record.status, "approved");
  assert.equal(record.selected_alternative, "A1");
  assert.equal(Object.hasOwn(record, "verified"), false);
});
