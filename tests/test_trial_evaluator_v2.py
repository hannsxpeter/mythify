"""Version 2 trial evaluator: which verification completed the step."""

import copy
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import mythify_io  # noqa: E402
import trial_evaluator_v2 as v2  # noqa: E402

PLAN = "demo-plan"
STEP = 1
CMD = "python3 -m unittest tests.test_demo"
EXPECTED = 5
SRC = {"git_commit": "c" * 40, "worktree_digest": "d" * 64}


def run(ident, **over):
    """An executed, green, scoped, full-count, current ledger record.

    It carries no timestamp: the evaluator reads no clock, and the boundary
    comes from the controller's id, never from the position of a time.
    """
    record = {
        "id": ident, "kind": "executed", "command": CMD, "exit_code": 0,
        "verified": True, "test_count": EXPECTED,
        "provenance": dict(SRC), "plan": PLAN, "step_id": STEP,
        "step_title": "t", "step_status": "in_progress",
    }
    record.update(over)
    return record


def detached(ident, **over):
    fields = {"plan": None, "step_id": None, "step_title": None, "step_status": None}
    fields.update(over)
    return run(ident, **fields)


def line_of(record):
    return json.dumps(record, allow_nan=False)


class Case(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.state = Path(self._dir.name) / ".mythify"
        (self.state / "plans").mkdir(parents=True)

    def build(self, records, pre=(), step=None, plan=None, raw_lines=None, anchor="auto"):
        """Write a chained ledger (PRE, then RECORDS) and a closed plan."""
        lines = [line_of(dict(item)) for item in (list(pre) + list(records))]
        chained, previous = [], None
        for text in lines:
            record = json.loads(text)
            record["prev_sha256"] = (
                hashlib.sha256(previous.encode("utf-8")).hexdigest() if previous else None
            )
            previous = line_of(record)
            chained.append(previous)
        if raw_lines is not None:
            chained = raw_lines(chained)
        (self.state / "verifications.jsonl").write_text(
            "".join(text + "\n" for text in chained), encoding="utf-8")
        if anchor == "auto":
            count = len(pre)
            anchor = {"after_sha256": hashlib.sha256(chained[count - 1].encode("utf-8")).hexdigest()
                      if count else None}
        body = {
            "id": STEP, "title": "t", "status": "completed", "result": "verify run exit 0",
            "verify_command": CMD, "verification_anchor": anchor,
        }
        body.update(step or {})
        for key in [key for key, value in body.items() if value is KeyError]:
            del body[key]
        document = {"name": PLAN, "goal": "g", "steps": [body]}
        document.update(plan or {})
        (self.state / "plans" / (PLAN + ".json")).write_text(json.dumps(document), encoding="utf-8")
        return chained

    def judge(self, hint=None, **over):
        """Judge with the controller's boundary id (None means the controller gave none)."""
        arguments = {"plan_name": PLAN, "step_id": STEP, "command": CMD,
                     "expected_test_count": EXPECTED, "current_provenance": dict(SRC),
                     "completion_verification_id": hint}
        arguments.update(over)
        return v2.select_completion_verification(self.state, **arguments)

    def expect(self, outcome, reason, ident=None, valid=False, invalidating=None):
        """Compare the whole result: a row is always attributed by controller order."""
        self.assertEqual(
            {"evaluator_version": 2, "verification_id": ident, "reason": reason,
             "valid_evidence": valid, "invalidating_verification_id": invalidating,
             "attribution_source": None if ident is None else v2.BY_CONTROLLER},
            {key: outcome[key] for key in (
                "evaluator_version", "verification_id", "reason", "valid_evidence",
                "invalidating_verification_id", "attribution_source")})
        if ident is None:
            self.assertIsNone(outcome["verification"])
        else:
            self.assertEqual(ident, outcome["verification"]["id"])


class CoreControls(Case):
    def test_clean_detached_confirmation_keeps_the_scoped_run(self):
        self.build([run("v-scoped"), detached("v-detached")])
        self.expect(self.judge("v-scoped"), "valid", "v-scoped", valid=True)

    def test_detached_only_fails(self):
        self.build([detached("v-detached")])
        self.expect(self.judge(), "detached_only")
        self.expect(self.judge("v-detached"), "wrong_scope", "v-detached", invalidating="v-detached")

    def test_a_plausible_green_run_without_the_controller_id_is_declined(self):
        self.build([run("v-scoped"), detached("v-detached")])
        self.expect(self.judge(), "completion_order_unproven")

    def test_all_defective_scoped_candidates_without_the_id_are_false(self):
        self.build([run("v-1", exit_code=1, verified=False), run("v-2", test_count=0),
                    detached("v-3")])
        self.expect(self.judge(), "all_candidates_defective")

    def test_interface_has_no_clock_or_serial_switch(self):
        parameters = list(inspect.signature(v2.select_completion_verification).parameters)
        self.assertEqual(
            ["state", "plan_name", "step_id", "command", "expected_test_count",
             "current_provenance", "completion_verification_id"], parameters)
        self.assertFalse(hasattr(v2, "BY_TIMESTAMP"))
        self.assertNotIn("datetime", Path(v2.__file__).read_text(encoding="ascii"))

    def test_result_shape_and_version(self):
        self.build([run("v-scoped")])
        outcome = self.judge("v-scoped")
        self.assertEqual(2, v2.EVALUATOR_VERSION)
        self.assertEqual(
            ["attribution_source", "evaluator_version", "invalidating_verification_id", "reason",
             "valid_evidence", "verification", "verification_id"], sorted(outcome))
        self.assertEqual("controller_event_order", outcome["attribution_source"])

    def test_v1_semantics_would_attribute_the_detached_run(self):
        # Version 1 as briefed: the latest exact-command run after the step
        # started, then checked for scope. A confirmation after completion
        # replaces the scoped run and the completion looks unscoped.
        records = [run("v-scoped"), detached("v-detached")]
        self.build(records)
        latest = [r for r in records if r["command"] == CMD][-1]
        self.assertEqual("v-detached", latest["id"])
        self.assertIsNone(latest["plan"])
        self.expect(self.judge("v-scoped"), "valid", "v-scoped", valid=True)


class Boundary(Case):
    def test_the_id_alone_decides_whatever_the_timestamps_say(self):
        # Clocks rolled back: the named run is stamped after, the later runs before it.
        rolled = [
            run("closed", timestamp="2099-01-01T00:00:00+00:00"),
            detached("late", timestamp="1970-01-01T00:00:00+00:00"),
            detached("later", timestamp="not a time"),
            detached("latest", timestamp=None),
        ]
        self.build(rolled)
        self.expect(self.judge("closed"), "valid", "closed", valid=True)

    def test_a_later_bad_run_invalidates_even_if_stamped_earlier_or_followed_by_green(self):
        self.build([
            run("closed", timestamp="2099-01-01T00:00:00+00:00"),
            detached("late-red", exit_code=1, verified=False, timestamp="1970-01-01T00:00:00+00:00"),
            detached("late-green"),
        ])
        self.expect(self.judge("closed"), "later_failed", "closed", invalidating="late-red")

    def test_the_named_row_is_not_replaced_by_an_earlier_or_later_green_one(self):
        self.build([run("v-1"), run("v-2", exit_code=1, verified=False), run("v-3")])
        self.expect(self.judge("v-2"), "failed", "v-2", invalidating="v-2")
        self.expect(self.judge("v-1"), "later_failed", "v-1", invalidating="v-2")
        self.expect(self.judge("v-3"), "valid", "v-3", valid=True)

    def test_earlier_appends_before_the_named_row_do_not_count_against_it(self):
        self.build([run("v-0", exit_code=1, verified=False), run("v-1")])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)

    def test_ids_that_name_no_candidate_are_unmatched(self):
        self.build([run("v-1"), run("v-2", command="other"),
                    {"id": "v-claim", "kind": "claim", "command": CMD}], pre=[run("v-old")])
        for label, hint in (("unknown", "v-nope"), ("pre-anchor", "v-old"),
                            ("other command", "v-2"), ("not executed", "v-claim")):
            with self.subTest(label):
                self.expect(self.judge(hint), "completion_id_unmatched")

    def test_unrelated_and_non_executed_records_are_ignored(self):
        self.build([
            run("v-1"),
            run("v-x", command="python3 other.py", exit_code=1, verified=False),
            {"id": "v-claim", "kind": "claim", "command": CMD},
        ])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)


