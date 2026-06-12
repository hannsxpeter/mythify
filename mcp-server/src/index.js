#!/usr/bin/env node
// Mythify MCP server v2.1.0
// Exposes the Mythify state model (memory, plans, lessons, verifications,
// reflections) as 12 core MCP tools over stdio, plus the 3 fanout tools for
// parallel delegation (src/fanout.js), 15 tools in total. On-disk formats are
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

const VERSION = "2.1.0";
const TAIL_CHARS = 4000;
const STEP_STATUSES = ["pending", "in_progress", "completed", "failed", "skipped"];
const STEP_ICONS = {
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

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

function appendJsonl(filePath, record) {
  ensureDir(path.dirname(filePath));
  fs.appendFileSync(filePath, JSON.stringify(record) + "\n", "utf8");
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
        .enum(["fact", "decision", "discovery", "state"])
        .default("fact")
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
        .enum(["fact", "decision", "discovery", "state", "all"])
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
    return (
      "[FAIL] Refusing to clear memory: no key was given and confirm_clear_all is not true. " +
      "Pass key to remove a single entry, or set confirm_clear_all to true to wipe everything. Nothing was cleared."
    );
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
