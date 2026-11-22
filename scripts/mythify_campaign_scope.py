"""Filesystem checkpoints for scoped campaigns, including HEAD and the index."""

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from mythify_outcomes import (
    ScopeInspectionError, frozen_path_violations, paths_outside_scope,
)

MAX_ENTRIES = 100_000
MAX_BYTES = 512 * 1024 * 1024
MAX_IGNORE_BYTES = 1024 * 1024
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
TOKEN = re.compile(r"(?:100644|100755|120000):" + OID.pattern)


def path_is_relative(path):
    return (
        isinstance(path, str) and "\0" not in path
        and all(part not in ("", ".", "..", ".git") for part in path.split("/"))
    )


def _git(root, *args, input_data=b"", exits=(0,)):
    # These commands read object, index, or ignore data without refreshing
    # the worktree, invoking a clean filter, or fetching missing objects.
    try:
        run = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
             "-c", "core.hooksPath=/dev/null", "-c", "core.preloadIndex=false", *args],
            cwd=root, input=input_data, capture_output=True, timeout=15,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
                 "GIT_NO_REPLACE_OBJECTS": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScopeInspectionError(str(exc)) from exc
    if run.returncode not in exits:
        raise ScopeInspectionError(run.stderr.decode("utf-8", "replace").strip() or
                                   "Git could not inspect " + args[0])
    return run.stdout


def _fields(raw):
    return raw[:-1].split(b"\0") if raw else []


def _tree(root, commit):
    files = {}
    for entry in _fields(_git(root, "ls-tree", "-r", "-z", "--full-tree", commit)):
        header, path = entry.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split()
        name, token = os.fsdecode(path), mode + ":" + oid
        if kind != "blob" or not TOKEN.fullmatch(token) or not path_is_relative(name):
            raise ScopeInspectionError("unsupported Git tree entry: " + repr(name))
        files[name] = token
        if len(files) > MAX_ENTRIES:
            raise ScopeInspectionError("scope exceeds the Git path limit")
    return files


def _index(root, commit, head):
    files = dict(head)
    raw = _git(root, "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames",
               "--no-ext-diff", "--no-textconv", "--ignore-submodules=none", commit, "--")
    entries = iter(_fields(raw))
    for header in entries:
        _, mode, _, oid, status = header.decode("ascii").split()
        path = os.fsdecode(next(entries, b""))
        if not path_is_relative(path):
            raise ScopeInspectionError("invalid Git index path: " + repr(path))
        if status == "U":
            raise ScopeInspectionError("the Git index has unmerged paths")
        if mode == "000000":
            files.pop(path, None)
        else:
            token = mode + ":" + oid
            if not TOKEN.fullmatch(token):
                raise ScopeInspectionError("unsupported Git index entry: " + repr(path))
            files[path] = token
        if len(files) > MAX_ENTRIES:
            raise ScopeInspectionError("scope exceeds the Git path limit")
    return files


def _paths_on_disk(root, state):
    files = {}
    directories = [root]
    scanned = 0
    while directories:
        directory = directories.pop()
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise ScopeInspectionError("directory changed during scope inspection: " + repr(str(directory)))
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if path == state or path == root / ".git":
                    continue
                if entry.name == ".git":
                    raise ScopeInspectionError("nested Git worktrees are unsupported in scoped campaigns")
                scanned += 1
                if scanned > MAX_ENTRIES:
                    raise ScopeInspectionError("scope exceeds the filesystem entry limit")
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                else:
                    files[path.relative_to(root).as_posix()] = path
    return files


def _blob(path, algorithm, remaining):
    info = path.lstat()
    digest = hashlib.new(algorithm)
    if stat.S_ISLNK(info.st_mode):
        target = os.fsencode(os.readlink(path))
        size, mode = len(target), "120000"
        digest.update(("blob %d\0" % size).encode("ascii") + target)
    elif stat.S_ISREG(info.st_mode):
        size = info.st_size
        mode = "100755" if info.st_mode & stat.S_IXUSR else "100644"
        if size > remaining:
            raise ScopeInspectionError("scope exceeds the file byte limit")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not os.path.samestat(opened, info) or not stat.S_ISREG(opened.st_mode) or (
                opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns
            ) != (size, info.st_mtime_ns, info.st_ctime_ns):
                raise ScopeInspectionError("file changed during scope inspection: " + repr(str(path)))
            digest.update(("blob %d\0" % size).encode("ascii"))
            left = size
            while left:
                chunk = handle.read(min(left, 1024 * 1024))
                if not chunk:
                    raise ScopeInspectionError("file shrank during scope inspection: " + repr(str(path)))
                digest.update(chunk)
                left -= len(chunk)
            closed = os.fstat(handle.fileno())
            if (closed.st_size, closed.st_mtime_ns, closed.st_ctime_ns) != (
                size, opened.st_mtime_ns, opened.st_ctime_ns
            ):
                raise ScopeInspectionError("file changed during scope inspection: " + repr(str(path)))
    else:
        raise ScopeInspectionError("unsupported file type: " + repr(str(path)))
    return mode + ":" + digest.hexdigest(), size


