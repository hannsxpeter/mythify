"""Best-effort source provenance for executed verification records."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def project_root_for_state(state):
    state_path = Path(state)
    return state_path.parent if state_path.name == ".mythify" else Path.cwd()


def git_commit(root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def git_worktree_clean(root):
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return not bool(result.stdout.strip())


def git_worktree_digest(root):
    """Hash tracked changes plus untracked file content for exact-change proof."""
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        diff = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
            cwd=str(root), capture_output=True, timeout=30, env=environment,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=str(root), capture_output=True, timeout=30, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if diff.returncode != 0 or untracked.returncode != 0:
        return None
    digest = hashlib.sha256()
    digest.update(b"tracked\0")
    digest.update(diff.stdout)
    for raw_path in sorted(item for item in untracked.stdout.split(b"\0") if item):
        file_path = os.fsdecode(raw_path)
        try:
            blob = subprocess.run(
                ["git", "hash-object", "--no-filters", "--", file_path],
                cwd=str(root), capture_output=True, timeout=30, env=environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if blob.returncode != 0:
            return None
        digest.update(b"untracked\0")
        digest.update(raw_path)
        digest.update(b"\0")
        digest.update(blob.stdout.strip())
        digest.update(b"\0")
    return digest.hexdigest()


def current_verification_provenance(version, state=None, root=None):
    project_root = Path(root) if root is not None else project_root_for_state(state)
    return {
        "git_commit": git_commit(project_root),
        "worktree_clean": git_worktree_clean(project_root),
        "worktree_digest": git_worktree_digest(project_root),
        "mythify_version": str(version),
    }


def evidence_moved_since_run(record, current):
    """Reason the world visibly moved between RECORD's run and now, or None.

    Deliberately narrower than verification_freshness: a dev-loop worktree is
    dirty both when the verifier runs and when the step completes, and that
    indeterminate case stays silent. Only visible movement counts: the commit
    changed since the run, or a clean-at-run tree became dirty.
    """
    provenance = record.get("provenance") if isinstance(record, dict) else None
    if not isinstance(provenance, dict) or not isinstance(current, dict):
        return None
    record_commit = provenance.get("git_commit")
    current_commit = current.get("git_commit")
    if record_commit and current_commit and record_commit != current_commit:
        return "git_commit_changed_since_run"
    if provenance.get("worktree_clean") is True and current.get("worktree_clean") is False:
        return "worktree_changed_since_run"
    record_digest = provenance.get("worktree_digest")
    current_digest = current.get("worktree_digest")
    if record_digest and current_digest and record_digest != current_digest:
        return "worktree_digest_changed_since_run"
    return None


def verification_freshness(record, current):
    provenance = record.get("provenance") if isinstance(record, dict) else None
    if not isinstance(provenance, dict):
        return {"status": "legacy", "reason": "missing_provenance"}

    current_version = current.get("mythify_version") if isinstance(current, dict) else None
    record_version = provenance.get("mythify_version")
    if current_version and record_version != current_version:
        return {"status": "stale", "reason": "mythify_version_mismatch"}

    current_commit = current.get("git_commit") if isinstance(current, dict) else None
    record_commit = provenance.get("git_commit")
    if not current_commit:
        return {"status": "stale", "reason": "current_git_commit_unavailable"}
    if record_commit != current_commit:
        return {"status": "stale", "reason": "git_commit_mismatch"}
    if current.get("worktree_clean") is not True:
        return {"status": "stale", "reason": "current_worktree_dirty"}
    if provenance.get("worktree_clean") is not True:
        reason = (
            "recorded_worktree_dirty"
            if provenance.get("worktree_clean") is False
            else "recorded_worktree_cleanliness_unavailable"
        )
        return {"status": "stale", "reason": reason}

    return {"status": "fresh", "reason": "provenance_matches"}
