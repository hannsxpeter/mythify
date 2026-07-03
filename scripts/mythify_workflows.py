"""Research and campaign workflow stores for the Mythify CLI."""

import json
import os
import sys
import time

from mythify_io import _write_text_atomic, read_json, write_json_atomic

RESEARCH_CONFIDENCE = ("low", "medium", "high")
RESEARCH_SOURCE_CREDIBILITY = ("unknown", "low", "medium", "high")
CAMPAIGN_TASK_STATUSES = ("pending", "in_progress", "completed", "failed", "skipped")
CAMPAIGN_PHASES = ("understand", "design", "build", "judge", "verify", "reflect")
CAMPAIGN_PHASE_GUIDANCE = {
    "understand": "Read context, restate the task, and identify constraints.",
    "design": "Choose the smallest useful approach and success check.",
    "build": "Make the focused change or artifact.",
    "judge": "Review the result against the task and campaign goal.",
    "verify": "Run the nearest executable check, or record why only attestation is possible.",
    "reflect": "Capture what improved the next task, then advance the frontier.",
}
CAMPAIGN_PROMPT_GUARDRAIL = (
    "Prompt output is steering material for the host agent, not verification evidence. "
    "The host must do the work, run checks when available, and advance the campaign with evidence."
)
EVIDENCE_MESSAGE = (
    "[FAIL] Evidence required: pass a RESULT describing what proves this status."
)


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_workflows dependencies are not configured")


now_iso = _missing_dependency
slugify = _missing_dependency
find_existing_slug_by_name = _missing_dependency
execute_verification = _missing_dependency


def fail(message):
    print(message, file=sys.stderr)


def configure_workflow_stores(
    *,
    now_iso_func=None,
    slugify_func=None,
    fail_func=None,
    find_existing_slug_by_name_func=None,
    execute_verification_func=None,
):
    global now_iso, slugify, fail, find_existing_slug_by_name, execute_verification
    if now_iso_func is not None:
        now_iso = now_iso_func
    if slugify_func is not None:
        slugify = slugify_func
    if fail_func is not None:
        fail = fail_func
    if find_existing_slug_by_name_func is not None:
        find_existing_slug_by_name = find_existing_slug_by_name_func
    if execute_verification_func is not None:
        execute_verification = execute_verification_func


# ---------------------------------------------------------------------------
# Research store
# ---------------------------------------------------------------------------

def research_dir(state):
    return state / "research"


def research_path(state, slug):
    return research_dir(state) / (slug + ".json")


def active_research_path(state):
    return research_dir(state) / "active"


def get_active_research_slug(state):
    path = active_research_path(state)
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    if value and research_path(state, value).exists():
        return value
    return None


def set_active_research_slug(state, slug):
    _write_text_atomic(active_research_path(state), slug + "\n")


def clear_active_research_slug(state, slug=None):
    path = active_research_path(state)
    if not path.exists():
        return
    if slug is not None and get_active_research_slug(state) != slug:
        return
    try:
        path.unlink()
    except OSError:
        pass


def find_research_slug(state, name):
    if name:
        return find_existing_slug_by_name(state, name, research_path)
    return get_active_research_slug(state)


def load_research(state, name=None):
    slug = find_research_slug(state, name)
    if not slug:
        return None, None
    record = read_json(research_path(state, slug), None)
    if not isinstance(record, dict):
        return slug, None
    return slug, record


def save_research(state, slug, record):
    record["updated"] = now_iso()
    write_json_atomic(research_path(state, slug), record)


def list_research_records(state):
    directory = research_dir(state)
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob("*.json")):
        record = read_json(path, None)
        if isinstance(record, dict):
            items.append((path.stem, record))
    return items


def next_item_id(items, prefix):
    numbers = []
    for item in items:
        raw = str(item.get("id", ""))
        if raw.startswith(prefix):
            try:
                numbers.append(int(raw[len(prefix):]))
            except ValueError:
                pass
    return "{0}{1}".format(prefix, max(numbers + [0]) + 1)


