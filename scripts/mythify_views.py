"""Read-only dashboard and progress surfaces for the Mythify CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from mythify_io import read_json, read_jsonl, read_jsonl_since, write_json_atomic
from mythify_outcomes import (
    get_active_outcome_slug,
    list_outcomes,
    load_outcome,
    outcome_iterations_path,
)
from mythify_views_status import (
    build_evidence_harness_view,
    build_fanout_timeline_view,
    build_phase_view,
    build_release_readiness_view,
    cmd_harness,
    cmd_phase,
    cmd_readiness,
    cmd_timeline,
    configure_status_views,
    format_evidence_harness_view,
    format_fanout_timeline_view,
    format_phase_view,
    format_release_readiness_view,
    git_status_summary,
)

WORKSPACE_DIR_NAME = ".mythify"
REPORT_SINCE_MODES = ("last", "start")
REPORT_FORMATS = ("chat", "json")
DEFAULT_REPORT_RECENT = 8
DEFAULT_REPORT_ATTENTION = 5


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_views dependencies are not configured")


get_active_slug = _missing_dependency
load_plan = _missing_dependency
plan_progress = _missing_dependency
next_pending_step = _missing_dependency
load_memory = _missing_dependency
load_lessons = _missing_dependency
global_lessons_dir = _missing_dependency
list_plan_slugs = _missing_dependency
format_step_line = _missing_dependency
timestamp_sort_key = _missing_dependency
timestamp_after = _missing_dependency
now_iso = _missing_dependency
slugify = _missing_dependency


def fail(message):
    print(message, file=sys.stderr)


def configure_views(
    *,
    get_active_slug_func=None,
    load_plan_func=None,
    plan_progress_func=None,
    next_pending_step_func=None,
    load_memory_func=None,
    load_lessons_func=None,
    global_lessons_dir_func=None,
    list_plan_slugs_func=None,
    format_step_line_func=None,
    timestamp_sort_key_func=None,
    timestamp_after_func=None,
    now_iso_func=None,
    slugify_func=None,
    fail_func=None,
):
    global get_active_slug, load_plan, plan_progress, next_pending_step
    global load_memory, load_lessons, global_lessons_dir, list_plan_slugs
    global format_step_line, timestamp_sort_key, timestamp_after, now_iso, slugify
    global fail
    if get_active_slug_func is not None:
        get_active_slug = get_active_slug_func
    if load_plan_func is not None:
        load_plan = load_plan_func
    if plan_progress_func is not None:
        plan_progress = plan_progress_func
    if next_pending_step_func is not None:
        next_pending_step = next_pending_step_func
    if load_memory_func is not None:
        load_memory = load_memory_func
    if load_lessons_func is not None:
        load_lessons = load_lessons_func
    if global_lessons_dir_func is not None:
        global_lessons_dir = global_lessons_dir_func
    if list_plan_slugs_func is not None:
        list_plan_slugs = list_plan_slugs_func
    if format_step_line_func is not None:
        format_step_line = format_step_line_func
    if timestamp_sort_key_func is not None:
        timestamp_sort_key = timestamp_sort_key_func
    if timestamp_after_func is not None:
        timestamp_after = timestamp_after_func
    if now_iso_func is not None:
        now_iso = now_iso_func
    if slugify_func is not None:
        slugify = slugify_func
    if fail_func is not None:
        fail = fail_func
    configure_status_views(
        build_dashboard=build_dashboard,
        build_background_view=build_background_view,
        count_statuses=count_statuses,
        compact_label=compact_label,
        list_fanout_summaries=list_fanout_summaries,
        _contains_any=_contains_any,
    )


def _contains_any(text, needles):
    lower = str(text or "").lower()
    return any(needle in lower for needle in needles)


def current_in_progress_step(plan):
    for step in plan.get("steps", []):
        if step.get("status") == "in_progress":
            return step
    return None


def recent_records(records, limit):
    if limit <= 0:
        return []
    return records[-limit:]


def build_dashboard(state, recent=3):
    active = get_active_slug(state)
    active_plan = None
    if active:
        plan = load_plan(state, active)
        if plan is not None:
            done, total = plan_progress(plan)
            active_plan = {
                "slug": active,
                "goal": plan.get("goal", ""),
                "completed_steps": done,
                "total_steps": total,
                "current_step": current_in_progress_step(plan),
                "next_pending_step": next_pending_step(plan),
                "steps": plan.get("steps", []),
            }
    active_outcome_slug = get_active_outcome_slug(state)
    active_outcome = None
    if active_outcome_slug:
        slug, goal = load_outcome(state, active_outcome_slug)
        if goal is not None:
            iterations = read_jsonl(outcome_iterations_path(state, slug))
            active_outcome = {
                "slug": slug,
                "goal": goal.get("goal", ""),
                "status": goal.get("status", "active"),
                "iteration_count": goal.get("iteration_count", 0),
                "max_iterations": goal.get("max_iterations", 1),
                "last_iteration": iterations[-1] if iterations else None,
            }
    memory = load_memory(state)
    project_lessons = load_lessons(state / "lessons", "project")
    global_lessons = load_lessons(global_lessons_dir(), "global")
    verifications = read_jsonl(state / "verifications.jsonl")
    executed = [record for record in verifications if record.get("kind") == "executed"]
    reflections = read_jsonl(state / "reflections.jsonl")
    return {
        "state_dir": str(state),
        "active_plan": active_plan,
        "active_outcome": active_outcome,
        "counts": {
            "memory": len(memory["entries"]),
            "project_lessons": len(project_lessons),
            "global_lessons": len(global_lessons),
            "verifications": len(verifications),
            "reflections": len(reflections),
        },
        "verification_summary": {
            "executed": len(executed),
            "executed_passed": sum(1 for record in executed if record.get("verified") is True),
            "executed_failed": sum(1 for record in executed if record.get("verified") is False),
            "attested": sum(1 for record in verifications if record.get("kind") == "attested"),
            "recent": recent_records(verifications, recent),
        },
        "reflection_summary": {
            "total": len(reflections),
            "recent": recent_records(reflections, recent),
        },
    }


def format_dashboard(dashboard):
    lines = ["[OK] Workflow dashboard: {0}".format(dashboard["state_dir"])]
    plan = dashboard.get("active_plan")
    if plan:
        lines.append(
            "Active plan: {0} ({1}/{2} completed)".format(
                plan["slug"], plan["completed_steps"], plan["total_steps"]
            )
        )
        lines.append("Goal: {0}".format(plan.get("goal", "")))
        current = plan.get("current_step")
        if current:
            lines.append("Current step: {0}".format(format_step_line(current, "").strip()))
        next_step = plan.get("next_pending_step")
        if next_step:
            lines.append(
                "Next pending: {0}. {1} (criteria: {2})".format(
                    next_step.get("id"),
                    next_step.get("title"),
                    next_step.get("success_criteria") or "none",
                )
            )
        elif not current:
            lines.append("Next pending: none")
    else:
        lines.append("Active plan: none")
    outcome = dashboard.get("active_outcome")
    if outcome:
        lines.append(
            "Active outcome: {0} ({1}, {2}/{3} iterations)".format(
                outcome["slug"],
                outcome["status"],
                outcome["iteration_count"],
                outcome["max_iterations"],
            )
        )
    else:
        lines.append("Active outcome: none")
    counts = dashboard["counts"]
    lines.append(
        "Counts: memory {0}, lessons {1} project + {2} global, verifications {3}, reflections {4}".format(
            counts["memory"],
            counts["project_lessons"],
            counts["global_lessons"],
            counts["verifications"],
            counts["reflections"],
        )
    )
    verification = dashboard["verification_summary"]
    lines.append(
        "Evidence: {0} executed ({1} passed, {2} failed), {3} attested".format(
            verification["executed"],
            verification["executed_passed"],
            verification["executed_failed"],
            verification["attested"],
        )
    )
    if verification["recent"]:
        lines.append("Recent verification:")
        for record in verification["recent"]:
            if record.get("kind") == "executed":
                verdict = "passed" if record.get("verified") is True else "failed"
                label = record.get("claim") or record.get("command") or "executed check"
                lines.append(
                    "  - {0}: {1} (exit {2})".format(
                        verdict, label, record.get("exit_code")
                    )
                )
            else:
                lines.append(
                    "  - attested: {0}".format(record.get("claim") or "claim")
                )
    reflections = dashboard["reflection_summary"]
    if reflections["recent"]:
        lines.append("Recent reflection:")
        for record in reflections["recent"]:
            lines.append(
                "  - {0}: {1}; next {2}".format(
                    record.get("outcome", "unknown"),
                    record.get("action", ""),
                    record.get("next", ""),
                )
            )
    return "\n".join(lines)


def cmd_dashboard(args, state):
    dashboard = build_dashboard(state, args.recent)
    if args.json_output:
        print(json.dumps(dashboard, indent=2))
    else:
        print(format_dashboard(dashboard))
    return 0


VERIFICATION_HISTORY_ICONS = {
    "passed": "[x]",
    "failed": "[!]",
    "attested": "[~]",
    "unknown": "[ ]",
}


def verification_verdict(record):
    if record.get("kind") == "attested":
        return "attested"
    if record.get("kind") == "executed" and record.get("verified") is True:
        return "passed"
    if record.get("kind") == "executed" and record.get("verified") is False:
        return "failed"
    return "unknown"


def summarize_verification_record(record, index):
    kind = record.get("kind", "unknown")
    verdict = verification_verdict(record)
    summary = {
        "index": index,
        "kind": kind,
        "verdict": verdict,
        "timestamp": record.get("timestamp", ""),
        "claim": record.get("claim"),
        "verified": record.get("verified"),
        "plan": record.get("plan"),
        "step_id": record.get("step_id"),
        "step_title": record.get("step_title"),
        "step_status": record.get("step_status"),
    }
    if kind == "executed":
        summary.update(
            {
                "command": record.get("command", ""),
                "exit_code": record.get("exit_code"),
                "duration_seconds": record.get("duration_seconds", 0),
                "stdout_tail_bytes": len(record.get("stdout_tail", "") or ""),
                "stderr_tail_bytes": len(record.get("stderr_tail", "") or ""),
            }
        )
    elif kind == "attested":
        summary.update(
            {
                "evidence": record.get("evidence", ""),
            }
        )
    return summary


def build_verification_history_view(state, recent=10):
    records = read_jsonl(state / "verifications.jsonl")
    rows = [
        summarize_verification_record(record, index + 1)
        for index, record in enumerate(records)
    ]
    executed = [row for row in rows if row["kind"] == "executed"]
    counts = {
        "total": len(rows),
        "executed": len(executed),
        "executed_passed": sum(1 for row in executed if row["verdict"] == "passed"),
        "executed_failed": sum(1 for row in executed if row["verdict"] == "failed"),
        "attested": sum(1 for row in rows if row["kind"] == "attested"),
        "unknown": sum(1 for row in rows if row["verdict"] == "unknown"),
    }
    if recent <= 0:
        recent_rows = []
    else:
        recent_rows = list(reversed(rows[-recent:]))
    return {
        "state_dir": str(state),
        "records": recent_rows,
        "counts": counts,
        "guardrail": (
            "history displays recorded evidence only; it does not rerun checks "
            "or upgrade attested claims"
        ),
    }


def verification_label(row):
    return compact_label(
        row.get("claim") or row.get("command") or row.get("evidence"),
        "verification",
    )


def format_verification_history_row(row):
    icon = VERIFICATION_HISTORY_ICONS.get(row.get("verdict"), "[ ]")
    label = verification_label(row)
    prefix = "  {0} {1} #{2} {3}: {4}".format(
        icon,
        row.get("timestamp") or "unknown-time",
        row.get("index"),
        row.get("verdict"),
        label,
    )
    details = []
    if row.get("kind") == "executed":
        details.append("exit {0}".format(row.get("exit_code")))
        details.append("{0}s".format(row.get("duration_seconds", 0)))
        if row.get("stdout_tail_bytes"):
            details.append("stdout {0} bytes".format(row.get("stdout_tail_bytes")))
        if row.get("stderr_tail_bytes"):
            details.append("stderr {0} bytes".format(row.get("stderr_tail_bytes")))
    elif row.get("kind") == "attested":
        details.append("self-reported")
    if row.get("plan"):
        step = row.get("step_id")
        if step is not None:
            details.append("plan {0} step {1}".format(row.get("plan"), step))
        else:
            details.append("plan {0}".format(row.get("plan")))
    if details:
        prefix += " ({0})".format("; ".join(details))
    return prefix


def format_verification_history_view(view):
    lines = ["[OK] Verification history: {0}".format(view["state_dir"])]
    counts = view["counts"]
    lines.append(
        "Evidence: {0} executed ({1} passed, {2} failed), {3} attested, {4} total".format(
            counts["executed"],
            counts["executed_passed"],
            counts["executed_failed"],
            counts["attested"],
            counts["total"],
        )
    )
    if view["records"]:
        lines.append("Recent verification:")
        for row in view["records"]:
            lines.append(format_verification_history_row(row))
    else:
        lines.append("No verification records found.")
    lines.append("Guardrail: {0}.".format(view["guardrail"]))
    return "\n".join(lines)


def cmd_history(args, state):
    view = build_verification_history_view(state, args.recent)
    if args.json_output:
        print(json.dumps(view, indent=2))
    else:
        print(format_verification_history_view(view))
    return 0


def reports_dir(state):
    return state / "reports"


def report_cursor_name(name):
    return slugify(name or "default") or "default"


def report_cursor_path(state, cursor):
    return reports_dir(state) / (report_cursor_name(cursor) + ".json")


def report_event_sort_key(event):
    return (
        timestamp_sort_key(event.get("timestamp", "")),
        event.get("order", 0),
        event.get("key", ""),
    )


def compact_report_detail(text):
    value = str(text or "").strip()
    return value if len(value) <= 140 else value[:137] + "..."


def report_attention_level(event):
    kind = event.get("kind", "")
    if (
        event.get("verified") is False
        or kind == "step_failed"
        or kind == "reflection_failure"
    ):
        return "issue"
    if kind == "verification_attested":
        return "warning"
    return ""


def build_report_attention_events(events):
    items = []
    for event in events:
        level = report_attention_level(event)
        if not level:
            continue
        items.append(
            {
                "level": level,
                "key": event.get("key", ""),
                "timestamp": event.get("timestamp", ""),
                "kind": event.get("kind", ""),
                "summary": event.get("summary", "Event recorded"),
                "detail": event.get("detail", ""),
                "plan": event.get("plan"),
                "step_id": event.get("step_id"),
                "verified": event.get("verified"),
            }
        )
    return items


def build_report_events(state, log_lower_bound=""):
    events = []
    for slug in list_plan_slugs(state):
        plan = load_plan(state, slug)
        if plan is None:
            continue
        created = plan.get("created") or plan.get("last_updated") or ""
        steps = plan.get("steps", [])
        events.append(
            {
                "key": "plan:{0}:created".format(slug),
                "timestamp": created,
                "order": 10,
                "kind": "plan_created",
                "summary": "Plan created: {0} ({1} steps)".format(slug, len(steps)),
                "detail": plan.get("goal", ""),
                "plan": slug,
                "step_id": None,
                "verified": None,
            }
        )
        for step in steps:
            updated = step.get("updated_at")
            if not updated:
                continue
            status = step.get("status", "pending")
            detail = step.get("result") or step.get("success_criteria") or ""
            events.append(
                {
                    "key": "step:{0}:{1}:{2}:{3}".format(
                        slug, step.get("id"), status, updated
                    ),
                    "timestamp": updated,
                    "order": 20,
                    "kind": "step_" + status,
                    "summary": "Step {0}: {1}. {2}".format(
                        status, step.get("id"), step.get("title")
                    ),
                    "detail": detail,
                    "plan": slug,
                    "step_id": step.get("id"),
                    "verified": None,
                }
            )
    verifications = read_jsonl_since(state / "verifications.jsonl", log_lower_bound)
    for index, record in enumerate(verifications, start=1):
        kind = record.get("kind", "unknown")
        if kind == "executed":
            passed = record.get("verified") is True
            verdict = "passed" if passed else "failed"
            label = record.get("claim") or record.get("command") or "executed check"
            summary = "Verification {0}: {1}".format(verdict, compact_report_detail(label))
            detail = "exit {0}".format(record.get("exit_code"))
            verified = passed
        elif kind == "attested":
            label = record.get("claim") or "claim"
            summary = "Verification attested: {0}".format(compact_report_detail(label))
            detail = "self-reported, not machine-checked"
            verified = None
        else:
            summary = "Verification recorded"
            detail = ""
            verified = None
        events.append(
            {
                "key": "verification:{0}:{1}".format(index, record.get("timestamp", "")),
                "timestamp": record.get("timestamp", ""),
                "order": 30,
                "kind": "verification_" + verification_verdict(record),
                "summary": summary,
                "detail": detail,
                "plan": record.get("plan"),
                "step_id": record.get("step_id"),
                "verified": verified,
            }
        )
    reflections = read_jsonl_since(state / "reflections.jsonl", log_lower_bound)
    for index, record in enumerate(reflections, start=1):
        summary = "Reflection {0}: {1}".format(
            record.get("outcome", "unknown"),
            compact_report_detail(record.get("action", "action")),
        )
        events.append(
            {
                "key": "reflection:{0}:{1}".format(index, record.get("timestamp", "")),
                "timestamp": record.get("timestamp", ""),
                "order": 40,
                "kind": "reflection_" + str(record.get("outcome", "unknown")),
                "summary": summary,
                "detail": "next: {0}".format(record.get("next", "")),
                "plan": None,
                "step_id": None,
                "verified": None,
            }
        )
    return sorted(events, key=report_event_sort_key)


def events_after_marker(events, marker):
    last_event = marker.get("last_event") if isinstance(marker, dict) else None
    if not isinstance(last_event, dict):
        return events
    last_key = last_event.get("key")
    if last_key:
        for index, event in enumerate(events):
            if event.get("key") == last_key:
                return events[index + 1:]
    last_timestamp = last_event.get("timestamp") or ""
    if last_timestamp:
        return [
            event for event in events
            if timestamp_after(event.get("timestamp", ""), last_timestamp)
        ]
    return events


def build_work_report(
    state,
    since="last",
    recent=DEFAULT_REPORT_RECENT,
    cursor="default",
    peek=False,
    mark=False,
):
    if recent < 0:
        fail("[FAIL] Invalid --recent: use 0 or a positive integer.")
        return None
    if mark and peek:
        fail("[FAIL] --mark cannot be combined with --peek.")
        return None
    cursor_name = report_cursor_name(cursor)
    marker_path = report_cursor_path(state, cursor_name)
    marker = read_json(marker_path, {})
    if not isinstance(marker, dict):
        marker = {}
    lower_bound = ""
    if since == "last" and not mark:
        last_event = marker.get("last_event") if isinstance(marker, dict) else None
        if isinstance(last_event, dict):
            lower_bound = last_event.get("timestamp") or ""
    all_events = build_report_events(state, lower_bound)
    if mark:
        candidate_events = []
    elif since == "last":
        candidate_events = events_after_marker(all_events, marker)
    else:
        candidate_events = all_events
    if recent == 0:
        visible_events = []
    else:
        visible_events = candidate_events[-recent:]
    omitted = max(0, len(candidate_events) - len(visible_events))
    attention_candidates = build_report_attention_events(candidate_events)
    attention_events = attention_candidates[-DEFAULT_REPORT_ATTENTION:]
    attention_omitted = max(0, len(attention_candidates) - len(attention_events))
    if mark or not peek:
        last_event = all_events[-1] if all_events else marker.get("last_event")
        write_json_atomic(
            marker_path,
            {
                "cursor": cursor_name,
                "updated_at": now_iso(),
                "last_event": last_event,
            },
        )
    return {
        "state_dir": str(state),
        "cursor": cursor_name,
        "since": since,
        "format": "chat",
        "peek": peek,
        "mark": mark,
        "events": visible_events,
        "new_event_count": len(candidate_events),
        "shown_event_count": len(visible_events),
        "omitted_new_events": omitted,
        "attention_events": attention_events,
        "attention_event_count": len(attention_candidates),
        "omitted_attention_events": attention_omitted,
        "cursor_updated": not peek,
        "last_event": all_events[-1] if all_events else None,
        "guardrail": (
            "report summarizes durable Mythify state only; it does not rerun "
            "checks or prove work beyond recorded evidence"
        ),
    }


def format_work_report(view):
    lines = ["[OK] Live work report: {0}".format(view["state_dir"])]
    if view.get("mark"):
        lines.append(
            "Scope: mark cursor {0}, {1} new events ({2} shown, {3} omitted)".format(
                view["cursor"],
                view["new_event_count"],
                view["shown_event_count"],
                view["omitted_new_events"],
            )
        )
    else:
        lines.append(
            "Scope: since {0}, cursor {1}, {2} new events ({3} shown, {4} omitted)".format(
                view["since"],
                view["cursor"],
                view["new_event_count"],
                view["shown_event_count"],
                view["omitted_new_events"],
            )
        )
    if view.get("attention_event_count", 0):
        lines.append("Attention:")
        for event in view.get("attention_events", []):
            detail = event.get("detail")
            line = "- {0}: {1}".format(
                event.get("level", "notice"),
                event.get("summary", "Event recorded"),
            )
            if detail:
                line += ", {0}".format(compact_report_detail(detail))
            lines.append(line)
        if view.get("omitted_attention_events", 0):
            lines.append(
                "- {0} older attention events omitted".format(
                    view["omitted_attention_events"]
                )
            )
    else:
        lines.append("Attention: none in this report window.")
    if view["events"]:
        for event in view["events"]:
            detail = event.get("detail")
            line = "- {0}".format(event.get("summary", "Event recorded"))
            if detail:
                line += ", {0}".format(compact_report_detail(detail))
            lines.append(line)
    elif view.get("mark"):
        lines.append(
            "Cursor is ready. Future reports with --since last will show only new events."
        )
    else:
        lines.append("No new Mythify events to report.")
    if view.get("mark"):
        lines.append("Cursor marked at latest event: {0}".format(view["cursor"]))
    elif view["cursor_updated"]:
        lines.append("Cursor advanced: {0}".format(view["cursor"]))
    else:
        lines.append("Cursor unchanged: --peek")
    lines.append("Guardrail: {0}.".format(view["guardrail"]))
    return "\n".join(lines)


def cmd_report(args, state):
    if args.mark and args.since is not None:
        fail(
            "[FAIL] --mark cannot be combined with --since. Use --mark to set "
            "a cursor, then run report --since last to read new events."
        )
        return 1
    view = build_work_report(
        state,
        since=args.since or "last",
        recent=args.recent,
        cursor=args.cursor,
        peek=args.peek,
        mark=args.mark,
    )
    if view is None:
        return 1
    if args.report_format == "json":
        payload = dict(view)
        payload["format"] = "json"
        print(json.dumps(payload, indent=2))
    else:
        print(format_work_report(view))
    return 0


BACKGROUND_STATUS_ICONS = {
    "active": "[>]",
    "running": "[>]",
    "pending": "[ ]",
    "completed": "[x]",
    "succeeded": "[x]",
    "failed": "[!]",
    "interrupted": "[~]",
    "stopped": "[~]",
    "empty": "[ ]",
}


def background_recent(items, limit):
    if limit <= 0:
        return []
    return list(reversed(items[-limit:]))


def fanout_root_dir(state):
    return state / "fanout"


def count_statuses(items, statuses):
    counts = {status: 0 for status in statuses}
    for item in items:
        status = item.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def summarize_fanout_job(job):
    tasks = job.get("tasks") if isinstance(job.get("tasks"), list) else []
    counts = count_statuses(
        tasks, ("pending", "running", "completed", "failed", "interrupted")
    )
    if counts.get("pending", 0) or counts.get("running", 0):
        status = "active"
    elif counts.get("failed", 0):
        status = "failed"
    elif counts.get("interrupted", 0):
        status = "interrupted"
    elif tasks:
        status = "completed"
    else:
        status = "empty"
    return {
        "id": job.get("id", ""),
        "status": status,
        "created": job.get("created", ""),
        "last_updated": job.get("last_updated", ""),
        "purpose": job.get("purpose", ""),
        "engine": job.get("engine", ""),
        "model": job.get("model", ""),
        "visibility": job.get("visibility", "summary"),
        "task_counts": counts,
        "task_total": len(tasks),
        "tasks": [
            {
                "id": task.get("id"),
                "title": task.get("title", ""),
                "status": task.get("status", "pending"),
                "role": task.get("role", "worker"),
                "engine": task.get("engine", ""),
                "model": task.get("model", ""),
                "started_at": task.get("started_at", ""),
                "finished_at": task.get("finished_at", ""),
                "duration_seconds": task.get("duration_seconds", 0),
                "error": task.get("error"),
                "output_file": task.get("output_file"),
                "output_bytes": task.get("output_bytes", 0),
            }
            for task in tasks
        ],
    }


def list_fanout_summaries(state):
    root = fanout_root_dir(state)
    if not root.exists():
        return []
    jobs = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or not re.match(r"^fo-\d{14}-[0-9a-f]{4}$", path.name):
            continue
        job = read_json(path / "job.json", None)
        if isinstance(job, dict):
            summary = summarize_fanout_job(job)
            if not summary["id"]:
                summary["id"] = path.name
            jobs.append(summary)
    return sorted(jobs, key=lambda item: (item.get("created") or "", item.get("id") or ""))


def summarize_outcome(state, slug, goal):
    iterations = read_jsonl(outcome_iterations_path(state, slug))
    last_iteration = iterations[-1] if iterations else None
    return {
        "id": slug,
        "goal": goal.get("goal", ""),
        "status": goal.get("status", "active"),
        "iteration_count": goal.get("iteration_count", 0),
        "max_iterations": goal.get("max_iterations", 1),
        "visibility": goal.get("visibility", "summary"),
        "created": goal.get("created", ""),
        "updated": goal.get("updated", ""),
        "last_verified": goal.get("last_verified"),
        "last_iteration": last_iteration,
        "next_action": last_iteration.get("next_action") if last_iteration else (
            "make a bounded attempt, then run outcome check"
        ),
    }


def list_outcome_summaries(state):
    items = []
    for slug, goal in list_outcomes(state):
        items.append(summarize_outcome(state, slug, goal))
    return sorted(items, key=lambda item: (item.get("updated") or item.get("created") or "", item.get("id") or ""))


def build_background_view(state, recent=5):
    outcomes = list_outcome_summaries(state)
    fanout_jobs = list_fanout_summaries(state)
    active_outcome_slug = get_active_outcome_slug(state)
    outcome_counts = count_statuses(
        outcomes, ("active", "succeeded", "failed", "stopped")
    )
    fanout_counts = count_statuses(
        fanout_jobs, ("active", "completed", "failed", "interrupted", "empty")
    )
    task_counts = {
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "interrupted": 0,
    }
    for job in fanout_jobs:
        for status, count in job.get("task_counts", {}).items():
            task_counts[status] = task_counts.get(status, 0) + count
    active_outcome = None
    for outcome in outcomes:
        if outcome.get("id") == active_outcome_slug:
            active_outcome = outcome
            break
    return {
        "state_dir": str(state),
        "active_outcome": active_outcome,
        "outcomes": background_recent(outcomes, recent),
        "fanout_jobs": background_recent(fanout_jobs, recent),
        "counts": {
            "outcomes": {"total": len(outcomes), **outcome_counts},
            "fanout_jobs": {"total": len(fanout_jobs), **fanout_counts},
            "fanout_tasks": task_counts,
        },
    }


def compact_label(text, fallback):
    value = str(text or "").strip()
    if not value:
        return fallback
    return value if len(value) <= 80 else value[:77] + "..."


def format_background_view(view):
    lines = ["[OK] Background tasks: {0}".format(view["state_dir"])]
    counts = view["counts"]
    outcomes = counts["outcomes"]
    lines.append(
        "Outcomes: {0} total; {1} active, {2} succeeded, {3} failed, {4} stopped".format(
            outcomes["total"],
            outcomes.get("active", 0),
            outcomes.get("succeeded", 0),
            outcomes.get("failed", 0),
            outcomes.get("stopped", 0),
        )
    )
    active_outcome = view.get("active_outcome")
    if active_outcome:
        lines.append(
            "Active outcome: {0} ({1}, {2}/{3} iterations)".format(
                active_outcome["id"],
                active_outcome["status"],
                active_outcome["iteration_count"],
                active_outcome["max_iterations"],
            )
        )
    else:
        lines.append("Active outcome: none")
    if view["outcomes"]:
        lines.append("Recent outcomes:")
        for outcome in view["outcomes"]:
            icon = BACKGROUND_STATUS_ICONS.get(outcome["status"], "[ ]")
            lines.append(
                "  {0} {1}: {2} ({3}, {4}/{5} iterations, last verified={6})".format(
                    icon,
                    outcome["id"],
                    compact_label(outcome["goal"], "outcome"),
                    outcome["status"],
                    outcome["iteration_count"],
                    outcome["max_iterations"],
                    outcome["last_verified"],
                )
            )
            if outcome.get("next_action"):
                lines.append("      next: {0}".format(outcome["next_action"]))
    fanout = counts["fanout_jobs"]
    tasks = counts["fanout_tasks"]
    lines.append(
        "Fanout jobs: {0} total; {1} active, {2} completed, {3} failed, {4} interrupted".format(
            fanout["total"],
            fanout.get("active", 0),
            fanout.get("completed", 0),
            fanout.get("failed", 0),
            fanout.get("interrupted", 0),
        )
    )
    lines.append(
        "Fanout tasks: {0} running, {1} pending, {2} completed, {3} failed, {4} interrupted".format(
            tasks.get("running", 0),
            tasks.get("pending", 0),
            tasks.get("completed", 0),
            tasks.get("failed", 0),
            tasks.get("interrupted", 0),
        )
    )
    if view["fanout_jobs"]:
        lines.append("Recent fanout jobs:")
        for job in view["fanout_jobs"]:
            icon = BACKGROUND_STATUS_ICONS.get(job["status"], "[ ]")
            task_counts = job["task_counts"]
            lines.append(
                "  {0} {1}: {2} ({3}; {4} tasks, {5} completed, {6} failed, {7} running, {8} pending)".format(
                    icon,
                    job["id"],
                    compact_label(job["purpose"], "fanout job"),
                    job["status"],
                    job["task_total"],
                    task_counts.get("completed", 0),
                    task_counts.get("failed", 0),
                    task_counts.get("running", 0),
                    task_counts.get("pending", 0),
                )
            )
            lines.append(
                "      visibility: {0}; engine: {1}; created: {2}".format(
                    job["visibility"] or "summary",
                    job["engine"] or "unknown",
                    job["created"] or "unknown",
                )
            )
            for task in job["tasks"]:
                task_icon = BACKGROUND_STATUS_ICONS.get(task["status"], "[ ]")
                detail = "      {0} {1}. {2} ({3})".format(
                    task_icon,
                    task["id"],
                    compact_label(task["title"], "task"),
                    task["status"],
                )
                if task.get("error"):
                    detail += ": {0}".format(compact_label(task["error"], "error"))
                lines.append(detail)
    if not view["outcomes"] and not view["fanout_jobs"]:
        lines.append("No background tasks found.")
    return "\n".join(lines)


def cmd_background(args, state):
    view = build_background_view(state, args.recent)
    if args.json_output:
        print(json.dumps(view, indent=2))
    else:
        print(format_background_view(view))
    return 0


def summarize_outcome_progress(state, slug, goal):
    iterations = read_jsonl(outcome_iterations_path(state, slug))
    last_iteration = iterations[-1] if iterations else None
    iteration_count = int(goal.get("iteration_count", 0) or 0)
    max_iterations = int(goal.get("max_iterations", 1) or 1)
    remaining = max(0, max_iterations - iteration_count)
    last_check = None
    if last_iteration:
        verify = last_iteration.get("verify") or {}
        metric = last_iteration.get("metric") or {}
        last_check = {
            "iteration": last_iteration.get("iteration"),
            "timestamp": last_iteration.get("timestamp", ""),
            "verified": last_iteration.get("verified"),
            "status_after": last_iteration.get("status_after", ""),
            "notes": last_iteration.get("notes", ""),
            "verify_exit_code": verify.get("exit_code"),
            "verify_duration_seconds": verify.get("duration_seconds", 0),
            "verify_verified": verify.get("verified"),
            "metric_exit_code": metric.get("exit_code") if metric else None,
            "metric_score": metric.get("score") if metric else None,
            "metric_verified": metric.get("verified") if metric else None,
        }
    return {
        "id": slug,
        "goal": goal.get("goal", ""),
        "success_criteria": goal.get("success_criteria", ""),
        "status": goal.get("status", "active"),
        "iteration_count": iteration_count,
        "max_iterations": max_iterations,
        "iterations_remaining": remaining,
        "progress_percent": round((iteration_count / max_iterations) * 100, 1)
        if max_iterations
        else 0,
        "visibility": goal.get("visibility", "summary"),
        "created": goal.get("created", ""),
        "updated": goal.get("updated", ""),
        "last_verified": goal.get("last_verified"),
        "last_check": last_check,
        "next_action": (
            last_iteration.get("next_action")
            if last_iteration
            else "make a bounded attempt, then run outcome check"
        ),
        "verify_command": goal.get("verify_command", ""),
        "metric_command": goal.get("metric_command", ""),
        "best_metric_score": goal.get("best_metric_score"),
        "allowed_paths": goal.get("allowed_paths") or [],
        "stop_reason": goal.get("stop_reason"),
    }


def list_outcome_progress_rows(state):
    rows = [
        summarize_outcome_progress(state, slug, goal)
        for slug, goal in list_outcomes(state)
    ]
    return sorted(
        rows,
        key=lambda item: (
            item.get("updated") or item.get("created") or "",
            item.get("id") or "",
        ),
    )


def build_outcome_progress_view(state, recent=5):
    rows = list_outcome_progress_rows(state)
    active_slug = get_active_outcome_slug(state)
    counts = count_statuses(rows, ("active", "succeeded", "failed", "stopped"))
    return {
        "state_dir": str(state),
        "active_outcome": next(
            (row for row in rows if row.get("id") == active_slug),
            None,
        ),
        "outcomes": background_recent(rows, recent),
        "counts": {"total": len(rows), **counts},
        "guardrail": (
            "progress displays recorded outcome verifier results only; it does "
            "not run checks, make attempts, stop loops, or treat notes as verification"
        ),
    }


def format_outcome_progress_row(row):
    icon = BACKGROUND_STATUS_ICONS.get(row.get("status"), "[ ]")
    lines = [
        "  {0} {1}: {2} ({3}, {4}/{5} iterations, {6} remaining)".format(
            icon,
            row.get("id"),
            compact_label(row.get("goal"), "outcome"),
            row.get("status"),
            row.get("iteration_count"),
            row.get("max_iterations"),
            row.get("iterations_remaining"),
        )
    ]
    last = row.get("last_check")
    if last:
        lines.append(
            "      verifier: iteration {0}, exit {1}, verified={2}, at {3}".format(
                last.get("iteration"),
                last.get("verify_exit_code"),
                last.get("verify_verified"),
                last.get("timestamp") or "unknown-time",
            )
        )
        if last.get("metric_exit_code") is not None:
            metric_line = "      metric: exit {0}".format(
                last.get("metric_exit_code")
            )
            if last.get("metric_score") is not None:
                metric_line += ", score {0}".format(last.get("metric_score"))
            lines.append(metric_line)
    else:
        lines.append("      verifier: no recorded iterations yet")
    if row.get("next_action"):
        lines.append("      next: {0}".format(row.get("next_action")))
    return lines


def format_outcome_progress_view(view):
    lines = ["[OK] Outcome progress: {0}".format(view["state_dir"])]
    counts = view["counts"]
    lines.append(
        "Outcomes: {0} total; {1} active, {2} succeeded, {3} failed, {4} stopped".format(
            counts["total"],
            counts.get("active", 0),
            counts.get("succeeded", 0),
            counts.get("failed", 0),
            counts.get("stopped", 0),
        )
    )
    active = view.get("active_outcome")
    if active:
        lines.append(
            "Active outcome: {0} ({1}, {2}/{3} iterations, {4} remaining)".format(
                active.get("id"),
                active.get("status"),
                active.get("iteration_count"),
                active.get("max_iterations"),
                active.get("iterations_remaining"),
            )
        )
    else:
        lines.append("Active outcome: none")
    if view["outcomes"]:
        lines.append("Recent outcomes:")
        for row in view["outcomes"]:
            lines.extend(format_outcome_progress_row(row))
    else:
        lines.append("No outcome loops found.")
    lines.append("Guardrail: {0}.".format(view["guardrail"]))
    return "\n".join(lines)


def cmd_progress(args, state):
    view = build_outcome_progress_view(state, args.recent)
    if args.json_output:
        print(json.dumps(view, indent=2))
    else:
        print(format_outcome_progress_view(view))
    return 0
