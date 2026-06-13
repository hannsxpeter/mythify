#!/usr/bin/env python3
"""Mythify v2 command line interface.

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
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_DIR_NAME = ".mythify"
REPO_ROOT = Path(__file__).resolve().parent.parent
OPERATION_REGISTRY_PATH = REPO_ROOT / "protocol" / "operation-registry.json"
PROTOCOL_SOURCE_SHA256 = "2ae980ce81f378aa3871b7430b719b7834069ac026046f5ea105b466bbed0f28"
PROTOCOL_HASH_PREFIX = "<!-- Mythify protocol-sha256: "
PROTOCOL_COPY_CANDIDATES = ("CLAUDE.md", "AGENTS.md", ".cursorrules")
NO_WORKSPACE_MESSAGE = (
    "[FAIL] No .mythify workspace found. Run: python3 scripts/mythify.py init"
)
EVIDENCE_MESSAGE = (
    "[FAIL] Evidence required: pass a RESULT describing what proves this status."
)
VERIFIED_EVIDENCE_MESSAGE = (
    "[FAIL] Verified evidence required: MYTHIFY_REQUIRE_VERIFIED_STEP=1 but no "
    "passing 'verify run' was recorded since this step started. Run 'verify run' "
    "with a passing check first."
)
VERIFY_RUN_DISABLED_MESSAGE = (
    "[FAIL] verify run is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded. Unset it to enable execution, or use "
    "verify claim to record a self-reported attestation."
)
STEP_STATUSES = ("pending", "in_progress", "completed", "failed", "skipped")
OUTCOME_STATUSES = ("active", "succeeded", "failed", "stopped")
STATUS_ICONS = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
    "failed": "[!]",
    "skipped": "[~]",
}


def load_operation_registry():
    with OPERATION_REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


OPERATION_REGISTRY = load_operation_registry()
MEMORY_OPERATION_REGISTRY = OPERATION_REGISTRY["surfaces"]["memory"]
MEMORY_CATEGORIES = tuple(MEMORY_OPERATION_REGISTRY["categories"])
MEMORY_DEFAULT_CATEGORY = MEMORY_OPERATION_REGISTRY["default_category"]
MEMORY_CLEAR_CLI_REFUSAL = (
    MEMORY_OPERATION_REGISTRY["operations"]["memory_clear"]["cli"]["refusal"]
)
REFLECT_OUTCOMES = ("success", "partial", "failure")
TAIL_CHARS = 4000
DEFAULT_VERIFY_TIMEOUT = 300.0
TRIAGE_ENGINES = ("claude-cli", "codex-cli", "cursor-agent", "command")
TRIAGE_MODES = ("never", "auto", "always")
PLATFORMS = (
    "auto",
    "unknown",
    "codex-desktop",
    "codex-cli",
    "claude-desktop",
    "claude-code",
    "cursor-desktop",
    "cursor-agent",
)
EFFORT_LEVELS = ("auto", "low", "medium", "high")
SPEED_LEVELS = ("auto", "standard", "fast")
HOST_THINKING_LEVELS = ("auto", "low", "medium", "high", "xhigh", "max")
SPAWN_CEILINGS = ("auto", "lower_only", "same_or_lower", "allow_stronger")
FANOUT_VISIBILITY_MODES = ("auto", "quiet", "summary", "verbose", "threaded")
HOST_MODEL_STATE_FILE = "host-model.json"
MODEL_TIERS = ("unknown", "small", "fast", "standard", "strong", "frontier")
MODEL_TIER_RANK = {
    "unknown": 0,
    "small": 1,
    "fast": 2,
    "standard": 3,
    "strong": 4,
    "frontier": 5,
}
HOST_PROFILE_RANK = {
    "fast": MODEL_TIER_RANK["fast"],
    "standard": MODEL_TIER_RANK["standard"],
    "strong": MODEL_TIER_RANK["frontier"],
}
HOST_MODEL_DEFAULTS = {
    "codex-desktop": {
        "fast": "gpt-5.4-mini",
        "standard": "gpt-5.4",
        "strong": "gpt-5.5",
    },
    "codex-cli": {
        "fast": "gpt-5.4-mini",
        "standard": "gpt-5.4",
        "strong": "gpt-5.5",
    },
    "claude-desktop": {
        "fast": "haiku",
        "standard": "sonnet",
        "strong": "opus",
    },
    "claude-code": {
        "fast": "haiku",
        "standard": "sonnet",
        "strong": "opus",
    },
    "cursor-desktop": {
        "fast": "gpt-5.3-codex-low-fast",
        "standard": "gpt-5.3-codex",
        "strong": "gpt-5.3-codex-high",
    },
    "cursor-agent": {
        "fast": "gpt-5.3-codex-low-fast",
        "standard": "gpt-5.3-codex",
        "strong": "gpt-5.3-codex-high",
    },
}
STRONG_HOST_TASK_TYPES = (
    "research",
    "benchmark",
    "design",
    "security",
    "release",
    "migration",
)

CLASSIFICATION_RULES = (
    (
        "security",
        (
            "security", "vulnerability", "secret", "credential", "auth",
            "authentication", "authorization", "login", "permission",
            "permissions", "sandbox", "exploit", "token",
        ),
    ),
    (
        "release",
        ("release", "publish", "version", "tag", "npm", "package", "ship", "deploy"),
    ),
    (
        "migration",
        ("migrate", "migration", "upgrade", "dependency", "dependencies", "schema", "breaking"),
    ),
    (
        "performance",
        ("performance", "optimize", "slow", "latency", "throughput", "memory leak", "profile"),
    ),
    (
        "frontend_ui",
        ("ui", "frontend", "component", "css", "responsive", "browser", "page", "screen", "layout"),
    ),
    (
        "benchmark",
        ("benchmark", "eval", "measure", "metric", "compare", "pass rate", "success rate"),
    ),
    (
        "research",
        ("research", "investigate", "find online", "look up", "survey", "source", "latest"),
    ),
    (
        "review",
        ("review", "audit", "inspect", "critique", "findings", "risk"),
    ),
    (
        "debugging",
        ("debug", "diagnose", "trace", "reproduce", "root cause"),
    ),
    (
        "bugfix",
        ("bug", "fix", "failing", "failure", "error", "exception", "broken", "crash", "regression"),
    ),
    (
        "test_generation",
        ("test", "tests", "coverage", "unit", "integration", "regression test"),
    ),
    (
        "refactor",
        ("refactor", "cleanup", "clean up", "simplify", "rename", "restructure"),
    ),
    (
        "feature",
        ("feature", "add", "implement", "support", "build", "create", "new"),
    ),
    (
        "docs",
        ("docs", "documentation", "readme", "guide", "changelog", "manual"),
    ),
    (
        "design",
        ("design", "architecture", "plan", "approach", "proposal", "spec"),
    ),
)

VERIFICATION_HINTS = {
    "security": "Run security-focused tests plus the relevant normal suite; inspect permissions and secret handling.",
    "release": "Run full tests, package/build checks, and version or artifact checks before publishing.",
    "migration": "Run migration tests, compatibility checks, and rollback or fixture validation.",
    "performance": "Run targeted benchmarks or profiling before and after the change.",
    "frontend_ui": "Run build/lint plus browser or screenshot checks for affected views.",
    "benchmark": "Run the benchmark harness and record JSON output, pass rates, evidence rates, and durations.",
    "research": "Cite sources and record a verify claim only when no executable check exists.",
    "review": "Read diffs/files and report findings with file and line references; tests are supporting evidence.",
    "debugging": "Reproduce the failure first, then run the failing check again after the fix.",
    "bugfix": "Run the failing or targeted regression test, then the nearest broader suite.",
    "test_generation": "Run the added tests and confirm they fail before the fix when practical.",
    "refactor": "Run the existing test suite and any type, lint, or build checks.",
    "feature": "Run targeted tests for the feature plus the nearest broader suite.",
    "docs": "Run docs generation, link checks, or a text/build check when available.",
    "design": "Use verify claim for the design rationale, then create executable checks for implementation steps.",
    "question": "No executable check is required unless the answer makes a factual or time-sensitive claim.",
    "trivial": "Use the smallest available check, or no protocol command for a one-line answer.",
}

TRIAGE_OUTPUT_SHAPE = {
    "primary_type": "string",
    "secondary_types": ["string"],
    "ambiguity": "low|medium|high",
    "hidden_questions": ["string"],
    "likely_files_or_surfaces": ["string"],
    "verification_plan": ["string"],
    "fanout_plan": ["string"],
    "risk_notes": ["string"],
    "recommended_first_step": "string",
}
VAGUE_REQUEST_TERMS = (
    "thing", "things", "stuff", "better", "problem", "issue", "issues",
    "it", "this", "that", "something", "somehow", "maybe", "unclear",
)


# ---------------------------------------------------------------------------
# Time and text helpers
# ---------------------------------------------------------------------------

def now_iso():
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


# ---------------------------------------------------------------------------
# Durable file IO
# ---------------------------------------------------------------------------

def _write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, str(path))
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def write_json_atomic(path, data):
    """Write JSON to a temp file in the same directory, then rename over the
    target so readers never observe a partial file."""
    _write_text_atomic(path, json.dumps(data, indent=2) + "\n")


def read_json(path, default):
    """Read a JSON file. On corruption, quarantine the bad file as
    <filename>.corrupt-<YYYYMMDDHHMMSS>, warn on stderr, and return the
    default. Never raises on bad state."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, UnicodeDecodeError):
        corrupt_name = path.name + ".corrupt-" + now_stamp()
        corrupt_path = path.with_name(corrupt_name)
        try:
            os.replace(str(path), str(corrupt_path))
            moved = " Moved it to " + corrupt_name + "."
        except OSError:
            moved = ""
        sys.stderr.write(
            "[WARN] Corrupt JSON in " + str(path) + "." + moved
            + " Continuing with a fresh default.\n"
        )
        return default


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read_jsonl(path):
    """Parse a jsonl file, skipping blank or unparseable lines."""
    path = Path(path)
    records = []
    if not path.exists():
        return records
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


