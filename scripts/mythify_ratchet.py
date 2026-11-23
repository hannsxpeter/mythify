"""Test-count ratchet: a check that runs fewer tests proves less.

Executed records carry a display-only test_count. These helpers compare it to
earlier passing runs of the same command so a shrinking suite is named when it
is recorded (test_count_drop on the record), at step completion (the step gate
baseline), and in `status` until a later run no longer drops. Pure functions
over parsed ledger records; nothing here prints or writes.
"""


ACCEPT_TEST_DROP_HELP = (
    "Why a lower test count than the baseline run is acceptable (for example, "
    "a deleted obsolete test). Without it a strict-context plan refuses "
    "completion when the passing run executed fewer tests than the baseline. "
    "A run that executed zero tests is never accepted this way."
)


def _passing_count(record):
    count = record.get("test_count")
    return (
        record.get("kind") == "executed"
        and record.get("verified") is True
        and record.get("exit_code") == 0
        and isinstance(count, int)
        and not isinstance(count, bool)
    )


def _command(record):
    return str(record.get("command") or "").strip()


def _note_floor(record):
    """The (count, run id) a passing record's valid test_count_drop note carries.

    Compaction can archive the high-count run an annotated partial was compared
    with; the retained note still names that count. Only a plausible note counts:
    previous is an int above the record's own count, current equals it.
    """
    note = record.get("test_count_drop")
    if not isinstance(note, dict) or not _passing_count(record):
        return None
    previous, current = note.get("previous"), note.get("current")
    if (
        isinstance(previous, int) and not isinstance(previous, bool)
        and isinstance(current, int) and not isinstance(current, bool)
        and current == record["test_count"] and previous > current
    ):
        return previous, note.get("previous_id")
    return None


def _raise_floor(best, record):
    """BEST raised by RECORD's own count and its valid drop note."""
    for found in ((record["test_count"], record.get("id")), _note_floor(record)):
        if found is not None and (best is None or found[0] > best[0]):
            best = found
    return best


def detect_test_count_drop(records, command, count):
    """The drop note for a new passing run of COMMAND with COUNT tests, or None.

    Compares with the highest earlier passing count of the same command,
    whatever step or ticket it was recorded for, so repeated partial passes
    cannot lower the baseline. A retained drop note counts as a record of the
    count it names.
    """
    wanted = str(command or "").strip()
    best = None
    for record in records:
        if _passing_count(record) and _command(record) == wanted:
            best = _raise_floor(best, record)
    if best is None or count >= best[0]:
        return None
    return {"previous": best[0], "previous_id": best[1], "current": count}


def drop_warning(command, drop):
    return (
        "[WARN] Test count dropped from {0} to {1} for '{2}' since run {3}. "
        "A check that runs fewer tests proves less.".format(
            drop["previous"], drop["current"], command, drop.get("previous_id")
        )
    )


def records_before_window(everything, window):
    """The records of EVERYTHING that precede WINDOW's first record.

    Located by record identity (equality of the parsed record), never by list
    lengths, so a record appended between two reads of the ledger cannot move
    the boundary. An unplaceable window yields no prior records.
    """
    if not window:
        return list(everything)
    for index, record in enumerate(everything):
        if record == window[0]:
            return list(everything[:index])
    return []


def step_baseline(prior_records, window_records, command, candidate):
    """The test count a step's evidence is held to, as (count, run id) or None.

    The highest passing count of COMMAND among the runs recorded before the
    step's evidence window and the window runs that precede CANDIDATE (the
    satisfying record), where a valid retained drop note counts as a record of
    the count it names. CANDIDATE's own count never raises the floor, but its
    valid note can: the only retained row may be the one that carries it.
    """
    wanted = str(command or "").strip()
    best = None
    for record in prior_records:
        if _passing_count(record) and _command(record) == wanted:
            best = _raise_floor(best, record)
    for record in window_records:
        if record is candidate or record == candidate:
            break
        if _passing_count(record) and _command(record) == wanted:
            best = _raise_floor(best, record)
    note = _note_floor(candidate)
    if note is not None and _command(candidate) == wanted and (best is None or note[0] > best[0]):
        best = note
    return best


def latest_test_count_drops(records):
    """Commands whose latest passing run ran fewer tests than an earlier one.

    Computed in one pass from observed counts, so a ledger whose second partial
    run was never annotated still shows the drop; a valid retained drop note
    also counts as the count it names, for a high-count run compaction
    archived. A reused record stands for its original, so reusing a dropped
    run does not clear it. Returns a list of (command, drop, timestamp).
    """
    by_id = {record.get("id"): record for record in records if record.get("id")}
    highest = {}
    latest = {}
    for record in records:
        if record.get("kind") != "executed" or record.get("verified") is not True:
            continue
        effective = by_id.get(record.get("reused_from"), record)
        command = _command(record)
        count = effective.get("test_count")
        latest.pop(command, None)
        latest[command] = (None, record)
        if _passing_count(effective):
            before = highest.get(command)
            best = before
            note = _note_floor(effective)
            if note is not None and (best is None or note[0] > best[0]):
                best = note
            if best is not None and count < best[0]:
                latest[command] = ({"previous": best[0], "previous_id": best[1],
                                    "current": count}, record)
            highest[command] = _raise_floor(before, effective)
    return [
        (command, drop, record.get("timestamp", ""))
        for command, (drop, record) in latest.items()
        if drop is not None
    ]
