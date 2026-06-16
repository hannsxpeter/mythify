"""Host model switch record helpers for Mythify.

The CLI owns file I/O. This module owns normalization, record construction,
legacy record enrichment, and human-readable formatting.
"""

from __future__ import annotations

import os

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
SPEED_LEVELS = ("auto", "standard", "fast")
HOST_THINKING_LEVELS = ("auto", "low", "medium", "high", "xhigh", "max")

NO_HOST_CAPABILITY = {
    "kind": "host",
    "status": "unsupported",
    "can_switch_current_thread": False,
    "can_set_new_thread_model": False,
    "can_set_worker_model": False,
    "can_set_thinking": False,
    "can_list_models": False,
    "can_confirm_current_model": False,
}
HOST_CAPABILITIES = {
    "unknown": dict(NO_HOST_CAPABILITY, status="unknown"),
    "codex-desktop": dict(
        NO_HOST_CAPABILITY,
        status="supported",
        can_set_new_thread_model=True,
        can_set_worker_model=True,
        can_set_thinking=True,
    ),
    "codex-cli": dict(
        NO_HOST_CAPABILITY,
        status="supported",
        can_set_new_thread_model=True,
        can_set_worker_model=True,
        can_set_thinking=True,
    ),
    "claude-desktop": dict(
        NO_HOST_CAPABILITY,
        status="supported",
        can_set_new_thread_model=True,
        can_set_worker_model=True,
    ),
    "claude-code": dict(
        NO_HOST_CAPABILITY,
        status="supported",
        can_set_new_thread_model=True,
        can_set_worker_model=True,
    ),
    "cursor-desktop": dict(
        NO_HOST_CAPABILITY,
        status="supported",
        can_set_new_thread_model=True,
        can_set_worker_model=True,
        can_set_thinking=True,
    ),
    "cursor-agent": dict(
        NO_HOST_CAPABILITY,
        status="supported",
        can_set_new_thread_model=True,
        can_set_worker_model=True,
        can_set_thinking=True,
    ),
}


def normalize_host_platform(platform):
    value = (platform or "auto").strip()
    return value if value in PLATFORMS else "auto"


def normalize_host_thinking(thinking):
    value = (thinking or "auto").strip()
    return value if value in HOST_THINKING_LEVELS else "auto"


def normalize_host_speed(speed):
    value = (speed or "auto").strip()
    return value if value in SPEED_LEVELS else "auto"


def detect_host_platform(platform, environ=None):
    explicit = normalize_host_platform(platform)
    if explicit != "auto":
        return explicit
    env = os.environ if environ is None else environ
    if str(env.get("CODEX_THREAD_ID", "")).strip():
        return "codex-desktop"
    if str(env.get("CLAUDECODE", "")).strip() or str(
        env.get("CLAUDE_CODE_ENTRYPOINT", "")
    ).strip():
        return "claude-code"
    return "unknown"


def host_switch_actions(platform, target_model, thinking, speed, environ=None):
    env = os.environ if environ is None else environ
    actions = []
    if platform == "codex-desktop":
        actions.append("Use the Codex Desktop model picker for the current chat.")
        thread_id = str(env.get("CODEX_THREAD_ID", "")).strip()
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
                "Use the host reasoning effort control for {0} when available.".format(
                    thinking
                )
            )
        if speed != "auto":
            actions.append(
                "Use Codex speed {0} for spawned workers; host chat speed remains "
                "host-controlled.".format(speed)
            )
    elif platform == "claude-code":
        actions.append("In interactive Claude Code, run /model {0}.".format(target_model))
        actions.append(
            "For a new Claude Code session, start with claude --model {0}.".format(
                target_model
            )
        )
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


def host_capability_for_record(platform):
    return dict(HOST_CAPABILITIES.get(platform, HOST_CAPABILITIES["unknown"]))


def host_adapter_proof_status(capability, key):
    if capability.get("status") == "unknown":
        return "unknown"
    return "supported" if capability.get(key) else "unsupported"