class SelectedRun(Case):
    def test_defects_of_the_named_run(self):
        cases = [
            ("failed", dict(exit_code=1, verified=False), "failed"),
            ("unverified", dict(verified=False), "failed"),
            ("bool exit code", dict(exit_code=False), "failed"),
            ("zero tests", dict(test_count=0), "zero_tests"),
            ("partial count", dict(test_count=3), "test_count_mismatch"),
            ("excess count", dict(test_count=9), "test_count_mismatch"),
            ("missing count", dict(test_count=None), "test_count_missing"),
            ("bool count", dict(test_count=True), "test_count_missing"),
            ("reused", dict(reused_from="v-orig"), "reused"),
            ("refusal", dict(campaign_refusal="changed"), "refused"),
            ("foreign plan", dict(plan="other-plan"), "wrong_scope"),
            ("other step", dict(step_id=2), "wrong_scope"),
            ("no scope", dict(plan=None, step_id=None, step_status=None), "wrong_scope"),
            ("step not in progress", dict(step_status="pending"), "wrong_scope"),
            ("stale commit", dict(provenance={"git_commit": "e" * 40, "worktree_digest": "d" * 64}),
             "stale_source"),
            ("stale digest", dict(provenance={"git_commit": "c" * 40, "worktree_digest": "f" * 64}),
             "stale_source"),
            ("blank digest", dict(provenance={"git_commit": "c" * 40, "worktree_digest": ""}),
             "provenance_missing"),
            ("no provenance", dict(provenance=None), "provenance_missing"),
        ]
        for label, over, reason in cases:
            with self.subTest(label):
                self.build([run("v-1", **over)])
                self.expect(self.judge("v-1"), reason, "v-1", invalidating="v-1")

    def test_current_provenance_override_differs_from_the_record(self):
        self.build([run("v-1")])
        other = {"git_commit": "c" * 40, "worktree_digest": "9" * 64}
        self.expect(self.judge("v-1", current_provenance=other), "stale_source", "v-1",
                    invalidating="v-1")


