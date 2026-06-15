// Mythify fanout: parallel delegation (MCP only), per docs/design.md.
// fanout_start accepts a one-shot declarative task list, registers the job
// under .mythify/fanout/<job_id>/, returns the job id immediately, and runs
// one fresh worker per task in a background concurrency pool. Workers are
// independent model invocations (local subscription CLIs, HTTP APIs, or a
// shell command template) with no memory of the conversation. fanout_status
// and fanout_results report on the job.
// The Python CLI deliberately does not implement fanout.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { z } from "zod";

const ENGINES = ["claude-cli", "codex-cli", "cursor-agent", "anthropic", "openai", "command"];
const HOST_PLATFORMS = [
  "auto",
  "unknown",
  "codex-desktop",
  "codex-cli",
  "claude-desktop",
  "claude-code",
  "cursor-desktop",
  "cursor-agent",
];
const EFFORT_LEVELS = ["auto", "low", "medium", "high"];
const SPEED_LEVELS = ["auto", "standard", "fast"];
const SPAWN_CEILINGS = ["auto", "lower_only", "same_or_lower", "allow_stronger"];
const TASK_ROLES = ["worker", "reviewer"];
const FANOUT_VISIBILITY_MODES = ["auto", "quiet", "summary", "verbose", "threaded"];
const HOSTED_PROVIDER_ENGINES = ["anthropic", "openai"];
const HOSTED_PROVIDER_REQUIRED_ACKS = [
  "hosted_provider_billing_ack",
  "hosted_provider_data_ack",
  "hosted_provider_material_ack",
];
const MODEL_TIER_RANK = {
  unknown: 0,
  small: 1,
  fast: 2,
  standard: 3,
  strong: 4,
  frontier: 5,
};

const TASK_STATUS_ICONS = {
  pending: "[ ]",
  running: "[>]",
  completed: "[x]",
  failed: "[!]",
  interrupted: "[~]",
};

// Per-task text cap in fanout_results; the full output stays on disk.
const RESULT_CAP_CHARS = 20000;

// Alias-to-ID map for the anthropic engine (docs/design.md engine table).
export const ANTHROPIC_MODEL_ALIASES = {
  haiku: "claude-haiku-4-5",
  sonnet: "claude-sonnet-4-6",
  opus: "claude-opus-4-8",
  fable: "claude-fable-5",
};

export function resolveAnthropicModelId(model) {
  return Object.prototype.hasOwnProperty.call(ANTHROPIC_MODEL_ALIASES, model)
    ? ANTHROPIC_MODEL_ALIASES[model]
    : model;
}

const LOGIN_REMEDIATION =
  'Authentication remediation: run "claude /login" once in a terminal, or run ' +
  '"claude setup-token" and set CLAUDE_CODE_OAUTH_TOKEN in the MCP client\'s env block.';

const CODEX_LOGIN_REMEDIATION =
  'Authentication remediation: run "codex login" once in a terminal, then retry the fanout job.';

const CURSOR_LOGIN_REMEDIATION =
  'Authentication remediation: run "cursor-agent login" once in a terminal (or "cursor agent login" if you use the cursor binary), then retry the fanout job.';

const WORKER_PREAMBLE = [
  "You are a delegated worker executing one self-contained task for an orchestrating agent.",
  "The task below is complete on its own: you have no access to the orchestrator's conversation and no other task's output.",
  "Do not ask questions and do not request clarification; if something is ambiguous, make the most reasonable assumption and proceed.",
  "Return only the deliverable the task asks for.",
].join("\n");

// Helpers injected by index.js through registerFanoutTools, so fanout reuses
// the server's state-directory resolution and durable IO helpers (atomic
// writes, corrupt-file recovery, ISO timestamps).
let io = null;

// In-memory job registry for this server process. A job present here was
// started by this process; a job on disk but absent here belonged to a dead
// server, so its unfinished tasks are reported as interrupted.
const jobRegistry = new Map();
let lastJobId = null;

// ---------------------------------------------------------------------------
// Environment and configuration
// ---------------------------------------------------------------------------

