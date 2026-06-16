#!/usr/bin/env node
// Mythify MCP server
// Exposes the Mythify state model (memory, plans, lessons, verifications,
// reflections) as 37 core MCP tools over stdio, plus the 3 fanout tools for
// parallel delegation (src/fanout.js), 40 tools in total. On-disk formats are
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
import {
  classifyTaskText,
  formatClassification,
} from "./classification.js";
import { registerAdapterTools } from "./adapter-tools.js";
import { registerViewTools } from "./view-tools.js";
import {
  buildModelPolicy,
  runModelTriage,
} from "./model-policy.js";
import { registerFanoutTools } from "./fanout.js";
import {
  EFFORT_LEVELS,
  FANOUT_VISIBILITY_MODES,
  HOST_PLATFORMS as PLATFORMS,
  REVIEWER_STRENGTH_MODES,
  SPAWN_CEILINGS,
  SPEED_LEVELS,
  TRIAGE_ENGINES,
  TRIAGE_MODES,
} from "./capability-registry.js";
import {
  MEMORY_CATEGORIES,
  MEMORY_CLEAR_MCP_REFUSAL,
  MEMORY_DEFAULT_CATEGORY,
} from "./operation-registry.js";

const PACKAGE_JSON = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const VERSION = PACKAGE_JSON.version;
const WORKFLOW_ROUTER_PATH = new URL("../protocol/workflow-router.json", import.meta.url);
const TAIL_CHARS = 4000;
const REDACTED_SECRET = "[REDACTED]";
const DEFAULT_VERIFY_MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
const JSONL_LOCK_TIMEOUT_MS = 10000;
const JSONL_LOCK_POLL_MS = 50;
const JSONL_TAIL_CHUNK_BYTES = 64 * 1024;
const STEP_STATUSES = ["pending", "in_progress", "completed", "failed", "skipped"];
const OUTCOME_STATUSES = ["active", "succeeded", "failed", "stopped"];
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

function campaignsDir() {
  return path.join(resolveStateDir(), "campaigns");
}

function campaignPath(slug) {
  return path.join(campaignsDir(), `${slug}.json`);
}

function activeCampaignPath() {
  return path.join(campaignsDir(), "active");
}

function getActiveCampaignSlug() {
  let value = "";
  try {
    value = fs.readFileSync(activeCampaignPath(), "utf8").trim();
  } catch {
    return null;
  }
  if (value && fs.existsSync(campaignPath(value))) {
    return value;
  }
  return null;
}

function findCampaignSlug(name) {
  const raw = String(name || "").trim();
  if (raw) {
    return findExistingSlugByName(raw, campaignPath);
  }
  return getActiveCampaignSlug();
}

function loadCampaign(name) {
  const slug = findCampaignSlug(name);
  if (!slug) {
    return [null, null];
  }
  const record = readJsonRecover(campaignPath(slug), () => null);
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return [slug, null];
  }
  return [slug, record];
}

function researchDir() {
  return path.join(resolveStateDir(), "research");
}

function researchPath(slug) {
  return path.join(researchDir(), `${slug}.json`);
}

function activeResearchPath() {
  return path.join(researchDir(), "active");
}

function getActiveResearchSlug() {
  let value = "";
  try {
    value = fs.readFileSync(activeResearchPath(), "utf8").trim();
  } catch {
    return null;
  }
  if (value && fs.existsSync(researchPath(value))) {
    return value;
  }
  return null;
}

function findResearchSlug(name) {
  const raw = String(name || "").trim();
  if (raw) {
    return findExistingSlugByName(raw, researchPath);
  }
  return getActiveResearchSlug();
}

function loadResearch(name) {
  const slug = findResearchSlug(name);
  if (!slug) {
    return [null, null];
  }
  const record = readJsonRecover(researchPath(slug), () => null);
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return [slug, null];
  }
  return [slug, record];
}

function currentCampaignTask(record) {
  const currentId = record?.current_task_id;
  for (const task of record?.tasks || []) {
    if (task?.id === currentId) {
      return task;
    }
  }
  return null;
}

function campaignProgress(record) {
  const tasks = Array.isArray(record?.tasks) ? record.tasks : [];
  const completed = tasks.filter((task) => task?.status === "completed").length;
  return [completed, tasks.length];
}

function campaignNextAction(record) {
  if (record?.status === "completed") {
    return "Campaign complete. Review lessons and final verification evidence.";
  }
  if (record?.status === "stopped") {
    return "Campaign stopped. Resume by creating a new campaign or updating the existing record manually.";
  }
  const task = currentCampaignTask(record);
  if (!task) {
    return "No current task. Add a task or complete the campaign.";
  }
  const phase = CAMPAIGN_PHASES.includes(task.phase) ? task.phase : CAMPAIGN_PHASES[0];
  return `Task ${task.id} ${phase}: ${CAMPAIGN_PHASE_GUIDANCE[phase] || "Continue the workflow."}`;
}

function campaignRecentLearningLines(record, limit = 5) {
  const learnings = Array.isArray(record?.learnings) ? record.learnings : [];
  return learnings
    .slice(-limit)
    .map((item) => {
      const lesson = String(item?.lesson || "").trim();
      if (!lesson) {
        return "";
      }
      const prefix = item?.task_id ? `task ${item.task_id}: ` : "";
      const suffix = item?.apply_next ? " [apply next]" : "";
      return `${prefix}${lesson}${suffix}`;
    })
    .filter(Boolean);
}

function buildCampaignPromptPayload(slug, record) {
  const [completed, total] = campaignProgress(record);
  const task = currentCampaignTask(record);
  const status = record?.status || "active";
  const verifyCommand = record?.verify_command || "";
  const learningLines = campaignRecentLearningLines(record);
  let phase = "";
  let phaseGuidance = "";
  let nextCommand = "";
  const lines = [
    `Continue Mythify campaign: ${slug}`,
    `Goal: ${record?.goal || ""}`,
    `Status: ${status}`,
    `Progress: ${completed}/${total} tasks completed`,
  ];
  if (record?.success_criteria) {
    lines.push(`Campaign success: ${record.success_criteria}`);
  }
  if (verifyCommand) {
    lines.push(`Campaign verifier: ${verifyCommand}`);
  }
  if (status === "completed") {
    lines.push("");
    lines.push("No current task remains. Review the final evidence, summarize risks, and archive related state when appropriate.");
  } else if (status === "stopped") {
    lines.push("");
    lines.push("This campaign is stopped. Do not continue it until the host or user explicitly resumes or creates a new campaign.");
  } else if (!task) {
    lines.push("");
    lines.push("No current task is selected. Add a task, set a task in progress, or close the campaign if it is complete.");
  } else {
    phase = CAMPAIGN_PHASES.includes(task.phase) ? task.phase : CAMPAIGN_PHASES[0];
    phaseGuidance = CAMPAIGN_PHASE_GUIDANCE[phase] || "Continue the workflow.";
    nextCommand = `mythify campaign advance ${slug} --result "<phase evidence>"`;
    lines.push("");
    lines.push(`Current task ${task.id}: ${task.title || ""}`);
    lines.push(`Task status: ${task.status || ""}`);
    lines.push(`Task criteria: ${task.success_criteria || "not specified"}`);
    lines.push(`Phase: ${phase}`);
    lines.push(`Phase guidance: ${phaseGuidance}`);
    if (learningLines.length > 0) {
      lines.push("");
      lines.push("Recent learnings:");
      for (const learning of learningLines) {
        lines.push(`- ${learning}`);
      }
    }
    lines.push("");
    lines.push("Instructions:");
    lines.push("- Work only on this current phase unless the host has already completed it.");
    lines.push("- Bring findings, failed checks, and uncertainty into the chat as they happen.");
    lines.push("- When this phase reaches verify, run the nearest executable check.");
    lines.push(`- When the phase is done, advance the durable frontier with: ${nextCommand}`);
  }
  lines.push("");
  lines.push(`Guardrail: ${CAMPAIGN_PROMPT_GUARDRAIL}`);
  return {
    id: slug,
    goal: record?.goal || "",
    status,
    progress: { completed, total },
    success_criteria: record?.success_criteria || "",
    verify_command: verifyCommand,
    current_task: task ? { ...task } : null,
    phase,
    phase_guidance: phaseGuidance,
    recent_learnings: learningLines,
    next_action: campaignNextAction(record),
    next_command: nextCommand,
    next_prompt: lines.join("\n"),
    guardrail: CAMPAIGN_PROMPT_GUARDRAIL,
  };
}

function formatCampaignPromptPayload(payload) {
  return `[OK] Campaign prompt: ${payload.id}\n${payload.next_prompt || ""}`;
}

// ---------------------------------------------------------------------------
// Prompt packets
// ---------------------------------------------------------------------------

function activePlanPacketContext() {
  const slug = readActiveSlug();
  if (!slug || !fs.existsSync(planPath(slug))) {
    return null;
  }
  const plan = readJsonRecover(planPath(slug), () => null);
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) {
    return null;
  }
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const completed = steps.filter((step) => step.status === "completed").length;
  return {
    slug,
    goal: plan.goal || "",
    progress: { completed, total: steps.length },
    current_step: steps.find((step) => step.status === "in_progress") || null,
    next_pending: steps.find((step) => step.status === "pending") || null,
    steps,
  };
}

function latestFailedVerification() {
  const records = readJsonl(verificationsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.kind === "executed" && record.verified === false) {
      return [index + 1, record];
    }
  }
  return [null, null];
}

function latestExecutedVerification() {
  const records = readJsonl(verificationsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.kind === "executed") {
      return [index + 1, record];
    }
  }
  return [null, null];
}

function latestFailureReflection() {
  const records = readJsonl(reflectionsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.outcome === "failure") {
      return [index + 1, record];
    }
  }
  return [null, null];
}

function promptRecentEvidence(limit = 5) {
  const rows = buildVerificationHistoryView(limit).records || [];
  return rows.map((row) => ({
    verdict: row.verdict,
    label: verificationLabel(row),
    exit_code: row.exit_code,
    timestamp: row.timestamp || "",
  }));
}

function promptPlanLines(planContext) {
  if (!planContext) {
    return ["Active plan: none"];
  }
  const lines = [
    `Active plan: ${planContext.slug}`,
    `Plan goal: ${planContext.goal || "not specified"}`,
    `Plan progress: ${planContext.progress.completed}/${planContext.progress.total} steps completed`,
  ];
  const current = planContext.current_step;
  const pending = planContext.next_pending;
  if (current) {
    lines.push(`Current step: ${current.id}. ${current.title || ""}`);
    if (current.success_criteria) {
      lines.push(`Current criteria: ${current.success_criteria}`);
    }
  } else if (pending) {
    lines.push(`Next pending step: ${pending.id}. ${pending.title || ""}`);
    if (pending.success_criteria) {
      lines.push(`Next criteria: ${pending.success_criteria}`);
    }
  } else {
    lines.push("Next pending step: none");
  }
  return lines;
}

function promptGitContext() {
  const gitState = gitStatusSummary(process.cwd());
  const lines = [
    `Git branch: ${gitState.branch || "unknown"}`,
    `Git status: ${gitState.status || "unknown"}`,
    `Git detail: ${gitState.detail || ""}`,
  ];
  for (const changedPath of gitState.changed_paths || []) {
    lines.push(`Changed path: ${changedPath}`);
  }
  return [gitState, lines];
}

