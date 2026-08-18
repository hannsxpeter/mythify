import assert from "node:assert/strict";
import test from "node:test";

import {
  createProfiledRegistrar,
  registerToolProfileStatus,
  resolveToolProfile,
} from "../src/tool-profiles.js";

function fakeServer() {
  const tools = new Map();
  return {
    tools,
    registerTool(name, config, handler) {
      tools.set(name, { config, handler });
    },
  };
}

const guarded = (handler) => async (args) => ({
  content: [{ type: "text", text: await handler(args || {}) }],
});

test("core profile selectively registers tools and reports its budget", async () => {
  const raw = fakeServer();
  const selection = resolveToolProfile({ MYTHIFY_MCP_TOOL_PROFILE: "core" });
  const server = createProfiledRegistrar(raw, selection);
  server.registerTool("workflow_route", { description: "route" }, async () => ({}));
  server.registerTool("plan_create", { description: "plan" }, async () => ({}));
  registerToolProfileStatus(server, { guarded, selection });

  assert.deepEqual([...raw.tools.keys()], ["workflow_route", "tool_profile_status"]);
  const result = await raw.tools.get("tool_profile_status").handler({});
  const status = JSON.parse(result.content[0].text);
  assert.equal(status.selected_profile, "core");
  assert.equal(status.registered_tool_count, 2);
  assert.ok(status.description_bytes > 5);
  assert.equal(status.quality_claim, "none");
});

test("full is the default and includes the complete canonical surface", () => {
  const selection = resolveToolProfile({});
  assert.equal(selection.selected, "full");
  assert.equal(selection.allowed.size, selection.full_tool_count);
  assert.equal(selection.full_tool_count, 60);
});

test("unknown profiles fail closed", () => {
  assert.throws(
    () => resolveToolProfile({ MYTHIFY_MCP_TOOL_PROFILE: "missing" }),
    /Unknown MCP tool profile/
  );
});