def format_research_summary(slug, record):
    sources = record.get("sources") or []
    claims = record.get("claims") or []
    questions = record.get("open_questions") or []
    lines = [
        "[OK] Research {0}: {1}".format(slug, record.get("question", "")),
        "status: {0}".format(record.get("status", "active")),
        "sources: {0}".format(len(sources)),
        "claims: {0}".format(len(claims)),
        "open questions: {0}".format(len(questions)),
    ]
    if sources:
        lines.append("Sources:")
        for source in sources:
            text = "  {0}. {1}".format(source.get("id"), source.get("title"))
            if source.get("url"):
                text += " ({0})".format(source.get("url"))
            text += " credibility={0}".format(source.get("credibility", "unknown"))
            lines.append(text)
            if source.get("note"):
                lines.append("       note: {0}".format(source.get("note")))
    if claims:
        lines.append("Claims:")
        for claim in claims:
            line = "  {0}. {1} confidence={2}".format(
                claim.get("id"),
                claim.get("claim"),
                claim.get("confidence", "medium"),
            )
            if claim.get("source_id"):
                line += " source={0}".format(claim.get("source_id"))
            lines.append(line)
            lines.append("       evidence: {0}".format(claim.get("evidence", "")))
    if questions:
        lines.append("Open questions:")
        for item in questions:
            lines.append("  {0}. {1}".format(item.get("id"), item.get("question")))
    if record.get("decision"):
        lines.append("Decision: {0}".format(record.get("decision")))
    lines.append("Guardrail: research records are material for decisions, not executed verification.")
    return "\n".join(lines)


def cmd_research_start(args, state):
    base = slugify(args.name or args.question) or "research"
    slug = base
    suffix = 2
    while research_path(state, slug).exists():
        slug = "{0}-{1}".format(base[:36], suffix)
        suffix += 1
    stamp = now_iso()
    record = {
        "id": slug,
        "question": args.question,
        "status": "active",
        "sources": [],
        "claims": [],
        "open_questions": [],
        "decision": "",
        "created": stamp,
        "updated": stamp,
    }
    save_research(state, slug, record)
    set_active_research_slug(state, slug)
    if args.json_output:
        print(json.dumps(record, indent=2))
    else:
        print("[OK] Started research: {0}".format(slug))
        print("Question: {0}".format(args.question))
    return 0


def cmd_research_list(args, state):
    items = list_research_records(state)
    active = get_active_research_slug(state)
    if args.json_output:
        print(json.dumps([
            {"id": slug, **record, "active": slug == active}
            for slug, record in items
        ], indent=2))
        return 0
    print("[OK] Research records ({0}):".format(len(items)))
    if not items:
        print("  none")
    for slug, record in items:
        marker = "* " if slug == active else "  "
        print(
            "{0}{1}: {2} sources, {3} claims, {4} open questions, status {5}".format(
                marker,
                slug,
                len(record.get("sources") or []),
                len(record.get("claims") or []),
                len(record.get("open_questions") or []),
                record.get("status", "active"),
            )
        )
    return 0


def cmd_research_add_source(args, state):
    slug, record = load_research(state, args.research)
    if record is None:
        fail("[FAIL] Research not found. Start one with: research start QUESTION")
        return 1
    source = {
        "id": next_item_id(record.get("sources") or [], "S"),
        "title": args.title,
        "url": args.url or "",
        "note": args.note or "",
        "credibility": args.credibility,
        "created": now_iso(),
    }
    record.setdefault("sources", []).append(source)
    save_research(state, slug, record)
    print("[OK] Added source {0} to research {1}".format(source["id"], slug))
    return 0


def cmd_research_add_claim(args, state):
    slug, record = load_research(state, args.research)
    if record is None:
        fail("[FAIL] Research not found. Start one with: research start QUESTION")
        return 1
    source_id = args.source or ""
    if source_id:
        source_ids = {source.get("id") for source in record.get("sources") or []}
        if source_id not in source_ids:
            fail("[FAIL] Source not found in research {0}: {1}".format(slug, source_id))
            return 1
    claim = {
        "id": next_item_id(record.get("claims") or [], "C"),
        "claim": args.claim,
        "evidence": args.evidence,
        "source_id": source_id,
        "confidence": args.confidence,
        "created": now_iso(),
    }
    record.setdefault("claims", []).append(claim)
    save_research(state, slug, record)
    print("[OK] Added claim {0} to research {1}".format(claim["id"], slug))
    return 0


def cmd_research_add_question(args, state):
    slug, record = load_research(state, args.research)
    if record is None:
        fail("[FAIL] Research not found. Start one with: research start QUESTION")
        return 1
    item = {
        "id": next_item_id(record.get("open_questions") or [], "Q"),
        "question": args.question,
        "created": now_iso(),
    }
    record.setdefault("open_questions", []).append(item)
    save_research(state, slug, record)
    print("[OK] Added open question {0} to research {1}".format(item["id"], slug))
    return 0