class LaterRuns(Case):
    def test_later_runs_that_invalidate(self):
        cases = [
            ("failing", dict(exit_code=1, verified=False), "later_failed"),
            ("reused", dict(reused_from="v-1"), "later_reused"),
            ("refused", dict(campaign_refusal="x"), "later_refused"),
            ("zero tests", dict(test_count=0), "later_zero_tests"),
            ("reduced", dict(test_count=3), "later_test_count_mismatch"),
            ("stale", dict(provenance={"git_commit": "c" * 40, "worktree_digest": "0" * 64}),
             "later_stale_source"),
            ("no provenance", dict(provenance={}), "later_provenance_missing"),
        ]
        for label, over, reason in cases:
            with self.subTest(label):
                self.build([run("v-1"), detached("v-2", **over)])
                self.expect(self.judge("v-1"), reason, "v-1", invalidating="v-2")

    def test_later_green_does_not_rescue_an_earlier_bad_later_run(self):
        self.build([run("v-1"), detached("v-2", exit_code=1, verified=False), detached("v-3")])
        self.expect(self.judge("v-1"), "later_failed", "v-1", invalidating="v-2")

    def test_the_first_bad_later_run_is_named(self):
        self.build([run("v-1"), detached("v-2", test_count=0),
                    detached("v-3", exit_code=1, verified=False)])
        self.expect(self.judge("v-1"), "later_zero_tests", "v-1", invalidating="v-2")

    def test_later_unrelated_failure_is_ignored(self):
        self.build([run("v-1"), detached("v-2", command="other", exit_code=1, verified=False)])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)

    def test_later_green_in_another_scope_still_confirms(self):
        self.build([run("v-1"), run("v-2", plan="other-plan", step_id=4)])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)


