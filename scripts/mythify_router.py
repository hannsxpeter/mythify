"""Prompt packet and workflow route helpers for the Mythify CLI.

Prompt packet kinds: next, handoff, failure, review, map, product. Route ids
come from protocol/workflow-router.json: direct, plan, map, product, outcome,
review, failure_recovery, handoff.
"""

import json
import shlex
import sys
from pathlib import Path

from mythify_classification import classify_task_text
from mythify_godfiles import godaudits_summary, godplans_summary
from mythify_loopfit import assess_loop_fit, loopfit_project_context, project_has_runnable_check
from mythify_maps import (
    format_ticket_line,
    frontier_tickets,
    get_active_map_slug,
    load_map,
    map_is_clear,
    map_next_action,
    open_tickets,
    ticket_name,
    ungraduated_fog,
)
from mythify_product import active_product_view, build_product_prompt_packet

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_ROUTER_PATH = REPO_ROOT / "protocol" / "workflow-router.json"
PROMPT_PACKET_KINDS = ("next", "handoff", "failure", "review", "map", "product")


def load_workflow_router():
    with WORKFLOW_ROUTER_PATH.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    routes = manifest.get("routes", [])
    seen = set()
    for entry in routes:
        route_id = str(entry.get("id", "")).strip()
        prompt_packet = str(entry.get("prompt_packet", "")).strip()
        if (
            not route_id
            or route_id in seen
            or prompt_packet not in PROMPT_PACKET_KINDS
        ):
            raise ValueError("Invalid workflow router entry")
        seen.add(route_id)
    if not routes:
        raise ValueError("Workflow router manifest is empty")
    return manifest


WORKFLOW_ROUTER = load_workflow_router()
PROMPT_PACKET_GUARDRAIL = (
    "Prompt packet output is steering material for the host agent, not verification evidence. "
    "The host must do the work, run checks when available, report issues in chat, and record evidence."
)
PROSE_QUALITY_INSTRUCTION = "Before delivering user-facing prose, remove boilerplate and vague claims; name the actor, action, evidence, or measurement, and preserve exact technical terms."
WORKFLOW_ROUTE_IDS = tuple(str(route["id"]) for route in WORKFLOW_ROUTER["routes"])
WORKFLOW_ROUTE_PROMPTS = {
    str(route["id"]): str(route.get("prompt_packet", "next"))
    for route in WORKFLOW_ROUTER["routes"]
}
WORKFLOW_ROUTE_GUARDRAIL = (
    "Workflow route output is steering material for the host agent, not verification evidence. "
    "The host must do the work, run checks when available, report issues in chat, and record evidence."
)
# "keep going" alone is a resume term; only "keep going until done" is full send.
ROUTE_FULL_SEND_TERMS = (
    "one shot", "one-shot", "one go", "in one go", "all in one go",
    "address all", "fix all", "do all", "do everything", "execute all",
    "continuous run", "keep going until done", "until no issues remain",
    "yolo", "full send", "ship it", "run it through",
)
ROUTE_PROMPT_TERMS = (
    "prompt packet", "reprompt", "inject the next task", "next prompt",
    "steer the chat", "steering prompt", "handoff packet",
)
ROUTE_RESEARCH_TERMS = (
    "research", "look up", "latest", "find sources", "source-backed",
    "online", "internet", "web search",
)
ROUTE_QUESTION_WORDS = ("what", "which", "how", "why", "whether", "where", "when", "who")
ROUTE_REVIEW_TERMS = (
    "audit", "review", "assess", "evaluate", "find issues", "code review",
    "risks", "risk sweep", "blast radius", "what could this break",
    "small diff i do not trust", "small diff i don't trust",
)
ROUTE_RESUME_TERMS = (
    "continue", "resume", "next", "keep going", "pick up", "carry on",
    "what is next",
)
ROUTE_OUTCOME_TERMS = (
    "until", "success criteria", "when tests pass", "when it passes",
    "verifier", "verify command", "outcome loop",
)
ROUTE_VERIFY_TERMS = (
    "verify", "test", "tests", "passes", "passing", "check", "build",
    "lint",
)
ROUTE_MAP_TERMS = (
    "wayfind", "wayfinder", "wayfinding", "decision map", "chart the map",
    "chart a map", "map the work", "map out the work", "loose idea",
    "too big for one session", "too big for one agent session",
    "what do we need to decide", "figure out what to decide",
    "decide before we plan", "scope this out", "not sure where to start",
    "shape of the work", "unknown unknowns",
)
ROUTE_GODPLANS_TERMS = ("godplans", "god plans")
ROUTE_GODAUDITS_TERMS = ("godaudits", "god audits")
# Terms that select a route on their own. A short prompt that carries one is
# not trivial. Resume, outcome, and verify terms are left out: they steer only
# together with durable state or with each other.
ROUTE_SELECTING_TERMS = (
    ROUTE_FULL_SEND_TERMS
    + ROUTE_PROMPT_TERMS
    + ROUTE_RESEARCH_TERMS
    + ROUTE_REVIEW_TERMS
    + ROUTE_MAP_TERMS
    + ROUTE_GODPLANS_TERMS
    + ROUTE_GODAUDITS_TERMS
)
WORKSPACE_DIR_NAME = ".mythify"


def artifact_project_root(state):
    if state is not None and state.name == WORKSPACE_DIR_NAME:
        return state.parent
    return Path.cwd()



def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_router dependencies are not configured")


get_active_slug = _missing_dependency
load_plan = _missing_dependency
plan_progress = _missing_dependency
next_pending_step = _missing_dependency
read_jsonl = _missing_dependency
build_verification_history_view = _missing_dependency
verification_label = _missing_dependency
git_status_summary = _missing_dependency
compact_report_detail = _missing_dependency
build_work_report = _missing_dependency
load_outcome = _missing_dependency