def cmd_research_summary(args, state):
    slug, record = load_research(state, args.name)
    if record is None:
        fail("[FAIL] Research not found. Start one with: research start QUESTION")
        return 1
    if args.json_output:
        print(json.dumps({"id": slug, **record}, indent=2))
    else:
        print(format_research_summary(slug, record))
    return 0


def cmd_research_close(args, state):
    slug, record = load_research(state, args.name)
    if record is None:
        fail("[FAIL] Research not found. Start one with: research start QUESTION")
        return 1
    record["status"] = "closed"
    record["decision"] = args.decision
    save_research(state, slug, record)
    clear_active_research_slug(state, slug)
    print("[OK] Closed research {0}".format(slug))
    return 0


# ---------------------------------------------------------------------------
# Campaign store
# ---------------------------------------------------------------------------

def campaigns_dir(state):
    return state / "campaigns"


def campaign_path(state, slug):
    return campaigns_dir(state) / (slug + ".json")


def active_campaign_path(state):
    return campaigns_dir(state) / "active"


def get_active_campaign_slug(state):
    path = active_campaign_path(state)
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    if value and campaign_path(state, value).exists():
        return value
    return None


def set_active_campaign_slug(state, slug):
    _write_text_atomic(active_campaign_path(state), slug + "\n")


def clear_active_campaign_slug(state, slug=None):
    path = active_campaign_path(state)
    if not path.exists():
        return
    if slug is not None and get_active_campaign_slug(state) != slug:
        return
    try:
        path.unlink()
    except OSError:
        pass


def find_campaign_slug(state, name):
    if name:
        return find_existing_slug_by_name(state, name, campaign_path)
    return get_active_campaign_slug(state)


def load_campaign(state, name=None):
    slug = find_campaign_slug(state, name)
    if not slug:
        return None, None
    record = read_json(campaign_path(state, slug), None)
    if not isinstance(record, dict):
        return slug, None
    return slug, record


def save_campaign(state, slug, record):
    record["updated"] = now_iso()
    write_json_atomic(campaign_path(state, slug), record)


def list_campaign_records(state):
    directory = campaigns_dir(state)
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob("*.json")):
        record = read_json(path, None)
        if isinstance(record, dict):
            items.append((path.stem, record))
    return items


def generated_campaign_tasks(goal):
    return [
        {
            "title": "Understand the project goal",
            "success_criteria": "Scope, constraints, and done criteria are explicit for {0}".format(goal),
        },
        {
            "title": "Design the smallest task sequence",
            "success_criteria": "A dependency-ordered implementation path exists",
        },
        {
            "title": "Build the first working slice",
            "success_criteria": "The smallest useful slice is implemented or documented",
        },
        {
            "title": "Verify and harden the result",
            "success_criteria": "Executable checks or attested limitations are recorded",
        },
        {
            "title": "Reflect and improve the next cycle",
            "success_criteria": "A lesson or adjustment is captured for future tasks",
        },
    ]


def parse_campaign_tasks(raw_tasks, goal):
    if not raw_tasks:
        parsed = generated_campaign_tasks(goal)
    else:
        try:
            parsed = json.loads(raw_tasks)
        except ValueError:
            fail("[FAIL] Invalid JSON for --tasks: expected an array of strings or task objects.")
            return None
        if not isinstance(parsed, list):
            fail("[FAIL] Invalid --tasks: expected a JSON array.")
            return None
    tasks = []
    for index, item in enumerate(parsed, start=1):
        if isinstance(item, str):
            title = item
            criteria = ""
        elif isinstance(item, dict) and item.get("title"):
            title = str(item["title"])
            criteria = str(item.get("success_criteria") or item.get("criteria") or "")
        else:
            fail("[FAIL] Invalid task {0}: expected string or object with title.".format(index))
            return None
        tasks.append({
            "id": index,
            "title": title,
            "success_criteria": criteria,
            "status": "in_progress" if index == 1 else "pending",
            "phase": CAMPAIGN_PHASES[0] if index == 1 else "pending",
            "result": "",
            "created": now_iso(),
            "updated": now_iso(),
        })
    return tasks


def current_campaign_task(record):
    current_id = record.get("current_task_id")
    for task in record.get("tasks") or []:
        if task.get("id") == current_id:
            return task
    return None


