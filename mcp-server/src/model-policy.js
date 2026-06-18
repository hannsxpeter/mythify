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
  FANOUT_VISIBILITY_MODES,
  HOST_MODEL_DEFAULTS,
  HOST_PLATFORMS as PLATFORMS,
  HOST_PROFILE_RANK,
  MODEL_TIER_RANK,
  REVIEWER_STRENGTH_MODES,
  SPAWN_CEILINGS,
  STRONG_HOST_TASK_TYPES,
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
  return engine === "claude-cli" ? "haiku" : "";
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

export function classifyModelTier(model) {
  const compact = String(model || "").toLowerCase().replace(/[_ ]+/g, "-");
  if (compact === "") {
    return "unknown";
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

function hostRecommendationProfile(classification) {
  const taskType = classification.task_type || "feature";
  const risk = classification.risk || "low";
  const ambiguity = classification.ambiguity || "low";
  const ceremony = classification.ceremony || "none";
  const executionProfile = classification.execution_profile || "standard";
  if (
    ["trivial", "question"].includes(taskType) &&
    risk === "low" &&
    executionProfile === "direct"
  ) {
    return {
      target_profile: "fast",
      thinking: "low",
      speed: "fast",
      reason: "Direct low-risk prompts should use the cheapest responsive host settings.",
    };
  }
  if (
    STRONG_HOST_TASK_TYPES.includes(taskType) ||
    risk === "high" ||
    ceremony === "full"
  ) {
    return {
      target_profile: "strong",
      thinking: "high",
      speed: "standard",
      reason:
        "Research, benchmark, release, security, migration, and design work benefit from stronger reasoning.",
    };
  }
  if (executionProfile === "fast" || ceremony === "light") {
    return {
      target_profile: "fast",
      thinking: "low",
      speed: "fast",
      reason: "Focused low-risk work is a good fit for fast host settings.",
    };
  }
  if (ambiguity === "high") {
    return {
      target_profile: "standard",
      thinking: "medium",
      speed: "auto",
      reason:
        "Ambiguous work needs enough reasoning to frame the problem, but more model size will not replace missing context.",
    };
  }
  return {
    target_profile: "standard",
    thinking: "medium",
    speed: "auto",
    reason: "Normal implementation, debugging, review, and docs work should use balanced host settings.",
  };
}

function hostRecommendationModel(platform, targetProfile) {
  const envName = `MYTHIFY_HOST_${targetProfile.toUpperCase()}_MODEL`;
  const envModel = (process.env[envName] || "").trim();
  if (envModel !== "") {
    return { model: envModel, source: `env:${envName}` };
  }
  const defaultModel = HOST_MODEL_DEFAULTS[platform]?.[targetProfile] || "";
  if (defaultModel !== "") {
    return { model: defaultModel, source: "platform_default" };
  }
  return { model: "", source: "none" };
}

function hostRecommendationAction(sessionModel, sessionTier, targetProfile) {
  if (!sessionModel) {
    return "recommend_set";
  }
  const sessionRank = MODEL_TIER_RANK[sessionTier] || 0;
  const targetRank = HOST_PROFILE_RANK[targetProfile] || MODEL_TIER_RANK.standard;
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

function hostPromptRecommendation(classification, platform, sessionModel, sessionTier) {
  const profile = hostRecommendationProfile(classification);
  const targetProfile = profile.target_profile;
  const targetModel = hostRecommendationModel(platform, targetProfile);
  return {
    policy: "task_classification",
    action: hostRecommendationAction(sessionModel, sessionTier, targetProfile),
    target_profile: targetProfile,
    target_model: targetModel.model,
    target_model_source: targetModel.source,
    target_model_tier: classifyModelTier(targetModel.model),
    thinking: profile.thinking,
    speed: profile.speed,
    reason: profile.reason,
  };
}

export function buildModelPolicy(classification, options = {}) {
  const platform = normalizePlatform(options.platform || "auto");
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
    classification,
    platform,
    sessionModel.model,
    sessionTier
  );
  const providerDefaults = buildProviderDefaults();
  return {
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
