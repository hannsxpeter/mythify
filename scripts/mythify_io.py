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


def write_jsonl_atomic(path, records):
    text = "".join(json.dumps(record, allow_nan=False) + "\n" for record in records)
    _write_text_atomic(path, text)


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
