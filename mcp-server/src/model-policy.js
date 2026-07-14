import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  buildTriagePrompt,
  shouldRunModelTriage,
} from "./classification.js";
import {
  buildProviderDefaults,
  roleBudgetFields,
  roleProviderFields,
} from "./provider-defaults.js";
import {
  CAPABILITY_PROFILE_RANK,
  CAPABILITY_PROFILES,
  FANOUT_VISIBILITY_MODES,
  HOST_PLATFORMS as PLATFORMS,
  MODEL_CAPABILITY_MANIFEST,
  MODEL_ESCALATION_POLICY,
  MODEL_MATCH_ORDER,
  MODEL_MATCH_TERMS,
  MODEL_PROFILE_ALIASES,
  MODEL_ROUTING_AXES,
  MODEL_TIER_RANK,
  MODEL_TOPOLOGY_POLICY,
  PLATFORM_MODEL_PROVIDERS,
  PROVIDER_MODEL_PROFILES,
  REVIEWER_STRENGTH_MODES,
  SPAWN_CEILINGS,
  TASK_MODEL_PROFILES,
  TRIAGE_ENGINES,
} from "./capability-registry.js";

const DEFAULT_WORKER_ENGINE = "codex-cli";
const CLAUDE_CLI_COST_WARNING =
  "Selecting claude-cli runs Claude Code non-interactively through claude -p. " +
  "Claude Code usage is token-cost-sensitive; included usage applies only within plan limits. " +
  "If usage credits are enabled and included limits are reached, continued usage can be billed at standard API pricing.";
const CLAUDE_CLI_COST_WARNING_URLS = [
  "https://code.claude.com/docs/en/headless",
  "https://code.claude.com/docs/en/costs",
  "https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans",
];

function tailText(text, limit = 4000) {
  const s = String(text == null ? "" : text);
  return s.length > limit ? s.slice(-limit) : s;
}

function splitShellArgs(raw) {
  const args = [];
  let current = "";
  let quote = null;
  let hasToken = false;
  for (const ch of String(raw || "")) {
    if (quote !== null) {
      if (ch === quote) {
        quote = null;
      } else {
        current += ch;
      }
      continue;
    }
    if (ch === "\"" || ch === "'") {
      quote = ch;
      hasToken = true;
      continue;
    }
    if (ch === " " || ch === "\t" || ch === "\n") {
      if (hasToken) {
        args.push(current);
        current = "";
        hasToken = false;
      }
      continue;
    }
    current += ch;
    hasToken = true;
  }
  if (hasToken) {
    args.push(current);
  }
  return args;
}

function triageDefaultModel(engine) {
  const provider = {
    "codex-cli": "openai",
    "claude-cli": "anthropic",
    "claude-ultracode": "anthropic",
    "cursor-agent": "cursor",
  }[engine] || "";
  return PROVIDER_MODEL_PROFILES[provider]?.utility?.model || "";
}

