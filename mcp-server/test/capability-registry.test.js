import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import {
  ADAPTER_CANDIDATES,
  ADAPTER_INTERFACE_FIELDS,
  ADAPTER_INTERFACE_LANES,
  ADAPTER_INTERFACE_VERSION,
  HOST_CAPABILITIES,
  HOST_MODEL_DEFAULTS,
  HOST_PLATFORMS,
  ROLE_PROVIDER_ALLOWED,
  ROLE_PROVIDER_DEFAULTS,
  ROLE_PROVIDER_ENV_NAMES,
  ROLE_PROVIDER_PROFILES,
  ROLE_COST_METADATA_FIELDS,
  ROLE_TIMEOUT_DEFAULTS,
  ROLE_TIMEOUT_METADATA_FIELDS,
  TRIAGE_ENGINES,
  adapterInterfaceForCandidate,
  buildAdapterInterfaceCatalog,
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
  assert.deepEqual(ROLE_TIMEOUT_METADATA_FIELDS, [
    "timeout_seconds",
    "timeout_source",
    "timeout_enforced_by",
    "can_override",
  ]);
  assert.deepEqual(ROLE_COST_METADATA_FIELDS, [
    "billing",
    "cost_estimate_supported",
    "cost_estimate_status",
    "cost_estimate_cents",
    "pricing_url",
    "usage_metadata_fields",
  ]);
  assert.equal(ROLE_TIMEOUT_DEFAULTS.triage.timeout_seconds, 120);
  assert.equal(ROLE_TIMEOUT_DEFAULTS.fanout_worker.timeout_enforced_by, "fanout_worker");
  assert.equal(ROLE_TIMEOUT_DEFAULTS.verifier.timeout_source, "verify_run_default");
});

test("stable adapter interface normalizes candidate lanes", () => {
  assert.equal(ADAPTER_INTERFACE_VERSION, 1);
  assert.deepEqual(ADAPTER_INTERFACE_LANES, [
    "host",
    "desktop_agent",
    "model_provider",
    "api_provider",
    "custom_adapter",
    "execution_substrate",
    "agent_lifecycle",
  ]);
  assert.ok(ADAPTER_INTERFACE_FIELDS.includes("evidence_status"));
  assert.ok(ADAPTER_INTERFACE_FIELDS.includes("guardrails"));

  const catalog = buildAdapterInterfaceCatalog();
  assert.equal(catalog.opencode.interface_version, 1);
  assert.equal(catalog.opencode.kind, "host");
  assert.equal(catalog.opencode.probe_supported, true);
  assert.equal(catalog.opencode.run_supported, true);
  assert.equal(catalog.opencode.execution_enabled, true);
  assert.equal(catalog.opencode.writes_state, false);
  assert.equal(catalog.opencode.evidence_status, "worker_output_not_verification");
  assert.deepEqual(catalog.opencode.roles, ["triage", "fanout_worker", "reviewer"]);
  assert.ok(catalog.opencode.guardrails.includes("material_not_verification"));

  assert.equal(catalog["openai-api"].kind, "api_provider");
  assert.equal(catalog["openai-api"].run_supported, false);
  assert.equal(catalog["openai-api"].execution_enabled, false);
  assert.ok(catalog["openai-api"].guardrails.includes("explicit_enable_required"));

  assert.equal(catalog["google-colab-cli"].kind, "execution_substrate");
  assert.equal(catalog["google-colab-cli"].run_supported, true);
  assert.equal(catalog["google-colab-cli"].writes_state, false);
  assert.deepEqual(catalog["google-colab-cli"].roles, ["remote_execution"]);
  assert.ok(catalog["google-colab-cli"].guardrails.includes("billing_ack_required"));

  assert.equal(catalog["google-agents-cli"].kind, "agent_lifecycle");
  assert.equal(
    catalog["google-agents-cli"].evidence_status,
    "lifecycle_probe_output_not_verification"
  );
  assert.ok(catalog["google-agents-cli"].guardrails.includes("no_eval_execution"));
  assert.ok(catalog["google-agents-cli"].guardrails.includes("no_deploy"));

  assert.deepEqual(
    adapterInterfaceForCandidate("custom-http", ADAPTER_CANDIDATES["custom-http"]).roles,
    []
  );
});

