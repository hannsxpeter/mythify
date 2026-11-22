"""Campaign steering over existing plans. The host remains the only executor.

Progress and evidence live in plan steps, with campaign goals and learning
stored as plan metadata. Legacy v5 campaign files are left untouched.
"""

import json
import math
import os
import shlex
import sys
import time
from types import SimpleNamespace

from mythify_checks import add_checks_line
from mythify_campaign_scope import (
    path_is_relative, scope_conflicts, scope_snapshot, valid_prefixes, valid_scope,
)
from mythify_outcomes import (
    ScopeInspectionError, frozen_path_violations, normalize_frozen_paths, parse_allowed_paths,
)
from mythify_path_risk import HUMAN_INPUT_HELP
from mythify_plans import PlanBusy, plan_transaction
from mythify_ratchet import ACCEPT_TEST_DROP_HELP

_store = {}


def configure_campaign_store(**dependencies):
    _store.update(dependencies)


def campaign_metadata(plan):
    meta = plan.get("campaign") if isinstance(plan, dict) else None
    if not isinstance(meta, dict) or meta.get("schema_version") != 1:
        return None
    if not isinstance(meta.get("goal"), str) or not meta["goal"].strip():
        return None
    if not isinstance(meta.get("success", ""), str) or not isinstance(meta.get("lessons", []), list):
        return None
    if any(not isinstance(item, dict) or not isinstance(item.get("text"), str)
           for item in meta.get("lessons", [])):
        return None
    return meta


class CampaignPolicyError(RuntimeError):
    """A campaign's order or scope refused an operation."""


class CampaignScopeViolation(CampaignPolicyError):
    """Inspection found changes outside the current campaign scope."""


def ordered_campaign(plan):
    meta = plan.get("campaign")
    if not isinstance(meta, dict) or not ("ordered" in meta or "scope" in meta):
        return False
    if campaign_metadata(plan) is None or meta.get("ordered") is not True:
        raise CampaignPolicyError("Campaign order metadata is unreadable.")
    if "scope" in meta and not valid_scope(meta["scope"]):
        raise CampaignPolicyError(
            "Campaign scope metadata is unreadable. The saved policy or checkpoint is missing or malformed."
        )
    return True


def first_unfinished_step(plan):
    """Read an ordered plan's completed prefix and only eligible next step."""
    current = None
    ids = set()
    for step in plan["steps"]:
        if not isinstance(step, dict):
            raise CampaignPolicyError("Ordered campaign step is unreadable.")
        status, step_id = step.get("status"), step.get("id")
        if type(step_id) is not int or step_id in ids or status not in (
            "pending", "in_progress", "failed", "skipped", "completed"
        ) or ("allowed_paths" in step and not valid_prefixes(step["allowed_paths"])):
            raise CampaignPolicyError("Ordered campaign step is unreadable.")
        ids.add(step_id)
        if current is None and status != "completed":
            current = step
        elif current is not None and status != "pending":
            raise CampaignPolicyError("Ordered campaign steps are out of order after step {0}.".format(
                current["id"]))
    return current


def require_ordered_step(plan, step, completing=False):
    current = first_unfinished_step(plan)
    if current is None:
        raise CampaignPolicyError("All ordered campaign steps are already complete.")
    if step is not current:
        raise CampaignPolicyError("Ordered campaign: the first unfinished step is {0}.".format(
            current["id"]))
    if completing and step["status"] != "in_progress":
        raise CampaignPolicyError("Start ordered campaign step {0} before completing it.".format(
            step["id"]))


def read_campaign_scope(state, plan):
    scope = plan["campaign"]["scope"]
    try:
        _, snapshot, baseline = scope_snapshot(
            state, scope["frozen_paths"], scope["root"], scope["initial_commit"])
    except ScopeInspectionError as exc:
        raise CampaignPolicyError("Cannot inspect campaign scope: " + str(exc)) from exc
    if snapshot["ignore_rules"] != scope["checkpoint"]["ignore_rules"]:
        raise CampaignPolicyError("Cannot inspect campaign scope after Git's external ignore rules changed.")
    return snapshot, baseline


