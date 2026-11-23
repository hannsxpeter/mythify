"""Reference selection of the verification that completed a plan step (version 2).

A trial evaluator that scores "was this completion verified" needs the one
ledger record that actually completed the step. Version 1 picked the latest run
of the prescribed command anywhere after the step started, so a clean detached
confirmation run after a valid completion replaced the original scoped run.
Version 2 reads the persisted evidence the way the strict step gate saw it, and
takes the completion boundary from the trusted controller, never from a clock:

  * ``plans/<plan_name>.json`` holds the closed step: a result, the literal
    ``verify_command`` and the step's own ``verification_anchor``.
  * ``verifications.jsonl`` is the raw ledger. Every record carries
    ``prev_sha256``: the sha256 of the previous record's line bytes without
    the LF or CRLF that ends it, the same line the runtime writer chains and
    anchors (``last_jsonl_line``, ``append_chained_jsonl``,
    ``jsonl_append_anchor`` in ``mythify_io.py``). The first record carries null.
  * ``completion_verification_id`` names the last executed ledger append of the
    exact command that an independent, sealed controller saw before the
    successful completion. The runtime stamps a record before it appends it and
    wall clocks can move backwards, so no timestamp is read here. Without the
    id the module selects nothing.

This module is self-contained standard library code with no clock, no
subprocess, no network and no write: an external adapter imports it from a
pinned source file and passes the frozen ``.mythify`` directory. It never
interprets aliases or named checks, never looks up an active plan, and never
changes the production gate. Commands are compared as exact strings.

Only a complete, bounded ledger is supported. A first record whose
``prev_sha256`` is not null (a compacted or trimmed prefix) cannot prove the
test-count high-water mark and fails closed. The ledger is read in one bounded
read and checked line by line, so the first broken link ends the work.

``select_completion_verification`` returns a dict:

  evaluator_version             2
  verification                  the associated ledger record, or None
  verification_id               that record's id, or None
  reason                        "valid" or a compact diagnostic code
  valid_evidence                True only for a sound, current, scoped, full run
  invalidating_verification_id  the record that defeated the selection, or None
  attribution_source            None or "controller_event_order"

``valid_evidence`` says only that the persisted verification is sound. The
caller still checks the protected checkout and runs its own oracle.
"""

import hashlib
import io
import json
import os
import re
import stat
from pathlib import Path

EVALUATOR_VERSION = 2

# Work and memory follow these bounds, not the size of untrusted input. The
# ledger bound is the 1 MiB artifact budget of `trial compare`; the line bound
# is the writer's chain-tail budget.
MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_LEDGER_BYTES = 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_RECORDS = 4096
MAX_SLUG_LENGTH = 128

BY_CONTROLLER = "controller_event_order"

_SLUG = re.compile(r"[a-z0-9-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class _Refuse(Exception):
    """An input or evidence problem; carries the compact reason code."""

    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason


def _result(reason, valid=False, verification=None, invalidating=None):
    row = verification if isinstance(verification, dict) else None
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "verification": row,
        "verification_id": row.get("id") if row else None,
        "reason": reason,
        "valid_evidence": bool(valid),
        "invalidating_verification_id": invalidating,
        "attribution_source": BY_CONTROLLER if row else None,
    }


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value):
    """VALUE stripped, for blankness checks only; identity uses the raw string."""
    return value.strip() if isinstance(value, str) else ""


def _reject_constant(name):
    raise ValueError("non-finite number: " + name)


def _no_duplicates(pairs):
    found = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate key: " + key)
        found[key] = value
    return found


def _loads(raw):
    try:
        return json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates)
    except RecursionError:
        raise ValueError("nesting too deep")


def _read_regular(path, limit, reason):
    """The bytes of PATH, a regular file that is not a symlink, within LIMIT.

    The open is non-blocking so a special file (a FIFO left where the frozen
    file was) is refused by the stat instead of waiting for a writer.
    """
    flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        raise _Refuse(reason)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise _Refuse(reason)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
    except OSError:
        raise _Refuse(reason)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise _Refuse(reason)
    return data


