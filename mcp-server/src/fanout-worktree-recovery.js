import { spawnSync } from "node:child_process";

import { isOwnedRecoveryWorktree } from "./fanout-paths.js";

function registeredWorktrees(projectRoot, runGit) {
  const listed = runGit("git", ["-C", projectRoot, "worktree", "list", "--porcelain"], {
    encoding: "utf8",
  });
  if (listed.status !== 0) {
    return [];
  }
  const registrations = [];
  let current = null;
  for (const line of String(listed.stdout || "").split(/\r?\n/)) {
    if (line.startsWith("worktree ")) {
      current = { path: line.slice("worktree ".length), branch: null };
      registrations.push(current);
    } else if (current && line.startsWith("branch refs/heads/")) {
      current.branch = line.slice("branch refs/heads/".length);
    }
  }
  return registrations;
}

export function recoverInterruptedWorktree(
  projectRoot,
  jobId,
  task,
  worktree,
  runGit = spawnSync
) {
  const original = { path: worktree.path, branch: worktree.branch };
  if (!isOwnedRecoveryWorktree(jobId, task, worktree, registeredWorktrees(projectRoot, runGit))) {
    return {
      ...worktree,
      cleaned_on_recovery: false,
      cleanup_failed: true,
      recovery_original: original,
      note: "Recovery cleanup refused: persisted worktree metadata is not owned by this job task.",
    };
  }
  const removed = runGit(
    "git", ["-C", projectRoot, "worktree", "remove", "--force", worktree.path],
    { encoding: "utf8" }
  );
  if (removed.status !== 0) {
    return {
      ...worktree,
      cleaned_on_recovery: false,
      cleanup_failed: true,
      recovery_original: original,
      note: `Recovery worktree removal failed: ${String(removed.stderr || "").trim()}`,
    };
  }
  const deleted = runGit(
    "git", ["-C", projectRoot, "branch", "-D", worktree.branch],
    { encoding: "utf8" }
  );
  if (deleted.status !== 0) {
    return {
      ...worktree,
      path: null,
      cleaned_on_recovery: false,
      cleanup_failed: true,
      recovery_original: original,
      note: `Recovery branch deletion failed: ${String(deleted.stderr || "").trim()}`,
    };
  }
  return {
    ...worktree,
    path: null,
    branch: null,
    cleaned_on_recovery: true,
    cleanup_failed: false,
    recovery_original: original,
  };
}