def check_campaign_scope(state, plan, step):
    scope = plan["campaign"].get("scope")
    if scope is None:
        return None, None
    snapshot, baseline = read_campaign_scope(state, plan)
    frozen, outside, changed = scope_conflicts(scope, step, snapshot, baseline)
    for paths, label in ((frozen, "Frozen campaign paths changed"),
                         (outside, "Paths outside campaign scope")):
        if paths:
            sample = ", ".join(repr(path) for path in paths[:10])
            more = " (+{0} more)".format(len(paths) - 10) if len(paths) > 10 else ""
            raise CampaignScopeViolation(label + ": " + sample + more)
    return snapshot, changed


def load_campaign(state, name=None):
    slug = _store["target_plan_slug"](state, name)
    plan = _store["load_plan"](state, slug) if slug else None
    if campaign_metadata(plan) is None:
        return None, None
    return slug, plan


def campaign_view(slug, plan):
    steps = plan["steps"]
    current = blocked_reason = None
    try:
        ordered = ordered_campaign(plan)
        if ordered:
            current = first_unfinished_step(plan)
        else:
            current = next((s for s in steps if s.get("status") == "in_progress"), None)
            if current is None:
                current = next((s for s in steps if s.get("status") != "completed"), None)
    except CampaignPolicyError as exc:
        blocked_reason = str(exc)
    done = sum(isinstance(s, dict) and s.get("status") == "completed" for s in steps)
    status = "completed" if steps and done == len(steps) else "active"
    if blocked_reason or (current and current.get("status") in ("failed", "skipped")):
        status = "blocked"
    view = {
        "plan": slug, "goal": plan["campaign"]["goal"],
        "success": plan["campaign"].get("success", ""),
        "status": status, "completed_steps": done, "total_steps": len(steps),
        "current_step": current, "lessons": plan["campaign"].get("lessons", []),
    }
    if blocked_reason:
        view["blocked_reason"] = blocked_reason
        return view
    if ordered:
        view["ordered"] = True
    scope = plan["campaign"].get("scope")
    if scope is not None:
        view.update({key: scope[key] for key in ("allowed_paths", "frozen_paths")})
    return view


def build_campaign_prompt(state, name=None):
    slug, plan = load_campaign(state, name)
    if plan is None:
        return {"error": "[FAIL] No campaign on this plan. Run: campaign start GOAL [--plan NAME]"}
    view = campaign_view(slug, plan)
    quoted = shlex.quote(slug)
    lines = [
        "Campaign: {0}".format(slug), "Goal: " + view["goal"],
        "Success criteria: " + (view["success"] or "Complete every plan step with executed evidence"),
        "Progress: {0}/{1} steps completed ({2})".format(
            view["completed_steps"], view["total_steps"], view["status"]),
    ]
    blocked_reason = view.get("blocked_reason")
    if blocked_reason:
        lines.append(blocked_reason)
    else:
        lines.append("Continue the existing plan; preserve its steps, results, and evidence.")
    step = view["current_step"]
    if view.get("ordered"):
        lines.append("Work on the first unfinished step only.")
    for key, label in (("allowed_paths", "Campaign paths"), ("frozen_paths", "Frozen paths")):
        if view.get(key):
            lines.append(label + ": " + ", ".join(view[key]))
    if step:
        lines.append("Current step: {0}. {1}".format(step["id"], step["title"]))
        lines.append("Step criteria: " + str(step.get("success_criteria") or ""))
        if "allowed_paths" in step:
            lines.append("Step paths: " + (", ".join(step["allowed_paths"]) or "no file changes"))
        if step.get("status") != "in_progress":
            lines.append("Start: mythify step {0} in_progress --plan {1}".format(step["id"], quoted))
        command = step.get("verify_command")
        if command:
            lines.append("Verify: mythify plan verify {0} --plan {1}".format(step["id"], quoted))
            lines.append("Stored verifier: " + command)
        else:
            lines.append("Select the plan for manual verification: mythify plan switch " + quoted)
            lines.append('Run a real check: mythify verify run "COMMAND" --claim "STEP OUTCOME"')
        lines.append('Advance: mythify campaign advance {0} --result "EXECUTED EVIDENCE"'.format(quoted))
    elif view["status"] == "completed":
        lines.append("All plan steps are complete. Report their evidence and any remaining limits.")
    elif not blocked_reason:
        lines.append("Add verifiable steps with mythify plan add-step TITLE --verify COMMAND --plan " + quoted)
    for lesson in view["lessons"]:
        lines.append("Learning for subsequent work: " + lesson["text"])
    guardrail = (
        "This prompt is steering material, not verification evidence. The host performs "
        "the work, runs checks, and reports results. No worker or background executor is started."
    )
    lines.append("Guardrail: " + guardrail)
    return add_checks_line({
        "kind": "campaign", "selected_kind": "campaign", "title": "Campaign prompt packet",
        "source": {"type": "plan", "id": slug}, "context": view,
        "next_prompt": "\n".join(lines), "guardrail": guardrail,
    }, state)


