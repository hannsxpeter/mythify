import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const PACKAGE_JSON = JSON.parse(
  fs.readFileSync(new URL("../package.json", import.meta.url), "utf8")
);

export function projectRootFromStateDir(stateDir) {
  return path.basename(stateDir) === ".mythify" ? path.dirname(stateDir) : process.cwd();
}

export function gitCommit(root) {
  const result = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
    timeout: 10000,
    env: { ...process.env, GIT_OPTIONAL_LOCKS: "0" },
  });
  if (result.error || result.status !== 0) {
    return null;
  }
  return String(result.stdout || "").trim() || null;
}

export function gitWorktreeClean(root) {
  const result = spawnSync("git", ["status", "--porcelain", "--untracked-files=all"], {
    cwd: root,
    encoding: "utf8",
    timeout: 10000,
    env: { ...process.env, GIT_OPTIONAL_LOCKS: "0" },
  });
  if (result.error || result.status !== 0) {
    return null;
  }
  return String(result.stdout || "").trim() === "";
}

export function currentVerificationProvenance(root = process.cwd()) {
  return {
    git_commit: gitCommit(root),
    worktree_clean: gitWorktreeClean(root),
    mythify_version: PACKAGE_JSON.version,
  };
}

export function currentVerificationProvenanceForStateDir(stateDir) {
  return currentVerificationProvenance(projectRootFromStateDir(stateDir));
}

export function verificationFreshness(record, current) {
  const provenance = record &&
    typeof record.provenance === "object" &&
    !Array.isArray(record.provenance)
    ? record.provenance
    : null;
  if (!provenance) {
    return { status: "legacy", reason: "missing_provenance" };
  }
  if (
    current?.mythify_version &&
    provenance.mythify_version !== current.mythify_version
  ) {
    return { status: "stale", reason: "mythify_version_mismatch" };
  }
  if (!current?.git_commit) {
    return { status: "stale", reason: "current_git_commit_unavailable" };
  }
  if (provenance.git_commit !== current.git_commit) {
    return { status: "stale", reason: "git_commit_mismatch" };
  }
  if (current.worktree_clean !== true) {
    return { status: "stale", reason: "current_worktree_dirty" };
  }
  if (provenance.worktree_clean !== true) {
    return {
      status: "stale",
      reason: provenance.worktree_clean === false
        ? "recorded_worktree_dirty"
        : "recorded_worktree_cleanliness_unavailable",
    };
  }
  return { status: "fresh", reason: "provenance_matches" };
}

// Reason the world visibly moved between the record's run and now, or null.
// Deliberately narrower than verificationFreshness: a dev-loop worktree is
// dirty both when the verifier runs and when the step completes, and that
// indeterminate case stays silent. Only visible movement counts: the commit
// changed since the run, or a clean-at-run tree became dirty.
export function evidenceMovedSinceRun(record, current) {
  const provenance = record &&
    typeof record.provenance === "object" &&
    !Array.isArray(record.provenance)
    ? record.provenance
    : null;
  if (!provenance || !current || typeof current !== "object") {
    return null;
  }
  const recordCommit = provenance.git_commit;
  const currentCommit = current.git_commit;
  if (recordCommit && currentCommit && recordCommit !== currentCommit) {
    return "git_commit_changed_since_run";
  }
  if (provenance.worktree_clean === true && current.worktree_clean === false) {
    return "worktree_changed_since_run";
  }
  return null;
}
