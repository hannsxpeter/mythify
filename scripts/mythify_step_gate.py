"""The strict step evidence gate, as a pure evaluator.

evaluate_step_evidence reads the verification log and the provenance of the
current source state, then returns a verdict. It never prints and never
changes the plan, so `step ... completed` and read-only callers can share it.
"""

import os
from pathlib import Path

from mythify_campaign_scope import snapshot_provenance
from mythify_checks import LITERAL_MARKER, NAME_PATTERN, load_checks, step_check, verifier_matches
from mythify_evidence_guard import zero_test_output_reason
from mythify_io import read_jsonl, read_jsonl_after_marker, read_jsonl_since
from mythify_path_risk import judge_path_risk
from mythify_provenance import current_verification_provenance, evidence_moved_since_run
from mythify_ratchet import records_before_window, step_baseline
from mythify_runtime_helpers import timestamp_at_or_after

VERIFIED_EVIDENCE_MESSAGE = (
    "[FAIL] Verified evidence required: strict evidence mode is enabled by "
    "default, but no passing executed 'verify run' with exit code 0 was recorded "
    "since this step started. When the step stores a verify_command, the recorded "
    "command must match it. Run the step's verifier first, or set "
    "MYTHIFY_REQUIRE_VERIFIED_STEP=0 to use legacy prose-only completion."
)
STRICT_CONTEXT_NOTICE = (
    "This plan was imported with strict step context: only verifications "
    "recorded while the step was in_progress count. Mark the step in_progress, "
    "run its verify command, then complete it."
)
FALSE_ENV_VALUES = ("0", "false", "no", "off")

# Keys that scope a record to a map ticket, review, or outcome. A record
# carrying one is never pre-6.0 legacy evidence, even without step keys.
# "product" stays so ledger records written by releases that had product
# planning keep counting as scoped, never as legacy evidence for a step.
SCOPED_RECORD_KEYS = ("map", "ticket_id", "product", "review", "outcome")

_config = {"version": ""}


def configure_step_gate(version):
    _config["version"] = version


def verification_record_matches_step(record, slug, step_id):
    has_legacy_context = (
        "plan" not in record
        and "step_id" not in record
        and not any(key in record for key in SCOPED_RECORD_KEYS)
    )
    if has_legacy_context:
        return True
    return record.get("plan") == slug and record.get("step_id") == step_id


def verification_record_has_explicit_step_context(record, slug, step_id):
    return (
        "plan" in record
        and "step_id" in record
        and record.get("plan") == slug
        and record.get("step_id") == step_id
    )


def verification_record_passed(record):
    return record.get("verified") is True and record.get("exit_code") == 0


def describe_record_scope(record):
    parts = []
    if record.get("plan") is not None:
        parts.append("plan {0} step {1}".format(record["plan"], record.get("step_id")))
    if record.get("map") is not None:
        parts.append("map {0} ticket {1}".format(record["map"], record.get("ticket_id")))
    parts.extend(
        "{0} {1}".format(key, record[key])
        for key in ("review", "product", "outcome")
        if record.get(key) is not None
    )
    return ", ".join(parts) or "no plan step"


def verification_record_counts_for_step(record, slug, step_id, strict_context):
    if strict_context:
        return verification_record_has_explicit_step_context(record, slug, step_id)
    return verification_record_matches_step(record, slug, step_id)


def strict_step_evidence_enabled():
    raw = os.environ.get("MYTHIFY_REQUIRE_VERIFIED_STEP", "")
    return raw.strip().lower() not in FALSE_ENV_VALUES


def _valid_partial(note, step):
    """The step's open partial-pass note when it is well formed, else None.

    Checked against the step's own stored verifier: a named note needs a valid
    check name, a nonblank template and a step that stores that @NAME; a literal
    note needs no check fields and the step's exact stored command. Nothing in
    a note is ever used to choose or run a command.
    """
    if not isinstance(note, dict):
        return None
    baseline, current = note.get("baseline"), note.get("current")
    for value in (baseline, current):
        if not isinstance(value, int) or isinstance(value, bool):
            return None
    if not 0 < current < baseline:
        return None
    for key in ("reason", "command"):
        if not isinstance(note.get(key), str) or not note[key].strip():
            return None
    check, template = note.get("check"), note.get("check_template")
    if check is not None:
        if not isinstance(check, str) or not NAME_PATTERN.fullmatch(check):
            return None
        if not isinstance(template, str) or not template.strip():
            return None
        return note if step_check(step) == check else None
    if template is not None or step_check(step) is not None:
        return None
    stored = str(step.get("verify_command") or "").strip()
    return note if stored and stored == note["command"].strip() else None