function isExecutableFile(filePath) {
  try {
    if (!fs.statSync(filePath).isFile()) {
      return false;
    }
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function findExecutableOnPath(binaryName) {
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (dir === "") {
      continue;
    }
    const candidate = path.join(dir, binaryName);
    if (isExecutableFile(candidate)) {
      return candidate;
    }
  }
  return null;
}

function resolveTriageBinary(envNames, binaryNames, fallbacks) {
  for (const envName of envNames) {
    const explicit = (process.env[envName] || "").trim();
    if (explicit !== "") {
      return isExecutableFile(explicit) ? explicit : null;
    }
  }
  for (const binaryName of binaryNames) {
    const onPath = findExecutableOnPath(binaryName);
    if (onPath !== null) {
      return onPath;
    }
  }
  for (const candidate of fallbacks) {
    if (isExecutableFile(candidate)) {
      return candidate;
    }
  }
  return null;
}

function resolveClaudeTriageBin() {
  return resolveTriageBinary(
    ["MYTHIFY_TRIAGE_CLAUDE_BIN", "MYTHIFY_FANOUT_CLAUDE_BIN"],
    ["claude"],
    [
      path.join(os.homedir(), ".claude", "local", "claude"),
      "/opt/homebrew/bin/claude",
      "/usr/local/bin/claude",
    ]
  );
}

function resolveCodexTriageBin() {
  return resolveTriageBinary(
    ["MYTHIFY_TRIAGE_CODEX_BIN", "MYTHIFY_FANOUT_CODEX_BIN"],
    ["codex"],
    [
      path.join(os.homedir(), ".local", "bin", "codex"),
      "/opt/homebrew/bin/codex",
      "/usr/local/bin/codex",
    ]
  );
}

function resolveCursorTriageInvocation() {
  const explicit = (process.env.MYTHIFY_TRIAGE_CURSOR_BIN || "").trim();
  if (explicit !== "") {
    if (!isExecutableFile(explicit)) {
      return null;
    }
    return { bin: explicit, prefixArgs: path.basename(explicit) === "cursor" ? ["agent"] : [] };
  }
  const fallbackBin = resolveTriageBinary(
    ["MYTHIFY_FANOUT_CURSOR_BIN", "MYTHIFY_FANOUT_CURSOR_AGENT_BIN"],
    ["cursor-agent", "cursor"],
    [
      path.join(os.homedir(), ".local", "bin", "cursor-agent"),
      path.join(os.homedir(), ".local", "bin", "cursor"),
      "/opt/homebrew/bin/cursor-agent",
      "/opt/homebrew/bin/cursor",
      "/usr/local/bin/cursor-agent",
      "/usr/local/bin/cursor",
    ]
  );
  if (fallbackBin === null) {
    return null;
  }
  return { bin: fallbackBin, prefixArgs: path.basename(fallbackBin) === "cursor" ? ["agent"] : [] };
}

function commandTriageTemplate() {
  return (process.env.MYTHIFY_TRIAGE_COMMAND || "").trim() || (process.env.MYTHIFY_FANOUT_COMMAND || "").trim();
}

function autoDetectTriageEngine() {
  const explicit = (process.env.MYTHIFY_TRIAGE_ENGINE || "").trim();
  if (explicit !== "") {
    return explicit;
  }
  if (resolveCodexTriageBin() !== null) {
    return "codex-cli";
  }
  if (resolveClaudeTriageBin() !== null) {
    return "claude-cli";
  }
  if (resolveCursorTriageInvocation() !== null) {
    return "cursor-agent";
  }
  if (commandTriageTemplate() !== "") {
    return "command";
  }
  return "";
}

function inferPlatform() {
  const configured = (process.env.MYTHIFY_HOST_PLATFORM || "").trim();
  if (configured !== "" && configured !== "auto") {
    return PLATFORMS.includes(configured) ? configured : "unknown";
  }
  const origin = (process.env.CODEX_INTERNAL_ORIGINATOR_OVERRIDE || "").toLowerCase();
  if (
    origin.includes("codex") ||
    process.env.CODEX_SHELL ||
    (process.env.CODEX_THREAD_ID || "").trim() !== ""
  ) {
    return "codex-desktop";
  }
  if (
    process.env.CLAUDECODE ||
    Object.keys(process.env).some((key) => key.startsWith("CLAUDE_CODE_"))
  ) {
    return "claude-code";
  }
  if (process.env.CURSOR_AGENT || process.env.CURSOR_TRACE_ID || process.env.CURSOR_SESSION_ID) {
    return "cursor-desktop";
  }
  return "unknown";
}

export function normalizePlatform(platform) {
  const value = (platform || "auto").trim();
  if (value === "auto") {
    return inferPlatform();
  }
  return PLATFORMS.includes(value) ? value : "unknown";
}

function triageEngineAvailable(engine) {
  if (engine === "claude-cli") {
    return resolveClaudeTriageBin() !== null;
  }
  if (engine === "codex-cli") {
    return resolveCodexTriageBin() !== null;
  }
  if (engine === "cursor-agent") {
    return resolveCursorTriageInvocation() !== null;
  }
  if (engine === "command") {
    return commandTriageTemplate() !== "";
  }
  return false;
}

function defaultLocalWorkerEngine() {
  return triageEngineAvailable(DEFAULT_WORKER_ENGINE) ? DEFAULT_WORKER_ENGINE : "";
}

function engineWarningMetadata(engine) {
  if (engine === "claude-cli") {
    return {
      cost_warnings: [CLAUDE_CLI_COST_WARNING],
      cost_warning_urls: CLAUDE_CLI_COST_WARNING_URLS,
    };
  }
  return {};
}

export function selectTriageEngine(requestedEngine, platform) {
  const explicit = (requestedEngine || "").trim();
  if (explicit !== "") {
    return { engine: explicit, enginePolicy: "explicit" };
  }
  const envEngine = (process.env.MYTHIFY_TRIAGE_ENGINE || "").trim();
  if (envEngine !== "") {
    return { engine: envEngine, enginePolicy: "env" };
  }
  const defaultEngine = defaultLocalWorkerEngine();
  if (defaultEngine !== "") {
    return { engine: defaultEngine, enginePolicy: "codex_default" };
  }
  const detected = autoDetectTriageEngine();
  if (detected !== "") {
    return { engine: detected, enginePolicy: "auto_detected" };
  }
  return { engine: "", enginePolicy: "unavailable" };
}

function selectWorkerEngine(platform) {
  const envEngine = (process.env.MYTHIFY_FANOUT_ENGINE || "").trim();
  if (envEngine !== "") {
    return { engine: envEngine, enginePolicy: "env" };
  }
  const defaultEngine = defaultLocalWorkerEngine();
  if (defaultEngine !== "") {
    return { engine: defaultEngine, enginePolicy: "codex_default" };
  }
  const detected = autoDetectTriageEngine();
  if (detected !== "") {
    return { engine: detected, enginePolicy: "auto_detected" };
  }
  return { engine: "auto", enginePolicy: "local_first" };
}

export function resolveTriageModelSelection(engine, requestedModel) {
  const explicit = (requestedModel || "").trim();
  if (explicit !== "") {
    return { model: explicit, modelPolicy: "explicit" };
  }
  const envModel = (process.env.MYTHIFY_TRIAGE_MODEL || "").trim();
  if (envModel !== "") {
    return { model: envModel, modelPolicy: "env" };
  }
  const defaultModel = triageDefaultModel(engine);
  if (defaultModel !== "") {
    return { model: defaultModel, modelPolicy: "engine_default" };
  }
  if (["codex-cli", "cursor-agent"].includes(engine)) {
    return { model: "", modelPolicy: "platform_default" };
  }
  if (engine === "command") {
    return { model: "", modelPolicy: "command_default" };
  }
  return { model: "", modelPolicy: "auto_after_engine_detection" };
}

function manifestModelProfile(model) {
  const value = String(model || "").toLowerCase().replaceAll("_", "-").replaceAll(" ", "-");
  if (value === "") {
    return "unknown";
  }
  for (const profile of MODEL_MATCH_ORDER) {
    const terms = MODEL_MATCH_TERMS[profile] || [];
    if (terms.some((term) => value.includes(String(term).toLowerCase()))) {
      return profile;
    }
  }
  return "unknown";
}

export function classifyModelTier(model) {
  const compact = String(model || "").toLowerCase().replace(/[_ ]+/g, "-");
  if (compact === "") {
    return "unknown";
  }
  const capabilityProfile = manifestModelProfile(model);
  if (capabilityProfile === "utility") {
    return "fast";
  }
  if (capabilityProfile === "balanced") {
    return "standard";
  }
  if (["strong", "max"].includes(capabilityProfile)) {
    return "frontier";
  }
  const frontierTerms = [
    "gpt-5",
    "o3",
    "o4",
    "opus",
    "max",
    "deep-research",
    "reasoning-pro",
  ];
  const strongTerms = [
    "sonnet",
    "gpt-4",
    "gpt4",
    "gemini-2.5-pro",
    "pro",
    "large",
    "grok-4",
  ];
  const fastTerms = [
    "haiku",
    "mini",
    "nano",
    "small",
    "lite",
    "flash",
    "fast",
    "instant",
  ];
  if (fastTerms.some((term) => compact.includes(term))) {
    return "fast";
  }
  if (frontierTerms.some((term) => compact.includes(term))) {
    return "frontier";
  }
  if (strongTerms.some((term) => compact.includes(term))) {
    return "strong";
  }
  if (compact.includes("3.5") || compact.includes("cheap")) {
    return "small";
  }
  return "standard";
}

export function normalizeModelProfile(profile) {
  const value = String(profile || "auto").trim().toLowerCase();
  if (CAPABILITY_PROFILES.includes(value)) {
    return value;
  }
  return MODEL_PROFILE_ALIASES[value] || "auto";
}

export function classifyModelProfile(model) {
  const matched = manifestModelProfile(model);
  if (matched !== "unknown") {
    return matched;
  }
  const tier = classifyModelTier(model);
  if (["small", "fast"].includes(tier)) {
    return "utility";
  }
  if (tier === "standard") {
    return "balanced";
  }
  if (["strong", "frontier"].includes(tier)) {
    return "strong";
  }
  return "unknown";
}

function requestedModelProfile(options) {
  const raw = String(options.model_profile || "auto").trim();
  const normalized = normalizeModelProfile(raw);
  if (normalized !== "auto") {
    return {
      profile: normalized,
      source: raw === normalized ? "explicit" : "explicit_legacy_alias",
      raw,
    };
  }
  const envRaw = String(process.env.MYTHIFY_MODEL_PROFILE || "").trim();
  const envNormalized = normalizeModelProfile(envRaw);
  if (envNormalized !== "auto") {
    return {
      profile: envNormalized,
      source: envRaw === envNormalized
        ? "env:MYTHIFY_MODEL_PROFILE"
        : "env:MYTHIFY_MODEL_PROFILE:legacy_alias",
      raw: envRaw,
    };
  }
  return { profile: "auto", source: "task_classification", raw: "auto" };
}

function requestedFailureCount(options) {
  if (options.failure_count !== undefined && options.failure_count !== null) {
    const value = Number(options.failure_count);
    if (Number.isInteger(value) && value >= 0) {
      return { count: value, source: "explicit" };
    }
    return { count: 0, source: "invalid_explicit_ignored" };
  }
  const envRaw = String(process.env.MYTHIFY_FAILURE_COUNT || "").trim();
  if (envRaw !== "") {
    const value = Number(envRaw);
    if (Number.isInteger(value) && value >= 0) {
      return { count: value, source: "env:MYTHIFY_FAILURE_COUNT" };
    }
    return { count: 0, source: "invalid_env_ignored" };
  }
  return { count: 0, source: "default" };
}

function baseModelProfile(classification) {
  const taskType = classification.task_type || "feature";
  const profile = TASK_MODEL_PROFILES[taskType] || "balanced";
  const overrides = MODEL_CAPABILITY_MANIFEST.strong_overrides;
  if ((overrides.risks || []).includes(classification.risk)) {
    return { profile: "strong", reason: "high_risk" };
  }
  if ((overrides.ceremonies || []).includes(classification.ceremony)) {
    return { profile: "strong", reason: "full_ceremony" };
  }
  return { profile, reason: `task_type:${taskType}` };
}

function escalateModelProfile(profile, failureCount) {
  if (!MODEL_ESCALATION_POLICY.enabled || failureCount <= 0) {
    return { profile, steps: 0 };
  }
  const threshold = Math.max(1, Number(MODEL_ESCALATION_POLICY.one_tier_after_failures || 1));
  const requestedSteps = Math.floor(failureCount / threshold);
  const cap = MODEL_ESCALATION_POLICY.automatic_cap || "strong";
  const capRank = CAPABILITY_PROFILE_RANK[cap] || CAPABILITY_PROFILE_RANK.strong;
  const startRank = CAPABILITY_PROFILE_RANK[profile];
  const targetRank = Math.min(startRank + requestedSteps, capRank);
  const selected = CAPABILITY_PROFILES.find(
    (candidate) => CAPABILITY_PROFILE_RANK[candidate] === targetRank
  ) || profile;
  return { profile: selected, steps: Math.max(0, targetRank - startRank) };
}

function selectModelProfile(classification, options) {
  const requested = requestedModelProfile(options);
  const failures = requestedFailureCount(options);
  const base = baseModelProfile(classification);
  let selected;
  let escalationSteps = 0;
  let reason;
  if (requested.profile !== "auto") {
    selected = requested.profile;
    reason = "Explicit model profile request.";
  } else {
    const escalated = escalateModelProfile(base.profile, failures.count);
    selected = escalated.profile;
    escalationSteps = escalated.steps;
    reason = `Selected from ${base.reason}.`;
    if (escalationSteps > 0) {
      reason += ` Escalated ${escalationSteps} tier(s) after recorded verifier failures.`;
    }
  }
  const row = MODEL_CAPABILITY_MANIFEST.profiles[selected];
  return {
    requested_profile: requested.profile,
    requested_profile_raw: requested.raw,
    requested_profile_source: requested.source,
    base_profile: base.profile,
    selected_profile: selected,
    legacy_profile: row.legacy_profile,
    cost_class: row.cost_class,
    failure_count: failures.count,
    failure_count_source: failures.source,
    escalated: escalationSteps > 0,
    escalation_steps: escalationSteps,
    automatic_max_enabled: Boolean(MODEL_CAPABILITY_MANIFEST.automatic_max_enabled),
    automatic_cap: MODEL_ESCALATION_POLICY.automatic_cap,
    max_requires: MODEL_ESCALATION_POLICY.max_requires,
    reason,
  };
}

function modelExecutionTopology(classification, taskText = "") {
  const taskType = classification.task_type || "feature";
  const executionProfile = classification.execution_profile || "standard";
  const fanout = classification.fanout || "not_recommended";
  const dynamicTypes = MODEL_TOPOLOGY_POLICY.dynamic_workflow_candidate_task_types || [];
  const explicitDynamicRequest =
    /\bultracode\b|\b(?:use|run|launch|start)\s+(?:a\s+)?(?:dynamic\s+)?workflow\b/i.test(
      String(taskText || "")
    );
  const dynamicCandidate =
    explicitDynamicRequest || (dynamicTypes.includes(taskType) && fanout === "recommended");
  const automaticDynamicWorkflow =
    dynamicCandidate && Boolean(MODEL_TOPOLOGY_POLICY.automatic_dynamic_workflow);
  const adapter = MODEL_TOPOLOGY_POLICY.native_adapter || {};
  let recommended;
  let reason;
  if (executionProfile === "direct") {
    recommended = "direct";
    reason = "The task is a direct answer or one reversible action.";
  } else if (fanout === "recommended") {
    recommended = "bounded_parallel";
    reason = "Independent analysis can be split, then synthesized and verified.";
  } else if (classification.ceremony === "full") {
    recommended = "verifier_gated_plan";
    reason = "High-risk or heavy work needs durable steps and verification gates.";
  } else if (executionProfile === "fast") {
    recommended = "direct_with_verification";
    reason = "Focused work can run directly with an executable completion gate.";
  } else {
    recommended = "verifier_gated_plan";
    reason = "Multi-step work should use a plan with executable gates.";
  }
  return {
    recommended,
    dynamic_workflow_candidate: dynamicCandidate,
    dynamic_workflow_candidate_source: explicitDynamicRequest
      ? "explicit_request"
      : dynamicCandidate
        ? "task_classification"
        : "not_recommended",
    automatic_dynamic_workflow: automaticDynamicWorkflow,
    native_adapter: {
      ...adapter,
      recommended: automaticDynamicWorkflow,
      activation: automaticDynamicWorkflow
        ? explicitDynamicRequest
          ? "explicit_request"
          : "automatic_candidate"
        : "not_recommended",
    },
    parallelism_requires: MODEL_TOPOLOGY_POLICY.parallelism_requires,
    reason,
  };
}

function modelReviewPolicy(classification, selectedProfile) {
  let level;
  let reviewerProfile;
  if (classification.risk === "high" || classification.ceremony === "full") {
    level = "required";
    reviewerProfile = "strong";
  } else if (classification.risk === "medium" || classification.ceremony === "standard") {
    level = "recommended";
    reviewerProfile = selectedProfile === "strong" ? "strong" : "balanced";
  } else {
    level = "optional";
    reviewerProfile = selectedProfile === "utility" ? "balanced" : selectedProfile;
  }
  return {
    level,
    independent: true,
    recommended_profile: reviewerProfile,
    stronger_model_requires_explicit_opt_in: true,
    material_not_verification: true,
  };
}

function buildModelRouter(classification, options) {
  const selection = selectModelProfile(classification, options);
  const genericEffort = {
    utility: "low",
    balanced: "medium",
    strong: "high",
    max: "max",
  }[selection.selected_profile];
  return {
    contract_version: MODEL_CAPABILITY_MANIFEST.version,
    status: MODEL_CAPABILITY_MANIFEST.status,
    axes: [...MODEL_ROUTING_AXES],
    selection,
    autonomy_policy: {
      mode: "bounded_proactive",
      mutation_authority: "inherits_user_request",
      permission_boundary: "host_owned",
      confirmation_required_for: [
        "destructive_actions",
        "external_writes",
        "purchases",
        "material_scope_expansion",
      ],
    },
    execution_topology: modelExecutionTopology(classification, options.task_text || ""),
    reasoning_effort: {
      profile_default: genericEffort,
      provider_resolved: false,
    },
    review_policy: modelReviewPolicy(classification, selection.selected_profile),
    verification_gate: {
      policy: "deterministic_command_first",
      model_is_verifier: false,
      executed_evidence_required_when_available: true,
      model_review_is_material_only: true,
    },
    fallback_policy: MODEL_CAPABILITY_MANIFEST.fallback_policy,
  };
}

function resolveSessionModel(sessionModel, hostModelRecord = null) {
  const explicit = (sessionModel || "").trim();
  if (explicit !== "") {
    return { model: explicit, source: "explicit" };
  }
  const envModel = (process.env.MYTHIFY_SESSION_MODEL || "").trim();
  if (envModel !== "") {
    return { model: envModel, source: "env" };
  }
  if (hostModelRecord !== null && String(hostModelRecord.target_model || "").trim() !== "") {
    return { model: hostModelRecord.target_model.trim(), source: "host_model_switch" };
  }
  return { model: "", source: "unknown" };
}

function resolveSpawnCeiling(spawnCeiling) {
  const explicit = (spawnCeiling || "auto").trim();
  if (explicit !== "" && explicit !== "auto") {
    return { ceiling: explicit, source: "explicit" };
  }
  const envCeiling = (process.env.MYTHIFY_SPAWN_CEILING || "").trim();
  if (SPAWN_CEILINGS.includes(envCeiling) && envCeiling !== "auto") {
    return { ceiling: envCeiling, source: "env" };
  }
  return { ceiling: "same_or_lower", source: "default" };
}

function resolveReviewerStrength(reviewerStrength) {
  const explicit = (reviewerStrength || "auto").trim();
  if (explicit !== "" && explicit !== "auto") {
    return { policy: explicit, source: "explicit" };
  }
  const envStrength = (process.env.MYTHIFY_REVIEWER_STRENGTH || "").trim();
  if (REVIEWER_STRENGTH_MODES.includes(envStrength) && envStrength !== "auto") {
    return { policy: envStrength, source: "env" };
  }
  return { policy: "same_or_lower", source: "default" };
}

function roleModelRelation(role, sessionTier, ceiling) {
  if (role === "verifier") {
    return "none";
  }
  if (role === "triage") {
    return "lower_preferred";
  }
  if (ceiling === "allow_stronger") {
    return "may_exceed_session";
  }
  if (role === "reviewer") {
    return "same_or_lower";
  }
  if (ceiling === "lower_only") {
    return "lower_only";
  }
  if (sessionTier === "unknown") {
    return "same_or_lower_when_session_known";
  }
  return "same_or_lower";
}

function effortForRole(role, classification, requestedEffort) {
  const requested = (requestedEffort || "auto").trim();
  if (requested !== "auto") {
    return { effort: requested, effortPolicy: "explicit" };
  }
  const risk = classification.risk || "low";
  const ceremony = classification.ceremony || "none";
  if (role === "triage") {
    return { effort: "low", effortPolicy: "role_default" };
  }
  if (role === "fanout_worker") {
    if (risk === "high" || ceremony === "full") {
      return { effort: "high", effortPolicy: "risk_default" };
    }
    if (ceremony === "standard") {
      return { effort: "medium", effortPolicy: "role_default" };
    }
    return { effort: "low", effortPolicy: "role_default" };
  }
  if (role === "reviewer") {
    if (risk === "high" || ceremony === "full") {
      return { effort: "high", effortPolicy: "risk_default" };
    }
    if (risk === "medium" || ceremony === "standard") {
      return { effort: "medium", effortPolicy: "role_default" };
    }
    return { effort: "low", effortPolicy: "role_default" };
  }
  return { effort: "none", effortPolicy: "command_first" };
}

function speedForRole(role, requestedSpeed) {
  const requested = (requestedSpeed || "auto").trim();
  if (requested !== "auto") {
    return { speed: requested, speedPolicy: "explicit" };
  }
  if (role === "verifier") {
    return { speed: "none", speedPolicy: "command_first" };
  }
  return { speed: "auto", speedPolicy: "host_default" };
}

function reviewerSpawnPolicy(classification) {
  if (classification.risk === "high" || classification.ceremony === "full") {
    return "recommended";
  }
  if (classification.risk === "medium" || classification.ceremony === "standard") {
    return "optional";
  }
  return "skip";
}

function providerProfileResolution(provider, capabilityProfile) {
  const providerRows = PROVIDER_MODEL_PROFILES[provider] || {};
  const row = { ...(providerRows[capabilityProfile] || {}) };
  const resolution = row.resolution || "unavailable";
  const result = {
    provider: provider || "unknown",
    capability_profile: capabilityProfile,
    model: row.model || "",
    api_model: row.api_model || "",
    effort: row.effort || "auto",
    mode: row.mode || "",
    resolution,
    status: row.model ? "resolved" : "unavailable",
    fallback_policy: MODEL_CAPABILITY_MANIFEST.fallback_policy,
  };
  if (resolution === "runtime_catalog") {
    Object.assign(result, {
      status: "discovery_required",
      discovery_command: providerRows.discovery_command || "",
      fallback_model: providerRows.fallback_model || "",
      runtime_owner: providerRows.runtime_owner || "",
      preferred_terms: [...(row.preferred_terms || [])],
    });
  }
  if (row.domain_fallback) {
    result.domain_fallback = row.domain_fallback;
  }
  return result;
}

function hostRecommendationModel(platform, capabilityProfile) {
  const provider = PLATFORM_MODEL_PROVIDERS[platform] || "";
  const result = providerProfileResolution(provider, capabilityProfile);
  const legacyProfile = MODEL_CAPABILITY_MANIFEST.profiles[capabilityProfile].legacy_profile;
  const envNames = [`MYTHIFY_HOST_${capabilityProfile.toUpperCase()}_MODEL`];
  const legacyEnv = `MYTHIFY_HOST_${legacyProfile.toUpperCase()}_MODEL`;
  if (!envNames.includes(legacyEnv)) {
    envNames.push(legacyEnv);
  }
  for (const envName of envNames) {
    const envModel = (process.env[envName] || "").trim();
    if (envModel !== "") {
      return {
        ...result,
        model: envModel,
        api_model: "",
        resolution: "environment_override",
        status: "resolved",
        source: `env:${envName}`,
      };
    }
  }
  if (result.status === "resolved") {
    result.source = "platform_default";
  } else if (result.status === "discovery_required") {
    result.source = "runtime_catalog";
  } else {
    result.source = "none";
  }
  return result;
}

function hostRecommendationAction(sessionModel, sessionProfile, targetProfile, resolutionStatus) {
  if (resolutionStatus === "discovery_required") {
    return "recommend_discover";
  }
  if (!sessionModel) {
    return "recommend_set";
  }
  const sessionRank = CAPABILITY_PROFILE_RANK[sessionProfile] || 0;
  const targetRank = CAPABILITY_PROFILE_RANK[targetProfile] || CAPABILITY_PROFILE_RANK.balanced;
  if (sessionRank === 0) {
    return "recommend_set";
  }
  if (targetRank < sessionRank) {
    return "downgrade";
  }
  if (targetRank > sessionRank) {
    return "upgrade";
  }
  return "keep";
}

function hostPromptRecommendation(platform, sessionModel, modelRouter) {
  const selection = modelRouter.selection;
  const capabilityProfile = selection.selected_profile;
  const profile = MODEL_CAPABILITY_MANIFEST.profiles[capabilityProfile];
  const resolution = hostRecommendationModel(platform, capabilityProfile);
  const sessionProfile = classifyModelProfile(sessionModel);
  modelRouter.provider_resolution = { ...resolution };
  modelRouter.reasoning_effort = {
    profile_default: modelRouter.reasoning_effort.profile_default,
    provider_resolved: resolution.status === "resolved",
    provider: resolution.provider,
    selected: resolution.effort,
    mode: resolution.mode,
  };
  return {
    policy: "task_classification",
    action: hostRecommendationAction(
      sessionModel,
      sessionProfile,
      capabilityProfile,
      resolution.status
    ),
    target_profile: profile.legacy_profile,
    capability_profile: capabilityProfile,
    cost_class: profile.cost_class,
    target_provider: resolution.provider,
    target_model: resolution.model,
    target_api_model: resolution.api_model,
    target_model_source: resolution.source,
    target_model_status: resolution.status,
    target_model_tier: classifyModelTier(resolution.model),
    target_model_profile: classifyModelProfile(resolution.model),
    thinking: resolution.effort,
    speed: profile.default_speed,
    resolution,
    reason: selection.reason,
  };
}

function engineProfileResolution(engine, capabilityProfile) {
  const provider = {
    "codex-cli": "openai",
    "claude-cli": "anthropic",
    "cursor-agent": "cursor",
  }[engine] || "";
  return providerProfileResolution(provider, capabilityProfile);
}

export function buildModelPolicy(classification, options = {}) {
  const platform = normalizePlatform(options.platform || "auto");
  const modelRouter = buildModelRouter(classification, options);
  const requestedEffort = options.effort || "auto";
  const requestedSpeed = options.speed || "auto";
  const sessionModel = resolveSessionModel(options.session_model || "", options.host_model_record || null);
  const sessionTier = classifyModelTier(sessionModel.model);
  const spawnCeiling = resolveSpawnCeiling(options.spawn_ceiling || "auto");
  const reviewerStrength = resolveReviewerStrength(options.reviewer_strength || "auto");
  const { engine: triageEngine, enginePolicy: triageEnginePolicy } = selectTriageEngine(
    options.triage_engine || "",
    platform
  );
  const { engine: workerEngine, enginePolicy: workerEnginePolicy } = selectWorkerEngine(platform);
  const { model: triageModel, modelPolicy: triageModelPolicy } = resolveTriageModelSelection(
    triageEngine,
    options.triage_model || ""
  );
  const triageEffort = effortForRole("triage", classification, requestedEffort);
  const fanoutEffort = effortForRole("fanout_worker", classification, requestedEffort);
  const reviewerEffort = effortForRole("reviewer", classification, requestedEffort);
  const triageSpeed = speedForRole("triage", requestedSpeed);
  const fanoutSpeed = speedForRole("fanout_worker", requestedSpeed);
  const reviewerSpeed = speedForRole("reviewer", requestedSpeed);
  const timeoutSeconds =
    typeof options.triage_timeout_seconds === "number" && options.triage_timeout_seconds > 0
      ? options.triage_timeout_seconds
      : 120;
  const hostRecommendation = hostPromptRecommendation(
    platform,
    sessionModel.model,
    modelRouter
  );
  const workerProfile = ["utility", "balanced"].includes(
    modelRouter.selection.selected_profile
  )
    ? modelRouter.selection.selected_profile
    : "balanced";
  const reviewerProfile = modelRouter.review_policy.recommended_profile;
  const workerResolution = engineProfileResolution(workerEngine, workerProfile);
  const reviewerResolution = engineProfileResolution(workerEngine, reviewerProfile);
  const providerDefaults = buildProviderDefaults();
  return {
    model_router: modelRouter,
    provider_defaults: providerDefaults,
    session: {
      role: "current_conversation",
      control: "host_selected",
      platform,
      ...roleProviderFields(providerDefaults, "session"),
      ...roleBudgetFields(providerDefaults, "session"),
      model: sessionModel.model,
      model_source: sessionModel.source,
      model_tier: sessionTier,
      capability_profile: classifyModelProfile(sessionModel.model),
      model_policy: "host_default",
      effort_policy: requestedEffort === "auto" ? "host_default" : `requested_${requestedEffort}`,
      speed_policy: requestedSpeed === "auto" ? "host_default" : `requested_${requestedSpeed}`,
      spawn_ceiling: spawnCeiling.ceiling,
      spawn_ceiling_source: spawnCeiling.source,
      recommendation: hostRecommendation,
      reason:
        "The active chat model belongs to the desktop or CLI host. Mythify records the policy and controls only spawned workers.",
    },
    spawn_ceiling: {
      policy: spawnCeiling.ceiling,
      source: spawnCeiling.source,
      session_model: sessionModel.model,
      session_model_source: sessionModel.source,
      session_model_tier: sessionTier,
      default: "same_or_lower",
      stronger_requires: "spawn_ceiling_allow_stronger_or_reviewer_specific_opt_in",
    },
    triage: {
      role: "problem_framing",
      spawn: classification.model_triage || "skip",
      ...roleProviderFields(providerDefaults, "triage"),
      ...roleBudgetFields(
        providerDefaults,
        "triage",
        timeoutSeconds,
        "triage_timeout_seconds_or_default"
      ),
      ...engineWarningMetadata(triageEngine),
      engine: triageEngine || "auto",
      engine_policy: triageEnginePolicy,
      model: triageModel,
      model_tier: classifyModelTier(triageModel),
      capability_profile: "utility",
      model_relation_to_session: roleModelRelation("triage", sessionTier, spawnCeiling.ceiling),
      model_policy: triageModelPolicy,
      effort: triageEffort.effort,
      effort_policy: triageEffort.effortPolicy,
      speed: triageSpeed.speed,
      speed_policy: triageSpeed.speedPolicy,
      timeout_seconds: timeoutSeconds,
      max_turns: 1,
      sandbox: "read-only",
      reason: "Use a cheap local CLI or command pass to frame the problem before planning.",
    },
    reader: {
      role: "read_only_material_inspection",
      spawn: "optional",
      ...roleProviderFields(providerDefaults, "reader"),
      ...roleBudgetFields(providerDefaults, "reader"),
      model_policy: "local_openai_compatible_when_configured",
      capability_profile: "utility",
      model_relation_to_session: "lower_preferred",
      effort: "low",
      effort_policy: "role_default",
      speed: "auto",
      speed_policy: "provider_default",
      writes_state: false,
      evidence_status: "model_output_not_verification",
      reason: "Reader output is material for the orchestrator, not verification evidence.",
    },
    fanout_worker: {
      role: "independent_subtask",
      spawn: classification.fanout || "not_recommended",
      ...roleProviderFields(providerDefaults, "fanout_worker"),
      ...roleBudgetFields(providerDefaults, "fanout_worker"),
      ...engineWarningMetadata(workerEngine),
      engine: workerEngine,
      engine_policy: workerEnginePolicy,
      model_policy: "per_task_over_job_over_env_over_engine_default",
      capability_profile: workerProfile,
      recommended_model: workerResolution.model,
      recommended_model_status: workerResolution.status,
      recommended_model_resolution: workerResolution,
      recommended_effort: workerResolution.effort,
      model_relation_to_session: roleModelRelation(
        "fanout_worker",
        sessionTier,
        spawnCeiling.ceiling
      ),
      effort: fanoutEffort.effort,
      effort_policy: fanoutEffort.effortPolicy,
      speed: fanoutSpeed.speed,
      speed_policy: fanoutSpeed.speedPolicy,
      visibility: classification.fanout_visibility || "summary",
      visibility_policy: classification.fanout_visibility_source || "default",
      visibility_modes: FANOUT_VISIBILITY_MODES,
      visibility_reason:
        classification.fanout_visibility_reason || "Summary visibility is the default.",
      timeout_seconds: 600,
      reason:
        "Spawn only independent tasks. The fanout_start tool can set engine, model, effort, speed, and visibility per job.",
    },
    reviewer: {
      role: "independent_review",
      spawn: reviewerSpawnPolicy(classification),
      ...roleProviderFields(providerDefaults, "reviewer"),
      ...roleBudgetFields(providerDefaults, "reviewer"),
      ...engineWarningMetadata(workerEngine),
      engine: workerEngine,
      engine_policy: workerEnginePolicy,
      model_policy: "prefer_stronger_than_worker_when_available",
      capability_profile: reviewerProfile,
      recommended_model: reviewerResolution.model,
      recommended_model_status: reviewerResolution.status,
      recommended_model_resolution: reviewerResolution,
      recommended_effort: reviewerResolution.effort,
      stronger_model_policy: reviewerStrength.policy,
      stronger_model_policy_source: reviewerStrength.source,
      stronger_models_allowed: reviewerStrength.policy === "allow_stronger",
      stronger_requires: "reviewer_strength_allow_stronger_or_fanout_reviewer_allow_stronger",
      model_relation_to_session:
        reviewerStrength.policy === "allow_stronger"
          ? "may_exceed_session_with_reviewer_opt_in"
          : roleModelRelation("reviewer", sessionTier, spawnCeiling.ceiling),
      effort: reviewerEffort.effort,
      effort_policy: reviewerEffort.effortPolicy,
      speed: reviewerSpeed.speed,
      speed_policy: reviewerSpeed.speedPolicy,
      reason: "Use a separate review pass for high-risk or broad changes.",
    },
    verifier: {
      role: "evidence",
      spawn: "not_model_based",
      ...roleProviderFields(providerDefaults, "verifier"),
      ...roleBudgetFields(providerDefaults, "verifier"),
      engine: "local_command",
      model_policy: "none_when_executable_check_exists",
      capability_profile: "none",
      model_relation_to_session: roleModelRelation("verifier", sessionTier, spawnCeiling.ceiling),
      effort: "none",
      effort_policy: "command_first",
      speed: "none",
      speed_policy: "command_first",
      reason: "Executable verify_run evidence beats model judgment.",
    },
  };
}

function triageEnv(model, speed = "auto") {
  return {
    ...process.env,
    TERM: "dumb",
    MYTHIFY_FANOUT_DEPTH: "1",
    MYTHIFY_DISABLE_FANOUT: "1",
    MYTHIFY_TRIAGE_MODEL: model || "",
    MYTHIFY_TRIAGE_SPEED: speed || "auto",
  };
}

function runTriageProcess({ command, bin, args, cwd, prompt, timeoutSeconds, env }) {
  const startedNs = process.hrtime.bigint();
  const options = {
    cwd,
    env,
    input: prompt,
    encoding: "utf8",
    timeout: Math.round(timeoutSeconds * 1000),
    maxBuffer: 1024 * 1024,
  };
  const result =
    command !== undefined
      ? spawnSync(command, { ...options, shell: true })
      : spawnSync(bin, args, options);
  const durationSeconds = Number((Number(process.hrtime.bigint() - startedNs) / 1e9).toFixed(3));
  const timedOut = result.error && result.error.code === "ETIMEDOUT";
  return {
    exit_code: result.status === null || result.status === undefined ? -1 : result.status,
    duration_seconds: durationSeconds,
    stdout_tail: tailText(result.stdout || ""),
    stderr_tail: tailText(result.stderr || (result.error ? result.error.message : "")),
    timed_out: Boolean(timedOut),
  };
}

function parseModelTriageJson(text) {
  const raw = String(text || "").trim();
  const candidates = [raw];
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start !== -1 && end > start) {
    candidates.push(raw.slice(start, end + 1));
  }
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed;
      }
    } catch {
      // Try the next candidate.
    }
  }
  return null;
}

