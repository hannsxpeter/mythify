// Smoke test for the Mythify MCP server.
// Spawns the real server over stdio with MYTHIFY_DIR and HOME pointed at
// fresh temp directories, exercises the tool surface through the SDK Client,
// then asserts the on-disk state formats byte-level field contracts.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));

const EXPECTED_TOOLS = [
  "memory_store",
  "memory_recall",
  "memory_clear",
  "lesson_record",
  "lesson_recall",
  "plan_create",
  "plan_add_step",
  "plan_update_step",
  "plan_status",
  "verify_run",
  "verify_claim",
  "reflect",
  "fanout_start",
  "fanout_status",
  "fanout_results",
];

function textOf(result) {
  assert.ok(Array.isArray(result.content), "tool result has a content array");
  const texts = result.content.filter((c) => c.type === "text").map((c) => c.text);
  assert.ok(texts.length > 0, "tool result has at least one text block");
  return texts.join("\n");
}

test("mythify MCP server smoke test", async (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-smoke-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-smoke-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: { ...process.env, MYTHIFY_DIR: stateDir, HOME: homeDir },
  });
  const client = new Client({ name: "mythify-smoke-test", version: "2.0.0" });
  await client.connect(transport);

  try {
    await t.test("tools/list returns exactly the 15 tools", async () => {
      const { tools } = await client.listTools();
      const names = tools.map((tool) => tool.name).sort();
      assert.deepEqual(names, [...EXPECTED_TOOLS].sort());
    });

    await t.test("memory_store then memory_recall round-trips a value", async () => {
      const stored = textOf(
        await client.callTool({
          name: "memory_store",
          arguments: { key: "color", value: "blue", category: "fact" },
        })
      );
      assert.ok(stored.startsWith("[OK]"), `store reports [OK]: ${stored}`);

      const recalled = textOf(
        await client.callTool({
          name: "memory_recall",
          arguments: { query: "blue" },
        })
      );
      assert.ok(recalled.startsWith("[OK]"), `recall reports [OK]: ${recalled}`);
      assert.match(recalled, /color/, "recall finds the stored key");
      assert.match(recalled, /blue/, "recall finds the stored value");
    });

    await t.test("plan_update_step enforces the evidence rule", async () => {
      const created = textOf(
        await client.callTool({
          name: "plan_create",
          arguments: {
            goal: "Smoke goal",
            steps: [{ title: "First step", success_criteria: "exit code is zero" }],
          },
        })
      );
      assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);

      const refused = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: { step_id: 1, status: "completed" },
        })
      );
      assert.ok(refused.startsWith("[FAIL]"), `refusal starts with [FAIL]: ${refused}`);
      assert.match(refused, /Evidence required/, "refusal explains the evidence rule");

      const planAfterRefusal = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "smoke-goal.json"), "utf8")
      );
      assert.equal(
        planAfterRefusal.steps[0].status,
        "pending",
        "refused update leaves the step pending"
      );

      const accepted = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "command exited 0 as required",
          },
        })
      );
      assert.ok(accepted.startsWith("[OK]"), `update with result succeeds: ${accepted}`);
    });

    await t.test("verify_run reports VERIFIED on exit 0 and UNVERIFIED otherwise", async () => {
      const passed = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(0)"', claim: "node can exit zero" },
        })
      );
      assert.ok(passed.startsWith("[OK]"), `passing run reports [OK]: ${passed}`);
      assert.match(passed, /VERIFIED/, "passing run is VERIFIED");
      assert.doesNotMatch(passed, /UNVERIFIED/, "passing run is not UNVERIFIED");

      const failed = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(3)"' },
        })
      );
      assert.ok(failed.startsWith("[FAIL]"), `failing run reports [FAIL]: ${failed}`);
      assert.match(failed, /UNVERIFIED/, "failing run is UNVERIFIED");
      assert.match(failed, /exit 3/, "failing run reports the exit code");
    });

    await t.test("memory_clear with no arguments refuses", async () => {
      const refused = textOf(
        await client.callTool({ name: "memory_clear", arguments: {} })
      );
      assert.ok(refused.startsWith("[FAIL]"), `clear-all refusal starts with [FAIL]: ${refused}`);
      assert.match(refused, /confirm_clear_all/, "refusal explains the guard");

      const stillThere = textOf(
        await client.callTool({ name: "memory_recall", arguments: { query: "color" } })
      );
      assert.match(stillThere, /blue/, "refused clear left memory intact");
    });

    await t.test("on-disk formats match the shared contract field names", async () => {
      const memory = JSON.parse(
        fs.readFileSync(path.join(stateDir, "memory.json"), "utf8")
      );
      assert.deepEqual(Object.keys(memory).sort(), ["entries", "metadata"]);
      assert.ok(Array.isArray(memory.entries), "entries is an array");
      assert.equal(memory.entries.length, 1, "one memory entry persisted");
      const entry = memory.entries[0];
      assert.deepEqual(
        Object.keys(entry).sort(),
        ["category", "key", "timestamp", "value"],
        "memory entry has the exact contract fields"
      );
      assert.equal(entry.key, "color");
      assert.equal(entry.value, "blue");
      assert.equal(entry.category, "fact");
      assert.equal(typeof entry.timestamp, "string");
      assert.deepEqual(
        Object.keys(memory.metadata).sort(),
        ["created", "last_updated", "total_entries"],
        "memory metadata has the exact contract fields"
      );
      assert.equal(memory.metadata.total_entries, 1);

      const plan = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "smoke-goal.json"), "utf8")
      );
      assert.deepEqual(
        Object.keys(plan).sort(),
        ["created", "goal", "last_updated", "name", "steps"],
        "plan file has the exact contract fields"
      );
      assert.equal(plan.name, "smoke-goal");
      assert.equal(plan.goal, "Smoke goal");
      assert.ok(Array.isArray(plan.steps), "steps is an array");
      assert.equal(plan.steps.length, 1);
      const step = plan.steps[0];
      assert.deepEqual(
        Object.keys(step).sort(),
        ["id", "result", "status", "success_criteria", "title", "updated_at"],
        "updated step has the exact contract fields including updated_at"
      );
      assert.equal(step.id, 1);
      assert.equal(step.title, "First step");
      assert.equal(step.success_criteria, "exit code is zero");
      assert.equal(step.status, "completed");
      assert.equal(step.result, "command exited 0 as required");
      assert.equal(typeof step.updated_at, "string");

      const activeSlug = fs
        .readFileSync(path.join(stateDir, "plans", "active"), "utf8")
        .trim();
      assert.equal(activeSlug, "smoke-goal", "active pointer holds the plan slug");
    });
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});
