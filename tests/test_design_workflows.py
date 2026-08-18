import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "mythify.py"


class DesignWorkflowTests(unittest.TestCase):
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

    def test_design_record_covers_product_system_and_program_decisions(self):
        created = self.run_cli(
            "design",
            "create",
            "Quality architecture",
            "--problem",
            "Tests do not prove maintainability",
            "--current-state",
            "Generic plan",
            "--desired-state",
            "Reviewable design",
            "--product",
            "User-visible result",
            "--system",
            "State contract",
            "--program",
            "Function seams",
            "--name",
            "quality-architecture",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        approved = self.run_cli(
            "design", "approve", "quality-architecture", "--note", "Reviewed"
        )
        self.assertEqual(approved.returncode, 0, approved.stderr)
        shown = self.run_cli("design", "show", "quality-architecture", "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        record = json.loads(shown.stdout)
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["system_decisions"], "State contract")
        self.assertNotIn("verified", record)

    def test_design_heavy_plan_requires_vertical_build_slices(self):
        missing = self.run_cli(
            "plan",
            "create",
            "Ship feature",
            "--name",
            "missing-slice",
            "--archetype",
            "design-heavy",
            "--steps",
            json.dumps([{"title": "Build", "phase": "build"}]),
        )
        self.assertEqual(missing.returncode, 1)
        self.assertIn("require vertical_slice", missing.stderr)

        steps = [
            {"title": "Research", "phase": "understand"},
            {"title": "Product decision", "phase": "product"},
            {"title": "System decision", "phase": "system"},
            {"title": "Program decision", "phase": "program"},
            {
                "title": "Runnable slice",
                "phase": "build",
                "vertical_slice": {
                    "result": "CLI returns the new record",
                    "files": ["scripts/example.py"],
                    "automated_checks": ["python3 -m unittest"],
                    "manual_checks": [],
                },
            },
        ]
        created = self.run_cli(
            "plan",
            "create",
            "Ship feature",
            "--name",
            "vertical-plan",
            "--archetype",
            "design-heavy",
            "--design",
            "quality-architecture",
            "--steps",
            json.dumps(steps),
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        plan = json.loads((self.state / "plans" / "vertical-plan.json").read_text())
        self.assertEqual(plan["archetype"], "design-heavy")
        self.assertEqual(plan["steps"][4]["vertical_slice"]["result"], "CLI returns the new record")

    def test_phase_view_prefers_explicit_product_system_and_program_phases(self):
        created = self.run_cli(
            "plan",
            "create",
            "Phase plan",
            "--name",
            "phase-plan",
            "--steps",
            json.dumps(
                [
                    {"title": "One", "phase": "product"},
                    {"title": "Two", "phase": "system"},
                    {"title": "Three", "phase": "program"},
                ]
            ),
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        viewed = self.run_cli("phase", "--json")
        self.assertEqual(viewed.returncode, 0, viewed.stderr)
        phases = {row["id"]: row for row in json.loads(viewed.stdout)["phases"]}
        self.assertEqual(phases["design"]["step_counts"]["total"], 3)

    def test_old_plan_without_archetype_remains_mutable(self):
        self.state.mkdir(parents=True)
        (self.state / "plans").mkdir()
        (self.state / "plans" / "active").write_text("legacy\n")
        (self.state / "plans" / "legacy.json").write_text(
            json.dumps({"name": "legacy", "goal": "Old", "steps": []}) + "\n"
        )
        added = self.run_cli("plan", "add-step", "Still works", "--phase", "build")
        self.assertEqual(added.returncode, 0, added.stderr)
        plan = json.loads((self.state / "plans" / "legacy.json").read_text())
        self.assertEqual(plan["steps"][0]["phase"], "build")


if __name__ == "__main__":
    unittest.main()
