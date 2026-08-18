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