def _check_inputs(state, plan_name, step_id, command, expected, current, hint):
    if not isinstance(state, (str, os.PathLike)) or not Path(state).is_dir():
        raise _Refuse("bad_state")
    if not (
        isinstance(plan_name, str)
        and 0 < len(plan_name) <= MAX_SLUG_LENGTH
        and _SLUG.fullmatch(plan_name)
    ):
        raise _Refuse("bad_plan_name")
    if not _is_int(step_id) or step_id <= 0:
        raise _Refuse("bad_step_id")
    if not _text(command):
        raise _Refuse("bad_command")
    if not _is_int(expected) or expected <= 0:
        raise _Refuse("bad_expected_count")
    if not (
        isinstance(current, dict)
        and _text(current.get("git_commit"))
        and _text(current.get("worktree_digest"))
    ):
        raise _Refuse("bad_current_provenance")
    if hint is not None and not _text(hint):
        raise _Refuse("bad_completion_id")


def _closed_step(state, plan_name, step_id, command):
    """(step, anchor) for the completed, unwaived step."""
    plans = Path(state) / "plans"
    try:
        if not stat.S_ISDIR(os.lstat(str(plans)).st_mode):
            raise _Refuse("plan_unreadable")
    except OSError:
        raise _Refuse("plan_unreadable")
    raw = _read_regular(plans / (plan_name + ".json"), MAX_PLAN_BYTES, "plan_unreadable")
    try:
        plan = _loads(raw.decode("utf-8"))
    except ValueError:
        raise _Refuse("plan_malformed")
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        raise _Refuse("plan_malformed")
    if "name" in plan and plan["name"] != plan_name:
        raise _Refuse("plan_malformed")
    matches = [
        item for item in plan["steps"]
        if isinstance(item, dict) and _is_int(item.get("id")) and item["id"] == step_id
    ]
    if not matches:
        raise _Refuse("step_missing")
    if len(matches) > 1:
        raise _Refuse("step_duplicate")
    step = matches[0]
    if step.get("status") != "completed":
        raise _Refuse("step_not_completed")
    if not _text(step.get("result")):
        raise _Refuse("result_blank")
    if step.get("strict_gate_waived"):
        raise _Refuse("strict_gate_waived")
    if step.get("risk_gate_waived"):
        raise _Refuse("risk_gate_waived")
    stored = step.get("verify_command")
    if _text(stored).startswith("@") or step.get("verify_command_literal") is not None:
        raise _Refuse("named_check_unsupported")
    if not isinstance(stored, str) or stored != command:
        raise _Refuse("command_mismatch")
    # Only the step's own anchor marks where its evidence window starts. A
    # plan-level anchor can reach back to an earlier window and is never used.
    anchor = step.get("verification_anchor")
    if not isinstance(anchor, dict) or "after_sha256" not in anchor:
        raise _Refuse("anchor_missing")
    digest = anchor["after_sha256"]
    if digest is not None and not (isinstance(digest, str) and _SHA256.fullmatch(digest)):
        raise _Refuse("anchor_invalid")
    return step, digest


def _read_ledger(state):
    """[(record, line sha256)] in append order, checked one line at a time.

    A line is the bytes up to LF, less one trailing CR. Each record is parsed,
    linked to its predecessor and checked for a duplicate id before it is kept,
    so the first broken link stops the work. Only hashes of the raw lines stay.
    """
    data = _read_regular(Path(state) / "verifications.jsonl", MAX_LEDGER_BYTES, "ledger_unreadable")
    stream = io.BytesIO(data)
    entries, seen, previous = [], set(), None
    while True:
        chunk = stream.readline(MAX_LINE_BYTES + 2)
        if not chunk:
            return entries
        line = chunk[:-1] if chunk.endswith(b"\n") else chunk
        if line.endswith(b"\r"):
            line = line[:-1]
        if len(line) > MAX_LINE_BYTES:
            raise _Refuse("ledger_malformed")
        if not line.strip():
            continue
        try:
            record = _loads(line.decode("utf-8"))
        except ValueError:
            raise _Refuse("ledger_malformed")
        if not isinstance(record, dict):
            raise _Refuse("ledger_malformed")
        if "prev_sha256" not in record:
            raise _Refuse("ledger_chain_broken")
        if previous is None:
            if record["prev_sha256"] is not None:
                raise _Refuse("ledger_compacted")
        elif record["prev_sha256"] != previous:
            raise _Refuse("ledger_chain_broken")
        if "id" in record:
            if not isinstance(record["id"], str) or record["id"] in seen:
                raise _Refuse("id_duplicate")
            seen.add(record["id"])
        if len(entries) >= MAX_RECORDS:
            raise _Refuse("ledger_too_large")
        previous = hashlib.sha256(line).hexdigest()
        entries.append((record, previous))


