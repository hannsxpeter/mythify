import fs from "node:fs";
import path from "node:path";

const WORKER_PREAMBLE = [
  "You are a delegated worker executing one self-contained task for an orchestrating agent.",
  "The task below is complete on its own: you have no access to the orchestrator's conversation and no other task's output.",
  "Do not ask questions and do not request clarification; if something is ambiguous, make the most reasonable assumption and proceed.",
  "Return only the deliverable the task asks for.",
].join("\n");

function isPathInside(parent, child) {
  const relative = path.relative(parent, child);
  return (
    relative === "" ||
    (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))
  );
}

function resolveContextPath(rawPath, projectRoot) {
  const root = path.resolve(projectRoot);
  const resolved = path.isAbsolute(rawPath) ? path.resolve(rawPath) : path.resolve(root, rawPath);
  if (!isPathInside(root, resolved)) {
    return {
      error: `context path "${rawPath}" resolves outside the project root (${root}). Use a path within the project root.`,
    };
  }
  if (fs.existsSync(resolved)) {
    const realRoot = fs.realpathSync(root);
    const realTarget = fs.realpathSync(resolved);
    if (!isPathInside(realRoot, realTarget)) {
      return {
        error: `context path "${rawPath}" resolves outside the project root (${realRoot}). Use a path within the project root.`,
      };
    }
  }
  return { resolved };
}

// Fixed preamble, then each context file as a labeled fenced block, then the
// task prompt. Context paths resolve inside the project root. Total inlined
// context is capped and an unreadable path is a validation error.
export function assembleWorkerPrompt(task, projectRoot, contextBytesCap) {
  const parts = [WORKER_PREAMBLE];
  if (typeof task.effort === "string" && task.effort !== "") {
    parts.push(
      `Requested effort: ${task.effort}. Match the depth and rigor to this level while keeping the requested deliverable format.`
    );
  }
  if (typeof task.speed === "string" && task.speed !== "" && task.speed !== "auto") {
    parts.push(
      `Requested speed: ${task.speed}. Prefer this latency setting for any platform-specific model controls when available.`
    );
  }
  let remaining = contextBytesCap;
  for (const rawPath of task.context_paths || []) {
    const checkedPath = resolveContextPath(rawPath, projectRoot);
    if (checkedPath.error) {
      return { error: checkedPath.error };
    }
    const resolved = checkedPath.resolved;
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
