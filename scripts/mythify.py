#!/usr/bin/env python3
"""Mythify command line interface.

Zero-dependency orchestrator for disciplined agent work: plans with stepwise
progress, executed-or-attested verification records, persistent key-value
memory, lessons, and structured reflections.

State lives in a per-project .mythify directory discovered by walking upward
from the current working directory, or in the directory named by the
MYTHIFY_DIR environment variable (created on demand). Global lessons live in
~/.mythify/lessons and are independent of project state.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mythify_classification import (  # noqa: E402
    classify_task_text,
    format_classification,
    should_run_model_triage,
)
from mythify_host_model import (  # noqa: E402
    HOST_THINKING_LEVELS,
    PLATFORMS,
    SPEED_LEVELS,
    cmd_host_model_clear,
    cmd_host_model_status,
    cmd_host_model_switch,
    configure_host_model_store,
    host_capability_for_record,
    normalize_host_platform,
    normalize_host_speed,
    normalize_host_thinking,
    read_host_model_state,
)
from mythify_model_policy import (  # noqa: E402
    EFFORT_LEVELS,
    FANOUT_VISIBILITY_MODES,
    REVIEWER_STRENGTH_MODES,
    SPAWN_CEILINGS,
    TRIAGE_ENGINES,
    TRIAGE_MODES,
    build_model_policy,
    classify_model_tier,
    run_model_triage,
)
from mythify_io import (  # noqa: E402
    JSONL_TAIL_CHUNK_BYTES,
    _write_text_atomic,
    append_jsonl,
    configure_durable_io,
    jsonl_file_lock,
    read_json,
    read_jsonl,
    read_jsonl_since,
    write_json_atomic,
    write_jsonl_atomic,
)
from mythify_memory import (  # noqa: E402
    MEMORY_CATEGORIES,
    MEMORY_DEFAULT_CATEGORY,
    cmd_lesson_add,
    cmd_lesson_list,
    cmd_memory_clear,
    cmd_memory_get,
    cmd_memory_set,
    configure_memory_store,
    default_memory,
    global_lessons_dir,
    load_lessons,
    load_memory,
    write_lesson,
)

from mythify_outcomes import (  # noqa: E402
    cmd_outcome_check,
    cmd_outcome_results,
    cmd_outcome_start,
    cmd_outcome_status,
    cmd_outcome_stop,
    configure_outcome_loops,
    get_active_outcome_slug,
    list_outcomes,
    load_outcome,
    outcome_iterations_path,
)
from mythify_trace import (  # noqa: E402
    cmd_trace_analyze,
    cmd_trace_compare,
    cmd_trace_distill,
    cmd_trace_install_playbook,
    cmd_trace_playbook,
    configure_trace_commands,
)
from mythify_router import (  # noqa: E402
    PROMPT_PACKET_KINDS,
    cmd_prompt_packet,
    cmd_route,
    configure_prompt_router,
)
from mythify_workflows import (  # noqa: E402
    CAMPAIGN_TASK_STATUSES,
    RESEARCH_CONFIDENCE,
    RESEARCH_SOURCE_CREDIBILITY,
    cmd_campaign_add_task,
    cmd_campaign_advance,
    cmd_campaign_learn,
    cmd_campaign_list,
    cmd_campaign_prompt,
    cmd_campaign_start,
    cmd_campaign_status,
    cmd_campaign_stop,
    cmd_campaign_task,
    cmd_campaign_watch,
    cmd_research_add_claim,
    cmd_research_add_question,
    cmd_research_add_source,
    cmd_research_close,
    cmd_research_list,
    cmd_research_start,
    cmd_research_summary,
    configure_workflow_stores,
)
from mythify_views import (  # noqa: E402
    DEFAULT_REPORT_RECENT,
    REPORT_FORMATS,
    REPORT_SINCE_MODES,
    build_verification_history_view,
    build_work_report,
    cmd_background,
    cmd_dashboard,
    cmd_history,
    cmd_phase,
    cmd_progress,
    cmd_readiness,
    cmd_report,
    cmd_timeline,
    compact_report_detail,
    configure_views,
    git_status_summary,
    verification_label,
)

WORKSPACE_DIR_NAME = ".mythify"
VERSION = "3.6.47"
REPO_ROOT = SCRIPT_DIR.parent
PROTOCOL_SOURCE_SHA256 = "00537ffff2a26e265d61d76c288a5e4f5d426e5c69b745d8a89d1c01fe32736b"
PROTOCOL_HASH_PREFIX = "<!-- Mythify protocol-sha256: "
PROTOCOL_COPY_CANDIDATES = ("CLAUDE.md", "AGENTS.md", ".cursorrules")
NO_WORKSPACE_MESSAGE = (
    "[FAIL] No .mythify workspace found. Run: python3 scripts/mythify.py init"
)
EVIDENCE_MESSAGE = (
    "[FAIL] Evidence required: pass a RESULT describing what proves this status."
)
VERIFIED_EVIDENCE_MESSAGE = (
    "[FAIL] Verified evidence required: strict evidence mode is enabled by "
    "default, but no passing 'verify run' was recorded since this step started. "
    "Run 'verify run' with a passing check first, or set "
    "MYTHIFY_REQUIRE_VERIFIED_STEP=0 to use legacy prose-only completion."
)
VERIFY_RUN_DISABLED_MESSAGE = (
    "[FAIL] verify run is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded. Unset it to enable execution, or use "
    "verify claim to record a self-reported attestation."
)
STEP_STATUSES = ("pending", "in_progress", "completed", "failed", "skipped")
FALSE_ENV_VALUES = ("0", "false", "no", "off")
STATUS_ICONS = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
    "failed": "[!]",
    "skipped": "[~]",
}


REFLECT_OUTCOMES = ("success", "partial", "failure")
TAIL_CHARS = 4000
REDACTED_SECRET = "[REDACTED]"
DEFAULT_VERIFY_TIMEOUT = 300.0
DEFAULT_VERIFY_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_LOG_COMPACT_KEEP = 1000
LOG_COMPACT_TARGETS = ("verifications.jsonl", "reflections.jsonl")
# ---------------------------------------------------------------------------
# Time and text helpers
# ---------------------------------------------------------------------------

def now_iso():
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def timestamp_sort_key(value):
    stamp = parse_iso_timestamp(value)
    if stamp is not None:
        return (1, stamp.timestamp(), str(value or ""))
    return (0, str(value or ""))


def timestamp_at_or_after(value, lower_bound, allow_same_second=False):
    left = parse_iso_timestamp(value)
    right = parse_iso_timestamp(lower_bound)
    if left is not None and right is not None:
        if allow_same_second:
            left = left.replace(microsecond=0)
            right = right.replace(microsecond=0)
        return left >= right
    return str(value or "") >= str(lower_bound or "")


def timestamp_after(value, lower_bound):
    left = parse_iso_timestamp(value)
    right = parse_iso_timestamp(lower_bound)
    if left is not None and right is not None:
        return left > right
    return str(value or "") > str(lower_bound or "")


def now_stamp():
    """Current UTC time as YYYYMMDDHHMMSS, for filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def slugify(text):
    """Lowercase, collapse runs of non-alphanumerics to '-', strip edge '-',
    truncate to 40 characters."""
    chars = []
    for ch in str(text).lower():
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            chars.append(ch)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-")[:40]


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(digest):
    if not digest:
        return "missing"
    return digest[:12]


def extract_protocol_copy_hash(text):
    for line in text.splitlines()[:8]:
        stripped = line.strip()
        if stripped.startswith(PROTOCOL_HASH_PREFIX) and stripped.endswith("-->"):
            return stripped[len(PROTOCOL_HASH_PREFIX):-3].strip()
    return None


def source_protocol_path():
    return REPO_ROOT / "protocol" / "PROTOCOL.md"


def default_protocol_check_paths():
    cwd = Path.cwd()
    return [cwd / name for name in PROTOCOL_COPY_CANDIDATES if (cwd / name).is_file()]


def protocol_source_check():
    path = source_protocol_path()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    actual = sha256_text(text)
    return {
        "kind": "source",
        "path": str(path),
        "expected": PROTOCOL_SOURCE_SHA256,
        "actual": actual,
        "status": "ok" if actual == PROTOCOL_SOURCE_SHA256 else "drift",
    }


def protocol_copy_check(path):
    path = Path(path)
    result = {
        "kind": "copy",
        "path": str(path),
        "expected": PROTOCOL_SOURCE_SHA256,
        "actual": None,
        "status": "ok",
    }
    if not path.is_file():
        result["status"] = "missing_file"
        return result
    text = path.read_text(encoding="utf-8")
    actual = extract_protocol_copy_hash(text)
    result["actual"] = actual
    if actual is None:
        result["status"] = "missing_header"
    elif actual != PROTOCOL_SOURCE_SHA256:
        result["status"] = "drift"
    return result


def format_protocol_check_failure(result):
    path = result["path"]
    status = result["status"]
    if status == "missing_file":
        return "[FAIL] Protocol file not found: {0}".format(path)
    if status == "missing_header":
        return (
            "[FAIL] Protocol handshake missing from {0}. Regenerate with "
            "scripts/build_variants.py or copy a current protocol variant."
        ).format(path)
    if status == "drift":
        return (
            "[FAIL] Protocol handshake drift in {0}: expected {1}, found {2}. "
            "Regenerate variants and copy the matching CLI."
        ).format(path, short_hash(result["expected"]), short_hash(result["actual"]))
    return "[FAIL] Protocol check failed for {0}: {1}".format(path, status)


def fail(message):
    """Print a failure line to stderr."""
    sys.stderr.write(message + "\n")


configure_trace_commands(
    slugify_func=slugify,
    fail_func=fail,
)
configure_memory_store(
    now_iso_func=now_iso,
    now_stamp_func=now_stamp,
    slugify_func=slugify,
    fail_func=fail,
)


# ---------------------------------------------------------------------------
# State directory resolution
# ---------------------------------------------------------------------------

