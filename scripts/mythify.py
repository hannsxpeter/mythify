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
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mythify_parser import build_parser as build_cli_parser  # noqa: E402
from mythify_artifacts import (  # noqa: E402
    ARTIFACT_API_KEY_ENV,
    DEFAULT_SERVICE_URL,
    cmd_artifact_clean,
    cmd_artifact_inspect,
    cmd_artifact_probe,
)
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
    MODEL_PROFILE_INPUTS,
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
    append_chained_jsonl,
    append_jsonl,
    configure_durable_io,
    read_json,
    read_jsonl,
    read_jsonl_since,
    write_json_atomic,
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
from mythify_loopfit import cmd_loop_fit  # noqa: E402
from mythify_maps import (  # noqa: E402
    MAP_TICKET_MODES,
    MAP_TICKET_TYPES,
    cmd_map_claim,
    cmd_map_create,
    cmd_map_fog,
    cmd_map_list,
    cmd_map_promote,
    cmd_map_resolve,
    cmd_map_scope_out,
    cmd_map_show,
    cmd_map_ticket,
    cmd_map_verify,
    configure_map_store,
    frontier_tickets,
    get_active_map_slug,
    load_map,
    map_is_clear,
    map_next_action,
    open_tickets,
)
from mythify_evals import (  # noqa: E402
    cmd_eval_adopt,
    cmd_eval_baseline,
    cmd_eval_list,
    cmd_eval_propose,
    cmd_eval_reject,
    cmd_eval_scan,
    cmd_eval_show,
    cmd_eval_verify,
    configure_evals_store,
)
from mythify_log_compaction import cmd_logs_compact  # noqa: E402
from mythify_evidence_guard import noop_verifier_reason  # noqa: E402
from mythify_designs import (  # noqa: E402
    PLAN_ARCHETYPES,
    PLAN_PHASES,
    cmd_design_add_alternative,
    cmd_design_approve,
    cmd_design_create,
    cmd_design_show,
    configure_design_store,
    plan_step_extensions,
)
from mythify_lineage import (  # noqa: E402
    capture_lineage,
    cmd_lineage_attach,
    cmd_lineage_status,
    configure_lineage_store,
    inspect_lineage,
)
from mythify_quality import (  # noqa: E402
    REVIEW_STATUSES,
    cmd_quality_review_create,
    cmd_quality_review_show,
    configure_quality_store,
)
from mythify_verification_commands import (  # noqa: E402
    configure_verification_commands,
    extract_test_count,
)
from mythify_protocol import cmd_protocol_check  # noqa: E402,F401
from mythify_provenance import (  # noqa: E402
    current_verification_provenance,
    evidence_moved_since_run,
)
from mythify_runtime_helpers import (  # noqa: E402
    now_iso,
    now_stamp,
    redact_sensitive_output,
    slugify,
    tail_text,
    timestamp_after,
    timestamp_at_or_after,
    timestamp_sort_key,
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
from mythify_plan_import import (  # noqa: E402
    cmd_plan_import,
    configure_plan_import,
    project_root_for_workspace,
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
VERSION = "5.7.0"
NO_WORKSPACE_MESSAGE = (
    "[FAIL] No .mythify workspace found. Run: mythify init"
)
EVIDENCE_MESSAGE = (
    "[FAIL] Evidence required: pass a RESULT describing what proves this status."
)
VERIFIED_EVIDENCE_MESSAGE = (
    "[FAIL] Verified evidence required: strict evidence mode is enabled by "
    "default, but no passing executed 'verify run' with exit code 0 was recorded "
    "since this step started. When the step stores a verify_command, the recorded "
    "command must match it. Run the step's verifier first, or set "
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
DEFAULT_VERIFY_TIMEOUT = 300.0
DEFAULT_VERIFY_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_LOG_COMPACT_KEEP = 1000


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
    (state / "maps").mkdir(parents=True, exist_ok=True)
    (state / "reports").mkdir(parents=True, exist_ok=True)
    (state / "designs").mkdir(parents=True, exist_ok=True)
    (state / "reviews").mkdir(parents=True, exist_ok=True)
    (state / "verification-artifacts").mkdir(parents=True, exist_ok=True)
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
configure_design_store(
    now_iso_func=now_iso,
    slugify_func=slugify,
    write_json_atomic_func=write_json_atomic,
    write_text_atomic_func=_write_text_atomic,
    read_json_func=read_json,
    fail_func=fail,
    capture_lineage_func=capture_lineage,
)
configure_lineage_store(
    now_iso_func=now_iso,
    slugify_func=slugify,
    read_json_func=read_json,
    read_jsonl_func=read_jsonl,
    write_json_atomic_func=write_json_atomic,
    fail_func=fail,
)
configure_quality_store(
    now_iso_func=now_iso,
    slugify_func=slugify,
    write_json_atomic_func=write_json_atomic,
    read_json_func=read_json,
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


def execute_recorded_verification(
    state, command, claim, timeout=None, context=None, parents=None
):
    """Run COMMAND, append an executed verification record, return the record.

    When ``context`` (a plan/step_id/step_title/step_status dict) is given it is
    stamped on the record verbatim; otherwise the active plan's in-progress step
    is auto-detected. An explicit context lets ``plan verify`` scope evidence to
    a specific step of any plan, not just the active one.
    """
    lineage = capture_lineage(state, parents) if parents else None
    verification_id = "v-{0}-{1}".format(now_stamp(), uuid.uuid4().hex[:12])
    artifact_dir = state / "verification-artifacts" / verification_id
    run = run_shell_capture(
        command,
        timeout if timeout is not None else DEFAULT_VERIFY_TIMEOUT,
        artifact_dir=artifact_dir,
    )
    record = {
        "id": verification_id,
        "kind": "executed",
        "claim": claim,
        "command": command,
        "exit_code": run["exit_code"],
        "duration_seconds": run["duration_seconds"],
        "stdout_tail": run["stdout_tail"],
        "stderr_tail": run["stderr_tail"],
        "verified": run["verified"],
        "timestamp": now_iso(),
        "provenance": current_verification_provenance(VERSION, state=state),
    }
    artifacts = run.get("artifacts")
    if isinstance(artifacts, dict):
        normalized = {}
        for channel, item in artifacts.items():
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            try:
                entry["path"] = str(Path(entry["path"]).relative_to(state))
            except (KeyError, ValueError):
                continue
            normalized[channel] = entry
        record["artifacts"] = normalized
    if run.get("artifact_error"):
        record["artifact_error"] = run["artifact_error"]
    artifact_texts = []
    for item in (run.get("artifacts") or {}).values():
        try:
            artifact_texts.append(Path(item["path"]).read_text(encoding="utf-8"))
        except (KeyError, OSError):
            pass
    test_count = extract_test_count(
        run.get("stdout_tail", ""), run.get("stderr_tail", ""), *artifact_texts
    )
    if test_count is not None:
        record["test_count"] = test_count
    if lineage is not None:
        record["lineage"] = lineage
    record.update(context if context is not None else verification_step_context(state))
    append_chained_jsonl(state / "verifications.jsonl", record)
    return record


def attach_plan_lineage(state, slug, parents):
    plan = load_plan(state, slug)
    if plan is None:
        raise ValueError("plan not found while attaching lineage: " + slug)
    plan["lineage"] = capture_lineage(state, parents)
    save_plan(state, slug, plan)


configure_workflow_stores(
    now_iso_func=now_iso,
    slugify_func=slugify,
    fail_func=fail,
    find_existing_slug_by_name_func=find_existing_slug_by_name,
    execute_verification_func=execute_recorded_verification,
)
configure_plan_import(
    now_iso_func=now_iso,
    slugify_func=slugify,
    list_plan_slugs_func=list_plan_slugs,
    load_plan_func=load_plan,
    plan_path_func=plan_path,
    save_plan_func=save_plan,
    set_active_slug_func=set_active_slug,
    describe_next_pending_func=lambda plan: describe_next_pending(plan),
    fail_func=fail,
)
configure_map_store(
    now_iso_func=now_iso,
    slugify_func=slugify,
    fail_func=fail,
    find_existing_slug_by_name_func=find_existing_slug_by_name,
    execute_verification_func=execute_recorded_verification,
    build_default_plan_steps_func=build_default_plan_steps,
    create_plan_record_func=lambda state, goal, name, steps, source: create_plan_record(
        state, goal, name=name, steps=steps, source=source
    ),
    attach_plan_lineage_func=attach_plan_lineage,
    environ_map=os.environ,
)
configure_evals_store(
    now_iso_func=now_iso,
    fail_func=fail,
    execute_verification_func=execute_recorded_verification,
    load_lessons_func=load_lessons,
    environ_map=os.environ,
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
    active_map_slug = get_active_map_slug(state)
    active_map = load_map(state, active_map_slug)[1] if active_map_slug else None
    if active_map is not None:
        print(
            "Active map: {0} ({1} open, {2} on the frontier, {3} decided)".format(
                active_map_slug,
                len(open_tickets(active_map)),
                len(frontier_tickets(active_map)),
                len(active_map.get("decisions") or []),
            )
        )
        print("Destination: {0}".format(active_map.get("destination", "")))
        print("Map next: {0}".format(map_next_action(active_map)))
    else:
        print("Active map: none")
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
    slug, error = create_plan_record(
        state,
        args.goal,
        args.name,
        steps_input,
        archetype=args.archetype,
        design=args.design,
        parents=args.parent,
    )
    if error:
        fail(error)
        return 1
    plan = load_plan(state, slug)
    steps = plan.get("steps", []) if plan else []
    print("[OK] Created plan: {0} ({1} steps). Active plan set to {0}.".format(slug, len(steps)))
    if not steps:
        print("Plan has no steps yet. Add steps with: plan add-step TITLE [--criteria TEXT]")
    return 0


def create_plan_record(
    state, goal, name=None, steps=None, source=None, archetype="direct", design=None,
    parents=None
):
    """Write a new plan, set it active, and return (slug, error_message).

    Shared by `plan create` and `map promote`, so a promoted map produces the
    same plan shape as a hand-written one, plus a `source` provenance block.
    """
    if archetype not in PLAN_ARCHETYPES:
        return None, "[FAIL] Invalid plan archetype: {0}.".format(archetype)
    for item in steps or []:
        if not isinstance(item, dict) or not item.get("title"):
            return None, "[FAIL] Invalid steps: every step needs a non-empty \"title\"."
        try:
            plan_step_extensions(item, archetype)
        except ValueError as exc:
            return None, "[FAIL] Invalid step {0}: {1}.".format(item.get("title"), exc)
    base = slugify(name if name else goal) or "plan"
    slug = base
    suffix = 2
    while plan_path(state, slug).exists():
        slug = "{0}-{1}".format(base, suffix)
        suffix += 1
    stamp = now_iso()
    plan_steps = []
    for index, item in enumerate(steps or []):
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
            warn_noop_verifier(step["id"], verify_command)
        step.update(plan_step_extensions(item, archetype))
        plan_steps.append(step)
    plan = {
        "name": slug,
        "goal": goal,
        "steps": plan_steps,
        "created": stamp,
        "last_updated": stamp,
        "archetype": archetype,
    }
    if design:
        plan["design"] = str(design)
    if parents:
        try:
            plan["lineage"] = capture_lineage(state, parents)
        except ValueError as exc:
            return None, "[FAIL] Invalid lineage: {0}.".format(exc)
    if source is not None:
        plan["source"] = source
    save_plan(state, slug, plan)
    set_active_slug(state, slug)
    return slug, None


def warn_noop_verifier(step_id, verify_command):
    """Advisory pairing for the exit-code anchor: name the cheap way to win."""
    reason = noop_verifier_reason(verify_command)
    if reason:
        fail(
            "[WARN] Step {0} verify command looks like a no-op ({1}): {2}. "
            "It will satisfy the strict gate without checking anything.".format(
                step_id, reason, verify_command
            )
        )


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
        warn_noop_verifier(new_id, verify_command)
    try:
        step.update(
            plan_step_extensions(
                {
                    "phase": getattr(args, "phase", None),
                    "vertical_slice": getattr(args, "vertical_slice", None),
                },
                plan.get("archetype", "direct"),
            )
        )
    except ValueError as exc:
        fail("[FAIL] Invalid step: {0}.".format(exc))
        return 1
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
        step["verification_cursor"] = len(read_jsonl(state / "verifications.jsonl"))
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
        print("Next: mythify step {0} completed \"verify run exit 0\"".format(step_id))
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
    if isinstance(source, dict) and source.get("kind") == "map":
        decisions = source.get("decisions") or []
        out_of_scope = source.get("out_of_scope") or []
        print("Source: map {0} (destination settled before planning)".format(source.get("map", "unknown")))
        if decisions:
            print("Decisions carried from the map:")
            for decision in decisions:
                print(
                    "  - {0} ({1}): {2}".format(
                        decision.get("title", ""),
                        decision.get("ticket_id", ""),
                        decision.get("gist", ""),
                    )
                )
        if out_of_scope:
            print("Out of scope for this plan:")
            for entry in out_of_scope:
                print("  - {0}: {1}".format(entry.get("id", ""), entry.get("note", "")))
    elif isinstance(source, dict) and source.get("kind"):
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
        expected_command = str(step.get("verify_command") or "").strip()
        lower_bound = step.get("updated_at") or plan.get("created", "")
        cursor = step.get("verification_cursor")
        if isinstance(cursor, int) and cursor >= 0:
            records = read_jsonl(state / "verifications.jsonl")[cursor:]
        else:
            records = read_jsonl_since(state / "verifications.jsonl", lower_bound)
        satisfying = [
            record
            for record in records
            if record.get("kind") == "executed"
            and record.get("verified") is True
            and record.get("exit_code") == 0
            and (
                not expected_command
                or str(record.get("command") or "").strip() == expected_command
            )
            and verification_record_counts_for_step(record, slug, step_id, strict_context)
            and timestamp_at_or_after(
                record.get("timestamp", ""),
                lower_bound,
                verification_record_has_explicit_step_context(record, slug, step_id),
            )
        ]
        if not satisfying:
            fail(VERIFIED_EVIDENCE_MESSAGE)
            if strict_context:
                fail(STRICT_CONTEXT_NOTICE)
            return 1
        moved = evidence_moved_since_run(
            satisfying[-1], current_verification_provenance(VERSION, state=state)
        )
        if moved and strict_context:
            fail(
                "[FAIL] Stale evidence under strict context: {0}. The passing "
                "run predates the current source state; re-run the step's "
                "verifier, then complete.".format(moved)
            )
            return 1
        if moved:
            fail(
                "[WARN] The world moved since the passing run ({0}); the "
                "evidence may not describe the current source state. Re-run "
                "the verifier if in doubt.".format(moved)
            )
    elif args.status == "completed":
        # The legacy opt-out stays available, but never silently: the waiver is
        # stamped on the step so watchers can surface prose-only completions.
        step["strict_gate_waived"] = True
        fail(
            "[WARN] Strict gate waived: MYTHIFY_REQUIRE_VERIFIED_STEP=0 is set, "
            "so this completion carries prose-only evidence. The waiver is "
            "stamped on the step as strict_gate_waived."
        )
    if args.status == "in_progress":
        step["verification_cursor"] = len(read_jsonl(state / "verifications.jsonl"))
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


def _persist_capture_artifacts(stdout_path, stderr_path, artifact_dir, max_output_bytes):
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    remaining = max_output_bytes
    results = {}
    for channel, source in (("stdout", stdout_path), ("stderr", stderr_path)):
        source_size = _file_size(source)
        try:
            with source.open("rb") as handle:
                raw = handle.read(max(0, remaining))
        except OSError:
            raw = b""
        remaining = max(0, remaining - len(raw))
        decoded = raw.decode("utf-8", errors="replace")
        redacted = redact_sensitive_output(decoded)
        destination = artifact_dir / (channel + ".txt")
        _write_text_atomic(destination, redacted)
        encoded = redacted.encode("utf-8")
        results[channel] = {
            "path": str(destination),
            "bytes": len(encoded),
            "source_bytes": source_size,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "truncated": source_size > len(raw),
            "redacted": redacted != decoded,
        }
    return results


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


def terminate_process_tree(process):
    if process is None:
        return True
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
            if result.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return True
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except OSError:
        pass
    return False


def run_shell_capture(command, timeout, max_output_bytes=None, artifact_dir=None):
    max_output_bytes = (
        verify_max_output_bytes() if max_output_bytes is None else max_output_bytes
    )
    started = datetime.now(timezone.utc)
    timed_out = False
    output_limit_exceeded = False
    containment_failed = False
    spawn_error = None
    exit_code = None
    artifacts = None
    artifact_error = None
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
                    start_new_session=(os.name != "nt"),
                )
            except OSError as exc:
                process = None
                spawn_error = str(exc)
            if process is not None:
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        containment_failed = not terminate_process_tree(process)
                        break
                    total_size = _file_size(stdout_path) + _file_size(stderr_path)
                    if total_size > max_output_bytes:
                        output_limit_exceeded = True
                        containment_failed = not terminate_process_tree(process)
                        break
                    time.sleep(0.02)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    containment_failed = (
                        not terminate_process_tree(process) or containment_failed
                    )
                    process.wait(timeout=1)
                exit_code = process.returncode
        stdout_tail = _read_file_tail_text(stdout_path, redactor=redact_sensitive_output)
        stderr_tail = _read_file_tail_text(stderr_path, redactor=redact_sensitive_output)
        total_size = _file_size(stdout_path) + _file_size(stderr_path)
        if artifact_dir is not None:
            try:
                artifacts = _persist_capture_artifacts(
                    stdout_path, stderr_path, artifact_dir, max_output_bytes
                )
            except OSError as exc:
                artifact_error = redact_sensitive_output(str(exc))
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
    if containment_failed:
        stderr_tail = _append_stderr_notice(
            stderr_tail,
            "(process-tree containment could not be confirmed; the parent was killed)",
        )
    result = {
        "command": command,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "verified": exit_code == 0 and not timed_out and not output_limit_exceeded,
        "timed_out": timed_out,
        "output_limit_exceeded": output_limit_exceeded,
        "containment_failed": containment_failed,
    }
    if artifacts is not None:
        result["artifacts"] = artifacts
    if artifact_error is not None:
        result["artifact_error"] = artifact_error
    return result


configure_outcome_loops(
    find_existing_slug_by_name_func=find_existing_slug_by_name,
    now_iso_func=now_iso,
    slugify_func=slugify,
    run_shell_capture_func=run_shell_capture,
    verification_step_context_func=verification_step_context,
    verification_provenance_func=lambda state: current_verification_provenance(
        VERSION, state=state
    ),
    fail_func=fail,
)

configure_verification_commands(
    disabled_message=VERIFY_RUN_DISABLED_MESSAGE,
    execute_recorded_verification_func=execute_recorded_verification,
    fail_func=fail,
    now_iso_func=now_iso,
    verification_step_context_func=verification_step_context,
    append_chained_jsonl_func=append_chained_jsonl,
    append_jsonl_func=append_jsonl,
    reflect_outcomes=REFLECT_OUTCOMES,
    write_lesson_func=write_lesson,
)


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
        lineage = inspect_lineage(state, plan.get("lineage"))
        print("    lineage: {0}".format(lineage["status"]))
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
    inspect_lineage_func=inspect_lineage,
    fail_func=fail,
    mythify_version=VERSION,
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
