import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ADAPTER_CANDIDATES,
  HOST_CAPABILITIES,
  HOST_MODEL_DEFAULTS,
  HOST_PLATFORMS,
  TRIAGE_ENGINES,
  getHostCapability,
  listAdapterCandidates,
} from "../src/capability-registry.js";

test("capability registry preserves current public MCP enums", () => {
  assert.deepEqual(HOST_PLATFORMS, [
    "auto",
    "unknown",
    "codex-desktop",
    "codex-cli",
    "claude-desktop",
    "claude-code",
    "cursor-desktop",
    "cursor-agent",
  ]);
  assert.deepEqual(TRIAGE_ENGINES, ["claude-cli", "codex-cli", "cursor-agent", "command"]);
});

test("host capabilities are explicit and conservative", () => {
  assert.equal(getHostCapability("codex-cli").can_switch_current_thread, false);
  assert.equal(getHostCapability("codex-cli").can_set_worker_model, true);
  assert.equal(getHostCapability("codex-cli").can_set_thinking, true);
  assert.equal(getHostCapability("claude-code").can_set_worker_model, true);
  assert.equal(getHostCapability("claude-code").can_set_thinking, false);
  assert.equal(getHostCapability("missing-host"), HOST_CAPABILITIES.unknown);
});

test("current host model defaults stay stable", () => {
  assert.equal(HOST_MODEL_DEFAULTS["codex-desktop"].fast, "gpt-5.4-mini");
  assert.equal(HOST_MODEL_DEFAULTS["codex-cli"].strong, "gpt-5.5");
  assert.equal(HOST_MODEL_DEFAULTS["claude-desktop"].standard, "sonnet");
  assert.equal(HOST_MODEL_DEFAULTS["cursor-agent"].fast, "gpt-5.3-codex-low-fast");
});

test("researched future adapters are candidates, not public host platforms", () => {
  const candidateNames = Object.keys(ADAPTER_CANDIDATES).sort();
  assert.deepEqual(candidateNames, [
    "antigravity",
    "generic-openai-compatible",
    "google-agents-cli",
    "google-colab-cli",
    "kimi-code",
    "llama-cpp",
    "lm-studio",
    "ollama",
    "opencode",
    "vllm",
  ]);

  for (const name of candidateNames) {
    assert.equal(HOST_PLATFORMS.includes(name), false, `${name} is not a public platform yet`);
  }

  assert.deepEqual(
    listAdapterCandidates("model_provider").map((candidate) => candidate.name).sort(),
    ["generic-openai-compatible", "llama-cpp", "lm-studio", "ollama", "vllm"]
  );
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].status, "probe_supported");
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].can_probe, true);
  assert.deepEqual(
    listAdapterCandidates("host").map((candidate) => candidate.name).sort(),
    ["antigravity", "kimi-code", "opencode"]
  );
});
