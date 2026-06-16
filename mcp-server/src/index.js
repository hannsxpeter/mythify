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
import { registerFanoutTools } from "./fanout.js";
import {
  ADAPTER_CANDIDATES,
  ADAPTER_INTERFACE_FIELDS,
  ADAPTER_INTERFACE_LANES,
  ADAPTER_INTERFACE_VERSION,
  EFFORT_LEVELS,
  FANOUT_VISIBILITY_MODES,
  HOST_MODEL_DEFAULTS,
  HOST_PLATFORMS as PLATFORMS,
  HOST_PROFILE_RANK,
  HOSTED_PROVIDER_FANOUT_ENGINES,
  HOSTED_PROVIDER_REQUIRED_ACKS,
  HOST_THINKING_LEVELS,
  MODEL_TIER_RANK,
  ROLE_PROVIDER_ALLOWED,
  ROLE_PROVIDER_DEFAULTS,
  ROLE_PROVIDER_ENV_NAMES,
  ROLE_PROVIDER_FALLBACK_POLICY,
  ROLE_PROVIDER_PROFILES,
  ROLE_COST_METADATA_FIELDS,
  ROLE_TIMEOUT_DEFAULTS,
  ROLE_TIMEOUT_METADATA_FIELDS,
  REVIEWER_STRENGTH_MODES,
  SPAWN_CEILINGS,
  SPEED_LEVELS,
  STRONG_HOST_TASK_TYPES,
  TRIAGE_ENGINES,
  TRIAGE_MODES,
  buildAdapterInterfaceCatalog,
  getHostCapability,
} from "./capability-registry.js";
import {
  MEMORY_CATEGORIES,
  MEMORY_CLEAR_MCP_REFUSAL,
  MEMORY_DEFAULT_CATEGORY,
} from "./operation-registry.js";

const PACKAGE_JSON = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const VERSION = PACKAGE_JSON.version;
const CLASSIFICATION_RULES_PATH = new URL("../protocol/classification-rules.json", import.meta.url);
const WORKFLOW_ROUTER_PATH = new URL("../protocol/workflow-router.json", import.meta.url);
const TAIL_CHARS = 4000;
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
const REPORT_SINCE_MODES = ["last", "start"];
const REPORT_FORMATS = ["chat", "json"];
const DEFAULT_REPORT_RECENT = 8;
const DEFAULT_REPORT_ATTENTION = 5;
const STEP_ICONS = {
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

function loadClassificationRules() {
  const manifest = JSON.parse(fs.readFileSync(CLASSIFICATION_RULES_PATH, "utf8"));
  const rules = [];
  const seen = new Set();
  for (const entry of manifest.task_types || []) {
    const taskType = String(entry?.id || "").trim();
    const terms = entry?.terms;
    if (!taskType || seen.has(taskType) || !Array.isArray(terms) || terms.length === 0) {
      throw new Error("Invalid classification rule entry");
    }
    seen.add(taskType);
    rules.push([taskType, terms.map(String)]);
  }
  if (rules.length === 0) {
    throw new Error("Classification rules manifest is empty");
  }
  return rules;
}

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

const CLASSIFICATION_RULES = loadClassificationRules();
const WORKFLOW_ROUTER = loadWorkflowRouter();
const WORKFLOW_ROUTE_IDS = WORKFLOW_ROUTER.routes.map((route) => String(route.id));
const WORKFLOW_ROUTE_PROMPTS = Object.fromEntries(
  WORKFLOW_ROUTER.routes.map((route) => [String(route.id), String(route.prompt_packet || "next")])
);

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
    if (fs.existsSync(campaignPath(raw))) {
      return raw;
    }
    const candidate = slugify(raw);
    if (candidate && fs.existsSync(campaignPath(candidate))) {
      return candidate;
    }
    return null;
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
    if (fs.existsSync(researchPath(raw))) {
      return raw;
    }
    const candidate = slugify(raw);
    if (candidate && fs.existsSync(researchPath(candidate))) {
      return candidate;
    }
    return null;
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

function hostAdapterProofStatus(capability, key) {
  if (capability.status === "unknown") {
    return "unknown";
  }
  return capability[key] ? "supported" : "unsupported";
}

function buildHostAdapterProofPath(capability, key, currentChat) {
  return {
    status: hostAdapterProofStatus(capability, key),
    proof_source: `host_capability.${key}`,
    current_chat_path: Boolean(currentChat),
    requires_executed_host_evidence: true,
  };
}

function buildHostAdapterProofScan(platform, capability, checkedAt) {
  return {
    status: "metadata_only",
    platform,
    proof_source: "host_capability_registry",
    checked_at: checkedAt || "",
    host_state_mutated: false,
    writes_state: false,
    verification_recorded: false,
    material_not_evidence: true,
    guardrail: "current_chat_apply_or_confirm_requires_executed_host_evidence",
    paths: {
      current_chat_model_apply: buildHostAdapterProofPath(
        capability,
        "can_switch_current_thread",
        true
      ),
      current_chat_model_confirm: buildHostAdapterProofPath(
        capability,
        "can_confirm_current_model",
        true
      ),
      new_thread_model_apply: buildHostAdapterProofPath(
        capability,
        "can_set_new_thread_model",
        false
      ),
      worker_model_apply: buildHostAdapterProofPath(
        capability,
        "can_set_worker_model",
        false
      ),
      thinking_apply: buildHostAdapterProofPath(capability, "can_set_thinking", false),
    },
  };
}

function buildHostSwitchResult(platform, targetModel, currentModel, thinking, speed, capability) {
  return {
    status: "manual",
    requested_model: targetModel,
    requested_thinking: thinking,
    requested_speed: speed,
    current_model: currentModel,
    current_thinking: "",
    current_chat_supported: Boolean(capability.can_switch_current_thread),
    current_chat_confirmed: false,
    manual_action_required: true,
    applied_by: "none",
    reason: "host_current_chat_unconfirmed",
  };
}

function buildHostConfirmation(targetModel, currentModel, thinking, capability, checkedAt) {
  const canConfirm = Boolean(capability.can_confirm_current_model);
  return {
    requested_model: targetModel,
    user_reported_current_model: currentModel,
    user_reported_current_thinking: thinking !== "auto" ? thinking : "",
    current_model_confirmed: false,
    confirmed_current_model: "",
    confirmed_current_thinking: "",
    confirmation_status: canConfirm ? "unconfirmed" : "unsupported",
    confirmation_source: "none",
    confirmation_checked_at: checkedAt || "",
    confirmed_at: "",
    unsupported_reason: canConfirm
      ? "host_adapter_has_not_confirmed_current_model"
      : "host_capability_cannot_confirm_current_model",
  };
}

function withHostCapability(record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return record;
  }
  const platform = String(record.platform || "unknown").trim() || "unknown";
  const capability = record.host_capability && typeof record.host_capability === "object"
    ? record.host_capability
    : hostCapabilityForRecord(platform);
  const thinking = normalizeHostThinking(record.thinking || "auto");
  const speed = normalizeHostSpeed(record.speed || "auto");
  const targetModel = String(record.target_model || "").trim();
  const currentModel = String(record.current_model || "").trim();
  return {
    ...record,
    host_capability: capability,
    can_apply_current_chat: false,
    switch_result:
      record.switch_result && typeof record.switch_result === "object"
        ? record.switch_result
        : buildHostSwitchResult(
            platform,
            targetModel,
            currentModel,
            thinking,
            speed,
            capability
          ),
    host_confirmation:
      record.host_confirmation && typeof record.host_confirmation === "object"
        ? record.host_confirmation
        : buildHostConfirmation(
            targetModel,
            currentModel,
            thinking,
            capability,
            String(record.updated || "")
          ),
    adapter_proof_scan:
      record.adapter_proof_scan && typeof record.adapter_proof_scan === "object"
        ? record.adapter_proof_scan
        : buildHostAdapterProofScan(platform, capability, String(record.updated || "")),
  };
}

