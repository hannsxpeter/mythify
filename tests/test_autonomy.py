"""Tests for the bounded self-driving outcome loop: agent dispatch, the cost
budget ledger, escalation, and git scope enforcement. The loop stays
evidence-gated (the verifier decides success) and bounded at all times."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class LoopCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self._git("init")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        self.assertEqual(self.run_cli("init").returncode, 0)
        self._git("add", "-A")
        self._git("commit", "-qm", "initialize Mythify workspace")

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=str(self.project), capture_output=True, text=True, check=False)

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

    def start(self, *extra, name, verify, agent, success="done"):
        args = ["outcome", "start", "goal", "--name", name, "--success", success,
                "--verify", verify, "--agent", agent, *extra]
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestDispatchLoop(LoopCase):
    def test_loop_succeeds_via_verifier(self):
        agent = "n=$(cat .n 2>/dev/null || echo 0); n=$((n+1)); echo $n > .n; [ $n -ge 2 ] && touch DONE; true"
        self.start("--max-iterations", "5", name="s", verify="test -f DONE", agent=agent)
        result = self.run_cli("outcome", "run", "s")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("succeeded", result.stdout)

    def test_iteration_budget_halts(self):
        self.start("--max-iterations", "3", name="i", verify="false", agent="true")
        result = self.run_cli("outcome", "run", "i")
        self.assertEqual(result.returncode, 2)
        self.assertIn("iteration budget exhausted", result.stdout)

    def test_cost_budget_halts(self):
        self.start("--max-iterations", "20", "--max-cost", "8", name="c",
                   verify="false", agent="echo MYTHIFY_COST=5")
        result = self.run_cli("outcome", "run", "c")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cost budget exhausted", result.stdout)
        goal = json.loads(self.run_cli("outcome", "status", "c", "--json").stdout)["goal"]
        self.assertGreaterEqual(goal["cost_spent"], 8)

    def test_escalation_after_consecutive_failures(self):
        self.start("--max-iterations", "20", "--escalate-after", "3", name="e",
                   verify="false", agent="true")
        result = self.run_cli("outcome", "run", "e")
        self.assertEqual(result.returncode, 2)
        self.assertIn("escalated after 3", result.stdout)

    def test_scope_violation_halts(self):
        self.start("--max-iterations", "10", "--allowed-paths", "src", name="v",
                   verify="false", agent="echo y > outside.txt")
        result = self.run_cli("outcome", "run", "v")
        self.assertEqual(result.returncode, 2)
        self.assertIn("scope violation", result.stdout)

    def test_run_without_agent_errors(self):
        self.run_cli("outcome", "start", "g", "--name", "na", "--success", "x", "--verify", "true")
        result = self.run_cli("outcome", "run", "na")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no agent command", result.stderr)

    def test_run_respects_disable_run(self):
        self.start("--max-iterations", "3", name="d", verify="true", agent="true")
        result = self.run_cli("outcome", "run", "d", env_extra={"MYTHIFY_DISABLE_RUN": "1"})
        self.assertEqual(result.returncode, 2)

    def test_cost_defaults_to_one_unit_per_iteration(self):
        self.start("--max-iterations", "2", name="u", verify="false", agent="true")
        self.run_cli("outcome", "run", "u")
        goal = json.loads(self.run_cli("outcome", "status", "u", "--json").stdout)["goal"]
        self.assertEqual(goal["cost_spent"], 2.0)

    def test_negative_cost_is_clamped(self):
        # An agent reporting negative cost must not drive the ledger down and
        # neutralize --max-cost; each iteration is clamped to at least zero.
        self.start("--max-iterations", "3", "--max-cost", "10", name="neg",
                   verify="false", agent="echo MYTHIFY_COST=-5")
        result = self.run_cli("outcome", "run", "neg")
        goal = json.loads(self.run_cli("outcome", "status", "neg", "--json").stdout)["goal"]
        self.assertGreaterEqual(goal["cost_spent"], 0.0)
        self.assertIn("iteration budget exhausted", result.stdout)

    def test_check_path_burns_no_cost(self):
        # outcome check (the host made the attempt) must not spend budget; the
        # cost ledger belongs to the self-driving run loop only.
        self.run_cli("outcome", "start", "g", "--name", "chk", "--success", "x",
                     "--verify", "false", "--max-cost", "5", "--max-iterations", "3")
        self.run_cli("outcome", "check", "chk")
        goal = json.loads(self.run_cli("outcome", "status", "chk", "--json").stdout)["goal"]
        self.assertEqual(goal["cost_spent"], 0.0)

    def test_scope_enforced_across_a_large_changeset(self):
        # Scope enforcement must read the full git status, not a truncated tail:
        # an out-of-scope file must still be flagged among hundreds of changes.
        (self.project / "src").mkdir()
        for i in range(250):
            (self.project / "src" / "f{0}.txt".format(i)).write_text("x\n", encoding="utf-8")
        (self.project / "AAA_escape.txt").write_text("out of scope\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "seed")
        agent = (
            "for i in $(seq 0 249); do echo y > src/f$i.txt; done; "
            "echo changed-out-of-scope > AAA_escape.txt"
        )
        self.start("--max-iterations", "1", "--allowed-paths", "src", name="big",
                   verify="false", agent=agent)
        result = self.run_cli("outcome", "run", "big")
        self.assertEqual(result.returncode, 2)
        self.assertIn("scope violation", result.stdout)
        self.assertIn("AAA_escape.txt", result.stdout)


if __name__ == "__main__":
    unittest.main()