class Ratchet(Case):
    def test_pre_anchor_high_water_count_holds_the_run(self):
        self.build([run("v-1")], pre=[run("v-old", plan=None, step_id=None, test_count=8)])
        self.expect(self.judge("v-1"), "test_count_drop", "v-1", invalidating="v-1")

    def test_window_high_water_count_before_the_named_run_holds_it(self):
        self.build([detached("v-hi", test_count=8), run("v-1")])
        self.expect(self.judge("v-1"), "test_count_drop", "v-1", invalidating="v-1")

    def test_a_high_count_after_the_named_run_is_no_floor(self):
        self.build([run("v-1"), detached("v-hi", test_count=8)])
        self.expect(self.judge("v-1"), "later_test_count_mismatch", "v-1", invalidating="v-hi")

    def test_lower_earlier_counts_do_not_matter(self):
        self.build([run("v-lo", test_count=2), run("v-1")], pre=[run("v-old", test_count=4)])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)

    def test_retained_drop_note_stands_for_an_archived_run(self):
        note = {"previous": 8, "current": EXPECTED, "previous_id": "v-gone"}
        self.build([run("v-1", test_count_drop=note)])
        self.expect(self.judge("v-1"), "test_count_drop", "v-1", invalidating="v-1")

    def test_implausible_note_is_ignored(self):
        note = {"previous": 3, "current": EXPECTED}
        self.build([run("v-1", test_count_drop=note)])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)

    def test_restored_partial_pass_at_the_floor_is_accepted(self):
        self.build([run("v-1")], step={"test_drop_partial": {
            "baseline": EXPECTED, "current": 3, "reason": "r", "command": CMD,
            "completed_count": EXPECTED}})
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)

    def test_partial_not_tied_to_the_named_run_is_refused(self):
        cases = [
            ("absent", None), ("below floor", 3), ("text", "5"),
            ("other count than the run", 8),
        ]
        for label, restored in cases:
            with self.subTest(label):
                partial = {"baseline": EXPECTED, "current": 3, "reason": "r", "command": CMD}
                if restored is not None:
                    partial["completed_count"] = restored
                self.build([run("v-1")], step={"test_drop_partial": partial})
                self.expect(self.judge("v-1"), "partial_not_restored", "v-1")

    def test_malformed_partial_or_legacy_note_is_refused(self):
        good = {"baseline": EXPECTED, "current": 3, "reason": "r", "command": CMD,
                "completed_count": EXPECTED}
        cases = [
            ("partial text", "test_drop_partial", "restored"),
            ("partial null", "test_drop_partial", None),
            ("partial blank reason", "test_drop_partial", dict(good, reason=" ")),
            ("partial other command", "test_drop_partial", dict(good, command="other")),
            ("partial no command", "test_drop_partial", {k: v for k, v in good.items() if k != "command"}),
            ("partial not a drop", "test_drop_partial", dict(good, current=EXPECTED)),
            ("partial zero current", "test_drop_partial", dict(good, current=0)),
            ("partial bool baseline", "test_drop_partial", dict(good, baseline=True)),
            ("partial named check", "test_drop_partial", dict(good, check="fast", check_template="x")),
            ("legacy text", "test_drop_accepted", "obsolete tests"),
            ("legacy no reason", "test_drop_accepted", {"baseline": 8, "current": 5}),
            ("legacy not a drop", "test_drop_accepted", {"reason": "r", "baseline": 5, "current": 5}),
        ]
        for label, key, note in cases:
            with self.subTest(label):
                self.build([run("v-1")], step={key: note})
                self.expect(self.judge("v-1"), "drop_note_invalid", "v-1")

    def test_legacy_accepted_drop_note_still_holds_its_baseline(self):
        # No earlier full-count run exists: only the historical note names 8.
        self.build([run("v-1")], step={"test_drop_accepted": {
            "reason": "obsolete tests", "baseline": 8, "current": EXPECTED}})
        self.expect(self.judge("v-1"), "test_count_drop", "v-1", invalidating="v-1")

    def test_legacy_note_at_or_below_the_run_is_not_a_drop(self):
        self.build([run("v-1")], step={"test_drop_accepted": {
            "reason": "obsolete tests", "baseline": EXPECTED, "current": 3}})
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)


