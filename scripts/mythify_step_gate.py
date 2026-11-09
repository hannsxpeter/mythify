"""The strict step evidence gate, as a pure evaluator.

evaluate_step_evidence reads the verification log and the provenance of the
current source state, then returns a verdict. It never prints and never
changes the plan, so `step ... completed` and read-only callers can share it.
"""

import os
from pathlib import Path

from mythify_io import read_jsonl_after_marker, read_jsonl_since
from mythify_provenance import current_verification_provenance, evidence_moved_since_run
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

# Keys that scope a record to a map ticket, product, review, or outcome. A
# record carrying one is never pre-6.0 legacy evidence, even without step keys.
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


def evaluate_step_evidence(state, slug, plan, step):
    """Judge whether STEP of PLAN has passing executed evidence.

    Returns a dict: ok (bool, False means refuse), satisfying (the latest
    passing record, or None), messages (list of (level, text) in print order;
    level is "fail" for lines meant for stderr), moved (staleness reason or
    None), and strict_context (bool). Pure: no printing, no plan changes.
    """
    step_id = step.get("id")
    strict_context = bool(plan.get("strict_context"))
    result = {"ok": True, "satisfying": None, "messages": [], "moved": None,
              "strict_context": strict_context}
    messages = result["messages"]
    expected_command = str(step.get("verify_command") or "").strip()
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
        if record.get("kind") != "executed" or (
            expected_command and command != expected_command
        ) or not timestamp_at_or_after(record.get("timestamp", ""), lower_bound, True):
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
    return result
