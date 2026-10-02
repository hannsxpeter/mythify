"""Argument parser construction for the Mythify CLI."""

from __future__ import annotations

import argparse
import sys

from mythify_lineage import add_lineage_parser
from mythify_quality import add_quality_parser
from mythify_verification_commands import add_verification_parsers
from mythify_map_parser import add_map_parser
from mythify_product_parser import add_product_parser
from mythify_mcp import serve as serve_mcp

# EX_USAGE from sysexits.h. Exit 2 is the recorded "unverified" verdict, so a
# usage error must never share it.
USAGE_EXIT_CODE = 64


class MythifyArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors exit 64 instead of argparse's 2.

    Subparsers inherit the class (add_subparsers defaults parser_class to the
    parent's type), so every command level reports usage errors the same way.
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(USAGE_EXIT_CODE, "{0}: error: {1}\n".format(self.prog, message))


def build_parser(symbols):
    globals().update(symbols)
    parser = MythifyArgumentParser(
        prog="mythify.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Mythify v{0}: evidence protocol for AI coding agents. Route broad ".format(VERSION) +
            "work first, keep state in .mythify, and verify completion claims "
            "with executed commands."
        ),
        epilog=(
            "Recommended front door:\n"
            "  mythify route \"TASK\"        choose direct, plan, map, product, outcome, review, failure_recovery, or handoff\n"
            "  mythify status              reorient from durable state, evidence, and attention items\n"
            "  mythify verify run ...      record executed proof before a completion claim\n"
            "  mythify report ...          show chat-ready progress and issue reports\n"
            "\n"
            "Workflow primitives:\n"
            "  plan, step, outcome, map, product, prompt\n"
            "\n"
            "Advanced surfaces:\n"
            "  history, summary, loop-fit, memory, lesson, logs, reflect, review, lineage,\n"
            "  protocol, init, mcp\n"
            "\n"
            "Strict evidence mode:\n"
            "  completed steps require a passing verify run by default\n"
            "  set MYTHIFY_REQUIRE_VERIFIED_STEP=0 only for legacy prose-only completion\n"
        ),
    )
    parser.add_argument("--version", action="version", version="Mythify v{0}".format(VERSION))
    parser.set_defaults(needs_state=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    p = sub.add_parser(
        "init",
        help="Create ./.mythify with subdirectories and an empty memory.json.",
        description=(
            "Create ./.mythify with subdirectories and an empty memory.json. "
            "If already inside a workspace, print [WARN] and exit 0."
        ),
    )
    p.set_defaults(handler=cmd_init, needs_state=False)

    protocol = sub.add_parser(
        "protocol",
        help="Protocol copy checks.",
        description="Check copied protocol files against the CLI's embedded source protocol hash.",
    )
    protocol_sub = protocol.add_subparsers(dest="protocol_command", metavar="ACTION", required=True)
    p = protocol_sub.add_parser(
        "check",
        help="Verify copied protocol files match this CLI.",
        description=(
            "Verify copied protocol files match this CLI's embedded source protocol "
            "hash. With no paths, check the source protocol when present, a local "
            "AGENTS.md (full copy), a local CLAUDE.md (the generated pointer that "
            "imports AGENTS.md, or a legacy full copy), and a leftover legacy .cursorrules."
        ),
    )
    p.add_argument("paths", nargs="*", help="Protocol copy files to check.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_protocol_check, needs_state=False)

    p = sub.add_parser(
        "status",
        help="Orient: active plan, outcome, map, and product, evidence breakdown, and attention items.",
        description=(
            "Read-only orientation: the active plan with step icons and the next "
            "pending step, the active outcome, map, and product (with readiness "
            "and traceability flag count), counts, the executed and "
            "attested evidence breakdown, recent verification records, and "
            "attention items (failed checks, no-op or zero-test passes, ledger "
            "chain breaks, legacy opt-outs, stale or drifted outcome evidence) "
            "sorted with issues before warnings."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=DEFAULT_STATUS_RECENT,
        help=(
            "Number of recent verification records and attention items to show. "
            "Defaults to {0}.".format(DEFAULT_STATUS_RECENT)
        ),
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser(
        "history",
        help="Show a read-only verification history.",
        description=(
            "Read-only verification history: executed and attested verification "
            "records, verdicts, commands, exit codes, duration, and plan or step "
            "context from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=10,
        help="Number of recent verification records to show. Defaults to 10.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_history)

    p = sub.add_parser(
        "report",
        help="Show a chat-ready live work report from durable Mythify events.",
        description=(
            "Chat-ready live work report: plan creation, step updates, "
            "verification records, and reflections from durable state. By "
            "default it advances a cursor so repeated calls with --since last "
            "only show new events; use --peek to leave the cursor unchanged."
        ),
    )
    p.add_argument(
        "--since",
        choices=REPORT_SINCE_MODES,
        default=None,
        help="Event window to report: last cursor or start of state. Defaults to last.",
    )
    p.add_argument(
        "--format",
        dest="report_format",
        choices=REPORT_FORMATS,
        default="chat",
        help="Output format: chat or json. Defaults to chat.",
    )
    p.add_argument(
        "--recent",
        type=int,
        default=DEFAULT_REPORT_RECENT,
        help="Maximum events to show. Defaults to {0}.".format(DEFAULT_REPORT_RECENT),
    )
    p.add_argument(
        "--cursor",
        default="default",
        help="Report cursor name. Defaults to default.",
    )
    p.add_argument(
        "--peek",
        action="store_true",
        help="Do not advance the report cursor.",
    )
    p.add_argument(
        "--mark",
        action="store_true",
        help="Advance the report cursor to the latest event without showing old events.",
    )
    p.set_defaults(handler=cmd_report)

    p = sub.add_parser(
        "route",
        help="Choose the next workflow route from prompt text and durable state.",
        description=(
            "Read-only workflow quarterback: classify a prompt, inspect durable "
            "state, and choose direct, plan, map, product, outcome, review, "
            "failure_recovery, or handoff routing. The JSON output carries the "
            "deterministic classification with neutral framing, parallelism, and "
            "review advisories plus the loop-fit assessment; the host chooses "
            "whether and where to delegate."
        ),
    )
    p.add_argument("task", help="Task request or problem statement to route.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_route, needs_state="optional")

    prompt = sub.add_parser(
        "prompt",
        help="Render read-only workflow prompt packets.",
        description=(
            "Render chat-ready workflow prompt packets from durable Mythify state. "
            "Prompt packets are steering material for the host agent, not "
            "verification evidence."
        ),
    )
    prompt_sub = prompt.add_subparsers(dest="prompt_command", metavar="KIND", required=True)

    def add_prompt_common(parser):
        parser.add_argument(
            "--goal",
            default="",
            help="Optional host goal to include in the packet.",
        )
        parser.add_argument(
            "--verify",
            default="",
            help="Optional verifier command to include in the packet.",
        )
        parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")

    p = prompt_sub.add_parser(
        "failure",
        help="Render a failure recovery prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="failure")

    p = prompt_sub.add_parser(
        "handoff",
        help="Render a session handoff prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="handoff")

    p = prompt_sub.add_parser(
        "review",
        help="Render a review or audit prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="review")

    p = prompt_sub.add_parser(
        "map",
        help="Render a wayfinding map prompt packet for the next decision ticket.",
    )
    p.add_argument("name", nargs="?", help="Map name. Defaults to the active map.")
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="map")

    p = prompt_sub.add_parser(
        "product",
        help="Render a product planning prompt packet from a product manager and director lens.",
    )
    p.add_argument("name", nargs="?", help="Product name. Defaults to the active product.")
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="product")

    p = prompt_sub.add_parser(
        "next",
        help="Select and render the next useful prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="next")

    add_map_parser(sub, symbols)
    add_product_parser(sub, symbols)
    add_lineage_parser(sub, symbols)
    add_quality_parser(sub, symbols)

    p = sub.add_parser(
        "loop-fit",
        help="Advise whether a task should be a loop, supervised, or done directly.",
        description=(
            "Read-only decision support: assess TASK against the loop-worthiness "
            "gates (is there a machine-checkable done-condition, does the work "
            "recur, is there a reproduction environment, does it need human "
            "judgment) and recommend a bounded self-driving loop (outcome run), a "
            "host-supervised loop or verifier-gated plan, or doing it directly. "
            "It runs nothing and records no evidence, and works without an "
            "initialized .mythify workspace."
        ),
    )
    p.add_argument("task", help="Task request or problem statement to assess.")
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    p.set_defaults(handler=cmd_loop_fit, needs_state=False)

    outcome = sub.add_parser(
        "outcome",
        help="Run outcome-driven loops with verifier and iteration budget.",
        description=(
            "Manage an outcome loop: define success, run verifier checks, track "
            "iteration budget, and tell the host whether to retry, stop, or report success."
        ),
    )
    outcome_sub = outcome.add_subparsers(dest="outcome_command", metavar="ACTION", required=True)

    p = outcome_sub.add_parser(
        "start",
        help="Start an outcome loop and set it active.",
        description="Start an outcome loop with a concrete verifier and iteration budget.",
    )
    p.add_argument("goal", help="Outcome goal.")
    p.add_argument("--success", required=True, help="Human-readable success criteria.")
    p.add_argument("--verify", required=True, help="Shell command that verifies the outcome.")
    p.add_argument("--metric", default="", help="Optional shell command that emits a metric.")
    p.add_argument(
        "--metric-floor",
        type=float,
        default=None,
        metavar="N",
        help=(
            "Minimum metric score required for success. Requires --metric; a "
            "green verifier with a score below the floor does not succeed."
        ),
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum verifier iterations before the outcome fails.",
    )
    p.add_argument(
        "--allowed-paths",
        default="",
        help=(
            "Comma-separated scope paths. The CLI outcome loop enforces this "
            "post-hoc via git: a check fails if files change outside the scope."
        ),
    )
    p.add_argument(
        "--frozen-paths",
        default="",
        help=(
            "Comma-separated paths the loop must never touch (e.g. tests/). "
            "Enforced in every mode; a change under a frozen prefix stops the loop."
        ),
    )
    p.add_argument(
        "--supersede",
        default=None,
        metavar="REASON",
        help=(
            "Retire the currently active outcome into this one, recording the "
            "reason and lineage. Without it, a second start is refused."
        ),
    )
    p.add_argument(
        "--agent",
        default="",
        help=(
            "Command that attempts the work each iteration (an agent CLI or a "
            "script). When set, outcome run drives the loop autonomously. The "
            "command may print MYTHIFY_COST=<n> to report its cost."
        ),
    )
    p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help=(
            "Cost ceiling for the loop. Each iteration costs what the agent "
            "reports via MYTHIFY_COST, else one unit; the loop fails when the "
            "cumulative cost reaches this ceiling."
        ),
    )
    p.add_argument(
        "--escalate-after",
        type=int,
        default=None,
        help="Stop and hand back to a human after N consecutive failed verifications.",
    )
    p.add_argument("--name", help="Outcome name; defaults to a slug of the goal.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_start)

    p = outcome_sub.add_parser(
        "run",
        help="Drive a self-driving outcome loop: fire the agent, verify, repeat.",
        description=(
            "Autonomously run an outcome started with --agent: each iteration "
            "runs the agent command, then the verifier, records evidence, and "
            "repeats until the outcome is met, the iteration or cost budget is "
            "spent, the scope is violated, or the escalation threshold of "
            "consecutive failures is reached. Bounded and evidence-gated. "
            "Exits 0 on success, 2 otherwise. CLI-only."
        ),
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--notes", default="", help="Notes stamped on each iteration.")
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_VERIFY_TIMEOUT,
        metavar="N",
        help="Timeout in seconds for the agent and each verifier command.",
    )
    p.set_defaults(handler=cmd_outcome_run)

    p = outcome_sub.add_parser(
        "check",
        help="Run the active outcome verifier and update loop state.",
        description=(
            "Run the verifier and optional metric for the active or named outcome. "
            "Exits 0 when the outcome is verified, 2 when it is not yet met or failed."
        ),
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--notes", default="", help="Notes for this iteration.")
    p.add_argument(
        "--audit",
        action="store_true",
        help=(
            "Re-run a finished outcome's verifier without mutating its "
            "history; a red run marks the outcome's evidence stale."
        ),
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_VERIFY_TIMEOUT,
        metavar="N",
        help="Timeout in seconds for each command.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_check)

    p = outcome_sub.add_parser(
        "status",
        help="Show the active or named outcome loop, or list all outcomes when none is active.",
        description=(
            "Show outcome status, iteration budget, verifier, and next action. "
            "With no name and no active outcome, list every outcome loop with "
            "its status, iteration budget, and last check."
        ),
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_status)

    p = outcome_sub.add_parser(
        "results",
        help="Show outcome loop iterations and final state.",
        description="Show all recorded verifier iterations for the active or named outcome.",
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_results)

    p = outcome_sub.add_parser(
        "stop",
        help="Stop the active or named outcome loop.",
        description="Mark an outcome stopped and clear the active pointer when it matches.",
    )
    p.add_argument("name", nargs="?", help="Outcome name; defaults to the active outcome.")
    p.add_argument("--reason", required=True, help="Why the loop is being stopped.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_outcome_stop)

    plan = sub.add_parser(
        "plan",
        help="Manage plans: create, import, add-step, list, show, switch, archive.",
        description="Manage plans: create, import, add-step, list, show, switch, archive.",
    )
    plan_sub = plan.add_subparsers(dest="plan_command", metavar="ACTION", required=True)

    p = plan_sub.add_parser(
        "create",
        help="Create a plan and set it active.",
        description=(
            "Create a plan and set it active. Without --steps the plan starts "
            "empty; add steps with plan add-step."
        ),
    )
    p.add_argument("goal", help="What the plan should accomplish.")
    p.add_argument(
        "--steps",
        help=(
            "JSON array of step objects: "
            "[{\"title\": str, \"success_criteria\": str (optional), "
            "\"verify_command\": str (optional)}]. A step's verify_command is "
            "the executable proof of its done-condition; run it with plan verify."
        ),
    )
    p.add_argument("--name", help="Plan name; defaults to a slug of the goal.")
    p.add_argument("--parent", action="append", default=[], help="Parent artifact kind:id. Repeat as needed.")
    p.set_defaults(handler=cmd_plan_create)

    p = plan_sub.add_parser(
        "import",
        help="Import godplans PLAN.mdx or godaudits AUDIT.mdx tasks as a plan.",
        description=(
            "Convert godplans or godaudits checkbox tasks into a Mythify plan. "
            "Each step keeps the task's exact Verify command, and completion "
            "requires that verification to pass while the step is in progress. "
            "Mythify never edits the artifact: checkbox flips stay with the "
            "executing agent per the artifact's embedded rules."
        ),
    )
    p.add_argument(
        "path",
        nargs="?",
        help=(
            "Artifact path; defaults to discovering .godplans/PLAN.mdx or "
            ".godaudits/AUDIT.mdx (with .md fallbacks) at the project root."
        ),
    )
    p.add_argument(
        "--source",
        choices=("godplans", "godaudits"),
        help="Artifact kind when the path does not make it obvious.",
    )
    p.add_argument(
        "--name",
        help="Plan name; defaults to the artifact name plus the source kind.",
    )
    p.set_defaults(handler=cmd_plan_import)

    p = plan_sub.add_parser(
        "add-step",
        help="Append a step to the named or active plan.",
        description="Append a step (id = max + 1) to the named or active plan.",
    )
    p.add_argument("title", help="Step title.")
    p.add_argument("--criteria", help="Success criteria for the step.")
    p.add_argument(
        "--verify",
        help="Executable command that proves the step is done; run it with plan verify.",
    )
    p.add_argument("--plan", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_plan_add_step)

    p = plan_sub.add_parser(
        "verify",
        help="Run a step's own verify command and record the evidence scoped to it.",
        description=(
            "Execute the step's verify_command, mark the step in progress, and "
            "record the executed verification against that step. On success the "
            "strict-evidence gate is satisfied, so step ID completed will pass. "
            "Exits 0 when verified, 2 when the command fails, 1 on usage errors."
        ),
    )
    p.add_argument("id", help="Step id (1-based integer).")
    p.add_argument("--plan", help="Plan name; defaults to the active plan.")
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_VERIFY_TIMEOUT,
        help="Timeout in seconds for the verify command.",
    )
    p.set_defaults(handler=cmd_plan_verify)

    p = plan_sub.add_parser(
        "list",
        help="List plans with the active marker, per-plan progress, and the archived count.",
        description="List plans with the active marker, per-plan progress, and the archived count.",
    )
    p.set_defaults(handler=cmd_plan_list)

    p = plan_sub.add_parser(
        "show",
        help="Show full detail of the named or active plan.",
        description="Show full detail of the named or active plan. Exits 1 if not found.",
    )
    p.add_argument("name", nargs="?", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_plan_show)

    p = plan_sub.add_parser(
        "switch",
        help="Set the active plan pointer.",
        description="Set the active plan pointer. Exits 1 if the plan is not found.",
    )
    p.add_argument("name", help="Plan name.")
    p.set_defaults(handler=cmd_plan_switch)

    p = plan_sub.add_parser(
        "archive",
        help="Move a plan file to plans/archive/ and clear the active pointer if needed.",
        description=(
            "Move the named or active plan file to plans/archive/, clearing the "
            "active pointer if it pointed there. On filename conflict in the "
            "archive, a timestamp is appended."
        ),
    )
    p.add_argument("name", nargs="?", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_plan_archive)

    p = sub.add_parser(
        "step",
        help="Update a step's status; completed requires RESULT plus passing verify run.",
        description=(
            "Update step ID to STATUS (pending, in_progress, completed, failed, "
            "skipped). completed and failed require the RESULT argument: evidence "
            "or a failure description. By default, completed also requires a "
            "passing verify run since the step started. Set "
            "MYTHIFY_REQUIRE_VERIFIED_STEP=0 only for legacy prose-only "
            "completion. Prints the next pending step afterward."
        ),
    )
    p.add_argument("id", help="Step id (1-based integer).")
    p.add_argument("status", help="One of: pending, in_progress, completed, failed, skipped.")
    p.add_argument(
        "result",
        nargs="?",
        help="Evidence or failure description; required for completed and failed.",
    )
    p.add_argument("--plan", help="Plan name; defaults to the active plan.")
    p.set_defaults(handler=cmd_step)

    memory = sub.add_parser(
        "memory",
        help="Persistent key-value memory: set, get, clear.",
        description="Persistent key-value memory: set, get, clear.",
    )
    memory_sub = memory.add_subparsers(dest="memory_command", metavar="ACTION", required=True)

    p = memory_sub.add_parser(
        "set",
        help="Store a memory entry; an existing key is overwritten.",
        description="Store a memory entry; an existing key is overwritten.",
    )
    p.add_argument("key", help="Entry key (unique).")
    p.add_argument("value", help="Entry value.")
    p.add_argument(
        "--category",
        choices=MEMORY_CATEGORIES,
        default=MEMORY_DEFAULT_CATEGORY,
        help="Entry category (default: {0}).".format(MEMORY_DEFAULT_CATEGORY),
    )
    p.set_defaults(handler=cmd_memory_set)

    p = memory_sub.add_parser(
        "get",
        help="Search memory: case-insensitive substring match over keys and values.",
        description=(
            "Search memory with a case-insensitive substring match over keys and "
            "values; --category narrows by category. Without QUERY, list all entries."
        ),
    )
    p.add_argument("query", nargs="?", help="Substring to match against keys and values.")
    p.add_argument("--category", choices=MEMORY_CATEGORIES, help="Filter by category.")
    p.set_defaults(handler=cmd_memory_get)

    p = memory_sub.add_parser(
        "clear",
        help="Remove one entry by KEY, or everything with --all.",
        description=(
            "Remove one entry by KEY, or everything with --all. With neither, "
            "refuse and exit 1: clearing requires an explicit target."
        ),
    )
    p.add_argument("key", nargs="?", help="Key of the entry to remove.")
    p.add_argument(
        "--all",
        dest="clear_all",
        action="store_true",
        help="Clear every memory entry.",
    )
    p.set_defaults(handler=cmd_memory_clear)

    lesson = sub.add_parser(
        "lesson",
        help="Record and list lessons (project store, or global with --global).",
        description="Record and list lessons (project store, or global with --global).",
    )
    lesson_sub = lesson.add_subparsers(dest="lesson_command", metavar="ACTION", required=True)

    p = lesson_sub.add_parser(
        "add",
        help="Record a lesson in the project store, or the global store with --global.",
        description="Record a lesson in the project store, or the global store with --global.",
    )
    p.add_argument("title", help="Lesson title.")
    p.add_argument("detail", help="Lesson detail.")
    p.add_argument("--tags", help="Comma-separated tags, for example: a,b.")
    p.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Store in the global lessons store (~/.mythify/lessons).",
    )
    p.set_defaults(handler=cmd_lesson_add)

    p = lesson_sub.add_parser(
        "list",
        help="List lessons labeled (project) or (global); filter with --tag and --scope.",
        description="List lessons labeled (project) or (global); filter with --tag and --scope.",
    )
    p.add_argument("--tag", help="Only lessons carrying this tag.")
    p.add_argument(
        "--scope",
        choices=("project", "global", "all"),
        default="all",
        help="Which store to list (default: all).",
    )
    p.set_defaults(handler=cmd_lesson_list)

    logs = sub.add_parser(
        "logs",
        help="Maintain Mythify jsonl logs.",
        description="Maintain Mythify jsonl logs without treating maintenance as verification.",
    )
    logs_sub = logs.add_subparsers(dest="logs_command", metavar="ACTION", required=True)

    p = logs_sub.add_parser(
        "compact",
        help="Archive and trim top-level verification and reflection logs.",
        description=(
            "Archive raw top-level verification and reflection logs, then keep "
            "only the most recent valid records in the active files."
        ),
    )
    p.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_LOG_COMPACT_KEEP,
        help="Number of recent valid records to keep per active log.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be compacted without writing files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_logs_compact)

    add_verification_parsers(sub, symbols)

    p = sub.add_parser(
        "summary",
        help="Full session report: plans, outcomes, maps, products, memory, lessons, evidence, reflections.",
        description=(
            "Full session report: plans and progress, outcome loops, decision "
            "maps, product records, memory count, project and global lesson counts, verification "
            "stats (executed passed, executed failed, attested count), and "
            "reflection count."
        ),
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_summary)

    p = sub.add_parser(
        "mcp",
        help="Serve Mythify commands as MCP tools over stdio.",
        description=(
            "Run the zero-dependency MCP stdio server. Each tool call runs one "
            "Mythify command in the project root and returns its output and "
            "exit code. Register bin/mythify-mcp, or this command, with an MCP "
            "client. The mcp command itself is not callable as a tool."
        ),
    )
    p.set_defaults(handler=lambda _args, _state: serve_mcp(parser, VERSION), needs_state=False)

    return parser
