"""Evidence reuse (`--reuse`) and the test-count ratchet, through the real CLI."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subprocess_env  # noqa: E402,F401  (faster subprocesses, same behavior)
import warm_cli  # noqa: E402  (runs the CLI forked from a warm interpreter)

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

    def set_count(self, count):
        (self.project / "count.txt").write_text("{0}\n".format(count), encoding="utf-8")

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        for name in ("MYTHIFY_DIR", "MYTHIFY_REQUIRE_VERIFIED_STEP", "MYTHIFY_DISABLE_RUN",
                     "MYTHIFY_REUSE_ENV"):
            env.pop(name, None)
        env["HOME"] = str(self.home)
        env.update(env_extra or {})
        return warm_cli.run(list(args), cwd=str(self.project), env=env, timeout=120)

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

    def test_gate_passes_with_acceptance_and_stamps_the_step(self):
        self.drop_scenario(strict=True)
        done = self.ok("step", "1", "completed", "ran fewer tests",
                       "--accept-test-drop", "obsolete tests were deleted")
        self.assertIn("accepted", done.stderr)
        step = self.plan()["steps"][0]
        self.assertEqual(step["status"], "completed")
        self.assertEqual(step["test_drop_accepted"],
                         {"reason": "obsolete tests were deleted", "baseline": 5, "current": 3})

    def test_campaign_advance_takes_the_same_flag(self):
        self.drop_scenario(strict=True)
        refused = self.run_cli("campaign", "advance", "--result", "ran fewer tests")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Test count dropped under strict context", refused.stderr)
        self.ok("campaign", "advance", "--result", "ran fewer tests",
                "--accept-test-drop", "obsolete tests were deleted")
        self.assertEqual(self.plan()["steps"][0]["test_drop_accepted"]["current"], 3)

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
        self.assertIsNone(denied["test_drop_accepted"])
        allowed = evaluate_step_evidence(self.state, "p", plan, step, "deleted obsolete tests")
        self.assertTrue(allowed["ok"], allowed["messages"])
        self.assertEqual(allowed["test_drop_accepted"],
                         {"reason": "deleted obsolete tests", "baseline": 5, "current": 3})
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
        baseline = step_baseline(records_before_window(ledger, [inside]), [inside], "c")
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
        self.assertEqual(step_baseline([record("a", 10)], [record("bb", 8)], "c"), (10, "a"))
        self.assertEqual(step_baseline([], [record("bb", 8), record("ccc", 6)], "c"), (8, "bb"))
        self.assertIsNone(step_baseline([], [], "c"))
        dropped = record("dd", 7, test_count_drop={"previous": 10, "previous_id": "a", "current": 7})
        reuse = record("eee", 7, reused_from="dd")
        self.assertEqual(len(latest_test_count_drops(log + [dropped])), 1)
        self.assertEqual(len(latest_test_count_drops(log + [dropped, reuse])), 1)
        self.assertEqual(latest_test_count_drops(log + [dropped, record("ffff", 11)]), [])


if __name__ == "__main__":
    unittest.main()