# ---------------------------------------------------------------------------
# State directory resolution
# ---------------------------------------------------------------------------

def ensure_layout(state):
    """Create the state directory and its subdirectories."""
    (state / "plans" / "archive").mkdir(parents=True, exist_ok=True)
    (state / "lessons").mkdir(parents=True, exist_ok=True)
    (state / "outcomes").mkdir(parents=True, exist_ok=True)


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


def global_lessons_dir():
    return Path.home() / WORKSPACE_DIR_NAME / "lessons"


def host_model_path(state):
    return Path(state) / HOST_MODEL_STATE_FILE


def normalize_host_platform(platform):
    value = (platform or "auto").strip()
    return value if value in PLATFORMS else "auto"


def normalize_host_thinking(thinking):
    value = (thinking or "auto").strip()
    return value if value in HOST_THINKING_LEVELS else "auto"


def normalize_host_speed(speed):
    value = (speed or "auto").strip()
    return value if value in SPEED_LEVELS else "auto"


def detect_host_platform(platform):
    explicit = normalize_host_platform(platform)
    if explicit != "auto":
        return explicit
    if os.environ.get("CODEX_THREAD_ID", "").strip():
        return "codex-desktop"
    if os.environ.get("CLAUDECODE", "").strip() or os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").strip():
        return "claude-code"
    return "unknown"


def read_host_model_state(state=None):
    resolved = state or resolve_state_dir()
    if resolved is None:
        return None
    record = read_json(host_model_path(resolved), None)
    if not isinstance(record, dict):
        return None
    if not str(record.get("target_model", "")).strip():
        return None
    return record


def host_switch_actions(platform, target_model, thinking, speed):
    actions = []
    if platform == "codex-desktop":
        actions.append("Use the Codex Desktop model picker for the current chat.")
        thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
        if thread_id:
            suffix = ', thinking="{0}"'.format(thinking) if thinking != "auto" else ""
            actions.append(
                'Codex app agents can continue this thread with model override: '
                'send_message_to_thread(threadId="{0}", model="{1}"{2}).'.format(
                    thread_id, target_model, suffix
                )
            )
        else:
            actions.append(
                "Codex app agents can use send_message_to_thread with a model override "
                "when they know the target thread id."
            )
    elif platform == "codex-cli":
        actions.append("Start or resume Codex with --model {0}.".format(target_model))
        if thinking != "auto":
            actions.append(
                "Use the host reasoning effort control for {0} when available.".format(thinking)
            )
        if speed != "auto":
            actions.append(
                "Use Codex speed {0} for spawned workers; host chat speed remains host-controlled.".format(
                    speed
                )
            )
    elif platform == "claude-code":
        actions.append("In interactive Claude Code, run /model {0}.".format(target_model))
        actions.append("For a new Claude Code session, start with claude --model {0}.".format(target_model))
    elif platform == "claude-desktop":
        actions.append("Use the Claude Desktop model picker for the current chat.")
        actions.append("MCP servers cannot directly mutate Claude Desktop's active chat model.")
    elif platform == "cursor-desktop":
        actions.append("Use the Cursor chat model picker for the current chat.")
        actions.append("For spawned Cursor Agent workers, pass model, effort, and speed through fanout_start.")
    elif platform == "cursor-agent":
        actions.append("Start or resume Cursor Agent with --model {0}.".format(target_model))
        actions.append("For Mythify fanout workers, pass model per task or per job.")
    else:
        actions.append("Use the host app's model picker or model command for the current chat.")
        actions.append("Mythify has recorded the target model for session policy and spawn ceiling checks.")
    return actions


def build_host_model_record(args):
    target_model = str(getattr(args, "target_model", "") or "").strip()
    platform = detect_host_platform(getattr(args, "platform", "auto"))
    thinking = normalize_host_thinking(getattr(args, "thinking", "auto"))
    speed = normalize_host_speed(getattr(args, "speed", "auto"))
    return {
        "platform": platform,
        "requested_platform": normalize_host_platform(getattr(args, "platform", "auto")),
        "target_model": target_model,
        "current_model": str(getattr(args, "current_model", "") or "").strip(),
        "target_model_tier": classify_model_tier(target_model),
        "thinking": thinking,
        "speed": speed,
        "reason": str(getattr(args, "reason", "") or "").strip(),
        "status": "recorded_requires_host_action",
        "control": "host_selected",
        "can_apply_current_chat": False,
        "updated": now_iso(),
        "host_actions": host_switch_actions(platform, target_model, thinking, speed),
    }


def format_host_model_record(record):
    lines = [
        "[OK] Host model switch {0}.".format(record.get("status", "recorded")),
        "platform: {0}".format(record.get("platform", "unknown")),
        "target model: {0} (tier {1})".format(
            record.get("target_model", ""), record.get("target_model_tier", "unknown")
        ),
        "current model: {0}".format(record.get("current_model") or "unknown"),
        "thinking: {0}".format(record.get("thinking", "auto")),
        "speed: {0}".format(record.get("speed", "auto")),
        "scope: Mythify recorded the requested host model for model_policy and spawn ceiling checks.",
        "host action required:",
    ]
    for action in record.get("host_actions", []):
        lines.append("- " + action)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Memory store
# ---------------------------------------------------------------------------

def default_memory():
    stamp = now_iso()
    return {
        "entries": [],
        "metadata": {"created": stamp, "last_updated": stamp, "total_entries": 0},
    }


def load_memory(state):
    memory = read_json(state / "memory.json", None)
    if not isinstance(memory, dict) or not isinstance(memory.get("entries"), list):
        memory = default_memory()
    if not isinstance(memory.get("metadata"), dict):
        memory["metadata"] = default_memory()["metadata"]
    return memory


def save_memory(state, memory):
    memory["metadata"]["last_updated"] = now_iso()
    memory["metadata"]["total_entries"] = len(memory["entries"])
    write_json_atomic(state / "memory.json", memory)


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


def find_plan_slug(state, name):
    """Map a user-supplied plan name to an existing plan slug, or None."""
    if plan_path(state, name).exists():
        return name
    candidate = slugify(name)
    if candidate and plan_path(state, candidate).exists():
        return candidate
    return None


def target_plan_slug(state, name):
    """Named plan if given, otherwise the active plan. None if unresolvable."""
    if name:
        return find_plan_slug(state, name)
    return get_active_slug(state)


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
    has_bound_context = record.get("plan") is not None or record.get("step_id") is not None
    if not has_bound_context:
        return True
    return record.get("plan") == slug and record.get("step_id") == step_id


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


# ---------------------------------------------------------------------------
# Outcome loops
# ---------------------------------------------------------------------------

def outcomes_dir(state):
    return state / "outcomes"


def active_outcome_path(state):
    return outcomes_dir(state) / "active"


def outcome_dir(state, slug):
    return outcomes_dir(state) / slug


def outcome_goal_path(state, slug):
    return outcome_dir(state, slug) / "goal.json"


def outcome_iterations_path(state, slug):
    return outcome_dir(state, slug) / "iterations.jsonl"


def get_active_outcome_slug(state):
    path = active_outcome_path(state)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def set_active_outcome_slug(state, slug):
    _write_text_atomic(active_outcome_path(state), slug + "\n")


def clear_active_outcome_slug(state, slug=None):
    path = active_outcome_path(state)
    if not path.exists():
        return
    if slug is not None and get_active_outcome_slug(state) != slug:
        return
    try:
        path.unlink()
    except OSError:
        pass


def find_outcome_slug(state, name):
    if name:
        if outcome_goal_path(state, name).exists():
            return name
        candidate = slugify(name)
        if candidate and outcome_goal_path(state, candidate).exists():
            return candidate
        return None
    return get_active_outcome_slug(state)


def load_outcome(state, name=None):
    slug = find_outcome_slug(state, name)
    if not slug:
        return None, None
    goal = read_json(outcome_goal_path(state, slug), None)
    if not isinstance(goal, dict):
        return slug, None
    return slug, goal


def save_outcome(state, slug, goal):
    write_json_atomic(outcome_goal_path(state, slug), goal)


def list_outcomes(state):
    root = outcomes_dir(state)
    if not root.exists():
        return []
    items = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        goal = read_json(path / "goal.json", None)
        if isinstance(goal, dict):
            items.append((path.name, goal))
    return items


