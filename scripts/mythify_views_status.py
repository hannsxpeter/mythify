"""Release readiness, fanout timeline, and phase views for Mythify."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from mythify_io import read_jsonl

WORKSPACE_DIR_NAME = ".mythify"


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_views_status dependencies are not configured")


build_dashboard = _missing_dependency
build_background_view = _missing_dependency
count_statuses = _missing_dependency
compact_label = _missing_dependency
list_fanout_summaries = _missing_dependency
_contains_any = _missing_dependency


def configure_status_views(**deps):
    globals().update(deps)


RELEASE_READINESS_GATES = (
    {
        "id": "python_tests",
        "label": "Python test suite",
        "required": True,
        "sources": ["tests/"],
        "match_any": [
            "python3 -m unittest discover -s tests",
            "Python suite passes",
        ],
    },
    {
        "id": "node_mcp_tests",
        "label": "Node MCP suite",
        "required": True,
        "sources": ["mcp-server/test/"],
        "match_any": [
            "npm test --prefix mcp-server",
            "Node MCP suite passes",
        ],
    },
    {
        "id": "surface_manifest",
        "label": "Surface manifest check",
        "required": True,
        "sources": [
            "protocol/surface-manifest.json",
            "mcp-server/protocol/surface-manifest.json",
            "scripts/check_surface_manifest.mjs",
        ],
        "match_any": [
            "node scripts/check_surface_manifest.mjs",
            "surface manifest",
        ],
    },
    {
        "id": "classification_rules_manifest",
        "label": "Runtime manifest mirror check",
        "required": True,
        "sources": [
            "protocol/classification-rules.json",
            "mcp-server/protocol/classification-rules.json",
            "protocol/operation-registry.json",
            "mcp-server/protocol/operation-registry.json",
            "scripts/check_classification_rules_manifest.mjs",
        ],
        "match_any": [
            "node scripts/check_classification_rules_manifest.mjs",
            "classification rules manifest",
        ],
    },
    {
        "id": "registry_docs",
        "label": "Generated registry docs check",
        "required": True,
        "sources": ["scripts/build_registry_docs.mjs", "docs/adapter-candidates.md"],
        "match_any": [
            "node scripts/build_registry_docs.mjs --check",
            "registry docs",
            "generated docs",
        ],
    },
    {
        "id": "protocol_check",
        "label": "Protocol variants check",
        "required": True,
        "sources": ["protocol/PROTOCOL.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"],
        "match_any": [
            "python3 scripts/mythify.py protocol check",
            "protocol check",
        ],
    },
    {
        "id": "variant_idempotence",
        "label": "Generated variants idempotence",
        "required": True,
        "sources": ["scripts/build_variants.py", "AGENTS.md", "CLAUDE.md", ".cursorrules"],
        "match_any": [
            "scripts/build_variants.py",
            "generated variants",
            "variant idempotence",
        ],
    },
    {
        "id": "whitespace",
        "label": "Whitespace check",
        "required": True,
        "sources": ["git diff --check"],
        "match_any": [
            "git diff --check",
            "whitespace",
        ],
    },
    {
        "id": "forbidden_dash_scan",
        "label": "Forbidden dash scan",
        "required": True,
        "sources": ["AGENTS.md", "docs/design.md"],
        "match_any": [
            "forbidden dash",
            "dash scan",
        ],
    },
    {
        "id": "emoji_scan",
        "label": "Emoji scan",
        "required": True,
        "sources": ["AGENTS.md", "docs/design.md"],
        "match_any": [
            "emoji scan",
            "emoji-like",
        ],
    },
)


RELEASE_READINESS_ICONS = {
    "passed": "[x]",
    "failed": "[!]",
    "missing": "[ ]",
    "unknown": "[~]",
    "clean": "[x]",
    "dirty": "[!]",
    "present": "[x]",
}


def project_root_for_state(state):
    return state.parent if state.name == WORKSPACE_DIR_NAME else Path.cwd()


def verification_search_text(record):
    return "\n".join(
        str(record.get(key) or "")
        for key in ("claim", "command", "stdout_tail", "stderr_tail")
    ).lower()


def latest_matching_verification(records, gate):
    needles = [item.lower() for item in gate["match_any"]]
    matches = [
        record
        for record in records
        if record.get("kind") == "executed"
        and any(needle in verification_search_text(record) for needle in needles)
    ]
    return matches[-1] if matches else None


def summarize_release_gate(gate, records):
    record = latest_matching_verification(records, gate)
    status = "missing"
    if record is not None:
        status = "passed" if record.get("verified") is True else "failed"
    return {
        "id": gate["id"],
        "label": gate["label"],
        "required": gate["required"],
        "sources": list(gate["sources"]),
        "status": status,
        "latest_record": None
        if record is None
        else {
            "timestamp": record.get("timestamp", ""),
            "claim": record.get("claim"),
            "command": record.get("command", ""),
            "exit_code": record.get("exit_code"),
            "verified": record.get("verified"),
            "plan": record.get("plan"),
            "step_id": record.get("step_id"),
        },
    }


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


def roadmap_summary(root):
    path = root / "roadmap.md"
    if not path.is_file():
        return {
            "status": "unknown",
            "path": str(path),
            "active_now": "",
            "detail": "roadmap.md not found",
        }
    text = path.read_text(encoding="utf-8")
    active_now = ""
    match = re.search(r"(?ms)^## Active Now\n\n(.*?)(?:\n## |\Z)", text)
    if match:
        for line in match.group(1).splitlines():
            stripped = line.strip()
            if stripped.startswith("- ["):
                active_now = stripped
                break
    return {
        "status": "present" if active_now else "unknown",
        "path": str(path),
        "active_now": active_now,
        "detail": "active slice found" if active_now else "no active slice found",
    }


def release_readiness_status(gates, git_state):
    failed = sum(1 for gate in gates if gate["status"] == "failed")
    missing = sum(1 for gate in gates if gate["status"] == "missing")
    if failed or git_state.get("status") == "dirty":
        return "blocked"
    if missing:
        return "needs_evidence"
    if git_state.get("status") == "unknown":
        return "needs_review"
    return "ready_for_release_review"


def build_release_readiness_view(state):
    records = read_jsonl(state / "verifications.jsonl")
    gates = [
        summarize_release_gate(gate, records)
        for gate in RELEASE_READINESS_GATES
    ]
    root = project_root_for_state(state)
    git_state = git_status_summary(root)
    roadmap = roadmap_summary(root)
    counts = count_statuses(gates, ("passed", "failed", "missing", "unknown"))
    return {
        "state_dir": str(state),
        "project_root": str(root),
        "status": release_readiness_status(gates, git_state),
        "gates": gates,
        "counts": {"total": len(gates), **counts},
        "project_state": {
            "git": git_state,
            "roadmap": roadmap,
        },
        "guardrail": (
            "readiness summarizes recorded evidence and project state only; it "
            "does not rerun gates or declare a release safe"
        ),
    }


def format_release_gate(row):
    icon = RELEASE_READINESS_ICONS.get(row["status"], "[ ]")
    line = "  {0} {1}: {2}".format(icon, row["label"], row["status"])
    record = row.get("latest_record")
    if record:
        line += " (exit {0}, {1})".format(
            record.get("exit_code"),
            record.get("timestamp") or "unknown-time",
        )
    else:
        line += " (no recorded executed verifier)"
    line += "; sources: {0}".format(", ".join(row["sources"]))
    return line


def format_release_readiness_view(view):
    lines = ["[OK] Release readiness: {0}".format(view["state_dir"])]
    counts = view["counts"]
    lines.append("Readiness: {0}".format(view["status"]))
    lines.append(
        "Recorded gates: {0} total; {1} passed, {2} failed, {3} missing".format(
            counts["total"],
            counts.get("passed", 0),
            counts.get("failed", 0),
            counts.get("missing", 0),
        )
    )
    lines.append("Gates:")
    for gate in view["gates"]:
        lines.append(format_release_gate(gate))
    git_state = view["project_state"]["git"]
    git_icon = RELEASE_READINESS_ICONS.get(git_state.get("status"), "[~]")
    lines.append(
        "Project git: {0} {1}; branch={2}; {3}".format(
            git_icon,
            git_state.get("status", "unknown"),
            git_state.get("branch") or "unknown",
            compact_label(git_state.get("detail"), "no detail"),
        )
    )
    roadmap = view["project_state"]["roadmap"]
    roadmap_icon = RELEASE_READINESS_ICONS.get(roadmap.get("status"), "[~]")
    lines.append(
        "Roadmap: {0} {1}; {2}".format(
            roadmap_icon,
            roadmap.get("status", "unknown"),
            compact_label(roadmap.get("active_now"), roadmap.get("detail")),
        )
    )
    lines.append("Guardrail: {0}.".format(view["guardrail"]))
    return "\n".join(lines)


def cmd_readiness(args, state):
    view = build_release_readiness_view(state)
    if args.json_output:
        print(json.dumps(view, indent=2))
    else:
        print(format_release_readiness_view(view))
    return 0


TIMELINE_EVENT_ICONS = {
    "job_created": "[ ]",
    "task_started": "[>]",
    "task_pending": "[ ]",
    "task_finished": "[x]",
    "task_failed": "[!]",
    "task_interrupted": "[~]",
}


def selected_recent_fanout_jobs(fanout_jobs, recent):
    if recent <= 0:
        return []
    return list(reversed(fanout_jobs[-recent:]))


def timeline_event_time(job, task, event):
    if event == "task_started":
        return task.get("started_at") or job.get("created", "")
    if event in ("task_finished", "task_failed", "task_interrupted"):
        return task.get("finished_at") or job.get("last_updated", "")
    return job.get("created", "")


def add_timeline_event(events, job, task, event):
    status = task.get("status", "pending") if task else job.get("status", "unknown")
    item = {
        "time": timeline_event_time(job, task or {}, event),
        "event": event,
        "job_id": job.get("id", ""),
        "job_purpose": job.get("purpose", ""),
        "task_id": task.get("id") if task else None,
        "task_title": task.get("title", "") if task else "",
        "status": status,
        "engine": (task.get("engine") if task else None) or job.get("engine", ""),
        "model": (task.get("model") if task else None) or job.get("model", ""),
        "duration_seconds": task.get("duration_seconds", 0) if task else 0,
        "error": task.get("error") if task else None,
        "output_file": task.get("output_file") if task else None,
        "output_bytes": task.get("output_bytes", 0) if task else 0,
    }
    events.append(item)


def build_fanout_timeline_events(job):
    events = []
    events.append(
        {
            "time": job.get("created", ""),
            "event": "job_created",
            "job_id": job.get("id", ""),
            "job_purpose": job.get("purpose", ""),
            "task_id": None,
            "task_title": "",
            "status": job.get("status", "unknown"),
            "engine": job.get("engine", ""),
            "model": job.get("model", ""),
            "duration_seconds": 0,
            "error": None,
            "output_file": None,
            "output_bytes": 0,
        }
    )
    for task in job.get("tasks", []):
        status = task.get("status", "pending")
        if status == "pending" and not task.get("started_at"):
            add_timeline_event(events, job, task, "task_pending")
            continue
        add_timeline_event(events, job, task, "task_started")
        if status == "failed":
            add_timeline_event(events, job, task, "task_failed")
        elif status == "interrupted":
            add_timeline_event(events, job, task, "task_interrupted")
        elif status == "completed":
            add_timeline_event(events, job, task, "task_finished")
    return events


def sort_timeline_events(events):
    return sorted(
        events,
        key=lambda item: (
            item.get("time") or "9999-12-31T23:59:59Z",
            item.get("job_id") or "",
            item.get("task_id") or 0,
            item.get("event") or "",
        ),
    )


def build_fanout_timeline_view(state, recent=5):
    fanout_jobs = list_fanout_summaries(state)
    selected_jobs = selected_recent_fanout_jobs(fanout_jobs, recent)
    selected_ids = {job.get("id") for job in selected_jobs}
    events = []
    for job in fanout_jobs:
        if job.get("id") in selected_ids:
            events.extend(build_fanout_timeline_events(job))
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
    job_counts = count_statuses(
        fanout_jobs, ("active", "completed", "failed", "interrupted", "empty")
    )
    return {
        "state_dir": str(state),
        "jobs": selected_jobs,
        "events": sort_timeline_events(events),
        "counts": {
            "fanout_jobs": {"total": len(fanout_jobs), **job_counts},
            "fanout_tasks": task_counts,
            "timeline_events": len(events),
        },
        "guardrail": (
            "timeline summarizes durable fanout state only; worker output is "
            "material, not verification evidence"
        ),
    }


def format_timeline_event(event):
    icon = TIMELINE_EVENT_ICONS.get(event.get("event"), "[ ]")
    stamp = event.get("time") or "unknown-time"
    job_id = event.get("job_id") or "unknown-job"
    task_id = event.get("task_id")
    if event.get("event") == "job_created":
        return "  {0} {1} {2}: job created ({3})".format(
            icon,
            stamp,
            job_id,
            compact_label(event.get("job_purpose"), "fanout job"),
        )
    title = compact_label(event.get("task_title"), "task")
    prefix = "  {0} {1} {2} task {3}: {4}".format(
        icon,
        stamp,
        job_id,
        task_id,
        title,
    )
    detail = " ({0}; engine={1}".format(
        event.get("status", "unknown"),
        event.get("engine") or "unknown",
    )
    if event.get("model"):
        detail += "; model={0}".format(event.get("model"))
    if event.get("duration_seconds"):
        detail += "; duration={0}s".format(event.get("duration_seconds"))
    if event.get("output_bytes"):
        detail += "; output={0} bytes".format(event.get("output_bytes"))
    detail += ")"
    if event.get("error"):
        detail += ": {0}".format(compact_label(event.get("error"), "error"))
    return prefix + detail


def format_fanout_timeline_view(view):
    lines = ["[OK] Fanout timeline: {0}".format(view["state_dir"])]
    jobs = view["counts"]["fanout_jobs"]
    tasks = view["counts"]["fanout_tasks"]
    lines.append(
        "Fanout jobs: {0} total; {1} active, {2} completed, {3} failed, {4} interrupted".format(
            jobs["total"],
            jobs.get("active", 0),
            jobs.get("completed", 0),
            jobs.get("failed", 0),
            jobs.get("interrupted", 0),
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
    if view["events"]:
        lines.append("Timeline events:")
        for event in view["events"]:
            lines.append(format_timeline_event(event))
    else:
        lines.append("No fanout timeline events found.")
    lines.append("Guardrail: {0}.".format(view["guardrail"]))
    return "\n".join(lines)


def cmd_timeline(args, state):
    view = build_fanout_timeline_view(state, args.recent)
    if args.json_output:
        print(json.dumps(view, indent=2))
    else:
        print(format_fanout_timeline_view(view))
    return 0


PHASE_CONFIG = (
    {
        "id": "understand",
        "label": "Understand",
        "keywords": (
            "understand",
            "map",
            "inspect",
            "research",
            "audit",
            "classify",
            "discover",
            "probe",
            "investigate",
            "analyze",
            "orient",
        ),
    },
    {
        "id": "design",
        "label": "Design",
        "keywords": (
            "design",
            "plan",
            "spec",
            "contract",
            "architecture",
            "outline",
            "docs design",
        ),
    },
    {
        "id": "build",
        "label": "Build",
        "keywords": (
            "implement",
            "build",
            "add",
            "create",
            "update",
            "write",
            "edit",
            "refactor",
            "wire",
        ),
    },
    {
        "id": "judge",
        "label": "Judge",
        "keywords": (
            "judge",
            "review",
            "evaluate",
            "assess",
            "reflect",
            "decide",
        ),
    },
    {
        "id": "verify",
        "label": "Verify",
        "keywords": (
            "verify",
            "test",
            "check",
            "gate",
            "lint",
            "suite",
        ),
    },
)

PHASE_STATUS_ICONS = {
    "empty": "[ ]",
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
    "failed": "[!]",
    "skipped": "[~]",
}


def phase_id_for_step(step):
    title = step.get("title", "")
    for phase in PHASE_CONFIG:
        if _contains_any(title, phase["keywords"]):
            return phase["id"]
    criteria = step.get("success_criteria", "")
    for phase in PHASE_CONFIG:
        if _contains_any(criteria, phase["keywords"]):
            return phase["id"]
    return "build"


def summarize_phase_step(step):
    return {
        "id": step.get("id"),
        "title": step.get("title", ""),
        "status": step.get("status", "pending"),
        "success_criteria": step.get("success_criteria", ""),
        "result": step.get("result"),
    }


def phase_step_counts(steps):
    return {
        "total": len(steps),
        "pending": sum(1 for step in steps if step.get("status") == "pending"),
        "in_progress": sum(1 for step in steps if step.get("status") == "in_progress"),
        "completed": sum(1 for step in steps if step.get("status") == "completed"),
        "failed": sum(1 for step in steps if step.get("status") == "failed"),
        "skipped": sum(1 for step in steps if step.get("status") == "skipped"),
    }


def phase_status(steps):
    if not steps:
        return "empty"
    statuses = [step.get("status", "pending") for step in steps]
    if "in_progress" in statuses:
        return "in_progress"
    if "failed" in statuses:
        return "failed"
    if all(status == "completed" for status in statuses):
        return "completed"
    if all(status == "skipped" for status in statuses):
        return "skipped"
    return "pending"


def phase_next_action(steps):
    for status in ("in_progress", "pending"):
        for step in steps:
            if step.get("status") == status:
                return "continue step {0}: {1}".format(
                    step.get("id"),
                    step.get("title", ""),
                )
    return None


def build_phase_evidence(phase_id, dashboard, background):
    plan = dashboard.get("active_plan")
    counts = dashboard["counts"]
    verification = dashboard["verification_summary"]
    reflections = dashboard["reflection_summary"]
    evidence = []
    if phase_id == "understand":
        if plan:
            evidence.append("active plan goal: {0}".format(plan.get("goal", "")))
        else:
            evidence.append("active plan: none")
        evidence.append(
            "memory {0}, lessons {1} project + {2} global".format(
                counts["memory"],
                counts["project_lessons"],
                counts["global_lessons"],
            )
        )
    elif phase_id == "design":
        if plan:
            evidence.append(
                "plan progress {0}/{1} completed".format(
                    plan["completed_steps"],
                    plan["total_steps"],
                )
            )
            next_step = plan.get("next_pending_step")
            if next_step:
                evidence.append(
                    "next pending step {0}: {1}".format(
                        next_step.get("id"),
                        next_step.get("title", ""),
                    )
                )
        else:
            evidence.append("no active plan")
    elif phase_id == "build":
        outcomes = background["counts"]["outcomes"]
        tasks = background["counts"]["fanout_tasks"]
        evidence.append(
            "outcomes {0} total, {1} active".format(
                outcomes["total"],
                outcomes.get("active", 0),
            )
        )
        evidence.append(
            "fanout tasks {0} running, {1} pending, {2} completed".format(
                tasks.get("running", 0),
                tasks.get("pending", 0),
                tasks.get("completed", 0),
            )
        )
    elif phase_id == "judge":
        evidence.append("reflections {0} total".format(reflections["total"]))
        if reflections["recent"]:
            latest = reflections["recent"][-1]
            evidence.append(
                "latest reflection: {0}; next {1}".format(
                    latest.get("outcome", "unknown"),
                    latest.get("next", ""),
                )
            )
    elif phase_id == "verify":
        evidence.append(
            "executed checks {0} total, {1} passed, {2} failed".format(
                verification["executed"],
                verification["executed_passed"],
                verification["executed_failed"],
            )
        )
        evidence.append("attested claims {0}".format(verification["attested"]))
        outcome = dashboard.get("active_outcome")
        if outcome:
            evidence.append(
                "active outcome {0} is {1}".format(
                    outcome["slug"],
                    outcome["status"],
                )
            )
    return evidence


def build_phase_view(state, recent=3):
    dashboard = build_dashboard(state, recent)
    background = build_background_view(state, recent)
    plan = dashboard.get("active_plan")
    step_buckets = {phase["id"]: [] for phase in PHASE_CONFIG}
    if plan:
        for step in plan.get("steps", []):
            step_buckets[phase_id_for_step(step)].append(summarize_phase_step(step))
    phases = []
    for phase in PHASE_CONFIG:
        steps = step_buckets[phase["id"]]
        status = phase_status(steps)
        phases.append(
            {
                "id": phase["id"],
                "label": phase["label"],
                "status": status,
                "steps": steps,
                "step_counts": phase_step_counts(steps),
                "evidence": build_phase_evidence(phase["id"], dashboard, background),
                "next_action": phase_next_action(steps),
            }
        )
    return {
        "state_dir": str(state),
        "active_plan": dashboard.get("active_plan"),
        "active_outcome": dashboard.get("active_outcome"),
        "phases": phases,
        "counts": {
            "memory": dashboard["counts"]["memory"],
            "project_lessons": dashboard["counts"]["project_lessons"],
            "global_lessons": dashboard["counts"]["global_lessons"],
            "verifications": dashboard["counts"]["verifications"],
            "reflections": dashboard["counts"]["reflections"],
            "outcomes": background["counts"]["outcomes"],
            "fanout_jobs": background["counts"]["fanout_jobs"],
            "fanout_tasks": background["counts"]["fanout_tasks"],
        },
        "guardrail": (
            "phase view summarizes durable state only; verification still "
            "requires executed checks"
        ),
    }


def format_phase_view(view):
    lines = ["[OK] Phase view: {0}".format(view["state_dir"])]
    plan = view.get("active_plan")
    if plan:
        lines.append(
            "Active plan: {0} ({1}/{2} completed)".format(
                plan["slug"],
                plan["completed_steps"],
                plan["total_steps"],
            )
        )
        lines.append("Goal: {0}".format(plan.get("goal", "")))
    else:
        lines.append("Active plan: none")
    lines.append("Phases:")
    for phase in view["phases"]:
        counts = phase["step_counts"]
        icon = PHASE_STATUS_ICONS.get(phase["status"], "[ ]")
        lines.append(
            "  {0} {1}: {2}; {3} plan steps ({4} completed, {5} in progress, {6} pending)".format(
                icon,
                phase["label"],
                phase["status"],
                counts["total"],
                counts["completed"],
                counts["in_progress"],
                counts["pending"],
            )
        )
        for item in phase["evidence"]:
            lines.append("      evidence: {0}".format(item))
        for step in phase["steps"]:
            lines.append(
                "      step: {0} {1}. {2}".format(
                    PHASE_STATUS_ICONS.get(step["status"], "[ ]"),
                    step["id"],
                    step["title"],
                )
            )
        if phase.get("next_action"):
            lines.append("      next: {0}".format(phase["next_action"]))
    lines.append("Guardrail: {0}.".format(view["guardrail"]))
    return "\n".join(lines)


def cmd_phase(args, state):
    view = build_phase_view(state, args.recent)
    if args.json_output:
        print(json.dumps(view, indent=2))
    else:
        print(format_phase_view(view))
    return 0
