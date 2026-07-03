"""Read-only parsers for godplans and godaudits artifacts.

godplans emits `.godplans/PLAN.mdx` and godaudits emits `.godaudits/AUDIT.mdx`.
Both are GFM-safe MDX by contract: YAML frontmatter digest plus checkbox tasks
(`- [ ] GP-201 [W2.1] Title` with indented `- Verify:` sub-fields). This module
parses them tolerantly for the importer, router, and readiness views. It never
writes: the artifact files stay owned by their emitting skills and the
executing agent; Mythify only reads them and holds the evidence trail.

Parser rules hardened against measured format quirks: task ids are opaque
tokens (never decomposed into phase digits), sub-fields are scanned until the
next task or heading (never a fixed line window), strikethrough superseded
tasks and `- Note (date):` lines are tolerated, and fenced code blocks are
skipped so quoted evidence can never masquerade as a task.
"""

from __future__ import annotations

import re
from pathlib import Path

GODPLANS_DIR_NAME = ".godplans"
GODAUDITS_DIR_NAME = ".godaudits"
GODPLANS_FILENAMES = ("PLAN.mdx", "PLAN.md")
GODAUDITS_FILENAMES = ("AUDIT.mdx", "AUDIT.md")

_TASK_RE = re.compile(
    r"^(?P<struck>~~)?- \[(?P<check>[ xX])\]\s+(?P<id>(?:GP|GA)-\S+)\s*(?P<rest>.*?)(?:~~)?\s*$"
)
_FLAG_RE = re.compile(r"^\[(?P<flag>[^\]]+)\]\s*")
_WAVE_FLAG_RE = re.compile(r"^W[0-9][0-9.]*$")
_FIELD_RE = re.compile(r"^(?:\t|\s{2,})- (?P<name>[A-Za-z][A-Za-z ]*?):\s*(?P<value>.*)$")
_NOTE_RE = re.compile(r"^(?:\t|\s{2,})- Note \(")
_PHASE_RE = re.compile(r"^## Phase\s+(?P<number>\S+):\s*(?P<title>.*)$")
_WAVE_RE = re.compile(r"^### Wave\s+(?P<wave>\S+)")
_FINDING_RE = re.compile(
    r"^#### (?P<id>F-[A-Z]+-[0-9]+)\s+(?P<title>.*?)\s*\[(?P<triple>[^\]]+)\]\s*$"
)
_INT_RE = re.compile(r"^-?[0-9]+$")
# CommonMark fence: 3+ backticks or 3+ tildes, same run closes the fence.
_FENCE_RE = re.compile(r"^(?P<marker>`{3,}|~{3,})")
# Byte-order mark: dropped at the parse boundary so BOM-prefixed artifacts parse
# identically to the JS mirror (which trims it) and to BOM-less files.
_BOM = "\ufeff"

_FIELD_KEYS = {
    "files": "files",
    "depends on": "depends_on",
    "reuses": "reuses",
    "acceptance": "acceptance",
    "verify": "verify_command",
    "requirements": "requirements",
    "fixes": "fixes",
    "checks": "checks",
}
_LIST_FIELDS = ("files", "depends_on", "fixes")


def find_godplans_file(root):
    return _find_artifact(Path(root) / GODPLANS_DIR_NAME, GODPLANS_FILENAMES)


def find_godaudits_file(root):
    return _find_artifact(Path(root) / GODAUDITS_DIR_NAME, GODAUDITS_FILENAMES)


def _find_artifact(directory, filenames):
    for name in filenames:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _scalar(value):
    text = value.strip()
    if text.startswith(("\"", "'")) and text.endswith(text[0]) and len(text) >= 2:
        return text[1:-1]
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if _INT_RE.match(text):
        return int(text)
    return text


def parse_god_frontmatter(text):
    """Tolerant YAML-subset reader for the artifact digest.

    Handles the scalar and one-level-nested keys the digests use (progress,
    scores, counts). Anything deeper or malformed is skipped, never fatal:
    the checkboxes are the truth and the digest is advisory.
    """
    lines = text.lstrip(_BOM).splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data = {}
    current = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if stripped.startswith("- "):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if indent == 0:
            if value == "":
                data[key] = {}
                current = key
            else:
                data[key] = _scalar(value)
                current = None
        elif current is not None and isinstance(data.get(current), dict):
            if value != "":
                data[current][key] = _scalar(value)
    return data


def _strip_backticks(value):
    text = value.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        return text[1:-1].strip()
    return text


