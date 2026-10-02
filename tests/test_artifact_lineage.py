import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "mythify.py"


class ArtifactLineageTests(unittest.TestCase):
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

    def create_parent_map(self):
        result = self.run_cli("map", "create", "Choose a seam", "--name", "parent")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plan_lineage_becomes_stale_when_parent_map_changes(self):
        self.create_parent_map()
        created = self.run_cli(
            "plan", "create", "Child", "--name", "child", "--parent", "map:parent"
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        current = self.run_cli("lineage", "status", "plan", "child", "--json")
        self.assertEqual(json.loads(current.stdout)["status"], "current")
        changed = self.run_cli(
            "map", "ticket", "Pick the interface", "--type", "grilling", "--map", "parent"
        )
        self.assertEqual(changed.returncode, 0, changed.stderr)
        stale = self.run_cli("lineage", "status", "plan", "child", "--json")
        payload = json.loads(stale.stdout)
        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["parents"][0]["status"], "stale")
        self.assertEqual(payload["precedence"][-1], "executed_verification_completion")
        status = self.run_cli("status", "--json")
        self.assertEqual(json.loads(status.stdout)["active_plan"]["lineage"]["status"], "stale")
        summary = self.run_cli("summary")
        self.assertIn("lineage: stale", summary.stdout)
        summary_json = json.loads(self.run_cli("summary", "--json").stdout)
        self.assertEqual(summary_json["plans"][0]["lineage"], "stale")

    def test_generic_attach_supports_plan_and_outcome_children(self):
        self.create_parent_map()
        fixtures = {
            "plan": self.state / "plans" / "delivery-plan.json",
            "outcome": self.state / "outcomes" / "delivery" / "goal.json",
        }
        for kind, target in fixtures.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"name": target.stem, "created": "2026-08-17T00:00:00Z"}) + "\n")
            attached = self.run_cli(
                "lineage", "attach", kind, "delivery" if kind == "outcome" else target.stem,
                "--parent", "map:parent",
            )
            self.assertEqual(attached.returncode, 0, attached.stderr)
            record = json.loads(target.read_text())
            self.assertEqual(record["lineage"]["parents"][0]["kind"], "map")

    def test_removed_kinds_are_rejected_and_legacy_parents_read_unknown(self):
        self.create_parent_map()
        for kind in ("design", "research"):
            refused = self.run_cli("lineage", "status", kind, "anything")
            self.assertEqual(refused.returncode, 64, kind)
            self.assertIn("invalid choice", refused.stderr)
        legacy = self.state / "plans" / "legacy-child.json"
        legacy.write_text(json.dumps({
            "name": "legacy-child",
            "goal": "old",
            "steps": [],
            "lineage": {
                "captured_at": "2026-08-17T00:00:00Z",
                "parents": [{"kind": "design", "id": "gone", "revision": "abc"}],
            },
        }) + "\n")
        status = self.run_cli("lineage", "status", "plan", "legacy-child", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["status"], "unknown")

    def test_verification_captures_parent_and_legacy_record_reports_unknown(self):
        self.create_parent_map()
        verified = self.run_cli(
            "verify", "run", "true", "--parent", "map:parent"
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        record = json.loads((self.state / "verifications.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["lineage"]["parents"][0]["id"], "parent")
        lineage = self.run_cli("lineage", "status", "verification", record["id"], "--json")
        self.assertEqual(json.loads(lineage.stdout)["status"], "current")
        legacy = self.state / "plans" / "legacy.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"name": "legacy", "goal": "old", "steps": []}) + "\n")
        unknown = self.run_cli("lineage", "status", "plan", "legacy", "--json")
        self.assertEqual(json.loads(unknown.stdout)["status"], "unknown")

    def test_invalid_verification_parent_fails_before_command_execution(self):
        marker = self.root / "command-ran"
        command = '{0} -c "from pathlib import Path; Path({1}).write_text(\'ran\')"'.format(
            sys.executable, repr(str(marker))
        )
        result = self.run_cli(
            "verify", "run", command, "--parent", "map:missing"
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(marker.exists())
        artifact_root = self.state / "verification-artifacts"
        self.assertFalse(artifact_root.exists() and any(artifact_root.iterdir()))

    def test_artifact_cannot_parent_itself(self):
        self.create_parent_map()
        result = self.run_cli(
            "lineage", "attach", "map", "parent", "--parent", "map:parent"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot be its own lineage parent", result.stderr)


if __name__ == "__main__":
    unittest.main()
