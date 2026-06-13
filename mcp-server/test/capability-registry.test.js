import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import {
  ADAPTER_CANDIDATES,
  HOST_CAPABILITIES,
  HOST_MODEL_DEFAULTS,
  HOST_PLATFORMS,
  ROLE_PROVIDER_ALLOWED,
  ROLE_PROVIDER_DEFAULTS,
  ROLE_PROVIDER_ENV_NAMES,
  TRIAGE_ENGINES,
  getHostCapability,
  listAdapterCandidates,
} from "../src/capability-registry.js";
import { renderAdapterCandidatesDoc } from "../../scripts/build_registry_docs.mjs";

const adapterDocPath = fileURLToPath(
  new URL("../../docs/adapter-candidates.md", import.meta.url)
);

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

test("role provider defaults stay explicit", () => {
  assert.deepEqual(ROLE_PROVIDER_DEFAULTS, {
    session: "host",
    triage: "host_cli",
    reader: "local_openai_compatible",
    fanout_worker: "host_cli",
    reviewer: "host_cli",
    verifier: "local_command",
  });
  assert.deepEqual(ROLE_PROVIDER_ALLOWED.triage, [
    "host_cli",
    "local_openai_compatible",
    "command",
  ]);
  assert.equal(ROLE_PROVIDER_ENV_NAMES.fanout_worker, "MYTHIFY_ROLE_WORKER_PROVIDER");
});

test("researched future adapters are candidates, not public host platforms", () => {
  const candidateNames = Object.keys(ADAPTER_CANDIDATES).sort();
  assert.deepEqual(candidateNames, [
    "antigravity",
    "generic-openai-compatible",
    "google-adk-cli",
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
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].status, "local_backend_supported");
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].can_run_local_roles, true);
  assert.deepEqual(ADAPTER_CANDIDATES["generic-openai-compatible"].local_roles, ["reader", "triage"]);
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].output_is_evidence, false);
  assert.deepEqual(
    listAdapterCandidates("host").map((candidate) => candidate.name).sort(),
    ["antigravity", "kimi-code", "opencode"]
  );
  assert.equal(ADAPTER_CANDIDATES.antigravity.status, "probe_supported");
  assert.equal(ADAPTER_CANDIDATES.antigravity.can_probe, true);
  assert.equal(ADAPTER_CANDIDATES.antigravity.can_run_noninteractive_prompt, false);
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].status, "worker_supported");
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].can_run_noninteractive_prompt, true);
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].can_run_bounded_worker, true);
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].worker_output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES.opencode.status, "worker_supported");
  assert.equal(ADAPTER_CANDIDATES.opencode.can_probe, true);
  assert.equal(ADAPTER_CANDIDATES.opencode.can_run_noninteractive_prompt, true);
  assert.equal(ADAPTER_CANDIDATES.opencode.can_run_bounded_worker, true);
  assert.equal(ADAPTER_CANDIDATES.opencode.worker_output_is_evidence, false);
  assert.deepEqual(
    listAdapterCandidates("execution_substrate").map((candidate) => candidate.name).sort(),
    ["google-colab-cli"]
  );
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].status, "probe_supported");
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].can_run_remote_job, false);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].non_billable_probe, true);
  assert.deepEqual(
    listAdapterCandidates("agent_lifecycle").map((candidate) => candidate.name).sort(),
    ["google-adk-cli", "google-agents-cli"]
  );
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].status, "probe_supported");
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_probe_eval, true);
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_run_eval, false);
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_deploy, false);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].status, "probe_supported");
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_probe_eval, true);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_run_eval, false);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_deploy, false);
});

test("generated adapter candidate docs stay in sync with registry", () => {
  const generated = renderAdapterCandidatesDoc();
  const committed = fs.readFileSync(adapterDocPath, "utf8");
  assert.equal(committed, generated);
});
