// Mythify fanout: parallel delegation (MCP only), per docs/design.md.
// fanout_start accepts a one-shot declarative task list, registers the job
// under .mythify/fanout/<job_id>/, returns the job id immediately, and runs
// one fresh worker per task in a background concurrency pool. Workers are
// independent model invocations (claude-cli subprocess, anthropic API,
// openai-compatible API, or a shell command template) with no memory of the
// conversation. fanout_status and fanout_results report on the job.
// The Python CLI deliberately does not implement fanout.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { z } from "zod";

const ENGINES = ["claude-cli", "anthropic", "openai", "command"];

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

function envSet(name) {
  return (process.env[name] || "").trim() !== "";
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

// Claude Desktop launches MCP servers with a minimal PATH, so the claude
// binary is resolved explicitly: MYTHIFY_FANOUT_CLAUDE_BIN if set, else
// claude on PATH, else well-known install locations.
function resolveClaudeBin() {
  const explicit = (process.env.MYTHIFY_FANOUT_CLAUDE_BIN || "").trim();
  if (explicit !== "") {
    return isExecutableFile(explicit) ? explicit : null;
  }
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (dir === "") {
      continue;
    }
    const candidate = path.join(dir, "claude");
    if (isExecutableFile(candidate)) {
      return candidate;
    }
  }
  const fallbacks = [
    path.join(os.homedir(), ".claude", "local", "claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
  ];
  for (const candidate of fallbacks) {
    if (isExecutableFile(candidate)) {
      return candidate;
    }
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

// Auto-detection order from the spec: explicit MYTHIFY_FANOUT_ENGINE, else
// claude-cli if a claude binary resolves, else anthropic if ANTHROPIC_API_KEY
// is set, else command if MYTHIFY_FANOUT_COMMAND is set, else refuse with a
// message listing all four options.
function autoDetectEngine() {
  const explicit = (process.env.MYTHIFY_FANOUT_ENGINE || "").trim();
  if (explicit !== "") {
    return { engine: explicit };
  }
  if (resolveClaudeBin() !== null) {
    return { engine: "claude-cli" };
  }
  if (envSet("ANTHROPIC_API_KEY")) {
    return { engine: "anthropic" };
  }
  if (envSet("MYTHIFY_FANOUT_COMMAND")) {
    return { engine: "command" };
  }
  return {
    error:
      "[FAIL] No fanout engine is available. Configure one of the four engines: " +
      "claude-cli (install the claude CLI or set MYTHIFY_FANOUT_CLAUDE_BIN), " +
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

// Most specific wins: per-task model, then per-job model, then
// MYTHIFY_FANOUT_MODEL, then the engine default.
function resolveModel(taskModel, jobModel, engine) {
  for (const candidate of [taskModel, jobModel, process.env.MYTHIFY_FANOUT_MODEL]) {
    if (candidate !== undefined && candidate !== null && String(candidate).trim() !== "") {
      return String(candidate).trim();
    }
  }
  return engineDefaultModel(engine);
}

// Validation-time availability check for a task's resolved engine. Returns an
// explanatory string on failure, null when the engine is usable.
function engineAvailabilityError(engine, model) {
  if (engine === "claude-cli") {
    return resolveClaudeBin() === null ? `engine claude-cli: ${claudeBinFailureText()}` : null;
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

// ---------------------------------------------------------------------------
// Worker prompt assembly
// ---------------------------------------------------------------------------

// Fixed preamble, then each context file as a labeled fenced block, then the
// task prompt. context_paths resolve relative to the project root (absolute
// paths allowed); total inlined context is capped at MYTHIFY_FANOUT_CONTEXT_BYTES
// with an explicit truncation marker. An unreadable path is a validation error.
function assembleWorkerPrompt(task, projectRoot, contextBytesCap) {
  const parts = [WORKER_PREAMBLE];
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
  for (const extra of ["/opt/homebrew/bin", "/usr/local/bin"]) {
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

async function runClaudeCliWorker(prompt, model, timeoutSeconds, projectRoot) {
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

function runWorker(engine, prompt, model, timeoutSeconds, projectRoot) {
  if (engine === "claude-cli") {
    return runClaudeCliWorker(prompt, model, timeoutSeconds, projectRoot);
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
  const startedNs = process.hrtime.bigint();
  let outcome;
  try {
    outcome = await runWorker(task.engine, prompt, task.model, timeoutSeconds, projectRoot);
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

function handleFanoutStart({ tasks, model, engine, timeout_seconds }) {
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
  const resolvedTasks = [];
  for (let i = 0; i < tasks.length; i += 1) {
    const task = tasks[i] || {};
    const title = typeof task.title === "string" ? task.title : "";
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
    const taskModel = resolveModel(task.model, model, taskEngine);
    const availability = engineAvailabilityError(taskEngine, taskModel);
    if (availability) {
      return `[FAIL] ${label}: ${availability} No job was started.`;
    }
    const assembled = assembleWorkerPrompt(task, projectRoot, contextBytesCap);
    if (assembled.error) {
      return `[FAIL] ${label}: ${assembled.error} No job was started.`;
    }
    resolvedTasks.push({ title, engine: taskEngine, model: taskModel, prompt: assembled.prompt });
  }

  const jobEngineRecord = jobEngine !== "" ? jobEngine : resolvedTasks[0].engine;
  const jobModelRecord = resolveModel(undefined, model, jobEngineRecord);
  const jobTimeout =
    typeof timeout_seconds === "number" && timeout_seconds > 0
      ? timeout_seconds
      : intEnv("MYTHIFY_FANOUT_TIMEOUT_SECONDS", 600);
  const jobId = `fo-${io.stampNow()}-${crypto.randomBytes(2).toString("hex")}`;
  const jobDir = path.join(stateDir, "fanout", jobId);
  const now = io.isoNow();
  const job = {
    id: jobId,
    created: now,
    engine: jobEngineRecord,
    model: jobModelRecord,
    timeout_seconds: jobTimeout,
    last_updated: now,
    tasks: resolvedTasks.map((resolved, i) => ({
      id: i + 1,
      title: resolved.title,
      status: "pending",
      engine: resolved.engine,
      model: resolved.model,
      started_at: null,
      finished_at: null,
      duration_seconds: 0,
      error: null,
      output_file: `task-${i + 1}-output.md`,
      output_bytes: 0,
    })),
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
    `[OK] Fanout job ${jobId} started: ${job.tasks.length} ${job.tasks.length === 1 ? "task" : "tasks"}, concurrency ${concurrency}, timeout ${jobTimeout}s per worker.`,
  ];
  for (const task of job.tasks) {
    lines.push(
      `[ ] ${task.id}. ${task.title} (engine: ${task.engine}${task.model !== "" ? `, model: ${task.model}` : ""})`
    );
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
    `[OK] Fanout job ${job.id} (engine: ${job.engine}${job.model ? `, model: ${job.model}` : ""}, timeout ${job.timeout_seconds}s per worker, created ${job.created}).`,
  ];
  if (interruptedNote) {
    lines.push(interruptedNote);
  }
  lines.push(
    `Tasks: ${job.tasks.length} total; ${counts.completed} completed, ${counts.failed} failed, ${counts.running} running, ${counts.pending} pending, ${counts.interrupted} interrupted.`
  );
  for (const task of job.tasks) {
    const icon = TASK_STATUS_ICONS[task.status] || "[ ]";
    let line = `${icon} ${task.id}. ${task.title} (${task.status}; engine: ${task.engine}`;
    if (task.model) {
      line += `, model: ${task.model}`;
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
      `=== Task ${task.id}: ${task.title} (${task.status}, ${typeof task.duration_seconds === "number" ? task.duration_seconds.toFixed(1) : "0.0"}s, engine: ${task.engine}${task.model ? `, model: ${task.model}` : ""}) ===`
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
              model: z
                .string()
                .optional()
                .describe("Per-task model override; beats the job model and MYTHIFY_FANOUT_MODEL."),
              engine: z
                .string()
                .optional()
                .describe(
                  "Per-task engine override (claude-cli, anthropic, openai, or command); beats the job engine and MYTHIFY_FANOUT_ENGINE."
                ),
            })
          )
          .describe(
            "1 to MYTHIFY_FANOUT_MAX_TASKS fully independent tasks. Each task is a fresh model call that costs real money or subscription quota."
          ),
        model: z
          .string()
          .optional()
          .describe("Default model for every task; per-task model overrides it."),
        engine: z
          .string()
          .optional()
          .describe(
            "Default engine for every task (claude-cli, anthropic, openai, or command); per-task engine overrides it. Omit to auto-detect."
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
        "Show a fanout job's progress: per-task status icons with engine, model, and elapsed time, plus overall counts. Defaults to the most recent job. " +
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
