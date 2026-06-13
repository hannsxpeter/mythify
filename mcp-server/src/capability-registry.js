// Capability and policy registry for MCP host and provider integrations.
// Public schemas stay stable until docs/design.md explicitly expands them.

export const TRIAGE_ENGINES = ["claude-cli", "codex-cli", "cursor-agent", "command"];
export const TRIAGE_MODES = ["never", "auto", "always"];
export const HOST_PLATFORMS = [
  "auto",
  "unknown",
  "codex-desktop",
  "codex-cli",
  "claude-desktop",
  "claude-code",
  "cursor-desktop",
  "cursor-agent",
];
export const EFFORT_LEVELS = ["auto", "low", "medium", "high"];
export const SPEED_LEVELS = ["auto", "standard", "fast"];
export const HOST_THINKING_LEVELS = ["auto", "low", "medium", "high", "xhigh", "max"];
export const SPAWN_CEILINGS = ["auto", "lower_only", "same_or_lower", "allow_stronger"];
export const REVIEWER_STRENGTH_MODES = ["auto", "same_or_lower", "allow_stronger"];
export const FANOUT_VISIBILITY_MODES = ["auto", "quiet", "summary", "verbose", "threaded"];

export const MODEL_TIER_RANK = {
  unknown: 0,
  small: 1,
  fast: 2,
  standard: 3,
  strong: 4,
  frontier: 5,
};

export const HOST_PROFILE_RANK = {
  fast: MODEL_TIER_RANK.fast,
  standard: MODEL_TIER_RANK.standard,
  strong: MODEL_TIER_RANK.frontier,
};

export const HOST_MODEL_DEFAULTS = {
  "codex-desktop": {
    fast: "gpt-5.4-mini",
    standard: "gpt-5.4",
    strong: "gpt-5.5",
  },
  "codex-cli": {
    fast: "gpt-5.4-mini",
    standard: "gpt-5.4",
    strong: "gpt-5.5",
  },
  "claude-desktop": {
    fast: "haiku",
    standard: "sonnet",
    strong: "opus",
  },
  "claude-code": {
    fast: "haiku",
    standard: "sonnet",
    strong: "opus",
  },
  "cursor-desktop": {
    fast: "gpt-5.3-codex-low-fast",
    standard: "gpt-5.3-codex",
    strong: "gpt-5.3-codex-high",
  },
  "cursor-agent": {
    fast: "gpt-5.3-codex-low-fast",
    standard: "gpt-5.3-codex",
    strong: "gpt-5.3-codex-high",
  },
};

export const ROLE_PROVIDER_DEFAULTS = {
  session: "host",
  triage: "host_cli",
  reader: "local_openai_compatible",
  fanout_worker: "host_cli",
  reviewer: "host_cli",
  verifier: "local_command",
};

export const ROLE_PROVIDER_ALLOWED = {
  session: ["host"],
  triage: ["host_cli", "local_openai_compatible", "command"],
  reader: ["local_openai_compatible", "host"],
  fanout_worker: ["host_cli", "api_provider", "command"],
  reviewer: ["host_cli", "api_provider", "command"],
  verifier: ["local_command"],
};

export const ROLE_PROVIDER_ENV_NAMES = {
  session: "MYTHIFY_ROLE_SESSION_PROVIDER",
  triage: "MYTHIFY_ROLE_TRIAGE_PROVIDER",
  reader: "MYTHIFY_ROLE_READER_PROVIDER",
  fanout_worker: "MYTHIFY_ROLE_WORKER_PROVIDER",
  reviewer: "MYTHIFY_ROLE_REVIEWER_PROVIDER",
  verifier: "MYTHIFY_ROLE_VERIFIER_PROVIDER",
};

export const ROLE_PROVIDER_FALLBACK_POLICY = "no_implicit_cross_provider_fallback";

export const HOSTED_PROVIDER_FANOUT_ENGINES = ["anthropic", "openai"];