function buildHostModelRecord({ platform, target_model, current_model, thinking, speed, reason }) {
  const targetModel = String(target_model || "").trim();
  const resolvedPlatform = detectHostPlatform(platform || "auto");
  const resolvedThinking = normalizeHostThinking(thinking || "auto");
  const resolvedSpeed = normalizeHostSpeed(speed || "auto");
  const currentModel = String(current_model || "").trim();
  const actions = hostSwitchActions(resolvedPlatform, targetModel, resolvedThinking, resolvedSpeed);
  const capability = hostCapabilityForRecord(resolvedPlatform);
  const updated = isoNow();
  return {
    platform: resolvedPlatform,
    requested_platform: normalizeHostPlatform(platform || "auto"),
    target_model: targetModel,
    current_model: currentModel,
    target_model_tier: classifyModelTier(targetModel),
    thinking: resolvedThinking,
    speed: resolvedSpeed,
    reason: String(reason || "").trim(),
    status: "recorded_requires_host_action",
    control: "host_selected",
    can_apply_current_chat: false,
    host_capability: capability,
    switch_result: buildHostSwitchResult(
      resolvedPlatform,
      targetModel,
      currentModel,
      resolvedThinking,
      resolvedSpeed,
      capability
    ),
    host_confirmation: buildHostConfirmation(
      targetModel,
      currentModel,
      resolvedThinking,
      capability,
      updated
    ),
    adapter_proof_scan: buildHostAdapterProofScan(resolvedPlatform, capability, updated),
    updated,
    host_actions: actions,
  };
}

function formatBool(value) {
  return value ? "yes" : "no";
}

