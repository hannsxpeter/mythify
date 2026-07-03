import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  findGodauditsFile,
  findGodplansFile,
  godArtifactHasOpenTasks,
  godauditsSummary,
  godplansSummary,
  loadGodArtifact,
  parseGodDocument,
  parseGodFrontmatter,
} from "../src/godfiles-core.js";

const FIXTURES = fileURLToPath(new URL("../../tests/fixtures/godfiles/", import.meta.url));
const PLAN_FIXTURE = path.join(FIXTURES, "PLAN.mdx");
const AUDIT_FIXTURE = path.join(FIXTURES, "AUDIT.mdx");

function makeProject({ plan = false, audit = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "godfiles-"));
  if (plan) {
    fs.mkdirSync(path.join(root, ".godplans"));
    fs.copyFileSync(PLAN_FIXTURE, path.join(root, ".godplans", "PLAN.mdx"));
  }
  if (audit) {
    fs.mkdirSync(path.join(root, ".godaudits"));
    fs.copyFileSync(AUDIT_FIXTURE, path.join(root, ".godaudits", "AUDIT.mdx"));
  }
  return root;
}

test("plan fixture parses with counts, waves, and verify commands", () => {
  const digest = loadGodArtifact(PLAN_FIXTURE, "godplans");
  assert.equal(digest.status, "executing");
  assert.equal(digest.plan_version, 2);
  assert.deepEqual(digest.counts, { tasks_total: 5, tasks_done: 2, tasks_open: 3 });
  assert.equal(digest.counter_drift, false);
  assert.equal(digest.next_task.id, "GP-201");
  const byId = Object.fromEntries(digest.tasks.map((task) => [task.id, task]));
  assert.equal(byId["GP-201"].verify_command, "npm run db:migrate && npm run db:check");
  assert.deepEqual(byId["GP-201"].depends_on, ["GP-102"]);
  assert.equal(byId["GP-201"].wave, "2.1");
  assert.match(byId["GP-201"].acceptance, /email column has unique index/);
  assert.equal(byId["GP-102"].parallel, true);
  assert.equal(byId["GP-202"].superseded, true);
  assert.deepEqual(byId["GP-301"].files, []);
});

test("audit fixture parses findings, scores, and remediation tasks", () => {
  const digest = loadGodArtifact(AUDIT_FIXTURE, "godaudits");
  assert.equal(digest.status, "reported");
  assert.equal(digest.overall_score, 64);
  assert.equal(digest.verdict, "at risk");
  assert.equal(digest.plan_aware, true);
  assert.equal(digest.open_critical, 1);
  assert.equal(digest.open_high, 1);
  const byId = Object.fromEntries(digest.findings.map((finding) => [finding.id, finding]));
  assert.equal(byId["F-SEC-1"].severity, "Critical");
  assert.equal(byId["F-SEC-1"].remediation, "GA-101");
  assert.equal(byId["F-CODE-1"].status, "resolved");
  const tasks = Object.fromEntries(digest.tasks.map((task) => [task.id, task]));
  assert.deepEqual(tasks["GA-101"].fixes, ["F-SEC-1"]);
  assert.deepEqual(tasks["GA-301"].depends_on, ["GA-101", "GA-201"]);
});

test("fenced task lines are ignored and drift is detected", () => {
  const text = [
    "---",
    "name: drifty",
    "status: executing",
    "progress:",
    "  tasks_total: 9",
    "  tasks_done: 9",
    "---",
    "```bash",
    "- [ ] GP-999 [W9.9] Not a real task",
    "```",
    "- [ ] GP-101 Real task",
    "  - Verify: `true`",
  ].join("\n");
  const parsed = parseGodDocument(text);
  assert.deepEqual(parsed.tasks.map((task) => task.id), ["GP-101"]);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "godfiles-"));
  fs.mkdirSync(path.join(root, ".godplans"));
  fs.writeFileSync(path.join(root, ".godplans", "PLAN.mdx"), text);
  const summary = godplansSummary(root);
  assert.equal(summary.counter_drift, true);
  assert.match(summary.detail, /counters disagree/);
});

test("summaries report missing artifacts and md fallback discovery", () => {
  const empty = makeProject();
  assert.equal(godplansSummary(empty).status, "missing");
  assert.equal(godauditsSummary(empty).status, "missing");
  assert.equal(findGodplansFile(empty), null);
  const root = makeProject();
  fs.mkdirSync(path.join(root, ".godplans"));
  fs.copyFileSync(PLAN_FIXTURE, path.join(root, ".godplans", "PLAN.md"));
  assert.equal(path.basename(findGodplansFile(root)), "PLAN.md");
});

