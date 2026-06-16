import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { ADAPTER_CANDIDATES } from "./capability-registry.js";

function tailText(text, limit = 4000) {
  const value = String(text || "");
  if (value.length <= limit) {
    return value;
  }
  return value.slice(value.length - limit);
}

function envValue(name) {
  return (process.env[name] || "").trim();
}

function isExecutableFile(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function findExecutableOnPath(binaryName) {
  const pathValue = process.env.PATH || "";
  for (const entry of pathValue.split(path.delimiter)) {
    if (!entry) {
      continue;
    }
    const candidate = path.join(entry, binaryName);
    if (isExecutableFile(candidate)) {
      return candidate;
    }
  }
  return null;
}

export const HOST_CLI_IDS = ["kimi-code", "opencode", "antigravity"];

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
    if (!isExecutableFile(explicit)) {
      return {
        bin: "",
        source: "explicit",
        error: `Configured binary is not executable: ${explicit}`,
      };
    }
    const allowedBasenames = new Set([
      ...config.binaryNames,
      ...config.fallbacks.map((candidate) => path.basename(candidate)),
    ]);
    const explicitBasename = path.basename(explicit);
    if (!allowedBasenames.has(explicitBasename)) {
      return {
        bin: "",
        source: "explicit",
        error:
          `Explicit binary is not allowed for ${host}: ${explicit}. ` +
          `Expected one of: ${Array.from(allowedBasenames).sort().join(", ")}`,
      };
    }
    return { bin: explicit, source: "explicit", error: "" };
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

export function probeHostCli({ host, bin, timeout_seconds }) {
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

export function formatHostCliProbe(result) {
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

function resolveHostCliRunCwd(rawCwd, defaultCwd) {
  const selected = String(rawCwd || "").trim();
  const resolved = selected === "" ? path.resolve(defaultCwd || process.cwd()) : path.resolve(selected);
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

export function runHostCliWorker({ host, bin, prompt, cwd, timeout_seconds, model, agent, default_cwd }) {
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
  const cwdResult = resolveHostCliRunCwd(cwd, default_cwd);
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

export function formatHostCliRun(result) {
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