def _window_start(entries, anchor):
    """The index of the first ledger record after ANCHOR (0 for an empty start)."""
    if anchor is None:
        return 0
    hits = [i for i, (_record, digest) in enumerate(entries) if digest == anchor]
    if not hits:
        raise _Refuse("anchor_unplaced")
    if len(hits) > 1:
        raise _Refuse("anchor_ambiguous")
    return hits[0] + 1


def _passing_count(record):
    code = record.get("exit_code")
    return (
        record.get("kind") == "executed"
        and record.get("verified") is True
        and _is_int(code) and code == 0
        and _is_int(record.get("test_count"))
    )


def _note_floor(record):
    note = record.get("test_count_drop")
    if not isinstance(note, dict) or not _passing_count(record):
        return None
    previous, current = note.get("previous"), note.get("current")
    if _is_int(previous) and _is_int(current) and current == record["test_count"] and previous > current:
        return previous
    return None


def _raised(best, record):
    for found in (record["test_count"], _note_floor(record)):
        if found is not None and (best is None or found > best):
            best = found
    return best


def _plausible_drop(baseline, current):
    return _is_int(baseline) and _is_int(current) and 0 < current < baseline


def _legacy_floor(step):
    """The baseline a historical test_drop_accepted note names, or None."""
    if "test_drop_accepted" not in step:
        return None
    note = step["test_drop_accepted"]
    if not (
        isinstance(note, dict) and _text(note.get("reason"))
        and _plausible_drop(note.get("baseline"), note.get("current"))
    ):
        raise _Refuse("drop_note_invalid")
    return note["baseline"]


def _partial_floor(step, selected, command):
    """The baseline of a restored test_drop_partial note, or None when absent."""
    if "test_drop_partial" not in step:
        return None
    note = step["test_drop_partial"]
    if not (
        isinstance(note, dict) and _text(note.get("reason"))
        and _plausible_drop(note.get("baseline"), note.get("current"))
        and _text(note.get("command")) and note["command"] == command
        and note.get("check") is None and "check_template" not in note
    ):
        raise _Refuse("drop_note_invalid")
    restored = note.get("completed_count")
    if not _is_int(restored) or restored < note["baseline"] or selected["test_count"] != restored:
        raise _Refuse("partial_not_restored")
    return note["baseline"]


def _floor(entries, upto, command, selected, step):
    """The test count the selected run is held to, or None for no floor."""
    best = None
    for record, _digest in entries[:upto]:
        if _passing_count(record) and record.get("command") == command:
            best = _raised(best, record)
    for found in (_note_floor(selected), _legacy_floor(step), _partial_floor(step, selected, command)):
        if found is not None and (best is None or found > best):
            best = found
    return best


def _defect(record, expected, current):
    """Why RECORD is not a sound, current, full-count real execution, or None."""
    if "reused_from" in record:
        return "reused"
    if record.get("campaign_refusal") is not None:
        return "refused"
    code = record.get("exit_code")
    if record.get("verified") is not True or not _is_int(code) or code != 0:
        return "failed"
    count = record.get("test_count")
    if not _is_int(count):
        return "test_count_missing"
    if count == 0:
        return "zero_tests"
    if count != expected:
        return "test_count_mismatch"
    source = record.get("provenance")
    commit = _text(source.get("git_commit")) if isinstance(source, dict) else ""
    digest = _text(source.get("worktree_digest")) if isinstance(source, dict) else ""
    if not commit or not digest:
        return "provenance_missing"
    if commit != current["git_commit"] or digest != current["worktree_digest"]:
        return "stale_source"
    return None