def ensure_layout(state):
    """Create the state directory and its subdirectories."""
    (state / "plans" / "archive").mkdir(parents=True, exist_ok=True)
    (state / "lessons").mkdir(parents=True, exist_ok=True)
    (state / "outcomes").mkdir(parents=True, exist_ok=True)
    (state / "research").mkdir(parents=True, exist_ok=True)
    (state / "campaigns").mkdir(parents=True, exist_ok=True)
    (state / "reports").mkdir(parents=True, exist_ok=True)
    (state / "logs" / "archive").mkdir(parents=True, exist_ok=True)


def gitignore_has_state_entry(text):
    entries = {line.strip() for line in text.splitlines()}
    return WORKSPACE_DIR_NAME in entries or (WORKSPACE_DIR_NAME + "/") in entries


def ensure_default_state_gitignored(project_dir):
    """Keep the default in-repo state directory out of accidental commits."""
    path = Path(project_dir) / ".gitignore"
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if gitignore_has_state_entry(existing):
            return False
        prefix = "" if existing == "" or existing.endswith("\n") else "\n"
        _write_text_atomic(path, existing + prefix + WORKSPACE_DIR_NAME + "/\n")
        return True
    except OSError as err:
        fail("[WARN] Could not add {0}/ to .gitignore: {1}".format(WORKSPACE_DIR_NAME, err))
        return False


def discover_state_dir():
    """Walk upward from cwd; the first directory containing .mythify wins."""
    current = Path.cwd().resolve()
    for base in [current] + list(current.parents):
        candidate = base / WORKSPACE_DIR_NAME
        if candidate.is_dir():
            return candidate
    return None


def resolve_state_dir():
    """MYTHIFY_DIR (created on demand) beats upward discovery."""
    env_dir = os.environ.get("MYTHIFY_DIR")
    if env_dir:
        state = Path(env_dir).expanduser()
        ensure_layout(state)
        return state
    return discover_state_dir()


configure_durable_io(
    resolve_state_dir_func=resolve_state_dir,
    now_stamp_func=now_stamp,
    timestamp_at_or_after_func=timestamp_at_or_after,
)
configure_host_model_store(
    resolve_state_dir_func=resolve_state_dir,
    now_iso_func=now_iso,
    classify_model_tier_func=classify_model_tier,
    fail_func=fail,
)


# ---------------------------------------------------------------------------
# Plan store
# ---------------------------------------------------------------------------

def plans_dir(state):
    return state / "plans"


def plan_path(state, slug):
    return plans_dir(state) / (slug + ".json")


def active_pointer_path(state):
    return plans_dir(state) / "active"


def list_plan_slugs(state):
    directory = plans_dir(state)
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def count_archived(state):
    directory = plans_dir(state) / "archive"
    if not directory.is_dir():
        return 0
    return len(list(directory.glob("*.json")))


def get_active_slug(state):
    pointer = active_pointer_path(state)
    if not pointer.is_file():
        return None
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if name and plan_path(state, name).exists():
        return name
    return None


def set_active_slug(state, slug):
    _write_text_atomic(active_pointer_path(state), slug + "\n")


def clear_active_slug(state):
    pointer = active_pointer_path(state)
    if pointer.exists():
        try:
            pointer.unlink()
        except OSError:
            pass


def load_plan(state, slug):
    path = plan_path(state, slug)
    if not path.exists():
        return None
    plan = read_json(path, None)
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return None
    return plan


def save_plan(state, slug, plan):
    write_json_atomic(plan_path(state, slug), plan)


def find_existing_slug_by_name(state, name, path_func):
    candidate = slugify(name)
    if candidate and path_func(state, candidate).exists():
        return candidate
    return None


def find_plan_slug(state, name):
    """Map a user-supplied plan name to an existing plan slug, or None."""
    return find_existing_slug_by_name(state, name, plan_path)


def target_plan_slug(state, name):
    """Named plan if given, otherwise the active plan. None if unresolvable."""
    if name:
        return find_plan_slug(state, name)
    return get_active_slug(state)


configure_workflow_stores(
    now_iso_func=now_iso,
    slugify_func=slugify,
    fail_func=fail,
    find_existing_slug_by_name_func=find_existing_slug_by_name,
)


def plan_progress(plan):
    steps = plan.get("steps", [])
    done = sum(1 for step in steps if step.get("status") == "completed")
    return done, len(steps)


def next_pending_step(plan):
    for step in plan.get("steps", []):
        if step.get("status") == "pending":
            return step
    return None


def verification_step_context(state):
    slug = get_active_slug(state)
    if not slug:
        return {
            "plan": None,
            "step_id": None,
            "step_title": None,
            "step_status": None,
        }
    plan = load_plan(state, slug)
    if plan is None:
        return {
            "plan": None,
            "step_id": None,
            "step_title": None,
            "step_status": None,
        }
    for step in plan.get("steps", []):
        if step.get("status") == "in_progress":
            return {
                "plan": slug,
                "step_id": step.get("id"),
                "step_title": step.get("title"),
                "step_status": step.get("status"),
            }
    return {
        "plan": None,
        "step_id": None,
        "step_title": None,
        "step_status": None,
    }


def verification_record_matches_step(record, slug, step_id):
    has_legacy_context = "plan" not in record and "step_id" not in record
    if has_legacy_context:
        return True
    return record.get("plan") == slug and record.get("step_id") == step_id


def verification_record_has_explicit_step_context(record, slug, step_id):
    return (
        "plan" in record
        and "step_id" in record
        and record.get("plan") == slug
        and record.get("step_id") == step_id
    )


def strict_step_evidence_enabled():
    raw = os.environ.get("MYTHIFY_REQUIRE_VERIFIED_STEP", "")
    return raw.strip().lower() not in FALSE_ENV_VALUES


def format_step_line(step, indent="  "):
    icon = STATUS_ICONS.get(step.get("status", "pending"), "[ ]")
    return "{0}{1} {2}. {3}".format(indent, icon, step.get("id"), step.get("title"))


def describe_next_pending(plan):
    step = next_pending_step(plan)
    if step is None:
        return "No pending steps remain."
    criteria = step.get("success_criteria") or "none"
    return "Next pending: {0}. {1} (criteria: {2})".format(
        step.get("id"), step.get("title"), criteria
    )


def tail_text(text, limit=TAIL_CHARS):
    return str(text or "")[-limit:]


def redact_sensitive_output(text):
    value = str(text or "")
    if not value:
        return ""
    value = re.sub(
        r"(?i)\b(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/\-=]+)",
        r"\1" + REDACTED_SECRET,
        value,
    )
    value = re.sub(
        r"(?i)\b([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)"
        r"[A-Za-z0-9_-]*\s*=\s*)([^\s,;]+)",
        r"\1" + REDACTED_SECRET,
        value,
    )
    value = re.sub(
        r"(?i)([\"']?[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)"
        r"[A-Za-z0-9_-]*[\"']?\s*:\s*)([\"'])([^\"']+)([\"'])",
        r"\1\2" + REDACTED_SECRET + r"\4",
        value,
    )
    value = re.sub(
        r"(?i)\b((?:authorization|x-api-key|api-key|api_key|token|secret|password|passwd|credential)"
        r"\s*:\s*)([^\s,;}]+)",
        r"\1" + REDACTED_SECRET,
        value,
    )
    value = re.sub(
        r"\b("
        r"sk-ant-[A-Za-z0-9_-]{16,}|"
        r"sk-[A-Za-z0-9_-]{16,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|"
        r"gh[pousr]_[A-Za-z0-9_]{20,}|"
        r"npm_[A-Za-z0-9_-]{20,}"
        r")\b",
        REDACTED_SECRET,
        value,
    )
    return value



# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_init(args, _state):
    env_dir = os.environ.get("MYTHIFY_DIR")
    if env_dir:
        state = Path(env_dir).expanduser()
        already_initialized = (state / "memory.json").exists()
        ensure_layout(state)
        if already_initialized:
            print("[WARN] Workspace already initialized at {0}. Nothing to do.".format(state))
            return 0
        write_json_atomic(state / "memory.json", default_memory())
        print("[OK] Initialized Mythify workspace at {0}".format(state))
        return 0
    existing = discover_state_dir()
    if existing is not None:
        if existing.name == WORKSPACE_DIR_NAME:
            ensure_default_state_gitignored(existing.parent)
        print("[WARN] Already inside a Mythify workspace: {0}. Nothing to do.".format(existing))
        return 0
    state = Path.cwd() / WORKSPACE_DIR_NAME
    ensure_layout(state)
    ensure_default_state_gitignored(Path.cwd())
    if not (state / "memory.json").exists():
        write_json_atomic(state / "memory.json", default_memory())
    print("[OK] Initialized Mythify workspace at {0}".format(state))
    return 0


def cmd_protocol_check(args, _state):
    explicit_paths = [Path(item) for item in args.paths]
    results = []
    if explicit_paths:
        results.extend(protocol_copy_check(path) for path in explicit_paths)
    else:
        source_result = protocol_source_check()
        if source_result is not None:
            results.append(source_result)
        results.extend(protocol_copy_check(path) for path in default_protocol_check_paths())

    if not results:
        output = {
            "status": "no_files",
            "expected": PROTOCOL_SOURCE_SHA256,
            "checked": [],
        }
        if args.json_output:
            print(json.dumps(output, indent=2))
        else:
            fail(
                "[FAIL] No protocol files found. Pass PATH or run from a directory "
                "containing CLAUDE.md, AGENTS.md, or .cursorrules."
            )
        return 1

    failures = [item for item in results if item["status"] != "ok"]
    output = {
        "status": "ok" if not failures else "failed",
        "expected": PROTOCOL_SOURCE_SHA256,
        "checked": results,
    }
    if args.json_output:
        print(json.dumps(output, indent=2))
        if failures:
            return 1
    elif failures:
        for failure in failures:
            fail(format_protocol_check_failure(failure))
        return 1
    else:
        names = ", ".join(result["path"] for result in results)
        print(
            "[OK] Protocol handshake verified ({0}) for: {1}".format(
                short_hash(PROTOCOL_SOURCE_SHA256), names
            )
        )
    return 0


