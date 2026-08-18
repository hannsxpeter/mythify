"""Wayfinding decision maps for the Mythify CLI.

A map is the surface before a plan exists: a destination, decision tickets on a
blocking frontier, fog for what cannot be specified yet, and an out-of-scope
register for work ruled past the destination. Tickets resolve decisions; they
are not build slices.

Mythify's contribution over a plain decision board is evidence discipline.
A ticket worked with a human (grilling, prototype) cannot be resolved from the
agent's own words: the resolution must carry the human's input, exactly as an
attested claim never counts as executed proof. A task ticket that carries a
verify command must show a passing executed run scoped to that ticket before it
closes, exactly as a plan step must.
"""

import json
import sys

from mythify_evidence_guard import noop_verifier_reason
from mythify_io import _write_text_atomic, read_json, read_jsonl, write_json_atomic

MAP_TICKET_TYPES = ("research", "prototype", "grilling", "task")
MAP_TICKET_MODES = ("afk", "hitl")
# Wayfinding fixes the mode per type so a session cannot quietly downgrade a
# human conversation into something it answers by itself. Only `task` is
# genuinely either, so only `task` accepts an explicit --mode.
MAP_TYPE_MODES = {
    "research": "afk",
    "prototype": "hitl",
    "grilling": "hitl",
    "task": "afk",
}
MAP_TICKET_STATUSES = ("open", "closed", "out_of_scope")
MAP_STATUSES = ("charting", "clear", "promoted")
MAP_GUARDRAIL = (
    "Map tickets record decisions, not executed verification. A resolved ticket "
    "clears a question; completion still requires a passing executed check."
)
MAP_HUMAN_INPUT_MESSAGE = (
    "[FAIL] Human input required: this is a HITL ticket, so the agent cannot "
    "resolve it from its own words. Hold the conversation, then pass "
    "--human-input with what the human actually decided. Set "
    "MYTHIFY_REQUIRE_HUMAN_INPUT=0 only for legacy self-resolved tickets."
)
MAP_HUMAN_INPUT_WAIVED_WARNING = (
    "[WARN] Human-input gate waived: MYTHIFY_REQUIRE_HUMAN_INPUT=0 is set, so "
    "this HITL ticket resolved without the human's words. The waiver is "
    "stamped on the ticket as human_input_waived."
)
MAP_ANSWER_MESSAGE = (
    "[FAIL] Answer required: pass --answer describing the decision this ticket "
    "resolved."
)
FALSE_ENV_VALUES = ("0", "false", "no", "off")


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_maps dependencies are not configured")


now_iso = _missing_dependency
slugify = _missing_dependency
find_existing_slug_by_name = _missing_dependency
execute_verification = _missing_dependency
build_default_plan_steps = _missing_dependency
create_plan_record = _missing_dependency
attach_plan_lineage = _missing_dependency
environ = None


def fail(message):
    print(message, file=sys.stderr)


def configure_map_store(
    *,
    now_iso_func=None,
    slugify_func=None,
    fail_func=None,
    find_existing_slug_by_name_func=None,
    execute_verification_func=None,
    build_default_plan_steps_func=None,
    create_plan_record_func=None,
    attach_plan_lineage_func=None,
    environ_map=None,
):
    global now_iso, slugify, fail, find_existing_slug_by_name, execute_verification
    global build_default_plan_steps, create_plan_record, attach_plan_lineage, environ
    if now_iso_func is not None:
        now_iso = now_iso_func
    if slugify_func is not None:
        slugify = slugify_func
    if fail_func is not None:
        fail = fail_func
    if find_existing_slug_by_name_func is not None:
        find_existing_slug_by_name = find_existing_slug_by_name_func
    if execute_verification_func is not None:
        execute_verification = execute_verification_func
    if build_default_plan_steps_func is not None:
        build_default_plan_steps = build_default_plan_steps_func
    if create_plan_record_func is not None:
        create_plan_record = create_plan_record_func
    if attach_plan_lineage_func is not None:
        attach_plan_lineage = attach_plan_lineage_func
    if environ_map is not None:
        environ = environ_map


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def maps_dir(state):
    return state / "maps"


def map_path(state, slug):
    return maps_dir(state) / (slug + ".json")


