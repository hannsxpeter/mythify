"""Unit coverage for the advisory evidence-quality guards.

These helpers pair the exit-code anchor with a watcher for the cheap way to
win it: always-pass verifiers, zero-test green runs, and sessions running with
a legacy gate opt-out active. They are advisory by contract, so the tests pin
detection behavior, not any blocking side effect.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import hashlib
import json

from mythify_evidence_guard import (  # noqa: E402
    active_legacy_opt_outs,
    ledger_chain_breaks,
    noop_verifier_reason,
    trivial_pass_reason,
)


def chained_lines(records):
    """Serialize records into raw chained lines the way the writers do."""
    lines = []
    for record in records:
        previous = lines[-1] if lines else ""
        record = dict(record)
        record["prev_sha256"] = (
            hashlib.sha256(previous.encode("utf-8")).hexdigest() if previous else None
        )
        lines.append(json.dumps(record))
    return lines


def executed_pass(command, stdout_tail="", stderr_tail=""):
    return {
        "kind": "executed",
        "verified": True,
        "exit_code": 0,
        "command": command,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }


class TestNoopVerifierReason(unittest.TestCase):
    def test_flags_always_pass_commands(self):
        for command in ("true", " true ", "TRUE", ":", "exit 0", "exit  0"):
            self.assertIsNotNone(noop_verifier_reason(command), command)

    def test_flags_bare_print_commands(self):
        self.assertIsNotNone(noop_verifier_reason("echo ok"))
        self.assertIsNotNone(noop_verifier_reason("printf done"))

    def test_print_commands_with_shell_operators_are_real_checks(self):
        self.assertIsNone(noop_verifier_reason("echo start && pytest"))
        self.assertIsNone(noop_verifier_reason("echo payload | grep expected"))
        self.assertIsNone(noop_verifier_reason("echo x > expected.txt"))

    def test_real_verifiers_are_not_flagged(self):
        for command in (
            "python3 -m unittest discover -s tests",
            "npm test --prefix mcp-server",
            "test -f dist/mythify.py",
            "git diff --check",
            "",
        ):
            self.assertIsNone(noop_verifier_reason(command), command)


class TestTrivialPassReason(unittest.TestCase):
    def test_flags_a_noop_command_pass(self):
        self.assertIsNotNone(trivial_pass_reason(executed_pass("true")))

    def test_flags_zero_test_runner_output(self):
        samples = (
            "Ran 0 tests in 0.000s\n\nNO TESTS RAN",
            "collected 0 items",
            "no tests collected",
            "?   \texample.com/pkg\t[no test files]",
            "  0 passing (2ms)",
        )
        for stdout in samples:
            record = executed_pass("run-the-suite", stdout_tail=stdout)
            self.assertIsNotNone(trivial_pass_reason(record), stdout)

    def test_zero_test_output_in_stderr_is_also_flagged(self):
        record = executed_pass("run-the-suite", stderr_tail="Ran 0 tests in 0.000s")
        self.assertIsNotNone(trivial_pass_reason(record))

    def test_substantive_passes_are_not_flagged(self):
        record = executed_pass(
            "python3 -m unittest discover -s tests",
            stderr_tail="Ran 214 tests in 92.1s\n\nOK",
        )
        self.assertIsNone(trivial_pass_reason(record))

    def test_only_passing_executed_records_are_considered(self):
        self.assertIsNone(
            trivial_pass_reason(
                {"kind": "executed", "verified": False, "command": "true"}
            )
        )
        self.assertIsNone(
            trivial_pass_reason(
                {"kind": "attested", "verified": None, "command": "true"}
            )
        )


class TestLedgerChainBreaks(unittest.TestCase):
    def test_an_intact_chain_reports_no_breaks(self):
        lines = chained_lines([{"claim": "a"}, {"claim": "b"}, {"claim": "c"}])
        self.assertEqual(ledger_chain_breaks("\n".join(lines) + "\n"), [])

    def test_an_edited_line_breaks_the_next_record(self):
        lines = chained_lines([{"claim": "a"}, {"claim": "b"}, {"claim": "c"}])
        lines[1] = lines[1].replace('"b"', '"forged"')
        self.assertEqual(ledger_chain_breaks("\n".join(lines)), [3])

    def test_a_deleted_line_breaks_the_chain(self):
        lines = chained_lines([{"claim": "a"}, {"claim": "b"}, {"claim": "c"}])
        del lines[1]
        self.assertEqual(ledger_chain_breaks("\n".join(lines)), [2])

    def test_legacy_records_without_prev_hash_stay_silent(self):
        lines = [json.dumps({"claim": "old-a"}), json.dumps({"claim": "old-b"})]
        self.assertEqual(ledger_chain_breaks("\n".join(lines)), [])

    def test_the_first_line_is_never_judged(self):
        # After compaction the first retained record's predecessor lives in
        # the archive, so its prev_sha256 cannot match anything on file.
        lines = chained_lines([{"claim": "a"}, {"claim": "b"}, {"claim": "c"}])[1:]
        self.assertEqual(ledger_chain_breaks("\n".join(lines)), [])

    def test_empty_ledger_is_clean(self):
        self.assertEqual(ledger_chain_breaks(""), [])


class TestActiveLegacyOptOuts(unittest.TestCase):
    def test_no_opt_outs_in_a_clean_environment(self):
        self.assertEqual(active_legacy_opt_outs({}), [])
        self.assertEqual(active_legacy_opt_outs(None), [])

    def test_enabled_gates_are_not_reported(self):
        env = {
            "MYTHIFY_REQUIRE_VERIFIED_STEP": "1",
            "MYTHIFY_REQUIRE_HUMAN_INPUT": "yes",
            "MYTHIFY_DISABLE_RUN": "",
        }
        self.assertEqual(active_legacy_opt_outs(env), [])

    def test_each_opt_out_is_named_with_its_effect(self):
        env = {
            "MYTHIFY_REQUIRE_VERIFIED_STEP": "0",
            "MYTHIFY_REQUIRE_HUMAN_INPUT": "off",
            "MYTHIFY_DISABLE_RUN": "1",
        }
        active = active_legacy_opt_outs(env)
        names = [item["name"] for item in active]
        self.assertEqual(
            names,
            [
                "MYTHIFY_REQUIRE_VERIFIED_STEP",
                "MYTHIFY_REQUIRE_HUMAN_INPUT",
                "MYTHIFY_DISABLE_RUN",
            ],
        )
        for item in active:
            self.assertTrue(item["effect"])


if __name__ == "__main__":
    unittest.main()