def campaign_progress(record):
    tasks = record.get("tasks") or []
    completed = sum(1 for task in tasks if task.get("status") == "completed")
    return completed, len(tasks)


def campaign_next_pending_task(record):
    for task in record.get("tasks") or []:
        if task.get("status") == "pending":
            return task
    return None


def campaign_set_next_task(record):
    next_task = campaign_next_pending_task(record)
    stamp = now_iso()
    if next_task is None:
        record["current_task_id"] = None
        record["status"] = "completed"
        return None
    next_task["status"] = "in_progress"
    next_task["phase"] = CAMPAIGN_PHASES[0]
    next_task["updated"] = stamp
    record["current_task_id"] = next_task["id"]
    record["status"] = "active"
    return next_task


def campaign_next_action(record):
    if record.get("status") == "completed":
        return "Campaign complete. Review lessons and final verification evidence."
    if record.get("status") == "stopped":
        return "Campaign stopped. Resume by creating a new campaign or updating the existing record manually."
    task = current_campaign_task(record)
    if task is None:
        return "No current task. Add a task or complete the campaign."
    if task.get("status") == "failed":
        return (
            "Task {0} failed: diagnose the failure, record a reflection, then "
            "retry it with campaign task {0} in_progress or skip it.".format(task.get("id"))
        )
    phase = task.get("phase", CAMPAIGN_PHASES[0])
    return "Task {0} {1}: {2}".format(
        task.get("id"),
        phase,
        CAMPAIGN_PHASE_GUIDANCE.get(phase, "Continue the workflow."),
    )


def campaign_recent_learning_lines(record, limit=5):
    lines = []
    for item in (record.get("learnings") or [])[-limit:]:
        lesson = str(item.get("lesson", "")).strip()
        if not lesson:
            continue
        prefix = "task {0}: ".format(item.get("task_id")) if item.get("task_id") else ""
        suffix = " [apply next]" if item.get("apply_next") else ""
        lines.append(prefix + lesson + suffix)
    return lines


def build_campaign_prompt_payload(slug, record):
    done, total = campaign_progress(record)
    task = current_campaign_task(record)
    status = record.get("status", "active")
    verify_command = record.get("verify_command") or ""
    learning_lines = campaign_recent_learning_lines(record)
    current_task = dict(task) if isinstance(task, dict) else None
    phase = ""
    phase_guidance = ""
    next_command = ""

    lines = [
        "Continue Mythify campaign: {0}".format(slug),
        "Goal: {0}".format(record.get("goal", "")),
        "Status: {0}".format(status),
        "Progress: {0}/{1} tasks completed".format(done, total),
    ]
    if record.get("success_criteria"):
        lines.append("Campaign success: {0}".format(record.get("success_criteria")))
    if verify_command:
        lines.append("Campaign verifier: {0}".format(verify_command))

    if status == "completed":
        lines.extend([
            "",
            "No current task remains. Review the final evidence, summarize risks, and archive related state when appropriate.",
        ])
    elif status == "stopped":
        lines.extend([
            "",
            "This campaign is stopped. Do not continue it until the host or user explicitly resumes or creates a new campaign.",
        ])
    elif task is None:
        lines.extend([
            "",
            "No current task is selected. Add a task, set a task in progress, or close the campaign if it is complete.",
        ])
    else:
        if task.get("status") == "failed":
            phase = "failed"
            phase_guidance = (
                "Diagnose the failure, record a reflection, then retry the task "
                "with campaign task {0} in_progress or skip it.".format(task.get("id"))
            )
        else:
            phase = task.get("phase") if task.get("phase") in CAMPAIGN_PHASES else CAMPAIGN_PHASES[0]
            phase_guidance = CAMPAIGN_PHASE_GUIDANCE.get(phase, "Continue the workflow.")
        next_command = (
            'python3 scripts/mythify.py campaign advance {0} --result "<phase evidence>"'.format(slug)
        )
        lines.extend([
            "",
            "Current task {0}: {1}".format(task.get("id"), task.get("title", "")),
            "Task status: {0}".format(task.get("status", "")),
            "Task criteria: {0}".format(task.get("success_criteria") or "not specified"),
            "Phase: {0}".format(phase),
            "Phase guidance: {0}".format(phase_guidance),
        ])
        if learning_lines:
            lines.append("")
            lines.append("Recent learnings:")
            for learning in learning_lines:
                lines.append("- {0}".format(learning))
        lines.extend([
            "",
            "Instructions:",
            "- Work only on this current phase unless the host has already completed it.",
            "- Bring findings, failed checks, and uncertainty into the chat as they happen.",
            "- When this phase reaches verify, run the nearest executable check.",
            "- When the phase is done, advance the durable frontier with: {0}".format(next_command),
        ])

    lines.extend([
        "",
        "Guardrail: {0}".format(CAMPAIGN_PROMPT_GUARDRAIL),
    ])
    return {
        "id": slug,
        "goal": record.get("goal", ""),
        "status": status,
        "progress": {"completed": done, "total": total},
        "success_criteria": record.get("success_criteria", ""),
        "verify_command": verify_command,
        "current_task": current_task,
        "phase": phase,
        "phase_guidance": phase_guidance,
        "recent_learnings": learning_lines,
        "next_action": campaign_next_action(record),
        "next_command": next_command,
        "next_prompt": "\n".join(lines),
        "guardrail": CAMPAIGN_PROMPT_GUARDRAIL,
    }