def fail(message):
    print(message, file=sys.stderr)


def configure_prompt_router(
    *,
    get_active_slug_func=None,
    load_plan_func=None,
    plan_progress_func=None,
    next_pending_step_func=None,
    read_jsonl_func=None,
    build_verification_history_view_func=None,
    verification_label_func=None,
    git_status_summary_func=None,
    compact_report_detail_func=None,
    build_work_report_func=None,
    load_outcome_func=None,
    fail_func=None,
):
    global get_active_slug, load_plan, plan_progress, next_pending_step
    global read_jsonl, build_verification_history_view, verification_label
    global git_status_summary, compact_report_detail, build_work_report
    global load_outcome, fail
    if get_active_slug_func is not None:
        get_active_slug = get_active_slug_func
    if load_plan_func is not None:
        load_plan = load_plan_func
    if plan_progress_func is not None:
        plan_progress = plan_progress_func
    if next_pending_step_func is not None:
        next_pending_step = next_pending_step_func
    if read_jsonl_func is not None:
        read_jsonl = read_jsonl_func
    if build_verification_history_view_func is not None:
        build_verification_history_view = build_verification_history_view_func
    if verification_label_func is not None:
        verification_label = verification_label_func
    if git_status_summary_func is not None:
        git_status_summary = git_status_summary_func
    if compact_report_detail_func is not None:
        compact_report_detail = compact_report_detail_func
    if build_work_report_func is not None:
        build_work_report = build_work_report_func
    if load_outcome_func is not None:
        load_outcome = load_outcome_func
    if fail_func is not None:
        fail = fail_func


# ---------------------------------------------------------------------------
# Prompt packets
# ---------------------------------------------------------------------------

def active_plan_packet_context(state):
    slug = get_active_slug(state)
    if not slug:
        return None
    plan = load_plan(state, slug)
    if plan is None:
        return None
    in_progress = None
    for step in plan.get("steps") or []:
        if step.get("status") == "in_progress":
            in_progress = step
            break
    done, total = plan_progress(plan)
    return {
        "slug": slug,
        "goal": plan.get("goal", ""),
        "progress": {"completed": done, "total": total},
        "current_step": in_progress,
        "next_pending": next_pending_step(plan),
        "steps": plan.get("steps") or [],
    }


def latest_failed_verification(state):
    records = read_jsonl(state / "verifications.jsonl")
    for index, record in reversed(list(enumerate(records, start=1))):
        if record.get("kind") == "executed" and record.get("verified") is False:
            return index, record
    return None, None


def latest_executed_verification(state):
    records = read_jsonl(state / "verifications.jsonl")
    for index, record in reversed(list(enumerate(records, start=1))):
        if record.get("kind") == "executed":
            return index, record
    return None, None


def latest_failure_reflection(state):
    records = read_jsonl(state / "reflections.jsonl")
    for index, record in reversed(list(enumerate(records, start=1))):
        if record.get("outcome") == "failure":
            return index, record
    return None, None


def prompt_recent_evidence(state, limit=5):
    rows = build_verification_history_view(state, recent=limit).get("records", [])
    items = []
    for row in rows:
        items.append({
            "verdict": row.get("verdict"),
            "label": verification_label(row),
            "exit_code": row.get("exit_code"),
            "timestamp": row.get("timestamp", ""),
        })
    return items


def prompt_plan_lines(plan_context):
    if not plan_context:
        return ["Active plan: none"]
    lines = [
        "Active plan: {0}".format(plan_context["slug"]),
        "Plan goal: {0}".format(plan_context.get("goal") or "not specified"),
        "Plan progress: {0}/{1} steps completed".format(
            plan_context["progress"]["completed"],
            plan_context["progress"]["total"],
        ),
    ]
    current = plan_context.get("current_step")
    pending = plan_context.get("next_pending")
    if current:
        lines.append("Current step: {0}. {1}".format(current.get("id"), current.get("title")))
        if current.get("success_criteria"):
            lines.append("Current criteria: {0}".format(current.get("success_criteria")))
    elif pending:
        lines.append("Next pending step: {0}. {1}".format(pending.get("id"), pending.get("title")))
        if pending.get("success_criteria"):
            lines.append("Next criteria: {0}".format(pending.get("success_criteria")))
    else:
        lines.append("Next pending step: none")
    return lines


def prompt_git_context():
    git_state = git_status_summary(Path.cwd())
    lines = [
        "Git branch: {0}".format(git_state.get("branch") or "unknown"),
        "Git status: {0}".format(git_state.get("status", "unknown")),
        "Git detail: {0}".format(git_state.get("detail", "")),
    ]
    for changed_path in git_state.get("changed_paths") or []:
        lines.append("Changed path: {0}".format(changed_path))
    return git_state, lines


def add_prose_quality_instruction(payload):
    if payload.get("error") or not payload.get("next_prompt"):
        return payload
    instruction = "- " + PROSE_QUALITY_INSTRUCTION
    prompt = payload["next_prompt"]
    if instruction in prompt:
        return payload
    marker = "\nGuardrail:"
    index = prompt.rfind(marker)
    if index < 0:
        payload["next_prompt"] = prompt.rstrip() + "\n" + instruction
    else:
        payload["next_prompt"] = prompt[:index] + "\n" + instruction + prompt[index:]
    return payload


