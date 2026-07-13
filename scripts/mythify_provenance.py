"""Best-effort source provenance for executed verification records."""

from __future__ import annotations

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


def current_verification_provenance(version, state=None, root=None):
    project_root = Path(root) if root is not None else project_root_for_state(state)
    return {
        "git_commit": git_commit(project_root),
        "worktree_clean": git_worktree_clean(project_root),
        "mythify_version": str(version),
    }


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
