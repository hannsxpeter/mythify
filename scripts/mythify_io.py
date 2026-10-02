"""Durable file IO helpers for Mythify.

The CLI configures state-dir and timestamp helpers at import time. This module
owns atomic text and JSON writes, JSONL locking, tolerant JSONL reads, and the
bounded tail reader used by recent-evidence surfaces.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

JSONL_LOCK_TIMEOUT_SECONDS = 10.0
JSONL_LOCK_OWNER_GRACE_SECONDS = 1.0
JSONL_TAIL_CHUNK_BYTES = 64 * 1024

_resolve_state_dir_func = None
_now_stamp_func = None
_timestamp_at_or_after_func = None


def configure_durable_io(resolve_state_dir_func=None, now_stamp_func=None, timestamp_at_or_after_func=None):
    global _resolve_state_dir_func, _now_stamp_func, _timestamp_at_or_after_func
    _resolve_state_dir_func = resolve_state_dir_func
    _now_stamp_func = now_stamp_func
    _timestamp_at_or_after_func = timestamp_at_or_after_func


def _resolve_state_dir():
    if _resolve_state_dir_func is None:
        return Path.cwd() / ".mythify"
    return _resolve_state_dir_func()


def _now_stamp():
    if _now_stamp_func is None:
        return "unknown"
    return _now_stamp_func()


def _timestamp_at_or_after(value, lower_bound, allow_same_second=False):
    if _timestamp_at_or_after_func is None:
        return str(value or "") >= str(lower_bound or "")
    return _timestamp_at_or_after_func(value, lower_bound, allow_same_second)


def _fsync_dir_best_effort(path):
    flags = getattr(os, "O_RDONLY", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
        _fsync_dir_best_effort(path.parent)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def write_json_atomic(path, data):
    """Write JSON to a temp file in the same directory, then rename over the
    target so readers never observe a partial file."""
    _write_text_atomic(path, json.dumps(data, indent=2, allow_nan=False) + "\n")


def read_json(path, default):
    """Read a JSON file. On corruption, quarantine the bad file as
    <filename>.corrupt-<YYYYMMDDHHMMSS>, warn on stderr, and return the
    default. Never raises on bad state."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, UnicodeDecodeError):
        corrupt_name = path.name + ".corrupt-" + _now_stamp()
        corrupt_path = path.with_name(corrupt_name)
        try:
            os.replace(str(path), str(corrupt_path))
            moved = " Moved it to " + corrupt_name + "."
        except OSError:
            moved = ""
        sys.stderr.write(
            "[WARN] Corrupt JSON in " + str(path) + "." + moved
            + " Continuing with a fresh default.\n"
        )
        return default


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_file_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, allow_nan=False) + "\n")


# The chained tail read is bounded but generous: a verification record with two
# redacted 4000-char tails serializes well under this, so the previous line is
# always read whole.
JSONL_CHAIN_TAIL_BYTES = 256 * 1024


def last_jsonl_line(path):
    """The last non-empty raw line of PATH, or empty when the file is absent."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(max(0, size - JSONL_CHAIN_TAIL_BYTES))
            window = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(window.splitlines()):
        if line.strip():
            return line
    return ""


def append_chained_jsonl(path, record):
    """Append RECORD carrying the sha256 of the previous raw line.

    The chain is tamper evidence, not cryptography: an edited or deleted line
    breaks the next record's prev_sha256, so silent in-place changes become
    visible to the chain checker. The first record carries None.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_file_lock(path):
        last = last_jsonl_line(path)
        record["prev_sha256"] = (
            hashlib.sha256(last.encode("utf-8")).hexdigest() if last else None
        )
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, allow_nan=False) + "\n")


def _line_sha256(line):
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def jsonl_append_anchor(path):
    """A position marker for the current end of PATH that survives compaction.

    The anchor is the sha256 of the last raw line, the same value the next
    chained record carries as prev_sha256. `logs compact` keeps retained lines
    byte for byte, so the anchor still resolves after compaction, where a line
    count would point past the shortened file. None marks an empty log.
    """
    last = last_jsonl_line(path)
    return {"after_sha256": _line_sha256(last) if last else None}


def _raw_jsonl_lines(path):
    try:
        text = Path(path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()]


def _records_after_anchor(path, anchor):
    """Records after ANCHOR's line, or None when the line cannot be placed."""
    target = anchor.get("after_sha256")
    if target is None:
        # The log was empty when the anchor was taken: every line is newer.
        return read_jsonl(path)
    lines = _raw_jsonl_lines(path)
    for index in range(len(lines) - 1, -1, -1):
        if _line_sha256(lines[index]) == target:
            return _parse_jsonl_lines(path, lines[index + 1:])
    # Compaction archives the whole log before trimming its oldest lines, so an
    # anchor found only in an archive was trimmed away and every line still in
    # the active log is newer than it.
    path = Path(path)
    archive_dir = path.parent / "logs" / "archive"
    for archive in sorted(archive_dir.glob(path.stem + "-*" + path.suffix)):
        if any(_line_sha256(line) == target for line in _raw_jsonl_lines(archive)):
            return read_jsonl(path)
    return None


