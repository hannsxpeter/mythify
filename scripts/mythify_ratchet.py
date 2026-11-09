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
    "completion when the passing run executed fewer tests than the baseline."
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


def detect_test_count_drop(records, command, count):
    """The drop note for a new passing run of COMMAND with COUNT tests, or None.

    Compares with the latest earlier passing run of the same command that has a
    test count, whatever step or ticket it was recorded for.
    """
    wanted = str(command or "").strip()
    previous = None
    for record in records:
        if _passing_count(record) and _command(record) == wanted:
            previous = record
    if previous is None or count >= previous["test_count"]:
        return None
    return {"previous": previous["test_count"], "previous_id": previous.get("id"), "current": count}


def drop_warning(command, drop):
    return (
        "[WARN] Test count dropped from {0} to {1} for '{2}' since run {3}. "
        "A check that runs fewer tests proves less.".format(
            drop["previous"], drop["current"], command, drop.get("previous_id")
        )
    )


def step_baseline(prior_records, window_records, command):
    """The test count a step's evidence is held to, as (count, run id) or None.

    The most recent passing run of COMMAND recorded before the step's evidence
    window started; with none, the first passing run inside the window.
    """
    wanted = str(command or "").strip()
    for records in (reversed(list(prior_records)), window_records):
        for record in records:
            if _passing_count(record) and _command(record) == wanted:
                return record["test_count"], record.get("id")
    return None


def latest_test_count_drops(records):
    """Commands whose latest passing run carries test_count_drop, in log order.

    A reused record stands for its original, so reusing a dropped run does not
    clear the note. Returns a list of (command, drop, timestamp).
    """
    by_id = {record.get("id"): record for record in records if record.get("id")}
    latest = {}
    for record in records:
        if record.get("kind") != "executed" or record.get("verified") is not True:
            continue
        effective = by_id.get(record.get("reused_from"), record)
        latest.pop(_command(record), None)
        latest[_command(record)] = (effective, record)
    return [
        (command, effective["test_count_drop"], record.get("timestamp", ""))
        for command, (effective, record) in latest.items()
        if isinstance(effective.get("test_count_drop"), dict)
    ]
