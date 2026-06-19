#!/usr/bin/env node
// Mythify MCP server
// Exposes the Mythify state model (memory, plans, lessons, verifications,
// reflections) as 38 core MCP tools over stdio, plus the 3 fanout tools for
// parallel delegation (src/fanout.js), 41 tools in total. On-disk formats are
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
import { registerAdapterTools } from "./adapter-tools.js";
import { registerViewTools } from "./view-tools.js";
import { registerWorkflowTools } from "./workflow-tools.js";
import {
  buildBackgroundView,
  buildEvidenceHarnessView,
  buildFanoutTimelineView,
  buildOutcomeProgressView,
  buildPhaseView,
  buildReleaseReadinessView,
  buildVerificationHistoryView,
  buildWorkReport,
  buildWorkflowDashboard,
  compactReportDetail,
  configureViewCore,
  formatBackgroundView,
  formatEvidenceHarnessView,
  formatFanoutTimelineView,
  formatOutcomeProgressView,
  formatPhaseView,
  formatReleaseReadinessView,
  formatVerificationHistoryView,
  formatWorkReport,
  formatWorkflowDashboard,
  gitStatusSummary,
  verificationLabel,
} from "./view-core.js";
import { registerMemoryTools } from "./memory-tools.js";
import { registerOutcomeTools } from "./outcome-tools.js";
import { registerPlanTools } from "./plan-tools.js";
import { registerVerificationTools } from "./verification-tools.js";
import { registerFanoutTools } from "./fanout.js";

const PACKAGE_JSON = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const VERSION = PACKAGE_JSON.version;
const WORKFLOW_ROUTER_PATH = new URL("../protocol/workflow-router.json", import.meta.url);
const TAIL_CHARS = 4000;
const REDACTED_SECRET = "[REDACTED]";
const DEFAULT_VERIFY_MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
const JSONL_LOCK_TIMEOUT_MS = 10000;
const JSONL_LOCK_POLL_MS = 50;
const JSONL_TAIL_CHUNK_BYTES = 64 * 1024;
const FALSE_ENV_VALUES = new Set(["0", "false", "no", "off"]);
const CAMPAIGN_PHASES = ["understand", "design", "build", "judge", "verify", "reflect"];
const CAMPAIGN_PHASE_GUIDANCE = {
  understand: "Read context, restate the task, and identify constraints.",
  design: "Choose the smallest useful approach and success check.",
  build: "Make the focused change or artifact.",
  judge: "Review the result against the task and campaign goal.",
  verify: "Run the nearest executable check, or record why only attestation is possible.",
  reflect: "Capture what improved the next task, then advance the frontier.",
};
const CAMPAIGN_PROMPT_GUARDRAIL =
  "Prompt output is steering material for the host agent, not verification evidence. " +
  "The host must do the work, run checks when available, and advance the campaign with evidence.";
const PROMPT_PACKET_KINDS = ["research", "analysis", "failure", "handoff", "review", "campaign", "next"];
const PROMPT_PACKET_GUARDRAIL =
  "Prompt packet output is steering material for the host agent, not verification evidence. " +
  "The host must do the work, run checks when available, report issues in chat, and record evidence.";
const WORKFLOW_ROUTE_GUARDRAIL =
  "Workflow route output is steering material for the host agent, not verification evidence. " +
  "The host must do the work, run checks when available, report issues in chat, and record evidence.";