function buildPromptPacket(kind, { name = "", goal = "", verifyCommand = "" } = {}) {
  if (kind === "next") {
    const selected = selectNextPromptPacketKind();
    const payload = buildPromptPacket(selected, { name, goal, verifyCommand });
    if (payload.error) {
      return payload;
    }
    return {
      ...payload,
      kind: "next",
      selected_kind: selected,
      title: "Next workflow prompt packet",
      next_prompt: `Selected next packet: ${selected}\n\n${payload.next_prompt || ""}`,
    };
  }
  if (kind === "campaign") {
    const [slug, record] = loadCampaign(name);
    if (!record) {
      return { error: "[FAIL] Campaign not found. Start one with: campaign start GOAL" };
    }
    const campaignPayload = buildCampaignPromptPayload(slug, record);
    return {
      kind: "campaign",
      selected_kind: "campaign",
      title: "Campaign prompt packet",
      source: { type: "campaign", id: slug },
      context: campaignPayload,
      next_prompt: campaignPayload.next_prompt || "",
      guardrail: PROMPT_PACKET_GUARDRAIL,
    };
  }
  if (kind === "research") {
    return buildResearchPromptPacket({ name, goal, verifyCommand });
  }
  if (kind === "analysis") {
    return buildAnalysisPromptPacket({ goal, verifyCommand });
  }
  if (kind === "failure") {
    return buildFailurePromptPacket({ verifyCommand });
  }
  if (kind === "handoff") {
    return buildHandoffPromptPacket({ goal, verifyCommand });
  }
  if (kind === "review") {
    return buildReviewPromptPacket({ goal, verifyCommand });
  }
  return { error: `[FAIL] Unknown prompt packet kind: ${kind}` };
}