def _prefix_list(items, label):
    if not isinstance(items, list) or any(not isinstance(path, str) for path in items):
        raise ValueError(label + " must be a list of project-relative file or directory prefixes.")
    paths = []
    for item in items:
        path = item.strip().rstrip("/")
        while path.startswith("./"):
            path = path[2:]
        if not path_is_relative(path) or os.path.isabs(path):
            raise ValueError("Invalid {0} prefix: {1!r}".format(label, item))
        paths.append(path)
    return list(dict.fromkeys(paths))


def _csv(value, label):
    paths = parse_allowed_paths(value)
    if value is not None and (not paths or any(not item.strip() for item in value.split(","))):
        raise ValueError(label + " needs comma-separated non-empty paths.")
    return paths


def _start_campaign_scope(state, allowed, frozen):
    if frozen:
        frozen, error = normalize_frozen_paths(state, frozen)
        if error:
            raise ValueError(error.removeprefix("[FAIL] "))
        frozen = _prefix_list(frozen, "--frozen-paths")
    try:
        root, checkpoint, _ = scope_snapshot(state, frozen)
    except ScopeInspectionError as exc:
        raise ValueError("Cannot start campaign scope: " + str(exc)) from exc
    if checkpoint["head"] != checkpoint["index"] or checkpoint["head"] != checkpoint["disk"]:
        raise ValueError("Scoped campaigns require a clean Git worktree with raw files matching HEAD.")
    for prefix in frozen:
        if not frozen_path_violations(checkpoint["disk"], [prefix]):
            raise ValueError("Frozen path has no files in the clean Git worktree: " + repr(prefix))
    return {"root": root, "initial_commit": checkpoint["commit"], "checkpoint": checkpoint,
            "allowed_paths": allowed, "frozen_paths": frozen}


