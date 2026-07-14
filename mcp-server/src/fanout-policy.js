import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import { MODEL_CAPABILITY_MANIFEST } from "./capability-registry.js";

export const ENGINES = [
  "claude-cli",
  "claude-ultracode",
  "codex-cli",
  "cursor-agent",
  "anthropic",
  "openai",
  "command",
];
export const DEFAULT_WORKER_ENGINE = "codex-cli";
export const CLAUDE_ULTRACODE_MIN_VERSION = "2.1.203";
export const CLAUDE_CLI_COST_WARNING =
  "Selecting claude-cli runs Claude Code non-interactively through claude -p. " +
  "Claude Code usage is token-cost-sensitive; included usage applies only within plan limits. " +
  "If usage credits are enabled and included limits are reached, continued usage can be billed at standard API pricing.";
export const CLAUDE_CLI_COST_WARNING_URLS = [
  "https://code.claude.com/docs/en/headless",
  "https://code.claude.com/docs/en/costs",
  "https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans",
];
export const CLAUDE_ULTRACODE_COST_WARNING =
  "Selecting claude-ultracode launches one native Claude dynamic workflow through " +
  "claude -p --effort ultracode. UltraCode uses xhigh reasoning plus workflow subagents, " +
  "so it can consume substantially more subscription quota or paid usage credits than a normal worker.";
export const CLAUDE_ULTRACODE_COST_WARNING_URLS = [
  "https://code.claude.com/docs/en/workflows",
  ...CLAUDE_CLI_COST_WARNING_URLS,
];
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
export const SPAWN_CEILINGS = ["auto", "lower_only", "same_or_lower", "allow_stronger"];
export const TASK_ROLES = ["worker", "reviewer"];
export const FANOUT_VISIBILITY_MODES = ["auto", "quiet", "summary", "verbose", "threaded"];
export const HOSTED_PROVIDER_ENGINES = ["anthropic", "openai"];
export const HOSTED_PROVIDER_REQUIRED_ACKS = [
  "hosted_provider_billing_ack",
  "hosted_provider_data_ack",
  "hosted_provider_material_ack",
];
export const MODEL_TIER_RANK = {
  unknown: 0,
  small: 1,
  fast: 2,
  standard: 3,
  strong: 4,
  frontier: 5,
};

let io = null;

export function configureFanoutPolicy(deps) {
  io = deps;
}