class PlanAndStep(Case):
    def test_plan_and_step_refusals(self):
        cases = [
            ("in progress", {"status": "in_progress"}, "step_not_completed"),
            ("failed", {"status": "failed"}, "step_not_completed"),
            ("blank result", {"result": "  "}, "result_blank"),
            ("no result", {"result": KeyError}, "result_blank"),
            ("strict waiver", {"strict_gate_waived": True}, "strict_gate_waived"),
            ("risk waiver", {"risk_gate_waived": True}, "risk_gate_waived"),
            ("other stored command", {"verify_command": CMD + " -v"}, "command_mismatch"),
            ("no stored command", {"verify_command": KeyError}, "command_mismatch"),
            ("named check", {"verify_command": "@fast"}, "named_check_unsupported"),
            ("literal marker", {"verify_command_literal": True}, "named_check_unsupported"),
            ("legacy cursor only", {"verification_anchor": KeyError, "verification_cursor": 2},
             "anchor_missing"),
            ("short anchor", {"verification_anchor": {"after_sha256": "abc"}}, "anchor_invalid"),
            ("anchor key missing", {"verification_anchor": {}}, "anchor_missing"),
        ]
        for label, step, reason in cases:
            with self.subTest(label):
                self.build([run("v-1")], step=step)
                self.expect(self.judge("v-1"), reason)

    def test_the_caller_command_must_equal_the_stored_literal(self):
        self.build([run("v-1")])
        nbsp = chr(0xA0)
        for label, command in (("extra flag", CMD + " -v"), ("padded", "  " + CMD + "\n"),
                               ("trailing no-break space", CMD + nbsp)):
            with self.subTest(label):
                self.expect(self.judge("v-1", command=command), "command_mismatch")

    def test_literal_identity_covers_records_floors_and_partial_notes(self):
        nbsp = chr(0xA0)
        literal = CMD + nbsp
        # The stored verifier ends in a no-break space; records without it are other commands.
        self.build([run("v-1")], step={"verify_command": literal})
        self.expect(self.judge("v-1", command=literal), "no_completion_verification")
        self.build([run("v-1", command=literal)], step={"verify_command": literal})
        self.expect(self.judge("v-1", command=literal), "valid", "v-1", valid=True)
        # A high count of the near-identical command is not this command's floor.
        self.build([run("v-1", command=literal)], pre=[run("v-old", test_count=8)],
                   step={"verify_command": literal})
        self.expect(self.judge("v-1", command=literal), "valid", "v-1", valid=True)
        self.build([run("v-1")], pre=[run("v-old", test_count=8, command=CMD + nbsp)])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)
        partial = {"baseline": EXPECTED, "current": 3, "reason": "r", "command": CMD + nbsp,
                   "completed_count": EXPECTED}
        self.build([run("v-1")], step={"test_drop_partial": partial})
        self.expect(self.judge("v-1"), "drop_note_invalid", "v-1")

    def test_absent_plan_and_absent_step(self):
        self.build([run("v-1")])
        self.expect(self.judge("v-1", plan_name="no-such-plan"), "plan_unreadable")
        self.expect(self.judge("v-1", step_id=2), "step_missing")

    def test_invalid_state_reports_its_own_reason_without_the_id(self):
        self.build([run("v-1")], step={"status": "in_progress"})
        self.expect(self.judge(), "step_not_completed")
        self.build([run("v-1")])
        self.expect(self.judge(plan_name="no-such-plan"), "plan_unreadable")

    def test_duplicate_step_ids_are_refused(self):
        self.build([run("v-1")])
        path = self.state / "plans" / (PLAN + ".json")
        document = json.loads(path.read_text(encoding="utf-8"))
        document["steps"].append(copy.deepcopy(document["steps"][0]))
        path.write_text(json.dumps(document), encoding="utf-8")
        self.expect(self.judge("v-1"), "step_duplicate")

    def test_plan_file_problems(self):
        path = self.state / "plans" / (PLAN + ".json")
        cases = [
            ("not json", "{", "plan_malformed"),
            ("duplicate keys", '{"steps": [], "steps": []}', "plan_malformed"),
            ("no steps", '{"name": "demo-plan"}', "plan_malformed"),
            ("other name", '{"name": "other", "steps": []}', "plan_malformed"),
            ("nan", '{"steps": [], "x": NaN}', "plan_malformed"),
        ]
        for label, text, reason in cases:
            with self.subTest(label):
                self.build([run("v-1")])
                path.write_text(text, encoding="utf-8")
                self.expect(self.judge("v-1"), reason)

    def test_symlinked_plan_and_ledger_are_unreadable(self):
        self.build([run("v-1")])
        for name, reason in ((Path("plans") / (PLAN + ".json"), "plan_unreadable"),
                             (Path("verifications.jsonl"), "ledger_unreadable")):
            with self.subTest(str(name)):
                self.build([run("v-1")])
                target = self.state / name
                moved = target.with_name(target.name + ".real")
                os.rename(str(target), str(moved))
                os.symlink(str(moved), str(target))
                self.expect(self.judge("v-1"), reason)
                os.unlink(str(target))
                os.rename(str(moved), str(target))

    def test_symlinked_plans_directory_is_unreadable(self):
        self.build([run("v-1")])
        os.rename(str(self.state / "plans"), str(self.state / "plans-real"))
        os.symlink(str(self.state / "plans-real"), str(self.state / "plans"))
        self.expect(self.judge("v-1"), "plan_unreadable")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")  # check-tests: allow this case requires POSIX FIFOs
    def test_special_files_are_refused_without_waiting_for_a_writer(self):
        code = (
            "import json, sys; sys.path.insert(0, sys.argv[1]);"
            "import trial_evaluator_v2 as v;"
            "print(v.select_completion_verification(sys.argv[2], plan_name=sys.argv[3], step_id=1,"
            " command=sys.argv[4], expected_test_count=5,"
            " current_provenance={'git_commit': 'c', 'worktree_digest': 'd'})['reason'])"
        )
        for relative, reason in ((Path("plans") / (PLAN + ".json"), "plan_unreadable"),
                                 (Path("verifications.jsonl"), "ledger_unreadable")):
            with self.subTest(str(relative)):
                self.build([run("v-1")])
                target = self.state / relative
                target.unlink()
                os.mkfifo(str(target))
                try:
                    done = subprocess.run(
                        [sys.executable, "-I", "-c", code, str(SCRIPTS), str(self.state), PLAN, CMD],
                        capture_output=True, text=True, timeout=30)
                except subprocess.TimeoutExpired:
                    self.fail("blocked for 30 seconds opening a FIFO at " + str(relative))
                finally:
                    # The next build must not open this FIFO for writing.
                    os.unlink(str(target))
                self.assertEqual((0, reason), (done.returncode, done.stdout.strip()), done.stderr)

    def test_missing_ledger_is_unreadable(self):
        self.build([run("v-1")])
        (self.state / "verifications.jsonl").unlink()
        self.expect(self.judge("v-1"), "ledger_unreadable")