def active_map_path(state):
    return maps_dir(state) / "active"


def get_active_map_slug(state):
    path = active_map_path(state)
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if value and map_path(state, value).exists():
        return value
    return None


def set_active_map_slug(state, slug):
    _write_text_atomic(active_map_path(state), slug + "\n")


def clear_active_map_slug(state, slug=None):
    path = active_map_path(state)
    if not path.exists():
        return
    if slug is not None and get_active_map_slug(state) != slug:
        return
    try:
        path.unlink()
    except OSError:
        pass


def find_map_slug(state, name):
    if name:
        return find_existing_slug_by_name(state, name, map_path)
    return get_active_map_slug(state)


def load_map(state, name=None):
    slug = find_map_slug(state, name)
    if not slug:
        return None, None
    record = read_json(map_path(state, slug), None)
    if not isinstance(record, dict) or not isinstance(record.get("tickets"), list):
        return slug, None
    return slug, record


def save_map(state, slug, record):
    record["updated"] = now_iso()
    write_json_atomic(map_path(state, slug), record)


def list_map_records(state):
    directory = maps_dir(state)
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob("*.json")):
        record = read_json(path, None)
        if isinstance(record, dict) and isinstance(record.get("tickets"), list):
            items.append((path.stem, record))
    return items


def next_map_id(items, prefix):
    numbers = []
    for item in items:
        raw = str(item.get("id", ""))
        if raw.startswith(prefix):
            try:
                numbers.append(int(raw[len(prefix):]))
            except ValueError:
                pass
    return "{0}{1}".format(prefix, max(numbers + [0]) + 1)


def find_ticket(record, ticket_id):
    wanted = str(ticket_id or "").strip().upper()
    for ticket in record.get("tickets") or []:
        if str(ticket.get("id", "")).upper() == wanted:
            return ticket
    return None


def open_tickets(record):
    return [t for t in record.get("tickets") or [] if t.get("status") == "open"]


def ticket_blockers(record, ticket):
    """Open tickets this ticket waits on. Closed and out-of-scope never block."""
    blockers = []
    for blocker_id in ticket.get("blocked_by") or []:
        blocker = find_ticket(record, blocker_id)
        if blocker is not None and blocker.get("status") == "open":
            blockers.append(blocker)
    return blockers


def frontier_tickets(record):
    """The edge of the known: open, unblocked, and unclaimed."""
    return [
        ticket
        for ticket in open_tickets(record)
        if not ticket_blockers(record, ticket) and not ticket.get("claimed_by")
    ]


def map_is_clear(record):
    """The way is clear when nothing is open and no fog remains ungraduated."""
    return not open_tickets(record) and not ungraduated_fog(record)


def ungraduated_fog(record):
    return [item for item in record.get("fog") or [] if not item.get("graduated_to")]


def ticket_name(ticket):
    """Refer by name. The id rides inside the name; it never stands in for it."""
    return "{0} ({1})".format(ticket.get("title", ""), ticket.get("id", ""))


def require_human_input_enabled():
    raw = (environ or {}).get("MYTHIFY_REQUIRE_HUMAN_INPUT", "")
    return raw.strip().lower() not in FALSE_ENV_VALUES


def normalized_command(text):
    return " ".join(str(text or "").split())


def passing_ticket_verification(state, ticket):
    """A passing executed run recorded since the ticket was claimed.

    The cursor is the append position of verifications.jsonl at claim time, so
    evidence recorded before the ticket was claimed can never be reused.
    """
    expected = normalized_command(ticket.get("verify_command"))
    if not expected:
        return None
    cursor = ticket.get("verification_cursor")
    records = read_jsonl(state / "verifications.jsonl")
    if isinstance(cursor, int) and cursor >= 0:
        records = records[cursor:]
    for record in records:
        if (
            record.get("kind") == "executed"
            and record.get("verified") is True
            and record.get("exit_code") == 0
            and normalized_command(record.get("command")) == expected
        ):
            return record
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def format_ticket_line(record, ticket, indent="- "):
    line = "{0}{1} [{2}/{3}]".format(
        indent, ticket_name(ticket), ticket.get("type", ""), ticket.get("mode", "")
    )
    if ticket.get("claimed_by"):
        line += " claimed by {0}".format(ticket.get("claimed_by"))
    blockers = ticket_blockers(record, ticket)
    if blockers:
        line += " blocked by: {0}".format(
            ", ".join(ticket_name(blocker) for blocker in blockers)
        )
    return line


