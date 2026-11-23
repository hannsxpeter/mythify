"""Evidence reuse (`--reuse`) and the test-count ratchet, through the real CLI."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "mythify.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from mythify_ratchet import (  # noqa: E402
    detect_test_count_drop,
    latest_test_count_drops,
    records_before_window,
    step_baseline,
)
from mythify_reuse import (  # noqa: E402
    _first_word,
    environment_digest,
    find_reusable_run,
)
from mythify_step_gate import configure_step_gate, evaluate_step_evidence  # noqa: E402

CHECK_SCRIPT = """\
import os
import sys

with open(sys.argv[1], "a") as log:
    log.write("x\\n")
if os.path.exists(sys.argv[2]):
    sys.exit(1)
with open("count.txt") as handle:
    sys.stderr.write("Ran {0} tests\\nOK\\n".format(handle.read().strip()))
"""


class GitProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Resolved: ignore-rule paths are part of the worktree digest.
        base = Path(self.tmp.name).resolve()
        self.project = base / "project"
        self.project.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        self.state = self.project / ".mythify"
        self.runs_log = base / "runs.log"
        self.fail_flag = base / "fail.flag"
        self.git("init", "-q")
        self.git("config", "user.email", "mythify@example.invalid")
        self.git("config", "user.name", "Mythify Test")
        (self.project / "check.py").write_text(CHECK_SCRIPT, encoding="utf-8")
        self.set_count(5)
        self.ok("init")
        self.git("add", "-A")
        self.git("commit", "-qm", "initial")
        self.command = "{0} check.py {1} {2}".format(
            shlex.quote(sys.executable), shlex.quote(str(self.runs_log)),
            shlex.quote(str(self.fail_flag)),
        )

    def git(self, *args):
        subprocess.run(["git", *args], cwd=str(self.project), check=True, capture_output=True)

    def git_status(self):
        return subprocess.run(["git", "status", "--porcelain"], cwd=str(self.project), check=True,
                              capture_output=True, text=True).stdout.strip()

    def set_count(self, count):
        (self.project / "count.txt").write_text("{0}\n".format(count), encoding="utf-8")

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        for name in ("MYTHIFY_DIR", "MYTHIFY_REQUIRE_VERIFIED_STEP", "MYTHIFY_DISABLE_RUN",
                     "MYTHIFY_REUSE_ENV"):
            env.pop(name, None)
        env["HOME"] = str(self.home)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args), cwd=str(self.project), env=env,
            capture_output=True, text=True, timeout=120,
        )

    def ok(self, *args, env_extra=None):
        result = self.run_cli(*args, env_extra=env_extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def executions(self):
        if not self.runs_log.exists():
            return 0
        return len(self.runs_log.read_text(encoding="utf-8").splitlines())

    def records(self):
        path = self.state / "verifications.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def plan(self, name="p"):
        return json.loads((self.state / "plans" / (name + ".json")).read_text(encoding="utf-8"))

    def create_plan(self):
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps(
            [{"title": "only step", "verify_command": self.command}]))


class ReuseTests(GitProjectCase):
    def test_reuse_hit_records_a_reused_record_without_executing(self):
        self.ok("verify", "run", self.command)
        self.assertEqual(self.executions(), 1)
        reused = self.ok("verify", "run", self.command, "--reuse")
        self.assertEqual(self.executions(), 1)
        first, second = self.records()
        self.assertIn(
            "[OK] Reused passing run {0} (same command, commit, worktree digest, and "
            "environment); not re-executed.".format(first["id"]), reused.stdout)
        self.assertIn("reused", reused.stdout.splitlines()[-1])
        self.assertEqual(second["kind"], "executed")
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["reused_from"], first["id"])
        self.assertEqual(second["duration_seconds"], 0)
        self.assertEqual(second["reused_duration_seconds"], first["duration_seconds"])
        for field in ("exit_code", "verified", "test_count", "stdout_tail", "stderr_tail"):
            self.assertEqual(second[field], first[field], field)
        self.assertEqual(second["test_count"], 5)
        self.assertNotIn("artifacts", second)
        self.assertTrue(first["provenance"]["environment_digest"])
        self.assertEqual(
            second["provenance"]["environment_digest"], first["provenance"]["environment_digest"])

    def test_reuse_follows_a_reused_record_to_its_original(self):
        self.ok("verify", "run", self.command)
        self.ok("verify", "run", self.command, "--reuse")
        self.ok("verify", "run", self.command, "--reuse")
        records = self.records()
        self.assertEqual(self.executions(), 1)
        self.assertEqual(records[2]["reused_from"], records[0]["id"])

    def test_no_reuse_without_the_flag(self):
        self.ok("verify", "run", self.command)
        self.ok("verify", "run", self.command)
        self.assertEqual(self.executions(), 2)
        self.assertNotIn("reused_from", self.records()[1])

    def test_no_reuse_after_a_source_edit(self):
        self.ok("verify", "run", self.command)
        self.set_count(6)
        result = self.ok("verify", "run", self.command, "--reuse")
        self.assertEqual(self.executions(), 2)
        self.assertIn("[INFO]", result.stdout)
        self.assertIn("no matching passing run", result.stdout)
        self.assertNotIn("reused_from", self.records()[1])

    def test_no_reuse_after_a_commit(self):
        self.ok("verify", "run", self.command)
        self.git("commit", "-q", "--allow-empty", "-m", "next")
        self.ok("verify", "run", self.command, "--reuse")
        self.assertEqual(self.executions(), 2)

    def test_declared_environment_changes_the_digest(self):
        declared = {"MYTHIFY_REUSE_ENV": "MYTHIFY_TEST_INPUT, MYTHIFY_OTHER"}
        self.ok("verify", "run", self.command, env_extra=dict(declared, MYTHIFY_TEST_INPUT="a"))
        self.ok("verify", "run", self.command, "--reuse",
                env_extra=dict(declared, MYTHIFY_TEST_INPUT="b"))
        self.assertEqual(self.executions(), 2)
        self.ok("verify", "run", self.command, "--reuse",
                env_extra=dict(declared, MYTHIFY_TEST_INPUT="b"))
        self.assertEqual(self.executions(), 2)
        self.assertEqual(self.records()[2]["reused_from"], self.records()[1]["id"])
        # A variable nobody declared is not part of the environment digest.
        self.ok("verify", "run", self.command, "--reuse", env_extra={"MYTHIFY_TEST_INPUT": "c"})
        self.assertEqual(self.executions(), 3)

    def test_no_reuse_when_the_latest_matching_run_failed(self):
        self.ok("verify", "run", self.command)
        self.fail_flag.write_text("fail", encoding="utf-8")
        self.assertEqual(self.run_cli("verify", "run", self.command).returncode, 2)
        self.fail_flag.unlink()
        result = self.ok("verify", "run", self.command, "--reuse")
        self.assertEqual(self.executions(), 3)
        self.assertIn("[INFO]", result.stdout)
        self.assertNotIn("reused_from", self.records()[2])

    def test_no_reuse_for_a_zero_test_pass(self):
        self.set_count(0)
        self.git("commit", "-qam", "zero tests")
        self.ok("verify", "run", self.command)
        result = self.ok("verify", "run", self.command, "--reuse")
        self.assertEqual(self.executions(), 2)
        self.assertIn("proves nothing", result.stdout)

    def test_reused_record_counts_for_the_step_gate(self):
        self.ok("verify", "run", self.command)
        self.create_plan()
        verified = self.ok("plan", "verify", "1", "--reuse")
        self.assertEqual(self.executions(), 1)
        self.assertIn("Reused passing run", verified.stdout)
        scoped = self.records()[-1]
        self.assertEqual((scoped["plan"], scoped["step_id"]), ("p", 1))
        self.assertTrue(scoped["reused_from"])
        self.ok("step", "1", "completed", "reused run exit 0")

    def test_reuse_is_refused_when_running_is_disabled(self):
        self.ok("verify", "run", self.command)
        self.create_plan()
        disabled = {"MYTHIFY_DISABLE_RUN": "1"}
        self.assertEqual(
            self.run_cli("verify", "run", self.command, "--reuse", env_extra=disabled).returncode, 2)
        self.assertEqual(
            self.run_cli("plan", "verify", "1", "--reuse", env_extra=disabled).returncode, 2)
        self.assertEqual(len(self.records()), 1)

    def test_history_status_and_report_label_reused_records(self):
        self.ok("verify", "run", self.command)
        self.ok("verify", "run", self.command, "--reuse")
        original, reused = self.records()
        history = self.ok("history").stdout
        self.assertIn("reused {0}".format(original["id"]), history)
        as_json = json.loads(self.ok("history", "--json").stdout)
        self.assertEqual(
            sorted(str(row["reused_from"]) for row in as_json["records"]),
            sorted(["None", original["id"]]))
        status = json.loads(self.ok("status", "--json").stdout)
        self.assertIn(original["id"], [r["reused_from"] for r in status["evidence"]["recent"]])
        report = self.ok("report", "--peek").stdout
        self.assertIn("reused {0}".format(original["id"]), report)
        self.assertEqual(reused["reused_from"], original["id"])

    def test_a_newer_outcome_failure_cancels_the_older_pass(self):
        self.ok("verify", "run", self.command)
        self.ok("outcome", "start", "goal", "--success", "passes", "--verify", self.command)
        self.fail_flag.write_text("fail", encoding="utf-8")
        self.assertEqual(self.run_cli("outcome", "check").returncode, 2)
        self.fail_flag.unlink()
        passed, failed = self.records()
        self.assertEqual(failed["outcome"], "goal")
        self.assertEqual(
            failed["provenance"]["environment_digest"], passed["provenance"]["environment_digest"])
        result = self.ok("verify", "run", self.command, "--reuse")
        self.assertEqual(self.executions(), 3)
        self.assertIn("[INFO]", result.stdout)
        self.assertNotIn("reused_from", self.records()[2])

    def test_failure_without_an_environment_digest_still_cancels_a_pass(self):
        key = {"git_commit": "c", "worktree_digest": "w", "mythify_version": "1",
               "environment_digest": "e"}
        passing = {"id": "v-1", "kind": "executed", "command": "suite", "verified": True,
                   "exit_code": 0, "provenance": dict(key)}
        legacy = {"kind": "executed", "command": "suite", "verified": False, "exit_code": 1,
                  "provenance": {k: v for k, v in key.items() if k != "environment_digest"}}
        self.assertEqual(find_reusable_run([passing], "suite", key)[0], passing)
        found, reason = find_reusable_run([passing, legacy], "suite", key)
        self.assertIsNone(found)
        self.assertIn("did not pass", reason)
        # A failure at another source state does not cancel it.
        elsewhere = dict(legacy, provenance=dict(legacy["provenance"], worktree_digest="other"))
        self.assertEqual(find_reusable_run([passing, elsewhere], "suite", key)[0], passing)

    def test_a_reused_record_is_followed_only_to_a_matching_original(self):
        key = {"git_commit": "c", "worktree_digest": "w", "mythify_version": "1",
               "environment_digest": "e"}
        original = {"id": "v-1", "kind": "executed", "command": "suite", "verified": True,
                    "exit_code": 0, "provenance": dict(key, worktree_digest="older")}
        reused = {"id": "v-2", "kind": "executed", "command": "suite", "verified": True,
                  "exit_code": 0, "reused_from": "v-1", "provenance": dict(key)}
        found, reason = find_reusable_run([original, reused], "suite", key)
        self.assertIsNone(found)
        self.assertIn("does not match", reason)
        original["provenance"] = dict(key)
        self.assertEqual(find_reusable_run([original, reused], "suite", key)[0], original)

    def test_a_zero_test_count_is_never_reused(self):
        key = {"git_commit": "c", "worktree_digest": "w", "mythify_version": "1",
               "environment_digest": "e"}
        record = {"id": "v-1", "kind": "executed", "command": "suite", "verified": True,
                  "exit_code": 0, "test_count": 0, "stdout_tail": "ok", "stderr_tail": "",
                  "provenance": dict(key)}
        found, reason = find_reusable_run([record], "suite", key)
        self.assertIsNone(found)
        self.assertIn("zero tests", reason)
        record["test_count"] = 3
        self.assertEqual(find_reusable_run([record], "suite", key)[0], record)

    def test_first_word_skips_assignments_and_splits_shell_operators(self):
        for command in ("tool>/dev/null", "FOO=1 BAR=2 tool --flag", "tool|cat",
                        "tool&& other", "(tool)", "  tool  "):
            self.assertEqual(_first_word(command), "tool", command)
        self.assertEqual(_first_word(""), "")
        for command in ("2>/dev/null tool a", "2> /dev/null tool", ">out tool", "FOO=1 2>&1 tool",
                        "&>log tool", "</dev/null tool", "2>>log FOO=1 tool x", "(2>/dev/null tool)"):
            self.assertEqual(_first_word(command), "tool", command)
        self.assertEqual(_first_word("echo 2 > file"), "echo")
        self.assertEqual(_first_word("tool 2>/dev/null"), "tool")
        self.assertEqual(_first_word("2>/dev/null"), "")
        self.assertEqual(_first_word("FOO=1"), "")

    def test_digest_follows_the_program_even_with_attached_operators(self):
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        tool = bin_dir / "check-tool"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
        environ = {"PATH": str(bin_dir)}
        before = environment_digest("check-tool>/dev/null", environ)
        tool.write_text("#!/bin/sh\n# changed\nexit 0\n", encoding="utf-8")
        self.assertNotEqual(before, environment_digest("check-tool>/dev/null", environ))
        self.assertEqual(
            environment_digest("check-tool>/dev/null", environ),
            environment_digest("FOO=1 check-tool", environ))

    def test_find_reusable_run_requires_a_git_state(self):
        current = {"git_commit": None, "worktree_digest": None, "mythify_version": "1",
                   "environment_digest": "e"}
        record = {"id": "v-1", "kind": "executed", "command": "c", "verified": True,
                  "exit_code": 0, "provenance": dict(current)}
        found, reason = find_reusable_run([record], "c", current)
        self.assertIsNone(found)
        self.assertIn("unavailable", reason)

    def test_environment_digest_covers_declared_variables(self):
        base = {"PATH": os.environ.get("PATH", ""), "MYTHIFY_REUSE_ENV": "A"}
        first = environment_digest("true", dict(base, A="1"))
        self.assertEqual(first, environment_digest("true", dict(base, A="1")))
        self.assertNotEqual(first, environment_digest("true", dict(base, A="2")))
        self.assertEqual(
            environment_digest("true", dict(base, A="1", B="1")),
            environment_digest("true", dict(base, A="1", B="2")))
        self.assertNotEqual(first, environment_digest("true", {"PATH": "/nonexistent"}))


class RatchetTests(GitProjectCase):
    def run_with_count(self, count, *extra):
        self.set_count(count)
        return self.run_cli("verify", "run", self.command, *extra)

    def test_a_lower_count_is_recorded_and_warned(self):
        self.assertEqual(self.run_with_count(5).returncode, 0)
        result = self.run_with_count(3)
        self.assertEqual(result.returncode, 0)
        first, second = self.records()
        self.assertNotIn("test_count_drop", first)
        self.assertEqual(second["test_count_drop"],
                         {"previous": 5, "previous_id": first["id"], "current": 3})
        self.assertIn(
            "[WARN] Test count dropped from 5 to 3 for '{0}' since run {1}. A check that "
            "runs fewer tests proves less.".format(self.command, first["id"]), result.stderr)

    def test_equal_or_higher_counts_record_no_drop(self):
        self.run_with_count(5)
        self.run_with_count(5)
        self.run_with_count(8)
        self.assertTrue(all("test_count_drop" not in r for r in self.records()))

    def test_a_failing_run_records_no_drop(self):
        self.run_with_count(5)
        self.fail_flag.write_text("fail", encoding="utf-8")
        self.set_count(1)
        self.assertEqual(self.run_cli("verify", "run", self.command).returncode, 2)
        self.assertTrue(all("test_count_drop" not in r for r in self.records()))

    def drop_scenario(self, strict):
        """Baseline 5 recorded before the step starts; the step's own run counts 3."""
        self.create_plan()
        if strict:
            self.ok("campaign", "start", "goal", "--plan", "p")
        self.assertEqual(self.run_with_count(5).returncode, 0)
        self.set_count(3)
        self.ok("plan", "verify", "1")

    def test_gate_warns_by_default_and_completes(self):
        self.drop_scenario(strict=False)
        done = self.ok("step", "1", "completed", "ran fewer tests")
        self.assertIn("[WARN] Test count dropped", done.stderr)
        self.assertIn("5", done.stderr)
        self.assertNotIn("test_drop_accepted", self.plan()["steps"][0])

    def test_gate_refuses_under_strict_context_without_acceptance(self):
        self.drop_scenario(strict=True)
        refused = self.run_cli("step", "1", "completed", "ran fewer tests")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("[FAIL] Test count dropped under strict context", refused.stderr)
        self.assertIn("--accept-test-drop", refused.stderr)
        self.assertEqual(self.plan()["steps"][0]["status"], "in_progress")
        blank = self.run_cli("step", "1", "completed", "x", "--accept-test-drop", "  ")
        self.assertEqual(blank.returncode, 1)
        self.assertIn("needs a reason", blank.stderr)

    # check-tests: allow renamed from test_gate_passes_with_acceptance_and_stamps_the_step
    def test_acceptance_records_a_partial_and_leaves_the_step_open(self):
        self.drop_scenario(strict=True)
        done = self.run_cli("step", "1", "completed", "ran fewer tests",
                            "--accept-test-drop", "obsolete tests were deleted")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("Partial pass recorded", done.stderr)
        step = self.plan()["steps"][0]
        self.assertEqual(step["status"], "in_progress")
        self.assertEqual(step["test_drop_partial"]["reason"], "obsolete tests were deleted")
        self.assertEqual((step["test_drop_partial"]["baseline"],
                          step["test_drop_partial"]["current"]), (5, 3))

    def test_campaign_advance_takes_the_same_flag(self):
        self.drop_scenario(strict=True)
        refused = self.run_cli("campaign", "advance", "--result", "ran fewer tests")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Test count dropped under strict context", refused.stderr)
        partial = self.run_cli("campaign", "advance", "--result", "ran fewer tests",
                               "--accept-test-drop", "obsolete tests were deleted")
        self.assertEqual(partial.returncode, 1, partial.stdout + partial.stderr)
        step = self.plan()["steps"][0]
        self.assertEqual(step["test_drop_partial"]["current"], 3)
        self.assertEqual(step["status"], "in_progress")

    def test_baseline_falls_back_to_the_first_run_in_the_window(self):
        self.create_plan()
        self.ok("campaign", "start", "goal", "--plan", "p")
        self.ok("step", "1", "in_progress")
        self.ok("verify", "run", self.command)
        self.set_count(3)
        self.ok("plan", "verify", "1")
        refused = self.run_cli("step", "1", "completed", "ran fewer tests")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("below the baseline of 5", refused.stderr)

    def test_evaluator_is_pure_and_takes_the_reason_as_an_argument(self):
        self.drop_scenario(strict=True)
        # Git ignore rules fold HOME into the worktree digest; match the CLI runs.
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        configure_step_gate("test")
        before = (self.state / "plans" / "p.json").read_bytes()
        plan = self.plan()
        step = plan["steps"][0]
        denied = evaluate_step_evidence(self.state, "p", plan, step)
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["test_drop"]["baseline"], 5)
        self.assertEqual(denied["test_drop"]["current"], 3)
        self.assertIsNone(denied["partial_candidate"])
        recorded = evaluate_step_evidence(self.state, "p", plan, step, "deleted obsolete tests")
        self.assertFalse(recorded["ok"])
        candidate = recorded["partial_candidate"]
        self.assertEqual((candidate["reason"], candidate["baseline"], candidate["current"]),
                         ("deleted obsolete tests", 5, 3))
        self.assertEqual((self.state / "plans" / "p.json").read_bytes(), before)

    def test_status_shows_and_clears_the_drop_attention_item(self):
        self.run_with_count(5)
        self.run_with_count(3)
        status = json.loads(self.ok("status", "--json").stdout)
        drops = [i for i in status["attention"] if i["summary"].startswith("test count dropped")]
        self.assertEqual(len(drops), 1, status["attention"])
        self.assertEqual(drops[0]["level"], "warning")
        self.assertIn("3 tests, down from 5", drops[0]["detail"])
        self.assertIn("test count dropped", self.ok("status").stdout)
        self.run_with_count(5)
        cleared = json.loads(self.ok("status", "--json").stdout)
        self.assertEqual(
            [i for i in cleared["attention"] if i["summary"].startswith("test count dropped")], [])

    def test_repeated_partial_passes_do_not_lower_the_baseline(self):
        self.create_plan()
        self.ok("campaign", "start", "goal", "--plan", "p")
        self.run_with_count(4)
        self.run_with_count(2)
        self.ok("step", "1", "in_progress")
        self.set_count(2)
        self.ok("plan", "verify", "1")
        gate = self.run_cli("verify", "gate")
        self.assertEqual(gate.returncode, 2, gate.stdout + gate.stderr)
        refused = self.run_cli("step", "1", "completed", "still partial")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("below the baseline of 4", refused.stderr)
        self.assertEqual(self.plan()["steps"][0]["status"], "in_progress")
        first, second, third = self.records()
        self.assertEqual(second["test_count_drop"]["previous"], 4)
        self.assertEqual(third.get("test_count_drop"),
                         {"previous": 4, "previous_id": first["id"], "current": 2})
        status = json.loads(self.ok("status", "--json").stdout)
        drops = [i for i in status["attention"] if i["summary"].startswith("test count dropped")]
        self.assertEqual(len(drops), 1, status["attention"])
        self.assertIn("2 tests, down from 4", drops[0]["detail"])
        self.set_count(4)
        self.ok("plan", "verify", "1")
        recovered = self.run_cli("verify", "gate")
        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        self.ok("step", "1", "completed", "full suite again")
        cleared = json.loads(self.ok("status", "--json").stdout)
        self.assertEqual(
            [i for i in cleared["attention"] if i["summary"].startswith("test count dropped")], [])

    def test_a_retained_drop_note_keeps_the_floor_after_compaction(self):
        self.create_plan()
        self.ok("campaign", "start", "goal", "--plan", "p")
        self.run_with_count(4)
        self.run_with_count(2)
        self.ok("logs", "compact", "--keep", "1")
        self.assertEqual(len(self.records()), 1)
        self.ok("step", "1", "in_progress")
        self.set_count(2)
        self.ok("plan", "verify", "1")
        retained, newest = self.records()
        self.assertEqual(newest["test_count_drop"]["previous"], 4)
        gate = self.run_cli("verify", "gate")
        self.assertEqual(gate.returncode, 2, gate.stdout + gate.stderr)
        refused = self.run_cli("step", "1", "completed", "still partial")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("below the baseline of 4", refused.stderr)
        status = json.loads(self.ok("status", "--json").stdout)
        drops = [i for i in status["attention"] if i["summary"].startswith("test count dropped")]
        self.assertEqual(len(drops), 1, status["attention"])
        self.assertIn("2 tests, down from 4", drops[0]["detail"])

    def test_a_rise_inside_the_window_then_a_drop_stays_refused(self):
        self.create_plan()
        self.ok("campaign", "start", "goal", "--plan", "p")
        self.ok("step", "1", "in_progress")
        self.set_count(3)
        self.ok("verify", "run", self.command)
        self.set_count(5)
        self.ok("verify", "run", self.command)
        self.set_count(3)
        self.ok("plan", "verify", "1")
        refused = self.run_cli("step", "1", "completed", "dropped again")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("below the baseline of 5", refused.stderr)

    def test_status_flags_a_ledger_whose_second_partial_was_unannotated(self):
        def passing(rid, count):
            return {"id": rid, "kind": "executed", "command": "c", "verified": True,
                    "exit_code": 0, "test_count": count, "timestamp": "t" + rid}

        ledger = [passing("a", 4), passing("b", 2), passing("c", 2)]
        ledger[1]["test_count_drop"] = {"previous": 4, "previous_id": "a", "current": 2}
        self.assertEqual(latest_test_count_drops(ledger),
                         [("c", {"previous": 4, "previous_id": "a", "current": 2}, "tc")])
        self.assertEqual(latest_test_count_drops(ledger + [passing("d", 4)]), [])

    def test_step_baseline_uses_the_highest_earlier_count_only(self):
        def passing(rid, count):
            return {"id": rid, "kind": "executed", "command": "c", "verified": True,
                    "exit_code": 0, "test_count": count}

        a, b, c, d = passing("a", 4), passing("b", 2), passing("c", 2), passing("d", 9)
        self.assertEqual(step_baseline([a, b], [c], "c", c), (4, "a"))
        self.assertEqual(step_baseline([], [passing("x", 3), passing("y", 5), c], "c", c), (5, "y"))
        self.assertEqual(step_baseline([a], [c, d], "c", c), (4, "a"))
        self.assertIsNone(step_baseline([], [c], "c", c))
        noted = dict(c, test_count_drop={"previous": 4, "previous_id": "gone", "current": 2})
        self.assertEqual(step_baseline([], [noted], "c", noted), (4, "gone"))
        self.assertEqual(step_baseline([b], [], "c", noted), (4, "gone"))
        for bad in ({"previous": True, "previous_id": "x", "current": 2},
                    {"previous": 4, "previous_id": "x", "current": 3},
                    {"previous": 2, "previous_id": "x", "current": 2}):
            self.assertIsNone(step_baseline([], [], "c", dict(c, test_count_drop=bad)))
        self.assertEqual(detect_test_count_drop([noted], "c", 2),
                         {"previous": 4, "previous_id": "gone", "current": 2})
        kept = [dict(b, test_count_drop={"previous": 4, "previous_id": "gone", "current": 2}), c]
        self.assertEqual(latest_test_count_drops([dict(r, command="c", timestamp="t") for r in kept]),
                         [("c", {"previous": 4, "previous_id": "gone", "current": 2}, "t")])

    def test_baseline_window_ignores_records_appended_after_the_window_read(self):
        def passing(rid, count):
            return {"id": rid, "kind": "executed", "command": "c", "verified": True,
                    "exit_code": 0, "test_count": count}

        before, inside = passing("a", 10), passing("b", 3)
        appended = passing("late", 3)
        ledger = [before, inside, appended]
        self.assertEqual(records_before_window(ledger, [inside]), [before])
        self.assertEqual(records_before_window(ledger[:2], [inside]), [before])
        self.assertEqual(records_before_window(ledger, []), ledger)
        self.assertEqual(records_before_window(ledger, [passing("gone", 1)]), [])
        baseline = step_baseline(records_before_window(ledger, [inside]), [inside], "c", inside)
        self.assertEqual(baseline, (10, "a"))

    def test_pure_helpers(self):
        def record(rid, count, command="c", verified=True, **extra):
            item = {"id": rid, "kind": "executed", "command": command, "verified": verified,
                    "exit_code": 0 if verified else 1, "test_count": count,
                    "timestamp": "2026-01-01T00:00:0{0}Z".format(len(rid))}
            item.update(extra)
            return item

        log = [record("a", 10), record("bb", 4, verified=False), record("ccc", 12, command="d")]
        self.assertEqual(detect_test_count_drop(log, "c", 7),
                         {"previous": 10, "previous_id": "a", "current": 7})
        self.assertIsNone(detect_test_count_drop(log, "c", 10))
        self.assertIsNone(detect_test_count_drop(log, "never-run", 1))
        later = record("zz", 1)
        self.assertEqual(step_baseline([record("a", 10)], [record("bb", 8)], "c", later), (10, "a"))
        self.assertEqual(step_baseline([], [record("bb", 8), record("ccc", 6)], "c", later), (8, "bb"))
        self.assertIsNone(step_baseline([], [], "c", later))
        dropped = record("dd", 7, test_count_drop={"previous": 10, "previous_id": "a", "current": 7})
        reuse = record("eee", 7, reused_from="dd")
        self.assertEqual(len(latest_test_count_drops(log + [dropped])), 1)
        self.assertEqual(len(latest_test_count_drops(log + [dropped, reuse])), 1)
        self.assertEqual(latest_test_count_drops(log + [dropped, record("ffff", 11)]), [])