export const HOSTED_PROVIDER_REQUIRED_ACKS = [
  "hosted_provider_billing_ack",
  "hosted_provider_data_ack",
  "hosted_provider_material_ack",
];

export const ROLE_TIMEOUT_METADATA_FIELDS = [
  "timeout_seconds",
  "timeout_source",
  "timeout_enforced_by",
  "can_override",
];

export const ROLE_COST_METADATA_FIELDS = [
  "billing",
  "cost_estimate_supported",
  "cost_estimate_status",
  "cost_estimate_cents",
  "pricing_url",
  "usage_metadata_fields",
];

export const ADAPTER_INTERFACE_VERSION = 1;
export const ADAPTER_INTERFACE_FIELDS = [
  "id",
  "kind",
  "status",
  "locality",
  "openai_compatible",
  "probe_supported",
  "run_supported",
  "execution_enabled",
  "writes_state",
  "evidence_status",
  "material_not_evidence",
  "billing",
  "roles",
  "guardrails",
];
export const ADAPTER_INTERFACE_LANES = [
  "host",
  "desktop_agent",
  "model_provider",
  "api_provider",
  "custom_adapter",
  "execution_substrate",
  "agent_lifecycle",
];

export const ROLE_TIMEOUT_DEFAULTS = {
  session: {
    timeout_seconds: null,
    timeout_source: "host_controlled",
    timeout_enforced_by: "host",
    can_override: false,
  },
  triage: {
    timeout_seconds: 120,
    timeout_source: "triage_timeout_seconds_or_default",
    timeout_enforced_by: "triage_worker",
    can_override: true,
  },
  reader: {
    timeout_seconds: 30,
    timeout_source: "local_model_run_default",
    timeout_enforced_by: "local_model_run",
    can_override: true,
  },
  fanout_worker: {
    timeout_seconds: 600,
    timeout_source: "fanout_start_or_env_or_default",
    timeout_enforced_by: "fanout_worker",
    can_override: true,
  },
  reviewer: {
    timeout_seconds: 600,
    timeout_source: "fanout_start_or_env_or_default",
    timeout_enforced_by: "fanout_reviewer",
    can_override: true,
  },
  verifier: {
    timeout_seconds: 300,
    timeout_source: "verify_run_default",
    timeout_enforced_by: "verify_run",
    can_override: true,
  },
};