export function intEnv(name, fallback) {
  const raw = (process.env[name] || "").trim();
  if (raw === "") {
    return fallback;
  }
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export function positiveIntEnvWithSource(name, fallback) {
  const raw = (process.env[name] || "").trim();
  if (raw === "") {
    return { value: fallback, source: "default" };
  }
  const value = Number.parseInt(raw, 10);
  if (Number.isFinite(value) && value > 0) {
    return { value, source: `env:${name}` };
  }
  return { value: fallback, source: "default_invalid_env_ignored" };
}

export function envSet(name) {
  return (process.env[name] || "").trim() !== "";
}

function augmentedPath() {
  const parts = (process.env.PATH || "").split(path.delimiter).filter((p) => p !== "");
  for (const extra of [path.join(os.homedir(), ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin"]) {
    if (!parts.includes(extra)) {
      parts.push(extra);
    }
  }
  return parts.join(path.delimiter);
}

function curatedLocalCliEnv() {
  const env = {
    HOME: process.env.HOME || os.homedir(),
    TERM: "dumb",
    PATH: augmentedPath(),
    MYTHIFY_FANOUT_DEPTH: "1",
    MYTHIFY_DISABLE_FANOUT: "1",
  };
  if (envSet("CODEX_HOME")) {
    env.CODEX_HOME = process.env.CODEX_HOME;
  }
  if (envSet("XDG_CONFIG_HOME")) {
    env.XDG_CONFIG_HOME = process.env.XDG_CONFIG_HOME;
  }
  return env;
}

export function containsPhrase(text, phrases) {
  const normalized = String(text || "").toLowerCase().split(/\s+/).join(" ");
  return phrases.some((phrase) => normalized.includes(String(phrase).toLowerCase()));
}

export function inferVisibilityFromText(text) {
  const quietTerms = [
    "quiet",
    "quietly",
    "silent",
    "silently",
    "background only",
    "do not show worker",
    "don't show worker",
    "do not show subagent",
    "don't show subagent",
    "no worker details",
    "minimal progress",
  ];
  const threadedTerms = [
    "threaded",
    "visible thread",
    "visible threads",
    "separate thread",
    "separate threads",
    "separate chat",
    "separate chats",
    "show subagent chats",
    "show sub-agent chats",
    "visible subagent",
    "visible sub-agent",
  ];
  const verboseTerms = [
    "verbose",
    "show details",
    "show full",
    "show logs",
    "show worker output",
    "show subagent output",
    "show sub-agent output",
    "detailed progress",
    "full worker output",
  ];
  if (containsPhrase(text, quietTerms)) {
    return {
      visibility: "quiet",
      source: "prompt",
      reason: "The prompt asks to keep background worker activity quiet.",
    };
  }
  if (containsPhrase(text, threadedTerms)) {
    return {
      visibility: "threaded",
      source: "prompt",
      reason:
        "The prompt asks for visible worker threads or separate chats when the host supports them.",
    };
  }
  if (containsPhrase(text, verboseTerms)) {
    return {
      visibility: "verbose",
      source: "prompt",
      reason: "The prompt asks to see detailed worker output or progress.",
    };
  }
  return {
    visibility: "summary",
    source: "default",
    reason:
      "Summary visibility is the default: show worker titles, status, and notable results without flooding the chat.",
  };
}

export function resolveVisibilitySelection(visibility, purpose, tasks) {
  const requested = (visibility || "").trim();
  if (requested !== "" && requested !== "auto") {
    return {
      visibility: requested,
      source: "explicit",
      requested: requested,
      reason: `The caller explicitly requested ${requested} fanout visibility.`,
    };
  }
  const envVisibility = (process.env.MYTHIFY_FANOUT_VISIBILITY || "").trim();
  if (
    envVisibility !== "" &&
    envVisibility !== "auto" &&
    FANOUT_VISIBILITY_MODES.includes(envVisibility)
  ) {
    return {
      visibility: envVisibility,
      source: "env",
      requested: requested || "auto",
      reason: `MYTHIFY_FANOUT_VISIBILITY requested ${envVisibility}.`,
    };
  }
  const promptText = [
    purpose || "",
    ...(Array.isArray(tasks)
      ? tasks.map((task) => `${task?.title || ""}\n${task?.prompt || ""}`)
      : []),
  ].join("\n");
  const inferred = inferVisibilityFromText(promptText);
  return {
    ...inferred,
    requested: requested || envVisibility || "auto",
  };
}

export function visibilityGuidance(visibility) {
  if (visibility === "quiet") {
    return "Chat visibility: quiet. Keep worker activity out of the user-facing chat unless a failure blocks progress.";
  }
  if (visibility === "verbose") {
    return "Chat visibility: verbose. It is appropriate to show detailed worker progress or outputs when useful.";
  }
  if (visibility === "threaded") {
    return "Chat visibility: threaded. Create visible host threads only when the host supports native thread creation; otherwise fall back to summary.";
  }
  return "Chat visibility: summary. Show worker titles, status counts, and notable findings without flooding the chat.";
}

export function killSwitchText() {
  if (process.env.MYTHIFY_DISABLE_FANOUT === "1") {
    return (
      "[FAIL] Fanout is disabled: the server environment sets MYTHIFY_DISABLE_FANOUT=1, " +
      "which disables fanout_start, fanout_status, and fanout_results. " +
      "Unset MYTHIFY_DISABLE_FANOUT to re-enable parallel delegation."
    );
  }
  return null;
}

export function depthGuardText() {
  if (envSet("MYTHIFY_FANOUT_DEPTH")) {
    return (
      "[FAIL] Fanout depth limit reached: MYTHIFY_FANOUT_DEPTH is set in this server's " +
      "environment, which means this process is already a fanout worker. Nested fanout " +
      "is not allowed (the depth limit is one); do the work directly instead."
    );
  }
  return null;
}

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

export function fanoutRootDir() {
  return path.join(io.resolveStateDir(), "fanout");
}

// Project root is the parent of the .mythify state directory; claude-cli
// workers run with this as their cwd and relative context_paths resolve here.
export function projectRootDir() {
  return path.dirname(io.resolveStateDir());
}

// ---------------------------------------------------------------------------
// Engine and model resolution
// ---------------------------------------------------------------------------

export function isExecutableFile(filePath) {
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

export function findExecutableOnPath(binaryName) {
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

// Desktop MCP clients often launch servers with a minimal PATH, so local CLI
// engines resolve their binaries explicitly through an env override, PATH, and
// common install locations.
export function resolveBinary(envName, binaryName, fallbacks) {
  const explicit = (process.env[envName] || "").trim();
  if (explicit !== "") {
    return isExecutableFile(explicit) ? explicit : null;
  }
  const onPath = findExecutableOnPath(binaryName);
  if (onPath !== null) {
    return onPath;
  }
  for (const candidate of fallbacks) {
    if (isExecutableFile(candidate)) {
      return candidate;
    }
  }
  return null;
}

export function resolveClaudeBin() {
  return resolveBinary("MYTHIFY_FANOUT_CLAUDE_BIN", "claude", [
    path.join(os.homedir(), ".claude", "local", "claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
  ]);
}

export function resolveCodexBin() {
  return resolveBinary("MYTHIFY_FANOUT_CODEX_BIN", "codex", [
    path.join(os.homedir(), ".local", "bin", "codex"),
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
  ]);
}

export function resolveCursorInvocation() {
  const explicit = (process.env.MYTHIFY_FANOUT_CURSOR_BIN || "").trim();
  if (explicit !== "") {
    if (!isExecutableFile(explicit)) {
      return null;
    }
    const base = path.basename(explicit);
    return { bin: explicit, prefixArgs: base === "cursor" ? ["agent"] : [] };
  }
  const explicitAgent = (process.env.MYTHIFY_FANOUT_CURSOR_AGENT_BIN || "").trim();
  if (explicitAgent !== "") {
    return isExecutableFile(explicitAgent) ? { bin: explicitAgent, prefixArgs: [] } : null;
  }
  const userLocalAgent = path.join(os.homedir(), ".local", "bin", "cursor-agent");
  if (isExecutableFile(userLocalAgent)) {
    return { bin: userLocalAgent, prefixArgs: [] };
  }
  const pathAgent = findExecutableOnPath("cursor-agent");
  if (pathAgent !== null) {
    return { bin: pathAgent, prefixArgs: [] };
  }
  for (const candidate of ["/opt/homebrew/bin/cursor-agent", "/usr/local/bin/cursor-agent"]) {
    if (isExecutableFile(candidate)) {
      return { bin: candidate, prefixArgs: [] };
    }
  }
  const cursor = resolveBinary("MYTHIFY_FANOUT_CURSOR_BIN", "cursor", [
    path.join(os.homedir(), ".local", "bin", "cursor"),
    "/opt/homebrew/bin/cursor",
    "/usr/local/bin/cursor",
  ]);
  if (cursor !== null) {
    return { bin: cursor, prefixArgs: ["agent"] };
  }
  return null;
}

export function claudeBinFailureText() {
  return (
    "no claude binary was found (checked MYTHIFY_FANOUT_CLAUDE_BIN, claude on PATH, " +
    "~/.claude/local/claude, /opt/homebrew/bin/claude, /usr/local/bin/claude). " +
    "Set MYTHIFY_FANOUT_CLAUDE_BIN to the claude binary path, or pick another engine."
  );
}

function compareVersionParts(left, right) {
  const leftParts = String(left).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const rightParts = String(right).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const count = Math.max(leftParts.length, rightParts.length);
  for (let i = 0; i < count; i += 1) {
    const difference = (leftParts[i] || 0) - (rightParts[i] || 0);
    if (difference !== 0) {
      return difference;
    }
  }
  return 0;
}

export function probeClaudeUltracodeSupport(bin = resolveClaudeBin()) {
  if (bin === null) {
    return {
      ok: false,
      version: "",
      minimum_version: CLAUDE_ULTRACODE_MIN_VERSION,
      error: claudeBinFailureText(),
    };
  }
  const options = {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 10000,
    maxBuffer: 1024 * 1024,
  };
  const versionResult = spawnSync(bin, ["--version"], options);
  const versionText = `${versionResult.stdout || ""}\n${versionResult.stderr || ""}`.trim();
  const match = versionText.match(/\b(\d+)\.(\d+)\.(\d+)\b/);
  if (versionResult.status !== 0 || match === null) {
    return {
      ok: false,
      version: "",
      minimum_version: CLAUDE_ULTRACODE_MIN_VERSION,
      error:
        `could not confirm Claude Code ${CLAUDE_ULTRACODE_MIN_VERSION} or newer from ` +
        `"${bin} --version". Run "claude update" and retry.`,
    };
  }
  const version = match[0];
  if (compareVersionParts(version, CLAUDE_ULTRACODE_MIN_VERSION) < 0) {
    return {
      ok: false,
      version,
      minimum_version: CLAUDE_ULTRACODE_MIN_VERSION,
      error:
        `Claude Code ${version} is installed, but native UltraCode requires ` +
        `${CLAUDE_ULTRACODE_MIN_VERSION} or newer. Run "claude update" and retry.`,
    };
  }
  const helpResult = spawnSync(bin, ["--help"], options);
  const helpText = `${helpResult.stdout || ""}\n${helpResult.stderr || ""}`;
  if (helpResult.status !== 0 || !/\bultracode\b/i.test(helpText)) {
    return {
      ok: false,
      version,
      minimum_version: CLAUDE_ULTRACODE_MIN_VERSION,
      error:
        `Claude Code ${version} does not advertise native UltraCode support. ` +
        'Run "claude update" and confirm "claude --help" lists ultracode.',
    };
  }
  return {
    ok: true,
    version,
    minimum_version: CLAUDE_ULTRACODE_MIN_VERSION,
    error: null,
  };
}

export function codexBinFailureText() {
  return (
    "no codex binary was found (checked MYTHIFY_FANOUT_CODEX_BIN, codex on PATH, " +
    "~/.local/bin/codex, /opt/homebrew/bin/codex, /usr/local/bin/codex). " +
    "Set MYTHIFY_FANOUT_CODEX_BIN to the codex binary path, or pick another engine."
  );
}

export function cursorBinFailureText() {
  return (
    "no cursor-agent or cursor binary was found (checked MYTHIFY_FANOUT_CURSOR_BIN, " +
    "MYTHIFY_FANOUT_CURSOR_AGENT_BIN, cursor-agent on PATH, cursor on PATH, " +
    "~/.local/bin, /opt/homebrew/bin, and /usr/local/bin). Set " +
    "MYTHIFY_FANOUT_CURSOR_BIN to the cursor-agent or cursor binary path, or pick another engine."
  );
}

export function inferHostPlatform() {
  const configured = (process.env.MYTHIFY_HOST_PLATFORM || "").trim();
  if (configured !== "" && configured !== "auto") {
    return HOST_PLATFORMS.includes(configured) ? configured : "unknown";
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

export function preferredLocalEngine(platform) {
  return fanoutEngineAvailable(DEFAULT_WORKER_ENGINE) ? DEFAULT_WORKER_ENGINE : "";
}

export function fanoutEngineAvailable(engine) {
  if (engine === "claude-cli") {
    return resolveClaudeBin() !== null;
  }
  if (engine === "codex-cli") {
    return resolveCodexBin() !== null;
  }
  if (engine === "cursor-agent") {
    return resolveCursorInvocation() !== null;
  }
  return false;
}

// Auto-detection order: explicit MYTHIFY_FANOUT_ENGINE, codex-cli when
// available, other local subscription CLIs, then API or command fallbacks,
// else refuse with a message listing every option.
export function autoDetectEngine() {
  const explicit = (process.env.MYTHIFY_FANOUT_ENGINE || "").trim();
  if (explicit !== "") {
    return { engine: explicit };
  }
  const preferred = preferredLocalEngine(inferHostPlatform());
  if (preferred !== "" && fanoutEngineAvailable(preferred)) {
    return { engine: preferred };
  }
  if (resolveCodexBin() !== null) {
    return { engine: "codex-cli" };
  }
  if (resolveClaudeBin() !== null) {
    return { engine: "claude-cli" };
  }
  if (resolveCursorInvocation() !== null) {
    return { engine: "cursor-agent" };
  }
  if (envSet("ANTHROPIC_API_KEY")) {
    return { engine: "anthropic" };
  }
  if (envSet("MYTHIFY_FANOUT_COMMAND")) {
    return { engine: "command" };
  }
  return {
    error:
      "[FAIL] No fanout engine is available. Configure one of the seven engines: " +
      "codex-cli (install the codex CLI or set MYTHIFY_FANOUT_CODEX_BIN), " +
      "claude-cli (install the claude CLI or set MYTHIFY_FANOUT_CLAUDE_BIN), " +
      `claude-ultracode (Claude Code ${CLAUDE_ULTRACODE_MIN_VERSION} or newer), ` +
      "cursor-agent (install Cursor Agent or set MYTHIFY_FANOUT_CURSOR_BIN), " +
      "anthropic (set ANTHROPIC_API_KEY), " +
      "openai (set MYTHIFY_FANOUT_ENGINE=openai plus MYTHIFY_FANOUT_BASE_URL and MYTHIFY_FANOUT_API_KEY), " +
      "or command (set MYTHIFY_FANOUT_COMMAND to a shell template that reads the prompt on stdin). " +
      "MYTHIFY_FANOUT_ENGINE selects an engine explicitly.",
  };
}

export function engineDefaultModel(engine) {
  if (engine === "claude-cli") {
    return MODEL_CAPABILITY_MANIFEST.provider_profiles.anthropic.utility.model;
  }
  if (engine === "claude-ultracode") {
    return MODEL_CAPABILITY_MANIFEST.provider_profiles.anthropic.strong.model;
  }
  if (engine === "anthropic") {
    return MODEL_CAPABILITY_MANIFEST.provider_profiles.anthropic.utility.api_model;
  }
  if (["codex-cli", "openai"].includes(engine)) {
    return MODEL_CAPABILITY_MANIFEST.provider_profiles.openai.utility.model;
  }
  return "";
}

export function classifyModelCapabilityProfile(model) {
  const compact = String(model || "").toLowerCase().replace(/[_ ]+/g, "-");
  if (compact === "") {
    return "unknown";
  }
  for (const profile of MODEL_CAPABILITY_MANIFEST.model_match_order) {
    const terms = MODEL_CAPABILITY_MANIFEST.model_match_terms[profile] || [];
    if (terms.some((term) => compact.includes(String(term).toLowerCase()))) {
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
  const capabilityProfile = classifyModelCapabilityProfile(model);
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

export function resolveSessionModel(sessionModel) {
  const explicit = (sessionModel || "").trim();
  if (explicit !== "") {
    return { model: explicit, source: "explicit", tier: classifyModelTier(explicit) };
  }
  const envModel = (process.env.MYTHIFY_SESSION_MODEL || "").trim();
  if (envModel !== "") {
    return { model: envModel, source: "env", tier: classifyModelTier(envModel) };
  }
  const hostModel = io.readJsonRecover(path.join(io.resolveStateDir(), "host-model.json"), () => null);
  if (
    hostModel &&
    typeof hostModel === "object" &&
    !Array.isArray(hostModel) &&
    typeof hostModel.target_model === "string" &&
    hostModel.target_model.trim() !== ""
  ) {
    const model = hostModel.target_model.trim();
    return { model, source: "host_model_switch", tier: classifyModelTier(model) };
  }
  return { model: "", source: "unknown", tier: "unknown" };
}

export function resolveSpawnCeiling(spawnCeiling) {
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

export function ceilingCheck(session, ceiling, workerModel, options = {}) {
  const workerTier = classifyModelTier(workerModel);
  if (ceiling === "allow_stronger") {
    return { ok: true, workerTier, status: "allowed_stronger" };
  }
  if (session.tier === "unknown" || workerTier === "unknown") {
    return { ok: true, workerTier, status: "uncheckable" };
  }
  const sessionRank = MODEL_TIER_RANK[session.tier] || 0;
  const workerRank = MODEL_TIER_RANK[workerTier] || 0;
  if (ceiling === "lower_only" && workerRank >= sessionRank) {
    return { ok: false, workerTier, status: "violates_lower_only" };
  }
  if (ceiling === "same_or_lower" && workerRank > sessionRank) {
    if (options.taskRole === "reviewer" && options.reviewerAllowStronger === true) {
      return { ok: true, workerTier, status: "reviewer_stronger_opt_in" };
    }
    return { ok: false, workerTier, status: "violates_same_or_lower" };
  }
  return { ok: true, workerTier, status: "within_ceiling" };
}

export function resolveModelSelection(taskModel, jobModel, engine) {
  const candidates = [
    [taskModel, "task"],
    [jobModel, "job"],
    [process.env.MYTHIFY_FANOUT_MODEL, "env"],
  ];
  for (const [candidate, source] of candidates) {
    if (candidate !== undefined && candidate !== null && String(candidate).trim() !== "") {
      return { model: String(candidate).trim(), modelSource: source };
    }
  }
  const defaultModel = engineDefaultModel(engine);
  if (defaultModel !== "") {
    return { model: defaultModel, modelSource: "engine_default" };
  }
  if (["codex-cli", "cursor-agent"].includes(engine)) {
    return { model: "", modelSource: "platform_default" };
  }
  if (engine === "command") {
    return { model: "", modelSource: "command_default" };
  }
  return { model: "", modelSource: "unset" };
}

// Most specific wins: per-task model, then per-job model, then
// MYTHIFY_FANOUT_MODEL, then the engine default.
export function resolveModel(taskModel, jobModel, engine) {
  return resolveModelSelection(taskModel, jobModel, engine).model;
}

export function effortFromModel(engine, model) {
  const text = `${engine} ${model || ""}`.toLowerCase();
  if (/(haiku|mini|nano|small|fast|lite)/.test(text)) {
    return "low";
  }
  if (/(opus|pro|max|large|deep|heavy)/.test(text)) {
    return "high";
  }
  return "medium";
}

export function normalizeEffort(value) {
  const effort = String(value || "").trim();
  return EFFORT_LEVELS.includes(effort) ? effort : "";
}

export function resolveEffortSelection(taskEffort, jobEffort, engine, model) {
  if (engine === "claude-ultracode") {
    return { effort: "ultracode", effortSource: "engine_required" };
  }
  const candidates = [
    [taskEffort, "task"],
    [jobEffort, "job"],
    [process.env.MYTHIFY_FANOUT_EFFORT, "env"],
  ];
  for (const [candidate, source] of candidates) {
    const effort = normalizeEffort(candidate);
    if (effort !== "" && effort !== "auto") {
      return { effort, effortSource: source };
    }
  }
  return { effort: effortFromModel(engine, model), effortSource: "model_default" };
}

export function normalizeSpeed(value) {
  const speed = String(value || "").trim();
  return SPEED_LEVELS.includes(speed) ? speed : "";
}

export function resolveSpeedSelection(taskSpeed, jobSpeed) {
  const candidates = [
    [taskSpeed, "task"],
    [jobSpeed, "job"],
    [process.env.MYTHIFY_FANOUT_SPEED, "env"],
  ];
  for (const [candidate, source] of candidates) {
    const speed = normalizeSpeed(candidate);
    if (speed !== "" && speed !== "auto") {
      return { speed, speedSource: source };
    }
  }
  return { speed: "auto", speedSource: "platform_default" };
}

let cursorModelsCache = null;

export function parseCursorModels(text) {
  const models = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const match = line.trim().match(/^([A-Za-z0-9._-]+)\s+-\s+/);
    if (match) {
      models.push(match[1]);
    }
  }
  return models;
}

export function cursorModelsFromEnv() {
  const raw = (process.env.MYTHIFY_FANOUT_CURSOR_MODELS || "").trim();
  if (raw === "") {
    return null;
  }
  return raw
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

export function listCursorModels(invocation) {
  const fromEnv = cursorModelsFromEnv();
  if (fromEnv !== null) {
    return fromEnv;
  }
  if (cursorModelsCache !== null) {
    return cursorModelsCache;
  }
  const res = spawnSync(invocation.bin, [...invocation.prefixArgs, "models"], {
    cwd: projectRootDir(),
    env: curatedLocalCliEnv(),
    encoding: "utf8",
    timeout: 10000,
    maxBuffer: 1024 * 1024,
  });
  if (res.status !== 0) {
    cursorModelsCache = [];
    return cursorModelsCache;
  }
  cursorModelsCache = parseCursorModels(res.stdout || "");
  return cursorModelsCache;
}

export function cursorProfileForEffort(effort) {
  if (effort === "low") {
    return "utility";
  }
  if (effort === "high") {
    return "strong";
  }
  return "balanced";
}

export function selectCursorCatalogModel(available, capabilityProfile) {
  const models = Array.isArray(available) ? available : [];
  const row = MODEL_CAPABILITY_MANIFEST.provider_profiles.cursor[capabilityProfile] || {};
  for (const term of row.preferred_terms || []) {
    const normalizedTerm = String(term).toLowerCase();
    const match = models.find((model) => String(model).toLowerCase().includes(normalizedTerm));
    if (match) {
      return match;
    }
  }
  const fallback = MODEL_CAPABILITY_MANIFEST.provider_profiles.cursor.fallback_model || "";
  return models.find((model) => String(model).toLowerCase() === fallback.toLowerCase()) || "";
}

export function stripCursorModelSuffixes(model) {
  let base = String(model || "").trim();
  if (base.endsWith("-fast")) {
    base = base.slice(0, -5);
  }
  const effortSuffixes = ["-extra-high", "-xhigh", "-medium", "-high", "-low", "-none", "-max"];
  for (const suffix of effortSuffixes) {
    if (base.endsWith(suffix)) {
      return base.slice(0, -suffix.length);
    }
  }
  return base;
}

export function cursorEffortSuffixes(effort) {
  if (effort === "low") {
    return ["-low"];
  }
  if (effort === "medium") {
    return ["-medium", ""];
  }
  if (effort === "high") {
    return ["-high", ""];
  }
  return [""];
}

export function cursorSpeedSuffixes(speed) {
  if (speed === "fast") {
    return ["-fast", ""];
  }
  if (speed === "standard") {
    return [""];
  }
  return ["", "-fast"];
}

export function resolveCursorEncodedModel(model, effort, speed, invocation) {
  const requested = String(model || "").trim();
  if (requested === "") {
    return requested;
  }
  const available = listCursorModels(invocation);
  if (available.length === 0) {
    return requested;
  }
  const availableSet = new Set(available);
  const base = stripCursorModelSuffixes(requested);
  const candidates = [];
  if (speed === "auto" && effort === "auto") {
    candidates.push(requested);
  }
  for (const effortSuffix of cursorEffortSuffixes(effort)) {
    for (const speedSuffix of cursorSpeedSuffixes(speed)) {
      candidates.push(`${base}${effortSuffix}${speedSuffix}`);
    }
  }
  candidates.push(requested);
  const unique = [...new Set(candidates)];
  for (const candidate of unique) {
    if (availableSet.has(candidate)) {
      return candidate;
    }
  }
  return requested;
}

export function resolveEngineSpecificModel(engine, model, effort, speed) {
  if (engine !== "cursor-agent") {
    return model;
  }
  const invocation = resolveCursorInvocation();
  if (invocation === null) {
    return model;
  }
  const requested = String(model || "").trim();
  if (requested !== "") {
    return resolveCursorEncodedModel(requested, effort, speed, invocation);
  }
  const discovered = selectCursorCatalogModel(
    listCursorModels(invocation),
    cursorProfileForEffort(effort)
  );
  if (discovered === "") {
    return "";
  }
  return resolveCursorEncodedModel(discovered, effort, speed, invocation);
}

// Validation-time availability check for a task's resolved engine. Returns an
// explanatory string on failure, null when the engine is usable.
export function engineAvailabilityError(engine, model) {
  if (engine === "claude-cli") {
    return resolveClaudeBin() === null ? `engine claude-cli: ${claudeBinFailureText()}` : null;
  }
  if (engine === "claude-ultracode") {
    const support = probeClaudeUltracodeSupport();
    return support.ok ? null : `engine claude-ultracode: ${support.error}`;
  }
  if (engine === "codex-cli") {
    return resolveCodexBin() === null ? `engine codex-cli: ${codexBinFailureText()}` : null;
  }
  if (engine === "cursor-agent") {
    return resolveCursorInvocation() === null ? `engine cursor-agent: ${cursorBinFailureText()}` : null;
  }
  if (engine === "anthropic") {
    return envSet("ANTHROPIC_API_KEY") ? null : "engine anthropic: ANTHROPIC_API_KEY is not set.";
  }
  if (engine === "openai") {
    if (!envSet("MYTHIFY_FANOUT_BASE_URL")) {
      return "engine openai: MYTHIFY_FANOUT_BASE_URL is not set.";
    }
    if ((model || "").trim() === "") {
      return "engine openai: no model resolved; pass model per task or per job, or set MYTHIFY_FANOUT_MODEL.";
    }
    return null;
  }
  if (engine === "command") {
    return envSet("MYTHIFY_FANOUT_COMMAND") ? null : "engine command: MYTHIFY_FANOUT_COMMAND is not set.";
  }
  return `unknown engine "${engine}"; valid engines: ${ENGINES.join(", ")}.`;
}

export function engineBilling(engine) {
  if (["claude-cli", "claude-ultracode", "codex-cli", "cursor-agent"].includes(engine)) {
    return "host_cli_subscription_or_local_quota";
  }
  if (["anthropic", "openai"].includes(engine)) {
    return "metered_external_account";
  }
  if (engine === "command") {
    return "user_defined";
  }
  return "unknown";
}

export function enginePricingUrl(engine) {
  if (engine === "anthropic") {
    return "https://docs.anthropic.com/en/docs/about-claude/pricing";
  }
  if (engine === "openai") {
    return (process.env.MYTHIFY_FANOUT_PRICING_URL || "").trim();
  }
  return "";
}

export function engineCostMetadata(engine) {
  const metadata = {
    billing: engineBilling(engine),
    cost_tracking: "metadata_only_no_estimate",
    cost_estimate_status: "not_estimated",
    cost_estimate_cents: null,
    pricing_url: enginePricingUrl(engine),
  };
  if (engine === "claude-cli") {
    metadata.cost_warnings = [CLAUDE_CLI_COST_WARNING];
    metadata.cost_warning_urls = CLAUDE_CLI_COST_WARNING_URLS;
  }
  if (engine === "claude-ultracode") {
    metadata.cost_warnings = [CLAUDE_ULTRACODE_COST_WARNING];
    metadata.cost_warning_urls = CLAUDE_ULTRACODE_COST_WARNING_URLS;
  }
  return metadata;
}

export function engineProvider(engine) {
  if (["claude-cli", "claude-ultracode", "codex-cli", "cursor-agent"].includes(engine)) {
    return "host_cli";
  }
  if (["anthropic", "openai"].includes(engine)) {
    return "api_provider";
  }
  if (engine === "command") {
    return "custom_command";
  }
  return "unknown";
}

export function sha256Hex(text) {
  return crypto.createHash("sha256").update(String(text || ""), "utf8").digest("hex");
}

export function auditCostMetadata(task) {
  const metadata = {
    billing: task.billing || "unknown",
    cost_tracking: task.cost_tracking || "metadata_only_no_estimate",
    cost_estimate_status: task.cost_estimate_status || "not_estimated",
    cost_estimate_cents: task.cost_estimate_cents ?? null,
    pricing_url: task.pricing_url || "",
  };
  if (Array.isArray(task.cost_warnings) && task.cost_warnings.length > 0) {
    metadata.cost_warnings = task.cost_warnings;
  }
  if (Array.isArray(task.cost_warning_urls) && task.cost_warning_urls.length > 0) {
    metadata.cost_warning_urls = task.cost_warning_urls;
  }
  return metadata;
}

export function providerAuditPath() {
  return path.join(io.resolveStateDir(), "provider-audit.jsonl");
}

export function appendProviderAudit(record) {
  const auditPath = providerAuditPath();
  fs.mkdirSync(path.dirname(auditPath), { recursive: true });
  fs.appendFileSync(auditPath, `${JSON.stringify(record)}\n`, "utf8");
}

export function providerAuditBase(job, task, prompt) {
  const cost = auditCostMetadata(task);
  const hostedProviderRequired = HOSTED_PROVIDER_ENGINES.includes(task.engine);
  return {
    timestamp: io.isoNow(),
    surface: "fanout_worker",
    provider: engineProvider(task.engine),
    provider_execution_scope: "fanout_worker_only",
    job_id: job.id,
    task_id: task.id,
    task_title: task.title,
    role: task.role || "worker",
    engine: task.engine,
    model: task.model || "",
    model_source: task.model_source || "",
    model_tier: task.model_tier || "unknown",
    effort: task.effort || "medium",
    speed: task.speed || "auto",
    billing: cost.billing,
    cost_metadata: cost,
    cost_metadata_fields: Object.keys(cost).sort(),
    hosted_provider_acknowledgements: {
      required: hostedProviderRequired,
      required_acknowledgements: HOSTED_PROVIDER_REQUIRED_ACKS,
      billing_acknowledged: Boolean(job.hosted_provider_billing_acknowledged),
      data_acknowledged: Boolean(job.hosted_provider_data_acknowledged),
      material_acknowledged: Boolean(job.hosted_provider_material_acknowledged),
    },
    request_metadata: {
      prompt_sha256: sha256Hex(prompt),
      prompt_bytes: Buffer.byteLength(String(prompt || ""), "utf8"),
      prompt_redacted: true,
      timeout_seconds: task.timeout_seconds,
      timeout_source: task.timeout_source,
    },
    output_material_status: "material_not_verification",
    verification_boundary: "worker output must be merged by the orchestrator and verified with verify_run or outcome_check",
    records_verification_evidence: false,
  };
}
