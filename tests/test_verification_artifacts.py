import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "mythify.py"


class VerificationArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / ".mythify"
        self.env = os.environ.copy()
        self.env["MYTHIFY_DIR"] = str(self.state)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def records(self):
        return [json.loads(line) for line in (self.state / "verifications.jsonl").read_text().splitlines()]

    def test_compact_success_retains_bounded_redacted_artifacts(self):
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        command = "{0} -c \"print('before {1} after')\"".format(sys.executable, secret)
        result = self.run_cli("verify", "run", command, "--claim", "redaction")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        record = self.records()[0]
        self.assertTrue(record["id"].startswith("v-"))
        stdout = self.state / record["artifacts"]["stdout"]["path"]
        self.assertTrue(stdout.is_file())
        content = stdout.read_text()
        self.assertNotIn(secret, content)
        self.assertIn("[REDACTED]", content)
        self.assertTrue(record["artifacts"]["stdout"]["redacted"])
        self.assertEqual(len(record["artifacts"]["stdout"]["sha256"]), 64)

    def test_failure_prints_tail_and_artifact_paths_without_rerun(self):
        command = "{0} -c \"import sys; print('diagnostic'); sys.exit(7)\"".format(sys.executable)
        result = self.run_cli("verify", "run", command)
        self.assertEqual(result.returncode, 2)
        self.assertIn("diagnostic", result.stdout)
        self.assertIn("Artifacts: verification-artifacts/", result.stdout)
        record = self.records()[0]
        self.assertEqual(record["exit_code"], 7)
        self.assertIn("diagnostic", (self.state / record["artifacts"]["stdout"]["path"]).read_text())

    def test_full_output_is_explicit(self):
        command = "{0} -c \"print('full diagnostic')\"".format(sys.executable)
        compact = self.run_cli("verify", "run", command)
        self.assertNotIn("full artifact", compact.stdout)
        full = self.run_cli("verify", "run", command, "--output", "full")
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertIn("--- stdout (full artifact) ---", full.stdout)
        self.assertIn("full diagnostic", full.stdout)

    def test_compact_success_extracts_test_count_without_deciding_verdict(self):
        command = "{0} -c \"print('Ran 17 tests in 0.01s')\"".format(sys.executable)
        result = self.run_cli("verify", "run", command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("17 tests", result.stdout)
        self.assertEqual(self.records()[0]["test_count"], 17)

        failing = "{0} -c \"import sys; print('Ran 19 tests in 0.01s'); sys.exit(4)\"".format(sys.executable)
        result = self.run_cli("verify", "run", failing)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.records()[1]["test_count"], 19)
        self.assertFalse(self.records()[1]["verified"])

    def test_log_compaction_removes_only_archived_verification_artifacts(self):
        for label in ("first", "second"):
            command = "{0} -c \"print('{1}')\"".format(sys.executable, label)
            result = self.run_cli("verify", "run", command)
            self.assertEqual(result.returncode, 0, result.stderr)
        before = self.records()
        first_dir = (self.state / before[0]["artifacts"]["stdout"]["path"]).parent
        second_dir = (self.state / before[1]["artifacts"]["stdout"]["path"]).parent
        compacted = self.run_cli("logs", "compact", "--keep", "1", "--json")
        self.assertEqual(compacted.returncode, 0, compacted.stderr)
        payload = json.loads(compacted.stdout)
        verification = next(row for row in payload["logs"] if row["log"] == "verifications.jsonl")
        self.assertEqual(verification["removed_artifacts"], 1)
        self.assertFalse(first_dir.exists())
        self.assertTrue(second_dir.exists())


if __name__ == "__main__":
    unittest.main()