def format_map(slug, record):
    tickets = record.get("tickets") or []
    frontier = frontier_tickets(record)
    claimed = [t for t in open_tickets(record) if t.get("claimed_by")]
    blocked = [
        t
        for t in open_tickets(record)
        if ticket_blockers(record, t) and not t.get("claimed_by")
    ]
    fog = ungraduated_fog(record)
    out_of_scope = record.get("out_of_scope") or []
    decisions = record.get("decisions") or []
    lines = [
        "[OK] Map {0}: {1}".format(slug, record.get("destination", "")),
        "Status: {0}".format(record.get("status", "charting")),
        "Tickets: {0} total, {1} open, {2} on the frontier".format(
            len(tickets), len(open_tickets(record)), len(frontier)
        ),
    ]
    if record.get("notes"):
        lines.append("Notes: {0}".format(record.get("notes")))
    lines.append("Decisions so far ({0}):".format(len(decisions)))
    if not decisions:
        lines.append("- none")
    for decision in decisions:
        lines.append(
            "- {0} ({1}) - {2}".format(
                decision.get("title", ""),
                decision.get("ticket_id", ""),
                decision.get("gist", ""),
            )
        )
    lines.append("Frontier ({0}):".format(len(frontier)))
    if not frontier:
        lines.append("- none")
    for ticket in frontier:
        lines.append(format_ticket_line(record, ticket))
    if claimed:
        lines.append("Claimed ({0}):".format(len(claimed)))
        for ticket in claimed:
            lines.append(format_ticket_line(record, ticket))
    if blocked:
        lines.append("Blocked ({0}):".format(len(blocked)))
        for ticket in blocked:
            lines.append(format_ticket_line(record, ticket))
    lines.append("Not yet specified ({0}):".format(len(fog)))
    if not fog:
        lines.append("- none")
    for item in fog:
        lines.append("- {0}: {1}".format(item.get("id"), item.get("note", "")))
    lines.append("Out of scope ({0}):".format(len(out_of_scope)))
    if not out_of_scope:
        lines.append("- none")
    for item in out_of_scope:
        text = "- {0}: {1}".format(item.get("id"), item.get("note", ""))
        if item.get("reason"):
            text += " (why: {0})".format(item.get("reason"))
        lines.append(text)
    if map_is_clear(record) and record.get("status") != "promoted":
        lines.append(
            "The way is clear: no open tickets and no fog. Hand off with map promote."
        )
    lines.append("Guardrail: {0}".format(MAP_GUARDRAIL))
    return "\n".join(lines)


def map_next_action(record):
    if record.get("status") == "promoted":
        return "This map is promoted; work its plan with step updates and verify run."
    if map_is_clear(record):
        return "The way is clear. Run map promote to hand the destination to a plan."
    claimed = [t for t in open_tickets(record) if t.get("claimed_by")]
    if claimed:
        ticket = claimed[0]
        return "Resolve the claimed ticket {0} with map resolve.".format(
            ticket_name(ticket)
        )
    frontier = frontier_tickets(record)
    if frontier:
        return "Claim the next frontier ticket {0} with map claim.".format(
            ticket_name(frontier[0])
        )
    if open_tickets(record):
        return (
            "Every open ticket is blocked. Resolve a blocker, or add a ticket that "
            "unblocks one."
        )
    return "Graduate a fog patch into a ticket with map ticket --from-fog."


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_map_create(args, state):
    base = slugify(args.name or args.destination) or "map"
    slug = base
    suffix = 2
    while map_path(state, slug).exists():
        slug = "{0}-{1}".format(base[:36], suffix)
        suffix += 1
    stamp = now_iso()
    fog = []
    for note in args.fog or []:
        fog.append({
            "id": next_map_id(fog, "F"),
            "note": note,
            "graduated_to": "",
            "created": stamp,
        })
    record = {
        "id": slug,
        "destination": args.destination,
        "notes": args.notes or "",
        "status": "charting",
        "tickets": [],
        "fog": fog,
        "out_of_scope": [],
        "decisions": [],
        "created": stamp,
        "updated": stamp,
    }
    save_map(state, slug, record)
    set_active_map_slug(state, slug)
    if args.json_output:
        print(json.dumps(record, indent=2))
        return 0
    print("[OK] Created map: {0}".format(slug))
    print("Destination: {0}".format(args.destination))
    print("Not yet specified: {0}".format(len(fog)))
    print("Next: add the decisions you can already state with map ticket.")
    return 0


