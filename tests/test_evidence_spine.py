"""Tests for the unified evidence spine: per-step verify commands and the
`plan verify` command that runs a step's own gate and records scoped evidence."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class SpineCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self.assertEqual(self.run_cli("init").returncode, 0)

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        env["HOME"] = str(self.home)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def load_plan(self, slug):
        path = self.project / ".mythify" / "plans" / (slug + ".json")
        with open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)


class TestEvidenceSpine(SpineCase):
    def test_create_carries_verify_command(self):
        steps = json.dumps([
            {"title": "a", "success_criteria": "ok", "verify_command": "true"},
            {"title": "b"},
        ])
        self.assertEqual(self.run_cli("plan", "create", "g", "--name", "p", "--steps", steps).returncode, 0)
        plan = self.load_plan("p")
        self.assertEqual(plan["steps"][0]["verify_command"], "true")
        self.assertNotIn("verify_command", plan["steps"][1])

    def test_add_step_verify_flag(self):
        self.run_cli("plan", "create", "g", "--name", "p", "--steps", json.dumps([{"title": "a"}]))
        result = self.run_cli("plan", "add-step", "b", "--verify", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verify: true", result.stdout)
        self.assertEqual(self.load_plan("p")["steps"][1]["verify_command"], "true")

    def test_plan_verify_passes_and_satisfies_gate(self):
        self.run_cli("plan", "create", "g", "--name", "p",
                     "--steps", json.dumps([{"title": "a", "verify_command": "true"}]))
        verify = self.run_cli("plan", "verify", "1")
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn("VERIFIED step 1", verify.stdout)
        # gate is now satisfied
        done = self.run_cli("step", "1", "completed", "verify run exit 0")
        self.assertEqual(done.returncode, 0, done.stderr)
        # the recorded verification is scoped to plan p, step 1
        records = []
        with open(str(self.project / ".mythify" / "verifications.jsonl"), encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        scoped = [r for r in records if r.get("plan") == "p" and r.get("step_id") == 1]
        self.assertTrue(scoped and scoped[-1]["verified"] is True)

    def test_plan_verify_failing_command_blocks_completion(self):
        self.run_cli("plan", "create", "g", "--name", "p",
                     "--steps", json.dumps([{"title": "a", "verify_command": "false"}]))
        verify = self.run_cli("plan", "verify", "1")
        self.assertEqual(verify.returncode, 2)
        self.assertIn("UNVERIFIED step 1", verify.stdout)
        done = self.run_cli("step", "1", "completed", "no passing verify")
        self.assertEqual(done.returncode, 1)
        self.assertIn("Verified evidence required", done.stderr)

    def test_plan_verify_without_command_errors(self):
        self.run_cli("plan", "create", "g", "--name", "p", "--steps", json.dumps([{"title": "a"}]))
        result = self.run_cli("plan", "verify", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no verify_command", result.stderr)

    def test_plan_verify_disabled_run(self):
        self.run_cli("plan", "create", "g", "--name", "p",
                     "--steps", json.dumps([{"title": "a", "verify_command": "true"}]))
        result = self.run_cli("plan", "verify", "1", env_extra={"MYTHIFY_DISABLE_RUN": "1"})
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
