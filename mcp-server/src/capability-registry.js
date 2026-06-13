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
    status: "candidate",
    local: true,
    openai_compatible: true,
  },
  "kimi-code": {
    kind: "host",
    status: "worker_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_noninteractive_prompt: true,
    can_run_bounded_worker: true,
    worker_output_is_evidence: false,
  },
  opencode: {
    kind: "host",
    status: "worker_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_noninteractive_prompt: true,
    can_run_bounded_worker: true,
    worker_output_is_evidence: false,
  },
  antigravity: {
    kind: "host",
    status: "probe_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_noninteractive_prompt: false,
  },
  "google-colab-cli": {
    kind: "execution_substrate",
    status: "probe_supported",
    local: false,
    openai_compatible: false,
    can_probe: true,
    can_run_remote_job: false,
    non_billable_probe: true,
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