def build_prompt_packet(kind, state, name=None, goal="", verify_command=""):
    if kind == "next":
        selected = select_next_prompt_packet_kind(state)
        payload = build_prompt_packet(
            selected,
            state,
            name=name,
            goal=goal,
            verify_command=verify_command,
        )
        if payload.get("error"):
            return payload
        payload["kind"] = "next"
        payload["selected_kind"] = selected
        payload["title"] = "Next workflow prompt packet"
        payload["next_prompt"] = "Selected next packet: {0}\n\n{1}".format(
            selected,
            payload.get("next_prompt", ""),
        )
        return payload
    if kind == "failure":
        return add_prose_quality_instruction(build_failure_prompt_packet(state, verify_command=verify_command))
    if kind == "handoff":
        return add_prose_quality_instruction(build_handoff_prompt_packet(state, goal=goal, verify_command=verify_command))
    if kind == "review":
        return add_prose_quality_instruction(build_review_prompt_packet(state, goal=goal, verify_command=verify_command))
    if kind == "map":
        return add_prose_quality_instruction(build_map_prompt_packet(state, name=name, goal=goal))
    if kind == "product":
        return add_prose_quality_instruction(
            build_product_prompt_packet(state, name=name, goal=goal, guardrail=PROMPT_PACKET_GUARDRAIL)
        )
    return {"error": "[FAIL] Unknown prompt packet kind: {0}".format(kind)}

def build_map_prompt_packet(state, name=None, goal=""):
    slug, record = load_map(state, name)
    if record is None:
        return {"error": "[FAIL] Map not found. Create one with: map create DESTINATION"}
    frontier = frontier_tickets(record)
    claimed = [t for t in open_tickets(record) if t.get("claimed_by")]
    fog = ungraduated_fog(record)
    decisions = record.get("decisions") or []
    out_of_scope = record.get("out_of_scope") or []
    lines = [
        "Wayfinding map prompt packet: {0}".format(slug),
        "Destination: {0}".format(record.get("destination", "")),
        "Status: {0}".format(record.get("status", "charting")),
    ]
    if goal:
        lines.append("Session goal: {0}".format(goal))
    if record.get("notes"):
        lines.append("Notes: {0}".format(record.get("notes")))
    lines.append("Decisions so far ({0}):".format(len(decisions)))
    for decision in decisions[-8:]:
        lines.append(
            "- {0} ({1}) - {2}".format(
                decision.get("title", ""),
                decision.get("ticket_id", ""),
                decision.get("gist", ""),
            )
        )
    lines.append("Frontier ({0}):".format(len(frontier)))
    for ticket in frontier:
        lines.append(format_ticket_line(record, ticket))
    if claimed:
        lines.append("Already claimed ({0}):".format(len(claimed)))
        for ticket in claimed:
            lines.append(format_ticket_line(record, ticket))
    if fog:
        lines.append("Not yet specified ({0}):".format(len(fog)))
        for item in fog[-8:]:
            lines.append("- {0}: {1}".format(item.get("id"), item.get("note", "")))
    if out_of_scope:
        lines.append("Out of scope ({0}):".format(len(out_of_scope)))
        for item in out_of_scope[-8:]:
            lines.append("- {0}: {1}".format(item.get("id"), item.get("note", "")))
    lines.extend([
        "",
        "Instructions:",
        "- Plan, do not do: every ticket resolves a decision, not a slice of the build.",
        "- Refer to the map and its tickets by name; a bare id reads as noise.",
        "- Claim one frontier ticket with map claim before any work; only research tickets run in parallel.",
        "- Resolve a HITL ticket only after the human answers, and pass their words as --human-input.",
        "- Resolve a task ticket that stores a verify command only after map verify passes.",
        "- Record the answer with map resolve, then ticket what the answer made specifiable and fog what it did not.",
        "- If the answer shows a ticket sits past the destination, rule it out of scope instead of resolving it.",
        "- Stop after one decision ticket; the map survives the session, the context window does not.",
        "- When no ticket and no fog remain, hand off with map promote.",
        "Next action: {0}".format(map_next_action(record)),
    ])
    lines.append("Guardrail: {0}".format(PROMPT_PACKET_GUARDRAIL))
    return {
        "kind": "map",
        "selected_kind": "map",
        "title": "Wayfinding map prompt packet",
        "source": {"type": "map", "id": slug},
        "context": {
            "destination": record.get("destination", ""),
            "status": record.get("status", "charting"),
            "notes": record.get("notes", ""),
            "goal": goal,
            "decisions": decisions[-8:],
            "frontier": [
                {
                    "id": ticket.get("id"),
                    "name": ticket_name(ticket),
                    "type": ticket.get("type"),
                    "mode": ticket.get("mode"),
                }
                for ticket in frontier
            ],
            "claimed": [ticket_name(ticket) for ticket in claimed],
            "fog": fog[-8:],
            "out_of_scope": out_of_scope[-8:],
            "clear": map_is_clear(record),
            "next_action": map_next_action(record),
        },
        "next_prompt": "\n".join(lines),
        "guardrail": PROMPT_PACKET_GUARDRAIL,
    }


def failed_command_streak(state, record):
    """Consecutive failed executed runs of RECORD's command, latest first."""
    if not record:
        return 0
    command = str(record.get("command") or "")
    streak = 0
    for row in reversed(read_jsonl(state / "verifications.jsonl")):
        if row.get("kind") != "executed" or str(row.get("command") or "") != command:
            continue
        if row.get("verified") is False:
            streak += 1
        else:
            break
    return streak