function tempStamp() {
  return new Date().toISOString().replace(/[^0-9A-Za-z]+/g, "-").replace(/^-|-$/g, "");
}

function tempTriagePath(prefix, suffix) {
  return path.join(os.tmpdir(), `${prefix}-${tempStamp()}-${crypto.randomBytes(3).toString("hex")}${suffix}`);
}

function runClaudeTriage(prompt, model, timeoutSeconds, cwd, speed = "auto") {
  const bin = resolveClaudeTriageBin();
  if (bin === null) {
    return { exit_code: 127, duration_seconds: 0, stdout_tail: "", stderr_tail: "claude binary not found", timed_out: false };
  }
  const args = [
    "-p",
    "--output-format",
    "json",
    "--model",
    model || "haiku",
    "--max-turns",
    process.env.MYTHIFY_TRIAGE_MAX_TURNS || "1",
    ...splitShellArgs(process.env.MYTHIFY_TRIAGE_CLAUDE_ARGS || ""),
  ];
  const result = runTriageProcess({
    bin,
    args,
    cwd,
    prompt,
    timeoutSeconds,
    env: triageEnv(model, speed),
  });
  try {
    const parsed = JSON.parse(result.stdout_tail);
    result.output_tail = parsed && typeof parsed.result === "string" ? tailText(parsed.result) : result.stdout_tail;
  } catch {
    result.output_tail = result.stdout_tail;
  }
  return result;
}