def cmd_status(args, state):
    print("[OK] Status: {0}".format(state))
    active = get_active_slug(state)
    if active:
        plan = load_plan(state, active)
        if plan is not None:
            done, total = plan_progress(plan)
            print("Active plan: {0} ({1}/{2} completed)".format(active, done, total))
            print("Goal: {0}".format(plan.get("goal", "")))
            for step in plan.get("steps", []):
                print(format_step_line(step))
            print(describe_next_pending(plan))
        else:
            print("Active plan: none")
    else:
        print("Active plan: none")
    active_outcome = get_active_outcome_slug(state)
    if active_outcome:
        _, outcome = load_outcome(state, active_outcome)
        if outcome is not None:
            print(
                "Active outcome: {0} ({1}, {2}/{3} iterations)".format(
                    active_outcome,
                    outcome.get("status", "active"),
                    outcome.get("iteration_count", 0),
                    outcome.get("max_iterations", 1),
                )
            )
            print("Outcome goal: {0}".format(outcome.get("goal", "")))
        else:
            print("Active outcome: none")
    else:
        print("Active outcome: none")
    memory = load_memory(state)
    project_lessons = load_lessons(state / "lessons", "project")
    global_lessons = load_lessons(global_lessons_dir(), "global")
    verifications = read_jsonl(state / "verifications.jsonl")
    reflections = read_jsonl(state / "reflections.jsonl")
    print(
        "Counts: memory {0}, lessons {1} project + {2} global, "
        "verifications {3}, reflections {4}".format(
            len(memory["entries"]),
            len(project_lessons),
            len(global_lessons),
            len(verifications),
            len(reflections),
        )
    )
    return 0


def cmd_classify(args, _state):
    result = classify_task_text(args.task)
    result["model_policy"] = build_model_policy(
        result,
        args,
        read_host_model_state(_state),
    )
    if args.triage != "never":
        result["model_triage_run"] = run_model_triage(args.task, result, args)
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(format_classification(result))
    return 0


def cmd_plan_create(args, state):
    steps_input = []
    if args.steps is not None:
        try:
            parsed = json.loads(args.steps)
        except ValueError:
            fail(
                "[FAIL] Invalid JSON for --steps: expected an array of "
                "{\"title\": str, \"success_criteria\": str} objects."
            )
            return 1
        if not isinstance(parsed, list):
            fail("[FAIL] Invalid --steps: expected a JSON array of step objects.")
            return 1
        for item in parsed:
            if not isinstance(item, dict) or not item.get("title"):
                fail("[FAIL] Invalid --steps: every step needs a non-empty \"title\".")
                return 1
        steps_input = parsed
    base = slugify(args.name if args.name else args.goal) or "plan"
    slug = base
    suffix = 2
    while plan_path(state, slug).exists():
        slug = "{0}-{1}".format(base, suffix)
        suffix += 1
    stamp = now_iso()
    steps = []
    for index, item in enumerate(steps_input):
        steps.append(
            {
                "id": index + 1,
                "title": str(item["title"]),
                "success_criteria": str(item.get("success_criteria", "")),
                "status": "pending",
                "result": None,
            }
        )
    plan = {
        "name": slug,
        "goal": args.goal,
        "steps": steps,
        "created": stamp,
        "last_updated": stamp,
    }
    save_plan(state, slug, plan)
    set_active_slug(state, slug)
    print("[OK] Created plan: {0} ({1} steps). Active plan set to {0}.".format(slug, len(steps)))
    if not steps:
        print("Plan has no steps yet. Add steps with: plan add-step TITLE [--criteria TEXT]")
    return 0


def cmd_plan_add_step(args, state):
    slug = target_plan_slug(state, args.plan)
    if slug is None:
        if args.plan:
            fail("[FAIL] Plan not found: {0}".format(args.plan))
        else:
            fail("[FAIL] No active plan. Create one with: plan create GOAL")
        return 1
    plan = load_plan(state, slug)
    if plan is None:
        fail("[FAIL] Plan not found: {0}".format(slug))
        return 1
    new_id = max([step.get("id", 0) for step in plan["steps"]] + [0]) + 1
    plan["steps"].append(
        {
            "id": new_id,
            "title": args.title,
            "success_criteria": args.criteria or "",
            "status": "pending",
            "result": None,
        }
    )
    plan["last_updated"] = now_iso()
    save_plan(state, slug, plan)
    print("[OK] Added step {0} to plan {1}: {2}".format(new_id, slug, args.title))
    return 0


def cmd_plan_list(args, state):
    slugs = list_plan_slugs(state)
    active = get_active_slug(state)
    print("[OK] Plans ({0}):".format(len(slugs)))
    if not slugs:
        print("  none")
    for slug in slugs:
        plan = load_plan(state, slug)
        if plan is None:
            continue
        done, total = plan_progress(plan)
        marker = "* " if slug == active else "  "
        label = " (active)" if slug == active else ""
        print("{0}{1}{2}: {3}/{4} completed".format(marker, slug, label, done, total))
    print("Archived plans: {0}".format(count_archived(state)))
    return 0


def cmd_plan_show(args, state):
    name = args.name
    if not name:
        name = get_active_slug(state)
        if not name:
            fail("[FAIL] No plan specified and no active plan.")
            return 1
    slug = find_plan_slug(state, name)
    plan = load_plan(state, slug) if slug else None
    if plan is None:
        fail("[FAIL] Plan not found: {0}".format(name))
        return 1
    active = get_active_slug(state)
    label = " (active)" if slug == active else ""
    done, total = plan_progress(plan)
    print("[OK] Plan: {0}{1}".format(slug, label))
    print("Goal: {0}".format(plan.get("goal", "")))
    print("Created: {0}".format(plan.get("created", "")))
    print("Last updated: {0}".format(plan.get("last_updated", "")))
    print("Progress: {0}/{1} completed".format(done, total))
    if plan.get("steps"):
        print("Steps:")
        for step in plan["steps"]:
            criteria = step.get("success_criteria") or "none"
            print("{0} (criteria: {1})".format(format_step_line(step), criteria))
            if step.get("result"):
                print("        result: {0}".format(step["result"]))
    else:
        print("Steps: none. Add with: plan add-step TITLE [--criteria TEXT]")
    return 0


def cmd_plan_switch(args, state):
    slug = find_plan_slug(state, args.name)
    if slug is None:
        fail("[FAIL] Plan not found: {0}".format(args.name))
        return 1
    set_active_slug(state, slug)
    print("[OK] Active plan: {0}".format(slug))
    return 0


def cmd_plan_archive(args, state):
    name = args.name
    if not name:
        name = get_active_slug(state)
        if not name:
            fail("[FAIL] No plan specified and no active plan.")
            return 1
    slug = find_plan_slug(state, name)
    if slug is None:
        fail("[FAIL] Plan not found: {0}".format(name))
        return 1
    archive_dir = plans_dir(state) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    source = plan_path(state, slug)
    destination = archive_dir / (slug + ".json")
    if destination.exists():
        destination = archive_dir / ("{0}-{1}.json".format(slug, now_stamp()))
    os.replace(str(source), str(destination))
    if get_active_slug(state) is None:
        clear_active_slug(state)
    print("[OK] Archived plan: {0} -> {1}".format(slug, destination.name))
    return 0


def cmd_step(args, state):
    if args.status not in STEP_STATUSES:
        fail(
            "[FAIL] Invalid status: {0}. Use one of: {1}.".format(
                args.status, ", ".join(STEP_STATUSES)
            )
        )
        return 1
    try:
        step_id = int(args.id)
    except ValueError:
        fail("[FAIL] Invalid step id: {0}. Step ids are integers.".format(args.id))
        return 1
    slug = target_plan_slug(state, args.plan)
    if slug is None:
        if args.plan:
            fail("[FAIL] Plan not found: {0}".format(args.plan))
        else:
            fail("[FAIL] No active plan. Create one with: plan create GOAL")
        return 1
    plan = load_plan(state, slug)
    if plan is None:
        fail("[FAIL] Plan not found: {0}".format(slug))
        return 1
    step = None
    for candidate in plan["steps"]:
        if candidate.get("id") == step_id:
            step = candidate
            break
    if step is None:
        fail("[FAIL] Step {0} not found in plan {1}.".format(step_id, slug))
        return 1
    if args.status in ("completed", "failed") and (
        args.result is None or not args.result.strip()
    ):
        fail(EVIDENCE_MESSAGE)
        return 1
    if args.status == "completed" and strict_step_evidence_enabled():
        lower_bound = step.get("updated_at") or plan.get("created", "")
        records = read_jsonl_since(state / "verifications.jsonl", lower_bound)
        satisfied = any(
            record.get("kind") == "executed"
            and record.get("verified") is True
            and verification_record_matches_step(record, slug, step_id)
            and timestamp_at_or_after(
                record.get("timestamp", ""),
                lower_bound,
                verification_record_has_explicit_step_context(record, slug, step_id),
            )
            for record in records
        )
        if not satisfied:
            fail(VERIFIED_EVIDENCE_MESSAGE)
            return 1
    step["status"] = args.status
    if args.result is not None:
        step["result"] = args.result
    step["updated_at"] = now_iso()
    plan["last_updated"] = now_iso()
    save_plan(state, slug, plan)
    print("[OK] Step {0} -> {1}: {2}".format(step_id, args.status, step.get("title")))
    print(describe_next_pending(plan))
    return 0


def _append_stderr_notice(stderr_tail, notice):
    return (stderr_tail + "\n" + notice) if stderr_tail else notice


def _read_file_tail_text(path, char_limit=TAIL_CHARS):
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    byte_limit = max(char_limit * 4, 1024)
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, size - byte_limit))
            return handle.read().decode("utf-8", errors="replace")[-char_limit:]
    except OSError:
        return ""


