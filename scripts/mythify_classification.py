"""Deterministic Mythify task classification.

This module is shared by the router and direct unit tests. It keeps the
manifest-backed classification policy out of the large command dispatcher.
Classification never names, ranks, or selects a model, provider, or subagent:
the framing, parallelism, and review outputs are neutral advisories, and the
host decides whether and where to delegate.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSIFICATION_RULES_PATH = REPO_ROOT / "protocol" / "classification-rules.json"


def load_classification_rules():
    with CLASSIFICATION_RULES_PATH.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    seen = set()
    for entry in manifest.get("task_types", []):
        task_type = str(entry.get("id", "")).strip()
        terms = entry.get("terms", [])
        if not task_type or task_type in seen or not isinstance(terms, list) or not terms:
            raise ValueError("Invalid classification rule entry")
        seen.add(task_type)
    if not seen:
        raise ValueError("Classification rules manifest is empty")
    required_sections = (
        "thresholds",
        "risk",
        "ceremony",
        "framing",
        "parallelism",
        "review",
        "quality_climb",
        "execution_profile",
        "plan_archetype",
        "next_actions",
        "verification_hints",
    )
    for section in required_sections:
        if not isinstance(manifest.get(section), dict):
            raise ValueError("Invalid classification policy section")
    if "feature" not in manifest["verification_hints"]:
        raise ValueError("Classification verification hints are missing feature fallback")
    return manifest


def classification_task_rules(manifest):
    return tuple(
        (str(entry["id"]), tuple(str(term) for term in entry.get("terms", [])))
        for entry in manifest.get("task_types", [])
    )


def classification_tuple(section, key):
    return tuple(str(item) for item in CLASSIFICATION_MANIFEST[section].get(key, []))


CLASSIFICATION_MANIFEST = load_classification_rules()
CLASSIFICATION_RULES = classification_task_rules(CLASSIFICATION_MANIFEST)
CLASSIFICATION_THRESHOLDS = CLASSIFICATION_MANIFEST["thresholds"]
TRIVIAL_WORD_COUNT = int(CLASSIFICATION_THRESHOLDS["trivial_word_count"])
HIGH_AMBIGUITY_WORD_COUNT = int(CLASSIFICATION_THRESHOLDS["high_ambiguity_word_count"])
MEDIUM_AMBIGUITY_WORD_COUNT = int(CLASSIFICATION_THRESHOLDS["medium_ambiguity_word_count"])
QUESTION_PREFIXES = tuple(str(prefix) for prefix in CLASSIFICATION_MANIFEST["question_prefixes"])
VAGUE_REQUEST_TERMS = tuple(str(term) for term in CLASSIFICATION_MANIFEST["vague_request_terms"])
HIGH_RISK_TERMS = classification_tuple("risk", "high_terms")
HIGH_RISK_TASK_TYPES = classification_tuple("risk", "high_task_types")
MEDIUM_RISK_TERMS = classification_tuple("risk", "medium_terms")
MEDIUM_RISK_TASK_TYPES = classification_tuple("risk", "medium_task_types")
CEREMONY_POLICY = CLASSIFICATION_MANIFEST["ceremony"]
FRAMING_POLICY = CLASSIFICATION_MANIFEST["framing"]
PARALLELISM_POLICY = CLASSIFICATION_MANIFEST["parallelism"]
REVIEW_POLICY = CLASSIFICATION_MANIFEST["review"]
QUALITY_CLIMB_POLICY = CLASSIFICATION_MANIFEST["quality_climb"]
EXECUTION_PROFILE_POLICY = CLASSIFICATION_MANIFEST["execution_profile"]
PLAN_ARCHETYPE_POLICY = CLASSIFICATION_MANIFEST["plan_archetype"]
NEXT_ACTIONS = CLASSIFICATION_MANIFEST["next_actions"]
VERIFICATION_HINTS = CLASSIFICATION_MANIFEST["verification_hints"]
PARALLELISM_CHOOSER = "host"


def wordish(text):
    return "".join(ch if ch.isalnum() else " " for ch in str(text).lower())


def contains_any(text, terms):
    haystack = " {0} ".format(" ".join(wordish(text).split()))
    matches = []
    for term in terms:
        needle_words = wordish(term).split()
        if needle_words and " {0} ".format(" ".join(needle_words)) in haystack:
            matches.append(term)
    return matches


def classify_ambiguity(text, words, signals, scores, task_type):
    if task_type in ("question", "trivial"):
        return "low"
    if contains_any(text, VAGUE_REQUEST_TERMS) or (
        not signals and len(words) <= HIGH_AMBIGUITY_WORD_COUNT
    ):
        return "high"
    if len(scores) > 1 or len(words) > MEDIUM_AMBIGUITY_WORD_COUNT:
        return "medium"
    return "low"


def _policy_tuple(policy, key):
    return tuple(str(item) for item in policy.get(key, []))


def framing_advisory(task_type, risk, ceremony, ambiguity, text):
    """How much framing the task needs before execution: none, light, or full."""
    if ceremony == "none":
        return {"level": "none", "reason": FRAMING_POLICY["none_reason"]}
    if (
        risk == "high"
        and ambiguity == "high"
        and contains_any(text, _policy_tuple(FRAMING_POLICY, "high_impact_terms"))
    ):
        return {"level": "full", "reason": FRAMING_POLICY["high_impact_reason"]}
    if ambiguity == "high":
        return {"level": "full", "reason": FRAMING_POLICY["high_ambiguity_reason"]}
    if task_type in _policy_tuple(FRAMING_POLICY, "full_task_types"):
        return {"level": "full", "reason": FRAMING_POLICY["full_task_type_reason"]}
    if task_type in _policy_tuple(FRAMING_POLICY, "light_task_types") or risk == "medium":
        return {"level": "light", "reason": FRAMING_POLICY["light_reason"]}
    return {"level": "none", "reason": FRAMING_POLICY["default_reason"]}


def parallelism_advisory(task_type, text):
    """Whether the work splits into independent parts. The host chooses who runs them."""
    if task_type in _policy_tuple(PARALLELISM_POLICY, "strong_task_types") or contains_any(
        text, _policy_tuple(PARALLELISM_POLICY, "strong_terms")
    ):
        fit, reason = "strong", PARALLELISM_POLICY["strong_reason"]
    elif task_type in _policy_tuple(PARALLELISM_POLICY, "possible_task_types") or contains_any(
        text, _policy_tuple(PARALLELISM_POLICY, "possible_terms")
    ):
        fit, reason = "possible", PARALLELISM_POLICY["possible_reason"]
    else:
        fit, reason = "none", PARALLELISM_POLICY["none_reason"]
    return {"fit": fit, "reason": reason, "chooser": PARALLELISM_CHOOSER}


def review_advisory(risk, ceremony):
    """Whether the integrated result deserves an independent review before completion."""
    if risk == "high" or ceremony == "full":
        return {"independent": True, "reason": REVIEW_POLICY["independent_reason"]}
    return {"independent": False, "reason": REVIEW_POLICY["self_check_reason"]}


def execution_profile_for(task_type, risk, ceremony, ambiguity, text):
    if ceremony == "none":
        return (
            "direct",
            EXECUTION_PROFILE_POLICY["direct_reason"],
        )
    if ceremony == "full" or risk == "high":
        return (
            "full",
            EXECUTION_PROFILE_POLICY["full_reason"],
        )
    if ambiguity == "high":
        return (
            "standard",
            EXECUTION_PROFILE_POLICY["ambiguous_reason"],
        )
    focused_terms = tuple(str(term) for term in EXECUTION_PROFILE_POLICY["focused_terms"])
    fast_task_types = tuple(
        str(item) for item in EXECUTION_PROFILE_POLICY["fast_task_types"]
    )
    fast_focused_task_types = tuple(
        str(item) for item in EXECUTION_PROFILE_POLICY["fast_focused_task_types"]
    )
    if task_type in fast_task_types or (
        task_type in fast_focused_task_types and contains_any(text, focused_terms)
    ):
        return (
            "fast",
            EXECUTION_PROFILE_POLICY["fast_reason"],
        )
    if ceremony == "light":
        return (
            "fast",
            EXECUTION_PROFILE_POLICY["light_reason"],
        )
    return (
        "standard",
        EXECUTION_PROFILE_POLICY["standard_reason"],
    )


def plan_archetype_for(task_type, execution_profile, text):
    direct_profiles = tuple(
        str(item) for item in PLAN_ARCHETYPE_POLICY["direct_execution_profiles"]
    )
    design_types = tuple(
        str(item) for item in PLAN_ARCHETYPE_POLICY["design_heavy_task_types"]
    )
    if execution_profile in direct_profiles:
        return "direct", PLAN_ARCHETYPE_POLICY["direct_reason"]
    if task_type in design_types or contains_any(
        text, tuple(str(term) for term in PLAN_ARCHETYPE_POLICY["design_heavy_terms"])
    ):
        return "design-heavy", PLAN_ARCHETYPE_POLICY["design_heavy_reason"]
    return "rpi", PLAN_ARCHETYPE_POLICY["rpi_reason"]


def classify_task_text(task_text):
    text = " ".join(str(task_text or "").lower().split())
    words = [word for word in text.replace("/", " ").replace("_", " ").split() if word]
    signals = []
    scores = {}
    for task_type, terms in CLASSIFICATION_RULES:
        matches = contains_any(text, terms)
        if matches:
            scores[task_type] = len(matches)
            signals.extend(matches)
    if scores:
        task_type = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]
    elif text.endswith("?") or any(text.startswith(prefix) for prefix in QUESTION_PREFIXES):
        task_type = "question"
    elif contains_any(text, VAGUE_REQUEST_TERMS):
        task_type = "feature"
    elif len(words) <= TRIVIAL_WORD_COUNT:
        task_type = "trivial"
    else:
        task_type = "feature"

    if contains_any(text, HIGH_RISK_TERMS) or task_type in HIGH_RISK_TASK_TYPES:
        risk = "high"
    elif contains_any(text, MEDIUM_RISK_TERMS) or task_type in MEDIUM_RISK_TASK_TYPES:
        risk = "medium"
    else:
        risk = "low"

    ambiguity = classify_ambiguity(text, words, signals, scores, task_type)

    if task_type in tuple(CEREMONY_POLICY["none_low_risk_task_types"]) and risk == "low":
        ceremony = "none"
    elif risk == "low" and task_type in tuple(CEREMONY_POLICY["light_low_risk_task_types"]):
        ceremony = "light"
    elif risk == "high" or task_type in tuple(CEREMONY_POLICY["full_task_types"]):
        ceremony = "full"
    else:
        ceremony = "standard"

    if contains_any(text, tuple(str(term) for term in QUALITY_CLIMB_POLICY["terms"])):
        quality_climb = "detected"
        quality_climb_reason = QUALITY_CLIMB_POLICY["detected_reason"]
        quality_climb_protocol = QUALITY_CLIMB_POLICY["protocol"]
    else:
        quality_climb = "not_detected"
        quality_climb_reason = QUALITY_CLIMB_POLICY["not_detected_reason"]
        quality_climb_protocol = ""

    verification = VERIFICATION_HINTS.get(task_type, VERIFICATION_HINTS["feature"])
    execution_profile, execution_profile_reason = execution_profile_for(
        task_type, risk, ceremony, ambiguity, text
    )
    plan_archetype, plan_archetype_reason = plan_archetype_for(
        task_type, execution_profile, text
    )
    if execution_profile == "direct":
        next_action = NEXT_ACTIONS["direct"]
    elif execution_profile == "fast":
        next_action = NEXT_ACTIONS["fast"]
    elif execution_profile == "standard":
        next_action = NEXT_ACTIONS["standard"]
    else:
        next_action = NEXT_ACTIONS["full"]

    return {
        "task_type": task_type,
        "risk": risk,
        "ambiguity": ambiguity,
        "ceremony": ceremony,
        "execution_profile": execution_profile,
        "execution_profile_reason": execution_profile_reason,
        "plan_archetype": plan_archetype,
        "plan_archetype_reason": plan_archetype_reason,
        "verification": verification,
        "framing": framing_advisory(task_type, risk, ceremony, ambiguity, text),
        "parallelism": parallelism_advisory(task_type, text),
        "review": review_advisory(risk, ceremony),
        "quality_climb": quality_climb,
        "quality_climb_reason": quality_climb_reason,
        "quality_climb_protocol": quality_climb_protocol,
        "signals": sorted(set(signals))[:10],
        "next_action": next_action,
    }
