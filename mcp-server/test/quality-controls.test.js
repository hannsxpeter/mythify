import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { registerQualityTools } from "../src/quality-tools.js";


test("MCP maintainability review records structured material without verification", async (t) => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-quality-"));
  t.after(() => fs.rmSync(state, { recursive: true, force: true }));
  const tools = [];
  registerQualityTools(
    { registerTool(name, config, handler) { tools.push({ name, config, handler }); } },
    {
      guarded: (handler) => async (args) => handler(args || {}),
      resolveStateDir: () => state,
      slugify: (value) => String(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""),
      isoNow: () => "2026-08-17T00:00:00.000Z",
      writeJsonAtomic: (target, record) => {
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, JSON.stringify(record, null, 2) + "\n");
      },
    }
  );
  const call = async (name, args) => tools.find((entry) => entry.name === name).handler(args);
  const created = await call("maintainability_review_create", {
    status: "warn", changed_paths: ["src/example.js"], interface_depth: "shallow",
    locality: "two modules", seam_count: "three", deletion_cost: "wide",
    invalid_state_exclusion: "partial", test_validity: "covered",
    findings: [{ path: "src/example.js", line: 12, detail: "wrapper duplicates interface" }],
    name: "seam-review",
  });
  assert.match(created, /^\[OK\]/);
  const record = JSON.parse(await call("maintainability_review_status", { review: "seam-review", format: "json" }));
  assert.equal(record.evidence_status, "material_not_verification");
  assert.equal(Object.hasOwn(record, "verified"), false);
  assert.deepEqual(record.findings[0], {
    path: "src/example.js", line: 12, detail: "wrapper duplicates interface",
  });

  await call("maintainability_review_create", {
    status: "warn", changed_paths: ["src/other.js"], interface_depth: "shallow",
    locality: "two modules", seam_count: "three", deletion_cost: "wide",
    invalid_state_exclusion: "partial", test_validity: "covered",
    findings: [{ path: "src/other.js", line: 8, detail: "wrapper duplicates interface" }],
    name: "seam-review-two",
  });
  const repeated = JSON.parse(await call("maintainability_review_status", { review: "seam-review-two", format: "json" }));
  assert.equal(repeated.eval_proposal_recommended, true);
  assert.deepEqual(repeated.eval_scenario_candidates[0].source_reviews, ["seam-review"]);
  assert.equal(Object.hasOwn(repeated, "verified"), false);

  await call("maintainability_review_create", {
    status: "pass", changed_paths: ["src/resolved.js"], interface_depth: "deep",
    locality: "one module", seam_count: "one", deletion_cost: "local",
    invalid_state_exclusion: "complete", test_validity: "covered",
    findings: [{ path: "src/resolved.js", line: 5, detail: "wrapper duplicates interface" }],
    name: "resolved-review",
  });
  const resolved = JSON.parse(await call("maintainability_review_status", { review: "resolved-review", format: "json" }));
  assert.equal(resolved.eval_proposal_recommended, false);
  assert.deepEqual(resolved.eval_scenario_candidates, []);
});