function intEnv(name, fallback) {
  const raw = (process.env[name] || "").trim();
  if (raw === "") {
    return fallback;
  }
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function positiveIntEnvWithSource(name, fallback) {
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

function envSet(name) {
  return (process.env[name] || "").trim() !== "";
}

function containsPhrase(text, phrases) {
  const normalized = String(text || "").toLowerCase().split(/\s+/).join(" ");
  return phrases.some((phrase) => normalized.includes(String(phrase).toLowerCase()));
}

function inferVisibilityFromText(text) {
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

function resolveVisibilitySelection(visibility, purpose, tasks) {
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

function visibilityGuidance(visibility) {
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

function killSwitchText() {
  if (process.env.MYTHIFY_DISABLE_FANOUT === "1") {
    return (
      "[FAIL] Fanout is disabled: the server environment sets MYTHIFY_DISABLE_FANOUT=1, " +
      "which disables fanout_start, fanout_status, and fanout_results. " +
      "Unset MYTHIFY_DISABLE_FANOUT to re-enable parallel delegation."
    );
  }
  return null;
}

function depthGuardText() {
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

function fanoutRootDir() {
  return path.join(io.resolveStateDir(), "fanout");
}

// Project root is the parent of the .mythify state directory; claude-cli
// workers run with this as their cwd and relative context_paths resolve here.
function projectRootDir() {
  return path.dirname(io.resolveStateDir());
}

// ---------------------------------------------------------------------------
// Engine and model resolution
// ---------------------------------------------------------------------------

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

// Desktop MCP clients often launch servers with a minimal PATH, so local CLI
// engines resolve their binaries explicitly through an env override, PATH, and
// common install locations.
function resolveBinary(envName, binaryName, fallbacks) {
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

function resolveClaudeBin() {
  return resolveBinary("MYTHIFY_FANOUT_CLAUDE_BIN", "claude", [
    path.join(os.homedir(), ".claude", "local", "claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
  ]);
}

function resolveCodexBin() {
  return resolveBinary("MYTHIFY_FANOUT_CODEX_BIN", "codex", [
    path.join(os.homedir(), ".local", "bin", "codex"),
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
  ]);
}

function resolveCursorInvocation() {
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

function claudeBinFailureText() {
  return (
    "no claude binary was found (checked MYTHIFY_FANOUT_CLAUDE_BIN, claude on PATH, " +
    "~/.claude/local/claude, /opt/homebrew/bin/claude, /usr/local/bin/claude). " +
    "Set MYTHIFY_FANOUT_CLAUDE_BIN to the claude binary path, or pick another engine."
  );
}

function codexBinFailureText() {
  return (
    "no codex binary was found (checked MYTHIFY_FANOUT_CODEX_BIN, codex on PATH, " +
    "~/.local/bin/codex, /opt/homebrew/bin/codex, /usr/local/bin/codex). " +
    "Set MYTHIFY_FANOUT_CODEX_BIN to the codex binary path, or pick another engine."
  );
}

function cursorBinFailureText() {
  return (
    "no cursor-agent or cursor binary was found (checked MYTHIFY_FANOUT_CURSOR_BIN, " +
    "MYTHIFY_FANOUT_CURSOR_AGENT_BIN, cursor-agent on PATH, cursor on PATH, " +
    "~/.local/bin, /opt/homebrew/bin, and /usr/local/bin). Set " +
    "MYTHIFY_FANOUT_CURSOR_BIN to the cursor-agent or cursor binary path, or pick another engine."
  );
}

function inferHostPlatform() {
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

function preferredLocalEngine(platform) {
  if (["codex-desktop", "codex-cli"].includes(platform)) {
    return "codex-cli";
  }
  if (["claude-desktop", "claude-code"].includes(platform)) {
    return "claude-cli";
  }
  if (["cursor-desktop", "cursor-agent"].includes(platform)) {
    return "cursor-agent";
  }
  return "";
}

function fanoutEngineAvailable(engine) {
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

// Auto-detection order: explicit MYTHIFY_FANOUT_ENGINE, initiating host CLI
// when available, local subscription CLIs, then API or command fallbacks, else
// refuse with a message listing every option.
function autoDetectEngine() {
  const explicit = (process.env.MYTHIFY_FANOUT_ENGINE || "").trim();
  if (explicit !== "") {
    return { engine: explicit };
  }
  const preferred = preferredLocalEngine(inferHostPlatform());
  if (preferred !== "" && fanoutEngineAvailable(preferred)) {
    return { engine: preferred };
  }
  if (resolveClaudeBin() !== null) {
    return { engine: "claude-cli" };
  }
  if (resolveCodexBin() !== null) {
    return { engine: "codex-cli" };
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
      "[FAIL] No fanout engine is available. Configure one of the six engines: " +
      "claude-cli (install the claude CLI or set MYTHIFY_FANOUT_CLAUDE_BIN), " +
      "codex-cli (install the codex CLI or set MYTHIFY_FANOUT_CODEX_BIN), " +
      "cursor-agent (install Cursor Agent or set MYTHIFY_FANOUT_CURSOR_BIN), " +
      "anthropic (set ANTHROPIC_API_KEY), " +
      "openai (set MYTHIFY_FANOUT_ENGINE=openai plus MYTHIFY_FANOUT_BASE_URL and MYTHIFY_FANOUT_API_KEY), " +
      "or command (set MYTHIFY_FANOUT_COMMAND to a shell template that reads the prompt on stdin). " +
      "MYTHIFY_FANOUT_ENGINE selects an engine explicitly.",
  };
}

function engineDefaultModel(engine) {
  if (engine === "claude-cli") {
    return "haiku";
  }
  if (engine === "anthropic") {
    return "claude-haiku-4-5";
  }
  return "";
}

function classifyModelTier(model) {
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
  if (frontierTerms.some((term) => compact.includes(term))) {
    return "frontier";
  }
  if (fastTerms.some((term) => compact.includes(term))) {
    return "fast";
  }
  if (strongTerms.some((term) => compact.includes(term))) {
    return "strong";
  }
  if (compact.includes("3.5") || compact.includes("cheap")) {
    return "small";
  }
  return "standard";
}

function resolveSessionModel(sessionModel) {
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

function ceilingCheck(session, ceiling, workerModel, options = {}) {
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

function resolveModelSelection(taskModel, jobModel, engine) {
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
function resolveModel(taskModel, jobModel, engine) {
  return resolveModelSelection(taskModel, jobModel, engine).model;
}

function effortFromModel(engine, model) {
  const text = `${engine} ${model || ""}`.toLowerCase();
  if (/(haiku|mini|nano|small|fast|lite)/.test(text)) {
    return "low";
  }
  if (/(opus|pro|max|large|deep|heavy)/.test(text)) {
    return "high";
  }
  return "medium";
}

function normalizeEffort(value) {
  const effort = String(value || "").trim();
  return EFFORT_LEVELS.includes(effort) ? effort : "";
}

function resolveEffortSelection(taskEffort, jobEffort, engine, model) {
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

function normalizeSpeed(value) {
  const speed = String(value || "").trim();
  return SPEED_LEVELS.includes(speed) ? speed : "";
}

function resolveSpeedSelection(taskSpeed, jobSpeed) {
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

function parseCursorModels(text) {
  const models = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const match = line.trim().match(/^([A-Za-z0-9._-]+)\s+-\s+/);
    if (match) {
      models.push(match[1]);
    }
  }
  return models;
}

function cursorModelsFromEnv() {
  const raw = (process.env.MYTHIFY_FANOUT_CURSOR_MODELS || "").trim();
  if (raw === "") {
    return null;
  }
  return raw
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

function listCursorModels(invocation) {
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

function stripCursorModelSuffixes(model) {
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

function cursorEffortSuffixes(effort) {
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

function cursorSpeedSuffixes(speed) {
  if (speed === "fast") {
    return ["-fast", ""];
  }
  if (speed === "standard") {
    return [""];
  }
  return ["", "-fast"];
}

function resolveCursorEncodedModel(model, effort, speed, invocation) {
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

function resolveEngineSpecificModel(engine, model, effort, speed) {
  if (engine !== "cursor-agent") {
    return model;
  }
  const invocation = resolveCursorInvocation();
  if (invocation === null) {
    return model;
  }
  return resolveCursorEncodedModel(model, effort, speed, invocation);
}

// Validation-time availability check for a task's resolved engine. Returns an
// explanatory string on failure, null when the engine is usable.
function engineAvailabilityError(engine, model) {
  if (engine === "claude-cli") {
    return resolveClaudeBin() === null ? `engine claude-cli: ${claudeBinFailureText()}` : null;
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

function engineBilling(engine) {
  if (["claude-cli", "codex-cli", "cursor-agent"].includes(engine)) {
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

function enginePricingUrl(engine) {
  if (engine === "anthropic") {
    return "https://docs.anthropic.com/en/docs/about-claude/pricing";
  }
  if (engine === "openai") {
    return (process.env.MYTHIFY_FANOUT_PRICING_URL || "").trim();
  }
  return "";
}

function engineCostMetadata(engine) {
  return {
    billing: engineBilling(engine),
    cost_tracking: "metadata_only_no_estimate",
    cost_estimate_status: "not_estimated",
    cost_estimate_cents: null,
    pricing_url: enginePricingUrl(engine),
  };
}

function engineProvider(engine) {
  if (["claude-cli", "codex-cli", "cursor-agent"].includes(engine)) {
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

function sha256Hex(text) {
  return crypto.createHash("sha256").update(String(text || ""), "utf8").digest("hex");
}

function auditCostMetadata(task) {
  return {
    billing: task.billing || "unknown",
    cost_tracking: task.cost_tracking || "metadata_only_no_estimate",
    cost_estimate_status: task.cost_estimate_status || "not_estimated",
    cost_estimate_cents: task.cost_estimate_cents ?? null,
    pricing_url: task.pricing_url || "",
  };
}

function providerAuditPath() {
  return path.join(io.resolveStateDir(), "provider-audit.jsonl");
}

function appendProviderAudit(record) {
  const auditPath = providerAuditPath();
  fs.mkdirSync(path.dirname(auditPath), { recursive: true });
  fs.appendFileSync(auditPath, `${JSON.stringify(record)}\n`, "utf8");
}

function providerAuditBase(job, task, prompt) {
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

// ---------------------------------------------------------------------------
// Worker prompt assembly
// ---------------------------------------------------------------------------

// Fixed preamble, then each context file as a labeled fenced block, then the
// task prompt. context_paths resolve relative to the project root (absolute
// paths allowed); total inlined context is capped at MYTHIFY_FANOUT_CONTEXT_BYTES
// with an explicit truncation marker. An unreadable path is a validation error.
function assembleWorkerPrompt(task, projectRoot, contextBytesCap) {
  const parts = [WORKER_PREAMBLE];
  if (typeof task.effort === "string" && task.effort !== "") {
    parts.push(
      `Requested effort: ${task.effort}. Match the depth and rigor to this level while keeping the requested deliverable format.`
    );
  }
  if (typeof task.speed === "string" && task.speed !== "" && task.speed !== "auto") {
    parts.push(
      `Requested speed: ${task.speed}. Prefer this latency setting for any platform-specific model controls when available.`
    );
  }
  let remaining = contextBytesCap;
  for (const rawPath of task.context_paths || []) {
    const resolved = path.isAbsolute(rawPath) ? rawPath : path.join(projectRoot, rawPath);
    let buffer;
    try {
      buffer = fs.readFileSync(resolved);
    } catch (err) {
      return {
        error: `context path "${rawPath}" is not readable (resolved to ${resolved}): ${err.message}`,
      };
    }
    let body;
    if (buffer.length <= remaining) {
      body = buffer.toString("utf8");
      remaining -= buffer.length;
    } else {
      body =
        buffer.subarray(0, Math.max(remaining, 0)).toString("utf8") +
        `\n[WARN] Context truncated: the per-task inlined context cap of ${contextBytesCap} bytes (MYTHIFY_FANOUT_CONTEXT_BYTES) was reached.`;
      remaining = 0;
    }
    parts.push(`Context file: ${rawPath}\n\`\`\`\n${body}\n\`\`\``);
  }
  parts.push(`Task:\n${task.prompt}`);
  return { prompt: parts.join("\n\n") };
}

// ---------------------------------------------------------------------------
// Subprocess plumbing (claude-cli and command engines)
// ---------------------------------------------------------------------------

// Spawns either a binary with args or a shell command template, writes the
// prompt to stdin, collects stdout and stderr, and enforces a kill timer at
// the per-worker timeout.
function runSubprocess(options) {
  return new Promise((resolve) => {
    let child;
    try {
      child =
        options.shellCommand !== undefined
          ? spawn(options.shellCommand, {
              shell: true,
              cwd: options.cwd,
              env: options.env,
              stdio: ["pipe", "pipe", "pipe"],
            })
          : spawn(options.bin, options.args, {
              cwd: options.cwd,
              env: options.env,
              stdio: ["pipe", "pipe", "pipe"],
            });
    } catch (err) {
      resolve({ exitCode: -1, stdout: "", stderr: "", timedOut: false, spawnError: err.message });
      return;
    }
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const finish = (result) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(killTimer);
      resolve(result);
    };
    const killTimer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill("SIGKILL");
      } catch {
        // The child may already be gone.
      }
    }, Math.round(options.timeoutSeconds * 1000));
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (err) =>
      finish({ exitCode: -1, stdout, stderr, timedOut, spawnError: err.message })
    );
    child.on("close", (code) =>
      finish({ exitCode: code === null ? -1 : code, stdout, stderr, timedOut })
    );
    // The child may exit before consuming stdin; swallow the EPIPE.
    child.stdin.on("error", () => {});
    child.stdin.write(options.input);
    child.stdin.end();
  });
}

// Minimal shell-style tokenizer for MYTHIFY_FANOUT_CLAUDE_ARGS, honoring
// single and double quotes, for example: --allowedTools "Bash".
function splitShellArgs(raw) {
  const args = [];
  let current = "";
  let quote = null;
  let hasToken = false;
  for (const ch of String(raw)) {
    if (quote !== null) {
      if (ch === quote) {
        quote = null;
      } else {
        current += ch;
      }
      continue;
    }
    if (ch === '"' || ch === "'") {
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

function augmentedPath() {
  const parts = (process.env.PATH || "").split(path.delimiter).filter((p) => p !== "");
  for (const extra of [path.join(os.homedir(), ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin"]) {
    if (!parts.includes(extra)) {
      parts.push(extra);
    }
  }
  return parts.join(path.delimiter);
}

// claude-cli worker environment is curated, not inherited: harness variables
// (CLAUDECODE, CLAUDE_CODE_*, ANTHROPIC_BASE_URL) are NOT passed through,
// because a server spawned by Claude Code inherits harness routing that
// breaks nested workers. Only CLAUDE_CODE_OAUTH_TOKEN crosses over, for
// subscription auth.
function curatedClaudeEnv() {
  const env = {
    HOME: process.env.HOME || os.homedir(),
    USER: process.env.USER || os.userInfo().username,
    TERM: "dumb",
    PATH: augmentedPath(),
    MYTHIFY_FANOUT_DEPTH: "1",
    MYTHIFY_DISABLE_FANOUT: "1",
  };
  if (envSet("CLAUDE_CODE_OAUTH_TOKEN")) {
    env.CLAUDE_CODE_OAUTH_TOKEN = process.env.CLAUDE_CODE_OAUTH_TOKEN;
  }
  return env;
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

function timeoutFailure(stdout, timeoutSeconds) {
  return {
    ok: false,
    output: stdout,
    error:
      `Worker timed out after ${timeoutSeconds} seconds and was killed. ` +
      "Raise the limit with fanout_start timeout_seconds or MYTHIFY_FANOUT_TIMEOUT_SECONDS.",
  };
}

// ---------------------------------------------------------------------------
// Engines
// ---------------------------------------------------------------------------

async function runClaudeCliWorker(prompt, model, effort, timeoutSeconds, projectRoot) {
  const bin = resolveClaudeBin();
  if (bin === null) {
    return { ok: false, output: "", error: `engine claude-cli: ${claudeBinFailureText()}` };
  }
  const args = [
    "-p",
    "--output-format",
    "json",
    "--model",
    model,
    "--max-turns",
    String(intEnv("MYTHIFY_FANOUT_MAX_TURNS", 25)),
  ];
  if ((effort || "").trim() !== "" && effort !== "auto") {
    args.push("--effort", effort);
  }
  args.push(...splitShellArgs(process.env.MYTHIFY_FANOUT_CLAUDE_ARGS || ""));
  const res = await runSubprocess({
    bin,
    args,
    cwd: projectRoot,
    env: curatedClaudeEnv(),
    input: prompt,
    timeoutSeconds,
  });
  if (res.timedOut) {
    return timeoutFailure(res.stdout, timeoutSeconds);
  }
  if (res.spawnError) {
    return { ok: false, output: "", error: `Failed to spawn claude binary "${bin}": ${res.spawnError}` };
  }
  let parsed = null;
  try {
    parsed = JSON.parse(res.stdout);
  } catch {
    parsed = null;
  }
  const resultText = parsed && typeof parsed.result === "string" ? parsed.result : "";
  const failed = res.exitCode !== 0 || parsed === null || (parsed && parsed.is_error === true);
  if (!failed) {
    return { ok: true, output: resultText, error: null };
  }
  const reasons = [];
  if (res.exitCode !== 0) {
    reasons.push(`claude exited ${res.exitCode}`);
  }
  if (parsed === null) {
    reasons.push("claude stdout was not valid JSON");
  }
  if (parsed && parsed.is_error === true) {
    reasons.push("claude reported is_error: true");
  }
  let error = `claude-cli worker failed (${reasons.join("; ")}).`;
  if (resultText !== "") {
    error += ` Result: ${resultText.slice(0, 2000)}`;
  } else if (res.stderr.trim() !== "") {
    error += ` stderr (tail): ${res.stderr.slice(-2000)}`;
  }
  const evidence = `${resultText}\n${res.stdout}\n${res.stderr}`;
  if (/not logged in/i.test(evidence) || evidence.includes("401")) {
    error += ` ${LOGIN_REMEDIATION}`;
  }
  return { ok: false, output: resultText !== "" ? resultText : res.stdout, error };
}

function tempWorkerPath(prefix, suffix) {
  const tmpDir = path.join(io.resolveStateDir(), "tmp");
  fs.mkdirSync(tmpDir, { recursive: true });
  return path.join(tmpDir, `${prefix}-${io.stampNow()}-${crypto.randomBytes(3).toString("hex")}${suffix}`);
}

function authLooksMissing(text) {
  return /not logged in/i.test(text) || /not authenticated/i.test(text) || /\blogin\b/i.test(text) || text.includes("401");
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

async function runCodexCliWorker(prompt, model, speed, timeoutSeconds, projectRoot) {
  const bin = resolveCodexBin();
  if (bin === null) {
    return { ok: false, output: "", error: `engine codex-cli: ${codexBinFailureText()}` };
  }
  const outputFile = tempWorkerPath("codex-output", ".md");
  const sandbox = (process.env.MYTHIFY_FANOUT_CODEX_SANDBOX || "read-only").trim() || "read-only";
  const args = [
    "--ask-for-approval",
    "never",
    "exec",
    "--cd",
    projectRoot,
    "--sandbox",
    sandbox,
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
  args.push(...splitShellArgs(process.env.MYTHIFY_FANOUT_CODEX_ARGS || ""));
  args.push("-");
  const res = await runSubprocess({
    bin,
    args,
    cwd: projectRoot,
    env: curatedLocalCliEnv(),
    input: prompt,
    timeoutSeconds,
  });
  let output = "";
  try {
    if (fs.existsSync(outputFile)) {
      output = fs.readFileSync(outputFile, "utf8");
    }
  } catch {
    output = "";
  }
  try {
    fs.rmSync(outputFile, { force: true });
  } catch {
    // Best effort cleanup.
  }
  if (output === "") {
    output = res.stdout;
  }
  if (res.timedOut) {
    return timeoutFailure(output, timeoutSeconds);
  }
  if (res.spawnError) {
    return { ok: false, output, error: `Failed to spawn codex binary "${bin}": ${res.spawnError}` };
  }
  if (res.exitCode === 0) {
    return { ok: true, output, error: null };
  }
  let error =
    `codex-cli worker failed (codex exited ${res.exitCode}). ` +
    `stdout (tail): ${res.stdout.slice(-2000) || "(empty)"} stderr (tail): ${res.stderr.slice(-2000) || "(empty)"}`;
  if (authLooksMissing(`${output}\n${res.stdout}\n${res.stderr}`)) {
    error += ` ${CODEX_LOGIN_REMEDIATION}`;
  }
  return { ok: false, output, error };
}

async function runCursorAgentWorker(prompt, model, timeoutSeconds, projectRoot) {
  const invocation = resolveCursorInvocation();
  if (invocation === null) {
    return { ok: false, output: "", error: `engine cursor-agent: ${cursorBinFailureText()}` };
  }
  const promptFile = tempWorkerPath("cursor-prompt", ".md");
  fs.writeFileSync(promptFile, prompt, "utf8");
  const mode = (process.env.MYTHIFY_FANOUT_CURSOR_MODE || "ask").trim();
  const args = [
    ...invocation.prefixArgs,
    "--print",
    "--output-format",
    "text",
    "--trust",
    "--workspace",
    projectRoot,
  ];
  if (mode !== "") {
    args.push("--mode", mode);
  }
  if ((model || "").trim() !== "") {
    args.push("--model", model);
  }
  if (process.env.MYTHIFY_FANOUT_CURSOR_FORCE === "1") {
    args.push("--force");
  }
  args.push(...splitShellArgs(process.env.MYTHIFY_FANOUT_CURSOR_ARGS || ""));
  args.push(
    `Read the task prompt from this file: ${promptFile}\nComplete it and return only the deliverable.`
  );
  const res = await runSubprocess({
    bin: invocation.bin,
    args,
    cwd: projectRoot,
    env: curatedLocalCliEnv(),
    input: "",
    timeoutSeconds,
  });
  try {
    fs.rmSync(promptFile, { force: true });
  } catch {
    // Best effort cleanup.
  }
  if (res.timedOut) {
    return timeoutFailure(res.stdout, timeoutSeconds);
  }
  if (res.spawnError) {
    return {
      ok: false,
      output: res.stdout,
      error: `Failed to spawn cursor agent binary "${invocation.bin}": ${res.spawnError}`,
    };
  }
  if (res.exitCode === 0) {
    return { ok: true, output: res.stdout, error: null };
  }
  let error =
    `cursor-agent worker failed (cursor exited ${res.exitCode}). ` +
    `stdout (tail): ${res.stdout.slice(-2000) || "(empty)"} stderr (tail): ${res.stderr.slice(-2000) || "(empty)"}`;
  if (authLooksMissing(`${res.stdout}\n${res.stderr}`)) {
    error += ` ${CURSOR_LOGIN_REMEDIATION}`;
  }
  return { ok: false, output: res.stdout, error };
}

async function runCommandWorker(prompt, timeoutSeconds, projectRoot) {
  const template = (process.env.MYTHIFY_FANOUT_COMMAND || "").trim();
  if (template === "") {
    return { ok: false, output: "", error: "engine command: MYTHIFY_FANOUT_COMMAND is not set." };
  }
  const env = { ...process.env, MYTHIFY_FANOUT_DEPTH: "1", MYTHIFY_DISABLE_FANOUT: "1" };
  const res = await runSubprocess({
    shellCommand: template,
    cwd: projectRoot,
    env,
    input: prompt,
    timeoutSeconds,
  });
  if (res.timedOut) {
    return timeoutFailure(res.stdout, timeoutSeconds);
  }
  if (res.spawnError) {
    return { ok: false, output: res.stdout, error: `Failed to run MYTHIFY_FANOUT_COMMAND: ${res.spawnError}` };
  }
  if (res.exitCode !== 0) {
    const stderrTail = res.stderr.trim() === "" ? "(empty)" : res.stderr.slice(-4000);
    return {
      ok: false,
      output: res.stdout,
      error: `Worker command exited ${res.exitCode}. stderr (tail): ${stderrTail}`,
    };
  }
  return { ok: true, output: res.stdout, error: null };
}

async function postJsonWorker({ url, headers, body, timeoutSeconds, engineLabel, extractText }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.round(timeoutSeconds * 1000));
  try {
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (err) {
      if (controller.signal.aborted) {
        return {
          ok: false,
          output: "",
          error: `${engineLabel} worker timed out after ${timeoutSeconds} seconds.`,
        };
      }
      return {
        ok: false,
        output: "",
        error: `${engineLabel} request failed: ${err && err.message ? err.message : String(err)}`,
      };
    }
    const raw = await response.text();
    if (!response.ok) {
      return {
        ok: false,
        output: "",
        error: `${engineLabel} endpoint returned HTTP ${response.status}: ${raw.slice(0, 2000)}`,
      };
    }
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return {
        ok: false,
        output: "",
        error: `${engineLabel} endpoint returned non-JSON output: ${raw.slice(0, 2000)}`,
      };
    }
    const text = extractText(data);
    if (typeof text !== "string") {
      return {
        ok: false,
        output: "",
        error: `${engineLabel} response had no text content: ${raw.slice(0, 2000)}`,
      };
    }
    return { ok: true, output: text, error: null };
  } finally {
    clearTimeout(timer);
  }
}

async function runAnthropicWorker(prompt, model, timeoutSeconds) {
  const apiKey = (process.env.ANTHROPIC_API_KEY || "").trim();
  if (apiKey === "") {
    return { ok: false, output: "", error: "engine anthropic: ANTHROPIC_API_KEY is not set." };
  }
  return postJsonWorker({
    url: "https://api.anthropic.com/v1/messages",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: {
      model: resolveAnthropicModelId(model),
      max_tokens: intEnv("MYTHIFY_FANOUT_MAX_TOKENS", 8000),
      messages: [{ role: "user", content: prompt }],
    },
    timeoutSeconds,
    engineLabel: "anthropic",
    extractText: (data) => {
      if (!Array.isArray(data.content)) {
        return null;
      }
      return data.content
        .filter((block) => block && block.type === "text" && typeof block.text === "string")
        .map((block) => block.text)
        .join("\n");
    },
  });
}

async function runOpenAiWorker(prompt, model, timeoutSeconds) {
  const baseUrl = (process.env.MYTHIFY_FANOUT_BASE_URL || "").trim();
  if (baseUrl === "") {
    return { ok: false, output: "", error: "engine openai: MYTHIFY_FANOUT_BASE_URL is not set." };
  }
  if ((model || "").trim() === "") {
    return {
      ok: false,
      output: "",
      error: "engine openai: no model resolved; pass model per task or per job, or set MYTHIFY_FANOUT_MODEL.",
    };
  }
  const headers = { "content-type": "application/json" };
  const apiKey = (process.env.MYTHIFY_FANOUT_API_KEY || "").trim();
  if (apiKey !== "") {
    headers.authorization = `Bearer ${apiKey}`;
  }
  return postJsonWorker({
    url: `${baseUrl.replace(/\/+$/, "")}/chat/completions`,
    headers,
    body: {
      model,
      max_tokens: intEnv("MYTHIFY_FANOUT_MAX_TOKENS", 8000),
      messages: [{ role: "user", content: prompt }],
    },
    timeoutSeconds,
    engineLabel: "openai",
    extractText: (data) => {
      const choice = Array.isArray(data.choices) ? data.choices[0] : null;
      if (choice && choice.message && typeof choice.message.content === "string") {
        return choice.message.content;
      }
      return null;
    },
  });
}

function runWorker(engine, prompt, model, effort, speed, timeoutSeconds, projectRoot) {
  if (engine === "claude-cli") {
    return runClaudeCliWorker(prompt, model, effort, timeoutSeconds, projectRoot);
  }
  if (engine === "codex-cli") {
    return runCodexCliWorker(prompt, model, speed, timeoutSeconds, projectRoot);
  }
  if (engine === "cursor-agent") {
    return runCursorAgentWorker(prompt, model, timeoutSeconds, projectRoot);
  }
  if (engine === "anthropic") {
    return runAnthropicWorker(prompt, model, timeoutSeconds);
  }
  if (engine === "openai") {
    return runOpenAiWorker(prompt, model, timeoutSeconds);
  }
  if (engine === "command") {
    return runCommandWorker(prompt, timeoutSeconds, projectRoot);
  }
  return Promise.resolve({ ok: false, output: "", error: `unknown engine "${engine}".` });
}

// ---------------------------------------------------------------------------
// Job lifecycle
// ---------------------------------------------------------------------------

function saveJob(job, jobDir) {
  job.last_updated = io.isoNow();
  io.writeJsonAtomic(path.join(jobDir, "job.json"), job);
}

async function runOneTask(job, jobDir, task, prompt, timeoutSeconds, projectRoot) {
  task.status = "running";
  task.started_at = io.isoNow();
  saveJob(job, jobDir);
  appendProviderAudit({
    ...providerAuditBase(job, task, prompt),
    event: "fanout_task_started",
    status: task.status,
  });
  const startedNs = process.hrtime.bigint();
  let outcome;
  try {
    outcome = await runWorker(
      task.engine,
      prompt,
      task.model,
      task.effort,
      task.speed,
      timeoutSeconds,
      projectRoot
    );
  } catch (err) {
    outcome = {
      ok: false,
      output: "",
      error: `Internal worker error: ${err && err.message ? err.message : String(err)}`,
    };
  }
  const durationSeconds = Number((Number(process.hrtime.bigint() - startedNs) / 1e9).toFixed(3));
  const outputText = typeof outcome.output === "string" ? outcome.output : "";
  let outputBytes = 0;
  try {
    io.writeTextAtomic(path.join(jobDir, task.output_file), outputText);
    outputBytes = Buffer.byteLength(outputText, "utf8");
  } catch (err) {
    outcome = {
      ok: false,
      output: outputText,
      error: `${outcome.error ? `${outcome.error} ` : ""}Failed to write the task output file: ${err.message}`,
    };
  }
  task.status = outcome.ok ? "completed" : "failed";
  task.finished_at = io.isoNow();
  task.duration_seconds = durationSeconds;
  task.error = outcome.ok ? null : outcome.error || "Worker failed with no error detail.";
  task.output_bytes = outputBytes;
  saveJob(job, jobDir);
  appendProviderAudit({
    ...providerAuditBase(job, task, prompt),
    event: "fanout_task_finished",
    status: task.status,
    duration_seconds: task.duration_seconds,
    output_metadata: {
      output_file: task.output_file,
      output_bytes: task.output_bytes,
      output_redacted: true,
      error_present: task.error !== null,
    },
  });
}

// Drains the task list through a fixed-size pool of sequential lanes.
async function runJob(job, jobDir, prompts, timeoutSeconds) {
  const projectRoot = projectRootDir();
  const concurrency = Math.max(1, intEnv("MYTHIFY_FANOUT_CONCURRENCY", 3));
  const laneCount = Math.min(concurrency, job.tasks.length);
  let cursor = 0;
  const lane = async () => {
    for (;;) {
      const index = cursor;
      cursor += 1;
      if (index >= job.tasks.length) {
        return;
      }
      await runOneTask(job, jobDir, job.tasks[index], prompts[index], timeoutSeconds, projectRoot);
    }
  };
  const lanes = [];
  for (let i = 0; i < laneCount; i += 1) {
    lanes.push(lane());
  }
  await Promise.all(lanes);
  const registered = jobRegistry.get(job.id);
  if (registered) {
    registered.running = false;
  }
}

// Safety net for an unexpected runner crash: every unfinished task is marked
// failed with the internal error, so the job never reports running forever.
function markUnfinishedFailed(job, jobDir, errorText) {
  for (const task of job.tasks) {
    if (task.status === "pending" || task.status === "running") {
      task.status = "failed";
      task.finished_at = io.isoNow();
      task.error = errorText;
    }
  }
  saveJob(job, jobDir);
}

function listJobIdsOnDisk() {
  let names;
  try {
    names = fs.readdirSync(fanoutRootDir());
  } catch {
    return [];
  }
  return names.filter((name) => /^fo-\d{14}-[0-9a-f]{4}$/.test(name)).sort();
}

// Loads a job by id, defaulting to the most recent one. If the job has
// unfinished tasks on disk but this server process never started it (the
// server restarted), those tasks are marked interrupted, because background
// workers die with the server process that spawned them.
function loadJob(jobId) {
  let id = (jobId || "").trim();
  if (id === "") {
    if (lastJobId !== null && fs.existsSync(path.join(fanoutRootDir(), lastJobId, "job.json"))) {
      id = lastJobId;
    } else {
      const ids = listJobIdsOnDisk();
      if (ids.length === 0) {
        return { error: "[FAIL] No fanout jobs found. Start one with fanout_start." };
      }
      id = ids[ids.length - 1];
    }
  }
  const jobDir = path.join(fanoutRootDir(), id);
  const job = io.readJsonRecover(path.join(jobDir, "job.json"), () => null);
  if (job === null || typeof job !== "object" || !Array.isArray(job.tasks)) {
    return {
      error:
        `[FAIL] No fanout job "${id}" found (or its job.json is missing or corrupt). ` +
        "Call fanout_status with no job_id for the most recent job, or start a new one with fanout_start.",
    };
  }
  let interruptedNote = null;
  const unfinished = job.tasks.filter((t) => t.status === "running" || t.status === "pending");
  if (unfinished.length > 0 && !jobRegistry.has(job.id)) {
    for (const task of unfinished) {
      task.status = "interrupted";
      task.error =
        "Interrupted: the MCP server process that ran this job exited before the task finished.";
      if (task.started_at !== null && task.finished_at === null) {
        task.finished_at = io.isoNow();
      }
    }
    saveJob(job, jobDir);
    interruptedNote =
      "[WARN] This job was started by a previous server process; background workers die " +
      "with the server, so its unfinished tasks are now marked interrupted.";
  }
  return { job, jobDir, interruptedNote };
}

// ---------------------------------------------------------------------------
// Tool handlers
// ---------------------------------------------------------------------------

function handleFanoutStart({
  tasks,
  purpose,
  model,
  engine,
  effort,
  speed,
  visibility,
  session_model,
  spawn_ceiling,
  reviewer_allow_stronger,
  hosted_provider_billing_ack,
  hosted_provider_data_ack,
  hosted_provider_material_ack,
  timeout_seconds,
}) {
  const disabled = killSwitchText();
  if (disabled) {
    return disabled;
  }
  const depthRefusal = depthGuardText();
  if (depthRefusal) {
    return depthRefusal;
  }
  const maxTasks = intEnv("MYTHIFY_FANOUT_MAX_TASKS", 16);
  if (!Array.isArray(tasks) || tasks.length === 0) {
    return "[FAIL] fanout_start requires at least one task. No job was started.";
  }
  if (tasks.length > maxTasks) {
    return `[FAIL] Too many tasks: ${tasks.length} given, the maximum is ${maxTasks} (MYTHIFY_FANOUT_MAX_TASKS). No job was started.`;
  }

  // Job-level engine: explicit parameter, else env/auto-detection. The
  // detection error is only fatal when some task lacks an explicit engine.
  let jobEngine = (engine || "").trim();
  let jobEngineError = null;
  if (jobEngine === "") {
    const detected = autoDetectEngine();
    if (detected.error) {
      jobEngineError = detected.error;
    } else {
      jobEngine = detected.engine;
    }
  }

  const stateDir = io.resolveStateDir();
  const projectRoot = path.dirname(stateDir);
  const contextBytesCap = intEnv("MYTHIFY_FANOUT_CONTEXT_BYTES", 200000);
  const sessionModel = resolveSessionModel(session_model);
  const spawnCeiling = resolveSpawnCeiling(spawn_ceiling);
  const reviewerAllowStronger = reviewer_allow_stronger === true;
  const visibilitySelection = resolveVisibilitySelection(visibility, purpose, tasks);
  const resolvedTasks = [];
  for (let i = 0; i < tasks.length; i += 1) {
    const task = tasks[i] || {};
    const title = typeof task.title === "string" ? task.title : "";
    const taskRole = TASK_ROLES.includes(task.role) ? task.role : "worker";
    const label = `Task ${i + 1}${title !== "" ? ` ("${title}")` : ""}`;
    if (typeof task.prompt !== "string" || task.prompt.trim() === "") {
      return `[FAIL] ${label}: prompt must be a non-empty string. No job was started.`;
    }
    let taskEngine = (task.engine || "").trim();
    if (taskEngine === "") {
      if (jobEngine === "") {
        return jobEngineError;
      }
      taskEngine = jobEngine;
    }
    if (!ENGINES.includes(taskEngine)) {
      return `[FAIL] ${label}: unknown engine "${taskEngine}". Valid engines: ${ENGINES.join(", ")}. No job was started.`;
    }
    const modelSelection = resolveModelSelection(task.model, model, taskEngine);
    const effortSelection = resolveEffortSelection(
      task.effort,
      effort,
      taskEngine,
      modelSelection.model
    );
    const speedSelection = resolveSpeedSelection(task.speed, speed);
    const taskModel = resolveEngineSpecificModel(
      taskEngine,
      modelSelection.model,
      effortSelection.effort,
      speedSelection.speed
    );
    const modelCeiling = ceilingCheck(sessionModel, spawnCeiling.ceiling, taskModel, {
      taskRole,
      reviewerAllowStronger,
    });
    if (!modelCeiling.ok) {
      const optInHint =
        taskRole === "reviewer" && modelCeiling.status === "violates_same_or_lower"
          ? 'Pass reviewer_allow_stronger: true with role: "reviewer", or pass spawn_ceiling: "allow_stronger", to opt in.'
          : 'Pass spawn_ceiling: "allow_stronger" to opt in.';
      return (
        `[FAIL] ${label}: spawned model "${taskModel}" (tier ${modelCeiling.workerTier}) ` +
        `exceeds session model "${sessionModel.model}" (tier ${sessionModel.tier}) ` +
        `under spawn ceiling ${spawnCeiling.ceiling}. ${optInHint} No job was started.`
      );
    }
    const availability = engineAvailabilityError(taskEngine, taskModel);
    if (availability) {
      return `[FAIL] ${label}: ${availability} No job was started.`;
    }
    const assembled = assembleWorkerPrompt(
      { ...task, effort: effortSelection.effort, speed: speedSelection.speed },
      projectRoot,
      contextBytesCap
    );
    if (assembled.error) {
      return `[FAIL] ${label}: ${assembled.error} No job was started.`;
    }
    resolvedTasks.push({
      title,
      role: taskRole,
      engine: taskEngine,
      model: taskModel,
      modelSource: modelSelection.modelSource,
      modelTier: modelCeiling.workerTier,
      modelCeilingStatus: modelCeiling.status,
      strongerReviewerOptIn: modelCeiling.status === "reviewer_stronger_opt_in",
      effort: effortSelection.effort,
      effortSource: effortSelection.effortSource,
      speed: speedSelection.speed,
      speedSource: speedSelection.speedSource,
      prompt: assembled.prompt,
    });
  }

  const hostedProviderEngines = [
    ...new Set(
      resolvedTasks
        .filter((resolved) => HOSTED_PROVIDER_ENGINES.includes(resolved.engine))
        .map((resolved) => resolved.engine)
    ),
  ].sort();
  const hostedProviderRequired = hostedProviderEngines.length > 0;
  const hostedProviderAcks = {
    hosted_provider_billing_ack: hosted_provider_billing_ack === true,
    hosted_provider_data_ack: hosted_provider_data_ack === true,
    hosted_provider_material_ack: hosted_provider_material_ack === true,
  };
  const missingHostedProviderAcks = HOSTED_PROVIDER_REQUIRED_ACKS.filter(
    (name) => hostedProviderAcks[name] !== true
  );
  if (hostedProviderRequired && missingHostedProviderAcks.length > 0) {
    return (
      "[FAIL] Hosted provider fanout requires explicit acknowledgement before using " +
      `metered remote engines (${hostedProviderEngines.join(", ")}): ` +
      `${missingHostedProviderAcks.map((name) => `${name}=true`).join(", ")}. ` +
      "No job was started."
    );
  }

  const jobEngineRecord = jobEngine !== "" ? jobEngine : resolvedTasks[0].engine;
  const jobModelSelection = resolveModelSelection(undefined, model, jobEngineRecord);
  const jobSpeedSelection = resolveSpeedSelection(undefined, speed);
  const jobEffortSelection = resolveEffortSelection(
    undefined,
    effort,
    jobEngineRecord,
    jobModelSelection.model
  );
  const jobModelRecord = resolveEngineSpecificModel(
    jobEngineRecord,
    jobModelSelection.model,
    jobEffortSelection.effort,
    jobSpeedSelection.speed
  );
  const jobModelCeiling = ceilingCheck(sessionModel, spawnCeiling.ceiling, jobModelRecord);
  const timeoutSelection =
    typeof timeout_seconds === "number" && timeout_seconds > 0
      ? { value: timeout_seconds, source: "explicit" }
      : positiveIntEnvWithSource("MYTHIFY_FANOUT_TIMEOUT_SECONDS", 600);
  const jobTimeout = timeoutSelection.value;
  const jobCostMetadata = engineCostMetadata(jobEngineRecord);
  const jobId = `fo-${io.stampNow()}-${crypto.randomBytes(2).toString("hex")}`;
  const jobDir = path.join(stateDir, "fanout", jobId);
  const now = io.isoNow();
  const job = {
    id: jobId,
    created: now,
    engine: jobEngineRecord,
    ...jobCostMetadata,
    model: jobModelRecord,
    model_source: jobModelSelection.modelSource,
    model_tier: jobModelCeiling.workerTier,
    model_ceiling_status: jobModelCeiling.status,
    session_model: sessionModel.model,
    session_model_source: sessionModel.source,
    session_model_tier: sessionModel.tier,
    spawn_ceiling: spawnCeiling.ceiling,
    spawn_ceiling_source: spawnCeiling.source,
    reviewer_allow_stronger: reviewerAllowStronger,
    hosted_provider_engines: hostedProviderEngines,
    hosted_provider_billing_acknowledged: hostedProviderAcks.hosted_provider_billing_ack,
    hosted_provider_data_acknowledged: hostedProviderAcks.hosted_provider_data_ack,
    hosted_provider_material_acknowledged: hostedProviderAcks.hosted_provider_material_ack,
    effort: jobEffortSelection.effort,
    effort_source: jobEffortSelection.effortSource,
    speed: jobSpeedSelection.speed,
    speed_source: jobSpeedSelection.speedSource,
    visibility: visibilitySelection.visibility,
    visibility_source: visibilitySelection.source,
    visibility_requested: visibilitySelection.requested,
    visibility_reason: visibilitySelection.reason,
    purpose: typeof purpose === "string" ? purpose : "",
    timeout_seconds: jobTimeout,
    timeout_source: timeoutSelection.source,
    last_updated: now,
    tasks: resolvedTasks.map((resolved, i) => {
      const taskCostMetadata = engineCostMetadata(resolved.engine);
      return {
        id: i + 1,
        title: resolved.title,
        role: resolved.role,
        status: "pending",
        engine: resolved.engine,
        ...taskCostMetadata,
        model: resolved.model,
        model_source: resolved.modelSource,
        model_tier: resolved.modelTier,
        model_ceiling_status: resolved.modelCeilingStatus,
        stronger_reviewer_opt_in: resolved.strongerReviewerOptIn,
        effort: resolved.effort,
        effort_source: resolved.effortSource,
        speed: resolved.speed,
        speed_source: resolved.speedSource,
        timeout_seconds: jobTimeout,
        timeout_source: timeoutSelection.source,
        started_at: null,
        finished_at: null,
        duration_seconds: 0,
        error: null,
        output_file: `task-${i + 1}-output.md`,
        output_bytes: 0,
      };
    }),
  };
  io.writeJsonAtomic(path.join(jobDir, "job.json"), job);
  jobRegistry.set(jobId, { running: true });
  lastJobId = jobId;

  // Kick off the background runner WITHOUT awaiting it: fanout_start returns
  // the job id immediately and the pool drains in the background.
  runJob(job, jobDir, resolvedTasks.map((resolved) => resolved.prompt), jobTimeout).catch((err) => {
    try {
      markUnfinishedFailed(
        job,
        jobDir,
        `Internal fanout runner error: ${err && err.message ? err.message : String(err)}`
      );
    } catch {
      // Last-resort guard; the job stays readable from its previous state.
    }
    const registered = jobRegistry.get(jobId);
    if (registered) {
      registered.running = false;
    }
  });

  const concurrency = Math.max(1, intEnv("MYTHIFY_FANOUT_CONCURRENCY", 3));
  const lines = [
    `[OK] Fanout job ${jobId} started: ${job.tasks.length} ${job.tasks.length === 1 ? "task" : "tasks"}, concurrency ${concurrency}, ceiling ${job.spawn_ceiling}, visibility ${job.visibility}, timeout ${jobTimeout}s per worker.`,
  ];
  lines.push(
    `Reviewer stronger opt-in: ${job.reviewer_allow_stronger ? "enabled" : "disabled"}.`
  );
  if (job.hosted_provider_engines.length > 0) {
    lines.push(
      `Hosted provider guard: acknowledged for ${job.hosted_provider_engines.join(", ")}; provider output remains material, not verification.`
    );
  }
  lines.push(visibilityGuidance(job.visibility));
  if (job.visibility === "quiet") {
    lines.push("Worker list suppressed by quiet visibility; use fanout_status for aggregate progress.");
  } else {
    for (const task of job.tasks) {
      lines.push(
        `[ ] ${task.id}. ${task.title} (role: ${task.role || "worker"}, engine: ${task.engine}${task.model !== "" ? `, model: ${task.model}` : ""}, effort: ${task.effort}, speed: ${task.speed})`
      );
    }
  }
  lines.push(
    "Workers run in the background inside this MCP server process; if the server dies, " +
      "running tasks die with it and are later reported as interrupted."
  );
  lines.push(
    "Monitor with fanout_status and collect outputs with fanout_results. Results are " +
      "material, not verification: merge them and verify the merged work with verify_run."
  );
  return lines.join("\n");
}

function handleFanoutStatus({ job_id }) {
  const disabled = killSwitchText();
  if (disabled) {
    return disabled;
  }
  const loaded = loadJob(job_id);
  if (loaded.error) {
    return loaded.error;
  }
  const { job, interruptedNote } = loaded;
  const counts = { pending: 0, running: 0, completed: 0, failed: 0, interrupted: 0 };
  for (const task of job.tasks) {
    counts[task.status] = (counts[task.status] || 0) + 1;
  }
  const lines = [
    `[OK] Fanout job ${job.id} (engine: ${job.engine}${job.model ? `, model: ${job.model}` : ""}, effort: ${job.effort || "medium"}, speed: ${job.speed || "auto"}, visibility: ${job.visibility || "summary"}, ceiling ${job.spawn_ceiling || "same_or_lower"}, timeout ${job.timeout_seconds}s per worker, created ${job.created}).`,
  ];
  if (interruptedNote) {
    lines.push(interruptedNote);
  }
  lines.push(
    `Reviewer stronger opt-in: ${job.reviewer_allow_stronger ? "enabled" : "disabled"}.`
  );
  lines.push(visibilityGuidance(job.visibility || "summary"));
  lines.push(
    `Tasks: ${job.tasks.length} total; ${counts.completed} completed, ${counts.failed} failed, ${counts.running} running, ${counts.pending} pending, ${counts.interrupted} interrupted.`
  );
  if ((job.visibility || "summary") === "quiet") {
    const failedTasks = job.tasks.filter((task) => task.status === "failed" && task.error);
    for (const task of failedTasks) {
      lines.push(`[!] ${task.id}. ${task.title} failed: ${String(task.error).slice(0, 500)}`);
    }
  } else {
    for (const task of job.tasks) {
      const icon = TASK_STATUS_ICONS[task.status] || "[ ]";
      let line = `${icon} ${task.id}. ${task.title} (${task.status}; role: ${task.role || "worker"}; engine: ${task.engine}`;
      if (task.model) {
        line += `, model: ${task.model}`;
      }
      if (task.model_tier) {
        line += `, tier: ${task.model_tier}`;
      }
      if (task.effort) {
        line += `, effort: ${task.effort}`;
      }
      if (task.speed) {
        line += `, speed: ${task.speed}`;
      }
      if (task.status === "running" && task.started_at) {
        const elapsed = Math.max(0, (Date.now() - Date.parse(task.started_at)) / 1000);
        line += `, elapsed ${elapsed.toFixed(1)}s`;
      } else if (typeof task.duration_seconds === "number" && task.duration_seconds > 0) {
        line += `, ${task.duration_seconds.toFixed(1)}s`;
      }
      line += ")";
      if (task.status === "failed" && task.error) {
        line += `\n    error: ${String(task.error).slice(0, 500)}`;
      }
      lines.push(line);
    }
  }
  if (counts.pending + counts.running === 0) {
    lines.push("All tasks finished. Collect outputs with fanout_results.");
  } else {
    lines.push("Workers are still running; call fanout_status again to refresh.");
  }
  return lines.join("\n");
}

function handleFanoutResults({ job_id, task_id }) {
  const disabled = killSwitchText();
  if (disabled) {
    return disabled;
  }
  const loaded = loadJob(job_id);
  if (loaded.error) {
    return loaded.error;
  }
  const { job, jobDir, interruptedNote } = loaded;
  let selected = job.tasks;
  if (task_id !== undefined && task_id !== null) {
    const match = job.tasks.find((task) => task.id === task_id);
    if (!match) {
      return `[FAIL] No task with id ${task_id} in fanout job ${job.id}.`;
    }
    selected = [match];
  }
  const finished = selected.filter((task) => task.status === "completed" || task.status === "failed");
  const unfinishedCount = selected.filter(
    (task) => task.status === "running" || task.status === "pending"
  ).length;
  const interruptedCount = selected.filter((task) => task.status === "interrupted").length;
  const lines = [];
  if (finished.length === 0) {
    lines.push(
      `[WARN] Fanout job ${job.id} has no completed or failed tasks${task_id !== undefined && task_id !== null ? ` matching task ${task_id}` : ""} yet.`
    );
  } else {
    lines.push(
      `[OK] Fanout job ${job.id}: results for ${finished.length} of ${selected.length} ${selected.length === 1 ? "task" : "tasks"}.`
    );
  }
  if (interruptedNote) {
    lines.push(interruptedNote);
  }
  lines.push(visibilityGuidance(job.visibility || "summary"));
  if (unfinishedCount > 0) {
    lines.push(
      `[WARN] ${unfinishedCount} ${unfinishedCount === 1 ? "task is" : "tasks are"} still running or pending; check fanout_status and call fanout_results again once they finish.`
    );
  }
  if (interruptedCount > 0) {
    lines.push(
      `[WARN] ${interruptedCount} ${interruptedCount === 1 ? "task was" : "tasks were"} interrupted and produced no result.`
    );
  }
  for (const task of finished) {
    lines.push("");
    lines.push(
      `=== Task ${task.id}: ${task.title} (${task.status}, ${typeof task.duration_seconds === "number" ? task.duration_seconds.toFixed(1) : "0.0"}s, engine: ${task.engine}${task.model ? `, model: ${task.model}` : ""}${task.model_tier ? `, tier: ${task.model_tier}` : ""}${task.effort ? `, effort: ${task.effort}` : ""}${task.speed ? `, speed: ${task.speed}` : ""}) ===`
    );
    if (task.status === "failed" && task.error) {
      lines.push(`[FAIL] ${task.error}`);
    }
    const outputPath = path.join(jobDir, task.output_file);
    let output = "";
    try {
      output = fs.readFileSync(outputPath, "utf8");
    } catch {
      output = "";
    }
    if (output === "") {
      lines.push("(no output)");
    } else if (output.length > RESULT_CAP_CHARS) {
      lines.push(output.slice(0, RESULT_CAP_CHARS));
      lines.push(
        `[WARN] Output truncated at ${RESULT_CAP_CHARS} characters; the full output (${task.output_bytes} bytes) is in ${outputPath}.`
      );
    } else {
      lines.push(output);
    }
  }
  if (finished.length > 0) {
    lines.push("");
    lines.push(
      "Fanout results are material, not verification: merge them and verify the merged work with verify_run."
    );
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

// Wires the three fanout tools into the server. deps supplies the helpers
// already defined in index.js: resolveStateDir, writeTextAtomic,
// writeJsonAtomic, readJsonRecover, isoNow, stampNow, and guarded.
export function registerFanoutTools(server, deps) {
  io = deps;

  server.registerTool(
    "fanout_start",
    {
      title: "Start a parallel delegation job",
      description:
        "Start a one-shot parallel delegation job: declare a list of tasks once and the server spawns, sequences, and collects background workers for you, returning a job id immediately. " +
        "Every task MUST be fully independent and self-contained: each one is a fresh model invocation with no memory of this conversation and no access to other tasks' outputs, and each one costs real money or subscription quota. " +
        "Visibility defaults to summary, can be quiet, verbose, or threaded, and can be inferred from the purpose or task prompts when set to auto. Threaded means request visible host threads only when the host supports them. " +
        "Use this to parallelize independent subtasks (drafting sections, analyzing separate files, generating variants) during long work; afterwards merge the results yourself and verify the merged work with verify_run, because fanout results are material, not verification.",
      inputSchema: {
        tasks: z
          .array(
            z.object({
              title: z.string().describe("Short task label shown in status and results."),
              prompt: z
                .string()
                .describe(
                  "The full self-contained instruction for this worker. The worker sees only this prompt plus any context_paths content; include everything it needs."
                ),
              context_paths: z
                .array(z.string())
                .optional()
                .describe(
                  "Files to inline into the worker prompt as labeled fenced blocks. Relative paths resolve against the project root (the parent of .mythify); absolute paths are allowed. Total inlined context per task is capped at MYTHIFY_FANOUT_CONTEXT_BYTES."
                ),
              role: z
                .enum(TASK_ROLES)
                .optional()
                .describe(
                  "Task role for model ceiling policy: worker by default, or reviewer for independent review tasks."
                ),
              model: z
                .string()
                .optional()
                .describe("Per-task model override; beats the job model and MYTHIFY_FANOUT_MODEL."),
              engine: z
                .string()
                .optional()
                .describe(
                  "Per-task engine override (claude-cli, codex-cli, cursor-agent, anthropic, openai, or command); beats the job engine and MYTHIFY_FANOUT_ENGINE."
                ),
              effort: z
                .enum(EFFORT_LEVELS)
                .optional()
                .describe(
                  "Per-task effort override: auto, low, medium, or high. Beats the job effort and MYTHIFY_FANOUT_EFFORT."
                ),
              speed: z
                .enum(SPEED_LEVELS)
                .optional()
                .describe(
                  "Per-task speed override: auto, standard, or fast. Beats the job speed and MYTHIFY_FANOUT_SPEED."
                ),
            })
          )
          .describe(
            "1 to MYTHIFY_FANOUT_MAX_TASKS fully independent tasks. Each task is a fresh model call that costs real money or subscription quota."
          ),
        purpose: z
          .string()
          .optional()
          .describe(
            "Optional original user request or reason for spawning workers. Used only to infer visibility when visibility is auto or omitted."
          ),
        model: z
          .string()
          .optional()
          .describe("Default model for every task; per-task model overrides it."),
        engine: z
          .string()
          .optional()
          .describe(
            "Default engine for every task (claude-cli, codex-cli, cursor-agent, anthropic, openai, or command); per-task engine overrides it. Omit to auto-detect."
          ),
        effort: z
          .enum(EFFORT_LEVELS)
          .optional()
          .describe(
            "Default effort for every task: auto, low, medium, or high. Per-task effort overrides it. Defaults to MYTHIFY_FANOUT_EFFORT or a model-derived default."
          ),
        speed: z
          .enum(SPEED_LEVELS)
          .optional()
          .describe(
            "Default speed for every task: auto, standard, or fast. Per-task speed overrides it. Auto preserves platform defaults; fast enables Codex fast mode where supported."
          ),
        visibility: z
          .enum(FANOUT_VISIBILITY_MODES)
          .optional()
          .describe(
            "How much worker activity the host should surface in the user chat: auto, quiet, summary, verbose, or threaded. Omit for auto inference, which defaults to summary unless the purpose or task prompts ask otherwise."
          ),
        session_model: z
          .string()
          .optional()
          .describe(
            "Current host session model used to enforce spawn_ceiling. Defaults to MYTHIFY_SESSION_MODEL."
          ),
        spawn_ceiling: z
          .enum(SPAWN_CEILINGS)
          .optional()
          .describe(
            "Maximum spawned model tier relative to session_model: auto, lower_only, same_or_lower, or allow_stronger. Defaults to MYTHIFY_SPAWN_CEILING or same_or_lower."
          ),
        reviewer_allow_stronger: z
          .boolean()
          .optional()
          .describe(
            'Reviewer-only opt-in that permits tasks with role: "reviewer" to exceed session_model under same_or_lower. It does not affect worker tasks or lower_only.'
          ),
        hosted_provider_billing_ack: z
          .boolean()
          .optional()
          .describe(
            "Required true before any anthropic or openai fanout task can run, acknowledging hosted providers can bill a metered external account."
          ),
        hosted_provider_data_ack: z
          .boolean()
          .optional()
          .describe(
            "Required true before any anthropic or openai fanout task can run, acknowledging prompts and inlined context are sent to a remote provider."
          ),
        hosted_provider_material_ack: z
          .boolean()
          .optional()
          .describe(
            "Required true before any anthropic or openai fanout task can run, acknowledging provider output is material and not verification evidence."
          ),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe(
            "Per-worker timeout in seconds; a worker is killed and its task fails on expiry. Defaults to MYTHIFY_FANOUT_TIMEOUT_SECONDS (600)."
          ),
      },
    },
    deps.guarded(handleFanoutStart)
  );

  server.registerTool(
    "fanout_status",
    {
      title: "Show fanout job status",
      description:
        "Show a fanout job's progress: per-task status icons with engine, model, model tier, effort, speed, and elapsed time, plus overall counts. Defaults to the most recent job. " +
        "Use this after fanout_start to monitor the background workers and to decide when fanout_results is worth calling. " +
        "If the server restarted since the job was started, its unfinished tasks are reported as interrupted, because background workers die with the server process.",
      inputSchema: {
        job_id: z.string().optional().describe("The job id from fanout_start; omit for the most recent job."),
      },
    },
    deps.guarded(handleFanoutStatus)
  );

  server.registerTool(
    "fanout_results",
    {
      title: "Collect fanout job results",
      description:
        "Return the outputs of a fanout job's completed and failed tasks (failures include the error and any remediation), optionally limited to one task by id. Defaults to the most recent job. " +
        "Per-task text is capped at 20000 characters with a pointer to the full output file on disk; tasks still running are flagged with a warning. " +
        "Use this once fanout_status shows tasks finished, then merge the material and verify the merged work with verify_run.",
      inputSchema: {
        job_id: z.string().optional().describe("The job id from fanout_start; omit for the most recent job."),
        task_id: z.number().int().optional().describe("Return only this task's result; omit for all finished tasks."),
      },
    },
    deps.guarded(handleFanoutResults)
  );
}