class AnchorWindow(Case):
    def test_records_before_the_anchor_are_never_candidates(self):
        self.build([], pre=[run("v-old")])
        self.expect(self.judge("v-old"), "no_completion_verification")

    def test_restart_window_ignores_the_earlier_attempt(self):
        self.build([run("v-new")], pre=[run("v-old", exit_code=1, verified=False)])
        self.expect(self.judge("v-new"), "valid", "v-new", valid=True)

    def test_only_the_step_anchor_counts(self):
        wrong = {"after_sha256": "1" * 64}
        self.build([run("v-1")], plan={"verification_anchor": wrong})
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)
        self.build([run("v-1")], step={"verification_anchor": wrong})
        self.expect(self.judge("v-1"), "anchor_unplaced")

    def test_a_plan_anchor_alone_is_refused(self):
        chained = self.build([run("v-1")], pre=[run("v-old")], step={"verification_anchor": KeyError})
        anchor = {"after_sha256": hashlib.sha256(chained[0].encode("utf-8")).hexdigest()}
        path = self.state / "plans" / (PLAN + ".json")
        document = json.loads(path.read_text(encoding="utf-8"))
        document["verification_anchor"] = anchor
        path.write_text(json.dumps(document), encoding="utf-8")
        self.expect(self.judge("v-1"), "anchor_missing")

    def test_a_trimmed_prefix_is_refused_with_or_without_a_placeable_anchor(self):
        # The dropped first run held the high-water count 8, which cannot be proven now.
        def trim(lines):
            return lines[1:]
        pre = [run("v-a", test_count=8), run("v-b")]
        for label, anchor in (("null anchor", {"after_sha256": None}), ("placeable anchor", "auto")):
            with self.subTest(label):
                chained = self.build([run("v-1")], pre=pre, anchor=anchor, raw_lines=trim)
                self.assertEqual(2, len(chained))
                self.expect(self.judge("v-1"), "ledger_compacted")


