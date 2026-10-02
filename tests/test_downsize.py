"""Mythify 6 downsize contract.

Cut commands, flags, modules, and manifests stay gone; `status` is the single
orientation view; `outcome status` lists every loop when none is active; init
creates only the folders v6 uses; and state left behind by older versions
(research, campaigns, designs, evals, fanout, plan archetypes) is ignored
without error.
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

DELETED_PATHS = (
    "scripts/mythify_trace.py",
    "scripts/mythify_artifacts.py",
    "scripts/mythify_artifact_parser.py",
    "scripts/mythify_evals.py",
    "scripts/mythify_eval_parser.py",
    "scripts/mythify_eval_scenarios.py",
    "scripts/local_model_eval.py",
    "scripts/local_eval_policy.py",
    "scripts/mythify_workflows.py",
    "scripts/mythify_workspace.py",
    "scripts/mythify_designs.py",
    "scripts/mythify_plan_horizon.py",
    "scripts/mythify_protocol_profiles.py",
    "scripts/mythify_views_status.py",
    "scripts/mythify_chat_report_hook.sh",
    "protocol/artifact-hygiene.json",
    "protocol/loading-profiles.json",
    "protocol/release-gates.json",
    "protocol/operation-registry.json",
    "protocol/surface-manifest.json",
    "protocol/variants",
    "docs/artifact-hygiene.md",
    ".mythify/workspace.json",
)

REMOVED_FOLDERS = ("research", "campaigns", "designs", "evals", "fanout")


class DownsizeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.state = self.project / ".mythify"

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        env["HOME"] = str(self.home)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def ok(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, "{0}: {1}".format(args, result.stderr))
        return result


class TestCutSurface(DownsizeCase):
    def test_deleted_paths_are_gone(self):
        for relative in DELETED_PATHS:
            self.assertFalse((REPO_ROOT / relative).exists(), relative)
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".mythify/\n", gitignore)
        self.assertNotIn("!.mythify", gitignore)

    def test_cut_commands_and_flags_are_rejected(self):
        self.ok("init")
        self.ok("plan", "create", "Goal", "--steps", json.dumps([{"title": "a"}]))
        cases = (
            ("trace", "analyze", "x.jsonl"),
            ("artifact", "probe"),
            ("eval", "scan"),
            ("campaign", "start", "goal"),
            ("research", "start", "question"),
            ("workspace", "show"),
            ("design", "create", "title", "--problem", "p"),
            ("dashboard",),
            ("harness",),
            ("background",),
            ("progress",),
            ("readiness",),
            ("timeline",),
            ("phase",),
            ("review", "create", "--status", "pass", "--path", "a.py"),
            ("prompt", "research"),
            ("prompt", "analysis"),
            ("prompt", "campaign"),
            ("plan", "create", "g", "--horizon", "3"),
            ("plan", "create", "g", "--archetype", "rpi"),
            ("plan", "create", "g", "--design", "d"),
            ("plan", "add-step", "s", "--phase", "build"),
            ("plan", "add-step", "s", "--vertical-slice", "{}"),
            ("map", "promote", "--horizon", "2"),
        )
        for argv in cases:
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
                self.assertRegex(result.stderr, r"invalid choice|unrecognized arguments")

    def test_plan_horizon_environment_is_ignored(self):
        self.ok("init")
        self.ok("plan", "create", "Env goal", env_extra={"MYTHIFY_PLAN_HORIZON": "3"})
        plan = json.loads((self.state / "plans" / "env-goal.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["steps"], [])
        self.assertNotIn("archetype", plan)


class TestInitLayout(DownsizeCase):
    def test_init_creates_no_folders_for_removed_features(self):
        self.ok("init")
        created = sorted(path.name for path in self.state.iterdir() if path.is_dir())
        self.assertEqual(
            created,
            ["lessons", "logs", "maps", "outcomes", "plans", "reports", "reviews",
             "verification-artifacts"],
        )
        for folder in REMOVED_FOLDERS:
            self.assertFalse((self.state / folder).exists(), folder)

    def test_mythify_dir_layout_matches_init(self):
        custom = self.project / "custom-state"
        self.ok("memory", "set", "k", "v", env_extra={"MYTHIFY_DIR": str(custom)})
        for folder in REMOVED_FOLDERS:
            self.assertFalse((custom / folder).exists(), folder)


class TestLegacyStateIsIgnored(DownsizeCase):
    def seed_v5_leftovers(self):
        self.ok("init")
        for folder in REMOVED_FOLDERS:
            (self.state / folder).mkdir()
            (self.state / folder / "active").write_text("legacy\n", encoding="utf-8")
            (self.state / folder / "legacy.json").write_text(
                json.dumps({"id": "legacy", "status": "active"}) + "\n", encoding="utf-8"
            )
        (self.state / "fanout" / "fo-20260613151515-abcd").mkdir()
        (self.state / "host-model.json").write_text("{}\n", encoding="utf-8")
        plan = {
            "name": "legacy-plan",
            "goal": "Old design-heavy plan",
            "archetype": "design-heavy",
            "design": "old-design",
            "created": "2026-06-16T00:00:00+00:00",
            "last_updated": "2026-06-16T00:00:00+00:00",
            "steps": [
                {
                    "id": 1,
                    "title": "Runnable slice",
                    "success_criteria": "",
                    "status": "pending",
                    "result": None,
                    "phase": "build",
                    "vertical_slice": {"result": "CLI works", "files": [],
                                       "automated_checks": [], "manual_checks": []},
                    "verify_command": "python3 -c 'raise SystemExit(0)'",
                },
            ],
        }
        (self.state / "plans" / "legacy-plan.json").write_text(
            json.dumps(plan) + "\n", encoding="utf-8"
        )
        (self.state / "plans" / "active").write_text("legacy-plan\n", encoding="utf-8")

    def test_v6_commands_ignore_leftover_folders_and_fields(self):
        self.seed_v5_leftovers()
        status = self.ok("status")
        self.assertIn("Active plan: legacy-plan (0/1 completed)", status.stdout)
        payload = json.loads(self.ok("status", "--json").stdout)
        self.assertEqual(payload["active_plan"]["id"], "legacy-plan")
        summary = json.loads(self.ok("summary", "--json").stdout)
        self.assertEqual([plan["id"] for plan in summary["plans"]], ["legacy-plan"])
        self.assertEqual(summary["outcomes"], [])
        self.ok("summary")
        self.ok("route", "continue", "--json")
        self.ok("prompt", "next")
        self.ok("history")
        self.ok("report", "--peek")
        self.ok("outcome", "status")
        self.ok("plan", "show")
        added = self.ok("plan", "add-step", "Still works", "--verify", "python3 -m unittest")
        self.assertIn("Added step 2", added.stdout)
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "verify run exit 0")
        for folder in REMOVED_FOLDERS:
            self.assertTrue((self.state / folder / "legacy.json").exists(), folder)


class TestOutcomeStatusListing(DownsizeCase):
    def start(self, name, verify="true"):
        return self.ok(
            "outcome", "start", "Goal " + name, "--success", "ok",
            "--verify", verify, "--name", name,
        )

    def test_no_outcomes_lists_none_and_exits_zero(self):
        self.ok("init")
        result = self.ok("outcome", "status")
        self.assertIn("Outcomes (0); none is active:", result.stdout)
        payload = json.loads(self.ok("outcome", "status", "--json").stdout)
        self.assertEqual(payload, {"active": None, "outcomes": []})

    def test_lists_every_outcome_when_none_is_active(self):
        self.ok("init")
        self.start("first")
        self.ok("outcome", "check")
        self.ok("outcome", "stop", "--reason", "done with it")
        self.start("second")
        self.ok("outcome", "stop", "--reason", "parked")
        before = sorted(p.name for p in (self.state / "outcomes").iterdir())
        result = self.ok("outcome", "status")
        self.assertIn("Outcomes (2); none is active:", result.stdout)
        self.assertIn("first: Goal first (", result.stdout)
        self.assertIn("second: Goal second (stopped, 0/3 iterations)", result.stdout)
        self.assertIn("last check: iteration 1, verified=True", result.stdout)
        payload = json.loads(self.ok("outcome", "status", "--json").stdout)
        self.assertIsNone(payload["active"])
        rows = {row["id"]: row for row in payload["outcomes"]}
        self.assertEqual(set(rows), {"first", "second"})
        self.assertEqual(rows["second"]["status"], "stopped")
        self.assertEqual(rows["first"]["last_check"]["iteration"], 1)
        self.assertEqual(sorted(p.name for p in (self.state / "outcomes").iterdir()), before)

    def test_active_and_named_outcomes_keep_the_detail_view(self):
        self.ok("init")
        self.start("live")
        active = self.ok("outcome", "status")
        self.assertIn("[OK] Outcome live: Goal live", active.stdout)
        detail = json.loads(self.ok("outcome", "status", "live", "--json").stdout)
        self.assertEqual(detail["goal"]["id"], "live")
        missing = self.run_cli("outcome", "status", "nope")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("No outcome found", missing.stdout + missing.stderr)


if __name__ == "__main__":
    unittest.main()