def cmd_map_list(args, state):
    items = list_map_records(state)
    active = get_active_map_slug(state)
    if args.json_output:
        print(json.dumps([
            {"id": slug, **record, "active": slug == active}
            for slug, record in items
        ], indent=2))
        return 0
    print("[OK] Maps ({0}):".format(len(items)))
    if not items:
        print("  none")
    for slug, record in items:
        marker = "* " if slug == active else "  "
        print(
            "{0}{1}: {2} open, {3} on the frontier, {4} decided, {5} fog, status {6}".format(
                marker,
                slug,
                len(open_tickets(record)),
                len(frontier_tickets(record)),
                len(record.get("decisions") or []),
                len(ungraduated_fog(record)),
                record.get("status", "charting"),
            )
        )
    return 0


def cmd_map_show(args, state):
    slug, record = load_map(state, args.name)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    if args.json_output:
        payload = {"id": slug, **record}
        payload["frontier"] = [t.get("id") for t in frontier_tickets(record)]
        payload["clear"] = map_is_clear(record)
        payload["next_action"] = map_next_action(record)
        print(json.dumps(payload, indent=2))
        return 0
    print(format_map(slug, record))
    print("Next action: {0}".format(map_next_action(record)))
    return 0


def cmd_map_ticket(args, state):
    slug, record = load_map(state, args.map)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    mode = MAP_TYPE_MODES[args.type]
    if args.mode:
        if args.type != "task":
            fail(
                "[FAIL] Mode is fixed for {0} tickets ({1}). Only task tickets "
                "choose between afk and hitl.".format(args.type, mode)
            )
            return 1
        mode = args.mode
    blocked_by = []
    for raw in args.blocked_by or []:
        for candidate in str(raw).split(","):
            candidate = candidate.strip().upper()
            if not candidate:
                continue
            if find_ticket(record, candidate) is None:
                fail("[FAIL] Blocking ticket not found in map {0}: {1}".format(slug, candidate))
                return 1
            if candidate not in blocked_by:
                blocked_by.append(candidate)
    fog_item = None
    if args.from_fog:
        wanted = str(args.from_fog).strip().upper()
        for item in record.get("fog") or []:
            if str(item.get("id", "")).upper() == wanted:
                fog_item = item
                break
        if fog_item is None:
            fail("[FAIL] Fog patch not found in map {0}: {1}".format(slug, args.from_fog))
            return 1
        if fog_item.get("graduated_to"):
            fail(
                "[FAIL] Fog patch {0} already graduated into {1}.".format(
                    fog_item.get("id"), fog_item.get("graduated_to")
                )
            )
            return 1
    ticket = {
        "id": next_map_id(record.get("tickets") or [], "T"),
        "title": args.title,
        "question": args.question or args.title,
        "type": args.type,
        "mode": mode,
        "status": "open",
        "blocked_by": blocked_by,
        "claimed_by": "",
        "claimed_at": "",
        "verify_command": (args.verify or "").strip(),
        "resolution": "",
        "human_input": "",
        "resolved_at": "",
        "created": now_iso(),
    }
    if not ticket["verify_command"]:
        del ticket["verify_command"]
    else:
        noop_reason = noop_verifier_reason(ticket["verify_command"])
        if noop_reason:
            fail(
                "[WARN] Ticket verify command looks like a no-op ({0}): {1}. "
                "It will satisfy the resolution gate without checking "
                "anything.".format(noop_reason, ticket["verify_command"])
            )
    record.setdefault("tickets", []).append(ticket)
    if fog_item is not None:
        fog_item["graduated_to"] = ticket["id"]
    save_map(state, slug, record)
    print("[OK] Added ticket {0} to map {1}".format(ticket_name(ticket), slug))
    print("Type: {0} ({1})".format(ticket["type"], ticket["mode"]))
    if blocked_by:
        print("Blocked by: {0}".format(", ".join(blocked_by)))
    if fog_item is not None:
        print("Graduated fog patch {0}.".format(fog_item.get("id")))
    if ticket["mode"] == "hitl":
        print(
            "This ticket needs a human. Resolving it requires --human-input; the "
            "agent must not answer its own question."
        )
    return 0


