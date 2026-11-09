"""Named checks: `verify check`, `.mythify/checks.json`, and `@NAME` step verifiers."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "mythify.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from mythify_checks import (  # noqa: E402
    add_checks_line,
    check_reference,
    load_checks,
    verifier_matches,
)

SHOW_SCRIPT = """\
import sys

for name in sys.argv[1:]:
    print("FILE:" + name)
"""

# Exits 1 when it is handed a file called bad.txt, so one template can pass and fail.
PICKY_SCRIPT = """\
import sys

sys.exit(1 if "bad.txt" in sys.argv[1:] else 0)
"""


class CheckProject(unittest.TestCase):
    use_git = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.project = base / "project"
        self.project.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        self.state = self.project / ".mythify"
        if self.use_git:
            self.git("init", "-q")
            self.git("config", "user.email", "mythify@example.invalid")
            self.git("config", "user.name", "Mythify Test")
        (self.project / "show.py").write_text(SHOW_SCRIPT, encoding="utf-8")
        (self.project / "picky.py").write_text(PICKY_SCRIPT, encoding="utf-8")
        (self.project / "tracked.txt").write_text("one\n", encoding="utf-8")
        (self.project / "gone.txt").write_text("soon deleted\n", encoding="utf-8")
        self.ok("init")
        if self.use_git:
            self.git("add", "-A")
            self.git("commit", "-qm", "initial")
        self.show = "{0} show.py {{files}}".format(shlex.quote(sys.executable))
        self.picky = "{0} picky.py {{files}}".format(shlex.quote(sys.executable))

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=str(self.project), check=True, capture_output=True, text=True
        ).stdout.strip()

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

    def records(self):
        path = self.state / "verifications.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def plan(self, name="p"):
        return json.loads((self.state / "plans" / (name + ".json")).read_text(encoding="utf-8"))

    def define(self, name, command, *extra):
        return self.ok("verify", "check", name, "--set", command, *extra)

    def write(self, name, text="x\n"):
        (self.project / name).write_text(text, encoding="utf-8")

    def create_check_plan(self, verify="@fast"):
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps(
            [{"title": "only step", "verify_command": verify}]))


class DefineAndListTests(CheckProject):
    def test_list_with_no_checks_says_how_to_add_one(self):
        result = self.ok("verify", "check")
        self.assertIn("No checks defined", result.stdout)
        self.assertIn("verify check NAME --set COMMAND", result.stdout)
        self.assertFalse((self.state / "checks.json").exists())

    def test_set_defines_without_running_and_list_shows_it(self):
        result = self.define("fast", "test -f tracked.txt", "--description", "the quick one")
        self.assertIn("[OK] Defined check fast: test -f tracked.txt", result.stdout)
        self.assertEqual(self.records(), [])
        stored = json.loads((self.state / "checks.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["schema_version"], 1)
        entry = stored["checks"]["fast"]
        self.assertEqual(entry["command"], "test -f tracked.txt")
        self.assertEqual(entry["description"], "the quick one")
        self.assertRegex(entry["updated"], r"^\d{4}-\d\d-\d\dT")
        listed = self.ok("verify", "check")
        self.assertIn("fast: test -f tracked.txt - the quick one", listed.stdout)
        as_json = json.loads(self.ok("verify", "check", "--json").stdout)
        self.assertEqual(as_json["checks"]["fast"]["command"], "test -f tracked.txt")

    def test_set_replaces_and_keeps_the_description_unless_given(self):
        self.define("fast", "test -f tracked.txt", "--description", "the quick one")
        replaced = self.define("fast", "test -f show.py")
        self.assertIn("[OK] Replaced check fast: test -f show.py", replaced.stdout)
        entry = load_checks(self.state)["fast"]
        self.assertEqual(entry["command"], "test -f show.py")
        self.assertEqual(entry["description"], "the quick one")
        self.define("fast", "test -f show.py", "--description", "now different")
        self.assertEqual(load_checks(self.state)["fast"]["description"], "now different")
        self.assertEqual(list(load_checks(self.state)), ["fast"])

    def test_set_refuses_a_no_op_command(self):
        for command in ("true", "exit 0", "echo ok", "pytest || true", "  "):
            result = self.run_cli("verify", "check", "fast", "--set", command)
            self.assertEqual(result.returncode, 1, command)
            self.assertIn("[FAIL] Check 'fast' not defined", result.stderr)
        self.assertFalse((self.state / "checks.json").exists())

    def test_set_refuses_a_bad_name(self):
        for name in ("Fast", "has space", "under_score", "dot.name"):
            result = self.run_cli("verify", "check", name, "--set", "test -f tracked.txt")
            self.assertEqual(result.returncode, 1, name)
            self.assertIn("Invalid check name", result.stderr)
        self.assertFalse((self.state / "checks.json").exists())

    def test_flags_that_need_a_name_or_set_are_refused(self):
        for args in (("--set", "test -f tracked.txt"), ("--description", "d"), ("--files", "a.txt")):
            result = self.run_cli("verify", "check", *args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn("Name the check", result.stderr)
        self.define("fast", "test -f tracked.txt")
        result = self.run_cli("verify", "check", "fast", "--description", "d")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--description only applies with --set", result.stderr)

    def test_unknown_name_exits_1_and_lists_the_defined_names(self):
        self.define("fast", "test -f tracked.txt")
        self.define("full", "test -f show.py")
        result = self.run_cli("verify", "check", "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown check 'nope'. Defined checks: fast, full.", result.stderr)
        self.assertEqual(self.records(), [])

    def test_a_damaged_file_reads_as_no_checks_without_moving_it(self):
        (self.state / "checks.json").write_text("{not json", encoding="utf-8")
        self.assertIn("No checks defined", self.ok("verify", "check").stdout)
        self.assertTrue((self.state / "checks.json").exists())
        self.define("fast", "test -f tracked.txt")
        self.assertEqual(list(load_checks(self.state)), ["fast"])

    def test_check_reference_and_matching(self):
        self.assertEqual(check_reference("@fast"), "fast")
        self.assertEqual(check_reference("  @fast-2 "), "fast-2")
        for text in ("fast", "@", "@Fast", "@a b", "", None):
            self.assertIsNone(check_reference(text), text)
        self.assertTrue(verifier_matches("@fast", "ls x", "fast"))
        self.assertFalse(verifier_matches("@fast", "ls x", "full"))
        self.assertFalse(verifier_matches("@fast", "ls x"))
        self.assertTrue(verifier_matches("ls x", "ls x"))
        self.assertFalse(verifier_matches("", ""))


class RunTests(CheckProject):
    def test_pass_exits_0_and_the_record_carries_the_check(self):
        self.define("fast", "test -f tracked.txt")
        result = self.run_cli("verify", "check", "fast")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] VERIFIED: check fast (exit 0", result.stdout)
        (record,) = self.records()
        self.assertEqual(record["kind"], "executed")
        self.assertEqual(record["claim"], "check fast")
        self.assertEqual(record["command"], "test -f tracked.txt")
        self.assertEqual(record["check"], "fast")
        self.assertEqual(record["check_template"], "test -f tracked.txt")
        self.assertTrue(record["verified"])

    def test_fail_exits_2_and_still_records(self):
        self.define("fast", "test -f missing.txt")
        result = self.run_cli("verify", "check", "fast", "--claim", "needs the file")
        self.assertEqual(result.returncode, 2)
        self.assertIn("[FAIL] UNVERIFIED: needs the file (exit 1", result.stdout)
        (record,) = self.records()
        self.assertEqual(record["claim"], "needs the file")
        self.assertEqual(record["check"], "fast")
        self.assertFalse(record["verified"])

    def test_json_result(self):
        self.define("fast", "test -f tracked.txt")
        result = self.ok("verify", "check", "fast", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["check"], "fast")
        self.assertTrue(payload["ran"] and payload["ok"])
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["record_id"], self.records()[0]["id"])

    def test_files_expands_changed_files_with_quoting(self):
        self.define("show", self.show)
        self.write("a b.txt")
        self.write("c.txt")
        self.write("tracked.txt", "two\n")
        (self.project / "gone.txt").unlink()
        result = self.ok("verify", "check", "show", "--claim", "list")
        (record,) = self.records()
        self.assertIn("'a b.txt'", record["command"])
        self.assertNotIn("gone.txt", record["command"])
        self.assertNotIn(".mythify", record["command"])
        self.assertEqual(record["check_template"], self.show)
        shown = [line for line in record["stdout_tail"].splitlines() if line.startswith("FILE:")]
        self.assertEqual(shown, ["FILE:a b.txt", "FILE:c.txt", "FILE:tracked.txt"])
        self.assertIn("VERIFIED: list", result.stdout)

    def test_a_hostile_file_name_is_quoted_not_executed(self):
        self.define("show", self.show)
        self.write("x; touch pwned.txt")
        self.ok("verify", "check", "show")
        self.assertFalse((self.project / "pwned.txt").exists())
        self.assertIn("FILE:x; touch pwned.txt", self.records()[0]["stdout_tail"])

    def test_files_flag_overrides_the_changed_set(self):
        self.define("show", self.show)
        self.write("changed.txt")
        self.ok("verify", "check", "show", "--files", "one.txt", "two words.txt")
        (record,) = self.records()
        shown = [line for line in record["stdout_tail"].splitlines() if line.startswith("FILE:")]
        self.assertEqual(shown, ["FILE:one.txt", "FILE:two words.txt"])
        self.assertIn("'two words.txt'", record["command"])
        self.assertNotIn("changed.txt", record["command"])

    def test_empty_changed_set_prints_the_nothing_to_run_line_and_records_nothing(self):
        self.define("show", self.show)
        result = self.run_cli("verify", "check", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] No changed files for check show; nothing to run.", result.stdout)
        self.assertEqual(self.records(), [])
        payload = json.loads(self.ok("verify", "check", "show", "--json").stdout)
        self.assertEqual(payload, {"check": "show", "ran": False, "ok": True, "reason": "no changed files"})

    def test_json_keeps_stdout_clean_when_a_run_is_reused(self):
        self.define("fast", "test -f tracked.txt")
        self.ok("verify", "check", "fast")
        result = self.ok("verify", "check", "fast", "--reuse", "--json")
        self.assertEqual(json.loads(result.stdout)["reused_from"], self.records()[0]["id"])
        self.assertIn("[OK] Reused passing run", result.stderr)

    def test_a_template_without_the_placeholder_runs_even_with_no_changes(self):
        self.define("fast", "test -f tracked.txt")
        self.ok("verify", "check", "fast")
        self.assertEqual(len(self.records()), 1)
        ignored = self.run_cli("verify", "check", "fast", "--files", "x.txt")
        self.assertEqual(ignored.returncode, 0)
        self.assertIn("--files was ignored", ignored.stderr)

    def test_reuse_hit_reuses_the_check_run(self):
        self.define("fast", "test -f tracked.txt")
        self.ok("verify", "check", "fast")
        reused = self.ok("verify", "check", "fast", "--reuse")
        first, second = self.records()
        self.assertIn("[OK] Reused passing run {0}".format(first["id"]), reused.stdout)
        self.assertEqual(second["reused_from"], first["id"])
        self.assertEqual(second["check"], "fast")
        self.assertEqual(second["check_template"], "test -f tracked.txt")
        self.assertEqual(second["duration_seconds"], 0)
        self.assertIn("reused", reused.stdout.splitlines()[-1])

    def test_reuse_miss_runs_for_real(self):
        self.define("fast", "test -f tracked.txt")
        result = self.ok("verify", "check", "fast", "--reuse")
        self.assertIn("[INFO] Running the command", result.stdout)
        self.assertNotIn("reused_from", self.records()[0])

    def test_disable_run_refuses_with_exit_2_and_records_nothing(self):
        self.define("fast", "test -f tracked.txt")
        result = self.run_cli("verify", "check", "fast", env_extra={"MYTHIFY_DISABLE_RUN": "1"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("MYTHIFY_DISABLE_RUN=1", result.stderr)
        self.assertEqual(self.records(), [])
        # Listing and defining never execute anything, so they still work.
        self.assertEqual(self.run_cli("verify", "check", env_extra={"MYTHIFY_DISABLE_RUN": "1"}).returncode, 0)
        self.assertEqual(self.run_cli("verify", "check", "other", "--set", "test -f show.py",
                                      env_extra={"MYTHIFY_DISABLE_RUN": "1"}).returncode, 0)

    def test_help_lists_the_flags(self):
        text = self.ok("verify", "check", "--help").stdout
        for flag in ("--set", "--description", "--files", "--reuse", "--timeout", "--claim", "--json"):
            self.assertIn(flag, text)


class NoGitTests(CheckProject):
    use_git = False

    def test_files_placeholder_needs_git_or_the_files_flag(self):
        self.define("show", self.show)
        result = self.run_cli("verify", "check", "show")
        self.assertEqual(result.returncode, 1)
        self.assertIn("git cannot list changed files here", result.stderr)
        self.assertEqual(self.records(), [])
        self.ok("verify", "check", "show", "--files", "tracked.txt")
        self.assertEqual(len(self.records()), 1)


class StepReferenceTests(CheckProject):
    def test_plan_verify_runs_the_check_and_completes_the_step(self):
        self.define("fast", "test -f tracked.txt")
        self.create_check_plan()
        result = self.ok("plan", "verify", "1", "--plan", "p")
        self.assertIn("VERIFIED step 1: @fast", result.stdout)
        (record,) = self.records()
        self.assertEqual(record["check"], "fast")
        self.assertEqual(record["command"], "test -f tracked.txt")
        self.assertEqual((record["plan"], record["step_id"]), ("p", 1))
        self.assertEqual(record["claim"], "step 1: only step")
        done = self.ok("step", "1", "completed", "check fast passed", "--plan", "p")
        self.assertEqual(self.plan()["steps"][0]["status"], "completed", done.stderr)

    def test_plan_verify_failure_exits_2(self):
        self.define("fast", "test -f missing.txt")
        self.create_check_plan()
        result = self.run_cli("plan", "verify", "1", "--plan", "p")
        self.assertEqual(result.returncode, 2)
        self.assertIn("UNVERIFIED step 1", result.stdout)
        refused = self.run_cli("step", "1", "completed", "x", "--plan", "p")
        self.assertEqual(refused.returncode, 1)

    def test_plan_verify_of_an_undefined_check_is_refused(self):
        self.create_check_plan("@fast")
        result = self.run_cli("plan", "verify", "1", "--plan", "p")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown check 'fast'", result.stderr)
        self.assertEqual(self.records(), [])

    def test_verify_check_counts_for_the_in_progress_step(self):
        self.define("fast", "test -f tracked.txt")
        self.create_check_plan()
        self.ok("step", "1", "in_progress", "--plan", "p")
        self.ok("verify", "check", "fast")
        (record,) = self.records()
        self.assertEqual((record["plan"], record["step_id"]), ("p", 1))
        self.ok("step", "1", "completed", "check fast passed", "--plan", "p")
        self.assertEqual(self.plan()["steps"][0]["status"], "completed")

    def test_the_step_is_found_in_another_plan_by_its_reference(self):
        self.define("fast", "test -f tracked.txt")
        self.create_check_plan()
        self.ok("step", "1", "in_progress", "--plan", "p")
        self.ok("plan", "create", "other", "--name", "q", "--steps", json.dumps([{"title": "x"}]))
        self.ok("step", "1", "in_progress", "--plan", "q")
        self.ok("verify", "check", "fast")
        (record,) = self.records()
        self.assertEqual((record["plan"], record["step_id"]), ("p", 1))

    def test_a_run_for_a_different_check_does_not_satisfy_the_step(self):
        self.define("fast", "test -f tracked.txt")
        self.define("full", "test -f show.py")
        self.create_check_plan("@fast")
        self.ok("step", "1", "in_progress", "--plan", "p")
        self.ok("verify", "check", "full")
        refused = self.run_cli("step", "1", "completed", "wrong check", "--plan", "p")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Verified evidence required", refused.stderr)
        # The same command text run as a plain verify run is not the check either.
        self.ok("verify", "run", "test -f tracked.txt")
        self.assertEqual(self.run_cli("step", "1", "completed", "plain", "--plan", "p").returncode, 1)
        self.ok("verify", "check", "fast")
        self.ok("step", "1", "completed", "right check", "--plan", "p")

    def test_a_later_failing_run_of_the_check_cancels_an_earlier_pass(self):
        self.define("picky", self.picky)
        self.create_check_plan("@picky")
        self.ok("step", "1", "in_progress", "--plan", "p")
        self.ok("verify", "check", "picky", "--files", "good.txt")
        self.assertEqual(self.run_cli("verify", "check", "picky", "--files", "bad.txt").returncode, 2)
        refused = self.run_cli("step", "1", "completed", "x", "--plan", "p")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("exited 1", refused.stderr)
        # A pass with different expanded text clears it: the check is the key.
        self.ok("verify", "check", "picky", "--files", "other.txt")
        self.ok("step", "1", "completed", "x", "--plan", "p")

    def test_plan_create_and_add_step_warn_when_the_check_is_not_defined(self):
        created = self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps(
            [{"title": "s", "verify_command": "@fast"}]))
        self.assertIn("[WARN] Step 1 verify command @fast names a check that is not defined yet",
                      created.stderr)
        added = self.ok("plan", "add-step", "second", "--verify", "@full", "--plan", "p")
        self.assertIn("Step 2 verify command @full names a check that is not defined yet", added.stderr)
        self.define("fast", "test -f tracked.txt")
        quiet = self.ok("plan", "add-step", "third", "--verify", "@fast", "--plan", "p")
        self.assertNotIn("not defined yet", quiet.stderr)

    def test_plan_verify_with_nothing_to_run_says_the_step_has_no_evidence(self):
        self.define("show", self.show)
        self.create_check_plan("@show")
        result = self.run_cli("plan", "verify", "1", "--plan", "p")
        self.assertEqual(result.returncode, 0)
        self.assertIn("nothing to run", result.stdout)
        self.assertIn("step 1 of plan p still has no evidence", result.stderr)
        self.assertEqual(self.records(), [])


class SurfaceTests(CheckProject):
    def test_prompt_packets_and_status_name_the_checks(self):
        self.assertNotIn("Checks:", self.ok("prompt", "handoff").stdout)
        self.assertNotIn("Checks:", self.ok("status").stdout)
        self.define("fast", "test -f tracked.txt")
        self.define("full", "test -f show.py")
        handoff = self.ok("prompt", "handoff").stdout
        self.assertIn("Checks: fast = test -f tracked.txt, full = test -f show.py", handoff)
        self.assertIn("Checks: fast = test -f tracked.txt, full = test -f show.py",
                      self.ok("prompt", "next").stdout)
        packet = json.loads(self.ok("prompt", "handoff", "--json").stdout)
        self.assertEqual(packet["checks"], {"fast": "test -f tracked.txt", "full": "test -f show.py"})
        self.assertIn("Checks: fast, full", self.ok("status").stdout)
        self.assertEqual(json.loads(self.ok("status", "--json").stdout)["checks"], ["fast", "full"])

    def test_campaign_prompt_names_the_checks(self):
        self.define("fast", "test -f tracked.txt")
        self.create_check_plan()
        self.ok("campaign", "start", "goal", "--plan", "p")
        prompt = self.ok("campaign", "prompt", "p").stdout
        self.assertIn("Checks: fast = test -f tracked.txt", prompt)
        self.assertIn("Stored verifier: @fast", prompt)

    def test_add_checks_line_is_idempotent_and_keeps_errors(self):
        self.define("fast", "test -f tracked.txt")
        payload = {"next_prompt": "Handoff\n\nInstructions:\n- go\nGuardrail: g"}
        once = add_checks_line(dict(payload), self.state)
        twice = add_checks_line(dict(once), self.state)
        self.assertEqual(once["next_prompt"], twice["next_prompt"])
        self.assertLess(once["next_prompt"].index("Checks:"), once["next_prompt"].index("Instructions:"))
        error = {"error": "[FAIL] no", "next_prompt": ""}
        self.assertEqual(add_checks_line(dict(error), self.state), error)


if __name__ == "__main__":
    unittest.main()
