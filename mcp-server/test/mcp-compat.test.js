import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { MCP_TOOL_COUNT, MCP_TOOL_NAMES } from "../src/surface-manifest.js";

const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));

test("MCP server serves the stateless 2026-07-28 protocol", async () => {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
  });
  const client = new Client(
    { name: "mythify-modern-protocol-test", version: "1.0.0" },
    { versionNegotiation: { mode: { pin: "2026-07-28" } } },
  );

  try {
    await client.connect(transport);
    const { tools } = await client.listTools();
    assert.equal(tools.length, MCP_TOOL_COUNT);
    assert.deepEqual(tools.map((tool) => tool.name).sort(), [...MCP_TOOL_NAMES].sort());
  } finally {
    await client.close();
  }
});
