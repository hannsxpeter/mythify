import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import {
  MEMORY_CATEGORIES,
  MEMORY_CLEAR_MCP_REFUSAL,
  MEMORY_DEFAULT_CATEGORY,
  MEMORY_OPERATION_REGISTRY,
  OPERATION_REGISTRY,
  getOperation,
} from "../src/operation-registry.js";

const registryPath = fileURLToPath(
  new URL("../../protocol/operation-registry.json", import.meta.url)
);

test("operation registry exports memory contract from protocol data", () => {
  const diskRegistry = JSON.parse(fs.readFileSync(registryPath, "utf8"));
  assert.deepEqual(OPERATION_REGISTRY, diskRegistry);
  assert.deepEqual(MEMORY_OPERATION_REGISTRY, diskRegistry.surfaces.memory);
  assert.deepEqual(MEMORY_CATEGORIES, ["fact", "decision", "discovery", "state"]);
  assert.equal(MEMORY_DEFAULT_CATEGORY, "fact");
  assert.equal(
    MEMORY_CLEAR_MCP_REFUSAL,
    diskRegistry.surfaces.memory.operations.memory_clear.mcp.refusal
  );
});

test("operation lookup returns the named surface operation", () => {
  const clear = getOperation("memory", "memory_clear");
  assert.equal(clear.kind, "mutating");
  assert.equal(clear.cli.command, "memory clear");
  assert.equal(clear.mcp.tool, "memory_clear");
  assert.equal(getOperation("missing", "memory_clear"), null);
  assert.equal(getOperation("memory", "missing"), null);
});