def build_host_adapter_proof_path(capability, key, current_chat):
    return {
        "status": host_adapter_proof_status(capability, key),
        "proof_source": "host_capability.{0}".format(key),
        "current_chat_path": bool(current_chat),
        "requires_executed_host_evidence": True,
    }


def build_host_adapter_proof_scan(platform, capability, checked_at):
    return {
        "status": "metadata_only",
        "platform": platform,
        "proof_source": "host_capability_registry",
        "checked_at": checked_at,
        "host_state_mutated": False,
        "writes_state": False,
        "verification_recorded": False,
        "material_not_evidence": True,
        "guardrail": "current_chat_apply_or_confirm_requires_executed_host_evidence",
        "paths": {
            "current_chat_model_apply": build_host_adapter_proof_path(
                capability, "can_switch_current_thread", True
            ),
            "current_chat_model_confirm": build_host_adapter_proof_path(
                capability, "can_confirm_current_model", True
            ),
            "new_thread_model_apply": build_host_adapter_proof_path(
                capability, "can_set_new_thread_model", False
            ),
            "worker_model_apply": build_host_adapter_proof_path(
                capability, "can_set_worker_model", False
            ),
            "thinking_apply": build_host_adapter_proof_path(
                capability, "can_set_thinking", False
            ),
        },
    }


def build_host_switch_result(platform, target_model, current_model, thinking, speed, capability):
    return {
        "status": "manual",
        "requested_model": target_model,
        "requested_thinking": thinking,
        "requested_speed": speed,
        "current_model": current_model,
        "current_thinking": "",
        "current_chat_supported": bool(capability.get("can_switch_current_thread")),
        "current_chat_confirmed": False,
        "manual_action_required": True,
        "applied_by": "none",
        "reason": "host_current_chat_unconfirmed",
    }


def build_host_confirmation(target_model, current_model, thinking, capability, checked_at):
    can_confirm = bool(capability.get("can_confirm_current_model"))
    status = "unconfirmed" if can_confirm else "unsupported"
    reason = (
        "host_adapter_has_not_confirmed_current_model"
        if can_confirm
        else "host_capability_cannot_confirm_current_model"
    )
    return {
        "requested_model": target_model,
        "user_reported_current_model": current_model,
        "user_reported_current_thinking": thinking if thinking != "auto" else "",
        "current_model_confirmed": False,
        "confirmed_current_model": "",
        "confirmed_current_thinking": "",
        "confirmation_status": status,
        "confirmation_source": "none",
        "confirmation_checked_at": checked_at,
        "confirmed_at": "",
        "unsupported_reason": reason,
    }


def with_host_capability(record):
    if not isinstance(record, dict):
        return record
    platform = str(record.get("platform", "") or "unknown").strip() or "unknown"
    enriched = dict(record)
    capability = enriched.get("host_capability")
    if not isinstance(capability, dict):
        capability = host_capability_for_record(platform)
    enriched["host_capability"] = capability
    enriched["can_apply_current_chat"] = False
    thinking = normalize_host_thinking(enriched.get("thinking", "auto"))
    speed = normalize_host_speed(enriched.get("speed", "auto"))
    target_model = str(enriched.get("target_model", "") or "").strip()
    current_model = str(enriched.get("current_model", "") or "").strip()
    if not isinstance(enriched.get("switch_result"), dict):
        enriched["switch_result"] = build_host_switch_result(
            platform, target_model, current_model, thinking, speed, capability
        )
    if not isinstance(enriched.get("host_confirmation"), dict):
        enriched["host_confirmation"] = build_host_confirmation(
            target_model,
            current_model,
            thinking,
            capability,
            str(enriched.get("updated", "") or ""),
        )
    if not isinstance(enriched.get("adapter_proof_scan"), dict):
        enriched["adapter_proof_scan"] = build_host_adapter_proof_scan(
            platform, capability, str(enriched.get("updated", "") or "")
        )
    return enriched