class PartialPassTests(GitProjectCase):
    """--accept-test-drop records a partial pass and leaves the step open."""

    REASON = "obsolete tests were deleted"

    def partial_scenario(self, strict=True, floor=4, partial=2):
        """Floor of FLOOR tests recorded before the step; the step's own run counts PARTIAL."""
        self.create_plan()
        if strict:
            self.ok("campaign", "start", "goal", "--plan", "p")
        self.set_count(floor)
        self.ok("verify", "run", self.command)
        self.set_count(partial)
        self.ok("plan", "verify", "1")

    def step(self):
        return self.plan()["steps"][0]

    def test_four_to_two_records_a_partial_and_refuses_completion(self):
        self.partial_scenario()
        before = self.step()
        floor_id = self.records()[0]["id"]
        result = self.run_cli("step", "1", "completed", "ran fewer tests",
                              "--accept-test-drop", self.REASON)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Partial pass recorded", result.stderr)
        after = self.step()
        self.assertEqual(after.pop("test_drop_partial"), {
            "reason": self.REASON, "baseline": 4, "baseline_id": floor_id,
            "current": 2, "command": self.command})
        self.assertEqual(after, before)
        self.assertEqual(after["status"], "in_progress")
        self.assertNotIn("test_drop_accepted", after)

    def accept(self, *extra, reason=None):
        return self.run_cli("step", "1", "completed", "ran fewer tests",
                            *(("--accept-test-drop", reason or self.REASON) if reason != "" else ()),
                            *extra)

    def assert_open(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.step()["status"], "in_progress")

    def test_retries_and_omitting_the_flag_never_complete_and_keep_the_floor(self):
        for strict in (True, False):
            with self.subTest(strict=strict):
                self.setUp()
                self.partial_scenario(strict=strict)
                self.assert_open(self.accept())
                note = self.step()["test_drop_partial"]
                self.assert_open(self.accept(reason=""))
                self.assertIn("partial pass", self.accept(reason="").stderr)
                self.set_count(3)
                self.ok("plan", "verify", "1")
                self.assert_open(self.accept(reason="a different reason"))
                again = self.step()["test_drop_partial"]
                self.assertEqual((again["baseline"], again["current"], again["reason"]),
                                 (4, 3, "a different reason"))
                self.assertEqual(again["baseline_id"], note["baseline_id"])
                self.assert_open(self.accept(reason=""))
                self.assertNotIn("completed_count", self.step()["test_drop_partial"])

    def test_a_full_run_at_the_floor_completes_and_keeps_the_audit_note(self):
        self.partial_scenario()
        self.assert_open(self.accept())
        self.set_count(4)
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "4 tests ran")
        step = self.step()
        self.assertEqual(step["status"], "completed")
        self.assertEqual(step["test_drop_partial"]["reason"], self.REASON)
        self.assertEqual((step["test_drop_partial"]["baseline"], step["test_drop_partial"]["current"],
                          step["test_drop_partial"]["completed_count"]), (4, 2, 4))
        self.assertNotIn("test_drop_accepted", step)

    def test_non_strict_plans_record_the_partial_and_then_hold_the_floor(self):
        self.partial_scenario(strict=False)
        self.assert_open(self.accept())
        self.assertEqual(self.step()["test_drop_partial"]["current"], 2)
        refused = self.accept(reason="")
        self.assert_open(refused)
        self.assertIn("holds a partial pass", refused.stderr)

    def test_the_floor_survives_compaction_of_the_high_count_run(self):
        self.partial_scenario()
        self.assert_open(self.accept())
        path = self.state / "verifications.jsonl"
        before = self.records()
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), len(before))
        # Compaction archived the count-4 run and its carried drop annotation.
        kept = [{k: v for k, v in row.items() if k != "test_count_drop"}
                for row in rows if row.get("test_count") != 4]
        self.assertLess(len(kept), len(before))
        path.write_text("".join(json.dumps(row) + "\n" for row in kept), encoding="utf-8")
        retained = self.records()
        self.assertEqual(retained, kept)
        self.assertNotIn(4, [r.get("test_count") for r in retained])
        self.assertTrue(all("test_count_drop" not in r for r in retained))
        self.assert_open(self.accept(reason=""))
        self.set_count(3)
        self.ok("plan", "verify", "1")
        appended = self.records()
        self.assertEqual(len(appended), len(kept) + 1)
        self.assertEqual(appended[-1]["test_count"], 3)
        self.assertNotIn("test_count_drop", appended[-1])
        missing = self.accept(reason="")
        self.assert_open(missing)
        self.assertIn("below the baseline of 4", missing.stderr)
        self.assert_open(self.accept(reason=""))
        self.assertEqual(self.step()["test_drop_partial"]["baseline"], 4)
        self.set_count(4)
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "4 tests ran")

    def test_a_count_free_run_or_an_unrelated_verifier_never_completes_a_partial(self):
        self.ok("plan", "create", "goal", "--name", "p", "--steps",
                json.dumps([{"title": "only step"}]))
        self.ok("campaign", "start", "goal", "--plan", "p")
        self.ok("step", "1", "in_progress")
        self.set_count(4)
        self.ok("verify", "run", self.command)
        self.set_count(2)
        self.ok("verify", "run", self.command)
        self.assert_open(self.accept())
        self.set_count("many")
        self.ok("verify", "run", self.command)
        self.assertNotIn("test_count", self.records()[-1])
        count_free = self.accept(reason="")
        self.assert_open(count_free)
        self.assertIn("no test count", count_free.stderr)
        script = "print('unrelated')"
        self.ok("verify", "run", "{0} -c {1}".format(shlex.quote(sys.executable), shlex.quote(script)))
        unrelated = self.accept(reason="")
        self.assert_open(unrelated)
        # The partial adopted the judged verifier, so an unrelated run is ignored
        # and the count-free run of the held command still decides.
        self.assertIn("no test count", unrelated.stderr)
        self.assertIn("test_drop_partial", self.step())
        self.assertEqual(self.step()["verify_command"], self.command)
        self.assertNotIn("verify_command_literal", self.step())

    def test_stale_evidence_never_stamps_an_acceptance(self):
        for strict in (True, False):
            with self.subTest(strict=strict):
                self.setUp()
                self.partial_scenario(strict=strict)
                (self.project / "later.txt").write_text("edit\n", encoding="utf-8")
                refused = self.accept()
                self.assert_open(refused)
                self.assertNotIn("test_drop_partial", self.step())
                self.assertNotIn("Partial pass recorded", refused.stderr)
                self.assertIn("No partial pass was recorded", refused.stderr)

    def test_a_recorded_partial_refuses_a_count_restoring_run_on_moved_source(self):
        self.partial_scenario(strict=False)
        self.assert_open(self.accept())
        self.set_count(4)
        self.ok("plan", "verify", "1")
        (self.project / "later.txt").write_text("edit\n", encoding="utf-8")
        stale = self.accept(reason="")
        self.assert_open(stale)
        self.assertIn("Stale evidence for a recorded partial pass", stale.stderr)
        self.assertNotIn("completed_count", self.step()["test_drop_partial"])
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "4 tests ran")
        self.assertEqual(self.step()["status"], "completed")

    def named_scenario(self, verify_command):
        """Check 'suite' (this project's command); floor 4 recorded, then a run of 2."""
        self.ok("verify", "check", "suite", "--set", self.command)
        item = {"title": "only step"}
        if verify_command:
            item["verify_command"] = verify_command
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps([item]))
        self.ok("step", "1", "in_progress")
        self.set_count(4)
        self.ok("verify", "check", "suite")
        self.set_count(2)
        self.ok("verify", "check", "suite")

    def test_a_named_partial_is_bound_to_the_check_template(self):
        self.named_scenario("@suite")
        self.assert_open(self.accept())
        note = self.step()["test_drop_partial"]
        self.assertEqual((note["check"], note["check_template"]), ("suite", self.command))
        other = "{0} -c {1}".format(shlex.quote(sys.executable),
                                    shlex.quote("import sys; sys.stderr.write('Ran 9 tests\\nOK\\n')"))
        self.ok("verify", "check", "suite", "--set", other)
        self.ok("verify", "check", "suite")
        for flag in ("", "--accept-test-drop"):
            args = ("step", "1", "completed", "x") + ((flag, "again") if flag else ())
            self.assert_open(self.run_cli(*args))
            self.assertEqual(self.step()["test_drop_partial"], note)
        self.ok("verify", "check", "suite", "--set", self.command)
        self.set_count(4)
        self.ok("verify", "check", "suite")
        self.ok("step", "1", "completed", "4 tests ran")
        self.assertEqual(self.step()["status"], "completed")

    def test_a_redefined_check_cannot_complete_a_named_partial_with_no_stored_verifier(self):
        self.named_scenario(None)
        first = self.accept()
        self.assert_open(first)
        self.assertIn("Partial pass recorded", first.stderr)
        note = self.step()["test_drop_partial"]
        other = "{0} -c {1}".format(shlex.quote(sys.executable),
                                    shlex.quote("import sys; sys.stderr.write('Ran 9 tests\\nOK\\n')"))
        self.ok("verify", "check", "suite", "--set", other)
        self.ok("verify", "check", "suite")
        refused = self.accept(reason="")
        self.assert_open(refused)
        self.assertIn("was redefined", refused.stderr)
        self.assertIn("verify check suite --set", refused.stderr)
        self.assert_open(self.accept(reason="other"))
        self.assertEqual(self.step()["test_drop_partial"], note)
        self.ok("verify", "check", "suite", "--set", self.command)
        self.set_count(4)
        self.ok("verify", "check", "suite")
        self.ok("step", "1", "completed", "4 tests ran")

    def test_a_literal_step_is_bound_to_its_command_not_to_a_check_field(self):
        self.named_scenario(self.command)
        self.assertTrue(all(r.get("check") == "suite" for r in self.records()))
        self.assert_open(self.accept())
        note = self.step()["test_drop_partial"]
        self.assertNotIn("check", note)
        self.assertEqual(note["command"], self.command)
        self.set_count(4)
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "4 tests ran")
        self.assertEqual(self.step()["status"], "completed")

    def test_a_literal_command_that_looks_like_a_check_name_stays_literal(self):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        tool = bindir / "@suite"
        tool.write_text("#!/bin/sh\nprintf 'Ran %s tests\\nOK\\n' \"$(cat count.txt)\" >&2\n",
                        encoding="utf-8")
        tool.chmod(0o755)
        env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
        # A distinct registered check of the same name exists and must never run.
        self.ok("verify", "check", "suite", "--set", self.command)
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps([{"title": "only"}]))
        self.ok("step", "1", "in_progress")
        self.set_count(4)
        self.ok("verify", "run", "@suite", env_extra=env)
        self.set_count(2)
        self.ok("verify", "run", "@suite", env_extra=env)
        recorded = self.run_cli("step", "1", "completed", "fewer", "--accept-test-drop", "why",
                                env_extra=env)
        self.assertEqual(recorded.returncode, 1, recorded.stdout + recorded.stderr)
        step = self.step()
        self.assertEqual((step["verify_command"], step["verify_command_literal"]), ("@suite", True))
        self.assertNotIn("check", step["test_drop_partial"])
        # Competing open plans: q stores the named check @suite, s stores @alias, a
        # check whose template text is the literal command "@suite".
        self.ok("verify", "check", "alias", "--set", "@suite")
        for name, stored in (("q", "@suite"), ("s", "@alias")):
            self.ok("plan", "create", name, "--name", name, "--steps",
                    json.dumps([{"title": name, "verify_command": stored}]))
            self.ok("step", "1", "in_progress", "--plan", name)
        # A named run whose command text is "@suite" belongs to s, never to the held
        # literal alias in p (the active plan), and does not restore p's floor.
        self.ok("plan", "switch", "p")
        self.set_count(4)
        self.ok("verify", "check", "alias", env_extra=env)
        named = self.records()[-1]
        self.assertEqual((named["command"], named["check"], named["plan"]), ("@suite", "alias", "s"))
        refused = self.run_cli("step", "1", "completed", "x", "--plan", "p", env_extra=env)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertEqual(self.step()["status"], "in_progress")
        # A generic `verify run @suite` belongs to the held literal alias in p, not to
        # the named @suite step in q even though q is the active plan.
        self.ok("plan", "switch", "q")
        self.ok("verify", "run", "@suite", env_extra=env)
        generic = self.records()[-1]
        self.assertEqual((generic["command"], generic["plan"], generic["step_id"]), ("@suite", "p", 1))
        self.assertNotIn("check", generic)
        self.set_count(4)
        self.ok("plan", "verify", "1", "--plan", "p", env_extra=env)
        last = self.records()[-1]
        self.assertEqual((last["command"], last["plan"], last["step_id"]), ("@suite", "p", 1))
        self.assertNotIn("check", last)
        self.ok("step", "1", "completed", "4 tests ran", "--plan", "p", env_extra=env)
        self.assertEqual(self.plan("q")["steps"][0]["status"], "in_progress")
        self.assertTrue(all("check" not in r for r in self.records() if r.get("plan") == "p"))

    def test_explicit_files_on_a_clean_tree_run_for_the_selected_step(self):
        template = self.command + " {files}"
        self.ok("verify", "check", "suite", "--set", template)
        self.assertEqual(self.git_status(), "")
        steps = json.dumps([{"title": "s", "verify_command": "@suite"}])
        self.ok("plan", "create", "goal", "--name", "p", "--steps", steps)
        self.ok("plan", "create", "other", "--name", "q", "--steps", steps)
        self.ok("step", "1", "in_progress", "--plan", "q")
        self.ok("step", "1", "in_progress", "--plan", "p")
        none = self.ok("plan", "verify", "1", "--plan", "p")
        self.assertIn("No changed files", none.stdout)
        self.assertEqual(self.executions(), 0)
        self.ok("plan", "verify", "1", "--plan", "p", "--files", "count.txt")
        last = self.records()[-1]
        self.assertEqual((last["plan"], last["step_id"], last["check"]), ("p", 1, "suite"))
        self.assertIn("count.txt", last["command"])
        self.assertEqual(last["check_template"], template)
        self.assertEqual(self.executions(), 1)

    def test_files_are_refused_for_a_literal_verifier_before_anything_runs(self):
        self.create_plan()
        before = (self.state / "plans" / "p.json").read_bytes()
        refused = self.run_cli("plan", "verify", "1", "--plan", "p", "--files", "count.txt")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("--files only applies", refused.stderr)
        self.assertEqual(self.executions(), 0)
        self.assertEqual((self.state / "plans" / "p.json").read_bytes(), before)
        self.assertFalse((self.state / "verifications.jsonl").exists()
                         and self.records())

    def test_a_flagged_retry_on_a_completed_step_writes_no_partial(self):
        self.create_plan()
        self.set_count(4)
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "4 tests ran")
        self.set_count(2)
        self.ok("plan", "verify", "1")
        before = self.plan()
        refused = self.accept()
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("already completed", refused.stderr)
        self.assertNotIn("Partial pass recorded", refused.stderr)
        self.assertEqual(self.plan(), before)
        self.set_count(4)
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "4 tests ran again")
        self.assertEqual(self.step()["status"], "completed")

    def test_ordinary_non_strict_stale_evidence_still_only_warns(self):
        self.partial_scenario(strict=False, floor=2, partial=2)
        (self.project / "later.txt").write_text("edit\n", encoding="utf-8")
        done = self.ok("step", "1", "completed", "x")
        self.assertIn("[WARN] The world moved", done.stderr)

    def test_the_legacy_waiver_still_works_without_the_flag_after_a_partial(self):
        self.partial_scenario(strict=False)
        self.assert_open(self.accept())
        done = self.run_cli("step", "1", "completed", "prose only",
                            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        step = self.step()
        self.assertEqual((step["status"], step["strict_gate_waived"]), ("completed", True))
        self.assertEqual(step["test_drop_partial"]["current"], 2)

    def test_the_flag_cannot_be_combined_with_the_legacy_waiver(self):
        self.partial_scenario(strict=False)
        before = self.step()
        refused = self.run_cli("step", "1", "completed", "prose only",
                               "--accept-test-drop", self.REASON,
                               env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"})
        self.assert_open(refused)
        self.assertIn("cannot be combined", refused.stderr)
        self.assertEqual(self.step(), before)

    def test_ordered_campaign_advance_records_without_moving_the_checkpoint(self):
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps(
            [{"title": "first", "verify_command": self.command},
             {"title": "second", "verify_command": self.command}]))
        self.set_count(4)
        self.ok("verify", "run", self.command)
        self.git("add", "-A")
        self.git("commit", "-qm", "baseline of four")
        self.ok("campaign", "start", "goal", "--plan", "p", "--allowed-paths", "count.txt")
        self.ok("step", "1", "in_progress")
        self.set_count(2)
        self.ok("plan", "verify", "1")
        before = self.plan()
        checkpoint = before["campaign"]["scope"]["checkpoint"]
        self.assertTrue(checkpoint)
        refused = self.run_cli("campaign", "advance", "--result", "fewer",
                               "--accept-test-drop", self.REASON)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        after = self.plan()
        note = after["steps"][0].pop("test_drop_partial", None)
        self.assertEqual((note["baseline"], note["current"]), (4, 2))
        self.assertEqual(after, before)
        self.assertEqual(after["campaign"]["scope"]["checkpoint"], checkpoint)
        self.assertEqual([s["status"] for s in after["steps"]], ["in_progress", "pending"])

    def test_recovery_is_scoped_to_the_held_step_when_another_plan_runs_the_check(self):
        self.named_scenario(None)
        self.assert_open(self.accept())
        # Another open plan stores @suite: a bare `verify check suite` now binds there.
        self.ok("plan", "create", "other", "--name", "q", "--steps", json.dumps(
            [{"title": "other step", "verify_command": "@suite"}]))
        self.ok("step", "1", "in_progress", "--plan", "q")
        self.set_count(4)
        self.ok("plan", "verify", "1", "--plan", "q")
        self.assertEqual((self.records()[-1]["plan"], self.records()[-1]["test_count"]), ("q", 4))
        refused = self.run_cli("step", "1", "completed", "x", "--plan", "p")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("recorded for plan q step 1", refused.stderr)
        self.assertEqual(self.plan("p")["steps"][0]["status"], "in_progress")
        gate = json.loads(self.run_cli("verify", "gate", "--plan", "p", "--json").stdout)
        self.assertFalse(gate["steps"][0]["ok"])
        hint = re.search(r"mythify (plan verify \d+ --plan \S+)", gate["steps"][0]["next"])
        self.assertEqual(hint.group(1), "plan verify 1 --plan p")
        self.ok(*hint.group(1).split())
        last = self.records()[-1]
        self.assertEqual((last["plan"], last["step_id"], last["check"], last["test_count"]),
                         ("p", 1, "suite", 4))
        self.assertEqual(self.run_cli("verify", "gate", "--plan", "p").returncode, 0)
        self.ok("step", "1", "completed", "4 tests ran", "--plan", "p")
        self.assertEqual(self.plan("p")["steps"][0]["status"], "completed")
        self.assertEqual(self.plan("q")["steps"][0]["status"], "in_progress")

    def test_replacing_the_held_check_alone_does_not_clear_a_named_partial(self):
        self.named_scenario(None)
        self.assert_open(self.accept())
        self.set_count(4)
        self.ok("verify", "check", "suite")
        other = "{0} -c {1}".format(shlex.quote(sys.executable),
                                    shlex.quote("import sys; sys.stderr.write('Ran 9 tests\\nOK\\n')"))
        self.ok("verify", "check", "suite", "--set", other)
        refused = self.accept(reason="")
        self.assert_open(refused)
        self.assertIn("changed since the run", refused.stderr)
        self.ok("verify", "check", "suite", "--set", self.command)
        self.ok("verify", "check", "suite")
        self.ok("step", "1", "completed", "4 tests ran")
        self.assertEqual(self.step()["status"], "completed")


class ZeroTestGateTests(GitProjectCase):
    """A green run that executed zero tests never completes a step."""

    def start_plan(self, command=None, strict=False):
        command = command or self.command
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps(
            [{"title": "only step", "verify_command": command}]))
        if strict:
            self.ok("campaign", "start", "goal", "--plan", "p")

    def step(self):
        return self.plan()["steps"][0]

    def assert_zero_refused(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("zero tests", result.stderr)
        self.assertNotIn("--accept-test-drop", result.stderr)
        self.assertNotEqual(self.step()["status"], "completed")
        self.assertNotIn("test_drop_accepted", self.step())
        self.assertNotIn("test_drop_partial", self.step())

    def test_zero_after_a_floor_is_refused_even_with_a_reason_and_recovers(self):
        self.start_plan(strict=True)
        self.set_count(4)
        self.ok("verify", "run", self.command)
        self.set_count(0)
        self.ok("plan", "verify", "1")
        gate = self.run_cli("verify", "gate")
        self.assertEqual(gate.returncode, 2, gate.stdout + gate.stderr)
        self.assertIn("zero tests", gate.stderr)
        self.assertIn("mythify plan verify 1 --plan p", gate.stderr)
        self.assertNotIn("--accept-test-drop", gate.stderr)
        self.assert_zero_refused(self.run_cli(
            "step", "1", "completed", "x", "--accept-test-drop", "tests were deleted"))
        self.set_count(6)
        self.ok("plan", "verify", "1")
        self.assertEqual(self.run_cli("verify", "gate").returncode, 0)
        self.ok("step", "1", "completed", "6 tests ran")
        self.assertEqual(self.step()["status"], "completed")

    def test_zero_without_a_baseline_is_refused_by_default(self):
        self.start_plan()
        self.set_count(0)
        self.ok("plan", "verify", "1")
        self.assert_zero_refused(self.run_cli("step", "1", "completed", "x"))
        self.assert_zero_refused(self.run_cli(
            "step", "1", "completed", "x", "--accept-test-drop", "nothing to run"))

    def test_campaign_advance_refuses_zero_with_a_reason(self):
        self.start_plan(strict=True)
        self.set_count(0)
        self.ok("plan", "verify", "1")
        self.assert_zero_refused(self.run_cli(
            "campaign", "advance", "--result", "x", "--accept-test-drop", "nothing to run"))

    def test_recognized_zero_output_without_a_count_is_refused(self):
        script = "import sys; sys.stderr.write('No tests ran\\n')"
        command = "{0} -c {1}".format(shlex.quote(sys.executable), shlex.quote(script))
        self.start_plan(command)
        self.ok("plan", "verify", "1")
        self.assertNotIn("test_count", self.records()[-1])
        self.assert_zero_refused(self.run_cli("step", "1", "completed", "x"))
        gate = self.run_cli("verify", "gate")
        self.assertEqual(gate.returncode, 2)
        self.assertNotIn("--accept-test-drop", gate.stderr)

    def uncounted_output_command(self, text):
        script = "import sys; sys.stdout.write({0!r})".format(text)
        return "{0} -c {1}".format(shlex.quote(sys.executable), shlex.quote(script))

    def test_mixed_go_output_completes(self):
        mixed = ("?    example.com/project/cmd    [no test files]\n"
                 "ok   example.com/project/pkg    0.022s\n")
        self.start_plan(self.uncounted_output_command(mixed))
        self.ok("plan", "verify", "1")
        self.assertNotIn("test_count", self.records()[-1])
        self.assertEqual(self.run_cli("verify", "gate").returncode, 0)
        self.ok("step", "1", "completed", "pkg tests ran")

    def test_a_package_only_tail_does_not_hide_a_passing_package_truncated_away(self):
        # Seven extra characters in the last package name move the 4000-char
        # tail start so it begins exactly at "no test files]": the leading
        # bracket of the first visible marker is clipped away.
        names = ["cmd{0:03d}".format(i) for i in range(120)]
        names[-1] += "padding"
        output = "ok   example.com/project/pkg    0.022s\n" + "".join(
            "?    example.com/project/{0}    [no test files]\n".format(name)
            for name in names)
        self.start_plan(self.uncounted_output_command(output))
        self.ok("plan", "verify", "1")
        record = self.records()[-1]
        self.assertNotIn("test_count", record)
        self.assertTrue(record["stdout_tail"].startswith("no test files]\n"),
                        record["stdout_tail"][:40])
        self.assertNotIn("ok   example.com/project/pkg", record["stdout_tail"])
        self.assertNotIn("\nok ", "\n" + record["stdout_tail"])
        self.assertEqual(self.run_cli("verify", "gate").returncode, 0)
        self.ok("step", "1", "completed", "pkg tests ran")

    def test_run_level_no_test_files_with_a_banner_is_refused(self):
        banner = " RUN  v1.6.0 /work/project\n\nNo test files found, exiting with code 0\n"
        self.start_plan(self.uncounted_output_command(banner))
        self.ok("plan", "verify", "1")
        self.assert_zero_refused(self.run_cli("step", "1", "completed", "x"))

    def test_ten_passing_is_not_read_as_zero_passing(self):
        self.start_plan(self.uncounted_output_command("  10 passing (20ms)\n"))
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "10 mocha tests ran")

    def test_a_check_with_no_count_and_no_zero_output_still_completes(self):
        command = "{0} -c pass".format(shlex.quote(sys.executable))
        self.start_plan(command)
        self.ok("plan", "verify", "1")
        self.assertEqual(self.run_cli("verify", "gate").returncode, 0)
        self.ok("step", "1", "completed", "check passed")

    def test_legacy_opt_out_is_not_the_drop_override(self):
        self.start_plan()
        self.set_count(0)
        self.ok("plan", "verify", "1")
        done = self.run_cli("step", "1", "completed", "x",
                            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.step()["strict_gate_waived"])


if __name__ == "__main__":
    unittest.main()
