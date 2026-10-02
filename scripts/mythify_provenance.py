"""Best-effort source provenance for executed verification records."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


# fsmonitor and the untracked cache let git answer from cached state instead of
# reading the worktree, so every inspection that feeds evidence turns them off.
GIT_FRESH_READ = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false")


def git_environment():
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def _git_bytes(root, args, environment, timeout=30):
    """stdout of `git ARGS` run in ROOT with fresh reads, or None on failure."""
    try:
        run = subprocess.run(
            ["git", *GIT_FRESH_READ, *args],
            cwd=str(root), capture_output=True, timeout=timeout, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return run.stdout if run.returncode == 0 else None


def git_flagged_index_paths(root, environment=None):
    """Index entries git was told to stop checking, or None when git fails.

    An assume-unchanged entry (a lowercase `ls-files -v` tag) or a
    skip-worktree entry (tag S) hides a worktree edit from `git diff` and
    `git status`, so no fingerprint built on them is trusted while one exists.
    A skip-worktree entry counts only when its file is on disk: sparse
    checkouts flag every file outside the cone, and those files are absent.
    """
    raw = _git_bytes(root, ["ls-files", "-v", "-z"], environment or git_environment())
    if raw is None:
        return None
    flagged = []
    for entry in raw.split(b"\0"):
        tag, path = entry[:1], os.fsdecode(entry[2:])
        if len(entry) > 2 and (
            tag.islower() or (tag == b"S" and os.path.lexists(os.path.join(str(root), path)))
        ):
            flagged.append(path)
    return flagged


def git_ignore_rules_digest(root, environment=None):
    """sha256 over the ignore rules that leave no trace in the diff, or None.

    `.git/info/exclude`, the global excludes file, and an ignored `.gitignore`
    (one that hides itself) can each hide a new file from every listing
    without changing tracked content, so a fingerprint covers their bytes.
    """
    environment = environment or git_environment()
    info = _git_bytes(root, ["rev-parse", "--git-path", "info/exclude"], environment)
    ignored = _git_bytes(
        root,
        ["ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"],
        environment,
    )
    if info is None or ignored is None:
        return None
    try:
        configured = subprocess.run(
            ["git", "config", "--path", "--get", "core.excludesFile"],
            cwd=str(root), capture_output=True, timeout=30, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if configured.returncode == 0:
        global_path = Path(os.fsdecode(configured.stdout.strip()))
    elif configured.returncode == 1:
        config_home = environment.get("XDG_CONFIG_HOME") or os.path.join(
            environment.get("HOME", ""), ".config"
        )
        global_path = Path(config_home) / "git" / "ignore"
    else:
        return None
    sources = [Path(root) / os.fsdecode(info.strip()), global_path]
    sources.extend(
        Path(root) / os.fsdecode(entry)
        for entry in sorted(ignored.split(b"\0"))
        if entry == b".gitignore" or entry.endswith(b"/.gitignore")
    )
    digest = hashlib.sha256()
    for source in sources:
        digest.update(os.fsencode(str(source)) + b"\0")
        try:
            digest.update(hashlib.sha256(source.read_bytes()).digest())
        except OSError:
            digest.update(b"absent")
        digest.update(b"\0")
    return digest.hexdigest()


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
            ["git", *GIT_FRESH_READ, "status", "--porcelain", "--untracked-files=all"],
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
    """Hash tracked changes plus untracked file content for exact-change proof.

    Returns None (fingerprint unavailable, so proof is refused) while an index
    entry is flagged assume-unchanged or skip-worktree. The ignore rules that
    live outside the worktree are folded in, so editing them changes the
    digest; files ignored by the repository's .gitignore files stay outside it.
    """
    environment = git_environment()
    flagged = git_flagged_index_paths(root, environment)
    rules = git_ignore_rules_digest(root, environment)
    diff = _git_bytes(root, ["diff", "--binary", "--no-ext-diff", "HEAD", "--"], environment)
    untracked = _git_bytes(root, ["ls-files", "--others", "--exclude-standard", "-z"], environment)
    if flagged is None or flagged or rules is None or diff is None or untracked is None:
        return None
    raw_paths = sorted(item for item in untracked.split(b"\0") if item)
    blob_ids = _hash_objects(root, [os.fsdecode(raw) for raw in raw_paths], environment)
    if blob_ids is None:
        return None
    digest = hashlib.sha256()
    digest.update(b"rules\0" + rules.encode("ascii") + b"\0")
    digest.update(b"tracked\0")
    digest.update(diff)
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