def _disk(root, state, tracked, frozen, commit):
    paths = _paths_on_disk(root, state)
    candidates = paths.keys() - tracked - set(frozen)
    # check-ignore only matches names, and --no-index avoids index refresh.
    # Include .gitignore itself even if one of its rules would hide it.
    candidates = {path for path in candidates if Path(path).name != ".gitignore"}
    ignored = set()
    if candidates:
        raw = _git(root, "check-ignore", "--no-index", "-z", "--stdin", exits=(0, 1),
                   input_data=b"".join(os.fsencode(path) + b"\0" for path in sorted(candidates)))
        ignored = {os.fsdecode(path) for path in _fields(raw)}
    remaining = MAX_BYTES
    algorithm = "sha256" if len(commit) == 64 else "sha1"
    files = {}
    for name in sorted(paths.keys() - ignored):
        files[name], size = _blob(paths[name], algorithm, remaining)
        remaining -= size
        if remaining < 0:
            raise ScopeInspectionError("scope exceeds the file byte limit")
    return files


def _external_ignore_rules(root):
    info = _git(root, "rev-parse", "--git-path", "info/exclude")
    user = _git(root, "config", "--path", "--get", "core.excludesFile", exits=(0, 1))
    if user:
        global_path = Path(os.fsdecode(user[:-1]))
    else:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        global_path = config_home / "git" / "ignore"
    sources = (root / os.fsdecode(info[:-1]), root / global_path)
    digest = hashlib.sha256()
    for path in sources:
        digest.update(os.fsencode(path) + b"\0")
        try:
            # Git can read a configured ignore file through a symlink.
            token, _ = _blob(path.resolve(strict=True), "sha256", MAX_IGNORE_BYTES)
        except FileNotFoundError:
            token = "absent"
        digest.update(token.encode("ascii") + b"\0")
    return digest.hexdigest()


def scope_snapshot(state, frozen, root=None, base_commit=None):
    """Return one snapshot and the initial commit's tree, or refuse inspection."""
    try:
        state_dir = state.resolve()
        project = state.parent if state.name == ".mythify" else Path.cwd()
        project = project.resolve()
        top = Path(os.fsdecode(_git(project, "rev-parse", "--show-toplevel")[:-1])).resolve()
        if project != top or (root is not None and str(top) != root) or state_dir == top:
            raise ScopeInspectionError("campaign scope requires its original Git worktree root")
        commit = _git(top, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
        if not OID.fullmatch(commit):
            raise ScopeInspectionError("Git HEAD is unavailable")
        if base_commit and base_commit != commit:
            _git(top, "merge-base", "--is-ancestor", base_commit, commit)
        head = _tree(top, commit)
        baseline = _tree(top, base_commit) if base_commit and base_commit != commit else head
        index = _index(top, commit, head)
        rules = _external_ignore_rules(top)
        disk = _disk(top, state_dir, head.keys() | index.keys() | baseline.keys(), frozen, commit)
        if rules != _external_ignore_rules(top):
            raise ScopeInspectionError("Git external ignore rules changed during scope inspection")
        snapshot = {"commit": commit, "head": head, "index": index, "disk": disk, "ignore_rules": rules}
        return str(top), snapshot, baseline
    except (OSError, ValueError, RuntimeError) as exc:
        raise ScopeInspectionError(str(exc)) from exc


def changed_since(before, after):
    changed = set()
    for layer in ("head", "index", "disk"):
        old, new = before[layer], after[layer]
        changed.update(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    return sorted(changed)


def checkpoint_changes(checkpoint, snapshot):
    """Keep changes beyond staging or committing accepted worktree state."""
    changed = []
    for path in changed_since(checkpoint, snapshot):
        accepted = checkpoint["disk"].get(path)
        if snapshot["disk"].get(path) == accepted and all(
            snapshot[layer].get(path) in (checkpoint[layer].get(path), accepted)
            for layer in ("head", "index")
        ):
            continue
        changed.append(path)
    return changed


def snapshot_provenance(snapshot):
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode("ascii"))
    return {"git_commit": snapshot["commit"], "worktree_digest": digest.hexdigest(),
            "worktree_clean": snapshot["head"] == snapshot["index"] == snapshot["disk"]}


def valid_prefixes(paths):
    return isinstance(paths, list) and all(path_is_relative(path) for path in paths)


def valid_scope(scope):
    if not isinstance(scope, dict) or not isinstance(scope.get("root"), str):
        return False
    initial_commit = scope.get("initial_commit")
    if not isinstance(initial_commit, str) or not OID.fullmatch(initial_commit) or not all(
        valid_prefixes(scope.get(key)) for key in ("allowed_paths", "frozen_paths")
    ):
        return False
    checkpoint = scope.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return False
    commit, rules = checkpoint.get("commit"), checkpoint.get("ignore_rules")
    if not isinstance(commit, str) or not OID.fullmatch(commit) or (
        not isinstance(rules, str) or not re.fullmatch(r"[0-9a-f]{64}", rules)
    ):
        return False
    return all(
        isinstance(checkpoint.get(layer), dict) and len(checkpoint[layer]) <= MAX_ENTRIES
        and all(path_is_relative(path) and isinstance(token, str) and TOKEN.fullmatch(token)
                for path, token in checkpoint[layer].items())
        for layer in ("head", "index", "disk")
    )


def scope_conflicts(scope, step, snapshot, baseline):
    initial = {"head": baseline, "index": baseline, "disk": baseline}
    overall = changed_since(initial, snapshot)
    since_step = checkpoint_changes(scope["checkpoint"], snapshot)
    frozen = frozen_path_violations(overall, scope["frozen_paths"])
    outside = paths_outside_scope(overall, scope["allowed_paths"], exempt_state=False)
    if step is None:
        outside = sorted(set(outside).union(since_step))
    elif "allowed_paths" in step:
        allowed = step["allowed_paths"]
        outside = sorted(set(outside).union(
            paths_outside_scope(since_step, allowed, exempt_state=False) if allowed else since_step
        ))
    return frozen, outside, since_step
