"""P-MUST-02 verification provenance and readiness freshness tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "mythify.py"
sys.path.insert(0, str(SCRIPTS_DIR))

from mythify_provenance import verification_freshness  # noqa: E402
from mythify_views_status import (  # noqa: E402
    release_readiness_status,
    summarize_release_gate,
)


class VerificationProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="mythify-provenance-proj-"))
        self.home = Path(tempfile.mkdtemp(prefix="mythify-provenance-home-"))
        self.addCleanup(shutil.rmtree, str(self.project), True)
        self.addCleanup(shutil.rmtree, str(self.home), True)

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def init_git_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "config", "user.email", "mythify@example.invalid"],
            cwd=self.project,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Mythify Test"],
            cwd=self.project,
            check=True,
        )
        (self.project / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.project, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def latest_record(self):
        path = self.project / ".mythify" / "verifications.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return rows[-1]

    def test_p_must_02_cli_records_commit_and_version_provenance(self):
        commit = self.init_git_repo()
        self.assertEqual(self.run_cli("init").returncode, 0)
        subprocess.run(["git", "add", ".gitignore"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "ignore state"], cwd=self.project, check=True)
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.project, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        result = self.run_cli("verify", "run", "true", "--claim", "provenance check")
        self.assertEqual(result.returncode, 0, result.stderr)

        record = self.latest_record()
        version = self.run_cli("--version").stdout.strip().removeprefix("Mythify v")
        self.assertEqual(
            record["provenance"],
            {"git_commit": commit, "worktree_clean": True, "mythify_version": version},
        )

    def test_p_must_02_non_git_execution_keeps_version_provenance(self):
        self.assertEqual(self.run_cli("init").returncode, 0)
        result = self.run_cli("verify", "run", "true")
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(
            self.latest_record()["provenance"],
            {
                "git_commit": None,
                "worktree_clean": None,
                "mythify_version": self.run_cli("--version").stdout.strip().removeprefix("Mythify v"),
            },
        )

    def test_p_must_02_freshness_rejects_stale_and_preserves_legacy(self):
        current = {"git_commit": "current", "worktree_clean": True, "mythify_version": "4.3.0"}
        self.assertEqual(
            verification_freshness({}, current),
            {"status": "legacy", "reason": "missing_provenance"},
        )
        self.assertEqual(
            verification_freshness(
                {"provenance": {"git_commit": "old", "worktree_clean": True, "mythify_version": "4.3.0"}},
                current,
            ),
            {"status": "stale", "reason": "git_commit_mismatch"},
        )
        self.assertEqual(
            verification_freshness(
                {"provenance": {"git_commit": "current", "worktree_clean": True, "mythify_version": "4.2.0"}},
                current,
            ),
            {"status": "stale", "reason": "mythify_version_mismatch"},
        )
        self.assertEqual(
            verification_freshness(
                {"provenance": {"git_commit": "current", "worktree_clean": True, "mythify_version": "4.3.0"}},
                current,
            ),
            {"status": "fresh", "reason": "provenance_matches"},
        )
        self.assertEqual(
            verification_freshness(
                {"provenance": {"git_commit": "recorded", "worktree_clean": True, "mythify_version": "4.3.0"}},
                {"git_commit": None, "worktree_clean": None, "mythify_version": "4.3.0"},
            ),
            {"status": "stale", "reason": "current_git_commit_unavailable"},
        )
        self.assertEqual(
            verification_freshness(
                {"provenance": {"git_commit": None, "worktree_clean": None, "mythify_version": "4.3.0"}},
                {"git_commit": None, "worktree_clean": None, "mythify_version": "4.3.0"},
            ),
            {"status": "stale", "reason": "current_git_commit_unavailable"},
        )
        self.assertEqual(
            verification_freshness(
                {"provenance": {"git_commit": "current", "worktree_clean": False, "mythify_version": "4.3.0"}},
                current,
            ),
            {"status": "stale", "reason": "recorded_worktree_dirty"},
        )
        self.assertEqual(
            verification_freshness({"provenance": []}, current),
            {"status": "legacy", "reason": "missing_provenance"},
        )

    def test_p_must_02_readiness_uses_only_fresh_passing_evidence(self):
        gate = {
            "id": "tests",
            "label": "Tests",
            "required": True,
            "sources": ["tests/"],
            "commands": ["python3 -m unittest discover -s tests -v"],
        }
        current = {"git_commit": "current", "worktree_clean": True, "mythify_version": "4.3.0"}
        base = {
            "kind": "executed",
            "claim": "suite passes",
            "command": "python3 -m unittest discover -s tests -v",
            "exit_code": 0,
            "verified": True,
            "timestamp": "2026-07-13T00:00:00Z",
        }

        legacy = summarize_release_gate(gate, [base], current)
        self.assertEqual(legacy["status"], "stale")
        self.assertEqual(legacy["freshness"], {"status": "legacy", "reason": "missing_provenance"})
        self.assertIsNone(legacy["latest_record"]["provenance"])

        stale_record = {
            **base,
            "provenance": {"git_commit": "old", "worktree_clean": True, "mythify_version": "4.3.0"},
        }
        stale = summarize_release_gate(gate, [stale_record], current)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["freshness"]["reason"], "git_commit_mismatch")
        self.assertEqual(
            release_readiness_status([stale], {"status": "clean"}),
            "needs_evidence",
        )

        fresh_record = {
            **base,
            "provenance": {"git_commit": "current", "worktree_clean": True, "mythify_version": "4.3.0"},
        }
        fresh = summarize_release_gate(gate, [fresh_record], current)
        self.assertEqual(fresh["status"], "passed")
        self.assertEqual(fresh["freshness"]["status"], "fresh")
        self.assertEqual(
            release_readiness_status([fresh], {"status": "clean"}),
            "ready_for_release_review",
        )

        spoof = summarize_release_gate(
            gate,
            [{**base, "command": "true", "claim": "python3 -m unittest discover -s tests -v"}],
            current,
        )
        self.assertEqual(spoof["status"], "missing")
        inconsistent = summarize_release_gate(
            gate,
            [{**fresh_record, "exit_code": 9}],
            current,
        )
        self.assertEqual(inconsistent["status"], "failed")


if __name__ == "__main__":
    unittest.main()