def build_host_model_record(
    args,
    *,
    now_iso_func,
    classify_model_tier_func,
    environ=None,
):
    target_model = str(getattr(args, "target_model", "") or "").strip()
    platform = detect_host_platform(getattr(args, "platform", "auto"), environ=environ)
    thinking = normalize_host_thinking(getattr(args, "thinking", "auto"))
    speed = normalize_host_speed(getattr(args, "speed", "auto"))
    current_model = str(getattr(args, "current_model", "") or "").strip()
    capability = host_capability_for_record(platform)
    updated = now_iso_func()
    return {
        "platform": platform,
        "requested_platform": normalize_host_platform(getattr(args, "platform", "auto")),
        "target_model": target_model,
        "current_model": current_model,
        "target_model_tier": classify_model_tier_func(target_model),
        "thinking": thinking,
        "speed": speed,
        "reason": str(getattr(args, "reason", "") or "").strip(),
        "status": "recorded_requires_host_action",
        "control": "host_selected",
        "can_apply_current_chat": False,
        "host_capability": capability,
        "switch_result": build_host_switch_result(
            platform, target_model, current_model, thinking, speed, capability
        ),
        "host_confirmation": build_host_confirmation(
            target_model, current_model, thinking, capability, updated
        ),
        "adapter_proof_scan": build_host_adapter_proof_scan(platform, capability, updated),
        "updated": updated,
        "host_actions": host_switch_actions(
            platform, target_model, thinking, speed, environ=environ
        ),
    }


def format_bool(value):
    return "yes" if value else "no"


def format_host_model_record(record):
    enriched = with_host_capability(record)
    capability = enriched.get("host_capability", host_capability_for_record("unknown"))
    switch_result = enriched.get("switch_result", {})
    confirmation = enriched.get("host_confirmation", {})
    proof = enriched.get("adapter_proof_scan", {})
    proof_paths = proof.get("paths", {})
    lines = [
        "[OK] Host model switch {0}.".format(enriched.get("status", "recorded")),
        "platform: {0}".format(enriched.get("platform", "unknown")),
        "target model: {0} (tier {1})".format(
            enriched.get("target_model", ""), enriched.get("target_model_tier", "unknown")
        ),
        "current model: {0}".format(enriched.get("current_model") or "unknown"),
        "host-confirmed model: {0}".format(
            confirmation.get("confirmed_current_model")
            or confirmation.get("confirmation_status", "unsupported")
        ),
        "confirmation source: {0}".format(confirmation.get("confirmation_source", "none")),
        "adapter proof scan: {0}".format(proof.get("status", "metadata_only")),
        "current-chat apply proof: {0}".format(
            proof_paths.get("current_chat_model_apply", {}).get("status", "unknown")
        ),
        "current-chat confirm proof: {0}".format(
            proof_paths.get("current_chat_model_confirm", {}).get("status", "unknown")
        ),
        "new-thread model proof: {0}".format(
            proof_paths.get("new_thread_model_apply", {}).get("status", "unknown")
        ),
        "worker model proof: {0}".format(
            proof_paths.get("worker_model_apply", {}).get("status", "unknown")
        ),
        "thinking proof: {0}".format(
            proof_paths.get("thinking_apply", {}).get("status", "unknown")
        ),
        "thinking: {0}".format(enriched.get("thinking", "auto")),
        "speed: {0}".format(enriched.get("speed", "auto")),
        "switch status: {0}".format(switch_result.get("status", "manual")),
        "current-chat confirmed: {0}".format(
            format_bool(switch_result.get("current_chat_confirmed"))
        ),
        "manual action required: {0}".format(
            format_bool(switch_result.get("manual_action_required", True))
        ),
        "current-chat switch: {0}".format(
            format_bool(capability.get("can_switch_current_thread"))
        ),
        "new-thread model: {0}".format(
            format_bool(capability.get("can_set_new_thread_model"))
        ),
        "worker model: {0}".format(format_bool(capability.get("can_set_worker_model"))),
        "thinking control: {0}".format(format_bool(capability.get("can_set_thinking"))),
        "scope: Mythify recorded the requested host model for model_policy and spawn ceiling checks.",
        "host action required:",
    ]
    for action in enriched.get("host_actions", []):
        lines.append("- " + action)
    return "\n".join(lines)
