"""The user's agent routing policy, shown by `mythify route` as advisory text.

A policy file is owned by the user, outside Mythify (schema version 1). When
one exists, `route` reports which step of that policy the task falls under and
what the policy says about it: the action, the role, the options and effort the
policy lists, and the review tier. Mythify prints what the file says. It does
not pick, rank, or switch a model, and it names no model or vendor itself; every
tool, model, and tier name in the output comes from the user's file.

Lookup: MYTHIFY_AGENT_POLICY (a path) wins, else ~/.agents/policy.json. No file
means no output change at all. An unreadable or invalid file prints one [WARN]
line to stderr and route behaves as if there were no policy.

Only the keys read below are used; anything else in the file is ignored.
"""

import json
import os
import re
import sys
from pathlib import Path

from mythify_classification import classify_policy_step, high_tier_term

POLICY_ENV = "MYTHIFY_AGENT_POLICY"
POLICY_VERSION = 1
EFFORT_ORDER = ("none", "low", "medium", "high", "xhigh", "max", "ultra")
# Mythify risk to the policy's review tier id.
RISK_TO_TIER = {"low": "low", "medium": "normal", "high": "high"}
# Route id to the policy step id it falls under when no policy step matched first.
# A direct route is a question step only for a task that changes no files (see
# NO_FILE_CHANGE_TYPES); every other direct task writes something, so it is build.
ROUTE_TO_STEP = {
    "direct": "build",
    "map": "design",
    "review": "build",
    "plan": "build",
    "outcome": "build",
    "handoff": "build",
    "failure_recovery": "build",
}
NO_FILE_CHANGE_TYPES = ("question", "research")
REVIEW_ROUTE = "review"
REVIEWER_ROLE = "reviewer"
TIER_SHOWN_FIRST = ("reviewer_effort", "user_review")
TIER_NOT_EXTRA = ("id",) + TIER_SHOWN_FIRST


class PolicyShapeError(ValueError):
    pass


def policy_path():
    override = os.environ.get(POLICY_ENV, "").strip()
    if override:
        return Path(os.path.expanduser(override))
    try:
        return Path.home() / ".agents" / "policy.json"
    except RuntimeError:
        return None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value):
    return isinstance(value, str)


def _check(condition, message):
    if not condition:
        raise PolicyShapeError(message)


def validate_policy(policy):
    """Raise PolicyShapeError unless POLICY has the keys route reads, well formed."""
    _check(isinstance(policy, dict), "top level is not an object")
    _check(_is_int(policy.get("version")) and policy["version"] == POLICY_VERSION,
           "unsupported version (expected {0})".format(POLICY_VERSION))
    routing = policy.get("routing")
    _check(isinstance(routing, list) and routing, "routing is not a non-empty list")
    for entry in routing:
        _check(isinstance(entry, dict), "a routing entry is not an object")
        _check(_is_int(entry.get("step")), "a routing entry has no integer step")
        for key in ("id", "when", "action"):
            _check(_is_text(entry.get(key)), "a routing entry has no text {0}".format(key))
        _check(entry.get("role") is None or _is_text(entry["role"]), "a routing role is not text or null")
        _check(entry.get("note") is None or _is_text(entry["note"]), "a routing note is not text")
    roles = policy.get("roles")
    _check(isinstance(roles, dict), "roles is not an object")
    for name, role in roles.items():
        _check(isinstance(role, dict), "role {0} is not an object".format(name))
        options = role.get("options")
        _check(isinstance(options, list), "role {0} options is not a list".format(name))
        for option in options:
            _check(isinstance(option, dict) and _is_text(option.get("tool")) and _is_text(option.get("model")),
                   "role {0} has an option without tool and model text".format(name))
        effort = role.get("effort")
        _check(isinstance(effort, dict), "role {0} effort is not an object".format(name))
        for value in effort.values():
            _check(value is None or isinstance(value, (str, bool)),
                   "role {0} has an effort that is not text, true, or null".format(name))
        _check(role.get("effort_text") is None or _is_text(role["effort_text"]),
               "role {0} effort_text is not text".format(name))
    tiers = policy.get("review_tiers")
    _check(isinstance(tiers, list), "review_tiers is not a list")
    for tier in tiers:
        _check(isinstance(tier, dict) and _is_text(tier.get("id")), "a review tier has no text id")
        _check(tier.get("reviewer_effort") is None or _is_text(tier["reviewer_effort"]),
               "a reviewer_effort is not text or null")
    rounds = policy.get("review_rounds_max")
    _check(rounds is None or _is_int(rounds), "review_rounds_max is not an integer")
    rules = policy.get("hard_rules")
    if rules is not None:
        _check(isinstance(rules, dict), "hard_rules is not an object")
        for key in ("banned_models", "never_efforts"):
            value = rules.get(key, [])
            _check(isinstance(value, list) and all(_is_text(item) for item in value),
                   "hard_rules {0} is not a list of text".format(key))
        ceiling = rules.get("effort_ceiling")
        _check(ceiling is None or (_is_text(ceiling) and ceiling.lower() in EFFORT_ORDER),
               "hard_rules effort_ceiling is not a known effort level")


