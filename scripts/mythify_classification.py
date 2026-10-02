"""Deterministic Mythify task classification.

This module is shared by the router and direct unit tests. It keeps the
manifest-backed classification policy out of the large command dispatcher.
Classification never names, ranks, or selects a model, provider, or subagent:
the framing, parallelism, and review outputs are neutral advisories, and the
host decides whether and where to delegate.

Two precision rules keep short or incidental wording from misleading the
router. A prompt of trivial length is trivial only when it matches no task,
risk, or route-selecting term. A destructive verb (delete, remove, drop, and
kin) is high risk unless the words right after it name a code-local object
such as an import, a comment, or a typo and the prompt names no destructive
object anywhere, so "remove an unused import" stays low risk while "delete the
user account", "delete production data", and "remove the imports from the s3
bucket" stay high.
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
DESTRUCTIVE_VERBS = classification_tuple("risk", "destructive_verbs")
DESTRUCTIVE_OBJECTS = classification_tuple("risk", "destructive_objects")
CODE_LOCAL_OBJECTS = classification_tuple("risk", "code_local_objects")
NEAR_TERMS = tuple(
    (
        str(entry["task_type"]),
        tuple(str(term) for term in entry["terms"]),
        tuple(str(term) for term in entry["near"]),
    )
    for entry in CLASSIFICATION_MANIFEST["risk"].get("near_terms", [])
)
RISK_WINDOW_WORDS = int(CLASSIFICATION_THRESHOLDS["risk_window_words"])
HIGH_RISK_TASK_TYPES = classification_tuple("risk", "high_task_types")
MEDIUM_RISK_TERMS = classification_tuple("risk", "medium_terms")
MEDIUM_RISK_TASK_TYPES = classification_tuple("risk", "medium_task_types")
CEREMONY_POLICY = CLASSIFICATION_MANIFEST["ceremony"]
FRAMING_POLICY = CLASSIFICATION_MANIFEST["framing"]
PARALLELISM_POLICY = CLASSIFICATION_MANIFEST["parallelism"]
REVIEW_POLICY = CLASSIFICATION_MANIFEST["review"]
QUALITY_CLIMB_POLICY = CLASSIFICATION_MANIFEST["quality_climb"]
EXECUTION_PROFILE_POLICY = CLASSIFICATION_MANIFEST["execution_profile"]
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


def term_count(term):
    return len(wordish(term).split())


def term_positions(tokens, term):
    needle = wordish(term).split()
    size = len(needle)
    return [i for i in range(len(tokens) - size + 1) if needle and tokens[i:i + size] == needle]


def destructive_wording(text):
    """True unless every destructive verb acts only on a code-local object.

    A destructive object anywhere in the prompt makes any destructive verb high
    risk, however far apart they are. Otherwise a verb is benign only when the
    next RISK_WINDOW_WORDS words name a code-local object; a verb followed by
    neither kind stays high, so unknown objects fail toward caution.
    """
    tokens = wordish(text).split()
    names_destructive_object = bool(contains_any(text, DESTRUCTIVE_OBJECTS))
    for verb in DESTRUCTIVE_VERBS:
        size = len(wordish(verb).split())
        for start in term_positions(tokens, verb):
            window = " ".join(tokens[start + size:start + size + RISK_WINDOW_WORDS])
            if names_destructive_object or not contains_any(window, CODE_LOCAL_OBJECTS):
                return True
    return False


def near_matches(text):
    """(task_type, signal) pairs where a term sits within the window of a partner."""
    tokens = wordish(text).split()
    found = []
    for task_type, terms, partners in NEAR_TERMS:
        for term in terms:
            for start in term_positions(tokens, term):
                low = max(0, start - RISK_WINDOW_WORDS)
                window = " ".join(tokens[low:start + RISK_WINDOW_WORDS + 1])
                for partner in contains_any(window, partners):
                    found.append((task_type, "{0} near {1}".format(term, partner)))
    return found


def term_risk(text):
    """Risk named by the wording alone: "high", "medium", or None."""
    if contains_any(text, HIGH_RISK_TERMS) or destructive_wording(text) or near_matches(text):
        return "high"
    if contains_any(text, MEDIUM_RISK_TERMS):
        return "medium"
    return None


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


def classify_task_text(task_text, route_terms=()):
    """Classify TASK_TEXT. ROUTE_TERMS are the router's route-selecting terms."""
    text = " ".join(str(task_text or "").lower().split())
    words = [word for word in text.replace("/", " ").replace("_", " ").split() if word]
    signals = []
    scores = {}
    longest = {}
    paired = near_matches(text)
    for task_type, terms in CLASSIFICATION_RULES:
        matches = contains_any(text, terms)
        matches.extend(signal for kind, signal in paired if kind == task_type)
        if matches:
            scores[task_type] = len(matches)
            longest[task_type] = max(term_count(term) for term in matches)
            signals.extend(matches)
    worded_risk = term_risk(text)
    if scores:
        # Ties go to the type with the longest matched phrase, so "product plan"
        # outranks the bare "plan", then to the alphabetically first type.
        task_type = sorted(
            scores.items(), key=lambda item: (-item[1], -longest[item[0]], item[0])
        )[0][0]
        # A generic verb (build, create, plan) ties with a product term in
        # "build a roadmap"; the product term says what the work is.
        if (
            task_type in ("feature", "design")
            and "product" in scores
            and (scores["product"], longest["product"]) == (scores[task_type], longest[task_type])
        ):
            task_type = "product"
    elif text.endswith("?") or any(text.startswith(prefix) for prefix in QUESTION_PREFIXES):
        task_type = "question"
    elif contains_any(text, VAGUE_REQUEST_TERMS):
        task_type = "feature"
    elif (
        len(words) <= TRIVIAL_WORD_COUNT
        and worded_risk is None
        and not contains_any(text, route_terms)
    ):
        task_type = "trivial"
    else:
        task_type = "feature"

    if worded_risk == "high" or task_type in HIGH_RISK_TASK_TYPES:
        risk = "high"
    elif worded_risk == "medium" or task_type in MEDIUM_RISK_TASK_TYPES:
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