def apply_partial_verdict(step, verdict):
    """Apply a step-completion VERDICT's partial-pass outcome to STEP in memory.

    Returns (message, record): MESSAGE is a refusal text to print or None, and
    RECORD is True when the caller must save the plan (a partial pass was
    recorded; the step stays open and the caller still refuses completion). A
    step with no stored verifier adopts the judged run's verifier, so plan
    verify, evidence matching and attribution hold to it; the note itself never
    names a command. A completed step never gains a partial. A restoring run
    keeps the audit note and adds the count that completed the step.
    """
    if verdict["partial_candidate"] is not None:
        if step.get("status") == "completed":
            return ("[FAIL] Step {0} is already completed, so no partial pass was recorded "
                    "and the step is unchanged.".format(step.get("id"))), False
        step["test_drop_partial"] = verdict["partial_candidate"]
        step.update(adopted_verifier(step, verdict["satisfying"]))
        return verdict["partial_message"], True
    if verdict["partial_restored"]:
        step["test_drop_partial"]["completed_count"] = verdict["partial_restored"]["count"]
    return None, False


def malformed_partial_message(step):
    """A refusal text when STEP holds a test_drop_partial note that fails
    _valid_partial (a present null included), else None. Never echoes the
    note's contents."""
    if "test_drop_partial" not in step or _valid_partial(step["test_drop_partial"], step) is not None:
        return None
    return ("[FAIL] Step {0} holds a malformed test_drop_partial note, so nothing was run or "
            "recorded. Repair the note's reason, baseline, current count and verifier fields "
            "to match the step, or restore the step's saved plan file, then retry.".format(
                step.get("id")))


def _same_verifier(partial, record):
    """Whether RECORD is a run of the verifier the partial was recorded for.

    A named check is held to its name and the template it had when the partial
    was recorded (its expansions, such as {files}, may differ per run); any
    other verifier is held to its literal command.
    """
    if partial.get("check"):
        return (record.get("check") == partial["check"]
                and record.get("check_template") == partial.get("check_template"))
    return str(record.get("command") or "").strip() == partial["command"]


def _partial_identity(step, record):
    """The check fields a new partial note carries for RECORD, as a dict.

    A step that stores a literal verify_command is held to that command even
    when the record carries a check field; a step with no stored verifier is
    held to the named check the run belongs to, and a step that stores @NAME to
    that check.
    """
    stored = str(step.get("verify_command") or "").strip()
    name = step_check(step) if stored else record.get("check")
    if not name or record.get("check") != name:
        return {}
    return {"check": name, "check_template": record.get("check_template")}


def adopted_verifier(step, record):
    """The step fields that make RECORD's verifier the step's stored one.

    For a step with no stored verifier only; {} otherwise. A named record
    becomes @NAME; a literal record keeps its exact command, marked literal
    when the command itself looks like @NAME so it is never read as a check.
    """
    if str(step.get("verify_command") or "").strip():
        return {}
    identity = _partial_identity(step, record)
    if identity:
        return {"verify_command": "@" + identity["check"]}
    command = str(record.get("command") or "").strip()
    fields = {"verify_command": command}
    if command.startswith("@"):
        fields[LITERAL_MARKER] = True
    return fields


def _block(result, text):
    result["ok"] = False
    result["partial_blocked"] = text
    result["messages"].append(("fail", "[FAIL] " + text))