function codexSpeedArgs(speed) {
  if (speed === "fast") {
    return ["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"];
  }
  if (speed === "standard") {
    return ["-c", "features.fast_mode=false"];
  }
  return [];
}

function runCodexTriage(prompt, model, timeoutSeconds, cwd, speed = "auto") {
  const bin = resolveCodexTriageBin();
  if (bin === null) {
    return { exit_code: 127, duration_seconds: 0, stdout_tail: "", stderr_tail: "codex binary not found", timed_out: false };
  }
  const outputFile = tempTriagePath("mythify-codex-triage", ".md");
  const args = [
    "--ask-for-approval",
    "never",
    "exec",
    "--cd",
    cwd,
    "--sandbox",
    process.env.MYTHIFY_TRIAGE_CODEX_SANDBOX || "read-only",
    "--skip-git-repo-check",
    "--ephemeral",
    "--color",
    "never",
    "--output-last-message",
    outputFile,
  ];
  if ((model || "").trim() !== "") {
    args.push("--model", model);
  }
  args.push(...codexSpeedArgs(speed));
  args.push(...splitShellArgs(process.env.MYTHIFY_TRIAGE_CODEX_ARGS || ""));
  args.push("-");
  const result = runTriageProcess({
    bin,
    args,
    cwd,
    prompt,
    timeoutSeconds,
    env: triageEnv(model, speed),
  });
  try {
    result.output_tail = fs.existsSync(outputFile) ? tailText(fs.readFileSync(outputFile, "utf8")) : result.stdout_tail;
  } catch {
    result.output_tail = result.stdout_tail;
  }
  try {
    fs.rmSync(outputFile, { force: true });
  } catch {
    // Best effort cleanup.
  }
  return result;
}