def load_policy():
    """(policy, path) for a usable policy file, else (None, None).

    A missing file is silent. A file that cannot be read or does not have the
    documented shape prints one [WARN] line to stderr and is ignored.
    """
    path = policy_path()
    if path is None:
        return None, None
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except (OSError, UnicodeDecodeError) as error:
        return _ignore(path, "cannot read it ({0})".format(type(error).__name__))
    try:
        policy = json.loads(text)
        validate_policy(policy)
    except (ValueError, RecursionError) as error:
        return _ignore(path, str(error) or "invalid JSON")
    return policy, path


def _ignore(path, reason):
    print("[WARN] Ignoring agent policy {0}: {1}".format(path, " ".join(str(reason).split())),
          file=sys.stderr)
    return None, None


def _rank(level):
    return EFFORT_ORDER.index(level) if level in EFFORT_ORDER else None


WITHHELD = "[withheld]"
# Keys Mythify itself puts in the delegation block; scrub() keeps them as they are.
OWN_KEYS = frozenset((
    "advisory", "matched", "step", "id", "when", "action", "note", "role", "name",
    "in_policy", "options", "tool", "model", "options_withheld", "effort",
    "effort_basis", "effort_text", "review_tier", "risk", "reviewer_effort",
    "user_review", "extra", "review_rounds_max", "guards", "banned_models_withheld",
    "effort_ceiling", "effort_clamped", "effort_withheld", "text_withheld", "raised_by",
))


class Guards(object):
    """The policy's hard rules: banned models and efforts.

    Two jobs. effort() picks the effort to show, never above the ceiling and
    never one the file forbids. scrub() is the single place that cleans every
    string the delegation block emits, so text and JSON cannot diverge.
    """

    def __init__(self, rules):
        rules = rules or {}
        self.banned = sorted(
            set(item.strip().lower() for item in rules.get("banned_models", []) if item.strip()),
            key=len, reverse=True,
        )
        ceiling = rules.get("effort_ceiling")
        self.ceiling = ceiling.lower() if ceiling else None
        self.never = set(item.strip().lower() for item in rules.get("never_efforts", []))
        above = set()
        if self.ceiling:
            above = set(level for level in EFFORT_ORDER if _rank(level) > _rank(self.ceiling))
        self.blocked = self.never | above
        self.clamped = False
        self.withheld_effort = False
        self.withheld_text = False
        parts = [re.escape(name) for name in self.banned]
        words = sorted((re.escape(word) for word in self.blocked if word), key=len, reverse=True)
        if words:
            parts.append("(?<![A-Za-z0-9])(?:{0})(?![A-Za-z0-9])".format("|".join(words)))
        self._pattern = re.compile("|".join(parts), re.IGNORECASE) if parts else None

    def allows_model(self, model):
        lowered = model.lower()
        return not any(banned in lowered for banned in self.banned)

    def effort(self, value):
        """VALUE limited to what the rules allow; None when nothing can be shown.

        A level above the ceiling, or one the file forbids, is replaced by the
        highest level at or below both the ceiling and the level itself that
        the file does not forbid. Without a ceiling, or with no such level, it
        is withheld.
        """
        if not _is_text(value):
            return None
        level = value.strip().lower()
        if level not in self.blocked:
            if self.ceiling and level not in EFFORT_ORDER:
                self.withheld_effort = True
                return None
            return value.strip()
        cap = _rank(level)
        if self.ceiling and cap is not None:
            cap = min(cap, _rank(self.ceiling))
        if self.ceiling and cap is not None:
            for name in reversed(EFFORT_ORDER[: cap + 1]):
                if name not in self.never:
                    self.clamped = True
                    return name
        self.withheld_effort = True
        return None

    def scrub(self, value, own=OWN_KEYS):
        """VALUE with banned model names and forbidden efforts replaced by [withheld].

        Walks strings, list items, dict keys, and dict values. Banned names
        match as case-insensitive substrings; forbidden efforts as whole words.
        Keys in OWN are Mythify's field names and stay as they are, except
        inside "extra", whose keys come from the policy file.
        """
        if isinstance(value, str):
            if self._pattern is None:
                return value
            cleaned, count = self._pattern.subn(WITHHELD, value)
            if count:
                self.withheld_text = True
            return cleaned
        if isinstance(value, list):
            return [self.scrub(item, own) for item in value]
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                name = key if (key in own or not isinstance(key, str)) else self.scrub(key, own)
                suffix = 2
                unique = name
                while unique in cleaned:
                    unique = "{0} {1}".format(name, suffix)
                    suffix += 1
                cleaned[unique] = self.scrub(item, () if key == "extra" else own)
            return cleaned
        return value


