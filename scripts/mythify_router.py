"""Prompt packet and workflow route helpers for the Mythify CLI."""

import json
import shlex
import sys
from pathlib import Path

from mythify_classification import classify_task_text
from mythify_godfiles import godaudits_summary, godplans_summary
from mythify_model_policy import build_model_policy, run_model_triage
from mythify_plan_horizon import route_plan_horizon
from mythify_workflows import (
    build_campaign_prompt_payload,
    campaign_next_action,
    campaign_progress,
    current_campaign_task,
    get_active_campaign_slug,
    get_active_research_slug,
    load_campaign,
    load_research,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_ROUTER_PATH = REPO_ROOT / "protocol" / "workflow-router.json"


def load_workflow_router():
    with WORKFLOW_ROUTER_PATH.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    routes = manifest.get("routes", [])
    seen = set()
    for entry in routes:
        route_id = str(entry.get("id", "")).strip()
        prompt_packet = str(entry.get("prompt_packet", "")).strip()
        if not route_id or route_id in seen or not prompt_packet:
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
WORKFLOW_ROUTE_IDS = tuple(str(route["id"]) for route in WORKFLOW_ROUTER["routes"])
WORKFLOW_ROUTE_PROMPTS = {
    str(route["id"]): str(route.get("prompt_packet", "next"))
    for route in WORKFLOW_ROUTER["routes"]
}
WORKFLOW_ROUTE_GUARDRAIL = (
    "Workflow route output is steering material for the host agent, not verification evidence. "
    "The host must do the work, run checks when available, report issues in chat, and record evidence."
)
ROUTE_FULL_SEND_TERMS = (
    "one shot", "one-shot", "one go", "in one go", "all in one go",
    "address all", "fix all", "do all", "do everything", "execute all",
    "continuous run", "keep going", "keep going until done", "until no issues remain",
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
ROUTE_REVIEW_TERMS = (
    "audit", "review", "assess", "evaluate", "find issues", "code review",
    "risks", "risk sweep",
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
ROUTE_GODPLANS_TERMS = ("godplans", "god plans")
ROUTE_GODAUDITS_TERMS = ("godaudits", "god audits")
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
read_host_model_state = _missing_dependency


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
    read_host_model_state_func=None,
    fail_func=None,
):
    global get_active_slug, load_plan, plan_progress, next_pending_step
    global read_jsonl, build_verification_history_view, verification_label
    global git_status_summary, compact_report_detail, build_work_report
    global load_outcome, read_host_model_state, fail
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
    if read_host_model_state_func is not None:
        read_host_model_state = read_host_model_state_func
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
    if kind == "campaign":
        slug, record = load_campaign(state, name)
        if record is None:
            return {"error": "[FAIL] Campaign not found. Start one with: campaign start GOAL"}
        campaign_payload = build_campaign_prompt_payload(slug, record)
        return {
            "kind": "campaign",
            "selected_kind": "campaign",
            "title": "Campaign prompt packet",
            "source": {"type": "campaign", "id": slug},
            "context": campaign_payload,
            "next_prompt": campaign_payload.get("next_prompt", ""),
            "guardrail": PROMPT_PACKET_GUARDRAIL,
        }
    if kind == "research":
        return build_research_prompt_packet(state, name=name, goal=goal, verify_command=verify_command)
    if kind == "analysis":
        return build_analysis_prompt_packet(state, goal=goal, verify_command=verify_command)
    if kind == "failure":
        return build_failure_prompt_packet(state, verify_command=verify_command)
    if kind == "handoff":
        return build_handoff_prompt_packet(state, goal=goal, verify_command=verify_command)
    if kind == "review":
        return build_review_prompt_packet(state, goal=goal, verify_command=verify_command)
    return {"error": "[FAIL] Unknown prompt packet kind: {0}".format(kind)}


def build_research_prompt_packet(state, name=None, goal="", verify_command=""):
    slug, record = load_research(state, name)
    if record is None:
        return {"error": "[FAIL] Research not found. Start one with: research start QUESTION"}
    sources = record.get("sources") or []
    claims = record.get("claims") or []
    questions = record.get("open_questions") or []
    decision = record.get("decision") or ""
    lines = [
        "Research to implementation prompt packet: {0}".format(slug),
        "Question: {0}".format(record.get("question", "")),
        "Status: {0}".format(record.get("status", "active")),
        "Sources: {0}; claims: {1}; open questions: {2}".format(len(sources), len(claims), len(questions)),
    ]
    if goal:
        lines.append("Implementation goal: {0}".format(goal))
    if decision:
        lines.append("Decision: {0}".format(decision))
    if claims:
        lines.append("Key claims:")
        for claim in claims[-5:]:
            source = " source={0}".format(claim.get("source_id")) if claim.get("source_id") else ""
            lines.append("- {0}: {1}{2}".format(claim.get("id"), claim.get("claim"), source))
            lines.append("  evidence: {0}".format(claim.get("evidence", "")))
    if questions:
        lines.append("Open questions:")
        for item in questions[-5:]:
            lines.append("- {0}: {1}".format(item.get("id"), item.get("question")))
    lines.extend([
        "",
        "Instructions:",
        "- Treat this research as material for direction, not proof of completion.",
        "- If a decision exists, implement the smallest next step consistent with it.",
        "- If open questions block implementation, answer those first and update the research record.",
        "- Convert implementation work into a plan, campaign, or outcome loop before claiming done.",
    ])
    if verify_command:
        lines.append("- Suggested verifier: {0}".format(verify_command))
    lines.append("Guardrail: {0}".format(PROMPT_PACKET_GUARDRAIL))
    return {
        "kind": "research",
        "selected_kind": "research",
        "title": "Research to implementation prompt packet",
        "source": {"type": "research", "id": slug},
        "context": {
            "question": record.get("question", ""),
            "status": record.get("status", "active"),
            "decision": decision,
            "sources": sources[-5:],
            "claims": claims[-5:],
            "open_questions": questions[-5:],
            "goal": goal,
            "verify_command": verify_command,
        },
        "next_prompt": "\n".join(lines),
        "guardrail": PROMPT_PACKET_GUARDRAIL,
    }


def build_analysis_prompt_packet(state, goal="", verify_command=""):
    plan_context = active_plan_packet_context(state)
    recent = prompt_recent_evidence(state, limit=3)
    lines = [
        "Analysis prompt packet",
        "Goal: {0}".format(goal or (plan_context or {}).get("goal") or "infer from current project context"),
    ]
    lines.extend(prompt_plan_lines(plan_context))
    if recent:
        lines.append("Recent evidence:")
        for item in recent:
            exit_text = "" if item.get("exit_code") is None else " exit {0}".format(item.get("exit_code"))
            lines.append("- {0}: {1}{2}".format(item.get("verdict"), item.get("label"), exit_text))
    lines.extend([
        "",
        "Instructions:",
        "- Read the smallest useful project context before editing.",
        "- Identify likely files, constraints, hidden risks, and the first reversible step.",
        "- Produce or update a plan with checkable success criteria.",
        "- Do not implement until the first step and verifier are explicit.",
    ])
    if verify_command:
        lines.append("- Candidate verifier: {0}".format(verify_command))
    lines.append("Guardrail: {0}".format(PROMPT_PACKET_GUARDRAIL))
    return {
        "kind": "analysis",
        "selected_kind": "analysis",
        "title": "Analysis prompt packet",
        "source": {"type": "workflow_state", "id": (plan_context or {}).get("slug")},
        "context": {
            "goal": goal,
            "active_plan": plan_context,
            "recent_evidence": recent,
            "verify_command": verify_command,
        },
        "next_prompt": "\n".join(lines),
        "guardrail": PROMPT_PACKET_GUARDRAIL,
    }


def build_failure_prompt_packet(state, verify_command=""):
    index, record = latest_failed_verification(state)
    reflection_index, reflection = latest_failure_reflection(state)
    lines = ["Failure recovery prompt packet"]
    context = {
        "failed_verification_index": index,
        "failed_verification": record,
        "failure_reflection_index": reflection_index,
        "failure_reflection": reflection,
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
    campaign_slug, campaign_record = load_campaign(state, None)
    research_slug, research_record = load_research(state, None)
    report = build_work_report(state, since="start", recent=5, cursor="handoff-prompt", peek=True, mark=False)
    lines = [
        "Handoff prompt packet",
        "Goal: {0}".format(goal or (plan_context or {}).get("goal") or "continue current Mythify work"),
    ]
    lines.extend(prompt_plan_lines(plan_context))
    if campaign_record:
        lines.append("Active campaign: {0}".format(campaign_slug))
        lines.append("Campaign next action: {0}".format(campaign_next_action(campaign_record)))
    if research_record:
        lines.append("Active research: {0}".format(research_slug))
        lines.append("Research question: {0}".format(research_record.get("question", "")))
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
        "- Continue the current step or campaign phase, then verify before claiming completion.",
        "- Surface any failed checks or warnings in chat.",
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
            "active_campaign": {"id": campaign_slug, "next_action": campaign_next_action(campaign_record)} if campaign_record else None,
            "active_research": {"id": research_slug, "question": research_record.get("question", "")} if research_record else None,
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
        "- Review changed files and relevant surrounding code.",
        "- Lead with actionable findings, with file and line references when possible.",
        "- Separate verified issues, warnings, open questions, and test gaps.",
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
    if get_active_campaign_slug(state):
        return "campaign"
    if get_active_research_slug(state):
        return "research"
    if get_active_slug(state):
        return "handoff"
    return "analysis"


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
        active_campaign_slug, active_campaign = None, None
        active_research_slug, active_research = None, None
        latest_index, latest = None, None
    else:
        active_plan_slug = get_active_slug(state)
        active_plan = load_plan(state, active_plan_slug) if active_plan_slug else None
        active_outcome_slug, active_outcome = load_outcome(state)
        active_campaign_slug, active_campaign = load_campaign(state)
        active_research_slug, active_research = load_research(state)
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
    # visible in status and background views but must not be a routing target.
    if active_outcome and active_outcome.get("status") == "active":
        outcome_view = {
            "id": active_outcome_slug,
            "goal": active_outcome.get("goal", ""),
            "status": active_outcome.get("status", ""),
            "iteration_count": active_outcome.get("iteration_count", 0),
            "max_iterations": active_outcome.get("max_iterations", 0),
        }
    campaign_view = None
    if active_campaign:
        done, total = campaign_progress(active_campaign)
        current_task = current_campaign_task(active_campaign)
        campaign_view = {
            "id": active_campaign_slug,
            "goal": active_campaign.get("goal", ""),
            "status": active_campaign.get("status", ""),
            "phase": (current_task or {}).get("phase", ""),
            "progress": {"completed": done, "total": total},
        }
    research_view = None
    if active_research:
        research_view = {
            "id": active_research_slug,
            "question": active_research.get("question", ""),
            "status": active_research.get("status", ""),
            "claim_count": len(active_research.get("claims") or []),
            "source_count": len(active_research.get("sources") or []),
        }
    root = artifact_project_root(state)
    godplans_view = godplans_summary(root)
    godaudits_view = godaudits_summary(root)
    return {
        "active_plan": plan_view,
        "active_outcome": outcome_view,
        "active_campaign": campaign_view,
        "active_research": research_view,
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
    packet = WORKFLOW_ROUTE_PROMPTS.get(route, "next")
    if route == "failure":
        return "mythify prompt failure"
    if route == "campaign":
        if state_view.get("active_campaign"):
            return "mythify campaign prompt"
        return (
            "mythify campaign start {0} --success {1}"
        ).format(quoted_task, shlex.quote("done criteria are verified"))
    if route == "outcome":
        if state_view.get("active_outcome"):
            return "mythify outcome status"
        return (
            "mythify outcome start {0} --success {1} --verify {2}"
        ).format(quoted_task, shlex.quote("DEFINE SUCCESS"), shlex.quote("DEFINE VERIFIER"))
    if route == "research":
        if state_view.get("active_research"):
            return "mythify prompt research"
        return "mythify research start {0}".format(quoted_task)
    if route == "review":
        if god_artifact_has_open_tasks(state_view.get("godaudits_audit")):
            return "mythify plan import --source godaudits"
        return "mythify prompt review --goal {0}".format(quoted_task)
    if route == "handoff":
        return "mythify prompt handoff --goal {0}".format(quoted_task)
    if route == "plan":
        if god_artifact_has_open_tasks(state_view.get("godplans_plan")):
            return "mythify plan import --source godplans"
        return "mythify plan create {0} --horizon {1}".format(
            quoted_task,
            route_plan_horizon(),
        )
    if route == "prompt":
        return "mythify prompt {0}".format(packet)
    return "Answer directly in the initiating chat; run verify run if an executable completion check exists."


def route_state_writes(route, state_view):
    if route == "failure":
        return [
            "record reflection after diagnosing the red check",
            "record verify run after the recovery attempt",
            "update the active step with evidence when fixed",
        ]
    if route == "campaign":
        if state_view.get("active_campaign"):
            return [
                "campaign advance after the host completes the current task with evidence",
                "campaign learn when the next task should improve",
            ]
        return ["campaign start when the host accepts the route"]
    if route == "outcome":
        if state_view.get("active_outcome"):
            return ["outcome check after each bounded attempt"]
        return ["outcome start with explicit success criteria and verifier"]
    if route == "research":
        if state_view.get("active_research"):
            return ["research add-source", "research add-claim", "research close"]
        return ["research start before implementation"]
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
    if route == "prompt":
        return []
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
        "active_campaign",
        "active_research",
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


def select_workflow_route(task, state_view, classification):
    text = " ".join(str(task or "").lower().split())
    latest = state_view.get("latest_executed_verification")
    if latest and latest.get("verified") is False:
        return (
            "failure",
            "The latest executed verification is red, so recover that failure before advancing unrelated work.",
        )
    if route_has(text, ROUTE_FULL_SEND_TERMS):
        return (
            "campaign",
            "The prompt uses full-send language, so route to a durable campaign loop with evidence-gated advancement.",
        )
    if state_view.get("active_campaign") and route_has(text, ROUTE_RESUME_TERMS):
        return (
            "campaign",
            "An active campaign exists and the prompt asks to continue.",
        )
    if route_has(text, ROUTE_PROMPT_TERMS):
        return (
            "prompt",
            "The prompt asks for steering material rather than immediate execution.",
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
        return (
            "research",
            "The task depends on external, uncertain, or source-backed information.",
        )
    if classification.get("task_type") == "review" or route_has(text, ROUTE_REVIEW_TERMS):
        return (
            "review",
            "The task asks for audit, review, evaluation, or issue finding.",
        )
    if state_view.get("active_research") and route_has(text, ROUTE_RESUME_TERMS):
        return (
            "research",
            "An active research record exists and the prompt asks to continue.",
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
    execution_adapter = (
        classification.get("model_policy", {})
        .get("model_router", {})
        .get("execution_topology", {})
        .get("native_adapter", {})
    )
    state_writes = route_state_writes(route, state_view)
    return {
        "kind": "workflow_route",
        "route": route,
        "reason": reason,
        "input": str(task or ""),
        "classification": classification,
        "state": state_view,
        "next_command": route_command_for(route, task, state_view),
        "prompt_packet": {
            "kind": packet_kind,
            "command": "mythify prompt {0}".format(packet_kind),
        },
        "execution_adapter": execution_adapter,
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
        "state_writes": state_writes,
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
    adapter = payload.get("execution_adapter") or {}
    if adapter.get("recommended") is True:
        lines.append(
            "Execution adapter: {0}; start={1}; status={2}; results={3}; evidence={4}".format(
                adapter.get("engine", "claude-ultracode"),
                adapter.get("start_tool", "fanout_start"),
                adapter.get("status_tool", "fanout_status"),
                adapter.get("results_tool", "fanout_results"),
                adapter.get("result_evidence_status", "material_not_verification"),
            )
        )
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
    classification = classify_task_text(args.task)
    classification["model_policy"] = build_model_policy(
        classification,
        args,
        read_host_model_state(state),
    )
    if args.triage != "never":
        classification["model_triage_run"] = run_model_triage(args.task, classification, args)
    payload = build_workflow_route(args.task, state, classification)
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(format_workflow_route(payload))
    return 0
