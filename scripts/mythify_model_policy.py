"""Model policy and triage helpers for Mythify.

The CLI owns state lookup and command dispatch. This module owns model policy
construction, provider-default metadata, and bounded model triage runners.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from mythify_classification import build_triage_prompt, should_run_model_triage
from mythify_host_model import PLATFORMS
from mythify_model_triage import configure_model_triage, run_model_triage

TRIAGE_OUTPUT_TAIL_CHARS = 4000

TRIAGE_ENGINES = ("claude-cli", "codex-cli", "cursor-agent", "command")
TRIAGE_MODES = ("never", "auto", "always")
EFFORT_LEVELS = ("auto", "low", "medium", "high")
SPAWN_CEILINGS = ("auto", "lower_only", "same_or_lower", "allow_stronger")
REVIEWER_STRENGTH_MODES = ("auto", "same_or_lower", "allow_stronger")
FANOUT_VISIBILITY_MODES = ("auto", "quiet", "summary", "verbose", "threaded")
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
ROLE_PROVIDER_ORDER = (
    "session",
    "triage",
    "reader",
    "fanout_worker",
    "reviewer",
    "verifier",
)
ROLE_PROVIDER_ENV_NAMES = {
    "session": "MYTHIFY_ROLE_SESSION_PROVIDER",
    "triage": "MYTHIFY_ROLE_TRIAGE_PROVIDER",
    "reader": "MYTHIFY_ROLE_READER_PROVIDER",
    "fanout_worker": "MYTHIFY_ROLE_WORKER_PROVIDER",
    "reviewer": "MYTHIFY_ROLE_REVIEWER_PROVIDER",
    "verifier": "MYTHIFY_ROLE_VERIFIER_PROVIDER",
}
ROLE_PROVIDER_DEFAULTS = {
    "session": "host",
    "triage": "host_cli",
    "reader": "local_openai_compatible",
    "fanout_worker": "host_cli",
    "reviewer": "host_cli",
    "verifier": "local_command",
}
ROLE_PROVIDER_ALLOWED = {
    "session": ("host",),
    "triage": ("host_cli", "local_openai_compatible", "command"),
    "reader": ("local_openai_compatible", "host"),
    "fanout_worker": ("host_cli", "api_provider", "command"),
    "reviewer": ("host_cli", "api_provider", "command"),
    "verifier": ("local_command",),
}
ROLE_PROVIDER_FALLBACK_POLICY = "no_implicit_cross_provider_fallback"
HOSTED_PROVIDER_FANOUT_ENGINES = ("anthropic", "openai")
HOSTED_PROVIDER_REQUIRED_ACKS = (
    "hosted_provider_billing_ack",
    "hosted_provider_data_ack",
    "hosted_provider_material_ack",
)
ROLE_TIMEOUT_METADATA_FIELDS = (
    "timeout_seconds",
    "timeout_source",
    "timeout_enforced_by",
    "can_override",
)
ROLE_COST_METADATA_FIELDS = (
    "billing",
    "cost_estimate_supported",
    "cost_estimate_status",
    "cost_estimate_cents",
    "pricing_url",
    "usage_metadata_fields",
)
ADAPTER_INTERFACE_VERSION = 1
ADAPTER_INTERFACE_FIELDS = (
    "id",
    "kind",
    "status",
    "locality",
    "openai_compatible",
    "probe_supported",
    "run_supported",
    "execution_enabled",
    "writes_state",
    "evidence_status",
    "material_not_evidence",
    "billing",
    "roles",
    "guardrails",
)
ADAPTER_INTERFACE_LANES = (
    "host",
    "desktop_agent",
    "model_provider",
    "api_provider",
    "custom_adapter",
    "execution_substrate",
    "agent_lifecycle",
)
ROLE_ASSIGNMENT_VERSION = 1
ROLE_ASSIGNMENT_ADAPTER_LANES = {
    "session": ("host",),
    "triage": ("host", "model_provider", "custom_adapter"),
    "reader": ("host", "model_provider"),
    "fanout_worker": ("host", "api_provider", "custom_adapter"),
    "reviewer": ("host", "api_provider", "custom_adapter"),
    "verifier": (),
}
ROLE_ASSIGNMENT_EXTRA_ROLES = {
    "remote_execution": {
        "status": "metadata_supported",
        "default_provider": None,
        "selected_provider": None,
        "provider_source": "not_enabled",
        "allowed_providers": [],
        "eligible_adapter_lanes": ("execution_substrate",),
        "adapter_interface_role": "remote_execution",
        "assignment_order": ("future_explicit_role_input", "built_in_disabled"),
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "execution_policy": "guarded_explicit_acknowledgement_only",
        "runtime_routing_changed": False,
        "writes_state_allowed": False,
        "material_not_evidence_required": True,
        "required_evidence_status": "remote_output_not_verification",
        "eligible_candidate_ids": (),
        "required_acknowledgements": (
            "billing_ack_required",
            "data_movement_ack_required",
            "cleanup_ack_required",
        ),
        "guardrails": (
            ROLE_PROVIDER_FALLBACK_POLICY,
            "explicit_acknowledgements_required",
            "material_not_verification",
            "no_mythify_state_write",
        ),
    },
    "agent_lifecycle": {
        "status": "metadata_supported",
        "default_provider": None,
        "selected_provider": None,
        "provider_source": "not_enabled",
        "allowed_providers": [],
        "eligible_adapter_lanes": ("agent_lifecycle",),
        "adapter_interface_role": "agent_lifecycle",
        "assignment_order": ("future_explicit_role_input", "built_in_disabled"),
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "execution_policy": "probe_only_no_eval_or_deploy",
        "runtime_routing_changed": False,
        "writes_state_allowed": False,
        "material_not_evidence_required": True,
        "required_evidence_status": "lifecycle_probe_output_not_verification",
        "eligible_candidate_ids": (),
        "guardrails": (
            ROLE_PROVIDER_FALLBACK_POLICY,
            "probe_only",
            "no_eval_execution",
            "no_deploy",
            "no_publish",
            "no_cloud_mutation",
            "no_mythify_state_write",
            "material_not_verification",
        ),
    },
}
ROLE_TIMEOUT_DEFAULTS = {
    "session": {
        "timeout_seconds": None,
        "timeout_source": "host_controlled",
        "timeout_enforced_by": "host",
        "can_override": False,
    },
    "triage": {
        "timeout_seconds": 120,
        "timeout_source": "triage_timeout_seconds_or_default",
        "timeout_enforced_by": "triage_worker",
        "can_override": True,
    },
    "reader": {
        "timeout_seconds": 30,
        "timeout_source": "local_model_run_default",
        "timeout_enforced_by": "local_model_run",
        "can_override": True,
    },
    "fanout_worker": {
        "timeout_seconds": 600,
        "timeout_source": "fanout_start_or_env_or_default",
        "timeout_enforced_by": "fanout_worker",
        "can_override": True,
    },
    "reviewer": {
        "timeout_seconds": 600,
        "timeout_source": "fanout_start_or_env_or_default",
        "timeout_enforced_by": "fanout_reviewer",
        "can_override": True,
    },
    "verifier": {
        "timeout_seconds": 300,
        "timeout_source": "verify_run_default",
        "timeout_enforced_by": "verify_run",
        "can_override": True,
    },
}
ROLE_PROVIDER_PROFILES = {
    "host": {
        "status": "supported",
        "allowed_roles": ("session", "reader"),
        "default_roles": ("session",),
        "control": "host_selected",
        "billing": "host_account_or_subscription",
        "execution_enabled": True,
        "writes_state": False,
        "evidence_status": "host_output_not_verification",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
    "host_cli": {
        "status": "supported",
        "allowed_roles": ("triage", "fanout_worker", "reviewer"),
        "default_roles": ("triage", "fanout_worker", "reviewer"),
        "control": "bounded_worker",
        "billing": "host_cli_subscription_or_local_quota",
        "execution_enabled": True,
        "writes_state": False,
        "evidence_status": "worker_output_not_verification",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
    "local_openai_compatible": {
        "status": "supported",
        "allowed_roles": ("triage", "reader"),
        "default_roles": ("reader",),
        "control": "localhost_model_provider",
        "billing": "local_compute",
        "execution_enabled": True,
        "writes_state": False,
        "evidence_status": "model_output_not_verification",
        "local_only": True,
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
    "api_provider": {
        "status": "metadata_supported",
        "allowed_roles": ("fanout_worker", "reviewer"),
        "default_roles": (),
        "control": "hosted_provider",
        "billing": "metered_external_account",
        "execution_enabled": False,
        "writes_state": False,
        "evidence_status": "provider_output_not_verification",
        "explicit_enable_required": True,
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
    "command": {
        "status": "supported",
        "allowed_roles": ("triage", "fanout_worker", "reviewer"),
        "default_roles": (),
        "control": "explicit_command",
        "billing": "user_defined",
        "execution_enabled": True,
        "writes_state": False,
        "evidence_status": "command_output_not_verification",
        "custom_adapter": "custom-command",
        "command_env": ("MYTHIFY_TRIAGE_COMMAND", "MYTHIFY_FANOUT_COMMAND"),
        "input_contract": "prompt_on_stdin",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
    "local_command": {
        "status": "supported",
        "allowed_roles": ("verifier",),
        "default_roles": ("verifier",),
        "control": "local_verifier",
        "billing": "local_compute",
        "execution_enabled": True,
        "writes_state": True,
        "evidence_status": "executed_verification",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
}
API_PROVIDER_COST_METADATA_FIELDS = (
    "provider",
    "model",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "pricing_url",
)
API_PROVIDER_TIMEOUT_METADATA_FIELDS = (
    "provider",
    "timeout_seconds",
    "timeout_source",
)
API_PROVIDER_METADATA = {
    "anthropic-api": {
        "status": "metadata_supported",
        "protocol": "anthropic_messages",
        "openai_compatible": False,
        "default_base_url": "https://api.anthropic.com/v1",
        "base_url_env": "",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_env": "MYTHIFY_ANTHROPIC_API_MODEL",
        "auth_header": "x-api-key",
        "version_header": "anthropic-version:2023-06-01",
        "billing": "metered_external_account",
        "explicit_enable_required": True,
        "execution_enabled": False,
        "default_timeout_seconds": 600,
        "cost_metadata_supported": True,
        "pricing_url": "https://docs.anthropic.com/en/docs/about-claude/pricing",
        "pricing_url_env": "",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
    "openai-api": {
        "status": "metadata_supported",
        "protocol": "openai_responses_or_chat",
        "openai_compatible": True,
        "default_base_url": "https://api.openai.com/v1",
        "base_url_env": "",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "MYTHIFY_OPENAI_API_MODEL",
        "auth_header": "authorization_bearer",
        "version_header": "",
        "billing": "metered_external_account",
        "explicit_enable_required": True,
        "execution_enabled": False,
        "default_timeout_seconds": 600,
        "cost_metadata_supported": True,
        "pricing_url": "https://openai.com/api/pricing/",
        "pricing_url_env": "",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
    },
    "openai-compatible-hosted": {
        "status": "metadata_supported",
        "protocol": "openai_compatible",
        "openai_compatible": True,
        "default_base_url": "",
        "base_url_env": "MYTHIFY_HOSTED_OPENAI_COMPAT_BASE_URL",
        "api_key_env": "MYTHIFY_HOSTED_OPENAI_COMPAT_API_KEY",
        "model_env": "MYTHIFY_HOSTED_OPENAI_COMPAT_MODEL",
        "auth_header": "authorization_bearer",
        "version_header": "",
        "billing": "metered_external_account",
        "explicit_enable_required": True,
        "execution_enabled": False,
        "default_timeout_seconds": 600,
        "cost_metadata_supported": True,
        "pricing_url": "",
        "pricing_url_env": "MYTHIFY_HOSTED_OPENAI_COMPAT_PRICING_URL",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
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


def tail_text(text, limit=TRIAGE_OUTPUT_TAIL_CHARS):
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
    configured = os.environ.get("MYTHIFY_HOST_PLATFORM", "").strip()
    if configured and configured != "auto":
        return configured if configured in PLATFORMS else "unknown"
    origin = os.environ.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "").lower()
    if (
        "codex" in origin
        or os.environ.get("CODEX_SHELL")
        or os.environ.get("CODEX_THREAD_ID", "").strip()
    ):
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


def select_worker_engine(platform):
    env_engine = os.environ.get("MYTHIFY_FANOUT_ENGINE", "").strip()
    if env_engine:
        return env_engine, "env"
    preferred = preferred_local_engine(platform)
    if preferred and triage_engine_available(preferred):
        return preferred, "platform_preferred"
    return "auto", "local_first"


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


def resolve_session_model(session_model, host_model_record=None):
    explicit = (session_model or "").strip()
    if explicit:
        return explicit, "explicit"
    env_model = os.environ.get("MYTHIFY_SESSION_MODEL", "").strip()
    if env_model:
        return env_model, "env"
    if host_model_record:
        return str(host_model_record.get("target_model", "")).strip(), "host_model_switch"
    return "", "unknown"


def resolve_spawn_ceiling(spawn_ceiling):
    explicit = (spawn_ceiling or "auto").strip()
    if explicit and explicit != "auto":
        return explicit, "explicit"
    env_ceiling = os.environ.get("MYTHIFY_SPAWN_CEILING", "").strip()
    if env_ceiling in SPAWN_CEILINGS and env_ceiling != "auto":
        return env_ceiling, "env"
    return "same_or_lower", "default"


def resolve_reviewer_strength(reviewer_strength):
    explicit = (reviewer_strength or "auto").strip()
    if explicit and explicit != "auto":
        return explicit, "explicit"
    env_strength = os.environ.get("MYTHIFY_REVIEWER_STRENGTH", "").strip()
    if env_strength in REVIEWER_STRENGTH_MODES and env_strength != "auto":
        return env_strength, "env"
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


def resolve_role_provider(role):
    default_provider = ROLE_PROVIDER_DEFAULTS[role]
    allowed = ROLE_PROVIDER_ALLOWED[role]
    env_name = ROLE_PROVIDER_ENV_NAMES[role]
    requested = os.environ.get(env_name, "").strip()
    status = "selected"
    provider = default_provider
    source = "built_in"
    if requested:
        if requested in allowed:
            provider = requested
            source = "env:" + env_name
        else:
            status = "invalid_env_ignored"
    return {
        "role": role,
        "provider": provider,
        "provider_source": source,
        "default_provider": default_provider,
        "allowed_providers": list(allowed),
        "requested_provider": requested or None,
        "status": status,
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "provider_profile": role_provider_profile(provider),
        "selection": "advisory_metadata_only",
    }


def role_provider_profile(provider):
    profile = dict(ROLE_PROVIDER_PROFILES.get(provider, {}))
    for key in ("allowed_roles", "default_roles"):
        if key in profile:
            profile[key] = list(profile[key])
    if profile:
        profile["fallback_policy"] = profile.get(
            "fallback_policy", ROLE_PROVIDER_FALLBACK_POLICY
        )
    return profile


def role_provider_catalog():
    return {
        provider: role_provider_profile(provider)
        for provider in sorted(ROLE_PROVIDER_PROFILES)
    }


def api_provider_contract():
    return {
        "version": 1,
        "status": "metadata_supported",
        "execution_enabled": False,
        "fanout_execution_enabled": True,
        "fanout_engines": list(HOSTED_PROVIDER_FANOUT_ENGINES),
        "required_fanout_acknowledgements": list(HOSTED_PROVIDER_REQUIRED_ACKS),
        "fanout_audit_log": ".mythify/provider-audit.jsonl",
        "fanout_output_material_status": "material_not_verification",
        "billing_policy": "explicit_provider_required",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "timeout_metadata_fields": list(API_PROVIDER_TIMEOUT_METADATA_FIELDS),
        "cost_metadata_fields": list(API_PROVIDER_COST_METADATA_FIELDS),
        "providers": {
            name: dict(API_PROVIDER_METADATA[name])
            for name in sorted(API_PROVIDER_METADATA)
        },
    }


def custom_adapter_contract():
    return {
        "version": 1,
        "status": "metadata_supported",
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "execution_policy": "explicit_only_no_hidden_fallback",
        "evidence_status": "adapter_output_not_verification",
        "command": {
            "adapter": "custom-command",
            "status": "bounded_execution_supported",
            "execution_enabled": True,
            "tools": [
                "classify_task triage_engine=command",
                "fanout_start engine=command",
            ],
            "command_env": ["MYTHIFY_TRIAGE_COMMAND", "MYTHIFY_FANOUT_COMMAND"],
            "input_contract": "prompt_on_stdin",
            "default_timeout_seconds": {
                "triage": ROLE_TIMEOUT_DEFAULTS["triage"]["timeout_seconds"],
                "fanout_worker": ROLE_TIMEOUT_DEFAULTS["fanout_worker"]["timeout_seconds"],
                "reviewer": ROLE_TIMEOUT_DEFAULTS["reviewer"]["timeout_seconds"],
            },
            "billing": "user_defined",
            "writes_state": False,
            "output_is_evidence": False,
            "evidence_status": "command_output_not_verification",
        },
        "http": {
            "adapter": "custom-http",
            "status": "metadata_only",
            "execution_enabled": False,
            "explicit_enable_required": True,
            "base_url_env": "MYTHIFY_CUSTOM_HTTP_BASE_URL",
            "api_key_env": "MYTHIFY_CUSTOM_HTTP_API_KEY",
            "model_env": "MYTHIFY_CUSTOM_HTTP_MODEL",
            "pricing_url_env": "MYTHIFY_CUSTOM_HTTP_PRICING_URL",
            "required_before_execution": [
                "method_allowlist",
                "auth_from_env_only",
                "bounded_timeout",
                "request_body_template",
                "response_extraction",
                "cost_metadata",
                "no_state_write",
                "material_not_evidence",
            ],
            "billing": "metered_external_account_or_user_defined",
            "writes_state": False,
            "output_is_evidence": False,
            "evidence_status": "http_output_not_verification",
        },
    }


def adapter_interface_contract():
    return {
        "version": ADAPTER_INTERFACE_VERSION,
        "status": "metadata_supported",
        "fields": list(ADAPTER_INTERFACE_FIELDS),
        "lanes": list(ADAPTER_INTERFACE_LANES),
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "execution_policy": "metadata_shape_only_no_runtime_change",
        "guardrail": "interface_does_not_enable_fallback_or_state_writes",
    }


def role_assignment_core_contract(role, resolved_role):
    profile = role_provider_profile(resolved_role["provider"])
    evidence_status = profile.get("evidence_status", "unknown")
    guardrails = [
        ROLE_PROVIDER_FALLBACK_POLICY,
        "advisory_metadata_only",
        "no_hidden_provider_fallback",
    ]
    if evidence_status != "executed_verification":
        guardrails.append("material_not_verification")
    if role == "reviewer":
        guardrails.append("stronger_model_requires_explicit_opt_in")
    record = {
        "role": role,
        "status": "metadata_supported",
        "default_provider": resolved_role["default_provider"],
        "selected_provider": resolved_role["provider"],
        "provider_source": resolved_role["provider_source"],
        "allowed_providers": list(resolved_role["allowed_providers"]),
        "eligible_adapter_lanes": list(ROLE_ASSIGNMENT_ADAPTER_LANES[role]),
        "adapter_interface_role": role if role != "session" else "host_session",
        "assignment_order": ["future_explicit_role_input", "env", "built_in"],
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "execution_policy": "advisory_metadata_only_no_runtime_routing",
        "runtime_routing_changed": False,
        "writes_state_allowed": profile.get("writes_state") is True,
        "material_not_evidence_required": evidence_status != "executed_verification",
        "required_evidence_status": evidence_status,
        "eligible_candidate_ids": [],
        "guardrails": guardrails,
    }
    if role == "reviewer":
        record["stronger_model_policy"] = "explicit_opt_in_required"
    return record


def role_assignment_contract(resolved_roles):
    roles = {
        role: role_assignment_core_contract(role, resolved_roles[role])
        for role in ROLE_PROVIDER_ORDER
    }
    for role, record in ROLE_ASSIGNMENT_EXTRA_ROLES.items():
        roles[role] = {
            **record,
            "eligible_adapter_lanes": list(record["eligible_adapter_lanes"]),
            "assignment_order": list(record["assignment_order"]),
            "eligible_candidate_ids": list(record["eligible_candidate_ids"]),
            "guardrails": list(record["guardrails"]),
            **(
                {
                    "required_acknowledgements": list(
                        record["required_acknowledgements"]
                    )
                }
                if "required_acknowledgements" in record
                else {}
            ),
        }
    return {
        "version": ROLE_ASSIGNMENT_VERSION,
        "status": "metadata_supported",
        "source": "adapter_interface_contract",
        "assignment_order": ["future_explicit_role_input", "env", "built_in"],
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "execution_policy": "metadata_shape_only_no_runtime_change",
        "runtime_routing_changed": False,
        "guardrail": "role_contract_does_not_enable_hidden_fallback",
        "candidate_id_source": "mcp_adapter_interface_catalog_when_available",
        "roles": roles,
    }


def build_provider_defaults():
    roles = {
        role: resolve_role_provider(role)
        for role in ROLE_PROVIDER_ORDER
    }
    return {
        "version": 1,
        "precedence": ["future_explicit_role_input", "env", "built_in"],
        "fallback_policy": ROLE_PROVIDER_FALLBACK_POLICY,
        "timeout_metadata_fields": list(ROLE_TIMEOUT_METADATA_FIELDS),
        "cost_metadata_fields": list(ROLE_COST_METADATA_FIELDS),
        "provider_catalog": role_provider_catalog(),
        "adapter_interface_contract": adapter_interface_contract(),
        "role_assignment_contract": role_assignment_contract(roles),
        "api_provider_contract": api_provider_contract(),
        "custom_adapter_contract": custom_adapter_contract(),
        "roles": roles,
    }


def role_provider_fields(provider_defaults, role):
    provider = provider_defaults["roles"][role]
    return {
        "provider": provider["provider"],
        "provider_source": provider["provider_source"],
        "provider_default": provider["default_provider"],
        "provider_status": provider["status"],
        "provider_fallback_policy": provider["fallback_policy"],
        "provider_profile": provider.get("provider_profile", {}),
    }


def role_timeout_metadata(role, timeout_seconds=None, timeout_source=None):
    metadata = dict(ROLE_TIMEOUT_DEFAULTS[role])
    if timeout_seconds is not None:
        metadata["timeout_seconds"] = timeout_seconds
    if timeout_source is not None:
        metadata["timeout_source"] = timeout_source
    return metadata


def role_cost_metadata(provider_defaults, role, pricing_url=""):
    provider_record = provider_defaults["roles"][role]
    provider = provider_record["provider"]
    profile = provider_record.get("provider_profile", {})
    usage_fields = (
        list(API_PROVIDER_COST_METADATA_FIELDS)
        if provider == "api_provider"
        else []
    )
    return {
        "billing": profile.get("billing", "unknown"),
        "cost_estimate_supported": False,
        "cost_estimate_status": "not_estimated",
        "cost_estimate_cents": None,
        "pricing_url": pricing_url,
        "usage_metadata_fields": usage_fields,
    }


def role_budget_fields(provider_defaults, role, timeout_seconds=None, timeout_source=None, pricing_url=""):
    return {
        "timeout": role_timeout_metadata(role, timeout_seconds, timeout_source),
        "cost": role_cost_metadata(provider_defaults, role, pricing_url),
    }


def build_model_policy(classification, args, host_model_record=None):
    platform = normalize_platform(getattr(args, "platform", "auto"))
    requested_effort = getattr(args, "effort", "auto")
    requested_speed = getattr(args, "speed", "auto")
    session_model, session_model_source = resolve_session_model(
        getattr(args, "session_model", ""),
        host_model_record,
    )
    session_tier = classify_model_tier(session_model)
    spawn_ceiling, spawn_ceiling_source = resolve_spawn_ceiling(
        getattr(args, "spawn_ceiling", "auto")
    )
    reviewer_strength, reviewer_strength_source = resolve_reviewer_strength(
        getattr(args, "reviewer_strength", "auto")
    )
    triage_engine, triage_engine_source = select_triage_engine(
        getattr(args, "triage_engine", ""), platform
    )
    worker_engine, worker_engine_source = select_worker_engine(platform)
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
    provider_defaults = build_provider_defaults()
    return {
        "provider_defaults": provider_defaults,
        "session": {
            "role": "current_conversation",
            "control": "host_selected",
            "platform": platform,
            **role_provider_fields(provider_defaults, "session"),
            **role_budget_fields(provider_defaults, "session"),
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
            "stronger_requires": (
                "spawn_ceiling_allow_stronger_or_reviewer_specific_opt_in"
            ),
        },
        "triage": {
            "role": "problem_framing",
            "spawn": classification.get("model_triage", "skip"),
            **role_provider_fields(provider_defaults, "triage"),
            **role_budget_fields(
                provider_defaults,
                "triage",
                getattr(args, "triage_timeout", 120.0),
                "triage_timeout_seconds_or_default",
            ),
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
        "reader": {
            "role": "read_only_material_inspection",
            "spawn": "optional",
            **role_provider_fields(provider_defaults, "reader"),
            **role_budget_fields(provider_defaults, "reader"),
            "model_policy": "local_openai_compatible_when_configured",
            "model_relation_to_session": "lower_preferred",
            "effort": "low",
            "effort_policy": "role_default",
            "speed": "auto",
            "speed_policy": "provider_default",
            "writes_state": False,
            "evidence_status": "model_output_not_verification",
            "reason": (
                "Reader output is material for the orchestrator, not "
                "verification evidence."
            ),
        },
        "fanout_worker": {
            "role": "independent_subtask",
            "spawn": classification.get("fanout", "not_recommended"),
            **role_provider_fields(provider_defaults, "fanout_worker"),
            **role_budget_fields(provider_defaults, "fanout_worker"),
            "engine": worker_engine,
            "engine_policy": worker_engine_source,
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
            **role_provider_fields(provider_defaults, "reviewer"),
            **role_budget_fields(provider_defaults, "reviewer"),
            "engine": worker_engine,
            "engine_policy": worker_engine_source,
            "model_policy": "prefer_stronger_than_worker_when_available",
            "stronger_model_policy": reviewer_strength,
            "stronger_model_policy_source": reviewer_strength_source,
            "stronger_models_allowed": reviewer_strength == "allow_stronger",
            "stronger_requires": (
                "reviewer_strength_allow_stronger_or_fanout_reviewer_allow_stronger"
            ),
            "model_relation_to_session": (
                "may_exceed_session_with_reviewer_opt_in"
                if reviewer_strength == "allow_stronger"
                else role_model_relation("reviewer", session_tier, spawn_ceiling)
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
            **role_provider_fields(provider_defaults, "verifier"),
            **role_budget_fields(provider_defaults, "verifier"),
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


configure_model_triage(
    TRIAGE_ENGINES=TRIAGE_ENGINES,
    build_triage_prompt=build_triage_prompt,
    should_run_model_triage=should_run_model_triage,
    tail_text=tail_text,
    command_triage_template=command_triage_template,
    resolve_triage_binary=resolve_triage_binary,
    normalize_platform=normalize_platform,
    select_triage_engine=select_triage_engine,
    resolve_triage_model_selection=resolve_triage_model_selection,
    effort_for_role=effort_for_role,
    speed_for_role=speed_for_role,
)
