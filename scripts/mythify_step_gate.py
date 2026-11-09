"""The strict step evidence gate, as a pure evaluator.

evaluate_step_evidence reads the verification log and the provenance of the
current source state, then returns a verdict. It never prints and never
changes the plan, so `step ... completed` and read-only callers can share it.
"""

import os
from pathlib import Path

from mythify_checks import check_reference, load_checks
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


def _judge_test_count(state, result, window, accept_test_drop):
    """Hold the satisfying record's test count to the step's baseline.

    Sets result["test_drop"] (a dict, or None) and result["test_drop_accepted"]
    (the dict a caller stamps on the step, or None). A drop warns, and under
    strict context refuses unless ACCEPT_TEST_DROP carries a reason.
    """
    result["test_drop"] = result["test_drop_accepted"] = None
    satisfying = result["satisfying"]
    count = satisfying.get("test_count")
    if not isinstance(count, int) or isinstance(count, bool):
        return
    everything = read_jsonl(Path(state) / "verifications.jsonl")
    prior = records_before_window(everything, window)
    found = step_baseline(prior, window, satisfying.get("command"))
    if found is None or count >= found[0]:
        return
    baseline, baseline_id = found
    command = str(satisfying.get("command") or "").strip()
    result["test_drop"] = {"command": command, "baseline": baseline,
                           "baseline_id": baseline_id, "current": count}
    detail = "{0} test(s) ran, below the baseline of {1} (run {2}) for '{3}'".format(
        count, baseline, baseline_id, command)
    reason = str(accept_test_drop or "").strip()
    if reason:
        result["test_drop_accepted"] = {"reason": reason, "baseline": baseline, "current": count}
        result["messages"].append((
            "fail",
            "[WARN] Test count drop accepted ({0}): {1}. Stamped on the step as "
            "test_drop_accepted.".format(reason, detail),
        ))
    elif result["strict_context"]:
        result["ok"] = False
        result["messages"].append((
            "fail",
            "[FAIL] Test count dropped under strict context: {0}. A check that "
            "runs fewer tests proves less. Restore the tests, or complete with "
            "--accept-test-drop REASON to say why fewer is right.".format(detail),
        ))
    else:
        result["messages"].append((
            "fail",
            "[WARN] Test count dropped: {0}. A check that runs fewer tests "
            "proves less; pass --accept-test-drop REASON to record why.".format(detail),
        ))


def evaluate_step_evidence(state, slug, plan, step, accept_test_drop=None, human_input=None):
    """Judge whether STEP of PLAN has passing executed evidence.

    Returns a dict: ok (bool, False means refuse), satisfying (the latest
    passing record, or None), messages (list of (level, text) in print order;
    level is "fail" for lines meant for stderr), moved (staleness reason or
    None), strict_context (bool), test_drop (the baseline comparison when the
    satisfying run executed fewer tests, else None), and test_drop_accepted
    (what to stamp on the step when ACCEPT_TEST_DROP gave a reason), and risk
    (the high-risk path verdict from judge_path_risk, judged with HUMAN_INPUT;
    None when the evidence is already refused). Pure: no printing, no plan
    changes. A read-only caller passes no reason and no human input.
    """
    step_id = step.get("id")
    strict_context = bool(plan.get("strict_context"))
    result = {"ok": True, "satisfying": None, "messages": [], "moved": None,
              "strict_context": strict_context, "test_drop": None,
              "test_drop_accepted": None, "risk": None}
    messages = result["messages"]
    expected_command = str(step.get("verify_command") or "").strip()
    # An @NAME verifier is met by any run of that check: its expanded command
    # text can differ from run to run, so the record's check field decides.
    expected_check = check_reference(expected_command)
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
            wrong_command = bool(expected_command) and command != expected_command
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
                        step_id, slug) if expected_command else "",
                ),
            ))
        if changed_runs:
            messages.append((
                "fail",
                "The check {0} changed since the run{1}: runs of an earlier command do not "
                "count. Run it again: plan verify {2} --plan {3}".format(
                    expected_command,
                    "" if current_template else " (it is no longer defined)", step_id, slug),
            ))
        if strict_context:
            messages.append(("fail", STRICT_CONTEXT_NOTICE))
        return result
    result["satisfying"] = satisfying[-1]
    moved = evidence_moved_since_run(
        satisfying[-1], current_verification_provenance(_config["version"], state=state)
    )
    result["moved"] = moved
    if moved and strict_context:
        result["ok"] = False
        messages.append((
            "fail",
            "[FAIL] Stale evidence under strict context: {0}. The passing "
            "run predates the current source state; re-run the step's "
            "verifier, then complete.".format(moved),
        ))
    elif moved:
        messages.append((
            "fail",
            "[WARN] The world moved since the passing run ({0}); the "
            "evidence may not describe the current source state. Re-run "
            "the verifier if in doubt.".format(moved),
        ))
    _judge_test_count(state, result, records, accept_test_drop)
    result["risk"] = judge_path_risk(state, plan, step, human_input)
    result["messages"].extend(result["risk"]["messages"])
    result["ok"] = result["ok"] and result["risk"]["ok"]
    return result