def _file_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def verify_max_output_bytes():
    raw = os.environ.get("MYTHIFY_VERIFY_MAX_OUTPUT_BYTES", "").strip()
    if not raw:
        return DEFAULT_VERIFY_MAX_OUTPUT_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_VERIFY_MAX_OUTPUT_BYTES
    return value if value > 0 else DEFAULT_VERIFY_MAX_OUTPUT_BYTES


def signal_name(signum):
    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


def run_shell_capture(command, timeout, max_output_bytes=None):
    max_output_bytes = (
        verify_max_output_bytes() if max_output_bytes is None else max_output_bytes
    )
    started = datetime.now(timezone.utc)
    timed_out = False
    output_limit_exceeded = False
    spawn_error = None
    exit_code = None
    with tempfile.TemporaryDirectory(prefix="mythify-capture-") as tempdir:
        stdout_path = Path(tempdir) / "stdout"
        stderr_path = Path(tempdir) / "stderr"
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            try:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=stdout_file,
                    stderr=stderr_file,
                )
            except OSError as exc:
                process = None
                spawn_error = str(exc)
            if process is not None:
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        process.kill()
                        break
                    total_size = _file_size(stdout_path) + _file_size(stderr_path)
                    if total_size > max_output_bytes:
                        output_limit_exceeded = True
                        process.kill()
                        break
                    time.sleep(0.02)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
                exit_code = process.returncode
        stdout_tail = redact_sensitive_output(_read_file_tail_text(stdout_path))
        stderr_tail = redact_sensitive_output(_read_file_tail_text(stderr_path))
        total_size = _file_size(stdout_path) + _file_size(stderr_path)
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    if (
        not timed_out
        and not output_limit_exceeded
        and max_output_bytes is not None
        and total_size > max_output_bytes
    ):
        output_limit_exceeded = True
    if timed_out:
        exit_code = -1
        notice = "(timed out after {0:g} seconds)".format(timeout)
        stderr_tail = _append_stderr_notice(stderr_tail, notice)
    elif output_limit_exceeded:
        exit_code = -1
        notice = "(output exceeded {0} bytes)".format(max_output_bytes)
        stderr_tail = _append_stderr_notice(stderr_tail, notice)
    elif spawn_error is not None:
        exit_code = -1
        stderr_tail = _append_stderr_notice(stderr_tail, "({0})".format(spawn_error))
    elif exit_code is None:
        exit_code = -1
        stderr_tail = _append_stderr_notice(
            stderr_tail,
            "(command did not produce an exit code)",
        )
    elif exit_code < 0:
        stderr_tail = _append_stderr_notice(
            stderr_tail,
            "(terminated by signal {0})".format(signal_name(-exit_code)),
        )
        exit_code = -1
    return {
        "command": command,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "verified": exit_code == 0 and not timed_out and not output_limit_exceeded,
        "timed_out": timed_out,
        "output_limit_exceeded": output_limit_exceeded,
    }


configure_outcome_loops(
    find_existing_slug_by_name_func=find_existing_slug_by_name,
    now_iso_func=now_iso,
    slugify_func=slugify,
    run_shell_capture_func=run_shell_capture,
    verification_step_context_func=verification_step_context,
    fail_func=fail,
)


def cmd_verify_run(args, state):
    if os.environ.get("MYTHIFY_DISABLE_RUN") == "1":
        fail(VERIFY_RUN_DISABLED_MESSAGE)
        return 2
    run = run_shell_capture(args.command, args.timeout)
    exit_code = run["exit_code"]
    stdout_tail = run["stdout_tail"]
    stderr_tail = run["stderr_tail"]
    verified = run["verified"]
    record = {
        "kind": "executed",
        "claim": args.claim,
        "command": args.command,
        "exit_code": exit_code,
        "duration_seconds": run["duration_seconds"],
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "verified": verified,
        "timestamp": now_iso(),
    }
    record.update(verification_step_context(state))
    append_jsonl(state / "verifications.jsonl", record)
    label = args.claim or args.command
    if verified:
        print(
            "[OK] VERIFIED: {0} (exit {1}, {2:.2f}s)".format(
                label,
                exit_code,
                run["duration_seconds"],
            )
        )
        return 0
    print(
        "[FAIL] UNVERIFIED: {0} (exit {1}, {2:.2f}s)".format(
            label,
            exit_code,
            run["duration_seconds"],
        )
    )
    if stdout_tail:
        print("--- stdout (tail) ---")
        print(stdout_tail)
    if stderr_tail:
        print("--- stderr (tail) ---")
        print(stderr_tail)
    return 2


def cmd_verify_claim(args, state):
    record = {
        "kind": "attested",
        "claim": args.claim,
        "evidence": args.evidence,
        "verified": None,
        "timestamp": now_iso(),
    }
    record.update(verification_step_context(state))
    append_jsonl(state / "verifications.jsonl", record)
    print(
        "[WARN] ATTESTED: {0} (self-reported, not machine-checked; "
        "prefer verify run)".format(args.claim)
    )
    return 0


def cmd_reflect(args, state):
    if args.json:
        try:
            payload = json.loads(args.json)
        except ValueError:
            fail("[FAIL] Invalid JSON for reflect: pass a single JSON object.")
            return 1
        if not isinstance(payload, dict):
            fail("[FAIL] Invalid reflect payload: expected a JSON object.")
            return 1
    else:
        payload = {}
        if args.action is not None:
            payload["action"] = args.action
        if args.outcome is not None:
            payload["outcome"] = args.outcome
        if args.observation is not None:
            payload["observation"] = args.observation
        if args.next is not None:
            payload["next"] = args.next
        if args.root_cause is not None:
            payload["root_cause"] = args.root_cause
        if args.lesson is not None:
            payload["lesson"] = args.lesson
    missing = [
        key for key in ("action", "outcome", "observation", "next")
        if not payload.get(key)
    ]
    if missing:
        fail("[FAIL] Missing required reflection keys: {0}.".format(", ".join(missing)))
        return 1
    if payload["outcome"] not in REFLECT_OUTCOMES:
        fail(
            "[FAIL] Invalid outcome: {0}. Use one of: {1}.".format(
                payload["outcome"], ", ".join(REFLECT_OUTCOMES)
            )
        )
        return 1
    lesson = payload.get("lesson") or None
    record = {
        "action": str(payload["action"]),
        "outcome": payload["outcome"],
        "observation": str(payload["observation"]),
        "root_cause": str(payload["root_cause"]) if payload.get("root_cause") else None,
        "next": str(payload["next"]),
        "lesson": str(lesson) if lesson else None,
        "timestamp": now_iso(),
    }
    append_jsonl(state / "reflections.jsonl", record)
    print("[OK] Reflection recorded ({0}).".format(record["outcome"]))
    if record["lesson"]:
        detail = "Auto-recorded from a reflection (outcome: {0}). Action: {1}".format(
            record["outcome"], record["action"]
        )
        write_lesson(state / "lessons", record["lesson"], detail, ["auto-reflected"])
        print("[OK] Lesson recorded (project): {0}".format(record["lesson"]))
    return 0


def compact_archive_path(state, log_name):
    archive_dir = state / "logs" / "archive"
    stamp = now_stamp()
    stem = Path(log_name).stem
    candidate = archive_dir / "{0}-{1}.jsonl".format(stem, stamp)
    counter = 2
    while candidate.exists():
        candidate = archive_dir / "{0}-{1}-{2}.jsonl".format(stem, stamp, counter)
        counter += 1
    return candidate


def compact_jsonl_log(state, log_name, keep, dry_run):
    path = state / log_name
    with jsonl_file_lock(path):
        return compact_jsonl_log_locked(state, log_name, keep, dry_run)


def compact_jsonl_log_locked(state, log_name, keep, dry_run):
    path = state / log_name
    result = {
        "log": log_name,
        "path": str(path),
        "status": "missing",
        "raw_lines": 0,
        "total_records": 0,
        "retained_records": 0,
        "removed_records": 0,
        "archived": False,
        "archive_path": None,
    }
    if not path.exists():
        return result
    raw_text = path.read_text(encoding="utf-8")
    records = read_jsonl(path)
    total = len(records)
    retained = min(total, keep)
    removed = max(0, total - keep)
    result.update({
        "status": "unchanged" if removed == 0 else "would_compact",
        "raw_lines": len(raw_text.splitlines()),
        "total_records": total,
        "retained_records": retained,
        "removed_records": removed,
    })
    if removed == 0:
        return result
    archive_path = compact_archive_path(state, log_name)
    result["archive_path"] = str(archive_path)
    if dry_run:
        return result
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(archive_path, raw_text)
    write_jsonl_atomic(path, records[-keep:])
    result["status"] = "compacted"
    result["archived"] = True
    return result


def format_log_compaction_result(result):
    before = result["total_records"]
    after = result["retained_records"]
    line = "{0}: {1}, records {2} -> {3}".format(
        result["log"], result["status"], before, after
    )
    if result["archive_path"]:
        line += ", archive {0}".format(result["archive_path"])
    if result["raw_lines"] != before:
        line += ", raw lines {0}".format(result["raw_lines"])
    return line


def cmd_logs_compact(args, state):
    if args.keep < 1:
        fail("[FAIL] logs compact requires --keep >= 1.")
        return 1
    results = [
        compact_jsonl_log(state, log_name, args.keep, args.dry_run)
        for log_name in LOG_COMPACT_TARGETS
    ]
    payload = {
        "status": "ok",
        "dry_run": args.dry_run,
        "keep": args.keep,
        "logs": results,
    }
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        label = "dry run" if args.dry_run else "complete"
        print("[OK] Log compaction {0}.".format(label))
        for result in results:
            print(format_log_compaction_result(result))
    return 0


