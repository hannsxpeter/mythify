import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";

import { registerQualityTools } from "../src/quality-tools.js";
import { captureLineage } from "../src/lineage-tools.js";


function register(state) {
  const tools = [];
  const isoNow = () => "2026-08-17T00:00:00.000Z";
  const verificationsPath = () => path.join(state, "verifications.jsonl");
  registerQualityTools(
    { registerTool(name, config, handler) { tools.push({ name, config, handler }); } },
    {
      guarded: (handler) => async (args) => handler(args || {}),
      resolveStateDir: () => state,
      slugify: (value) => String(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""),
      isoNow,
      writeJsonAtomic: (target, record) => {
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, JSON.stringify(record, null, 2) + "\n");
      },
      runShellCapture: (command) => {
        const run = spawnSync(command, { shell: true, encoding: "utf8" });
        return {
          exit_code: run.status ?? -1,
          duration_seconds: 0.001,
          stdout_tail: String(run.stdout || "").trim(),
          stderr_tail: String(run.stderr || "").trim(),
          verified: run.status === 0,
        };
      },
      appendJsonl: (target, record) => fs.appendFileSync(target, JSON.stringify(record) + "\n"),
      verificationsPath,
      captureLineage: (stateDir, parents) => captureLineage(stateDir, parents, isoNow),
    }
  );
  return async (name, args) => tools.find((entry) => entry.name === name).handler(args);
}


test("MCP maintainability review records structured material without verification", async (t) => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-quality-"));
  t.after(() => fs.rmSync(state, { recursive: true, force: true }));
  const call = register(state);
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


test("MCP blast-radius review derives proof from immutable linked verification", async (t) => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-blast-radius-"));
  t.after(() => fs.rmSync(project, { recursive: true, force: true }));
  const state = path.join(project, ".mythify");
  fs.mkdirSync(path.join(state, "reviews"), { recursive: true });
  fs.writeFileSync(path.join(project, ".gitignore"), ".mythify/\n");
  fs.writeFileSync(path.join(project, "tracked.txt"), "tracked\n");
  execFileSync("git", ["init", "-q"], { cwd: project });
  execFileSync("git", ["config", "user.email", "mythify@example.invalid"], { cwd: project });
  execFileSync("git", ["config", "user.name", "Mythify Test"], { cwd: project });
  execFileSync("git", ["add", ".gitignore", "tracked.txt"], { cwd: project });
  execFileSync("git", ["commit", "-qm", "baseline"], { cwd: project });
  const call = register(state);

  const created = await call("blast_radius_review_create", {
    status: "warn",
    changed_paths: ["tracked.txt"],
    safety_fact: "the payload stays parseable",
    proof_depth: 2,
    risks: [{
      failure_mode: "the parser rejects the payload",
      path: "tracked.txt",
      line: 1,
      likelihood: "medium",
      impact: "high",
      disposition: "unproven",
      check: "true",
    }],
    merge_command: "true",
    name: "payload-safety",
  });
  assert.match(created, /^\[OK\]/);
  const reviewPath = path.join(state, "reviews", "payload-safety.json");
  const storedBefore = fs.readFileSync(reviewPath, "utf8");
  const proved = await call("blast_radius_review_prove", { review: "payload-safety" });
  assert.match(proved, /^\[OK\] VERIFIED/);
  const view = JSON.parse(await call("blast_radius_review_status", {
    review: "payload-safety", format: "json",
  }));
  assert.equal(view.change_freshness.status, "current");
  assert.equal(view.safety_fact.status, "proven");
  assert.equal(view.safety_fact.proof_depth, 4);
  assert.match(view.safety_fact.verification_id, /^v-/);
  assert.equal(view.merge_gate.verified, true);
  assert.equal(fs.readFileSync(reviewPath, "utf8"), storedBefore);
});


test("MCP blast-radius review refuses proof after dirty-to-dirty movement", async (t) => {
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-blast-stale-"));
  t.after(() => fs.rmSync(project, { recursive: true, force: true }));
  const state = path.join(project, ".mythify");
  fs.mkdirSync(path.join(state, "reviews"), { recursive: true });
  fs.writeFileSync(path.join(project, ".gitignore"), ".mythify/\n");
  fs.writeFileSync(path.join(project, "tracked.txt"), "tracked\n");
  execFileSync("git", ["init", "-q"], { cwd: project });
  execFileSync("git", ["config", "user.email", "mythify@example.invalid"], { cwd: project });
  execFileSync("git", ["config", "user.name", "Mythify Test"], { cwd: project });
  execFileSync("git", ["add", ".gitignore", "tracked.txt"], { cwd: project });
  execFileSync("git", ["commit", "-qm", "baseline"], { cwd: project });
  fs.writeFileSync(path.join(project, "tracked.txt"), "first dirty state\n");
  const call = register(state);
  await call("blast_radius_review_create", {
    status: "warn", changed_paths: ["tracked.txt"], safety_fact: "the dirty change is safe",
    proof_depth: 1, merge_command: "true", name: "dirty-safety",
  });
  fs.writeFileSync(path.join(project, "tracked.txt"), "second dirty state\n");
  const refused = await call("blast_radius_review_prove", { review: "dirty-safety" });
  assert.match(refused, /^\[FAIL\].*worktree_digest_mismatch/);
  const view = JSON.parse(await call("blast_radius_review_status", {
    review: "dirty-safety", format: "json",
  }));
  assert.equal(view.change_freshness.status, "stale");
  assert.equal(view.safety_fact.status, "unproven");
});


test("MCP blast-radius proof honors the execution disable switch", async (t) => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-blast-disabled-"));
  t.after(() => fs.rmSync(state, { recursive: true, force: true }));
  const call = register(state);
  await call("blast_radius_review_create", {
    status: "warn", changed_paths: ["tracked.txt"], safety_fact: "execution is authorized",
    proof_depth: 1, merge_command: "true", name: "disabled-safety",
  });
  const previous = process.env.MYTHIFY_DISABLE_RUN;
  process.env.MYTHIFY_DISABLE_RUN = "1";
  try {
    const disabled = await call("blast_radius_review_prove", { review: "disabled-safety" });
    assert.match(disabled, /^\[FAIL\].*MYTHIFY_DISABLE_RUN=1/);
  } finally {
    if (previous === undefined) delete process.env.MYTHIFY_DISABLE_RUN;
    else process.env.MYTHIFY_DISABLE_RUN = previous;
  }
});
