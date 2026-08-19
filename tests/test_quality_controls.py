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

    def review_args(self):
        return (
            "review", "create", "--status", "warn", "--path", "scripts/example.py",
            "--interface-depth", "shallow wrapper", "--locality", "two modules",
            "--seam-count", "three", "--deletion-cost", "touches callers",
            "--invalid-state-exclusion", "partial", "--test-validity", "behavior covered",
            "--finding", "scripts/example.py:12: wrapper duplicates the underlying interface",
            "--name", "seam-review",
        )

    def init_git_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "mythify@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Mythify Test"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text(".mythify/\n", encoding="utf-8")
        (self.root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

    def test_maintainability_review_is_structured_and_material_only(self):
        created = self.run_cli(*self.review_args())
        self.assertEqual(created.returncode, 0, created.stderr)
        shown = self.run_cli("review", "show", "seam-review", "--json")
        record = json.loads(shown.stdout)
        self.assertEqual(record["status"], "warn")
        self.assertEqual(record["findings"][0]["line"], 12)
        self.assertEqual(record["evidence_status"], "material_not_verification")
        self.assertNotIn("verified", record)

    def test_review_cannot_satisfy_strict_plan_completion(self):
        plan = self.run_cli(
            "plan", "create", "Ship", "--steps",
            json.dumps([{"title": "Build", "verify_command": "true"}]),
        )
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(self.run_cli("step", "1", "in_progress").returncode, 0)
        self.assertEqual(self.run_cli(*self.review_args()).returncode, 0)
        completed = self.run_cli("step", "1", "completed", "review passed")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Verified evidence required", completed.stderr)

    def test_repeated_finding_creates_an_executable_eval_candidate(self):
        first = self.run_cli(*self.review_args())
        self.assertEqual(first.returncode, 0, first.stderr)
        second_args = list(self.review_args())
        second_args[-1] = "seam-review-two"
        second = self.run_cli(*second_args)
        self.assertEqual(second.returncode, 0, second.stderr)
        shown = self.run_cli("review", "show", "seam-review-two", "--json")
        record = json.loads(shown.stdout)
        self.assertTrue(record["eval_proposal_recommended"])
        self.assertEqual(record["eval_scenario_candidates"][0]["source_reviews"], ["seam-review"])
        self.assertNotIn("verified", record)

    def test_passing_review_does_not_propose_a_regression_from_old_findings(self):
        self.assertEqual(self.run_cli(*self.review_args()).returncode, 0)
        second_args = list(self.review_args())
        second_args[3] = "pass"
        second_args[-1] = "resolved-review"
        self.assertEqual(self.run_cli(*second_args).returncode, 0)
        shown = self.run_cli("review", "show", "resolved-review", "--json")
        record = json.loads(shown.stdout)
        self.assertFalse(record["eval_proposal_recommended"])
        self.assertEqual(record["eval_scenario_candidates"], [])

    def test_review_rejects_empty_dimension_assessments(self):
        args = list(self.review_args())
        args[args.index("--locality") + 1] = " "
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 1)
        self.assertIn("non-empty assessment", result.stderr)

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
            "--risk", risk, "--cleared", cleared, "--merge-command", "true",
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
            "--safety-fact", "the dirty change remains safe", "--merge-command", "true",
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
        self.assertEqual(depth.returncode, 2)
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
            "--safety-fact", "the reviewed source is unchanged", "--merge-command", "true",
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

    def test_design_comparison_requires_two_distinct_interfaces_and_selection(self):
        self.assertEqual(
            self.run_cli("design", "create", "Seam", "--problem", "Choose", "--name", "seam").returncode,
            0,
        )
        base = (
            "--call-sites", "caller.py", "--locality", "one module",
            "--migration-cost", "low", "--deletion-cost", "one file",
            "--reversal-evidence", "second consumer", "--name", "seam",
        )
        first = self.run_cli("design", "alternative", "One", "--interface", "command", *base)
        self.assertEqual(first.returncode, 0, first.stderr)
        early = self.run_cli("design", "approve", "seam", "--note", "too early")
        self.assertEqual(early.returncode, 1)
        duplicate = self.run_cli("design", "alternative", "Duplicate", "--interface", " command ", *base)
        self.assertEqual(duplicate.returncode, 1)
        second = self.run_cli("design", "alternative", "Two", "--interface", "adapter object", "--select", *base)
        self.assertEqual(second.returncode, 0, second.stderr)
        approved = self.run_cli("design", "approve", "seam", "--note", "compared")
        self.assertEqual(approved.returncode, 0, approved.stderr)


if __name__ == "__main__":
    unittest.main()