def build_failure_prompt_packet(state, verify_command=""):
    index, record = latest_failed_verification(state)
    reflection_index, reflection = latest_failure_reflection(state)
    streak = failed_command_streak(state, record)
    lines = ["Failure recovery prompt packet"]
    context = {
        "failed_verification_index": index,
        "failed_verification": record,
        "failure_reflection_index": reflection_index,
        "failure_reflection": reflection,
        "failed_command_streak": streak,
        "verify_command": verify_command,
    }
    if record:
        lines.extend([
            "Failed verification #{0}: {1}".format(index, record.get("claim") or record.get("command")),
            "Command: {0}".format(record.get("command", "")),
            "Exit code: {0}".format(record.get("exit_code")),
        ])
        stdout_tail = (record.get("stdout_tail") or "").strip()
        stderr_tail = (record.get("stderr_tail") or "").strip()
        if stdout_tail:
            lines.append("Stdout tail: {0}".format(compact_report_detail(stdout_tail)))
        if stderr_tail:
            lines.append("Stderr tail: {0}".format(compact_report_detail(stderr_tail)))
    else:
        lines.append("No failed executed verification was found.")
    if reflection:
        lines.append("Latest failure reflection: {0}".format(reflection.get("action", "")))
        if reflection.get("root_cause"):
            lines.append("Recorded root cause: {0}".format(reflection.get("root_cause")))
        if reflection.get("next"):
            lines.append("Recorded next action: {0}".format(reflection.get("next")))
    lines.extend([
        "",
        "Instructions:",
        "- Reproduce or inspect the failure before changing code.",
        "- Fix the smallest likely root cause.",
        "- Rerun the failed verifier, or the provided verifier if it is more specific.",
        "- Report the failure, fix, and verification evidence in chat.",
        "- If the fix is hard to reverse, first lay out 2-3 labeled approaches with tradeoffs, then recommend one.",
    ])
    if streak >= 2:
        lines.append(
            "- This verifier has failed {0} consecutive runs. Question the "
            "reference itself: is the success criterion right? Route that "
            "doubt to a human with a grilling ticket (map ticket TITLE "
            "--type grilling); never weaken the verifier to pass.".format(streak)
        )
    if verify_command:
        lines.append("- Verifier to run: {0}".format(verify_command))
    elif record and record.get("command"):
        lines.append("- Verifier to rerun: {0}".format(record.get("command")))
    lines.append("Guardrail: {0}".format(PROMPT_PACKET_GUARDRAIL))
    return {
        "kind": "failure",
        "selected_kind": "failure",
        "title": "Failure recovery prompt packet",
        "source": {"type": "verification", "id": index},
        "context": context,
        "next_prompt": "\n".join(lines),
        "guardrail": PROMPT_PACKET_GUARDRAIL,
    }


def build_handoff_prompt_packet(state, goal="", verify_command=""):
    plan_context = active_plan_packet_context(state)
    outcome_slug, outcome_record = load_outcome(state)
    report = build_work_report(state, since="start", recent=5, cursor="handoff-prompt", peek=True, mark=False)
    recent = prompt_recent_evidence(state, limit=3)
    lines = [
        "Handoff prompt packet",
        "Goal: {0}".format(goal or (plan_context or {}).get("goal") or "continue current Mythify work"),
    ]
    lines.extend(prompt_plan_lines(plan_context))
    active_outcome = None
    if outcome_record and outcome_record.get("status") == "active":
        active_outcome = {
            "id": outcome_slug,
            "goal": outcome_record.get("goal", ""),
            "iteration_count": outcome_record.get("iteration_count", 0),
            "max_iterations": outcome_record.get("max_iterations", 1),
            "verify_command": outcome_record.get("verify_command", ""),
        }
        lines.append(
            "Active outcome: {0} ({1}/{2} iterations); verifier: {3}".format(
                outcome_slug,
                active_outcome["iteration_count"],
                active_outcome["max_iterations"],
                active_outcome["verify_command"],
            )
        )
    if recent:
        lines.append("Recent evidence:")
        for item in recent:
            exit_text = "" if item.get("exit_code") is None else " exit {0}".format(item.get("exit_code"))
            lines.append("- {0}: {1}{2}".format(item.get("verdict"), item.get("label"), exit_text))
    if report.get("attention_events"):
        lines.append("Attention items:")
        for event in report["attention_events"][-5:]:
            lines.append("- {0}: {1}".format(event.get("level"), event.get("summary")))
    if report.get("events"):
        lines.append("Recent events:")
        for event in report["events"][-5:]:
            lines.append("- {0}".format(event.get("summary")))
    lines.extend([
        "",
        "Instructions:",
        "- Resume from this packet without assuming hidden chat context.",
        "- Re-read files before editing if the packet mentions uncertainty.",
    ])
    if plan_context or active_outcome:
        lines.extend([
            "- Continue the current step or outcome attempt, then verify before claiming completion.",
            "- Surface any failed checks or warnings in chat.",
        ])
    else:
        lines.extend([
            "- Read the smallest useful project context before editing.",
            "- Identify likely files, constraints, hidden risks, and the first reversible step.",
            "- For any hard-to-reverse fix, lay out 2-3 labeled approaches with tradeoffs, name the one that looks good but is not and why, then recommend one.",
            "- Produce or update a plan with checkable success criteria.",
            "- Do not implement until the first step and verifier are explicit.",
        ])
    if verify_command:
        lines.append("- Suggested verifier: {0}".format(verify_command))
    lines.append("Guardrail: {0}".format(PROMPT_PACKET_GUARDRAIL))
    return {
        "kind": "handoff",
        "selected_kind": "handoff",
        "title": "Handoff prompt packet",
        "source": {"type": "workflow_state", "id": (plan_context or {}).get("slug")},
        "context": {
            "goal": goal,
            "active_plan": plan_context,
            "active_outcome": active_outcome,
            "recent_evidence": recent,
            "recent_report": report,
            "verify_command": verify_command,
        },
        "next_prompt": "\n".join(lines),
        "guardrail": PROMPT_PACKET_GUARDRAIL,
    }

