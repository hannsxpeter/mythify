import { test } from "node:test";
import assert from "node:assert/strict";

import { terminateProcessTree } from "../src/process-tree.js";

test("Windows taskkill failure kills the parent and reports uncontained descendants", () => {
  const descendant = { alive: true };
  const child = {
    pid: 4242,
    killed: false,
    kill(signal) {
      this.killed = signal === "SIGKILL";
    },
  };
  const calls = [];
  const contained = terminateProcessTree(child, {
    platform: "win32",
    runTaskkill(command, args) {
      calls.push({ command, args });
      return { status: 1, error: null };
    },
  });

  assert.equal(contained, false);
  assert.equal(child.killed, true, "parent fallback is killed");
  assert.equal(descendant.alive, true, "test models the descendant that taskkill missed");
  assert.deepEqual(calls, [
    { command: "taskkill", args: ["/pid", "4242", "/t", "/f"] },
  ]);
});
