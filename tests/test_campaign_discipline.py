"""Campaign evidence-discipline tests: the campaign verifier executes and
gates the verify phase, skipped final tasks release the active pointer, and
failed tasks get explicit recovery guidance."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class CampaignCliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
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

    def read_jsonl(self, relative_path):
        records = []
        path = self.project / ".mythify" / relative_path
        if not path.is_file():
            return records
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def advance(self, name, result_text="phase done"):
        return self.run_cli("campaign", "advance", name, "--result", result_text)

    def advance_to_verify(self, name):
        for _ in ("understand", "design", "build", "judge"):
            result = self.advance(name)
            self.assertEqual(result.returncode, 0, result.stderr)


class TestCampaignVerifierGate(CampaignCliCase):
    def start(self, verify_command=None, name="gate"):
        args = ["campaign", "start", "Ship the fix", "--name", name,
                "--tasks", json.dumps(["only task"])]
        if verify_command:
            args.extend(["--verify", verify_command])
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failing_verifier_blocks_verify_phase(self):
        self.start(verify_command="false")
        self.advance_to_verify("gate")
        blocked = self.advance("gate")
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("Campaign verifier failed", blocked.stderr)
        status = self.run_cli("campaign", "status", "gate")
        self.assertIn("verify", status.stdout)
        records = self.read_jsonl("verifications.jsonl")
        self.assertTrue(
            any(
                record.get("kind") == "executed"
                and record.get("verified") is False
                and "campaign gate task 1 verifier" in str(record.get("claim"))
                for record in records
            ),
            records,
        )

    def test_passing_verifier_advances_and_records_evidence(self):
        self.start(verify_command="true")
        self.advance_to_verify("gate")
        passed = self.advance("gate")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("Campaign verifier passed", passed.stdout)
        records = self.read_jsonl("verifications.jsonl")
        self.assertTrue(
            any(
                record.get("kind") == "executed" and record.get("verified") is True
                for record in records
            ),
            records,
        )

    def test_campaign_without_verifier_keeps_legacy_advance(self):
        self.start(verify_command=None)
        self.advance_to_verify("gate")
        advanced = self.advance("gate")
        self.assertEqual(advanced.returncode, 0, advanced.stderr)
        self.assertNotIn("Campaign verifier", advanced.stdout)
        self.assertEqual(self.read_jsonl("verifications.jsonl"), [])


class TestCampaignHygiene(CampaignCliCase):
    def test_skipping_final_task_clears_active_pointer(self):
        result = self.run_cli(
            "campaign", "start", "One and done", "--name", "solo",
            "--tasks", json.dumps(["only task"]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        skipped = self.run_cli("campaign", "task", "1", "skipped", "--campaign", "solo")
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        pointer = self.project / ".mythify" / "campaigns" / "active"
        self.assertFalse(pointer.exists(), "active pointer should be released")

    def test_failed_task_gets_recovery_guidance(self):
        result = self.run_cli(
            "campaign", "start", "Fragile work", "--name", "fragile",
            "--tasks", json.dumps(["breaks"]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        failed = self.run_cli(
            "campaign", "task", "1", "failed", "boom", "--campaign", "fragile"
        )
        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.assertIn("diagnose the failure", failed.stdout)
        prompt = self.run_cli("campaign", "prompt", "fragile")
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        self.assertIn("Phase: failed", prompt.stdout)
        self.assertNotIn("Phase: understand", prompt.stdout)

    def test_campaign_prompt_uses_canonical_command(self):
        result = self.run_cli(
            "campaign", "start", "Command shape", "--name", "shape",
            "--tasks", json.dumps(["task"]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        prompt = self.run_cli("campaign", "prompt", "shape")
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        self.assertIn("python3 scripts/mythify.py campaign advance shape", prompt.stdout)


if __name__ == "__main__":
    unittest.main()
