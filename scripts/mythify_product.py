"""Product planning records for the Mythify CLI.

A product record sits above execution plans and holds the answers a product
manager or director needs before anyone builds: the problem in the user's
words, one primary user, at most five measurable outcomes, what is out and
why, the bets with their kill criteria and human deciders, and the four
product risks. The record is material, never verification evidence. Two parts
are executable: `product check` lints readiness deterministically per stage,
and `product measure` runs an outcome's measure command through the same
recorded-verification path as `verify run`.

Two decisions belong to a human: approving product direction and setting a
bet verdict. Both are refused without a non-empty --human-input, exactly as a
HITL map ticket is; Mythify records the words but cannot verify who supplied
them, so an agent must never write them itself. Approval also requires a
clean readiness check. Editing outcomes, non-goals, bets, or risks after
approval returns the product to draft, so a bet the human never saw cannot be
promoted under an older approval.
"""

import json
import os
import re
import sys
from datetime import date, datetime

from mythify_evidence_guard import noop_verifier_reason, run_disabled
from mythify_io import (
    _timestamp_strictly_after,
    _write_text_atomic,
    jsonl_append_anchor,
    read_json,
    read_jsonl,
    read_jsonl_after_marker,
    read_jsonl_since,
    write_json_atomic,
)
from mythify_runtime_helpers import now_iso, slugify

SCHEMA_VERSION = 1
PRODUCT_STATUSES = ("draft", "approved", "closed")
PRODUCT_STAGES = ("prototype", "pre-launch", "growth", "enterprise")
TARGET_SOURCES = ("user", "cited", "decided", "hypothesis")
BET_STATUSES = ("proposed", "in_flight", "shipped", "stopped")
BET_VERDICTS = ("pending", "continue", "pivot", "stop")
DECIDE_VERDICTS = ("continue", "pivot", "stop")
RISK_KINDS = ("value", "usability", "feasibility", "viability")
RISK_QUESTIONS = {
    "value": "will the user want it",
    "usability": "can the user work it",
    "feasibility": "can we build it",
    "viability": "does it work for the business",
}
MAX_OUTCOMES = 5
SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
MEASURE_MARKER = " measured: "
FALSE_ENV_VALUES = ("0", "false", "no", "off")
NO_PRODUCT_MESSAGE = (
    "[FAIL] Product not found. Create one with: product create TITLE "
    "--problem TEXT --user TEXT"
)
PRODUCT_GUARDRAIL = (
    "A product record is material, not verification. Approval and bet verdicts "
    "carry a human's words; completion still needs executed checks, and "
    "outcomes count as moved only through product measure."
)
APPROVE_HUMAN_INPUT_MESSAGE = (
    "[FAIL] Human input required: approving product direction is a human "
    "decision. Show the human the one-pager (mythify product show), then pass "
    "--human-input with what the human actually said. Mythify records those "
    "words but cannot verify who supplied them, so an agent must never write "
    "them itself. Set MYTHIFY_REQUIRE_HUMAN_INPUT=0 only for legacy "
    "self-approved records."
)
DECIDE_HUMAN_INPUT_MESSAGE = (
    "[FAIL] Human input required: a bet verdict is the decider's call. Bring "
    "the evidence to the decider, then pass --human-input with what they "
    "decided. Mythify records those words but cannot verify who supplied "
    "them, so an agent must never write them itself. Set "
    "MYTHIFY_REQUIRE_HUMAN_INPUT=0 only for legacy self-decided verdicts."
)
HUMAN_INPUT_WAIVED_WARNING = (
    "[WARN] Human-input gate waived: MYTHIFY_REQUIRE_HUMAN_INPUT=0 is set, so "
    "this {0} was recorded without the human's words. The waiver is stamped "
    "on the record as human_input_waived."
)
MEASURE_DISABLED_MESSAGE = (
    "[FAIL] product measure is disabled: MYTHIFY_DISABLE_RUN=1 is set. No "
    "command was executed and nothing was recorded."
)
PACKET_INSTRUCTIONS = (
    "State the problem in the user's words without naming the solution.",
    "Name one primary user; anyone else waits for a later product record or a non-goal.",
    "Keep at most five outcomes, each with a metric, a baseline when known, a target, and the "
    "target's source: user, cited, decided, or hypothesis.",
    "Give an outcome a measure command whenever a script can read its metric.",
    "Record non-goals with the reason they are out and a revisit trigger: what would reopen them.",
    "Frame each bet as a hypothesis linked to the outcomes it should move, with a kill "
    "criterion, a named human decider, and a decide-by date.",
    "Cover the four product risks: value (will the user want it), usability (can the user "
    "work it), feasibility (can we build it), viability (does it work for the business).",
    "Run product check until it exits 0, then show the human the one-pager. Only the human "
    "approves: pass their words with product approve --human-input.",
    "Promote one approved bet at a time with product promote; a stopped bet never becomes a plan.",
    "After the plan ships, run product measure for each outcome and take the evidence to the "
    "decider; record their continue, pivot, or stop with product decide --human-input.",
)


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_product dependencies are not configured")


execute_verification = _missing_dependency
create_plan_record = _missing_dependency
attach_plan_lineage = _missing_dependency
environ = None


def fail(message):
    print(message, file=sys.stderr)


def configure_product_store(
    *,
    fail_func=None,
    execute_verification_func=None,
    create_plan_record_func=None,
    attach_plan_lineage_func=None,
    environ_map=None,
):
    global fail, execute_verification, create_plan_record, attach_plan_lineage, environ
    if fail_func is not None:
        fail = fail_func
    if execute_verification_func is not None:
        execute_verification = execute_verification_func
    if create_plan_record_func is not None:
        create_plan_record = create_plan_record_func
    if attach_plan_lineage_func is not None:
        attach_plan_lineage = attach_plan_lineage_func
    if environ_map is not None:
        environ = environ_map


def _env():
    return environ if environ is not None else os.environ


def require_human_input_enabled():
    raw = _env().get("MYTHIFY_REQUIRE_HUMAN_INPUT", "")
    return raw.strip().lower() not in FALSE_ENV_VALUES


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def products_dir(state):
    return state / "products"


def product_path(state, slug):
    return products_dir(state) / (slug + ".json")


def active_product_path(state):
    return state / "active_product"