def parse_allowed_paths(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def run_shell_capture(command, timeout):
    started = datetime.now(timezone.utc)
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = _coerce_stream_text(exc.stdout)
        stderr = _coerce_stream_text(exc.stderr)
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    stdout_tail = stdout[-TAIL_CHARS:]
    stderr_tail = stderr[-TAIL_CHARS:]
    if timed_out:
        notice = "(timed out after {0:g} seconds)".format(timeout)
        stderr_tail = (stderr_tail + "\n" + notice) if stderr_tail else notice
    return {
        "command": command,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "verified": (not timed_out) and exit_code == 0,
        "timed_out": timed_out,
    }


def parse_metric_score(output):
    match = re.search(r"-?\d+(?:\.\d+)?", str(output or ""))
    return float(match.group(0)) if match else None


def format_outcome_status(slug, goal, iterations=None):
    iterations = iterations if iterations is not None else []
    lines = [
        "[OK] Outcome {0}: {1}".format(slug, goal.get("goal", "")),
        "status: {0}".format(goal.get("status", "active")),
        "success: {0}".format(goal.get("success_criteria", "")),
        "verify: {0}".format(goal.get("verify_command", "")),
        "iterations: {0}/{1}".format(
            goal.get("iteration_count", 0), goal.get("max_iterations", 1)
        ),
    ]
    metric = goal.get("metric_command", "")
    if metric:
        lines.append("metric: {0}".format(metric))
    allowed = goal.get("allowed_paths") or []
    if allowed:
        lines.append("allowed paths: {0}".format(", ".join(allowed)))
    if iterations:
        last = iterations[-1]
        lines.append(
            "last check: iteration {0}, verified={1}, status={2}".format(
                last.get("iteration"), last.get("verified"), last.get("status_after")
            )
        )
        next_action = last.get("next_action")
        if next_action:
            lines.append("next: {0}".format(next_action))
    else:
        lines.append("next: do the first bounded attempt, then run outcome check.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task classification
# ---------------------------------------------------------------------------

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


def classify_ambiguity(text, words, signals, scores, task_type):
    if task_type in ("question", "trivial"):
        return "low"
    if _contains_any(text, VAGUE_REQUEST_TERMS) or (not signals and len(words) <= 18):
        return "high"
    if len(scores) > 1 or len(words) > 22:
        return "medium"
    return "low"


def model_triage_gate(task_type, risk, ceremony, ambiguity, text):
    if ceremony == "none":
        return (
            "skip",
            "The deterministic classifier is enough for a simple question or one-step task.",
        )
    high_impact_terms = (
        "production", "payment", "credential", "secret", "data loss",
        "delete", "remove", "drop", "deploy",
    )
    if risk == "high" and ambiguity == "high" and _contains_any(text, high_impact_terms):
        return (
            "required",
            "High-impact ambiguous work deserves a cheap second read before planning.",
        )
    if ambiguity == "high":
        return (
            "recommended",
            "The request is underspecified enough that a fast framing pass can reduce rework.",
        )
    if task_type in (
        "research", "review", "benchmark", "design", "debugging",
        "security", "migration", "release", "performance",
    ):
        return (
            "recommended",
            "This problem type benefits from an independent framing pass before execution.",
        )
    if task_type in ("feature", "refactor", "frontend_ui", "bugfix", "test_generation") or risk == "medium":
        return (
            "optional",
            "A fast triage pass may help, but the main worker can proceed without it.",
        )
    return (
        "skip",
        "The deterministic classification gives enough routing signal for this task.",
    )


def infer_fanout_visibility(text):
    normalized = " ".join(str(text or "").lower().split())
    quiet_terms = (
        "quiet",
        "quietly",
        "silent",
        "silently",
        "background only",
        "do not show worker",
        "don't show worker",
        "do not show subagent",
        "don't show subagent",
        "no worker details",
        "minimal progress",
    )
    threaded_terms = (
        "threaded",
        "visible thread",
        "visible threads",
        "separate thread",
        "separate threads",
        "separate chat",
        "separate chats",
        "show subagent chats",
        "show sub-agent chats",
        "visible subagent",
        "visible sub-agent",
    )
    verbose_terms = (
        "verbose",
        "show details",
        "show full",
        "show logs",
        "show worker output",
        "show subagent output",
        "show sub-agent output",
        "detailed progress",
        "full worker output",
    )
    if _contains_any(normalized, quiet_terms):
        return (
            "quiet",
            "prompt",
            "The prompt asks to keep background worker activity quiet.",
        )
    if _contains_any(normalized, threaded_terms):
        return (
            "threaded",
            "prompt",
            "The prompt asks for visible worker threads or separate chats when the host supports them.",
        )
    if _contains_any(normalized, verbose_terms):
        return (
            "verbose",
            "prompt",
            "The prompt asks to see detailed worker output or progress.",
        )
    return (
        "summary",
        "default",
        "Summary visibility is the default: show worker titles, status, and notable results without flooding the chat.",
    )


def execution_profile_for(task_type, risk, ceremony, ambiguity, text):
    if ceremony == "none":
        return (
            "direct",
            "No protocol state is needed for a simple answer or one reversible edit.",
        )
    if ceremony == "full" or risk == "high":
        return (
            "full",
            "High-risk or heavy work needs the full plan, verify, reflect, and state loop.",
        )
    if ambiguity == "high":
        return (
            "standard",
            "Ambiguous work needs a plan or fast triage before execution.",
        )
    focused_terms = (
        "small", "single", "one file", "focused", "unit", "unittest",
        "test", "tests", "bug", "fix", "failing", "regression",
    )
    if task_type in ("bugfix", "test_generation") or (
        task_type in ("docs", "refactor") and _contains_any(text, focused_terms)
    ):
        return (
            "fast",
            "Focused low-risk work can skip plan state but must still use verify run.",
        )
    if ceremony == "light":
        return (
            "fast",
            "Light work can use the fast profile unless it expands into multiple steps.",
        )
    return (
        "standard",
        "Use a plan with verifiable steps and verify run before completion.",
    )


def classify_task_text(task_text):
    text = " ".join(str(task_text or "").lower().split())
    words = [word for word in text.replace("/", " ").replace("_", " ").split() if word]
    signals = []
    scores = {}
    for task_type, terms in CLASSIFICATION_RULES:
        matches = _contains_any(text, terms)
        if matches:
            scores[task_type] = len(matches)
            signals.extend(matches)
    if scores:
        task_type = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]
    elif text.endswith("?") or any(text.startswith(prefix) for prefix in ("what ", "why ", "how ", "can ", "should ")):
        task_type = "question"
    elif _contains_any(text, VAGUE_REQUEST_TERMS):
        task_type = "feature"
    elif len(words) <= 12:
        task_type = "trivial"
    else:
        task_type = "feature"

    high_risk_terms = (
        "delete", "remove", "drop", "destructive", "production", "payment",
        "security", "secret", "credential", "auth", "authentication",
        "authorization", "login", "release", "deploy", "migration", "schema",
        "data loss", "permission", "permissions",
    )
    medium_risk_terms = (
        "refactor", "dependency", "upgrade", "performance", "benchmark",
        "multiple", "multi", "large", "cross", "api",
    )
    if _contains_any(text, high_risk_terms) or task_type in ("security", "release", "migration"):
        risk = "high"
    elif _contains_any(text, medium_risk_terms) or task_type in (
        "feature", "refactor", "benchmark", "performance", "frontend_ui",
    ):
        risk = "medium"
    else:
        risk = "low"

    ambiguity = classify_ambiguity(text, words, signals, scores, task_type)

    if task_type in ("trivial", "question") and risk == "low":
        ceremony = "none"
    elif risk == "low" and task_type in ("docs", "review", "research", "design"):
        ceremony = "light"
    elif risk == "high" or task_type in ("benchmark", "migration", "release", "security"):
        ceremony = "full"
    else:
        ceremony = "standard"

    if task_type in ("research", "review", "benchmark", "design") or "parallel" in text:
        fanout = "recommended"
        fanout_reason = "Independent analysis or comparison work can be split across workers."
    elif task_type in ("feature", "refactor", "frontend_ui") or "multiple files" in text:
        fanout = "optional"
        fanout_reason = "Use fanout only for independent subtasks; keep dependent implementation sequential."
    else:
        fanout = "not_recommended"
        fanout_reason = "A single focused worker is simpler for this task type."

    verification = VERIFICATION_HINTS.get(task_type, VERIFICATION_HINTS["feature"])
    execution_profile, execution_profile_reason = execution_profile_for(
        task_type, risk, ceremony, ambiguity, text
    )
    if execution_profile == "direct":
        next_action = "Answer directly or make the single reversible edit; no plan is required."
    elif execution_profile == "fast":
        next_action = "Use the fast profile: skip plan state, make the focused change, and run verify run before completion."
    elif execution_profile == "standard":
        next_action = "Create a plan with verifiable steps, act step by step, and use verify run before completion."
    else:
        next_action = "Use the full loop: plan, memory, step updates, verify run, reflect on failures, and summarize."

    model_triage, model_triage_reason = model_triage_gate(
        task_type, risk, ceremony, ambiguity, text
    )
    fanout_visibility, fanout_visibility_source, fanout_visibility_reason = (
        infer_fanout_visibility(text)
    )

    return {
        "task_type": task_type,
        "risk": risk,
        "ambiguity": ambiguity,
        "ceremony": ceremony,
        "execution_profile": execution_profile,
        "execution_profile_reason": execution_profile_reason,
        "verification": verification,
        "fanout": fanout,
        "fanout_reason": fanout_reason,
        "fanout_visibility": fanout_visibility,
        "fanout_visibility_source": fanout_visibility_source,
        "fanout_visibility_reason": fanout_visibility_reason,
        "model_triage": model_triage,
        "model_triage_reason": model_triage_reason,
        "signals": sorted(set(signals))[:10],
        "next_action": next_action,
    }


def should_run_model_triage(result, mode):
    if mode == "never":
        return False
    if mode == "always":
        return True
    return result.get("model_triage") in ("recommended", "required")


def build_triage_prompt(task_text, classification):
    return "\n".join(
        [
            "You are a fast triage model helping Mythify frame a task before the main agent plans.",
            "Do not edit files, run commands, or ask questions.",
            "Return only valid JSON with this exact shape:",
            json.dumps(TRIAGE_OUTPUT_SHAPE, indent=2),
            "",
            "User task:",
            str(task_text),
            "",
            "Deterministic classification:",
            json.dumps(classification, indent=2, sort_keys=True),
            "",
            "Focus on the problem shape, likely hidden requirements, verification, risk, and whether independent fanout would help.",
        ]
    )


def format_classification(result):
    lines = [
        "[OK] Task classification",
        "type: {0}".format(result["task_type"]),
        "risk: {0}".format(result["risk"]),
        "ambiguity: {0}".format(result["ambiguity"]),
        "ceremony: {0}".format(result["ceremony"]),
        "execution profile: {0} ({1})".format(
            result["execution_profile"], result["execution_profile_reason"]
        ),
        "verification: {0}".format(result["verification"]),
        "fanout: {0} ({1})".format(result["fanout"], result["fanout_reason"]),
        "fanout visibility: {0} ({1})".format(
            result.get("fanout_visibility", "summary"),
            result.get("fanout_visibility_reason", "Summary visibility is the default."),
        ),
        "model triage: {0} ({1})".format(
            result["model_triage"], result["model_triage_reason"]
        ),
        "next: {0}".format(result["next_action"]),
    ]
    if result["signals"]:
        lines.append("signals: {0}".format(", ".join(result["signals"])))
    policy = result.get("model_policy")
    if policy:
        recommendation = policy.get("session", {}).get("recommendation", {})
        lines.append(
            "model policy: session={0}/{1}; ceiling={2}; triage={3}/{4}/{5}/{6}; fanout={7}/{8}/{9}/{10}; verifier={11}".format(
                policy.get("session", {}).get("control", "host_selected"),
                policy.get("session", {}).get("model_tier", "unknown"),
                policy.get("spawn_ceiling", {}).get("policy", "same_or_lower"),
                policy.get("triage", {}).get("engine", "auto"),
                policy.get("triage", {}).get("model_policy", "engine_default"),
                policy.get("triage", {}).get("effort", "low"),
                policy.get("triage", {}).get("speed", "auto"),
                policy.get("fanout_worker", {}).get("engine_policy", "local_first"),
                policy.get("fanout_worker", {}).get("effort", "medium"),
                policy.get("fanout_worker", {}).get("speed", "auto"),
                policy.get("fanout_worker", {}).get("visibility", "summary"),
                policy.get("verifier", {}).get("engine", "local_command"),
            )
        )
        lines.append(
            "host recommendation: {0} to {1}/{2} thinking={3} speed={4}".format(
                recommendation.get("action", "recommend_set"),
                recommendation.get("target_profile", "standard"),
                recommendation.get("target_model", ""),
                recommendation.get("thinking", "medium"),
                recommendation.get("speed", "auto"),
            )
        )
    run = result.get("model_triage_run")
    if run:
        if not run.get("attempted"):
            lines.append("fast triage run: skipped ({0})".format(run.get("reason", "")))
        elif run.get("ok"):
            lines.append(
                "fast triage run: [OK] {0} model={1} duration={2}s".format(
                    run.get("engine", ""),
                    run.get("model", ""),
                    run.get("duration_seconds", 0),
                )
            )
            if run.get("parsed") is not None:
                lines.append("fast triage json: {0}".format(json.dumps(run["parsed"], sort_keys=True)))
            elif run.get("output_tail"):
                lines.append("fast triage output: {0}".format(run["output_tail"]))
        else:
            lines.append(
                "fast triage run: [FAIL] {0}".format(
                    run.get("error") or "triage worker failed"
                )
            )
    return "\n".join(lines)


def tail_text(text, limit=TAIL_CHARS):
    return str(text or "")[-limit:]


def triage_default_model(engine):
    if engine == "claude-cli":
        return "haiku"
    return ""


def resolve_triage_binary(names, env_names):
    for env_name in env_names:
        value = os.environ.get(env_name, "").strip()
        if value:
            path = Path(value)
            if path.is_file() and os.access(str(path), os.X_OK):
                return str(path)
            return None
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    home = Path.home()
    fallbacks = []
    if "claude" in names:
        fallbacks.extend([
            home / ".claude" / "local" / "claude",
            Path("/opt/homebrew/bin/claude"),
            Path("/usr/local/bin/claude"),
        ])
    if "codex" in names:
        fallbacks.extend([
            home / ".local" / "bin" / "codex",
            Path("/opt/homebrew/bin/codex"),
            Path("/usr/local/bin/codex"),
        ])
    if "cursor-agent" in names:
        fallbacks.extend([
            home / ".local" / "bin" / "cursor-agent",
            Path("/opt/homebrew/bin/cursor-agent"),
            Path("/usr/local/bin/cursor-agent"),
        ])
    if "cursor" in names:
        fallbacks.extend([
            home / ".local" / "bin" / "cursor",
            Path("/opt/homebrew/bin/cursor"),
            Path("/usr/local/bin/cursor"),
        ])
    for candidate in fallbacks:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return None


def command_triage_template():
    return (
        os.environ.get("MYTHIFY_TRIAGE_COMMAND", "").strip()
        or os.environ.get("MYTHIFY_FANOUT_COMMAND", "").strip()
    )


def auto_detect_triage_engine():
    explicit = os.environ.get("MYTHIFY_TRIAGE_ENGINE", "").strip()
    if explicit:
        return explicit
    if resolve_triage_binary(["claude"], ["MYTHIFY_TRIAGE_CLAUDE_BIN", "MYTHIFY_FANOUT_CLAUDE_BIN"]):
        return "claude-cli"
    if resolve_triage_binary(["codex"], ["MYTHIFY_TRIAGE_CODEX_BIN", "MYTHIFY_FANOUT_CODEX_BIN"]):
        return "codex-cli"
    if resolve_triage_binary(
        ["cursor-agent", "cursor"],
        [
            "MYTHIFY_TRIAGE_CURSOR_BIN",
            "MYTHIFY_FANOUT_CURSOR_BIN",
            "MYTHIFY_FANOUT_CURSOR_AGENT_BIN",
        ],
    ):
        return "cursor-agent"
    if command_triage_template():
        return "command"
    return ""


def infer_platform():
    origin = os.environ.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "").lower()
    if "codex" in origin or os.environ.get("CODEX_SHELL"):
        return "codex-desktop"
    if os.environ.get("CLAUDECODE") or any(
        key.startswith("CLAUDE_CODE_") for key in os.environ
    ):
        return "claude-code"
    if (
        os.environ.get("CURSOR_AGENT")
        or os.environ.get("CURSOR_TRACE_ID")
        or os.environ.get("CURSOR_SESSION_ID")
    ):
        return "cursor-desktop"
    return "unknown"


