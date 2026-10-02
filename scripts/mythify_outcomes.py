"""Outcome loop store and command handlers for the Mythify CLI."""

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from mythify_evidence_guard import noop_verifier_reason, run_disabled
from mythify_io import (
    _write_text_atomic,
    append_chained_jsonl,
    append_jsonl,
    jsonl_file_lock,
    read_json,
    read_jsonl,
    write_json_atomic,
)
from mythify_provenance import (
    GIT_FRESH_READ,
    git_flagged_index_paths,
    git_ignore_rules_digest,
)

OUTCOME_CHECK_DISABLED_MESSAGE = (
    "[FAIL] outcome check is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded. Unset it to enable execution."
)
OUTCOME_STATUSES = ("active", "succeeded", "failed", "stopped")
# Cost charged for an agent attempt that reports no MYTHIFY_COST, and the cost
# reserved before every attempt runs.
DEFAULT_ATTEMPT_COST = 1.0


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_outcomes dependencies are not configured")


find_existing_slug_by_name = _missing_dependency
now_iso = _missing_dependency
slugify = _missing_dependency
run_shell_capture = _missing_dependency
verification_step_context = _missing_dependency
verification_provenance = _missing_dependency


def fail(message):
    print(message, file=sys.stderr)


def configure_outcome_loops(
    *,
    find_existing_slug_by_name_func=None,
    now_iso_func=None,
    slugify_func=None,
    run_shell_capture_func=None,
    verification_step_context_func=None,
    verification_provenance_func=None,
    fail_func=None,
):
    global find_existing_slug_by_name, now_iso, slugify, run_shell_capture
    global verification_step_context, verification_provenance, fail
    if find_existing_slug_by_name_func is not None:
        find_existing_slug_by_name = find_existing_slug_by_name_func
    if now_iso_func is not None:
        now_iso = now_iso_func
    if slugify_func is not None:
        slugify = slugify_func
    if run_shell_capture_func is not None:
        run_shell_capture = run_shell_capture_func
    if verification_step_context_func is not None:
        verification_step_context = verification_step_context_func
    if verification_provenance_func is not None:
        verification_provenance = verification_provenance_func
    if fail_func is not None:
        fail = fail_func


# ---------------------------------------------------------------------------
# Outcome loops
# ---------------------------------------------------------------------------

def outcomes_dir(state):
    return state / "outcomes"


def active_outcome_path(state):
    return outcomes_dir(state) / "active"


def outcome_dir(state, slug):
    return outcomes_dir(state) / slug


def outcome_goal_path(state, slug):
    return outcome_dir(state, slug) / "goal.json"


def outcome_iterations_path(state, slug):
    return outcome_dir(state, slug) / "iterations.jsonl"


def get_active_outcome_slug(state):
    path = active_outcome_path(state)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    if value and not outcome_goal_path(state, value).exists():
        return None
    return value or None


def set_active_outcome_slug(state, slug):
    _write_text_atomic(active_outcome_path(state), slug + "\n")


def clear_active_outcome_slug(state, slug=None):
    path = active_outcome_path(state)
    if not path.exists():
        return
    if slug is not None and get_active_outcome_slug(state) != slug:
        return
    try:
        path.unlink()
    except OSError:
        pass


def find_outcome_slug(state, name):
    if name:
        return find_existing_slug_by_name(state, name, outcome_goal_path)
    return get_active_outcome_slug(state)


def load_outcome(state, name=None):
    slug = find_outcome_slug(state, name)
    if not slug:
        return None, None
    goal = read_json(outcome_goal_path(state, slug), None)
    if not isinstance(goal, dict):
        return slug, None
    return slug, goal


def save_outcome(state, slug, goal):
    write_json_atomic(outcome_goal_path(state, slug), goal)


def with_outcome_lock(state, slug, action):
    """Run ACTION(goal) holding the outcome exclusively, with the goal re-read.

    check, run, and audit read the iteration budget, run a verifier, and write
    the goal back. Without the lock, concurrent calls each spent the same
    budget slot. A second caller is refused at once rather than queued, since
    an outcome run can hold the outcome for many iterations.
    """
    lock = jsonl_file_lock(outcome_goal_path(state, slug), timeout=0)
    try:
        lock.__enter__()
    except TimeoutError:
        fail(
            "[FAIL] Outcome {0} is being checked or run by another process; "
            "nothing was run. Try again when it finishes.".format(slug)
        )
        return 1
    try:
        goal = read_json(outcome_goal_path(state, slug), None)
        if not isinstance(goal, dict):
            print("[FAIL] No outcome found. Start one with outcome start.")
            return 1
        return action(goal)
    finally:
        lock.__exit__(None, None, None)


def list_outcomes(state):
    root = outcomes_dir(state)
    if not root.exists():
        return []
    items = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        goal = read_json(path / "goal.json", None)
        if isinstance(goal, dict):
            items.append((path.name, goal))
    return items


def parse_allowed_paths(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def outcome_project_root(state):
    return state.parent if state.name == ".mythify" else Path.cwd()


def normalize_frozen_paths(state, frozen):
    """Frozen paths relative to the project root, or (None, refusal).

    A deny-list that matches nothing protects nothing, so an absolute path is
    made relative to the root, and a path outside the root or naming no
    existing file or directory is refused instead of silently passing.
    """
    root = outcome_project_root(state).resolve()
    normalized = []
    for item in frozen:
        path = Path(item).expanduser()
        relative = os.path.relpath(str(path.resolve()), str(root)) if path.is_absolute() else item
        clean = os.path.normpath(relative.replace(os.sep, "/").strip("/") or ".").replace(os.sep, "/")
        if clean == ".." or clean.startswith("../"):
            return None, "[FAIL] Frozen path {0} is outside the project root {1}.".format(item, root)
        if not os.path.lexists(str(root / clean)):
            return None, (
                "[FAIL] Frozen path {0} matches no file or directory under the "
                "project root {1}, so it would protect nothing. Frozen paths are "
                "relative to the project root; check the spelling."
            ).format(item, root)
        normalized.append(clean)
    return normalized, None


def git_changed_paths(root):
    """Return the working-tree paths git reports as changed, or None off-git.

    Reads the FULL, unredacted `git status --porcelain` output directly, not the
    truncated/redacted human-facing tail: scope enforcement is a correctness
    gate and must see every changed path and the real path text.
    """
    try:
        run = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if run.returncode != 0:
        return None
    paths = []
    for line in run.stdout.splitlines():
        entry = line[3:].strip() if len(line) > 3 else ""
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        if entry:
            paths.append(entry.strip('"'))
    return paths


def paths_outside_scope(changed, allowed):
    """Paths not contained by any allowed prefix. Empty allowed => no scope."""
    if not allowed:
        return []
    prefixes = [item.rstrip("/") for item in allowed]
    outside = []
    for path in changed:
        normalized = path.rstrip("/")
        # Mythify's own state directory is never the agent's target.
        if normalized == ".mythify" or normalized.startswith(".mythify/"):
            continue
        if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in prefixes):
            continue
        outside.append(path)
    return outside


