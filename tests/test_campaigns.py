"""Campaigns reuse plans and their evidence gates through the real CLI."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "scripts" / "mythify.py"


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.state = self.project / ".mythify"
        self.env = dict(os.environ)
        self.env.pop("MYTHIFY_DIR", None)
        self.env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        self.env["HOME"] = self.tmp.name
        self.ok("init")
        self.verifier = shlex.quote(sys.executable) + " -c 'assert 2 + 2 == 4'"

    def run_cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(CLI), *args], cwd=self.project,
            env=env or self.env, capture_output=True, text=True, timeout=30,
        )

    def ok(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def plan(self, name="existing"):
        return json.loads((self.state / "plans" / (name + ".json")).read_text())

    def snapshot(self):
        return {str(p.relative_to(self.state)): p.read_bytes()
                for p in self.state.rglob("*") if p.is_file()}

    def create_plan(self):
        self.ok("plan", "create", "Existing goal", "--name", "existing", "--steps",
                json.dumps([{"title": "First", "verify_command": self.verifier},
                            {"title": "Second", "verify_command": self.verifier}]))

    def test_start_reuses_existing_plan_and_preserves_progress_and_evidence(self):
        self.create_plan()
        self.ok("step", "1", "in_progress")
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "First check passed")
        self.ok("step", "2", "in_progress")
        before = self.snapshot()
        original = self.plan()
        self.ok("campaign", "start", "Finish existing work", "--success", "Both steps pass")
        updated = self.plan()
        self.assertEqual(updated["steps"], original["steps"])
        self.assertEqual(updated["verification_anchor"], original["verification_anchor"])
        self.assertEqual(updated["goal"], original["goal"])
        self.assertTrue(updated["strict_context"])
        self.assertEqual(len(list((self.state / "plans").glob("*.json"))), 1)
        self.assertFalse((self.state / "campaigns").exists())
        after = self.snapshot()
        for path, content in before.items():
            if path != "plans/existing.json":
                self.assertEqual(after[path], content, path)
        stable = self.snapshot()
        self.ok("campaign", "start", "Finish existing work")
        self.assertEqual(self.snapshot(), stable)

    def test_advance_requires_started_step_and_scoped_verification(self):
        self.create_plan()
        self.ok("campaign", "start", "Finish")
        before = self.snapshot()
        result = self.run_cli("campaign", "advance", "--result", "looks done")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.snapshot(), before)
        self.ok("step", "1", "in_progress")
        result = self.run_cli("campaign", "advance", "--result", "looks done")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Verified evidence required", result.stderr)
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Stored verifier passed")
        self.assertEqual(self.plan()["steps"][0]["status"], "completed")
        self.assertEqual(self.plan()["steps"][1]["status"], "pending")

    def test_failed_latest_check_and_waiver_cannot_advance(self):
        self.create_plan()
        self.ok("campaign", "start", "Finish")
        self.ok("step", "1", "in_progress")
        env = dict(self.env, MYTHIFY_REQUIRE_VERIFIED_STEP="0")
        result = self.run_cli("campaign", "advance", "--result", "waive", env=env)
        self.assertEqual(result.returncode, 1)
        # Same command passes, then fails after its input changes.
        check = self.project / "check.py"
        check.write_text("raise SystemExit(0)\n")
        command = shlex.quote(sys.executable) + " check.py"
        self.ok("plan", "create", "Gate", "--steps", json.dumps([
            {"title": "Gate", "verify_command": command}]))
        self.ok("campaign", "start", "Gate")
        self.ok("step", "1", "in_progress")
        self.ok("plan", "verify", "1")
        check.write_text("raise SystemExit(1)\n")
        self.assertEqual(self.run_cli("plan", "verify", "1").returncode, 2)
        self.assertEqual(self.run_cli("campaign", "advance", "--result", "old pass").returncode, 1)

    def test_named_prompt_and_status_are_read_only_and_resume_in_new_process(self):
        self.ok("campaign", "start", "Deliver", "--name", "delivery", "--success", "Checks pass",
                "--tasks", json.dumps([{"title": "Implement", "verify_command": self.verifier}]))
        self.ok("campaign", "learn", "Keep the existing verifier", "--apply-next")
        self.ok("plan", "create", "Other")
        before = self.snapshot()
        status = json.loads(self.ok("campaign", "status", "delivery", "--json").stdout)
        packet = json.loads(self.ok("campaign", "prompt", "delivery", "--json").stdout)
        self.assertEqual(status["plan"], "delivery")
        self.assertEqual(status["status"], "active")
        self.assertIn("Keep the existing verifier", packet["next_prompt"])
        self.assertIn("--plan delivery", packet["next_prompt"])
        resumed = self.ok("campaign", "start", "Deliver", "--plan", "delivery")
        self.assertIn("Next: mythify campaign prompt delivery", resumed.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_complete_campaign_has_no_next_step_and_skips_are_not_completion(self):
        self.ok("campaign", "start", "Deliver", "--verify", self.verifier)
        self.ok("step", "1", "in_progress")
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Passed")
        view = json.loads(self.ok("campaign", "status", "--json").stdout)
        self.assertEqual(view["status"], "completed")
        self.assertIsNone(view["current_step"])
        before = self.snapshot()
        self.assertEqual(self.run_cli("campaign", "advance", "--result", "again").returncode, 1)
        self.assertEqual(self.snapshot(), before)
        self.ok("step", "1", "skipped")
        view = json.loads(self.ok("campaign", "status", "--json").stdout)
        self.assertEqual(view["status"], "blocked")

    def test_invalid_tasks_and_conflicting_start_leave_state_unchanged(self):
        for tasks in ('{}', '[{}]', '[{"title": ""}]', '[{"title": 4}]'):
            before = self.snapshot()
            self.assertEqual(self.run_cli("campaign", "start", "New", "--tasks", tasks).returncode, 1)
            self.assertEqual(self.snapshot(), before)
        self.create_plan()
        before = self.snapshot()
        self.assertEqual(self.run_cli("campaign", "start", "New", "--tasks", '[{"title":"replace"}]').returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_prompt_packet_next_and_route_resume_campaign(self):
        self.ok("campaign", "start", "Deliver", "--verify", self.verifier)
        before = self.snapshot()
        packet = json.loads(self.ok("prompt", "next", "--json").stdout)
        self.assertEqual(packet["selected_kind"], "campaign")
        explicit = json.loads(self.ok("prompt", "campaign", "--json").stdout)
        self.assertEqual(explicit["source"]["id"], "deliver")
        for task in ("continue", "keep going until done", "full send"):
            route = json.loads(self.ok("route", task, "--json").stdout)
            self.assertIn("mythify campaign prompt", json.dumps(route))
        self.assertEqual(self.snapshot(), before)

    def test_advance_rejects_evidence_for_another_plan(self):
        self.create_plan()
        self.ok("campaign", "start", "Finish")
        self.ok("step", "1", "in_progress")
        self.ok("plan", "create", "Other", "--steps", json.dumps([
            {"title": "Unrelated", "verify_command": self.verifier}]))
        self.ok("plan", "verify", "1")
        before = self.snapshot()
        result = self.run_cli("campaign", "advance", "existing", "--result", "Other check passed")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_manual_verifier_works_for_an_existing_plan_without_stored_command(self):
        self.ok("plan", "create", "Manual", "--steps", '[{"title":"Check manually"}]')
        self.ok("campaign", "start", "Manual")
        self.ok("step", "1", "in_progress")
        self.ok("verify", "run", self.verifier)
        self.ok("campaign", "advance", "--result", "Manual check passed")

    def test_watch_is_read_only_and_rejects_invalid_limits(self):
        self.ok("campaign", "start", "Deliver")
        before = self.snapshot()
        result = self.ok("campaign", "watch", "--interval", "0.001", "--max-iterations", "2")
        self.assertEqual(result.stdout.count("Campaign: deliver"), 1)
        for args in (("--interval", "0"), ("--interval", "nan"),
                     ("--interval", "inf"), ("--max-iterations", "-1")):
            self.assertEqual(self.run_cli("campaign", "watch", *args).returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_watch_reloads_changed_prompts_without_writing_state(self):
        self.ok("campaign", "start", "Deliver")
        process = subprocess.Popen(
            [sys.executable, str(CLI), "campaign", "watch", "--interval", "0.02",
             "--max-iterations", "60"], cwd=self.project, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            first = []
            while True:
                line = process.stdout.readline()
                self.assertTrue(line, "Watcher exited before first prompt")
                first.append(line)
                if line.startswith("Guardrail:"):
                    break
            self.ok("campaign", "learn", "Changed frontier", "--apply-next")
            before = self.snapshot()
            output, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual(output.count("Campaign: deliver"), 1)
            self.assertEqual(output.count("Changed frontier"), 1)
            self.assertNotIn("Changed frontier", "".join(first))
            self.assertEqual(self.snapshot(), before)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_malformed_campaign_metadata_does_not_break_orientation(self):
        self.create_plan()
        path = self.state / "plans" / "existing.json"
        for metadata in ({"schema_version": 1}, {"schema_version": 1, "goal": "g", "lessons": [None]}):
            plan = self.plan()
            plan["campaign"] = metadata
            path.write_text(json.dumps(plan))
            before = self.snapshot()
            self.ok("status")
            self.ok("prompt", "next")
            self.assertEqual(self.run_cli("campaign", "prompt").returncode, 1)
            self.assertEqual(self.run_cli("campaign", "start", "Finish").returncode, 1)
            self.assertEqual(self.snapshot(), before)

    def test_learning_handles_optional_lessons_field(self):
        self.ok("campaign", "start", "Deliver")
        path = self.state / "plans" / "deliver.json"
        plan = json.loads(path.read_text())
        del plan["campaign"]["lessons"]
        path.write_text(json.dumps(plan))
        self.ok("campaign", "status")
        self.ok("campaign", "learn", "Keep progress")
        self.assertIn("Keep progress", self.ok("campaign", "prompt").stdout)

    def test_active_pointer_cannot_escape_plan_store(self):
        self.ok("campaign", "start", "Deliver")
        outside = self.project / "outside.json"
        outside.write_bytes((self.state / "plans" / "deliver.json").read_bytes())
        original = outside.read_bytes()
        pointer = self.state / "plans" / "active"
        for name in ("../../outside", str(outside.with_suffix("")), "../plans/deliver"):
            pointer.write_text(name + "\n")
            before = self.snapshot()
            for args in (("learn", "Tampered"), ("start", "Other"), ("prompt",), ("status",)):
                result = self.run_cli("campaign", *args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(outside.read_bytes(), original)
                self.assertEqual(self.snapshot(), before)

    def test_campaign_refuses_plan_and_parent_symlinks_outside_state(self):
        self.ok("campaign", "start", "Deliver")
        path = self.state / "plans" / "deliver.json"
        outside = self.project / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
        original = outside.read_bytes()
        for args in (("learn", "Tampered"), ("start", "Deliver", "--plan", "deliver"),
                     ("prompt", "deliver"), ("status", "deliver")):
            result = self.run_cli("campaign", *args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(outside.read_bytes(), original)
            self.assertTrue(path.is_symlink())
        path.unlink()
        outside.rename(path)
        plans = self.state / "plans"
        outside_plans = self.project / "outside-plans"
        plans.rename(outside_plans)
        plans.symlink_to(outside_plans, target_is_directory=True)
        before = {p.name: p.read_bytes() for p in outside_plans.glob("*") if p.is_file()}
        for args in (("learn", "Tampered"), ("start", "Other"), ("prompt",), ("status",)):
            result = self.run_cli("campaign", *args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        after = {p.name: p.read_bytes() for p in outside_plans.glob("*") if p.is_file()}
        self.assertEqual(after, before)
        (outside_plans / "archive").rmdir()
        env = dict(self.env, MYTHIFY_DIR=str(self.state))
        result = self.run_cli("campaign", "status", env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((outside_plans / "archive").exists())

    def test_pointer_and_archive_symlinks_cannot_escape_store(self):
        self.ok("campaign", "start", "Deliver")
        pointer = self.state / "plans" / "active"
        outside_pointer = self.project / "outside-pointer"
        outside_pointer.write_text("deliver\n")
        pointer.unlink()
        pointer.symlink_to(outside_pointer)
        self.assertEqual(self.run_cli("campaign", "learn", "Tampered").returncode, 1)
        self.assertEqual(outside_pointer.read_text(), "deliver\n")
        pointer.unlink()
        pointer.write_text("deliver\n")
        archive = self.state / "plans" / "archive"
        archive.rmdir()
        outside_archive = self.project / "outside-archive"
        outside_archive.mkdir()
        archive.symlink_to(outside_archive, target_is_directory=True)
        for args in (("plan", "archive", "deliver"), ("plan", "create", "Other")):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(outside_archive.iterdir()), [])
        self.assertTrue((self.state / "plans" / "deliver.json").is_file())

    def test_campaign_inspection_preserves_corrupt_plan_bytes(self):
        self.ok("campaign", "start", "Deliver")
        path = self.state / "plans" / "deliver.json"
        for bad in (b"{broken", bytes([255, 254])):
            path.write_bytes(bad)
            before = self.snapshot()
            for args in (("campaign", "status"), ("campaign", "prompt"),
                         ("campaign", "watch"), ("prompt", "campaign")):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.snapshot(), before)
            for args in (("prompt", "next"), ("prompt", "handoff"), ("status",)):
                result = self.ok(*args)
                self.assertIn("file left unchanged", result.stderr)
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
