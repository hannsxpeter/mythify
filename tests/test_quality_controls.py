import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "mythify.py"


class QualityControlTests(unittest.TestCase):
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
            [sys.executable, str(CLI), *args], cwd=self.root, env=self.env,
            capture_output=True, text=True, check=False,
        )

    def init_git_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "mythify@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Mythify Test"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text(".mythify/\n", encoding="utf-8")
        (self.root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

    def test_review_create_is_removed(self):
        result = self.run_cli(
            "review", "create", "--status", "warn", "--path", "scripts/example.py",
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("invalid choice", result.stderr)

    def test_legacy_maintainability_record_shows_as_material(self):
        reviews = self.state / "reviews"
        reviews.mkdir(parents=True)
        (reviews / "old-review.json").write_text(json.dumps({
            "kind": "maintainability_review",
            "name": "old-review",
            "status": "warn",
            "changed_paths": ["scripts/example.py"],
            "findings": [],
        }) + "\n", encoding="utf-8")
        shown = self.run_cli("review", "show", "old-review")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("maintainability_review", shown.stdout)
        self.assertIn("cannot satisfy verification", shown.stdout)

    def test_review_cannot_satisfy_strict_plan_completion(self):
        self.init_git_repo()
        plan = self.run_cli(
            "plan", "create", "Ship", "--steps",
            json.dumps([{"title": "Build", "verify_command": "true"}]),
        )
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(self.run_cli("step", "1", "in_progress").returncode, 0)
        created = self.run_cli(
            "review", "blast-radius", "--status", "pass", "--path", "tracked.txt",
            "--safety-fact", "the build is safe", "--name", "build-safety",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        completed = self.run_cli("step", "1", "completed", "review passed")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Verified evidence required", completed.stderr)

    def test_blast_radius_review_links_executed_proof_without_mutating_parent(self):
        self.init_git_repo()
        risk = json.dumps({
            "failure_mode": "downstream parser rejects the payload",
            "path": "tracked.txt",
            "line": 1,
            "likelihood": "medium",
            "impact": "high",
            "disposition": "confirmed",
            "check": "true",
        })
        cleared = json.dumps({
            "failure_mode": "unrelated cache entry is removed",
            "path": "tracked.txt",
            "line": 1,
            "likelihood": "low",
            "impact": "low",
        })
        created = self.run_cli(
            "review", "blast-radius", "--status", "warn", "--path", "tracked.txt",
            "--safety-fact", "the changed payload remains parseable", "--proof-depth", "2",
            "--risk", risk, "--cleared", cleared, "--merge-command", "test -f tracked.txt",
            "--name", "payload-safety",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        review_path = self.state / "reviews" / "payload-safety.json"
        stored_before = json.loads(review_path.read_text(encoding="utf-8"))
        self.assertEqual(stored_before["safety_fact"]["status"], "unproven")
        self.assertEqual(stored_before["safety_fact"]["proof_depth"], 2)
        self.assertEqual(stored_before["risks"][1]["disposition"], "cleared")
        self.assertEqual(len(stored_before["change_fingerprint"]["worktree_digest"]), 64)

        proved = self.run_cli("review", "prove", "payload-safety")
        self.assertEqual(proved.returncode, 0, proved.stderr)
        shown = self.run_cli("review", "show", "payload-safety", "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        view = json.loads(shown.stdout)
        self.assertEqual(view["change_freshness"]["status"], "current")
        self.assertEqual(view["safety_fact"]["status"], "proven")
        self.assertEqual(view["safety_fact"]["proof_depth"], 4)
        self.assertTrue(view["safety_fact"]["verification_id"].startswith("v-"))
        self.assertTrue(view["merge_gate"]["verified"])

        stored_after = json.loads(review_path.read_text(encoding="utf-8"))
        self.assertEqual(stored_after, stored_before)
        lineage = self.run_cli(
            "lineage", "status", "verification", view["safety_fact"]["verification_id"], "--json"
        )
        self.assertEqual(lineage.returncode, 0, lineage.stderr)
        self.assertEqual(json.loads(lineage.stdout)["status"], "current")

    def test_blast_radius_review_refuses_stale_dirty_to_dirty_proof(self):
        self.init_git_repo()
        tracked = self.root / "tracked.txt"
        tracked.write_text("first dirty state\n", encoding="utf-8")
        created = self.run_cli(
            "review", "blast-radius", "--status", "warn", "--path", "tracked.txt",
            "--safety-fact", "the dirty change remains safe", "--merge-command", "test -f tracked.txt",
            "--name", "dirty-safety",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        tracked.write_text("second dirty state\n", encoding="utf-8")
        proved = self.run_cli("review", "prove", "dirty-safety")
        self.assertEqual(proved.returncode, 1)
        self.assertIn("worktree_digest_mismatch", proved.stderr)
        shown = self.run_cli("review", "show", "dirty-safety", "--json")
        view = json.loads(shown.stdout)
        self.assertEqual(view["change_freshness"]["status"], "stale")
        self.assertEqual(view["safety_fact"]["status"], "unproven")

    def test_failed_blast_radius_proof_stays_unproven_at_executed_depth(self):
        self.init_git_repo()
        created = self.run_cli(
            "review", "blast-radius", "--status", "fail", "--path", "tracked.txt",
            "--safety-fact", "the failing behavior is safe", "--merge-command", "false",
            "--name", "failed-safety",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        proved = self.run_cli("review", "prove", "failed-safety")
        self.assertEqual(proved.returncode, 2)
        shown = self.run_cli("review", "show", "failed-safety", "--json")
        view = json.loads(shown.stdout)
        self.assertEqual(view["safety_fact"]["proof_depth"], 4)
        self.assertEqual(view["safety_fact"]["status"], "unproven")
        self.assertFalse(view["merge_gate"]["verified"])

    def test_blast_radius_review_rejects_claimed_execution_and_invalid_risk(self):
        self.init_git_repo()
        depth = self.run_cli(
            "review", "blast-radius", "--status", "warn", "--path", "tracked.txt",
            "--safety-fact", "claimed execution", "--proof-depth", "4", "--name", "too-deep",
        )
        self.assertEqual(depth.returncode, 64)
        self.assertIn("invalid choice", depth.stderr)
        invalid = self.run_cli(
            "review", "blast-radius", "--status", "warn", "--path", "tracked.txt",
            "--safety-fact", "risk data is valid", "--risk",
            json.dumps({
                "failure_mode": " ", "path": "tracked.txt", "line": 1,
                "likelihood": "medium", "impact": "high",
            }),
            "--name", "invalid-risk",
        )
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("risk is missing: failure_mode", invalid.stderr)

    def test_blast_radius_proof_cannot_move_source_or_bypass_disabled_run(self):
        self.init_git_repo()
        created = self.run_cli(
            "review", "blast-radius", "--status", "warn", "--path", "tracked.txt",
            "--safety-fact", "the reviewed source is unchanged", "--merge-command", "test -f tracked.txt",
            "--name", "source-safety",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        mutating_command = (
            "python3 -c \"from pathlib import Path; "
            "Path('tracked.txt').write_text('mutated\\n', encoding='utf-8')\""
        )
        moved = self.run_cli(
            "review", "prove", "source-safety", "--command", mutating_command
        )
        self.assertEqual(moved.returncode, 2)
        self.assertIn("changed the reviewed source", moved.stderr)
        view = json.loads(self.run_cli("review", "show", "source-safety", "--json").stdout)
        self.assertEqual(view["change_freshness"]["status"], "stale")
        self.assertEqual(view["safety_fact"]["status"], "unproven")

        self.env["MYTHIFY_DISABLE_RUN"] = "1"
        disabled = self.run_cli("review", "prove", "source-safety")
        self.assertEqual(disabled.returncode, 2)
        self.assertIn("MYTHIFY_DISABLE_RUN=1", disabled.stderr)


if __name__ == "__main__":
    unittest.main()
