"""Campaign steering over existing plans. The host remains the only executor.

Progress and evidence live in plan steps, with campaign goals and learning
stored as plan metadata. Legacy v5 campaign files are left untouched.
"""

import json
import math
import shlex
import sys
import time
from types import SimpleNamespace

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


def load_campaign(state, name=None):
    slug = _store["target_plan_slug"](state, name)
    plan = _store["load_plan"](state, slug) if slug else None
    if campaign_metadata(plan) is None:
        return None, None
    return slug, plan


def campaign_view(slug, plan):
    steps = plan["steps"]
    current = next((s for s in steps if s.get("status") == "in_progress"), None)
    if current is None:
        current = next((s for s in steps if s.get("status") != "completed"), None)
    done = sum(s.get("status") == "completed" for s in steps)
    status = "completed" if steps and done == len(steps) else "active"
    if current and current.get("status") in ("failed", "skipped"):
        status = "blocked"
    return {
        "plan": slug, "goal": plan["campaign"]["goal"],
        "success": plan["campaign"].get("success", ""),
        "status": status, "completed_steps": done, "total_steps": len(steps),
        "current_step": current, "lessons": plan["campaign"].get("lessons", []),
    }


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
        "Continue the existing plan; preserve its steps, results, and evidence.",
    ]
    step = view["current_step"]
    if step:
        lines.append("Current step: {0}. {1}".format(step["id"], step["title"]))
        lines.append("Step criteria: " + str(step.get("success_criteria") or ""))
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
    else:
        lines.append("Add verifiable steps with mythify plan add-step TITLE --verify COMMAND --plan " + quoted)
    for lesson in view["lessons"]:
        lines.append("Learning for subsequent work: " + lesson["text"])
    guardrail = (
        "This prompt is steering material, not verification evidence. The host performs "
        "the work, runs checks, and reports results. No worker or background executor is started."
    )
    lines.append("Guardrail: " + guardrail)
    return {
        "kind": "campaign", "selected_kind": "campaign", "title": "Campaign prompt packet",
        "source": {"type": "plan", "id": slug}, "context": view,
        "next_prompt": "\n".join(lines), "guardrail": guardrail,
    }


def start_campaign(args, state):
    goal = args.goal.strip()
    if not goal:
        raise ValueError("Campaign goal must be non-empty.")
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
        print("[OK] Resumed campaign on plan: " + slug)
        print("Next: mythify campaign prompt " + shlex.quote(slug))
        return 0
    if plan is None:
        tasks = tasks or [{"title": goal, "success_criteria": args.success or "",
                           "verify_command": args.verify or ""}]
        if args.verify:
            for task in tasks:
                task.setdefault("verify_command", args.verify)
        slug, error = _store["create_plan_record"](state, goal, name=args.name, steps=tasks)
        if error:
            raise ValueError(error)
        plan = _store["load_plan"](state, slug)
    plan["campaign"] = {"schema_version": 1, "goal": goal, "success": args.success or "",
                        "created": _store["now_iso"](), "lessons": []}
    plan["strict_context"] = True
    _store["save_plan"](state, slug, plan)
    print("[OK] Campaign uses plan {0}; existing progress and evidence are preserved.".format(slug))
    print("Next: mythify campaign prompt " + shlex.quote(slug))
    return 0


def cmd_campaign(args, state):
    try:
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
                print("Next: mythify campaign prompt " + shlex.quote(slug))
            return 0
        if action == "learn":
            if not args.lesson.strip():
                raise ValueError("Learning must be non-empty.")
            plan["campaign"].setdefault("lessons", []).append({"text": args.lesson.strip(),
                "apply_next": args.apply_next, "timestamp": _store["now_iso"]()})
            _store["save_plan"](state, slug, plan)
            print("[OK] Campaign learning recorded for subsequent prompts: " + slug)
            return 0
        # Reuse the plan gate for evidence matching, latest-red rejection,
        # source freshness, and transitions. Campaigns never waive that gate.
        if not _store["strict_step_evidence_enabled"]():
            raise ValueError("Campaign advancement requires strict evidence; unset MYTHIFY_REQUIRE_VERIFIED_STEP.")
        current = [s for s in plan["steps"] if s.get("status") == "in_progress"]
        if len(current) != 1:
            raise ValueError("Start exactly one step with step ID in_progress --plan " + shlex.quote(slug))
        return _store["cmd_step"](SimpleNamespace(
            id=str(current[0]["id"]), status="completed", result=args.result, plan=slug), state)
    except ValueError as exc:
        print("[FAIL] " + str(exc), file=sys.stderr)
        return 1


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
        if action == "watch":
            p.add_argument("--interval", type=float, default=5, help="Polling interval in seconds.")
            p.add_argument("--max-iterations", type=int, default=1, help="Poll count; 0 watches until interrupted.")
        p.set_defaults(handler=cmd_campaign)