function formatHostModelRecord(record) {
  const withCapability = withHostCapability(record);
  const capability = withCapability.host_capability || hostCapabilityForRecord(withCapability.platform);
  const switchResult = withCapability.switch_result || {};
  const confirmation = withCapability.host_confirmation || {};
  const proof = withCapability.adapter_proof_scan || {};
  const proofPaths = proof.paths || {};
  const lines = [
    `[OK] Host model switch ${withCapability.status}.`,
    `platform: ${withCapability.platform}`,
    `target model: ${withCapability.target_model} (tier ${withCapability.target_model_tier})`,
    `current model: ${withCapability.current_model || "unknown"}`,
    `host-confirmed model: ${confirmation.confirmed_current_model || confirmation.confirmation_status || "unsupported"}`,
    `confirmation source: ${confirmation.confirmation_source || "none"}`,
    `adapter proof scan: ${proof.status || "metadata_only"}`,
    `current-chat apply proof: ${proofPaths.current_chat_model_apply?.status || "unknown"}`,
    `current-chat confirm proof: ${proofPaths.current_chat_model_confirm?.status || "unknown"}`,
    `new-thread model proof: ${proofPaths.new_thread_model_apply?.status || "unknown"}`,
    `worker model proof: ${proofPaths.worker_model_apply?.status || "unknown"}`,
    `thinking proof: ${proofPaths.thinking_apply?.status || "unknown"}`,
    `thinking: ${withCapability.thinking}`,
    `speed: ${withCapability.speed}`,
    `switch status: ${switchResult.status || "manual"}`,
    `current-chat confirmed: ${formatBool(switchResult.current_chat_confirmed)}`,
    `manual action required: ${formatBool(switchResult.manual_action_required !== false)}`,
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

function buildReportEvents() {
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
  const verifications = readJsonl(verificationsPath());
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
  const reflections = readJsonl(reflectionsPath());
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
  const allEvents = buildReportEvents();
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
    const roles = policy.provider_defaults?.roles || {};
    if (Object.keys(roles).length > 0) {
      lines.push(
        `providers: session=${roles.session?.provider || "host"}; ` +
        `triage=${roles.triage?.provider || "host_cli"}; ` +
        `reader=${roles.reader?.provider || "local_openai_compatible"}; ` +
        `worker=${roles.fanout_worker?.provider || "host_cli"}; ` +
        `reviewer=${roles.reviewer?.provider || "host_cli"}; ` +
        `verifier=${roles.verifier?.provider || "local_command"}`
      );
    }
    lines.push(
      `model policy: session=${policy.session?.control || "host_selected"}/${policy.session?.model_tier || "unknown"}; ` +
      `ceiling=${policy.spawn_ceiling?.policy || "same_or_lower"}; ` +
      `triage=${policy.triage?.engine || "auto"}/${policy.triage?.model_policy || "engine_default"}/${policy.triage?.effort || "low"}/${policy.triage?.speed || "auto"}; ` +
      `fanout=${policy.fanout_worker?.engine_policy || "local_first"}/${policy.fanout_worker?.effort || "medium"}/${policy.fanout_worker?.speed || "auto"}/${policy.fanout_worker?.visibility || "summary"}; ` +
      `verifier=${policy.verifier?.engine || "local_command"}`
    );
    lines.push(
      `reviewer opt-in: ${policy.reviewer?.stronger_model_policy || "same_or_lower"} ` +
      `(${policy.reviewer?.stronger_model_policy_source || "default"})`
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

function selectWorkerEngine(platform) {
  const envEngine = (process.env.MYTHIFY_FANOUT_ENGINE || "").trim();
  if (envEngine !== "") {
    return { engine: envEngine, enginePolicy: "env" };
  }
  const preferred = preferredLocalEngine(platform);
  if (preferred !== "" && triageEngineAvailable(preferred)) {
    return { engine: preferred, enginePolicy: "platform_preferred" };
  }
  return { engine: "auto", enginePolicy: "local_first" };
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

const ROLE_PROVIDER_ORDER = [
  "session",
  "triage",
  "reader",
  "fanout_worker",
  "reviewer",
  "verifier",
];
const ROLE_ASSIGNMENT_VERSION = 1;
const ROLE_ASSIGNMENT_ADAPTER_LANES = {
  session: ["host"],
  triage: ["host", "model_provider", "custom_adapter"],
  reader: ["host", "model_provider"],
  fanout_worker: ["host", "api_provider", "custom_adapter"],
  reviewer: ["host", "api_provider", "custom_adapter"],
  verifier: [],
};
const ROLE_ASSIGNMENT_EXTRA_ROLES = {
  remote_execution: {
    status: "metadata_supported",
    default_provider: null,
    selected_provider: null,
    provider_source: "not_enabled",
    allowed_providers: [],
    eligible_adapter_lanes: ["execution_substrate"],
    adapter_interface_role: "remote_execution",
    assignment_order: ["future_explicit_role_input", "built_in_disabled"],
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    execution_policy: "guarded_explicit_acknowledgement_only",
    runtime_routing_changed: false,
    writes_state_allowed: false,
    material_not_evidence_required: true,
    required_evidence_status: "remote_output_not_verification",
    required_acknowledgements: [
      "billing_ack_required",
      "data_movement_ack_required",
      "cleanup_ack_required",
    ],
    guardrails: [
      ROLE_PROVIDER_FALLBACK_POLICY,
      "explicit_acknowledgements_required",
      "material_not_verification",
      "no_mythify_state_write",
    ],
  },
  agent_lifecycle: {
    status: "metadata_supported",
    default_provider: null,
    selected_provider: null,
    provider_source: "not_enabled",
    allowed_providers: [],
    eligible_adapter_lanes: ["agent_lifecycle"],
    adapter_interface_role: "agent_lifecycle",
    assignment_order: ["future_explicit_role_input", "built_in_disabled"],
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    execution_policy: "probe_only_no_eval_or_deploy",
    runtime_routing_changed: false,
    writes_state_allowed: false,
    material_not_evidence_required: true,
    required_evidence_status: "lifecycle_probe_output_not_verification",
    guardrails: [
      ROLE_PROVIDER_FALLBACK_POLICY,
      "probe_only",
      "no_eval_execution",
      "no_deploy",
      "no_publish",
      "no_cloud_mutation",
      "no_mythify_state_write",
      "material_not_verification",
    ],
  },
};

function resolveRoleProvider(role) {
  const defaultProvider = ROLE_PROVIDER_DEFAULTS[role];
  const allowed = ROLE_PROVIDER_ALLOWED[role] || [];
  const envName = ROLE_PROVIDER_ENV_NAMES[role];
  const requested = (process.env[envName] || "").trim();
  let provider = defaultProvider;
  let source = "built_in";
  let status = "selected";
  if (requested !== "") {
    if (allowed.includes(requested)) {
      provider = requested;
      source = `env:${envName}`;
    } else {
      status = "invalid_env_ignored";
    }
  }
  return {
    role,
    provider,
    provider_source: source,
    default_provider: defaultProvider,
    allowed_providers: allowed,
    requested_provider: requested || null,
    status,
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    provider_profile: ROLE_PROVIDER_PROFILES[provider] || {},
    selection: "advisory_metadata_only",
  };
}

function roleProviderCatalog() {
  const catalog = {};
  for (const [provider, profile] of Object.entries(ROLE_PROVIDER_PROFILES).sort(([left], [right]) =>
    left.localeCompare(right)
  )) {
    catalog[provider] = {
      ...profile,
      fallback_policy: profile.fallback_policy || ROLE_PROVIDER_FALLBACK_POLICY,
    };
  }
  return catalog;
}

function apiProviderContract() {
  const providers = {};
  for (const [name, candidate] of Object.entries(ADAPTER_CANDIDATES).sort(([left], [right]) =>
    left.localeCompare(right)
  )) {
    if (candidate.kind !== "api_provider") {
      continue;
    }
    providers[name] = {
      status: candidate.status,
      protocol: candidate.protocol || "unknown",
      openai_compatible: Boolean(candidate.openai_compatible),
      default_base_url: candidate.default_base_url || "",
      base_url_env: candidate.base_url_env || "",
      api_key_env: candidate.api_key_env || "",
      model_env: candidate.model_env || "",
      auth_header: candidate.auth_header || "",
      version_header: candidate.version_header || "",
      billing: candidate.billing || "unknown",
      explicit_enable_required: candidate.explicit_enable_required === true,
      execution_enabled: candidate.can_run_api_worker === true,
      default_timeout_seconds: candidate.default_timeout_seconds || 600,
      cost_metadata_supported: candidate.cost_metadata_supported === true,
      pricing_url: candidate.pricing_url || "",
      pricing_url_env: candidate.pricing_url_env || "",
      fallback_policy: candidate.fallback_policy || ROLE_PROVIDER_FALLBACK_POLICY,
    };
  }
  return {
    version: 1,
    status: "metadata_supported",
    execution_enabled: false,
    fanout_execution_enabled: true,
    fanout_engines: HOSTED_PROVIDER_FANOUT_ENGINES,
    required_fanout_acknowledgements: HOSTED_PROVIDER_REQUIRED_ACKS,
    fanout_audit_log: ".mythify/provider-audit.jsonl",
    fanout_output_material_status: "material_not_verification",
    billing_policy: "explicit_provider_required",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    timeout_metadata_fields: ["provider", "timeout_seconds", "timeout_source"],
    cost_metadata_fields: [
      "provider",
      "model",
      "input_tokens",
      "cached_input_tokens",
      "output_tokens",
      "pricing_url",
    ],
    providers,
  };
}

function customAdapterContract() {
  const command = ADAPTER_CANDIDATES["custom-command"] || {};
  const http = ADAPTER_CANDIDATES["custom-http"] || {};
  return {
    version: 1,
    status: "metadata_supported",
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    execution_policy: "explicit_only_no_hidden_fallback",
    evidence_status: "adapter_output_not_verification",
    command: {
      adapter: "custom-command",
      status: command.status || "bounded_execution_supported",
      execution_enabled: command.execution_enabled === true,
      tools: ["classify_task triage_engine=command", "fanout_start engine=command"],
      command_env: command.command_env || ["MYTHIFY_TRIAGE_COMMAND", "MYTHIFY_FANOUT_COMMAND"],
      input_contract: command.input_contract || "prompt_on_stdin",
      default_timeout_seconds: {
        triage: ROLE_TIMEOUT_DEFAULTS.triage.timeout_seconds,
        fanout_worker: ROLE_TIMEOUT_DEFAULTS.fanout_worker.timeout_seconds,
        reviewer: ROLE_TIMEOUT_DEFAULTS.reviewer.timeout_seconds,
      },
      billing: command.billing || "user_defined",
      writes_state: command.writes_state === true,
      output_is_evidence: command.output_is_evidence === true,
      evidence_status: command.evidence_status || "command_output_not_verification",
    },
    http: {
      adapter: "custom-http",
      status: http.status || "metadata_only",
      execution_enabled: http.execution_enabled === true,
      explicit_enable_required: http.explicit_enable_required === true,
      base_url_env: http.base_url_env || "MYTHIFY_CUSTOM_HTTP_BASE_URL",
      api_key_env: http.api_key_env || "MYTHIFY_CUSTOM_HTTP_API_KEY",
      model_env: http.model_env || "MYTHIFY_CUSTOM_HTTP_MODEL",
      pricing_url_env: http.pricing_url_env || "MYTHIFY_CUSTOM_HTTP_PRICING_URL",
      required_before_execution: [
        "method_allowlist",
        "auth_from_env_only",
        "bounded_timeout",
        "request_body_template",
        "response_extraction",
        "cost_metadata",
        "no_state_write",
        "material_not_evidence",
      ],
      billing: http.billing || "metered_external_account_or_user_defined",
      writes_state: http.writes_state === true,
      output_is_evidence: http.output_is_evidence === true,
      evidence_status: http.evidence_status || "http_output_not_verification",
    },
  };
}

function adapterInterfaceContract() {
  return {
    version: ADAPTER_INTERFACE_VERSION,
    status: "metadata_supported",
    fields: ADAPTER_INTERFACE_FIELDS,
    lanes: ADAPTER_INTERFACE_LANES,
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    execution_policy: "metadata_shape_only_no_runtime_change",
    guardrail: "interface_does_not_enable_fallback_or_state_writes",
    candidates: buildAdapterInterfaceCatalog(),
  };
}

function roleAssignmentCandidateGroups(adapterRole, lanes, catalog) {
  const eligible = [];
  const executionEnabled = [];
  const metadataOnly = [];
  for (const [id, candidate] of Object.entries(catalog)) {
    if (!lanes.includes(candidate.kind) || !(candidate.roles || []).includes(adapterRole)) {
      continue;
    }
    eligible.push(id);
    if (candidate.execution_enabled === true) {
      executionEnabled.push(id);
    } else {
      metadataOnly.push(id);
    }
  }
  return {
    eligible_candidate_ids: eligible,
    execution_enabled_candidate_ids: executionEnabled,
    metadata_only_candidate_ids: metadataOnly,
  };
}

function roleAssignmentCoreContract(role, resolvedRole, catalog) {
  const profile = resolvedRole.provider_profile || ROLE_PROVIDER_PROFILES[resolvedRole.provider] || {};
  const evidenceStatus = profile.evidence_status || "unknown";
  const guardrails = [
    ROLE_PROVIDER_FALLBACK_POLICY,
    "advisory_metadata_only",
    "no_hidden_provider_fallback",
  ];
  if (evidenceStatus !== "executed_verification") {
    guardrails.push("material_not_verification");
  }
  if (role === "reviewer") {
    guardrails.push("stronger_model_requires_explicit_opt_in");
  }
  const record = {
    role,
    status: "metadata_supported",
    default_provider: resolvedRole.default_provider,
    selected_provider: resolvedRole.provider,
    provider_source: resolvedRole.provider_source,
    allowed_providers: resolvedRole.allowed_providers,
    eligible_adapter_lanes: ROLE_ASSIGNMENT_ADAPTER_LANES[role],
    adapter_interface_role: role === "session" ? "host_session" : role,
    assignment_order: ["future_explicit_role_input", "env", "built_in"],
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    execution_policy: "advisory_metadata_only_no_runtime_routing",
    runtime_routing_changed: false,
    writes_state_allowed: profile.writes_state === true,
    material_not_evidence_required: evidenceStatus !== "executed_verification",
    required_evidence_status: evidenceStatus,
    guardrails,
  };
  if (role === "reviewer") {
    record.stronger_model_policy = "explicit_opt_in_required";
  }
  return {
    ...record,
    ...roleAssignmentCandidateGroups(record.adapter_interface_role, record.eligible_adapter_lanes, catalog),
  };
}

function roleAssignmentExtraContract(record, catalog) {
  return {
    ...record,
    allowed_providers: [...record.allowed_providers],
    eligible_adapter_lanes: [...record.eligible_adapter_lanes],
    assignment_order: [...record.assignment_order],
    required_acknowledgements: record.required_acknowledgements
      ? [...record.required_acknowledgements]
      : undefined,
    guardrails: [...record.guardrails],
    ...roleAssignmentCandidateGroups(record.adapter_interface_role, record.eligible_adapter_lanes, catalog),
  };
}

function roleAssignmentContract(resolvedRoles) {
  const catalog = buildAdapterInterfaceCatalog();
  const roles = {};
  for (const role of ROLE_PROVIDER_ORDER) {
    roles[role] = roleAssignmentCoreContract(role, resolvedRoles[role], catalog);
  }
  for (const [role, record] of Object.entries(ROLE_ASSIGNMENT_EXTRA_ROLES)) {
    roles[role] = roleAssignmentExtraContract(record, catalog);
  }
  return {
    version: ROLE_ASSIGNMENT_VERSION,
    status: "metadata_supported",
    source: "adapter_interface_contract",
    assignment_order: ["future_explicit_role_input", "env", "built_in"],
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    execution_policy: "metadata_shape_only_no_runtime_change",
    runtime_routing_changed: false,
    guardrail: "role_contract_does_not_enable_hidden_fallback",
    candidate_id_source: "mcp_adapter_interface_catalog",
    roles,
  };
}

function buildProviderDefaults() {
  const roles = {};
  for (const role of ROLE_PROVIDER_ORDER) {
    roles[role] = resolveRoleProvider(role);
  }
  return {
    version: 1,
    precedence: ["future_explicit_role_input", "env", "built_in"],
    fallback_policy: ROLE_PROVIDER_FALLBACK_POLICY,
    timeout_metadata_fields: ROLE_TIMEOUT_METADATA_FIELDS,
    cost_metadata_fields: ROLE_COST_METADATA_FIELDS,
    provider_catalog: roleProviderCatalog(),
    adapter_interface_contract: adapterInterfaceContract(),
    role_assignment_contract: roleAssignmentContract(roles),
    api_provider_contract: apiProviderContract(),
    custom_adapter_contract: customAdapterContract(),
    roles,
  };
}

function roleProviderFields(providerDefaults, role) {
  const provider = providerDefaults.roles[role];
  return {
    provider: provider.provider,
    provider_source: provider.provider_source,
    provider_default: provider.default_provider,
    provider_status: provider.status,
    provider_fallback_policy: provider.fallback_policy,
    provider_profile: provider.provider_profile || {},
  };
}

function roleTimeoutMetadata(role, timeoutSeconds, timeoutSource) {
  const metadata = { ...(ROLE_TIMEOUT_DEFAULTS[role] || {}) };
  if (timeoutSeconds !== undefined) {
    metadata.timeout_seconds = timeoutSeconds;
  }
  if (timeoutSource !== undefined) {
    metadata.timeout_source = timeoutSource;
  }
  return metadata;
}

function roleCostMetadata(providerDefaults, role, pricingUrl = "") {
  const providerRecord = providerDefaults.roles[role];
  const provider = providerRecord.provider;
  const profile = providerRecord.provider_profile || {};
  return {
    billing: profile.billing || "unknown",
    cost_estimate_supported: false,
    cost_estimate_status: "not_estimated",
    cost_estimate_cents: null,
    pricing_url: pricingUrl,
    usage_metadata_fields: provider === "api_provider" ? apiProviderContract().cost_metadata_fields : [],
  };
}

function roleBudgetFields(providerDefaults, role, timeoutSeconds, timeoutSource, pricingUrl = "") {
  return {
    timeout: roleTimeoutMetadata(role, timeoutSeconds, timeoutSource),
    cost: roleCostMetadata(providerDefaults, role, pricingUrl),
  };
}

function buildModelPolicy(classification, options) {
  const platform = normalizePlatform(options.platform || "auto");
  const requestedEffort = options.effort || "auto";
  const requestedSpeed = options.speed || "auto";
  const sessionModel = resolveSessionModel(options.session_model || "");
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

const MODEL_PROVIDER_IDS = ["generic-openai-compatible", "ollama", "lm-studio", "llama-cpp", "vllm"];
const DEFAULT_MODEL_PROVIDER = "generic-openai-compatible";

function normalizeModelProvider(provider) {
  return MODEL_PROVIDER_IDS.includes(provider) ? provider : DEFAULT_MODEL_PROVIDER;
}

function modelProviderProfile(provider) {
  const name = normalizeModelProvider(provider);
  return {
    name,
    adapter: ADAPTER_CANDIDATES[name] || ADAPTER_CANDIDATES[DEFAULT_MODEL_PROVIDER] || {},
  };
}

function providerBaseUrl(input, provider) {
  const explicit = String(input || "").trim();
  if (explicit !== "") {
    return explicit;
  }
  const profile = modelProviderProfile(provider);
  if (profile.name !== DEFAULT_MODEL_PROVIDER) {
    const baseUrlEnv = profile.adapter.base_url_env || "";
    return (baseUrlEnv ? envValue(baseUrlEnv) : "") || profile.adapter.default_base_url || "";
  }
  return envValue("MYTHIFY_OPENAI_COMPAT_BASE_URL") || envValue("MYTHIFY_PROVIDER_BASE_URL");
}

function providerModel(input, provider) {
  const explicit = String(input || "").trim();
  if (explicit !== "") {
    return explicit;
  }
  const profile = modelProviderProfile(provider);
  if (profile.name !== DEFAULT_MODEL_PROVIDER) {
    const modelEnv = profile.adapter.model_env || "";
    return modelEnv ? envValue(modelEnv) : "";
  }
  return envValue("MYTHIFY_OPENAI_COMPAT_MODEL") || envValue("MYTHIFY_PROVIDER_MODEL");
}

function providerApiKeyEnv(input, provider) {
  if (input !== undefined && input !== null) {
    return String(input).trim();
  }
  const profile = modelProviderProfile(provider);
  if (profile.name !== DEFAULT_MODEL_PROVIDER) {
    return profile.adapter.api_key_env || "";
  }
  return "MYTHIFY_OPENAI_COMPAT_API_KEY";
}

function normalizeProviderBaseUrl(raw, toolName = "provider_probe") {
  const value = String(raw || "").trim();
  if (value === "") {
    return { ok: false, baseUrl: "", error: `${toolName} requires base_url or provider profile base URL env.` };
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return { ok: false, baseUrl: "", error: `Invalid provider base_url: ${value}` };
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    return { ok: false, baseUrl: "", error: `${toolName} base_url must use http or https.` };
  }
  return { ok: true, baseUrl: parsed.toString().replace(/\/+$/, ""), error: "" };
}

function isLocalProviderBaseUrl(baseUrl) {
  const parsed = new URL(baseUrl);
  return LOCAL_MODEL_HOSTS.has(parsed.hostname);
}

function normalizeProfileProviderBaseUrl(raw, provider, toolName) {
  const profile = modelProviderProfile(provider);
  const base = normalizeProviderBaseUrl(providerBaseUrl(raw, profile.name), toolName);
  if (!base.ok) {
    return base;
  }
  if (profile.adapter.local_only === true && !isLocalProviderBaseUrl(base.baseUrl)) {
    return {
      ok: false,
      baseUrl: base.baseUrl,
      error: `${toolName} provider ${profile.name} requires a localhost, 127.0.0.1, ::1, or 0.0.0.0 base_url.`,
    };
  }
  return base;
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

async function probeOpenAICompatibleProvider({ provider, base_url, model, timeout_seconds, api_key_env, check, prompt }) {
  const selectedProvider = normalizeModelProvider(provider);
  const selectedCheck = check || "both";
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 10;
  const base = normalizeProfileProviderBaseUrl(base_url, selectedProvider, "provider_probe");
  const selectedModel = providerModel(model, selectedProvider);
  const keyEnv = providerApiKeyEnv(api_key_env, selectedProvider);
  const adapter = ADAPTER_CANDIDATES[selectedProvider] || {};
  const result = {
    provider: selectedProvider,
    provider_kind: adapter.kind || "model_provider",
    status: "blocked",
    openai_compatible: adapter.openai_compatible === true,
    local_only: adapter.local_only === true,
    base_url: base.baseUrl,
    default_base_url: adapter.default_base_url || "",
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
    const modelEnv = adapter.model_env || "MYTHIFY_OPENAI_COMPAT_MODEL";
    result.error = selectedProvider === DEFAULT_MODEL_PROVIDER
      ? "provider_probe check=chat or both requires model or MYTHIFY_OPENAI_COMPAT_MODEL."
      : `provider_probe provider=${selectedProvider} check=chat or both requires model or ${modelEnv}.`;
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

function normalizeLocalProviderBaseUrl(raw, provider) {
  const base = normalizeProfileProviderBaseUrl(raw, provider, "local_model_run");
  if (!base.ok) {
    return base;
  }
  if (!isLocalProviderBaseUrl(base.baseUrl)) {
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

async function runLocalModelRole({ provider, role, base_url, model, api_key_env, timeout_seconds, prompt, max_tokens }) {
  const selectedProvider = normalizeModelProvider(provider);
  const selectedRole = role || "reader";
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 30;
  const selectedMaxTokens =
    typeof max_tokens === "number" && max_tokens > 0 ? Math.min(Math.floor(max_tokens), 2048) : 512;
  const base = normalizeLocalProviderBaseUrl(base_url, selectedProvider);
  const selectedModel = providerModel(model, selectedProvider);
  const keyEnv = providerApiKeyEnv(api_key_env, selectedProvider);
  const adapter = ADAPTER_CANDIDATES[selectedProvider] || {};
  const result = {
    provider: selectedProvider,
    provider_kind: adapter.kind || "model_provider",
    role: selectedRole,
    status: "blocked",
    openai_compatible: adapter.openai_compatible === true,
    base_url: base.baseUrl,
    default_base_url: adapter.default_base_url || "",
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
    const modelEnv = adapter.model_env || "MYTHIFY_OPENAI_COMPAT_MODEL";
    result.error = selectedProvider === DEFAULT_MODEL_PROVIDER
      ? "local_model_run requires model or MYTHIFY_OPENAI_COMPAT_MODEL."
      : `local_model_run provider=${selectedProvider} requires model or ${modelEnv}.`;
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

function hostCliProofPath(status, source, currentChat) {
  return {
    status: status || "unknown",
    proof_source: source,
    current_chat_path: Boolean(currentChat),
    requires_executed_host_evidence: true,
  };
}

function buildHostCliProofScan(host, adapter) {
  return {
    status: "metadata_only",
    host,
    proof_source: adapter.proof_source || "capability_registry",
    host_state_mutated: false,
    writes_state: false,
    verification_recorded: false,
    material_not_evidence: true,
    guardrail: "current_chat_apply_or_confirm_requires_executed_host_evidence",
    paths: {
      current_chat_model_apply: hostCliProofPath(
        adapter.current_chat_model_apply_status,
        "adapter_candidate.current_chat_model_apply_status",
        true
      ),
      current_chat_model_confirm: hostCliProofPath(
        adapter.current_chat_model_confirm_status,
        "adapter_candidate.current_chat_model_confirm_status",
        true
      ),
      worker_model_override: hostCliProofPath(
        adapter.worker_model_override_status,
        "adapter_candidate.worker_model_override_status",
        false
      ),
      thinking_override: hostCliProofPath(
        adapter.thinking_override_status,
        "adapter_candidate.thinking_override_status",
        false
      ),
    },
  };
}

function probeHostCli({ host, bin, timeout_seconds }) {
  const selectedHost = host || "opencode";
  const config = HOST_CLI_PROBES[selectedHost];
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 10;
  const adapter = ADAPTER_CANDIDATES[selectedHost] || {};
  const adapterProofScan = buildHostCliProofScan(selectedHost, adapter);
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
    adapter_proof_scan: adapterProofScan,
    current_chat_apply_status: adapterProofScan.paths.current_chat_model_apply.status,
    current_chat_confirm_status: adapterProofScan.paths.current_chat_model_confirm.status,
    worker_model_override_status: adapterProofScan.paths.worker_model_override.status,
    thinking_override_status: adapterProofScan.paths.thinking_override.status,
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
    `current-chat apply proof: ${result.current_chat_apply_status || "unknown"}`,
    `current-chat confirm proof: ${result.current_chat_confirm_status || "unknown"}`,
    `worker model override proof: ${result.worker_model_override_status || "unknown"}`,
    `thinking override proof: ${result.thinking_override_status || "unknown"}`,
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
  antigravity: {
    outputMode: "print",
    requiresExplicitCwd: true,
    buildArgs: ({ prompt, model }) => {
      const args = [];
      if (model !== "") {
        args.push("--model", model);
      }
      args.push("-p", prompt);
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
  const explicitCwd = String(cwd || "").trim() !== "";
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
    trust_policy: selectedHost === "antigravity" ? "explicit_cwd_required" : "cwd_only",
    permission_policy:
      selectedHost === "antigravity"
        ? "native_permissions_no_auto_bypass"
        : "native_permissions",
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
  if (runner.requiresExplicitCwd && !explicitCwd) {
    result.error = "host_cli_run requires explicit cwd for antigravity so workspace trust is deliberate.";
    return result;
  }
  if (resolved.bin === "") {
    return result;
  }

  result.model_applied = ["opencode", "antigravity"].includes(selectedHost) && selectedModel !== "";
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
    `trust policy: ${result.trust_policy || "unset"}`,
    `permission policy: ${result.permission_policy || "unset"}`,
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

const COLAB_GPU_ACCELERATORS = ["T4", "L4", "G4", "H100", "A100"];
const COLAB_TPU_ACCELERATORS = ["v5e1", "v6e1"];

function resolveExecutionRunCwd(rawCwd) {
  const selected = String(rawCwd || "").trim();
  const resolved = selected === "" ? path.dirname(resolveStateDir()) : path.resolve(selected);
  try {
    const stat = fs.statSync(resolved);
    if (!stat.isDirectory()) {
      return { ok: false, cwd: resolved, error: `execution_run cwd is not a directory: ${resolved}` };
    }
  } catch {
    return { ok: false, cwd: resolved, error: `execution_run cwd is not accessible: ${resolved}` };
  }
  return { ok: true, cwd: resolved, error: "" };
}

function resolveExecutionScriptPath(rawScriptPath, cwd) {
  const selected = String(rawScriptPath || "").trim();
  if (selected === "") {
    return { ok: false, path: "", error: "execution_run requires script_path." };
  }
  const resolved = path.isAbsolute(selected) ? selected : path.resolve(cwd, selected);
  try {
    const stat = fs.statSync(resolved);
    if (!stat.isFile()) {
      return { ok: false, path: resolved, error: `execution_run script_path is not a file: ${resolved}` };
    }
  } catch {
    return { ok: false, path: resolved, error: `execution_run script_path is not accessible: ${resolved}` };
  }
  return { ok: true, path: resolved, error: "" };
}

function normalizeColabExecutionAccelerator(acceleratorType, accelerator) {
  const type = String(acceleratorType || "cpu").trim();
  const selected = String(accelerator || "").trim();
  if (type === "cpu") {
    if (selected !== "") {
      return { ok: false, type, accelerator: selected, args: [], error: "execution_run cpu mode must not set accelerator." };
    }
    return { ok: true, type, accelerator: "", args: [], error: "" };
  }
  if (type === "gpu") {
    if (!COLAB_GPU_ACCELERATORS.includes(selected)) {
      return {
        ok: false,
        type,
        accelerator: selected,
        args: [],
        error: `execution_run gpu mode requires accelerator: ${COLAB_GPU_ACCELERATORS.join(", ")}.`,
      };
    }
    return { ok: true, type, accelerator: selected, args: ["--gpu", selected], error: "" };
  }
  if (type === "tpu") {
    if (!COLAB_TPU_ACCELERATORS.includes(selected)) {
      return {
        ok: false,
        type,
        accelerator: selected,
        args: [],
        error: `execution_run tpu mode requires accelerator: ${COLAB_TPU_ACCELERATORS.join(", ")}.`,
      };
    }
    return { ok: true, type, accelerator: selected, args: ["--tpu", selected], error: "" };
  }
  return { ok: false, type, accelerator: selected, args: [], error: `execution_run does not support accelerator_type ${type}.` };
}

function runExecutionAdapter({
  adapter,
  bin,
  script_path,
  cwd,
  timeout_seconds,
  accelerator_type,
  accelerator,
  script_args,
  billing_ack,
  data_movement_ack,
  cleanup_ack,
}) {
  const selectedAdapter = adapter || "google-colab-cli";
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 600;
  const adapterInfo = ADAPTER_CANDIDATES[selectedAdapter] || {};
  const resolved = resolveExecutionProbeBinary(selectedAdapter, bin);
  const cwdResult = resolveExecutionRunCwd(cwd);
  const result = {
    adapter: selectedAdapter,
    adapter_kind: adapterInfo.kind || "execution_substrate",
    status: "blocked",
    binary: resolved.bin,
    binary_source: resolved.source,
    cwd: cwdResult.cwd,
    script_path: "",
    command: "",
    args: [],
    exit_code: -1,
    started_at: "",
    ended_at: "",
    duration_seconds: 0,
    stdout_tail: "",
    stderr_tail: "",
    output_tail: "",
    timed_out: false,
    material_not_evidence: true,
    evidence_status: "remote_output_not_verification",
    writes_state: false,
    verification_recorded: false,
    job_execution_enabled: true,
    billing_acknowledged: Boolean(billing_ack),
    data_movement_acknowledged: Boolean(data_movement_ack),
    cleanup_acknowledged: Boolean(cleanup_ack),
    remote_runtime_requested: false,
    accelerator_requested: false,
    accelerator_type: accelerator_type || "cpu",
    accelerator: accelerator || "",
    artifact_retrieval_enabled: false,
    cleanup_guard: "colab_run_without_keep",
    billing_guard: "requires_billing_ack",
    data_movement_guard: "requires_data_movement_ack",
    error: resolved.error,
  };
  if (selectedAdapter !== "google-colab-cli") {
    result.error = `execution_run does not support ${selectedAdapter}.`;
    return result;
  }
  if (envValue("MYTHIFY_DISABLE_RUN") === "1") {
    result.error = "MYTHIFY_DISABLE_RUN=1 disables execution_run.";
    return result;
  }
  if (!billing_ack) {
    result.error = "execution_run requires billing_ack=true before running billable Colab work.";
    return result;
  }
  if (!data_movement_ack) {
    result.error = "execution_run requires data_movement_ack=true because Colab run transmits a local script to a remote runtime.";
    return result;
  }
  if (!cleanup_ack) {
    result.error = "execution_run requires cleanup_ack=true because remote runtime teardown must be explicit.";
    return result;
  }
  if (!cwdResult.ok) {
    result.error = cwdResult.error;
    return result;
  }
  const scriptResult = resolveExecutionScriptPath(script_path, cwdResult.cwd);
  result.script_path = scriptResult.path;
  if (!scriptResult.ok) {
    result.error = scriptResult.error;
    return result;
  }
  const acceleratorResult = normalizeColabExecutionAccelerator(accelerator_type, accelerator);
  result.accelerator_type = acceleratorResult.type;
  result.accelerator = acceleratorResult.accelerator;
  if (!acceleratorResult.ok) {
    result.error = acceleratorResult.error;
    return result;
  }
  if (resolved.bin === "") {
    return result;
  }

  const extraArgs = Array.isArray(script_args) ? script_args.map((item) => String(item)) : [];
  result.args = ["run", ...acceleratorResult.args, scriptResult.path, ...extraArgs];
  result.command = [path.basename(resolved.bin), ...result.args].join(" ");
  result.remote_runtime_requested = true;
  result.accelerator_requested = acceleratorResult.type !== "cpu";

  result.started_at = isoNow();
  const startedAt = process.hrtime.bigint();
  const run = spawnSync(resolved.bin, result.args, {
    shell: false,
    encoding: "utf8",
    cwd: cwdResult.cwd,
    timeout: Math.round(timeoutSeconds * 1000),
    maxBuffer: 1024 * 1024,
  });
  const durationSeconds = Number(process.hrtime.bigint() - startedAt) / 1e9;
  result.ended_at = isoNow();
  const timedOut = Boolean(run.error && run.error.code === "ETIMEDOUT");
  const exitCode = typeof run.status === "number" ? run.status : -1;
  result.exit_code = exitCode;
  result.duration_seconds = Number(durationSeconds.toFixed(3));
  result.stdout_tail = tailText(run.stdout, 4000);
  result.stderr_tail = tailText(run.stderr, 4000);
  result.output_tail = result.stdout_tail || result.stderr_tail;
  result.timed_out = timedOut;
  result.status = exitCode === 0 ? "succeeded" : "failed";
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

function formatExecutionRun(result) {
  const prefix = result.status === "succeeded" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Execution run ${result.status}.`,
    `adapter: ${result.adapter}`,
    `binary: ${result.binary || "not found"}`,
    `binary source: ${result.binary_source}`,
    `cwd: ${result.cwd || "unset"}`,
    `script: ${result.script_path || "unset"}`,
    `accelerator: ${result.accelerator_type}${result.accelerator ? ` ${result.accelerator}` : ""}`,
    `billing acknowledged: ${result.billing_acknowledged ? "yes" : "no"}`,
    `data movement acknowledged: ${result.data_movement_acknowledged ? "yes" : "no"}`,
    `cleanup acknowledged: ${result.cleanup_acknowledged ? "yes" : "no"}`,
    `remote runtime requested: ${result.remote_runtime_requested ? "yes" : "no"}`,
    `exit: ${result.exit_code}`,
    `started at: ${result.started_at || "unset"}`,
    `ended at: ${result.ended_at || "unset"}`,
    `writes state: ${result.writes_state ? "yes" : "no"}`,
    `verification recorded: ${result.verification_recorded ? "yes" : "no"}`,
    "evidence: remote output is material, not verification evidence.",
  ];
  if (result.output_tail) {
    lines.push(`output: ${result.output_tail}`);
  }
  if (result.error) {
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
const LIFECYCLE_CONTRACT_VERSION = 1;
const LIFECYCLE_REQUIRED_BEFORE_EVAL_EXECUTION = [
  "explicit_user_request",
  "agent_project_path",
  "eval_dataset_or_eval_set",
  "bounded_timeout",
  "credential_source_summary",
  "project_mutation_ack",
  "material_not_verification",
  "artifact_or_report_path",
];
const LIFECYCLE_REQUIRED_BEFORE_DEPLOYMENT = [
  "explicit_user_request",
  "target_platform",
  "project_id",
  "region",
  "billing_ack",
  "data_movement_ack",
  "cloud_mutation_ack",
  "rollback_or_teardown_posture",
  "material_not_verification",
];

function lifecycleLaneContract(adapter, adapterInfo = {}) {
  const commonDisabledActions = [
    "project_scaffold",
    "project_create",
    "agent_run",
    "eval_execution",
    "deployment",
    "publishing",
    "cloud_mutation",
    "project_mutation",
  ];
  const adapterDisabledActions = adapterInfo.lifecycle_disabled_actions || [];
  return {
    version: LIFECYCLE_CONTRACT_VERSION,
    adapter,
    lane: "agent_lifecycle",
    status: adapterInfo.status || "probe_supported",
    current_policy: "probe_only",
    allowed_probe_actions: adapterInfo.lifecycle_allowed_probe_actions || [
      "probe_version",
      "probe_help",
      "probe_eval_help",
    ],
    allowed_probe_commands: adapterInfo.lifecycle_allowed_probe_commands || [
      "--version",
      "--help",
      "eval --help",
    ],
    adapter_specific_disabled_actions: adapterDisabledActions,
    disabled_actions: [...new Set([...commonDisabledActions, ...adapterDisabledActions])],
    future_guarded_actions: adapterInfo.lifecycle_future_guarded_actions || [
      "eval_execution",
      "deployment",
      "publishing",
    ],
    required_before_eval_execution: LIFECYCLE_REQUIRED_BEFORE_EVAL_EXECUTION,
    required_before_deployment: LIFECYCLE_REQUIRED_BEFORE_DEPLOYMENT,
    mutation_policy: adapterInfo.lifecycle_mutation_policy || "probe_only_no_project_or_cloud_mutation",
    material_not_evidence: true,
    evidence_status: adapterInfo.evidence_status || "lifecycle_probe_output_not_verification",
    writes_state: false,
    verification_recorded: false,
    eval_execution_enabled: false,
    deployment_enabled: false,
    scaffold_enabled: false,
    run_enabled: false,
    cloud_mutation_enabled: false,
    project_mutation_enabled: false,
  };
}

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
  const lifecycleContract = lifecycleLaneContract(selectedAdapter, adapterInfo);
  const resolved = resolveLifecycleProbeBinary(selectedAdapter, bin);
  const result = {
    adapter: selectedAdapter,
    adapter_kind: adapterInfo.kind || "agent_lifecycle",
    status: "blocked",
    binary: resolved.bin,
    binary_source: resolved.source,
    material_not_evidence: true,
    evidence_status: lifecycleContract.evidence_status,
    writes_state: lifecycleContract.writes_state,
    verification_recorded: lifecycleContract.verification_recorded,
    lifecycle_lane_contract: lifecycleContract,
    allowed_probe_actions: lifecycleContract.allowed_probe_actions,
    allowed_probe_commands: lifecycleContract.allowed_probe_commands,
    disabled_lifecycle_actions: lifecycleContract.disabled_actions,
    future_guarded_actions: lifecycleContract.future_guarded_actions,
    can_probe_eval: false,
    eval_execution_enabled: lifecycleContract.eval_execution_enabled,
    deployment_enabled: lifecycleContract.deployment_enabled,
    scaffold_enabled: lifecycleContract.scaffold_enabled,
    run_enabled: lifecycleContract.run_enabled,
    cloud_mutation_enabled: lifecycleContract.cloud_mutation_enabled,
    project_mutation_enabled: lifecycleContract.project_mutation_enabled,
    billing_guard: lifecycleContract.mutation_policy,
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
    `writes state: ${result.writes_state ? "yes" : "no"}`,
    `verification recorded: ${result.verification_recorded ? "yes" : "no"}`,
    `allowed probe actions: ${result.allowed_probe_actions.join(", ")}`,
    `disabled lifecycle actions: ${result.disabled_lifecycle_actions.join(", ")}`,
    `future guarded actions: ${result.future_guarded_actions.join(", ")}`,
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
      "Probe a configured OpenAI-compatible provider by calling /v1/models and, when requested, /v1/chat/completions. The ollama, lm-studio, llama-cpp, and vllm profiles default to their local /v1 endpoints and send no auth header by default. " +
      "Use this before assigning local reader or triage roles to a provider. The result is material, not verification evidence, and does not enable worker execution.",
    inputSchema: {
      provider: z
        .enum(MODEL_PROVIDER_IDS)
        .optional()
        .describe("Provider adapter to probe. Defaults to generic-openai-compatible; ollama, lm-studio, llama-cpp, and vllm use local /v1 profiles."),
      base_url: z
        .string()
        .optional()
        .describe("OpenAI-compatible /v1 base URL. Generic defaults to MYTHIFY_OPENAI_COMPAT_BASE_URL; ollama defaults to MYTHIFY_OLLAMA_BASE_URL or http://localhost:11434/v1; lm-studio defaults to MYTHIFY_LM_STUDIO_BASE_URL or http://localhost:1234/v1; llama-cpp defaults to MYTHIFY_LLAMA_CPP_BASE_URL or http://localhost:8080/v1; vllm defaults to MYTHIFY_VLLM_BASE_URL or http://localhost:8000/v1."),
      model: z
        .string()
        .optional()
        .describe("Model id for chat probes. Generic defaults to MYTHIFY_OPENAI_COMPAT_MODEL; ollama defaults to MYTHIFY_OLLAMA_MODEL; lm-studio defaults to MYTHIFY_LM_STUDIO_MODEL; llama-cpp defaults to MYTHIFY_LLAMA_CPP_MODEL; vllm defaults to MYTHIFY_VLLM_MODEL."),
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
    const result = await probeOpenAICompatibleProvider({
      provider: provider || DEFAULT_MODEL_PROVIDER,
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
      "Run a reader or triage prompt against a localhost OpenAI-compatible model provider. The ollama, lm-studio, llama-cpp, and vllm profiles default to their local /v1 endpoints and send no auth header by default. " +
      "Use this for low-risk local model material before the orchestrator verifies claims with commands. The result is material, not verification evidence, and the tool writes no Mythify state.",
    inputSchema: {
      provider: z
        .enum(MODEL_PROVIDER_IDS)
        .optional()
        .describe("Local provider profile. Defaults to generic-openai-compatible; ollama, lm-studio, llama-cpp, and vllm use local /v1 profiles."),
      role: z
        .enum(LOCAL_MODEL_ROLES)
        .optional()
        .describe("Role to run. Defaults to reader. Allowed roles are reader and triage."),
      base_url: z
        .string()
        .optional()
        .describe("Local OpenAI-compatible /v1 base URL. Generic defaults to MYTHIFY_OPENAI_COMPAT_BASE_URL; ollama defaults to MYTHIFY_OLLAMA_BASE_URL or http://localhost:11434/v1; lm-studio defaults to MYTHIFY_LM_STUDIO_BASE_URL or http://localhost:1234/v1; llama-cpp defaults to MYTHIFY_LLAMA_CPP_BASE_URL or http://localhost:8080/v1; vllm defaults to MYTHIFY_VLLM_BASE_URL or http://localhost:8000/v1. Must be localhost, 127.0.0.1, ::1, or 0.0.0.0."),
      model: z
        .string()
        .optional()
        .describe("Local model id. Generic defaults to MYTHIFY_OPENAI_COMPAT_MODEL; ollama defaults to MYTHIFY_OLLAMA_MODEL; lm-studio defaults to MYTHIFY_LM_STUDIO_MODEL; llama-cpp defaults to MYTHIFY_LLAMA_CPP_MODEL; vllm defaults to MYTHIFY_VLLM_MODEL."),
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
  guarded(async ({ provider, role, base_url, model, prompt, api_key_env, timeout_seconds, max_tokens, format }) => {
    const result = await runLocalModelRole({
      provider: provider || DEFAULT_MODEL_PROVIDER,
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
      "Run a bounded non-interactive prompt through Kimi Code, OpenCode, or Antigravity. " +
      "Use this only for worker material that the orchestrator will inspect and then verify with commands. The result is material, not verification evidence, and the tool writes no Mythify state.",
    inputSchema: {
      host: z
        .enum(["kimi-code", "opencode", "antigravity"])
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
        .describe("Working directory for the worker. Defaults to the project root inferred from MYTHIFY_DIR. Antigravity requires this to be explicit."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("Prompt run timeout in seconds. Defaults to 120."),
      model: z
        .string()
        .optional()
        .describe("Optional OpenCode or Antigravity model id. Kimi Code does not receive a model flag in this adapter."),
      agent: z
        .string()
        .optional()
        .describe("Optional OpenCode agent id. Kimi Code and Antigravity do not receive an agent flag in this adapter."),
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

server.registerTool(
  "execution_run",
  {
    title: "Run a guarded execution adapter job",
    description:
      "Run a guarded Google Colab CLI ephemeral job through colab run. " +
      "Use this only after the user explicitly accepts billing, data movement, and cleanup. The result is material, not verification evidence, writes no Mythify state, and does not use persistent sessions, Drive mounting, artifact download, or notebook log export.",
    inputSchema: {
      adapter: z
        .enum(["google-colab-cli"])
        .optional()
        .describe("Execution adapter to run. Defaults to google-colab-cli."),
      bin: z
        .string()
        .optional()
        .describe("Explicit CLI binary path. Defaults to MYTHIFY_COLAB_BIN, then PATH and common install paths."),
      cwd: z
        .string()
        .optional()
        .describe("Working directory for resolving relative script_path. Defaults to the project root inferred from MYTHIFY_DIR."),
      script_path: z
        .string()
        .describe("Local script path to pass to colab run. Relative paths resolve from cwd."),
      script_args: z
        .array(z.string())
        .optional()
        .describe("Optional arguments forwarded after the script path."),
      accelerator_type: z
        .enum(["cpu", "gpu", "tpu"])
        .optional()
        .describe("Remote runtime accelerator class. Defaults to cpu."),
      accelerator: z
        .enum(["T4", "L4", "G4", "H100", "A100", "v5e1", "v6e1"])
        .optional()
        .describe("Required for gpu or tpu runs. GPUs: T4, L4, G4, H100, A100. TPUs: v5e1, v6e1."),
      billing_ack: z
        .boolean()
        .optional()
        .describe("Must be true to acknowledge Colab remote execution can consume compute units or quota."),
      data_movement_ack: z
        .boolean()
        .optional()
        .describe("Must be true to acknowledge the local script is transmitted to a remote Colab runtime."),
      cleanup_ack: z
        .boolean()
        .optional()
        .describe("Must be true to acknowledge this adapter relies on colab run ephemeral teardown and never passes --keep."),
      timeout_seconds: z
        .number()
        .positive()
        .optional()
        .describe("Remote job timeout in seconds. Defaults to 600."),
      format: z
        .enum(["text", "json"])
        .optional()
        .describe("Return text by default, or JSON for machine-readable execution records."),
    },
  },
  guarded(({
    adapter,
    bin,
    script_path,
    cwd,
    timeout_seconds,
    accelerator_type,
    accelerator,
    script_args,
    billing_ack,
    data_movement_ack,
    cleanup_ack,
    format,
  }) => {
    const result = runExecutionAdapter({
      adapter: adapter || "google-colab-cli",
      bin: bin || "",
      script_path,
      cwd,
      timeout_seconds,
      accelerator_type,
      accelerator,
      script_args,
      billing_ack,
      data_movement_ack,
      cleanup_ack,
    });
    const prefix = result.status === "succeeded" ? "[OK] " : "[FAIL] ";
    return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatExecutionRun(result);
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

server.registerTool(
  "workflow_status",
  {
    title: "Show workflow dashboard",
    description:
      "Show a read-only dashboard of the active plan, current step, next step, active outcome, evidence counts, recent verification records, and recent reflections. " +
      "Use this to orient without mutating state or treating model confidence as evidence.",
    inputSchema: {
      recent: z
        .number()
        .int()
        .min(0)
        .optional()
        .describe("Number of recent verification and reflection records to include. Defaults to 3."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ recent, format }) => {
    const dashboard = buildWorkflowDashboard(typeof recent === "number" ? recent : 3);
    if (format === "json") {
      return `[OK] ${JSON.stringify(dashboard, null, 2)}`;
    }
    return formatWorkflowDashboard(dashboard);
  })
);

server.registerTool(
  "verification_history",
  {
    title: "Show verification history",
    description:
      "Show a read-only history of executed and attested verification records, including verdict, command or evidence, exit code, duration, and plan or step context. " +
      "Use this to inspect recorded evidence without rerunning checks or upgrading self-reported attestations.",
    inputSchema: {
      recent: z
        .number()
        .int()
        .min(0)
        .optional()
        .describe("Number of recent verification records to include. Defaults to 10."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ recent, format }) => {
    const view = buildVerificationHistoryView(typeof recent === "number" ? recent : 10);
    if (format === "json") {
      return `[OK] ${JSON.stringify(view, null, 2)}`;
    }
    return formatVerificationHistoryView(view);
  })
);

server.registerTool(
  "work_report",
  {
    title: "Show chat-ready work report",
    description:
      "Show a chat-ready live work report from durable Mythify events: plan creation, step updates, verification records, and reflections. " +
      "Use this during multi-step work to narrate what happened since the last report; set peek true to avoid advancing the cursor.",
    inputSchema: {
      since: z
        .enum(REPORT_SINCE_MODES)
        .optional()
        .describe("Report events since the last cursor or from the start. Defaults to last."),
      recent: z
        .number()
        .int()
        .min(0)
        .optional()
        .describe(`Maximum events to include. Defaults to ${DEFAULT_REPORT_RECENT}.`),
      cursor: z.string().optional().describe("Report cursor name. Defaults to default."),
      peek: z.boolean().optional().describe("When true, leave the report cursor unchanged."),
      mark: z
        .boolean()
        .optional()
        .describe("When true, advance the cursor to the latest event without returning old events."),
      format: z.enum(REPORT_FORMATS).optional().describe("Return chat text or JSON. Defaults to chat."),
    },
  },
  guarded(({ since, recent, cursor, peek, mark, format }) => {
    if (mark && typeof since === "string") {
      return "[FAIL] mark cannot be combined with since. Use mark to set a cursor, then call work_report with since last to read new events.";
    }
    const view = buildWorkReport({
      since: since || "last",
      recent: typeof recent === "number" ? recent : DEFAULT_REPORT_RECENT,
      cursor: cursor || "default",
      peek: Boolean(peek),
      mark: Boolean(mark),
    });
    if (view.error) {
      return view.error;
    }
    if (format === "json") {
      return `[OK] ${JSON.stringify({ ...view, format: "json" }, null, 2)}`;
    }
    return formatWorkReport(view);
  })
);

server.registerTool(
  "background_status",
  {
    title: "Show background task state",
    description:
      "Show a read-only background task view of durable outcome loops and fanout jobs, including task counts, statuses, and next actions. " +
      "Use this to orient on long-running delegated work without mutating state or treating model confidence as progress.",
    inputSchema: {
      recent: z
        .number()
        .int()
        .min(0)
        .optional()
        .describe("Number of recent outcomes and fanout jobs to include. Defaults to 5."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ recent, format }) => {
    const view = buildBackgroundView(typeof recent === "number" ? recent : 5);
    if (format === "json") {
      return `[OK] ${JSON.stringify(view, null, 2)}`;
    }
    return formatBackgroundView(view);
  })
);

server.registerTool(
  "outcome_progress",
  {
    title: "Show outcome loop progress",
    description:
      "Show a read-only progress view of active and recent outcome loops, including iteration budget, verifier exit details, metric score when present, and next action from durable state. " +
      "Use this to inspect verifier-backed outcome progress without running checks, making attempts, stopping loops, or treating notes as verification.",
    inputSchema: {
      recent: z
        .number()
        .int()
        .min(0)
        .optional()
        .describe("Number of recent outcomes to include. Defaults to 5."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ recent, format }) => {
    const view = buildOutcomeProgressView(typeof recent === "number" ? recent : 5);
    if (format === "json") {
      return `[OK] ${JSON.stringify(view, null, 2)}`;
    }
    return formatOutcomeProgressView(view);
  })
);

server.registerTool(
  "release_readiness",
  {
    title: "Show release readiness",
    description:
      "Show a read-only release readiness view from recorded verification gates, project git state, and roadmap state. " +
      "Use this before tagging or publishing to see which expected gates have recorded evidence without rerunning gates or declaring the release safe.",
    inputSchema: {
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ format }) => {
    const view = buildReleaseReadinessView();
    if (format === "json") {
      return `[OK] ${JSON.stringify(view, null, 2)}`;
    }
    return formatReleaseReadinessView(view);
  })
);

server.registerTool(
  "fanout_timeline",
  {
    title: "Show fanout worker timeline",
    description:
      "Show a read-only timeline of fanout worker job creation, task starts, task finishes, duration, status, errors, and output metadata. " +
      "Use this to inspect durable delegated-worker history without mutating state or treating worker output as verification evidence.",
    inputSchema: {
      recent: z
        .number()
        .int()
        .min(0)
        .optional()
        .describe("Number of recent fanout jobs to include. Defaults to 5."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ recent, format }) => {
    const view = buildFanoutTimelineView(typeof recent === "number" ? recent : 5);
    if (format === "json") {
      return `[OK] ${JSON.stringify(view, null, 2)}`;
    }
    return formatFanoutTimelineView(view);
  })
);

server.registerTool(
  "phase_status",
  {
    title: "Show workflow phase state",
    description:
      "Show a read-only Understand, Design, Build, Judge, Verify phase view of active plan steps and supporting durable evidence counts. " +
      "Use this to orient on where the current work sits without mutating state or treating model confidence as evidence.",
    inputSchema: {
      recent: z
        .number()
        .int()
        .min(0)
        .optional()
        .describe("Number of recent verification and reflection records to consider. Defaults to 3."),
      format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
    },
  },
  guarded(({ recent, format }) => {
    const view = buildPhaseView(typeof recent === "number" ? recent : 3);
    if (format === "json") {
      return `[OK] ${JSON.stringify(view, null, 2)}`;
    }
    return formatPhaseView(view);
  })
);

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
      const verifications = readJsonl(verificationsPath());
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
