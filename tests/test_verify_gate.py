"""`verify gate`: the read-only hook check for work in flight without evidence."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subprocess_env  # noqa: E402,F401  (faster subprocesses, same behavior)
import warm_cli  # noqa: E402  (runs the CLI forked from a warm interpreter)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "mythify.py"
sys.path.insert(0, str(SCRIPTS_DIR))

CHECK_SCRIPT = """\
import os
import sys

if os.path.exists(sys.argv[1]):
    sys.exit(1)
with open("count.txt") as handle:
    sys.stderr.write("Ran {0} tests\\nOK\\n".format(handle.read().strip()))
"""


class GateProject(unittest.TestCase):
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
        self.fail_flag = base / "fail.flag"
        if self.use_git:
            self.git("init", "-q")
            self.git("config", "user.email", "mythify@example.invalid")
            self.git("config", "user.name", "Mythify Test")
        (self.project / "check.py").write_text(CHECK_SCRIPT, encoding="utf-8")
        (self.project / "count.txt").write_text("5\n", encoding="utf-8")
        (self.project / "README.md").write_text("docs\n", encoding="utf-8")
        self.ok("init")
        if self.use_git:
            self.git("add", "-A")
            self.git("commit", "-qm", "initial")
        self.command = "{0} check.py {1}".format(shlex.quote(sys.executable), shlex.quote(str(self.fail_flag)))

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=str(self.project), check=True, capture_output=True, text=True
        ).stdout.strip()

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        for name in ("MYTHIFY_DIR", "MYTHIFY_REQUIRE_VERIFIED_STEP", "MYTHIFY_DISABLE_RUN",
                     "MYTHIFY_REUSE_ENV", "MYTHIFY_RISK_GATE"):
            env.pop(name, None)
        env["HOME"] = str(self.home)
        env.update(env_extra or {})
        return warm_cli.run(list(args), cwd=str(self.project), env=env, timeout=120)

    def ok(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def gate(self, *args):
        return self.run_cli("verify", "gate", *args)

    def plan(self, name="p"):
        return json.loads((self.state / "plans" / (name + ".json")).read_text(encoding="utf-8"))

    def start(self, verify=None, strict=False, steps=1):
        items = [{"title": "step {0}".format(n + 1), "verify_command": verify or self.command}
                 for n in range(steps)]
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps(items))
        if strict:
            self.ok("campaign", "start", "goal", "--plan", "p")
        self.ok("step", "1", "in_progress", "--plan", "p")

    def write(self, name, text="x = 1\n"):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def snapshot(self):
        """Every file under the state directory with its size, mtime, and content hash."""
        rows = {}
        for path in sorted(self.state.rglob("*")):
            if path.is_file():
                info = path.stat()
                rows[str(path.relative_to(self.state))] = (
                    info.st_size, info.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        return rows


class PassingTests(GateProject):
    def test_exit_0_with_no_plan(self):
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Gate passed", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(self.gate("--json").stdout), {"ok": True, "plan": None, "steps": []})

    def test_exit_0_with_no_in_progress_step(self):
        self.ok("plan", "create", "goal", "--name", "p", "--steps",
                json.dumps([{"title": "idle", "verify_command": self.command}]))
        result = self.gate()
        self.assertEqual(result.returncode, 0)
        self.assertIn("no in-progress step", result.stdout)
        self.assertEqual(json.loads(self.gate("--json").stdout)["steps"], [])

    def test_exit_0_with_passing_current_evidence(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plan p has 1 in-progress step(s), all with current passing evidence", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_a_reused_run_counts(self):
        self.start()
        self.ok("verify", "run", self.command)
        self.ok("plan", "verify", "1", "--plan", "p", "--reuse")
        self.assertIn("reused_from", self.state.joinpath("verifications.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(self.gate().returncode, 0)

    def test_completed_steps_are_not_judged(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        self.ok("step", "1", "completed", "verified", "--plan", "p")
        self.write("README.md", "changed after\n")
        self.assertEqual(self.gate().returncode, 0)

    def test_a_named_plan_is_judged_instead_of_the_active_one(self):
        self.start()
        self.ok("plan", "create", "goal", "--name", "q", "--steps", json.dumps([{"title": "idle"}]))
        self.assertEqual(self.gate().returncode, 0)
        refused = self.gate("--plan", "p")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("plan p step 1", refused.stderr)

    def test_no_active_pointer_leaves_nothing_in_flight(self):
        self.assertFalse((self.state / "plans" / "active").exists())
        self.assertEqual(self.gate().returncode, 0)


class FailingTests(GateProject):
    def test_exit_2_with_no_evidence_names_the_step_and_the_next_command(self):
        self.start()
        result = self.gate()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        lines = result.stderr.splitlines()
        self.assertEqual(lines[0],
                         "[FAIL] Unverified work in flight: plan p step 1 (step 1): no passing "
                         "executed run was recorded since the step started.")
        self.assertEqual(lines[1], "Next: mythify plan verify 1 --plan p")
        self.assertEqual(len(lines), 2)

    def test_a_step_without_a_stored_verifier_gets_the_verify_run_line(self):
        self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps([{"title": "manual"}]))
        self.ok("step", "1", "in_progress", "--plan", "p")
        result = self.gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Next: mythify verify run "COMMAND" --claim "step 1"', result.stderr)

    def test_exit_2_when_the_latest_run_failed(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        self.fail_flag.write_text("fail", encoding="utf-8")
        self.assertEqual(self.run_cli("verify", "run", self.command).returncode, 2)
        result = self.gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("plan p step 1", result.stderr)
        self.assertIn("Next: mythify plan verify 1 --plan p", result.stderr)
        view = json.loads(self.gate("--json").stdout)
        self.assertTrue(any("exited 1" in reason for reason in view["steps"][0]["reasons"]))

    def test_exit_2_when_the_evidence_is_stale(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        self.assertEqual(self.gate().returncode, 0)
        self.write("README.md", "edited after the run\n")
        result = self.gate()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("predates the current source state (worktree_changed_since_run)", result.stderr)
        view = json.loads(self.gate("--json").stdout)
        self.assertTrue(any("The world moved since the passing run" in reason
                            for reason in view["steps"][0]["reasons"]))
        # Running the verifier again makes it current.
        self.ok("plan", "verify", "1", "--plan", "p")
        self.assertEqual(self.gate().returncode, 0)

    def test_stale_by_a_new_commit(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        self.write("README.md", "second\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "later")
        result = self.gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("git_commit_changed_since_run", result.stderr)

    def test_every_failing_step_gets_its_own_lines(self):
        self.start(steps=2)
        self.ok("step", "2", "in_progress", "--plan", "p")
        result = self.gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("plan p step 1", result.stderr)
        self.assertIn("plan p step 2", result.stderr)
        self.assertEqual(result.stderr.count("Next: mythify plan verify"), 2)
        self.ok("plan", "verify", "1", "--plan", "p")
        again = self.gate()
        self.assertEqual(again.returncode, 2)
        self.assertNotIn("step 1 (", again.stderr)
        self.assertIn("step 2 (", again.stderr)


class PlanRefusalTests(GateProject):
    """A plan that is named or pointed at but missing or unreadable is a refusal, not a pass."""

    def test_an_unknown_named_plan_is_refused(self):
        result = self.gate("--plan", "nope")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("[FAIL] verify gate cannot judge: Plan not found: nope", result.stderr)

    def test_an_active_pointer_to_a_missing_plan_is_refused(self):
        self.start()
        (self.state / "plans" / "p.json").unlink()
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("The active plan does not exist: p", result.stderr)

    def test_a_malformed_plan_file_is_refused_and_left_alone(self):
        self.start()
        plan_file = self.state / "plans" / "p.json"
        plan_file.write_text("{not json", encoding="utf-8")
        before = self.snapshot()
        for args in ((), ("--plan", "p"), ("--json",)):
            result = self.gate(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn("Plan p cannot be loaded", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_an_empty_pointer_leaves_nothing_in_flight(self):
        (self.state / "plans").mkdir(exist_ok=True)
        (self.state / "plans" / "active").write_text("\n", encoding="utf-8")
        self.assertEqual(self.gate().returncode, 0)


class StrictContextTests(GateProject):
    def test_a_test_count_drop_passes_by_default_and_fails_under_strict_context(self):
        for strict in (False, True):
            with self.subTest(strict=strict):
                self.setUp()
                self.ok("plan", "create", "goal", "--name", "p", "--steps", json.dumps(
                    [{"title": "only", "verify_command": self.command}]))
                if strict:
                    self.ok("campaign", "start", "goal", "--plan", "p")
                self.assertEqual(self.run_cli("verify", "run", self.command).returncode, 0)
                (self.project / "count.txt").write_text("3\n", encoding="utf-8")
                self.ok("plan", "verify", "1", "--plan", "p")
                result = self.gate()
                if not strict:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    continue
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("executed fewer tests than the step's baseline", result.stderr)
                self.assertIn("--accept-test-drop REASON", result.stderr)
                view = json.loads(self.gate("--json").stdout)
                self.assertTrue(any("Test count dropped under strict context" in reason
                                    for reason in view["steps"][0]["reasons"]))

    def test_a_high_risk_step_lacks_review_and_human_input_under_strict_context(self):
        self.start(strict=True)
        self.write("auth.py")
        self.ok("plan", "verify", "1", "--plan", "p")
        result = self.gate()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("high-risk paths changed without a covering review", result.stderr)
        self.assertIn("--human-input TEXT", result.stderr)
        view = json.loads(self.gate("--json").stdout)
        self.assertTrue(any("auth.py" in reason for reason in view["steps"][0]["reasons"]))

    def test_a_high_risk_step_only_warns_outside_strict_context(self):
        self.start()
        self.write("auth.py")
        self.ok("plan", "verify", "1", "--plan", "p")
        self.assertEqual(self.gate().returncode, 0)


class JsonShapeTests(GateProject):
    def test_failing_shape(self):
        self.start()
        result = self.gate("--json")
        self.assertEqual(result.returncode, 2)
        view = json.loads(result.stdout)
        self.assertEqual(sorted(view), ["ok", "plan", "steps"])
        self.assertFalse(view["ok"])
        self.assertEqual(view["plan"], "p")
        (row,) = view["steps"]
        self.assertEqual(sorted(row), ["id", "next", "ok", "reasons", "title"])
        self.assertEqual((row["id"], row["title"], row["ok"]), (1, "step 1", False))
        self.assertEqual(row["next"], "mythify plan verify 1 --plan p")
        self.assertTrue(row["reasons"])
        # The next-command lines still go to stderr.
        self.assertIn("Next: mythify plan verify 1 --plan p", result.stderr)

    def test_passing_shape(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        view = json.loads(self.gate("--json").stdout)
        self.assertEqual(view, {"ok": True, "plan": "p", "steps": [
            {"id": 1, "title": "step 1", "ok": True, "reasons": [], "next": ""}]})


class NamedCheckStepTests(GateProject):
    def test_an_at_name_step_passes_after_its_check_ran(self):
        self.ok("verify", "check", "fast", "--set", "test -f README.md")
        self.start(verify="@fast")
        result = self.gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Next: mythify plan verify 1 --plan p", result.stderr)
        self.ok("verify", "check", "fast")
        self.assertEqual(self.gate().returncode, 0)

    def test_a_run_for_a_different_check_does_not_pass(self):
        self.ok("verify", "check", "fast", "--set", "test -f README.md")
        self.ok("verify", "check", "full", "--set", "test -f check.py")
        self.start(verify="@fast")
        self.ok("verify", "check", "full")
        self.assertEqual(self.gate().returncode, 2)
        self.ok("plan", "verify", "1", "--plan", "p")
        self.assertEqual(self.gate().returncode, 0)

    def test_gate_and_step_completed_agree(self):
        self.ok("verify", "check", "fast", "--set", "test -f README.md")
        self.start(verify="@fast")
        self.assertEqual(self.gate().returncode, 2)
        self.assertEqual(self.run_cli("step", "1", "completed", "x", "--plan", "p").returncode, 1)
        self.ok("verify", "check", "fast")
        self.assertEqual(self.gate().returncode, 0)
        self.ok("step", "1", "completed", "x", "--plan", "p")


class ReadOnlyTests(GateProject):
    def test_the_gate_never_writes_to_the_state_directory(self):
        self.start()
        scenarios = [
            lambda: None,
            lambda: self.ok("plan", "verify", "1", "--plan", "p"),
            lambda: self.write("auth.py"),
            lambda: self.fail_flag.write_text("fail", encoding="utf-8"),
        ]
        for prepare in scenarios:
            prepare()
            before = self.snapshot()
            for args in ((), ("--json",), ("--plan", "p"), ("--plan", "missing")):
                self.gate(*args)
            self.assertEqual(self.snapshot(), before)

    def test_a_malformed_review_is_left_in_place_and_named_as_a_reason(self):
        self.start(strict=True)
        self.write("auth.py")
        self.ok("plan", "verify", "1", "--plan", "p")
        (self.state / "reviews").mkdir(exist_ok=True)
        bad = self.state / "reviews" / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        before = self.snapshot()
        result = self.gate("--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        reasons = json.loads(result.stdout)["steps"][0]["reasons"]
        self.assertTrue(any("bad" in reason and "could not be read" in reason for reason in reasons), reasons)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(path.name for path in bad.parent.iterdir()), ["bad.json"])

    def test_a_state_directory_with_missing_subdirectories_is_not_filled_in(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        env = self.project.parent / "env-state"
        shutil.copytree(self.state, env)
        for missing in ("reviews", "locks", "lessons", "outcomes", "maps", "reports"):
            shutil.rmtree(env / missing, ignore_errors=True)

        def listing(root):
            return sorted(str(path.relative_to(root)) for path in root.rglob("*"))

        before = listing(env)
        result = self.run_cli("verify", "gate", env_extra={"MYTHIFY_DIR": str(env)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(listing(env), before)
        empty = self.project.parent / "empty-state"
        self.assertEqual(
            self.run_cli("verify", "gate", env_extra={"MYTHIFY_DIR": str(empty)}).returncode, 0)
        self.assertFalse(empty.exists())

    def test_the_gate_runs_no_command(self):
        marker = self.project.parent / "ran.txt"
        command = "{0} -c \"open({1!r}, 'a').write('x')\"".format(
            shlex.quote(sys.executable), str(marker))
        self.start(verify=command)
        self.assertEqual(self.gate().returncode, 2)
        self.assertFalse(marker.exists())

    def test_the_gate_works_when_running_is_disabled(self):
        self.start()
        self.ok("plan", "verify", "1", "--plan", "p")
        result = self.run_cli("verify", "gate", env_extra={"MYTHIFY_DISABLE_RUN": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)


class NoGitTests(GateProject):
    use_git = False

    def test_the_gate_judges_evidence_outside_git(self):
        self.start()
        self.assertEqual(self.gate().returncode, 2)
        self.ok("plan", "verify", "1", "--plan", "p")
        self.assertEqual(self.gate().returncode, 0)


if __name__ == "__main__":
    unittest.main()
