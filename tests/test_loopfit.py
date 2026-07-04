"""Tests for the loop-fit advisory: a read-only recommendation of whether a
task should be a bounded self-driving loop, a host-supervised loop, or done
directly, assessed against the loop-worthiness gates."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class LoopFitCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        self.home = self.project / "home"
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def git_init(self):
        for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git", *cmd], cwd=str(self.project), capture_output=True, text=True)

    def assess(self, task):
        result = self.run_cli("loop-fit", task, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class TestLoopFit(LoopFitCase):
    def test_loop_when_recurring_verifiable_and_repro(self):
        self.git_init()
        (self.project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        payload = self.assess("Run the unit tests on every commit until they pass")
        self.assertEqual(payload["recommendation"], "loop")
        self.assertTrue(payload["criteria"]["automated_verification"])
        self.assertTrue(payload["criteria"]["recurring"])
        self.assertTrue(payload["criteria"]["reproduction_env"])
        self.assertIn("outcome run", payload["suggested_next"])

    def test_supervised_when_verifiable_but_one_off(self):
        self.git_init()
        (self.project / "package.json").write_text("{}", encoding="utf-8")
        payload = self.assess("Fix the failing parser test")
        self.assertEqual(payload["recommendation"], "supervised")
        self.assertIn("plan create", payload["suggested_next"])

    def test_direct_when_no_machine_check(self):
        self.git_init()
        payload = self.assess("Write a short poem about autumn")
        self.assertEqual(payload["recommendation"], "direct")
        self.assertFalse(payload["criteria"]["automated_verification"])

    def test_direct_when_needs_human_judgment(self):
        self.git_init()
        (self.project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        payload = self.assess("Decide which landing-page design looks best")
        self.assertEqual(payload["recommendation"], "direct")
        self.assertTrue(payload["criteria"]["needs_human_judgment"])

    def test_verify_terms_gate_recurring_judgment_still_loops(self):
        # Recurring + verifiable overrides a judgment word: keep looping.
        self.git_init()
        (self.project / "tests").mkdir()
        payload = self.assess("Regenerate and re-run the test suite until the build passes")
        self.assertEqual(payload["recommendation"], "loop")

    def test_read_only_and_no_workspace(self):
        # loop-fit works with no .mythify and creates no state.
        payload = self.assess("run the tests until green")
        self.assertIn(payload["recommendation"], ("loop", "supervised", "direct"))
        self.assertFalse((self.project / ".mythify").exists())
        self.assertIn("read-only", payload["guardrail"])

    def test_text_output_shows_criteria_checklist(self):
        self.git_init()
        result = self.run_cli("loop-fit", "run the tests on every push")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Loop-fit:", result.stdout)
        self.assertIn("automated verification available", result.stdout)
        self.assertIn("Suggested next:", result.stdout)


if __name__ == "__main__":
    unittest.main()