def normalize_platform(platform):
    value = (platform or "auto").strip()
    if value == "auto":
        return infer_platform()
    return value if value in PLATFORMS else "unknown"


def preferred_local_engine(platform):
    if platform in ("codex-desktop", "codex-cli"):
        return "codex-cli"
    if platform in ("claude-desktop", "claude-code"):
        return "claude-cli"
    if platform in ("cursor-desktop", "cursor-agent"):
        return "cursor-agent"
    return ""


def triage_engine_available(engine):
    if engine == "claude-cli":
        return bool(resolve_triage_binary(
            ["claude"],
            ["MYTHIFY_TRIAGE_CLAUDE_BIN", "MYTHIFY_FANOUT_CLAUDE_BIN"],
        ))
    if engine == "codex-cli":
        return bool(resolve_triage_binary(
            ["codex"],
            ["MYTHIFY_TRIAGE_CODEX_BIN", "MYTHIFY_FANOUT_CODEX_BIN"],
        ))
    if engine == "cursor-agent":
        return bool(resolve_triage_binary(
            ["cursor-agent", "cursor"],
            [
                "MYTHIFY_TRIAGE_CURSOR_BIN",
                "MYTHIFY_FANOUT_CURSOR_BIN",
                "MYTHIFY_FANOUT_CURSOR_AGENT_BIN",
            ],
        ))
    if engine == "command":
        return bool(command_triage_template())
    return False


def select_triage_engine(requested_engine, platform):
    explicit = (requested_engine or "").strip()
    if explicit:
        return explicit, "explicit"
    env_engine = os.environ.get("MYTHIFY_TRIAGE_ENGINE", "").strip()
    if env_engine:
        return env_engine, "env"
    preferred = preferred_local_engine(platform)
    if preferred and triage_engine_available(preferred):
        return preferred, "platform_preferred"
    detected = auto_detect_triage_engine()
    if detected:
        return detected, "auto_detected"
    return "", "unavailable"


