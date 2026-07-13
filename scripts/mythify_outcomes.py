"""Outcome loop store and command handlers for the Mythify CLI."""

import json
import math
import os
import re
import subprocess
import sys

from mythify_io import _write_text_atomic, append_jsonl, read_json, read_jsonl, write_json_atomic

OUTCOME_CHECK_DISABLED_MESSAGE = (
    "[FAIL] outcome check is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded. Unset it to enable execution."
)
OUTCOME_STATUSES = ("active", "succeeded", "failed", "stopped")


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
    from pathlib import Path

    return state.parent if state.name == ".mythify" else Path.cwd()


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


class ScopeInspectionError(RuntimeError):
    pass


def _run_git_scope(root, args):
    try:
        run = subprocess.run(
            ["git", "-C", str(root)] + list(args),
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
    return {"git_commit": commit}


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


def self_driving_scope_violations(state, allowed_paths, baseline):
    root = outcome_project_root(state)
    commit = str((baseline or {}).get("git_commit") or "")
    if not commit:
        raise ScopeInspectionError("scope baseline commit is unavailable")
    _run_git_scope(root, ["merge-base", "--is-ancestor", commit, "HEAD"])
    tracked = _diff_name_status_paths(
        _run_git_scope(root, ["diff", "--name-status", "-z", "--find-renames", commit])
    )
    untracked_raw = _run_git_scope(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    untracked = [
        item for item in untracked_raw.decode("utf-8", "surrogateescape").split("\0") if item
    ]
    changed = list(dict.fromkeys(tracked + untracked))
    return paths_outside_scope(changed, allowed_paths)


def parse_metric_score(output):
    match = re.search(r"-?\d+(?:\.\d+)?", str(output or ""))
    return float(match.group(0)) if match else None


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
        "agent_command": getattr(args, "agent", None) or "",
        "max_iterations": args.max_iterations,
        "iteration_count": 0,
        "max_cost": max_cost,
        "cost_spent": 0.0,
        "escalate_after": escalate_after,
        "allowed_paths": parse_allowed_paths(args.allowed_paths),
        "visibility": args.visibility,
        "status": "active",
        "created": now,
        "updated": now,
        "last_verified": None,
        "best_metric_score": None,
        "stop_reason": None,
    }
    save_outcome(state, slug, goal)
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
        print("iterations: 0/{0}".format(args.max_iterations))
        print("next: make a bounded attempt, then run outcome check.")
    return 0


def cmd_outcome_status(args, state):
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
    """Read a MYTHIFY_COST=<number> line an agent may emit; None if absent."""
    match = re.search(r"MYTHIFY_COST\s*=\s*(-?\d+(?:\.\d+)?)", str(output or ""))
    return float(match.group(1)) if match else None


def perform_outcome_iteration(
    state, slug, goal, timeout, notes="", agent_record=None, scope_violations_override=None
):
    """Run one verifier (and optional metric) iteration, enforce scope and the
    cost budget, append the iteration and executed-verification records, update
    the goal, and return the iteration record. Shared by outcome check (the host
    made the attempt) and outcome run (the loop invoked the agent)."""
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
    violations = (
        list(scope_violations_override)
        if scope_violations_override is not None
        else scope_violations(state, goal.get("allowed_paths") or [])
    )
    scope_enforced = scope_violations_override is not None
    verified = bool(verify["verified"] and metric_ok and not (scope_enforced and violations))
    iteration_count = int(goal.get("iteration_count", 0))
    max_iterations = int(goal.get("max_iterations", 1))
    next_iteration = iteration_count + 1

    # Cost ledger applies only to the self-driving loop (an agent ran this
    # iteration). The host-driven `outcome check` path burns no cost, matching
    # the MCP outcome_check. Reported cost is clamped non-negative so a bad or
    # adversarial agent cannot drive the ledger down and neutralize --max-cost.
    iteration_cost = 0.0
    if agent_record is not None:
        reported = agent_record.get("cost")
        iteration_cost = max(0.0, float(reported)) if reported is not None else 1.0
    cost_spent = float(goal.get("cost_spent", 0.0)) + iteration_cost
    max_cost = goal.get("max_cost")
    budget_exhausted = (
        agent_record is not None and max_cost is not None and cost_spent >= float(max_cost)
    )

    if scope_enforced and violations:
        status_after = "stopped"
        next_action = "Scope violation detected. Stop and report the out-of-scope changes."
    elif verified:
        status_after = "succeeded"
        next_action = "Outcome met. Report the evidence and stop."
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
    record = {
        "iteration": next_iteration,
        "timestamp": now_iso(),
        "notes": notes,
        "agent": agent_record,
        "cost": iteration_cost,
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
        "verified": verified,
        "scope_violations": violations,
        "status_after": status_after,
        "next_action": next_action,
    }
    append_jsonl(outcome_iterations_path(state, slug), record)
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
    if status_after == "stopped" and scope_enforced:
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
    if scope_enforced and violations:
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
    verification_record.update(verification_step_context(state))
    append_jsonl(state / "verifications.jsonl", verification_record)
    return record


def cmd_outcome_check(args, state):
    if os.environ.get("MYTHIFY_DISABLE_RUN") == "1":
        fail(OUTCOME_CHECK_DISABLED_MESSAGE)
        return 2
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
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
    if os.environ.get("MYTHIFY_DISABLE_RUN") == "1":
        fail(OUTCOME_CHECK_DISABLED_MESSAGE)
        return 2
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    agent_command = (goal.get("agent_command") or "").strip()
    if not agent_command:
        fail(
            "[FAIL] Outcome {0} has no agent command. Start it with "
            "outcome start --agent \"CMD\" to run it autonomously.".format(slug)
        )
        return 1
    if goal.get("status") in ("succeeded", "failed", "stopped"):
        print("[OK] Outcome {0} is already {1}.".format(slug, goal.get("status")))
        return 0 if goal.get("status") == "succeeded" else 2
    escalate_after = goal.get("escalate_after")
    allowed_paths = goal.get("allowed_paths") or []
    scope_baseline = goal.get("scope_baseline")
    if allowed_paths and not scope_baseline:
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
        attempt = run_shell_capture(agent_command, args.timeout)
        agent_record = {
            "command": agent_command,
            "exit_code": attempt["exit_code"],
            "duration_seconds": attempt["duration_seconds"],
            "stdout_tail": attempt["stdout_tail"],
            "stderr_tail": attempt["stderr_tail"],
            "cost": parse_reported_cost(
                (attempt.get("stdout_tail") or "") + "\n" + (attempt.get("stderr_tail") or "")
            ),
        }
        try:
            strict_violations = (
                self_driving_scope_violations(state, allowed_paths, scope_baseline)
                if allowed_paths
                else []
            )
        except ScopeInspectionError as exc:
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
