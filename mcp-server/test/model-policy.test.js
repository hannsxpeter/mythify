import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  buildModelPolicy,
  classifyModelProfile,
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

test("model router separates profile, topology, review, and verification policy", () => {
  assert.equal(classifyModelProfile("gpt-5.6-luna"), "utility");
  assert.equal(classifyModelProfile("gpt-5.6-terra"), "balanced");
  assert.equal(classifyModelProfile("gpt-5.6-sol"), "strong");
  assert.equal(classifyModelProfile("claude-fable-5"), "max");

  const direct = buildModelPolicy(
    classification({
      task_type: "question",
      risk: "low",
      ceremony: "none",
      execution_profile: "direct",
      fanout: "not_recommended",
    }),
    { platform: "codex-cli" }
  );
  assert.equal(direct.model_router.selection.selected_profile, "utility");
  assert.equal(direct.model_router.execution_topology.recommended, "direct");
  assert.equal(direct.model_router.verification_gate.model_is_verifier, false);
  assert.equal(direct.session.recommendation.target_model, "gpt-5.6-luna");
  assert.equal(direct.session.recommendation.target_model_status, "resolved");

  const escalated = buildModelPolicy(
    classification({
      task_type: "question",
      risk: "low",
      ceremony: "none",
      execution_profile: "direct",
      fanout: "not_recommended",
    }),
    { platform: "codex-cli", failure_count: 2 }
  );
  assert.equal(escalated.model_router.selection.selected_profile, "strong");
  assert.equal(escalated.model_router.selection.escalation_steps, 2);
  assert.equal(escalated.model_router.selection.automatic_max_enabled, false);

  withEnv({ MYTHIFY_FAILURE_COUNT: "-1" }, () => {
    const invalidFailureCount = buildModelPolicy(
      classification({
        task_type: "question",
        risk: "low",
        ceremony: "none",
        execution_profile: "direct",
        fanout: "not_recommended",
      }),
      { platform: "codex-cli" }
    );
    assert.equal(invalidFailureCount.model_router.selection.failure_count, 0);
    assert.equal(
      invalidFailureCount.model_router.selection.failure_count_source,
      "invalid_env_ignored"
    );
  });

  const explicitMax = buildModelPolicy(classification(), {
    platform: "claude-code",
    model_profile: "max",
  });
  assert.equal(explicitMax.model_router.selection.selected_profile, "max");
  assert.equal(explicitMax.model_router.selection.requested_profile_source, "explicit");
  assert.equal(explicitMax.session.recommendation.target_model, "fable");
  assert.equal(explicitMax.session.recommendation.target_api_model, "claude-fable-5");
  assert.equal(explicitMax.session.recommendation.thinking, "max");

  const legacy = buildModelPolicy(classification(), {
    platform: "claude-code",
    model_profile: "frontier",
  });
  assert.equal(legacy.model_router.selection.selected_profile, "strong");
  assert.equal(legacy.model_router.selection.requested_profile_source, "explicit_legacy_alias");

  const research = buildModelPolicy(
    classification({ task_type: "research", risk: "low", ceremony: "light" }),
    { platform: "claude-code" }
  );
  assert.equal(research.model_router.selection.selected_profile, "strong");
  assert.equal(research.model_router.execution_topology.dynamic_workflow_candidate, true);
  assert.equal(research.model_router.execution_topology.automatic_dynamic_workflow, true);
  assert.equal(research.model_router.execution_topology.native_adapter.engine, "claude-ultracode");
  assert.equal(research.model_router.execution_topology.native_adapter.start_tool, "fanout_start");
  assert.equal(research.model_router.execution_topology.native_adapter.status_tool, "fanout_status");
  assert.equal(research.model_router.execution_topology.native_adapter.results_tool, "fanout_results");
  assert.equal(
    research.model_router.execution_topology.native_adapter.result_evidence_status,
    "material_not_verification"
  );
  assert.equal(research.model_router.review_policy.independent, true);

  const explicitUltracode = buildModelPolicy(
    classification({
      task_type: "feature",
      risk: "low",
      ceremony: "standard",
      fanout: "not_recommended",
    }),
    { platform: "claude-code", task_text: "ultracode: implement this migration" }
  );
  assert.equal(
    explicitUltracode.model_router.execution_topology.dynamic_workflow_candidate_source,
    "explicit_request"
  );
  assert.equal(explicitUltracode.model_router.execution_topology.native_adapter.recommended, true);
  assert.equal(
    explicitUltracode.model_router.execution_topology.native_adapter.activation,
    "explicit_request"
  );

  const cursor = buildModelPolicy(
    classification({ task_type: "feature", risk: "low", ceremony: "standard" }),
    { platform: "cursor-agent" }
  );
  assert.equal(cursor.session.recommendation.action, "recommend_discover");
  assert.equal(cursor.session.recommendation.target_model_status, "discovery_required");
  assert.equal(cursor.session.recommendation.resolution.discovery_command, "agent models");
  assert.equal(cursor.session.recommendation.resolution.fallback_model, "auto");
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