def _timestamp_strictly_after(value, lower_bound):
    return _timestamp_at_or_after(value, lower_bound) and not _timestamp_at_or_after(
        lower_bound, value
    )


def read_jsonl_after_marker(path, anchor=None, legacy_cursor=None, lower_bound=""):
    """Records appended after a stored position marker, or None without one.

    ANCHOR comes from jsonl_append_anchor. LEGACY_CURSOR is the integer line
    count stored before 6.0; it stays exact until a compaction shrinks the log
    below it. When neither marker can be placed, only records strictly after
    LOWER_BOUND count: evidence from before the marker is never reused, at the
    cost of asking for a re-run when a new record shares the marker's second.
    """
    if isinstance(anchor, dict):
        records = _records_after_anchor(path, anchor)
    elif (
        isinstance(legacy_cursor, int)
        and not isinstance(legacy_cursor, bool)
        and legacy_cursor >= 0
    ):
        everything = read_jsonl(path)
        records = everything[legacy_cursor:] if legacy_cursor <= len(everything) else None
    else:
        return None
    if records is None:
        records = [
            record
            for record in read_jsonl_since(path, lower_bound)
            if _timestamp_strictly_after(record.get("timestamp", ""), lower_bound)
        ]
    return records


def jsonl_lock_dir(path):
    path = Path(path)
    state = _resolve_state_dir()
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return state / "locks" / ("jsonl-" + digest + ".lock")


def _process_is_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_stale_jsonl_lock(lock_dir):
    owner_path = lock_dir / "owner.json"
    try:
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        owner = None
    if isinstance(owner, dict) and isinstance(owner.get("pid"), int):
        if _process_is_alive(owner["pid"]):
            return False
    else:
        try:
            if time.time() - lock_dir.stat().st_mtime < JSONL_LOCK_OWNER_GRACE_SECONDS:
                return False
        except OSError:
            return True
    try:
        if owner_path.exists():
            owner_path.unlink()
        lock_dir.rmdir()
        return True
    except OSError:
        return False


@contextmanager
def jsonl_file_lock(path, timeout=JSONL_LOCK_TIMEOUT_SECONDS):
    lock_dir = jsonl_lock_dir(path)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = False
    while not acquired:
        try:
            lock_dir.mkdir()
            acquired = True
        except FileExistsError:
            if _remove_stale_jsonl_lock(lock_dir):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for JSONL lock: {0}".format(lock_dir))
            time.sleep(0.05)
    owner_path = lock_dir / "owner.json"
    owner_path.write_text(
        json.dumps({"pid": os.getpid(), "created_unix": time.time()}, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    try:
        yield
    finally:
        try:
            owner_path.unlink()
        except OSError:
            pass
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def read_jsonl(path):
    """Parse a jsonl file, skipping blanks and warning on malformed lines."""
    path = Path(path)
    records = []
    if not path.exists():
        return records
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                sys.stderr.write(
                    "[WARN] Skipping malformed JSONL record in {0} at line {1}.\n".format(
                        path, line_number
                    )
                )
                continue
    return records


def _parse_jsonl_lines(path, lines, line_number_offset=None):
    records = []
    for index, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            if line_number_offset is None:
                location = "while reading tail"
            else:
                location = "at line {0}".format(line_number_offset + index)
            sys.stderr.write(
                "[WARN] Skipping malformed JSONL record in {0} {1}.\n".format(
                    path, location
                )
            )
            continue
    return records


def read_jsonl_since(path, lower_bound):
    """Read JSONL records at or after lower_bound with a tail-window fast path."""
    if not lower_bound:
        return read_jsonl(path)
    path = Path(path)
    if not path.exists():
        return []
    size = path.stat().st_size
    offset = size
    data = b""
    while offset > 0:
        read_size = min(JSONL_TAIL_CHUNK_BYTES, offset)
        offset -= read_size
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read(read_size) + data
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if offset > 0 and lines:
            lines = lines[1:]
        records = _parse_jsonl_lines(path, lines)
        if any(
            record.get("timestamp")
            and not _timestamp_at_or_after(record.get("timestamp", ""), lower_bound, True)
            for record in records
        ):
            return [
                record for record in records
                if _timestamp_at_or_after(record.get("timestamp", ""), lower_bound, True)
            ]
    return [
        record
        for record in _parse_jsonl_lines(
            path, data.decode("utf-8", errors="replace").splitlines()
        )
        if _timestamp_at_or_after(record.get("timestamp", ""), lower_bound, True)
    ]