function buildResearchPromptPacket({ name = "", goal = "", verifyCommand = "" } = {}) {
  const [slug, record] = loadResearch(name);
  if (!record) {
    return { error: "[FAIL] Research not found. Start one with: research start QUESTION" };
  }
  const sources = Array.isArray(record.sources) ? record.sources : [];
  const claims = Array.isArray(record.claims) ? record.claims : [];
  const questions = Array.isArray(record.open_questions) ? record.open_questions : [];
  const decision = record.decision || "";
  const lines = [
    `Research to implementation prompt packet: ${slug}`,
    `Question: ${record.question || ""}`,
    `Status: ${record.status || "active"}`,
    `Sources: ${sources.length}; claims: ${claims.length}; open questions: ${questions.length}`,
  ];
  if (goal) {
    lines.push(`Implementation goal: ${goal}`);
  }
  if (decision) {
    lines.push(`Decision: ${decision}`);
  }
  if (claims.length > 0) {
    lines.push("Key claims:");
    for (const claim of claims.slice(-5)) {
      const source = claim.source_id ? ` source=${claim.source_id}` : "";
      lines.push(`- ${claim.id}: ${claim.claim || ""}${source}`);
      lines.push(`  evidence: ${claim.evidence || ""}`);
    }
  }
  if (questions.length > 0) {
    lines.push("Open questions:");
    for (const item of questions.slice(-5)) {
      lines.push(`- ${item.id}: ${item.question || ""}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Treat this research as material for direction, not proof of completion.");
  lines.push("- If a decision exists, implement the smallest next step consistent with it.");
  lines.push("- If open questions block implementation, answer those first and update the research record.");
  lines.push("- Convert implementation work into a plan, campaign, or outcome loop before claiming done.");
  if (verifyCommand) {
    lines.push(`- Suggested verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "research",
    selected_kind: "research",
    title: "Research to implementation prompt packet",
    source: { type: "research", id: slug },
    context: {
      question: record.question || "",
      status: record.status || "active",
      decision,
      sources: sources.slice(-5),
      claims: claims.slice(-5),
      open_questions: questions.slice(-5),
      goal,
      verify_command: verifyCommand,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildAnalysisPromptPacket({ goal = "", verifyCommand = "" } = {}) {
  const planContext = activePlanPacketContext();
  const recent = promptRecentEvidence(3);
  const lines = [
    "Analysis prompt packet",
    `Goal: ${goal || planContext?.goal || "infer from current project context"}`,
  ];
  lines.push(...promptPlanLines(planContext));
  if (recent.length > 0) {
    lines.push("Recent evidence:");
    for (const item of recent) {
      const exitText = item.exit_code === undefined || item.exit_code === null ? "" : ` exit ${item.exit_code}`;
      lines.push(`- ${item.verdict}: ${item.label}${exitText}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Read the smallest useful project context before editing.");
  lines.push("- Identify likely files, constraints, hidden risks, and the first reversible step.");
  lines.push("- Produce or update a plan with checkable success criteria.");
  lines.push("- Do not implement until the first step and verifier are explicit.");
  if (verifyCommand) {
    lines.push(`- Candidate verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "analysis",
    selected_kind: "analysis",
    title: "Analysis prompt packet",
    source: { type: "workflow_state", id: planContext?.slug || null },
    context: {
      goal,
      active_plan: planContext,
      recent_evidence: recent,
      verify_command: verifyCommand,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildFailurePromptPacket({ verifyCommand = "" } = {}) {
  const [index, record] = latestFailedVerification();
  const [reflectionIndex, reflection] = latestFailureReflection();
  const context = {
    failed_verification_index: index,
    failed_verification: record,
    failure_reflection_index: reflectionIndex,
    failure_reflection: reflection,
    verify_command: verifyCommand,
  };
  const lines = ["Failure recovery prompt packet"];
  if (record) {
    lines.push(`Failed verification #${index}: ${record.claim || record.command || ""}`);
    lines.push(`Command: ${record.command || ""}`);
    lines.push(`Exit code: ${record.exit_code}`);
    const stdoutTail = String(record.stdout_tail || "").trim();
    const stderrTail = String(record.stderr_tail || "").trim();
    if (stdoutTail) {
      lines.push(`Stdout tail: ${compactReportDetail(stdoutTail)}`);
    }
    if (stderrTail) {
      lines.push(`Stderr tail: ${compactReportDetail(stderrTail)}`);
    }
  } else {
    lines.push("No failed executed verification was found.");
  }
  if (reflection) {
    lines.push(`Latest failure reflection: ${reflection.action || ""}`);
    if (reflection.root_cause) {
      lines.push(`Recorded root cause: ${reflection.root_cause}`);
    }
    if (reflection.next) {
      lines.push(`Recorded next action: ${reflection.next}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Reproduce or inspect the failure before changing code.");
  lines.push("- Fix the smallest likely root cause.");
  lines.push("- Rerun the failed verifier, or the provided verifier if it is more specific.");
  lines.push("- Report the failure, fix, and verification evidence in chat.");
  if (verifyCommand) {
    lines.push(`- Verifier to run: ${verifyCommand}`);
  } else if (record?.command) {
    lines.push(`- Verifier to rerun: ${record.command}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "failure",
    selected_kind: "failure",
    title: "Failure recovery prompt packet",
    source: { type: "verification", id: index },
    context,
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildHandoffPromptPacket({ goal = "", verifyCommand = "" } = {}) {
  const planContext = activePlanPacketContext();
  const [campaignSlug, campaignRecord] = loadCampaign("");
  const [researchSlug, researchRecord] = loadResearch("");
  const report = buildWorkReport({
    since: "start",
    recent: 5,
    cursor: "handoff-prompt",
    peek: true,
    mark: false,
  });
  const lines = [
    "Handoff prompt packet",
    `Goal: ${goal || planContext?.goal || "continue current Mythify work"}`,
  ];
  lines.push(...promptPlanLines(planContext));
  if (campaignRecord) {
    lines.push(`Active campaign: ${campaignSlug}`);
    lines.push(`Campaign next action: ${campaignNextAction(campaignRecord)}`);
  }
  if (researchRecord) {
    lines.push(`Active research: ${researchSlug}`);
    lines.push(`Research question: ${researchRecord.question || ""}`);
  }
  if ((report.attention_events || []).length > 0) {
    lines.push("Attention items:");
    for (const event of (report.attention_events || []).slice(-5)) {
      lines.push(`- ${event.level}: ${event.summary}`);
    }
  }
  if ((report.events || []).length > 0) {
    lines.push("Recent events:");
    for (const event of (report.events || []).slice(-5)) {
      lines.push(`- ${event.summary}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Resume from this packet without assuming hidden chat context.");
  lines.push("- Re-read files before editing if the packet mentions uncertainty.");
  lines.push("- Continue the current step or campaign phase, then verify before claiming completion.");
  lines.push("- Surface any failed checks or warnings in chat.");
  if (verifyCommand) {
    lines.push(`- Suggested verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "handoff",
    selected_kind: "handoff",
    title: "Handoff prompt packet",
    source: { type: "workflow_state", id: planContext?.slug || null },
    context: {
      goal,
      active_plan: planContext,
      active_campaign: campaignRecord
        ? { id: campaignSlug, next_action: campaignNextAction(campaignRecord) }
        : null,
      active_research: researchRecord
        ? { id: researchSlug, question: researchRecord.question || "" }
        : null,
      recent_report: report,
      verify_command: verifyCommand,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildReviewPromptPacket({ goal = "", verifyCommand = "" } = {}) {
  const planContext = activePlanPacketContext();
  const [gitState, gitLines] = promptGitContext();
  const recent = promptRecentEvidence(5);
  const lines = [
    "Review prompt packet",
    `Goal: ${goal || "review current changes and risks"}`,
  ];
  lines.push(...gitLines);
  lines.push(...promptPlanLines(planContext));
  if (recent.length > 0) {
    lines.push("Recent evidence:");
    for (const item of recent) {
      const exitText = item.exit_code === undefined || item.exit_code === null ? "" : ` exit ${item.exit_code}`;
      lines.push(`- ${item.verdict}: ${item.label}${exitText}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Review changed files and relevant surrounding code.");
  lines.push("- Lead with actionable findings, with file and line references when possible.");
  lines.push("- Separate verified issues, warnings, open questions, and test gaps.");
  lines.push("- If fixes are requested, address findings one by one and verify the result.");
  if (verifyCommand) {
    lines.push(`- Suggested verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "review",
    selected_kind: "review",
    title: "Review prompt packet",
    source: { type: "git", id: gitState.branch || null },
    context: {
      goal,
      git: gitState,
      active_plan: planContext,
      recent_evidence: recent,
      verify_command: verifyCommand,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function selectNextPromptPacketKind() {
  const [, latest] = latestExecutedVerification();
  if (latest && latest.verified === false) {
    return "failure";
  }
  if (getActiveCampaignSlug()) {
    return "campaign";
  }
  if (getActiveResearchSlug()) {
    return "research";
  }
  if (readActiveSlug()) {
    return "handoff";
  }
  return "analysis";
}

function formatPromptPacket(payload) {
  const lines = [
    `[OK] Prompt packet ${payload.kind || "unknown"}: ${payload.selected_kind || payload.kind || "unknown"}`,
  ];
  if (payload.source) {
    lines.push(`Source: ${payload.source.type || ""} ${payload.source.id || ""}`);
  }
  lines.push("Next prompt:");
  lines.push(payload.next_prompt || "");
  lines.push(`Guardrail: ${payload.guardrail || PROMPT_PACKET_GUARDRAIL}`);
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Workflow router
// ---------------------------------------------------------------------------

function shellQuote(value) {
  const text = String(value || "task").trim() || "task";
  return `'${text.replaceAll("'", "'\"'\"'")}'`;
}

function workflowRouteState() {
  const activePlanSlug = readActiveSlug();
  let activePlan = null;
  if (activePlanSlug && fs.existsSync(planPath(activePlanSlug))) {
    activePlan = readJsonRecover(planPath(activePlanSlug), () => null);
  }
  const activeOutcomeSlug = readActiveOutcomeSlug();
  let activeOutcome = null;
  if (activeOutcomeSlug && fs.existsSync(outcomeGoalPath(activeOutcomeSlug))) {
    activeOutcome = readJsonRecover(outcomeGoalPath(activeOutcomeSlug), () => null);
  }
  const [activeCampaignSlug, activeCampaign] = loadCampaign();
  const [activeResearchSlug, activeResearch] = loadResearch();
  const [latestIndex, latest] = latestExecutedVerification();
  let latestView = null;
  if (latest) {
    latestView = {
      index: latestIndex,
      verified: latest.verified,
      claim: latest.claim || "",
      command: latest.command || "",
      exit_code: latest.exit_code,
      timestamp: latest.timestamp || "",
    };
  }
  let planView = null;
  if (activePlan) {
    const steps = Array.isArray(activePlan.steps) ? activePlan.steps : [];
    const completed = steps.filter((step) => step?.status === "completed").length;
    const pending = steps.find((step) => step?.status === "pending") || null;
    planView = {
      id: activePlanSlug,
      goal: activePlan.goal || "",
      progress: { completed, total: steps.length },
      next_pending: pending
        ? {
            id: pending.id,
            title: pending.title || "",
            success_criteria: pending.success_criteria || "",
          }
        : null,
    };
  }
  let outcomeView = null;
  if (activeOutcome) {
    outcomeView = {
      id: activeOutcomeSlug,
      goal: activeOutcome.goal || "",
      status: activeOutcome.status || "",
      iteration_count: activeOutcome.iteration_count || 0,
      max_iterations: activeOutcome.max_iterations || 0,
    };
  }
  let campaignView = null;
  if (activeCampaign) {
    const [completed, total] = campaignProgress(activeCampaign);
    campaignView = {
      id: activeCampaignSlug,
      goal: activeCampaign.goal || "",
      status: activeCampaign.status || "",
      phase: activeCampaign.phase || "",
      progress: { completed, total },
    };
  }
  let researchView = null;
  if (activeResearch) {
    researchView = {
      id: activeResearchSlug,
      question: activeResearch.question || "",
      status: activeResearch.status || "",
      claim_count: Array.isArray(activeResearch.claims) ? activeResearch.claims.length : 0,
      source_count: Array.isArray(activeResearch.sources) ? activeResearch.sources.length : 0,
    };
  }
  return {
    active_plan: planView,
    active_outcome: outcomeView,
    active_campaign: campaignView,
    active_research: researchView,
    latest_executed_verification: latestView,
  };
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

function routeHas(text, terms) {
  return containsAny(text, terms).length > 0;
}

function routeCommandFor(route, task, stateView) {
  const quotedTask = shellQuote(task);
  const packet = WORKFLOW_ROUTE_PROMPTS[route] || "next";
  if (route === "failure") {
    return "python3 scripts/mythify.py prompt failure";
  }
  if (route === "campaign") {
    if (stateView.active_campaign) {
      return "python3 scripts/mythify.py campaign prompt";
    }
    return `python3 scripts/mythify.py campaign start ${quotedTask} --success ${shellQuote("done criteria are verified")}`;
  }
  if (route === "outcome") {
    if (stateView.active_outcome) {
      return "python3 scripts/mythify.py outcome status";
    }
    return (
      `python3 scripts/mythify.py outcome start ${quotedTask} ` +
      `--success ${shellQuote("DEFINE SUCCESS")} --verify ${shellQuote("DEFINE VERIFIER")}`
    );
  }
  if (route === "research") {
    if (stateView.active_research) {
      return "python3 scripts/mythify.py prompt research";
    }
    return `python3 scripts/mythify.py research start ${quotedTask}`;
  }
  if (route === "review") {
    return `python3 scripts/mythify.py prompt review --goal ${quotedTask}`;
  }
  if (route === "handoff") {
    return `python3 scripts/mythify.py prompt handoff --goal ${quotedTask}`;
  }
  if (route === "plan") {
    const steps = JSON.stringify([
      {
        title: "Understand and design",
        success_criteria: "scope and verifier are explicit",
      },
      {
        title: "Implement",
        success_criteria: "requested behavior is present",
      },
      {
        title: "Verify",
        success_criteria: "nearest executable checks pass",
      },
    ]);
    return `python3 scripts/mythify.py plan create ${quotedTask} --steps ${shellQuote(steps)}`;
  }
  if (route === "prompt") {
    return `python3 scripts/mythify.py prompt ${packet}`;
  }
  return "Answer directly in the initiating chat; run verify run if an executable completion check exists.";
}

function routeStateWrites(route, stateView) {
  if (route === "failure") {
    return [
      "record reflection after diagnosing the red check",
      "record verify run after the recovery attempt",
      "update the active step with evidence when fixed",
    ];
  }
  if (route === "campaign") {
    if (stateView.active_campaign) {
      return [
        "campaign advance after the host completes the current task with evidence",
        "campaign learn when the next task should improve",
      ];
    }
    return ["campaign start when the host accepts the route"];
  }
  if (route === "outcome") {
    if (stateView.active_outcome) {
      return ["outcome check after each bounded attempt"];
    }
    return ["outcome start with explicit success criteria and verifier"];
  }
  if (route === "research") {
    if (stateView.active_research) {
      return ["research add-source", "research add-claim", "research close"];
    }
    return ["research start before implementation"];
  }
  if (route === "review") {
    return ["report findings in chat", "verify run supporting checks when fixes are made"];
  }
  if (route === "handoff") {
    return ["step updates and verify run as the active plan advances"];
  }
  if (route === "plan") {
    return ["plan create", "step updates", "verify run", "reflect on failures"];
  }
  return [];
}

function workflowRouteEvidence(route, stateView, classification) {
  const evidence = [
    {
      type: "router_manifest",
      version: WORKFLOW_ROUTER.version,
      routes: WORKFLOW_ROUTE_IDS,
    },
    {
      type: "classification",
      task_type: classification.task_type,
      risk: classification.risk,
      execution_profile: classification.execution_profile,
    },
  ];
  if (stateView.latest_executed_verification) {
    evidence.push({ type: "latest_executed_verification", ...stateView.latest_executed_verification });
  }
  for (const key of ["active_plan", "active_outcome", "active_campaign", "active_research"]) {
    if (stateView[key]) {
      evidence.push({ type: key, ...stateView[key] });
    }
  }
  evidence.push({ type: "route_decision", route, mutates_state: false });
  return evidence;
}

function selectWorkflowRoute(task, stateView, classification) {
  const text = String(task || "").toLowerCase().split(/\s+/).join(" ");
  const latest = stateView.latest_executed_verification;
  if (latest && latest.verified === false) {
    return [
      "failure",
      "The latest executed verification is red, so recover that failure before advancing unrelated work.",
    ];
  }
  if (routeHas(text, ROUTE_FULL_SEND_TERMS)) {
    return [
      "campaign",
      "The prompt uses full-send language, so route to a durable campaign loop with evidence-gated advancement.",
    ];
  }
  if (stateView.active_campaign && routeHas(text, ROUTE_RESUME_TERMS)) {
    return ["campaign", "An active campaign exists and the prompt asks to continue."];
  }
  if (routeHas(text, ROUTE_PROMPT_TERMS)) {
    return ["prompt", "The prompt asks for steering material rather than immediate execution."];
  }
  if (stateView.active_outcome && (routeHas(text, ROUTE_RESUME_TERMS) || routeHas(text, ROUTE_OUTCOME_TERMS))) {
    return ["outcome", "An active outcome loop exists and the prompt asks to continue or check it."];
  }
  if (routeHas(text, ROUTE_OUTCOME_TERMS) && routeHas(text, ROUTE_VERIFY_TERMS)) {
    return ["outcome", "The prompt names success or verification conditions, so use a bounded outcome loop."];
  }
  if (classification.task_type === "research" || routeHas(text, ROUTE_RESEARCH_TERMS)) {
    return ["research", "The task depends on external, uncertain, or source-backed information."];
  }
  if (classification.task_type === "review" || routeHas(text, ROUTE_REVIEW_TERMS)) {
    return ["review", "The task asks for audit, review, evaluation, or issue finding."];
  }
  if (stateView.active_research && routeHas(text, ROUTE_RESUME_TERMS)) {
    return ["research", "An active research record exists and the prompt asks to continue."];
  }
  if (stateView.active_plan && routeHas(text, ROUTE_RESUME_TERMS)) {
    return ["handoff", "An active plan exists and the prompt asks to continue from durable state."];
  }
  if (classification.execution_profile === "direct") {
    return ["direct", "Classification says this is a simple question or single reversible action."];
  }
  return ["plan", "Classification says this is multi-step work that should be planned and verified."];
}

function buildWorkflowRoute(task, classification) {
  const stateView = workflowRouteState();
  let [route, reason] = selectWorkflowRoute(task, stateView, classification);
  if (!WORKFLOW_ROUTE_IDS.includes(route)) {
    route = "plan";
    reason = "Router returned an unknown route, so Mythify fell back to a verifiable plan.";
  }
  const packetKind = WORKFLOW_ROUTE_PROMPTS[route] || "next";
  return {
    kind: "workflow_route",
    route,
    reason,
    input: String(task || ""),
    classification,
    state: stateView,
    next_command: routeCommandFor(route, task, stateView),
    prompt_packet: {
      kind: packetKind,
      command: `python3 scripts/mythify.py prompt ${packetKind}`,
    },
    verification_strategy: classification.verification || "",
    chat_policy: {
      executor: "initiating_host",
      surface: "chat",
      report_issues: true,
      progress_command: "python3 scripts/mythify.py report --since last --cursor chat --format chat",
      host_boundary: "Run the next step in the chat or host that initiated Mythify unless the user explicitly hands it elsewhere.",
    },
    pause_rules: [
      "destructive or irreversible actions",
      "real scope changes",
      "missing credentials, secrets, or billing acknowledgements",
      "decisions only the user can make",
    ],
    state_writes: routeStateWrites(route, stateView),
    evidence: workflowRouteEvidence(route, stateView, classification),
    guardrail: WORKFLOW_ROUTE_GUARDRAIL,
  };
}

function formatWorkflowRoute(payload) {
  const lines = [
    `[OK] Workflow route: ${payload.route || "unknown"}`,
    `Reason: ${payload.reason || ""}`,
    `Next command: ${payload.next_command || ""}`,
    `Prompt packet: ${payload.prompt_packet?.kind || ""} (${payload.prompt_packet?.command || ""})`,
    `Verification strategy: ${payload.verification_strategy || ""}`,
  ];
  const policy = payload.chat_policy || {};
  lines.push(
    `Chat policy: executor=${policy.executor || "initiating_host"}; ` +
      `surface=${policy.surface || "chat"}; report_issues=${String(policy.report_issues !== false)}`
  );
  if (payload.state_writes?.length > 0) {
    lines.push("Expected state writes:");
    for (const item of payload.state_writes) {
      lines.push(`- ${item}`);
    }
  }
  if (payload.pause_rules?.length > 0) {
    lines.push("Pause for:");
    for (const item of payload.pause_rules) {
      lines.push(`- ${item}`);
    }
  }
  lines.push(`Guardrail: ${payload.guardrail || WORKFLOW_ROUTE_GUARDRAIL}`);
  return lines.join("\n");
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

function currentInProgressStep(plan) {
  return (plan.steps || []).find((step) => step.status === "in_progress") || null;
}

function recentRecords(records, limit) {
  if (limit <= 0) {
    return [];
  }
  return records.slice(Math.max(0, records.length - limit));
}

function buildWorkflowDashboard(recent = 3) {
  const active = readActiveSlug();
  let activePlan = null;
  if (active && fs.existsSync(planPath(active))) {
    const plan = readJsonRecover(planPath(active), () => null);
    if (plan !== null) {
      const steps = Array.isArray(plan.steps) ? plan.steps : [];
      const completed = steps.filter((step) => step.status === "completed").length;
      activePlan = {
        slug: active,
        goal: plan.goal || "",
        completed_steps: completed,
        total_steps: steps.length,
        current_step: currentInProgressStep(plan),
        next_pending_step: steps.find((step) => step.status === "pending") || null,
        steps,
      };
    }
  }
  const activeOutcomeSlug = readActiveOutcomeSlug();
  let activeOutcome = null;
  if (activeOutcomeSlug) {
    const resolved = resolveOutcome(activeOutcomeSlug);
    if (!resolved.error) {
      const iterations = readOutcomeIterations(resolved.slug);
      activeOutcome = {
        slug: resolved.slug,
        goal: resolved.goal.goal || "",
        status: resolved.goal.status || "active",
        iteration_count: resolved.goal.iteration_count || 0,
        max_iterations: resolved.goal.max_iterations || 1,
        last_iteration: iterations.length > 0 ? iterations[iterations.length - 1] : null,
      };
    }
  }
  const memory = loadMemory();
  const projectLessons = readLessonsFrom(projectLessonsDir(), "project");
  const globalLessons = readLessonsFrom(globalLessonsDir(), "global");
  const verifications = readJsonl(verificationsPath());
  const executed = verifications.filter((record) => record.kind === "executed");
  const reflections = readJsonl(reflectionsPath());
  return {
    state_dir: resolveStateDir(),
    active_plan: activePlan,
    active_outcome: activeOutcome,
    counts: {
      memory: memory.entries.length,
      project_lessons: projectLessons.length,
      global_lessons: globalLessons.length,
      verifications: verifications.length,
      reflections: reflections.length,
    },
    verification_summary: {
      executed: executed.length,
      executed_passed: executed.filter((record) => record.verified === true).length,
      executed_failed: executed.filter((record) => record.verified === false).length,
      attested: verifications.filter((record) => record.kind === "attested").length,
      recent: recentRecords(verifications, recent),
    },
    reflection_summary: {
      total: reflections.length,
      recent: recentRecords(reflections, recent),
    },
  };
}

function formatWorkflowDashboard(dashboard) {
  const lines = [`[OK] Workflow dashboard: ${dashboard.state_dir}`];
  const plan = dashboard.active_plan;
  if (plan) {
    lines.push(`Active plan: ${plan.slug} (${plan.completed_steps}/${plan.total_steps} completed)`);
    lines.push(`Goal: ${plan.goal}`);
    if (plan.current_step) {
      lines.push(`Current step: ${stepLine(plan.current_step)}`);
    }
    if (plan.next_pending_step) {
      lines.push(
        `Next pending: ${plan.next_pending_step.id}. ${plan.next_pending_step.title} ` +
          `(criteria: ${plan.next_pending_step.success_criteria || "none"})`
      );
    } else if (!plan.current_step) {
      lines.push("Next pending: none");
    }
  } else {
    lines.push("Active plan: none");
  }
  const outcome = dashboard.active_outcome;
  if (outcome) {
    lines.push(
      `Active outcome: ${outcome.slug} (${outcome.status}, ` +
        `${outcome.iteration_count}/${outcome.max_iterations} iterations)`
    );
  } else {
    lines.push("Active outcome: none");
  }
  const counts = dashboard.counts;
  lines.push(
    `Counts: memory ${counts.memory}, lessons ${counts.project_lessons} project + ` +
      `${counts.global_lessons} global, verifications ${counts.verifications}, ` +
      `reflections ${counts.reflections}`
  );
  const verification = dashboard.verification_summary;
  lines.push(
    `Evidence: ${verification.executed} executed (${verification.executed_passed} passed, ` +
      `${verification.executed_failed} failed), ${verification.attested} attested`
  );
  if (verification.recent.length > 0) {
    lines.push("Recent verification:");
    for (const record of verification.recent) {
      if (record.kind === "executed") {
        const verdict = record.verified === true ? "passed" : "failed";
        const label = record.claim || record.command || "executed check";
        lines.push(`  - ${verdict}: ${label} (exit ${record.exit_code})`);
      } else {
        lines.push(`  - attested: ${record.claim || "claim"}`);
      }
    }
  }
  const reflections = dashboard.reflection_summary;
  if (reflections.recent.length > 0) {
    lines.push("Recent reflection:");
    for (const record of reflections.recent) {
      lines.push(`  - ${record.outcome || "unknown"}: ${record.action || ""}; next ${record.next || ""}`);
    }
  }
  return lines.join("\n");
}

const VERIFICATION_HISTORY_ICONS = {
  passed: "[x]",
  failed: "[!]",
  attested: "[~]",
  unknown: "[ ]",
};

function verificationVerdict(record) {
  if (record.kind === "attested") {
    return "attested";
  }
  if (record.kind === "executed" && record.verified === true) {
    return "passed";
  }
  if (record.kind === "executed" && record.verified === false) {
    return "failed";
  }
  return "unknown";
}

function summarizeVerificationRecord(record, index) {
  const kind = record.kind || "unknown";
  const verdict = verificationVerdict(record);
  const summary = {
    index,
    kind,
    verdict,
    timestamp: record.timestamp || "",
    claim: record.claim,
    verified: record.verified,
    plan: record.plan,
    step_id: record.step_id,
    step_title: record.step_title,
    step_status: record.step_status,
  };
  if (kind === "executed") {
    return {
      ...summary,
      command: record.command || "",
      exit_code: record.exit_code,
      duration_seconds: record.duration_seconds || 0,
      stdout_tail_bytes: String(record.stdout_tail || "").length,
      stderr_tail_bytes: String(record.stderr_tail || "").length,
    };
  }
  if (kind === "attested") {
    return {
      ...summary,
      evidence: record.evidence || "",
    };
  }
  return summary;
}

function buildVerificationHistoryView(recent = 10) {
  const rows = readJsonl(verificationsPath()).map((record, index) =>
    summarizeVerificationRecord(record, index + 1)
  );
  const executed = rows.filter((row) => row.kind === "executed");
  const recentRows = recent <= 0 ? [] : rows.slice(Math.max(0, rows.length - recent)).reverse();
  return {
    state_dir: resolveStateDir(),
    records: recentRows,
    counts: {
      total: rows.length,
      executed: executed.length,
      executed_passed: executed.filter((row) => row.verdict === "passed").length,
      executed_failed: executed.filter((row) => row.verdict === "failed").length,
      attested: rows.filter((row) => row.kind === "attested").length,
      unknown: rows.filter((row) => row.verdict === "unknown").length,
    },
    guardrail: "history displays recorded evidence only; it does not rerun checks or upgrade attested claims",
  };
}

function verificationLabel(row) {
  return compactLabel(row.claim || row.command || row.evidence, "verification");
}

function formatVerificationHistoryRow(row) {
  const icon = VERIFICATION_HISTORY_ICONS[row.verdict] || "[ ]";
  let line = `  ${icon} ${row.timestamp || "unknown-time"} #${row.index} ${row.verdict}: ${verificationLabel(row)}`;
  const details = [];
  if (row.kind === "executed") {
    details.push(`exit ${row.exit_code}`);
    details.push(`${row.duration_seconds || 0}s`);
    if (row.stdout_tail_bytes) {
      details.push(`stdout ${row.stdout_tail_bytes} bytes`);
    }
    if (row.stderr_tail_bytes) {
      details.push(`stderr ${row.stderr_tail_bytes} bytes`);
    }
  } else if (row.kind === "attested") {
    details.push("self-reported");
  }
  if (row.plan) {
    if (row.step_id !== null && row.step_id !== undefined) {
      details.push(`plan ${row.plan} step ${row.step_id}`);
    } else {
      details.push(`plan ${row.plan}`);
    }
  }
  if (details.length > 0) {
    line += ` (${details.join("; ")})`;
  }
  return line;
}

function formatVerificationHistoryView(view) {
  const lines = [`[OK] Verification history: ${view.state_dir}`];
  const counts = view.counts;
  lines.push(
    `Evidence: ${counts.executed} executed (${counts.executed_passed} passed, ` +
      `${counts.executed_failed} failed), ${counts.attested} attested, ${counts.total} total`
  );
  if (view.records.length > 0) {
    lines.push("Recent verification:");
    for (const row of view.records) {
      lines.push(formatVerificationHistoryRow(row));
    }
  } else {
    lines.push("No verification records found.");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

function reportsDir() {
  return path.join(resolveStateDir(), "reports");
}

function reportCursorName(name) {
  return slugify(name || "default") || "default";
}

function reportCursorPath(cursor) {
  return path.join(reportsDir(), `${reportCursorName(cursor)}.json`);
}

function reportEventSortKey(event) {
  return [event.timestamp || "", event.order || 0, event.key || ""];
}

function compareReportEvents(left, right) {
  const leftKey = reportEventSortKey(left);
  const rightKey = reportEventSortKey(right);
  const timestampOrder = compareTimestampValues(leftKey[0], rightKey[0]);
  if (timestampOrder !== 0) {
    return timestampOrder;
  }
  for (let index = 1; index < leftKey.length; index += 1) {
    if (leftKey[index] < rightKey[index]) {
      return -1;
    }
    if (leftKey[index] > rightKey[index]) {
      return 1;
    }
  }
  return 0;
}

function compactReportDetail(text) {
  const value = String(text || "").trim();
  return value.length <= 140 ? value : `${value.slice(0, 137)}...`;
}

function reportAttentionLevel(event) {
  if (
    event.verified === false ||
    event.kind === "step_failed" ||
    event.kind === "reflection_failure"
  ) {
    return "issue";
  }
  if (event.kind === "verification_attested") {
    return "warning";
  }
  return "";
}

function buildReportAttentionEvents(events) {
  const items = [];
  for (const event of events) {
    const level = reportAttentionLevel(event);
    if (!level) {
      continue;
    }
    items.push({
      level,
      key: event.key || "",
      timestamp: event.timestamp || "",
      kind: event.kind || "",
      summary: event.summary || "Event recorded",
      detail: event.detail || "",
      plan: event.plan,
      step_id: event.step_id,
      verified: event.verified,
    });
  }
  return items;
}

function buildReportEvents(logLowerBound = "") {
  const events = [];
  for (const slug of listPlanSlugs()) {
    const plan = readJsonRecover(planPath(slug), () => null);
    if (!plan || !Array.isArray(plan.steps)) {
      continue;
    }
    const steps = plan.steps || [];
    const created = plan.created || plan.last_updated || "";
    events.push({
      key: `plan:${slug}:created`,
      timestamp: created,
      order: 10,
      kind: "plan_created",
      summary: `Plan created: ${slug} (${steps.length} steps)`,
      detail: plan.goal || "",
      plan: slug,
      step_id: null,
      verified: null,
    });
    for (const step of steps) {
      const updated = step.updated_at;
      if (!updated) {
        continue;
      }
      const status = step.status || "pending";
      events.push({
        key: `step:${slug}:${step.id}:${status}:${updated}`,
        timestamp: updated,
        order: 20,
        kind: `step_${status}`,
        summary: `Step ${status}: ${step.id}. ${step.title}`,
        detail: step.result || step.success_criteria || "",
        plan: slug,
        step_id: step.id,
        verified: null,
      });
    }
  }
  const verifications = readJsonlSince(verificationsPath(), logLowerBound);
  verifications.forEach((record, index) => {
    let summary;
    let detail;
    let verified = null;
    if (record.kind === "executed") {
      verified = record.verified === true;
      const verdict = verified ? "passed" : "failed";
      const label = record.claim || record.command || "executed check";
      summary = `Verification ${verdict}: ${compactReportDetail(label)}`;
      detail = `exit ${record.exit_code}`;
    } else if (record.kind === "attested") {
      const label = record.claim || "claim";
      summary = `Verification attested: ${compactReportDetail(label)}`;
      detail = "self-reported, not machine-checked";
    } else {
      summary = "Verification recorded";
      detail = "";
    }
    events.push({
      key: `verification:${index + 1}:${record.timestamp || ""}`,
      timestamp: record.timestamp || "",
      order: 30,
      kind: `verification_${verificationVerdict(record)}`,
      summary,
      detail,
      plan: record.plan,
      step_id: record.step_id,
      verified,
    });
  });
  const reflections = readJsonlSince(reflectionsPath(), logLowerBound);
  reflections.forEach((record, index) => {
    events.push({
      key: `reflection:${index + 1}:${record.timestamp || ""}`,
      timestamp: record.timestamp || "",
      order: 40,
      kind: `reflection_${record.outcome || "unknown"}`,
      summary: `Reflection ${record.outcome || "unknown"}: ${compactReportDetail(record.action || "action")}`,
      detail: `next: ${record.next || ""}`,
      plan: null,
      step_id: null,
      verified: null,
    });
  });
  return events.sort(compareReportEvents);
}

function eventsAfterMarker(events, marker) {
  const lastEvent = marker && typeof marker === "object" ? marker.last_event : null;
  if (!lastEvent || typeof lastEvent !== "object") {
    return events;
  }
  if (lastEvent.key) {
    const index = events.findIndex((event) => event.key === lastEvent.key);
    if (index >= 0) {
      return events.slice(index + 1);
    }
  }
  if (lastEvent.timestamp) {
    return events.filter((event) => timestampAfter(event.timestamp || "", lastEvent.timestamp));
  }
  return events;
}

function buildWorkReport({
  since = "last",
  recent = DEFAULT_REPORT_RECENT,
  cursor = "default",
  peek = false,
  mark = false,
} = {}) {
  if (!Number.isInteger(recent) || recent < 0) {
    return { error: "[FAIL] Invalid recent: use 0 or a positive integer." };
  }
  if (mark && peek) {
    return { error: "[FAIL] mark cannot be combined with peek." };
  }
  const cursorName = reportCursorName(cursor);
  const marker = readJsonRecover(reportCursorPath(cursorName), () => ({}));
  const lastEvent = marker && typeof marker === "object" ? marker.last_event : null;
  const logLowerBound =
    since === "last" && !mark && lastEvent && typeof lastEvent === "object"
      ? lastEvent.timestamp || ""
      : "";
  const allEvents = buildReportEvents(logLowerBound);
  const candidateEvents = mark ? [] : since === "last" ? eventsAfterMarker(allEvents, marker) : allEvents;
  const visibleEvents = recent === 0 ? [] : candidateEvents.slice(Math.max(0, candidateEvents.length - recent));
  const attentionCandidates = buildReportAttentionEvents(candidateEvents);
  const attentionEvents = attentionCandidates.slice(Math.max(0, attentionCandidates.length - DEFAULT_REPORT_ATTENTION));
  if (mark || !peek) {
    writeJsonAtomic(reportCursorPath(cursorName), {
      cursor: cursorName,
      updated_at: isoNow(),
      last_event: allEvents.length > 0 ? allEvents[allEvents.length - 1] : marker.last_event,
    });
  }
  return {
    state_dir: resolveStateDir(),
    cursor: cursorName,
    since,
    format: "chat",
    peek,
    mark,
    events: visibleEvents,
    new_event_count: candidateEvents.length,
    shown_event_count: visibleEvents.length,
    omitted_new_events: Math.max(0, candidateEvents.length - visibleEvents.length),
    attention_events: attentionEvents,
    attention_event_count: attentionCandidates.length,
    omitted_attention_events: Math.max(0, attentionCandidates.length - attentionEvents.length),
    cursor_updated: !peek,
    last_event: allEvents.length > 0 ? allEvents[allEvents.length - 1] : null,
    guardrail:
      "report summarizes durable Mythify state only; it does not rerun checks or prove work beyond recorded evidence",
  };
}

function formatWorkReport(view) {
  const lines = [`[OK] Live work report: ${view.state_dir}`];
  if (view.mark) {
    lines.push(
      `Scope: mark cursor ${view.cursor}, ${view.new_event_count} new events ` +
        `(${view.shown_event_count} shown, ${view.omitted_new_events} omitted)`
    );
  } else {
    lines.push(
      `Scope: since ${view.since}, cursor ${view.cursor}, ${view.new_event_count} new events ` +
        `(${view.shown_event_count} shown, ${view.omitted_new_events} omitted)`
    );
  }
  if (view.attention_event_count > 0) {
    lines.push("Attention:");
    for (const event of view.attention_events || []) {
      let line = `- ${event.level || "notice"}: ${event.summary || "Event recorded"}`;
      if (event.detail) {
        line += `, ${compactReportDetail(event.detail)}`;
      }
      lines.push(line);
    }
    if (view.omitted_attention_events > 0) {
      lines.push(`- ${view.omitted_attention_events} older attention events omitted`);
    }
  } else {
    lines.push("Attention: none in this report window.");
  }
  if (view.events.length > 0) {
    for (const event of view.events) {
      let line = `- ${event.summary || "Event recorded"}`;
      if (event.detail) {
        line += `, ${compactReportDetail(event.detail)}`;
      }
      lines.push(line);
    }
  } else if (view.mark) {
    lines.push("Cursor is ready. Future reports with --since last will show only new events.");
  } else {
    lines.push("No new Mythify events to report.");
  }
  if (view.mark) {
    lines.push(`Cursor marked at latest event: ${view.cursor}`);
  } else {
    lines.push(view.cursor_updated ? `Cursor advanced: ${view.cursor}` : "Cursor unchanged: --peek");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

const BACKGROUND_STATUS_ICONS = {
  active: "[>]",
  running: "[>]",
  pending: "[ ]",
  completed: "[x]",
  succeeded: "[x]",
  failed: "[!]",
  interrupted: "[~]",
  stopped: "[~]",
  empty: "[ ]",
};

function backgroundRecent(items, limit) {
  if (limit <= 0) {
    return [];
  }
  return items.slice(Math.max(0, items.length - limit)).reverse();
}

function fanoutRootDir() {
  return path.join(resolveStateDir(), "fanout");
}

function countStatuses(items, statuses) {
  const counts = Object.fromEntries(statuses.map((status) => [status, 0]));
  for (const item of items) {
    const status = item.status || "unknown";
    counts[status] = (counts[status] || 0) + 1;
  }
  return counts;
}

function summarizeFanoutJob(job) {
  const tasks = Array.isArray(job.tasks) ? job.tasks : [];
  const counts = countStatuses(tasks, ["pending", "running", "completed", "failed", "interrupted"]);
  let status;
  if (counts.pending > 0 || counts.running > 0) {
    status = "active";
  } else if (counts.failed > 0) {
    status = "failed";
  } else if (counts.interrupted > 0) {
    status = "interrupted";
  } else if (tasks.length > 0) {
    status = "completed";
  } else {
    status = "empty";
  }
  return {
    id: job.id || "",
    status,
    created: job.created || "",
    last_updated: job.last_updated || "",
    purpose: job.purpose || "",
    engine: job.engine || "",
    model: job.model || "",
    visibility: job.visibility || "summary",
    task_counts: counts,
    task_total: tasks.length,
    tasks: tasks.map((task) => ({
      id: task.id,
      title: task.title || "",
      status: task.status || "pending",
      role: task.role || "worker",
      engine: task.engine || "",
      model: task.model || "",
      started_at: task.started_at || "",
      finished_at: task.finished_at || "",
      duration_seconds: task.duration_seconds || 0,
      error: task.error || null,
      output_file: task.output_file || null,
      output_bytes: task.output_bytes || 0,
    })),
  };
}

function listFanoutSummaries() {
  let names;
  try {
    names = fs.readdirSync(fanoutRootDir());
  } catch {
    return [];
  }
  const jobs = [];
  for (const name of names.sort()) {
    if (!/^fo-\d{14}-[0-9a-f]{4}$/.test(name)) {
      continue;
    }
    const job = readJsonRecover(path.join(fanoutRootDir(), name, "job.json"), () => null);
    if (job && typeof job === "object") {
      const summary = summarizeFanoutJob(job);
      if (!summary.id) {
        summary.id = name;
      }
      jobs.push(summary);
    }
  }
  return jobs.sort((left, right) =>
    `${left.created || ""}${left.id || ""}`.localeCompare(`${right.created || ""}${right.id || ""}`)
  );
}

function summarizeOutcome(slug, goal) {
  const iterations = readOutcomeIterations(slug);
  const lastIteration = iterations.length > 0 ? iterations[iterations.length - 1] : null;
  return {
    id: slug,
    goal: goal.goal || "",
    status: goal.status || "active",
    iteration_count: goal.iteration_count || 0,
    max_iterations: goal.max_iterations || 1,
    visibility: goal.visibility || "summary",
    created: goal.created || "",
    updated: goal.updated || "",
    last_verified: goal.last_verified,
    last_iteration: lastIteration,
    next_action: lastIteration ? lastIteration.next_action : "make a bounded attempt, then call outcome_check",
  };
}

function listOutcomeSummaries() {
  let names;
  try {
    names = fs.readdirSync(outcomesDir());
  } catch {
    return [];
  }
  const outcomes = [];
  for (const name of names.sort()) {
    const goalPath = outcomeGoalPath(name);
    if (!fs.existsSync(goalPath)) {
      continue;
    }
    const goal = readJsonRecover(goalPath, () => null);
    if (goal && typeof goal === "object") {
      outcomes.push(summarizeOutcome(name, goal));
    }
  }
  return outcomes.sort((left, right) =>
    `${left.updated || left.created || ""}${left.id || ""}`.localeCompare(
      `${right.updated || right.created || ""}${right.id || ""}`
    )
  );
}

function buildBackgroundView(recent = 5) {
  const outcomes = listOutcomeSummaries();
  const fanoutJobs = listFanoutSummaries();
  const activeOutcomeSlug = readActiveOutcomeSlug();
  const outcomeCounts = countStatuses(outcomes, ["active", "succeeded", "failed", "stopped"]);
  const fanoutCounts = countStatuses(fanoutJobs, ["active", "completed", "failed", "interrupted", "empty"]);
  const taskCounts = {
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
    interrupted: 0,
  };
  for (const job of fanoutJobs) {
    for (const [status, count] of Object.entries(job.task_counts || {})) {
      taskCounts[status] = (taskCounts[status] || 0) + count;
    }
  }
  return {
    state_dir: resolveStateDir(),
    active_outcome: outcomes.find((outcome) => outcome.id === activeOutcomeSlug) || null,
    outcomes: backgroundRecent(outcomes, recent),
    fanout_jobs: backgroundRecent(fanoutJobs, recent),
    counts: {
      outcomes: { total: outcomes.length, ...outcomeCounts },
      fanout_jobs: { total: fanoutJobs.length, ...fanoutCounts },
      fanout_tasks: taskCounts,
    },
  };
}

function compactLabel(text, fallback) {
  const value = String(text || "").trim();
  if (value === "") {
    return fallback;
  }
  return value.length <= 80 ? value : `${value.slice(0, 77)}...`;
}

function formatBackgroundView(view) {
  const lines = [`[OK] Background tasks: ${view.state_dir}`];
  const outcomes = view.counts.outcomes;
  lines.push(
    `Outcomes: ${outcomes.total} total; ${outcomes.active || 0} active, ` +
      `${outcomes.succeeded || 0} succeeded, ${outcomes.failed || 0} failed, ` +
      `${outcomes.stopped || 0} stopped`
  );
  if (view.active_outcome) {
    lines.push(
      `Active outcome: ${view.active_outcome.id} (${view.active_outcome.status}, ` +
        `${view.active_outcome.iteration_count}/${view.active_outcome.max_iterations} iterations)`
    );
  } else {
    lines.push("Active outcome: none");
  }
  if (view.outcomes.length > 0) {
    lines.push("Recent outcomes:");
    for (const outcome of view.outcomes) {
      const icon = BACKGROUND_STATUS_ICONS[outcome.status] || "[ ]";
      lines.push(
        `  ${icon} ${outcome.id}: ${compactLabel(outcome.goal, "outcome")} ` +
          `(${outcome.status}, ${outcome.iteration_count}/${outcome.max_iterations} iterations, ` +
          `last verified=${outcome.last_verified})`
      );
      if (outcome.next_action) {
        lines.push(`      next: ${outcome.next_action}`);
      }
    }
  }
  const fanout = view.counts.fanout_jobs;
  const tasks = view.counts.fanout_tasks;
  lines.push(
    `Fanout jobs: ${fanout.total} total; ${fanout.active || 0} active, ` +
      `${fanout.completed || 0} completed, ${fanout.failed || 0} failed, ` +
      `${fanout.interrupted || 0} interrupted`
  );
  lines.push(
    `Fanout tasks: ${tasks.running || 0} running, ${tasks.pending || 0} pending, ` +
      `${tasks.completed || 0} completed, ${tasks.failed || 0} failed, ` +
      `${tasks.interrupted || 0} interrupted`
  );
  if (view.fanout_jobs.length > 0) {
    lines.push("Recent fanout jobs:");
    for (const job of view.fanout_jobs) {
      const icon = BACKGROUND_STATUS_ICONS[job.status] || "[ ]";
      const taskCounts = job.task_counts;
      lines.push(
        `  ${icon} ${job.id}: ${compactLabel(job.purpose, "fanout job")} ` +
          `(${job.status}; ${job.task_total} tasks, ${taskCounts.completed || 0} completed, ` +
          `${taskCounts.failed || 0} failed, ${taskCounts.running || 0} running, ` +
          `${taskCounts.pending || 0} pending)`
      );
      lines.push(
        `      visibility: ${job.visibility || "summary"}; engine: ${job.engine || "unknown"}; ` +
          `created: ${job.created || "unknown"}`
      );
      for (const task of job.tasks) {
        const taskIcon = BACKGROUND_STATUS_ICONS[task.status] || "[ ]";
        let detail = `      ${taskIcon} ${task.id}. ${compactLabel(task.title, "task")} (${task.status})`;
        if (task.error) {
          detail += `: ${compactLabel(task.error, "error")}`;
        }
        lines.push(detail);
      }
    }
  }
  if (view.outcomes.length === 0 && view.fanout_jobs.length === 0) {
    lines.push("No background tasks found.");
  }
  return lines.join("\n");
}

function summarizeOutcomeProgress(slug, goal) {
  const iterations = readOutcomeIterations(slug);
  const lastIteration = iterations.length > 0 ? iterations[iterations.length - 1] : null;
  const iterationCount = Number(goal.iteration_count || 0);
  const maxIterations = Number(goal.max_iterations || 1);
  const metric = lastIteration && lastIteration.metric ? lastIteration.metric : null;
  const verify = lastIteration && lastIteration.verify ? lastIteration.verify : {};
  const lastCheck = lastIteration
    ? {
        iteration: lastIteration.iteration,
        timestamp: lastIteration.timestamp || "",
        verified: lastIteration.verified,
        status_after: lastIteration.status_after || "",
        notes: lastIteration.notes || "",
        verify_exit_code: verify.exit_code,
        verify_duration_seconds: verify.duration_seconds || 0,
        verify_verified: verify.verified,
        metric_exit_code: metric ? metric.exit_code : null,
        metric_score: metric ? metric.score : null,
        metric_verified: metric ? metric.verified : null,
      }
    : null;
  return {
    id: slug,
    goal: goal.goal || "",
    success_criteria: goal.success_criteria || "",
    status: goal.status || "active",
    iteration_count: iterationCount,
    max_iterations: maxIterations,
    iterations_remaining: Math.max(0, maxIterations - iterationCount),
    progress_percent: maxIterations ? Math.round((iterationCount / maxIterations) * 1000) / 10 : 0,
    visibility: goal.visibility || "summary",
    created: goal.created || "",
    updated: goal.updated || "",
    last_verified: goal.last_verified,
    last_check: lastCheck,
    next_action: lastIteration
      ? lastIteration.next_action
      : "make a bounded attempt, then call outcome_check",
    verify_command: goal.verify_command || "",
    metric_command: goal.metric_command || "",
    best_metric_score: goal.best_metric_score,
    allowed_paths: Array.isArray(goal.allowed_paths) ? goal.allowed_paths : [],
    stop_reason: goal.stop_reason,
  };
}

function listOutcomeProgressRows() {
  let names;
  try {
    names = fs.readdirSync(outcomesDir());
  } catch {
    return [];
  }
  const rows = [];
  for (const name of names.sort()) {
    const goalPath = outcomeGoalPath(name);
    if (!fs.existsSync(goalPath)) {
      continue;
    }
    const goal = readJsonRecover(goalPath, () => null);
    if (goal && typeof goal === "object") {
      rows.push(summarizeOutcomeProgress(name, goal));
    }
  }
  return rows.sort((left, right) =>
    `${left.updated || left.created || ""}${left.id || ""}`.localeCompare(
      `${right.updated || right.created || ""}${right.id || ""}`
    )
  );
}

function buildOutcomeProgressView(recent = 5) {
  const outcomes = listOutcomeProgressRows();
  const activeOutcomeSlug = readActiveOutcomeSlug();
  const counts = countStatuses(outcomes, ["active", "succeeded", "failed", "stopped"]);
  return {
    state_dir: resolveStateDir(),
    active_outcome: outcomes.find((outcome) => outcome.id === activeOutcomeSlug) || null,
    outcomes: backgroundRecent(outcomes, recent),
    counts: { total: outcomes.length, ...counts },
    guardrail:
      "progress displays recorded outcome verifier results only; it does not run checks, make attempts, stop loops, or treat notes as verification",
  };
}

function formatOutcomeProgressRow(row) {
  const icon = BACKGROUND_STATUS_ICONS[row.status] || "[ ]";
  const lines = [
    `  ${icon} ${row.id}: ${compactLabel(row.goal, "outcome")} ` +
      `(${row.status}, ${row.iteration_count}/${row.max_iterations} iterations, ` +
      `${row.iterations_remaining} remaining)`,
  ];
  const last = row.last_check;
  if (last) {
    lines.push(
      `      verifier: iteration ${last.iteration}, exit ${last.verify_exit_code}, ` +
        `verified=${last.verify_verified}, at ${last.timestamp || "unknown-time"}`
    );
    if (last.metric_exit_code !== null && last.metric_exit_code !== undefined) {
      let metricLine = `      metric: exit ${last.metric_exit_code}`;
      if (last.metric_score !== null && last.metric_score !== undefined) {
        metricLine += `, score ${last.metric_score}`;
      }
      lines.push(metricLine);
    }
  } else {
    lines.push("      verifier: no recorded iterations yet");
  }
  if (row.next_action) {
    lines.push(`      next: ${row.next_action}`);
  }
  return lines;
}

function formatOutcomeProgressView(view) {
  const lines = [`[OK] Outcome progress: ${view.state_dir}`];
  const counts = view.counts;
  lines.push(
    `Outcomes: ${counts.total} total; ${counts.active || 0} active, ` +
      `${counts.succeeded || 0} succeeded, ${counts.failed || 0} failed, ` +
      `${counts.stopped || 0} stopped`
  );
  const active = view.active_outcome;
  if (active) {
    lines.push(
      `Active outcome: ${active.id} (${active.status}, ` +
        `${active.iteration_count}/${active.max_iterations} iterations, ` +
        `${active.iterations_remaining} remaining)`
    );
  } else {
    lines.push("Active outcome: none");
  }
  if (view.outcomes.length > 0) {
    lines.push("Recent outcomes:");
    for (const row of view.outcomes) {
      lines.push(...formatOutcomeProgressRow(row));
    }
  } else {
    lines.push("No outcome loops found.");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

const RELEASE_READINESS_GATES = [
  {
    id: "python_tests",
    label: "Python test suite",
    required: true,
    sources: ["tests/"],
    match_any: ["python3 -m unittest discover -s tests", "Python suite passes"],
  },
  {
    id: "node_mcp_tests",
    label: "Node MCP suite",
    required: true,
    sources: ["mcp-server/test/"],
    match_any: ["npm test --prefix mcp-server", "Node MCP suite passes"],
  },
  {
    id: "surface_manifest",
    label: "Surface manifest check",
    required: true,
    sources: ["protocol/surface-manifest.json", "scripts/check_surface_manifest.mjs"],
    match_any: ["node scripts/check_surface_manifest.mjs", "surface manifest"],
  },
  {
    id: "classification_rules_manifest",
    label: "Classification rules manifest check",
    required: true,
    sources: [
      "protocol/classification-rules.json",
      "mcp-server/protocol/classification-rules.json",
      "scripts/check_classification_rules_manifest.mjs",
    ],
    match_any: ["node scripts/check_classification_rules_manifest.mjs", "classification rules manifest"],
  },
  {
    id: "registry_docs",
    label: "Generated registry docs check",
    required: true,
    sources: ["scripts/build_registry_docs.mjs", "docs/adapter-candidates.md"],
    match_any: ["node scripts/build_registry_docs.mjs --check", "registry docs", "generated docs"],
  },
  {
    id: "protocol_check",
    label: "Protocol variants check",
    required: true,
    sources: ["protocol/PROTOCOL.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"],
    match_any: ["python3 scripts/mythify.py protocol check", "protocol check"],
  },
  {
    id: "variant_idempotence",
    label: "Generated variants idempotence",
    required: true,
    sources: ["scripts/build_variants.py", "AGENTS.md", "CLAUDE.md", ".cursorrules"],
    match_any: ["scripts/build_variants.py", "generated variants", "variant idempotence"],
  },
  {
    id: "whitespace",
    label: "Whitespace check",
    required: true,
    sources: ["git diff --check"],
    match_any: ["git diff --check", "whitespace"],
  },
  {
    id: "forbidden_dash_scan",
    label: "Forbidden dash scan",
    required: true,
    sources: ["AGENTS.md", "docs/design.md"],
    match_any: ["forbidden dash", "dash scan"],
  },
  {
    id: "emoji_scan",
    label: "Emoji scan",
    required: true,
    sources: ["AGENTS.md", "docs/design.md"],
    match_any: ["emoji scan", "emoji-like"],
  },
];

const RELEASE_READINESS_ICONS = {
  passed: "[x]",
  failed: "[!]",
  missing: "[ ]",
  unknown: "[~]",
  clean: "[x]",
  dirty: "[!]",
  present: "[x]",
};

function projectRootFromState(stateDir) {
  return path.basename(stateDir) === ".mythify" ? path.dirname(stateDir) : process.cwd();
}

function verificationSearchText(record) {
  return ["claim", "command", "stdout_tail", "stderr_tail"]
    .map((key) => String(record[key] || ""))
    .join("\n")
    .toLowerCase();
}

function latestMatchingVerification(records, gate) {
  const needles = gate.match_any.map((item) => item.toLowerCase());
  const matches = records.filter(
    (record) =>
      record.kind === "executed" &&
      needles.some((needle) => verificationSearchText(record).includes(needle))
  );
  return matches.length > 0 ? matches[matches.length - 1] : null;
}

function summarizeReleaseGate(gate, records) {
  const record = latestMatchingVerification(records, gate);
  const status = record ? (record.verified === true ? "passed" : "failed") : "missing";
  return {
    id: gate.id,
    label: gate.label,
    required: gate.required,
    sources: [...gate.sources],
    status,
    latest_record: record
      ? {
          timestamp: record.timestamp || "",
          claim: record.claim,
          command: record.command || "",
          exit_code: record.exit_code,
          verified: record.verified,
          plan: record.plan,
          step_id: record.step_id,
        }
      : null,
  };
}

function gitStatusSummary(root) {
  const result = spawnSync("git", ["--no-optional-locks", "status", "--short", "--branch"], {
    cwd: root,
    encoding: "utf8",
    timeout: 10000,
    env: { ...process.env, GIT_OPTIONAL_LOCKS: "0" },
  });
  if (result.error) {
    return {
      status: "unknown",
      branch: "",
      clean: null,
      detail: result.error.message,
    };
  }
  const output = result.stdout || "";
  if (result.status !== 0) {
    return {
      status: "unknown",
      branch: "",
      clean: null,
      detail: String(result.stderr || output || "git status failed").trim(),
    };
  }
  const lines = output.split(/\r?\n/).filter((line) => line.trim() !== "");
  const branch = lines.length > 0 && lines[0].startsWith("## ") ? lines[0].slice(3).trim() : "";
  const changedPaths = lines.filter((line) => !line.startsWith("## "));
  const clean = changedPaths.length === 0;
  return {
    status: clean ? "clean" : "dirty",
    branch,
    clean,
    detail: clean ? "working tree clean" : `${changedPaths.length} changed paths`,
    changed_paths: changedPaths.slice(0, 20),
  };
}

function roadmapSummary(root) {
  const roadmapPath = path.join(root, "roadmap.md");
  if (!fs.existsSync(roadmapPath)) {
    return {
      status: "unknown",
      path: roadmapPath,
      active_now: "",
      detail: "roadmap.md not found",
    };
  }
  const text = fs.readFileSync(roadmapPath, "utf8");
  const match = text.match(/^## Active Now\n\n([\s\S]*?)(?:\n## |\n?$)/m);
  let activeNow = "";
  if (match) {
    activeNow = (match[1].split(/\r?\n/).find((line) => line.trim().startsWith("- [")) || "").trim();
  }
  return {
    status: activeNow ? "present" : "unknown",
    path: roadmapPath,
    active_now: activeNow,
    detail: activeNow ? "active slice found" : "no active slice found",
  };
}

function releaseReadinessStatus(gates, gitState) {
  const failed = gates.filter((gate) => gate.status === "failed").length;
  const missing = gates.filter((gate) => gate.status === "missing").length;
  if (failed > 0 || gitState.status === "dirty") {
    return "blocked";
  }
  if (missing > 0) {
    return "needs_evidence";
  }
  if (gitState.status === "unknown") {
    return "needs_review";
  }
  return "ready_for_release_review";
}

function buildReleaseReadinessView() {
  const stateDir = resolveStateDir();
  const records = readJsonl(verificationsPath());
  const gates = RELEASE_READINESS_GATES.map((gate) => summarizeReleaseGate(gate, records));
  const root = projectRootFromState(stateDir);
  const gitState = gitStatusSummary(root);
  const roadmap = roadmapSummary(root);
  const counts = countStatuses(gates, ["passed", "failed", "missing", "unknown"]);
  return {
    state_dir: stateDir,
    project_root: root,
    status: releaseReadinessStatus(gates, gitState),
    gates,
    counts: { total: gates.length, ...counts },
    project_state: {
      git: gitState,
      roadmap,
    },
    guardrail:
      "readiness summarizes recorded evidence and project state only; it does not rerun gates or declare a release safe",
  };
}

function formatReleaseGate(row) {
  const icon = RELEASE_READINESS_ICONS[row.status] || "[ ]";
  let line = `  ${icon} ${row.label}: ${row.status}`;
  const record = row.latest_record;
  if (record) {
    line += ` (exit ${record.exit_code}, ${record.timestamp || "unknown-time"})`;
  } else {
    line += " (no recorded executed verifier)";
  }
  line += `; sources: ${row.sources.join(", ")}`;
  return line;
}

function formatReleaseReadinessView(view) {
  const lines = [`[OK] Release readiness: ${view.state_dir}`];
  const counts = view.counts;
  lines.push(`Readiness: ${view.status}`);
  lines.push(
    `Recorded gates: ${counts.total} total; ${counts.passed || 0} passed, ` +
      `${counts.failed || 0} failed, ${counts.missing || 0} missing`
  );
  lines.push("Gates:");
  for (const gate of view.gates) {
    lines.push(formatReleaseGate(gate));
  }
  const gitState = view.project_state.git;
  const gitIcon = RELEASE_READINESS_ICONS[gitState.status] || "[~]";
  lines.push(
    `Project git: ${gitIcon} ${gitState.status}; branch=${gitState.branch || "unknown"}; ` +
      compactLabel(gitState.detail, "no detail")
  );
  const roadmap = view.project_state.roadmap;
  const roadmapIcon = RELEASE_READINESS_ICONS[roadmap.status] || "[~]";
  lines.push(
    `Roadmap: ${roadmapIcon} ${roadmap.status}; ` +
      compactLabel(roadmap.active_now, roadmap.detail)
  );
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

const TIMELINE_EVENT_ICONS = {
  job_created: "[ ]",
  task_started: "[>]",
  task_pending: "[ ]",
  task_finished: "[x]",
  task_failed: "[!]",
  task_interrupted: "[~]",
};

function selectedRecentFanoutJobs(fanoutJobs, recent) {
  if (recent <= 0) {
    return [];
  }
  return fanoutJobs.slice(Math.max(0, fanoutJobs.length - recent)).reverse();
}

function timelineEventTime(job, task, event) {
  if (event === "task_started") {
    return task.started_at || job.created || "";
  }
  if (["task_finished", "task_failed", "task_interrupted"].includes(event)) {
    return task.finished_at || job.last_updated || "";
  }
  return job.created || "";
}

function addTimelineEvent(events, job, task, event) {
  const status = task ? task.status || "pending" : job.status || "unknown";
  events.push({
    time: timelineEventTime(job, task || {}, event),
    event,
    job_id: job.id || "",
    job_purpose: job.purpose || "",
    task_id: task ? task.id : null,
    task_title: task ? task.title || "" : "",
    status,
    engine: (task ? task.engine : null) || job.engine || "",
    model: (task ? task.model : null) || job.model || "",
    duration_seconds: task ? task.duration_seconds || 0 : 0,
    error: task ? task.error || null : null,
    output_file: task ? task.output_file || null : null,
    output_bytes: task ? task.output_bytes || 0 : 0,
  });
}

function buildFanoutTimelineEvents(job) {
  const events = [
    {
      time: job.created || "",
      event: "job_created",
      job_id: job.id || "",
      job_purpose: job.purpose || "",
      task_id: null,
      task_title: "",
      status: job.status || "unknown",
      engine: job.engine || "",
      model: job.model || "",
      duration_seconds: 0,
      error: null,
      output_file: null,
      output_bytes: 0,
    },
  ];
  for (const task of job.tasks || []) {
    const status = task.status || "pending";
    if (status === "pending" && !task.started_at) {
      addTimelineEvent(events, job, task, "task_pending");
      continue;
    }
    addTimelineEvent(events, job, task, "task_started");
    if (status === "failed") {
      addTimelineEvent(events, job, task, "task_failed");
    } else if (status === "interrupted") {
      addTimelineEvent(events, job, task, "task_interrupted");
    } else if (status === "completed") {
      addTimelineEvent(events, job, task, "task_finished");
    }
  }
  return events;
}

function sortTimelineEvents(events) {
  return [...events].sort((left, right) => {
    const leftKey = `${left.time || "9999-12-31T23:59:59Z"}${left.job_id || ""}${left.task_id || 0}${left.event || ""}`;
    const rightKey = `${right.time || "9999-12-31T23:59:59Z"}${right.job_id || ""}${right.task_id || 0}${right.event || ""}`;
    return leftKey.localeCompare(rightKey);
  });
}

function buildFanoutTimelineView(recent = 5) {
  const fanoutJobs = listFanoutSummaries();
  const selectedJobs = selectedRecentFanoutJobs(fanoutJobs, recent);
  const selectedIds = new Set(selectedJobs.map((job) => job.id));
  let events = [];
  for (const job of fanoutJobs) {
    if (selectedIds.has(job.id)) {
      events = events.concat(buildFanoutTimelineEvents(job));
    }
  }
  const taskCounts = {
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
    interrupted: 0,
  };
  for (const job of fanoutJobs) {
    for (const [status, count] of Object.entries(job.task_counts || {})) {
      taskCounts[status] = (taskCounts[status] || 0) + count;
    }
  }
  const jobCounts = countStatuses(fanoutJobs, ["active", "completed", "failed", "interrupted", "empty"]);
  return {
    state_dir: resolveStateDir(),
    jobs: selectedJobs,
    events: sortTimelineEvents(events),
    counts: {
      fanout_jobs: { total: fanoutJobs.length, ...jobCounts },
      fanout_tasks: taskCounts,
      timeline_events: events.length,
    },
    guardrail: "timeline summarizes durable fanout state only; worker output is material, not verification evidence",
  };
}

function formatTimelineEvent(event) {
  const icon = TIMELINE_EVENT_ICONS[event.event] || "[ ]";
  const stamp = event.time || "unknown-time";
  const jobId = event.job_id || "unknown-job";
  if (event.event === "job_created") {
    return `  ${icon} ${stamp} ${jobId}: job created (${compactLabel(event.job_purpose, "fanout job")})`;
  }
  let detail =
    `  ${icon} ${stamp} ${jobId} task ${event.task_id}: ` +
    `${compactLabel(event.task_title, "task")} (${event.status || "unknown"}; ` +
    `engine=${event.engine || "unknown"}`;
  if (event.model) {
    detail += `; model=${event.model}`;
  }
  if (event.duration_seconds) {
    detail += `; duration=${event.duration_seconds}s`;
  }
  if (event.output_bytes) {
    detail += `; output=${event.output_bytes} bytes`;
  }
  detail += ")";
  if (event.error) {
    detail += `: ${compactLabel(event.error, "error")}`;
  }
  return detail;
}

function formatFanoutTimelineView(view) {
  const lines = [`[OK] Fanout timeline: ${view.state_dir}`];
  const jobs = view.counts.fanout_jobs;
  const tasks = view.counts.fanout_tasks;
  lines.push(
    `Fanout jobs: ${jobs.total} total; ${jobs.active || 0} active, ` +
      `${jobs.completed || 0} completed, ${jobs.failed || 0} failed, ` +
      `${jobs.interrupted || 0} interrupted`
  );
  lines.push(
    `Fanout tasks: ${tasks.running || 0} running, ${tasks.pending || 0} pending, ` +
      `${tasks.completed || 0} completed, ${tasks.failed || 0} failed, ` +
      `${tasks.interrupted || 0} interrupted`
  );
  if (view.events.length > 0) {
    lines.push("Timeline events:");
    for (const event of view.events) {
      lines.push(formatTimelineEvent(event));
    }
  } else {
    lines.push("No fanout timeline events found.");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

const PHASE_CONFIG = [
  {
    id: "understand",
    label: "Understand",
    keywords: [
      "understand",
      "map",
      "inspect",
      "research",
      "audit",
      "classify",
      "discover",
      "probe",
      "investigate",
      "analyze",
      "orient",
    ],
  },
  {
    id: "design",
    label: "Design",
    keywords: ["design", "plan", "spec", "contract", "architecture", "outline", "docs design"],
  },
  {
    id: "build",
    label: "Build",
    keywords: ["implement", "build", "add", "create", "update", "write", "edit", "refactor", "wire"],
  },
  {
    id: "judge",
    label: "Judge",
    keywords: ["judge", "review", "evaluate", "assess", "reflect", "decide"],
  },
  {
    id: "verify",
    label: "Verify",
    keywords: ["verify", "test", "check", "gate", "lint", "suite"],
  },
];

const PHASE_STATUS_ICONS = {
  empty: "[ ]",
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

function phaseIdForStep(step) {
  const title = step.title || "";
  for (const phase of PHASE_CONFIG) {
    if (containsAny(title, phase.keywords).length > 0) {
      return phase.id;
    }
  }
  const criteria = step.success_criteria || "";
  for (const phase of PHASE_CONFIG) {
    if (containsAny(criteria, phase.keywords).length > 0) {
      return phase.id;
    }
  }
  return "build";
}

function summarizePhaseStep(step) {
  return {
    id: step.id,
    title: step.title || "",
    status: step.status || "pending",
    success_criteria: step.success_criteria || "",
    result: step.result,
  };
}

function phaseStepCounts(steps) {
  return {
    total: steps.length,
    pending: steps.filter((step) => step.status === "pending").length,
    in_progress: steps.filter((step) => step.status === "in_progress").length,
    completed: steps.filter((step) => step.status === "completed").length,
    failed: steps.filter((step) => step.status === "failed").length,
    skipped: steps.filter((step) => step.status === "skipped").length,
  };
}

function phaseStatus(steps) {
  if (steps.length === 0) {
    return "empty";
  }
  const statuses = steps.map((step) => step.status || "pending");
  if (statuses.includes("in_progress")) {
    return "in_progress";
  }
  if (statuses.includes("failed")) {
    return "failed";
  }
  if (statuses.every((status) => status === "completed")) {
    return "completed";
  }
  if (statuses.every((status) => status === "skipped")) {
    return "skipped";
  }
  return "pending";
}

function phaseNextAction(steps) {
  for (const status of ["in_progress", "pending"]) {
    const step = steps.find((candidate) => candidate.status === status);
    if (step) {
      return `continue step ${step.id}: ${step.title}`;
    }
  }
  return null;
}

function buildPhaseEvidence(phaseId, dashboard, background) {
  const plan = dashboard.active_plan;
  const counts = dashboard.counts;
  const verification = dashboard.verification_summary;
  const reflections = dashboard.reflection_summary;
  const evidence = [];
  if (phaseId === "understand") {
    evidence.push(plan ? `active plan goal: ${plan.goal || ""}` : "active plan: none");
    evidence.push(
      `memory ${counts.memory}, lessons ${counts.project_lessons} project + ${counts.global_lessons} global`
    );
  } else if (phaseId === "design") {
    if (plan) {
      evidence.push(`plan progress ${plan.completed_steps}/${plan.total_steps} completed`);
      if (plan.next_pending_step) {
        evidence.push(`next pending step ${plan.next_pending_step.id}: ${plan.next_pending_step.title || ""}`);
      }
    } else {
      evidence.push("no active plan");
    }
  } else if (phaseId === "build") {
    const outcomes = background.counts.outcomes;
    const tasks = background.counts.fanout_tasks;
    evidence.push(`outcomes ${outcomes.total} total, ${outcomes.active || 0} active`);
    evidence.push(
      `fanout tasks ${tasks.running || 0} running, ${tasks.pending || 0} pending, ` +
        `${tasks.completed || 0} completed`
    );
  } else if (phaseId === "judge") {
    evidence.push(`reflections ${reflections.total} total`);
    if (reflections.recent.length > 0) {
      const latest = reflections.recent[reflections.recent.length - 1];
      evidence.push(`latest reflection: ${latest.outcome || "unknown"}; next ${latest.next || ""}`);
    }
  } else if (phaseId === "verify") {
    evidence.push(
      `executed checks ${verification.executed} total, ${verification.executed_passed} passed, ` +
        `${verification.executed_failed} failed`
    );
    evidence.push(`attested claims ${verification.attested}`);
    if (dashboard.active_outcome) {
      evidence.push(`active outcome ${dashboard.active_outcome.slug} is ${dashboard.active_outcome.status}`);
    }
  }
  return evidence;
}

function buildPhaseView(recent = 3) {
  const dashboard = buildWorkflowDashboard(recent);
  const background = buildBackgroundView(recent);
  const stepBuckets = Object.fromEntries(PHASE_CONFIG.map((phase) => [phase.id, []]));
  if (dashboard.active_plan) {
    for (const step of dashboard.active_plan.steps || []) {
      stepBuckets[phaseIdForStep(step)].push(summarizePhaseStep(step));
    }
  }
  const phases = PHASE_CONFIG.map((phase) => {
    const steps = stepBuckets[phase.id];
    return {
      id: phase.id,
      label: phase.label,
      status: phaseStatus(steps),
      steps,
      step_counts: phaseStepCounts(steps),
      evidence: buildPhaseEvidence(phase.id, dashboard, background),
      next_action: phaseNextAction(steps),
    };
  });
  return {
    state_dir: resolveStateDir(),
    active_plan: dashboard.active_plan,
    active_outcome: dashboard.active_outcome,
    phases,
    counts: {
      memory: dashboard.counts.memory,
      project_lessons: dashboard.counts.project_lessons,
      global_lessons: dashboard.counts.global_lessons,
      verifications: dashboard.counts.verifications,
      reflections: dashboard.counts.reflections,
      outcomes: background.counts.outcomes,
      fanout_jobs: background.counts.fanout_jobs,
      fanout_tasks: background.counts.fanout_tasks,
    },
    guardrail: "phase view summarizes durable state only; verification still requires executed checks",
  };
}

function formatPhaseView(view) {
  const lines = [`[OK] Phase view: ${view.state_dir}`];
  const plan = view.active_plan;
  if (plan) {
    lines.push(`Active plan: ${plan.slug} (${plan.completed_steps}/${plan.total_steps} completed)`);
    lines.push(`Goal: ${plan.goal || ""}`);
  } else {
    lines.push("Active plan: none");
  }
  lines.push("Phases:");
  for (const phase of view.phases) {
    const counts = phase.step_counts;
    const icon = PHASE_STATUS_ICONS[phase.status] || "[ ]";
    lines.push(
      `  ${icon} ${phase.label}: ${phase.status}; ${counts.total} plan steps ` +
        `(${counts.completed} completed, ${counts.in_progress} in progress, ${counts.pending} pending)`
    );
    for (const item of phase.evidence) {
      lines.push(`      evidence: ${item}`);
    }
    for (const step of phase.steps) {
      lines.push(`      step: ${PHASE_STATUS_ICONS[step.status] || "[ ]"} ${step.id}. ${step.title}`);
    }
    if (phase.next_action) {
      lines.push(`      next: ${phase.next_action}`);
    }
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
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
const MCP_WORKFLOW_ROUTE_NOTE =
  " This is the recommended first tool for broad, ambiguous, multi-step, review, research, one-shot, in-one-go, or recovery prompts.";

// ---------------------------------------------------------------------------
// Classification tool
// ---------------------------------------------------------------------------

server.registerTool(
  "classify_task",
  {
    title: "Classify a task before planning",
    description:
      "Classify a user request when you only need task type, risk, recommended Mythify ceremony level, execution profile, verification strategy, or fanout fit. " +
      "For broad or ambiguous user prompts, call workflow_route first so Mythify can choose the full workflow path before this lower-level classification primitive is used.",
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
      reviewer_strength: z
        .enum(REVIEWER_STRENGTH_MODES)
        .optional()
        .describe(
          "Reviewer model strength relative to the session. Auto uses MYTHIFY_REVIEWER_STRENGTH or same_or_lower; allow_stronger is a reviewer-only opt-in."
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
    reviewer_strength,
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
      host_model_record: readHostModelState(),
      spawn_ceiling: spawn_ceiling || "auto",
      reviewer_strength: reviewer_strength || "auto",
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
        cwd: path.dirname(resolveStateDir()),
      });
    }
    if (format === "json") {
      return "[OK] " + JSON.stringify(result, null, 2);
    }
    return formatClassification(result);
  })
);

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
  buildOutcomeProgressView,
  formatOutcomeProgressView,
  buildReleaseReadinessView,
  formatReleaseReadinessView,
  buildFanoutTimelineView,
  formatFanoutTimelineView,
  buildPhaseView,
  formatPhaseView,
});

server.registerTool(
  "campaign_next_prompt",
  {
    title: "Render campaign next prompt",
    description:
      "Render a chat-ready next prompt for the active or named campaign's current task and phase. " +
      "Use this when a host wants Mythify campaign guidance inside the chat without mutating state, running checks, or treating prompt material as verification evidence." +
      MCP_FRONT_DOOR_NOTE,
    inputSchema: {
      name: z.string().optional().describe("Campaign name. Defaults to the active campaign."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ name, format }) => {
    const [slug, record] = loadCampaign(name || "");
    if (!record) {
      return "[FAIL] Campaign not found. Start one with: campaign start GOAL";
    }
    const payload = buildCampaignPromptPayload(slug, record);
    if (format === "json") {
      return `[OK] ${JSON.stringify(payload, null, 2)}`;
    }
    return formatCampaignPromptPayload(payload);
  })
);

server.registerTool(
  "prompt_packet",
  {
    title: "Render workflow prompt packet",
    description:
      "Render a chat-ready prompt packet for research, analysis, failure recovery, handoff, review, campaign, or the next useful workflow move. " +
      "Use this when a host wants Mythify guidance inside the chat without mutating state, running checks, or treating prompt material as verification evidence." +
      MCP_FRONT_DOOR_NOTE,
    inputSchema: {
      kind: z
        .enum(PROMPT_PACKET_KINDS)
        .default("next")
        .describe("Packet kind: research, analysis, failure, handoff, review, campaign, or next."),
      name: z.string().optional().describe("Research or campaign name. Defaults to the active record."),
      goal: z.string().optional().describe("Optional host goal to include in the packet."),
      verify_command: z.string().optional().describe("Optional verifier command to include in the packet."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ kind, name, goal, verify_command, format }) => {
    const payload = buildPromptPacket(kind || "next", {
      name: name || "",
      goal: goal || "",
      verifyCommand: verify_command || "",
    });
    if (payload.error) {
      return payload.error;
    }
    if (format === "json") {
      return `[OK] ${JSON.stringify(payload, null, 2)}`;
    }
    return formatPromptPacket(payload);
  })
);

server.registerTool(
  "workflow_route",
  {
    title: "Choose workflow route",
    description:
      "Read-only workflow quarterback. Classify a prompt, inspect durable Mythify state, and choose direct, plan, research, review, outcome, campaign, failure recovery, handoff, or prompt packet routing. " +
      "Use this when the host wants Mythify to steer the next chat-native workflow move without mutating state or treating route output as verification evidence." +
      MCP_WORKFLOW_ROUTE_NOTE,
    inputSchema: {
      task: z.string().describe("The user request or problem statement to route."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
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
        .describe("Host platform for model policy. Defaults to auto."),
      effort: z
        .enum(EFFORT_LEVELS)
        .optional()
        .describe("Overall effort preference for spawned model roles."),
      speed: z
        .enum(SPEED_LEVELS)
        .optional()
        .describe("Overall speed preference for spawned model roles."),
      session_model: z
        .string()
        .optional()
        .describe("Current host session model for spawn ceiling policy. Defaults to MYTHIFY_SESSION_MODEL."),
      spawn_ceiling: z
        .enum(SPAWN_CEILINGS)
        .optional()
        .describe("Maximum spawned model tier relative to the session model."),
      reviewer_strength: z
        .enum(REVIEWER_STRENGTH_MODES)
        .optional()
        .describe("Reviewer model strength relative to the session."),
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
    reviewer_strength,
  }) => {
    const classification = classifyTaskText(task);
    classification.model_policy = buildModelPolicy(classification, {
      triage_engine: triage_engine || "",
      triage_model: triage_model || "",
      triage_timeout_seconds,
      platform: platform || "auto",
      effort: effort || "auto",
      speed: speed || "auto",
      session_model: session_model || "",
      host_model_record: readHostModelState(),
      spawn_ceiling: spawn_ceiling || "auto",
      reviewer_strength: reviewer_strength || "auto",
    });
    if ((triage || "never") !== "never") {
      classification.model_triage_run = runModelTriage(task, classification, {
        triage: triage || "never",
        triage_engine: triage_engine || "",
        triage_model: triage_model || "",
        triage_timeout_seconds,
        platform: platform || "auto",
        effort: effort || "auto",
        speed: speed || "auto",
        session_model: session_model || "",
        spawn_ceiling: spawn_ceiling || "auto",
        cwd: path.dirname(resolveStateDir()),
      });
    }
    const payload = buildWorkflowRoute(task, classification);
    if (format === "json") {
      return `[OK] ${JSON.stringify(payload, null, 2)}`;
    }
    return formatWorkflowRoute(payload);
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
      "Use this at the start of any multi-step task so progress is tracked outside the context window; trivial single-edit tasks do not need a plan." +
      MCP_FRONT_DOOR_NOTE,
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
      "By default, completed also requires a passing verify_run since the step started; set MYTHIFY_REQUIRE_VERIFIED_STEP=0 only for legacy prose-only completion. " +
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
    if (status === "completed" && strictStepEvidenceEnabled()) {
      const lowerBound =
        typeof step.updated_at === "string" && step.updated_at !== ""
          ? step.updated_at
          : plan.created;
      const verifications = readJsonlSince(verificationsPath(), lowerBound);
      const hasPassingRun = verifications.some(
        (record) =>
          record &&
          record.kind === "executed" &&
          record.verified === true &&
          typeof record.timestamp === "string" &&
          verificationRecordMatchesStep(record, slug, step_id) &&
          timestampAtOrAfter(
            record.timestamp,
            lowerBound,
            verificationRecordHasExplicitStepContext(record, slug, step_id)
          )
      );
      if (!hasPassingRun) {
        return (
          "[FAIL] Verified evidence required: strict evidence mode is enabled by default, but no passing 'verify run' " +
          "was recorded since this step started. Run 'verify run' with a passing check first, or set " +
          "MYTHIFY_REQUIRE_VERIFIED_STEP=0 to use legacy prose-only completion."
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
      "The host agent performs bounded attempts between outcome_check calls; Mythify records evidence and decides whether to retry, stop, or report success." +
      MCP_FRONT_DOOR_NOTE,
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
        .describe("Optional advisory path hints for host edits; recorded for policy, not enforced as a sandbox."),
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
    const run = runShellCapture(command, timeoutSeconds);
    const stdoutTail = run.stdout_tail;
    const stderrTail = run.stderr_tail;
    const exitCode = run.exit_code;
    const verified = run.verified;
    const record = {
      kind: "executed",
      claim: claim !== undefined && claim !== null ? claim : null,
      command,
      exit_code: exitCode,
      duration_seconds: run.duration_seconds,
      stdout_tail: stdoutTail,
      stderr_tail: stderrTail,
      verified,
      timestamp: isoNow(),
      ...verificationStepContext(),
    };
    appendJsonl(verificationsPath(), record);
    const label = record.claim !== null ? record.claim : command;
    const timing = `(exit ${exitCode}, ${run.duration_seconds.toFixed(2)}s)`;
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
