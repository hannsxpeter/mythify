#!/usr/bin/env node
// Mythify MCP server v2.5.0
// Exposes the Mythify state model (memory, plans, lessons, verifications,
// reflections) as 25 core MCP tools over stdio, plus the 3 fanout tools for
// parallel delegation (src/fanout.js), 28 tools in total. On-disk formats are
// shared with the Python CLI (scripts/mythify.py); both implementations must
// interoperate on the same .mythify state directory. Fanout is MCP-only; the
// CLI deliberately does not implement it.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { registerFanoutTools } from "./fanout.js";
import {
  ADAPTER_CANDIDATES,
  EFFORT_LEVELS,
  FANOUT_VISIBILITY_MODES,
  HOST_MODEL_DEFAULTS,
  HOST_PLATFORMS as PLATFORMS,
  HOST_PROFILE_RANK,
  HOST_THINKING_LEVELS,
  MODEL_TIER_RANK,
  SPAWN_CEILINGS,
  SPEED_LEVELS,
  STRONG_HOST_TASK_TYPES,
  TRIAGE_ENGINES,
  TRIAGE_MODES,
  getHostCapability,
} from "./capability-registry.js";
import {
  MEMORY_CATEGORIES,
  MEMORY_CLEAR_MCP_REFUSAL,
  MEMORY_DEFAULT_CATEGORY,
} from "./operation-registry.js";

const VERSION = "2.5.0";
const TAIL_CHARS = 4000;
const STEP_STATUSES = ["pending", "in_progress", "completed", "failed", "skipped"];
const OUTCOME_STATUSES = ["active", "succeeded", "failed", "stopped"];
const STEP_ICONS = {
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

const CLASSIFICATION_RULES = [
  [
    "security",
    [
      "security", "vulnerability", "secret", "credential", "auth",
      "authentication", "authorization", "login", "permission",
      "permissions", "sandbox", "exploit", "token",
    ],
  ],
  ["release", ["release", "publish", "version", "tag", "npm", "package", "ship", "deploy"]],
  ["migration", ["migrate", "migration", "upgrade", "dependency", "dependencies", "schema", "breaking"]],
  ["performance", ["performance", "optimize", "slow", "latency", "throughput", "memory leak", "profile"]],
  ["frontend_ui", ["ui", "frontend", "component", "css", "responsive", "browser", "page", "screen", "layout"]],
  ["benchmark", ["benchmark", "eval", "measure", "metric", "compare", "pass rate", "success rate"]],
  ["research", ["research", "investigate", "find online", "look up", "survey", "source", "latest"]],
  ["review", ["review", "audit", "inspect", "critique", "findings", "risk"]],
  ["debugging", ["debug", "diagnose", "trace", "reproduce", "root cause"]],
  ["bugfix", ["bug", "fix", "failing", "failure", "error", "exception", "broken", "crash", "regression"]],
  ["test_generation", ["test", "tests", "coverage", "unit", "integration", "regression test"]],
  ["refactor", ["refactor", "cleanup", "clean up", "simplify", "rename", "restructure"]],
  ["feature", ["feature", "add", "implement", "support", "build", "create", "new"]],
  ["docs", ["docs", "documentation", "readme", "guide", "changelog", "manual"]],
  ["design", ["design", "architecture", "plan", "approach", "proposal", "spec"]],
];

const VERIFICATION_HINTS = {
  security: "Run security-focused tests plus the relevant normal suite; inspect permissions and secret handling.",
  release: "Run full tests, package/build checks, and version or artifact checks before publishing.",
  migration: "Run migration tests, compatibility checks, and rollback or fixture validation.",
  performance: "Run targeted benchmarks or profiling before and after the change.",
  frontend_ui: "Run build/lint plus browser or screenshot checks for affected views.",
  benchmark: "Run the benchmark harness and record JSON output, pass rates, evidence rates, and durations.",
  research: "Cite sources and record a verify claim only when no executable check exists.",
  review: "Read diffs/files and report findings with file and line references; tests are supporting evidence.",
  debugging: "Reproduce the failure first, then run the failing check again after the fix.",
  bugfix: "Run the failing or targeted regression test, then the nearest broader suite.",
  test_generation: "Run the added tests and confirm they fail before the fix when practical.",
  refactor: "Run the existing test suite and any type, lint, or build checks.",
  feature: "Run targeted tests for the feature plus the nearest broader suite.",
  docs: "Run docs generation, link checks, or a text/build check when available.",
  design: "Use verify claim for the design rationale, then create executable checks for implementation steps.",
  question: "No executable check is required unless the answer makes a factual or time-sensitive claim.",
  trivial: "Use the smallest available check, or no protocol command for a one-line answer.",
};

const TRIAGE_OUTPUT_SHAPE = {
  primary_type: "string",
  secondary_types: ["string"],
  ambiguity: "low|medium|high",
  hidden_questions: ["string"],
  likely_files_or_surfaces: ["string"],
  verification_plan: ["string"],
  fanout_plan: ["string"],
  risk_notes: ["string"],
  recommended_first_step: "string",
};

const VAGUE_REQUEST_TERMS = [
  "thing", "things", "stuff", "better", "problem", "issue", "issues",
  "it", "this", "that", "something", "somehow", "maybe", "unclear",
];

// ---------------------------------------------------------------------------
// Time and string helpers
// ---------------------------------------------------------------------------

function isoNow() {
  return new Date().toISOString();
}

function stampNow() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return (
    String(d.getUTCFullYear()) +
    p(d.getUTCMonth() + 1) +
    p(d.getUTCDate()) +
    p(d.getUTCHours()) +
    p(d.getUTCMinutes()) +
    p(d.getUTCSeconds())
  );
}

// Shared slug contract (docs/design.md, "Slugs"): lowercase, collapse runs of
// non-alphanumerics to "-", strip edge "-", truncate to 40 characters. May
// return an empty string; callers apply context-specific fallbacks, matching
// the Python CLI byte for byte.
function slugify(text) {
  return String(text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

function tail(text) {
  const s = String(text == null ? "" : text);
  return s.length > TAIL_CHARS ? s.slice(-TAIL_CHARS) : s;
}

// ---------------------------------------------------------------------------
// State directory resolution
// ---------------------------------------------------------------------------
// 1. MYTHIFY_DIR, used directly, created on demand (at write time).
// 2. Walk upward from cwd; the first directory containing .mythify wins.
// 3. Fall back to <cwd>/.mythify, lazily created on first write. Reads with
//    no state respond gracefully and never crash.

function resolveStateDir() {
  const envDir = process.env.MYTHIFY_DIR;
  if (envDir && envDir.trim() !== "") {
    return path.resolve(envDir);
  }
  let dir = process.cwd();
  for (;;) {
    const candidate = path.join(dir, ".mythify");
    try {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
        return candidate;
      }
    } catch {
      // Unreadable directory while walking up: keep going.
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      break;
    }
    dir = parent;
  }
  return path.join(process.cwd(), ".mythify");
}

function globalLessonsDir() {
  return path.join(os.homedir(), ".mythify", "lessons");
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

// ---------------------------------------------------------------------------
// Durable JSON IO: atomic writes, corrupt-file recovery, never crash
// ---------------------------------------------------------------------------

function writeTextAtomic(filePath, text) {
  ensureDir(path.dirname(filePath));
  const tmp = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.tmp-${process.pid}-${crypto.randomBytes(4).toString("hex")}`
  );
  fs.writeFileSync(tmp, text, "utf8");
  fs.renameSync(tmp, filePath);
}

function writeJsonAtomic(filePath, value) {
  writeTextAtomic(filePath, JSON.stringify(value, null, 2));
}

// Reads JSON from filePath. Missing file: returns defaultFactory(). Corrupt
// file: renames it aside to <filename>.corrupt-<YYYYMMDDHHMMSS>, prints a
// [WARN] to stderr, and returns defaultFactory().
function readJsonRecover(filePath, defaultFactory) {
  let raw;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch {
    return defaultFactory();
  }
  try {
    return JSON.parse(raw);
  } catch {
    const quarantine = `${filePath}.corrupt-${stampNow()}`;
    try {
      fs.renameSync(filePath, quarantine);
      process.stderr.write(
        `[WARN] Corrupt JSON in ${filePath}; moved to ${quarantine}; continuing with a fresh default.\n`
      );
    } catch {
      process.stderr.write(
        `[WARN] Corrupt JSON in ${filePath}; could not quarantine it; continuing with a fresh default.\n`
      );
    }
    return defaultFactory();
  }
}

function hostModelPath() {
  return path.join(resolveStateDir(), "host-model.json");
}

function normalizeHostPlatform(platform) {
  const value = (platform || "auto").trim();
  return PLATFORMS.includes(value) ? value : "auto";
}

function normalizeHostThinking(thinking) {
  const value = (thinking || "auto").trim();
  return HOST_THINKING_LEVELS.includes(value) ? value : "auto";
}

function normalizeHostSpeed(speed) {
  const value = (speed || "auto").trim();
  return SPEED_LEVELS.includes(value) ? value : "auto";
}

function detectHostPlatform(platform) {
  const explicit = normalizeHostPlatform(platform);
  if (explicit !== "auto") {
    return explicit;
  }
  if ((process.env.CODEX_THREAD_ID || "").trim() !== "") {
    return "codex-desktop";
  }
  if ((process.env.CLAUDECODE || "").trim() !== "" || (process.env.CLAUDE_CODE_ENTRYPOINT || "").trim() !== "") {
    return "claude-code";
  }
  return "unknown";
}

function readHostModelState() {
  const record = readJsonRecover(hostModelPath(), () => null);
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return null;
  }
  if (typeof record.target_model !== "string" || record.target_model.trim() === "") {
    return null;
  }
  return record;
}

function clearHostModelState() {
  try {
    fs.rmSync(hostModelPath(), { force: true });
  } catch {
    // Best effort clear.
  }
}

function hostSwitchActions(platform, targetModel, thinking, speed) {
  const actions = [];
  if (platform === "codex-desktop") {
    actions.push(
      "Use the Codex Desktop model picker for the current chat."
    );
    const threadId = (process.env.CODEX_THREAD_ID || "").trim();
    if (threadId !== "") {
      actions.push(
        `Codex app agents can continue this thread with model override: send_message_to_thread(threadId="${threadId}", model="${targetModel}"${thinking !== "auto" ? `, thinking="${thinking}"` : ""}).`
      );
    } else {
      actions.push(
        "Codex app agents can use send_message_to_thread with a model override when they know the target thread id."
      );
    }
  } else if (platform === "codex-cli") {
    actions.push(`Start or resume Codex with --model ${targetModel}.`);
    if (thinking !== "auto") {
      actions.push(`Use the host's reasoning effort control for ${thinking} when available.`);
    }
    if (speed !== "auto") {
      actions.push(`Use Codex speed ${speed} for spawned workers; host chat speed remains host-controlled.`);
    }
  } else if (platform === "claude-code") {
    actions.push(`In interactive Claude Code, run /model ${targetModel}.`);
    actions.push(`For a new Claude Code session, start with claude --model ${targetModel}.`);
  } else if (platform === "claude-desktop") {
    actions.push("Use the Claude Desktop model picker for the current chat.");
    actions.push("MCP servers cannot directly mutate Claude Desktop's active chat model.");
  } else if (platform === "cursor-desktop") {
    actions.push("Use the Cursor chat model picker for the current chat.");
    actions.push("For spawned Cursor Agent workers, pass model, effort, and speed through fanout_start.");
  } else if (platform === "cursor-agent") {
    actions.push(`Start or resume Cursor Agent with --model ${targetModel}.`);
    actions.push("For Mythify fanout workers, pass model per task or per job.");
  } else {
    actions.push("Use the host app's model picker or model command for the current chat.");
    actions.push("Mythify has recorded the target model for session policy and spawn ceiling checks.");
  }
  return actions;
}

function hostCapabilityForRecord(platform) {
  const capability = getHostCapability(platform);
  return {
    kind: capability.kind,
    status: capability.status,
    can_switch_current_thread: capability.can_switch_current_thread,
    can_set_new_thread_model: capability.can_set_new_thread_model,
    can_set_worker_model: capability.can_set_worker_model,
    can_set_thinking: capability.can_set_thinking,
    can_list_models: capability.can_list_models,
    can_confirm_current_model: capability.can_confirm_current_model,
  };
}

function withHostCapability(record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return record;
  }
  const platform = String(record.platform || "unknown").trim() || "unknown";
  return {
    ...record,
    host_capability: hostCapabilityForRecord(platform),
    can_apply_current_chat: false,
  };
}

function buildHostModelRecord({ platform, target_model, current_model, thinking, speed, reason }) {
  const targetModel = String(target_model || "").trim();
  const resolvedPlatform = detectHostPlatform(platform || "auto");
  const resolvedThinking = normalizeHostThinking(thinking || "auto");
  const resolvedSpeed = normalizeHostSpeed(speed || "auto");
  const actions = hostSwitchActions(resolvedPlatform, targetModel, resolvedThinking, resolvedSpeed);
  return {
    platform: resolvedPlatform,
    requested_platform: normalizeHostPlatform(platform || "auto"),
    target_model: targetModel,
    current_model: String(current_model || "").trim(),
    target_model_tier: classifyModelTier(targetModel),
    thinking: resolvedThinking,
    speed: resolvedSpeed,
    reason: String(reason || "").trim(),
    status: "recorded_requires_host_action",
    control: "host_selected",
    can_apply_current_chat: false,
    host_capability: hostCapabilityForRecord(resolvedPlatform),
    updated: isoNow(),
    host_actions: actions,
  };
}

function formatBool(value) {
  return value ? "yes" : "no";
}

function formatHostModelRecord(record) {
  const withCapability = withHostCapability(record);
  const capability = withCapability.host_capability || hostCapabilityForRecord(withCapability.platform);
  const lines = [
    `[OK] Host model switch ${withCapability.status}.`,
    `platform: ${withCapability.platform}`,
    `target model: ${withCapability.target_model} (tier ${withCapability.target_model_tier})`,
    `current model: ${withCapability.current_model || "unknown"}`,
    `thinking: ${withCapability.thinking}`,
    `speed: ${withCapability.speed}`,
    `current-chat switch: ${formatBool(capability.can_switch_current_thread)}`,
    `new-thread model: ${formatBool(capability.can_set_new_thread_model)}`,
    `worker model: ${formatBool(capability.can_set_worker_model)}`,
    `thinking control: ${formatBool(capability.can_set_thinking)}`,
    "scope: Mythify recorded the requested host model for model_policy and spawn ceiling checks.",
    "host action required:",
  ];
  for (const action of withCapability.host_actions || []) {
    lines.push(`- ${action}`);
  }
  return lines.join("\n");
}

function appendJsonl(filePath, record) {
  ensureDir(path.dirname(filePath));
  fs.appendFileSync(filePath, JSON.stringify(record) + "\n", "utf8");
}

function readJsonl(filePath) {
  let raw;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch {
    return [];
  }
  const records = [];
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (trimmed === "") {
      continue;
    }
    try {
      records.push(JSON.parse(trimmed));
    } catch {
      // Skip bad jsonl records, matching the CLI's tolerant reader.
    }
  }
  return records;
}

// ---------------------------------------------------------------------------
// Memory store
// ---------------------------------------------------------------------------

function freshMemory() {
  const now = isoNow();
  return {
    entries: [],
    metadata: { created: now, last_updated: now, total_entries: 0 },
  };
}

function memoryPath() {
  return path.join(resolveStateDir(), "memory.json");
}

function loadMemory() {
  const data = readJsonRecover(memoryPath(), freshMemory);
  if (!Array.isArray(data.entries)) {
    data.entries = [];
  }
  if (typeof data.metadata !== "object" || data.metadata === null) {
    data.metadata = freshMemory().metadata;
  }
  return data;
}