def cmd_map_claim(args, state):
    slug, record = load_map(state, args.map)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    ticket = find_ticket(record, args.id)
    if ticket is None:
        fail("[FAIL] Ticket {0} not found in map {1}.".format(args.id, slug))
        return 1
    if ticket.get("status") != "open":
        fail("[FAIL] Ticket {0} is {1}, not open.".format(ticket_name(ticket), ticket.get("status")))
        return 1
    blockers = ticket_blockers(record, ticket)
    if blockers:
        fail(
            "[FAIL] Ticket {0} is blocked by: {1}. Resolve the blockers first.".format(
                ticket_name(ticket),
                ", ".join(ticket_name(blocker) for blocker in blockers),
            )
        )
        return 1
    claimant = (
        args.by
        or (environ or {}).get("MYTHIFY_MAP_CLAIMANT", "")
        or "session"
    ).strip()
    if ticket.get("claimed_by"):
        if ticket.get("claimed_by") == claimant:
            print("[WARN] Ticket {0} is already claimed by {1}.".format(ticket_name(ticket), claimant))
            return 0
        fail(
            "[FAIL] Ticket {0} is already claimed by {1}.".format(
                ticket_name(ticket), ticket.get("claimed_by")
            )
        )
        return 1
    # One decision ticket per session. Research runs in parallel because it
    # reads sources rather than settling anything.
    if ticket.get("type") != "research":
        held = [
            other
            for other in open_tickets(record)
            if other.get("claimed_by") == claimant and other.get("type") != "research"
        ]
        if held:
            fail(
                "[FAIL] {0} already holds {1}. Resolve it before claiming another "
                "decision ticket; only research tickets run in parallel.".format(
                    claimant, ticket_name(held[0])
                )
            )
            return 1
    ticket["claimed_by"] = claimant
    ticket["claimed_at"] = now_iso()
    ticket["verification_cursor"] = len(read_jsonl(state / "verifications.jsonl"))
    save_map(state, slug, record)
    print("[OK] Claimed ticket {0} for {1}".format(ticket_name(ticket), claimant))
    print("Question: {0}".format(ticket.get("question", "")))
    if ticket.get("mode") == "hitl":
        print("HITL: resolve only after the human answers; pass --human-input.")
    if ticket.get("verify_command"):
        print("Verify: {0}".format(ticket["verify_command"]))
        print("Run it with: mythify map verify {0}".format(ticket.get("id")))
    return 0


def cmd_map_verify(args, state):
    """Run a ticket's own verify command and scope the evidence to that ticket."""
    if (environ or {}).get("MYTHIFY_DISABLE_RUN") == "1":
        fail(
            "[FAIL] map verify is disabled: MYTHIFY_DISABLE_RUN=1 is set. No "
            "command was executed and nothing was recorded."
        )
        return 2
    slug, record = load_map(state, args.map)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    ticket = find_ticket(record, args.id)
    if ticket is None:
        fail("[FAIL] Ticket {0} not found in map {1}.".format(args.id, slug))
        return 1
    command = (ticket.get("verify_command") or "").strip()
    if not command:
        fail(
            "[FAIL] Ticket {0} has no verify_command. Add one with "
            "map ticket --verify, or run verify run manually.".format(ticket_name(ticket))
        )
        return 1
    if not ticket.get("claimed_by"):
        fail(
            "[FAIL] Ticket {0} is unclaimed. Claim it first with map claim so the "
            "evidence is scoped to this session.".format(ticket_name(ticket))
        )
        return 1
    context = {
        "map": slug,
        "ticket_id": ticket.get("id"),
        "ticket_title": ticket.get("title"),
        "ticket_type": ticket.get("type"),
    }
    claim = "map ticket {0}: {1}".format(ticket.get("id"), ticket.get("title", ""))
    result = execute_verification(state, command, claim, args.timeout, context)
    if result["verified"]:
        print(
            "[OK] VERIFIED ticket {0}: {1} (exit 0, {2:.2f}s)".format(
                ticket.get("id"), command, result["duration_seconds"]
            )
        )
        print("Next: mythify map resolve {0} --answer \"...\"".format(ticket.get("id")))
        return 0
    print(
        "[FAIL] UNVERIFIED ticket {0}: {1} (exit {2}, {3:.2f}s)".format(
            ticket.get("id"), command, result["exit_code"], result["duration_seconds"]
        )
    )
    if result["stdout_tail"]:
        print("--- stdout (tail) ---")
        print(result["stdout_tail"])
    if result["stderr_tail"]:
        print("--- stderr (tail) ---")
        print(result["stderr_tail"])
    return 2


