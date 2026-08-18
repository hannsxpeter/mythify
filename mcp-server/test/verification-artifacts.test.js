import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";


const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));


function textOf(result) {
  return result.content.filter((item) => item.type === "text").map((item) => item.text).join("\n");
}


test("MCP verify_run retains redacted artifacts and supports explicit full output", async (t) => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-verification-artifacts-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-verification-home-"));
  t.after(() => {
    fs.rmSync(state, { recursive: true, force: true });
    fs.rmSync(home, { recursive: true, force: true });
  });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: { ...process.env, MYTHIFY_DIR: state, HOME: home },
  });
  const client = new Client({ name: "verification-artifact-test", version: "1.0.0" });
  await client.connect(transport);
  t.after(async () => client.close());

  const secret = "sk-abcdefghijklmnopqrstuvwxyz123456";
  const command = `${JSON.stringify(process.execPath)} -e ${JSON.stringify(`console.log("value ${secret}")`)}`;
  const compact = await client.callTool({ name: "verify_run", arguments: { command } });
  assert.match(textOf(compact), /^\[OK\] VERIFIED:/);
  assert.doesNotMatch(textOf(compact), /full artifact/);
  const records = fs.readFileSync(path.join(state, "verifications.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
  const record = records[0];
  assert.ok(record.id.startsWith("v-"));
  const stdoutPath = path.join(state, record.artifacts.stdout.path);
  assert.equal(fs.existsSync(stdoutPath), true);
  const retained = fs.readFileSync(stdoutPath, "utf8");
  assert.doesNotMatch(retained, new RegExp(secret));
  assert.match(retained, /\[REDACTED\]/);
  assert.equal(record.artifacts.stdout.redacted, true);
  assert.equal(record.artifacts.stdout.truncated, false);

  const full = await client.callTool({
    name: "verify_run",
    arguments: { command: `${JSON.stringify(process.execPath)} -e ${JSON.stringify('console.log("full output")')}`, output: "full" },
  });
  assert.match(textOf(full), /--- stdout \(full artifact\) ---/);
  assert.match(textOf(full), /full output/);
});

test("MCP compact verification reports a display-only test count", async (t) => {
  const state = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-verification-count-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-verification-count-home-"));
  t.after(() => {
    fs.rmSync(state, { recursive: true, force: true });
    fs.rmSync(home, { recursive: true, force: true });
  });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: { ...process.env, MYTHIFY_DIR: state, HOME: home },
  });
  const client = new Client({ name: "verification-count-test", version: "1.0.0" });
  await client.connect(transport);
  t.after(async () => client.close());

  const command = `${JSON.stringify(process.execPath)} -e ${JSON.stringify('console.log("# tests 23")')}`;
  const result = await client.callTool({ name: "verify_run", arguments: { command } });
  assert.match(textOf(result), /23 tests/);
  const record = JSON.parse(fs.readFileSync(path.join(state, "verifications.jsonl"), "utf8").trim());
  assert.equal(record.test_count, 23);
  assert.equal(record.verified, true);
});