function saveMemory(data) {
  data.metadata.last_updated = isoNow();
  data.metadata.total_entries = data.entries.length;
  writeJsonAtomic(memoryPath(), data);
}

// ---------------------------------------------------------------------------
// Plans
// ---------------------------------------------------------------------------

function plansDir() {
  return path.join(resolveStateDir(), "plans");
}

function planPath(slug) {
  return path.join(plansDir(), `${slug}.json`);
}

function activePointerPath() {
  return path.join(plansDir(), "active");
}

function readActiveSlug() {
  try {
    const slug = fs.readFileSync(activePointerPath(), "utf8").trim();
    return slug === "" ? null : slug;
  } catch {
    return null;
  }
}

// Atomic write with a trailing newline, byte-identical to the CLI's
// set_active_slug (scripts/mythify.py).
function setActiveSlug(slug) {
  writeTextAtomic(activePointerPath(), slug + "\n");
}

// Resolves a plan by explicit name (accepting the raw name or its slug) or by
// the active pointer. Returns {slug, plan} or {error} with explanatory text.
function resolvePlan(name) {
  if (name !== undefined && name !== null && String(name).trim() !== "") {
    const candidates = [String(name).trim(), slugify(name)].filter((c) => c !== "");
    for (const slug of candidates) {
      if (fs.existsSync(planPath(slug))) {
        const plan = readJsonRecover(planPath(slug), () => null);
        if (plan === null) {
          return { error: `[FAIL] Plan file for "${slug}" was corrupt and has been quarantined. Recreate it with plan_create.` };
        }
        return { slug, plan };
      }
    }
    return { error: `[FAIL] No plan named "${name}" found. Use plan_create to create one or plan_status to inspect the active plan.` };
  }
  const active = readActiveSlug();
  if (!active) {
    return { error: "[FAIL] No active plan. Create one with plan_create, or pass a plan name." };
  }
  if (!fs.existsSync(planPath(active))) {
    return { error: `[FAIL] Active plan pointer references "${active}" but no such plan file exists. Create a plan with plan_create.` };
  }
  const plan = readJsonRecover(planPath(active), () => null);
  if (plan === null) {
    return { error: `[FAIL] Active plan file "${active}" was corrupt and has been quarantined. Recreate it with plan_create.` };
  }
  return { slug: active, plan };
}

function verificationStepContext() {
  const active = readActiveSlug();
  if (!active || !fs.existsSync(planPath(active))) {
    return {
      plan: null,
      step_id: null,
      step_title: null,
      step_status: null,
    };
  }
  const plan = readJsonRecover(planPath(active), () => null);
  if (plan === null) {
    return {
      plan: null,
      step_id: null,
      step_title: null,
      step_status: null,
    };
  }
  const step = (plan.steps || []).find((item) => item.status === "in_progress");
  if (!step) {
    return {
      plan: null,
      step_id: null,
      step_title: null,
      step_status: null,
    };
  }
  return {
    plan: active,
    step_id: step.id,
    step_title: step.title,
    step_status: step.status,
  };
}

function verificationRecordMatchesStep(record, slug, stepId) {
  const hasBoundContext = record && (record.plan !== null && record.plan !== undefined
    || record.step_id !== null && record.step_id !== undefined);
  if (!hasBoundContext) {
    return true;
  }
  return record.plan === slug && record.step_id === stepId;
}

function savePlan(slug, plan) {
  plan.last_updated = isoNow();
  writeJsonAtomic(planPath(slug), plan);
}

function uniquePlanSlug(base) {
  if (!fs.existsSync(planPath(base))) {
    return base;
  }
  for (let n = 2; ; n += 1) {
    const candidate = `${base}-${n}`;
    if (!fs.existsSync(planPath(candidate))) {
      return candidate;
    }
  }
}

function stepLine(step) {
  const icon = STEP_ICONS[step.status] || "[ ]";
  let line = `${icon} ${step.id}. ${step.title}`;
  if (step.success_criteria) {
    line += ` (criteria: ${step.success_criteria})`;
  }
  if (step.result !== null && step.result !== undefined && step.result !== "") {
    line += `\n    result: ${step.result}`;
  }
  return line;
}

function nextPendingText(plan) {
  const next = (plan.steps || []).find((s) => s.status === "pending");
  if (!next) {
    return "No pending steps remain.";
  }
  let line = `Next pending step: [ ] ${next.id}. ${next.title}`;
  if (next.success_criteria) {
    line += ` (criteria: ${next.success_criteria})`;
  }
  return line;
}

// ---------------------------------------------------------------------------
// Outcome loops
// ---------------------------------------------------------------------------

function outcomesDir() {
  return path.join(resolveStateDir(), "outcomes");
}

function outcomeDir(slug) {
  return path.join(outcomesDir(), slug);
}

function outcomeGoalPath(slug) {
  return path.join(outcomeDir(slug), "goal.json");
}

function outcomeIterationsPath(slug) {
  return path.join(outcomeDir(slug), "iterations.jsonl");
}

function activeOutcomePath() {
  return path.join(outcomesDir(), "active");
}

function readActiveOutcomeSlug() {
  try {
    const value = fs.readFileSync(activeOutcomePath(), "utf8").trim();
    return value === "" ? null : value;
  } catch {
    return null;
  }
}

function setActiveOutcomeSlug(slug) {
  writeTextAtomic(activeOutcomePath(), slug + "\n");
}

function clearActiveOutcomeSlug(slug = null) {
  if (slug !== null && readActiveOutcomeSlug() !== slug) {
    return;
  }
  try {
    fs.rmSync(activeOutcomePath(), { force: true });
  } catch {
    // Best effort clear.
  }
}

function resolveOutcome(name) {
  if (name !== undefined && name !== null && String(name).trim() !== "") {
    const candidates = [String(name).trim(), slugify(name)].filter((c) => c !== "");
    for (const slug of candidates) {
      if (fs.existsSync(outcomeGoalPath(slug))) {
        const goal = readJsonRecover(outcomeGoalPath(slug), () => null);
        if (goal === null) {
          return { error: `[FAIL] Outcome file for "${slug}" was corrupt and has been quarantined.` };
        }
        return { slug, goal };
      }
    }
    return { error: `[FAIL] No outcome named "${name}" found. Use outcome_start to create one.` };
  }
  const active = readActiveOutcomeSlug();
  if (!active) {
    return { error: "[FAIL] No active outcome. Create one with outcome_start, or pass an outcome name." };
  }
  if (!fs.existsSync(outcomeGoalPath(active))) {
    return { error: `[FAIL] Active outcome pointer references "${active}" but no such outcome exists.` };
  }
  const goal = readJsonRecover(outcomeGoalPath(active), () => null);
  if (goal === null) {
    return { error: `[FAIL] Active outcome file "${active}" was corrupt and has been quarantined.` };
  }
  return { slug: active, goal };
}

function saveOutcome(slug, goal) {
  goal.updated = isoNow();
  writeJsonAtomic(outcomeGoalPath(slug), goal);
}

function uniqueOutcomeSlug(base) {
  if (!fs.existsSync(outcomeGoalPath(base))) {
    return base;
  }
  for (let n = 2; ; n += 1) {
    const candidate = `${base.slice(0, 36)}-${n}`;
    if (!fs.existsSync(outcomeGoalPath(candidate))) {
      return candidate;
    }
  }
}

function runShellCapture(command, timeoutSeconds) {
  const startedAt = process.hrtime.bigint();
  const run = spawnSync(command, {
    shell: true,
    encoding: "utf8",
    timeout: Math.round(timeoutSeconds * 1000),
    maxBuffer: 16 * 1024 * 1024,
  });
  const durationSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
  let stdoutTail = tail(run.stdout);
  let stderrTail = tail(run.stderr);
  const timedOut = Boolean(run.error && run.error.code === "ETIMEDOUT");
  let exitCode;
  let verified;
  if (timedOut) {
    exitCode = -1;
    verified = false;
    stderrTail = stderrTail + (stderrTail ? "\n" : "") + `(timed out after ${timeoutSeconds} seconds)`;
  } else if (typeof run.status === "number") {
    exitCode = run.status;
    verified = exitCode === 0;
  } else {
    exitCode = -1;
    verified = false;
    const reason = run.error
      ? run.error.message
      : run.signal
        ? `terminated by signal ${run.signal}`
        : "command did not produce an exit code";
    stderrTail = stderrTail + (stderrTail ? "\n" : "") + `(${reason})`;
  }
  return {
    command,
    exit_code: exitCode,
    duration_seconds: Number(durationSeconds.toFixed(3)),
    stdout_tail: stdoutTail,
    stderr_tail: stderrTail,
    verified,
    timed_out: timedOut,
  };
}

function parseMetricScore(output) {
  const match = String(output || "").match(/-?\d+(?:\.\d+)?/);
  return match ? Number.parseFloat(match[0]) : null;
}

function readOutcomeIterations(slug) {
  return readJsonl(outcomeIterationsPath(slug));
}

