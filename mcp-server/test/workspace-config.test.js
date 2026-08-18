import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { loadWorkspaceConfig, registerWorkspaceTools } from "../src/workspace-tools.js";

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-workspace-"));
  const state = path.join(root, ".mythify");
  fs.mkdirSync(state);
  fs.mkdirSync(path.join(root, ".git"));
  return { root, state };
}

test("workspace config merges local paths without weakening shared boundaries", () => {
  const { root, state } = fixture();
  fs.mkdirSync(path.join(root, "service", ".git"), { recursive: true });
  fs.writeFileSync(path.join(state, "workspace.json"), JSON.stringify({
    task_isolation: "none",
    frozen_paths: ["protocol"],
    authorization: { approval: true },
    repositories: [{ id: "app", path: ".", primary: true }],
  }));
  fs.writeFileSync(path.join(state, "workspace.local.json"), JSON.stringify({
    task_isolation: "worktree",
    frozen_paths: ["docs"],
    repositories: [{ id: "app", path: "service" }],
  }));
  const result = loadWorkspaceConfig(state);
  assert.equal(result.configuration.task_isolation, "worktree");
  assert.deepEqual(result.configuration.frozen_paths, ["protocol", "docs"]);
  assert.equal(
    result.configuration.repositories[0].resolved_path,
    fs.realpathSync.native(path.join(root, "service"))
  );
  assert.equal(result.quality_claim, "none");
});

test("workspace config rejects weakened isolation and escaped paths", () => {
  const weakened = fixture();
  fs.writeFileSync(path.join(weakened.state, "workspace.json"), JSON.stringify({
    task_isolation: "worktree",
    repositories: [{ id: "app", path: "." }],
  }));
  fs.writeFileSync(path.join(weakened.state, "workspace.local.json"), JSON.stringify({ task_isolation: "none" }));
  assert.throws(() => loadWorkspaceConfig(weakened.state), /may not weaken task_isolation/);

  const escaped = fixture();
  fs.writeFileSync(path.join(escaped.state, "workspace.json"), JSON.stringify({ repositories: [{ id: "app", path: ".." }] }));
  assert.throws(() => loadWorkspaceConfig(escaped.state), /escapes workspace root/);
});

test("workspace config rejects symlink escapes", () => {
  const { root, state } = fixture();
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-workspace-outside-"));
  fs.mkdirSync(path.join(outside, ".git"));
  fs.symlinkSync(outside, path.join(root, "linked-outside"), "dir");
  fs.writeFileSync(path.join(state, "workspace.json"), JSON.stringify({
    repositories: [{ id: "outside", path: "linked-outside" }],
  }));
  assert.throws(() => loadWorkspaceConfig(state), /escapes workspace root/);
  fs.rmSync(outside, { recursive: true, force: true });
});

test("workspace config rejects malformed repository collections", () => {
  const { state } = fixture();
  fs.writeFileSync(path.join(state, "workspace.json"), JSON.stringify({ repositories: { app: "." } }));
  assert.throws(() => loadWorkspaceConfig(state), /repositories must be an array/);
});

test("workspace_status is read-only and returns merged provenance", async () => {
  const { state } = fixture();
  fs.writeFileSync(path.join(state, "workspace.json"), JSON.stringify({ repositories: [{ id: "app", path: ".", primary: true }] }));
  const tools = new Map();
  const server = { registerTool(name, config, handler) { tools.set(name, { config, handler }); } };
  const guarded = (handler) => async (args) => ({ content: [{ type: "text", text: await handler(args || {}) }] });
  registerWorkspaceTools(server, { guarded, resolveStateDir: () => state });
  const response = await tools.get("workspace_status").handler({ format: "json" });
  const result = JSON.parse(response.content[0].text);
  assert.equal(result.sources.shared.exists, true);
  assert.equal(result.sources.local.exists, false);
  assert.equal(result.mutation, "none");
});