def build_review_prompt_packet(state, goal="", verify_command=""):
    plan_context = active_plan_packet_context(state)
    git_state, git_lines = prompt_git_context()
    recent = prompt_recent_evidence(state, limit=5)
    god_audit = godaudits_summary(artifact_project_root(state))
    god_audit_present = god_audit.get("present")
    lines = [
        "Review prompt packet",
        "Goal: {0}".format(goal or "review current changes and risks"),
    ]
    lines.extend(git_lines)
    lines.extend(prompt_plan_lines(plan_context))
    if god_audit_present:
        lines.append(
            "Godaudits audit: {0} ({1})".format(
                god_audit.get("path"), god_audit.get("detail")
            )
        )
    if recent:
        lines.append("Recent evidence:")
        for item in recent:
            exit_text = "" if item.get("exit_code") is None else " exit {0}".format(item.get("exit_code"))
            lines.append("- {0}: {1}{2}".format(item.get("verdict"), item.get("label"), exit_text))
    lines.extend([
        "",
        "Instructions:",
        "- Read the diff, the changed symbols, and the behavior that changed outside the visible diff.",
        "- State the one safety fact the change depends on and report its proof depth from 1 to 5; anything below executed depth 4 stays unproven.",
        "- Continue past symbol search into lifecycle timing, teardown, wire formats, database columns, feature flags, pinned dependencies, local patches, and downstream or cross-language consumers where relevant.",
        "- Give each risk a concrete failure mode, file and line, likelihood, impact, disposition, and cheapest check.",
        "- Separate confirmed, cleared, and unproven risks. A search that finds no caller is evidence, but never invent a caller or API.",
        "- Prove the safety fact with the smallest script, test, or running-app reproduction that exercises the shipped code. If proof is not cheap, mark it unproven.",
        "- For broad or wide changes, run independent runtime-lifecycle, data-contract, and dependency-config passes, merge their findings, then verify the integrated result.",
        "- Before merge, name the cheapest executable repro that catches the real bug.",
        "- Lead with actionable findings, with file and line references when possible.",
        "- Separate verified issues, warnings, open questions, and test gaps.",
        "- For subjective quality (design, UX, prose, feel), name a best-in-class reference and judge blind: put both side by side unlabeled and say which is better and why.",
        "- Grade the integrated deliverable (the running app, rendered page, or built artifact), not intermediate artifacts such as mockups, asset grids, or isolated diffs.",
        "- Critic verdicts are material, not verification evidence: record them with verify claim and keep executed checks as the completion gate.",
        "- If fixes are requested, address findings one by one and verify the result.",
        "- For any hard-to-reverse fix, lay out 2-3 labeled approaches with tradeoffs before recommending one.",
    ])
    if verify_command:
        lines.append("- Suggested verifier: {0}".format(verify_command))
    lines.append("Guardrail: {0}".format(PROMPT_PACKET_GUARDRAIL))
    return {
        "kind": "review",
        "selected_kind": "review",
        "title": "Review prompt packet",
        "source": {"type": "git", "id": git_state.get("branch")},
        "context": {
            "goal": goal,
            "git": git_state,
            "active_plan": plan_context,
            "recent_evidence": recent,
            "verify_command": verify_command,
            "godaudits_audit": god_audit if god_audit_present else None,
        },
        "next_prompt": "\n".join(lines),
        "guardrail": PROMPT_PACKET_GUARDRAIL,
    }


def select_next_prompt_packet_kind(state):
    _, latest = latest_executed_verification(state)
    if latest is not None and latest.get("verified") is False:
        return "failure"
    if get_active_map_slug(state):
        return "map"
    return "handoff"

def format_prompt_packet(payload):
    lines = [
        "[OK] Prompt packet {0}: {1}".format(
            payload.get("kind", "unknown"),
            payload.get("selected_kind", payload.get("kind", "unknown")),
        )
    ]
    if payload.get("source"):
        source = payload["source"]
        lines.append("Source: {0} {1}".format(source.get("type", ""), source.get("id", "")))
    lines.append("Next prompt:")
    lines.append(payload.get("next_prompt", ""))
    lines.append("Guardrail: {0}".format(payload.get("guardrail", PROMPT_PACKET_GUARDRAIL)))
    return "\n".join(lines)


def cmd_prompt_packet(args, state):
    payload = build_prompt_packet(
        args.packet_kind,
        state,
        name=getattr(args, "name", None),
        goal=getattr(args, "goal", "") or "",
        verify_command=getattr(args, "verify", "") or "",
    )
    if payload.get("error"):
        fail(payload["error"])
        return 1
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(format_prompt_packet(payload))
    return 0


# ---------------------------------------------------------------------------
# Workflow router
# ---------------------------------------------------------------------------