def _split_list(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def _finish_task(task, tasks):
    if task is None:
        return
    for key in _LIST_FIELDS:
        if key in task and isinstance(task[key], str):
            items = _split_list(task[key])
            if len(items) == 1 and items[0].lower().startswith("none"):
                items = []
            task[key] = items
    verify = task.get("verify_command", "")
    task["verify_command"] = _strip_backticks(verify) if verify else ""
    tasks.append(task)


def parse_god_document(text):
    """Parse the body of a PLAN.mdx or AUDIT.mdx into tasks and findings."""
    frontmatter = parse_god_frontmatter(text)
    tasks = []
    findings = []
    phases = []
    current_phase = None
    current_wave = ""
    task = None
    field = None
    finding = None
    fence = None
    for line in text.lstrip(_BOM).splitlines():
        fence_match = _FENCE_RE.match(line.lstrip())
        if fence is None:
            if fence_match:
                marker = fence_match.group("marker")
                fence = (marker[0], len(marker))
                continue
        else:
            if fence_match:
                marker = fence_match.group("marker")
                closes = (
                    marker[0] == fence[0]
                    and len(marker) >= fence[1]
                    and line.lstrip().rstrip() == marker
                )
                if closes:
                    fence = None
            continue
        if line.startswith("#"):
            _finish_task(task, tasks)
            task = None
            field = None
            finding = None
            phase_match = _PHASE_RE.match(line)
            if phase_match:
                current_phase = {
                    "number": phase_match.group("number"),
                    "title": phase_match.group("title").strip(),
                }
                phases.append(current_phase)
                current_wave = ""
                continue
            wave_match = _WAVE_RE.match(line)
            if wave_match:
                current_wave = wave_match.group("wave").rstrip(".")
                continue
            finding_match = _FINDING_RE.match(line)
            if finding_match:
                triple = [part.strip() for part in finding_match.group("triple").split("|")]
                finding = {
                    "id": finding_match.group("id"),
                    "title": finding_match.group("title").strip(),
                    "severity": triple[0] if triple else "",
                    "confidence": triple[1] if len(triple) > 1 else "",
                    "effort": triple[2] if len(triple) > 2 else "",
                    "status": "open",
                    "remediation": "",
                }
                findings.append(finding)
            continue
        task_match = _TASK_RE.match(line)
        if task_match:
            _finish_task(task, tasks)
            field = None
            finding = None
            rest = task_match.group("rest")
            parallel = False
            wave = current_wave
            while True:
                flag_match = _FLAG_RE.match(rest)
                if not flag_match:
                    break
                flag = flag_match.group("flag")
                if flag == "P":
                    parallel = True
                elif _WAVE_FLAG_RE.match(flag):
                    wave = flag[1:]
                else:
                    break
                rest = rest[flag_match.end():]
            task = {
                "id": task_match.group("id"),
                "title": rest.strip(),
                "checked": task_match.group("check") in ("x", "X"),
                "superseded": bool(task_match.group("struck")),
                "parallel": parallel,
                "wave": wave,
                "phase_number": current_phase["number"] if current_phase else "",
                "phase_title": current_phase["title"] if current_phase else "",
                "notes": [],
            }
            continue
        if line and not line[0].isspace():
            if finding is not None and line.startswith("- "):
                name, _, value = line[2:].partition(":")
                key = name.strip().lower()
                if key == "status":
                    finding["status"] = value.strip()
                elif key == "remediation":
                    finding["remediation"] = value.strip()
                continue
            _finish_task(task, tasks)
            task = None
            field = None
            finding = None
            continue
        if task is not None:
            if _NOTE_RE.match(line):
                task["notes"].append(line.strip()[2:])
                field = None
                continue
            field_match = _FIELD_RE.match(line)
            if field_match:
                key = _FIELD_KEYS.get(field_match.group("name").strip().lower())
                if key:
                    task[key] = field_match.group("value").strip()
                    field = key
                else:
                    field = None
                continue
            if field and line.strip():
                task[field] = (task[field] + " " + line.strip()).strip()
            continue
    _finish_task(task, tasks)
    live_tasks = [entry for entry in tasks if not entry["superseded"]]
    done = sum(1 for entry in live_tasks if entry["checked"])
    next_task = next((entry for entry in live_tasks if not entry["checked"]), None)
    return {
        "frontmatter": frontmatter,
        "tasks": tasks,
        "findings": findings,
        "phases": phases,
        "counts": {
            "tasks_total": len(live_tasks),
            "tasks_done": done,
            "tasks_open": len(live_tasks) - done,
        },
        "next_task": next_task,
    }


def _digest_counter_drift(frontmatter, counts):
    progress = frontmatter.get("progress")
    if not isinstance(progress, dict):
        progress = frontmatter.get("counts")
    if not isinstance(progress, dict):
        return False
    drift = False
    for digest_key, body_key in (("tasks_total", "tasks_total"), ("tasks_done", "tasks_done")):
        digest_value = progress.get(digest_key)
        if (
            isinstance(digest_value, int)
            and not isinstance(digest_value, bool)
            and digest_value != counts[body_key]
        ):
            drift = True
    return drift


def load_god_artifact(path, kind):
    """Parse one artifact file into a stable digest dict; never raises.

    Load failures (unreadable file, unrecognized content) return a digest with
    ``load_error`` set, so callers branch on that structural flag rather than
    the author-controlled ``status`` string, which could itself be the word
    ``unreadable`` or ``unrecognized``.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as error:
        return {
            "kind": kind,
            "path": str(path),
            "status": "unreadable",
            "load_error": True,
            "detail": str(error),
        }
    parsed = parse_god_document(text)
    frontmatter = parsed["frontmatter"]
    counts = parsed["counts"]
    expected_prefix = "GP-" if kind == "godplans" else "GA-"
    recognized = any(entry["id"].startswith(expected_prefix) for entry in parsed["tasks"])
    if not recognized and not frontmatter:
        return {
            "kind": kind,
            "path": str(path),
            "status": "unrecognized",
            "load_error": True,
            "detail": "no frontmatter and no {0} tasks found".format(expected_prefix),
        }
    digest = {
        "kind": kind,
        "path": str(path),
        "status": str(frontmatter.get("status") or "unknown"),
        "name": str(frontmatter.get("name") or ""),
        "counts": counts,
        "counter_drift": _digest_counter_drift(frontmatter, counts),
        "next_task": parsed["next_task"],
        "tasks": parsed["tasks"],
        "phases": parsed["phases"],
    }
    if kind == "godplans":
        digest["plan_version"] = frontmatter.get("plan_version")
    else:
        digest["audit_version"] = frontmatter.get("audit_version")
        digest["plan_aware"] = bool(frontmatter.get("plan_aware"))
        scores = frontmatter.get("scores")
        digest["overall_score"] = scores.get("overall") if isinstance(scores, dict) else None
        digest["verdict"] = str(scores.get("verdict") or "") if isinstance(scores, dict) else ""
        digest["findings"] = parsed["findings"]
        digest["open_critical"] = sum(
            1
            for finding in parsed["findings"]
            if finding["status"] == "open" and finding["severity"] == "Critical"
        )
        digest["open_high"] = sum(
            1
            for finding in parsed["findings"]
            if finding["status"] == "open" and finding["severity"] == "High"
        )
    return digest


def _task_progress_detail(digest):
    counts = digest.get("counts") or {}
    parts = ["{0}/{1} tasks done".format(counts.get("tasks_done", 0), counts.get("tasks_total", 0))]
    next_task = digest.get("next_task")
    if next_task:
        parts.append("next {0} {1}".format(next_task["id"], next_task["title"]))
    if digest.get("counter_drift"):
        parts.append("frontmatter counters disagree with checkboxes")
    return "; ".join(parts)


def godplans_summary(root):
    """Views-facing digest of .godplans/PLAN.mdx.

    Callers surface the artifact when ``present`` is true and treat the file as
    absent otherwise; they never key off the author-controlled ``status``.
    """
    path = find_godplans_file(root)
    if path is None:
        return {"status": "missing", "present": False, "path": "", "detail": "no .godplans plan found"}
    digest = load_god_artifact(path, "godplans")
    if digest.get("load_error"):
        return {
            "status": digest["status"],
            "present": True,
            "path": digest["path"],
            "detail": digest.get("detail", ""),
        }
    summary = {
        "status": digest["status"],
        "present": True,
        "path": digest["path"],
        "detail": _task_progress_detail(digest),
        "tasks_total": digest["counts"]["tasks_total"],
        "tasks_done": digest["counts"]["tasks_done"],
        "counter_drift": digest["counter_drift"],
    }
    next_task = digest.get("next_task")
    if next_task:
        summary["next_task_id"] = next_task["id"]
        summary["next_task_title"] = next_task["title"]
    return summary


def godaudits_summary(root):
    """Views-facing digest of .godaudits/AUDIT.mdx.

    Callers surface the artifact when ``present`` is true and treat the file as
    absent otherwise; they never key off the author-controlled ``status``.
    """
    path = find_godaudits_file(root)
    if path is None:
        return {"status": "missing", "present": False, "path": "", "detail": "no .godaudits audit found"}
    digest = load_god_artifact(path, "godaudits")
    if digest.get("load_error"):
        return {
            "status": digest["status"],
            "present": True,
            "path": digest["path"],
            "detail": digest.get("detail", ""),
        }
    detail_parts = []
    if digest.get("overall_score") is not None:
        verdict = digest.get("verdict") or "unrated"
        detail_parts.append("score {0} ({1})".format(digest["overall_score"], verdict))
    if digest.get("open_critical"):
        detail_parts.append("{0} open Critical".format(digest["open_critical"]))
    if digest.get("open_high"):
        detail_parts.append("{0} open High".format(digest["open_high"]))
    detail_parts.append(_task_progress_detail(digest))
    summary = {
        "status": digest["status"],
        "present": True,
        "path": digest["path"],
        "detail": "; ".join(detail_parts),
        "tasks_total": digest["counts"]["tasks_total"],
        "tasks_done": digest["counts"]["tasks_done"],
        "counter_drift": digest["counter_drift"],
        "open_critical": digest.get("open_critical", 0),
        "open_high": digest.get("open_high", 0),
        "overall_score": digest.get("overall_score"),
        "verdict": digest.get("verdict", ""),
    }
    next_task = digest.get("next_task")
    if next_task:
        summary["next_task_id"] = next_task["id"]
        summary["next_task_title"] = next_task["title"]
    return summary
