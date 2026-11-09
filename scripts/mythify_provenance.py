"""Best-effort source provenance for executed verification records."""

from __future__ import annotations

import contextlib
import hashlib
import os
import subprocess
from pathlib import Path

from mythify_reuse import environment_digest


# fsmonitor and the untracked cache let git answer from cached state instead of
# reading the worktree, so every inspection that feeds evidence turns them off.
GIT_FRESH_READ = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false")


# Results of read-only git calls while a git_read_snapshot() block is open,
# keyed by directory, arguments, and input; None outside a block.
_SNAPSHOT = None


@contextlib.contextmanager
def git_read_snapshot():
    """Run each distinct read-only git call once inside the block.

    For a reader that inspects the tree several times with no write in
    between: step completion samples the provenance, then lists the changed
    paths, and a high-risk step samples the provenance again for the review
    check. Inside the block a repeated call (the same directory, arguments,
    and input) gets the first call's result instead of running git again.
    Outside a block every call runs git. A nested block joins the outer one.
    """
    global _SNAPSHOT
    if _SNAPSHOT is not None:
        yield
        return
    _SNAPSHOT = {}
    try:
        yield
    finally:
        _SNAPSHOT = None


def _run_git(root, argv, **options):
    """Run ARGV in ROOT, or return the open snapshot's result for the same call."""
    key = (str(root), tuple(argv), options.get("input"), bool(options.get("text")))
    if _SNAPSHOT is not None and key in _SNAPSHOT:
        outcome = _SNAPSHOT[key]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    try:
        outcome = subprocess.run(argv, cwd=str(root), capture_output=True, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if _SNAPSHOT is not None:
            _SNAPSHOT[key] = exc
        raise
    if _SNAPSHOT is not None:
        _SNAPSHOT[key] = outcome
    return outcome


def git_environment():
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def _git_bytes(root, args, environment, timeout=30):
    """stdout of `git ARGS` run in ROOT with fresh reads, or None on failure."""
    try:
        run = _run_git(root, ["git", *GIT_FRESH_READ, *args], timeout=timeout, env=environment)
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
        configured = _run_git(
            root, ["git", "config", "--path", "--get", "core.excludesFile"],
            timeout=30, env=environment,
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


def git_inside_worktree(root):
    """True when ROOT is inside a git work tree."""
    output = _git_bytes(root, ["rev-parse", "--is-inside-work-tree"], git_environment())
    return output is not None and output.strip() == b"true"


def project_root_for_state(state):
    state_path = Path(state)
    return state_path.parent if state_path.name == ".mythify" else Path.cwd()


def state_exclude_pathspec(state, root):
    """A git pathspec that leaves STATE out, or [] when STATE is outside ROOT.

    Every ledger append writes under the state directory, so a fingerprint
    that covered it would move on each record whenever `.mythify/` is tracked
    or not ignored. An exclude-only pathspec keeps git's default scope.
    """
    if state is None:
        return []
    try:
        relative = os.path.relpath(str(Path(state).resolve()), str(Path(root).resolve()))
    except (OSError, ValueError):
        return []
    if relative == "." or relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return []
    return ["--", ":(exclude)" + relative.replace(os.sep, "/")]


def git_commit(root):
    try:
        result = _run_git(
            root, ["git", "rev-parse", "HEAD"],
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


def git_worktree_clean(root, exclude=()):
    try:
        result = _run_git(
            root, ["git", *GIT_FRESH_READ, "status", "--porcelain", "--untracked-files=all", *exclude],
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
            run = _run_git(
                root, ["git", "hash-object", "--no-filters", "--stdin-paths"],
                timeout=60, env=environment, input=os.fsencode("\n".join(batch) + "\n"),
            )
            lines = run.stdout.splitlines()
            if run.returncode != 0 or len(lines) != len(batch):
                return None
            ids.update(zip(batch, (line.strip() for line in lines)))
        for path in single:
            run = _run_git(
                root, ["git", "hash-object", "--no-filters", "--", path],
                timeout=30, env=environment,
            )
            if run.returncode != 0:
                return None
            ids[path] = run.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return ids


def git_worktree_digest(root, exclude=()):
    """Hash tracked changes plus untracked file content for exact-change proof.

    Returns None (fingerprint unavailable, so proof is refused) while an index
    entry is flagged assume-unchanged or skip-worktree. The ignore rules that
    live outside the worktree are folded in, so editing them changes the
    digest; files ignored by the repository's .gitignore files stay outside it.
    EXCLUDE is a pathspec from state_exclude_pathspec.
    """
    environment = git_environment()
    flagged = git_flagged_index_paths(root, environment)
    rules = git_ignore_rules_digest(root, environment)
    diff = _git_bytes(
        root, ["diff", "--binary", "--no-ext-diff", "HEAD", *(exclude or ["--"])], environment
    )
    untracked = _git_bytes(
        root, ["ls-files", "--others", "--exclude-standard", "-z", *exclude], environment
    )
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


def changed_paths(state, base_commit=None, root=None, whole_tree=False):
    """Paths changed in the working tree since BASE_COMMIT, or None when unknown.

    Covers committed and uncommitted work (a working-tree diff against the
    commit, HEAD when none is given or the commit is gone) plus untracked
    files, leaves out the state directory, and returns paths relative to the
    project root. None means git cannot answer (no repository). With no
    commit to compare against (an unborn repository) or WHOLE_TREE, every
    tracked and untracked file counts as changed: all of it is new work.
    """
    project_root = Path(root) if root is not None else project_root_for_state(state)
    if not git_inside_worktree(project_root):
        return None
    environment = git_environment()
    exclude = state_exclude_pathspec(state, project_root)
    if whole_tree or git_commit(project_root) is None:
        listed = _git_bytes(
            project_root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z", *(exclude or ["--"])],
            environment,
        )
        if listed is None:
            return None
        return sorted(dict.fromkeys(os.fsdecode(item) for item in listed.split(b"\0") if item))
    command = ["diff", "--name-only", "--no-renames", "--relative", "-z"]
    diff = _git_bytes(project_root, command + [base_commit or "HEAD", *(exclude or ["--"])], environment)
    if diff is None and base_commit:
        diff = _git_bytes(project_root, command + ["HEAD", *(exclude or ["--"])], environment)
    untracked = _git_bytes(
        project_root, ["ls-files", "--others", "--exclude-standard", "-z", *(exclude or ["--"])], environment
    )
    if diff is None or untracked is None:
        return None
    names = [os.fsdecode(item) for item in (diff + b"\0" + untracked).split(b"\0") if item]
    return sorted(dict.fromkeys(names))


def commits_since(root, commit):
    """How many commits HEAD is ahead of COMMIT, or None when git cannot say."""
    raw = _git_bytes(root, ["rev-list", "--count", str(commit) + "..HEAD"], git_environment())
    try:
        return int(raw.strip()) if raw is not None else None
    except ValueError:
        return None


def current_verification_provenance(version, state=None, root=None, command=None):
    """Provenance of the current source state.

    With COMMAND (the shell command an executed record stands for) the result
    also carries environment_digest, so every writer of executed records stamps
    the same fields.
    """
    project_root = Path(root) if root is not None else project_root_for_state(state)
    exclude = state_exclude_pathspec(state, project_root)
    provenance = {
        "git_commit": git_commit(project_root),
        "worktree_clean": git_worktree_clean(project_root, exclude),
        "worktree_digest": git_worktree_digest(project_root, exclude),
        "mythify_version": str(version),
    }
    if command is not None:
        provenance["environment_digest"] = environment_digest(command)
    return provenance


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