def resolve_triage_model_selection(engine, requested_model):
    explicit = (requested_model or "").strip()
    if explicit:
        return explicit, "explicit"
    env_model = os.environ.get("MYTHIFY_TRIAGE_MODEL", "").strip()
    if env_model:
        return env_model, "env"
    default_model = triage_default_model(engine)
    if default_model:
        return default_model, "engine_default"
    if engine in ("codex-cli", "cursor-agent"):
        return "", "platform_default"
    if engine == "command":
        return "", "command_default"
    return "", "auto_after_engine_detection"


def classify_model_tier(model):
    value = str(model or "").lower()
    compact = value.replace("_", "-").replace(" ", "-")
    if not compact:
        return "unknown"
    frontier_terms = (
        "gpt-5",
        "o3",
        "o4",
        "opus",
        "max",
        "deep-research",
        "reasoning-pro",
    )
    strong_terms = (
        "sonnet",
        "gpt-4",
        "gpt4",
        "gemini-2.5-pro",
        "pro",
        "large",
        "grok-4",
    )
    fast_terms = (
        "haiku",
        "mini",
        "nano",
        "small",
        "lite",
        "flash",
        "fast",
        "instant",
    )
    if any(term in compact for term in fast_terms):
        return "fast"
    if any(term in compact for term in frontier_terms):
        return "frontier"
    if any(term in compact for term in strong_terms):
        return "strong"
    if "3.5" in compact or "cheap" in compact:
        return "small"
    return "standard"


def resolve_session_model(session_model):
    explicit = (session_model or "").strip()
    if explicit:
        return explicit, "explicit"
    env_model = os.environ.get("MYTHIFY_SESSION_MODEL", "").strip()
    if env_model:
        return env_model, "env"
    host_model = read_host_model_state()
    if host_model:
        return host_model["target_model"].strip(), "host_model_switch"
    return "", "unknown"


def resolve_spawn_ceiling(spawn_ceiling):
    explicit = (spawn_ceiling or "auto").strip()
    if explicit and explicit != "auto":
        return explicit, "explicit"
    env_ceiling = os.environ.get("MYTHIFY_SPAWN_CEILING", "").strip()
    if env_ceiling in SPAWN_CEILINGS and env_ceiling != "auto":
        return env_ceiling, "env"
    return "same_or_lower", "default"


def role_model_relation(role, session_tier, ceiling):
    if role == "verifier":
        return "none"
    if role == "triage":
        return "lower_preferred"
    if ceiling == "allow_stronger":
        return "may_exceed_session"
    if role == "reviewer":
        return "same_or_lower"
    if ceiling == "lower_only":
        return "lower_only"
    if session_tier == "unknown":
        return "same_or_lower_when_session_known"
    return "same_or_lower"


def effort_for_role(role, classification, requested_effort):
    requested = (requested_effort or "auto").strip()
    if requested != "auto":
        return requested, "explicit"
    risk = classification.get("risk", "low")
    ceremony = classification.get("ceremony", "none")
    if role == "triage":
        return "low", "role_default"
    if role == "fanout_worker":
        if risk == "high" or ceremony == "full":
            return "high", "risk_default"
        if ceremony == "standard":
            return "medium", "role_default"
        return "low", "role_default"
    if role == "reviewer":
        if risk == "high" or ceremony == "full":
            return "high", "risk_default"
        if risk == "medium" or ceremony == "standard":
            return "medium", "role_default"
        return "low", "role_default"
    return "none", "command_first"


def speed_for_role(role, requested_speed):
    requested = (requested_speed or "auto").strip()
    if requested != "auto":
        return requested, "explicit"
    if role == "verifier":
        return "none", "command_first"
    return "auto", "host_default"


def reviewer_spawn_policy(classification):
    if classification.get("risk") == "high" or classification.get("ceremony") == "full":
        return "recommended"
    if classification.get("risk") == "medium" or classification.get("ceremony") == "standard":
        return "optional"
    return "skip"


def host_recommendation_profile(classification):
    task_type = classification.get("task_type", "feature")
    risk = classification.get("risk", "low")
    ambiguity = classification.get("ambiguity", "low")
    ceremony = classification.get("ceremony", "none")
    execution_profile = classification.get("execution_profile", "standard")
    if (
        task_type in ("trivial", "question")
        and risk == "low"
        and execution_profile == "direct"
    ):
        return {
            "target_profile": "fast",
            "thinking": "low",
            "speed": "fast",
            "reason": "Direct low-risk prompts should use the cheapest responsive host settings.",
        }
    if (
        task_type in STRONG_HOST_TASK_TYPES
        or risk == "high"
        or ceremony == "full"
    ):
        return {
            "target_profile": "strong",
            "thinking": "high",
            "speed": "standard",
            "reason": "Research, benchmark, release, security, migration, and design work benefit from stronger reasoning.",
        }
    if execution_profile == "fast" or ceremony == "light":
        return {
            "target_profile": "fast",
            "thinking": "low",
            "speed": "fast",
            "reason": "Focused low-risk work is a good fit for fast host settings.",
        }
    if ambiguity == "high":
        return {
            "target_profile": "standard",
            "thinking": "medium",
            "speed": "auto",
            "reason": "Ambiguous work needs enough reasoning to frame the problem, but more model size will not replace missing context.",
        }
    return {
        "target_profile": "standard",
        "thinking": "medium",
        "speed": "auto",
        "reason": "Normal implementation, debugging, review, and docs work should use balanced host settings.",
    }


def host_recommendation_model(platform, target_profile):
    env_name = "MYTHIFY_HOST_{0}_MODEL".format(target_profile.upper())
    env_model = os.environ.get(env_name, "").strip()
    if env_model:
        return env_model, "env:" + env_name
    defaults = HOST_MODEL_DEFAULTS.get(platform, {})
    default_model = defaults.get(target_profile, "")
    if default_model:
        return default_model, "platform_default"
    return "", "none"


def host_recommendation_action(session_model, session_tier, target_profile):
    if not session_model:
        return "recommend_set"
    session_rank = MODEL_TIER_RANK.get(session_tier, 0)
    target_rank = HOST_PROFILE_RANK.get(target_profile, MODEL_TIER_RANK["standard"])
    if session_rank == 0:
        return "recommend_set"
    if target_rank < session_rank:
        return "downgrade"
    if target_rank > session_rank:
        return "upgrade"
    return "keep"


def host_prompt_recommendation(classification, platform, session_model, session_tier):
    profile = host_recommendation_profile(classification)
    target_profile = profile["target_profile"]
    target_model, target_model_source = host_recommendation_model(
        platform, target_profile
    )
    return {
        "policy": "task_classification",
        "action": host_recommendation_action(
            session_model, session_tier, target_profile
        ),
        "target_profile": target_profile,
        "target_model": target_model,
        "target_model_source": target_model_source,
        "target_model_tier": classify_model_tier(target_model),
        "thinking": profile["thinking"],
        "speed": profile["speed"],
        "reason": profile["reason"],
    }