def start_campaign(args, state):
    goal = args.goal.strip()
    if not goal:
        raise ValueError("Campaign goal must be non-empty.")
    allowed = _prefix_list(_csv(args.allowed_paths, "--allowed-paths"), "--allowed-paths")
    frozen = _csv(args.frozen_paths, "--frozen-paths")
    tasks = None
    if args.tasks is not None:
        try:
            tasks = json.loads(args.tasks)
        except ValueError:
            raise ValueError("Invalid --tasks: expected a JSON array of step objects.") from None
        if not isinstance(tasks, list) or not tasks or any(
            not isinstance(t, dict) or not isinstance(t.get("title"), str) or not t["title"].strip()
            or ("verify_command" in t and not isinstance(t["verify_command"], str))
            for t in tasks
        ):
            raise ValueError("Invalid --tasks: provide non-empty step titles and string verify_command values.")
        for task in tasks:
            if "allowed_paths" in task:
                task["allowed_paths"] = _prefix_list(task["allowed_paths"], "Task allowed_paths")
    scoped = bool(allowed or frozen or any("allowed_paths" in task for task in tasks or []))
    ordered = args.ordered or scoped
    selector = args.plan or args.name
    slug = _store["target_plan_slug"](state, selector)
    if args.plan and slug is None:
        raise ValueError("Plan not found: " + args.plan)
    plan = _store["load_plan"](state, slug) if slug else None
    if slug and plan is None:
        raise ValueError("Plan could not be read: " + slug)
    if plan is not None and (tasks is not None or args.verify):
        raise ValueError("Existing plan preserved. Use its stored steps and verifiers; --tasks and --verify create new plans only.")
    if plan is not None and "campaign" in plan:
        meta = campaign_metadata(plan)
        if meta is None:
            raise ValueError("Unsupported campaign metadata; existing plan preserved.")
        if goal != meta["goal"] or (args.success and args.success != meta.get("success")):
            raise ValueError("Campaign already exists with a different goal or success criteria; resume with campaign prompt.")
        existing_ordered = ordered_campaign(plan)
        if existing_ordered:
            first_unfinished_step(plan)
        if frozen:
            frozen, error = normalize_frozen_paths(state, frozen)
            if error:
                raise ValueError(error.removeprefix("[FAIL] "))
            frozen = _prefix_list(frozen, "--frozen-paths")
        scope = meta.get("scope") or {}
        if ((ordered and not existing_ordered)
            or (args.allowed_paths is not None and allowed != scope.get("allowed_paths"))
            or (args.frozen_paths is not None and frozen != scope.get("frozen_paths"))):
            raise ValueError("Campaign already exists with different order or scope; resume with campaign prompt.")
        print("[OK] Resumed campaign on plan: " + slug)
        print("Next: mythify campaign prompt " + shlex.quote(slug))
        return 0
    metadata = {"schema_version": 1, "goal": goal, "success": args.success or "",
                "created": _store["now_iso"](), "lessons": []}
    if ordered:
        if plan is not None:
            first_unfinished_step(plan)
        metadata["ordered"] = True
    if scoped:
        metadata["scope"] = _start_campaign_scope(state, allowed, frozen)
    if plan is None:
        tasks = tasks or [{"title": goal, "success_criteria": args.success or "",
                           "verify_command": args.verify or ""}]
        if args.verify:
            for task in tasks:
                task.setdefault("verify_command", args.verify)
        slug, error = _store["create_plan_record"](
            state, goal, name=args.name, steps=tasks, campaign=metadata)
        if error:
            raise ValueError(error)
    else:
        plan["campaign"] = metadata
        plan["strict_context"] = True
        _store["save_plan"](state, slug, plan)
    print("[OK] Campaign uses plan {0}; existing progress and evidence are preserved.".format(slug))
    print("Next: mythify campaign prompt " + shlex.quote(slug))
    return 0


def cmd_campaign(args, state):
    try:
        if args.campaign_command in ("start", "learn", "advance"):
            with plan_transaction(state):
                return _cmd_campaign(args, state)
        return _cmd_campaign(args, state)
    except (ValueError, PlanBusy, CampaignPolicyError) as exc:
        print("[FAIL] " + str(exc), file=sys.stderr)
        return 1


