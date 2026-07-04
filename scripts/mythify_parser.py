"""Argument parser construction for the Mythify CLI."""

from __future__ import annotations

import argparse


def build_parser(symbols):
    globals().update(symbols)
    parser = argparse.ArgumentParser(
        prog="mythify.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Mythify v{0}: evidence protocol for AI coding agents. Route broad ".format(VERSION) +
            "work first, keep state in .mythify, and verify completion claims "
            "with executed commands."
        ),
        epilog=(
            "Recommended front door:\n"
            "  mythify route \"TASK\"        choose direct, plan, research, review, outcome, campaign, failure, handoff, or prompt routing\n"
            "  mythify report ...          show chat-ready progress and issue reports\n"
            "  mythify verify run ...      record executed proof before a completion claim\n"
            "  mythify status              reorient from durable state\n"
            "\n"
            "Workflow primitives:\n"
            "  plan, outcome, campaign, research, prompt\n"
            "\n"
            "Advanced surfaces:\n"
            "  dashboard, harness, history, background, progress, readiness, timeline, phase, trace,\n"
            "  classify, memory, lesson, logs, reflect, summary, protocol, fanout through MCP\n"
            "\n"
            "Labs surfaces:\n"
            "  host-model, provider probes, local model runs, host CLI workers,\n"
            "  execution probes/runs, lifecycle probes\n"
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
            "hash. With no paths, check source protocol when present and local "
            "CLAUDE.md, AGENTS.md, and .cursorrules files."
        ),
    )
    p.add_argument("paths", nargs="*", help="Protocol copy files to check.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_protocol_check, needs_state=False)

    p = sub.add_parser(
        "status",
        help="Show the active plan with step icons, the next pending step, and state counts.",
        description=(
            "Orientation: active plan with step icons, next pending step and its "
            "criteria, and one-line counts for memory, lessons, verifications, "
            "and reflections."
        ),
    )
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser(
        "dashboard",
        help="Show a read-only workflow dashboard with plan, outcome, and evidence state.",
        description=(
            "Read-only workflow dashboard: active plan, current and next step, "
            "active outcome, memory and lesson counts, verification totals, "
            "recent verification records, and recent reflections."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=3,
        help="Number of recent verification and reflection records to show. Defaults to 3.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_dashboard)

    p = sub.add_parser(
        "harness",
        help="Show a read-only evidence harness for autonomous agent work.",
        description=(
            "Read-only evidence harness: active steering state, evidence mix, "
            "attention items, delegated work counts, release readiness, and the "
            "next control action from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="Number of recent verification and reflection records to inspect. Defaults to 5.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_harness)

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
            "state, and choose direct, plan, research, review, outcome, campaign, "
            "failure recovery, handoff, or prompt packet routing."
        ),
    )
    p.add_argument("task", help="Task request or problem statement to route.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.add_argument(
        "--triage",
        choices=TRIAGE_MODES,
        default="never",
        help=(
            "Run a fast model triage pass: never (default), auto when the gate "
            "is recommended or required, or always."
        ),
    )
    p.add_argument(
        "--triage-engine",
        choices=TRIAGE_ENGINES,
        default="",
        help=(
            "Fast triage engine. Defaults to MYTHIFY_TRIAGE_ENGINE, then "
            "codex-cli when available, then local auto-detection."
        ),
    )
    p.add_argument(
        "--triage-model",
        default="",
        help="Fast triage model. Defaults to MYTHIFY_TRIAGE_MODEL or the engine default.",
    )
    p.add_argument(
        "--triage-timeout",
        type=float,
        default=120.0,
        help="Fast triage timeout in seconds.",
    )
    p.add_argument(
        "--platform",
        choices=PLATFORMS,
        default="auto",
        help="Host platform for model policy. Defaults to auto-detection.",
    )
    p.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default="auto",
        help="Overall effort preference for spawned model roles.",
    )
    p.add_argument(
        "--speed",
        choices=SPEED_LEVELS,
        default="auto",
        help="Overall speed preference for spawned model roles.",
    )
    p.add_argument(
        "--session-model",
        default="",
        help="Current host session model for spawn ceiling policy.",
    )
    p.add_argument(
        "--spawn-ceiling",
        choices=SPAWN_CEILINGS,
        default="auto",
        help="Maximum spawned model tier relative to the session model.",
    )
    p.add_argument(
        "--reviewer-strength",
        choices=REVIEWER_STRENGTH_MODES,
        default="auto",
        help="Reviewer model strength relative to the session.",
    )
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
        "research",
        help="Render a research to implementation prompt packet.",
    )
    p.add_argument("name", nargs="?", help="Research name. Defaults to the active research.")
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="research")

    p = prompt_sub.add_parser(
        "analysis",
        help="Render an analysis to plan prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="analysis")

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
        "campaign",
        help="Render a campaign prompt packet through the common packet contract.",
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to the active campaign.")
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="campaign")

    p = prompt_sub.add_parser(
        "next",
        help="Select and render the next useful prompt packet.",
    )
    add_prompt_common(p)
    p.set_defaults(handler=cmd_prompt_packet, packet_kind="next")

    p = sub.add_parser(
        "background",
        help="Show read-only background task state for outcomes and fanout jobs.",
        description=(
            "Read-only background task view: outcome loops, fanout jobs, task "
            "counts, current statuses, and next actions from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="Number of recent outcomes and fanout jobs to show. Defaults to 5.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_background)

    p = sub.add_parser(
        "progress",
        help="Show read-only outcome loop progress.",
        description=(
            "Read-only outcome loop progress: active and recent outcomes, "
            "iteration budget, verifier exit details, metric score when present, "
            "and next action from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="Number of recent outcomes to show. Defaults to 5.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_progress)

    p = sub.add_parser(
        "readiness",
        help="Show read-only release readiness from recorded gates.",
        description=(
            "Read-only release readiness: recorded verification gates, project "
            "git state, roadmap state, and release-review status without "
            "rerunning gates or declaring the release safe."
        ),
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_readiness)

    p = sub.add_parser(
        "timeline",
        help="Show a read-only fanout worker timeline.",
        description=(
            "Read-only fanout worker timeline: recent fanout jobs, task start "
            "and finish events, duration, status, errors, and output metadata "
            "from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="Number of recent fanout jobs to include. Defaults to 5.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_timeline)

    p = sub.add_parser(
        "phase",
        help="Show a read-only Understand, Design, Build, Judge, Verify phase view.",
        description=(
            "Read-only phase view: active plan steps grouped into Understand, "
            "Design, Build, Judge, and Verify, with supporting evidence counts "
            "from durable state."
        ),
    )
    p.add_argument(
        "--recent",
        type=int,
        default=3,
        help="Number of recent verification and reflection records to consider. Defaults to 3.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_phase)

    trace = sub.add_parser(
        "trace",
        help="Analyze exported agent traces and scenario rows.",
        description=(
            "Analyze exported Fable-style session traces, model action rows, "
            "and scenario prompt rows. Trace analysis is material for planning "
            "and eval design, not verification evidence."
        ),
    )
    trace_sub = trace.add_subparsers(dest="trace_command", metavar="ACTION", required=True)
    p = trace_sub.add_parser(
        "analyze",
        help="Summarize local JSON or JSONL trace exports.",
        description=(
            "Read JSONL or JSON files, detect trace shape, count tools and "
            "sessions, surface verification-like command signals, and suggest "
            "Mythify product or eval improvements."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files, directories, or - for JSONL stdin.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum records to read across inputs. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_analyze, needs_state=False)

    p = trace_sub.add_parser(
        "distill",
        help="Distill a model slice into a Markdown behavior profile.",
        description=(
            "Filter local trace exports by model, calculate visible workflow "
            "metrics, and write a trace-derived behavior profile."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files, directories, or - for JSONL stdin.",
    )
    p.add_argument("--model", help="Exact model name to filter, such as claude-fable-5.")
    p.add_argument("--title", help="Markdown title. Defaults to a trace-derived behavior profile title.")
    p.add_argument("--output", help="Write Markdown to this path instead of printing it.")
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum matching records to read across inputs. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_distill, needs_state=False)

    p = trace_sub.add_parser(
        "compare",
        help="Compare target and baseline model trace slices.",
        description=(
            "Compare visible workflow metrics between a target model slice "
            "and a baseline model slice, then render a delta playbook."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files or directories. Stdin is not supported for compare.",
    )
    p.add_argument("--target", required=True, help="Exact target model name to filter.")
    p.add_argument("--baseline", required=True, help="Exact baseline model name to filter.")
    p.add_argument("--target-label", help="Human label for the target slice.")
    p.add_argument("--baseline-label", help="Human label for the baseline slice.")
    p.add_argument("--output", help="Write Markdown to this path instead of printing it.")
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum matching records to read for each slice. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_compare, needs_state=False)

    p = trace_sub.add_parser(
        "playbook",
        help="Generate a concise agent playbook from trace behavior.",
        description=(
            "Build a session-start playbook from a target trace slice, "
            "optionally comparing it with a baseline model slice."
        ),
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="JSON or JSONL files, directories, or - for JSONL stdin. Stdin cannot be combined with --baseline.",
    )
    p.add_argument("--target", required=True, help="Exact target model name to filter.")
    p.add_argument("--baseline", help="Exact baseline model name to filter.")
    p.add_argument("--target-label", help="Human label for the target slice.")
    p.add_argument("--baseline-label", help="Human label for the baseline slice.")
    p.add_argument("--title", help="Markdown title. Defaults to a trace-derived agent playbook title.")
    p.add_argument("--output", help="Write Markdown to this path instead of printing it.")
    p.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum matching records to read for each slice. Use 0 for no limit.",
    )
    p.add_argument(
        "--recursive",
        action="store_true",
        help="Scan directories recursively for .json and .jsonl files.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_trace_playbook, needs_state=False)

    p = trace_sub.add_parser(
        "install-playbook",
        help="Install a generated playbook as a local Code or Codex skill.",
        description=(
            "Wrap a generated Markdown playbook in SKILL.md frontmatter and "
            "install it under a local skill root."
        ),
    )
    p.add_argument("playbook", help="Markdown playbook file to install.")
    p.add_argument("--skill", help="Skill directory name. Defaults to the playbook filename.")
    p.add_argument(
        "--skill-root",
        default="~/.codex/skills",
        help="Local skill root. Defaults to ~/.codex/skills.",
    )
    p.add_argument("--force", action="store_true", help="Overwrite an existing skill directory.")
    p.set_defaults(handler=cmd_trace_install_playbook, needs_state=False)

    research = sub.add_parser(
        "research",
        help="Manage source-backed research records.",
        description=(
            "Manage source-backed research: start a question, add sources, add "
            "claims, track open questions, and close with a decision. Research "
            "records are material for decisions, not executed verification."
        ),
    )
    research_sub = research.add_subparsers(dest="research_command", metavar="ACTION", required=True)

    p = research_sub.add_parser(
        "start",
        help="Start a research record and set it active.",
        description="Start a source-backed research record and set it active.",
    )
    p.add_argument("question", help="Research question or decision to investigate.")
    p.add_argument("--name", help="Research record name. Defaults to a slug of the question.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_research_start)

    p = research_sub.add_parser(
        "list",
        help="List research records.",
        description="List research records with active marker, counts, and status.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_research_list)

    p = research_sub.add_parser(
        "add-source",
        help="Add a source to the active or named research record.",
        description="Add a source with URL, note, and credibility to a research record.",
    )
    p.add_argument("title", help="Source title.")
    p.add_argument("--url", default="", help="Source URL or local path.")
    p.add_argument("--note", default="", help="Short note about why this source matters.")
    p.add_argument(
        "--credibility",
        choices=RESEARCH_SOURCE_CREDIBILITY,
        default="unknown",
        help="Source credibility marker. Defaults to unknown.",
    )
    p.add_argument("--research", help="Research record name. Defaults to active.")
    p.set_defaults(handler=cmd_research_add_source)

    p = research_sub.add_parser(
        "add-claim",
        help="Add a claim and its evidence to a research record.",
        description="Add a claim, evidence note, optional source id, and confidence marker.",
    )
    p.add_argument("claim", help="Claim learned from the research.")
    p.add_argument("--evidence", required=True, help="Evidence supporting the claim.")
    p.add_argument("--source", help="Source id such as S1.")
    p.add_argument(
        "--confidence",
        choices=RESEARCH_CONFIDENCE,
        default="medium",
        help="Confidence marker. Defaults to medium.",
    )
    p.add_argument("--research", help="Research record name. Defaults to active.")
    p.set_defaults(handler=cmd_research_add_claim)

    p = research_sub.add_parser(
        "add-question",
        help="Add an open question to a research record.",
        description="Add an unresolved question that future research should answer.",
    )
    p.add_argument("question", help="Open question.")
    p.add_argument("--research", help="Research record name. Defaults to active.")
    p.set_defaults(handler=cmd_research_add_question)

    p = research_sub.add_parser(
        "summary",
        help="Show the active or named research record.",
        description="Show sources, claims, open questions, and decision for a research record.",
    )
    p.add_argument("name", nargs="?", help="Research record name. Defaults to active.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_research_summary)

    p = research_sub.add_parser(
        "close",
        help="Close a research record with a decision.",
        description="Mark a research record closed and record the resulting decision.",
    )
    p.add_argument("name", nargs="?", help="Research record name. Defaults to active.")
    p.add_argument("--decision", required=True, help="Decision or conclusion from the research.")
    p.set_defaults(handler=cmd_research_close)

    campaign = sub.add_parser(
        "campaign",
        help="Manage long-running task campaigns.",
        description=(
            "Manage a long-running campaign: decompose a goal into tasks, move "
            "each task through understand, design, build, judge, verify, and "
            "reflect, and record learnings that improve later tasks."
        ),
    )
    campaign_sub = campaign.add_subparsers(dest="campaign_command", metavar="ACTION", required=True)

    p = campaign_sub.add_parser(
        "start",
        help="Start a campaign and set it active.",
        description=(
            "Start a long-running campaign. If --tasks is omitted, Mythify "
            "generates a small default task list for the goal."
        ),
    )
    p.add_argument("goal", help="Campaign end goal.")
    p.add_argument(
        "--tasks",
        help=(
            "JSON array of task strings or objects with title and optional "
            "success_criteria."
        ),
    )
    p.add_argument("--name", help="Campaign name. Defaults to a slug of the goal.")
    p.add_argument("--success", help="Overall success criteria.")
    p.add_argument("--verify", help="Optional campaign-level verifier command.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_start)

    p = campaign_sub.add_parser(
        "list",
        help="List campaigns.",
        description="List campaigns with active marker, progress, and status.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_list)

    p = campaign_sub.add_parser(
        "status",
        help="Show the active or named campaign.",
        description="Show campaign progress, tasks, current phase, and recent learnings.",
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_status)

    p = campaign_sub.add_parser(
        "prompt",
        help="Render the next host prompt for a campaign.",
        description=(
            "Render a chat-ready prompt for the active or named campaign's "
            "current task and phase without mutating campaign state."
        ),
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_prompt)

    p = campaign_sub.add_parser(
        "watch",
        help="Poll a campaign and emit refreshed host prompts.",
        description=(
            "Poll the active or named campaign and emit the current host prompt. "
            "Defaults to one iteration; pass --max-iterations 0 for an explicit "
            "long-running watch managed by the host."
        ),
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--interval", type=float, default=5.0, help="Seconds between prompt refreshes.")
    p.add_argument(
        "--max-iterations",
        type=int,
        default=1,
        help="Prompt refresh count. Use 0 for an explicit unbounded watch.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_campaign_watch)

    p = campaign_sub.add_parser(
        "add-task",
        help="Append a task to the active or named campaign.",
        description="Append a task to a campaign with optional success criteria.",
    )
    p.add_argument("title", help="Task title.")
    p.add_argument("--criteria", help="Task success criteria.")
    p.add_argument("--campaign", help="Campaign name. Defaults to active.")
    p.set_defaults(handler=cmd_campaign_add_task)

    p = campaign_sub.add_parser(
        "advance",
        help="Advance the current task to the next campaign phase.",
        description=(
            "Advance the current task through understand, design, build, judge, "
            "verify, and reflect. Advancing from reflect completes the task and "
            "starts the next pending task."
        ),
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--result", required=True, help="Evidence, observation, or output from this phase.")
    p.set_defaults(handler=cmd_campaign_advance)

    p = campaign_sub.add_parser(
        "task",
        help="Set a campaign task status directly.",
        description="Set a task status directly. completed and failed require RESULT evidence.",
    )
    p.add_argument("id", help="Task id.")
    p.add_argument("status", help="One of: pending, in_progress, completed, failed, skipped.")
    p.add_argument("result", nargs="?", help="Evidence or failure description.")
    p.add_argument("--campaign", help="Campaign name. Defaults to active.")
    p.set_defaults(handler=cmd_campaign_task)

    p = campaign_sub.add_parser(
        "learn",
        help="Record a learning for the campaign.",
        description="Record a learning that should improve the current or next task cycle.",
    )
    p.add_argument("lesson", help="Learning to carry forward.")
    p.add_argument("--task", help="Task id. Defaults to the current task when present.")
    p.add_argument("--apply-next", action="store_true", help="Mark the learning as guidance for next tasks.")
    p.add_argument("--campaign", help="Campaign name. Defaults to active.")
    p.set_defaults(handler=cmd_campaign_learn)

    p = campaign_sub.add_parser(
        "stop",
        help="Stop the active or named campaign.",
        description="Mark a campaign stopped and clear the active pointer when it matches.",
    )
    p.add_argument("name", nargs="?", help="Campaign name. Defaults to active.")
    p.add_argument("--reason", required=True, help="Why the campaign is being stopped.")
    p.set_defaults(handler=cmd_campaign_stop)

    p = sub.add_parser(
        "classify",
        help="Classify a task and recommend ceremony, verification, and fanout.",
        description=(
            "Classify TASK before planning. Returns task type, risk, ceremony "
            "level, verification strategy, model triage fit, and whether fanout is useful. This "
            "command does not require an initialized .mythify workspace."
        ),
    )
    p.add_argument("task", help="Task request or problem statement to classify.")
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    p.add_argument(
        "--triage",
        choices=TRIAGE_MODES,
        default="never",
        help=(
            "Run a fast model triage pass: never (default), auto when the gate "
            "is recommended or required, or always."
        ),
    )
    p.add_argument(
        "--triage-engine",
        choices=TRIAGE_ENGINES,
        default="",
        help=(
            "Fast triage engine. Defaults to MYTHIFY_TRIAGE_ENGINE, then "
            "codex-cli when available, then local auto-detection: "
            "claude-cli, cursor-agent, command."
        ),
    )
    p.add_argument(
        "--triage-model",
        default="",
        help="Fast triage model. Defaults to MYTHIFY_TRIAGE_MODEL or the engine default.",
    )
    p.add_argument(
        "--triage-timeout",
        type=float,
        default=120.0,
        help="Fast triage timeout in seconds.",
    )
    p.add_argument(
        "--platform",
        choices=PLATFORMS,
        default="auto",
        help=(
            "Host platform for model policy. Defaults to auto-detection; use "
            "codex-desktop, claude-desktop, or cursor-desktop when the host is known."
        ),
    )
    p.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default="auto",
        help=(
            "Overall effort preference for spawned model roles. Auto keeps "
            "triage cheap and scales worker or reviewer effort by risk."
        ),
    )
    p.add_argument(
        "--speed",
        choices=SPEED_LEVELS,
        default="auto",
        help=(
            "Overall speed preference for spawned model roles. Auto preserves "
            "host defaults; fast enables Codex fast mode where supported."
        ),
    )
    p.add_argument(
        "--session-model",
        default="",
        help=(
            "Current host session model for spawn ceiling policy. Defaults to "
            "MYTHIFY_SESSION_MODEL when set."
        ),
    )
    p.add_argument(
        "--spawn-ceiling",
        choices=SPAWN_CEILINGS,
        default="auto",
        help=(
            "Maximum spawned model tier relative to the session model. Auto "
            "uses MYTHIFY_SPAWN_CEILING or same_or_lower."
        ),
    )
    p.add_argument(
        "--reviewer-strength",
        choices=REVIEWER_STRENGTH_MODES,
        default="auto",
        help=(
            "Reviewer model strength relative to the session. Auto uses "
            "MYTHIFY_REVIEWER_STRENGTH or same_or_lower; allow_stronger is "
            "an explicit reviewer-only opt-in."
        ),
    )
    p.set_defaults(handler=cmd_classify, needs_state=False)

    host_model = sub.add_parser(
        "host-model",
        help="Record or inspect the intended host chat model.",
        description=(
            "Record a requested host chat model switch. Mythify uses the recorded "
            "target as the default session model for model policy and spawn ceiling "
            "checks, while the actual current chat model remains controlled by the host."
        ),
    )
    host_model_sub = host_model.add_subparsers(dest="host_model_command", metavar="ACTION", required=True)

    p = host_model_sub.add_parser(
        "switch",
        help="Record a requested host chat model switch.",
        description="Record a target host model and print host-specific switch guidance.",
    )
    p.add_argument("target_model", help="Target host model to record.")
    p.add_argument(
        "--platform",
        choices=PLATFORMS,
        default="auto",
        help="Host platform. Defaults to auto.",
    )
    p.add_argument(
        "--current-model",
        default="",
        help="Current host model when known, recorded for audit only.",
    )
    p.add_argument(
        "--thinking",
        choices=HOST_THINKING_LEVELS,
        default="auto",
        help="Requested host reasoning effort when the host supports it.",
    )
    p.add_argument(
        "--speed",
        choices=SPEED_LEVELS,
        default="auto",
        help="Requested host speed preference when the host supports it.",
    )
    p.add_argument("--reason", default="", help="Reason for the host switch.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_host_model_switch)

    p = host_model_sub.add_parser(
        "status",
        help="Show the recorded host model switch.",
        description="Show the recorded host model switch, if any.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_host_model_status)

    p = host_model_sub.add_parser(
        "clear",
        help="Clear the recorded host model switch.",
        description="Remove host-model.json from the Mythify state directory.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_host_model_clear)

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
    p.add_argument(
        "--visibility",
        choices=FANOUT_VISIBILITY_MODES,
        default="summary",
        help="How much loop progress the host should surface.",
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
        help="Show the active or named outcome loop.",
        description="Show outcome status, iteration budget, verifier, and next action.",
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
            "Create a plan and set it active. Without --steps the plan is empty "
            "unless --horizon or MYTHIFY_PLAN_HORIZON supplies a default step count."
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
    p.add_argument(
        "--horizon",
        help=(
            "Create N default lookahead steps when --steps is omitted. "
            "Accepted range: 1-20."
        ),
    )
    p.add_argument("--name", help="Plan name; defaults to a slug of the goal.")
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

    verify = sub.add_parser(
        "verify",
        help="Verification: run a command (executed) or record a claim (attested).",
        description="Verification: run a command (executed) or record a claim (attested).",
    )
    verify_sub = verify.add_subparsers(dest="verify_command", metavar="ACTION", required=True)

    p = verify_sub.add_parser(
        "run",
        help="Execute COMMAND through the shell and record an executed verification.",
        description=(
            "Execute COMMAND through the shell, capture exit code, duration, and "
            "output tails, append an executed verification record, and print the "
            "verdict. Exits 0 if verified (exit code 0), 2 if unverified."
        ),
    )
    p.add_argument("command", help="Shell command to execute.")
    p.add_argument("--claim", help="What this command verifies.")
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_VERIFY_TIMEOUT,
        metavar="N",
        help="Timeout in seconds (default: 300).",
    )
    p.set_defaults(handler=cmd_verify_run)

    p = verify_sub.add_parser(
        "claim",
        help="Record a self-reported (attested) verification; never counts as verified.",
        description=(
            "Append an attested verification record and print the [WARN] ATTESTED "
            "line. Attested entries are never marked verified."
        ),
    )
    p.add_argument("claim", help="The claim being attested.")
    p.add_argument("evidence", help="Why you believe the claim holds.")
    p.set_defaults(handler=cmd_verify_claim)

    p = sub.add_parser(
        "reflect",
        help="Record a structured reflection (JSON object or flags).",
        description=(
            "Record a structured reflection. Required keys: action, outcome "
            "(success, partial, failure), observation, next. A provided lesson is "
            "auto-recorded as a project lesson tagged auto-reflected. The JSON "
            "positional takes precedence over flags."
        ),
    )
    p.add_argument(
        "json",
        nargs="?",
        help=(
            "Reflection as a JSON object with keys action, outcome, observation, "
            "next, and optional root_cause and lesson."
        ),
    )
    p.add_argument("--action", help="What was attempted.")
    p.add_argument("--outcome", help="One of: success, partial, failure.")
    p.add_argument("--observation", help="What actually happened.")
    p.add_argument("--next", help="The next action to take.")
    p.add_argument("--root-cause", dest="root_cause", help="Root cause, if known.")
    p.add_argument("--lesson", help="Lesson to auto-record as a project lesson.")
    p.set_defaults(handler=cmd_reflect)

    p = sub.add_parser(
        "summary",
        help="Full session report: plans, memory, lessons, verification stats, reflections.",
        description=(
            "Full session report: plans and progress, memory count, project and "
            "global lesson counts, verification stats (executed passed, executed "
            "failed, attested count), and reflection count."
        ),
    )
    p.set_defaults(handler=cmd_summary)

    return parser
