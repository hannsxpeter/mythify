"""Deterministic Mythify task classification.

This module is shared by the CLI entrypoint and direct unit tests. It keeps the
manifest-backed classification policy out of the large command dispatcher.
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
        "fanout",
        "fanout_visibility",
        "execution_profile",
        "next_actions",
        "model_triage",
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
FANOUT_POLICY = CLASSIFICATION_MANIFEST["fanout"]
FANOUT_VISIBILITY_POLICY = CLASSIFICATION_MANIFEST["fanout_visibility"]
EXECUTION_PROFILE_POLICY = CLASSIFICATION_MANIFEST["execution_profile"]
NEXT_ACTIONS = CLASSIFICATION_MANIFEST["next_actions"]
MODEL_TRIAGE_POLICY = CLASSIFICATION_MANIFEST["model_triage"]
VERIFICATION_HINTS = CLASSIFICATION_MANIFEST["verification_hints"]

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


def model_triage_gate(task_type, risk, ceremony, ambiguity, text):
    if ceremony == "none":
        return (
            "skip",
            MODEL_TRIAGE_POLICY["none_reason"],
        )
    high_impact_terms = tuple(
        str(term) for term in MODEL_TRIAGE_POLICY["high_impact_terms"]
    )
    if (
        risk == "high"
        and ambiguity == "high"
        and contains_any(text, high_impact_terms)
    ):
        return (
            "required",
            MODEL_TRIAGE_POLICY["high_impact_required_reason"],
        )
    if ambiguity == "high":
        return (
            "recommended",
            MODEL_TRIAGE_POLICY["high_ambiguity_reason"],
        )
    if task_type in tuple(str(item) for item in MODEL_TRIAGE_POLICY["recommended_task_types"]):
        return (
            "recommended",
            MODEL_TRIAGE_POLICY["recommended_reason"],
        )
    if (
        task_type in tuple(str(item) for item in MODEL_TRIAGE_POLICY["optional_task_types"])
        or risk == "medium"
    ):
        return (
            "optional",
            MODEL_TRIAGE_POLICY["optional_reason"],
        )
    return (
        "skip",
        MODEL_TRIAGE_POLICY["skip_reason"],
    )


def infer_fanout_visibility(text):
    normalized = " ".join(str(text or "").lower().split())
    for mode in FANOUT_VISIBILITY_POLICY["modes"]:
        if contains_any(normalized, tuple(str(term) for term in mode["terms"])):
            return (
                str(mode["visibility"]),
                str(mode["source"]),
                str(mode["reason"]),
            )
    default = FANOUT_VISIBILITY_POLICY["default"]
    return (
        str(default["visibility"]),
        str(default["source"]),
        str(default["reason"]),
    )


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

    if (
        task_type in tuple(FANOUT_POLICY["recommended_task_types"])
        or contains_any(text, tuple(FANOUT_POLICY["recommended_terms"]))
    ):
        fanout = "recommended"
        fanout_reason = FANOUT_POLICY["recommended_reason"]
    elif (
        task_type in tuple(FANOUT_POLICY["optional_task_types"])
        or contains_any(text, tuple(FANOUT_POLICY["optional_terms"]))
    ):
        fanout = "optional"
        fanout_reason = FANOUT_POLICY["optional_reason"]
    else:
        fanout = "not_recommended"
        fanout_reason = FANOUT_POLICY["not_recommended_reason"]

    verification = VERIFICATION_HINTS.get(task_type, VERIFICATION_HINTS["feature"])
    execution_profile, execution_profile_reason = execution_profile_for(
        task_type, risk, ceremony, ambiguity, text
    )
    if execution_profile == "direct":
        next_action = NEXT_ACTIONS["direct"]
    elif execution_profile == "fast":
        next_action = NEXT_ACTIONS["fast"]
    elif execution_profile == "standard":
        next_action = NEXT_ACTIONS["standard"]
    else:
        next_action = NEXT_ACTIONS["full"]

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
        roles = policy.get("provider_defaults", {}).get("roles", {})
        if roles:
            lines.append(
                "providers: session={0}; triage={1}; reader={2}; worker={3}; reviewer={4}; verifier={5}".format(
                    roles.get("session", {}).get("provider", "host"),
                    roles.get("triage", {}).get("provider", "host_cli"),
                    roles.get("reader", {}).get("provider", "local_openai_compatible"),
                    roles.get("fanout_worker", {}).get("provider", "host_cli"),
                    roles.get("reviewer", {}).get("provider", "host_cli"),
                    roles.get("verifier", {}).get("provider", "local_command"),
                )
            )
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
            "reviewer opt-in: {0} ({1})".format(
                policy.get("reviewer", {}).get(
                    "stronger_model_policy", "same_or_lower"
                ),
                policy.get("reviewer", {}).get(
                    "stronger_model_policy_source", "default"
                ),
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