def format_campaign_prompt_payload(payload):
    return "[OK] Campaign prompt: {0}\n{1}".format(payload.get("id"), payload.get("next_prompt", ""))


def format_campaign_status(slug, record):
    done, total = campaign_progress(record)
    lines = [
        "[OK] Campaign {0}: {1}".format(slug, record.get("goal", "")),
        "status: {0}".format(record.get("status", "active")),
        "progress: {0}/{1} tasks completed".format(done, total),
        "loop: understand, design, build, judge, verify, reflect",
        "next: {0}".format(campaign_next_action(record)),
    ]
    if record.get("success_criteria"):
        lines.append("success: {0}".format(record.get("success_criteria")))
    if record.get("verify_command"):
        lines.append("campaign verifier: {0}".format(record.get("verify_command")))
    if record.get("tasks"):
        lines.append("Tasks:")
        for task in record["tasks"]:
            line = "  {0}. [{1}] {2}".format(
                task.get("id"),
                task.get("status"),
                task.get("title"),
            )
            if task.get("phase") and task.get("status") == "in_progress":
                line += " phase={0}".format(task.get("phase"))
            lines.append(line)
            if task.get("success_criteria"):
                lines.append("       criteria: {0}".format(task.get("success_criteria")))
            if task.get("result"):
                lines.append("       result: {0}".format(task.get("result")))
    learnings = record.get("learnings") or []
    if learnings:
        lines.append("Learnings:")
        for item in learnings[-5:]:
            task_id = item.get("task_id")
            prefix = "  task {0}: ".format(task_id) if task_id else "  "
            lines.append(prefix + item.get("lesson", ""))
    return "\n".join(lines)


def cmd_campaign_start(args, state):
    tasks = parse_campaign_tasks(args.tasks, args.goal)
    if tasks is None:
        return 1
    base = slugify(args.name or args.goal) or "campaign"
    slug = base
    suffix = 2
    while campaign_path(state, slug).exists():
        slug = "{0}-{1}".format(base[:36], suffix)
        suffix += 1
    stamp = now_iso()
    record = {
        "id": slug,
        "goal": args.goal,
        "success_criteria": args.success or "",
        "verify_command": args.verify or "",
        "status": "active" if tasks else "completed",
        "current_task_id": tasks[0]["id"] if tasks else None,
        "loop": list(CAMPAIGN_PHASES),
        "tasks": tasks,
        "events": [],
        "learnings": [],
        "created": stamp,
        "updated": stamp,
    }
    save_campaign(state, slug, record)
    set_active_campaign_slug(state, slug)
    if args.json_output:
        print(json.dumps(record, indent=2))
    else:
        print("[OK] Started campaign: {0} ({1} tasks)".format(slug, len(tasks)))
        print("Next: {0}".format(campaign_next_action(record)))
    return 0


def cmd_campaign_list(args, state):
    items = list_campaign_records(state)
    active = get_active_campaign_slug(state)
    if args.json_output:
        print(json.dumps([
            {"id": slug, **record, "active": slug == active}
            for slug, record in items
        ], indent=2))
        return 0
    print("[OK] Campaigns ({0}):".format(len(items)))
    if not items:
        print("  none")
    for slug, record in items:
        done, total = campaign_progress(record)
        marker = "* " if slug == active else "  "
        print("{0}{1}: {2}/{3} completed, status {4}".format(
            marker,
            slug,
            done,
            total,
            record.get("status", "active"),
        ))
    return 0