def _find(items, key, wanted):
    for item in items:
        if item.get(key) == wanted:
            return item
    return None


def _tier_view(policy, risk, guards, raised_by=None):
    tier_id = "high" if raised_by else RISK_TO_TIER.get(risk, "normal")
    tier = _find(policy["review_tiers"], "id", tier_id)
    if tier is None:
        return {"id": tier_id, "risk": risk, "in_policy": False, "raised_by": raised_by}
    extra = {}
    for key, value in tier.items():
        if key not in TIER_NOT_EXTRA:
            extra[key] = value
    view = {
        "id": tier_id,
        "risk": risk,
        "in_policy": True,
        "raised_by": raised_by,
        "reviewer_effort": guards.effort(tier.get("reviewer_effort")),
        "user_review": tier.get("user_review"),
        "extra": extra,
    }
    if policy.get("review_rounds_max") is not None:
        view["review_rounds_max"] = policy["review_rounds_max"]
    return view


def _role_view(policy, role_name, risk, tier, guards, raised=False):
    view = {"name": role_name, "in_policy": False}
    role = policy["roles"].get(role_name)
    if role is None:
        return view
    view["in_policy"] = True
    options = [
        {"tool": option["tool"], "model": option["model"]}
        for option in role["options"]
        if guards.allows_model(option["model"])
    ]
    view["options"] = options
    view["options_withheld"] = len(role["options"]) - len(options)
    efforts = role["effort"]
    if efforts.get("by_tier"):
        raw, basis = tier.get("reviewer_effort"), "review tier"
    elif (risk == "high" or raised) and efforts.get("tricky"):
        raw = guards.effort(efforts["tricky"])
        basis = "tricky effort, risk high" if risk == "high" else "tricky effort, tier raised to high"
    else:
        raw, basis = guards.effort(efforts.get("default")), "default"
    view["effort"] = raw
    view["effort_basis"] = basis
    if role.get("effort_text") is not None:
        view["effort_text"] = role["effort_text"]
    return view


def build_delegation(task, route, classification):
    """The `delegation` block for `route`, or None when there is no usable policy."""
    policy, path = load_policy()
    if policy is None:
        return None
    guards = Guards(policy.get("hard_rules"))
    risk = classification.get("risk", "medium")
    hit = classify_policy_step(task)
    if hit:
        step_id = hit["id"]
        basis = "policy step {0} (matched: {1})".format(step_id, hit["signal"])
    else:
        step_id = ROUTE_TO_STEP.get(route, "build")
        basis = "route {0}".format(route)
        if route == "direct":
            task_type = classification.get("task_type")
            if task_type in NO_FILE_CHANGE_TYPES:
                step_id = "question"
                basis += " (task type {0}: no file changes)".format(task_type)
            else:
                basis += " (task type {0}: may change files)".format(task_type)
        if route == REVIEW_ROUTE:
            basis += "; uses the reviewer role with the wording of the {0} step".format(step_id)
    step = _find(policy["routing"], "id", step_id)
    delegation = {
        "advisory": True,
        "source": str(path),
        "basis": basis,
        "matched": None,
    }
    if step is None:
        delegation["missing_step"] = step_id
        return delegation
    delegation["matched"] = {
        key: step[key] for key in ("step", "id", "when", "action") + (("note",) if step.get("note") else ())
    }
    role_name = REVIEWER_ROLE if (route == REVIEW_ROUTE and not hit) else step.get("role")
    # Tier floors: step 1 and risky-code wording are at least the high tier,
    # whatever route's own risk says. The block says why in basis and raised_by.
    floor = None
    if risk != "high":
        if hit and hit["id"] == "production":
            floor = "step 1 production"
        else:
            term = high_tier_term(task)
            floor = "names {0}".format(term) if term else None
    if floor:
        delegation["basis"] += "; raised to high: {0}".format(floor)
    tier = _tier_view(policy, risk, guards, floor)
    delegation["role"] = (
        _role_view(policy, role_name, risk, tier, guards, bool(floor)) if role_name else None
    )
    delegation["review_tier"] = tier
    # One pass over everything taken from the policy file, so the text and the
    # JSON cannot disagree. source and basis are Mythify's own.
    own = dict((key, delegation.pop(key)) for key in ("source", "basis"))
    cleaned = guards.scrub(delegation)
    cleaned["guards"] = {
        "banned_models_withheld": (delegation["role"] or {}).get("options_withheld", 0),
        "effort_ceiling": guards.scrub(guards.ceiling),
        "effort_clamped": guards.clamped,
        "effort_withheld": guards.withheld_effort,
        "text_withheld": guards.withheld_text,
    }
    result = {"advisory": True, "source": own["source"], "basis": own["basis"]}
    result.update(cleaned)
    return result