class Malformed(Case):
    def test_structural_ledger_failures(self):
        cases = [
            ("bad json", lambda lines: lines + ["{oops"], "ledger_malformed"),
            ("not an object", lambda lines: lines + ["[1]"], "ledger_malformed"),
            ("non finite", lambda lines: [lines[0].replace('"test_count": 5', '"test_count": NaN')],
             "ledger_malformed"),
            ("duplicate key", lambda lines: [lines[0].replace('"kind"', '"kind": "x", "kind"', 1)],
             "ledger_malformed"),
            ("edited line", lambda lines: [lines[0].replace("v-1", "v-9")] + lines[1:], "ledger_chain_broken"),
            ("removed middle", lambda lines: lines[:1] + lines[2:], "ledger_chain_broken"),
        ]
        for label, edit, reason in cases:
            with self.subTest(label):
                self.build([run("v-1"), run("v-2"), detached("v-3")], raw_lines=edit)
                self.expect(self.judge("v-1"), reason)

    def test_deep_nesting_is_a_refusal_not_a_crash(self):
        deep = "[" * 100000 + "]" * 100000
        self.build([run("v-1")], raw_lines=lambda lines: lines + [deep])
        self.expect(self.judge("v-1"), "ledger_malformed")
        self.build([run("v-1")])
        (self.state / "plans" / (PLAN + ".json")).write_text(deep, encoding="utf-8")
        self.expect(self.judge("v-1"), "plan_malformed")

    def test_duplicate_record_ids_are_refused(self):
        self.build([run("v-1"), run("v-1")])
        self.expect(self.judge("v-1"), "id_duplicate")

    def test_a_candidate_without_a_usable_id_is_refused(self):
        self.build([run("")])
        self.expect(self.judge("v-1"), "record_malformed")

    def test_malformed_unrelated_record_fields_do_not_matter(self):
        self.build([run("v-1"), run("v-x", command="other", timestamp="garbage")])
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)

    def test_bad_inputs_are_reported_not_raised(self):
        self.build([run("v-1")])
        cases = [
            ("state", dict(), "bad_state"),
            ("traversal", dict(plan_name="../x"), "bad_plan_name"),
            ("uppercase", dict(plan_name="Demo"), "bad_plan_name"),
            ("empty plan", dict(plan_name=""), "bad_plan_name"),
            ("long plan", dict(plan_name="a" * 200), "bad_plan_name"),
            ("step zero", dict(step_id=0), "bad_step_id"),
            ("step text", dict(step_id="1"), "bad_step_id"),
            ("step bool", dict(step_id=True), "bad_step_id"),
            ("blank command", dict(command="  "), "bad_command"),
            ("count zero", dict(expected_test_count=0), "bad_expected_count"),
            ("count negative", dict(expected_test_count=-1), "bad_expected_count"),
            ("count bool", dict(expected_test_count=True), "bad_expected_count"),
            ("count text", dict(expected_test_count="5"), "bad_expected_count"),
            ("no provenance", dict(current_provenance=None), "bad_current_provenance"),
            ("blank commit", dict(current_provenance={"git_commit": "", "worktree_digest": "d"}),
             "bad_current_provenance"),
            ("blank digest", dict(current_provenance={"git_commit": "c", "worktree_digest": " "}),
             "bad_current_provenance"),
            ("blank hint", dict(completion_verification_id=" "), "bad_completion_id"),
        ]
        for label, over, reason in cases:
            with self.subTest(label):
                if label == "state":
                    outcome = v2.select_completion_verification(
                        self.state / "missing", plan_name=PLAN, step_id=STEP, command=CMD,
                        expected_test_count=EXPECTED, current_provenance=dict(SRC))
                else:
                    outcome = self.judge("v-1", **over)
                self.expect(outcome, reason)


