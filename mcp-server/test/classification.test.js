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
  assert.equal(payload.plan_archetype, "rpi");
  assert.equal(shouldRunModelTriage(payload, "auto"), true);
  assert.match(formatClassification(payload), /type: security/);
});

test("classification selects plan archetypes by reversibility", () => {
  assert.equal(classifyTaskText("fix the focused failing parser test").plan_archetype, "direct");
  assert.equal(
    classifyTaskText("implement a new export feature across modules with validation integration tests documentation and error handling").plan_archetype,
    "rpi"
  );
  assert.equal(classifyTaskText("migrate the public API schema across runtimes").plan_archetype, "design-heavy");
});

test("classification detects an open-ended quality climb", () => {
  const climb = classifyTaskText(
    "Build the landing page at the level of Linear, utterly perfect, AAA polish"
  );
  assert.equal(climb.quality_climb, "detected");
  assert.match(climb.quality_climb_protocol, /harsh critic/);
  assert.match(climb.quality_climb_protocol, /brake/);
  assert.match(formatClassification(climb), /quality climb: detected/);
  const plain = classifyTaskText("Fix the failing parser test");
  assert.equal(plain.quality_climb, "not_detected");
  assert.equal(plain.quality_climb_protocol, "");
  assert.doesNotMatch(formatClassification(plain), /quality climb:/);
});