def _scope_defect(record, plan_name, step_id):
    in_scope = (
        record.get("plan") == plan_name
        and _is_int(record.get("step_id")) and record["step_id"] == step_id
        and record.get("step_status") == "in_progress"
    )
    return None if in_scope else "wrong_scope"


def select_completion_verification(
    state, *, plan_name, step_id, command, expected_test_count, current_provenance,
    completion_verification_id=None,
):
    """Select and judge the verification that completed a step.

    STATE is the frozen ``.mythify`` directory; the caller owns its trust
    boundary (the directory itself is opened as given, below it symlinks are
    refused). COMMAND must equal the step's stored ``verify_command`` exactly.
    CURRENT_PROVENANCE is the ``git_commit`` and ``worktree_digest`` of the
    checkout, recomputed by the trusted runtime.

    COMPLETION_VERIFICATION_ID is the trusted controller's witness: the id of
    the last executed append of the exact command that it saw before the
    successful completion. The controller and its raw trace own that
    chronology; no timestamp is read. The row is positioned by id among the
    appends after the step's anchor and judged: it must be scoped to the plan
    and step, an actual green full-count run on the current source, and meet
    the test-count floor. Later appends of the same command only confirm or
    invalidate it. Each must be an actual, green, full-count run on the same
    source, whatever its scope. The first that is not names itself as the
    invalidating id, even if a later run is green. Without the id no run is
    selected: ``completion_order_unproven`` when a plausible scoped candidate
    exists. An empty window returns ``no_completion_verification``. In a
    populated window, an id without a matching append is
    ``completion_id_unmatched``.

    Every input and evidence problem comes back as a ``reason`` with
    ``valid_evidence`` False. Unexpected implementation errors are not caught.
    """
    try:
        return _select(
            state, plan_name, step_id, command, expected_test_count,
            current_provenance, completion_verification_id,
        )
    except _Refuse as refusal:
        return _result(refusal.reason)


def _select(state, plan_name, step_id, command, expected, current, hint):
    _check_inputs(state, plan_name, step_id, command, expected, current, hint)
    step, anchor = _closed_step(state, plan_name, step_id, command)
    entries = _read_ledger(state)
    first = _window_start(entries, anchor)

    window = []
    for index in range(first, len(entries)):
        record = entries[index][0]
        if record.get("kind") == "executed" and record.get("command") == command:
            if not _text(record.get("id")):
                raise _Refuse("record_malformed")
            window.append((index, record))
    if not window:
        return _result("no_completion_verification")

    if hint is None:
        scoped = [r for _i, r in window if _scope_defect(r, plan_name, step_id) is None]
        if not scoped:
            return _result("detached_only")
        if all(_defect(r, expected, current) for r in scoped):
            return _result("all_candidates_defective")
        return _result("completion_order_unproven")

    cut = [n for n, (_i, record) in enumerate(window) if record["id"] == hint]
    if not cut:
        raise _Refuse("completion_id_unmatched")
    selected_index, selected = window[cut[0]]
    for problem in (_defect(selected, expected, current), _scope_defect(selected, plan_name, step_id)):
        if problem:
            # A defective or out-of-scope named run is never replaced by another one.
            return _result(problem, verification=selected, invalidating=selected["id"])
    try:
        floor = _floor(entries, selected_index, command, selected, step)
    except _Refuse as refusal:
        return _result(refusal.reason, verification=selected)
    if floor is not None and selected["test_count"] < floor:
        return _result("test_count_drop", verification=selected, invalidating=selected["id"])

    for _i, later in window[cut[0] + 1:]:
        problem = _defect(later, expected, current)
        if problem:
            return _result("later_" + problem, verification=selected, invalidating=later["id"])
    return _result("valid", valid=True, verification=selected)