def build_model_policy(classification, args):
    platform = normalize_platform(getattr(args, "platform", "auto"))
    requested_effort = getattr(args, "effort", "auto")
    requested_speed = getattr(args, "speed", "auto")
    session_model, session_model_source = resolve_session_model(
        getattr(args, "session_model", "")
    )
    session_tier = classify_model_tier(session_model)
    spawn_ceiling, spawn_ceiling_source = resolve_spawn_ceiling(
        getattr(args, "spawn_ceiling", "auto")
    )
    triage_engine, triage_engine_source = select_triage_engine(
        getattr(args, "triage_engine", ""), platform
    )
    triage_model, triage_model_source = resolve_triage_model_selection(
        triage_engine, getattr(args, "triage_model", "")
    )
    triage_effort, triage_effort_source = effort_for_role(
        "triage", classification, requested_effort
    )
    fanout_effort, fanout_effort_source = effort_for_role(
        "fanout_worker", classification, requested_effort
    )
    reviewer_effort, reviewer_effort_source = effort_for_role(
        "reviewer", classification, requested_effort
    )
    triage_speed, triage_speed_source = speed_for_role("triage", requested_speed)
    fanout_speed, fanout_speed_source = speed_for_role(
        "fanout_worker", requested_speed
    )
    reviewer_speed, reviewer_speed_source = speed_for_role(
        "reviewer", requested_speed
    )
    session_effort_policy = (
        "host_default" if requested_effort == "auto" else "requested_" + requested_effort
    )
    session_speed_policy = (
        "host_default" if requested_speed == "auto" else "requested_" + requested_speed
    )
    host_recommendation = host_prompt_recommendation(
        classification, platform, session_model, session_tier
    )
    return {
        "session": {
            "role": "current_conversation",
            "control": "host_selected",
            "platform": platform,
            "model": session_model,
            "model_source": session_model_source,
            "model_tier": session_tier,
            "model_policy": "host_default",
            "effort_policy": session_effort_policy,
            "speed_policy": session_speed_policy,
            "spawn_ceiling": spawn_ceiling,
            "spawn_ceiling_source": spawn_ceiling_source,
            "recommendation": host_recommendation,
            "reason": (
                "The active chat model belongs to the desktop or CLI host. "
                "Mythify records the policy and controls only spawned workers."
            ),
        },
        "spawn_ceiling": {
            "policy": spawn_ceiling,
            "source": spawn_ceiling_source,
            "session_model": session_model,
            "session_model_source": session_model_source,
            "session_model_tier": session_tier,
            "default": "same_or_lower",
            "stronger_requires": "spawn_ceiling_allow_stronger",
        },
        "triage": {
            "role": "problem_framing",
            "spawn": classification.get("model_triage", "skip"),
            "engine": triage_engine or "auto",
            "engine_policy": triage_engine_source,
            "model": triage_model,
            "model_tier": classify_model_tier(triage_model),
            "model_relation_to_session": role_model_relation(
                "triage", session_tier, spawn_ceiling
            ),
            "model_policy": triage_model_source,
            "effort": triage_effort,
            "effort_policy": triage_effort_source,
            "speed": triage_speed,
            "speed_policy": triage_speed_source,
            "timeout_seconds": getattr(args, "triage_timeout", 120.0),
            "max_turns": 1,
            "sandbox": "read-only",
            "reason": "Use a cheap local CLI or command pass to frame the problem before planning.",
        },
        "fanout_worker": {
            "role": "independent_subtask",
            "spawn": classification.get("fanout", "not_recommended"),
            "engine": "auto",
            "engine_policy": "local_first",
            "model_policy": "per_task_over_job_over_env_over_engine_default",
            "model_relation_to_session": role_model_relation(
                "fanout_worker", session_tier, spawn_ceiling
            ),
            "effort": fanout_effort,
            "effort_policy": fanout_effort_source,
            "speed": fanout_speed,
            "speed_policy": fanout_speed_source,
            "visibility": classification.get("fanout_visibility", "summary"),
            "visibility_policy": classification.get(
                "fanout_visibility_source", "default"
            ),
            "visibility_modes": list(FANOUT_VISIBILITY_MODES),
            "visibility_reason": classification.get(
                "fanout_visibility_reason",
                "Summary visibility is the default.",
            ),
            "timeout_seconds": 600,
            "reason": (
                "Spawn only independent tasks. The fanout_start tool can set "
                "engine, model, effort, speed, and visibility per job."
            ),
        },
        "reviewer": {
            "role": "independent_review",
            "spawn": reviewer_spawn_policy(classification),
            "engine": "auto",
            "engine_policy": "local_first",
            "model_policy": "prefer_stronger_than_worker_when_available",
            "model_relation_to_session": role_model_relation(
                "reviewer", session_tier, spawn_ceiling
            ),
            "effort": reviewer_effort,
            "effort_policy": reviewer_effort_source,
            "speed": reviewer_speed,
            "speed_policy": reviewer_speed_source,
            "reason": "Use a separate review pass for high-risk or broad changes.",
        },
        "verifier": {
            "role": "evidence",
            "spawn": "not_model_based",
            "engine": "local_command",
            "model_policy": "none_when_executable_check_exists",
            "model_relation_to_session": role_model_relation(
                "verifier", session_tier, spawn_ceiling
            ),
            "effort": "none",
            "effort_policy": "command_first",
            "speed": "none",
            "speed_policy": "command_first",
            "reason": "Executable verify_run evidence beats model judgment.",
        },
    }


def triage_shell_env(model, speed="auto"):
    env = dict(os.environ)
    env["TERM"] = "dumb"
    env["MYTHIFY_FANOUT_DEPTH"] = "1"
    env["MYTHIFY_DISABLE_FANOUT"] = "1"
    env["MYTHIFY_TRIAGE_MODEL"] = model or ""
    env["MYTHIFY_TRIAGE_SPEED"] = speed or "auto"
    return env


def run_triage_process(args, cwd, prompt, timeout, env, shell=False):
    started = time.monotonic()
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            input=prompt,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
        )
        return {
            "exit_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail_text(result.stdout),
            "stderr_tail": tail_text(result.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "exit_code": -1,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": tail_text(stdout),
            "stderr_tail": tail_text(stderr + "\n[FAIL] timed out"),
            "timed_out": True,
        }


def parse_model_triage_json(text):
    raw = str(text or "").strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def run_claude_triage(prompt, model, timeout, cwd, speed="auto"):
    binary = resolve_triage_binary(["claude"], ["MYTHIFY_TRIAGE_CLAUDE_BIN", "MYTHIFY_FANOUT_CLAUDE_BIN"])
    if not binary:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "claude binary not found",
            "timed_out": False,
        }
    args = [
        binary,
        "-p",
        "--output-format",
        "json",
        "--model",
        model or "haiku",
        "--max-turns",
        os.environ.get("MYTHIFY_TRIAGE_MAX_TURNS", "1"),
    ]
    args.extend(shlex.split(os.environ.get("MYTHIFY_TRIAGE_CLAUDE_ARGS", "")))
    result = run_triage_process(args, cwd, prompt, timeout, triage_shell_env(model, speed))
    try:
        parsed = json.loads(result["stdout_tail"])
        if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
            result["output_tail"] = tail_text(parsed["result"])
        else:
            result["output_tail"] = result["stdout_tail"]
    except ValueError:
        result["output_tail"] = result["stdout_tail"]
    return result


def codex_speed_args(speed):
    if speed == "fast":
        return ["-c", 'service_tier="fast"', "-c", "features.fast_mode=true"]
    if speed == "standard":
        return ["-c", "features.fast_mode=false"]
    return []


def run_codex_triage(prompt, model, timeout, cwd, speed="auto"):
    binary = resolve_triage_binary(["codex"], ["MYTHIFY_TRIAGE_CODEX_BIN", "MYTHIFY_FANOUT_CODEX_BIN"])
    if not binary:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "codex binary not found",
            "timed_out": False,
        }
    with tempfile.NamedTemporaryFile(prefix="mythify-codex-triage-", suffix=".md", delete=False) as handle:
        output_path = Path(handle.name)
    args = [
        binary,
        "--ask-for-approval",
        "never",
        "exec",
        "--cd",
        str(cwd),
        "--sandbox",
        os.environ.get("MYTHIFY_TRIAGE_CODEX_SANDBOX", "read-only"),
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "--output-last-message",
        str(output_path),
    ]
    if model:
        args.extend(["--model", model])
    args.extend(codex_speed_args(speed))
    args.extend(shlex.split(os.environ.get("MYTHIFY_TRIAGE_CODEX_ARGS", "")))
    args.append("-")
    result = run_triage_process(args, cwd, prompt, timeout, triage_shell_env(model, speed))
    try:
        if output_path.exists():
            result["output_tail"] = tail_text(output_path.read_text(encoding="utf-8"))
        else:
            result["output_tail"] = result["stdout_tail"]
    finally:
        try:
            output_path.unlink()
        except OSError:
            pass
    return result