def workflow_route_state(state):
    if state is None:
        active_plan_slug = None
        active_plan = None
        active_outcome_slug, active_outcome = None, None
        active_map_slug, active_map = None, None
        latest_index, latest = None, None
    else:
        active_plan_slug = get_active_slug(state)
        active_plan = load_plan(state, active_plan_slug) if active_plan_slug else None
        active_outcome_slug, active_outcome = load_outcome(state)
        active_map_slug, active_map = load_map(state)
        latest_index, latest = latest_executed_verification(state)
    latest_view = None
    if latest is not None:
        latest_view = {
            "index": latest_index,
            "verified": latest.get("verified"),
            "claim": latest.get("claim", ""),
            "command": latest.get("command", ""),
            "exit_code": latest.get("exit_code"),
            "timestamp": latest.get("timestamp", ""),
        }
    plan_view = None
    if active_plan:
        done, total = plan_progress(active_plan)
        pending = next_pending_step(active_plan)
        plan_view = {
            "id": active_plan_slug,
            "goal": active_plan.get("goal", ""),
            "progress": {"completed": done, "total": total},
            "next_pending": {
                "id": pending.get("id"),
                "title": pending.get("title", ""),
                "success_criteria": pending.get("success_criteria", ""),
            } if pending else None,
        }
    outcome_view = None
    # Only an outcome that is still active steers routing; a finished loop stays
    # visible in status and outcome status but must not be a routing target.
    if active_outcome and active_outcome.get("status") == "active":
        outcome_view = {
            "id": active_outcome_slug,
            "goal": active_outcome.get("goal", ""),
            "status": active_outcome.get("status", ""),
            "iteration_count": active_outcome.get("iteration_count", 0),
            "max_iterations": active_outcome.get("max_iterations", 0),
        }
    map_view = None
    # A promoted map has handed its destination to a plan, so it stops steering.
    if active_map and active_map.get("status") != "promoted":
        map_view = {
            "id": active_map_slug,
            "destination": active_map.get("destination", ""),
            "status": active_map.get("status", "charting"),
            "open_tickets": len(open_tickets(active_map)),
            "frontier": len(frontier_tickets(active_map)),
            "decisions": len(active_map.get("decisions") or []),
            "fog": len(ungraduated_fog(active_map)),
            "clear": map_is_clear(active_map),
        }
    root = artifact_project_root(state)
    godplans_view = godplans_summary(root)
    godaudits_view = godaudits_summary(root)
    return {
        "active_plan": plan_view,
        "active_outcome": outcome_view,
        "active_map": map_view,
        "active_product": active_product_view(state),
        "latest_executed_verification": latest_view,
        "godplans_plan": godplans_view if godplans_view.get("present") else None,
        "godaudits_audit": godaudits_view if godaudits_view.get("present") else None,
    }


def god_artifact_has_open_tasks(view):
    if not view:
        return False
    total = view.get("tasks_total")
    done = view.get("tasks_done")
    if not isinstance(total, int) or not isinstance(done, int):
        return False
    return done < total


def _wordish(text):
    return "".join(ch if ch.isalnum() else " " for ch in str(text).lower())


def _contains_any(text, terms):
    haystack = " {0} ".format(" ".join(_wordish(text).split()))
    matches = []
    for term in terms:
        needle_words = _wordish(term).split()
        if needle_words and " {0} ".format(" ".join(needle_words)) in haystack:
            matches.append(term)
    return matches


def route_has(text, terms):
    return bool(_contains_any(text, terms))


def route_command_for(route, task, state_view):
    quoted_task = shlex.quote(str(task or "").strip() or "task")
    if route == "failure_recovery":
        return "mythify prompt failure"
    if route == "outcome":
        if state_view.get("active_outcome"):
            return "mythify outcome status"
        return (
            "mythify outcome start {0} --success {1} --verify {2}"
        ).format(quoted_task, shlex.quote("DEFINE SUCCESS"), shlex.quote("DEFINE VERIFIER"))
    if route == "map":
        active_map = state_view.get("active_map")
        if active_map:
            if active_map.get("clear"):
                return "mythify map promote"
            return "mythify prompt map"
        return "mythify map create {0}".format(quoted_task)
    if route == "product":
        product = state_view.get("active_product")
        if not product:
            return 'mythify product create "TITLE" --problem "..." --user "..."'
        return "mythify product show" if product.get("ready") else "mythify product check"
    if route == "review":
        if god_artifact_has_open_tasks(state_view.get("godaudits_audit")):
            return "mythify plan import --source godaudits"
        return "mythify prompt review --goal {0}".format(quoted_task)
    if route == "handoff":
        return "mythify prompt handoff --goal {0}".format(quoted_task)
    if route == "plan":
        if god_artifact_has_open_tasks(state_view.get("godplans_plan")):
            return "mythify plan import --source godplans"
        return "mythify plan create {0}".format(quoted_task)
    return "Answer directly in the initiating chat; run verify run if an executable completion check exists."


def route_state_writes(route, state_view):
    if route == "failure_recovery":
        return [
            "record reflection after diagnosing the red check",
            "record verify run after the recovery attempt",
            "update the active step with evidence when fixed",
        ]
    if route == "outcome":
        if state_view.get("active_outcome"):
            return ["outcome check after each bounded attempt"]
        return ["outcome start with explicit success criteria and verifier"]
    if route == "map":
        active_map = state_view.get("active_map")
        if active_map and active_map.get("clear"):
            return ["map promote once the destination is handed to a plan"]
        if active_map:
            return [
                "map claim before working a ticket",
                "map resolve with the answer, and human input for a HITL ticket",
                "map ticket for newly specifiable questions",
                "map fog for what is still too dim to ticket",
            ]
        return [
            "map create with the destination",
            "map ticket for each decision you can already state",
            "map fog for what you cannot state sharply yet",
        ]
    if route == "product":
        product = state_view.get("active_product")
        if not product:
            return [
                "product create with the problem in the user's words and one primary user",
                "product outcome, non-goal, bet, and risk entries",
                "product check until it exits 0",
                "product approve with the human's words before any promote",
            ]
        if not product.get("ready"):
            return [
                "product outcome, non-goal, bet, or risk entries that close the check gaps",
                "product check until it exits 0",
            ]
        return [
            "product approve with the human's words",
            "product promote for the top-priority bet",
            "product measure after the plan ships",
            "product decide with the decider's words",
        ]
    if route == "review":
        if god_artifact_has_open_tasks(state_view.get("godaudits_audit")):
            return [
                "plan import --source godaudits when remediation is accepted",
                "step updates and verify run per remediation task",
                "report findings in chat",
            ]
        return ["report findings in chat", "verify run supporting checks when fixes are made"]
    if route == "handoff":
        return ["step updates and verify run as the active plan advances"]
    if route == "plan":
        if god_artifact_has_open_tasks(state_view.get("godplans_plan")):
            return [
                "plan import --source godplans",
                "step updates",
                "verify run per imported task",
                "reflect on failures",
            ]
        return ["plan create", "step updates", "verify run", "reflect on failures"]
    return []