class LedgerFraming(Case):
    def test_every_record_needs_its_chain_link(self):
        def drop_link(index):
            def edit(lines):
                record = json.loads(lines[index])
                del record["prev_sha256"]
                return lines[:index] + [line_of(record)] + lines[index + 1:]
            return edit
        cases = [
            ("singleton", [run("v-1")], 0),
            ("first of two", [run("v-1"), run("v-2")], 0),
            ("second of two", [run("v-1"), run("v-2")], 1),
        ]
        for label, records, index in cases:
            with self.subTest(label):
                self.build(records, raw_lines=drop_link(index))
                self.expect(self.judge("v-1"), "ledger_chain_broken")

    def test_crlf_framing_hashes_the_line_without_its_terminator(self):
        originals = []

        def crlf(lines):
            originals[:] = lines
            return [text + "\r" for text in lines]
        self.build([run("v-1")], pre=[run("v-old")], raw_lines=crlf)
        anchor = {"after_sha256": hashlib.sha256(originals[0].encode("utf-8")).hexdigest()}
        self.build([run("v-1")], pre=[run("v-old")], raw_lines=crlf, anchor=anchor)
        ledger = self.state / "verifications.jsonl"
        self.assertEqual(2, ledger.read_bytes().count(b"\r\n"))
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)
        parity = mythify_io.jsonl_append_anchor(ledger)
        self.assertEqual(hashlib.sha256(originals[1].encode("utf-8")).hexdigest(), parity["after_sha256"])

    def test_the_first_broken_link_ends_the_work(self):
        def break_second(lines):
            record = json.loads(lines[1])
            record["prev_sha256"] = "0" * 64
            return [lines[0], line_of(record)] + lines[2:]
        records = [run("v-{0}".format(n)) for n in range(500)]
        self.build(records, raw_lines=break_second)
        with mock.patch.object(v2, "_loads", wraps=v2._loads) as parsed:
            outcome = self.judge("v-1")
        self.expect(outcome, "ledger_chain_broken")
        self.assertEqual(3, parsed.call_count)  # the plan, then two ledger lines

    def test_bounds_refuse_before_unbounded_work(self):
        self.build([run("v-1"), run("v-2"), run("v-3")])
        cases = [
            ("record count", "MAX_RECORDS", 2, "ledger_too_large"),
            ("line length", "MAX_LINE_BYTES", 50, "ledger_malformed"),
            ("file size", "MAX_LEDGER_BYTES", 100, "ledger_unreadable"),
        ]
        for label, name, value, reason in cases:
            with self.subTest(label), mock.patch.object(v2, name, value):
                self.expect(self.judge("v-1"), reason)
        self.assertEqual(4096, v2.MAX_RECORDS)
        self.assertEqual(256 * 1024, v2.MAX_LINE_BYTES)
        self.assertEqual(1024 * 1024, v2.MAX_LEDGER_BYTES)


class Format(Case):
    def test_reads_what_the_runtime_writer_chains(self):
        ledger = self.state / "verifications.jsonl"
        stamp = "2026-11-23T10:00:00+00:00"
        with mock.patch.object(mythify_io, "_resolve_state_dir", return_value=self.state):
            mythify_io.append_chained_jsonl(ledger, run("v-old", timestamp=stamp))
            anchor = mythify_io.jsonl_append_anchor(ledger)
            mythify_io.append_chained_jsonl(ledger, run("v-1", timestamp=stamp))
            mythify_io.append_chained_jsonl(ledger, detached("v-2", timestamp=stamp))
        self.assertIsNotNone(anchor["after_sha256"])
        step = {"id": STEP, "status": "completed", "result": "ok", "verify_command": CMD,
                "started_at": stamp, "updated_at": stamp, "verification_anchor": anchor}
        (self.state / "plans" / (PLAN + ".json")).write_text(
            json.dumps({"name": PLAN, "steps": [step]}), encoding="utf-8")
        self.expect(self.judge("v-1"), "valid", "v-1", valid=True)

    def test_sources_are_ascii(self):
        for path in (Path(v2.__file__), Path(__file__)):
            with self.subTest(path.name):
                path.read_bytes().decode("ascii")


if __name__ == "__main__":
    unittest.main()