def cmd_summary(args, state):
    slugs = list_plan_slugs(state)
    active = get_active_slug(state)
    print("[OK] Summary: {0}".format(state))
    print("Plans ({0}):".format(len(slugs)))
    if not slugs:
        print("  none")
    for slug in slugs:
        plan = load_plan(state, slug)
        if plan is None:
            continue
        done, total = plan_progress(plan)
        label = " (active)" if slug == active else ""
        print(
            "  {0}{1}: {2}/{3} completed - {4}".format(
                slug, label, done, total, plan.get("goal", "")
            )
        )
    print("Archived plans: {0}".format(count_archived(state)))
    memory = load_memory(state)
    print("Memory entries: {0}".format(len(memory["entries"])))
    project_lessons = load_lessons(state / "lessons", "project")
    global_lessons = load_lessons(global_lessons_dir(), "global")
    print("Lessons: {0} project, {1} global".format(len(project_lessons), len(global_lessons)))
    verifications = read_jsonl(state / "verifications.jsonl")
    executed = [r for r in verifications if r.get("kind") == "executed"]
    passed = sum(1 for r in executed if r.get("verified") is True)
    failed = sum(1 for r in executed if r.get("verified") is False)
    attested = sum(1 for r in verifications if r.get("kind") == "attested")
    print(
        "Verifications: {0} executed ({1} passed, {2} failed), {3} attested".format(
            len(executed), passed, failed, attested
        )
    )
    reflections = read_jsonl(state / "reflections.jsonl")
    print("Reflections: {0}".format(len(reflections)))
    return 0


configure_views(
    get_active_slug_func=get_active_slug,
    load_plan_func=load_plan,
    plan_progress_func=plan_progress,
    next_pending_step_func=next_pending_step,
    load_memory_func=load_memory,
    load_lessons_func=load_lessons,
    global_lessons_dir_func=global_lessons_dir,
    list_plan_slugs_func=list_plan_slugs,
    format_step_line_func=format_step_line,
    timestamp_sort_key_func=timestamp_sort_key,
    timestamp_after_func=timestamp_after,
    now_iso_func=now_iso,
    slugify_func=slugify,
    fail_func=fail,
)