function formatOutcomeStatus(slug, goal, iterations = []) {
  const lines = [
    `[OK] Outcome ${slug}: ${goal.goal || ""}`,
    `status: ${goal.status || "active"}`,
    `success: ${goal.success_criteria || ""}`,
    `verify: ${goal.verify_command || ""}`,
    `iterations: ${goal.iteration_count || 0}/${goal.max_iterations || 1}`,
  ];
  if (goal.metric_command) {
    lines.push(`metric: ${goal.metric_command}`);
  }
  if (Array.isArray(goal.allowed_paths) && goal.allowed_paths.length > 0) {
    lines.push(`allowed paths: ${goal.allowed_paths.join(", ")}`);
  }
  if (iterations.length > 0) {
    const last = iterations[iterations.length - 1];
    lines.push(`last check: iteration ${last.iteration}, verified=${last.verified}, status=${last.status_after}`);
    if (last.next_action) {
      lines.push(`next: ${last.next_action}`);
    }
  } else {
    lines.push("next: do the first bounded attempt, then call outcome_check.");
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Lessons
// ---------------------------------------------------------------------------

function projectLessonsDir() {
  return path.join(resolveStateDir(), "lessons");
}

function lessonFileName(title) {
  const base = slugify(title).slice(0, 50) || "lesson";
  return `${base}-${stampNow()}.json`;
}

function recordLesson(title, detail, tags, scope) {
  const dir = scope === "global" ? globalLessonsDir() : projectLessonsDir();
  ensureDir(dir);
  const fileName = lessonFileName(title);
  const lesson = {
    title,
    detail,
    tags: Array.isArray(tags) ? tags : [],
    created: isoNow(),
  };
  writeJsonAtomic(path.join(dir, fileName), lesson);
  return fileName;
}

function readLessonsFrom(dir, scopeLabel) {
  let names;
  try {
    names = fs.readdirSync(dir);
  } catch {
    return [];
  }
  const lessons = [];
  for (const name of names.sort()) {
    if (!name.endsWith(".json")) {
      continue;
    }
    const lesson = readJsonRecover(path.join(dir, name), () => null);
    if (lesson && typeof lesson === "object") {
      lessons.push({ scope: scopeLabel, lesson });
    }
  }
  return lessons;
}

// ---------------------------------------------------------------------------
// Verification and reflection logs
// ---------------------------------------------------------------------------

function verificationsPath() {
  return path.join(resolveStateDir(), "verifications.jsonl");
}

function reflectionsPath() {
  return path.join(resolveStateDir(), "reflections.jsonl");
}

// ---------------------------------------------------------------------------
// Tool handler plumbing
// ---------------------------------------------------------------------------

function textResult(text) {
  return { content: [{ type: "text", text }] };
}

function wordish(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, " ");
}

function containsAny(text, terms) {
  const haystack = ` ${wordish(text).trim().split(/\s+/).filter(Boolean).join(" ")} `;
  return terms.filter((term) => {
    const needle = wordish(term).trim().split(/\s+/).filter(Boolean).join(" ");
    return needle !== "" && haystack.includes(` ${needle} `);
  });
}

function classifyAmbiguity(text, words, signals, scores, taskType) {
  if (["question", "trivial"].includes(taskType)) {
    return "low";
  }
  if (containsAny(text, VAGUE_REQUEST_TERMS).length > 0 || (signals.length === 0 && words.length <= 18)) {
    return "high";
  }
  if (Object.keys(scores).length > 1 || words.length > 22) {
    return "medium";
  }
  return "low";
}

function modelTriageGate(taskType, risk, ceremony, ambiguity, text) {
  if (ceremony === "none") {
    return [
      "skip",
      "The deterministic classifier is enough for a simple question or one-step task.",
    ];
  }
  const highImpactTerms = [
    "production", "payment", "credential", "secret", "data loss",
    "delete", "remove", "drop", "deploy",
  ];
  if (risk === "high" && ambiguity === "high" && containsAny(text, highImpactTerms).length > 0) {
    return [
      "required",
      "High-impact ambiguous work deserves a cheap second read before planning.",
    ];
  }
  if (ambiguity === "high") {
    return [
      "recommended",
      "The request is underspecified enough that a fast framing pass can reduce rework.",
    ];
  }
  if (
    [
      "research", "review", "benchmark", "design", "debugging",
      "security", "migration", "release", "performance",
    ].includes(taskType)
  ) {
    return [
      "recommended",
      "This problem type benefits from an independent framing pass before execution.",
    ];
  }
  if (["feature", "refactor", "frontend_ui", "bugfix", "test_generation"].includes(taskType) || risk === "medium") {
    return [
      "optional",
      "A fast triage pass may help, but the main worker can proceed without it.",
    ];
  }
  return [
    "skip",
    "The deterministic classification gives enough routing signal for this task.",
  ];
}

function inferFanoutVisibility(text) {
  const normalized = String(text || "").toLowerCase().split(/\s+/).join(" ");
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
  if (containsAny(normalized, quietTerms).length > 0) {
    return {
      visibility: "quiet",
      source: "prompt",
      reason: "The prompt asks to keep background worker activity quiet.",
    };
  }
  if (containsAny(normalized, threadedTerms).length > 0) {
    return {
      visibility: "threaded",
      source: "prompt",
      reason:
        "The prompt asks for visible worker threads or separate chats when the host supports them.",
    };
  }
  if (containsAny(normalized, verboseTerms).length > 0) {
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

function executionProfileFor(taskType, risk, ceremony, ambiguity, text) {
  if (ceremony === "none") {
    return [
      "direct",
      "No protocol state is needed for a simple answer or one reversible edit.",
    ];
  }
  if (ceremony === "full" || risk === "high") {
    return [
      "full",
      "High-risk or heavy work needs the full plan, verify, reflect, and state loop.",
    ];
  }
  if (ambiguity === "high") {
    return [
      "standard",
      "Ambiguous work needs a plan or fast triage before execution.",
    ];
  }
  const focusedTerms = [
    "small", "single", "one file", "focused", "unit", "unittest",
    "test", "tests", "bug", "fix", "failing", "regression",
  ];
  if (
    ["bugfix", "test_generation"].includes(taskType) ||
    (["docs", "refactor"].includes(taskType) && containsAny(text, focusedTerms).length > 0)
  ) {
    return [
      "fast",
      "Focused low-risk work can skip plan state but must still use verify run.",
    ];
  }
  if (ceremony === "light") {
    return [
      "fast",
      "Light work can use the fast profile unless it expands into multiple steps.",
    ];
  }
  return [
    "standard",
    "Use a plan with verifiable steps and verify run before completion.",
  ];
}

function classifyTaskText(taskText) {
  const text = String(taskText || "").toLowerCase().split(/\s+/).join(" ");
  const words = text.replaceAll("/", " ").replaceAll("_", " ").split(/\s+/).filter(Boolean);
  const signals = [];
  const scores = {};
  for (const [taskType, terms] of CLASSIFICATION_RULES) {
    const matches = containsAny(text, terms);
    if (matches.length > 0) {
      scores[taskType] = matches.length;
      signals.push(...matches);
    }
  }
  let taskType;
  const scoreEntries = Object.entries(scores).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (scoreEntries.length > 0) {
    taskType = scoreEntries[0][0];
  } else if (
    text.endsWith("?") ||
    ["what ", "why ", "how ", "can ", "should "].some((prefix) => text.startsWith(prefix))
  ) {
    taskType = "question";
  } else if (containsAny(text, VAGUE_REQUEST_TERMS).length > 0) {
    taskType = "feature";
  } else if (words.length <= 12) {
    taskType = "trivial";
  } else {
    taskType = "feature";
  }

  const highRiskTerms = [
    "delete", "remove", "drop", "destructive", "production", "payment",
    "security", "secret", "credential", "auth", "authentication",
    "authorization", "login", "release", "deploy", "migration", "schema",
    "data loss", "permission", "permissions",
  ];
  const mediumRiskTerms = [
    "refactor", "dependency", "upgrade", "performance", "benchmark",
    "multiple", "multi", "large", "cross", "api",
  ];
  let risk;
  if (containsAny(text, highRiskTerms).length > 0 || ["security", "release", "migration"].includes(taskType)) {
    risk = "high";
  } else if (
    containsAny(text, mediumRiskTerms).length > 0 ||
    ["feature", "refactor", "benchmark", "performance", "frontend_ui"].includes(taskType)
  ) {
    risk = "medium";
  } else {
    risk = "low";
  }

  const ambiguity = classifyAmbiguity(text, words, signals, scores, taskType);

  let ceremony;
  if (["trivial", "question"].includes(taskType) && risk === "low") {
    ceremony = "none";
  } else if (risk === "low" && ["docs", "review", "research", "design"].includes(taskType)) {
    ceremony = "light";
  } else if (risk === "high" || ["benchmark", "migration", "release", "security"].includes(taskType)) {
    ceremony = "full";
  } else {
    ceremony = "standard";
  }

  let fanout;
  let fanoutReason;
  if (["research", "review", "benchmark", "design"].includes(taskType) || text.includes("parallel")) {
    fanout = "recommended";
    fanoutReason = "Independent analysis or comparison work can be split across workers.";
  } else if (["feature", "refactor", "frontend_ui"].includes(taskType) || text.includes("multiple files")) {
    fanout = "optional";
    fanoutReason = "Use fanout only for independent subtasks; keep dependent implementation sequential.";
  } else {
    fanout = "not_recommended";
    fanoutReason = "A single focused worker is simpler for this task type.";
  }

  const [executionProfile, executionProfileReason] = executionProfileFor(
    taskType,
    risk,
    ceremony,
    ambiguity,
    text
  );

  let nextAction;
  if (executionProfile === "direct") {
    nextAction = "Answer directly or make the single reversible edit; no plan is required.";
  } else if (executionProfile === "fast") {
    nextAction = "Use the fast profile: skip plan state, make the focused change, and run verify run before completion.";
  } else if (executionProfile === "standard") {
    nextAction = "Create a plan with verifiable steps, act step by step, and use verify run before completion.";
  } else {
    nextAction = "Use the full loop: plan, memory, step updates, verify run, reflect on failures, and summarize.";
  }

  const [modelTriage, modelTriageReason] = modelTriageGate(taskType, risk, ceremony, ambiguity, text);
  const fanoutVisibility = inferFanoutVisibility(text);

  return {
    task_type: taskType,
    risk,
    ambiguity,
    ceremony,
    execution_profile: executionProfile,
    execution_profile_reason: executionProfileReason,
    verification: VERIFICATION_HINTS[taskType] || VERIFICATION_HINTS.feature,
    fanout,
    fanout_reason: fanoutReason,
    fanout_visibility: fanoutVisibility.visibility,
    fanout_visibility_source: fanoutVisibility.source,
    fanout_visibility_reason: fanoutVisibility.reason,
    model_triage: modelTriage,
    model_triage_reason: modelTriageReason,
    signals: [...new Set(signals)].sort().slice(0, 10),
    next_action: nextAction,
  };
}

function shouldRunModelTriage(result, mode) {
  if (mode === "never") {
    return false;
  }
  if (mode === "always") {
    return true;
  }
  return ["recommended", "required"].includes(result.model_triage);
}

function buildTriagePrompt(taskText, classification) {
  return [
    "You are a fast triage model helping Mythify frame a task before the main agent plans.",
    "Do not edit files, run commands, or ask questions.",
    "Return only valid JSON with this exact shape:",
    JSON.stringify(TRIAGE_OUTPUT_SHAPE, null, 2),
    "",
    "User task:",
    String(taskText || ""),
    "",
    "Deterministic classification:",
    JSON.stringify(classification, null, 2),
    "",
    "Focus on the problem shape, likely hidden requirements, verification, risk, and whether independent fanout would help.",
  ].join("\n");
}

function formatClassification(result) {
  const lines = [
    "[OK] Task classification",
    `type: ${result.task_type}`,
    `risk: ${result.risk}`,
    `ambiguity: ${result.ambiguity}`,
    `ceremony: ${result.ceremony}`,
    `execution profile: ${result.execution_profile} (${result.execution_profile_reason})`,
    `verification: ${result.verification}`,
    `fanout: ${result.fanout} (${result.fanout_reason})`,
    `fanout visibility: ${result.fanout_visibility || "summary"} (${result.fanout_visibility_reason || "Summary visibility is the default."})`,
    `model triage: ${result.model_triage} (${result.model_triage_reason})`,
    `next: ${result.next_action}`,
  ];
  if (result.signals.length > 0) {
    lines.push(`signals: ${result.signals.join(", ")}`);
  }
  const policy = result.model_policy;
  if (policy) {
    const recommendation = policy.session?.recommendation || {};
    lines.push(
      `model policy: session=${policy.session?.control || "host_selected"}/${policy.session?.model_tier || "unknown"}; ` +
      `ceiling=${policy.spawn_ceiling?.policy || "same_or_lower"}; ` +
      `triage=${policy.triage?.engine || "auto"}/${policy.triage?.model_policy || "engine_default"}/${policy.triage?.effort || "low"}/${policy.triage?.speed || "auto"}; ` +
      `fanout=${policy.fanout_worker?.engine_policy || "local_first"}/${policy.fanout_worker?.effort || "medium"}/${policy.fanout_worker?.speed || "auto"}/${policy.fanout_worker?.visibility || "summary"}; ` +
      `verifier=${policy.verifier?.engine || "local_command"}`
    );
    lines.push(
      `host recommendation: ${recommendation.action || "recommend_set"} to ` +
      `${recommendation.target_profile || "standard"}/${recommendation.target_model || ""} ` +
      `thinking=${recommendation.thinking || "medium"} speed=${recommendation.speed || "auto"}`
    );
  }
  const run = result.model_triage_run;
  if (run) {
    if (!run.attempted) {
      lines.push(`fast triage run: skipped (${run.reason || ""})`);
    } else if (run.ok) {
      lines.push(`fast triage run: [OK] ${run.engine} model=${run.model || ""} duration=${run.duration_seconds}s`);
      if (run.parsed !== null && run.parsed !== undefined) {
        lines.push(`fast triage json: ${JSON.stringify(run.parsed)}`);
      } else if (run.output_tail) {
        lines.push(`fast triage output: ${run.output_tail}`);
      }
    } else {
      lines.push(`fast triage run: [FAIL] ${run.error || "triage worker failed"}`);
    }
  }
  return lines.join("\n");
}

function tailText(text, limit = TAIL_CHARS) {
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
  if (resolveClaudeTriageBin() !== null) {
    return "claude-cli";
  }
  if (resolveCodexTriageBin() !== null) {
    return "codex-cli";
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
  const origin = (process.env.CODEX_INTERNAL_ORIGINATOR_OVERRIDE || "").toLowerCase();
  if (origin.includes("codex") || process.env.CODEX_SHELL) {
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

function normalizePlatform(platform) {
  const value = (platform || "auto").trim();
  if (value === "auto") {
    return inferPlatform();
  }
  return PLATFORMS.includes(value) ? value : "unknown";
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

function selectTriageEngine(requestedEngine, platform) {
  const explicit = (requestedEngine || "").trim();
  if (explicit !== "") {
    return { engine: explicit, enginePolicy: "explicit" };
  }
  const envEngine = (process.env.MYTHIFY_TRIAGE_ENGINE || "").trim();
  if (envEngine !== "") {
    return { engine: envEngine, enginePolicy: "env" };
  }
  const preferred = preferredLocalEngine(platform);
  if (preferred !== "" && triageEngineAvailable(preferred)) {
    return { engine: preferred, enginePolicy: "platform_preferred" };
  }
  const detected = autoDetectTriageEngine();
  if (detected !== "") {
    return { engine: detected, enginePolicy: "auto_detected" };
  }
  return { engine: "", enginePolicy: "unavailable" };
}

function resolveTriageModelSelection(engine, requestedModel) {
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

function resolveSessionModel(sessionModel) {
  const explicit = (sessionModel || "").trim();
  if (explicit !== "") {
    return { model: explicit, source: "explicit" };
  }
  const envModel = (process.env.MYTHIFY_SESSION_MODEL || "").trim();
  if (envModel !== "") {
    return { model: envModel, source: "env" };
  }
  const hostModel = readHostModelState();
  if (hostModel !== null) {
    return { model: hostModel.target_model.trim(), source: "host_model_switch" };
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

function buildModelPolicy(classification, options) {
  const platform = normalizePlatform(options.platform || "auto");
  const requestedEffort = options.effort || "auto";
  const requestedSpeed = options.speed || "auto";
  const sessionModel = resolveSessionModel(options.session_model || "");
  const sessionTier = classifyModelTier(sessionModel.model);
  const spawnCeiling = resolveSpawnCeiling(options.spawn_ceiling || "auto");
  const { engine: triageEngine, enginePolicy: triageEnginePolicy } = selectTriageEngine(
    options.triage_engine || "",
    platform
  );
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
  return {
    session: {
      role: "current_conversation",
      control: "host_selected",
      platform,
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
      stronger_requires: "spawn_ceiling_allow_stronger",
    },
    triage: {
      role: "problem_framing",
      spawn: classification.model_triage || "skip",
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
    fanout_worker: {
      role: "independent_subtask",
      spawn: classification.fanout || "not_recommended",
      engine: "auto",
      engine_policy: "local_first",
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
      engine: "auto",
      engine_policy: "local_first",
      model_policy: "prefer_stronger_than_worker_when_available",
      model_relation_to_session: roleModelRelation("reviewer", sessionTier, spawnCeiling.ceiling),
      effort: reviewerEffort.effort,
      effort_policy: reviewerEffort.effortPolicy,
      speed: reviewerSpeed.speed,
      speed_policy: reviewerSpeed.speedPolicy,
      reason: "Use a separate review pass for high-risk or broad changes.",
    },
    verifier: {
      role: "evidence",
      spawn: "not_model_based",
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

function tempTriagePath(prefix, suffix) {
  return path.join(os.tmpdir(), `${prefix}-${stampNow()}-${crypto.randomBytes(3).toString("hex")}${suffix}`);
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

function runModelTriage(taskText, classification, options) {
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
  const cwd = path.dirname(resolveStateDir());
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

function envValue(name) {
  return (process.env[name] || "").trim();
}

function providerBaseUrl(input) {
  return String(input || "").trim() || envValue("MYTHIFY_OPENAI_COMPAT_BASE_URL") || envValue("MYTHIFY_PROVIDER_BASE_URL");
}

function providerModel(input) {
  return String(input || "").trim() || envValue("MYTHIFY_OPENAI_COMPAT_MODEL") || envValue("MYTHIFY_PROVIDER_MODEL");
}

function providerApiKeyEnv(input) {
  return String(input || "").trim() || "MYTHIFY_OPENAI_COMPAT_API_KEY";
}

function normalizeProviderBaseUrl(raw) {
  const value = String(raw || "").trim();
  if (value === "") {
    return { ok: false, baseUrl: "", error: "provider_probe requires base_url or MYTHIFY_OPENAI_COMPAT_BASE_URL." };
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return { ok: false, baseUrl: "", error: `Invalid provider base_url: ${value}` };
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    return { ok: false, baseUrl: "", error: "provider_probe base_url must use http or https." };
  }
  return { ok: true, baseUrl: parsed.toString().replace(/\/+$/, ""), error: "" };
}

function providerEndpoint(baseUrl, pathSuffix) {
  return `${baseUrl}/${pathSuffix.replace(/^\/+/, "")}`;
}

function providerHeaders(apiKeyEnv) {
  const headers = { accept: "application/json" };
  if (apiKeyEnv !== "") {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(apiKeyEnv)) {
      return { ok: false, headers, error: "api_key_env must be a valid environment variable name." };
    }
    const apiKey = envValue(apiKeyEnv);
    if (apiKey !== "") {
      headers.authorization = `Bearer ${apiKey}`;
    }
  }
  return { ok: true, headers, error: "" };
}

async function fetchProviderJson(url, options, timeoutSeconds) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.round(timeoutSeconds * 1000));
  const startedAt = process.hrtime.bigint();
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const text = await response.text();
    let json = null;
    if (text.trim() !== "") {
      try {
        json = JSON.parse(text);
      } catch {
        json = null;
      }
    }
    return {
      ok: response.ok,
      status_code: response.status,
      duration_seconds: Number((Number(process.hrtime.bigint() - startedAt) / 1e9).toFixed(3)),
      json,
      body_tail: tailText(text),
      error: response.ok ? "" : `HTTP ${response.status}`,
      timed_out: false,
    };
  } catch (err) {
    const timedOut = err && err.name === "AbortError";
    return {
      ok: false,
      status_code: 0,
      duration_seconds: Number((Number(process.hrtime.bigint() - startedAt) / 1e9).toFixed(3)),
      json: null,
      body_tail: "",
      error: timedOut ? `timed out after ${timeoutSeconds} seconds` : err && err.message ? err.message : String(err),
      timed_out: timedOut,
    };
  } finally {
    clearTimeout(timer);
  }
}

function modelNamesFromList(json) {
  const data = json && Array.isArray(json.data) ? json.data : [];
  return data.map((item) => String(item && item.id ? item.id : "")).filter(Boolean);
}

function chatContentFromCompletion(json) {
  const choice = json && Array.isArray(json.choices) ? json.choices[0] : null;
  const message = choice && choice.message ? choice.message : null;
  return String(message && message.content ? message.content : "");
}

async function probeOpenAICompatibleProvider({ base_url, model, timeout_seconds, api_key_env, check, prompt }) {
  const selectedCheck = check || "both";
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 10;
  const base = normalizeProviderBaseUrl(providerBaseUrl(base_url));
  const selectedModel = providerModel(model);
  const keyEnv = providerApiKeyEnv(api_key_env);
  const adapter = ADAPTER_CANDIDATES["generic-openai-compatible"] || {};
  const result = {
    provider: "generic-openai-compatible",
    provider_kind: adapter.kind || "model_provider",
    status: "blocked",
    base_url: base.baseUrl,
    model: selectedModel,
    check: selectedCheck,
    api_key_env: keyEnv,
    api_key_present: keyEnv !== "" && envValue(keyEnv) !== "",
    material_not_evidence: true,
    evidence_status: "probe_only_not_verification",
    can_answer_prompt: false,
    checks: [],
    error: "",
  };
  if (!base.ok) {
    result.error = base.error;
    return result;
  }
  if (!["models", "chat", "both"].includes(selectedCheck)) {
    result.error = "provider_probe check must be models, chat, or both.";
    return result;
  }
  if (["chat", "both"].includes(selectedCheck) && selectedModel === "") {
    result.error = "provider_probe check=chat or both requires model or MYTHIFY_OPENAI_COMPAT_MODEL.";
    return result;
  }
  const headersResult = providerHeaders(keyEnv);
  if (!headersResult.ok) {
    result.error = headersResult.error;
    return result;
  }

  if (["models", "both"].includes(selectedCheck)) {
    const models = await fetchProviderJson(
      providerEndpoint(base.baseUrl, "models"),
      { method: "GET", headers: headersResult.headers },
      timeoutSeconds
    );
    const names = modelNamesFromList(models.json);
    result.checks.push({
      name: "models",
      ok: models.ok,
      status_code: models.status_code,
      duration_seconds: models.duration_seconds,
      models_count: names.length,
      model_present: selectedModel === "" ? null : names.includes(selectedModel),
      error: models.error,
      timed_out: models.timed_out,
    });
  }

  if (["chat", "both"].includes(selectedCheck)) {
    const body = {
      model: selectedModel,
      messages: [
        {
          role: "user",
          content: String(prompt || "").trim() || "Reply with exactly: mythify-provider-probe-ok",
        },
      ],
      max_tokens: 32,
      temperature: 0,
    };
    const chat = await fetchProviderJson(
      providerEndpoint(base.baseUrl, "chat/completions"),
      {
        method: "POST",
        headers: { ...headersResult.headers, "content-type": "application/json" },
        body: JSON.stringify(body),
      },
      timeoutSeconds
    );
    const content = chatContentFromCompletion(chat.json);
    result.checks.push({
      name: "chat",
      ok: chat.ok && content !== "",
      status_code: chat.status_code,
      duration_seconds: chat.duration_seconds,
      response_tail: tailText(content, 1000),
      error: chat.ok && content !== "" ? "" : chat.error || "empty chat completion content",
      timed_out: chat.timed_out,
    });
  }

  result.can_answer_prompt = result.checks.some((item) => item.name === "chat" && item.ok);
  result.status = result.checks.length > 0 && result.checks.every((item) => item.ok) ? "available" : "blocked";
  result.error = result.status === "available"
    ? ""
    : result.checks.find((item) => !item.ok)?.error || "provider probe failed";
  return result;
}

function formatProviderProbe(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Provider probe ${result.status}.`,
    `provider: ${result.provider}`,
    `base_url: ${result.base_url || "unset"}`,
    `model: ${result.model || "unset"}`,
    `check: ${result.check}`,
    `api key env: ${result.api_key_env || "none"} (${result.api_key_present ? "set" : "unset"})`,
    "evidence: probe output is material, not verification evidence.",
  ];
  for (const item of result.checks || []) {
    const details = [`${item.name}: ${item.ok ? "ok" : "failed"}`, `status=${item.status_code}`];
    if (typeof item.models_count === "number") {
      details.push(`models=${item.models_count}`);
    }
    if (item.model_present !== null && item.model_present !== undefined) {
      details.push(`model_present=${item.model_present}`);
    }
    if (item.response_tail) {
      details.push(`response=${item.response_tail}`);
    }
    if (item.error) {
      details.push(`error=${item.error}`);
    }
    lines.push(details.join("; "));
  }
  if (result.error && (!result.checks || result.checks.length === 0)) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

const LOCAL_MODEL_ROLES = ["reader", "triage"];
const LOCAL_MODEL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);

function normalizeLocalProviderBaseUrl(raw) {
  const base = normalizeProviderBaseUrl(providerBaseUrl(raw));
  if (!base.ok) {
    return {
      ok: false,
      baseUrl: "",
      error: base.error.replace(/^provider_probe/, "local_model_run"),
    };
  }
  const parsed = new URL(base.baseUrl);
  if (!LOCAL_MODEL_HOSTS.has(parsed.hostname)) {
    return {
      ok: false,
      baseUrl: base.baseUrl,
      error: "local_model_run requires a localhost, 127.0.0.1, ::1, or 0.0.0.0 base_url.",
    };
  }
  return base;
}

function localModelSystemPrompt(role) {
  if (role === "triage") {
    return [
      "You are a local triage model helping Mythify frame a task before planning.",
      "Return concise material for the orchestrator to inspect.",
      "Do not claim verification, run commands, edit files, or decide completion.",
    ].join(" ");
  }
  return [
    "You are a local read-only model helping Mythify inspect supplied material.",
    "Summarize, extract facts, and note uncertainty for the orchestrator to inspect.",
    "Do not claim verification, run commands, edit files, or decide completion.",
  ].join(" ");
}

async function runLocalModelRole({ role, base_url, model, api_key_env, timeout_seconds, prompt, max_tokens }) {
  const selectedRole = role || "reader";
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 30;
  const selectedMaxTokens =
    typeof max_tokens === "number" && max_tokens > 0 ? Math.min(Math.floor(max_tokens), 2048) : 512;
  const base = normalizeLocalProviderBaseUrl(base_url);
  const selectedModel = providerModel(model);
  const keyEnv = providerApiKeyEnv(api_key_env);
  const adapter = ADAPTER_CANDIDATES["generic-openai-compatible"] || {};
  const result = {
    provider: "generic-openai-compatible",
    provider_kind: adapter.kind || "model_provider",
    role: selectedRole,
    status: "blocked",
    base_url: base.baseUrl,
    model: selectedModel,
    local_only: true,
    material_not_evidence: true,
    evidence_status: "model_output_not_verification",
    writes_state: false,
    verification_recorded: false,
    can_answer_prompt: false,
    max_tokens: selectedMaxTokens,
    output_tail: "",
    checks: [],
    error: "",
  };
  if (!LOCAL_MODEL_ROLES.includes(selectedRole)) {
    result.error = "local_model_run role must be reader or triage.";
    return result;
  }
  if (!base.ok) {
    result.error = base.error;
    return result;
  }
  if (selectedModel === "") {
    result.error = "local_model_run requires model or MYTHIFY_OPENAI_COMPAT_MODEL.";
    return result;
  }
  const userPrompt = String(prompt || "").trim();
  if (userPrompt === "") {
    result.error = "local_model_run requires prompt.";
    return result;
  }
  const headersResult = providerHeaders(keyEnv);
  if (!headersResult.ok) {
    result.error = headersResult.error;
    return result;
  }
  const body = {
    model: selectedModel,
    messages: [
      { role: "system", content: localModelSystemPrompt(selectedRole) },
      { role: "user", content: userPrompt },
    ],
    max_tokens: selectedMaxTokens,
    temperature: 0,
  };
  const chat = await fetchProviderJson(
    providerEndpoint(base.baseUrl, "chat/completions"),
    {
      method: "POST",
      headers: { ...headersResult.headers, "content-type": "application/json" },
      body: JSON.stringify(body),
    },
    timeoutSeconds
  );
  const content = chatContentFromCompletion(chat.json);
  result.checks.push({
    name: "chat",
    ok: chat.ok && content !== "",
    status_code: chat.status_code,
    duration_seconds: chat.duration_seconds,
    error: chat.ok && content !== "" ? "" : chat.error || "empty chat completion content",
    timed_out: chat.timed_out,
  });
  result.output_tail = tailText(content, 4000);
  result.can_answer_prompt = chat.ok && content !== "";
  result.status = result.can_answer_prompt ? "available" : "blocked";
  result.error = result.status === "available"
    ? ""
    : result.checks.find((item) => !item.ok)?.error || "local model run failed";
  return result;
}

function formatLocalModelRun(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Local model run ${result.status}.`,
    `role: ${result.role}`,
    `provider: ${result.provider}`,
    `base_url: ${result.base_url || "unset"}`,
    `model: ${result.model || "unset"}`,
    `local only: ${result.local_only ? "yes" : "no"}`,
    `writes state: ${result.writes_state ? "yes" : "no"}`,
    `verification recorded: ${result.verification_recorded ? "yes" : "no"}`,
    "evidence: model output is material, not verification evidence.",
  ];
  if (result.output_tail) {
    lines.push(`output: ${result.output_tail}`);
  }
  for (const item of result.checks || []) {
    const details = [
      `${item.name}: ${item.ok ? "ok" : "failed"}`,
      `status=${item.status_code}`,
    ];
    if (item.error) {
      details.push(`error=${item.error}`);
    }
    lines.push(details.join("; "));
  }
  if (result.error && (!result.checks || result.checks.length === 0)) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

const HOST_CLI_PROBES = {
  "kimi-code": {
    envName: "MYTHIFY_KIMI_BIN",
    binaryNames: ["kimi"],
    fallbacks: [
      path.join(os.homedir(), ".kimi-code", "bin", "kimi"),
      "/opt/homebrew/bin/kimi",
      "/usr/local/bin/kimi",
    ],
    checks: [
      { name: "version", args: ["--version"] },
      { name: "help", args: ["--help"] },
    ],
  },
  opencode: {
    envName: "MYTHIFY_OPENCODE_BIN",
    binaryNames: ["opencode"],
    fallbacks: [
      path.join(os.homedir(), ".local", "bin", "opencode"),
      "/opt/homebrew/bin/opencode",
      "/usr/local/bin/opencode",
    ],
    checks: [
      { name: "version", args: ["--version"] },
      { name: "run_help", args: ["run", "--help"] },
    ],
  },
  antigravity: {
    envName: "MYTHIFY_ANTIGRAVITY_BIN",
    binaryNames: ["agy"],
    fallbacks: [
      path.join(os.homedir(), ".local", "bin", "agy"),
      "/opt/homebrew/bin/agy",
      "/usr/local/bin/agy",
      "/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity",
    ],
    checks: [
      { name: "version", args: ["--version"] },
      { name: "help", args: ["--help"] },
    ],
  },
};

function resolveHostCliBinary(host, explicitBin) {
  const config = HOST_CLI_PROBES[host];
  if (!config) {
    return { bin: "", source: "unsupported", error: `Unsupported host ${host}.` };
  }
  const explicit = String(explicitBin || "").trim();
  if (explicit !== "") {
    return isExecutableFile(explicit)
      ? { bin: explicit, source: "explicit", error: "" }
      : { bin: "", source: "explicit", error: `Configured binary is not executable: ${explicit}` };
  }
  const envBin = envValue(config.envName);
  if (envBin !== "") {
    return isExecutableFile(envBin)
      ? { bin: envBin, source: `env:${config.envName}`, error: "" }
      : { bin: "", source: `env:${config.envName}`, error: `Configured binary is not executable: ${envBin}` };
  }
  for (const binaryName of config.binaryNames) {
    const found = findExecutableOnPath(binaryName);
    if (found !== null) {
      return { bin: found, source: "path", error: "" };
    }
  }
  for (const candidate of config.fallbacks) {
    if (isExecutableFile(candidate)) {
      return { bin: candidate, source: "fallback", error: "" };
    }
  }
  return {
    bin: "",
    source: "missing",
    error: `No ${host} binary found. Set ${config.envName} or pass bin.`,
  };
}

function runCliProbeCommand(bin, args, timeoutSeconds) {
  const startedAt = process.hrtime.bigint();
  const run = spawnSync(bin, args, {
    shell: false,
    encoding: "utf8",
    timeout: Math.round(timeoutSeconds * 1000),
    maxBuffer: 1024 * 1024,
  });
  const durationSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
  const timedOut = Boolean(run.error && run.error.code === "ETIMEDOUT");
  const exitCode = typeof run.status === "number" ? run.status : -1;
  let error = "";
  if (timedOut) {
    error = `timed out after ${timeoutSeconds} seconds`;
  } else if (exitCode !== 0) {
    error = tailText(run.stderr) || `command exited ${exitCode}`;
  } else if (run.error) {
    error = run.error.message;
  }
  return {
    command: [path.basename(bin), ...args].join(" "),
    args,
    ok: exitCode === 0,
    exit_code: exitCode,
    duration_seconds: Number(durationSeconds.toFixed(3)),
    stdout_tail: tailText(run.stdout, 2000),
    stderr_tail: tailText(run.stderr, 2000),
    error,
    timed_out: timedOut,
  };
}

function outputContains(check, pattern) {
  const text = `${check.stdout_tail || ""}\n${check.stderr_tail || ""}`.toLowerCase();
  return text.includes(pattern.toLowerCase());
}

function inferHostCliFeatures(host, checks) {
  if (host === "kimi-code") {
    const help = checks.find((item) => item.name === "help");
    return {
      can_run_noninteractive_prompt: Boolean(help && help.ok && outputContains(help, "-p")),
      evidence:
        help && help.ok && outputContains(help, "-p")
          ? "help output includes -p prompt mode"
          : "help output did not expose -p prompt mode",
    };
  }
  if (host === "opencode") {
    const runHelp = checks.find((item) => item.name === "run_help");
    return {
      can_run_noninteractive_prompt: Boolean(runHelp && runHelp.ok),
      evidence: runHelp && runHelp.ok ? "run --help succeeded" : "run --help failed",
    };
  }
  if (host === "antigravity") {
    const help = checks.find((item) => item.name === "help");
    return {
      can_run_noninteractive_prompt: Boolean(help && help.ok && outputContains(help, "-p")),
      evidence:
        help && help.ok && outputContains(help, "-p")
          ? "help output includes -p prompt mode"
          : "help output did not expose -p prompt mode",
    };
  }
  return { can_run_noninteractive_prompt: false, evidence: "unsupported host" };
}

function probeHostCli({ host, bin, timeout_seconds }) {
  const selectedHost = host || "opencode";
  const config = HOST_CLI_PROBES[selectedHost];
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 10;
  const adapter = ADAPTER_CANDIDATES[selectedHost] || {};
  const resolved = resolveHostCliBinary(selectedHost, bin);
  const result = {
    host: selectedHost,
    host_kind: adapter.kind || "host",
    status: "blocked",
    binary: resolved.bin,
    binary_source: resolved.source,
    material_not_evidence: true,
    evidence_status: "probe_only_not_verification",
    can_run_noninteractive_prompt: false,
    feature_evidence: "",
    mcp_setup_guide: selectedHost === "antigravity" ? "docs/antigravity-mcp-setup.md" : "",
    checks: [],
    error: resolved.error,
  };
  if (!config) {
    result.error = `host_cli_probe does not support ${selectedHost}.`;
    return result;
  }
  if (resolved.bin === "") {
    return result;
  }
  result.checks = config.checks.map((check) => ({
    name: check.name,
    ...runCliProbeCommand(resolved.bin, check.args, timeoutSeconds),
  }));
  const features = inferHostCliFeatures(selectedHost, result.checks);
  result.can_run_noninteractive_prompt = features.can_run_noninteractive_prompt;
  result.feature_evidence = features.evidence;
  const checksOk = result.checks.every((item) => item.ok);
  result.status = checksOk && result.can_run_noninteractive_prompt ? "available" : "blocked";
  result.error = result.status === "available"
    ? ""
    : result.checks.find((item) => !item.ok)?.error || features.evidence || "host CLI probe failed";
  return result;
}

function formatHostCliProbe(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Host CLI probe ${result.status}.`,
    `host: ${result.host}`,
    `binary: ${result.binary || "not found"}`,
    `binary source: ${result.binary_source}`,
    `noninteractive prompt: ${result.can_run_noninteractive_prompt ? "yes" : "no"}`,
    `feature evidence: ${result.feature_evidence || "none"}`,
    "evidence: probe output is material, not verification evidence.",
  ];
  if (result.mcp_setup_guide) {
    lines.push(`mcp setup guide: ${result.mcp_setup_guide}`);
  }
  for (const item of result.checks || []) {
    const details = [
      `${item.name}: ${item.ok ? "ok" : "failed"}`,
      `exit=${item.exit_code}`,
      `command=${item.command}`,
    ];
    if (item.error) {
      details.push(`error=${item.error}`);
    }
    lines.push(details.join("; "));
  }
  if (result.error && (!result.checks || result.checks.length === 0)) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

const HOST_CLI_RUNNERS = {
  "kimi-code": {
    outputMode: "final-message-only",
    buildArgs: ({ prompt }) => ["--print", "-p", prompt, "--final-message-only"],
  },
  opencode: {
    outputMode: "json",
    buildArgs: ({ prompt, model, agent }) => {
      const args = ["run", "--format", "json"];
      if (model !== "") {
        args.push("--model", model);
      }
      if (agent !== "") {
        args.push("--agent", agent);
      }
      args.push(prompt);
      return args;
    },
  },
};

function resolveHostCliRunCwd(rawCwd) {
  const selected = String(rawCwd || "").trim();
  const resolved = selected === "" ? path.dirname(resolveStateDir()) : path.resolve(selected);
  try {
    const stat = fs.statSync(resolved);
    if (!stat.isDirectory()) {
      return { ok: false, cwd: resolved, error: `host_cli_run cwd is not a directory: ${resolved}` };
    }
  } catch {
    return { ok: false, cwd: resolved, error: `host_cli_run cwd is not accessible: ${resolved}` };
  }
  return { ok: true, cwd: resolved, error: "" };
}

function runHostCliWorker({ host, bin, prompt, cwd, timeout_seconds, model, agent }) {
  const selectedHost = host || "opencode";
  const runner = HOST_CLI_RUNNERS[selectedHost];
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 120;
  const selectedPrompt = String(prompt || "").trim();
  const selectedModel = String(model || "").trim();
  const selectedAgent = String(agent || "").trim();
  const adapter = ADAPTER_CANDIDATES[selectedHost] || {};
  const resolved = resolveHostCliBinary(selectedHost, bin);
  const cwdResult = resolveHostCliRunCwd(cwd);
  const result = {
    host: selectedHost,
    host_kind: adapter.kind || "host",
    status: "blocked",
    binary: resolved.bin,
    binary_source: resolved.source,
    cwd: cwdResult.cwd,
    material_not_evidence: true,
    evidence_status: "worker_output_not_verification",
    writes_state: false,
    verification_recorded: false,
    worker_output_is_evidence: false,
    can_run_noninteractive_prompt: false,
    timeout_seconds: timeoutSeconds,
    model: selectedModel,
    agent: selectedAgent,
    model_applied: false,
    agent_applied: false,
    output_mode: runner ? runner.outputMode : "",
    command: "",
    args: [],
    exit_code: -1,
    duration_seconds: 0,
    stdout_tail: "",
    stderr_tail: "",
    output_tail: "",
    error: resolved.error,
    timed_out: false,
  };
  if (!runner) {
    result.error = `host_cli_run does not support ${selectedHost}.`;
    return result;
  }
  if (selectedPrompt === "") {
    result.error = "host_cli_run requires prompt.";
    return result;
  }
  if (!cwdResult.ok) {
    result.error = cwdResult.error;
    return result;
  }
  if (resolved.bin === "") {
    return result;
  }

  result.model_applied = selectedHost === "opencode" && selectedModel !== "";
  result.agent_applied = selectedHost === "opencode" && selectedAgent !== "";
  result.args = runner.buildArgs({
    prompt: selectedPrompt,
    model: selectedModel,
    agent: selectedAgent,
  });
  result.command = [path.basename(resolved.bin), ...result.args].join(" ");

  const startedAt = process.hrtime.bigint();
  const run = spawnSync(resolved.bin, result.args, {
    shell: false,
    encoding: "utf8",
    cwd: cwdResult.cwd,
    timeout: Math.round(timeoutSeconds * 1000),
    maxBuffer: 1024 * 1024,
  });
  const durationSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
  const timedOut = Boolean(run.error && run.error.code === "ETIMEDOUT");
  const exitCode = typeof run.status === "number" ? run.status : -1;
  result.exit_code = exitCode;
  result.duration_seconds = Number(durationSeconds.toFixed(3));
  result.stdout_tail = tailText(run.stdout, 4000);
  result.stderr_tail = tailText(run.stderr, 4000);
  result.output_tail = result.stdout_tail || result.stderr_tail;
  result.timed_out = timedOut;
  result.can_run_noninteractive_prompt = exitCode === 0;
  result.status = exitCode === 0 ? "available" : "blocked";
  if (timedOut) {
    result.error = `timed out after ${timeoutSeconds} seconds`;
  } else if (exitCode !== 0) {
    result.error = result.stderr_tail || `command exited ${exitCode}`;
  } else if (run.error) {
    result.error = run.error.message;
  } else {
    result.error = "";
  }
  return result;
}

function formatHostCliRun(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Host CLI run ${result.status}.`,
    `host: ${result.host}`,
    `binary: ${result.binary || "not found"}`,
    `binary source: ${result.binary_source}`,
    `cwd: ${result.cwd || "unset"}`,
    `model: ${result.model || "unset"} (${result.model_applied ? "applied" : "not applied"})`,
    `agent: ${result.agent || "unset"} (${result.agent_applied ? "applied" : "not applied"})`,
    `exit: ${result.exit_code}`,
    `writes state: ${result.writes_state ? "yes" : "no"}`,
    `verification recorded: ${result.verification_recorded ? "yes" : "no"}`,
    "evidence: worker output is material, not verification evidence.",
  ];
  if (result.output_tail) {
    lines.push(`output: ${result.output_tail}`);
  }
  if (result.error) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

const EXECUTION_PROBES = {
  "google-colab-cli": {
    envName: "MYTHIFY_COLAB_BIN",
    binaryNames: ["colab", "colab-cli", "google-colab"],
    fallbacks: [
      path.join(os.homedir(), ".local", "bin", "colab"),
      path.join(os.homedir(), ".local", "bin", "colab-cli"),
      "/opt/homebrew/bin/colab",
      "/opt/homebrew/bin/colab-cli",
      "/usr/local/bin/colab",
      "/usr/local/bin/colab-cli",
    ],
    checks: [
      { name: "version", args: ["--version"] },
      { name: "help", args: ["--help"] },
    ],
  },
};

function resolveExecutionProbeBinary(adapter, explicitBin) {
  const config = EXECUTION_PROBES[adapter];
  if (!config) {
    return { bin: "", source: "unsupported", error: `Unsupported execution adapter ${adapter}.` };
  }
  const explicit = String(explicitBin || "").trim();
  if (explicit !== "") {
    return isExecutableFile(explicit)
      ? { bin: explicit, source: "explicit", error: "" }
      : { bin: "", source: "explicit", error: `Configured binary is not executable: ${explicit}` };
  }
  const envBin = envValue(config.envName);
  if (envBin !== "") {
    return isExecutableFile(envBin)
      ? { bin: envBin, source: `env:${config.envName}`, error: "" }
      : { bin: "", source: `env:${config.envName}`, error: `Configured binary is not executable: ${envBin}` };
  }
  for (const binaryName of config.binaryNames) {
    const found = findExecutableOnPath(binaryName);
    if (found !== null) {
      return { bin: found, source: "path", error: "" };
    }
  }
  for (const candidate of config.fallbacks) {
    if (isExecutableFile(candidate)) {
      return { bin: candidate, source: "fallback", error: "" };
    }
  }
  return {
    bin: "",
    source: "missing",
    error: `No ${adapter} binary found. Set ${config.envName} or pass bin.`,
  };
}

function inferExecutionProbeFeatures(adapter, checks) {
  if (adapter === "google-colab-cli") {
    const checksOk = checks.length > 0 && checks.every((item) => item.ok);
    return {
      feature_evidence: checksOk
        ? "version and help commands succeeded; no remote runtime, accelerator, upload, or job was requested"
        : "version or help command failed before any remote job was attempted",
    };
  }
  return { feature_evidence: "unsupported execution adapter" };
}

function probeExecutionAdapter({ adapter, bin, timeout_seconds }) {
  const selectedAdapter = adapter || "google-colab-cli";
  const config = EXECUTION_PROBES[selectedAdapter];
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 10;
  const adapterInfo = ADAPTER_CANDIDATES[selectedAdapter] || {};
  const resolved = resolveExecutionProbeBinary(selectedAdapter, bin);
  const result = {
    adapter: selectedAdapter,
    adapter_kind: adapterInfo.kind || "execution_substrate",
    status: "blocked",
    binary: resolved.bin,
    binary_source: resolved.source,
    material_not_evidence: true,
    evidence_status: "probe_only_not_verification",
    non_billable: true,
    job_execution_enabled: false,
    can_run_remote_job: false,
    remote_runtime_provisioned: false,
    accelerator_requested: false,
    data_uploaded: false,
    artifact_retrieval_enabled: false,
    billing_guard: "probe_only_no_runtime_provisioning",
    feature_evidence: "",
    checks: [],
    error: resolved.error,
  };
  if (!config) {
    result.error = `execution_probe does not support ${selectedAdapter}.`;
    return result;
  }
  if (resolved.bin === "") {
    return result;
  }
  result.checks = config.checks.map((check) => ({
    name: check.name,
    ...runCliProbeCommand(resolved.bin, check.args, timeoutSeconds),
  }));
  const features = inferExecutionProbeFeatures(selectedAdapter, result.checks);
  result.feature_evidence = features.feature_evidence;
  const checksOk = result.checks.every((item) => item.ok);
  result.status = checksOk ? "available" : "blocked";
  result.error = result.status === "available"
    ? ""
    : result.checks.find((item) => !item.ok)?.error || features.feature_evidence || "execution probe failed";
  return result;
}

function formatExecutionProbe(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Execution probe ${result.status}.`,
    `adapter: ${result.adapter}`,
    `binary: ${result.binary || "not found"}`,
    `binary source: ${result.binary_source}`,
    `non-billable probe: ${result.non_billable ? "yes" : "no"}`,
    `job execution enabled: ${result.job_execution_enabled ? "yes" : "no"}`,
    `remote runtime provisioned: ${result.remote_runtime_provisioned ? "yes" : "no"}`,
    `accelerator requested: ${result.accelerator_requested ? "yes" : "no"}`,
    `data uploaded: ${result.data_uploaded ? "yes" : "no"}`,
    `feature evidence: ${result.feature_evidence || "none"}`,
    `billing guard: ${result.billing_guard}`,
    "evidence: probe output is material, not verification evidence.",
  ];
  for (const item of result.checks || []) {
    const details = [
      `${item.name}: ${item.ok ? "ok" : "failed"}`,
      `exit=${item.exit_code}`,
      `command=${item.command}`,
    ];
    if (item.error) {
      details.push(`error=${item.error}`);
    }
    lines.push(details.join("; "));
  }
  if (result.error && (!result.checks || result.checks.length === 0)) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

const LIFECYCLE_PROBES = {
  "google-agents-cli": {
    envName: "MYTHIFY_AGENTS_CLI_BIN",
    binaryNames: ["agents-cli"],
    fallbacks: [
      path.join(os.homedir(), ".local", "bin", "agents-cli"),
      "/opt/homebrew/bin/agents-cli",
      "/usr/local/bin/agents-cli",
    ],
    checks: [
      { name: "version", args: ["--version"] },
      { name: "help", args: ["--help"] },
      { name: "eval_help", args: ["eval", "--help"] },
    ],
  },
  "google-adk-cli": {
    envName: "MYTHIFY_ADK_BIN",
    binaryNames: ["adk"],
    fallbacks: [
      path.join(os.homedir(), ".local", "bin", "adk"),
      "/opt/homebrew/bin/adk",
      "/usr/local/bin/adk",
    ],
    checks: [
      { name: "version", args: ["--version"] },
      { name: "help", args: ["--help"] },
      { name: "eval_help", args: ["eval", "--help"] },
    ],
  },
};

function resolveLifecycleProbeBinary(adapter, explicitBin) {
  const config = LIFECYCLE_PROBES[adapter];
  if (!config) {
    return { bin: "", source: "unsupported", error: `Unsupported lifecycle adapter ${adapter}.` };
  }
  const explicit = String(explicitBin || "").trim();
  if (explicit !== "") {
    return isExecutableFile(explicit)
      ? { bin: explicit, source: "explicit", error: "" }
      : { bin: "", source: "explicit", error: `Configured binary is not executable: ${explicit}` };
  }
  const envBin = envValue(config.envName);
  if (envBin !== "") {
    return isExecutableFile(envBin)
      ? { bin: envBin, source: `env:${config.envName}`, error: "" }
      : { bin: "", source: `env:${config.envName}`, error: `Configured binary is not executable: ${envBin}` };
  }
  for (const binaryName of config.binaryNames) {
    const found = findExecutableOnPath(binaryName);
    if (found !== null) {
      return { bin: found, source: "path", error: "" };
    }
  }
  for (const candidate of config.fallbacks) {
    if (isExecutableFile(candidate)) {
      return { bin: candidate, source: "fallback", error: "" };
    }
  }
  return {
    bin: "",
    source: "missing",
    error: `No ${adapter} binary found. Set ${config.envName} or pass bin.`,
  };
}

function inferLifecycleProbeFeatures(adapter, checks) {
  const evalHelp = checks.find((item) => item.name === "eval_help");
  const checksOk = checks.length > 0 && checks.every((item) => item.ok);
  if (adapter === "google-agents-cli") {
    return {
      can_probe_eval: Boolean(evalHelp && evalHelp.ok),
      feature_evidence: checksOk
        ? "version, help, and eval help commands succeeded; no scaffold, run, eval execution, deploy, publish, or cloud mutation was requested"
        : "version, help, or eval help command failed before any lifecycle action was executed",
    };
  }
  if (adapter === "google-adk-cli") {
    return {
      can_probe_eval: Boolean(evalHelp && evalHelp.ok),
      feature_evidence: checksOk
        ? "version, help, and eval help commands succeeded; no create, run, eval execution, deploy, web server, or project mutation was requested"
        : "version, help, or eval help command failed before any lifecycle action was executed",
    };
  }
  return { can_probe_eval: false, feature_evidence: "unsupported lifecycle adapter" };
}

function probeLifecycleAdapter({ adapter, bin, timeout_seconds }) {
  const selectedAdapter = adapter || "google-agents-cli";
  const config = LIFECYCLE_PROBES[selectedAdapter];
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 10;
  const adapterInfo = ADAPTER_CANDIDATES[selectedAdapter] || {};
  const resolved = resolveLifecycleProbeBinary(selectedAdapter, bin);
  const result = {
    adapter: selectedAdapter,
    adapter_kind: adapterInfo.kind || "agent_lifecycle",
    status: "blocked",
    binary: resolved.bin,
    binary_source: resolved.source,
    material_not_evidence: true,
    evidence_status: "probe_only_not_verification",
    can_probe_eval: false,
    eval_execution_enabled: false,
    deployment_enabled: false,
    scaffold_enabled: false,
    run_enabled: false,
    cloud_mutation_enabled: false,
    project_mutation_enabled: false,
    billing_guard: "probe_only_no_lifecycle_mutation",
    feature_evidence: "",
    checks: [],
    error: resolved.error,
  };
  if (!config) {
    result.error = `lifecycle_probe does not support ${selectedAdapter}.`;
    return result;
  }
  if (resolved.bin === "") {
    return result;
  }
  result.checks = config.checks.map((check) => ({
    name: check.name,
    ...runCliProbeCommand(resolved.bin, check.args, timeoutSeconds),
  }));
  const features = inferLifecycleProbeFeatures(selectedAdapter, result.checks);
  result.can_probe_eval = features.can_probe_eval;
  result.feature_evidence = features.feature_evidence;
  const checksOk = result.checks.every((item) => item.ok);
  result.status = checksOk && result.can_probe_eval ? "available" : "blocked";
  result.error = result.status === "available"
    ? ""
    : result.checks.find((item) => !item.ok)?.error || features.feature_evidence || "lifecycle probe failed";
  return result;
}

function formatLifecycleProbe(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Lifecycle probe ${result.status}.`,
    `adapter: ${result.adapter}`,
    `binary: ${result.binary || "not found"}`,
    `binary source: ${result.binary_source}`,
    `eval help probe: ${result.can_probe_eval ? "yes" : "no"}`,
    `eval execution enabled: ${result.eval_execution_enabled ? "yes" : "no"}`,
    `deployment enabled: ${result.deployment_enabled ? "yes" : "no"}`,
    `scaffold enabled: ${result.scaffold_enabled ? "yes" : "no"}`,
    `run enabled: ${result.run_enabled ? "yes" : "no"}`,
    `cloud mutation enabled: ${result.cloud_mutation_enabled ? "yes" : "no"}`,
    `project mutation enabled: ${result.project_mutation_enabled ? "yes" : "no"}`,
    `feature evidence: ${result.feature_evidence || "none"}`,
    `billing guard: ${result.billing_guard}`,
    "evidence: probe output is material, not verification evidence.",
  ];
  for (const item of result.checks || []) {
    const details = [
      `${item.name}: ${item.ok ? "ok" : "failed"}`,
      `exit=${item.exit_code}`,
      `command=${item.command}`,
    ];
    if (item.error) {
      details.push(`error=${item.error}`);
    }
    lines.push(details.join("; "));
  }
  if (result.error && (!result.checks || result.checks.length === 0)) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

// Handlers never throw on bad state: any unexpected error becomes an
// explanatory [FAIL] text result.
function guarded(handler) {
  return async (args) => {
    try {
      return textResult(await handler(args || {}));
    } catch (err) {
      const message = err && err.message ? err.message : String(err);
      return textResult(`[FAIL] Internal error: ${message}`);
    }
  };
}

const server = new McpServer({ name: "mythify-mcp", version: VERSION });

// ---------------------------------------------------------------------------
// Classification tool
// ---------------------------------------------------------------------------

server.registerTool(
  "classify_task",
  {
    title: "Classify a task before planning",
    description:
      "Classify a user request before planning or acting. Returns task type, risk, recommended Mythify ceremony level, execution profile, verification strategy, and whether fanout is useful. " +
      "Use this at the start of non-trivial work so the agent can choose the right amount of planning, verification, reflection, memory, and delegation.",
    inputSchema: {
      task: z.string().describe("The user request or problem statement to classify."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable routing."),
      triage: z
        .enum(TRIAGE_MODES)
        .optional()
        .describe("Run a fast model triage pass: never by default, auto when the gate recommends it, or always."),
      triage_engine: z
        .enum(TRIAGE_ENGINES)
        .optional()
        .describe("Fast triage engine. Defaults to MYTHIFY_TRIAGE_ENGINE or local auto-detection."),
      triage_model: z
        .string()
        .optional()
        .describe("Fast triage model. Defaults to MYTHIFY_TRIAGE_MODEL or the engine default."),
      triage_timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("Fast triage timeout in seconds. Defaults to 120."),
      platform: z
        .enum(PLATFORMS)
        .optional()
        .describe(
          "Host platform for model policy. Use codex-desktop, claude-desktop, or cursor-desktop when known; defaults to auto."
        ),
      effort: z
        .enum(EFFORT_LEVELS)
        .optional()
        .describe(
          "Overall effort preference for spawned model roles. Auto keeps triage cheap and scales workers or reviewers by risk."
        ),
      speed: z
        .enum(SPEED_LEVELS)
        .optional()
        .describe(
          "Overall speed preference for spawned model roles. Auto preserves host defaults; fast enables Codex fast mode where supported."
        ),
      session_model: z
        .string()
        .optional()
        .describe("Current host session model for spawn ceiling policy. Defaults to MYTHIFY_SESSION_MODEL."),
      spawn_ceiling: z
        .enum(SPAWN_CEILINGS)
        .optional()
        .describe(
          "Maximum spawned model tier relative to the session model. Auto uses MYTHIFY_SPAWN_CEILING or same_or_lower."
        ),
    },
  },
  guarded(({
    task,
    format,
    triage,
    triage_engine,
    triage_model,
    triage_timeout_seconds,
    platform,
    effort,
    speed,
    session_model,
    spawn_ceiling,
  }) => {
    const result = classifyTaskText(task);
    result.model_policy = buildModelPolicy(result, {
      triage_engine: triage_engine || "",
      triage_model: triage_model || "",
      triage_timeout_seconds,
      platform: platform || "auto",
      effort: effort || "auto",
      speed: speed || "auto",
      session_model: session_model || "",
      spawn_ceiling: spawn_ceiling || "auto",
    });
    if ((triage || "never") !== "never") {
      result.model_triage_run = runModelTriage(task, result, {
        triage: triage || "never",
        triage_engine: triage_engine || "",
        triage_model: triage_model || "",
        triage_timeout_seconds,
        platform: platform || "auto",
        effort: effort || "auto",
        speed: speed || "auto",
        session_model: session_model || "",
        spawn_ceiling: spawn_ceiling || "auto",
      });
    }
    if (format === "json") {
      return "[OK] " + JSON.stringify(result, null, 2);
    }
    return formatClassification(result);
  })
);

// ---------------------------------------------------------------------------
// Host model switch tool
// ---------------------------------------------------------------------------

server.registerTool(
  "host_model_switch",
  {
    title: "Record a host chat model switch request",
    description:
      "Record the intended host chat model and return platform-specific switch guidance. " +
      "This updates Mythify session model policy for later classify_task and fanout_start calls, but the current chat model remains owned by the host app unless that host exposes a native switch action.",
    inputSchema: {
      action: z
        .enum(["switch", "status", "clear"])
        .optional()
        .describe("switch records a target model, status shows the recorded model, clear removes it."),
      platform: z
        .enum(PLATFORMS)
        .optional()
        .describe("Host platform. Use codex-desktop, claude-desktop, claude-code, cursor-desktop, or cursor-agent when known."),
      target_model: z
        .string()
        .optional()
        .describe("Target host model for action=switch."),
      current_model: z
        .string()
        .optional()
        .describe("Current host model when known, recorded for audit only."),
      thinking: z
        .enum(HOST_THINKING_LEVELS)
        .optional()
        .describe("Requested host reasoning effort when the host supports it."),
      speed: z
        .enum(SPEED_LEVELS)
        .optional()
        .describe("Requested host speed preference when the host supports it."),
      reason: z
        .string()
        .optional()
        .describe("Why this host switch was requested."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable host integrations."),
    },
  },
  guarded(({ action, platform, target_model, current_model, thinking, speed, reason, format }) => {
    const selectedAction = action || "switch";
    if (selectedAction === "status") {
      const record = readHostModelState();
      if (record === null) {
        const empty = { status: "unset", target_model: "", source: "unknown" };
        return format === "json" ? "[OK] " + JSON.stringify(empty, null, 2) : "[OK] No host model switch is recorded.";
      }
      const enriched = withHostCapability(record);
      return format === "json" ? "[OK] " + JSON.stringify(enriched, null, 2) : formatHostModelRecord(enriched);
    }
    if (selectedAction === "clear") {
      clearHostModelState();
      const cleared = { status: "cleared", target_model: "" };
      return format === "json" ? "[OK] " + JSON.stringify(cleared, null, 2) : "[OK] Host model switch record cleared.";
    }
    if (String(target_model || "").trim() === "") {
      return "[FAIL] host_model_switch action=switch requires target_model.";
    }
    const record = buildHostModelRecord({
      platform: platform || "auto",
      target_model,
      current_model: current_model || "",
      thinking: thinking || "auto",
      speed: speed || "auto",
      reason: reason || "",
    });
    writeJsonAtomic(hostModelPath(), record);
    return format === "json" ? "[OK] " + JSON.stringify(record, null, 2) : formatHostModelRecord(record);
  })
);

// ---------------------------------------------------------------------------
// Provider probe tool
// ---------------------------------------------------------------------------

server.registerTool(
  "provider_probe",
  {
    title: "Probe an OpenAI-compatible model provider",
    description:
      "Probe a configured generic OpenAI-compatible provider by calling /v1/models and, when requested, /v1/chat/completions. " +
      "Use this before assigning local reader or triage roles to a provider. The result is material, not verification evidence, and does not enable worker execution.",
    inputSchema: {
      provider: z
        .enum(["generic-openai-compatible"])
        .optional()
        .describe("Provider adapter to probe. Currently only generic-openai-compatible is supported."),
      base_url: z
        .string()
        .optional()
        .describe("OpenAI-compatible /v1 base URL. Defaults to MYTHIFY_OPENAI_COMPAT_BASE_URL."),
      model: z
        .string()
        .optional()
        .describe("Model id for chat probes. Defaults to MYTHIFY_OPENAI_COMPAT_MODEL."),
      check: z
        .enum(["models", "chat", "both"])
        .optional()
        .describe("Probe /models, /chat/completions, or both. Defaults to both."),
      api_key_env: z
        .string()
        .optional()
        .describe("Environment variable containing the API key. Defaults to MYTHIFY_OPENAI_COMPAT_API_KEY."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("HTTP timeout per request in seconds. Defaults to 10."),
      prompt: z
        .string()
        .optional()
        .describe("Optional tiny prompt for check=chat or both."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable probes."),
    },
  },
  guarded(async ({ provider, base_url, model, check, api_key_env, timeout_seconds, prompt, format }) => {
    const selectedProvider = provider || "generic-openai-compatible";
    if (selectedProvider !== "generic-openai-compatible") {
      const blocked = {
        provider: selectedProvider,
        status: "blocked",
        material_not_evidence: true,
        evidence_status: "probe_only_not_verification",
        error: "provider_probe currently supports only generic-openai-compatible.",
        checks: [],
      };
      return format === "json" ? "[FAIL] " + JSON.stringify(blocked, null, 2) : formatProviderProbe(blocked);
    }
    const result = await probeOpenAICompatibleProvider({
      base_url,
      model,
      check: check || "both",
      api_key_env,
      timeout_seconds,
      prompt,
    });
    const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
    return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatProviderProbe(result);
  })
);

// ---------------------------------------------------------------------------
// Local model role runner
// ---------------------------------------------------------------------------

server.registerTool(
  "local_model_run",
  {
    title: "Run a role-limited local model",
    description:
      "Run a reader or triage prompt against a localhost OpenAI-compatible model provider. " +
      "Use this for low-risk local model material before the orchestrator verifies claims with commands. The result is material, not verification evidence, and the tool writes no Mythify state.",
    inputSchema: {
      role: z
        .enum(LOCAL_MODEL_ROLES)
        .optional()
        .describe("Role to run. Defaults to reader. Allowed roles are reader and triage."),
      base_url: z
        .string()
        .optional()
        .describe("Local OpenAI-compatible /v1 base URL. Defaults to MYTHIFY_OPENAI_COMPAT_BASE_URL and must be localhost, 127.0.0.1, ::1, or 0.0.0.0."),
      model: z
        .string()
        .optional()
        .describe("Local model id. Defaults to MYTHIFY_OPENAI_COMPAT_MODEL."),
      prompt: z
        .string()
        .describe("Prompt or material for the local model."),
      api_key_env: z
        .string()
        .optional()
        .describe("Environment variable containing an optional local provider API key. Defaults to MYTHIFY_OPENAI_COMPAT_API_KEY."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("HTTP timeout in seconds. Defaults to 30."),
      max_tokens: z
        .number()
        .positive()
        .optional()
        .describe("Maximum requested completion tokens, capped at 2048. Defaults to 512."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable local model runs."),
    },
  },
  guarded(async ({ role, base_url, model, prompt, api_key_env, timeout_seconds, max_tokens, format }) => {
    const result = await runLocalModelRole({
      role: role || "reader",
      base_url,
      model,
      prompt,
      api_key_env,
      timeout_seconds,
      max_tokens,
    });
    const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
    return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatLocalModelRun(result);
  })
);

// ---------------------------------------------------------------------------
// Host CLI probe tool
// ---------------------------------------------------------------------------

server.registerTool(
  "host_cli_probe",
  {
    title: "Probe a host CLI adapter",
    description:
      "Probe Kimi Code, OpenCode, or Antigravity CLI availability by running only version and help commands. " +
      "Use this before enabling a host CLI adapter. The result is material, not verification evidence, and does not execute a prompt or start workers.",
    inputSchema: {
      host: z
        .enum(["kimi-code", "opencode", "antigravity"])
        .optional()
        .describe("Host CLI to probe. Defaults to opencode."),
      bin: z
        .string()
        .optional()
        .describe("Explicit CLI binary path. Defaults to MYTHIFY_KIMI_BIN, MYTHIFY_OPENCODE_BIN, or MYTHIFY_ANTIGRAVITY_BIN, then PATH and common install paths."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("Timeout per version or help command in seconds. Defaults to 10."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable probes."),
    },
  },
  guarded(({ host, bin, timeout_seconds, format }) => {
    const result = probeHostCli({
      host: host || "opencode",
      bin: bin || "",
      timeout_seconds,
    });
    const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
    return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatHostCliProbe(result);
  })
);

// ---------------------------------------------------------------------------
// Host CLI worker run tool
// ---------------------------------------------------------------------------

server.registerTool(
  "host_cli_run",
  {
    title: "Run a bounded host CLI worker",
    description:
      "Run a bounded non-interactive prompt through Kimi Code or OpenCode. " +
      "Use this only for worker material that the orchestrator will inspect and then verify with commands. The result is material, not verification evidence, and the tool writes no Mythify state.",
    inputSchema: {
      host: z
        .enum(["kimi-code", "opencode"])
        .optional()
        .describe("Host CLI worker to run. Defaults to opencode."),
      bin: z
        .string()
        .optional()
        .describe("Explicit CLI binary path. Defaults to MYTHIFY_KIMI_BIN or MYTHIFY_OPENCODE_BIN, then PATH and common install paths."),
      prompt: z
        .string()
        .describe("Prompt to pass to the host CLI non-interactive runner."),
      cwd: z
        .string()
        .optional()
        .describe("Working directory for the worker. Defaults to the project root inferred from MYTHIFY_DIR."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("Prompt run timeout in seconds. Defaults to 120."),
      model: z
        .string()
        .optional()
        .describe("Optional OpenCode model id. Kimi Code does not receive a model flag in this adapter."),
      agent: z
        .string()
        .optional()
        .describe("Optional OpenCode agent id. Kimi Code does not receive an agent flag in this adapter."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable host CLI runs."),
    },
  },
  guarded(({ host, bin, prompt, cwd, timeout_seconds, model, agent, format }) => {
    const result = runHostCliWorker({
      host: host || "opencode",
      bin: bin || "",
      prompt,
      cwd,
      timeout_seconds,
      model,
      agent,
    });
    const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
    return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatHostCliRun(result);
  })
);

// ---------------------------------------------------------------------------
// Execution adapter probe tool
// ---------------------------------------------------------------------------

server.registerTool(
  "execution_probe",
  {
    title: "Probe an execution adapter",
    description:
      "Probe Google Colab CLI availability by running only version and help commands. " +
      "Use this before planning remote execution work. The result is material, not verification evidence, and does not provision runtimes, request accelerators, upload data, execute jobs, or retrieve artifacts.",
    inputSchema: {
      adapter: z
        .enum(["google-colab-cli"])
        .optional()
        .describe("Execution adapter to probe. Defaults to google-colab-cli."),
      bin: z
        .string()
        .optional()
        .describe("Explicit CLI binary path. Defaults to MYTHIFY_COLAB_BIN, then PATH and common install paths."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("Timeout per version or help command in seconds. Defaults to 10."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable probes."),
    },
  },
  guarded(({ adapter, bin, timeout_seconds, format }) => {
    const result = probeExecutionAdapter({
      adapter: adapter || "google-colab-cli",
      bin: bin || "",
      timeout_seconds,
    });
    const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
    return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatExecutionProbe(result);
  })
);

// ---------------------------------------------------------------------------
// Lifecycle adapter probe tool
// ---------------------------------------------------------------------------

server.registerTool(
  "lifecycle_probe",
  {
    title: "Probe an agent lifecycle adapter",
    description:
      "Probe Google Agents CLI or ADK CLI availability by running only version, help, and eval-help commands. " +
      "Use this before planning agent lifecycle work. The result is material, not verification evidence, and does not scaffold projects, run agents, execute evals, deploy, publish, mutate cloud resources, or write project state.",
    inputSchema: {
      adapter: z
        .enum(["google-agents-cli", "google-adk-cli"])
        .optional()
        .describe("Lifecycle adapter to probe. Defaults to google-agents-cli."),
      bin: z
        .string()
        .optional()
        .describe("Explicit CLI binary path. Defaults to MYTHIFY_AGENTS_CLI_BIN or MYTHIFY_ADK_BIN, then PATH and common install paths."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("Timeout per version, help, or eval-help command in seconds. Defaults to 10."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable probes."),
    },
  },
  guarded(({ adapter, bin, timeout_seconds, format }) => {
    const result = probeLifecycleAdapter({
      adapter: adapter || "google-agents-cli",
      bin: bin || "",
      timeout_seconds,
    });
    const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
    return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatLifecycleProbe(result);
  })
);

// ---------------------------------------------------------------------------
// Memory tools
// ---------------------------------------------------------------------------

server.registerTool(
  "memory_store",
  {
    title: "Store a memory entry",
    description:
      "Store or update a key-value memory entry in the project's persistent .mythify state. " +
      "Keys are unique: storing an existing key overwrites it. " +
      "Use this to persist facts, decisions, discoveries, or task state that must survive beyond the current context window, especially on long or multi-session tasks.",
    inputSchema: {
      key: z.string().describe("Unique key for the entry; storing an existing key overwrites it."),
      value: z.string().describe("The content to remember."),
      category: z
        .enum(MEMORY_CATEGORIES)
        .default(MEMORY_DEFAULT_CATEGORY)
        .describe("Entry category: fact, decision, discovery, or state. Defaults to fact."),
    },
  },
  guarded(({ key, value, category }) => {
    const data = loadMemory();
    const existing = data.entries.find((e) => e.key === key);
    const entry = { key, value, category, timestamp: isoNow() };
    if (existing) {
      const idx = data.entries.indexOf(existing);
      data.entries[idx] = entry;
    } else {
      data.entries.push(entry);
    }
    saveMemory(data);
    const verb = existing ? "Updated" : "Stored";
    return `[OK] ${verb} memory entry "${key}" (category: ${category}). Total entries: ${data.entries.length}.`;
  })
);

server.registerTool(
  "memory_recall",
  {
    title: "Recall memory entries",
    description:
      "Search the project's persistent memory with a case-insensitive substring match over keys and values, optionally filtered by category. " +
      "Use this at session start and before making decisions, to recover facts, decisions, discoveries, and task state recorded earlier.",
    inputSchema: {
      query: z
        .string()
        .optional()
        .describe("Case-insensitive substring matched against keys and values. Omit to list every entry."),
      category: z
        .enum([...MEMORY_CATEGORIES, "all"])
        .optional()
        .describe("Restrict results to one category, or 'all' for no filter."),
    },
  },
  guarded(({ query, category }) => {
    const data = loadMemory();
    if (data.entries.length === 0) {
      return "[OK] No memory entries yet.";
    }
    const q = (query || "").toLowerCase();
    const matches = data.entries.filter((e) => {
      if (category && category !== "all" && e.category !== category) {
        return false;
      }
      if (q === "") {
        return true;
      }
      return (
        String(e.key).toLowerCase().includes(q) ||
        String(e.value).toLowerCase().includes(q)
      );
    });
    if (matches.length === 0) {
      return "[OK] No memory entries match the given query.";
    }
    const lines = matches.map((e) => `- [${e.category}] ${e.key}: ${e.value}`);
    return `[OK] ${matches.length} memory ${matches.length === 1 ? "entry" : "entries"}:\n${lines.join("\n")}`;
  })
);

server.registerTool(
  "memory_clear",
  {
    title: "Clear memory entries",
    description:
      "Remove one memory entry by key, or wipe all entries when confirm_clear_all is true. " +
      "Use this to retire stale or incorrect memories. Calling it with no key and without confirm_clear_all is refused as a safety guard.",
    inputSchema: {
      key: z.string().optional().describe("Key of the single entry to remove."),
      confirm_clear_all: z
        .boolean()
        .optional()
        .describe("Set true to confirm clearing every memory entry. Ignored when key is given."),
    },
  },
  guarded(({ key, confirm_clear_all }) => {
    const data = loadMemory();
    if (key !== undefined && key !== null && key !== "") {
      const idx = data.entries.findIndex((e) => e.key === key);
      if (idx === -1) {
        return `[FAIL] No memory entry with key "${key}". Nothing was cleared.`;
      }
      data.entries.splice(idx, 1);
      saveMemory(data);
      return `[OK] Removed memory entry "${key}". Remaining entries: ${data.entries.length}.`;
    }
    if (confirm_clear_all === true) {
      const count = data.entries.length;
      data.entries = [];
      saveMemory(data);
      return `[OK] Cleared all memory entries (${count} removed).`;
    }
    return MEMORY_CLEAR_MCP_REFUSAL;
  })
);

// ---------------------------------------------------------------------------
// Lesson tools
// ---------------------------------------------------------------------------

server.registerTool(
  "lesson_record",
  {
    title: "Record a lesson",
    description:
      "Record a durable lesson learned, either in the project store or the cross-project global store. " +
      "Use this after a surprising failure, a non-obvious fix, or a reusable insight, so the lesson survives beyond this session and this project.",
    inputSchema: {
      title: z.string().describe("Short lesson title; it becomes the basis of the lesson filename."),
      detail: z.string().describe("Full lesson detail: what happened, why, and what to do next time."),
      tags: z.array(z.string()).optional().describe("Optional tags for later filtering."),
      scope: z
        .enum(["project", "global"])
        .default("project")
        .describe("project stores under the workspace .mythify; global stores under ~/.mythify for every project."),
    },
  },
  guarded(({ title, detail, tags, scope }) => {
    const fileName = recordLesson(title, detail, tags || [], scope);
    return `[OK] Recorded ${scope} lesson "${title}" (${fileName}).`;
  })
);

server.registerTool(
  "lesson_recall",
  {
    title: "Recall recorded lessons",
    description:
      "List recorded lessons from the project store, the global store, or both, optionally filtered by tag. " +
      "Use this at session start and before architectural or risky decisions, to apply lessons learned from earlier work.",
    inputSchema: {
      tag: z.string().optional().describe("Only return lessons carrying this exact tag."),
      scope: z
        .enum(["project", "global", "all"])
        .default("all")
        .describe("Which store to read: project, global, or all. Defaults to all."),
    },
  },
  guarded(({ tag, scope }) => {
    let lessons = [];
    if (scope === "project" || scope === "all") {
      lessons = lessons.concat(readLessonsFrom(projectLessonsDir(), "project"));
    }
    if (scope === "global" || scope === "all") {
      lessons = lessons.concat(readLessonsFrom(globalLessonsDir(), "global"));
    }
    if (lessons.length === 0) {
      return "[OK] No lessons recorded yet.";
    }
    if (tag) {
      lessons = lessons.filter(({ lesson }) => Array.isArray(lesson.tags) && lesson.tags.includes(tag));
      if (lessons.length === 0) {
        return `[OK] No lessons carry the tag "${tag}".`;
      }
    }
    const lines = lessons.map(({ scope: s, lesson }) => {
      let line = `- (${s}) ${lesson.title}: ${lesson.detail}`;
      if (Array.isArray(lesson.tags) && lesson.tags.length > 0) {
        line += ` [tags: ${lesson.tags.join(", ")}]`;
      }
      return line;
    });
    return `[OK] ${lessons.length} ${lessons.length === 1 ? "lesson" : "lessons"}:\n${lines.join("\n")}`;
  })
);

// ---------------------------------------------------------------------------
// Plan tools
// ---------------------------------------------------------------------------

server.registerTool(
  "plan_create",
  {
    title: "Create a plan",
    description:
      "Create a new plan with a goal and optional initial steps, and set it as the active plan. " +
      "Use this at the start of any multi-step task so progress is tracked outside the context window; trivial single-edit tasks do not need a plan.",
    inputSchema: {
      goal: z.string().describe("What the plan accomplishes; shown in plan_status."),
      name: z.string().optional().describe("Optional plan name; slugified for the filename. Defaults to a slug of the goal."),
      steps: z
        .array(
          z.object({
            title: z.string().describe("Step title."),
            success_criteria: z.string().optional().describe("How to tell the step is done. Defaults to empty."),
          })
        )
        .optional()
        .describe("Initial steps; ids are auto-assigned starting at 1."),
    },
  },
  guarded(({ goal, name, steps }) => {
    const base =
      slugify(name !== undefined && name !== null && String(name).trim() !== "" ? name : goal) || "plan";
    const slug = uniquePlanSlug(base);
    const now = isoNow();
    const planSteps = (steps || []).map((s, i) => ({
      id: i + 1,
      title: s.title,
      success_criteria: s.success_criteria || "",
      status: "pending",
      result: null,
    }));
    const plan = {
      name: slug,
      goal,
      steps: planSteps,
      created: now,
      last_updated: now,
    };
    writeJsonAtomic(planPath(slug), plan);
    setActiveSlug(slug);
    const lines = [
      `[OK] Created plan "${slug}" with ${planSteps.length} ${planSteps.length === 1 ? "step" : "steps"}; it is now the active plan.`,
      `Goal: ${goal}`,
    ];
    if (planSteps.length === 0) {
      lines.push("The plan has no steps yet; add them with plan_add_step.");
    } else {
      for (const step of planSteps) {
        lines.push(stepLine(step));
      }
    }
    return lines.join("\n");
  })
);

server.registerTool(
  "plan_add_step",
  {
    title: "Add a step to a plan",
    description:
      "Append a step to the named plan, or to the active plan when no name is given. The step id is assigned automatically. " +
      "Use this when new work is discovered mid-task or when fleshing out a plan created without steps.",
    inputSchema: {
      title: z.string().describe("Step title."),
      success_criteria: z.string().optional().describe("How to tell the step is done. Defaults to empty."),
      plan: z.string().optional().describe("Plan name; omit to use the active plan."),
    },
  },
  guarded(({ title, success_criteria, plan: planName }) => {
    const resolved = resolvePlan(planName);
    if (resolved.error) {
      return resolved.error;
    }
    const { slug, plan } = resolved;
    if (!Array.isArray(plan.steps)) {
      plan.steps = [];
    }
    const maxId = plan.steps.reduce((m, s) => (typeof s.id === "number" && s.id > m ? s.id : m), 0);
    const step = {
      id: maxId + 1,
      title,
      success_criteria: success_criteria || "",
      status: "pending",
      result: null,
    };
    plan.steps.push(step);
    savePlan(slug, plan);
    return `[OK] Added step ${step.id} to plan "${slug}": ${title}`;
  })
);

server.registerTool(
  "plan_update_step",
  {
    title: "Update a plan step's status",
    description:
      "Set a step's status (pending, in_progress, completed, failed, skipped) on the named or active plan. " +
      "Marking a step completed or failed REQUIRES a result describing the evidence; without it the plan is left unmodified. " +
      "Use this as you start, finish, fail, or skip each step of the active plan.",
    inputSchema: {
      step_id: z.number().int().describe("The 1-based id of the step to update."),
      status: z
        .enum(STEP_STATUSES)
        .describe("New status: pending, in_progress, completed, failed, or skipped."),
      result: z
        .string()
        .optional()
        .describe("Evidence or outcome description. Required for completed and failed."),
      plan: z.string().optional().describe("Plan name; omit to use the active plan."),
    },
  },
  guarded(({ step_id, status, result, plan: planName }) => {
    const needsEvidence = status === "completed" || status === "failed";
    const hasResult = typeof result === "string" && result.trim() !== "";
    if (needsEvidence && !hasResult) {
      return (
        "[FAIL] Evidence required: pass a RESULT describing what proves this status. " +
        "The plan was not modified."
      );
    }
    const resolved = resolvePlan(planName);
    if (resolved.error) {
      return resolved.error;
    }
    const { slug, plan } = resolved;
    const step = (plan.steps || []).find((s) => s.id === step_id);
    if (!step) {
      return `[FAIL] No step with id ${step_id} in plan "${slug}".`;
    }
    if (status === "completed" && process.env.MYTHIFY_REQUIRE_VERIFIED_STEP === "1") {
      const lowerBound =
        typeof step.updated_at === "string" && step.updated_at !== ""
          ? step.updated_at
          : plan.created;
      const verifications = readJsonl(verificationsPath());
      const hasPassingRun = verifications.some(
        (record) =>
          record &&
          record.kind === "executed" &&
          record.verified === true &&
          typeof record.timestamp === "string" &&
          record.timestamp >= lowerBound &&
          verificationRecordMatchesStep(record, slug, step_id)
      );
      if (!hasPassingRun) {
        return (
          "[FAIL] Verified evidence required: MYTHIFY_REQUIRE_VERIFIED_STEP=1 but no passing 'verify run' " +
          "was recorded since this step started. Run 'verify run' with a passing check first."
        );
      }
    }
    step.status = status;
    if (hasResult) {
      step.result = result;
    }
    step.updated_at = isoNow();
    savePlan(slug, plan);
    return [
      `[OK] Step ${step_id} of plan "${slug}" is now ${status}: ${step.title}`,
      nextPendingText(plan),
    ].join("\n");
  })
);

server.registerTool(
  "plan_status",
  {
    title: "Show plan status",
    description:
      "Show the named or active plan: its goal, progress count, and every step with a status icon, criteria, and result. " +
      "Use this to orient at session start, after each step update, and before deciding what to do next.",
    inputSchema: {
      plan: z.string().optional().describe("Plan name; omit to use the active plan."),
    },
  },
  guarded(({ plan: planName }) => {
    if (
      (planName === undefined || planName === null || String(planName).trim() === "") &&
      !readActiveSlug()
    ) {
      return "[OK] No active plan yet. Create one with plan_create.";
    }
    const resolved = resolvePlan(planName);
    if (resolved.error) {
      return resolved.error;
    }
    const { slug, plan } = resolved;
    const steps = Array.isArray(plan.steps) ? plan.steps : [];
    const done = steps.filter((s) => s.status === "completed").length;
    const lines = [
      `[OK] Plan "${slug}": ${plan.goal}`,
      `Progress: ${done}/${steps.length} steps completed.`,
    ];
    if (steps.length === 0) {
      lines.push("No steps yet; add them with plan_add_step.");
    } else {
      for (const step of steps) {
        lines.push(stepLine(step));
      }
      lines.push(nextPendingText(plan));
    }
    return lines.join("\n");
  })
);

// ---------------------------------------------------------------------------
// Outcome loop tools
// ---------------------------------------------------------------------------

server.registerTool(
  "outcome_start",
  {
    title: "Start an outcome loop",
    description:
      "Start a supervised outcome loop: define the desired outcome, the success criteria, the verifier command, and the iteration budget. " +
      "The host agent performs bounded attempts between outcome_check calls; Mythify records evidence and decides whether to retry, stop, or report success.",
    inputSchema: {
      goal: z.string().describe("Outcome goal."),
      success: z.string().describe("Human-readable success criteria."),
      verify_command: z.string().describe("Shell command that verifies the outcome."),
      metric_command: z.string().optional().describe("Optional shell command that emits a metric."),
      max_iterations: z
        .number()
        .int()
        .positive()
        .default(3)
        .describe("Maximum verifier iterations before the outcome fails."),
      allowed_paths: z
        .array(z.string())
        .optional()
        .describe("Optional path scope for host edits; recorded for policy."),
      visibility: z
        .enum(FANOUT_VISIBILITY_MODES)
        .optional()
        .describe("How much loop progress the host should surface: auto, quiet, summary, verbose, or threaded."),
      name: z.string().optional().describe("Outcome name; defaults to a slug of the goal."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ goal, success, verify_command, metric_command, max_iterations, allowed_paths, visibility, name, format }) => {
    const base = slugify(name || goal) || "outcome";
    const slug = uniqueOutcomeSlug(base);
    const now = isoNow();
    const record = {
      id: slug,
      goal,
      success_criteria: success,
      verify_command,
      metric_command: metric_command || "",
      max_iterations: max_iterations || 3,
      iteration_count: 0,
      allowed_paths: Array.isArray(allowed_paths) ? allowed_paths : [],
      visibility: visibility || "summary",
      status: "active",
      created: now,
      updated: now,
      last_verified: null,
      best_metric_score: null,
      stop_reason: null,
    };
    saveOutcome(slug, record);
    setActiveOutcomeSlug(slug);
    if (format === "json") {
      return `[OK] ${JSON.stringify(record, null, 2)}`;
    }
    return [
      `[OK] Outcome started: ${slug}`,
      `goal: ${goal}`,
      `success: ${success}`,
      `verify: ${verify_command}`,
      metric_command ? `metric: ${metric_command}` : null,
      `iterations: 0/${record.max_iterations}`,
      "next: make a bounded attempt, then call outcome_check.",
    ].filter(Boolean).join("\n");
  })
);

server.registerTool(
  "outcome_check",
  {
    title: "Run an outcome verifier iteration",
    description:
      "Run the verifier and optional metric for the active or named outcome, record the iteration, and return whether the host should retry, stop, or report success.",
    inputSchema: {
      name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
      notes: z.string().optional().describe("Notes for this iteration."),
      timeout_seconds: z
        .number()
        .positive()
        .default(300)
        .describe("Kill each command after this many seconds. Defaults to 300."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ name, notes, timeout_seconds, format }) => {
    if (process.env.MYTHIFY_DISABLE_RUN === "1") {
      return (
        "[FAIL] outcome_check is disabled: the server environment sets MYTHIFY_DISABLE_RUN=1. " +
        "No command was executed and nothing was recorded."
      );
    }
    const resolved = resolveOutcome(name);
    if (resolved.error) {
      return resolved.error;
    }
    const { slug, goal } = resolved;
    if (["succeeded", "failed", "stopped"].includes(goal.status)) {
      if (format === "json") {
        return `[OK] ${JSON.stringify({ goal, record: null }, null, 2)}`;
      }
      return `[OK] Outcome ${slug} is already ${goal.status}.`;
    }
    const iterationCount = Number.parseInt(goal.iteration_count || 0, 10);
    const maxIterations = Number.parseInt(goal.max_iterations || 1, 10);
    if (iterationCount >= maxIterations) {
      goal.status = "failed";
      goal.stop_reason = "iteration budget exhausted before check";
      saveOutcome(slug, goal);
      if (format === "json") {
        return `[FAIL] ${JSON.stringify({ goal, record: null }, null, 2)}`;
      }
      return `[FAIL] Outcome ${slug} failed: iteration budget exhausted.`;
    }
    const timeout = timeout_seconds || 300;
    const verify = runShellCapture(goal.verify_command, timeout);
    let metricRecord = null;
    let metricOk = true;
    let metricScore = null;
    if (goal.metric_command) {
      const metric = runShellCapture(goal.metric_command, timeout);
      metricOk = metric.verified;
      metricScore = parseMetricScore(metric.stdout_tail);
      metricRecord = {
        command: metric.command,
        exit_code: metric.exit_code,
        duration_seconds: metric.duration_seconds,
        stdout_tail: metric.stdout_tail,
        stderr_tail: metric.stderr_tail,
        verified: metric.verified,
        score: metricScore,
      };
    }
    const verified = Boolean(verify.verified && metricOk);
    const nextIteration = iterationCount + 1;
    let statusAfter;
    let nextAction;
    if (verified) {
      statusAfter = "succeeded";
      nextAction = "Outcome met. Report the evidence and stop.";
    } else if (nextIteration >= maxIterations) {
      statusAfter = "failed";
      nextAction = "Iteration budget exhausted. Summarize the blocker and stop.";
    } else {
      statusAfter = "active";
      nextAction = "Outcome not met. Inspect verifier output, make another bounded attempt, then call outcome_check again.";
    }
    const record = {
      iteration: nextIteration,
      timestamp: isoNow(),
      notes: notes || "",
      verify: {
        command: verify.command,
        exit_code: verify.exit_code,
        duration_seconds: verify.duration_seconds,
        stdout_tail: verify.stdout_tail,
        stderr_tail: verify.stderr_tail,
        verified: verify.verified,
      },
      metric: metricRecord,
      verified,
      status_after: statusAfter,
      next_action: nextAction,
    };
    appendJsonl(outcomeIterationsPath(slug), record);
    goal.iteration_count = nextIteration;
    goal.status = statusAfter;
    goal.last_verified = verified;
    if (metricScore !== null) {
      const best = goal.best_metric_score;
      if (best === null || best === undefined || metricScore > best) {
        goal.best_metric_score = metricScore;
      }
    }
    if (statusAfter === "failed") {
      goal.stop_reason = "iteration budget exhausted";
    }
    if (statusAfter === "succeeded") {
      goal.stop_reason = "success criteria verified";
    }
    saveOutcome(slug, goal);
    appendJsonl(verificationsPath(), {
      kind: "executed",
      claim: `Outcome ${slug}: ${goal.success_criteria || ""}`,
      command: goal.verify_command,
      exit_code: verify.exit_code,
      duration_seconds: verify.duration_seconds,
      stdout_tail: verify.stdout_tail,
      stderr_tail: verify.stderr_tail,
      verified: verify.verified,
      timestamp: record.timestamp,
      outcome: slug,
      iteration: nextIteration,
      ...verificationStepContext(),
    });
    if (format === "json") {
      const prefix = verified ? "[OK]" : "[FAIL]";
      return `${prefix} ${JSON.stringify({ goal, record }, null, 2)}`;
    }
    const prefix = verified ? "[OK]" : "[FAIL]";
    const lines = [
      `${prefix} Outcome ${slug} iteration ${nextIteration}/${maxIterations}: ${statusAfter}`,
      `verify exit: ${verify.exit_code}`,
    ];
    if (metricRecord) {
      lines.push(`metric exit: ${metricRecord.exit_code}`);
      if (metricScore !== null) {
        lines.push(`metric score: ${metricScore}`);
      }
    }
    lines.push(`next: ${nextAction}`);
    if (verify.stdout_tail) {
      lines.push("--- verify stdout (tail) ---");
      lines.push(verify.stdout_tail);
    }
    if (verify.stderr_tail) {
      lines.push("--- verify stderr (tail) ---");
      lines.push(verify.stderr_tail);
    }
    return lines.join("\n");
  })
);

server.registerTool(
  "outcome_status",
  {
    title: "Show outcome loop status",
    description:
      "Show the active or named outcome loop: status, verifier, iteration budget, and next action.",
    inputSchema: {
      name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ name, format }) => {
    const resolved = resolveOutcome(name);
    if (resolved.error) {
      return resolved.error;
    }
    const { slug, goal } = resolved;
    const iterations = readOutcomeIterations(slug);
    if (format === "json") {
      return `[OK] ${JSON.stringify({ goal, iterations }, null, 2)}`;
    }
    return formatOutcomeStatus(slug, goal, iterations);
  })
);

server.registerTool(
  "outcome_results",
  {
    title: "Show outcome loop results",
    description:
      "Show all verifier iterations for the active or named outcome, including verifier exits, metric exits, final status, and next action.",
    inputSchema: {
      name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ name, format }) => {
    const resolved = resolveOutcome(name);
    if (resolved.error) {
      return resolved.error;
    }
    const { slug, goal } = resolved;
    const iterations = readOutcomeIterations(slug);
    if (format === "json") {
      return `[OK] ${JSON.stringify({ goal, iterations }, null, 2)}`;
    }
    const lines = [formatOutcomeStatus(slug, goal, iterations)];
    for (const item of iterations) {
      lines.push("");
      lines.push(`iteration ${item.iteration}: verified=${item.verified}, status=${item.status_after}`);
      lines.push(`  verify exit: ${item.verify?.exit_code}`);
      if (item.metric) {
        lines.push(`  metric exit: ${item.metric.exit_code}`);
        if (item.metric.score !== null && item.metric.score !== undefined) {
          lines.push(`  metric score: ${item.metric.score}`);
        }
      }
    }
    return lines.join("\n");
  })
);

server.registerTool(
  "outcome_stop",
  {
    title: "Stop an outcome loop",
    description:
      "Mark the active or named outcome loop stopped and clear the active pointer when it matches.",
    inputSchema: {
      name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
      reason: z.string().describe("Why the outcome loop is being stopped."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ name, reason, format }) => {
    const resolved = resolveOutcome(name);
    if (resolved.error) {
      return resolved.error;
    }
    const { slug, goal } = resolved;
    goal.status = "stopped";
    goal.stop_reason = reason;
    saveOutcome(slug, goal);
    clearActiveOutcomeSlug(slug);
    if (format === "json") {
      return `[OK] ${JSON.stringify(goal, null, 2)}`;
    }
    return `[OK] Outcome ${slug} stopped: ${reason}`;
  })
);

// ---------------------------------------------------------------------------
// Verification tools
// ---------------------------------------------------------------------------

server.registerTool(
  "verify_run",
  {
    title: "Run a command as executed verification",
    description:
      "Execute a shell command, record the exit code, duration, and output tails as an executed verification, and return a VERIFIED or UNVERIFIED verdict. " +
      "Use this to ground every completion claim in machine-checked evidence (tests, builds, linters, curl, file checks). Executed verification always beats self-reported attestation.",
    inputSchema: {
      command: z.string().describe("Shell command to execute."),
      claim: z.string().optional().describe("The claim this command verifies; shown in the verdict."),
      timeout_seconds: z
        .number()
        .positive()
        .default(300)
        .describe("Kill the command after this many seconds. Defaults to 300."),
    },
  },
  guarded(({ command, claim, timeout_seconds }) => {
    if (process.env.MYTHIFY_DISABLE_RUN === "1") {
      return (
        "[FAIL] verify_run is disabled: the server environment sets MYTHIFY_DISABLE_RUN=1. " +
        "No command was executed and nothing was recorded. " +
        "Unset MYTHIFY_DISABLE_RUN to enable command execution, or use verify_claim to record a self-reported attestation."
      );
    }
    const timeoutSeconds = timeout_seconds || 300;
    const startedAt = process.hrtime.bigint();
    const run = spawnSync(command, {
      shell: true,
      encoding: "utf8",
      timeout: Math.round(timeoutSeconds * 1000),
      maxBuffer: 16 * 1024 * 1024,
    });
    const durationSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
    let stdoutTail = tail(run.stdout);
    let stderrTail = tail(run.stderr);
    const timedOut = Boolean(run.error && run.error.code === "ETIMEDOUT");
    let exitCode;
    let verified;
    if (timedOut) {
      exitCode = -1;
      verified = false;
      stderrTail = stderrTail + (stderrTail ? "\n" : "") + `(timed out after ${timeoutSeconds} seconds)`;
    } else if (typeof run.status === "number") {
      exitCode = run.status;
      verified = exitCode === 0;
    } else {
      exitCode = -1;
      verified = false;
      const reason = run.error
        ? run.error.message
        : run.signal
          ? `terminated by signal ${run.signal}`
          : "command did not produce an exit code";
      stderrTail = stderrTail + (stderrTail ? "\n" : "") + `(${reason})`;
    }
    const record = {
      kind: "executed",
      claim: claim !== undefined && claim !== null ? claim : null,
      command,
      exit_code: exitCode,
      duration_seconds: Number(durationSeconds.toFixed(3)),
      stdout_tail: stdoutTail,
      stderr_tail: stderrTail,
      verified,
      timestamp: isoNow(),
      ...verificationStepContext(),
    };
    appendJsonl(verificationsPath(), record);
    const label = record.claim !== null ? record.claim : command;
    const timing = `(exit ${exitCode}, ${durationSeconds.toFixed(2)}s)`;
    if (verified) {
      return `[OK] VERIFIED: ${label} ${timing}`;
    }
    const lines = [`[FAIL] UNVERIFIED: ${label} ${timing}`];
    if (stdoutTail !== "") {
      lines.push("--- stdout (tail) ---");
      lines.push(stdoutTail);
    }
    if (stderrTail !== "") {
      lines.push("--- stderr (tail) ---");
      lines.push(stderrTail);
    }
    return lines.join("\n");
  })
);

server.registerTool(
  "verify_claim",
  {
    title: "Record a self-reported attestation",
    description:
      "Record a claim with self-reported evidence as an attested verification entry. It is never marked verified. " +
      "Use this only when nothing executable exists to check the claim; whenever a command can check it, use verify_run instead.",
    inputSchema: {
      claim: z.string().describe("The claim being attested."),
      evidence: z.string().describe("The self-reported evidence supporting the claim."),
    },
  },
  guarded(({ claim, evidence }) => {
    const record = {
      kind: "attested",
      claim,
      evidence,
      verified: null,
      timestamp: isoNow(),
      ...verificationStepContext(),
    };
    appendJsonl(verificationsPath(), record);
    return `[WARN] ATTESTED: ${claim} (self-reported, not machine-checked; prefer verify run)`;
  })
);

// ---------------------------------------------------------------------------
// Reflection tool
// ---------------------------------------------------------------------------

server.registerTool(
  "reflect",
  {
    title: "Record a structured reflection",
    description:
      "Record a structured reflection: what was done, the outcome, what was observed, the root cause when known, and the next action. A provided lesson is auto-recorded as a project lesson tagged auto-reflected. " +
      "Use this after each significant action or failure, so course corrections are grounded in recorded observations rather than guesswork.",
    inputSchema: {
      action_taken: z.string().describe("What was just attempted."),
      outcome: z.enum(["success", "partial", "failure"]).describe("How it went: success, partial, or failure."),
      observation: z.string().describe("What was actually observed (output, behavior, evidence)."),
      root_cause: z.string().optional().describe("Root cause of a partial or failed outcome, when known."),
      next_action: z.string().describe("The concrete next action to take."),
      lesson: z.string().optional().describe("Optional reusable lesson; auto-recorded to the project lesson store."),
    },
  },
  guarded(({ action_taken, outcome, observation, root_cause, next_action, lesson }) => {
    const record = {
      action: action_taken,
      outcome,
      observation,
      root_cause: root_cause !== undefined && root_cause !== null ? root_cause : null,
      next: next_action,
      lesson: lesson !== undefined && lesson !== null ? lesson : null,
      timestamp: isoNow(),
    };
    appendJsonl(reflectionsPath(), record);
    const lines = [
      `[OK] Reflection recorded (outcome: ${outcome}).`,
      `Next action: ${next_action}`,
    ];
    if (record.lesson !== null && record.lesson.trim() !== "") {
      const detail = `Auto-recorded from a reflection (outcome: ${record.outcome}). Action: ${record.action}`;
      const fileName = recordLesson(record.lesson, detail, ["auto-reflected"], "project");
      lines.push(`Lesson auto-recorded as a project lesson tagged auto-reflected (${fileName}).`);
    }
    return lines.join("\n");
  })
);

// ---------------------------------------------------------------------------
// Fanout tools (parallel delegation; implementation in src/fanout.js)
// ---------------------------------------------------------------------------

registerFanoutTools(server, {
  resolveStateDir,
  writeTextAtomic,
  writeJsonAtomic,
  readJsonRecover,
  isoNow,
  stampNow,
  guarded,
});

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  process.stderr.write(`[FAIL] Mythify MCP server failed to start: ${err && err.message ? err.message : err}\n`);
  process.exit(1);
});
