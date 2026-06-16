import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyTaskText,
  formatClassification,
  shouldRunModelTriage,
} from "../src/classification.js";

test("classification module is directly importable", () => {
  const payload = classifyTaskText("audit authentication token permissions");
  assert.equal(payload.task_type, "security");
  assert.equal(payload.risk, "high");
  assert.equal(payload.ceremony, "full");
  assert.equal(payload.execution_profile, "full");
  assert.equal(shouldRunModelTriage(payload, "auto"), true);
  assert.match(formatClassification(payload), /type: security/);
});
