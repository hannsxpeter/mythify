import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { ADAPTER_CANDIDATES } from "./capability-registry.js";
import { redactSensitiveOutput } from "./redact.js";

function isoNow() {
  return new Date().toISOString();
}

function tailText(text, limit = 4000) {
  const value = redactSensitiveOutput(String(text || ""));
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

export const EXECUTION_ADAPTER_IDS = ["google-colab-cli"];

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

export function probeExecutionAdapter({ adapter, bin, timeout_seconds }) {
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

export function formatExecutionProbe(result) {
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

export const COLAB_GPU_ACCELERATORS = ["T4", "L4", "G4", "H100", "A100"];
export const COLAB_TPU_ACCELERATORS = ["v5e1", "v6e1"];

function resolveExecutionRunCwd(rawCwd, defaultCwd) {
  const selected = String(rawCwd || "").trim();
  const resolved = selected === "" ? path.resolve(defaultCwd || process.cwd()) : path.resolve(selected);
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

export function runExecutionAdapter({
  adapter,
  bin,
  script_path,
  cwd,
  default_cwd,
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
  const cwdResult = resolveExecutionRunCwd(cwd, default_cwd);
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

export function formatExecutionRun(result) {
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
