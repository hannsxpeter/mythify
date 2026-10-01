"""Memory and lesson stores plus command handlers for the Mythify CLI."""

from __future__ import annotations

import sys
from pathlib import Path

from mythify_io import read_json, write_json_atomic

WORKSPACE_DIR_NAME = ".mythify"
MEMORY_CATEGORIES = ("fact", "decision", "discovery", "state")
MEMORY_DEFAULT_CATEGORY = "fact"
MEMORY_CLEAR_CLI_REFUSAL = (
    "[FAIL] Refusing to clear memory: pass KEY to remove a single entry, or "
    "--all to clear every entry."
)


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_memory dependencies are not configured")


now_iso = _missing_dependency
now_stamp = _missing_dependency
slugify = _missing_dependency


def fail(message):
    print(message, file=sys.stderr)


def configure_memory_store(
    *,
    now_iso_func=None,
    now_stamp_func=None,
    slugify_func=None,
    fail_func=None,
):
    global now_iso, now_stamp, slugify, fail
    if now_iso_func is not None:
        now_iso = now_iso_func
    if now_stamp_func is not None:
        now_stamp = now_stamp_func
    if slugify_func is not None:
        slugify = slugify_func
    if fail_func is not None:
        fail = fail_func


def global_lessons_dir():
    return Path.home() / WORKSPACE_DIR_NAME / "lessons"


# ---------------------------------------------------------------------------
# Memory store
# ---------------------------------------------------------------------------

def default_memory():
    stamp = now_iso()
    return {
        "entries": [],
        "metadata": {"created": stamp, "last_updated": stamp, "total_entries": 0},
    }


def load_memory(state):
    memory = read_json(state / "memory.json", None)
    if not isinstance(memory, dict) or not isinstance(memory.get("entries"), list):
        memory = default_memory()
    if not isinstance(memory.get("metadata"), dict):
        memory["metadata"] = default_memory()["metadata"]
    return memory


def save_memory(state, memory):
    memory["metadata"]["last_updated"] = now_iso()
    memory["metadata"]["total_entries"] = len(memory["entries"])
    write_json_atomic(state / "memory.json", memory)


# ---------------------------------------------------------------------------
# Lesson store
# ---------------------------------------------------------------------------

def lesson_filename(title):
    base = slugify(title)[:50] or "lesson"
    return base + "-" + now_stamp() + ".json"


def write_lesson(directory, title, detail, tags):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    record = {"title": title, "detail": detail, "tags": list(tags), "created": now_iso()}
    path = directory / lesson_filename(title)
    write_json_atomic(path, record)
    return path


def load_lessons(directory, scope_label):
    items = []
    directory = Path(directory)
    if not directory.is_dir():
        return items
    for path in sorted(directory.glob("*.json")):
        record = read_json(path, None)
        if isinstance(record, dict) and record.get("title") is not None:
            items.append((scope_label, record))
    return items


def cmd_memory_set(args, state):
    memory = load_memory(state)
    stamp = now_iso()
    for entry in memory["entries"]:
        if entry.get("key") == args.key:
            entry["value"] = args.value
            entry["category"] = args.category
            entry["timestamp"] = stamp
            break
    else:
        memory["entries"].append(
            {
                "key": args.key,
                "value": args.value,
                "category": args.category,
                "timestamp": stamp,
            }
        )
    save_memory(state, memory)
    print("[OK] Stored memory entry: {0} (category: {1})".format(args.key, args.category))
    return 0


def cmd_memory_get(args, state):
    memory = load_memory(state)
    entries = memory["entries"]
    if not entries:
        print("No memory entries yet.")
        return 0
    query = (args.query or "").lower()
    matches = []
    for entry in entries:
        if args.category and entry.get("category") != args.category:
            continue
        if query:
            haystack = (
                str(entry.get("key", "")).lower() + "\n" + str(entry.get("value", "")).lower()
            )
            if query not in haystack:
                continue
        matches.append(entry)
    if not matches:
        print("No matching memory entries.")
        return 0
    print("[OK] Memory entries ({0}):".format(len(matches)))
    for entry in matches:
        print(
            "  [{0}] {1} = {2} ({3})".format(
                entry.get("category"), entry.get("key"), entry.get("value"),
                entry.get("timestamp"),
            )
        )
    return 0


def cmd_memory_clear(args, state):
    if not args.key and not args.clear_all:
        fail(MEMORY_CLEAR_CLI_REFUSAL)
        return 1
    memory = load_memory(state)
    if args.clear_all:
        removed = len(memory["entries"])
        memory["entries"] = []
        save_memory(state, memory)
        print("[OK] Cleared all memory entries ({0} removed).".format(removed))
        return 0
    before = len(memory["entries"])
    memory["entries"] = [e for e in memory["entries"] if e.get("key") != args.key]
    if len(memory["entries"]) == before:
        print("[WARN] No memory entry with key: {0}".format(args.key))
        return 0
    save_memory(state, memory)
    print("[OK] Cleared memory entry: {0}".format(args.key))
    return 0


def cmd_lesson_add(args, state):
    tags = []
    if args.tags:
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
    if args.global_scope:
        directory = global_lessons_dir()
        scope = "global"
    else:
        directory = state / "lessons"
        scope = "project"
    write_lesson(directory, args.title, args.detail, tags)
    print("[OK] Lesson recorded ({0}): {1}".format(scope, args.title))
    return 0


def cmd_lesson_list(args, state):
    items = []
    if args.scope in ("project", "all"):
        items.extend(load_lessons(state / "lessons", "project"))
    if args.scope in ("global", "all"):
        items.extend(load_lessons(global_lessons_dir(), "global"))
    if args.tag:
        items = [
            (scope, record)
            for scope, record in items
            if args.tag in (record.get("tags") or [])
        ]
    items.sort(key=lambda item: str(item[1].get("created", "")))
    if not items:
        print("No lessons recorded.")
        return 0
    print("[OK] Lessons ({0}):".format(len(items)))
    for scope, record in items:
        line = "  ({0}) {1}: {2}".format(scope, record.get("title"), record.get("detail"))
        tags = record.get("tags") or []
        if tags:
            line += " [tags: {0}]".format(", ".join(tags))
        print(line)
    return 0