def workflow_route_evidence(route, state_view, classification):
    evidence = [
        {
            "type": "router_manifest",
            "version": WORKFLOW_ROUTER.get("version"),
            "routes": WORKFLOW_ROUTE_IDS,
        },
        {
            "type": "classification",
            "task_type": classification.get("task_type"),
            "risk": classification.get("risk"),
            "execution_profile": classification.get("execution_profile"),
        },
    ]
    latest = state_view.get("latest_executed_verification")
    if latest:
        evidence.append({"type": "latest_executed_verification", **latest})
    for key in (
        "active_plan",
        "active_outcome",
        "active_map",
        "active_product",
        "godplans_plan",
        "godaudits_audit",
    ):
        if state_view.get(key):
            evidence.append({"type": key, **state_view[key]})
    evidence.append({
        "type": "route_decision",
        "route": route,
        "mutates_state": False,
    })
    return evidence


def research_route(text, classification):
    """Research-like prompts: a map when several questions sit in fog, else direct.

    Source-backed lookups no longer get their own record type. A prompt that
    asks two or more questions while the classification reads it as ambiguous
    is a decision map in disguise; anything else is answered directly, with a
    verify claim citing sources when nothing executable exists.
    """
    question_marks = text.count("?")
    question_words = sum(1 for word in _wordish(text).split() if word in ROUTE_QUESTION_WORDS)
    multi_question = question_marks >= 2 or question_words >= 2
    foggy = classification.get("ambiguity") in ("medium", "high")
    if multi_question and foggy:
        return (
            "map",
            "The prompt is research-like and asks several questions whose answers are "
            "not visible yet, so chart a decision map and settle them one ticket at a time.",
        )
    return (
        "direct",
        "The prompt is a research-like lookup with one clear question, so answer it "
        "directly, cite sources, and record a verify claim when nothing executable exists.",
    )


def select_workflow_route(task, state_view, classification):
    text = " ".join(str(task or "").lower().split())
    latest = state_view.get("latest_executed_verification")
    if latest and latest.get("verified") is False:
        return (
            "failure_recovery",
            "The latest executed verification is red, so recover that failure before advancing unrelated work.",
        )
    if route_has(text, ROUTE_PROMPT_TERMS):
        return (
            "handoff",
            "The prompt asks for steering material rather than immediate execution, so render the handoff packet.",
        )
    if state_view.get("active_outcome") and (
        route_has(text, ROUTE_RESUME_TERMS) or route_has(text, ROUTE_OUTCOME_TERMS)
    ):
        return (
            "outcome",
            "An active outcome loop exists and the prompt asks to continue or check it.",
        )
    if route_has(text, ROUTE_OUTCOME_TERMS) and route_has(text, ROUTE_VERIFY_TERMS):
        return (
            "outcome",
            "The prompt names success or verification conditions, so use a bounded outcome loop.",
        )
    if route_has(text, ROUTE_FULL_SEND_TERMS):
        if state_view.get("active_plan"):
            return (
                "handoff",
                "The prompt uses full-send language and an active plan exists, so drive that plan to done step by step with evidence.",
            )
        return (
            "plan",
            "The prompt uses full-send language, so plan the whole job with verifiable steps and drive it to done step by step.",
        )
    if classification.get("task_type") == "product":
        return (
            "product",
            "The prompt asks product-planning questions (problem, users, outcomes, "
            "priorities), so frame them in a product record and get a human's "
            "approval before any execution plan.",
        )
    if route_has(text, ROUTE_MAP_TERMS):
        return (
            "map",
            "The prompt describes work whose route is not visible yet, so chart a "
            "decision map before planning execution.",
        )
    if state_view.get("active_map") and route_has(text, ROUTE_RESUME_TERMS):
        return (
            "map",
            "An active decision map exists and the prompt asks to continue, so work "
            "its next ticket before planning execution.",
        )
    if route_has(text, ROUTE_GODAUDITS_TERMS):
        return (
            "review",
            "The prompt names godaudits, so route to review work around the .godaudits audit artifact.",
        )
    if route_has(text, ROUTE_GODPLANS_TERMS):
        return (
            "plan",
            "The prompt names godplans, so route to plan work around the .godplans plan artifact.",
        )
    if classification.get("task_type") == "research" or route_has(text, ROUTE_RESEARCH_TERMS):
        return research_route(text, classification)
    if classification.get("task_type") == "review" or route_has(text, ROUTE_REVIEW_TERMS):
        return (
            "review",
            "The task asks for audit, review, evaluation, or issue finding.",
        )
    if state_view.get("active_plan") and route_has(text, ROUTE_RESUME_TERMS):
        return (
            "handoff",
            "An active plan exists and the prompt asks to continue from durable state.",
        )
    if classification.get("execution_profile") == "direct":
        return (
            "direct",
            "Classification says this is a simple question or single reversible action.",
        )
    return (
        "plan",
        "Classification says this is multi-step work that should be planned and verified.",
    )


