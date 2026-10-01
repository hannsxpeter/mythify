"""Read-only status, history, and report views for the Mythify CLI.

`status` is the single orientation view: active plan, outcome, and map, the
executed and attested evidence breakdown, recent verification records, and
attention items from the evidence detectors, sorted so issues come before
warnings. Every view reads durable state only; none reruns checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from mythify_evidence_guard import (
    active_legacy_opt_outs,
    ledger_chain_breaks,
    trivial_pass_reason,
)
from mythify_godfiles import godaudits_summary, godplans_summary
from mythify_io import read_json, read_jsonl, read_jsonl_since, write_json_atomic
from mythify_maps import (
    frontier_tickets,
    get_active_map_slug,
    load_map,
    map_is_clear,
    map_next_action,
    open_tickets,
    ungraduated_fog,
)
from mythify_outcomes import get_active_outcome_slug, list_outcome_rows

WORKSPACE_DIR_NAME = ".mythify"
REPORT_SINCE_MODES = ("last", "start")
REPORT_FORMATS = ("chat", "json")
DEFAULT_REPORT_RECENT = 8
DEFAULT_REPORT_ATTENTION = 5
DEFAULT_STATUS_RECENT = 5


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_views dependencies are not configured")


get_active_slug = _missing_dependency
load_plan = _missing_dependency
plan_progress = _missing_dependency
next_pending_step = _missing_dependency
describe_next_pending = _missing_dependency
load_memory = _missing_dependency
load_lessons = _missing_dependency
global_lessons_dir = _missing_dependency
list_plan_slugs = _missing_dependency
format_step_line = _missing_dependency
timestamp_sort_key = _missing_dependency
timestamp_after = _missing_dependency
now_iso = _missing_dependency
slugify = _missing_dependency


def inspect_lineage(_state, _lineage):
    """Preserve direct-import compatibility until the runtime injects lineage IO."""
    return {
        "status": "unknown",
        "parents": [],
        "precedence": [
            "live_code_current_behavior",
            "approved_design_desired_behavior",
            "latest_linked_plan_implementation_order",
            "executed_verification_completion",
        ],
    }


def fail(message):
    print(message, file=sys.stderr)


def configure_views(
    *,
    get_active_slug_func=None,
    load_plan_func=None,
    plan_progress_func=None,
    next_pending_step_func=None,
    describe_next_pending_func=None,
    load_memory_func=None,
    load_lessons_func=None,
    global_lessons_dir_func=None,
    list_plan_slugs_func=None,
    format_step_line_func=None,
    timestamp_sort_key_func=None,
    timestamp_after_func=None,
    now_iso_func=None,
    slugify_func=None,
    inspect_lineage_func=None,
    fail_func=None,
):
    global get_active_slug, load_plan, plan_progress, next_pending_step
    global describe_next_pending
    global load_memory, load_lessons, global_lessons_dir, list_plan_slugs
    global format_step_line, timestamp_sort_key, timestamp_after, now_iso, slugify
    global inspect_lineage
    global fail
    if get_active_slug_func is not None:
        get_active_slug = get_active_slug_func
    if load_plan_func is not None:
        load_plan = load_plan_func
    if plan_progress_func is not None:
        plan_progress = plan_progress_func
    if next_pending_step_func is not None:
        next_pending_step = next_pending_step_func
    if describe_next_pending_func is not None:
        describe_next_pending = describe_next_pending_func
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
    if inspect_lineage_func is not None:
        inspect_lineage = inspect_lineage_func
    if fail_func is not None:
        fail = fail_func


def current_in_progress_step(plan):
    for step in plan.get("steps", []):
        if step.get("status") == "in_progress":
            return step
    return None


def recent_tail(items, limit):
    if limit <= 0:
        return []
    return list(items[-limit:])


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
    # Log events in the same second keep their append order (seq); plan and
    # step events, which carry no seq, keep ordering by key.
    return (
        timestamp_sort_key(event.get("timestamp", "")),
        event.get("order", 0),
        event.get("seq", 0),
        event.get("key", ""),
    )


def record_event_key(prefix, record):
    """A key that names RECORD itself, not its position in a filtered read.

    Executed verifications carry an id; other records (attested claims,
    reflections) are keyed by a digest of their content, which includes the
    chained prev_sha256 when present. Neither depends on which subset of the
    log a report happened to read, so a cursor key never drifts onto another
    record.
    """
    ident = record.get("id")
    if isinstance(ident, str) and ident:
        return "{0}:{1}".format(prefix, ident)
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return "{0}:sha256:{1}".format(prefix, hashlib.sha256(payload.encode("utf-8")).hexdigest())


def unique_event_keys(events):
    """Suffix repeated keys (byte-identical records) with their occurrence."""
    counts = {}
    for event in events:
        key = event.get("key", "")
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            event["key"] = "{0}#{1}".format(key, counts[key])
    return events


def event_instant(event):
    """The event's timestamp as a comparable instant (sort key minus raw text)."""
    return timestamp_sort_key(event.get("timestamp", ""))[:2]


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
    for seq, record in enumerate(verifications):
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
                "key": record_event_key("verification", record),
                "seq": seq,
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
    for seq, record in enumerate(reflections):
        summary = "Reflection {0}: {1}".format(
            record.get("outcome", "unknown"),
            compact_report_detail(record.get("action", "action")),
        )
        events.append(
            {
                "key": record_event_key("reflection", record),
                "seq": seq,
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
    events = sorted(unique_event_keys(events), key=report_event_sort_key)
    for event in events:
        # seq is a read position, good for ordering but not for identity.
        event.pop("seq", None)
    return events


def events_after_marker(events, marker):
    """Events the cursor has not shown yet.

    A cursor stores the latest event plus every event key it had seen at that
    instant (seen_keys). Anything later is new, and anything at the same
    instant is new unless its key was seen, so a record landing in the same
    second as the cursor is neither dropped nor replayed, whatever its sort
    position. Cursors written before seen_keys existed use the legacy rule.
    """
    last_event = marker.get("last_event") if isinstance(marker, dict) else None
    if not isinstance(last_event, dict):
        return events
    seen_keys = marker.get("seen_keys")
    if isinstance(seen_keys, list):
        seen = {str(key) for key in seen_keys}
        last_instant = event_instant(last_event)
        return [
            event for event in events
            if event_instant(event) > last_instant
            or (event_instant(event) == last_instant and event.get("key") not in seen)
        ]
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
        if all_events:
            last_event = all_events[-1]
            last_instant = event_instant(last_event)
            seen_keys = [
                event.get("key", "") for event in all_events
                if event_instant(event) == last_instant
            ]
        else:
            last_event = marker.get("last_event")
            seen_keys = marker.get("seen_keys")
        payload = {
            "cursor": cursor_name,
            "updated_at": now_iso(),
            "last_event": last_event,
        }
        if isinstance(seen_keys, list):
            payload["seen_keys"] = seen_keys
        write_json_atomic(marker_path, payload)
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


def compact_label(text, fallback):
    value = str(text or "").strip()
    if not value:
        return fallback
    return value if len(value) <= 80 else value[:77] + "..."


def project_root_for_state(state):
    return state.parent if state.name == WORKSPACE_DIR_NAME else Path.cwd()


def git_status_summary(root):
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "status", "--short", "--branch"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "unknown",
            "branch": "",
            "clean": None,
            "detail": str(exc),
        }
    output = result.stdout or ""
    if result.returncode != 0:
        return {
            "status": "unknown",
            "branch": "",
            "clean": None,
            "detail": (result.stderr or output or "git status failed").strip(),
        }
    lines = [line for line in output.splitlines() if line.strip()]
    branch = ""
    if lines and lines[0].startswith("## "):
        branch = lines[0][3:].strip()
    dirty_lines = [line for line in lines if not line.startswith("## ")]
    clean = len(dirty_lines) == 0
    return {
        "status": "clean" if clean else "dirty",
        "branch": branch,
        "clean": clean,
        "detail": "working tree clean" if clean else "{0} changed paths".format(len(dirty_lines)),
        "changed_paths": dirty_lines[:20],
    }


def god_artifact_views(root):
    views = {}
    plan = godplans_summary(root)
    if plan.get("present"):
        views["godplans"] = plan
    audit = godaudits_summary(root)
    if audit.get("present"):
        views["godaudits"] = audit
    return views


# ---------------------------------------------------------------------------
# Attention detectors
# ---------------------------------------------------------------------------

# Reminder thresholds. Both signals are computed from durable state that
# already exists; neither adds new tracking. Drift is windowed (recent tail)
# and stale-evidence self-resets the moment an executed verify is recorded, so
# neither grows unbounded with the cumulative verification log.
ATTENTION_DRIFT_MIN_ATTESTED = 2
ATTENTION_STALE_EXECUTED_RECORDS = 8
ATTENTION_LEVEL_ORDER = {"issue": 0, "warning": 1}
STATUS_GUARDRAIL = (
    "status summarizes durable state only; it does not rerun checks, and "
    "delegated or worker output is material until an executed verifier "
    "records evidence"
)


def evidence_record_label(record):
    return compact_label(
        record.get("claim") or record.get("command") or record.get("evidence"),
        "verification",
    )


def attention_item(level, source, summary, detail, timestamp=""):
    return {
        "level": level,
        "source": source,
        "summary": summary,
        "detail": detail,
        "timestamp": timestamp or "",
    }


def evidence_attention_from_drift(records, recent):
    """Warn when the recent evidence window leans on attested claims."""
    window = recent_tail(records, recent)
    executed = sum(1 for record in window if record.get("kind") == "executed")
    attested = sum(1 for record in window if record.get("kind") == "attested")
    if attested >= ATTENTION_DRIFT_MIN_ATTESTED and attested > executed:
        return [attention_item(
            "warning",
            "drift",
            "verification drift: recent evidence leans on attested claims",
            "{0} attested vs {1} executed in the last {2} records; record an executed verify".format(
                attested, executed, len(window)
            ),
            window[-1].get("timestamp", "") if window else "",
        )]
    return []


def evidence_attention_from_stale_executed(records):
    """Warn after a long run of records with no executed verification."""
    if not records:
        return []
    since = 0
    for record in reversed(records):
        if record.get("kind") == "executed":
            break
        since += 1
    if since >= ATTENTION_STALE_EXECUTED_RECORDS:
        return [attention_item(
            "warning",
            "session",
            "long run without an executed verify",
            "{0} records since the last executed check; run a real verifier or summarize".format(since),
            records[-1].get("timestamp", ""),
        )]
    return []


def evidence_attention_from_trivial_passes(records, recent):
    """Flag recent passes whose command cannot fail or that ran zero tests."""
    attention = []
    for record in recent_tail(records, recent):
        reason = trivial_pass_reason(record)
        if reason:
            attention.append(attention_item(
                "warning",
                "verification",
                "trivial pass: {0}".format(evidence_record_label(record)),
                "{0}; the green exit code proves nothing".format(reason),
                record.get("timestamp", ""),
            ))
    return attention


def evidence_attention_from_opt_outs(environ=None):
    """Name every legacy gate opt-out active in this session."""
    environ = os.environ if environ is None else environ
    return [
        attention_item(
            "warning",
            "session",
            "legacy opt-out active: {0}".format(item["name"]),
            item["effect"],
        )
        for item in active_legacy_opt_outs(environ)
    ]


def evidence_attention_from_ledger_chain(state):
    """Flag chained ledger lines whose prev_sha256 no longer matches."""
    try:
        text = (state / "verifications.jsonl").read_text(encoding="utf-8")
    except OSError:
        return []
    breaks = ledger_chain_breaks(text)
    if not breaks:
        return []
    return [attention_item(
        "issue",
        "ledger",
        "verification ledger chain break at record {0}".format(
            ", ".join(str(index) for index in breaks[:5])
        ),
        "a chained record's prev_sha256 does not match the preceding line; the ledger may have been edited",
    )]


def evidence_attention_from_verifications(records, recent):
    attention = []
    for record in recent_tail(records, recent):
        if record.get("kind") == "executed" and record.get("verified") is False:
            attention.append(attention_item(
                "issue",
                "verification",
                "failed verification: {0}".format(evidence_record_label(record)),
                "exit {0}".format(record.get("exit_code")),
                record.get("timestamp", ""),
            ))
        elif record.get("kind") == "attested":
            attention.append(attention_item(
                "warning",
                "verification",
                "attested claim: {0}".format(evidence_record_label(record)),
                "self-reported, not machine-checked",
                record.get("timestamp", ""),
            ))
    return attention


def evidence_attention_from_plan(plan):
    attention = []
    if not plan:
        return attention
    for step in plan.get("steps", []):
        if step.get("status") == "failed":
            attention.append(attention_item(
                "issue",
                "plan",
                "failed step {0}: {1}".format(
                    step.get("id"),
                    compact_label(step.get("title"), "step"),
                ),
                compact_label(step.get("result"), "no result recorded"),
                step.get("updated_at", ""),
            ))
        elif step.get("status") == "completed" and step.get("strict_gate_waived"):
            attention.append(attention_item(
                "warning",
                "plan",
                "step {0} completed under a waived strict gate".format(step.get("id")),
                "MYTHIFY_REQUIRE_VERIFIED_STEP=0 was active; evidence is prose-only",
                step.get("updated_at", ""),
            ))
    return attention


def evidence_attention_from_outcomes(outcomes):
    attention = []
    for outcome in outcomes:
        timestamp = outcome.get("updated", "") or outcome.get("created", "")
        name = outcome.get("id") or "outcome"
        if outcome.get("status") == "failed":
            attention.append(attention_item(
                "issue",
                "outcome",
                "failed outcome: {0}".format(name),
                compact_label(outcome.get("goal"), "outcome"),
                timestamp,
            ))
        if outcome.get("verifier_drift"):
            attention.append(attention_item(
                "warning",
                "outcome",
                "outcome verifier changed mid-loop: {0}".format(name),
                "iterations record different verify commands; the sensor may have been tuned",
                timestamp,
            ))
        if outcome.get("evidence_stale"):
            attention.append(attention_item(
                "issue",
                "outcome",
                "audit recheck failed for outcome: {0}".format(name),
                "the recorded result no longer reproduces; re-verify before trusting it",
                timestamp,
            ))
    return attention


def evidence_attention_from_god_artifacts(god_views):
    attention = []
    audit = god_views.get("godaudits")
    if audit:
        if audit.get("open_critical"):
            attention.append(attention_item(
                "issue",
                "godaudits",
                "{0} open Critical finding(s) in the godaudits audit".format(
                    audit["open_critical"]
                ),
                compact_label(audit.get("detail"), "audit"),
            ))
        if audit.get("counter_drift"):
            attention.append(attention_item(
                "warning",
                "godaudits",
                "godaudits frontmatter counters disagree with checkboxes",
                compact_label(audit.get("path"), "audit"),
            ))
    plan = god_views.get("godplans")
    if plan and plan.get("counter_drift"):
        attention.append(attention_item(
            "warning",
            "godplans",
            "godplans frontmatter counters disagree with checkboxes",
            compact_label(plan.get("path"), "plan"),
        ))
    return attention


def sort_attention(items):
    """Issues before warnings; detector order is kept within a level."""
    return sorted(items, key=lambda item: ATTENTION_LEVEL_ORDER.get(item.get("level"), 2))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def active_plan_open_steps(plan):
    if not plan:
        return []
    return [
        step for step in plan.get("steps", [])
        if step.get("status") in ("pending", "in_progress", "failed")
    ]


def status_next_action(view):
    attention = view.get("attention", [])
    if attention:
        return "resolve attention item: {0}".format(attention[0]["summary"])
    plan = view.get("active_plan")
    if plan:
        current = plan.get("current_step")
        if current:
            return "continue step {0}: {1}".format(
                current.get("id"),
                compact_label(current.get("title"), "step"),
            )
        pending = plan.get("next_pending_step")
        if pending:
            return "start step {0}: {1}".format(
                pending.get("id"),
                compact_label(pending.get("title"), "step"),
            )
    outcome = view.get("active_outcome")
    if outcome and outcome.get("status") == "active":
        return "make a bounded attempt, then run outcome check"
    active_map = view.get("active_map")
    if active_map and active_map.get("status") != "promoted":
        return active_map.get("next_action") or "work the next map ticket"
    god = view.get("god_artifacts") or {}
    if not plan:
        for key in ("godaudits", "godplans"):
            summary = god.get(key)
            if summary and summary.get("next_task_id"):
                return (
                    "import the open {0} tasks: mythify "
                    "plan import --source {0}".format(key)
                )
    if view["evidence"]["executed"] == 0:
        return "run the nearest verify run before claiming completion"
    return "ready for human judgment or release review"


def status_control(view):
    if view.get("attention_total"):
        return "needs_attention"
    if view["evidence"]["executed"] == 0:
        return "needs_evidence"
    if view.get("active_plan") and active_plan_open_steps(view["active_plan"]):
        return "in_progress"
    outcome = view.get("active_outcome")
    if outcome and outcome.get("status") == "active":
        return "in_progress"
    return "controlled"


def build_status_view(state, recent=DEFAULT_STATUS_RECENT):
    active_plan = None
    active_slug = get_active_slug(state)
    plan_record = load_plan(state, active_slug) if active_slug else None
    if plan_record is not None:
        done, total = plan_progress(plan_record)
        active_plan = {
            "id": active_slug,
            "goal": plan_record.get("goal", ""),
            "completed_steps": done,
            "total_steps": total,
            "current_step": current_in_progress_step(plan_record),
            "next_pending_step": next_pending_step(plan_record),
            "next_summary": describe_next_pending(plan_record),
            "steps": plan_record.get("steps", []),
            "lineage": inspect_lineage(state, plan_record.get("lineage")),
        }
    outcomes = list_outcome_rows(state)
    active_outcome_slug = get_active_outcome_slug(state)
    active_outcome = next(
        (outcome for outcome in outcomes if outcome.get("id") == active_outcome_slug),
        None,
    )
    active_map = None
    map_slug = get_active_map_slug(state)
    map_record = load_map(state, map_slug)[1] if map_slug else None
    if map_record is not None:
        active_map = {
            "id": map_slug,
            "destination": map_record.get("destination", ""),
            "status": map_record.get("status", "charting"),
            "open_tickets": len(open_tickets(map_record)),
            "frontier": len(frontier_tickets(map_record)),
            "decisions": len(map_record.get("decisions") or []),
            "fog": len(ungraduated_fog(map_record)),
            "clear": map_is_clear(map_record),
            "next_action": map_next_action(map_record),
        }
    memory = load_memory(state)
    project_lessons = load_lessons(state / "lessons", "project")
    global_lessons = load_lessons(global_lessons_dir(), "global")
    records = read_jsonl(state / "verifications.jsonl")
    reflections = read_jsonl(state / "reflections.jsonl")
    executed = [record for record in records if record.get("kind") == "executed"]
    rows = [
        summarize_verification_record(record, index + 1)
        for index, record in enumerate(records)
    ]
    god_views = god_artifact_views(project_root_for_state(state))
    attention = sort_attention(
        evidence_attention_from_plan(plan_record)
        + evidence_attention_from_verifications(records, recent)
        + evidence_attention_from_trivial_passes(records, recent)
        + evidence_attention_from_outcomes(recent_tail(outcomes, recent))
        + evidence_attention_from_god_artifacts(god_views)
        + evidence_attention_from_drift(records, recent)
        + evidence_attention_from_stale_executed(records)
        + evidence_attention_from_ledger_chain(state)
        + evidence_attention_from_opt_outs()
    )
    shown = attention[:max(recent, 0)]
    view = {
        "state_dir": str(state),
        "status": "unknown",
        "active_plan": active_plan,
        "active_outcome": active_outcome,
        "active_map": active_map,
        "god_artifacts": god_views,
        "counts": {
            "memory": len(memory["entries"]),
            "project_lessons": len(project_lessons),
            "global_lessons": len(global_lessons),
            "verifications": len(records),
            "reflections": len(reflections),
            "outcomes": len(outcomes),
        },
        "evidence": {
            "total": len(records),
            "executed": len(executed),
            "executed_passed": sum(1 for record in executed if record.get("verified") is True),
            "executed_failed": sum(1 for record in executed if record.get("verified") is False),
            "attested": sum(1 for record in records if record.get("kind") == "attested"),
            "recent": list(reversed(recent_tail(rows, recent))),
        },
        "attention": shown,
        "attention_total": len(attention),
        "attention_omitted": len(attention) - len(shown),
        "next_action": "",
        "guardrail": STATUS_GUARDRAIL,
    }
    view["status"] = status_control(view)
    view["next_action"] = status_next_action(view)
    return view


def format_status_view(view):
    lines = ["[OK] Status: {0}".format(view["state_dir"])]
    plan = view.get("active_plan")
    if plan:
        lines.append(
            "Active plan: {0} ({1}/{2} completed)".format(
                plan["id"], plan["completed_steps"], plan["total_steps"]
            )
        )
        lines.append("Goal: {0}".format(plan.get("goal", "")))
        for step in plan.get("steps", []):
            lines.append(format_step_line(step))
        lines.append(plan["next_summary"])
    else:
        lines.append("Active plan: none")
    outcome = view.get("active_outcome")
    if outcome:
        lines.append(
            "Active outcome: {0} ({1}, {2}/{3} iterations)".format(
                outcome["id"],
                outcome["status"],
                outcome["iteration_count"],
                outcome["max_iterations"],
            )
        )
        lines.append("Outcome goal: {0}".format(outcome.get("goal", "")))
    else:
        lines.append("Active outcome: none")
    active_map = view.get("active_map")
    if active_map:
        lines.append(
            "Active map: {0} ({1} open, {2} on the frontier, {3} decided)".format(
                active_map["id"],
                active_map["open_tickets"],
                active_map["frontier"],
                active_map["decisions"],
            )
        )
        lines.append("Destination: {0}".format(active_map.get("destination", "")))
        lines.append("Map next: {0}".format(active_map["next_action"]))
    else:
        lines.append("Active map: none")
    god = view.get("god_artifacts") or {}
    for label, key in (("Godplans plan", "godplans"), ("Godaudits audit", "godaudits")):
        summary = god.get(key)
        if summary:
            lines.append(
                "{0}: {1}; {2}".format(
                    label,
                    summary.get("status", "unknown"),
                    compact_label(summary.get("detail"), "no detail"),
                )
            )
    counts = view["counts"]
    lines.append(
        "Counts: memory {0}, lessons {1} project + {2} global, "
        "verifications {3}, reflections {4}".format(
            counts["memory"],
            counts["project_lessons"],
            counts["global_lessons"],
            counts["verifications"],
            counts["reflections"],
        )
    )
    evidence = view["evidence"]
    lines.append(
        "Evidence: {0} executed ({1} passed, {2} failed), {3} attested".format(
            evidence["executed"],
            evidence["executed_passed"],
            evidence["executed_failed"],
            evidence["attested"],
        )
    )
    if evidence["recent"]:
        lines.append("Recent verification:")
        for row in evidence["recent"]:
            lines.append(format_verification_history_row(row))
    if view["attention"]:
        lines.append("Attention ({0}):".format(view["attention_total"]))
        for item in view["attention"]:
            lines.append("  {0}: {1} ({2})".format(item["level"], item["summary"], item["detail"]))
        if view["attention_omitted"]:
            lines.append(
                "  {0} more omitted; raise --recent to see them".format(view["attention_omitted"])
            )
    elif view["attention_total"]:
        lines.append(
            "Attention ({0}): hidden by --recent 0".format(view["attention_total"])
        )
    else:
        lines.append("Attention: none")
    lines.append("Next: {0}".format(view["next_action"]))
    return "\n".join(lines)


def cmd_status(args, state):
    recent = getattr(args, "recent", DEFAULT_STATUS_RECENT)
    if recent < 0:
        fail("[FAIL] Invalid --recent: use 0 or a positive integer.")
        return 1
    view = build_status_view(state, recent)
    if getattr(args, "json_output", False):
        print(json.dumps(view, indent=2))
    else:
        print(format_status_view(view))
    return 0