configure_prompt_router(
    get_active_slug_func=get_active_slug,
    load_plan_func=load_plan,
    plan_progress_func=plan_progress,
    next_pending_step_func=next_pending_step,
    read_jsonl_func=read_jsonl,
    build_verification_history_view_func=build_verification_history_view,
    verification_label_func=verification_label,
    git_status_summary_func=git_status_summary,
    compact_report_detail_func=compact_report_detail,
    build_work_report_func=build_work_report,
    load_outcome_func=load_outcome,
    read_host_model_state_func=read_host_model_state,
    fail_func=fail,
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="mythify.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Mythify v{0}: evidence protocol for AI coding agents. Route broad ".format(VERSION) +
            "work first, keep state in .mythify, and verify completion claims "
            "with executed commands."
        ),
        epilog=(
            "Recommended front door:\n"
            "  mythify route \"TASK\"        choose direct, plan, research, review, outcome, campaign, failure, handoff, or prompt routing\n"
            "  mythify report ...          show chat-ready progress and issue reports\n"
            "  mythify verify run ...      record executed proof before a completion claim\n"
            "  mythify status              reorient from durable state\n"
            "\n"
            "Workflow primitives:\n"
            "  plan, outcome, campaign, research, prompt\n"
            "\n"
            "Advanced surfaces:\n"
            "  dashboard, history, background, progress, readiness, timeline, phase, trace,\n"
            "  classify, memory, lesson, logs, reflect, summary, protocol, fanout through MCP\n"
            "\n"
            "Labs surfaces:\n"
            "  host-model, provider probes, local model runs, host CLI workers,\n"
            "  execution probes/runs, lifecycle probes\n"
            "\n"
            "Strict evidence mode:\n"
            "  completed steps require a passing verify run by default\n"
            "  set MYTHIFY_REQUIRE_VERIFIED_STEP=0 only for legacy prose-only completion\n"
        ),
    )
    parser.add_argument("--version", action="version", version="Mythify v{0}".format(VERSION))
    parser.set_defaults(needs_state=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p = sub.add_parser(
        "init",
        help="Create ./.mythify with subdirectories and an empty memory.json.",
        description=(
            "Create ./.mythify with subdirectories and an empty memory.json. "
            "If already inside a workspace, print [WARN] and exit 0."
        ),
    )
    p.set_defaults(handler=cmd_init, needs_state=False)

    protocol = sub.add_parser(
        "protocol",
        help="Protocol copy checks.",
        description="Check copied protocol files against the CLI's embedded source protocol hash.",
    )
    protocol_sub = protocol.add_subparsers(dest="protocol_command", metavar="ACTION", required=True)
    p = protocol_sub.add_parser(
        "check",
        help="Verify copied protocol files match this CLI.",
        description=(
            "Verify copied protocol files match this CLI's embedded source protocol "
            "hash. With no paths, check source protocol when present and local "
            "CLAUDE.md, AGENTS.md, and .cursorrules files."
        ),
    )
    p.add_argument("paths", nargs="*", help="Protocol copy files to check.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_protocol_check, needs_state=False)

    p = sub.add_parser(
        "status",
        help="Show the active plan with step icons, the next pending step, and state counts.",
        description=(
            "Orientation: active plan with step icons, next pending step and its "
            "criteria, and one-line counts for memory, lessons, verifications, "
            "and reflections."
        ),
    )
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser(
        "dashboard",
        help="Show a read-only workflow dashboard with plan, outcome, and evidence state.",
        description=(
            "Read-only workflow dashboard: active plan, current and next step, "
            "active outcome, memory and lesson counts, verification totals, "
            "recent verification records, and recent reflections."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=3,
        help="Number of recent verification and reflection records to show. Defaults to 3.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_dashboard)

    p = sub.add_parser(
        "history",
        help="Show a read-only verification history.",
        description=(
            "Read-only verification history: executed and attested verification "
            "records, verdicts, commands, exit codes, duration, and plan or step "
            "context from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=10,
        help="Number of recent verification records to show. Defaults to 10.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_history)

    p = sub.add_parser(
        "report",
        help="Show a chat-ready live work report from durable Mythify events.",
        description=(
            "Chat-ready live work report: plan creation, step updates, "
            "verification records, and reflections from durable state. By "
            "default it advances a cursor so repeated calls with --since last "
            "only show new events; use --peek to leave the cursor unchanged."
        ),
    )
    p.add_argument(
        "--since",
        choices=REPORT_SINCE_MODES,
        default=None,
        help="Event window to report: last cursor or start of state. Defaults to last.",
    )
    p.add_argument(
        "--format",
        dest="report_format",
        choices=REPORT_FORMATS,
        default="chat",
        help="Output format: chat or json. Defaults to chat.",
    )
    p.add_argument(
        "--recent",
        type=int,
        default=DEFAULT_REPORT_RECENT,
        help="Maximum events to show. Defaults to {0}.".format(DEFAULT_REPORT_RECENT),
    )
    p.add_argument(
        "--cursor",
        default="default",
        help="Report cursor name. Defaults to default.",
    )
    p.add_argument(
        "--peek",
        action="store_true",
        help="Do not advance the report cursor.",
    )
    p.add_argument(
        "--mark",
        action="store_true",
        help="Advance the report cursor to the latest event without showing old events.",
    )
    p.set_defaults(handler=cmd_report)

    p = sub.add_parser(
        "route",
        help="Choose the next workflow route from prompt text and durable state.",
        description=(
            "Read-only workflow quarterback: classify a prompt, inspect durable "
            "state, and choose direct, plan, research, review, outcome, campaign, "
            "failure recovery, handoff, or prompt packet routing."
        ),
    )
    p.add_argument("task", help="Task request or problem statement to route.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.add_argument(
        "--triage",
        choices=TRIAGE_MODES,
        default="never",
        help=(
            "Run a fast model triage pass: never (default), auto when the gate "
            "is recommended or required, or always."
        ),
    )
    p.add_argument(
        "--triage-engine",
        choices=TRIAGE_ENGINES,
        default="",
        help="Fast triage engine. Defaults to MYTHIFY_TRIAGE_ENGINE or auto-detection.",
    )
    p.add_argument(
        "--triage-model",
        default="",
        help="Fast triage model. Defaults to MYTHIFY_TRIAGE_MODEL or the engine default.",
    )
    p.add_argument(
        "--triage-timeout",
        type=float,
        default=120.0,
        help="Fast triage timeout in seconds.",
    )
    p.add_argument(
        "--platform",
        choices=PLATFORMS,
        default="auto",
        help="Host platform for model policy. Defaults to auto-detection.",
    )
    p.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default="auto",
        help="Overall effort preference for spawned model roles.",
    )
    p.add_argument(
        "--speed",
        choices=SPEED_LEVELS,
        default="auto",
        help="Overall speed preference for spawned model roles.",
    )
    p.add_argument(
        "--session-model",
        default="",
        help="Current host session model for spawn ceiling policy.",
    )
    p.add_argument(
        "--spawn-ceiling",
        choices=SPAWN_CEILINGS,
        default="auto",
        help="Maximum spawned model tier relative to the session model.",
    )
    p.add_argument(
        "--reviewer-strength",
        choices=REVIEWER_STRENGTH_MODES,
        default="auto",
        help="Reviewer model strength relative to the session.",
    )
    p.set_defaults(handler=cmd_route)

    prompt = sub.add_parser(
        "prompt",
        help="Render read-only workflow prompt packets.",
        description=(
            "Render chat-ready workflow prompt packets from durable Mythify state. "
            "Prompt packets are steering material for the host agent, not "
            "verification evidence."
        ),
    )
    prompt_sub = prompt.add_subparsers(dest="prompt_command", metavar="KIND", required=True)

    def add_prompt_common(parser):
        parser.add_argument(
            "--goal",
            default="",
            help="Optional host goal to include in the packet.",
        )
        parser.add_argument(
            "--verify",
            default="",
            help="Optional verifier command to include in the packet.",
        )
        parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")

    p = prompt_sub.add_parser(
        "research",
        help="Render a research to implementation prompt packet.",
    )
    p.add_argument("name", nargs="?", help="Research name. Defaults to the active research.")
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="research")

    p = prompt_sub.add_parser(
        "analysis",
        help="Render an analysis to plan prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="analysis")

    p = prompt_sub.add_parser(
        "failure",
        help="Render a failure recovery prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="failure")

    p = prompt_sub.add_parser(
        "handoff",
        help="Render a session handoff prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="handoff")

    p = prompt_sub.add_parser(
        "review",
        help="Render a review or audit prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="review")

    p = prompt_sub.add_parser(
        "campaign",
        help="Render a campaign prompt packet through the common packet contract.",
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to the active campaign.")
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="campaign")

    p = prompt_sub.add_parser(
        "next",
        help="Select and render the next useful prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="next")

    p = sub.add_parser(
        "background",
        help="Show read-only background task state for outcomes and fanout jobs.",
        description=(
            "Read-only background task view: outcome loops, fanout jobs, task "
            "counts, current statuses, and next actions from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="Number of recent outcomes and fanout jobs to show. Defaults to 5.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_background)

    p = sub.add_parser(
        "progress",
        help="Show read-only outcome loop progress.",
        description=(
            "Read-only outcome loop progress: active and recent outcomes, "
            "iteration budget, verifier exit details, metric score when present, "
            "and next action from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="Number of recent outcomes to show. Defaults to 5.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_progress)

    p = sub.add_parser(
        "readiness",
        help="Show read-only release readiness from recorded gates.",
        description=(
            "Read-only release readiness: recorded verification gates, project "
            "git state, roadmap state, and release-review status without "
            "rerunning gates or declaring the release safe."
        ),
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_readiness)

    p = sub.add_parser(
        "timeline",
        help="Show a read-only fanout worker timeline.",
        description=(
            "Read-only fanout worker timeline: recent fanout jobs, task start "
            "and finish events, duration, status, errors, and output metadata "
            "from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="Number of recent fanout jobs to include. Defaults to 5.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_timeline)

    p = sub.add_parser(
        "phase",
        help="Show a read-only Understand, Design, Build, Judge, Verify phase view.",
        description=(
            "Read-only phase view: active plan steps grouped into Understand, "
            "Design, Build, Judge, and Verify, with supporting evidence counts "
            "from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=3,
        help="Number of recent verification and reflection records to consider. Defaults to 3.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_phase)

    trace = sub.add_parser(
        "trace",
        help="Analyze exported agent traces and scenario rows.",
        description=(
            "Analyze exported Fable-style session traces, model action rows, "
            "and scenario prompt rows. Trace analysis is material for planning "
            "and eval design, not verification evidence."
        ),
    )
    trace_sub = trace.add_subparsers(dest="trace_command", metavar="ACTION", required=True)
    p = trace_sub.add_parser(
        "analyze",
        help="Summarize local JSON or JSONL trace exports.",
        description=(
            "Read JSONL or JSON files, detect trace shape, count tools and "
            "sessions, surface verification-like command signals, and suggest "
            "Mythify product or eval improvements."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files, directories, or - for JSONL stdin.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum records to read across inputs. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_analyze, needs_state=False)

    p = trace_sub.add_parser(
        "distill",
        help="Distill a model slice into a Markdown behavior profile.",
        description=(
            "Filter local trace exports by model, calculate visible workflow "
            "metrics, and write a trace-derived behavior profile."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files, directories, or - for JSONL stdin.",
    )
    p.add_argument("--model", help="Exact model name to filter, such as claude-fable-5.")
    p.add_argument("--title", help="Markdown title. Defaults to a trace-derived behavior profile title.")
    p.add_argument("--output", help="Write Markdown to this path instead of printing it.")
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum matching records to read across inputs. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_distill, needs_state=False)

    p = trace_sub.add_parser(
        "compare",
        help="Compare target and baseline model trace slices.",
        description=(
            "Compare visible workflow metrics between a target model slice "
            "and a baseline model slice, then render a delta playbook."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files or directories. Stdin is not supported for compare.",
    )
    p.add_argument("--target", required=True, help="Exact target model name to filter.")
    p.add_argument("--baseline", required=True, help="Exact baseline model name to filter.")
    p.add_argument("--target-label", help="Human label for the target slice.")
    p.add_argument("--baseline-label", help="Human label for the baseline slice.")
    p.add_argument("--output", help="Write Markdown to this path instead of printing it.")
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum matching records to read for each slice. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_compare, needs_state=False)

    p = trace_sub.add_parser(
        "playbook",
        help="Generate a concise agent playbook from trace behavior.",
        description=(
            "Build a session-start playbook from a target trace slice, "
            "optionally comparing it with a baseline model slice."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files, directories, or - for JSONL stdin. Stdin cannot be combined with --baseline.",
    )
    p.add_argument("--target", required=True, help="Exact target model name to filter.")
    p.add_argument("--baseline", help="Exact baseline model name to filter.")
    p.add_argument("--target-label", help="Human label for the target slice.")
    p.add_argument("--baseline-label", help="Human label for the baseline slice.")
    p.add_argument("--title", help="Markdown title. Defaults to a trace-derived agent playbook title.")
    p.add_argument("--output", help="Write Markdown to this path instead of printing it.")
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum matching records to read for each slice. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_playbook, needs_state=False)

    p = trace_sub.add_parser(
        "install-playbook",
        help="Install a generated playbook as a local Code or Codex skill.",
        description=(
            "Wrap a generated Markdown playbook in SKILL.md frontmatter and "
            "install it under a local skill root."
        ),
    )
    p.add_argument("playbook", help="Markdown playbook file to install.")
    p.add_argument("--skill", help="Skill directory name. Defaults to the playbook filename.")
    p.add_argument(
        "--skill-root",
        default="~/.codex/skills",
        help="Local skill root. Defaults to ~/.codex/skills.",
    )
    p.add_argument("--force", action="store_true", help="Overwrite an existing skill directory.")
    p.set_defaults(handler=cmd_trace_install_playbook, needs_state=False)

    research = sub.add_parser(
        "research",
        help="Manage source-backed research records.",
        description=(
            "Manage source-backed research: start a question, add sources, add "
            "claims, track open questions, and close with a decision. Research "
            "records are material for decisions, not executed verification."
        ),
    )
    research_sub = research.add_subparsers(dest="research_command", metavar="ACTION", required=True)

    p = research_sub.add_parser(
        "start",
        help="Start a research record and set it active.",
        description="Start a source-backed research record and set it active.",
    )
    p.add_argument("question", help="Research question or decision to investigate.")
    p.add_argument("--name", help="Research record name. Defaults to a slug of the question.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_research_start)

    p = research_sub.add_parser(
        "list",
        help="List research records.",
        description="List research records with active marker, counts, and status.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_research_list)

    p = research_sub.add_parser(
        "add-source",
        help="Add a source to the active or named research record.",
        description="Add a source with URL, note, and credibility to a research record.",
    )
    p.add_argument("title", help="Source title.")
    p.add_argument("--url", default="", help="Source URL or local path.")
    p.add_argument("--note", default="", help="Short note about why this source matters.")
    p.add_argument(
        "--credibility",
        choices=RESEARCH_SOURCE_CREDIBILITY,
        default="unknown",
        help="Source credibility marker. Defaults to unknown.",
    )
    p.add_argument("--research", help="Research record name. Defaults to active.")
    p.set_defaults(handler=cmd_research_add_source)

    p = research_sub.add_parser(
        "add-claim",
        help="Add a claim and its evidence to a research record.",
        description="Add a claim, evidence note, optional source id, and confidence marker.",
    )
    p.add_argument("claim", help="Claim learned from the research.")
    p.add_argument("--evidence", required=True, help="Evidence supporting the claim.")
    p.add_argument("--source", help="Source id such as S1.")
    p.add_argument(
        "--confidence",
        choices=RESEARCH_CONFIDENCE,
        default="medium",
        help="Confidence marker. Defaults to medium.",
    )
    p.add_argument("--research", help="Research record name. Defaults to active.")
    p.set_defaults(handler=cmd_research_add_claim)

    p = research_sub.add_parser(
        "add-question",
        help="Add an open question to a research record.",
        description="Add an unresolved question that future research should answer.",
    )
    p.add_argument("question", help="Open question.")
    p.add_argument("--research", help="Research record name. Defaults to active.")
    p.set_defaults(handler=cmd_research_add_question)

    p = research_sub.add_parser(
        "summary",
        help="Show the active or named research record.",
        description="Show sources, claims, open questions, and decision for a research record.",
    )
    p.add_argument("name", nargs="?", help="Research record name. Defaults to active.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_research_summary)

    p = research_sub.add_parser(
        "close",
        help="Close a research record with a decision.",
        description="Mark a research record closed and record the resulting decision.",
    )
    p.add_argument("name", nargs="?", help="Research record name. Defaults to active.")
    p.add_argument("--decision", required=True, help="Decision or conclusion from the research.")
    p.set_defaults(handler=cmd_research_close)

    campaign = sub.add_parser(
        "campaign",
        help="Manage long-running task campaigns.",
        description=(
            "Manage a long-running campaign: decompose a goal into tasks, move "
            "each task through understand, design, build, judge, verify, and "
            "reflect, and record learnings that improve later tasks."
        ),
    )
    campaign_sub = campaign.add_subparsers(dest="campaign_command", metavar="ACTION", required=True)

    p = campaign_sub.add_parser(
        "start",
        help="Start a campaign and set it active.",
        description=(
            "Start a long-running campaign. If --tasks is omitted, Mythify "
            "generates a small default task list for the goal."
        ),
    )
    p.add_argument("goal", help="Campaign end goal.")
    p.add_argument(
        "--tasks",
        help=(
            "JSON array of task strings or objects with title and optional "
            "success_criteria."
        ),
    )
    p.add_argument("--name", help="Campaign name. Defaults to a slug of the goal.")
    p.add_argument("--success", help="Overall success criteria.")
    p.add_argument("--verify", help="Optional campaign-level verifier command.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_start)

    p = campaign_sub.add_parser(
        "list",
        help="List campaigns.",
        description="List campaigns with active marker, progress, and status.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_list)

    p = campaign_sub.add_parser(
        "status",
        help="Show the active or named campaign.",
        description="Show campaign progress, tasks, current phase, and recent learnings.",
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_status)

    p = campaign_sub.add_parser(
        "prompt",
        help="Render the next host prompt for a campaign.",
        description=(
            "Render a chat-ready prompt for the active or named campaign's "
            "current task and phase without mutating campaign state."
        ),
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_prompt)

    p = campaign_sub.add_parser(
        "watch",
        help="Poll a campaign and emit refreshed host prompts.",
        description=(
            "Poll the active or named campaign and emit the current host prompt. "
            "Defaults to one iteration; pass --max-iterations 0 for an explicit "
            "long-running watch managed by the host."
        ),
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--interval", type=float, default=5.0, help="Seconds between prompt refreshes.")
    p.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help="Prompt refresh count. Use 0 for an explicit unbounded watch.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_watch)

    p = campaign_sub.add_parser(
        "add-task",
        help="Append a task to the active or named campaign.",
        description="Append a task to a campaign with optional success criteria.",
    )
    p.add_argument("title", help="Task title.")
    p.add_argument("--criteria", help="Task success criteria.")
    p.add_argument("--campaign", help="Campaign name. Defaults to active.")
    p.set_defaults(handler=cmd_campaign_add_task)

    p = campaign_sub.add_parser(
        "advance",
        help="Advance the current task to the next campaign phase.",
        description=(
            "Advance the current task through understand, design, build, judge, "
            "verify, and reflect. Advancing from reflect completes the task and "
            "starts the next pending task."
        ),
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--result", required=True, help="Evidence, observation, or output from this phase.")
    p.set_defaults(handler=cmd_campaign_advance)

    p = campaign_sub.add_parser(
        "task",
        help="Set a campaign task status directly.",
        description="Set a task status directly. completed and failed require RESULT evidence.",
    )
    p.add_argument("id", help="Task id.")
    p.add_argument("status", help="One of: pending, in_progress, completed, failed, skipped.")
    p.add_argument("result", nargs="?", help="Evidence or failure description.")
    p.add_argument("--campaign", help="Campaign name. Defaults to active.")
    p.set_defaults(handler=cmd_campaign_task)

    p = campaign_sub.add_parser(
        "learn",
        help="Record a learning for the campaign.",
        description="Record a learning that should improve the current or next task cycle.",
    )
    p.add_argument("lesson", help="Learning to carry forward.")
    p.add_argument("--task", help="Task id. Defaults to the current task when present.")
    p.add_argument("--apply-next", action="store_true", help="Mark the learning as guidance for next tasks.")
    p.add_argument("--campaign", help="Campaign name. Defaults to active.")
    p.set_defaults(handler=cmd_campaign_learn)

    p = campaign_sub.add_parser(
        "stop",
        help="Stop the active or named campaign.",
        description="Mark a campaign stopped and clear the active pointer when it matches.",
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--reason", required=True, help="Why the campaign is being stopped.")
    p.set_defaults(handler=cmd_campaign_stop)

    p = sub.add_parser(
        "classify",
        help="Classify a task and recommend ceremony, verification, and fanout.",
        description=(
            "Classify TASK before planning. Returns task type, risk, ceremony "
            "level, verification strategy, model triage fit, and whether fanout is useful. This "
            "command does not require an initialized .mythify workspace."
        ),
    )
    p.add_argument("task", help="Task request or problem statement to classify.")
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    p.add_argument(
        "--triage",
        choices=TRIAGE_MODES,
        default="never",
        help=(
            "Run a fast model triage pass: never (default), auto when the gate "
            "is recommended or required, or always."
        ),
    )
    p.add_argument(
        "--triage-engine",
        choices=TRIAGE_ENGINES,
        default="",
        help=(
            "Fast triage engine. Defaults to MYTHIFY_TRIAGE_ENGINE or local "
            "auto-detection: claude-cli, codex-cli, cursor-agent, command."
        ),
    )
    p.add_argument(
        "--triage-model",
        default="",
        help="Fast triage model. Defaults to MYTHIFY_TRIAGE_MODEL or the engine default.",
    )
    p.add_argument(
        "--triage-timeout",
        type=float,
        default=120.0,
        help="Fast triage timeout in seconds.",
    )
    p.add_argument(
        "--platform",
        choices=PLATFORMS,
        default="auto",
        help=(
            "Host platform for model policy. Defaults to auto-detection; use "
            "codex-desktop, claude-desktop, or cursor-desktop when the host is known."
        ),
    )
    p.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default="auto",
        help=(
            "Overall effort preference for spawned model roles. Auto keeps "
            "triage cheap and scales worker or reviewer effort by risk."
        ),
    )
    p.add_argument(
        "--speed",
        choices=SPEED_LEVELS,
        default="auto",
        help=(
            "Overall speed preference for spawned model roles. Auto preserves "
            "host defaults; fast enables Codex fast mode where supported."
        ),
    )
    p.add_argument(
        "--session-model",
        default="",
        help=(
            "Current host session model for spawn ceiling policy. Defaults to "
            "MYTHIFY_SESSION_MODEL when set."
        ),
    )
    p.add_argument(
        "--spawn-ceiling",
        choices=SPAWN_CEILINGS,
        default="auto",
        help=(
            "Maximum spawned model tier relative to the session model. Auto "
            "uses MYTHIFY_SPAWN_CEILING or same_or_lower."
        ),
    )
    p.add_argument(
        "--reviewer-strength",
        choices=REVIEWER_STRENGTH_MODES,
        default="auto",
        help=(
            "Reviewer model strength relative to the session. Auto uses "
            "MYTHIFY_REVIEWER_STRENGTH or same_or_lower; allow_stronger is "
            "an explicit reviewer-only opt-in."
        ),
    )
    p.set_defaults(handler=cmd_classify, needs_state=False)

    host_model = sub.add_parser(
        "host-model",
        help="Record or inspect the intended host chat model.",
        description=(
            "Record a requested host chat model switch. Mythify uses the recorded "
            "target as the default session model for model policy and spawn ceiling "
            "checks, while the actual current chat model remains controlled by the host."
        ),
    )
    host_model_sub = host_model.add_subparsers(dest="host_model_command", metavar="ACTION", required=True)

    p = host_model_sub.add_parser(
        "switch",
        help="Record a requested host chat model switch.",
        description="Record a target host model and print host-specific switch guidance.",
    )
    p.add_argument("target_model", help="Target host model to record.")
    p.add_argument(
        "--platform",
        choices=PLATFORMS,
        default="auto",
        help="Host platform. Defaults to auto.",
    )
    p.add_argument(
        "--current-model",
        default="",
        help="Current host model when known, recorded for audit only.",
    )
    p.add_argument(
        "--thinking",
        choices=HOST_THINKING_LEVELS,
        default="auto",
        help="Requested host reasoning effort when the host supports it.",
    )
    p.add_argument(
        "--speed",
        choices=SPEED_LEVELS,
        default="auto",
        help="Requested host speed preference when the host supports it.",
    )
    p.add_argument("--reason", default="", help="Reason for the host switch.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_host_model_switch)

    p = host_model_sub.add_parser(
        "status",
        help="Show the recorded host model switch.",
        description="Show the recorded host model switch, if any.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_host_model_status)

    p = host_model_sub.add_parser(
        "clear",
        help="Clear the recorded host model switch.",
        description="Remove host-model.json from the Mythify state directory.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_host_model_clear)

    outcome = sub.add_parser(
        "outcome",
        help="Run outcome-driven loops with verifier and iteration budget.",
        description=(
            "Manage an outcome loop: define success, run verifier checks, track "
            "iteration budget, and tell the host whether to retry, stop, or report success."
        ),
    )
    outcome_sub = outcome.add_subparsers(dest="outcome_command", metavar="ACTION", required=True)

    p = outcome_sub.add_parser(
        "start",
        help="Start an outcome loop and set it active.",
        description="Start an outcome loop with a concrete verifier and iteration budget.",
    )
    p.add_argument("goal", help="Outcome goal.")
    p.add_argument("--success", required=True, help="Human-readable success criteria.")
    p.add_argument("--verify", required=True, help="Shell command that verifies the outcome.")
    p.add_argument("--metric", default="", help="Optional shell command that emits a metric.")
    p.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum verifier iterations before the outcome fails.",
    )
    p.add_argument(
        "--allowed-paths",
        default="",
        help="Comma-separated advisory path hints for host edits; not enforced as a sandbox.",
    )
    p.add_argument(
        "--visibility",
        choices=FANOUT_VISIBILITY_MODES,
        default="summary",
        help="How much loop progress the host should surface.",
    )
    p.add_argument("--name", help="Outcome name; defaults to a slug of the goal.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_start)

    p = outcome_sub.add_parser(
        "check",
        help="Run the active outcome verifier and update loop state.",
        description=(
            "Run the verifier and optional metric for the active or named outcome. "
            "Exits 0 when the outcome is verified, 2 when it is not yet met or failed."
        ),
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--notes", default="", help="Notes for this iteration.")
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_VERIFY_TIMEOUT,
        metavar="N",
        help="Timeout in seconds for each command.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_check)

    p = outcome_sub.add_parser(
        "status",
        help="Show the active or named outcome loop.",
        description="Show outcome status, iteration budget, verifier, and next action.",
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_status)

    p = outcome_sub.add_parser(
        "results",
        help="Show outcome loop iterations and final state.",
        description="Show all recorded verifier iterations for the active or named outcome.",
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_results)

    p = outcome_sub.add_parser(
        "stop",
        help="Stop the active or named outcome loop.",
        description="Mark an outcome stopped and clear the active pointer when it matches.",
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--reason", required=True, help="Why the loop is being stopped.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_stop)

    plan = sub.add_parser(
        "plan",
        help="Manage plans: create, add-step, list, show, switch, archive.",
        description="Manage plans: create, add-step, list, show, switch, archive.",
    )
    plan_sub = plan.add_subparsers(dest="plan_command", metavar="ACTION", required=True)

    p = plan_sub.add_parser(
        "create",
        help="Create a plan and set it active.",
        description=(
            "Create a plan and set it active. Without --steps the plan is empty "
            "and steps are added later with plan add-step."
        ),
    )
    p.add_argument("goal", help="What the plan should accomplish.")
    p.add_argument(
        "--steps",
        help=(
            "JSON array of step objects: "
            "[{\"title\": str, \"success_criteria\": str (optional)}]."
        ),
    )
    p.add_argument("--name", help="Plan name; defaults to a slug of the goal.")
    p.set_defaults(handler=cmd_plan_create)

    p = plan_sub.add_parser(
        "add-step",
        help="Append a step to the named or active plan.",
        description="Append a step (id = max + 1) to the named or active plan.",
    )
    p.add_argument("title", help="Step title.")
    p.add_argument("--criteria", help="Success criteria for the step.")
    p.add_argument("--plan", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_plan_add_step)

    p = plan_sub.add_parser(
        "list",
        help="List plans with the active marker, per-plan progress, and the archived count.",
        description="List plans with the active marker, per-plan progress, and the archived count.",
    )
    p.set_defaults(handler=cmd_plan_list)

    p = plan_sub.add_parser(
        "show",
        help="Show full detail of the named or active plan.",
        description="Show full detail of the named or active plan. Exits 1 if not found.",
    )
    p.add_argument("name", nargs="?", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_plan_show)

    p = plan_sub.add_parser(
        "switch",
        help="Set the active plan pointer.",
        description="Set the active plan pointer. Exits 1 if the plan is not found.",
    )
    p.add_argument("name", help="Plan name.")
    p.set_defaults(handler=cmd_plan_switch)

    p = plan_sub.add_parser(
        "archive",
        help="Move a plan file to plans/archive/ and clear the active pointer if needed.",
        description=(
            "Move the named or active plan file to plans/archive/, clearing the "
            "active pointer if it pointed there. On filename conflict in the "
            "archive, a timestamp is appended."
        ),
    )
    p.add_argument("name", nargs="?", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_plan_archive)

    p = sub.add_parser(
        "step",
        help="Update a step's status; completed requires RESULT plus passing verify run.",
        description=(
            "Update step ID to STATUS (pending, in_progress, completed, failed, "
            "skipped). completed and failed require the RESULT argument: evidence "
            "or a failure description. By default, completed also requires a "
            "passing verify run since the step started. Set "
            "MYTHIFY_REQUIRE_VERIFIED_STEP=0 only for legacy prose-only "
            "completion. Prints the next pending step afterward."
        ),
    )
    p.add_argument("id", help="Step id (1-based integer).")
    p.add_argument("status", help="One of: pending, in_progress, completed, failed, skipped.")
    p.add_argument(
        "result",
        nargs="?",
        help="Evidence or failure description; required for completed and failed.",
    )
    p.add_argument("--plan", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_step)

    memory = sub.add_parser(
        "memory",
        help="Persistent key-value memory: set, get, clear.",
        description="Persistent key-value memory: set, get, clear.",
    )
    memory_sub = memory.add_subparsers(dest="memory_command", metavar="ACTION", required=True)

    p = memory_sub.add_parser(
        "set",
        help="Store a memory entry; an existing key is overwritten.",
        description="Store a memory entry; an existing key is overwritten.",
    )
    p.add_argument("key", help="Entry key (unique).")
    p.add_argument("value", help="Entry value.")
    p.add_argument(
        "--category",
        choices=MEMORY_CATEGORIES,
        default=MEMORY_DEFAULT_CATEGORY,
        help="Entry category (default: {0}).".format(MEMORY_DEFAULT_CATEGORY),
    )
    p.set_defaults(handler=cmd_memory_set)

    p = memory_sub.add_parser(
        "get",
        help="Search memory: case-insensitive substring match over keys and values.",
        description=(
            "Search memory with a case-insensitive substring match over keys and "
            "values; --category narrows by category. Without QUERY, list all entries."
        ),
    )
    p.add_argument("query", nargs="?", help="Substring to match against keys and values.")
    p.add_argument("--category", choices=MEMORY_CATEGORIES, help="Filter by category.")
    p.set_defaults(handler=cmd_memory_get)

    p = memory_sub.add_parser(
        "clear",
        help="Remove one entry by KEY, or everything with --all.",
        description=(
            "Remove one entry by KEY, or everything with --all. With neither, "
            "refuse and exit 1: clearing requires an explicit target."
        ),
    )
    p.add_argument("key", nargs="?", help="Key of the entry to remove.")
    p.add_argument(
        "--all",
        dest="clear_all",
        action="store_true",
        help="Clear every memory entry.",
    )
    p.set_defaults(handler=cmd_memory_clear)

    lesson = sub.add_parser(
        "lesson",
        help="Record and list lessons (project store, or global with --global).",
        description="Record and list lessons (project store, or global with --global).",
    )
    lesson_sub = lesson.add_subparsers(dest="lesson_command", metavar="ACTION", required=True)

    p = lesson_sub.add_parser(
        "add",
        help="Record a lesson in the project store, or the global store with --global.",
        description="Record a lesson in the project store, or the global store with --global.",
    )
    p.add_argument("title", help="Lesson title.")
    p.add_argument("detail", help="Lesson detail.")
    p.add_argument("--tags", help="Comma-separated tags, for example: a,b.")
    p.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Store in the global lessons store (~/.mythify/lessons).",
    )
    p.set_defaults(handler=cmd_lesson_add)

    p = lesson_sub.add_parser(
        "list",
        help="List lessons labeled (project) or (global); filter with --tag and --scope.",
        description="List lessons labeled (project) or (global); filter with --tag and --scope.",
    )
    p.add_argument("--tag", help="Only lessons carrying this tag.")
    p.add_argument(
        "--scope",
        choices=("project", "global", "all"),
        default="all",
        help="Which store to list (default: all).",
    )
    p.set_defaults(handler=cmd_lesson_list)

    logs = sub.add_parser(
        "logs",
        help="Maintain Mythify jsonl logs.",
        description="Maintain Mythify jsonl logs without treating maintenance as verification.",
    )
    logs_sub = logs.add_subparsers(dest="logs_command", metavar="ACTION", required=True)

    p = logs_sub.add_parser(
        "compact",
        help="Archive and trim top-level verification and reflection logs.",
        description=(
            "Archive raw top-level verification and reflection logs, then keep "
            "only the most recent valid records in the active files."
        ),
    )
    p.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_LOG_COMPACT_KEEP,
        help="Number of recent valid records to keep per active log.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be compacted without writing files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_logs_compact)

    verify = sub.add_parser(
        "verify",
        help="Verification: run a command (executed) or record a claim (attested).",
        description="Verification: run a command (executed) or record a claim (attested).",
    )
    verify_sub = verify.add_subparsers(dest="verify_command", metavar="ACTION", required=True)

    p = verify_sub.add_parser(
        "run",
        help="Execute COMMAND through the shell and record an executed verification.",
        description=(
            "Execute COMMAND through the shell, capture exit code, duration, and "
            "output tails, append an executed verification record, and print the "
            "verdict. Exits 0 if verified (exit code 0), 2 if unverified."
        ),
    )
    p.add_argument("command", help="Shell command to execute.")
    p.add_argument("--claim", help="What this command verifies.")
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_VERIFY_TIMEOUT,
        metavar="N",
        help="Timeout in seconds (default: 300).",
    )
    p.set_defaults(handler=cmd_verify_run)

    p = verify_sub.add_parser(
        "claim",
        help="Record a self-reported (attested) verification; never counts as verified.",
        description=(
            "Append an attested verification record and print the [WARN] ATTESTED "
            "line. Attested entries are never marked verified."
        ),
    )
    p.add_argument("claim", help="The claim being attested.")
    p.add_argument("evidence", help="Why you believe the claim holds.")
    p.set_defaults(handler=cmd_verify_claim)

    p = sub.add_parser(
        "reflect",
        help="Record a structured reflection (JSON object or flags).",
        description=(
            "Record a structured reflection. Required keys: action, outcome "
            "(success, partial, failure), observation, next. A provided lesson is "
            "auto-recorded as a project lesson tagged auto-reflected. The JSON "
            "positional takes precedence over flags."
        ),
    )
    p.add_argument(
        "json",
        nargs="?",
        help=(
            "Reflection as a JSON object with keys action, outcome, observation, "
            "next, and optional root_cause and lesson."
        ),
    )
    p.add_argument("--action", help="What was attempted.")
    p.add_argument("--outcome", help="One of: success, partial, failure.")
    p.add_argument("--observation", help="What actually happened.")
    p.add_argument("--next", help="The next action to take.")
    p.add_argument("--root-cause", dest="root_cause", help="Root cause, if known.")
    p.add_argument("--lesson", help="Lesson to auto-record as a project lesson.")
    p.set_defaults(handler=cmd_reflect)

    p = sub.add_parser(
        "summary",
        help="Full session report: plans, memory, lessons, verification stats, reflections.",
        description=(
            "Full session report: plans and progress, memory count, project and "
            "global lesson counts, verification stats (executed passed, executed "
            "failed, attested count), and reflection count."
        ),
    )
    p.set_defaults(handler=cmd_summary)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.needs_state:
        return args.handler(args, None)
    state = resolve_state_dir()
    if state is None:
        fail(NO_WORKSPACE_MESSAGE)
        return 1
    return args.handler(args, state)


if __name__ == "__main__":
    sys.exit(main())
