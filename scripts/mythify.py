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

from mythify_parser import build_parser as build_cli_parser  # noqa: E402
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
from mythify_plan_horizon import (  # noqa: E402
    build_default_plan_steps,
    env_plan_horizon,
    parse_plan_horizon,
)

from mythify_outcomes import (  # noqa: E402
    cmd_outcome_check,
    cmd_outcome_run,
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
    cmd_prompt_packet,
    cmd_route,
    configure_prompt_router,
)
from mythify_workflows import (  # noqa: E402
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
from mythify_godfiles import (  # noqa: E402
    GODAUDITS_DIR_NAME,
    GODPLANS_DIR_NAME,
    find_godaudits_file,
    find_godplans_file,
    load_god_artifact,
)
from mythify_views import (  # noqa: E402
    DEFAULT_REPORT_RECENT,
    REPORT_FORMATS,
    REPORT_SINCE_MODES,
    build_verification_history_view,
    build_work_report,
    cmd_background,
    cmd_dashboard,
    cmd_harness,
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
VERSION = "4.1.0"
REPO_ROOT = SCRIPT_DIR.parent
PROTOCOL_SOURCE_SHA256 = "2a3b9ebe62efc0c6f5d4a4d7f62b147acdaf38fc24f160e64a432e4fb02a1df2"
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
STRICT_CONTEXT_NOTICE = (
    "This plan was imported with strict step context: only verifications "
    "recorded while the step was in_progress count. Mark the step in_progress, "
    "run its verify command, then complete it."
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


def execute_recorded_verification(state, command, claim, timeout=None, context=None):
    """Run COMMAND, append an executed verification record, return the record.

    When ``context`` (a plan/step_id/step_title/step_status dict) is given it is
    stamped on the record verbatim; otherwise the active plan's in-progress step
    is auto-detected. An explicit context lets ``plan verify`` scope evidence to
    a specific step of any plan, not just the active one.
    """
    run = run_shell_capture(command, timeout if timeout is not None else DEFAULT_VERIFY_TIMEOUT)
    record = {
        "kind": "executed",
        "claim": claim,
        "command": command,
        "exit_code": run["exit_code"],
        "duration_seconds": run["duration_seconds"],
        "stdout_tail": run["stdout_tail"],
        "stderr_tail": run["stderr_tail"],
        "verified": run["verified"],
        "timestamp": now_iso(),
    }
    record.update(context if context is not None else verification_step_context(state))
    append_jsonl(state / "verifications.jsonl", record)
    return record


configure_workflow_stores(
    now_iso_func=now_iso,
    slugify_func=slugify,
    fail_func=fail,
    find_existing_slug_by_name_func=find_existing_slug_by_name,
    execute_verification_func=execute_recorded_verification,
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


def verification_record_counts_for_step(record, slug, step_id, strict_context):
    if strict_context:
        return verification_record_has_explicit_step_context(record, slug, step_id)
    return verification_record_matches_step(record, slug, step_id)


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
    line = "Next pending: {0}. {1} (criteria: {2})".format(
        step.get("id"), step.get("title"), criteria
    )
    if step.get("verify_command"):
        line += "\nNext verify: {0}".format(step["verify_command"])
    return line


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


# ---------------------------------------------------------------------------
# Loop-fit advisory
# ---------------------------------------------------------------------------
# Read-only decision support: should a task be run as a bounded self-driving
# loop, a host-supervised loop, or done directly? Structured as ordered gates
# (evaluate fit before recommending) and grounded in whether an objective,
# machine-checkable gate exists at all: without one, a loop has nothing to stop
# on, so the honest answer is "do it directly."

LOOPFIT_VERIFY_TERMS = (
    "test", "tests", "build", "lint", "passes", "pass", "compile", "typecheck",
    "type check", "e2e", "ci", "coverage", "benchmark", "smoke", "exit 0",
    "assert", "regression", "check that", "verify",
)
LOOPFIT_RECUR_TERMS = (
    "every", "each", "recurring", "recur", "nightly", "daily", "weekly", "hourly",
    "regenerate", "re-run", "rerun", "keep going", "continuously", "watch",
    "monitor", "until", "per pr", "each pr", "batch", "for all", "sweep",
    "repeatedly", "for every", "on every",
)
LOOPFIT_JUDGMENT_TERMS = (
    "design", "ux", "aesthetic", "subjective", "judgment", "judgement", "decide",
    "tradeoff", "trade-off", "opinion", "creative", "wording", "prioritize",
    "which is better", "looks good", "beautiful", "brainstorm", "explore",
    "what should", "recommend", "advise",
)
LOOPFIT_CHECK_FILES = (
    "pyproject.toml", "setup.py", "tox.ini", "pytest.ini", "package.json",
    "Makefile", "makefile", "Cargo.toml", "go.mod", "build.gradle", "pom.xml",
    "tests", "test",
)


def _loopfit_has_any(text, terms):
    # Normalize punctuation to spaces so "tests." matches "tests"; the padded
    # word-boundary join still keeps "latest" from matching "test".
    normalized = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())
    lowered = " {0} ".format(" ".join(normalized.split()))
    matches = []
    for term in terms:
        needle = " {0} ".format(" ".join(term.split()))
        if needle in lowered:
            matches.append(term)
    return matches


def project_has_runnable_check(root):
    for name in LOOPFIT_CHECK_FILES:
        if (root / name).exists():
            return True
    return False


def loopfit_project_context():
    """Return (project_root, is_git_repo) for the directory the work happens in.

    Resolved from the current working directory, never from where .mythify state
    lives, so loop-fit stays strictly read-only and assesses the real project.
    """
    try:
        run = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return Path.cwd(), False
    if run.returncode == 0 and run.stdout.strip():
        return Path(run.stdout.strip()), True
    return Path.cwd(), False


def assess_loop_fit(task, is_git_repo, has_check):
    """Return a loop-fit recommendation for a task. Pure and deterministic.

    The done-condition must come from the task itself (verify_hits); a runnable
    check merely existing in the repo (has_check) is context, not proof that
    THIS task is checkable, so it can lift a task to supervised but never to an
    unattended loop.
    """
    verify_hits = _loopfit_has_any(task, LOOPFIT_VERIFY_TERMS)
    recur_hits = _loopfit_has_any(task, LOOPFIT_RECUR_TERMS)
    judgment_hits = _loopfit_has_any(task, LOOPFIT_JUDGMENT_TERMS)
    automated_verification = bool(verify_hits)
    reproduction_env = bool(is_git_repo)
    recurring = bool(recur_hits)
    needs_judgment = bool(judgment_hits)
    criteria = {
        "automated_verification": automated_verification,
        "recurring": recurring,
        "reproduction_env": reproduction_env,
        "needs_human_judgment": needs_judgment,
    }
    # Ordered gates: fail the cheapest disqualifier first.
    if needs_judgment and not (automated_verification and recurring):
        recommendation = "direct"
        reason = (
            "The goal leans on human judgment. Automate only the checkable parts; "
            "keep the judgment call in the chat."
        )
    elif not automated_verification:
        if has_check:
            recommendation = "supervised"
            reason = (
                "The task names no explicit check, but this repo has runnable "
                "checks. Wrap it in a verifier-gated plan (plan add-step "
                "--verify), not an unattended loop."
            )
        else:
            recommendation = "direct"
            reason = (
                "No machine-checkable done-condition is evident. A loop has "
                "nothing to stop on without an objective gate; do it directly "
                "and record evidence with verify run if a check exists, else "
                "verify claim."
            )
    elif recurring and reproduction_env and not needs_judgment:
        recommendation = "loop"
        reason = (
            "Recurring, machine-checkable, and runs in a reproduction environment: "
            "worth a bounded self-driving loop."
        )
    else:
        recommendation = "supervised"
        reason = (
            "Machine-checkable but one-off or judgment-adjacent: run a "
            "verifier-gated plan or a host-supervised outcome loop, not an "
            "unattended one."
        )
    quoted = shlex.quote(str(task or "").strip() or "task")
    if recommendation == "loop":
        suggested = (
            "python3 scripts/mythify.py outcome start {0} --success DEFINE "
            "--verify DEFINE_CHECK --agent DEFINE_AGENT --max-iterations 5 "
            "--max-cost 100 --escalate-after 3, then outcome run".format(quoted)
        )
    elif recommendation == "supervised":
        suggested = (
            "python3 scripts/mythify.py plan create {0} "
            "--steps '[{{\"title\": \"...\", \"verify_command\": \"DEFINE_CHECK\"}}]', "
            "then plan verify 1; or outcome start ... --verify ... then outcome check".format(quoted)
        )
    else:
        suggested = (
            "Do it directly in the chat. Run verify run if an executable check "
            "exists, else record a verify claim."
        )
    return {
        "kind": "loop_fit",
        "task": str(task or ""),
        "recommendation": recommendation,
        "reason": reason,
        "criteria": criteria,
        "signals": {
            "verify_terms": verify_hits,
            "recurring_terms": recur_hits,
            "judgment_terms": judgment_hits,
            "has_runnable_check": has_check,
            "is_git_repo": is_git_repo,
        },
        "suggested_next": suggested,
        "guardrail": (
            "loop-fit is read-only decision support; it does not run anything, "
            "start a loop, or record evidence."
        ),
    }


def format_loop_fit(payload):
    lines = [
        "[OK] Loop-fit: {0}".format(payload["recommendation"]),
        "Reason: {0}".format(payload["reason"]),
        "Criteria:",
    ]
    labels = {
        "automated_verification": "task names a machine-checkable done-condition",
        "recurring": "work recurs / repeats",
        "reproduction_env": "reproduction environment (git repo)",
        "needs_human_judgment": "needs human judgment",
    }
    for key, label in labels.items():
        mark = "[x]" if payload["criteria"][key] else "[ ]"
        lines.append("  {0} {1}".format(mark, label))
    if payload["signals"].get("has_runnable_check"):
        lines.append("  (note) the repo has runnable checks")
    lines.append("Suggested next: {0}".format(payload["suggested_next"]))
    lines.append("Guardrail: {0}".format(payload["guardrail"]))
    return "\n".join(lines)


def cmd_loop_fit(args, _state):
    root, is_git = loopfit_project_context()
    payload = assess_loop_fit(
        args.task,
        is_git,
        project_has_runnable_check(root),
    )
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(format_loop_fit(payload))
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
        if getattr(args, "horizon", None) is not None:
            fail("[FAIL] --horizon can only be used when --steps is omitted.")
            return 1
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
    else:
        try:
            horizon = (
                parse_plan_horizon(args.horizon, "--horizon")
                if getattr(args, "horizon", None) is not None
                else env_plan_horizon()
            )
        except ValueError as exc:
            fail("[FAIL] {0}".format(exc))
            return 1
        if horizon is not None:
            steps_input = build_default_plan_steps(horizon)
    base = slugify(args.name if args.name else args.goal) or "plan"
    slug = base
    suffix = 2
    while plan_path(state, slug).exists():
        slug = "{0}-{1}".format(base, suffix)
        suffix += 1
    stamp = now_iso()
    steps = []
    for index, item in enumerate(steps_input):
        step = {
            "id": index + 1,
            "title": str(item["title"]),
            "success_criteria": str(item.get("success_criteria", "")),
            "status": "pending",
            "result": None,
        }
        verify_command = str(item.get("verify_command", "")).strip()
        if verify_command:
            step["verify_command"] = verify_command
        steps.append(step)
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


def project_root_for_workspace(state):
    return state.parent if state.name == WORKSPACE_DIR_NAME else Path.cwd()


def _existing_import_slug(state, source_kind, source_path):
    for slug in list_plan_slugs(state):
        plan = load_plan(state, slug)
        if plan is None:
            continue
        source = plan.get("source")
        if (
            isinstance(source, dict)
            and source.get("kind") == source_kind
            and source.get("path") == source_path
        ):
            return slug
    return None


def _resolve_import_artifact(args, root):
    """Resolve (path, source_kind) for plan import; returns (None, error)."""
    source = args.source
    if args.path:
        path = Path(args.path).expanduser()
        if not path.is_file():
            return None, "[FAIL] Artifact not found: {0}".format(path)
    else:
        plan_file = find_godplans_file(root)
        audit_file = find_godaudits_file(root)
        if source == "godplans":
            path = plan_file
        elif source == "godaudits":
            path = audit_file
        elif plan_file is not None and audit_file is not None:
            return None, (
                "[FAIL] Found both a godplans plan and a godaudits audit. "
                "Pass a PATH or --source to choose one."
            )
        else:
            path = plan_file or audit_file
            source = "godplans" if plan_file is not None else source
            source = "godaudits" if plan_file is None and audit_file is not None else source
        if path is None:
            return None, (
                "[FAIL] No godplans or godaudits artifact found under {0}. Run "
                "the /godplans or /godaudits skill first, or pass a PATH.".format(root)
            )
    if source is None:
        lowered = str(path).lower()
        if "plan" in path.name.lower() or GODPLANS_DIR_NAME in lowered:
            source = "godplans"
        elif "audit" in path.name.lower() or GODAUDITS_DIR_NAME in lowered:
            source = "godaudits"
        else:
            return None, (
                "[FAIL] Cannot infer the artifact kind from {0}. Pass --source "
                "godplans or --source godaudits.".format(path)
            )
    return (path, source), None


def cmd_plan_import(args, state):
    root = project_root_for_workspace(state)
    resolved, error = _resolve_import_artifact(args, root)
    if error:
        fail(error)
        return 1
    path, source = resolved
    digest = load_god_artifact(path, source)
    if digest["status"] in ("unreadable", "unrecognized"):
        fail(
            "[FAIL] Cannot import {0}: {1} ({2}).".format(
                path, digest["status"], digest.get("detail", "")
            )
        )
        return 1
    live_tasks = [task for task in digest["tasks"] if not task["superseded"]]
    if not live_tasks:
        fail("[FAIL] No importable tasks found in {0}.".format(path))
        return 1
    existing = _existing_import_slug(state, source, str(path))
    if existing and not args.name:
        fail(
            "[FAIL] {0} was already imported as plan {1}. Archive that plan "
            "first, or pass --name to import a fresh copy.".format(path.name, existing)
        )
        return 1
    base = slugify(args.name) if args.name else (
        (slugify(digest.get("name") or "") or "imported") + "-" + source
    )
    slug = base or "imported-" + source
    suffix = 2
    while plan_path(state, slug).exists():
        slug = "{0}-{1}".format(base, suffix)
        suffix += 1
    stamp = now_iso()
    steps = []
    for index, task in enumerate(live_tasks):
        step = {
            "id": index + 1,
            "title": "{0} {1}".format(task["id"], task["title"]).strip(),
            "success_criteria": task.get("acceptance") or "verify command passes",
            "status": "completed" if task["checked"] else "pending",
            "result": (
                "imported: checkbox already checked in {0}".format(path.name)
                if task["checked"]
                else None
            ),
            "source_id": task["id"],
            "verify_command": task.get("verify_command", ""),
            "wave": task.get("wave", ""),
            "phase": task.get("phase_title", ""),
            "updated_at": stamp,
        }
        if task.get("depends_on"):
            step["depends_on"] = task["depends_on"]
        if task.get("fixes"):
            step["fixes"] = task["fixes"]
        steps.append(step)
    plan = {
        "name": slug,
        "goal": "Execute {0} tasks from {1}".format(source, path.name),
        "steps": steps,
        "created": stamp,
        "last_updated": stamp,
        "strict_context": True,
        "source": {
            "kind": source,
            "path": str(path),
            "version": digest.get("plan_version") or digest.get("audit_version"),
            "imported_at": stamp,
        },
    }
    save_plan(state, slug, plan)
    set_active_slug(state, slug)
    done = sum(1 for step in steps if step["status"] == "completed")
    print(
        "[OK] Imported {0} tasks from {1} into plan {2} ({3} already completed). "
        "Active plan set to {2}.".format(len(steps), path.name, slug, done)
    )
    if digest.get("counter_drift"):
        print(
            "[WARN] Frontmatter counters disagree with the checkboxes in {0}; "
            "the checkboxes were trusted.".format(path.name)
        )
    print(
        "Checkbox flips in the artifact stay with the executing agent per its "
        "embedded rules; Mythify holds the evidence trail."
    )
    print(describe_next_pending(plan))
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
    step = {
        "id": new_id,
        "title": args.title,
        "success_criteria": args.criteria or "",
        "status": "pending",
        "result": None,
    }
    verify_command = (getattr(args, "verify", None) or "").strip()
    if verify_command:
        step["verify_command"] = verify_command
    plan["steps"].append(step)
    plan["last_updated"] = now_iso()
    save_plan(state, slug, plan)
    print("[OK] Added step {0} to plan {1}: {2}".format(new_id, slug, args.title))
    if verify_command:
        print("     verify: {0}".format(verify_command))
    return 0


def cmd_plan_verify(args, state):
    """Run a step's own verify command and record the evidence scoped to it.

    This is the executable half of the evidence spine: a step that carries a
    verify_command can prove its own definition of done. On success the step's
    strict-evidence gate is satisfied, so `step ID completed` will pass.
    """
    if os.environ.get("MYTHIFY_DISABLE_RUN") == "1":
        fail(VERIFY_RUN_DISABLED_MESSAGE)
        return 2
    try:
        step_id = int(args.id)
    except ValueError:
        fail("[FAIL] Invalid step id: {0}. Step ids are integers.".format(args.id))
        return 1
    slug = target_plan_slug(state, args.plan)
    if slug is None:
        fail("[FAIL] No active plan. Create one with: plan create GOAL")
        return 1
    plan = load_plan(state, slug)
    if plan is None:
        fail("[FAIL] Plan not found: {0}".format(slug))
        return 1
    step = next((candidate for candidate in plan["steps"] if candidate.get("id") == step_id), None)
    if step is None:
        fail("[FAIL] Step {0} not found in plan {1}.".format(step_id, slug))
        return 1
    command = (step.get("verify_command") or "").strip()
    if not command:
        fail(
            "[FAIL] Step {0} has no verify_command. Add one with "
            "plan add-step --verify, or run verify run manually.".format(step_id)
        )
        return 1
    if step.get("status") != "completed":
        step["status"] = "in_progress"
        step["updated_at"] = now_iso()
        plan["last_updated"] = step["updated_at"]
        save_plan(state, slug, plan)
    context = {
        "plan": slug,
        "step_id": step_id,
        "step_title": step.get("title"),
        "step_status": "in_progress",
    }
    claim = "step {0}: {1}".format(step_id, step.get("title", ""))
    record = execute_recorded_verification(state, command, claim, args.timeout, context)
    if record["verified"]:
        print(
            "[OK] VERIFIED step {0}: {1} (exit 0, {2:.2f}s)".format(
                step_id, command, record["duration_seconds"]
            )
        )
        print("Next: python3 scripts/mythify.py step {0} completed \"verify run exit 0\"".format(step_id))
        return 0
    print(
        "[FAIL] UNVERIFIED step {0}: {1} (exit {2}, {3:.2f}s)".format(
            step_id, command, record["exit_code"], record["duration_seconds"]
        )
    )
    if record["stdout_tail"]:
        print("--- stdout (tail) ---")
        print(record["stdout_tail"])
    if record["stderr_tail"]:
        print("--- stderr (tail) ---")
        print(record["stderr_tail"])
    return 2


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
    source = plan.get("source")
    if isinstance(source, dict) and source.get("kind"):
        print(
            "Source: {0} artifact {1} (imported {2})".format(
                source.get("kind"),
                source.get("path", "unknown"),
                source.get("imported_at", "unknown"),
            )
        )
    print("Created: {0}".format(plan.get("created", "")))
    print("Last updated: {0}".format(plan.get("last_updated", "")))
    print("Progress: {0}/{1} completed".format(done, total))
    if plan.get("steps"):
        print("Steps:")
        for step in plan["steps"]:
            criteria = step.get("success_criteria") or "none"
            print("{0} (criteria: {1})".format(format_step_line(step), criteria))
            if step.get("verify_command"):
                print("        verify: {0}".format(step["verify_command"]))
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
        strict_context = bool(plan.get("strict_context"))
        lower_bound = step.get("updated_at") or plan.get("created", "")
        records = read_jsonl_since(state / "verifications.jsonl", lower_bound)
        satisfied = any(
            record.get("kind") == "executed"
            and record.get("verified") is True
            and verification_record_counts_for_step(record, slug, step_id, strict_context)
            and timestamp_at_or_after(
                record.get("timestamp", ""),
                lower_bound,
                verification_record_has_explicit_step_context(record, slug, step_id),
            )
            for record in records
        )
        if not satisfied:
            fail(VERIFIED_EVIDENCE_MESSAGE)
            if strict_context:
                fail(STRICT_CONTEXT_NOTICE)
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


def _read_file_tail_text(path, char_limit=TAIL_CHARS, redactor=None):
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    byte_limit = max(char_limit * 4, 1024)
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, size - byte_limit))
            window = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    # Redact the wider read window before the final char slice so a secret that
    # straddles the char boundary is caught whole, matching the Node order.
    if redactor is not None:
        window = redactor(window)
    return window[-char_limit:]


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
        stdout_tail = _read_file_tail_text(stdout_path, redactor=redact_sensitive_output)
        stderr_tail = _read_file_tail_text(stderr_path, redactor=redact_sensitive_output)
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
    record = execute_recorded_verification(state, args.command, args.claim, args.timeout)
    exit_code = record["exit_code"]
    stdout_tail = record["stdout_tail"]
    stderr_tail = record["stderr_tail"]
    label = args.claim or args.command
    if record["verified"]:
        print(
            "[OK] VERIFIED: {0} (exit {1}, {2:.2f}s)".format(
                label,
                exit_code,
                record["duration_seconds"],
            )
        )
        return 0
    print(
        "[FAIL] UNVERIFIED: {0} (exit {1}, {2:.2f}s)".format(
            label,
            exit_code,
            record["duration_seconds"],
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

# Parser construction lives in mythify_parser.py so the entrypoint stays small.


def main(argv=None):
    parser = build_cli_parser(globals())
    args = parser.parse_args(argv)
    if args.needs_state == "optional":
        return args.handler(args, resolve_state_dir())
    if not args.needs_state:
        return args.handler(args, None)
    state = resolve_state_dir()
    if state is None:
        fail(NO_WORKSPACE_MESSAGE)
        return 1
    return args.handler(args, state)


if __name__ == "__main__":
    sys.exit(main())
