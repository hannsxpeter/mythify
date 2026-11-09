"""`report --metrics`: read-only flow metrics, and the CI concurrency block."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subprocess_env  # noqa: E402,F401  (faster subprocesses, same behavior)
import warm_cli  # noqa: E402  (runs the CLI forked from a warm interpreter)

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class MetricsProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.project = base / "project"
        self.project.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        self.state = self.project / ".mythify"
        self.ok("init")

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        return warm_cli.run(list(args), cwd=str(self.project), env=env, timeout=120)

    def ok(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def make_plan(self, name, started_at=None, steps=1):
        items = [{"title": "step {0}".format(n + 1)} for n in range(steps)]
        self.ok("plan", "create", "goal", "--name", name, "--steps", json.dumps(items))
        if started_at:
            path = self.state / "plans" / (name + ".json")
            plan = json.loads(path.read_text(encoding="utf-8"))
            plan["steps"][0]["started_at"] = started_at
            path.write_text(json.dumps(plan), encoding="utf-8")

    def ledger(self, rows):
        with open(self.state / "verifications.jsonl", "a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def metrics(self, *extra):
        return json.loads(self.ok("report", "--metrics", "--json", *extra).stdout)


class MetricsTests(MetricsProject):
    def test_no_plans(self):
        view = self.metrics()
        self.assertEqual(view["plans"], [])
        self.assertEqual(view["steps"], [])
        self.assertEqual(view["totals"]["runs"], 0)
        self.assertIn("No plans yet", self.ok("report", "--metrics").stdout)

    def test_step_counts_and_times(self):
        self.make_plan("p", started_at="2026-01-01T00:00:00Z")
        self.ledger([
            {"plan": "p", "step_id": 1, "verified": False, "exit_code": 1, "duration_seconds": 2,
             "kind": "executed", "timestamp": "2026-01-01T00:00:10Z", "test_count_drop": {"from": 5, "to": 3}},
            {"plan": "p", "step_id": 1, "verified": True, "exit_code": 0, "duration_seconds": 3,
             "kind": "executed", "timestamp": "2026-01-01T00:01:00Z"},
            {"plan": "p", "step_id": 1, "verified": True, "exit_code": 0, "duration_seconds": 0,
             "kind": "executed", "timestamp": "2026-01-01T00:02:00Z", "reused_from": "x", "reused_duration_seconds": 3},
            {"plan": "other", "step_id": 1, "verified": True, "kind": "executed", "timestamp": "2026-01-01T00:03:00Z"},
        ])
        view = self.metrics()
        row = view["steps"][0]
        self.assertEqual((row["runs"], row["failures"], row["reused"]), (3, 1, 1))
        self.assertEqual(row["seconds_saved"], 3)
        self.assertEqual(row["executed_seconds"], 5)
        self.assertEqual(row["seconds_to_first_failure"], 10)
        self.assertEqual(row["seconds_to_first_pass"], 60)
        self.assertEqual(row["test_count_drops"], 1)
        self.assertEqual(view["totals"]["runs"], 4)
        self.assertEqual(view["totals"]["test_count_drops"], 1)
        text = self.ok("report", "--metrics").stdout
        self.assertIn("p#1", text)
        self.assertIn("Totals: runs 4", text)

    def test_missing_started_at_is_na(self):
        self.make_plan("p")
        self.ledger([{"plan": "p", "step_id": 1, "verified": True, "kind": "executed", "timestamp": "2026-01-01T00:01:00Z"}])
        row = self.metrics()["steps"][0]
        self.assertIsNone(row["seconds_to_first_pass"])
        self.assertIn("first pass n/a", self.ok("report", "--metrics").stdout)

    def test_plan_filter_and_unknown_plan(self):
        self.make_plan("a")
        self.make_plan("b")
        self.ledger([
            {"plan": "a", "step_id": 1, "verified": True, "kind": "executed", "timestamp": "2026-01-01T00:01:00Z"},
            {"plan": "b", "step_id": 1, "verified": False, "kind": "executed", "timestamp": "2026-01-01T00:01:00Z"},
        ])
        view = self.metrics("--plan", "b")
        self.assertEqual([row["plan"] for row in view["steps"]], ["b"])
        self.assertEqual(view["totals"]["runs"], 1)
        result = self.run_cli("report", "--metrics", "--plan", "nope")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stderr)

    def test_plan_without_metrics_is_refused(self):
        self.assertEqual(self.run_cli("report", "--plan", "a").returncode, 1)

    def test_findings_reviews(self):
        folder = self.state / "reviews"
        folder.mkdir(exist_ok=True)
        review = {"kind": "findings_review", "review_type": "findings", "name": "r", "updated": "z",
                  "rounds": [
                      {"number": 1, "findings": [{"id": "F1", "status": "fixed"},
                                                 {"id": "F2", "status": "dismissed"}]},
                      {"number": 2, "findings": [{"id": "F3", "status": "open"}]}]}
        (folder / "r.json").write_text(json.dumps(review), encoding="utf-8")
        (folder / "blast.json").write_text(json.dumps({"review_type": "blast_radius"}), encoding="utf-8")
        view = self.metrics()
        self.assertEqual(view["reviews"], [{"review": "r", "rounds": 2, "open": 1, "fixed": 1, "dismissed": 1}])
        self.assertEqual(view["totals"]["findings_reviews"], 1)
        self.assertEqual(view["totals"]["review_rounds"], 2)

    def test_json_shape(self):
        view = self.metrics()
        self.assertEqual(set(view), {"plans", "steps", "reviews", "totals"})
        self.assertEqual(set(view["totals"]), {
            "runs", "failures", "reused", "seconds_saved", "test_count_drops", "findings_reviews",
            "review_rounds", "unreadable_reviews", "findings_open", "findings_fixed", "findings_dismissed"})

    def tree(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_malformed_review_stays_and_tree_unchanged(self):
        folder = self.state / "reviews"
        folder.mkdir(exist_ok=True)
        (folder / "bad.json").write_text("{not json", encoding="utf-8")
        before = self.tree(self.state)
        view = self.metrics()
        self.assertEqual(view["totals"]["unreadable_reviews"], 1)
        self.assertIn("Unreadable review files skipped: 1", self.ok("report", "--metrics").stdout)
        self.assertEqual(self.tree(self.state), before)
        self.assertTrue((folder / "bad.json").exists())
        self.assertEqual(list(folder.glob("*.corrupt*")), [])

    def test_mythify_dir_is_not_filled_in(self):
        bare = Path(self.tmp.name) / "bare"
        bare.mkdir()
        env = dict(os.environ, MYTHIFY_DIR=str(bare), HOME=str(self.home))
        result = warm_cli.run(["report", "--metrics"], cwd=str(self.project), env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(bare.iterdir()), [])

    def test_attested_records_change_no_counts(self):
        self.make_plan("p")
        self.ledger([
            {"plan": "p", "step_id": 1, "kind": "executed", "verified": True, "duration_seconds": 2,
             "timestamp": "2026-01-01T00:01:00Z"},
            {"plan": "p", "step_id": 1, "kind": "attested", "verified": False,
             "timestamp": "2026-01-01T00:02:00Z", "reused_from": "x", "reused_duration_seconds": 9},
        ])
        view = self.metrics()
        self.assertEqual(view["steps"][0]["runs"], 1)
        self.assertEqual(view["steps"][0]["failures"], 0)
        self.assertEqual(view["totals"]["runs"], 1)
        self.assertEqual(view["totals"]["reused"], 0)
        self.assertEqual(view["totals"]["seconds_saved"], 0)

    def test_cursor_unchanged(self):
        self.make_plan("p")
        self.ok("report", "--since", "last")
        before = {p: p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        self.assertTrue(any("reports" in p.parts for p in before), "expected a report cursor file")
        self.ok("report", "--metrics")
        self.ok("report", "--metrics", "--json")
        after = {p: p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class CiConcurrencyTests(unittest.TestCase):
    def test_ci_cancels_only_pull_request_runs(self):
        text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("concurrency:\n  group: ci-${{ github.event.pull_request.number || github.ref }}", text)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text)
        self.assertLess(text.index("concurrency:"), text.index("jobs:"))


if __name__ == "__main__":
    unittest.main()