def cmd_campaign_status(args, state):
    slug, record = load_campaign(state, args.name)
    if record is None:
        fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
        return 1
    if args.json_output:
        print(json.dumps({"id": slug, **record, "next_action": campaign_next_action(record)}, indent=2))
    else:
        print(format_campaign_status(slug, record))
    return 0


def cmd_campaign_prompt(args, state):
    slug, record = load_campaign(state, args.name)
    if record is None:
        fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
        return 1
    payload = build_campaign_prompt_payload(slug, record)
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(format_campaign_prompt_payload(payload))
    return 0


def cmd_campaign_watch(args, state):
    if args.interval < 0:
        fail("[FAIL] --interval must be zero or greater.")
        return 1
    if args.max_iterations < 0:
        fail("[FAIL] --max-iterations must be zero or greater.")
        return 1
    if args.json_output and args.max_iterations == 0:
        fail("[FAIL] --json requires a bounded --max-iterations value.")
        return 1

    iterations = []
    iteration = 0
    try:
        while args.max_iterations == 0 or iteration < args.max_iterations:
            slug, record = load_campaign(state, args.name)
            if record is None:
                fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
                return 1
            payload = build_campaign_prompt_payload(slug, record)
            payload["iteration"] = iteration + 1
            payload["timestamp"] = now_iso()
            if args.json_output:
                iterations.append(payload)
            else:
                if iteration:
                    print("")
                print("[OK] Campaign watch: {0} iteration {1}".format(slug, iteration + 1))
                print(payload["next_prompt"])
                sys.stdout.flush()
            iteration += 1
            if args.max_iterations != 0 and iteration >= args.max_iterations:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        if not args.json_output:
            print("\n[OK] Campaign watch stopped by interrupt.")
        return 130

    if args.json_output:
        print(json.dumps({
            "campaign": iterations[0]["id"] if iterations else args.name,
            "iterations": iterations,
            "guardrail": CAMPAIGN_PROMPT_GUARDRAIL,
        }, indent=2))
    return 0


def cmd_campaign_add_task(args, state):
    slug, record = load_campaign(state, args.campaign)
    if record is None:
        fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
        return 1
    tasks = record.setdefault("tasks", [])
    task_id = max([task.get("id", 0) for task in tasks] + [0]) + 1
    status = "pending"
    phase = "pending"
    if record.get("status") == "completed" and record.get("current_task_id") is None:
        status = "in_progress"
        phase = CAMPAIGN_PHASES[0]
        record["current_task_id"] = task_id
        record["status"] = "active"
    task = {
        "id": task_id,
        "title": args.title,
        "success_criteria": args.criteria or "",
        "status": status,
        "phase": phase,
        "result": "",
        "created": now_iso(),
        "updated": now_iso(),
    }
    tasks.append(task)
    save_campaign(state, slug, record)
    print("[OK] Added task {0} to campaign {1}: {2}".format(task_id, slug, args.title))
    return 0