def scope_violations(state, allowed_paths):
    """Files changed outside the declared scope, enforced post-hoc via git."""
    if not allowed_paths:
        return []
    changed = git_changed_paths(outcome_project_root(state))
    if changed is None:
        return []
    return paths_outside_scope(changed, allowed_paths)


def frozen_path_violations(changed, frozen):
    """Changed paths under a frozen prefix. A deny-list, enforced everywhere.

    Frozen paths are the held-out set the loop must never touch, tests being
    the canonical case, so a scoped agent cannot rewrite its own verifier.
    Unlike allowed_paths, this is enforced in host-supervised checks too, and
    the .mythify/ exemption does not apply: the user named these prefixes.
    """
    if not frozen or not changed:
        return []
    prefixes = [item.rstrip("/") for item in frozen]
    hits = []
    for path in changed:
        normalized = path.rstrip("/")
        if any(
            normalized == prefix or normalized.startswith(prefix + "/")
            for prefix in prefixes
        ):
            hits.append(path)
    return hits


class ScopeInspectionError(RuntimeError):
    pass


def _run_git_scope(root, args):
    try:
        run = subprocess.run(
            ["git", "-C", str(root), *GIT_FRESH_READ] + list(args),
            capture_output=True,
            timeout=30,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScopeInspectionError(str(exc))
    if run.returncode != 0:
        detail = run.stderr.decode("utf-8", "replace").strip() or "git inspection failed"
        raise ScopeInspectionError(detail)
    return run.stdout


def _require_trusted_index(root):
    """Raise when git cannot vouch for the worktree: an index entry is flagged
    assume-unchanged or skip-worktree, so its edits never show in a diff."""
    flagged = git_flagged_index_paths(root)
    if flagged is None:
        raise ScopeInspectionError("git ls-files -v failed")
    if flagged:
        raise ScopeInspectionError(
            "index entries flagged assume-unchanged or skip-worktree hide edits: "
            "{0}".format(", ".join(flagged[:5]))
        )


def start_scope_baseline(state):
    root = outcome_project_root(state)
    commit = _run_git_scope(root, ["rev-parse", "HEAD"]).decode("ascii", "replace").strip()
    if not commit:
        raise ScopeInspectionError("Git HEAD is unavailable")
    dirty = _run_git_scope(
        root, ["status", "--porcelain", "-z", "--untracked-files=all"]
    )
    if dirty:
        raise ScopeInspectionError("scoped self-driving runs require a clean Git worktree")
    _require_trusted_index(root)
    return {"git_commit": commit, "ignore_rules": git_ignore_rules_digest(root)}


def _diff_name_status_paths(raw):
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    paths = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        index += 1
        if index >= len(fields) or not fields[index]:
            raise ScopeInspectionError("malformed Git name-status output")
        paths.append(fields[index])
        index += 1
        if status.startswith(("R", "C")):
            if index >= len(fields) or not fields[index]:
                raise ScopeInspectionError("malformed Git rename or copy output")
            paths.append(fields[index])
            index += 1
    return paths


def _changed_paths_since(root, commit):
    """Paths whose working-tree content differs from COMMIT, plus untracked.

    Diffing the worktree against a recorded commit sees committed and
    uncommitted changes alike, which a plain `git status` misses.
    """
    tracked = _diff_name_status_paths(
        _run_git_scope(root, ["diff", "--name-status", "-z", "--find-renames", commit])
    )
    untracked_raw = _run_git_scope(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    untracked = [
        item for item in untracked_raw.decode("utf-8", "surrogateescape").split("\0") if item
    ]
    return list(dict.fromkeys(tracked + untracked))


def self_driving_changed_paths(state, baseline):
    root = outcome_project_root(state)
    commit = str((baseline or {}).get("git_commit") or "")
    if not commit:
        raise ScopeInspectionError("scope baseline commit is unavailable")
    _run_git_scope(root, ["merge-base", "--is-ancestor", commit, "HEAD"])
    _require_trusted_index(root)
    rules = baseline.get("ignore_rules")
    if rules and git_ignore_rules_digest(root) != rules:
        raise ScopeInspectionError(
            "ignore rules outside the worktree (.git/info/exclude, the global "
            "excludes file, or a self-ignoring .gitignore) changed since the scope baseline"
        )
    return _changed_paths_since(root, commit)


def _path_token(path):
    """sha256 of a file's bytes, or the target of a symlink."""
    try:
        if path.is_symlink():
            return "symlink:" + os.readlink(str(path))
        digest = hashlib.sha256()
        with open(str(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return "unreadable"


def frozen_manifest(state, frozen):
    """Digest of every file under the frozen prefixes, read from disk.

    Content is hashed directly, so a git index flag (assume-unchanged,
    skip-worktree) cannot hide an edit. In a git repository, files ignored by
    the repository's .gitignore files are left out (verifier caches such as
    __pycache__ live there); .git/info/exclude and the global excludes file are
    not applied, and every .gitignore under a frozen prefix is always covered,
    so a self-ignoring one is caught. A frozen path that names a file is
    always covered. Off git, every file counts. The state directory never does.
    """
    root = outcome_project_root(state)
    state_dir = Path(state).resolve()
    state_rel = os.path.relpath(str(state_dir), str(root.resolve())).replace(os.sep, "/")
    prefixes = [item.strip("/") or "." for item in frozen]
    walked = []
    named = []
    for prefix in prefixes:
        base = root / prefix
        if base.is_symlink() or base.is_file():
            named.append(prefix)
            continue
        for dirpath, dirnames, filenames in os.walk(str(base)):
            current = Path(dirpath)
            kept = []
            for name in dirnames:
                child = current / name
                if child.is_symlink():
                    filenames.append(name)
                elif name != ".git" and child.resolve() != state_dir:
                    kept.append(name)
            dirnames[:] = kept
            walked.extend(
                os.path.relpath(str(current / name), str(root)).replace(os.sep, "/")
                for name in filenames
            )
    try:
        raw = _run_git_scope(
            root,
            ["ls-files", "--cached", "--others", "--exclude-per-directory=.gitignore", "-z", "--"]
            + prefixes,
        )
        files = {item for item in raw.decode("utf-8", "surrogateescape").split("\0") if item}
        files.update(path for path in walked if path.rsplit("/", 1)[-1] == ".gitignore")
    except ScopeInspectionError:
        files = set(walked)
    files.update(named)
    return {
        path: _path_token(root / path)
        for path in sorted(files)
        if os.path.lexists(str(root / path))
        and path != state_rel
        and not path.startswith(state_rel + "/")
    }


def current_frozen_violations(state, goal, changed_override=None):
    """Frozen paths added, removed, or changed since the outcome started.

    A goal with a manifest compares file digests read from disk. A goal
    started by 5.x has none and falls back to git's changed-path list.
    """
    frozen = goal.get("frozen_paths") or []
    baseline = goal.get("frozen_baseline")
    manifest = baseline.get("manifest") if isinstance(baseline, dict) else None
    if isinstance(manifest, dict):
        current = frozen_manifest(state, frozen)
        return sorted(
            path for path in set(manifest) | set(current) if manifest.get(path) != current.get(path)
        )
    if changed_override is not None:
        changed = list(changed_override)
    else:
        changed = git_changed_paths(outcome_project_root(state)) or []
    return frozen_path_violations(changed, frozen)


def parse_metric_score(output):
    """First number in OUTPUT, or None. A number too large for a float (it
    parses as inf) is unparseable, so it can neither pass a metric floor nor
    crash the JSON record."""
    match = re.search(r"-?\d+(?:\.\d+)?", str(output or ""))
    if not match:
        return None
    score = float(match.group(0))
    return score if math.isfinite(score) else None


def format_outcome_status(slug, goal, iterations=None):
    iterations = iterations if iterations is not None else []
    lines = [
        "[OK] Outcome {0}: {1}".format(slug, goal.get("goal", "")),
        "status: {0}".format(goal.get("status", "active")),
        "success: {0}".format(goal.get("success_criteria", "")),
        "verify: {0}".format(goal.get("verify_command", "")),
        "iterations: {0}/{1}".format(
            goal.get("iteration_count", 0), goal.get("max_iterations", 1)
        ),
    ]
    metric = goal.get("metric_command", "")
    if metric:
        lines.append("metric: {0}".format(metric))
    agent = goal.get("agent_command", "")
    if agent:
        lines.append("agent: {0}".format(agent))
    if goal.get("max_cost") is not None:
        lines.append("cost: {0}/{1}".format(round(float(goal.get("cost_spent", 0.0)), 4), goal.get("max_cost")))
    allowed = goal.get("allowed_paths") or []
    if allowed:
        lines.append("scope (enforced post-hoc via git): {0}".format(", ".join(allowed)))
    frozen = goal.get("frozen_paths") or []
    if frozen:
        lines.append("frozen (never touched, enforced): {0}".format(", ".join(frozen)))
    if goal.get("metric_floor") is not None:
        lines.append("metric floor: {0}".format(goal.get("metric_floor")))
    if goal.get("supersedes"):
        lines.append("supersedes: {0}".format(goal.get("supersedes")))
    if goal.get("evidence_stale"):
        lines.append("evidence: STALE (the last audit recheck failed; re-verify before trusting)")
    if iterations:
        last = iterations[-1]
        lines.append(
            "last check: iteration {0}, verified={1}, status={2}".format(
                last.get("iteration"), last.get("verified"), last.get("status_after")
            )
        )
        next_action = last.get("next_action")
        if next_action:
            lines.append("next: {0}".format(next_action))
    else:
        lines.append("next: do the first bounded attempt, then run outcome check.")
    return "\n".join(lines)



def cmd_outcome_start(args, state):
    if args.max_iterations < 1:
        print("[FAIL] outcome start requires --max-iterations >= 1.")
        return 1
    max_cost = getattr(args, "max_cost", None)
    if max_cost is not None and (not math.isfinite(max_cost) or max_cost <= 0):
        print("[FAIL] outcome start requires --max-cost to be finite and greater than 0.")
        return 1
    escalate_after = getattr(args, "escalate_after", None)
    if escalate_after is not None and escalate_after < 1:
        print("[FAIL] outcome start requires --escalate-after >= 1.")
        return 1
    metric_floor = getattr(args, "metric_floor", None)
    if metric_floor is not None and not args.metric:
        print("[FAIL] outcome start requires --metric when --metric-floor is set.")
        return 1
    if metric_floor is not None and not math.isfinite(metric_floor):
        print("[FAIL] outcome start requires --metric-floor to be a finite number.")
        return 1
    # Two live loops fight each other and neither owns the trade-off, so a
    # second start needs an explicit supersession instead of silently stealing
    # the active pointer.
    supersede_reason = (getattr(args, "supersede", None) or "").strip()
    superseded = None
    active_slug = get_active_outcome_slug(state)
    if active_slug:
        active_goal = read_json(outcome_goal_path(state, active_slug), None)
        if isinstance(active_goal, dict) and active_goal.get("status") == "active":
            if not supersede_reason:
                print(
                    "[FAIL] Outcome {0} is still active. Stop it with outcome "
                    "stop --reason, or pass --supersede REASON to retire it "
                    "into this one.".format(active_slug)
                )
                return 1
            superseded = (active_slug, active_goal)
    frozen_paths, frozen_error = normalize_frozen_paths(
        state, parse_allowed_paths(getattr(args, "frozen_paths", ""))
    )
    if frozen_error:
        print(frozen_error)
        return 1
    noop = noop_verifier_reason(args.verify)
    if noop:
        # Advisory, like plan steps: the loop would end on its first check.
        print(
            "[WARN] Outcome verifier looks like a no-op ({0}): {1}. The first "
            "outcome check will report success without checking anything.".format(noop, args.verify),
            file=sys.stderr,
        )
    base = args.name or args.goal
    slug = slugify(base) or "outcome"
    original = slug
    counter = 2
    while outcome_goal_path(state, slug).exists():
        slug = "{0}-{1}".format(original[:36], counter)
        counter += 1
    now = now_iso()
    goal = {
        "id": slug,
        "goal": args.goal,
        "success_criteria": args.success,
        "verify_command": args.verify,
        "metric_command": args.metric or "",
        "metric_floor": metric_floor,
        "agent_command": getattr(args, "agent", None) or "",
        "max_iterations": args.max_iterations,
        "iteration_count": 0,
        "max_cost": max_cost,
        "cost_spent": 0.0,
        "escalate_after": escalate_after,
        "allowed_paths": parse_allowed_paths(args.allowed_paths),
        "frozen_paths": frozen_paths,
        "status": "active",
        "created": now,
        "updated": now,
        "last_verified": None,
        "best_metric_score": None,
        "stop_reason": None,
        "supersedes": superseded[0] if superseded else None,
    }
    if goal["frozen_paths"]:
        goal["frozen_baseline"] = {"manifest": frozen_manifest(state, goal["frozen_paths"])}
    # The new outcome is written before the old one is retired, so a failed
    # write leaves the old loop active instead of superseded by nothing.
    save_outcome(state, slug, goal)
    if superseded:
        old_slug, old_goal = superseded
        old_goal["status"] = "stopped"
        old_goal["stop_reason"] = "superseded by {0}: {1}".format(slug, supersede_reason)
        old_goal["superseded_by"] = slug
        old_goal["updated"] = now
        save_outcome(state, old_slug, old_goal)
    set_active_outcome_slug(state, slug)
    if args.json_output:
        print(json.dumps(goal, indent=2))
    else:
        print("[OK] Outcome started: {0}".format(slug))
        print("goal: {0}".format(args.goal))
        print("success: {0}".format(args.success))
        print("verify: {0}".format(args.verify))
        if args.metric:
            print("metric: {0}".format(args.metric))
        if metric_floor is not None:
            print("metric floor: {0}".format(metric_floor))
        if goal["frozen_paths"]:
            print("frozen (never touched, enforced): {0}".format(", ".join(goal["frozen_paths"])))
        if superseded:
            print("superseded: {0} ({1})".format(superseded[0], supersede_reason))
        print("iterations: 0/{0}".format(args.max_iterations))
        print("next: make a bounded attempt, then run outcome check.")
    return 0


def summarize_outcome_row(state, slug, goal):
    """One outcome loop as a summary row, shared by outcome status and status."""
    iterations = read_jsonl(outcome_iterations_path(state, slug))
    last = iterations[-1] if iterations else None
    # Sensor-drift watcher: every iteration records the command it actually
    # ran, so a verifier swapped mid-loop is visible in the durable record.
    commands = {
        str((item.get("verify") or {}).get("command") or "")
        for item in iterations
        if isinstance(item.get("verify"), dict)
    }
    goal_command = str(goal.get("verify_command") or "")
    verifier_drift = bool(commands) and (
        len(commands) > 1 or (goal_command != "" and goal_command not in commands)
    )
    return {
        "id": slug,
        "goal": goal.get("goal", ""),
        "status": goal.get("status", "active"),
        "iteration_count": goal.get("iteration_count", 0),
        "max_iterations": goal.get("max_iterations", 1),
        "verify_command": goal_command,
        "last_verified": goal.get("last_verified"),
        "verifier_drift": verifier_drift,
        "evidence_stale": bool(goal.get("evidence_stale")),
        "created": goal.get("created", ""),
        "updated": goal.get("updated", ""),
        "last_check": {
            "iteration": last.get("iteration"),
            "verified": last.get("verified"),
            "status_after": last.get("status_after"),
            "timestamp": last.get("timestamp", ""),
        } if last else None,
        "next_action": (last or {}).get("next_action")
        or "make a bounded attempt, then run outcome check",
    }


def format_outcome_list(rows):
    lines = ["[OK] Outcomes ({0}); none is active:".format(len(rows))]
    if not rows:
        lines.append("  none. Start one with outcome start.")
    for row in rows:
        lines.append(
            "  {0}: {1} ({2}, {3}/{4} iterations{5})".format(
                row["id"],
                row["goal"],
                row["status"],
                row["iteration_count"],
                row["max_iterations"],
                ", evidence stale" if row["evidence_stale"] else "",
            )
        )
        last = row.get("last_check")
        if last:
            lines.append(
                "      last check: iteration {0}, verified={1}, status={2}".format(
                    last.get("iteration"), last.get("verified"), last.get("status_after")
                )
            )
    return "\n".join(lines)


def list_outcome_rows(state):
    """Every outcome loop as a summary row, oldest update first."""
    return sorted(
        (summarize_outcome_row(state, slug, goal) for slug, goal in list_outcomes(state)),
        key=lambda row: (row.get("updated") or row.get("created") or "", row["id"]),
    )


def cmd_outcome_status(args, state):
    if not args.name and get_active_outcome_slug(state) is None:
        # No name and nothing active: list every outcome loop instead.
        rows = list_outcome_rows(state)
        if args.json_output:
            print(json.dumps({"active": None, "outcomes": rows}, indent=2))
        else:
            print(format_outcome_list(rows))
        return 0
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    iterations = read_jsonl(outcome_iterations_path(state, slug))
    if args.json_output:
        print(json.dumps({"goal": goal, "iterations": iterations}, indent=2))
    else:
        print(format_outcome_status(slug, goal, iterations))
    return 0


def parse_reported_cost(output):
    """Read a MYTHIFY_COST=<number> line an agent may emit; None if absent.

    A number too large for a float parses as inf. The caller treats it as
    spending the whole cost budget, never as a value to store.
    """
    match = re.search(r"MYTHIFY_COST\s*=\s*(-?\d+(?:\.\d+)?)", str(output or ""))
    return float(match.group(1)) if match else None


def agent_attempt_record(command, attempt):
    """The JSON-safe record of one agent attempt, with its reported cost."""
    reported = parse_reported_cost(
        (attempt.get("stdout_tail") or "") + "\n" + (attempt.get("stderr_tail") or "")
    )
    unbounded = reported is not None and not math.isfinite(reported)
    return {
        "command": command,
        "exit_code": attempt["exit_code"],
        "duration_seconds": attempt["duration_seconds"],
        "stdout_tail": attempt["stdout_tail"],
        "stderr_tail": attempt["stderr_tail"],
        "cost": None if unbounded else reported,
        "cost_unbounded": unbounded,
    }


def reserve_agent_attempt(state, slug, goal):
    """Charge one iteration and the default cost before the agent runs.

    The charge is saved before the agent starts, so a process that is killed,
    times out, or crashes while writing the record still spends the slot.
    perform_outcome_iteration reconciles the cost with what the agent
    reported; settle_interrupted_attempt counts a reservation that was never
    reconciled.
    """
    reservation = {
        "iteration": int(goal.get("iteration_count", 0)) + 1,
        "started": now_iso(),
        "prior_cost_spent": float(goal.get("cost_spent", 0.0)),
    }
    goal["iteration_count"] = reservation["iteration"]
    goal["cost_spent"] = reservation["prior_cost_spent"] + DEFAULT_ATTEMPT_COST
    goal["attempt_started"] = reservation
    goal["updated"] = reservation["started"]
    save_outcome(state, slug, goal)
    return reservation


def settle_interrupted_attempt(state, slug, goal):
    """Record a reserved attempt whose process ended before recording it.

    Callers hold the outcome lock, so a reservation found here belongs to a
    process that is gone. Its iteration and default cost stay spent; the
    iteration is logged as interrupted, and an exhausted budget ends the loop.
    """
    reservation = goal.pop("attempt_started", None)
    if not isinstance(reservation, dict):
        return None
    stamp = now_iso()
    cost_spent = float(goal.get("cost_spent", 0.0))
    max_cost = goal.get("max_cost")
    status = goal.get("status", "active")
    if status == "active":
        if max_cost is not None and cost_spent >= float(max_cost):
            status = "failed"
            goal["stop_reason"] = "cost budget exhausted"
        elif int(goal.get("iteration_count", 0)) >= int(goal.get("max_iterations", 1)):
            status = "failed"
            goal["stop_reason"] = "iteration budget exhausted"
    record = {
        "iteration": reservation.get("iteration"),
        "timestamp": stamp,
        "notes": "",
        "interrupted": True,
        "attempt_started": reservation.get("started"),
        "agent": {"command": goal.get("agent_command", "")},
        "cost": DEFAULT_ATTEMPT_COST,
        "cost_spent": cost_spent,
        "verify": None,
        "metric": None,
        "verified": False,
        "status_after": status,
        "next_action": (
            "The process running this attempt ended before it recorded a "
            "result. The iteration and its default cost stay spent."
        ),
    }
    append_jsonl(outcome_iterations_path(state, slug), record)
    goal["status"] = status
    goal["last_verified"] = False
    goal["updated"] = stamp
    save_outcome(state, slug, goal)
    fail(
        "[WARN] Outcome {0}: iteration {1} ended before it recorded a result "
        "and stays counted (status {2}).".format(slug, record["iteration"], status)
    )
    return record


def perform_outcome_iteration(
    state, slug, goal, timeout, notes="", agent_record=None,
    scope_violations_override=None, changed_paths_override=None,
    reservation=None,
):
    """Run one verifier (and optional metric) iteration, enforce scope, frozen
    paths, the metric floor, and the cost budget, append the iteration and
    executed-verification records, update the goal, and return the iteration
    record. Shared by outcome check (the host made the attempt) and outcome run
    (the loop invoked the agent, after reserve_agent_attempt charged the slot
    named by RESERVATION)."""
    verify = run_shell_capture(goal["verify_command"], timeout)
    metric_record = None
    metric_ok = True
    metric_score = None
    if goal.get("metric_command"):
        metric = run_shell_capture(goal["metric_command"], timeout)
        metric_ok = metric["verified"]
        metric_score = parse_metric_score(metric.get("stdout_tail", ""))
        metric_record = {
            "command": metric["command"],
            "exit_code": metric["exit_code"],
            "duration_seconds": metric["duration_seconds"],
            "stdout_tail": metric["stdout_tail"],
            "stderr_tail": metric["stderr_tail"],
            "verified": metric["verified"],
            "score": metric_score,
        }
    metric_floor = goal.get("metric_floor")
    floor_unmet = metric_floor is not None and (
        metric_score is None or metric_score < float(metric_floor)
    )
    best_before = goal.get("best_metric_score")
    metric_regressed = (
        metric_score is not None
        and best_before is not None
        and metric_score < best_before
    )
    violations = (
        list(scope_violations_override)
        if scope_violations_override is not None
        else scope_violations(state, goal.get("allowed_paths") or [])
    )
    scope_enforced = scope_violations_override is not None
    if goal.get("frozen_paths"):
        frozen_hits = current_frozen_violations(state, goal, changed_paths_override)
    else:
        frozen_hits = []
    verified = bool(
        verify["verified"]
        and metric_ok
        and not floor_unmet
        and not (scope_enforced and violations)
        and not frozen_hits
    )
    max_iterations = int(goal.get("max_iterations", 1))
    if reservation is not None:
        next_iteration = int(reservation["iteration"])
        prior_cost = float(reservation["prior_cost_spent"])
    else:
        next_iteration = int(goal.get("iteration_count", 0)) + 1
        prior_cost = float(goal.get("cost_spent", 0.0))

    # Cost ledger applies only to the self-driving loop (an agent ran this
    # iteration). The host-driven `outcome check` path burns no cost, matching
    # the MCP outcome_check. Reported cost is clamped non-negative so a bad or
    # adversarial agent cannot drive the ledger down and neutralize --max-cost.
    # A cost too large to add up spends the whole budget.
    max_cost = goal.get("max_cost")
    iteration_cost = 0.0
    cost_unbounded = False
    if agent_record is not None:
        reported = agent_record.get("cost")
        if reported is not None:
            iteration_cost = max(0.0, float(reported))
        else:
            iteration_cost = DEFAULT_ATTEMPT_COST
        cost_unbounded = bool(agent_record.get("cost_unbounded")) or not math.isfinite(
            prior_cost + iteration_cost
        )
        if cost_unbounded:
            iteration_cost = (
                max(DEFAULT_ATTEMPT_COST, float(max_cost) - prior_cost)
                if max_cost is not None
                else DEFAULT_ATTEMPT_COST
            )
    cost_spent = prior_cost + iteration_cost
    budget_exhausted = agent_record is not None and max_cost is not None and (
        cost_unbounded or cost_spent >= float(max_cost)
    )

    if frozen_hits:
        status_after = "stopped"
        next_action = (
            "Frozen-path violation detected: the loop changed paths it must "
            "never touch ({0}). Stop and revert them.".format(", ".join(frozen_hits[:5]))
        )
    elif scope_enforced and violations:
        status_after = "stopped"
        next_action = "Scope violation detected. Stop and report the out-of-scope changes."
    elif verified:
        status_after = "succeeded"
        next_action = "Outcome met. Report the evidence and stop."
        if next_iteration == 1:
            next_action += (
                " Caution: the verifier passed before any recorded failed "
                "attempt; confirm the verifier can fail."
            )
    elif budget_exhausted:
        status_after = "failed"
        next_action = "Cost budget exhausted ({0}/{1}). Summarize the blocker and stop.".format(
            round(cost_spent, 4), max_cost
        )
    elif next_iteration >= max_iterations:
        status_after = "failed"
        next_action = "Iteration budget exhausted. Summarize the blocker and stop."
    else:
        status_after = "active"
        next_action = (
            "Outcome not met. Inspect verifier output, make another bounded attempt, "
            "then run outcome check again."
        )
    if violations:
        next_action = "{0} Changed outside scope: {1}.".format(
            next_action, ", ".join(violations[:5])
        )
    if floor_unmet and verify["verified"] and status_after == "active":
        next_action = "Metric floor not met (score {0}, floor {1}). {2}".format(
            metric_score, metric_floor, next_action
        )
    record = {
        "iteration": next_iteration,
        "timestamp": now_iso(),
        "notes": notes,
        "agent": agent_record,
        "cost": iteration_cost,
        "cost_unbounded": cost_unbounded,
        "cost_spent": cost_spent,
        "verify": {
            "command": verify["command"],
            "exit_code": verify["exit_code"],
            "duration_seconds": verify["duration_seconds"],
            "stdout_tail": verify["stdout_tail"],
            "stderr_tail": verify["stderr_tail"],
            "verified": verify["verified"],
        },
        "metric": metric_record,
        "metric_regressed": metric_regressed,
        "metric_floor_unmet": floor_unmet,
        "verified": verified,
        "scope_violations": violations,
        "frozen_violations": frozen_hits,
        "status_after": status_after,
        "next_action": next_action,
    }
    append_jsonl(outcome_iterations_path(state, slug), record)
    goal.pop("attempt_started", None)
    goal["iteration_count"] = next_iteration
    goal["status"] = status_after
    goal["last_verified"] = verified
    goal["cost_spent"] = cost_spent
    goal["updated"] = record["timestamp"]
    if metric_score is not None:
        best = goal.get("best_metric_score")
        if best is None or metric_score > best:
            goal["best_metric_score"] = metric_score
    if status_after == "failed":
        goal["stop_reason"] = "cost budget exhausted" if budget_exhausted else "iteration budget exhausted"
    if status_after == "stopped" and frozen_hits:
        goal["stop_reason"] = "frozen-path violation: {0}".format(
            ", ".join(frozen_hits[:5])
        )
    elif status_after == "stopped" and scope_enforced:
        goal["stop_reason"] = "scope violation: {0}".format(
            ", ".join(violations[:5])
        )
    if status_after == "succeeded":
        goal["stop_reason"] = "success criteria verified"
    save_outcome(state, slug, goal)
    combined_exit_code = verify["exit_code"]
    if verify["verified"] and metric_record is not None and not metric_ok:
        combined_exit_code = metric_record["exit_code"]
    combined_duration = verify["duration_seconds"]
    if metric_record is not None:
        combined_duration += metric_record["duration_seconds"]
    verification_stderr = verify["stderr_tail"]
    if frozen_hits:
        combined_exit_code = -1
        verification_stderr = (
            verification_stderr + ("\n" if verification_stderr else "") +
            "(frozen-path violation: {0})".format(", ".join(frozen_hits[:5]))
        )
    elif scope_enforced and violations:
        combined_exit_code = -1
        verification_stderr = (
            verification_stderr + ("\n" if verification_stderr else "") +
            "(scope violation: {0})".format(", ".join(violations[:5]))
        )
    verification_record = {
        "kind": "executed",
        "claim": "Outcome {0}: {1}".format(slug, goal.get("success_criteria", "")),
        "command": goal["verify_command"],
        "exit_code": combined_exit_code,
        "duration_seconds": combined_duration,
        "stdout_tail": verify["stdout_tail"],
        "stderr_tail": verification_stderr,
        "verified": verified,
        "outcome_verify": record["verify"],
        "outcome_metric": metric_record,
        "timestamp": record["timestamp"],
        "outcome": slug,
        "iteration": next_iteration,
        "provenance": verification_provenance(state),
    }
    verification_record.update(verification_step_context(state, goal["verify_command"]))
    append_chained_jsonl(state / "verifications.jsonl", verification_record)
    return record


def run_outcome_audit(state, slug, goal, timeout, notes, json_output):
    """Re-run a finished outcome's verifier without mutating its history.

    The audit loop's only job is checking that the recorded result still
    touches reality: iteration_count and status stay untouched, the run is
    appended flagged audit, and a red run marks the goal evidence_stale.
    """
    if goal.get("status") == "active":
        fail(
            "[FAIL] --audit re-checks a finished outcome, but {0} is still "
            "active. Run outcome check without --audit.".format(slug)
        )
        return 1
    verify = run_shell_capture(goal["verify_command"], timeout)
    stamp = now_iso()
    record = {
        "iteration": int(goal.get("iteration_count", 0)),
        "timestamp": stamp,
        "notes": notes,
        "audit": True,
        "verify": {
            "command": verify["command"],
            "exit_code": verify["exit_code"],
            "duration_seconds": verify["duration_seconds"],
            "stdout_tail": verify["stdout_tail"],
            "stderr_tail": verify["stderr_tail"],
            "verified": verify["verified"],
        },
        "verified": verify["verified"],
        "status_after": goal.get("status"),
        "next_action": (
            "Audit green: the recorded result still reproduces."
            if verify["verified"]
            else "Audit red: the recorded result no longer reproduces. "
            "Evidence marked stale; investigate before trusting this outcome."
        ),
    }
    append_jsonl(outcome_iterations_path(state, slug), record)
    goal["evidence_stale"] = not verify["verified"]
    goal["last_audit"] = stamp
    goal["updated"] = stamp
    save_outcome(state, slug, goal)
    verification_record = {
        "kind": "executed",
        "claim": "Outcome {0} audit: {1}".format(slug, goal.get("success_criteria", "")),
        "command": goal["verify_command"],
        "exit_code": verify["exit_code"],
        "duration_seconds": verify["duration_seconds"],
        "stdout_tail": verify["stdout_tail"],
        "stderr_tail": verify["stderr_tail"],
        "verified": verify["verified"],
        "timestamp": stamp,
        "outcome": slug,
        "audit": True,
        "provenance": verification_provenance(state),
    }
    verification_record.update(verification_step_context(state, goal["verify_command"]))
    append_chained_jsonl(state / "verifications.jsonl", verification_record)
    if json_output:
        print(json.dumps({"goal": goal, "record": record}, indent=2))
    else:
        prefix = "[OK]" if verify["verified"] else "[FAIL]"
        print(
            "{0} Outcome {1} audit: verify exit {2} against recorded status "
            "{3}.".format(prefix, slug, verify["exit_code"], goal.get("status"))
        )
        print("next: {0}".format(record["next_action"]))
    return 0 if verify["verified"] else 2


def cmd_outcome_check(args, state):
    if run_disabled(os.environ):
        fail(OUTCOME_CHECK_DISABLED_MESSAGE)
        return 2
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    return with_outcome_lock(state, slug, lambda locked: _outcome_check_locked(args, state, slug, locked))


def _outcome_check_locked(args, state, slug, goal):
    settle_interrupted_attempt(state, slug, goal)
    if getattr(args, "audit", False):
        return run_outcome_audit(
            state, slug, goal, args.timeout, args.notes or "", args.json_output
        )
    if goal.get("status") in ("succeeded", "failed", "stopped"):
        if args.json_output:
            print(json.dumps({"goal": goal, "record": None}, indent=2))
        else:
            print("[OK] Outcome {0} is already {1}.".format(slug, goal.get("status")))
        return 0 if goal.get("status") == "succeeded" else 2
    iteration_count = int(goal.get("iteration_count", 0))
    max_iterations = int(goal.get("max_iterations", 1))
    if iteration_count >= max_iterations:
        goal["status"] = "failed"
        goal["stop_reason"] = "iteration budget exhausted before check"
        goal["updated"] = now_iso()
        save_outcome(state, slug, goal)
        if args.json_output:
            print(json.dumps({"goal": goal, "record": None}, indent=2))
        else:
            print("[FAIL] Outcome {0} failed: iteration budget exhausted.".format(slug))
        return 2
    record = perform_outcome_iteration(state, slug, goal, args.timeout, args.notes or "")
    verify = record["verify"]
    verified = record["verified"]
    status_after = record["status_after"]
    next_action = record["next_action"]
    metric_record = record["metric"]
    metric_score = metric_record.get("score") if metric_record else None
    if args.json_output:
        print(json.dumps({"goal": goal, "record": record}, indent=2))
    else:
        prefix = "[OK]" if verified else "[FAIL]"
        print(
            "{0} Outcome {1} iteration {2}/{3}: {4}".format(
                prefix, slug, record["iteration"], max_iterations, status_after
            )
        )
        print("verify exit: {0}".format(verify["exit_code"]))
        if metric_record:
            print("metric exit: {0}".format(metric_record["exit_code"]))
            if metric_score is not None:
                print("metric score: {0}".format(metric_score))
        print("next: {0}".format(next_action))
        if verify["stdout_tail"]:
            print("--- verify stdout (tail) ---")
            print(verify["stdout_tail"])
        if verify["stderr_tail"]:
            print("--- verify stderr (tail) ---")
            print(verify["stderr_tail"])
    return 0 if verified else 2


def cmd_outcome_run(args, state):
    """Self-driving dispatch loop: fire the agent command, run the verifier,
    record evidence, and repeat until the outcome is met, the iteration or cost
    budget is spent, the scope is violated, or the escalation threshold of
    consecutive red verifications is hit. Bounded and evidence-gated by design.
    """
    if run_disabled(os.environ):
        fail(OUTCOME_CHECK_DISABLED_MESSAGE)
        return 2
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    return with_outcome_lock(state, slug, lambda locked: _outcome_run_locked(args, state, slug, locked))


def _outcome_run_locked(args, state, slug, goal):
    agent_command = (goal.get("agent_command") or "").strip()
    if not agent_command:
        fail(
            "[FAIL] Outcome {0} has no agent command. Start it with "
            "outcome start --agent \"CMD\" to run it autonomously.".format(slug)
        )
        return 1
    settle_interrupted_attempt(state, slug, goal)
    if goal.get("status") in ("succeeded", "failed", "stopped"):
        print("[OK] Outcome {0} is already {1}.".format(slug, goal.get("status")))
        return 0 if goal.get("status") == "succeeded" else 2
    escalate_after = goal.get("escalate_after")
    allowed_paths = goal.get("allowed_paths") or []
    frozen_paths = goal.get("frozen_paths") or []
    watch_paths = bool(allowed_paths or frozen_paths)
    scope_baseline = goal.get("scope_baseline")
    if watch_paths and not scope_baseline:
        try:
            scope_baseline = start_scope_baseline(state)
        except ScopeInspectionError as exc:
            goal["status"] = "stopped"
            goal["stop_reason"] = "scope inspection unavailable: {0}".format(exc)
            goal["updated"] = now_iso()
            save_outcome(state, slug, goal)
            fail("[FAIL] Outcome {0} stopped before agent execution: {1}".format(slug, goal["stop_reason"]))
            return 2
        goal["scope_baseline"] = scope_baseline
        save_outcome(state, slug, goal)
    consecutive_red = 0
    final = goal.get("status", "active")
    while True:
        if int(goal.get("iteration_count", 0)) >= int(goal.get("max_iterations", 1)):
            goal["status"] = "failed"
            goal["stop_reason"] = "iteration budget exhausted"
            goal["updated"] = now_iso()
            save_outcome(state, slug, goal)
            final = "failed"
            break
        reservation = reserve_agent_attempt(state, slug, goal)
        attempt = run_shell_capture(agent_command, args.timeout)
        agent_record = agent_attempt_record(agent_command, attempt)
        try:
            changed = (
                self_driving_changed_paths(state, scope_baseline)
                if watch_paths
                else []
            )
            strict_violations = (
                paths_outside_scope(changed, allowed_paths) if allowed_paths else []
            )
        except ScopeInspectionError as exc:
            # The attempt ran and keeps its reserved iteration and cost.
            goal.pop("attempt_started", None)
            goal["status"] = "stopped"
            goal["stop_reason"] = "scope inspection unavailable: {0}".format(exc)
            goal["updated"] = now_iso()
            save_outcome(state, slug, goal)
            final = "stopped"
            break
        record = perform_outcome_iteration(
            state,
            slug,
            goal,
            args.timeout,
            args.notes or "",
            agent_record,
            strict_violations,
            changed,
            reservation,
        )
        print(
            "iteration {0}/{1}: agent exit {2}, verify {3}, status {4}".format(
                record["iteration"],
                goal.get("max_iterations"),
                attempt["exit_code"],
                "pass" if record["verified"] else "fail",
                record["status_after"],
            )
        )
        if record.get("frozen_violations"):
            # perform_outcome_iteration already stopped the goal and recorded
            # the frozen-path stop reason.
            final = "stopped"
            break
        if record["scope_violations"]:
            goal["status"] = "stopped"
            goal["stop_reason"] = "scope violation: {0}".format(
                ", ".join(record["scope_violations"][:5])
            )
            goal["updated"] = now_iso()
            save_outcome(state, slug, goal)
            final = "stopped"
            break
        if record["status_after"] in ("succeeded", "failed"):
            final = record["status_after"]
            break
        consecutive_red = 0 if record["verified"] else consecutive_red + 1
        if escalate_after and consecutive_red >= int(escalate_after):
            goal["status"] = "stopped"
            goal["stop_reason"] = "escalated after {0} consecutive failed verifications".format(
                consecutive_red
            )
            goal["updated"] = now_iso()
            save_outcome(state, slug, goal)
            final = "stopped"
            break
    goal = load_outcome(state, slug)[1] or goal
    print("[{0}] Outcome {1} finished: {2} ({3})".format(
        "OK" if final == "succeeded" else "FAIL",
        slug,
        final,
        goal.get("stop_reason") or "",
    ))
    if goal.get("max_cost") is not None:
        print("cost spent: {0}/{1}".format(round(float(goal.get("cost_spent", 0.0)), 4), goal.get("max_cost")))
    return 0 if final == "succeeded" else 2


def cmd_outcome_results(args, state):
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    iterations = read_jsonl(outcome_iterations_path(state, slug))
    if args.json_output:
        print(json.dumps({"goal": goal, "iterations": iterations}, indent=2))
        return 0
    print(format_outcome_status(slug, goal, iterations))
    for item in iterations:
        print("")
        print(
            "iteration {0}: verified={1}, status={2}".format(
                item.get("iteration"), item.get("verified"), item.get("status_after")
            )
        )
        verify = item.get("verify") or {}
        print("  verify exit: {0}".format(verify.get("exit_code")))
        metric = item.get("metric")
        if metric:
            print("  metric exit: {0}".format(metric.get("exit_code")))
            if metric.get("score") is not None:
                print("  metric score: {0}".format(metric.get("score")))
    return 0 if goal.get("status") == "succeeded" else 2


def cmd_outcome_stop(args, state):
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    goal["status"] = "stopped"
    goal["stop_reason"] = args.reason
    goal["updated"] = now_iso()
    save_outcome(state, slug, goal)
    clear_active_outcome_slug(state, slug)
    if args.json_output:
        print(json.dumps(goal, indent=2))
    else:
        print("[OK] Outcome {0} stopped: {1}".format(slug, args.reason))
    return 0