def run_cursor_triage(prompt, model, timeout, cwd, speed="auto"):
    binary = resolve_triage_binary(
        ["cursor-agent", "cursor"],
        [
            "MYTHIFY_TRIAGE_CURSOR_BIN",
            "MYTHIFY_FANOUT_CURSOR_BIN",
            "MYTHIFY_FANOUT_CURSOR_AGENT_BIN",
        ],
    )
    if not binary:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "cursor-agent or cursor binary not found",
            "timed_out": False,
        }
    with tempfile.NamedTemporaryFile(prefix="mythify-cursor-triage-", suffix=".md", delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(prompt)
        prompt_path = Path(handle.name)
    args = [binary]
    if Path(binary).name == "cursor":
        args.append("agent")
    args.extend(["--print", "--output-format", "text", "--trust", "--workspace", str(cwd)])
    mode = os.environ.get("MYTHIFY_TRIAGE_CURSOR_MODE", "ask")
    if mode:
        args.extend(["--mode", mode])
    if model:
        args.extend(["--model", model])
    if os.environ.get("MYTHIFY_TRIAGE_CURSOR_FORCE", "") == "1":
        args.append("--force")
    args.extend(shlex.split(os.environ.get("MYTHIFY_TRIAGE_CURSOR_ARGS", "")))
    args.append("Read the triage prompt from this file and return only the requested JSON: {0}".format(prompt_path))
    result = run_triage_process(args, cwd, "", timeout, triage_shell_env(model, speed))
    result["output_tail"] = result["stdout_tail"]
    try:
        prompt_path.unlink()
    except OSError:
        pass
    return result


def run_command_triage(prompt, model, timeout, cwd, speed="auto"):
    command = command_triage_template()
    if not command:
        return {
            "exit_code": 127,
            "duration_seconds": 0,
            "stdout_tail": "",
            "stderr_tail": "MYTHIFY_TRIAGE_COMMAND is not set",
            "timed_out": False,
        }
    result = run_triage_process(command, cwd, prompt, timeout, triage_shell_env(model, speed), shell=True)
    result["output_tail"] = result["stdout_tail"]
    return result


def run_model_triage(task_text, classification, args):
    if not should_run_model_triage(classification, args.triage):
        return {
            "attempted": False,
            "reason": "triage mode {0} with gate {1}".format(
                args.triage, classification.get("model_triage")
            ),
        }
    platform = normalize_platform(getattr(args, "platform", "auto"))
    engine, engine_policy = select_triage_engine(
        getattr(args, "triage_engine", ""), platform
    )
    if not engine:
        return {
            "attempted": True,
            "ok": False,
            "engine": "",
            "engine_policy": engine_policy,
            "model": "",
            "model_policy": "unavailable",
            "effort": "low",
            "speed": "auto",
            "duration_seconds": 0,
            "exit_code": 127,
            "error": (
                "No fast triage engine is available. Configure a local engine with "
                "MYTHIFY_TRIAGE_ENGINE plus the matching CLI login, or set "
                "MYTHIFY_TRIAGE_COMMAND for a command that reads the prompt on stdin."
            ),
            "output_tail": "",
            "parsed": None,
        }
    if engine not in TRIAGE_ENGINES:
        return {
            "attempted": True,
            "ok": False,
            "engine": engine,
            "engine_policy": engine_policy,
            "model": "",
            "model_policy": "unavailable",
            "effort": "low",
            "speed": "auto",
            "duration_seconds": 0,
            "exit_code": 127,
            "error": "Unknown triage engine {0}. Valid engines: {1}.".format(
                engine, ", ".join(TRIAGE_ENGINES)
            ),
            "output_tail": "",
            "parsed": None,
        }
    model, model_policy = resolve_triage_model_selection(
        engine, getattr(args, "triage_model", "")
    )
    effort, effort_policy = effort_for_role(
        "triage", classification, getattr(args, "effort", "auto")
    )
    speed, speed_policy = speed_for_role(
        "triage", getattr(args, "speed", "auto")
    )
    prompt = build_triage_prompt(task_text, classification)
    cwd = Path.cwd()
    if engine == "claude-cli":
        raw = run_claude_triage(prompt, model, args.triage_timeout, cwd, speed)
    elif engine == "codex-cli":
        raw = run_codex_triage(prompt, model, args.triage_timeout, cwd, speed)
    elif engine == "cursor-agent":
        raw = run_cursor_triage(prompt, model, args.triage_timeout, cwd, speed)
    else:
        raw = run_command_triage(prompt, model, args.triage_timeout, cwd, speed)
    output_tail = raw.get("output_tail", raw.get("stdout_tail", ""))
    parsed = parse_model_triage_json(output_tail)
    ok = raw["exit_code"] == 0 and parsed is not None
    error = ""
    if raw["exit_code"] != 0:
        error = raw.get("stderr_tail") or "triage worker exited {0}".format(raw["exit_code"])
    elif parsed is None:
        error = "triage worker exited 0 but did not return valid JSON"
    return {
        "attempted": True,
        "ok": ok,
        "engine": engine,
        "engine_policy": engine_policy,
        "model": model,
        "model_policy": model_policy,
        "effort": effort,
        "effort_policy": effort_policy,
        "speed": speed,
        "speed_policy": speed_policy,
        "duration_seconds": raw.get("duration_seconds", 0),
        "exit_code": raw["exit_code"],
        "error": error,
        "output_tail": output_tail,
        "stderr_tail": raw.get("stderr_tail", ""),
        "timed_out": raw.get("timed_out", False),
        "parsed": parsed,
    }


# ---------------------------------------------------------------------------
# Lesson store
# ---------------------------------------------------------------------------

def lesson_filename(title):
    base = slugify(title)[:50] or "lesson"
    return base + "-" + now_stamp() + ".json"


def write_lesson(directory, title, detail, tags):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    record = {"title": title, "detail": detail, "tags": list(tags), "created": now_iso()}
    path = directory / lesson_filename(title)
    write_json_atomic(path, record)
    return path


def load_lessons(directory, scope_label):
    items = []
    directory = Path(directory)
    if not directory.is_dir():
        return items
    for path in sorted(directory.glob("*.json")):
        record = read_json(path, None)
        if isinstance(record, dict) and record.get("title") is not None:
            items.append((scope_label, record))
    return items


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
        print("[WARN] Already inside a Mythify workspace: {0}. Nothing to do.".format(existing))
        return 0
    state = Path.cwd() / WORKSPACE_DIR_NAME
    ensure_layout(state)
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
    result["model_policy"] = build_model_policy(result, args)
    if args.triage != "never":
        result["model_triage_run"] = run_model_triage(args.task, result, args)
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(format_classification(result))
    return 0


def cmd_host_model_switch(args, state):
    if not str(args.target_model or "").strip():
        fail("[FAIL] host-model switch requires TARGET_MODEL.")
        return 1
    record = build_host_model_record(args)
    write_json_atomic(host_model_path(state), record)
    if args.json_output:
        print(json.dumps(record, indent=2))
    else:
        print(format_host_model_record(record))
    return 0


def cmd_host_model_status(args, state):
    record = read_host_model_state(state)
    if record is None:
        empty = {"status": "unset", "target_model": "", "source": "unknown"}
        if args.json_output:
            print(json.dumps(empty, indent=2))
        else:
            print("[OK] No host model switch is recorded.")
        return 0
    if args.json_output:
        print(json.dumps(record, indent=2))
    else:
        print(format_host_model_record(record))
    return 0


def cmd_host_model_clear(args, state):
    try:
        host_model_path(state).unlink()
    except FileNotFoundError:
        pass
    if args.json_output:
        print(json.dumps({"status": "cleared", "target_model": ""}, indent=2))
    else:
        print("[OK] Host model switch record cleared.")
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
    if args.status == "completed" and os.environ.get(
        "MYTHIFY_REQUIRE_VERIFIED_STEP"
    ) == "1":
        lower_bound = step.get("updated_at") or plan.get("created", "")
        records = read_jsonl(state / "verifications.jsonl")
        satisfied = any(
            record.get("kind") == "executed"
            and record.get("verified") is True
            and str(record.get("timestamp", "")) >= lower_bound
            and verification_record_matches_step(record, slug, step_id)
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


def cmd_memory_set(args, state):
    memory = load_memory(state)
    stamp = now_iso()
    for entry in memory["entries"]:
        if entry.get("key") == args.key:
            entry["value"] = args.value
            entry["category"] = args.category
            entry["timestamp"] = stamp
            break
    else:
        memory["entries"].append(
            {
                "key": args.key,
                "value": args.value,
                "category": args.category,
                "timestamp": stamp,
            }
        )
    save_memory(state, memory)
    print("[OK] Stored memory entry: {0} (category: {1})".format(args.key, args.category))
    return 0


def cmd_memory_get(args, state):
    memory = load_memory(state)
    entries = memory["entries"]
    if not entries:
        print("No memory entries yet.")
        return 0
    query = (args.query or "").lower()
    matches = []
    for entry in entries:
        if args.category and entry.get("category") != args.category:
            continue
        if query:
            haystack = (
                str(entry.get("key", "")).lower() + "\n" + str(entry.get("value", "")).lower()
            )
            if query not in haystack:
                continue
        matches.append(entry)
    if not matches:
        print("No matching memory entries.")
        return 0
    print("[OK] Memory entries ({0}):".format(len(matches)))
    for entry in matches:
        print(
            "  [{0}] {1} = {2} ({3})".format(
                entry.get("category"), entry.get("key"), entry.get("value"),
                entry.get("timestamp"),
            )
        )
    return 0


def cmd_memory_clear(args, state):
    if not args.key and not args.clear_all:
        fail(MEMORY_CLEAR_CLI_REFUSAL)
        return 1
    memory = load_memory(state)
    if args.clear_all:
        removed = len(memory["entries"])
        memory["entries"] = []
        save_memory(state, memory)
        print("[OK] Cleared all memory entries ({0} removed).".format(removed))
        return 0
    before = len(memory["entries"])
    memory["entries"] = [e for e in memory["entries"] if e.get("key") != args.key]
    if len(memory["entries"]) == before:
        print("[WARN] No memory entry with key: {0}".format(args.key))
        return 0
    save_memory(state, memory)
    print("[OK] Cleared memory entry: {0}".format(args.key))
    return 0


def cmd_lesson_add(args, state):
    tags = []
    if args.tags:
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
    if args.global_scope:
        directory = global_lessons_dir()
        scope = "global"
    else:
        directory = state / "lessons"
        scope = "project"
    write_lesson(directory, args.title, args.detail, tags)
    print("[OK] Lesson recorded ({0}): {1}".format(scope, args.title))
    return 0


def cmd_lesson_list(args, state):
    items = []
    if args.scope in ("project", "all"):
        items.extend(load_lessons(state / "lessons", "project"))
    if args.scope in ("global", "all"):
        items.extend(load_lessons(global_lessons_dir(), "global"))
    if args.tag:
        items = [
            (scope, record)
            for scope, record in items
            if args.tag in (record.get("tags") or [])
        ]
    items.sort(key=lambda item: str(item[1].get("created", "")))
    if not items:
        print("No lessons recorded.")
        return 0
    print("[OK] Lessons ({0}):".format(len(items)))
    for scope, record in items:
        line = "  ({0}) {1}: {2}".format(scope, record.get("title"), record.get("detail"))
        tags = record.get("tags") or []
        if tags:
            line += " [tags: {0}]".format(", ".join(tags))
        print(line)
    return 0


def cmd_outcome_start(args, state):
    if args.max_iterations < 1:
        print("[FAIL] outcome start requires --max-iterations >= 1.")
        return 1
    base = args.name or args.goal
    slug = slugify(base) or "outcome"
    original = slug
    counter = 2
    while outcome_goal_path(state, slug).exists():
        slug = "{0}-{1}".format(original[:36], counter)
        counter += 1
    now = now_iso()
    goal = {
        "id": slug,
        "goal": args.goal,
        "success_criteria": args.success,
        "verify_command": args.verify,
        "metric_command": args.metric or "",
        "max_iterations": args.max_iterations,
        "iteration_count": 0,
        "allowed_paths": parse_allowed_paths(args.allowed_paths),
        "visibility": args.visibility,
        "status": "active",
        "created": now,
        "updated": now,
        "last_verified": None,
        "best_metric_score": None,
        "stop_reason": None,
    }
    save_outcome(state, slug, goal)
    set_active_outcome_slug(state, slug)
    if args.json_output:
        print(json.dumps(goal, indent=2))
    else:
        print("[OK] Outcome started: {0}".format(slug))
        print("goal: {0}".format(args.goal))
        print("success: {0}".format(args.success))
        print("verify: {0}".format(args.verify))
        if args.metric:
            print("metric: {0}".format(args.metric))
        print("iterations: 0/{0}".format(args.max_iterations))
        print("next: make a bounded attempt, then run outcome check.")
    return 0


def cmd_outcome_status(args, state):
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    iterations = read_jsonl(outcome_iterations_path(state, slug))
    if args.json_output:
        print(json.dumps({"goal": goal, "iterations": iterations}, indent=2))
    else:
        print(format_outcome_status(slug, goal, iterations))
    return 0


def cmd_outcome_check(args, state):
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    if goal.get("status") in ("succeeded", "failed", "stopped"):
        if args.json_output:
            print(json.dumps({"goal": goal, "record": None}, indent=2))
        else:
            print("[OK] Outcome {0} is already {1}.".format(slug, goal.get("status")))
        return 0 if goal.get("status") == "succeeded" else 2
    iteration_count = int(goal.get("iteration_count", 0))
    max_iterations = int(goal.get("max_iterations", 1))
    if iteration_count >= max_iterations:
        goal["status"] = "failed"
        goal["stop_reason"] = "iteration budget exhausted before check"
        goal["updated"] = now_iso()
        save_outcome(state, slug, goal)
        if args.json_output:
            print(json.dumps({"goal": goal, "record": None}, indent=2))
        else:
            print("[FAIL] Outcome {0} failed: iteration budget exhausted.".format(slug))
        return 2
    verify = run_shell_capture(goal["verify_command"], args.timeout)
    metric_record = None
    metric_ok = True
    metric_score = None
    if goal.get("metric_command"):
        metric = run_shell_capture(goal["metric_command"], args.timeout)
        metric_ok = metric["verified"]
        metric_score = parse_metric_score(metric.get("stdout_tail", ""))
        metric_record = {
            "command": metric["command"],
            "exit_code": metric["exit_code"],
            "duration_seconds": metric["duration_seconds"],
            "stdout_tail": metric["stdout_tail"],
            "stderr_tail": metric["stderr_tail"],
            "verified": metric["verified"],
            "score": metric_score,
        }
    verified = bool(verify["verified"] and metric_ok)
    next_iteration = iteration_count + 1
    if verified:
        status_after = "succeeded"
        next_action = "Outcome met. Report the evidence and stop."
    elif next_iteration >= max_iterations:
        status_after = "failed"
        next_action = "Iteration budget exhausted. Summarize the blocker and stop."
    else:
        status_after = "active"
        next_action = (
            "Outcome not met. Inspect verifier output, make another bounded attempt, "
            "then run outcome check again."
        )
    record = {
        "iteration": next_iteration,
        "timestamp": now_iso(),
        "notes": args.notes or "",
        "verify": {
            "command": verify["command"],
            "exit_code": verify["exit_code"],
            "duration_seconds": verify["duration_seconds"],
            "stdout_tail": verify["stdout_tail"],
            "stderr_tail": verify["stderr_tail"],
            "verified": verify["verified"],
        },
        "metric": metric_record,
        "verified": verified,
        "status_after": status_after,
        "next_action": next_action,
    }
    append_jsonl(outcome_iterations_path(state, slug), record)
    goal["iteration_count"] = next_iteration
    goal["status"] = status_after
    goal["last_verified"] = verified
    goal["updated"] = record["timestamp"]
    if metric_score is not None:
        best = goal.get("best_metric_score")
        if best is None or metric_score > best:
            goal["best_metric_score"] = metric_score
    if status_after == "failed":
        goal["stop_reason"] = "iteration budget exhausted"
    if status_after == "succeeded":
        goal["stop_reason"] = "success criteria verified"
    save_outcome(state, slug, goal)
    verification_record = {
        "kind": "executed",
        "claim": "Outcome {0}: {1}".format(slug, goal.get("success_criteria", "")),
        "command": goal["verify_command"],
        "exit_code": verify["exit_code"],
        "duration_seconds": verify["duration_seconds"],
        "stdout_tail": verify["stdout_tail"],
        "stderr_tail": verify["stderr_tail"],
        "verified": verify["verified"],
        "timestamp": record["timestamp"],
        "outcome": slug,
        "iteration": next_iteration,
    }
    verification_record.update(verification_step_context(state))
    append_jsonl(state / "verifications.jsonl", verification_record)
    if args.json_output:
        print(json.dumps({"goal": goal, "record": record}, indent=2))
    else:
        prefix = "[OK]" if verified else "[FAIL]"
        print(
            "{0} Outcome {1} iteration {2}/{3}: {4}".format(
                prefix, slug, next_iteration, max_iterations, status_after
            )
        )
        print("verify exit: {0}".format(verify["exit_code"]))
        if metric_record:
            print("metric exit: {0}".format(metric_record["exit_code"]))
            if metric_score is not None:
                print("metric score: {0}".format(metric_score))
        print("next: {0}".format(next_action))
        if verify["stdout_tail"]:
            print("--- verify stdout (tail) ---")
            print(verify["stdout_tail"])
        if verify["stderr_tail"]:
            print("--- verify stderr (tail) ---")
            print(verify["stderr_tail"])
    return 0 if verified else 2


def cmd_outcome_results(args, state):
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    iterations = read_jsonl(outcome_iterations_path(state, slug))
    if args.json_output:
        print(json.dumps({"goal": goal, "iterations": iterations}, indent=2))
        return 0
    print(format_outcome_status(slug, goal, iterations))
    for item in iterations:
        print("")
        print(
            "iteration {0}: verified={1}, status={2}".format(
                item.get("iteration"), item.get("verified"), item.get("status_after")
            )
        )
        verify = item.get("verify") or {}
        print("  verify exit: {0}".format(verify.get("exit_code")))
        metric = item.get("metric")
        if metric:
            print("  metric exit: {0}".format(metric.get("exit_code")))
            if metric.get("score") is not None:
                print("  metric score: {0}".format(metric.get("score")))
    return 0 if goal.get("status") == "succeeded" else 2


def cmd_outcome_stop(args, state):
    slug, goal = load_outcome(state, args.name)
    if not slug or goal is None:
        print("[FAIL] No outcome found. Start one with outcome start.")
        return 1
    goal["status"] = "stopped"
    goal["stop_reason"] = args.reason
    goal["updated"] = now_iso()
    save_outcome(state, slug, goal)
    clear_active_outcome_slug(state, slug)
    if args.json_output:
        print(json.dumps(goal, indent=2))
    else:
        print("[OK] Outcome {0} stopped: {1}".format(slug, args.reason))
    return 0


def _coerce_stream_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def cmd_verify_run(args, state):
    if os.environ.get("MYTHIFY_DISABLE_RUN") == "1":
        fail(VERIFY_RUN_DISABLED_MESSAGE)
        return 2
    timeout = args.timeout
    started = datetime.now(timezone.utc)
    timed_out = False
    try:
        completed = subprocess.run(
            args.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = _coerce_stream_text(exc.stdout)
        stderr = _coerce_stream_text(exc.stderr)
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    stdout_tail = stdout[-TAIL_CHARS:]
    stderr_tail = stderr[-TAIL_CHARS:]
    if timed_out:
        notice = "(timed out after {0:g} seconds)".format(timeout)
        stderr_tail = (stderr_tail + "\n" + notice) if stderr_tail else notice
    verified = (not timed_out) and exit_code == 0
    record = {
        "kind": "executed",
        "claim": args.claim,
        "command": args.command,
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "verified": verified,
        "timestamp": now_iso(),
    }
    record.update(verification_step_context(state))
    append_jsonl(state / "verifications.jsonl", record)
    label = args.claim or args.command
    if verified:
        print("[OK] VERIFIED: {0} (exit {1}, {2:.2f}s)".format(label, exit_code, duration))
        return 0
    print("[FAIL] UNVERIFIED: {0} (exit {1}, {2:.2f}s)".format(label, exit_code, duration))
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


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="mythify.py",
        description=(
            "Mythify v2: plans with verified steps, executed verification, "
            "persistent memory, lessons, and structured reflections. State lives "
            "in the nearest .mythify directory or in MYTHIFY_DIR."
        ),
    )
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
        help="Comma-separated path scope for host edits; recorded for policy.",
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
        help="Update a step's status; completed and failed require RESULT evidence.",
        description=(
            "Update step ID to STATUS (pending, in_progress, completed, failed, "
            "skipped). completed and failed require the RESULT argument: evidence "
            "or a failure description. Prints the next pending step afterward."
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