def cmd_map_resolve(args, state):
    slug, record = load_map(state, args.map)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    ticket = find_ticket(record, args.id)
    if ticket is None:
        fail("[FAIL] Ticket {0} not found in map {1}.".format(args.id, slug))
        return 1
    if ticket.get("status") != "open":
        fail("[FAIL] Ticket {0} is already {1}.".format(ticket_name(ticket), ticket.get("status")))
        return 1
    answer = (args.answer or "").strip()
    if not answer:
        fail(MAP_ANSWER_MESSAGE)
        return 1
    out_of_scope = bool(args.out_of_scope)
    human_input = (args.human_input or "").strip()
    # Ruling a human's question out of scope is itself the human's call, so the
    # HITL gate applies on every resolution path, including --out-of-scope.
    if ticket.get("mode") == "hitl" and not human_input:
        if require_human_input_enabled():
            fail(MAP_HUMAN_INPUT_MESSAGE)
            return 1
        ticket["human_input_waived"] = True
        fail(MAP_HUMAN_INPUT_WAIVED_WARNING)
    if not out_of_scope:
        if not ticket.get("claimed_by"):
            fail(
                "[FAIL] Ticket {0} is unclaimed. Claim it before resolving so "
                "concurrent sessions skip it.".format(ticket_name(ticket))
            )
            return 1
        blockers = ticket_blockers(record, ticket)
        if blockers:
            fail(
                "[FAIL] Ticket {0} is blocked by: {1}.".format(
                    ticket_name(ticket),
                    ", ".join(ticket_name(blocker) for blocker in blockers),
                )
            )
            return 1
        if ticket.get("verify_command"):
            evidence = passing_ticket_verification(state, ticket)
            if evidence is None:
                fail(
                    "[FAIL] Verified evidence required: ticket {0} stores a verify "
                    "command, but no passing executed run with exit code 0 matching "
                    "it was recorded since the ticket was claimed. Run: mythify map "
                    "verify {1}".format(ticket_name(ticket), ticket.get("id"))
                )
                return 1
            ticket["verified_command"] = evidence.get("command", "")
    ticket["human_input"] = human_input
    stamp = now_iso()
    ticket["resolution"] = answer
    ticket["resolved_at"] = stamp
    ticket["status"] = "out_of_scope" if out_of_scope else "closed"
    ticket["claimed_by"] = ""
    if out_of_scope:
        entry = {
            "id": next_map_id(record.get("out_of_scope") or [], "X"),
            "note": ticket.get("title", ""),
            "reason": answer,
            "ticket_id": ticket.get("id"),
            "created": stamp,
        }
        record.setdefault("out_of_scope", []).append(entry)
    else:
        record.setdefault("decisions", []).append({
            "ticket_id": ticket.get("id"),
            "title": ticket.get("title", ""),
            "gist": args.gist or answer,
            "recorded": stamp,
        })
    for note in args.fog or []:
        record.setdefault("fog", []).append({
            "id": next_map_id(record.get("fog") or [], "F"),
            "note": note,
            "graduated_to": "",
            "created": stamp,
        })
    for note in args.scope_out or []:
        record.setdefault("out_of_scope", []).append({
            "id": next_map_id(record.get("out_of_scope") or [], "X"),
            "note": note,
            "reason": "ruled past the destination while resolving {0}".format(ticket.get("id")),
            "ticket_id": "",
            "created": stamp,
        })
    if map_is_clear(record) and record.get("status") == "charting":
        record["status"] = "clear"
    save_map(state, slug, record)
    if out_of_scope:
        print("[OK] Ruled ticket {0} out of scope in map {1}".format(ticket_name(ticket), slug))
        print("Why: {0}".format(answer))
        if ticket.get("human_input"):
            print("Human input: {0}".format(ticket["human_input"]))
    else:
        print("[OK] Resolved ticket {0} in map {1}".format(ticket_name(ticket), slug))
        print("Answer: {0}".format(answer))
        if ticket.get("human_input"):
            print("Human input: {0}".format(ticket["human_input"]))
    print("Next action: {0}".format(map_next_action(record)))
    return 0