test("researched future adapters are candidates, not public host platforms", () => {
  const candidateNames = Object.keys(ADAPTER_CANDIDATES).sort();
  assert.deepEqual(candidateNames, [
    "anthropic-api",
    "antigravity",
    "custom-command",
    "custom-http",
    "generic-openai-compatible",
    "google-adk-cli",
    "google-agents-cli",
    "google-colab-cli",
    "kimi-code",
    "kimi-work",
    "llama-cpp",
    "lm-studio",
    "ollama",
    "openai-api",
    "openai-compatible-hosted",
    "opencode",
    "opencode-desktop",
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
  assert.deepEqual(
    listAdapterCandidates("custom_adapter").map((candidate) => candidate.name).sort(),
    ["custom-command", "custom-http"]
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
  assert.equal(ADAPTER_CANDIDATES["custom-command"].status, "bounded_execution_supported");
  assert.equal(ADAPTER_CANDIDATES["custom-command"].execution_enabled, true);
  assert.deepEqual(ADAPTER_CANDIDATES["custom-command"].command_env, [
    "MYTHIFY_TRIAGE_COMMAND",
    "MYTHIFY_FANOUT_COMMAND",
  ]);
  assert.equal(ADAPTER_CANDIDATES["custom-command"].input_contract, "prompt_on_stdin");
  assert.equal(ADAPTER_CANDIDATES["custom-command"].output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES["custom-http"].status, "metadata_only");
  assert.equal(ADAPTER_CANDIDATES["custom-http"].execution_enabled, false);
  assert.equal(ADAPTER_CANDIDATES["custom-http"].explicit_enable_required, true);
  assert.equal(ADAPTER_CANDIDATES["custom-http"].base_url_env, "MYTHIFY_CUSTOM_HTTP_BASE_URL");
  assert.equal(ADAPTER_CANDIDATES["custom-http"].can_run_http_worker, false);
  assert.equal(ADAPTER_CANDIDATES["custom-http"].output_is_evidence, false);
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
  assert.deepEqual(
    listAdapterCandidates("desktop_agent").map((candidate) => candidate.name).sort(),
    ["kimi-work", "opencode-desktop"]
  );
  assert.equal(ADAPTER_CANDIDATES.antigravity.status, "worker_supported");
  assert.equal(ADAPTER_CANDIDATES.antigravity.can_probe, true);
  assert.equal(ADAPTER_CANDIDATES.antigravity.can_run_noninteractive_prompt, true);
  assert.equal(ADAPTER_CANDIDATES.antigravity.can_run_bounded_worker, true);
  assert.equal(ADAPTER_CANDIDATES.antigravity.current_chat_model_apply_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES.antigravity.current_chat_model_confirm_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES.antigravity.worker_model_override_status, "supported");
  assert.equal(ADAPTER_CANDIDATES.antigravity.worker_model_override_command, "agy --model");
  assert.equal(ADAPTER_CANDIDATES.antigravity.thinking_override_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES.antigravity.worker_output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES.antigravity.permission_policy, "native_permissions_no_auto_bypass");
  assert.equal(ADAPTER_CANDIDATES.antigravity.trust_policy, "explicit_cwd_required");
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].status, "worker_supported");
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].can_run_noninteractive_prompt, true);
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].can_run_bounded_worker, true);
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].current_chat_model_apply_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].worker_model_override_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES["kimi-code"].worker_output_is_evidence, false);
  assert.equal(ADAPTER_CANDIDATES["kimi-work"].status, "metadata_only");
  assert.equal(ADAPTER_CANDIDATES["kimi-work"].can_probe, false);
  assert.equal(ADAPTER_CANDIDATES["kimi-work"].can_run_bounded_worker, false);
  assert.equal(ADAPTER_CANDIDATES["kimi-work"].current_chat_model_confirm_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES["kimi-work"].metadata_only, true);
  assert.equal(ADAPTER_CANDIDATES["kimi-work"].permission_policy, "manual_ask_before_acting");
  assert.equal(ADAPTER_CANDIDATES["kimi-work"].automation_policy, "no_documented_cli_or_api");
  assert.equal(ADAPTER_CANDIDATES["opencode-desktop"].status, "metadata_only");
  assert.equal(ADAPTER_CANDIDATES["opencode-desktop"].can_probe, false);
  assert.equal(ADAPTER_CANDIDATES["opencode-desktop"].can_run_bounded_worker, false);
  assert.equal(ADAPTER_CANDIDATES["opencode-desktop"].worker_model_override_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES["opencode-desktop"].metadata_only, true);
  assert.equal(
    ADAPTER_CANDIDATES["opencode-desktop"].automation_policy,
    "use_opencode_cli_server_or_sdk_not_desktop_app"
  );
  assert.equal(ADAPTER_CANDIDATES["opencode-desktop"].app_bundle_id, "ai.opencode.desktop");
  assert.equal(ADAPTER_CANDIDATES.opencode.status, "worker_supported");
  assert.equal(ADAPTER_CANDIDATES.opencode.can_probe, true);
  assert.equal(ADAPTER_CANDIDATES.opencode.can_run_noninteractive_prompt, true);
  assert.equal(ADAPTER_CANDIDATES.opencode.can_run_bounded_worker, true);
  assert.equal(ADAPTER_CANDIDATES.opencode.current_chat_model_apply_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES.opencode.current_chat_model_confirm_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES.opencode.worker_model_override_status, "supported");
  assert.equal(ADAPTER_CANDIDATES.opencode.worker_model_override_command, "opencode run --model");
  assert.equal(ADAPTER_CANDIDATES.opencode.thinking_override_status, "unsupported");
  assert.equal(ADAPTER_CANDIDATES.opencode.worker_output_is_evidence, false);
  assert.deepEqual(
    listAdapterCandidates("execution_substrate").map((candidate) => candidate.name).sort(),
    ["google-colab-cli"]
  );
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].status, "guarded_remote_execution_supported");
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].can_run_remote_job, true);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].non_billable_probe, true);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].remote_run_command, "colab run");
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].execution_mode, "ephemeral_run_no_keep");
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].requires_billing_ack, true);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].requires_data_movement_ack, true);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].requires_cleanup_ack, true);
  assert.equal(ADAPTER_CANDIDATES["google-colab-cli"].output_is_evidence, false);
  assert.deepEqual(
    listAdapterCandidates("agent_lifecycle").map((candidate) => candidate.name).sort(),
    ["google-adk-cli", "google-agents-cli"]
  );
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].status, "probe_supported");
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_probe_eval, true);
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_run_eval, false);
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].can_deploy, false);
  assert.equal(ADAPTER_CANDIDATES["google-agents-cli"].output_is_evidence, false);
  assert.equal(
    ADAPTER_CANDIDATES["google-agents-cli"].lifecycle_mutation_policy,
    "probe_only_no_project_or_cloud_mutation"
  );
  assert.deepEqual(ADAPTER_CANDIDATES["google-agents-cli"].lifecycle_allowed_probe_commands, [
    "--version",
    "--help",
    "eval --help",
  ]);
  assert.ok(ADAPTER_CANDIDATES["google-agents-cli"].lifecycle_disabled_actions.includes("setup"));
  assert.ok(
    ADAPTER_CANDIDATES["google-agents-cli"].lifecycle_future_guarded_actions.includes(
      "deployment"
    )
  );
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].status, "probe_supported");
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_probe, true);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_probe_eval, true);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_run_eval, false);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].can_deploy, false);
  assert.equal(ADAPTER_CANDIDATES["google-adk-cli"].output_is_evidence, false);
  assert.equal(
    ADAPTER_CANDIDATES["google-adk-cli"].lifecycle_mutation_policy,
    "probe_only_no_project_or_cloud_mutation"
  );
  assert.ok(ADAPTER_CANDIDATES["google-adk-cli"].lifecycle_disabled_actions.includes("web"));
  assert.ok(
    ADAPTER_CANDIDATES["google-adk-cli"].lifecycle_future_guarded_actions.includes(
      "eval_execution"
    )
  );
});

test("generated adapter candidate docs stay in sync with registry", () => {
  const generated = renderAdapterCandidatesDoc();
  const committed = fs.readFileSync(adapterDocPath, "utf8");
  assert.equal(committed, generated);
});
