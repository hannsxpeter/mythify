"""Eval evolution subcommand parser for the Mythify CLI.

Kept beside the main parser so the eval grammar stays cohesive and the
top-level parser module stays under the runtime source-size ceiling.
"""

from __future__ import annotations


def add_eval_parser(sub, symbols):
    """Attach the `eval` command family to SUB using the CLI's handler symbols."""
    globals().update(symbols)

    evals = sub.add_parser(
        "eval",
        help="Grow the local eval harness through human-gated proposals.",
        description=(
            "The eval evolution loop: durable failure signals become candidate "
            "harness scenarios, a candidate must show a passing executed verify "
            "run, and adoption into the scenario registry requires the human's "
            "words. Proposals are material; only executed runs count as "
            "evidence. CLI-only."
        ),
    )
    eval_sub = evals.add_subparsers(dest="eval_command", metavar="ACTION", required=True)

    p = eval_sub.add_parser(
        "scan",
        help="Show eval gap signals from durable state. Read-only.",
        description=(
            "Surface failure reflections, failing executed verifications, "
            "repeated failing commands, and recent lessons as material for "
            "drafting candidate scenarios. Writes nothing."
        ),
    )
    p.add_argument("--recent", type=int, default=10, help="Records per source. Default 10.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_eval_scan)

    p = eval_sub.add_parser(
        "propose",
        help="Propose a candidate scenario from a file.",
        description=(
            "Register a candidate scenario as a proposal. The file carries one "
            "scenario object with name, title, task_category, task, and files. "
            "The proposal's verify command is always its own red-baseline "
            "check, and adoption matches evidence by proposal, so another "
            "proposal's run cannot stand in for it."
        ),
    )
    p.add_argument("title", help="Proposal title. Reports refer to it by name.")
    p.add_argument(
        "--scenario-file",
        dest="scenario_file",
        required=True,
        help="Path to the candidate scenario JSON.",
    )
    p.add_argument(
        "--rationale",
        required=True,
        help="The observed failure or gap this scenario would catch.",
    )
    p.add_argument(
        "--source",
        action="append",
        help="A signal behind the rationale: a reflection, lesson, or report. Repeatable.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_eval_propose)

    p = eval_sub.add_parser(
        "baseline",
        help="Check that a candidate scenario starts red.",
        description=(
            "Materialize the proposal's files into a scratch workspace and run "
            "its verifier. Exit 0 only when the verifier genuinely fails, "
            "because an eval that starts green cannot detect the failure it "
            "claims to test, and a verifier that was not found, is not "
            "executable, or collected no tests never exercised the scenario at "
            "all. This is the verify command for every proposal."
        ),
    )
    p.add_argument("id", help="Proposal id such as E1.")
    p.add_argument("--timeout", type=float, help="Timeout in seconds. Default 120.")
    p.set_defaults(handler=cmd_eval_baseline)

    p = eval_sub.add_parser(
        "verify",
        help="Run a proposal's own verify command.",
        description=(
            "Run the proposal's verify_command and record the executed evidence "
            "scoped to that proposal, satisfying its adoption gate. CLI-only."
        ),
    )
    p.add_argument("id", help="Proposal id such as E1.")
    p.add_argument("--timeout", type=float, help="Timeout in seconds. Default 300.")
    p.set_defaults(handler=cmd_eval_verify)

    p = eval_sub.add_parser(
        "adopt",
        help="Adopt a verified proposal into the scenario registry.",
        description=(
            "Move a proposal's scenario into .mythify/evals/scenarios.json so "
            "the local eval harness can load it. Requires --human-input and a "
            "passing executed run recorded since the proposal was created."
        ),
    )
    p.add_argument("id", help="Proposal id such as E1.")
    p.add_argument(
        "--human-input",
        dest="human_input",
        default="",
        help="What the human actually decided. Required.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_eval_adopt)

    p = eval_sub.add_parser(
        "reject",
        help="Reject a proposal with a recorded reason.",
        description=(
            "Close a proposal without adopting it. The scenario name becomes "
            "available again."
        ),
    )
    p.add_argument("id", help="Proposal id such as E1.")
    p.add_argument("--reason", required=True, help="Why this scenario is refused.")
    p.set_defaults(handler=cmd_eval_reject)

    p = eval_sub.add_parser(
        "list",
        help="List proposals and adopted scenarios.",
        description="List every proposal with status, plus the adopted registry names.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_eval_list)

    p = eval_sub.add_parser(
        "show",
        help="Show one proposal in full.",
        description="Show a proposal's rationale, sources, verify command, and state.",
    )
    p.add_argument("id", help="Proposal id such as E1.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_eval_show)
