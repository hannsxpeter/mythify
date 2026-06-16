import test from "node:test";
import assert from "node:assert/strict";
import {
  buildHostModelRecord,
  formatHostModelRecord,
  withHostCapability,
} from "../src/host-model.js";

test("host model module builds and formats records directly", () => {
  const record = buildHostModelRecord(
    {
      platform: "auto",
      target_model: "gpt-5.4",
      current_model: "gpt-5.3-codex",
      thinking: "high",
      speed: "fast",
      reason: "direct module test",
    },
    {
      now: () => "2026-06-16T00:00:00.000Z",
      classifyModelTier: () => "frontier",
      env: { CODEX_THREAD_ID: "thread-123" },
    }
  );

  assert.equal(record.platform, "codex-desktop");
  assert.equal(record.requested_platform, "auto");
  assert.equal(record.target_model_tier, "frontier");
  assert.equal(record.switch_result.requested_thinking, "high");
  assert.equal(record.host_capability.status, "supported");
  assert.match(record.host_actions.join("\n"), /threadId="thread-123"/);

  const formatted = formatHostModelRecord(record);
  assert.match(formatted, /target model: gpt-5\.4 \(tier frontier\)/);
  assert.match(formatted, /switch status: manual/);
  assert.match(formatted, /current-chat confirmed: no/);

  const legacy = withHostCapability({
    platform: "codex-cli",
    target_model: "gpt-5.4",
    current_model: "",
    target_model_tier: "frontier",
    thinking: "auto",
    speed: "auto",
    updated: "2026-06-16T00:00:00.000Z",
  });
  assert.equal(legacy.host_capability.status, "supported");
  assert.equal(legacy.switch_result.status, "manual");
  assert.equal(legacy.host_confirmation.confirmation_status, "unsupported");
  assert.equal(legacy.adapter_proof_scan.status, "metadata_only");
});