def is_slug(value):
    """True for a stored name made of slug characters, so no path can escape."""
    return isinstance(value, str) and SLUG_PATTERN.fullmatch(value) is not None


def valid_product_record(record):
    return isinstance(record, dict) and isinstance(record.get("outcomes"), list)


def get_active_product_slug(state):
    path = active_product_path(state)
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if is_slug(value) and product_path(state, value).exists():
        return value
    return None


def find_product_slug(state, name):
    if name:
        candidate = slugify(name)
        if candidate and product_path(state, candidate).exists():
            return candidate
        return None
    return get_active_product_slug(state)


def load_product(state, name=None):
    slug = find_product_slug(state, name)
    if not slug:
        return None, None
    record = read_json(product_path(state, slug), None)
    if not valid_product_record(record):
        return slug, None
    return slug, record


def list_product_records(state):
    directory = products_dir(state)
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob("*.json")):
        record = read_json(path, None)
        if valid_product_record(record):
            items.append((path.stem, record))
    return items


def save_product(state, slug, record):
    record["updated"] = now_iso()
    write_json_atomic(product_path(state, slug), record)


def require_product(state, name):
    slug, record = load_product(state, name)
    if record is None:
        fail("[FAIL] Product not found: {0}".format(name) if name else NO_PRODUCT_MESSAGE)
    return slug, record


def items(record, key):
    return [item for item in record.get(key) or [] if isinstance(item, dict)]


def item_id(item):
    return str(item.get("id", "")).strip().upper()


def next_item_id(entries, prefix):
    numbers = []
    for entry in entries:
        raw = item_id(entry)
        if raw.startswith(prefix):
            try:
                numbers.append(int(raw[len(prefix):]))
            except ValueError:
                pass
    return "{0}{1}".format(prefix, max(numbers + [0]) + 1)


def find_item(entries, wanted):
    key = str(wanted or "").strip().upper()
    for entry in entries or []:
        if isinstance(entry, dict) and item_id(entry) == key:
            return entry
    return None


def filled(value):
    return bool(str(value or "").strip())


