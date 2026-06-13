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
  ROLE_PROVIDER_PROFILES,
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
  assert.deepEqual(ROLE_PROVIDER_PROFILES.host_cli.default_roles, [
    "triage",
    "fanout_worker",
    "reviewer",
  ]);
  assert.deepEqual(ROLE_PROVIDER_PROFILES.api_provider.allowed_roles, [
    "fanout_worker",
    "reviewer",
  ]);
  assert.equal(ROLE_PROVIDER_PROFILES.api_provider.execution_enabled, false);
  assert.equal(ROLE_PROVIDER_PROFILES.api_provider.billing, "metered_external_account");
  assert.equal(ROLE_PROVIDER_PROFILES.local_openai_compatible.local_only, true);
  assert.equal(ROLE_PROVIDER_PROFILES.local_command.evidence_status, "executed_verification");
});

test("researched future adapters are candidates, not public host platforms", () => {
  const candidateNames = Object.keys(ADAPTER_CANDIDATES).sort();
  assert.deepEqual(candidateNames, [
    "anthropic-api",
    "antigravity",
    "generic-openai-compatible",
    "google-adk-cli",
    "google-agents-cli",
    "google-colab-cli",
    "kimi-code",
    "llama-cpp",
    "lm-studio",
    "ollama",
    "openai-api",
    "openai-compatible-hosted",
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
  assert.deepEqual(
    listAdapterCandidates("api_provider").map((candidate) => candidate.name).sort(),
    ["anthropic-api", "openai-api", "openai-compatible-hosted"]
  );
  assert.equal(ADAPTER_CANDIDATES["openai-api"].status, "metadata_supported");
  assert.equal(ADAPTER_CANDIDATES["openai-api"].openai_compatible, true);
  assert.equal(ADAPTER_CANDIDATES["openai-api"].default_base_url, "https://api.openai.com/v1");
  assert.equal(ADAPTER_CANDIDATES["openai-api"].api_key_env, "OPENAI_API_KEY");
  assert.equal(ADAPTER_CANDIDATES["openai-api"].billing, "metered_external_account");
  assert.equal(ADAPTER_CANDIDATES["openai-api"].explicit_enable_required, true);
  assert.equal(ADAPTER_CANDIDATES["openai-api"].can_run_api_worker, false);
  assert.equal(ADAPTER_CANDIDATES["openai-api"].fallback_policy, "no_implicit_cross_provider_fallback");
  assert.equal(ADAPTER_CANDIDATES["anthropic-api"].status, "metadata_supported");
  assert.equal(ADAPTER_CANDIDATES["anthropic-api"].openai_compatible, false);
  assert.equal(ADAPTER_CANDIDATES["anthropic-api"].default_base_url, "https://api.anthropic.com/v1");
  assert.equal(ADAPTER_CANDIDATES["anthropic-api"].api_key_env, "ANTHROPIC_API_KEY");
  assert.equal(ADAPTER_CANDIDATES["anthropic-api"].auth_header, "x-api-key");
  assert.equal(ADAPTER_CANDIDATES["anthropic-api"].version_header, "anthropic-version:2023-06-01");
  assert.equal(ADAPTER_CANDIDATES["openai-compatible-hosted"].status, "metadata_supported");
  assert.equal(ADAPTER_CANDIDATES["openai-compatible-hosted"].openai_compatible, true);
  assert.equal(
    ADAPTER_CANDIDATES["openai-compatible-hosted"].base_url_env,
    "MYTHIFY_HOSTED_OPENAI_COMPAT_BASE_URL"
  );
  assert.equal(ADAPTER_CANDIDATES["openai-compatible-hosted"].pricing_url_env, "MYTHIFY_HOSTED_OPENAI_COMPAT_PRICING_URL");
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].status, "local_backend_supported");
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].can_run_local_roles, true);
  assert.deepEqual(ADAPTER_CANDIDATES["generic-openai-compatible"].local_roles, ["reader", "triage"]);
  assert.equal(ADAPTER_CANDIDATES["generic-openai-compatible"].output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES.ollama.status, "local_profile_supported");
  assert.equal(ADAPTER_CANDIDATES.ollama.openai_compatible, true);
  assert.equal(ADAPTER_CANDIDATES.ollama.can_probe, true);
  assert.equal(ADAPTER_CANDIDATES.ollama.can_run_local_roles, true);
  assert.deepEqual(ADAPTER_CANDIDATES.ollama.local_roles, ["reader", "triage"]);
  assert.equal(ADAPTER_CANDIDATES.ollama.default_base_url, "http://localhost:11434/v1");
  assert.equal(ADAPTER_CANDIDATES.ollama.output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES["lm-studio"].status, "local_profile_supported");
  assert.equal(ADAPTER_CANDIDATES["lm-studio"].openai_compatible, true);
  assert.equal(ADAPTER_CANDIDATES["lm-studio"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["lm-studio"].can_run_local_roles, true);
  assert.deepEqual(ADAPTER_CANDIDATES["lm-studio"].local_roles, ["reader", "triage"]);
  assert.equal(ADAPTER_CANDIDATES["lm-studio"].default_base_url, "http://localhost:1234/v1");
  assert.equal(ADAPTER_CANDIDATES["lm-studio"].output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].status, "local_profile_supported");
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].openai_compatible, true);
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].can_run_local_roles, true);
  assert.deepEqual(ADAPTER_CANDIDATES["llama-cpp"].local_roles, ["reader", "triage"]);
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].default_base_url, "http://localhost:8080/v1");
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].model_env, "MYTHIFY_LLAMA_CPP_MODEL");
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].base_url_env, "MYTHIFY_LLAMA_CPP_BASE_URL");
  assert.equal(ADAPTER_CANDIDATES["llama-cpp"].output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES.vllm.status, "local_profile_supported");
  assert.equal(ADAPTER_CANDIDATES.vllm.openai_compatible, true);
  assert.equal(ADAPTER_CANDIDATES.vllm.can_probe, true);
  assert.equal(ADAPTER_CANDIDATES.vllm.can_run_local_roles, true);
  assert.deepEqual(ADAPTER_CANDIDATES.vllm.local_roles, ["reader", "triage"]);
  assert.equal(ADAPTER_CANDIDATES.vllm.default_base_url, "http://localhost:8000/v1");
  assert.equal(ADAPTER_CANDIDATES.vllm.model_env, "MYTHIFY_VLLM_MODEL");
  assert.equal(ADAPTER_CANDIDATES.vllm.base_url_env, "MYTHIFY_VLLM_BASE_URL");
  assert.equal(ADAPTER_CANDIDATES.vllm.output_is_evidence, false);
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