test("non-utf8 bytes never crash and load_error flags real failures", () => {
  const root = makeProject();
  fs.mkdirSync(path.join(root, ".godplans"));
  fs.writeFileSync(
    path.join(root, ".godplans", "PLAN.mdx"),
    Buffer.from("---\nname: caf\xe9\nstatus: executing\n---\n- [ ] GP-101 x\n  - Verify: `t`\n", "binary")
  );
  const summary = godplansSummary(root);
  assert.equal(summary.status, "executing");
  assert.equal(summary.present, true);
  assert.equal(loadGodArtifact("/nonexistent/PLAN.mdx", "godplans").load_error, true);
});

test("author status colliding with a sentinel still surfaces", () => {
  for (const sentinel of ["unrecognized", "unreadable", "missing"]) {
    const root = makeProject();
    fs.mkdirSync(path.join(root, ".godplans"));
    fs.writeFileSync(
      path.join(root, ".godplans", "PLAN.mdx"),
      `---\nname: x\nstatus: ${sentinel}\n---\n- [ ] GP-101 Task\n  - Verify: \`t\`\n`
    );
    const summary = godplansSummary(root);
    assert.equal(summary.present, true, sentinel);
    assert.equal(summary.tasks_total, 1, sentinel);
  }
});

test("BOM, bracket tokens, tilde fences, ints, and tabs mirror Python", () => {
  assert.equal(parseGodFrontmatter("\ufeff---\nname: withbom\n---\n").name, "withbom");
  for (const token of ["[Windows]", "[WIP]", "[W]"]) {
    const t = parseGodDocument(`### Wave 3.1\n- [ ] GP-310 ${token} Title\n  - Verify: \`t\`\n`).tasks[0];
    assert.equal(t.wave, "3.1", token);
    assert.ok(t.title.startsWith(token), token);
  }
  const fenced = parseGodDocument(
    "~~~\n- [ ] GP-901 phantom\n~~~\n````\n- [ ] GP-902 phantom\n````\n- [ ] GP-101 real\n  - Verify: `t`\n"
  );
  assert.deepEqual(fenced.tasks.map((x) => x.id), ["GP-101"]);
  const fm = parseGodFrontmatter("---\na: +5\nb: 1_000\nc: 42\n---\n");
  assert.equal(fm.a, "+5");
  assert.equal(fm.b, "1_000");
  assert.equal(fm.c, 42);
  const tab = parseGodDocument("- [ ] GP-101 Task\n\t- Verify: `npm test`\n").tasks[0];
  assert.equal(tab.verify_command, "npm test");
});

test("a field named after an Object property does not inject a key", () => {
  const t = parseGodDocument("- [ ] GP-101 Task\n  - Constructor: injected\n  - Verify: `t`\n").tasks[0];
  assert.equal(t.verify_command, "t");
  assert.ok(!Object.keys(t).some((k) => k === "constructor" || k.includes("Object")));
});

test("edge fixture parses identically to the parity contract", () => {
  const digest = loadGodArtifact(path.join(FIXTURES, "EDGE.mdx"), "godplans");
  const byId = Object.fromEntries(digest.tasks.map((t) => [t.id, t]));
  assert.deepEqual(Object.keys(byId).sort(), ["GP-101", "GP-102"]);
  assert.ok(byId["GP-101"].title.startsWith("[Windows]"));
  assert.equal(byId["GP-102"].verify_command, "npm test -- b");
});

test("summaries surface progress, criticals, and next task", () => {
  const root = makeProject({ plan: true, audit: true });
  const plan = godplansSummary(root);
  assert.equal(plan.status, "executing");
  assert.equal(plan.tasks_done, 2);
  assert.equal(plan.next_task_id, "GP-201");
  assert.match(plan.detail, /2\/5 tasks done/);
  const audit = godauditsSummary(root);
  assert.equal(audit.open_critical, 1);
  assert.match(audit.detail, /score 64 \(at risk\)/);
  assert.equal(godArtifactHasOpenTasks(plan), true);
  assert.equal(godArtifactHasOpenTasks(null), false);
  assert.equal(findGodauditsFile(root) !== null, true);
});