const ROUTE_FULL_SEND_TERMS = [
  "one shot", "one-shot", "one go", "in one go", "all in one go",
  "address all", "fix all", "do all", "do everything", "execute all",
  "continuous run", "keep going", "keep going until done", "until no issues remain",
  "yolo", "full send", "ship it", "run it through",
];
const ROUTE_PROMPT_TERMS = [
  "prompt packet", "reprompt", "inject the next task", "next prompt",
  "steer the chat", "steering prompt", "handoff packet",
];
const ROUTE_RESEARCH_TERMS = [
  "research", "look up", "latest", "find sources", "source-backed",
  "online", "internet", "web search",
];
const ROUTE_REVIEW_TERMS = [
  "audit", "review", "assess", "evaluate", "find issues", "code review",
  "risks", "risk sweep",
];
const ROUTE_RESUME_TERMS = [
  "continue", "resume", "next", "keep going", "pick up", "carry on",
  "what is next",
];
const ROUTE_OUTCOME_TERMS = [
  "until", "success criteria", "when tests pass", "when it passes",
  "verifier", "verify command", "outcome loop",
];
const ROUTE_VERIFY_TERMS = [
  "verify", "test", "tests", "passes", "passing", "check", "build",
  "lint",
];
const DEFAULT_REPORT_RECENT = 8;
const DEFAULT_REPORT_ATTENTION = 5;
const STEP_ICONS = {
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

function loadWorkflowRouter() {
  const manifest = JSON.parse(fs.readFileSync(WORKFLOW_ROUTER_PATH, "utf8"));
  const routes = manifest.routes || [];
  const seen = new Set();
  for (const entry of routes) {
    const routeId = String(entry?.id || "").trim();
    const promptPacket = String(entry?.prompt_packet || "").trim();
    if (!routeId || seen.has(routeId) || !promptPacket) {
      throw new Error("Invalid workflow router entry");
    }
    seen.add(routeId);
  }
  if (routes.length === 0) {
    throw new Error("Workflow router manifest is empty");
  }
  return manifest;
}

const WORKFLOW_ROUTER = loadWorkflowRouter();
const WORKFLOW_ROUTE_IDS = WORKFLOW_ROUTER.routes.map((route) => String(route.id));
const WORKFLOW_ROUTE_PROMPTS = Object.fromEntries(
  WORKFLOW_ROUTER.routes.map((route) => [String(route.id), String(route.prompt_packet || "next")])
);

// ---------------------------------------------------------------------------
// Time and string helpers
// ---------------------------------------------------------------------------

function isoNow() {
  return new Date().toISOString();
}

function parseIsoTimestamp(value) {
  const text = String(value || "").trim();
  if (text === "") {
    return null;
  }
  const millis = Date.parse(text);
  return Number.isNaN(millis) ? null : millis;
}

function timestampAtOrAfter(value, lowerBound, allowSameSecond = false) {
  const left = parseIsoTimestamp(value);
  const right = parseIsoTimestamp(lowerBound);
  if (left !== null && right !== null) {
    if (allowSameSecond) {
      return Math.floor(left / 1000) >= Math.floor(right / 1000);
    }
    return left >= right;
  }
  return String(value || "") >= String(lowerBound || "");
}

function timestampAfter(value, lowerBound) {
  const left = parseIsoTimestamp(value);
  const right = parseIsoTimestamp(lowerBound);
  if (left !== null && right !== null) {
    return left > right;
  }
  return String(value || "") > String(lowerBound || "");
}

function compareTimestampValues(leftValue, rightValue) {
  const left = parseIsoTimestamp(leftValue);
  const right = parseIsoTimestamp(rightValue);
  if (left !== null && right !== null && left !== right) {
    return left < right ? -1 : 1;
  }
  const leftText = String(leftValue || "");
  const rightText = String(rightValue || "");
  if (leftText < rightText) {
    return -1;
  }
  if (leftText > rightText) {
    return 1;
  }
  return 0;
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

function findExistingSlugByName(name, pathForSlug) {
  const candidate = slugify(name);
  if (candidate && fs.existsSync(pathForSlug(candidate))) {
    return candidate;
  }
  return null;
}

function tail(text) {
  const s = String(text == null ? "" : text);
  return s.length > TAIL_CHARS ? s.slice(-TAIL_CHARS) : s;
}

function redactSensitiveOutput(text) {
  let value = String(text == null ? "" : text);
  if (value === "") {
    return "";
  }
  value = value.replace(
    /\b(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/\-=]+)/gi,
    `$1${REDACTED_SECRET}`
  );
  value = value.replace(
    /\b([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)[A-Za-z0-9_-]*\s*=\s*)([^\s,;]+)/gi,
    `$1${REDACTED_SECRET}`
  );
  value = value.replace(
    /(["']?[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)[A-Za-z0-9_-]*["']?\s*:\s*)(["'])([^"']+)(["'])/gi,
    `$1$2${REDACTED_SECRET}$4`
  );
  value = value.replace(
    /\b((?:authorization|x-api-key|api-key|api_key|token|secret|password|passwd|credential)\s*:\s*)([^\s,;}]+)/gi,
    `$1${REDACTED_SECRET}`
  );
  value = value.replace(
    /\b(sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|npm_[A-Za-z0-9_-]{20,})\b/g,
    REDACTED_SECRET
  );
  return value;
}

function verifyMaxOutputBytes() {
  const raw = String(process.env.MYTHIFY_VERIFY_MAX_OUTPUT_BYTES || "").trim();
  if (raw === "") {
    return DEFAULT_VERIFY_MAX_OUTPUT_BYTES;
  }
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0
    ? parsed
    : DEFAULT_VERIFY_MAX_OUTPUT_BYTES;
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

const LOCK_WAIT = new Int32Array(new SharedArrayBuffer(4));

function sleepSync(ms) {
  Atomics.wait(LOCK_WAIT, 0, 0, ms);
}

function jsonlLockDir(filePath) {
  const digest = crypto
    .createHash("sha256")
    .update(path.resolve(filePath))
    .digest("hex")
    .slice(0, 16);
  return path.join(resolveStateDir(), "locks", `jsonl-${digest}.lock`);
}

function withJsonlFileLock(filePath, fn, timeoutMs = JSONL_LOCK_TIMEOUT_MS) {
  const lockDir = jsonlLockDir(filePath);
  ensureDir(path.dirname(lockDir));
  const deadline = Date.now() + timeoutMs;
  let acquired = false;
  while (!acquired) {
    try {
      fs.mkdirSync(lockDir);
      acquired = true;
    } catch (err) {
      if (!err || err.code !== "EEXIST") {
        throw err;
      }
      if (Date.now() >= deadline) {
        throw new Error(`Timed out waiting for JSONL lock: ${lockDir}`);
      }
      sleepSync(JSONL_LOCK_POLL_MS);
    }
  }
  try {
    return fn();
  } finally {
    try {
      fs.rmdirSync(lockDir);
    } catch {
      // Best effort cleanup. A later lock attempt will timeout if this remains.
    }
  }
}

// ---------------------------------------------------------------------------
// Durable JSON IO: atomic writes, corrupt-file recovery, never crash
// ---------------------------------------------------------------------------

function fsyncDirectoryBestEffort(dirPath) {
  let fd = null;
  try {
    fd = fs.openSync(dirPath, "r");
    fs.fsyncSync(fd);
  } catch {
    // Some platforms do not allow opening or fsyncing directories.
  } finally {
    if (fd !== null) {
      try {
        fs.closeSync(fd);
      } catch {
        // Best effort cleanup after a directory fsync attempt.
      }
    }
  }
}

function writeTextAtomic(filePath, text) {
  ensureDir(path.dirname(filePath));
  const tmp = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.tmp-${process.pid}-${crypto.randomBytes(4).toString("hex")}`
  );
  let fd = null;
  try {
    fd = fs.openSync(tmp, "w");
    fs.writeFileSync(fd, text, "utf8");
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = null;
    fs.renameSync(tmp, filePath);
    fsyncDirectoryBestEffort(path.dirname(filePath));
  } finally {
    if (fd !== null) {
      try {
        fs.closeSync(fd);
      } catch {
        // Best effort cleanup before removing the temp file.
      }
    }
    if (fs.existsSync(tmp)) {
      try {
        fs.unlinkSync(tmp);
      } catch {
        // Best effort temp cleanup. The next write uses a unique name.
      }
    }
  }
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

function appendJsonl(filePath, record) {
  ensureDir(path.dirname(filePath));
  withJsonlFileLock(filePath, () => {
    fs.appendFileSync(filePath, JSON.stringify(record) + "\n", "utf8");
  });
}

function readJsonl(filePath) {
  let raw;
  try {
    raw = fs.readFileSync(filePath, "utf8");
  } catch {
    return [];
  }
  const records = [];
  raw.split(/\r?\n/).forEach((line, index) => {
    const trimmed = line.trim();
    if (trimmed === "") {
      return;
    }
    try {
      records.push(JSON.parse(trimmed));
    } catch {
      process.stderr.write(
        `[WARN] Skipping malformed JSONL record in ${filePath} at line ${index + 1}.\n`
      );
    }
  });
  return records;
}

function parseJsonlLines(filePath, lines) {
  const records = [];
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (trimmed === "") {
      return;
    }
    try {
      records.push(JSON.parse(trimmed));
    } catch {
      process.stderr.write(`[WARN] Skipping malformed JSONL record in ${filePath} while reading tail.\n`);
    }
  });
  return records;
}

function readJsonlSince(filePath, lowerBound) {
  if (typeof lowerBound !== "string" || lowerBound === "") {
    return readJsonl(filePath);
  }
  let size;
  try {
    size = fs.statSync(filePath).size;
  } catch {
    return [];
  }
  let offset = size;
  let data = Buffer.alloc(0);
  const fd = fs.openSync(filePath, "r");
  try {
    while (offset > 0) {
      const readSize = Math.min(JSONL_TAIL_CHUNK_BYTES, offset);
      offset -= readSize;
      const chunk = Buffer.alloc(readSize);
      fs.readSync(fd, chunk, 0, readSize, offset);
      data = Buffer.concat([chunk, data]);
      let lines = data.toString("utf8").split(/\r?\n/);
      if (offset > 0 && lines.length > 0) {
        lines = lines.slice(1);
      }
      const records = parseJsonlLines(filePath, lines);
      if (
        records.some(
          (record) =>
            record.timestamp && !timestampAtOrAfter(String(record.timestamp), lowerBound, true)
        )
      ) {
        return records.filter((record) =>
          timestampAtOrAfter(String(record.timestamp || ""), lowerBound, true)
        );
      }
    }
  } finally {
    fs.closeSync(fd);
  }
  return parseJsonlLines(filePath, data.toString("utf8").split(/\r?\n/)).filter((record) =>
    timestampAtOrAfter(String(record.timestamp || ""), lowerBound, true)
  );
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

function listPlanSlugs() {
  try {
    return fs
      .readdirSync(plansDir())
      .filter((name) => name.endsWith(".json"))
      .map((name) => name.slice(0, -5))
      .sort();
  } catch {
    return [];
  }
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
    const slug = findExistingSlugByName(String(name).trim(), planPath);
    if (slug) {
      const plan = readJsonRecover(planPath(slug), () => null);
      if (plan === null) {
        return { error: `[FAIL] Plan file for "${slug}" was corrupt and has been quarantined. Recreate it with plan_create.` };
      }
      return { slug, plan };
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
  if (!record) {
    return false;
  }
  const hasPlanContext = Object.prototype.hasOwnProperty.call(record, "plan");
  const hasStepContext = Object.prototype.hasOwnProperty.call(record, "step_id");
  if (!hasPlanContext && !hasStepContext) {
    return true;
  }
  return record.plan === slug && record.step_id === stepId;
}

function verificationRecordHasExplicitStepContext(record, slug, stepId) {
  return (
    record &&
    Object.prototype.hasOwnProperty.call(record, "plan") &&
    Object.prototype.hasOwnProperty.call(record, "step_id") &&
    record.plan === slug &&
    record.step_id === stepId
  );
}

function strictStepEvidenceEnabled() {
  const raw = String(process.env.MYTHIFY_REQUIRE_VERIFIED_STEP || "").trim().toLowerCase();
  return !FALSE_ENV_VALUES.has(raw);
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
    const slug = findExistingSlugByName(String(name).trim(), outcomeGoalPath);
    if (slug) {
      const goal = readJsonRecover(outcomeGoalPath(slug), () => null);
      if (goal === null) {
        return { error: `[FAIL] Outcome file for "${slug}" was corrupt and has been quarantined.` };
      }
      return { slug, goal };
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
  const maxOutputBytes = verifyMaxOutputBytes();
  const startedAt = process.hrtime.bigint();
  const run = spawnSync(command, {
    shell: true,
    encoding: "utf8",
    timeout: Math.round(timeoutSeconds * 1000),
    maxBuffer: maxOutputBytes,
  });
  const durationSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
  let stdoutTail = redactSensitiveOutput(tail(run.stdout));
  let stderrTail = redactSensitiveOutput(tail(run.stderr));
  const timedOut = Boolean(run.error && run.error.code === "ETIMEDOUT");
  const outputLimitExceeded = Boolean(run.error && run.error.code === "ENOBUFS");
  let exitCode;
  let verified;
  if (timedOut) {
    exitCode = -1;
    verified = false;
    stderrTail = stderrTail + (stderrTail ? "\n" : "") + `(timed out after ${timeoutSeconds} seconds)`;
  } else if (outputLimitExceeded) {
    exitCode = -1;
    verified = false;
    stderrTail = stderrTail + (stderrTail ? "\n" : "") + `(output exceeded ${maxOutputBytes} bytes)`;
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
    output_limit_exceeded: outputLimitExceeded,
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
    lines.push(`allowed path hints (advisory): ${goal.allowed_paths.join(", ")}`);
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
  const result = { content: [{ type: "text", text }] };
  if (String(text).startsWith("[FAIL]")) {
    result.isError = true;
  }
  return result;
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

const MCP_FRONT_DOOR_NOTE =
  " For broad or ambiguous user prompts, call workflow_route first; use this tool directly only after workflow_route selects this workflow or the user explicitly asks for this primitive.";
// Workflow and classification tools
// ---------------------------------------------------------------------------

registerWorkflowTools(server, {
  guarded,
  resolveStateDir,
  readHostModelState,
  readJsonRecover,
  findExistingSlugByName,
  readActiveSlug,
  planPath,
  readActiveOutcomeSlug,
  outcomeGoalPath,
  verificationsPath,
  reflectionsPath,
  readJsonl,
  buildVerificationHistoryView,
  verificationLabel,
  gitStatusSummary,
  compactReportDetail,
  buildWorkReport,
});

// ---------------------------------------------------------------------------
// Adapter and host integration tools
// ---------------------------------------------------------------------------

registerAdapterTools(server, {
  guarded,
  isoNow,
  readHostModelState,
  clearHostModelState,
  writeHostModelState: (record) => writeJsonAtomic(hostModelPath(), record),
  resolveStateDir,
});

// ---------------------------------------------------------------------------
// Read-only view tools
// ---------------------------------------------------------------------------

configureViewCore({
  resolveStateDir,
  readActiveSlug,
  planPath,
  readJsonRecover,
  listPlanSlugs,
  loadMemory,
  readLessonsFrom,
  projectLessonsDir,
  globalLessonsDir,
  verificationsPath,
  reflectionsPath,
  readJsonl,
  readJsonlSince,
  writeJsonAtomic,
  isoNow,
  timestampAfter,
  compareTimestampValues,
  slugify,
  outcomesDir,
  readActiveOutcomeSlug,
  resolveOutcome,
  outcomeGoalPath,
  outcomeIterationsPath,
  readOutcomeIterations,
});

registerViewTools(server, {
  guarded,
  buildWorkflowDashboard,
  formatWorkflowDashboard,
  buildVerificationHistoryView,
  formatVerificationHistoryView,
  buildWorkReport,
  formatWorkReport,
  buildBackgroundView,
  formatBackgroundView,
  buildEvidenceHarnessView,
  formatEvidenceHarnessView,
  buildOutcomeProgressView,
  formatOutcomeProgressView,
  buildReleaseReadinessView,
  formatReleaseReadinessView,
  buildFanoutTimelineView,
  formatFanoutTimelineView,
  buildPhaseView,
  formatPhaseView,
});


// ---------------------------------------------------------------------------
// Memory and lesson tools
// ---------------------------------------------------------------------------

registerMemoryTools(server, {
  guarded,
  isoNow,
  loadMemory,
  saveMemory,
  recordLesson,
  readLessonsFrom,
  projectLessonsDir,
  globalLessonsDir,
});

// ---------------------------------------------------------------------------
// Plan tools
// ---------------------------------------------------------------------------

registerPlanTools(server, {
  guarded,
  slugify,
  uniquePlanSlug,
  isoNow,
  writeJsonAtomic,
  planPath,
  setActiveSlug,
  stepLine,
  resolvePlan,
  savePlan,
  strictStepEvidenceEnabled,
  readJsonlSince,
  verificationsPath,
  verificationRecordMatchesStep,
  timestampAtOrAfter,
  verificationRecordHasExplicitStepContext,
  nextPendingText,
  readActiveSlug,
  mcpFrontDoorNote: MCP_FRONT_DOOR_NOTE,
});

// ---------------------------------------------------------------------------
// Outcome loop tools
// ---------------------------------------------------------------------------

registerOutcomeTools(server, {
  guarded,
  slugify,
  uniqueOutcomeSlug,
  isoNow,
  saveOutcome,
  setActiveOutcomeSlug,
  resolveOutcome,
  readOutcomeIterations,
  formatOutcomeStatus,
  runShellCapture,
  parseMetricScore,
  appendJsonl,
  outcomeIterationsPath,
  verificationsPath,
  verificationStepContext,
  clearActiveOutcomeSlug,
  mcpFrontDoorNote: MCP_FRONT_DOOR_NOTE,
});

// ---------------------------------------------------------------------------
// Verification tools
// ---------------------------------------------------------------------------

registerVerificationTools(server, {
  guarded,
  runShellCapture,
  isoNow,
  verificationStepContext,
  appendJsonl,
  verificationsPath,
  reflectionsPath,
  recordLesson,
});

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
