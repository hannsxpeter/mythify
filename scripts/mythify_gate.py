"""`verify gate`: a read-only check that no step is in flight without evidence.

A host can wire it into a stop or pre-commit hook. Exit 0 means every
in-progress step has passing evidence the step gate accepts right now; exit 2
means unverified work is in flight, with the next command for each failing step
on stderr. The gate never runs a command and never writes to the state
directory: it asks evaluate_step_evidence, the same judge `step ... completed`
uses, with no test-drop reason and no human input.
"""

import json
import shlex
import sys

from mythify_plans import active_pointer_path, find_plan_slug, load_plan, plan_path
from mythify_review_cover import unreadable_reviews
from mythify_step_gate import evaluate_step_evidence


def _failure(verdict):
    """(short label, kind) for a verdict the gate does not pass, else None."""
    if verdict["satisfying"] is None:
        if any("changed since the run" in text for _level, text in verdict["messages"]):
            return "the check changed since its last passing run, so that run no longer counts", "evidence"
        return "no passing executed run was recorded since the step started", "evidence"
    if verdict["moved"]:
        return "the passing run predates the current source state ({0})".format(verdict["moved"]), "evidence"
    if not verdict["ok"] and verdict["test_drop"]:
        return "the passing run executed fewer tests than the step's baseline", "test_drop"
    if not verdict["ok"]:
        return "high-risk paths changed without a covering review and the person's approval", "risk"
    return None


def _next_command(step, slug, kind):
    quoted, step_id = shlex.quote(slug), step.get("id")
    if kind == "test_drop":
        return ("restore the removed tests and re-run the verifier, or complete with: mythify step "
                "{0} completed RESULT --plan {1} --accept-test-drop REASON".format(step_id, quoted))
    if kind == "risk":
        return ("get a review that covers the current source (mythify review round NAME --clean), "
                "ask the person to approve, then: mythify step {0} completed RESULT --plan {1} "
                "--human-input TEXT".format(step_id, quoted))
    if str(step.get("verify_command") or "").strip():
        return "mythify plan verify {0} --plan {1}".format(step_id, quoted)
    return 'mythify verify run "COMMAND" --claim "step {0}"'.format(step_id)


def _plan_to_judge(state, plan_name):
    """(slug, plan, error): the plan the gate judges.

    No active pointer at all means nothing is in flight: (None, None, None).
    A pointer or --plan that names a plan which is missing or cannot be read is
    an error, so a typo in a hook never passes silently.
    """
    if plan_name:
        slug = find_plan_slug(state, plan_name)
        if slug is None:
            return None, None, "Plan not found: {0}".format(plan_name)
    else:
        pointer = active_pointer_path(state)
        if not pointer.is_file():
            return None, None, None
        try:
            slug = pointer.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None, None, "The active plan pointer cannot be read: {0}".format(pointer)
        if not slug:
            return None, None, None
        if not plan_path(state, slug).exists():
            return slug, None, "The active plan does not exist: {0}".format(slug)
    plan = load_plan(state, slug)
    if plan is None:
        return slug, None, "Plan {0} cannot be loaded: its file is unreadable or malformed.".format(slug)
    return slug, plan, None


def evaluate_gate(state, plan_name=None):
    """The gate verdict: {"ok", "plan", "steps": [{id, title, ok, reasons, next}]}.

    Only in-progress steps are judged. With no active plan pointer nothing is
    in flight. A plan that is named or pointed at but missing or unreadable
    adds an "error" key and a False "ok": the gate refuses rather than pass.
    """
    slug, plan, error = _plan_to_judge(state, plan_name)
    view = {"ok": error is None, "plan": slug, "steps": []}
    if error:
        view["error"] = error
        return view
    for step in (plan or {}).get("steps", []):
        if step.get("status") != "in_progress":
            continue
        verdict = evaluate_step_evidence(state, slug, plan, step)
        failure = _failure(verdict)
        row = {"id": step.get("id"), "title": step.get("title"), "ok": failure is None,
               "reasons": [], "next": ""}
        if failure is not None:
            row["reasons"] = [failure[0]] + [text for _level, text in verdict["messages"]]
            if failure[1] == "risk" and unreadable_reviews(state):
                row["reasons"].append("Review file(s) {0} could not be read and were ignored; they "
                                      "cover nothing.".format(", ".join(unreadable_reviews(state))))
            row["next"] = _next_command(step, slug, failure[1])
            view["ok"] = False
        view["steps"].append(row)
    return view


def cmd_verify_gate(args, state):
    view = evaluate_gate(state, args.plan)
    if view.get("error"):
        sys.stderr.write("[FAIL] verify gate cannot judge: {0}\n".format(view["error"]))
        return 1
    for row in view["steps"]:
        if row["ok"]:
            continue
        sys.stderr.write("[FAIL] Unverified work in flight: plan {0} step {1} ({2}): {3}.\n".format(
            view["plan"], row["id"], row["title"], row["reasons"][0]))
        sys.stderr.write("Next: {0}\n".format(row["next"]))
    if args.json_output:
        print(json.dumps(view, indent=2))
    elif view["ok"]:
        print("[OK] Gate passed: {0}.".format(
            "plan {0} has {1} in-progress step(s), all with current passing evidence".format(
                view["plan"], len(view["steps"])) if view["steps"]
            else "no in-progress step to verify"))
    return 0 if view["ok"] else 2


def add_gate_parser(actions):
    """Register `verify gate` under the `verify` action parsers."""
    p = actions.add_parser(
        "gate",
        help="Exit 2 while an in-progress step lacks current passing evidence (read-only).",
        description=(
            "Read-only check for host hooks. Judges each in-progress step of the "
            "active (or named) plan with the same rules as step completed, "
            "including stale evidence, a test-count drop and a high-risk change "
            "under strict context. Never runs a command and never writes state. "
            "Exits 0 when nothing is in flight or every in-progress step is "
            "verified, 2 otherwise, with the next command per step on stderr, "
            "and 1 when the active or named plan is missing or unreadable."
        ),
    )
    p.add_argument("--plan", help="Plan name; defaults to the active plan.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    # "readonly": resolve the state directory without creating any of it.
    p.set_defaults(handler=cmd_verify_gate, needs_state="readonly")