def _judge_test_count(state, result, window, accept_test_drop, step):
    """Hold the satisfying record's test count to the step's baseline.

    Sets result["test_drop"] (a dict, or None) and result["partial_candidate"]
    (the note a caller records when ACCEPT_TEST_DROP carries a reason; the
    step is then refused, never completed). A drop warns, and under strict
    context refuses. A step that already holds a partial pass (test_drop_partial)
    is refused, in any mode, until a run of the same verifier reaches the
    recorded floor. A run that executed zero tests (a recorded count of 0, or
    recognized zero-test output when no count was read) is always refused and
    sets result["zero_tests"]; ACCEPT_TEST_DROP never waives it.
    """
    result["test_drop"] = result["partial_candidate"] = result["zero_tests"] = None
    result["partial_blocked"] = result["partial_restored"] = None
    result["partial_message"] = None
    satisfying = result["satisfying"]
    count = satisfying.get("test_count")
    has_count = isinstance(count, int) and not isinstance(count, bool)
    zero = "the recorded test count is 0" if has_count and count == 0 else (
        None if has_count else zero_test_output_reason(satisfying))
    if zero:
        # No baseline and no reason can make a run that tested nothing count.
        result["zero_tests"] = zero
        result["ok"] = False
        result["messages"].append((
            "fail",
            "[FAIL] The passing run executed zero tests ({0}) for '{1}': a green exit "
            "code that ran nothing proves nothing. Fix the test selection and re-run "
            "the verifier.".format(zero, str(satisfying.get("command") or "").strip()),
        ))
        return
    partial = _valid_partial(step.get("test_drop_partial"), step)
    if "test_drop_partial" in step and partial is None:
        _block(result, "The step's test_drop_partial note is malformed, so its test floor "
                       "cannot be read. Repair or remove it, then re-run the verifier.")
        return
    if partial is not None and not _same_verifier(partial, satisfying):
        if partial.get("check") and satisfying.get("check") == partial["check"]:
            _block(result, "This step holds a partial pass (floor {0}) for the check {1} as it "
                           "was defined then ({2}). The check was redefined, so its runs do not "
                           "count: restore that definition with verify check {1} --set, then "
                           "re-run it at the floor.".format(
                               partial["baseline"], partial["check"], partial.get("check_template")))
        else:
            _block(result, "This step holds a partial pass for '{0}' (floor {1}). The passing run "
                           "is of a different verifier; re-run the same verifier at the floor."
                           .format(partial["command"], partial["baseline"]))
        return
    if partial is not None and not has_count:
        _block(result, "This step holds a partial pass for '{0}' (floor {1}). The passing run "
                       "reported no test count, so the floor cannot be checked; re-run the "
                       "verifier.".format(partial["command"], partial["baseline"]))
        return
    if not has_count:
        return
    everything = read_jsonl(Path(state) / "verifications.jsonl")
    prior = records_before_window(everything, window)
    found = step_baseline(prior, window, satisfying.get("command"), satisfying)
    if partial is not None and (found is None or partial["baseline"] > found[0]):
        found = (partial["baseline"], partial.get("baseline_id"))
    if found is None or count >= found[0]:
        if partial is not None:
            result["partial_restored"] = {"count": count}
        return
    baseline, baseline_id = found
    command = str(satisfying.get("command") or "").strip()
    result["test_drop"] = {"command": command, "baseline": baseline,
                           "baseline_id": baseline_id, "current": count}
    detail = "{0} test(s) ran, below the baseline of {1} (run {2}) for '{3}'".format(
        count, baseline, baseline_id, command)
    reason = str(accept_test_drop or "").strip()
    if reason:
        note = {"reason": reason, "baseline": baseline, "baseline_id": baseline_id,
                "current": count, "command": command}
        note.update(_partial_identity(step, satisfying))
        result["partial_candidate"] = note
        result["ok"] = False
        # Said by the caller only after the note is saved (partial_candidate kept).
        result["partial_message"] = (
            "[FAIL] Partial pass recorded ({0}): {1}. Stamped on the step as "
            "test_drop_partial; the step stays open and completion is refused. Restore "
            "the tests and re-run the verifier until it reaches {2} test(s), then "
            "complete.".format(reason, detail, baseline))
    elif partial is not None:
        _block(result, "This step holds a partial pass: {0}. Restore the tests and re-run "
                       "the verifier until it reaches the floor; --accept-test-drop REASON "
                       "records another partial but never completes.".format(detail))
    elif result["strict_context"]:
        result["ok"] = False
        result["messages"].append((
            "fail",
            "[FAIL] Test count dropped under strict context: {0}. A check that "
            "runs fewer tests proves less. Restore the tests, or record a partial "
            "pass with --accept-test-drop REASON (the step stays open).".format(detail),
        ))
    else:
        result["messages"].append((
            "fail",
            "[WARN] Test count dropped: {0}. A check that runs fewer tests "
            "proves less; pass --accept-test-drop REASON to record a partial "
            "pass.".format(detail),
        ))