def _plain(value):
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_text(value):
        return value
    return json.dumps(value, sort_keys=True)


def format_delegation(delegation):
    """Text lines for the delegation block, to follow the rest of the route output."""
    lines = [
        "Delegation (advisory; from the policy file {0}; Mythify does not pick or switch a model):".format(
            delegation["source"]
        )
    ]
    matched = delegation.get("matched")
    if not matched:
        lines.append("- Matched: the policy has no step with id {0} ({1})".format(
            delegation.get("missing_step", ""), delegation["basis"]))
        return lines
    lines.append("- Matched: step {0} {1}: {2} -> {3} ({4})".format(
        matched["step"], matched["id"], matched["when"], matched["action"], delegation["basis"]))
    if matched.get("note"):
        lines.append("- Policy note: {0}".format(matched["note"]))
    role = delegation.get("role")
    if role is None:
        lines.append("- Role: not set (the policy names no role for this step)")
    elif not role["in_policy"]:
        lines.append("- Role: {0} (no entry in the policy roles)".format(role["name"]))
    else:
        lines.append("- Role: {0}".format(role["name"]))
        options = "; ".join("{0} / {1}".format(o["tool"], o["model"]) for o in role["options"])
        if role["options_withheld"]:
            options = (options + "; " if options else "") + (
                "{0} option(s) withheld by the policy hard rules".format(role["options_withheld"]))
        lines.append("- Options: {0}".format(options or "no options listed"))
        lines.append("- Effort: {0} ({1})".format(
            role["effort"] if role["effort"] is not None else "not set", role["effort_basis"]))
        if role.get("effort_text"):
            lines.append("- Effort note: {0}".format(role["effort_text"]))
    tier = delegation["review_tier"]
    if not tier["in_policy"]:
        lines.append("- Review tier: {0} (risk {1}; no such tier in the policy)".format(tier["id"], tier["risk"]))
    else:
        parts = ["reviewer_effort: {0}".format(_plain(tier["reviewer_effort"])),
                 "user_review: {0}".format(_plain(tier["user_review"]))]
        parts.extend("{0}: {1}".format(key, _plain(value)) for key, value in tier["extra"].items())
        if "review_rounds_max" in tier:
            parts.append("review_rounds_max: {0}".format(tier["review_rounds_max"]))
        risk_note = "risk {0}".format(tier["risk"])
        if tier.get("raised_by"):
            risk_note += ", raised to high: {0}".format(tier["raised_by"])
        lines.append("- Review tier: {0} ({1}); {2}".format(tier["id"], risk_note, "; ".join(parts)))
    guards = delegation["guards"]
    notes = []
    if guards["effort_clamped"]:
        notes.append("an effort above the ceiling ({0}) or forbidden was lowered to the highest allowed level".format(guards["effort_ceiling"]))
    if guards["effort_withheld"]:
        notes.append("an effort the hard rules forbid was withheld (no allowed level at or below the ceiling)")
    if guards["text_withheld"]:
        notes.append("policy text naming a banned model or a forbidden effort was replaced with [withheld]")
    if notes:
        lines.append("- Guards: {0}".format("; ".join(notes)))
    return lines
