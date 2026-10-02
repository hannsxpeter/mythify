"""Product planning subcommand parser for the Mythify CLI.

Kept beside the main parser, as the map grammar is, so the product surface
stays cohesive and the top-level parser module stays under the runtime
source-size ceiling.
"""

from __future__ import annotations

from mythify_product import (
    DECIDE_VERDICTS,
    PRODUCT_STAGES,
    RISK_KINDS,
    TARGET_SOURCES,
    cmd_product_approve,
    cmd_product_bet,
    cmd_product_check,
    cmd_product_create,
    cmd_product_decide,
    cmd_product_list,
    cmd_product_measure,
    cmd_product_non_goal,
    cmd_product_outcome,
    cmd_product_promote,
    cmd_product_risk,
    cmd_product_show,
)

PRODUCT_OPTION_HELP = "Product name. Defaults to the active product."


def add_product_parser(sub, _symbols=None):
    """Attach the `product` command family to SUB."""
    product = sub.add_parser(
        "product",
        help="Plan a product: problem, user, outcomes, non-goals, bets, risks.",
        description=(
            "Product planning from a product manager or director lens: the problem "
            "in the user's words, one primary user, at most five measurable "
            "outcomes, non-goals, bets with kill criteria and human deciders, and "
            "the four product risks. The record is material. product check lints "
            "readiness per stage; approve and decide need the human's words; "
            "promote turns an approved bet into a plan; measure records an "
            "outcome's metric as executed evidence."
        ),
    )
    product_sub = product.add_subparsers(dest="product_command", metavar="ACTION", required=True)

    p = product_sub.add_parser(
        "create",
        help="Create a draft product record and set it active.",
        description=(
            "Create a draft product with the problem in the user's words and one "
            "primary user, then set it active. The stage sets how strict product "
            "check is: prototype, pre-launch, growth, or enterprise."
        ),
    )
    p.add_argument("title", help="Product or initiative title.")
    p.add_argument("--problem", required=True, help="The problem in the user's words, without naming the solution.")
    p.add_argument("--user", required=True, help="The one primary user this product serves.")
    p.add_argument(
        "--stage",
        choices=PRODUCT_STAGES,
        default="prototype",
        help="Product stage; later stages add readiness rules. Defaults to prototype.",
    )
    p.add_argument("--appetite", default="", help="How much time or effort this is worth.")
    p.add_argument("--name", help="Product name. Defaults to a slug of the title.")
    p.set_defaults(handler=cmd_product_create)

    p = product_sub.add_parser(
        "outcome",
        help="Add a measurable outcome (at most five).",
        description=(
            "Add an outcome with a metric and a target, optionally a baseline, "
            "where the target came from, and a measure command that reads the "
            "metric. A product keeps at most five outcomes."
        ),
    )
    p.add_argument("statement", help="The change in the world this outcome describes.")
    p.add_argument("--metric", required=True, help="The metric that moves.")
    p.add_argument("--target", required=True, help="The target value for the metric.")
    p.add_argument("--baseline", default="", help="The metric's current value, when known.")
    p.add_argument(
        "--target-source",
        dest="target_source",
        choices=TARGET_SOURCES,
        help="Where the target came from: user, cited, decided, or hypothesis.",
    )
    p.add_argument(
        "--measure",
        default="",
        help="Shell command that reads the metric and exits 0 only when the target is met.",
    )
    p.add_argument("--product", help=PRODUCT_OPTION_HELP)
    p.set_defaults(handler=cmd_product_outcome)

    p = product_sub.add_parser(
        "non-goal",
        help="Record a non-goal with its reason.",
        description="Record what this product will not do, why, and what would reopen it.",
    )
    p.add_argument("text", help="What is out.")
    p.add_argument("--reason", required=True, help="Why it is out.")
    p.add_argument("--revisit-when", dest="revisit_when", default="", help="The trigger that would reopen it.")
    p.add_argument("--product", help=PRODUCT_OPTION_HELP)
    p.set_defaults(handler=cmd_product_non_goal)

    p = product_sub.add_parser(
        "bet",
        help="Add a bet linked to outcomes, with a kill criterion.",
        description=(
            "Add a bet: a hypothesis linked to the outcomes it should move, the "
            "result that would stop it, the human who decides, and when. Lower "
            "priority numbers go sooner."
        ),
    )
    p.add_argument("hypothesis", help="What we believe and will build to test it.")
    p.add_argument(
        "--outcome",
        action="append",
        required=True,
        help="Outcome id this bet should move, such as O1. Repeat or comma separate.",
    )
    p.add_argument("--kill", required=True, help="The result that stops this bet.")
    p.add_argument("--decider", default="", help="The human who decides continue, pivot, or stop.")
    p.add_argument("--decide-by", dest="decide_by", default="", help="Decision date, as YYYY-MM-DD.")
    p.add_argument("--priority", type=int, help="Priority; lower goes sooner. Defaults to after the last bet.")
    p.add_argument("--product", help=PRODUCT_OPTION_HELP)
    p.set_defaults(handler=cmd_product_bet)

    p = product_sub.add_parser(
        "risk",
        help="Record a value, usability, feasibility, or viability risk.",
        description=(
            "Record one of the four product risks: value (will the user want it), "
            "usability (can the user work it), feasibility (can we build it), or "
            "viability (does it work for the business)."
        ),
    )
    p.add_argument("text", help="The risk.")
    p.add_argument("--kind", choices=RISK_KINDS, required=True, help="value, usability, feasibility, or viability.")
    p.add_argument("--mitigation", default="", help="How the risk is reduced.")
    p.add_argument("--validation", default="", help="How we will learn whether the risk is real.")
    p.add_argument("--product", help=PRODUCT_OPTION_HELP)
    p.set_defaults(handler=cmd_product_risk)

    p = product_sub.add_parser(
        "check",
        help="Lint product readiness for its stage.",
        description=(
            "Deterministic readiness lint. Every stage needs a problem, a user, at "
            "least one outcome with metric and target, a non-goal, and bets linked "
            "to existing outcomes. pre-launch adds target sources and a value risk; "
            "growth adds measure commands and bet deciders; enterprise adds "
            "decide-by dates, risk mitigations, and all four risk kinds. Exits 0 "
            "when ready and 2 when gaps remain."
        ),
    )
    p.add_argument("name", nargs="?", help=PRODUCT_OPTION_HELP)
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_product_check)

    p = product_sub.add_parser(
        "approve",
        help="Record the human's approval of product direction.",
        description=(
            "Approve the product direction. Refused without a non-empty "
            "--human-input carrying what the human said, and refused while "
            "product check has gaps. Mythify records the words but cannot "
            "verify who supplied them; an agent must never write them itself."
        ),
    )
    p.add_argument("name", nargs="?", help=PRODUCT_OPTION_HELP)
    p.add_argument(
        "--human-input",
        dest="human_input",
        default="",
        help="What the human actually said when approving. Required.",
    )
    p.set_defaults(handler=cmd_product_approve)

    p = product_sub.add_parser(
        "decide",
        help="Record the decider's verdict on a bet.",
        description=(
            "Record continue, pivot, or stop for a bet. Refused without a "
            "non-empty --human-input carrying the decider's verdict. Mythify "
            "records the words but cannot verify who supplied them; an agent "
            "must never write them itself. stop also stops the bet."
        ),
    )
    p.add_argument("bet", help="Bet id such as B1.")
    p.add_argument("--verdict", choices=DECIDE_VERDICTS, required=True, help="continue, pivot, or stop.")
    p.add_argument(
        "--human-input",
        dest="human_input",
        default="",
        help="What the decider actually decided. Required.",
    )
    p.add_argument("--product", help=PRODUCT_OPTION_HELP)
    p.set_defaults(handler=cmd_product_decide)

    p = product_sub.add_parser(
        "promote",
        help="Turn an approved bet into an execution plan.",
        description=(
            "Create a plan whose goal is the bet's hypothesis. Requires an approved "
            "product and a bet that is not stopped. The plan records lineage parent "
            "product:NAME and a product_ref; the bet goes in flight."
        ),
    )
    p.add_argument("bet", help="Bet id such as B1.")
    p.add_argument("--steps", help="JSON array of step objects for the new plan, as plan create takes.")
    p.add_argument("--plan", help="Plan name. Defaults to the product name plus the bet id.")
    p.add_argument("--product", help=PRODUCT_OPTION_HELP)
    p.set_defaults(handler=cmd_product_promote)

    p = product_sub.add_parser(
        "measure",
        help="Run an outcome's measure command and record executed evidence.",
        description=(
            "Run the outcome's measure command through the same recorded "
            "verification path as verify run, with lineage parent product:NAME "
            "and the claim 'O1 measured: METRIC'. Exits 0 when the command passes, "
            "2 when it fails, 1 when there is nothing to measure."
        ),
    )
    p.add_argument("outcome", help="Outcome id such as O1.")
    p.add_argument("--product", help=PRODUCT_OPTION_HELP)
    p.add_argument("--timeout", type=float, help="Timeout in seconds. Default 300.")
    p.set_defaults(handler=cmd_product_measure)

    p = product_sub.add_parser(
        "show",
        help="Show the product one-pager with traceability flags.",
        description=(
            "Show the one-pager: problem, user, stage, appetite, approval, "
            "outcomes with their last measurement, non-goals, bets in priority "
            "order with plan progress, risks by kind, and traceability flags."
        ),
    )
    p.add_argument("name", nargs="?", help=PRODUCT_OPTION_HELP)
    output = p.add_mutually_exclusive_group()
    output.add_argument("--json", dest="json_output", action="store_true", help="Print the record plus computed flags as JSON.")
    output.add_argument("--markdown", action="store_true", help="Print Markdown suitable for a ROADMAP.md.")
    p.set_defaults(handler=cmd_product_show)

    p = product_sub.add_parser(
        "list",
        help="List product records.",
        description="List products with the active marker, status, stage, and readiness.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_product_list)