def cmd_map_fog(args, state):
    slug, record = load_map(state, args.map)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    item = {
        "id": next_map_id(record.get("fog") or [], "F"),
        "note": args.note,
        "graduated_to": "",
        "created": now_iso(),
    }
    record.setdefault("fog", []).append(item)
    if record.get("status") == "clear":
        record["status"] = "charting"
    save_map(state, slug, record)
    print("[OK] Added fog patch {0} to map {1}".format(item["id"], slug))
    print("Graduate it with: mythify map ticket TITLE --type TYPE --from-fog {0}".format(item["id"]))
    return 0


def cmd_map_scope_out(args, state):
    slug, record = load_map(state, args.map)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    entry = {
        "id": next_map_id(record.get("out_of_scope") or [], "X"),
        "note": args.note,
        "reason": args.reason,
        "ticket_id": "",
        "created": now_iso(),
    }
    record.setdefault("out_of_scope", []).append(entry)
    if map_is_clear(record) and record.get("status") == "charting":
        record["status"] = "clear"
    save_map(state, slug, record)
    print("[OK] Ruled out of scope in map {0}: {1}".format(slug, entry["id"]))
    print("Why: {0}".format(args.reason))
    return 0


def cmd_map_promote(args, state):
    slug, record = load_map(state, args.name)
    if record is None:
        fail("[FAIL] Map not found. Create one with: map create DESTINATION")
        return 1
    if record.get("status") == "promoted":
        fail(
            "[FAIL] Map {0} was already promoted to plan {1}.".format(
                slug, record.get("promoted_plan", "")
            )
        )
        return 1
    if not map_is_clear(record):
        blockers = []
        if open_tickets(record):
            blockers.append("{0} open ticket(s)".format(len(open_tickets(record))))
        if ungraduated_fog(record):
            blockers.append("{0} fog patch(es)".format(len(ungraduated_fog(record))))
        fail(
            "[FAIL] Map {0} is not clear yet: {1}. A map is done when nothing is "
            "left to decide.".format(slug, " and ".join(blockers))
        )
        return 1
    steps = []
    if args.steps:
        try:
            parsed = json.loads(args.steps)
        except ValueError as err:
            fail("[FAIL] Invalid --steps JSON: {0}".format(err))
            return 1
        if not isinstance(parsed, list):
            fail("[FAIL] --steps must be a JSON array of step objects.")
            return 1
        steps = parsed
    elif args.horizon:
        steps = build_default_plan_steps(args.horizon)
    source = {
        "kind": "map",
        "map": slug,
        "destination": record.get("destination", ""),
        "decisions": record.get("decisions") or [],
        "out_of_scope": record.get("out_of_scope") or [],
    }
    plan_slug, error = create_plan_record(
        state,
        goal=record.get("destination", ""),
        name=args.plan or slug,
        steps=steps,
        source=source,
    )
    if error:
        fail(error)
        return 1
    record["status"] = "promoted"
    record["promoted_plan"] = plan_slug
    save_map(state, slug, record)
    attach_plan_lineage(state, plan_slug, ["map:" + slug])
    clear_active_map_slug(state, slug)
    print("[OK] Promoted map {0} to plan {1}".format(slug, plan_slug))
    print("Destination: {0}".format(record.get("destination", "")))
    print(
        "Carried into the plan: {0} decision(s), {1} out-of-scope entr(ies).".format(
            len(record.get("decisions") or []), len(record.get("out_of_scope") or [])
        )
    )
    if not steps:
        print("The plan has no steps yet; add them with plan add-step.")
    return 0
