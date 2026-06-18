import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  buildModelPolicy,
  classifyModelTier,
  runModelTriage,
} from "../src/model-policy.js";

function withEnv(updates, fn) {
  const previous = {};
  for (const key of Object.keys(updates)) {
    previous[key] = process.env[key];
    if (updates[key] === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = updates[key];
    }
  }
  try {
    fn();
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

function classification(overrides = {}) {
  return {
    task_type: "feature",
    risk: "high",
    ceremony: "full",
    ambiguity: "medium",
    execution_profile: "standard",
    model_triage: "run",
    fanout: "recommended",
    fanout_visibility: "summary",
    fanout_visibility_source: "default",
    fanout_visibility_reason: "Summary visibility is the default.",
    ...overrides,
  };
}

test("model policy module classifies tiers and builds host-aware policy", () => {
  assert.equal(classifyModelTier("gpt-5.5"), "frontier");
  assert.equal(classifyModelTier("haiku-fast"), "fast");
  assert.equal(classifyModelTier("plain-model"), "standard");

  withEnv({ MYTHIFY_FANOUT_ENGINE: "command" }, () => {
    const policy = buildModelPolicy(classification(), {
      platform: "codex-desktop",
      triage_engine: "command",
      triage_timeout_seconds: 33,
      host_model_record: { target_model: "gpt-5.5" },
    });

    assert.equal(policy.session.model, "gpt-5.5");
    assert.equal(policy.session.model_source, "host_model_switch");
    assert.equal(policy.session.model_tier, "frontier");
    assert.equal(policy.session.recommendation.target_profile, "strong");
    assert.equal(policy.triage.engine, "command");
    assert.equal(policy.triage.model_policy, "command_default");
    assert.equal(policy.triage.timeout_seconds, 33);
    assert.equal(policy.fanout_worker.engine, "command");
    assert.equal(policy.reviewer.spawn, "recommended");
    assert.equal(policy.verifier.provider_profile.evidence_status, "executed_verification");
  });
});

test("model policy warns when claude-cli is selected for spawned roles", () => {
  withEnv({ MYTHIFY_FANOUT_ENGINE: "claude-cli", MYTHIFY_TRIAGE_ENGINE: "claude-cli" }, () => {
    const policy = buildModelPolicy(classification(), {
      platform: "cursor-desktop",
      triage_timeout_seconds: 33,
    });

    assert.equal(policy.triage.engine, "claude-cli");
    assert.equal(policy.triage.engine_policy, "env");
    assert.equal(policy.fanout_worker.engine, "claude-cli");
    assert.equal(policy.fanout_worker.engine_policy, "env");
    assert.ok(policy.triage.cost_warnings[0].includes("claude -p"));
    assert.ok(policy.fanout_worker.cost_warnings[0].includes("standard API pricing"));
    assert.ok(
      policy.reviewer.cost_warning_urls.includes("https://code.claude.com/docs/en/costs")
    );
  });
});

test("model triage command runner returns parsed material without state writes", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-model-policy-"));
  const script = path.join(root, "triage.cjs");
  fs.writeFileSync(
    script,
    [
      "let input = '';",
      "process.stdin.on('data', (chunk) => { input += chunk; });",
      "process.stdin.on('end', () => {",
      "  console.log(JSON.stringify({ risk: 'low', received: input.length > 0 }));",
      "});",
      "",
    ].join("\n"),
    "utf8"
  );
  try {
    withEnv({ MYTHIFY_TRIAGE_COMMAND: `${JSON.stringify(process.execPath)} ${JSON.stringify(script)}` }, () => {
      const skipped = runModelTriage("implement x", classification({ model_triage: "skip" }), {
        triage: "auto",
        cwd: root,
      });
      assert.equal(skipped.attempted, false);

      const result = runModelTriage("implement x", classification({ model_triage: "run" }), {
        triage: "always",
        triage_engine: "command",
        cwd: root,
        triage_timeout_seconds: 5,
      });
      assert.equal(result.attempted, true);
      assert.equal(result.ok, true);
      assert.equal(result.engine, "command");
      assert.equal(result.model_policy, "command_default");
      assert.equal(result.parsed.risk, "low");
      assert.equal(result.parsed.received, true);
    });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
