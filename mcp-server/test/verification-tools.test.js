import test from "node:test";
import assert from "node:assert/strict";
import {
  VERIFICATION_TOOL_NAMES,
  registerVerificationTools,
} from "../src/verification-tools.js";

function makeHarness({ verified = true } = {}) {
  const registered = [];
  const verifications = [];
  const reflections = [];
  const lessons = [];
  const runs = [];

  const server = {
    registerTool(name, config, handler) {
      registered.push({ name, config, handler });
    },
  };

  registerVerificationTools(server, {
    guarded: (handler) => async (args) => handler(args || {}),
    runShellCapture: (command, timeoutSeconds) => {
      runs.push({ command, timeoutSeconds });
      return {
        command,
        exit_code: verified ? 0 : 2,
        duration_seconds: 0.12,
        stdout_tail: verified ? "ok" : "stdout tail",
        stderr_tail: verified ? "" : "stderr tail",
        verified,
      };
    },
    isoNow: () => "2026-06-16T00:00:00.000Z",
    verificationStepContext: () => ({
      plan: "release",
      step_id: 38,
      step_title: "Extract verification tools",
      step_status: "in_progress",
    }),
    appendJsonl: (target, record) => {
      if (target === "verifications") {
        verifications.push(record);
      } else if (target === "reflections") {
        reflections.push(record);
      }
    },
    verificationsPath: () => "verifications",
    reflectionsPath: () => "reflections",
    recordLesson: (title, detail, tags, scope) => {
      lessons.push({ title, detail, tags, scope });
      return `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.json`;
    },
  });

  return { registered, verifications, reflections, lessons, runs };
}

test("verification tool registrar wires verify and reflection handlers", async () => {
  const harness = makeHarness();
  const { registered, verifications, reflections, lessons, runs } = harness;

  assert.deepEqual(registered.map((entry) => entry.name), VERIFICATION_TOOL_NAMES);

  const verifyRun = registered.find((entry) => entry.name === "verify_run");
  assert.ok(verifyRun.config.inputSchema.command);
  const runResult = await verifyRun.handler({
    command: "npm test",
    claim: "tests pass",
    timeout_seconds: 7,
  });
  assert.match(runResult, /^\[OK\] VERIFIED: tests pass/);
  assert.deepEqual(runs[0], { command: "npm test", timeoutSeconds: 7 });
  assert.equal(verifications[0].kind, "executed");
  assert.equal(verifications[0].step_id, 38);
  assert.deepEqual(Object.keys(verifications[0].provenance).sort(), [
    "git_commit",
    "mythify_version",
    "worktree_clean",
  ]);
  assert.equal(verifications[0].provenance.mythify_version, "5.0.0");

  const verifyClaim = registered.find((entry) => entry.name === "verify_claim");
  const claimResult = await verifyClaim.handler({
    claim: "manual review",
    evidence: "read the docs",
  });
  assert.match(claimResult, /^\[WARN\] ATTESTED: manual review/);
  assert.equal(verifications[1].kind, "attested");
  assert.equal(verifications[1].verified, null);

  const reflect = registered.find((entry) => entry.name === "reflect");
  const reflectionResult = await reflect.handler({
    action_taken: "extracted module",
    outcome: "success",
    observation: "tests passed",
    next_action: "release",
    lesson: "Small verified registrars are easier to ship",
  });
  assert.match(reflectionResult, /^\[OK\] Reflection recorded/);
  assert.equal(reflections[0].action, "extracted module");
  assert.equal(lessons[0].scope, "project");
  assert.deepEqual(lessons[0].tags, ["auto-reflected"]);
});

test("verify_run reports failed commands with captured tails", async () => {
  const harness = makeHarness({ verified: false });
  const verifyRun = harness.registered.find((entry) => entry.name === "verify_run");

  const result = await verifyRun.handler({ command: "false" });
  assert.match(result, /^\[FAIL\] UNVERIFIED: false/);
  assert.match(result, /stdout tail/);
  assert.match(result, /stderr tail/);
  assert.equal(harness.verifications[0].verified, false);
});

test("verify_run kill switch refuses execution", async () => {
  const harness = makeHarness();
  const verifyRun = harness.registered.find((entry) => entry.name === "verify_run");

  const previous = process.env.MYTHIFY_DISABLE_RUN;
  process.env.MYTHIFY_DISABLE_RUN = "1";
  try {
    const result = await verifyRun.handler({ command: "npm test" });
    assert.match(result, /^\[FAIL\] verify_run is disabled/);
    assert.equal(harness.runs.length, 0);
    assert.equal(harness.verifications.length, 0);
  } finally {
    if (previous === undefined) {
      delete process.env.MYTHIFY_DISABLE_RUN;
    } else {
      process.env.MYTHIFY_DISABLE_RUN = previous;
    }
  }
});

test("verification tool registrar rejects missing required deps", () => {
  assert.throws(
    () => registerVerificationTools({ registerTool() {} }, {}),
    /requires deps\.guarded/
  );
});
