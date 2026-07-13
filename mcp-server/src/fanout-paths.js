import path from "node:path";
import os from "node:os";
import fs from "node:fs";

const FANOUT_JOB_ID_PATTERN = /^fo-\d{14}-[0-9a-f]{4}$/;

function canonicalPath(value) {
  try {
    return fs.realpathSync.native(value);
  } catch {
    return path.resolve(value);
  }
}

export function isFanoutJobId(value) {
  return FANOUT_JOB_ID_PATTERN.test(String(value));
}

export function taskOutputPath(jobDir, task) {
  const outputFile = String(task && task.output_file ? task.output_file : "");
  const expected = `task-${task && task.id}-output.md`;
  if (outputFile !== expected || path.basename(outputFile) !== outputFile) {
    return null;
  }
  const root = path.resolve(jobDir);
  const candidate = path.resolve(root, outputFile);
  return candidate.startsWith(root + path.sep) ? candidate : null;
}

export function isOwnedRecoveryWorktree(jobId, task, worktree, registrations) {
  if (
    !isFanoutJobId(jobId) ||
    !task ||
    !Number.isInteger(task.id) ||
    !worktree ||
    typeof worktree.path !== "string" ||
    typeof worktree.branch !== "string"
  ) {
    return false;
  }
  const branchPrefix = `mythify/fanout-${jobId}-t${task.id}-`;
  const branchSuffix = worktree.branch.slice(branchPrefix.length);
  if (!worktree.branch.startsWith(branchPrefix) || !/^[0-9a-f]{6}$/.test(branchSuffix)) {
    return false;
  }
  const worktreePath = canonicalPath(worktree.path);
  if (
    path.dirname(worktreePath) !== canonicalPath(os.tmpdir()) ||
    !/^mythify-fanout-wt-[A-Za-z0-9_-]+$/.test(path.basename(worktreePath))
  ) {
    return false;
  }
  return registrations.some(
    (entry) => canonicalPath(entry.path) === worktreePath && entry.branch === worktree.branch
  );
}