function runCursorTriage(prompt, model, timeoutSeconds, cwd, speed = "auto") {
  const invocation = resolveCursorTriageInvocation();
  if (invocation === null) {
    return { exit_code: 127, duration_seconds: 0, stdout_tail: "", stderr_tail: "cursor-agent or cursor binary not found", timed_out: false };
  }
  const promptFile = tempTriagePath("mythify-cursor-triage", ".md");
  fs.writeFileSync(promptFile, prompt, "utf8");
  const args = [
    ...invocation.prefixArgs,
    "--print",
    "--output-format",
    "text",
    "--trust",
    "--workspace",
    cwd,
  ];
  const mode = process.env.MYTHIFY_TRIAGE_CURSOR_MODE === undefined ? "ask" : process.env.MYTHIFY_TRIAGE_CURSOR_MODE.trim();
  if (mode !== "") {
    args.push("--mode", mode);
  }
  if ((model || "").trim() !== "") {
    args.push("--model", model);
  }
  if (process.env.MYTHIFY_TRIAGE_CURSOR_FORCE === "1") {
    args.push("--force");
  }
  args.push(...splitShellArgs(process.env.MYTHIFY_TRIAGE_CURSOR_ARGS || ""));
  args.push(`Read the triage prompt from this file and return only the requested JSON: ${promptFile}`);
  const result = runTriageProcess({
    bin: invocation.bin,
    args,
    cwd,
    prompt: "",
    timeoutSeconds,
    env: triageEnv(model, speed),
  });
  result.output_tail = result.stdout_tail;
  try {
    fs.rmSync(promptFile, { force: true });
  } catch {
    // Best effort cleanup.
  }
  return result;
}