def id_number(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else 0


def bet_sort_key(bet):
    priority = bet.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        priority = 10 ** 9
    return (priority, id_number(bet.get("id")), item_id(bet))


def sorted_bets(record):
    return sorted(items(record, "bets"), key=bet_sort_key)


def bet_outcome_ids(bet):
    return [str(value).strip().upper() for value in bet.get("outcomes") or [] if str(value).strip()]


def parse_iso_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def revoke_approval(slug, record, change):
    """Return an approved product to draft when its direction changes."""
    if record.get("status") != "approved":
        return
    previous = dict(record.get("approval") or {})
    previous["revoked_at"] = now_iso()
    previous["revoked_by"] = change
    record.setdefault("approval_history", []).append(previous)
    record["status"] = "draft"
    record["approval"] = None
    fail(
        "[WARN] Product {0} changed after approval ({1}), so it is back to "
        "draft. A human must approve the changed direction before any bet is "
        "promoted.".format(slug, change)
    )


# ---------------------------------------------------------------------------
# Readiness check, measurements, plans, and traceability flags
# ---------------------------------------------------------------------------

def readiness_gaps(record):
    """Return (stage, gaps). Rules are cumulative from prototype to enterprise."""
    stage = record.get("stage") if record.get("stage") in PRODUCT_STAGES else "prototype"
    level = PRODUCT_STAGES.index(stage)
    outcomes = items(record, "outcomes")
    bets = items(record, "bets")
    risks = items(record, "risks")
    gaps = []

    def gap(code, item, message):
        gaps.append({"code": code, "item": item, "message": message})

    if not filled(record.get("problem")):
        gap("problem_missing", "problem", "problem: state the problem in the user's words")
    if not filled(record.get("user")):
        gap("user_missing", "user", "user: name one primary user")
    if not outcomes:
        gap("outcome_missing", "outcomes", "outcomes: add at least one measurable outcome")
    for outcome in outcomes:
        oid = item_id(outcome)
        if not filled(outcome.get("metric")):
            gap("outcome_metric_missing", oid, "{0}.metric: name the metric this outcome moves".format(oid))
        if not filled(outcome.get("target")):
            gap("outcome_target_missing", oid, "{0}.target: set the target value for the metric".format(oid))
    if not items(record, "non_goals"):
        gap("non_goal_missing", "non_goals", "non_goals: record at least one non-goal with its reason")
    known = {item_id(outcome) for outcome in outcomes}
    for bet in bets:
        bid = item_id(bet)
        linked = bet_outcome_ids(bet)
        if not linked:
            gap("bet_outcome_missing", bid, "{0}.outcomes: link the bet to the outcome it should move".format(bid))
        for oid in linked:
            if oid not in known:
                gap(
                    "bet_outcome_unknown",
                    bid,
                    "{0}.outcomes: {1} is not an outcome of this product".format(bid, oid),
                )
    if level >= 1:
        for outcome in outcomes:
            if outcome.get("target_source") not in TARGET_SOURCES:
                oid = item_id(outcome)
                gap(
                    "outcome_target_source_missing",
                    oid,
                    "{0}.target_source: say where the target came from (user, cited, "
                    "decided, or hypothesis); required from pre-launch".format(oid),
                )
        if not any(risk.get("kind") == "value" for risk in risks):
            gap(
                "value_risk_missing",
                "value",
                "risks.value: record at least one value risk (will the user want it); "
                "required from pre-launch",
            )
    if level >= 2:
        for outcome in outcomes:
            oid = item_id(outcome)
            measure = str(outcome.get("measure") or "").strip()
            if not measure:
                gap(
                    "outcome_measure_missing",
                    oid,
                    "{0}.measure: give the outcome an executable measure command; "
                    "required from growth".format(oid),
                )
            elif noop_verifier_reason(measure):
                gap(
                    "outcome_measure_noop",
                    oid,
                    "{0}.measure: the measure command cannot fail ({1}); required from "
                    "growth".format(oid, noop_verifier_reason(measure)),
                )
        for bet in bets:
            if not filled(bet.get("decider")):
                bid = item_id(bet)
                gap(
                    "bet_decider_missing",
                    bid,
                    "{0}.decider: name the human who decides continue, pivot, or stop; "
                    "required from growth".format(bid),
                )
    if level >= 3:
        for bet in bets:
            if not filled(bet.get("decide_by")):
                bid = item_id(bet)
                gap(
                    "bet_decide_by_missing",
                    bid,
                    "{0}.decide_by: set the date the decider rules on this bet; required "
                    "at enterprise".format(bid),
                )
        for risk in risks:
            if not filled(risk.get("mitigation")):
                rid = item_id(risk)
                gap(
                    "risk_mitigation_missing",
                    rid,
                    "{0}.mitigation: say how this risk is reduced; required at "
                    "enterprise".format(rid),
                )
        recorded = {risk.get("kind") for risk in risks}
        for kind in RISK_KINDS:
            if kind not in recorded:
                gap(
                    "risk_kind_missing",
                    kind,
                    "risks.{0}: record a {0} risk ({1}); enterprise covers all four "
                    "kinds".format(kind, RISK_QUESTIONS[kind]),
                )
    return stage, gaps


def measurements(state, slug, product, records=None):
    """Latest executed measurement per outcome id, from the verification ledger.

    Only records that product measure stamped count: verify run can borrow the
    parent and the claim wording, but never the product and outcome_id fields,
    and the command must be the outcome's current measure command. RECORDS
    narrows the ledger, as bet_measurements does; the default is all of it.
    """
    commands = {
        item_id(outcome): str(outcome.get("measure") or "").strip()
        for outcome in items(product, "outcomes")
    }
    if records is None:
        records = read_jsonl(state / "verifications.jsonl")
    latest = {}
    for record in records:
        if record.get("kind") != "executed" or record.get("product") != slug:
            continue
        parents = (record.get("lineage") or {}).get("parents") or []
        if not any(
            isinstance(parent, dict)
            and parent.get("kind") == "product"
            and parent.get("id") == slug
            for parent in parents
        ):
            continue
        head, marker, _metric = str(record.get("claim") or "").partition(MEASURE_MARKER)
        oid = head.strip().upper()
        if not marker or not oid or record.get("outcome_id") != oid:
            continue
        if not commands.get(oid) or record.get("command") != commands[oid]:
            continue
        latest[oid] = {
            "verification_id": record.get("id"),
            "verified": record.get("verified") is True,
            "exit_code": record.get("exit_code"),
            "command": record.get("command", ""),
            "timestamp": record.get("timestamp", ""),
        }
    return latest


def bet_measurements(state, slug, product, bet):
    """Latest measurement per outcome recorded after BET was promoted.

    A measurement taken before the bet became work, even while the product
    was a draft, says nothing about what the bet shipped. product promote
    stores a ledger anchor; when it cannot be placed, only records strictly
    after the promoted_at second count. A bet never promoted counts nothing.
    """
    promoted_at = str(bet.get("promoted_at") or "")
    if not promoted_at:
        return {}
    path = state / "verifications.jsonl"
    records = read_jsonl_after_marker(path, bet.get("promoted_anchor"), lower_bound=promoted_at)
    if records is None:
        records = [
            record
            for record in read_jsonl_since(path, promoted_at)
            if _timestamp_strictly_after(record.get("timestamp", ""), promoted_at)
        ]
    return measurements(state, slug, product, records)


def read_plan(state, slug):
    """A plan by slug from plans/, falling back to plans/archive/."""
    if not is_slug(slug):
        return None, False
    for path, archived in (
        (state / "plans" / (slug + ".json"), False),
        (state / "plans" / "archive" / (slug + ".json"), True),
    ):
        plan = read_json(path, None)
        if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
            return plan, archived
    return None, False


def bet_plan_view(state, bet):
    slug = bet.get("plan")
    if not slug:
        return None
    plan, archived = read_plan(state, slug)
    if plan is None:
        return {"id": slug, "found": False, "archived": False, "completed": 0, "total": 0, "complete": False}
    steps = plan.get("steps") or []
    done = sum(1 for step in steps if isinstance(step, dict) and step.get("status") == "completed")
    return {
        "id": slug,
        "found": True,
        "archived": archived,
        "completed": done,
        "total": len(steps),
        "complete": bool(steps) and done == len(steps),
    }


def traceability_flags(state, slug, record, today=None):
    """Traces, not opinions: gaps between outcomes, bets, plans, and measurements."""
    today = today or date.today()
    bets = sorted_bets(record)
    live = [bet for bet in bets if bet.get("status") != "stopped"]
    flags = []

    def flag(code, item, message):
        flags.append({"code": code, "item": item, "message": message})

    for outcome in items(record, "outcomes"):
        oid = item_id(outcome)
        if not any(oid in bet_outcome_ids(bet) for bet in live):
            flag(
                "outcome_without_bet",
                oid,
                "Outcome {0} has no live bet, so nothing is planned to move it.".format(oid),
            )
    for bet in bets:
        bid = item_id(bet)
        status = bet.get("status", "proposed")
        if record.get("status") == "approved" and status == "proposed" and not bet.get("plan"):
            flag(
                "approved_bet_without_plan",
                bid,
                "Bet {0} is approved direction but still proposed with no plan; promote "
                "it or stop it.".format(bid),
            )
        if status == "in_flight":
            view = bet_plan_view(state, bet)
            if view and view["complete"]:
                measured = bet_measurements(state, slug, record, bet)
                unmeasured = [
                    oid for oid in bet_outcome_ids(bet)
                    if not (measured.get(oid) or {}).get("verified")
                ]
                if unmeasured:
                    flag(
                        "completed_plan_unmeasured",
                        bid,
                        "Bet {0}'s plan {1} is complete, but {2} has no passing "
                        "measurement since the bet was promoted; run product "
                        "measure.".format(
                            bid, view["id"], ", ".join(unmeasured)
                        ),
                    )
        if bet.get("verdict", "pending") == "pending" and status != "stopped":
            deadline = parse_iso_date(bet.get("decide_by"))
            if deadline is not None and deadline < today:
                flag(
                    "verdict_overdue",
                    bid,
                    "Bet {0} passed its decide-by date {1} with the verdict still "
                    "pending; take the evidence to {2}.".format(
                        bid, deadline.isoformat(), bet.get("decider") or "the decider"
                    ),
                )
    return flags


def product_next_action(record, gaps, flags):
    if gaps:
        return "Close the readiness gaps, starting with {0}".format(gaps[0]["message"])
    if record.get("status") != "approved":
        return (
            "Show the human the one-pager (mythify product show), then record their "
            "approval with product approve --human-input."
        )
    if flags:
        return "Resolve the traceability flag: {0}".format(flags[0]["message"])
    for bet in sorted_bets(record):
        if bet.get("status") == "proposed" and not bet.get("plan"):
            return "Promote bet {0} with product promote {0}.".format(item_id(bet))
    if any(bet.get("status") == "in_flight" for bet in items(record, "bets")):
        return "Work the in-flight plan, then run product measure for its outcomes."
    return "Measure outcomes with product measure and take the evidence to each bet's decider."


def product_view(state, slug, record, today=None):
    """The record plus computed readiness, measurements, plans, and flags."""
    stage, gaps = readiness_gaps(record)
    measured = measurements(state, slug, record)
    flags = traceability_flags(state, slug, record, today)
    return {
        "id": slug,
        "active": get_active_product_slug(state) == slug,
        "record": record,
        "stage": stage,
        "ready": not gaps,
        "gaps": gaps,
        "measurements": measured,
        "plans": {
            item_id(bet): bet_plan_view(state, bet)
            for bet in items(record, "bets")
            if bet.get("plan")
        },
        "flags": flags,
        "next_action": product_next_action(record, gaps, flags),
    }


def active_product_view(state):
    """Compact view of the active product for status and route, or None."""
    if state is None:
        return None
    slug = get_active_product_slug(state)
    if not slug:
        return None
    record = read_json(product_path(state, slug), None)
    if not valid_product_record(record):
        return None
    view = product_view(state, slug, record)
    return {
        "id": slug,
        "title": record.get("title", ""),
        "status": record.get("status", "draft"),
        "stage": view["stage"],
        "ready": view["ready"],
        "gap_count": len(view["gaps"]),
        "flag_count": len(view["flags"]),
        "next_action": view["next_action"],
    }


def product_summary_rows(state):
    active = get_active_product_slug(state)
    rows = []
    for slug, record in list_product_records(state):
        stage, gaps = readiness_gaps(record)
        rows.append({
            "id": slug,
            "title": record.get("title", ""),
            "active": slug == active,
            "status": record.get("status", "draft"),
            "stage": stage,
            "outcomes": len(items(record, "outcomes")),
            "bets": len(items(record, "bets")),
            "ready": not gaps,
        })
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def approval_text(record):
    approval = record.get("approval")
    if record.get("status") != "approved" or not isinstance(approval, dict):
        return "not approved"
    words = approval.get("human_input") or "(waived: no human words recorded)"
    return 'approved {0}: "{1}"'.format(approval.get("at", ""), words)


def measurement_text(row):
    if not row:
        return "never measured"
    return "{0} (exit {1}, {2})".format(
        "passed" if row.get("verified") else "failed",
        row.get("exit_code"),
        row.get("timestamp", ""),
    )


def plan_text(view):
    if not view:
        return "no plan"
    if not view["found"]:
        return "plan {0} (not found)".format(view["id"])
    return "plan {0} ({1}/{2} steps completed{3})".format(
        view["id"], view["completed"], view["total"], ", archived" if view["archived"] else ""
    )


def format_product(view):
    record = view["record"]
    lines = [
        "[OK] Product {0}: {1}".format(view["id"], record.get("title", "")),
        "Status: {0}; {1}".format(record.get("status", "draft"), approval_text(record)),
        "Stage: {0}; appetite: {1}".format(view["stage"], record.get("appetite") or "not set"),
        "Readiness: {0}".format(
            "ready" if view["ready"] else "{0} gap(s); run product check".format(len(view["gaps"]))
        ),
        "Problem: {0}".format(record.get("problem", "")),
        "User: {0}".format(record.get("user", "")),
    ]
    outcomes = items(record, "outcomes")
    lines.append("Outcomes ({0}):".format(len(outcomes)))
    if not outcomes:
        lines.append("- none")
    for outcome in outcomes:
        oid = item_id(outcome)
        lines.append("- {0}: {1}".format(oid, outcome.get("statement", "")))
        lines.append(
            "  metric: {0}; baseline: {1}; target: {2} (source: {3})".format(
                outcome.get("metric", ""),
                outcome.get("baseline") or "not recorded",
                outcome.get("target", ""),
                outcome.get("target_source") or "not recorded",
            )
        )
        lines.append(
            "  measure: {0}; last measurement: {1}".format(
                outcome.get("measure") or "none",
                measurement_text(view["measurements"].get(oid)),
            )
        )
    non_goals = items(record, "non_goals")
    lines.append("Non-goals ({0}):".format(len(non_goals)))
    if not non_goals:
        lines.append("- none")
    for entry in non_goals:
        lines.append(
            "- {0}: {1} (why: {2}; revisit when: {3})".format(
                item_id(entry),
                entry.get("text", ""),
                entry.get("reason", ""),
                entry.get("revisit_when") or "not set",
            )
        )
    bets = sorted_bets(record)
    lines.append("Bets ({0}):".format(len(bets)))
    if not bets:
        lines.append("- none")
    for bet in bets:
        bid = item_id(bet)
        lines.append(
            "- {0} [{1}, verdict {2}{3}, priority {4}]: {5}".format(
                bid,
                bet.get("status", "proposed"),
                bet.get("verdict", "pending"),
                " (human input waived)" if bet.get("human_input_waived") else "",
                bet.get("priority", ""),
                bet.get("hypothesis", ""),
            )
        )
        lines.append(
            "  outcomes: {0}; kill: {1}".format(
                ", ".join(bet_outcome_ids(bet)) or "none", bet.get("kill", "")
            )
        )
        lines.append(
            "  decider: {0}; decide by: {1}; {2}".format(
                bet.get("decider") or "not named",
                bet.get("decide_by") or "not set",
                plan_text(view["plans"].get(bid)),
            )
        )
    lines.append("Risks:")
    risks = items(record, "risks")
    for kind in RISK_KINDS:
        group = [risk for risk in risks if risk.get("kind") == kind]
        lines.append("  {0} ({1}): {2} recorded".format(kind, RISK_QUESTIONS[kind], len(group) or "none"))
        for risk in group:
            lines.append(
                "  - {0}: {1} (mitigation: {2}; validation: {3})".format(
                    item_id(risk),
                    risk.get("text", ""),
                    risk.get("mitigation") or "none",
                    risk.get("validation") or "none",
                )
            )
    lines.append("Traceability ({0}):".format(len(view["flags"])))
    if not view["flags"]:
        lines.append("- none")
    for item in view["flags"]:
        lines.append("- {0} {1}: {2}".format(item["code"], item["item"], item["message"]))
    lines.append("Next action: {0}".format(view["next_action"]))
    lines.append("Guardrail: {0}".format(PRODUCT_GUARDRAIL))
    return "\n".join(lines)


def md_cell(value):
    return " ".join(str(value or "").split()).replace("|", "\\|") or "-"


def format_product_markdown(view):
    record = view["record"]
    lines = [
        "# {0}".format(record.get("title") or view["id"]),
        "",
        "- Product: `{0}`".format(view["id"]),
        "- Status: {0} ({1})".format(record.get("status", "draft"), approval_text(record)),
        "- Stage: {0}".format(view["stage"]),
        "- Appetite: {0}".format(record.get("appetite") or "not set"),
        "- Readiness: {0}".format(
            "ready" if view["ready"] else "{0} gap(s)".format(len(view["gaps"]))
        ),
        "",
        "## Problem",
        "",
        str(record.get("problem", "")),
        "",
        "## Primary user",
        "",
        str(record.get("user", "")),
        "",
        "## Outcomes",
        "",
    ]
    outcomes = items(record, "outcomes")
    if outcomes:
        lines.append("| ID | Outcome | Metric | Baseline | Target | Source | Last measurement |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for outcome in outcomes:
            oid = item_id(outcome)
            lines.append("| {0} |".format(" | ".join(md_cell(value) for value in (
                oid,
                outcome.get("statement"),
                outcome.get("metric"),
                outcome.get("baseline") or "not recorded",
                outcome.get("target"),
                outcome.get("target_source") or "not recorded",
                measurement_text(view["measurements"].get(oid)),
            ))))
    else:
        lines.append("None recorded.")
    lines.extend(["", "## Non-goals", ""])
    non_goals = items(record, "non_goals")
    if not non_goals:
        lines.append("None recorded.")
    for entry in non_goals:
        lines.append(
            "- **{0}** {1}. Why: {2}. Revisit when: {3}.".format(
                item_id(entry),
                str(entry.get("text", "")).rstrip("."),
                str(entry.get("reason", "")).rstrip("."),
                str(entry.get("revisit_when") or "not set").rstrip("."),
            )
        )
    lines.extend(["", "## Bets", ""])
    bets = sorted_bets(record)
    if bets:
        lines.append("| Priority | ID | Hypothesis | Outcomes | Status | Verdict | Kill criterion | Decider | Decide by | Plan |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for bet in bets:
            bid = item_id(bet)
            lines.append("| {0} |".format(" | ".join(md_cell(value) for value in (
                bet.get("priority", ""),
                bid,
                bet.get("hypothesis"),
                ", ".join(bet_outcome_ids(bet)),
                bet.get("status", "proposed"),
                bet.get("verdict", "pending"),
                bet.get("kill"),
                bet.get("decider") or "not named",
                bet.get("decide_by") or "not set",
                plan_text(view["plans"].get(bid)),
            ))))
    else:
        lines.append("None recorded.")
    lines.extend(["", "## Risks", ""])
    risks = items(record, "risks")
    for kind in RISK_KINDS:
        lines.append("### {0} ({1})".format(kind.capitalize(), RISK_QUESTIONS[kind]))
        lines.append("")
        group = [risk for risk in risks if risk.get("kind") == kind]
        if not group:
            lines.append("None recorded.")
        for risk in group:
            lines.append(
                "- **{0}** {1}. Mitigation: {2}. Validation: {3}.".format(
                    item_id(risk),
                    str(risk.get("text", "")).rstrip("."),
                    str(risk.get("mitigation") or "none").rstrip("."),
                    str(risk.get("validation") or "none").rstrip("."),
                )
            )
        lines.append("")
    lines.extend(["## Traceability", ""])
    if not view["flags"]:
        lines.append("No flags.")
    for item in view["flags"]:
        lines.append("- `{0}` {1}: {2}".format(item["code"], item["item"], item["message"]))
    lines.append("")
    return "\n".join(lines)


def build_product_prompt_packet(state, name=None, goal="", guardrail=PRODUCT_GUARDRAIL):
    """PM and director lens prompt packet for the active or named product."""
    slug, record = load_product(state, name) if state is not None else (None, None)
    if name and record is None:
        return {"error": "[FAIL] Product not found: {0}".format(name)}
    lines = ["Product planning prompt packet{0}".format(": " + slug if record else "")]
    if goal:
        lines.append("Session goal: {0}".format(goal))
    context = {"goal": goal, "product": None}
    if record:
        view = product_view(state, slug, record)
        context["product"] = {
            "id": slug,
            "title": record.get("title", ""),
            "status": record.get("status", "draft"),
            "stage": view["stage"],
            "ready": view["ready"],
            "gaps": view["gaps"][:8],
            "flags": view["flags"][:8],
        }
        lines.extend([
            "Title: {0}".format(record.get("title", "")),
            "Status: {0}; stage: {1}".format(record.get("status", "draft"), view["stage"]),
            "Problem: {0}".format(record.get("problem", "")),
            "User: {0}".format(record.get("user", "")),
            "Outcomes: {0}; non-goals: {1}; bets: {2}; risks: {3}".format(
                len(items(record, "outcomes")),
                len(items(record, "non_goals")),
                len(items(record, "bets")),
                len(items(record, "risks")),
            ),
        ])
        if view["gaps"]:
            lines.append("Readiness gaps ({0}):".format(len(view["gaps"])))
            lines.extend("- {0}".format(gap["message"]) for gap in view["gaps"][:8])
        if view["flags"]:
            lines.append("Traceability flags ({0}):".format(len(view["flags"])))
            lines.extend("- {0}".format(item["message"]) for item in view["flags"][:8])
        next_action = view["next_action"]
    else:
        lines.append("Active product: none")
        next_action = (
            'Create the record: mythify product create "TITLE" --problem "..." --user "..."'
        )
    lines.extend(["", "Instructions:"])
    lines.extend("- {0}".format(text) for text in PACKET_INSTRUCTIONS)
    lines.append("Next action: {0}".format(next_action))
    lines.append("Guardrail: {0}".format(guardrail))
    context["next_action"] = next_action
    return {
        "kind": "product",
        "selected_kind": "product",
        "title": "Product planning prompt packet",
        "source": {"type": "product", "id": slug if record else None},
        "context": context,
        "next_prompt": "\n".join(lines),
        "guardrail": guardrail,
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_product_create(args, state):
    title = (args.title or "").strip()
    problem = (args.problem or "").strip()
    user = (args.user or "").strip()
    for value, message in (
        (title, "[FAIL] Title required: pass a non-empty TITLE."),
        (problem, "[FAIL] Problem required: state the problem in the user's words with --problem."),
        (user, "[FAIL] User required: name one primary user with --user."),
    ):
        if not value:
            fail(message)
            return 1
    base = slugify(args.name or title) or "product"
    slug = base
    suffix = 2
    while product_path(state, slug).exists():
        slug = "{0}-{1}".format(base[:36], suffix)
        suffix += 1
    stamp = now_iso()
    record = {
        "schema_version": SCHEMA_VERSION,
        "name": slug,
        "title": title,
        "status": "draft",
        "stage": args.stage,
        "problem": problem,
        "user": user,
        "appetite": (args.appetite or "").strip(),
        "created": stamp,
        "updated": stamp,
        "approval": None,
        "outcomes": [],
        "non_goals": [],
        "bets": [],
        "risks": [],
    }
    save_product(state, slug, record)
    _write_text_atomic(active_product_path(state), slug + "\n")
    print("[OK] Created product: {0} (draft, stage {1}). Active product set to {0}.".format(slug, args.stage))
    print(
        "Next: add outcomes (product outcome), non-goals (product non-goal), bets "
        "(product bet), and risks (product risk), then run product check."
    )
    return 0


def cmd_product_outcome(args, state):
    slug, record = require_product(state, args.product)
    if record is None:
        return 1
    outcomes = record.setdefault("outcomes", [])
    if len(outcomes) >= MAX_OUTCOMES:
        fail(
            "[FAIL] Product {0} already has {1} outcomes, the most a product record "
            "keeps. Merge or cut an outcome before adding another.".format(slug, MAX_OUTCOMES)
        )
        return 1
    statement = (args.statement or "").strip()
    metric = (args.metric or "").strip()
    target = (args.target or "").strip()
    for value, message in (
        (statement, "[FAIL] Outcome statement required: pass a non-empty STATEMENT."),
        (metric, "[FAIL] Metric required: name the metric this outcome moves with --metric."),
        (target, "[FAIL] Target required: set the target value with --target."),
    ):
        if not value:
            fail(message)
            return 1
    measure = (args.measure or "").strip() or None
    if measure and noop_verifier_reason(measure):
        fail(
            "[FAIL] Measure command looks like a no-op ({0}): {1}. A measurement must "
            "be able to fail; pass a command that reads the metric.".format(
                noop_verifier_reason(measure), measure
            )
        )
        return 1
    outcome = {
        "id": next_item_id(outcomes, "O"),
        "statement": statement,
        "metric": metric,
        "baseline": (args.baseline or "").strip(),
        "target": target,
        "target_source": args.target_source,
        "measure": measure,
        "created": now_iso(),
    }
    outcomes.append(outcome)
    revoke_approval(slug, record, "outcome {0} added".format(outcome["id"]))
    save_product(state, slug, record)
    print("[OK] Added outcome {0} to product {1}: {2}".format(outcome["id"], slug, statement))
    print("Metric: {0}; target: {1}".format(metric, target))
    if not measure:
        print("No measure command: product measure cannot record this outcome until one is set.")
    return 0


def cmd_product_non_goal(args, state):
    slug, record = require_product(state, args.product)
    if record is None:
        return 1
    text = (args.text or "").strip()
    reason = (args.reason or "").strip()
    if not text or not reason:
        fail("[FAIL] A non-goal needs TEXT and a non-empty --reason saying why it is out.")
        return 1
    entries = record.setdefault("non_goals", [])
    entry = {
        "id": next_item_id(entries, "N"),
        "text": text,
        "reason": reason,
        "revisit_when": (args.revisit_when or "").strip(),
        "created": now_iso(),
    }
    entries.append(entry)
    revoke_approval(slug, record, "non-goal {0} added".format(entry["id"]))
    save_product(state, slug, record)
    print("[OK] Added non-goal {0} to product {1}: {2}".format(entry["id"], slug, text))
    print("Why: {0}".format(reason))
    return 0


def cmd_product_bet(args, state):
    slug, record = require_product(state, args.product)
    if record is None:
        return 1
    hypothesis = (args.hypothesis or "").strip()
    kill = (args.kill or "").strip()
    if not hypothesis:
        fail("[FAIL] Bet hypothesis required: pass a non-empty HYPOTHESIS.")
        return 1
    if not kill:
        fail("[FAIL] Kill criterion required: say what result stops this bet with --kill.")
        return 1
    linked = []
    for raw in args.outcome or []:
        for candidate in str(raw).split(","):
            candidate = candidate.strip().upper()
            if not candidate:
                continue
            if find_item(record.get("outcomes"), candidate) is None:
                fail("[FAIL] Outcome {0} not found in product {1}.".format(candidate, slug))
                return 1
            if candidate not in linked:
                linked.append(candidate)
    if not linked:
        fail("[FAIL] A bet must name at least one outcome it should move with --outcome.")
        return 1
    bets = record.setdefault("bets", [])
    priority = args.priority
    if priority is None:
        known = [bet.get("priority") for bet in items(record, "bets")]
        known = [value for value in known if isinstance(value, int) and not isinstance(value, bool)]
        priority = max(known + [0]) + 1
    decide_by = (args.decide_by or "").strip()
    bet = {
        "id": next_item_id(bets, "B"),
        "hypothesis": hypothesis,
        "outcomes": linked,
        "kill": kill,
        "decider": (args.decider or "").strip(),
        "decide_by": decide_by,
        "priority": priority,
        "status": "proposed",
        "verdict": "pending",
        "verdict_human_input": "",
        "plan": None,
        "created": now_iso(),
    }
    bets.append(bet)
    revoke_approval(slug, record, "bet {0} added".format(bet["id"]))
    save_product(state, slug, record)
    print("[OK] Added bet {0} to product {1}: {2}".format(bet["id"], slug, hypothesis))
    print("Moves: {0}; kill: {1}; priority {2}".format(", ".join(linked), kill, priority))
    if decide_by and parse_iso_date(decide_by) is None:
        print(
            "[WARN] decide_by is not an ISO date (YYYY-MM-DD), so product show cannot "
            "flag it when it passes."
        )
    return 0


def cmd_product_risk(args, state):
    slug, record = require_product(state, args.product)
    if record is None:
        return 1
    text = (args.text or "").strip()
    if not text:
        fail("[FAIL] Risk text required: pass a non-empty TEXT.")
        return 1
    risks = record.setdefault("risks", [])
    risk = {
        "id": next_item_id(risks, "R"),
        "text": text,
        "kind": args.kind,
        "mitigation": (args.mitigation or "").strip(),
        "validation": (args.validation or "").strip(),
        "created": now_iso(),
    }
    risks.append(risk)
    revoke_approval(slug, record, "risk {0} added".format(risk["id"]))
    save_product(state, slug, record)
    print("[OK] Added {0} risk {1} to product {2}: {3}".format(args.kind, risk["id"], slug, text))
    return 0


def cmd_product_check(args, state):
    slug, record = require_product(state, args.name)
    if record is None:
        return 1
    stage, gaps = readiness_gaps(record)
    if args.json_output:
        print(json.dumps({"ready": not gaps, "stage": stage, "gaps": gaps}, indent=2))
        return 0 if not gaps else 2
    if gaps:
        print("[WARN] Product {0} has {1} gap(s) at stage {2}:".format(slug, len(gaps), stage))
        for gap in gaps:
            print("- {0}".format(gap["message"]))
        print("Close the gaps, then run product check again; approval needs a clean check.")
        return 2
    print("[OK] Product {0} is ready at stage {1}: no gaps.".format(slug, stage))
    if record.get("status") != "approved":
        print("Next: show the human the one-pager (mythify product show), then product approve --human-input.")
    return 0


def take_human_input(args, message):
    """Return (human_input, waived), or (None, False) when the gate refuses."""
    human_input = (args.human_input or "").strip()
    if human_input:
        return human_input, False
    if require_human_input_enabled():
        fail(message)
        return None, False
    return "", True


def cmd_product_approve(args, state):
    slug, record = require_product(state, args.name)
    if record is None:
        return 1
    human_input, waived = take_human_input(args, APPROVE_HUMAN_INPUT_MESSAGE)
    if human_input is None:
        return 1
    stage, gaps = readiness_gaps(record)
    if gaps:
        fail(
            "[FAIL] Product {0} has {1} readiness gap(s) at stage {2}; approval needs "
            "a clean product check:".format(slug, len(gaps), stage)
        )
        for gap in gaps:
            fail("- {0}".format(gap["message"]))
        return 1
    if record.get("status") == "approved":
        print("[WARN] Product {0} is already approved. Nothing changed.".format(slug))
        return 0
    approval = {"human_input": human_input, "at": now_iso(), "stage": stage}
    if waived:
        approval["human_input_waived"] = True
        fail(HUMAN_INPUT_WAIVED_WARNING.format("approval"))
    record["approval"] = approval
    record["status"] = "approved"
    save_product(state, slug, record)
    print("[OK] Approved product {0} at stage {1}.".format(slug, stage))
    if human_input:
        print("Human input: {0}".format(human_input))
    print("Next action: {0}".format(product_next_action(record, [], [])))
    return 0


def cmd_product_decide(args, state):
    slug, record = require_product(state, args.product)
    if record is None:
        return 1
    bet = find_item(record.get("bets"), args.bet)
    if bet is None:
        fail("[FAIL] Bet {0} not found in product {1}.".format(args.bet, slug))
        return 1
    human_input, waived = take_human_input(args, DECIDE_HUMAN_INPUT_MESSAGE)
    if human_input is None:
        return 1
    bid = item_id(bet)
    if bet.get("status") == "stopped":
        fail("[FAIL] Bet {0} is stopped, and a stopped bet is final. Record a new bet instead.".format(bid))
        return 1
    bet["verdict"] = args.verdict
    bet["verdict_human_input"] = human_input
    bet["decided_at"] = now_iso()
    # The stamp describes the latest verdict, so a verdict with the human's
    # words clears a waiver left by an earlier one.
    bet.pop("human_input_waived", None)
    if waived:
        bet["human_input_waived"] = True
        fail(HUMAN_INPUT_WAIVED_WARNING.format("verdict"))
    if args.verdict == "stop":
        bet["status"] = "stopped"
    save_product(state, slug, record)
    print("[OK] Recorded verdict {0} for bet {1} in product {2}.".format(args.verdict, bid, slug))
    if human_input:
        print("Human input: {0}".format(human_input))
    if args.verdict == "stop":
        print("Bet {0} is stopped. Its plan, if any, stays on disk; archive it with plan archive.".format(bid))
    elif args.verdict == "pivot":
        print("Pivot: record the changed bet with product bet, then stop this one once the new bet replaces it.")
    return 0


def parse_steps(raw):
    """Return (steps, error) for an optional --steps JSON array."""
    if not raw:
        return [], None
    try:
        parsed = json.loads(raw)
    except ValueError as err:
        return None, "[FAIL] Invalid --steps JSON: {0}".format(err)
    if not isinstance(parsed, list):
        return None, "[FAIL] --steps must be a JSON array of step objects."
    return parsed, None


def plan_file_exists(state, plan_slug):
    return is_slug(plan_slug) and (state / "plans" / (plan_slug + ".json")).exists()


def cmd_product_promote(args, state):
    slug, record = require_product(state, args.product)
    if record is None:
        return 1
    if record.get("status") != "approved":
        fail(
            "[FAIL] Product {0} is {1}, not approved. A human approves product "
            "direction before any bet becomes a plan: run product check, show the "
            "human the one-pager, then product approve --human-input.".format(
                slug, record.get("status", "draft")
            )
        )
        return 1
    bet = find_item(record.get("bets"), args.bet)
    if bet is None:
        fail("[FAIL] Bet {0} not found in product {1}.".format(args.bet, slug))
        return 1
    bid = item_id(bet)
    if bet.get("status") == "stopped":
        fail("[FAIL] Bet {0} is stopped; a stopped bet never becomes a plan.".format(bid))
        return 1
    if plan_file_exists(state, bet.get("plan")):
        fail(
            "[FAIL] Bet {0} was already promoted to plan {1}. Work that plan; a bet "
            "holds one plan at a time.".format(bid, bet.get("plan"))
        )
        return 1
    steps, error = parse_steps(args.steps)
    if error:
        fail(error)
        return 1
    product_ref = {"product": slug, "bet": bid, "outcomes": bet_outcome_ids(bet)}
    plan_slug, error = create_plan_record(
        state,
        str(bet.get("hypothesis", "")),
        name=args.plan or "{0}-{1}".format(slug, bid.lower()),
        steps=steps,
        extra={"product_ref": product_ref},
    )
    if error:
        fail(error)
        return 1
    bet["status"] = "in_flight"
    bet["plan"] = plan_slug
    bet["promoted_at"] = now_iso()
    # Measurements recorded before this point cannot ship the bet.
    bet["promoted_anchor"] = jsonl_append_anchor(state / "verifications.jsonl")
    save_product(state, slug, record)
    attach_plan_lineage(state, plan_slug, ["product:" + slug])
    print("[OK] Promoted bet {0} of product {1} to plan {2}.".format(bid, slug, plan_slug))
    print("Goal: {0}".format(bet.get("hypothesis", "")))
    print("Outcomes to move: {0}; kill: {1}".format(", ".join(product_ref["outcomes"]), bet.get("kill", "")))
    if not steps:
        print("The plan has no steps yet; add them with plan add-step.")
    print("After the plan ships, record the result with product measure.")
    return 0


def mark_shipped_bets(state, slug, record):
    """In-flight bets whose plan is complete and whose every outcome's latest
    measurement since promotion is green."""
    shipped = []
    for bet in items(record, "bets"):
        if bet.get("status") != "in_flight":
            continue
        view = bet_plan_view(state, bet)
        outcomes = bet_outcome_ids(bet)
        if not (view and view["complete"] and outcomes):
            continue
        measured = bet_measurements(state, slug, record, bet)
        if all((measured.get(oid) or {}).get("verified") for oid in outcomes):
            bet["status"] = "shipped"
            bet["shipped_at"] = now_iso()
            shipped.append(item_id(bet))
    return shipped


def cmd_product_measure(args, state):
    if run_disabled(_env()):
        fail(MEASURE_DISABLED_MESSAGE)
        return 2
    slug, record = require_product(state, args.product)
    if record is None:
        return 1
    outcome = find_item(record.get("outcomes"), args.outcome)
    if outcome is None:
        fail("[FAIL] Outcome {0} not found in product {1}.".format(args.outcome, slug))
        return 1
    oid = item_id(outcome)
    command = str(outcome.get("measure") or "").strip()
    if not command:
        fail(
            "[FAIL] Outcome {0} has no measure command, so nothing can be measured. "
            "Record the outcome with a --measure command that reads the metric.".format(oid)
        )
        return 1
    reason = noop_verifier_reason(command)
    if reason:
        fail(
            "[FAIL] Outcome {0} measure command looks like a no-op ({1}): {2}. A "
            "measurement that cannot fail proves nothing; nothing was executed or "
            "recorded.".format(oid, reason, command)
        )
        return 1
    claim = "{0}{1}{2}".format(oid, MEASURE_MARKER, outcome.get("metric", ""))
    context = {
        "plan": None,
        "step_id": None,
        "step_title": None,
        "step_status": None,
        "product": slug,
        "outcome_id": oid,
    }
    try:
        result = execute_verification(
            state, command, claim, timeout=args.timeout, context=context,
            parents=["product:" + slug],
        )
    except ValueError as exc:
        fail("[FAIL] Invalid lineage: {0}.".format(exc))
        return 1
    if not result["verified"]:
        print(
            "[FAIL] UNMEASURED outcome {0}: {1} (exit {2}, {3:.2f}s)".format(
                oid, command, result["exit_code"], result["duration_seconds"]
            )
        )
        for channel in ("stdout", "stderr"):
            if result.get(channel + "_tail"):
                print("--- {0} (tail) ---".format(channel))
                print(result[channel + "_tail"])
        return 2
    print(
        "[OK] MEASURED outcome {0}: {1} (exit 0, {2:.2f}s)".format(
            oid, command, result["duration_seconds"]
        )
    )
    print("Target: {0}; the command's exit code is the verdict.".format(outcome.get("target", "")))
    shipped = mark_shipped_bets(state, slug, record)
    if shipped:
        save_product(state, slug, record)
        print(
            "Shipped: {0} (plan complete and every targeted outcome measured "
            "green since promotion).".format(", ".join(shipped))
        )
    return 0


def cmd_product_show(args, state):
    slug, record = require_product(state, args.name)
    if record is None:
        return 1
    view = product_view(state, slug, record)
    if args.json_output:
        payload = dict(record)
        payload.update({
            "id": slug,
            "active": view["active"],
            "ready": view["ready"],
            "gaps": view["gaps"],
            "measurements": view["measurements"],
            "plans": view["plans"],
            "flags": view["flags"],
            "next_action": view["next_action"],
        })
        print(json.dumps(payload, indent=2))
    elif args.markdown:
        print(format_product_markdown(view), end="")
    else:
        print(format_product(view))
    return 0


def cmd_product_list(args, state):
    rows = product_summary_rows(state)
    if args.json_output:
        print(json.dumps(rows, indent=2))
        return 0
    print("[OK] Products ({0}):".format(len(rows)))
    if not rows:
        print("  none")
    for row in rows:
        print(
            "{0}{1}: {2}, stage {3}, {4} outcome(s), {5} bet(s), {6} - {7}".format(
                "* " if row["active"] else "  ",
                row["id"],
                row["status"],
                row["stage"],
                row["outcomes"],
                row["bets"],
                "ready" if row["ready"] else "gaps",
                row["title"],
            )
        )
    return 0
