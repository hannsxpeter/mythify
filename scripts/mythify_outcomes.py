"""Outcome loop store and command handlers for the Mythify CLI."""

import json
import os
import re
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


def fail(message):
    print(message, file=sys.stderr)


def configure_outcome_loops(
    *,
    find_existing_slug_by_name_func=None,
    now_iso_func=None,
    slugify_func=None,
    run_shell_capture_func=None,
    verification_step_context_func=None,
    fail_func=None,
):
    global find_existing_slug_by_name, now_iso, slugify, run_shell_capture
    global verification_step_context, fail
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
    allowed = goal.get("allowed_paths") or []
    if allowed:
        lines.append("allowed path hints (advisory): {0}".format(", ".join(allowed)))
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
        "max_iterations": args.max_iterations,
        "iteration_count": 0,
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
    verify = run_shell_capture(goal["verify_command"], args.timeout)
    metric_record = None
    metric_ok = True
    metric_score = None
    if goal.get("metric_command"):
        metric = run_shell_capture(goal["metric_command"], args.timeout)
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
    verified = bool(verify["verified"] and metric_ok)
    next_iteration = iteration_count + 1
    if verified:
        status_after = "succeeded"
        next_action = "Outcome met. Report the evidence and stop."
    elif next_iteration >= max_iterations:
        status_after = "failed"
        next_action = "Iteration budget exhausted. Summarize the blocker and stop."
    else:
        status_after = "active"
        next_action = (
            "Outcome not met. Inspect verifier output, make another bounded attempt, "
            "then run outcome check again."
        )
    record = {
        "iteration": next_iteration,
        "timestamp": now_iso(),
        "notes": args.notes or "",
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
        "status_after": status_after,
        "next_action": next_action,
    }
    append_jsonl(outcome_iterations_path(state, slug), record)
    goal["iteration_count"] = next_iteration
    goal["status"] = status_after
    goal["last_verified"] = verified
    goal["updated"] = record["timestamp"]
    if metric_score is not None:
        best = goal.get("best_metric_score")
        if best is None or metric_score > best:
            goal["best_metric_score"] = metric_score
    if status_after == "failed":
        goal["stop_reason"] = "iteration budget exhausted"
    if status_after == "succeeded":
        goal["stop_reason"] = "success criteria verified"
    save_outcome(state, slug, goal)
    verification_record = {
        "kind": "executed",
        "claim": "Outcome {0}: {1}".format(slug, goal.get("success_criteria", "")),
        "command": goal["verify_command"],
        "exit_code": verify["exit_code"],
        "duration_seconds": verify["duration_seconds"],
        "stdout_tail": verify["stdout_tail"],
        "stderr_tail": verify["stderr_tail"],
        "verified": verify["verified"],
        "timestamp": record["timestamp"],
        "outcome": slug,
        "iteration": next_iteration,
    }
    verification_record.update(verification_step_context(state))
    append_jsonl(state / "verifications.jsonl", verification_record)
    if args.json_output:
        print(json.dumps({"goal": goal, "record": record}, indent=2))
    else:
        prefix = "[OK]" if verified else "[FAIL]"
        print(
            "{0} Outcome {1} iteration {2}/{3}: {4}".format(
                prefix, slug, next_iteration, max_iterations, status_after
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