function runCommandTriage(prompt, model, timeoutSeconds, cwd, speed = "auto") {
  const command = commandTriageTemplate();
  if (command === "") {
    return { exit_code: 127, duration_seconds: 0, stdout_tail: "", stderr_tail: "MYTHIFY_TRIAGE_COMMAND is not set", timed_out: false };
  }
  const result = runTriageProcess({
    command,
    cwd,
    prompt,
    timeoutSeconds,
    env: triageEnv(model, speed),
  });
  result.output_tail = result.stdout_tail;
  return result;
}

export function runModelTriage(taskText, classification, options = {}) {
  const mode = options.triage || "never";
  if (!shouldRunModelTriage(classification, mode)) {
    return {
      attempted: false,
      reason: `triage mode ${mode} with gate ${classification.model_triage}`,
    };
  }
  const platform = normalizePlatform(options.platform || "auto");
  const { engine, enginePolicy } = selectTriageEngine(options.triage_engine || "", platform);
  if (engine === "") {
    return {
      attempted: true,
      ok: false,
      engine: "",
      engine_policy: enginePolicy,
      model: "",
      model_policy: "unavailable",
      effort: "low",
      speed: "auto",
      duration_seconds: 0,
      exit_code: 127,
      error:
        "No fast triage engine is available. Configure a local engine with MYTHIFY_TRIAGE_ENGINE plus the matching CLI login, or set MYTHIFY_TRIAGE_COMMAND for a command that reads the prompt on stdin.",
      output_tail: "",
      parsed: null,
    };
  }
  if (!TRIAGE_ENGINES.includes(engine)) {
    return {
      attempted: true,
      ok: false,
      engine,
      engine_policy: enginePolicy,
      model: "",
      model_policy: "unavailable",
      effort: "low",
      speed: "auto",
      duration_seconds: 0,
      exit_code: 127,
      error: `Unknown triage engine ${engine}. Valid engines: ${TRIAGE_ENGINES.join(", ")}.`,
      output_tail: "",
      parsed: null,
    };
  }
  const { model, modelPolicy } = resolveTriageModelSelection(engine, options.triage_model || "");
  const { effort, effortPolicy } = effortForRole(
    "triage",
    classification,
    options.effort || "auto"
  );
  const { speed, speedPolicy } = speedForRole("triage", options.speed || "auto");
  const timeoutSeconds =
    typeof options.triage_timeout_seconds === "number" && options.triage_timeout_seconds > 0
      ? options.triage_timeout_seconds
      : 120;
  const cwd = options.cwd || process.cwd();
  const prompt = buildTriagePrompt(taskText, classification);
  let raw;
  if (engine === "claude-cli") {
    raw = runClaudeTriage(prompt, model, timeoutSeconds, cwd, speed);
  } else if (engine === "codex-cli") {
    raw = runCodexTriage(prompt, model, timeoutSeconds, cwd, speed);
  } else if (engine === "cursor-agent") {
    raw = runCursorTriage(prompt, model, timeoutSeconds, cwd, speed);
  } else {
    raw = runCommandTriage(prompt, model, timeoutSeconds, cwd, speed);
  }
  const outputTail = raw.output_tail || raw.stdout_tail || "";
  const parsed = parseModelTriageJson(outputTail);
  let error = "";
  if (raw.exit_code !== 0) {
    error = raw.stderr_tail || `triage worker exited ${raw.exit_code}`;
  } else if (parsed === null) {
    error = "triage worker exited 0 but did not return valid JSON";
  }
  return {
    attempted: true,
    ok: raw.exit_code === 0 && parsed !== null,
    engine,
    engine_policy: enginePolicy,
    model,
    model_policy: modelPolicy,
    effort,
    effort_policy: effortPolicy,
    speed,
    speed_policy: speedPolicy,
    duration_seconds: raw.duration_seconds || 0,
    exit_code: raw.exit_code,
    error,
    output_tail: outputTail,
    stderr_tail: raw.stderr_tail || "",
    timed_out: Boolean(raw.timed_out),
    parsed,
  };
}