def active_loop_collision(state_view):
    """Name the live loop families and which one steers, or None when at most
    one family is active. Collisions are legal; unnamed collisions are not:
    the priority order picks a winner, and this makes the pick visible."""
    families = [
        label
        for key, label in (
            ("active_outcome", "outcome"),
            ("active_map", "map"),
            ("active_plan", "plan"),
        )
        if state_view.get(key)
    ]
    if len(families) < 2:
        return None
    return {
        "families": families,
        "steers": families[0],
        "note": (
            "multiple loop families are active ({0}); under the route "
            "priority order, {1} steers when the prompt is ambiguous".format(
                ", ".join(families), families[0]
            )
        ),
    }


def route_loop_fit(task):
    root, is_git = loopfit_project_context()
    return assess_loop_fit(task, is_git, project_has_runnable_check(root))


def build_workflow_route(task, state, classification):
    state_view = workflow_route_state(state)
    route, reason = select_workflow_route(task, state_view, classification)
    if route not in WORKFLOW_ROUTE_IDS:
        route = "plan"
        reason = "Router returned an unknown route, so Mythify fell back to a verifiable plan."
    god_plan = state_view.get("godplans_plan")
    god_audit = state_view.get("godaudits_audit")
    if route == "plan" and god_artifact_has_open_tasks(god_plan):
        reason += (
            " A godplans plan exists at {0} ({1}); import it with plan import "
            "instead of drafting a new plan.".format(
                god_plan.get("path"), god_plan.get("detail")
            )
        )
    if route == "review" and god_audit:
        reason += " A godaudits audit exists at {0} ({1}).".format(
            god_audit.get("path"), god_audit.get("detail")
        )
    packet_kind = WORKFLOW_ROUTE_PROMPTS.get(route, "next")
    return {
        "kind": "workflow_route",
        "route": route,
        "reason": reason,
        "loop_collision": active_loop_collision(state_view),
        "input": str(task or ""),
        "classification": classification,
        "loop_fit": route_loop_fit(task),
        "state": state_view,
        "next_command": route_command_for(route, task, state_view),
        "prompt_packet": {
            "kind": packet_kind,
            "command": "mythify prompt {0}".format(packet_kind),
        },
        "verification_strategy": classification.get("verification", ""),
        "chat_policy": {
            "executor": "initiating_host",
            "surface": "chat",
            "report_issues": True,
            "progress_command": "mythify report --since last --cursor chat --format chat",
            "host_boundary": "Run the next step in the chat or host that initiated Mythify unless the user explicitly hands it elsewhere.",
        },
        "pause_rules": [
            "destructive or irreversible actions",
            "real scope changes",
            "missing credentials, secrets, or billing acknowledgements",
            "decisions only the user can make",
        ],
        "state_writes": route_state_writes(route, state_view),
        "evidence": workflow_route_evidence(route, state_view, classification),
        "guardrail": WORKFLOW_ROUTE_GUARDRAIL,
    }


def format_workflow_route(payload):
    lines = [
        "[OK] Workflow route: {0}".format(payload.get("route", "unknown")),
        "Reason: {0}".format(payload.get("reason", "")),
        "Next command: {0}".format(payload.get("next_command", "")),
        "Prompt packet: {0} ({1})".format(
            payload.get("prompt_packet", {}).get("kind", ""),
            payload.get("prompt_packet", {}).get("command", ""),
        ),
        "Verification strategy: {0}".format(payload.get("verification_strategy", "")),
    ]
    classification = payload.get("classification") or {}
    framing = classification.get("framing") or {}
    parallelism = classification.get("parallelism") or {}
    review = classification.get("review") or {}
    lines.append(
        "Classification: type={0}; risk={1}; ambiguity={2}; profile={3}".format(
            classification.get("task_type", ""),
            classification.get("risk", ""),
            classification.get("ambiguity", ""),
            classification.get("execution_profile", ""),
        )
    )
    lines.append(
        "Advisories: framing={0}; parallelism={1} (chooser: {2}); independent review={3}".format(
            framing.get("level", "none"),
            parallelism.get("fit", "none"),
            parallelism.get("chooser", "host"),
            "yes" if review.get("independent") else "no",
        )
    )
    loop_fit = payload.get("loop_fit") or {}
    if loop_fit:
        lines.append(
            "Loop fit: {0}; {1}".format(
                loop_fit.get("recommendation", ""), loop_fit.get("reason", "")
            )
        )
    if classification.get("quality_climb") == "detected":
        lines.append(
            "Quality climb: {0}".format(classification.get("quality_climb_protocol", ""))
        )
    collision = payload.get("loop_collision")
    if collision:
        lines.append("Loop collision: {0}".format(collision.get("note", "")))
    policy = payload.get("chat_policy") or {}
    lines.append("Chat policy: executor={0}; surface={1}; report_issues={2}".format(
        policy.get("executor", "initiating_host"),
        policy.get("surface", "chat"),
        str(policy.get("report_issues", True)).lower(),
    ))
    if payload.get("state_writes"):
        lines.append("Expected state writes:")
        for item in payload["state_writes"]:
            lines.append("- {0}".format(item))
    if payload.get("pause_rules"):
        lines.append("Pause for:")
        for item in payload["pause_rules"]:
            lines.append("- {0}".format(item))
    lines.append("Guardrail: {0}".format(payload.get("guardrail", WORKFLOW_ROUTE_GUARDRAIL)))
    return "\n".join(lines)


def cmd_route(args, state):
    classification = classify_task_text(args.task, route_terms=ROUTE_SELECTING_TERMS)
    payload = build_workflow_route(args.task, state, classification)
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(format_workflow_route(payload))
    return 0
