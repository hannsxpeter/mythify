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


class TestEvidenceGuards(SpineCase):
    def test_noop_verify_command_warns_at_plan_create(self):
        steps = json.dumps([{"title": "a", "verify_command": "true"}])
        result = self.run_cli("plan", "create", "g", "--name", "p", "--steps", steps)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("looks like a no-op", result.stderr)

    def test_noop_verify_command_warns_at_add_step(self):
        self.run_cli("plan", "create", "g", "--name", "p", "--steps", json.dumps([{"title": "a"}]))
        result = self.run_cli("plan", "add-step", "b", "--verify", "echo ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("looks like a no-op", result.stderr)

    def test_real_verify_command_does_not_warn(self):
        self.run_cli("plan", "create", "g", "--name", "p", "--steps", json.dumps([{"title": "a"}]))
        result = self.run_cli(
            "plan", "add-step", "b", "--verify", "python3 -m unittest discover -s tests"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("looks like a no-op", result.stderr)

    def test_legacy_completion_stamps_a_strict_gate_waiver(self):
        self.run_cli("plan", "create", "g", "--name", "p", "--steps", json.dumps([{"title": "a"}]))
        done = self.run_cli(
            "step", "1", "completed", "prose only",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Strict gate waived", done.stderr)
        self.assertTrue(self.load_plan("p")["steps"][0]["strict_gate_waived"])

    def test_strict_completion_leaves_no_waiver_stamp(self):
        self.run_cli("plan", "create", "g", "--name", "p",
                     "--steps", json.dumps([{"title": "a", "verify_command": "true"}]))
        self.run_cli("plan", "verify", "1")
        done = self.run_cli("step", "1", "completed", "verify run exit 0")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("strict_gate_waived", self.load_plan("p")["steps"][0])

    def harness_attention(self, env_extra=None):
        result = self.run_cli("harness", "--json", env_extra=env_extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout).get("attention", [])

    def test_harness_flags_a_trivial_pass(self):
        self.run_cli("verify", "run", "true", "--claim", "cheap win")
        summaries = [item["summary"] for item in self.harness_attention()]
        self.assertTrue(
            any(summary.startswith("trivial pass:") for summary in summaries), summaries
        )

    def test_harness_names_active_legacy_opt_outs(self):
        attention = self.harness_attention(
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"}
        )
        summaries = [item["summary"] for item in attention]
        self.assertIn(
            "legacy opt-out active: MYTHIFY_REQUIRE_VERIFIED_STEP", summaries
        )

    def test_verifications_are_chained_and_a_tampered_line_is_flagged(self):
        self.run_cli("verify", "run", "true", "--claim", "first")
        self.run_cli("verify", "run", "true", "--claim", "second")
        path = self.project / ".mythify" / "verifications.jsonl"
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertIsNone(records[0]["prev_sha256"])
        self.assertTrue(records[1]["prev_sha256"])
        self.assertFalse(
            [a for a in self.harness_attention() if a["source"] == "ledger"]
        )
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("first", "forged")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        summaries = [a["summary"] for a in self.harness_attention()]
        self.assertTrue(
            any(s.startswith("verification ledger chain break") for s in summaries),
            summaries,
        )

    def test_compaction_preserves_the_ledger_chain(self):
        for claim in ("one", "two", "three"):
            self.run_cli("verify", "run", "true", "--claim", claim)
        result = self.run_cli("logs", "compact", "--keep", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.project / ".mythify" / "verifications.jsonl"
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(records), 2)
        self.assertFalse(
            [a for a in self.harness_attention() if a["source"] == "ledger"]
        )

    def test_harness_flags_a_waived_step_completion(self):
        self.run_cli("plan", "create", "g", "--name", "p", "--steps", json.dumps([{"title": "a"}]))
        self.run_cli(
            "step", "1", "completed", "prose only",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"},
        )
        summaries = [item["summary"] for item in self.harness_attention()]
        self.assertIn("step 1 completed under a waived strict gate", summaries)


if __name__ == "__main__":
    unittest.main()