export const ROLE_PROVIDER_PROFILES = {
  host: {
    status: "supported",
    allowed_roles: ["session", "reader"],
    default_roles: ["session"],
    control: "host_selected",
    billing: "host_account_or_subscription",
    execution_enabled: true,
    writes_state: false,
    evidence_status: "host_output_not_verification",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  host_cli: {
    status: "supported",
    allowed_roles: ["triage", "fanout_worker", "reviewer"],
    default_roles: ["triage", "fanout_worker", "reviewer"],
    control: "bounded_worker",
    billing: "host_cli_subscription_or_local_quota",
    execution_enabled: true,
    writes_state: false,
    evidence_status: "worker_output_not_verification",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  local_openai_compatible: {
    status: "supported",
    allowed_roles: ["triage", "reader"],
    default_roles: ["reader"],
    control: "localhost_model_provider",
    billing: "local_compute",
    execution_enabled: true,
    writes_state: false,
    evidence_status: "model_output_not_verification",
    local_only: true,
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  api_provider: {
    status: "metadata_supported",
    allowed_roles: ["fanout_worker", "reviewer"],
    default_roles: [],
    control: "hosted_provider",
    billing: "metered_external_account",
    execution_enabled: false,
    writes_state: false,
    evidence_status: "provider_output_not_verification",
    explicit_enable_required: true,
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  command: {
    status: "supported",
    allowed_roles: ["triage", "fanout_worker", "reviewer"],
    default_roles: [],
    control: "explicit_command",
    billing: "user_defined",
    execution_enabled: true,
    writes_state: false,
    evidence_status: "command_output_not_verification",
    custom_adapter: "custom-command",
    command_env: ["MYTHIFY_TRIAGE_COMMAND", "MYTHIFY_FANOUT_COMMAND"],
    input_contract: "prompt_on_stdin",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  local_command: {
    status: "supported",
    allowed_roles: ["verifier"],
    default_roles: ["verifier"],
    control: "local_verifier",
    billing: "local_compute",
    execution_enabled: true,
    writes_state: true,
    evidence_status: "executed_verification",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
};

export const STRONG_HOST_TASK_TYPES = [
  "research",
  "benchmark",
  "design",
  "security",
  "release",
  "migration",
];

const NO_HOST_CAPABILITY = {
  kind: "host",
  status: "unsupported",
  can_switch_current_thread: false,
  can_set_new_thread_model: false,
  can_set_worker_model: false,
  can_set_thinking: false,
  can_list_models: false,
  can_confirm_current_model: false,
};

export const HOST_CAPABILITIES = {
  unknown: {
    ...NO_HOST_CAPABILITY,
    status: "unknown",
  },
  "codex-desktop": {
    ...NO_HOST_CAPABILITY,
    status: "supported",
    can_set_new_thread_model: true,
    can_set_worker_model: true,
    can_set_thinking: true,
  },
  "codex-cli": {
    ...NO_HOST_CAPABILITY,
    status: "supported",
    can_set_new_thread_model: true,
    can_set_worker_model: true,
    can_set_thinking: true,
  },
  "claude-desktop": {
    ...NO_HOST_CAPABILITY,
    status: "supported",
    can_set_new_thread_model: true,
    can_set_worker_model: true,
  },
  "claude-code": {
    ...NO_HOST_CAPABILITY,
    status: "supported",
    can_set_new_thread_model: true,
    can_set_worker_model: true,
  },
  "cursor-desktop": {
    ...NO_HOST_CAPABILITY,
    status: "supported",
    can_set_new_thread_model: true,
    can_set_worker_model: true,
    can_set_thinking: true,
  },
  "cursor-agent": {
    ...NO_HOST_CAPABILITY,
    status: "supported",
    can_set_new_thread_model: true,
    can_set_worker_model: true,
    can_set_thinking: true,
  },
};

export const ADAPTER_CANDIDATES = {
  "generic-openai-compatible": {
    kind: "model_provider",
    status: "local_backend_supported",
    local: true,
    openai_compatible: true,
    can_probe: true,
    can_answer_prompt: false,
    can_run_local_roles: true,
    local_roles: ["reader", "triage"],
    output_is_evidence: false,
  },
  ollama: {
    kind: "model_provider",
    status: "local_profile_supported",
    local: true,
    openai_compatible: true,
    can_probe: true,
    can_answer_prompt: true,
    can_run_local_roles: true,
    local_roles: ["reader", "triage"],
    output_is_evidence: false,
    local_only: true,
    default_base_url: "http://localhost:11434/v1",
    model_env: "MYTHIFY_OLLAMA_MODEL",
    base_url_env: "MYTHIFY_OLLAMA_BASE_URL",
    api_key_env: "",
  },
  "lm-studio": {
    kind: "model_provider",
    status: "local_profile_supported",
    local: true,
    openai_compatible: true,
    can_probe: true,
    can_answer_prompt: true,
    can_run_local_roles: true,
    local_roles: ["reader", "triage"],
    output_is_evidence: false,
    local_only: true,
    default_base_url: "http://localhost:1234/v1",
    model_env: "MYTHIFY_LM_STUDIO_MODEL",
    base_url_env: "MYTHIFY_LM_STUDIO_BASE_URL",
    api_key_env: "",
  },
  "llama-cpp": {
    kind: "model_provider",
    status: "local_profile_supported",
    local: true,
    openai_compatible: true,
    can_probe: true,
    can_answer_prompt: true,
    can_run_local_roles: true,
    local_roles: ["reader", "triage"],
    output_is_evidence: false,
    local_only: true,
    default_base_url: "http://localhost:8080/v1",
    model_env: "MYTHIFY_LLAMA_CPP_MODEL",
    base_url_env: "MYTHIFY_LLAMA_CPP_BASE_URL",
    api_key_env: "",
  },
  vllm: {
    kind: "model_provider",
    status: "local_profile_supported",
    local: true,
    openai_compatible: true,
    can_probe: true,
    can_answer_prompt: true,
    can_run_local_roles: true,
    local_roles: ["reader", "triage"],
    output_is_evidence: false,
    local_only: true,
    default_base_url: "http://localhost:8000/v1",
    model_env: "MYTHIFY_VLLM_MODEL",
    base_url_env: "MYTHIFY_VLLM_BASE_URL",
    api_key_env: "",
  },
  "openai-api": {
    kind: "api_provider",
    status: "metadata_supported",
    local: false,
    openai_compatible: true,
    can_probe: false,
    can_run_api_worker: false,
    metadata_only: true,
    output_is_evidence: false,
    protocol: "openai_responses_or_chat",
    default_base_url: "https://api.openai.com/v1",
    api_key_env: "OPENAI_API_KEY",
    model_env: "MYTHIFY_OPENAI_API_MODEL",
    auth_header: "authorization_bearer",
    billing: "metered_external_account",
    explicit_enable_required: true,
    timeout_metadata_supported: true,
    default_timeout_seconds: 600,
    cost_metadata_supported: true,
    cost_metadata_fields: [
      "provider",
      "model",
      "input_tokens",
      "cached_input_tokens",
      "output_tokens",
      "pricing_url",
    ],
    pricing_url: "https://openai.com/api/pricing/",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  "anthropic-api": {
    kind: "api_provider",
    status: "metadata_supported",
    local: false,
    openai_compatible: false,
    can_probe: false,
    can_run_api_worker: false,
    metadata_only: true,
    output_is_evidence: false,
    protocol: "anthropic_messages",
    default_base_url: "https://api.anthropic.com/v1",
    api_key_env: "ANTHROPIC_API_KEY",
    model_env: "MYTHIFY_ANTHROPIC_API_MODEL",
    auth_header: "x-api-key",
    version_header: "anthropic-version:2023-06-01",
    billing: "metered_external_account",
    explicit_enable_required: true,
    timeout_metadata_supported: true,
    default_timeout_seconds: 600,
    cost_metadata_supported: true,
    cost_metadata_fields: [
      "provider",
      "model",
      "input_tokens",
      "cached_input_tokens",
      "output_tokens",
      "pricing_url",
    ],
    pricing_url: "https://docs.anthropic.com/en/docs/about-claude/pricing",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  "openai-compatible-hosted": {
    kind: "api_provider",
    status: "metadata_supported",
    local: false,
    openai_compatible: true,
    can_probe: false,
    can_run_api_worker: false,
    metadata_only: true,
    output_is_evidence: false,
    protocol: "openai_compatible",
    base_url_env: "MYTHIFY_HOSTED_OPENAI_COMPAT_BASE_URL",
    api_key_env: "MYTHIFY_HOSTED_OPENAI_COMPAT_API_KEY",
    model_env: "MYTHIFY_HOSTED_OPENAI_COMPAT_MODEL",
    provider_name_env: "MYTHIFY_HOSTED_OPENAI_COMPAT_PROVIDER",
    pricing_url_env: "MYTHIFY_HOSTED_OPENAI_COMPAT_PRICING_URL",
    auth_header: "authorization_bearer",
    billing: "metered_external_account",
    explicit_enable_required: true,
    timeout_metadata_supported: true,
    default_timeout_seconds: 600,
    cost_metadata_supported: true,
    cost_metadata_fields: [
      "provider",
      "model",
      "input_tokens",
      "cached_input_tokens",
      "output_tokens",
      "pricing_url",
    ],
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  "custom-command": {
    kind: "custom_adapter",
    status: "bounded_execution_supported",
    local: true,
    openai_compatible: false,
    can_probe: false,
    can_run_noninteractive_prompt: true,
    can_run_bounded_worker: true,
    execution_enabled: true,
    command_env: ["MYTHIFY_TRIAGE_COMMAND", "MYTHIFY_FANOUT_COMMAND"],
    input_contract: "prompt_on_stdin",
    timeout_metadata_supported: true,
    cost_metadata_supported: true,
    billing: "user_defined",
    writes_state: false,
    output_is_evidence: false,
    evidence_status: "command_output_not_verification",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  "custom-http": {
    kind: "custom_adapter",
    status: "metadata_only",
    local: false,
    openai_compatible: false,
    can_probe: false,
    can_run_api_worker: false,
    can_run_http_worker: false,
    metadata_only: true,
    execution_enabled: false,
    base_url_env: "MYTHIFY_CUSTOM_HTTP_BASE_URL",
    api_key_env: "MYTHIFY_CUSTOM_HTTP_API_KEY",
    model_env: "MYTHIFY_CUSTOM_HTTP_MODEL",
    pricing_url_env: "MYTHIFY_CUSTOM_HTTP_PRICING_URL",
    explicit_enable_required: true,
    timeout_metadata_supported: true,
    cost_metadata_supported: true,
    billing: "metered_external_account_or_user_defined",
    writes_state: false,
    output_is_evidence: false,
    evidence_status: "http_output_not_verification",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
  },
  "kimi-code": {
    kind: "host",
    status: "worker_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_noninteractive_prompt: true,
    can_run_bounded_worker: true,
    current_chat_model_apply_status: "unsupported",
    current_chat_model_confirm_status: "unsupported",
    worker_model_override_status: "unsupported",
    thinking_override_status: "unsupported",
    proof_source: "capability_registry",
    worker_output_is_evidence: false,
  },
  "kimi-work": {
    kind: "desktop_agent",
    status: "metadata_only",
    local: true,
    openai_compatible: false,
    can_probe: false,
    can_run_noninteractive_prompt: false,
    can_run_bounded_worker: false,
    can_switch_current_thread: false,
    current_chat_model_apply_status: "unsupported",
    current_chat_model_confirm_status: "unsupported",
    worker_model_override_status: "unsupported",
    thinking_override_status: "unsupported",
    proof_source: "capability_registry",
    metadata_only: true,
    output_is_evidence: false,
    permission_policy: "manual_ask_before_acting",
    automation_policy: "no_documented_cli_or_api",
    product_url: "https://www.kimi.com/products/kimi-work",
  },
  opencode: {
    kind: "host",
    status: "worker_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_noninteractive_prompt: true,
    can_run_bounded_worker: true,
    current_chat_model_apply_status: "unsupported",
    current_chat_model_confirm_status: "unsupported",
    worker_model_override_status: "supported",
    worker_model_override_command: "opencode run --model",
    thinking_override_status: "unsupported",
    proof_source: "capability_registry",
    worker_output_is_evidence: false,
  },
  "opencode-desktop": {
    kind: "desktop_agent",
    status: "metadata_only",
    local: true,
    openai_compatible: false,
    can_probe: false,
    can_run_noninteractive_prompt: false,
    can_run_bounded_worker: false,
    can_switch_current_thread: false,
    current_chat_model_apply_status: "unsupported",
    current_chat_model_confirm_status: "unsupported",
    worker_model_override_status: "unsupported",
    thinking_override_status: "unsupported",
    proof_source: "capability_registry",
    metadata_only: true,
    output_is_evidence: false,
    automation_policy: "use_opencode_cli_server_or_sdk_not_desktop_app",
    app_bundle_id: "ai.opencode.desktop",
    product_url: "https://opencode.ai/download",
  },
  antigravity: {
    kind: "host",
    status: "worker_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_noninteractive_prompt: true,
    can_run_bounded_worker: true,
    current_chat_model_apply_status: "unsupported",
    current_chat_model_confirm_status: "unsupported",
    worker_model_override_status: "supported",
    worker_model_override_command: "agy --model",
    thinking_override_status: "unsupported",
    proof_source: "capability_registry",
    worker_output_is_evidence: false,
    permission_policy: "native_permissions_no_auto_bypass",
    trust_policy: "explicit_cwd_required",
  },
  "google-colab-cli": {
    kind: "execution_substrate",
    status: "guarded_remote_execution_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_remote_job: true,
    non_billable_probe: true,
    remote_run_command: "colab run",
    execution_mode: "ephemeral_run_no_keep",
    requires_billing_ack: true,
    requires_data_movement_ack: true,
    requires_cleanup_ack: true,
    output_is_evidence: false,
  },
  "google-agents-cli": {
    kind: "agent_lifecycle",
    status: "probe_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_probe_eval: true,
    can_run_eval: false,
    can_deploy: false,
    can_publish: false,
    can_scaffold: false,
    can_run_agent: false,
    can_mutate_project: false,
    can_mutate_cloud: false,
    writes_state: false,
    output_is_evidence: false,
    evidence_status: "lifecycle_probe_output_not_verification",
    lifecycle_allowed_probe_actions: ["probe_version", "probe_help", "probe_eval_help"],
    lifecycle_allowed_probe_commands: ["--version", "--help", "eval --help"],
    lifecycle_disabled_actions: [
      "setup",
      "scaffold",
      "project_create",
      "agent_run",
      "eval_execution",
      "deployment",
      "publishing",
      "cloud_mutation",
      "project_mutation",
    ],
    lifecycle_future_guarded_actions: ["eval_execution", "deployment", "publishing"],
    lifecycle_mutation_policy: "probe_only_no_project_or_cloud_mutation",
    guardrails: [
      "probe_only",
      "no_scaffold",
      "no_agent_run",
      "no_eval_execution",
      "no_deploy",
      "no_publish",
      "no_cloud_mutation",
      "no_project_mutation",
      "material_not_verification",
      "no_mythify_state_write",
    ],
  },
  "google-adk-cli": {
    kind: "agent_lifecycle",
    status: "probe_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_probe_eval: true,
    can_run_eval: false,
    can_deploy: false,
    can_publish: false,
    can_scaffold: false,
    can_run_agent: false,
    can_mutate_project: false,
    can_mutate_cloud: false,
    writes_state: false,
    output_is_evidence: false,
    evidence_status: "lifecycle_probe_output_not_verification",
    lifecycle_allowed_probe_actions: ["probe_version", "probe_help", "probe_eval_help"],
    lifecycle_allowed_probe_commands: ["--version", "--help", "eval --help"],
    lifecycle_disabled_actions: [
      "create",
      "run",
      "web",
      "eval_execution",
      "deployment",
      "publishing",
      "cloud_mutation",
      "project_mutation",
    ],
    lifecycle_future_guarded_actions: ["eval_execution", "deployment", "publishing"],
    lifecycle_mutation_policy: "probe_only_no_project_or_cloud_mutation",
    guardrails: [
      "probe_only",
      "no_create",
      "no_agent_run",
      "no_eval_execution",
      "no_deploy",
      "no_publish",
      "no_cloud_mutation",
      "no_project_mutation",
      "material_not_verification",
      "no_mythify_state_write",
    ],
  },
};

export function getHostCapability(platform) {
  return HOST_CAPABILITIES[platform] || HOST_CAPABILITIES.unknown;
}

export function listAdapterCandidates(kind = "") {
  return Object.entries(ADAPTER_CANDIDATES)
    .filter(([, candidate]) => kind === "" || candidate.kind === kind)
    .map(([name, candidate]) => ({ name, ...candidate }));
}

function adapterLocality(candidate) {
  if (candidate.local === true || candidate.local_only === true) {
    return "local";
  }
  if (candidate.local === false) {
    return "remote_or_hosted";
  }
  return "unknown";
}

function adapterRunSupported(candidate) {
  return Boolean(
    candidate.can_run_local_roles ||
      candidate.can_run_noninteractive_prompt ||
      candidate.can_run_bounded_worker ||
      candidate.can_run_api_worker ||
      candidate.can_run_http_worker ||
      candidate.can_run_remote_job ||
      candidate.can_run_eval ||
      candidate.can_deploy
  );
}

function adapterExecutionEnabled(candidate) {
  if (candidate.execution_enabled === true) {
    return true;
  }
  if (candidate.execution_enabled === false || candidate.metadata_only) {
    return false;
  }
  return adapterRunSupported(candidate);
}

function adapterMaterialNotEvidence(candidate) {
  return Boolean(
    candidate.metadata_only ||
      candidate.output_is_evidence === false ||
      candidate.worker_output_is_evidence === false ||
      candidate.evidence_status?.endsWith("_not_verification")
  );
}

function adapterEvidenceStatus(candidate) {
  if (candidate.evidence_status) {
    return candidate.evidence_status;
  }
  if (candidate.metadata_only) {
    return "metadata_not_verification";
  }
  if (candidate.worker_output_is_evidence === false) {
    return "worker_output_not_verification";
  }
  if (candidate.output_is_evidence === false) {
    return "adapter_output_not_verification";
  }
  return "unknown";
}

function adapterRoles(name, candidate) {
  if (Array.isArray(candidate.local_roles)) {
    return candidate.local_roles;
  }
  if (candidate.kind === "host") {
    return ["triage", "fanout_worker", "reviewer"];
  }
  if (candidate.kind === "api_provider") {
    return ["fanout_worker", "reviewer"];
  }
  if (name === "custom-command") {
    return ["triage", "fanout_worker", "reviewer"];
  }
  if (candidate.kind === "execution_substrate") {
    return ["remote_execution"];
  }
  if (candidate.kind === "agent_lifecycle") {
    return ["agent_lifecycle"];
  }
  return [];
}

function adapterGuardrails(candidate) {
  const guardrails = [ROLE_PROVIDER_FALLBACK_POLICY];
  if (Array.isArray(candidate.guardrails)) {
    guardrails.push(...candidate.guardrails);
  }
  if (candidate.metadata_only) {
    guardrails.push("metadata_only");
  }
  if (candidate.explicit_enable_required) {
    guardrails.push("explicit_enable_required");
  }
  if (candidate.requires_billing_ack) {
    guardrails.push("billing_ack_required");
  }
  if (candidate.requires_data_movement_ack) {
    guardrails.push("data_movement_ack_required");
  }
  if (candidate.requires_cleanup_ack) {
    guardrails.push("cleanup_ack_required");
  }
  if (adapterMaterialNotEvidence(candidate)) {
    guardrails.push("material_not_verification");
  }
  if (candidate.writes_state !== true) {
    guardrails.push("no_mythify_state_write");
  }
  return [...new Set(guardrails)];
}

export function adapterInterfaceForCandidate(name, candidate) {
  return {
    interface_version: ADAPTER_INTERFACE_VERSION,
    id: name,
    kind: candidate.kind || "unknown",
    status: candidate.status || "unknown",
    locality: adapterLocality(candidate),
    openai_compatible: candidate.openai_compatible === true,
    probe_supported: Boolean(candidate.can_probe || candidate.non_billable_probe || candidate.can_probe_eval),
    run_supported: adapterRunSupported(candidate),
    execution_enabled: adapterExecutionEnabled(candidate),
    writes_state: candidate.writes_state === true,
    evidence_status: adapterEvidenceStatus(candidate),
    material_not_evidence: adapterMaterialNotEvidence(candidate),
    billing: candidate.billing || "unknown",
    roles: adapterRoles(name, candidate),
    guardrails: adapterGuardrails(candidate),
  };
}

export function buildAdapterInterfaceCatalog(candidates = ADAPTER_CANDIDATES) {
  const catalog = {};
  for (const [name, candidate] of Object.entries(candidates).sort(([left], [right]) =>
    left.localeCompare(right)
  )) {
    catalog[name] = adapterInterfaceForCandidate(name, candidate);
  }
  return catalog;
}
