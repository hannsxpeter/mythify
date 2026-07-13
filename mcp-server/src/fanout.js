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
import { redactSensitiveOutput } from "./redact.js";
import { assembleWorkerPrompt } from "./fanout-prompt.js";
import { isFanoutJobId, taskOutputPath } from "./fanout-paths.js";
import { recoverInterruptedWorktree } from "./fanout-worktree-recovery.js";
import {
  EFFORT_LEVELS,
  ENGINES,
  FANOUT_VISIBILITY_MODES,
  CLAUDE_CLI_COST_WARNING,
  HOSTED_PROVIDER_ENGINES,
  HOSTED_PROVIDER_REQUIRED_ACKS,
  SPAWN_CEILINGS,
  SPEED_LEVELS,
  TASK_ROLES,
  appendProviderAudit,
  auditCostMetadata,
  autoDetectEngine,
  ceilingCheck,
  claudeBinFailureText,
  codexBinFailureText,
  configureFanoutPolicy,
  cursorBinFailureText,
  depthGuardText,
  engineAvailabilityError,
  engineCostMetadata,
  engineProvider,
  envSet,
  fanoutRootDir,
  intEnv,
  killSwitchText,
  positiveIntEnvWithSource,
  projectRootDir,
  providerAuditBase,
  resolveClaudeBin,
  resolveCodexBin,
  resolveCursorInvocation,
  resolveEffortSelection,
  resolveEngineSpecificModel,
  resolveModelSelection,
  resolveSessionModel,
  resolveSpawnCeiling,
  resolveSpeedSelection,
  resolveVisibilitySelection,
  visibilityGuidance,
} from "./fanout-policy.js";
import { registerFanoutToolHandlers } from "./fanout-registration.js";

const TASK_STATUS_ICONS = {
  pending: "[ ]",
  running: "[>]",
  completed: "[x]",
  failed: "[!]",
  interrupted: "[~]",
};

// Per-task text cap in fanout_results; task output stays on disk.
const RESULT_CAP_CHARS = 20000;
const DEFAULT_FANOUT_OUTPUT_BYTES = 1024 * 1024;

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

// ---------------------------------------------------------------------------
// Subprocess plumbing (local CLI and command engines)
// ---------------------------------------------------------------------------

