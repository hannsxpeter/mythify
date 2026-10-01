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


def _hash_objects(root, paths, environment):
    """Git blob ids for PATHS (relative str paths), or None on any failure.

    One `git hash-object --stdin-paths` call covers every ordinary name. Names
    that call would misread (a leading double quote is C-unquoted, and CR or LF
    split lines) are hashed one call each, so ids match a per-file run exactly.
    """
    batch = []
    single = []
    for path in paths:
        quoted_or_split = path.startswith('"') or "\n" in path or "\r" in path
        (single if quoted_or_split else batch).append(path)
    ids = {}
    try:
        if batch:
            run = subprocess.run(
                ["git", "hash-object", "--no-filters", "--stdin-paths"],
                cwd=str(root), capture_output=True, timeout=60, env=environment,
                input=os.fsencode("\n".join(batch) + "\n"),
            )
            lines = run.stdout.splitlines()
            if run.returncode != 0 or len(lines) != len(batch):
                return None
            ids.update(zip(batch, (line.strip() for line in lines)))
        for path in single:
            run = subprocess.run(
                ["git", "hash-object", "--no-filters", "--", path],
                cwd=str(root), capture_output=True, timeout=30, env=environment,
            )
            if run.returncode != 0:
                return None
            ids[path] = run.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return ids


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
    raw_paths = sorted(item for item in untracked.stdout.split(b"\0") if item)
    blob_ids = _hash_objects(root, [os.fsdecode(raw) for raw in raw_paths], environment)
    if blob_ids is None:
        return None
    digest = hashlib.sha256()
    digest.update(b"tracked\0")
    digest.update(diff.stdout)
    for raw_path in raw_paths:
        digest.update(b"untracked\0")
        digest.update(raw_path)
        digest.update(b"\0")
        digest.update(blob_ids[os.fsdecode(raw_path)])
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

    Deliberately narrow: a dev-loop worktree is dirty both when the verifier
    runs and when the step completes, and that indeterminate case stays
    silent. Only visible movement counts: the commit changed since the run, or
    a clean-at-run tree became dirty.
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