def evaluate_step_evidence(
    state, slug, plan, step, accept_test_drop=None, human_input=None,
    *, scope_snapshot=None, scope_paths=None,
):
    """Judge whether STEP of PLAN has passing executed evidence.

    Returns a dict: ok (bool, False means refuse), satisfying (the latest
    passing record, or None), messages (list of (level, text) in print order;
    level is "fail" for lines meant for stderr), moved (staleness reason or
    None), strict_context (bool), test_drop (the baseline comparison when the
    satisfying run executed fewer tests, else None), zero_tests (why the
    satisfying run executed none, else None; always refused), partial_candidate
    (the test_drop_partial note to record when ACCEPT_TEST_DROP gave a reason and
    the run is current; ok is False then), partial_blocked (why an open partial
    pass refuses, else None), partial_restored (set when a run reached an open
    partial's floor), and risk
    (the high-risk path verdict from judge_path_risk, judged with HUMAN_INPUT;
    None when the evidence is already refused). Pure: no printing, no plan
    changes. A read-only caller passes no reason and no human input.
    """
    step_id = step.get("id")
    strict_context = bool(plan.get("strict_context"))
    result = {"ok": True, "satisfying": None, "messages": [], "moved": None,
              "strict_context": strict_context, "test_drop": None, "zero_tests": None,
              "partial_candidate": None, "partial_blocked": None,
              "partial_restored": None, "partial_message": None, "risk": None}
    messages = result["messages"]
    expected_command = str(step.get("verify_command") or "").strip()
    # An @NAME verifier is met by any run of that check: its expanded command
    # text can differ from run to run, so the record's check field decides.
    expected_check = step_check(step)
    held = "test_drop_partial" in step
    # A redefined check is a different check: only runs of its current command count.
    current_template = (load_checks(state).get(expected_check) or {}).get("command") if expected_check else None
    changed_runs = []
    lower_bound = step.get("updated_at") or plan.get("created", "")
    records = read_jsonl_after_marker(
        Path(state) / "verifications.jsonl",
        # A step completed straight from pending has no anchor of its own;
        # the plan's anchor keeps an older plan's records out.
        anchor=step.get("verification_anchor") or plan.get("verification_anchor"),
        legacy_cursor=step.get("verification_cursor"),
        lower_bound=lower_bound,
    )
    if records is None:
        records = read_jsonl_since(Path(state) / "verifications.jsonl", lower_bound)
    # The latest run of each command since the step started decides it,
    # whatever plan, step, ticket, review, or outcome the run is stamped
    # to, so a pass followed by a failing run never completes on red. Only
    # a passing run scoped to this step can satisfy the gate.
    latest, scoped = {}, set()
    for record in records:
        command = str(record.get("command") or "").strip()
        if expected_check:
            # Runs of the check share one key, whatever command text they expanded to.
            wrong_command, command = record.get("check") != expected_check, expected_command
        else:
            wrong_command = bool(expected_command) and not verifier_matches(
                expected_command, command, record.get("check"), step.get(LITERAL_MARKER) is True)
        if record.get("kind") != "executed" or wrong_command or not timestamp_at_or_after(
            record.get("timestamp", ""), lower_bound, True
        ):
            continue
        if expected_check and record.get("check_template") != current_template:
            if verification_record_passed(record):
                changed_runs.append(record)
            continue
        latest.pop(command, None)
        latest[command] = record
        if (
            verification_record_passed(record)
            and verification_record_counts_for_step(record, slug, step_id, strict_context)
            and timestamp_at_or_after(
                record.get("timestamp", ""),
                lower_bound,
                verification_record_has_explicit_step_context(record, slug, step_id),
            )
        ):
            scoped.add(command)
    satisfying = [
        record
        for command, record in latest.items()
        if command in scoped and verification_record_passed(record)
    ]
    if held:
        # A recorded partial is closed only by a run stamped to this very step:
        # a run for another plan or step never stands in, though a later failing
        # run anywhere still cancels (latest above).
        satisfying = [r for r in satisfying
                      if verification_record_has_explicit_step_context(r, slug, step_id)]
    if not satisfying:
        result["ok"] = False
        messages.append(("fail", VERIFIED_EVIDENCE_MESSAGE))
        failing = [
            record for command, record in latest.items()
            if (command in scoped or expected_command)
            and not verification_record_passed(record)
        ]
        elsewhere = [r for r in latest.values() if verification_record_passed(r)]
        if failing:
            messages.append((
                "fail",
                "The latest run of '{0}' since this step started exited {1} "
                "(recorded for {2}); a failing run cancels an earlier pass of "
                "the same command wherever it was recorded. Fix the cause, then "
                "re-run it.".format(
                    failing[-1].get("command"), failing[-1].get("exit_code"),
                    describe_record_scope(failing[-1]),
                ),
            ))
        elif elsewhere:
            messages.append((
                "fail",
                "The passing run of '{0}' since this step started was recorded "
                "for {1}, not plan {2} step {3}: a run counts for the "
                "in-progress step whose verify_command matches it, else the "
                "first in-progress step of the active plan.{4}".format(
                    elsewhere[-1].get("command"), describe_record_scope(elsewhere[-1]),
                    slug, step_id,
                    " Record one for this step with: plan verify {0} --plan {1}".format(
                        step_id, slug) if step.get("verify_command") else "",
                ),
            ))
        if changed_runs:
            messages.append((
                "fail",
                "The check {0} changed since the run{1}: runs of an earlier command do not "
                "count. Run it again: {2}".format(
                    expected_command,
                    "" if current_template else " (it is no longer defined)",
                    "plan verify {0} --plan {1}".format(step_id, slug) if step.get("verify_command")
                    else "verify check {0}".format(expected_check)),
            ))
        if strict_context:
            messages.append(("fail", STRICT_CONTEXT_NOTICE))
        return result
    result["satisfying"] = satisfying[-1]
    if scope_snapshot is None:
        current = current_verification_provenance(_config["version"], state=state)
        moved = evidence_moved_since_run(satisfying[-1], current)
    else:
        current = snapshot_provenance(scope_snapshot)
        moved = evidence_moved_since_run(satisfying[-1], current)
        if not (satisfying[-1].get("provenance") or {}).get("worktree_digest"):
            moved = "campaign_source_was_not_inspected"
    result["moved"] = moved
    if moved and strict_context:
        result["ok"] = False
        messages.append((
            "fail",
            "[FAIL] Stale evidence under strict context: {0}. The passing "
            "run predates the current source state; re-run the step's "
            "verifier, then complete.".format(moved),
        ))
    elif moved and "test_drop_partial" in step:
        # A recorded partial pass is only closed by current evidence, in any mode.
        result["ok"] = False
        messages.append((
            "fail",
            "[FAIL] Stale evidence for a recorded partial pass: {0}. The passing run "
            "predates the current source state; re-run the verifier at the floor, "
            "then complete.".format(moved),
        ))
    elif moved:
        messages.append((
            "fail",
            "[WARN] The world moved since the passing run ({0}); the "
            "evidence may not describe the current source state. Re-run "
            "the verifier if in doubt.".format(moved),
        ))
    _judge_test_count(state, result, records, accept_test_drop, step)
    result["risk"] = judge_path_risk(state, plan, step, human_input, path_changes=scope_paths)
    result["messages"].extend(result["risk"]["messages"])
    result["ok"] = result["ok"] and result["risk"]["ok"]
    if result["partial_candidate"] is not None and (moved or not result["risk"]["ok"]):
        # Only a current run with no other refusal can be recorded as a partial.
        result["partial_candidate"] = result["partial_message"] = None
        messages.append((
            "fail",
            "[FAIL] No partial pass was recorded and the step is unchanged: {0}.".format(
                "the passing run predates the current source state; re-run the verifier"
                if moved else "the high-risk path check refused it; resolve that first"),
        ))
    return result