// Spawns either a binary with args or a shell command template, writes the
// prompt to stdin, collects bounded stdout and stderr, and enforces a kill
// timer at the per-worker timeout. POSIX workers run in a fresh process group
// so shell-engine grandchildren are killed with their parent worker.
function runSubprocess(options) {
  return new Promise((resolve) => {
    let child;
    try {
      const baseSpawnOptions = {
        cwd: options.cwd,
        env: options.env,
        stdio: ["pipe", "pipe", "pipe"],
        detached: process.platform !== "win32",
      };
      child =
        options.shellCommand !== undefined
          ? spawn(options.shellCommand, {
              ...baseSpawnOptions,
              shell: true,
            })
          : spawn(options.bin, options.args, baseSpawnOptions);
    } catch (err) {
      resolve({ exitCode: -1, stdout: "", stderr: "", timedOut: false, spawnError: err.message });
      return;
    }
    let stdout = "";
    let stderr = "";
    let outputBytes = 0;
    let outputLimitExceeded = false;
    const outputBytesCap = intEnv("MYTHIFY_FANOUT_OUTPUT_BYTES", DEFAULT_FANOUT_OUTPUT_BYTES);
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
    const killWorkerTree = () => {
      try {
        if (process.platform !== "win32" && child.pid) {
          process.kill(-child.pid, "SIGKILL");
        } else {
          child.kill("SIGKILL");
        }
      } catch {
        try {
          child.kill("SIGKILL");
        } catch {
          // The worker tree may already be gone.
        }
      }
    };
    const killForOutputLimit = () => {
      if (outputLimitExceeded) {
        return;
      }
      outputLimitExceeded = true;
      killWorkerTree();
    };
    const captureChunk = (streamName, chunk) => {
      const text = String(chunk);
      const chunkBytes = Buffer.byteLength(text, "utf8");
      const remaining = Math.max(0, outputBytesCap - outputBytes);
      if (remaining > 0) {
        const kept = Buffer.from(text, "utf8").subarray(0, remaining).toString("utf8");
        if (streamName === "stdout") {
          stdout += kept;
        } else {
          stderr += kept;
        }
      }
      outputBytes += chunkBytes;
      if (outputBytes > outputBytesCap) {
        killForOutputLimit();
      }
    };
    const killTimer = setTimeout(() => {
      if (outputLimitExceeded) {
        return;
      }
      timedOut = true;
      killWorkerTree();
    }, Math.round(options.timeoutSeconds * 1000));
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      captureChunk("stdout", chunk);
    });
    child.stderr.on("data", (chunk) => {
      captureChunk("stderr", chunk);
    });
    child.on("error", (err) =>
      finish({
        exitCode: -1,
        stdout,
        stderr,
        timedOut,
        outputLimitExceeded,
        outputBytes,
        outputBytesCap,
        spawnError: err.message,
      })
    );
    child.on("close", (code) =>
      finish({
        exitCode: code === null ? -1 : code,
        stdout,
        stderr,
        timedOut,
        outputLimitExceeded,
        outputBytes,
        outputBytesCap,
      })
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

function outputLimitFailure(stdout, stderr, outputBytesCap, outputBytes) {
  const stderrTail = stderr.trim() === "" ? "" : ` stderr (tail): ${stderr.slice(-2000)}`;
  return {
    ok: false,
    output: stdout,
    error:
      `Worker output exceeded ${outputBytesCap} bytes (MYTHIFY_FANOUT_OUTPUT_BYTES) and was killed after ${outputBytes} bytes. ` +
      `Reduce worker output or raise MYTHIFY_FANOUT_OUTPUT_BYTES.${stderrTail}`,
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
  if (res.outputLimitExceeded) {
    return outputLimitFailure(res.stdout, res.stderr, res.outputBytesCap, res.outputBytes);
  }
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
  if (res.outputLimitExceeded) {
    return outputLimitFailure(output, res.stderr, res.outputBytesCap, res.outputBytes);
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
  if (res.outputLimitExceeded) {
    return outputLimitFailure(res.stdout, res.stderr, res.outputBytesCap, res.outputBytes);
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
  if (res.outputLimitExceeded) {
    return outputLimitFailure(res.stdout, res.stderr, res.outputBytesCap, res.outputBytes);
  }
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

// --- Worktree isolation -----------------------------------------------------
// A task with isolation "worktree" runs in its own git worktree on a fresh
// branch, so parallel writing workers cannot collide on the same files. When
// the worker changed nothing the worktree and branch are removed; when it did,
// they are left for the host to inspect and merge. Falls back to the shared
// project root (with a note) when the project is not a git repository.

function gitInsideWorkTree(projectRoot) {
  const r = spawnSync("git", ["-C", projectRoot, "rev-parse", "--is-inside-work-tree"], {
    encoding: "utf8",
  });
  return r.status === 0 && String(r.stdout).trim() === "true";
}

function setupWorktree(projectRoot, label) {
  if (!gitInsideWorkTree(projectRoot)) {
    return { ok: false, note: "project is not a git repository; ran in the shared project root" };
  }
  const branch = `mythify/fanout-${label}-${crypto.randomBytes(3).toString("hex")}`;
  let dir;
  try {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-fanout-wt-"));
  } catch (err) {
    return { ok: false, note: `could not create worktree dir: ${err.message}` };
  }
  const r = spawnSync("git", ["-C", projectRoot, "worktree", "add", "-b", branch, dir, "HEAD"], {
    encoding: "utf8",
  });
  if (r.status !== 0) {
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch {
      // best effort
    }
    return { ok: false, note: `git worktree add failed: ${String(r.stderr || "").trim()}` };
  }
  return { ok: true, dir, branch };
}

function teardownWorktree(projectRoot, worktree) {
  const status = spawnSync("git", ["-C", worktree.dir, "status", "--porcelain"], {
    encoding: "utf8",
  });
  const changed = status.status === 0 && String(status.stdout).trim() !== "";
  if (changed) {
    // Commit the worker's changes onto its branch so the diff is a real,
    // mergeable commit in the main repo's object store, not uncommitted state
    // stranded in a temp dir the OS may reap. Use an explicit identity so the
    // commit succeeds even when the repo has none configured.
    spawnSync("git", ["-C", worktree.dir, "add", "-A"], { encoding: "utf8" });
    const commit = spawnSync(
      "git",
      [
        "-C", worktree.dir,
        "-c", "user.email=fanout@mythify.local",
        "-c", "user.name=mythify-fanout",
        "commit", "--no-gpg-sign", "-m", `fanout worker ${worktree.branch}`,
      ],
      { encoding: "utf8" }
    );
    if (commit.status !== 0) {
      // Could not commit; leave everything in place for the host to recover.
      return {
        isolated: true,
        branch: worktree.branch,
        path: worktree.dir,
        changed: true,
        committed: false,
        cleanup_failed: true,
        note: String(commit.stderr || "commit failed").trim(),
      };
    }
    // The branch now carries the commit; the temp worktree can be removed.
    const remove = spawnSync(
      "git", ["-C", projectRoot, "worktree", "remove", "--force", worktree.dir],
      { encoding: "utf8" }
    );
    return {
      isolated: true,
      branch: worktree.branch,
      path: remove.status === 0 ? null : worktree.dir,
      changed: true,
      committed: true,
      cleanup_failed: remove.status !== 0,
    };
  }
  const remove = spawnSync(
    "git", ["-C", projectRoot, "worktree", "remove", "--force", worktree.dir],
    { encoding: "utf8" }
  );
  const branchDelete = spawnSync(
    "git", ["-C", projectRoot, "branch", "-D", worktree.branch],
    { encoding: "utf8" }
  );
  const cleaned = remove.status === 0 && branchDelete.status === 0;
  return {
    isolated: true,
    branch: cleaned ? null : worktree.branch,
    path: cleaned ? null : worktree.dir,
    changed: false,
    cleanup_failed: !cleaned,
  };
}

async function runOneTask(job, jobDir, task, prompt, timeoutSeconds, projectRoot) {
  task.status = "running";
  task.started_at = io.isoNow();
  let effectiveRoot = projectRoot;
  let worktree = null;
  if (task.isolation === "worktree") {
    const setup = setupWorktree(projectRoot, `${job.id}-t${task.id}`);
    if (setup.ok) {
      worktree = setup;
      effectiveRoot = setup.dir;
      task.worktree = { isolated: true, branch: setup.branch, path: setup.dir };
    } else {
      task.worktree = { isolated: false, note: setup.note };
    }
  }
  saveJob(job, jobDir);
  appendProviderAudit({
    ...providerAuditBase(job, task, prompt),
    event: "fanout_task_started",
    status: task.status,
  });
  const startedNs = process.hrtime.bigint();
  try {
    let outcome;
    try {
      outcome = await runWorker(
        task.engine,
        prompt,
        task.model,
        task.effort,
        task.speed,
        timeoutSeconds,
        effectiveRoot
      );
    } catch (err) {
      outcome = {
        ok: false,
        output: "",
        error: `Internal worker error: ${err && err.message ? err.message : String(err)}`,
      };
    }
    const durationSeconds = Number((Number(process.hrtime.bigint() - startedNs) / 1e9).toFixed(3));
    // Redact worker output before it is persisted or returned. Unattended fanout
    // logs are exactly where the article warns secrets a worker echoes would leak.
    const outputText = redactSensitiveOutput(typeof outcome.output === "string" ? outcome.output : "");
    if (typeof outcome.output === "string") {
      outcome.output = outputText;
    }
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
    // Redact the error channel at the same choke point as the output: worker
    // stderr tails end up here and can carry the same secrets.
    task.error = outcome.ok
      ? null
      : redactSensitiveOutput(outcome.error || "Worker failed with no error detail.");
    task.output_bytes = outputBytes;
  } finally {
    // Always tear the worktree down, even if the worker or a save threw, so an
    // isolated task can never leak a worktree or branch.
    if (worktree) {
      task.worktree = teardownWorktree(projectRoot, worktree);
    }
    saveJob(job, jobDir);
    appendProviderAudit({
      ...providerAuditBase(job, task, prompt),
      event: "fanout_task_finished",
      status: task.status,
      duration_seconds: task.duration_seconds,
      output_metadata: {
        output_file: task.output_file,
        output_bytes: task.output_bytes || 0,
        output_redacted: true,
        error_redacted: true,
        error_present: task.error != null,
      },
    });
  }
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
  return names.filter(isFanoutJobId).sort();
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
  if (!isFanoutJobId(id)) {
    return { error: `[FAIL] Invalid fanout job id "${id}".` };
  }
  const jobDir = path.join(fanoutRootDir(), id);
  const job = io.readJsonRecover(path.join(jobDir, "job.json"), () => null);
  if (job === null || typeof job !== "object" || job.id !== id || !Array.isArray(job.tasks)) {
    return {
      error:
        `[FAIL] No fanout job "${id}" found (or its job.json is missing or corrupt). ` +
        "Call fanout_status with no job_id for the most recent job, or start a new one with fanout_start.",
    };
  }
  let interruptedNote = null;
  const unfinished = job.tasks.filter((t) => t.status === "running" || t.status === "pending");
  if (unfinished.length > 0 && !jobRegistry.has(job.id)) {
    const root = projectRootDir();
    for (const task of unfinished) {
      task.status = "interrupted";
      task.error =
        "Interrupted: the MCP server process that ran this job exited before the task finished.";
      if (task.started_at !== null && task.finished_at === null) {
        task.finished_at = io.isoNow();
      }
      const wt = task.worktree;
      if (wt && wt.isolated && wt.path) {
        task.worktree = recoverInterruptedWorktree(root, id, task, wt);
      }
    }
    spawnSync("git", ["-C", root, "worktree", "prune"], { encoding: "utf8" });
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
    const taskIsolation = task.isolation === "worktree" ? "worktree" : "none";
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
      isolation: taskIsolation,
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
  const claudeCliWorkerSelected = resolvedTasks.some((resolved) => resolved.engine === "claude-cli");
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
        isolation: resolved.isolation,
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
  if (claudeCliWorkerSelected) {
    lines.push(`[WARN] ${CLAUDE_CLI_COST_WARNING}`);
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
    const outputPath = taskOutputPath(jobDir, task);
    if (outputPath === null) {
      lines.push("[FAIL] Invalid persisted task output path; output was not read.");
      continue;
    }
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
        `[WARN] Output truncated at ${RESULT_CAP_CHARS} characters; task output (${task.output_bytes} bytes) is in ${outputPath}.`
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
  configureFanoutPolicy(deps);
  registerFanoutToolHandlers(server, deps, {
    handleFanoutStart,
    handleFanoutStatus,
    handleFanoutResults,
  });
}