def cmd_campaign_advance(args, state):
    slug, record = load_campaign(state, args.name)
    if record is None:
        fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
        return 1
    task = current_campaign_task(record)
    if task is None:
        fail("[FAIL] Campaign has no current task.")
        return 1
    phase = task.get("phase", CAMPAIGN_PHASES[0])
    if phase not in CAMPAIGN_PHASES:
        phase = CAMPAIGN_PHASES[0]
    stamp = now_iso()
    event = {
        "task_id": task.get("id"),
        "phase": phase,
        "result": args.result,
        "timestamp": stamp,
    }
    record.setdefault("events", []).append(event)
    if phase == "verify":
        verify_command = str(record.get("verify_command") or "").strip()
        if verify_command and os.environ.get("MYTHIFY_DISABLE_RUN") != "1":
            verification = execute_verification(
                state,
                verify_command,
                "campaign {0} task {1} verifier".format(slug, task.get("id")),
            )
            event["verifier_exit_code"] = verification.get("exit_code")
            event["verifier_verified"] = bool(verification.get("verified"))
            if not verification.get("verified"):
                task["updated"] = stamp
                save_campaign(state, slug, record)
                fail(
                    "[FAIL] Campaign verifier failed (exit {0}): {1}. The task "
                    "stays in verify; fix the cause, then advance again.".format(
                        verification.get("exit_code"), verify_command
                    )
                )
                return 1
            print(
                "[OK] Campaign verifier passed (exit {0}): {1}".format(
                    verification.get("exit_code"), verify_command
                )
            )
    if phase == "reflect":
        task["status"] = "completed"
        task["phase"] = "done"
        task["result"] = args.result
        task["updated"] = stamp
        next_task = campaign_set_next_task(record)
        save_campaign(state, slug, record)
        if next_task is None:
            clear_active_campaign_slug(state, slug)
            print("[OK] Campaign {0} completed.".format(slug))
        else:
            print("[OK] Completed task {0}; next task {1} is in progress.".format(
                task.get("id"),
                next_task.get("id"),
            ))
            print("Next: {0}".format(campaign_next_action(record)))
        return 0
    next_phase = CAMPAIGN_PHASES[CAMPAIGN_PHASES.index(phase) + 1]
    task["phase"] = next_phase
    task["status"] = "in_progress"
    task["updated"] = stamp
    save_campaign(state, slug, record)
    print("[OK] Campaign {0} task {1} advanced: {2} -> {3}".format(
        slug,
        task.get("id"),
        phase,
        next_phase,
    ))
    print("Next: {0}".format(campaign_next_action(record)))
    return 0


def cmd_campaign_task(args, state):
    if args.status not in CAMPAIGN_TASK_STATUSES:
        fail("[FAIL] Invalid task status: {0}".format(args.status))
        return 1
    if args.status in ("completed", "failed") and not args.result:
        fail(EVIDENCE_MESSAGE)
        return 1
    slug, record = load_campaign(state, args.campaign)
    if record is None:
        fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
        return 1
    try:
        task_id = int(args.id)
    except ValueError:
        fail("[FAIL] Invalid task id: {0}. Task ids are integers.".format(args.id))
        return 1
    task = None
    for candidate in record.get("tasks") or []:
        if candidate.get("id") == task_id:
            task = candidate
            break
    if task is None:
        fail("[FAIL] Task {0} not found in campaign {1}.".format(task_id, slug))
        return 1
    task["status"] = args.status
    task["result"] = args.result or task.get("result", "")
    task["updated"] = now_iso()
    if args.status == "in_progress":
        task["phase"] = task.get("phase") if task.get("phase") in CAMPAIGN_PHASES else CAMPAIGN_PHASES[0]
        record["current_task_id"] = task_id
        record["status"] = "active"
    elif args.status == "completed":
        task["phase"] = "done"
        if record.get("current_task_id") == task_id:
            campaign_set_next_task(record)
            if record.get("status") == "completed":
                clear_active_campaign_slug(state, slug)
    elif args.status == "failed":
        task["phase"] = "failed"
    elif args.status == "skipped":
        task["phase"] = "skipped"
        if record.get("current_task_id") == task_id:
            campaign_set_next_task(record)
            if record.get("status") == "completed":
                clear_active_campaign_slug(state, slug)
    save_campaign(state, slug, record)
    print("[OK] Campaign {0} task {1} -> {2}".format(slug, task_id, args.status))
    print("Next: {0}".format(campaign_next_action(record)))
    return 0


def cmd_campaign_learn(args, state):
    slug, record = load_campaign(state, args.campaign)
    if record is None:
        fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
        return 1
    task_id = None
    if args.task is not None:
        try:
            task_id = int(args.task)
        except ValueError:
            fail("[FAIL] Invalid task id: {0}. Task ids are integers.".format(args.task))
            return 1
    elif record.get("current_task_id") is not None:
        task_id = record.get("current_task_id")
    item = {
        "task_id": task_id,
        "lesson": args.lesson,
        "apply_next": bool(args.apply_next),
        "created": now_iso(),
    }
    record.setdefault("learnings", []).append(item)
    save_campaign(state, slug, record)
    print("[OK] Campaign learning recorded for {0}".format(slug))
    return 0


def cmd_campaign_stop(args, state):
    slug, record = load_campaign(state, args.name)
    if record is None:
        fail("[FAIL] Campaign not found. Start one with: campaign start GOAL")
        return 1
    record["status"] = "stopped"
    record["stop_reason"] = args.reason
    save_campaign(state, slug, record)
    clear_active_campaign_slug(state, slug)
    print("[OK] Stopped campaign {0}: {1}".format(slug, args.reason))
    return 0