def _cmd_campaign(args, state):
    if args.campaign_command == "start":
        return start_campaign(args, state)
    slug, plan = load_campaign(state, getattr(args, "name", None))
    if plan is None:
        raise ValueError("No campaign on this plan. Run: campaign start GOAL [--plan NAME]")
    action = args.campaign_command
    if action in ("prompt", "watch"):
        if action == "watch" and (
            not math.isfinite(args.interval) or args.interval <= 0 or args.max_iterations < 0
        ):
            raise ValueError("Use a positive interval and non-negative max-iterations.")
        iteration = 0
        previous = None
        while True:
            payload = build_campaign_prompt(state, slug)
            if payload.get("error"):
                raise ValueError(payload["error"])
            rendered = json.dumps(payload, indent=2) if args.json_output else payload["next_prompt"]
            if rendered != previous:
                print(rendered, flush=True)
                previous = rendered
            iteration += 1
            if action != "watch" or (args.max_iterations and iteration >= args.max_iterations):
                return 0
            time.sleep(args.interval)
    if action == "status":
        view = campaign_view(slug, plan)
        if args.json_output:
            print(json.dumps(view, indent=2))
        else:
            print("[OK] Campaign {0}: {1} ({2}/{3} steps completed)".format(
                slug, view["status"], view["completed_steps"], view["total_steps"]))
            print("Goal: " + view["goal"])
            if view.get("blocked_reason"):
                print(view["blocked_reason"])
            print("Next: mythify campaign prompt " + shlex.quote(slug))
        return 0
    if action == "learn":
        if not args.lesson.strip():
            raise ValueError("Learning must be non-empty.")
        if ordered_campaign(plan):
            first_unfinished_step(plan)
        plan["campaign"].setdefault("lessons", []).append({"text": args.lesson.strip(),
            "apply_next": args.apply_next, "timestamp": _store["now_iso"]()})
        _store["save_plan"](state, slug, plan)
        print("[OK] Campaign learning recorded for subsequent prompts: " + slug)
        return 0
    # Reuse the plan gate for evidence matching, latest-red rejection,
    # source freshness, and transitions. Campaigns never waive that gate.
    if not _store["strict_step_evidence_enabled"]():
        raise ValueError("Campaign advancement requires strict evidence; unset MYTHIFY_REQUIRE_VERIFIED_STEP.")
    if ordered_campaign(plan):
        first = first_unfinished_step(plan)
        current = [first] if first and first["status"] == "in_progress" else []
    else:
        current = [s for s in plan["steps"] if s.get("status") == "in_progress"]
    if len(current) != 1:
        raise ValueError("Start exactly one step with step ID in_progress --plan " + shlex.quote(slug))
    return _store["cmd_step"](SimpleNamespace(
        id=str(current[0]["id"]), status="completed", result=args.result, plan=slug,
        accept_test_drop=args.accept_test_drop, human_input=args.human_input), state)


def add_campaign_parser(sub):
    family = sub.add_parser("campaign", help="Continue durable goals through strict plan steps.")
    actions = family.add_subparsers(dest="campaign_command", metavar="ACTION", required=True)
    p = actions.add_parser("start", help="Attach a campaign to the current or named plan, or create one.")
    p.add_argument("goal", help="Campaign goal.")
    selector = p.add_mutually_exclusive_group()
    selector.add_argument("--plan", help="Existing plan to preserve and use.")
    selector.add_argument("--name", help="Plan name to create or resume.")
    p.add_argument("--success", default="", help="Success criteria for the campaign.")
    p.add_argument("--tasks", help="JSON step objects for a new plan only.")
    p.add_argument("--verify", default="", help="Default step verifier for a new plan only.")
    p.add_argument("--ordered", action="store_true", help="Allow only the first unfinished step to run.")
    p.add_argument("--allowed-paths", help="Comma-separated project path prefixes. Implies --ordered.")
    p.add_argument("--frozen-paths", help="Comma-separated immutable project path prefixes. Implies --ordered.")
    p.set_defaults(handler=cmd_campaign)
    for action in ("status", "prompt", "watch", "advance", "learn"):
        p = actions.add_parser(action, help={
            "status": "Show campaign progress from its plan.",
            "prompt": "Render the next host prompt without changing state.",
            "watch": "Watch for changed prompts; never execute work or advance steps.",
            "advance": "Complete the current plan step through its strict evidence gate.",
            "learn": "Record learning for subsequent host prompts.",
        }[action])
        if action == "learn":
            p.add_argument("lesson", help="Learning to retain.")
            p.add_argument("--plan", dest="name", help="Campaign plan name; defaults to active plan.")
            p.add_argument("--apply-next", action="store_true", help="Mark the learning for subsequent work.")
        else:
            p.add_argument("name", nargs="?", help="Campaign plan name; defaults to active plan.")
        if action in ("status", "prompt", "watch"):
            p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
        if action == "advance":
            p.add_argument("--result", required=True, help="Executed evidence supporting completion.")
            p.add_argument("--accept-test-drop", metavar="REASON", help=ACCEPT_TEST_DROP_HELP)
            p.add_argument("--human-input", metavar="TEXT", help=HUMAN_INPUT_HELP)
        if action == "watch":
            p.add_argument("--interval", type=float, default=5, help="Polling interval in seconds.")
            p.add_argument("--max-iterations", type=int, default=1, help="Poll count; 0 watches until interrupted.")
        p.set_defaults(handler=cmd_campaign)
