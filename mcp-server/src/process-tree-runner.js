import fs from "node:fs";
import { spawn } from "node:child_process";
import { terminateProcessTree } from "./process-tree.js";

const config = JSON.parse(fs.readFileSync(0, "utf8"));
const maxOutputBytes = Number(config.max_output_bytes);
const tailBytes = 8192;
let stdoutTail = Buffer.alloc(0);
let stderrTail = Buffer.alloc(0);
const stdoutFull = [];
const stderrFull = [];
let outputBytes = 0;
let stdoutSourceBytes = 0;
let stderrSourceBytes = 0;
let timedOut = false;
let outputLimitExceeded = false;
let containmentFailed = false;
let settled = false;

function appendTail(current, chunk) {
  const combined = Buffer.concat([current, chunk]);
  return combined.length <= tailBytes ? combined : combined.subarray(combined.length - tailBytes);
}

function containChildTree() {
  if (!terminateProcessTree(child)) {
    containmentFailed = true;
  }
}

function finish(payload) {
  if (settled) {
    return;
  }
  settled = true;
  process.stdout.write(JSON.stringify({
    stdout: stdoutTail.toString("utf8"),
    stderr: stderrTail.toString("utf8"),
    stdout_full: Buffer.concat(stdoutFull).toString("utf8"),
    stderr_full: Buffer.concat(stderrFull).toString("utf8"),
    stdout_source_bytes: stdoutSourceBytes,
    stderr_source_bytes: stderrSourceBytes,
    timed_out: timedOut,
    output_limit_exceeded: outputLimitExceeded,
    containment_failed: containmentFailed,
    ...payload,
  }));
}

const child = spawn(String(config.command || ""), {
  shell: true,
  detached: process.platform !== "win32",
  stdio: ["ignore", "pipe", "pipe"],
  windowsHide: true,
});

const timer = setTimeout(() => {
  timedOut = true;
  containChildTree();
}, Math.max(1, Math.round(Number(config.timeout_seconds) * 1000)));

function capture(channel, chunk) {
  const remaining = Math.max(0, maxOutputBytes - outputBytes);
  const retained = remaining > 0 ? chunk.subarray(0, remaining) : Buffer.alloc(0);
  outputBytes += chunk.length;
  if (channel === "stdout") {
    stdoutSourceBytes += chunk.length;
    stdoutTail = appendTail(stdoutTail, chunk);
    if (retained.length > 0) stdoutFull.push(retained);
  } else {
    stderrSourceBytes += chunk.length;
    stderrTail = appendTail(stderrTail, chunk);
    if (retained.length > 0) stderrFull.push(retained);
  }
  if (!outputLimitExceeded && outputBytes > maxOutputBytes) {
    outputLimitExceeded = true;
    containChildTree();
  }
}

child.stdout.on("data", (chunk) => capture("stdout", chunk));
child.stderr.on("data", (chunk) => capture("stderr", chunk));
child.on("error", (error) => {
  clearTimeout(timer);
  finish({ status: null, signal: null, error: error.message });
});
child.on("close", (code, signal) => {
  clearTimeout(timer);
  finish({ status: code, signal, error: null });
});
