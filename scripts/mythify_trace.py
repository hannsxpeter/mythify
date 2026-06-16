"""Trace analysis and playbook formatting helpers for Mythify."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TRACE_JSON_SUFFIXES = (".jsonl", ".json")
TRACE_TEXT_KEYS = (
    "prompt",
    "instruction",
    "input",
    "output",
    "completion",
    "context",
    "cot",
)
TRACE_VERIFY_PATTERNS = (
    ("test", re.compile(r"\b(npm test|pytest|unittest|vitest|playwright|cargo test|go test|pnpm test|bun test|tests?)\b", re.I)),
    ("build", re.compile(r"\b(npm run build|pnpm build|bun run build|tsc|typecheck|vite build|build)\b", re.I)),
    ("lint", re.compile(r"\b(lint|eslint|ruff|prettier|biome)\b", re.I)),
    ("server", re.compile(r"\b(localhost|npm start|npm run dev|vite|server|port|curl)\b", re.I)),
    ("browser", re.compile(r"\b(chrome|chromium|screenshot|playwright|browser|preview)\b", re.I)),
    ("git", re.compile(r"\b(git status|git diff|git add|git commit|git checkout|git branch|branch)\b", re.I)),
)
TRACE_ERROR_PATTERNS = (
    ("error", re.compile(r"\b(error|failed|failure|exception|traceback)\b", re.I)),
    ("limit", re.compile(r"\b(limit|rate limit|context|token)\b", re.I)),
    ("permission", re.compile(r"\b(permission|denied|approval|bypasspermissions)\b", re.I)),
)
TRACE_COMMAND_TOOLS = {
    "bash",
    "powershell",
    "shell",
    "terminal",
    "runcommand",
}
TRACE_READ_TOOL_KEYWORDS = (
    "read",
    "grep",
    "glob",
    "list",
    "ls",
    "search",
)
TRACE_EDIT_TOOL_KEYWORDS = (
    "edit",
    "write",
    "patch",
    "replace",
    "notebookedit",
)
TRACE_SHELL_TOOL_KEYWORDS = (
    "bash",
    "shell",
    "terminal",
    "powershell",
    "runcommand",
)


def counter_top(counter, limit=10):
    return [{"name": key, "count": value} for key, value in counter.most_common(limit)]


def detect_trace_row_format(row):
    if not isinstance(row, dict):
        return "unknown"
    if "trace" in row or "messages" in row or "num_tool_calls" in row:
        return "session_trace"
    if "output_type" in row or (
        "completion" in row and "context" in row and isinstance(row.get("output"), dict)
    ):
        return "action_row"
    if "instruction" in row and "prompt" in row and "output" in row:
        return "scenario_row"
    return "unknown"


def trace_session_id(row):
    metadata = row.get("metadata") if isinstance(row, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    value = row.get("session") or row.get("session_id") or metadata.get("session_id")
    return str(value) if value else ""


def trace_model(row):
    metadata = row.get("metadata") if isinstance(row, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    value = row.get("model") or metadata.get("model")
    return str(value) if value else ""


def trace_model_matches(row, model_filter):
    if not model_filter:
        return True
    actual = trace_model(row).strip().lower()
    expected = str(model_filter).strip().lower()
    return bool(actual and actual == expected)


def trace_metadata_value(row, key):
    metadata = row.get("metadata") if isinstance(row, dict) else None
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get(key)
    return str(value) if value else ""


def collect_trace_text(value, out, depth=0):
    if depth > 5:
        return
    if isinstance(value, str):
        if value:
            out.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            collect_trace_text(item, out, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            collect_trace_text(item, out, depth + 1)


def record_trace_command(tool, input_value, commands):
    if not isinstance(input_value, dict):
        return
    tool_name = str(tool or "").lower()
    if tool_name not in TRACE_COMMAND_TOOLS:
        return
    command = input_value.get("command") or input_value.get("script")
    if isinstance(command, str) and command.strip():
        commands.append(command)


def record_trace_tool(tool_counts, commands, tool, input_value=None):
    if not tool:
        return
    name = str(tool)
    tool_counts[name] += 1
    record_trace_command(name, input_value, commands)


def collect_trace_tools_from_content(content, tool_counts, commands):
    if isinstance(content, dict):
        if content.get("type") in ("tool_use", "server_tool_use"):
            record_trace_tool(
                tool_counts,
                commands,
                content.get("name") or content.get("tool_name") or "<unknown>",
                content.get("input"),
            )
        return
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in ("tool_use", "server_tool_use"):
            record_trace_tool(
                tool_counts,
                commands,
                item.get("name") or item.get("tool_name") or "<unknown>",
                item.get("input"),
            )


def collect_trace_row_signals(row, summary):
    row_format = detect_trace_row_format(row)
    summary["format_counts"][row_format] += 1
    command_start = len(summary["commands"])
    session = trace_session_id(row)
    if session:
        summary["sessions"][session]["records"] += 1
    model = trace_model(row)
    if model:
        summary["models"][model] += 1
    harness = row.get("harness") if isinstance(row, dict) else None
    if harness:
        summary["harnesses"][str(harness)] += 1
    output_type = row.get("output_type") if isinstance(row, dict) else None
    if output_type:
        summary["output_types"][str(output_type)] += 1
    for key, counter_name in (
        ("entrypoint", "entrypoints"),
        ("permission_mode", "permission_modes"),
        ("mode", "modes"),
    ):
        value = trace_metadata_value(row, key)
        if value:
            summary[counter_name][value] += 1

    try:
        tool_count = int(row.get("num_tool_calls") or 0)
    except (TypeError, ValueError):
        tool_count = 0
    if session and tool_count:
        summary["sessions"][session]["tool_score"] += tool_count
    if tool_count:
        summary["tool_call_values"].append(tool_count)

    text_blobs = []
    for key in TRACE_TEXT_KEYS:
        if key in row:
            collect_trace_text(row.get(key), text_blobs)

    output = row.get("output") if isinstance(row, dict) else None
    if isinstance(output, dict):
        record_trace_tool(
            summary["tools"],
            summary["commands"],
            output.get("tool"),
            output.get("input"),
        )

    for message in row.get("messages") or []:
        if not isinstance(message, dict):
            continue
        collect_trace_tools_from_content(message.get("content"), summary["tools"], summary["commands"])
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                function = {}
            record_trace_tool(
                summary["tools"],
                summary["commands"],
                function.get("name") or call.get("name") or "<unknown>",
                function.get("arguments") if isinstance(function.get("arguments"), dict) else None,
            )
        collect_trace_text(message.get("content"), text_blobs)

    for event in row.get("trace") or []:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type:
            summary["trace_types"][str(event_type)] += 1
        message = event.get("message")
        if isinstance(message, dict):
            collect_trace_tools_from_content(message.get("content"), summary["tools"], summary["commands"])
            collect_trace_text(message.get("content"), text_blobs)
        attachment = event.get("attachment")
        if isinstance(attachment, dict):
            collect_trace_text(attachment.get("content"), text_blobs)

    for command in summary["commands"][command_start:]:
        for name, pattern in TRACE_VERIFY_PATTERNS:
            if pattern.search(command):
                summary["command_verification_hits"][name] += 1

    for text in text_blobs:
        for name, pattern in TRACE_VERIFY_PATTERNS:
            if pattern.search(text):
                summary["text_verification_hits"][name] += 1
        for name, pattern in TRACE_ERROR_PATTERNS:
            if pattern.search(text):
                summary["error_hits"][name] += 1


def read_trace_json_file(path, errors):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append("{0}: {1}".format(path, exc))
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    errors.append("{0}: top-level JSON is not an object or array".format(path))
    return []


def read_trace_jsonl_file(path, errors):
    records = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    errors.append("{0}:{1}: {2}".format(path, line_number, exc))
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except Exception as exc:
        errors.append("{0}: {1}".format(path, exc))
    return records


def trace_input_files(paths, recursive):
    files = []
    for raw_path in paths:
        if raw_path == "-":
            files.append("-")
            continue
        path = Path(raw_path).expanduser()
        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.iterdir()
            files.extend(
                sorted(
                    item
                    for item in iterator
                    if item.is_file() and item.suffix.lower() in TRACE_JSON_SUFFIXES
                )
            )
        else:
            files.append(path)
    return files


def trace_count_matching_tools(tool_counter, keywords):
    total = 0
    for name, count in tool_counter.items():
        normalized = str(name).lower()
        if any(keyword in normalized for keyword in keywords):
            total += count
    return total


def trace_ratio(numerator, denominator):
    return round(float(numerator) / max(int(denominator), 1), 2)


def trace_behavior_metrics_from_summary(summary):
    tool_counter = summary["tools"]
    command_hits = summary["command_verification_hits"]
    text_hits = summary["text_verification_hits"]
    error_hits = summary["error_hits"]
    read_count = trace_count_matching_tools(tool_counter, TRACE_READ_TOOL_KEYWORDS)
    edit_count = trace_count_matching_tools(tool_counter, TRACE_EDIT_TOOL_KEYWORDS)
    shell_count = trace_count_matching_tools(tool_counter, TRACE_SHELL_TOOL_KEYWORDS)
    tool_total = sum(tool_counter.values())
    command_total = len(summary["commands"])
    verification_total = sum(command_hits.values())
    text_verification_total = sum(text_hits.values())
    error_total = sum(error_hits.values())
    records = summary["records_read"]
    return {
        "records": records,
        "sessions": len(summary["sessions"]),
        "tool_total": tool_total,
        "command_total": command_total,
        "read_tool_count": read_count,
        "edit_tool_count": edit_count,
        "shell_tool_count": shell_count,
        "verification_command_count": verification_total,
        "verification_text_count": text_verification_total,
        "error_signal_count": error_total,
        "tool_density": trace_ratio(tool_total, records),
        "command_density": trace_ratio(command_total, records),
        "read_to_edit_ratio": trace_ratio(read_count, edit_count),
        "test_to_edit_ratio": trace_ratio(command_hits.get("test", 0), edit_count),
        "verify_to_edit_ratio": trace_ratio(verification_total, edit_count),
        "recovery_to_record_ratio": trace_ratio(error_total, records),
    }


def build_trace_analysis(paths, limit=5000, recursive=False, model_filter=None):
    summary = {
        "status": "ok",
        "inputs": [str(path) for path in paths],
        "model_filter": model_filter or "",
        "files": [],
        "errors": [],
        "records_read": 0,
        "records_seen": 0,
        "limit": limit,
        "format_counts": Counter(),
        "sessions": defaultdict(lambda: {"records": 0, "tool_score": 0}),
        "models": Counter(),
        "harnesses": Counter(),
        "output_types": Counter(),
        "entrypoints": Counter(),
        "permission_modes": Counter(),
        "modes": Counter(),
        "trace_types": Counter(),
        "tools": Counter(),
        "commands": [],
        "tool_call_values": [],
        "command_verification_hits": Counter(),
        "text_verification_hits": Counter(),
        "error_hits": Counter(),
    }
    files = trace_input_files(paths, recursive)
    for item in files:
        if limit and summary["records_read"] >= limit:
            break
        if item == "-":
            records = []
            for line_number, line in enumerate(sys.stdin, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    summary["errors"].append("<stdin>:{0}: {1}".format(line_number, exc))
                    continue
                if isinstance(record, dict):
                    records.append(record)
            label = "<stdin>"
        else:
            path = Path(item)
            label = str(path)
            if not path.exists():
                summary["errors"].append("{0}: file not found".format(path))
                continue
            if path.suffix.lower() == ".json":
                records = read_trace_json_file(path, summary["errors"])
            elif path.suffix.lower() == ".jsonl":
                records = read_trace_jsonl_file(path, summary["errors"])
            else:
                summary["errors"].append("{0}: unsupported file type".format(path))
                continue
        used = 0
        for record in records:
            if limit and summary["records_read"] >= limit:
                break
            summary["records_seen"] += 1
            if not trace_model_matches(record, model_filter):
                continue
            collect_trace_row_signals(record, summary)
            summary["records_read"] += 1
            used += 1
        summary["files"].append({"path": label, "records": used})
    return finalize_trace_analysis(summary)


def percentile(values, pct):
    if not values:
        return 0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * pct)))
    return sorted_values[index]


def build_trace_recommendations(view):
    recommendations = []
    formats = view["format_counts"]
    tools = {item["name"]: item["count"] for item in view["top_tools"]}
    command_hits = view["command_verification_hits"]
    text_hits = view["text_verification_hits"]
    if formats.get("scenario_row", 0):
        recommendations.append({
            "id": "scenario-classifier-evals",
            "title": "Use scenario rows as classifier and quick-start evals.",
            "detail": "Instruction/output rows are best for task classification, verifier recommendation, and concise workflow examples.",
        })
    if formats.get("action_row", 0) or formats.get("session_trace", 0):
        recommendations.append({
            "id": "action-first-runtime",
            "title": "Optimize for visible action streams, not protocol narration.",
            "detail": "Trace rows are dominated by tool actions, so Mythify should surface current command, changed files, evidence, and next action.",
        })
    if any(command_hits.values()) or any(text_hits.values()):
        recommendations.append({
            "id": "auto-evidence-detection",
            "title": "Infer verification evidence from shell behavior.",
            "detail": "Detected test, build, lint, server, browser, or git signals can seed step evidence and suggested verify_run commands.",
        })
    if tools.get("Monitor", 0) or tools.get("ScheduleWakeup", 0):
        recommendations.append({
            "id": "background-monitoring",
            "title": "Treat monitors and wakeups as first-class outcome loops.",
            "detail": "Long-running traces rely on waiting, checking, and resuming, which maps directly to Mythify outcome progress.",
        })
    if command_hits.get("browser", 0) or text_hits.get("browser", 0):
        recommendations.append({
            "id": "visual-verification",
            "title": "Promote browser and screenshot checks to a visual verification lane.",
            "detail": "UI work should record browser, console, screenshot, and responsive checks as durable evidence.",
        })
    if view["error_hits"].get("limit", 0):
        recommendations.append({
            "id": "context-and-limit-recovery",
            "title": "Track limit and context failures as recoverable workflow events.",
            "detail": "Limit language appears often enough that Mythify should make recovery, compaction, and resumption explicit.",
        })
    return recommendations


def finalize_trace_analysis(summary):
    session_items = [
        {"session": key, **value}
        for key, value in summary["sessions"].items()
    ]
    session_items.sort(key=lambda item: (item["tool_score"], item["records"]), reverse=True)
    command_counter = Counter(summary["commands"])
    values = summary["tool_call_values"]
    view = {
        "status": summary["status"],
        "inputs": summary["inputs"],
        "model_filter": summary["model_filter"],
        "files": summary["files"],
        "errors": summary["errors"],
        "records_read": summary["records_read"],
        "records_seen": summary["records_seen"],
        "limit": summary["limit"],
        "format_counts": dict(summary["format_counts"]),
        "unique_sessions": len(summary["sessions"]),
        "top_sessions": session_items[:10],
        "tool_call_stats": {
            "count": len(values),
            "min": min(values) if values else 0,
            "median": percentile(values, 0.5),
            "max": max(values) if values else 0,
        },
        "models": dict(summary["models"].most_common(10)),
        "harnesses": dict(summary["harnesses"].most_common(10)),
        "output_types": dict(summary["output_types"].most_common(10)),
        "entrypoints": dict(summary["entrypoints"].most_common(10)),
        "permission_modes": dict(summary["permission_modes"].most_common(10)),
        "modes": dict(summary["modes"].most_common(10)),
        "trace_types": dict(summary["trace_types"].most_common(15)),
        "tool_total": sum(summary["tools"].values()),
        "command_total": len(summary["commands"]),
        "top_tools": counter_top(summary["tools"], 20),
        "top_commands": [
            {"command": command, "count": count}
            for command, count in command_counter.most_common(10)
        ],
        "command_verification_hits": dict(summary["command_verification_hits"]),
        "text_verification_hits": dict(summary["text_verification_hits"]),
        "error_hits": dict(summary["error_hits"]),
        "behavior_metrics": trace_behavior_metrics_from_summary(summary),
    }
    view["recommendations"] = build_trace_recommendations(view)
    return view


def format_trace_analysis(view):
    lines = [
        "[OK] Trace analysis: {0} records from {1} files".format(
            view["records_read"], len(view["files"])
        )
    ]
    if view["limit"]:
        lines.append("Limit: {0}".format(view["limit"]))
    if view.get("model_filter"):
        lines.append("Model filter: {0}".format(view["model_filter"]))
    if view["format_counts"]:
        parts = [
            "{0}={1}".format(name, count)
            for name, count in sorted(view["format_counts"].items())
        ]
        lines.append("Formats: {0}".format(", ".join(parts)))
    lines.append("Sessions: {0}".format(view["unique_sessions"]))
    stats = view["tool_call_stats"]
    if stats["count"]:
        lines.append(
            "Session tool calls: min {0}, median {1}, max {2}".format(
                stats["min"], stats["median"], stats["max"]
            )
        )
    if view["top_tools"]:
        lines.append(
            "Top tools: {0}".format(
                ", ".join("{0}={1}".format(item["name"], item["count"]) for item in view["top_tools"][:8])
            )
        )
    metrics = view.get("behavior_metrics") or {}
    if metrics:
        lines.append(
            "Behavior metrics: tool density {0}, read/edit {1}, verify/edit {2}".format(
                metrics["tool_density"],
                metrics["read_to_edit_ratio"],
                metrics["verify_to_edit_ratio"],
            )
        )
    if view["command_verification_hits"]:
        lines.append(
            "Command verification signals: {0}".format(
                ", ".join(
                    "{0}={1}".format(name, count)
                    for name, count in sorted(view["command_verification_hits"].items())
                )
            )
        )
    if view["text_verification_hits"]:
        lines.append(
            "Text verification signals: {0}".format(
                ", ".join(
                    "{0}={1}".format(name, count)
                    for name, count in sorted(view["text_verification_hits"].items())
                )
            )
        )
    if view["error_hits"]:
        lines.append(
            "Error and recovery signals: {0}".format(
                ", ".join(
                    "{0}={1}".format(name, count)
                    for name, count in sorted(view["error_hits"].items())
                )
            )
        )
    if view["top_sessions"]:
        lines.append("Largest sessions:")
        for item in view["top_sessions"][:5]:
            lines.append(
                "  {0}: records {1}, tool score {2}".format(
                    item["session"], item["records"], item["tool_score"]
                )
            )
    if view["recommendations"]:
        lines.append("Recommendations:")
        for item in view["recommendations"]:
            lines.append("  - {0}: {1}".format(item["title"], item["detail"]))
    if view["errors"]:
        lines.append("Warnings:")
        for error in view["errors"][:5]:
            lines.append("  - {0}".format(error))
        if len(view["errors"]) > 5:
            lines.append("  - ... {0} more".format(len(view["errors"]) - 5))
    lines.append(
        "Guardrail: trace analysis is material for planning and eval design, not verification evidence."
    )
    return "\n".join(lines)


def trace_top_tools_text(view, limit=6):
    tools = view.get("top_tools") or []
    if not tools:
        return "none detected"
    return ", ".join(
        "{0}={1}".format(item["name"], item["count"])
        for item in tools[:limit]
    )


def trace_hits_text(hits):
    if not hits:
        return "none detected"
    return ", ".join(
        "{0}={1}".format(name, count)
        for name, count in sorted(hits.items())
        if count
    ) or "none detected"


def trace_metric(view, key):
    return (view.get("behavior_metrics") or {}).get(key, 0)


def trace_metric_row(label, key, target_view, baseline_view=None):
    target = trace_metric(target_view, key)
    if baseline_view is None:
        return "| {0} | {1} |".format(label, target)
    baseline = trace_metric(baseline_view, key)
    if isinstance(target, float) or isinstance(baseline, float):
        diff = round(float(target) - float(baseline), 2)
    else:
        diff = int(target) - int(baseline)
    return "| {0} | {1} | {2} | {3} |".format(label, target, baseline, diff)


def trace_scope_lines(view, label):
    lines = [
        "- Label: {0}".format(label),
        "- Records used: {0}".format(view["records_read"]),
        "- Records seen: {0}".format(view.get("records_seen", view["records_read"])),
        "- Files: {0}".format(len(view["files"])),
        "- Sessions: {0}".format(view["unique_sessions"]),
    ]
    if view.get("model_filter"):
        lines.append("- Model filter: {0}".format(view["model_filter"]))
    if view.get("models"):
        lines.append(
            "- Models: {0}".format(
                ", ".join("{0}={1}".format(name, count) for name, count in view["models"].items())
            )
        )
    return lines


def trace_playbook_instructions(view, compare_view=None):
    metrics = view.get("behavior_metrics") or {}
    command_hits = view.get("command_verification_hits") or {}
    has_edits = metrics.get("edit_tool_count", 0) > 0
    has_verification = metrics.get("verification_command_count", 0) > 0
    lines = [
        "- Start by mapping the request to concrete files, commands, and visible state.",
        "- Keep a short action ledger in chat: current step, command or edit, result, and next move.",
        "- Prefer small reversible edits, then run the closest executable check before reporting completion.",
    ]
    if metrics.get("read_to_edit_ratio", 0) >= 1:
        lines.append(
            "- Read before editing. Preserve at least one inspect step for each meaningful code change."
        )
    if has_edits and has_verification:
        lines.append(
            "- After edits, run tests, build, lint, browser checks, or git checks that match the changed surface."
        )
    if command_hits.get("browser", 0):
        lines.append(
            "- For UI work, include browser or screenshot evidence instead of relying on visual guesses."
        )
    if metrics.get("error_signal_count", 0):
        lines.append(
            "- Treat failures, limits, and permission issues as workflow events: name the cause, correct it, and re-check."
        )
    if compare_view is not None:
        target_verify = trace_metric(view, "verify_to_edit_ratio")
        baseline_verify = trace_metric(compare_view, "verify_to_edit_ratio")
        if target_verify > baseline_verify:
            lines.append(
                "- Increase post-edit verification until the work rhythm matches the target trace slice."
            )
        target_reads = trace_metric(view, "read_to_edit_ratio")
        baseline_reads = trace_metric(compare_view, "read_to_edit_ratio")
        if target_reads > baseline_reads:
            lines.append(
                "- Add more inspection before modification when the baseline jumps to edits too quickly."
            )
    lines.append(
        "- Report final status as outcome, evidence, and remaining risk. Do not treat this playbook as verification."
    )
    return lines


def format_trace_distillation_markdown(view, title, label):
    metrics = view.get("behavior_metrics") or {}
    lines = [
        "# {0}".format(title),
        "",
        "Trace-derived behavior profile for {0}.".format(label),
        "",
        "## Scope",
    ]
    lines.extend(trace_scope_lines(view, label))
    lines.extend([
        "",
        "## Behavioral Metrics",
        "",
        "| Metric | Value |",
        "| :--- | ---: |",
        trace_metric_row("Tool density per record", "tool_density", view),
        trace_metric_row("Command density per record", "command_density", view),
        trace_metric_row("Read tools", "read_tool_count", view),
        trace_metric_row("Edit tools", "edit_tool_count", view),
        trace_metric_row("Shell tools", "shell_tool_count", view),
        trace_metric_row("Read to edit ratio", "read_to_edit_ratio", view),
        trace_metric_row("Test to edit ratio", "test_to_edit_ratio", view),
        trace_metric_row("Verify to edit ratio", "verify_to_edit_ratio", view),
        trace_metric_row("Recovery signals per record", "recovery_to_record_ratio", view),
        "",
        "## Tool Rhythm",
        "",
        "- Top tools: {0}".format(trace_top_tools_text(view)),
        "- Repeated commands: {0}".format(len(view.get("top_commands") or [])),
        "- Total visible tool calls: {0}".format(metrics.get("tool_total", 0)),
        "",
        "## Verification Habits",
        "",
        "- Command signals: {0}".format(trace_hits_text(view.get("command_verification_hits"))),
        "- Text signals: {0}".format(trace_hits_text(view.get("text_verification_hits"))),
        "",
        "## Recovery Signals",
        "",
        "- Error and limit language: {0}".format(trace_hits_text(view.get("error_hits"))),
        "",
        "## Playbook",
        "",
    ])
    lines.extend(trace_playbook_instructions(view))
    if view.get("recommendations"):
        lines.extend(["", "## Product Recommendations", ""])
        for item in view["recommendations"]:
            lines.append("- {0}: {1}".format(item["title"], item["detail"]))
    lines.extend([
        "",
        "## Guardrail",
        "",
        "This playbook is steering material from observed traces. It is not proof that any local task is complete.",
    ])
    return "\n".join(lines)


def format_trace_compare_markdown(target_view, baseline_view, target_label, baseline_label):
    lines = [
        "# Trace Behavior Comparison",
        "",
        "Target: {0}".format(target_label),
        "",
        "Baseline: {0}".format(baseline_label),
        "",
        "## Scope",
        "",
        "### Target",
    ]
    lines.extend(trace_scope_lines(target_view, target_label))
    lines.extend(["", "### Baseline"])
    lines.extend(trace_scope_lines(baseline_view, baseline_label))
    lines.extend([
        "",
        "## Metrics",
        "",
        "| Metric | Target | Baseline | Target minus baseline |",
        "| :--- | ---: | ---: | ---: |",
        trace_metric_row("Tool density per record", "tool_density", target_view, baseline_view),
        trace_metric_row("Command density per record", "command_density", target_view, baseline_view),
        trace_metric_row("Read tools", "read_tool_count", target_view, baseline_view),
        trace_metric_row("Edit tools", "edit_tool_count", target_view, baseline_view),
        trace_metric_row("Shell tools", "shell_tool_count", target_view, baseline_view),
        trace_metric_row("Read to edit ratio", "read_to_edit_ratio", target_view, baseline_view),
        trace_metric_row("Test to edit ratio", "test_to_edit_ratio", target_view, baseline_view),
        trace_metric_row("Verify to edit ratio", "verify_to_edit_ratio", target_view, baseline_view),
        trace_metric_row("Recovery signals per record", "recovery_to_record_ratio", target_view, baseline_view),
        "",
        "## Tool Rhythm",
        "",
        "- Target top tools: {0}".format(trace_top_tools_text(target_view)),
        "- Baseline top tools: {0}".format(trace_top_tools_text(baseline_view)),
        "",
        "## Delta Playbook",
        "",
    ])
    lines.extend(trace_playbook_instructions(target_view, baseline_view))
    lines.extend([
        "",
        "## Guardrail",
        "",
        "This comparison is trace-derived steering material. It does not verify implementation quality or task completion.",
    ])
    return "\n".join(lines)


def format_trace_playbook_markdown(target_view, baseline_view, target_label, baseline_label=None, title=None):
    title = title or "Trace-Derived Agent Playbook"
    lines = [
        "# {0}".format(title),
        "",
        "Use this at the start of an agent session to steer visible work habits toward {0}.".format(target_label),
        "",
        "## Operating Rules",
        "",
    ]
    lines.extend(trace_playbook_instructions(target_view, baseline_view))
    lines.extend([
        "",
        "## Target Rhythm",
        "",
        "- Top tools: {0}".format(trace_top_tools_text(target_view)),
        "- Command verification: {0}".format(trace_hits_text(target_view.get("command_verification_hits"))),
        "- Read/edit ratio: {0}".format(trace_metric(target_view, "read_to_edit_ratio")),
        "- Verify/edit ratio: {0}".format(trace_metric(target_view, "verify_to_edit_ratio")),
    ])
    if baseline_view is not None:
        lines.extend([
            "",
            "## Baseline Adjustment",
            "",
            "- Baseline: {0}".format(baseline_label),
            "- Baseline top tools: {0}".format(trace_top_tools_text(baseline_view)),
            "- Baseline read/edit ratio: {0}".format(trace_metric(baseline_view, "read_to_edit_ratio")),
            "- Baseline verify/edit ratio: {0}".format(trace_metric(baseline_view, "verify_to_edit_ratio")),
            "- Main adjustment: move the baseline closer to the target metrics while preserving Mythify verification rules.",
        ])
    lines.extend([
        "",
        "## Reporting Contract",
        "",
        "- In chat, show the workstream as current action, evidence, issue, and next action.",
        "- Completion requires an executed verifier when one exists.",
        "- If no executable verifier exists, mark the claim as attested and explain the limitation.",
        "",
        "## Guardrail",
        "",
        "This playbook copies visible workflow habits. It does not copy model capability and does not replace verification.",
    ])
    return "\n".join(lines)
