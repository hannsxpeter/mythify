"""Wayfinding map subcommand parser for the Mythify CLI.

Kept beside the main parser so the decision-map grammar stays cohesive and the
top-level parser module stays under the runtime source-size ceiling.
"""

from __future__ import annotations


def add_map_parser(sub, symbols):
    """Attach the `map` command family to SUB using the CLI's handler symbols."""
    globals().update(symbols)

    mapping = sub.add_parser(
        "map",
        help="Chart and work a wayfinding decision map.",
        description=(
            "Chart work that is too big for one session as a decision map: a "
            "destination, decision tickets on a blocking frontier, fog for what "
            "cannot be specified yet, and an out-of-scope register. Tickets "
            "resolve decisions, not build slices. A human-in-the-loop ticket "
            "cannot be resolved from the agent's own words, and a task ticket "
            "with a verify command needs a passing executed run."
        ),
    )
    map_sub = mapping.add_subparsers(dest="map_command", metavar="ACTION", required=True)

    p = map_sub.add_parser(
        "create",
        help="Create a decision map and set it active.",
        description=(
            "Create a decision map with a destination, optional notes, and any "
            "fog you can already see, then set it active."
        ),
    )
    p.add_argument("destination", help="What reaching the end of this map looks like.")
    p.add_argument("--name", help="Map name. Defaults to a slug of the destination.")
    p.add_argument("--notes", default="", help="Domain, skills, and standing preferences for this effort.")
    p.add_argument(
        "--fog",
        action="append",
        help="A question you can see coming but cannot yet state sharply. Repeatable.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_map_create)

    p = map_sub.add_parser(
        "list",
        help="List decision maps.",
        description="List maps with active marker, open and frontier counts, and status.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_map_list)

    p = map_sub.add_parser(
        "show",
        help="Show the active or named map at low resolution.",
        description=(
            "Show the map body: destination, notes, decisions so far, the "
            "frontier, claimed and blocked tickets, fog, and out of scope."
        ),
    )
    p.add_argument("name", nargs="?", help="Map name. Defaults to active.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_map_show)

    p = map_sub.add_parser(
        "ticket",
        help="Add a decision ticket to a map.",
        description=(
            "Add a decision ticket. The type fixes whether a human is in the "
            "loop: research and task run AFK, prototype and grilling are HITL. "
            "Only task tickets may choose their mode."
        ),
    )
    p.add_argument("title", help="Ticket title. This is its name; reports refer to it by name.")
    p.add_argument(
        "--type",
        choices=MAP_TICKET_TYPES,
        required=True,
        help="research, prototype, grilling, or task.",
    )
    p.add_argument("--question", default="", help="The decision this ticket resolves. Defaults to the title.")
    p.add_argument(
        "--mode",
        choices=MAP_TICKET_MODES,
        help="Only for task tickets: afk when the agent can drive it alone, hitl otherwise.",
    )
    p.add_argument(
        "--blocked-by",
        dest="blocked_by",
        action="append",
        help="Ticket ids this ticket waits on, comma separated or repeated.",
    )
    p.add_argument("--verify", default="", help="Executable proof for a task ticket's done-condition.")
    p.add_argument("--from-fog", dest="from_fog", help="Fog patch id this ticket graduates.")
    p.add_argument("--map", help="Map name. Defaults to active.")
    p.set_defaults(handler=cmd_map_ticket)

    p = map_sub.add_parser(
        "claim",
        help="Claim a frontier ticket before working it.",
        description=(
            "Claim an open, unblocked ticket so concurrent sessions skip it. One "
            "decision ticket per claimant at a time; research tickets run in "
            "parallel."
        ),
    )
    p.add_argument("id", help="Ticket id such as T1.")
    p.add_argument("--by", default="", help="Claimant. Defaults to MYTHIFY_MAP_CLAIMANT or session.")
    p.add_argument("--map", help="Map name. Defaults to active.")
    p.set_defaults(handler=cmd_map_claim)

    p = map_sub.add_parser(
        "verify",
        help="Run a task ticket's own verify command.",
        description=(
            "Run the ticket's verify_command and record the executed evidence "
            "scoped to that ticket, satisfying its resolution gate. CLI-only."
        ),
    )
    p.add_argument("id", help="Ticket id such as T1.")
    p.add_argument("--map", help="Map name. Defaults to active.")
    p.add_argument("--timeout", type=float, help="Timeout in seconds. Default 300.")
    p.set_defaults(handler=cmd_map_verify)

    p = map_sub.add_parser(
        "resolve",
        help="Resolve a claimed ticket with its answer.",
        description=(
            "Close a claimed ticket with the decision it reached. HITL tickets "
            "require --human-input; task tickets with a verify command require a "
            "passing executed run recorded since the claim. Use --out-of-scope "
            "for a ticket that turns out to sit past the destination."
        ),
    )
    p.add_argument("id", help="Ticket id such as T1.")
    p.add_argument("--answer", required=True, help="The decision this ticket reached.")
    p.add_argument("--gist", default="", help="One-line index entry. Defaults to the answer.")
    p.add_argument(
        "--human-input",
        dest="human_input",
        default="",
        help="What the human actually decided. Required for HITL tickets.",
    )
    p.add_argument(
        "--out-of-scope",
        dest="out_of_scope",
        action="store_true",
        help="Rule the ticket past the destination instead of recording a decision.",
    )
    p.add_argument("--fog", action="append", help="New fog the answer surfaced. Repeatable.")
    p.add_argument(
        "--scope-out",
        dest="scope_out",
        action="append",
        help="Work the answer ruled past the destination. Repeatable.",
    )
    p.add_argument("--map", help="Map name. Defaults to active.")
    p.set_defaults(handler=cmd_map_resolve)

    p = map_sub.add_parser(
        "fog",
        help="Record a question you cannot state sharply yet.",
        description=(
            "Add a patch to Not yet specified: in scope, headed toward the "
            "destination, but not sharp enough to ticket."
        ),
    )
    p.add_argument("note", help="The dim view: the suspected question or area to revisit.")
    p.add_argument("--map", help="Map name. Defaults to active.")
    p.set_defaults(handler=cmd_map_fog)

    p = map_sub.add_parser(
        "scope-out",
        help="Rule work past the destination out of scope.",
        description=(
            "Record work consciously ruled beyond this destination. Out-of-scope "
            "work never graduates; it returns only as a fresh effort."
        ),
    )
    p.add_argument("note", help="The work being ruled out.")
    p.add_argument("--reason", required=True, help="Why it sits past the destination.")
    p.add_argument("--map", help="Map name. Defaults to active.")
    p.set_defaults(handler=cmd_map_scope_out)

    p = map_sub.add_parser(
        "promote",
        help="Hand a clear map off to a plan.",
        description=(
            "Create a plan from a map whose way is clear: no open tickets and no "
            "fog. The plan carries the destination as its goal and the decisions "
            "and out-of-scope register as provenance."
        ),
    )
    p.add_argument("name", nargs="?", help="Map name. Defaults to active.")
    p.add_argument("--plan", help="Plan name. Defaults to the map name.")
    p.add_argument("--steps", help="JSON array of step objects for the new plan.")
    p.add_argument(
        "--horizon",
        type=int,
        help="Create N default lookahead steps when --steps is omitted.",
    )
    p.set_defaults(handler=cmd_map_promote)

