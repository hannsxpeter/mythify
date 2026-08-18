import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";


const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));
const textOf = (result) => result.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");


test("MCP captures plan parent revisions and reports staleness", async (t) => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-lineage-state-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-lineage-home-"));
  t.after(() => {
    fs.rmSync(state, { recursive: true, force: true });
    fs.rmSync(home, { recursive: true, force: true });
  });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: { ...process.env, MYTHIFY_DIR: state, HOME: home },
  });
  const client = new Client({ name: "lineage-test", version: "1.0.0" });
  await client.connect(transport);
  t.after(async () => client.close());
  const call = async (name, args) => textOf(await client.callTool({ name, arguments: args }));

  assert.match(await call("design_create", { title: "Parent", problem: "Choose" }), /^\[OK\]/);
  assert.match(await call("plan_create", {
    goal: "Child", name: "child", parents: [{ kind: "design", id: "parent" }],
  }), /^\[OK\]/);
  let lineage = JSON.parse(await call("lineage_status", { kind: "plan", id: "child", format: "json" }));
  assert.equal(lineage.status, "current");
  assert.match(await call("design_add_alternative", {
    title: "Option", interface: "one method", design: "parent",
    call_sites: "caller.js", locality: "one module", migration_cost: "low",
    deletion_cost: "one file", reversal_evidence: "second consumer",
  }), /^\[OK\]/);
  lineage = JSON.parse(await call("lineage_status", { kind: "plan", id: "child", format: "json" }));
  assert.equal(lineage.status, "stale");
  assert.equal(lineage.parents[0].status, "stale");
  const dashboard = JSON.parse((await call("workflow_status", { format: "json" })).replace(/^\[OK\]\s*/, ""));
  assert.equal(dashboard.active_plan.lineage.status, "stale");
  const harness = JSON.parse((await call("evidence_harness", { format: "json" })).replace(/^\[OK\]\s*/, ""));
  assert.equal(harness.active_plan.lineage.status, "stale");
  assert.match(await call("lineage_attach", {
    kind: "design", id: "parent", parents: [{ kind: "design", id: "parent" }],
  }), /^\[FAIL\].*cannot be its own lineage parent/);

  const marker = path.join(state, "command-ran");
  const command = `${JSON.stringify(process.execPath)} -e "require('node:fs').writeFileSync(process.argv[1], 'ran')" ${JSON.stringify(marker)}`;
  assert.match(await call("verify_run", {
    command,
    parents: [{ kind: "design", id: "missing" }],
  }), /^\[FAIL\] Invalid lineage/);
  assert.equal(fs.existsSync(marker), false);
  assert.equal(fs.existsSync(path.join(state, "verification-artifacts")), false);
});


test("MCP lineage lookups cannot escape state directories", async (t) => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-lineage-path-state-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-lineage-path-home-"));
  const outsideName = `${path.basename(state)}-outside`;
  const outsidePlan = path.resolve(state, "plans", "..", "..", `${outsideName}.json`);
  t.after(() => {
    fs.rmSync(state, { recursive: true, force: true });
    fs.rmSync(home, { recursive: true, force: true });
    fs.rmSync(outsidePlan, { force: true });
  });
  fs.mkdirSync(path.dirname(outsidePlan), { recursive: true });
  fs.writeFileSync(outsidePlan, JSON.stringify({
    name: outsideName,
    goal: "Outside lineage sentinel",
    steps: [],
  }));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: { ...process.env, MYTHIFY_DIR: state, HOME: home },
  });
  const client = new Client({ name: "lineage-path-test", version: "1.0.0" });
  await client.connect(transport);
  t.after(async () => client.close());
  const result = await client.callTool({
    name: "lineage_status",
    arguments: { kind: "plan", id: `../../${outsideName}` },
  });
  const output = textOf(result);
  assert.match(output, /^\[FAIL\]/);
  assert.doesNotMatch(output, /Outside lineage sentinel/);
});
