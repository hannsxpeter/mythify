"""End-to-end coverage of the workflow router decision tree.

Asserts every route id the router can emit, the documented precedence order
(full-send language outranks outcome terms), and the active-state resume
branches. Routing is read-only: each case sets up only the durable state it
needs, then asserts the chosen route and next command. Companion coverage:
tests/test_godfiles.py owns god-artifact routing, import, and the strict gate;
tests/test_interop.py owns CLI/MCP route parity.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class RouteCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self.assertEqual(self.run_cli("init").returncode, 0)

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env.pop("MYTHIFY_PLAN_HORIZON", None)
        env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def route(self, task):
        result = self.run_cli("route", task, "--json", "--triage", "never")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class TestRouteMatrix(RouteCase):
    def test_direct(self):
        self.assertEqual(self.route("What does the status command show?")["route"], "direct")

    def test_plan_fallback(self):
        payload = self.route("Add an export endpoint with validation, tests, and docs")
        self.assertEqual(payload["route"], "plan")
        self.assertIn("plan create", payload["next_command"])
        self.assertIn("--horizon 20", payload["next_command"])

    def test_research(self):
        payload = self.route("Research the latest options for the wire format")
        self.assertEqual(payload["route"], "research")
        self.assertIn("research start", payload["next_command"])

    def test_review(self):
        payload = self.route("Audit this module and find the risks")
        self.assertEqual(payload["route"], "review")
        self.assertIn("prompt review", payload["next_command"])

    def test_outcome(self):
        payload = self.route("Iterate until the unit tests pass")
        self.assertEqual(payload["route"], "outcome")
        self.assertIn("outcome start", payload["next_command"])

    def test_prompt(self):
        payload = self.route("Give me the next prompt packet to steer the chat")
        self.assertEqual(payload["route"], "prompt")

    def test_campaign_full_send(self):
        payload = self.route("One shot this project end to end, ship it")
        self.assertEqual(payload["route"], "campaign")
        self.assertIn("campaign start", payload["next_command"])

    def test_full_send_beats_outcome(self):
        # Documented precedence: full-send language (priority 2) outranks the
        # outcome-terms branch (priority 6). "keep going" is a full-send term.
        self.assertEqual(self.route("Keep going until the tests pass")["route"], "campaign")

    def test_failure_on_red_verification(self):
        self.run_cli("plan", "create", "temp", "--steps", json.dumps([{"title": "t"}]))
        self.run_cli("step", "1", "in_progress")
        self.run_cli("verify", "run", "false", "--claim", "intentionally red")
        payload = self.route("continue the work")
        self.assertEqual(payload["route"], "failure")
        self.assertEqual(payload["next_command"], "python3 scripts/mythify.py prompt failure")

    def test_handoff_on_active_plan_resume(self):
        self.run_cli("plan", "create", "feature", "--steps", json.dumps([{"title": "s1"}]))
        payload = self.route("continue from where we left off")
        self.assertEqual(payload["route"], "handoff")
        self.assertIn("prompt handoff", payload["next_command"])

    def test_campaign_active_resume(self):
        self.run_cli("campaign", "start", "ship docs", "--tasks", json.dumps(["a"]))
        payload = self.route("continue")
        self.assertEqual(payload["route"], "campaign")
        self.assertIn("campaign prompt", payload["next_command"])

    def test_outcome_active_resume(self):
        self.run_cli("outcome", "start", "green suite", "--success", "tests pass", "--verify", "true")
        payload = self.route("continue")
        self.assertEqual(payload["route"], "outcome")
        self.assertIn("outcome status", payload["next_command"])

    def test_research_active_resume(self):
        self.run_cli("research", "start", "wire format", "--name", "wf")
        self.assertEqual(self.route("continue")["route"], "research")


if __name__ == "__main__":
    unittest.main()
