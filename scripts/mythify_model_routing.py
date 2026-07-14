"""Provider-neutral execution topology and review policy helpers."""

from __future__ import annotations

import re


def model_execution_topology(classification, topology_policy, task_text=""):
    task_type = classification.get("task_type", "feature")
    execution_profile = classification.get("execution_profile", "standard")
    fanout = classification.get("fanout", "not_recommended")
    dynamic_types = topology_policy.get("dynamic_workflow_candidate_task_types", ())
    explicit_dynamic_request = bool(re.search(
        r"\bultracode\b|\b(?:use|run|launch|start)\s+(?:a\s+)?(?:dynamic\s+)?workflow\b",
        str(task_text or ""),
        re.IGNORECASE,
    ))
    dynamic_candidate = (
        explicit_dynamic_request
        or (task_type in dynamic_types and fanout == "recommended")
    )
    automatic_dynamic_workflow = (
        dynamic_candidate and bool(topology_policy.get("automatic_dynamic_workflow"))
    )
    adapter = dict(topology_policy.get("native_adapter") or {})
    if execution_profile == "direct":
        recommended = "direct"
        reason = "The task is a direct answer or one reversible action."
    elif fanout == "recommended":
        recommended = "bounded_parallel"
        reason = "Independent analysis can be split, then synthesized and verified."
    elif classification.get("ceremony") == "full":
        recommended = "verifier_gated_plan"
        reason = "High-risk or heavy work needs durable steps and verification gates."
    elif execution_profile == "fast":
        recommended = "direct_with_verification"
        reason = "Focused work can run directly with an executable completion gate."
    else:
        recommended = "verifier_gated_plan"
        reason = "Multi-step work should use a plan with executable gates."
    return {
        "recommended": recommended,
        "dynamic_workflow_candidate": dynamic_candidate,
        "dynamic_workflow_candidate_source": (
            "explicit_request"
            if explicit_dynamic_request
            else "task_classification"
            if dynamic_candidate
            else "not_recommended"
        ),
        "automatic_dynamic_workflow": automatic_dynamic_workflow,
        "native_adapter": {
            **adapter,
            "recommended": automatic_dynamic_workflow,
            "activation": (
                "explicit_request"
                if automatic_dynamic_workflow and explicit_dynamic_request
                else "automatic_candidate"
                if automatic_dynamic_workflow
                else "not_recommended"
            ),
        },
        "parallelism_requires": topology_policy["parallelism_requires"],
        "reason": reason,
    }


def model_review_policy(classification, selected_profile):
    if classification.get("risk") == "high" or classification.get("ceremony") == "full":
        level = "required"
        reviewer_profile = "strong"
    elif classification.get("risk") == "medium" or classification.get("ceremony") == "standard":
        level = "recommended"
        reviewer_profile = "strong" if selected_profile == "strong" else "balanced"
    else:
        level = "optional"
        reviewer_profile = "balanced" if selected_profile == "utility" else selected_profile
    return {
        "level": level,
        "independent": True,
        "recommended_profile": reviewer_profile,
        "stronger_model_requires_explicit_opt_in": True,
        "material_not_verification": True,
    }
