"""P-MUST-02 verification provenance and freshness tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subprocess_env  # noqa: E402,F401  (faster subprocesses, same behavior)
import warm_cli  # noqa: E402  (runs the CLI forked from a warm interpreter)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "mythify.py"
sys.path.insert(0, str(SCRIPTS_DIR))

import mythify_provenance  # noqa: E402
from mythify_provenance import evidence_moved_since_run  # noqa: E402


class VerificationProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="mythify-provenance-proj-"))
        self.home = Path(tempfile.mkdtemp(prefix="mythify-provenance-home-"))
        self.addCleanup(shutil.rmtree, str(self.project), True)
        self.addCleanup(shutil.rmtree, str(self.home), True)

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        return warm_cli.run(list(args), cwd=str(self.project), env=env, timeout=120)

    def init_git_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "config", "user.email", "mythify@example.invalid"],
            cwd=self.project,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Mythify Test"],
            cwd=self.project,
            check=True,
        )
        (self.project / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.project, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def latest_record(self):
        path = self.project / ".mythify" / "verifications.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return rows[-1]

    def test_p_must_02_cli_records_commit_and_version_provenance(self):
        commit = self.init_git_repo()
        self.assertEqual(self.run_cli("init").returncode, 0)
        subprocess.run(["git", "add", ".gitignore"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "ignore state"], cwd=self.project, check=True)
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.project, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        result = self.run_cli("verify", "run", "true", "--claim", "provenance check")
        self.assertEqual(result.returncode, 0, result.stderr)

        record = self.latest_record()
        version = self.run_cli("--version").stdout.strip().removeprefix("Mythify v")
        self.assertEqual(record["provenance"]["git_commit"], commit)
        self.assertTrue(record["provenance"]["worktree_clean"])
        self.assertEqual(len(record["provenance"]["worktree_digest"]), 64)
        self.assertEqual(record["provenance"]["mythify_version"], version)

    def test_p_must_02_non_git_execution_keeps_version_provenance(self):
        self.assertEqual(self.run_cli("init").returncode, 0)
        result = self.run_cli("verify", "run", "true")
        self.assertEqual(result.returncode, 0, result.stderr)

        provenance = dict(self.latest_record()["provenance"])
        self.assertEqual(len(provenance.pop("environment_digest")), 64)
        self.assertEqual(
            provenance,
            {
                "git_commit": None,
                "worktree_clean": None,
                "worktree_digest": None,
                "mythify_version": self.run_cli("--version").stdout.strip().removeprefix("Mythify v"),
            },
        )

    def test_evidence_moved_since_run_names_only_visible_movement(self):
        clean = {"git_commit": "same", "worktree_clean": True}
        self.assertIsNone(evidence_moved_since_run({}, clean))
        self.assertIsNone(
            evidence_moved_since_run({"provenance": {"git_commit": "same", "worktree_clean": True}}, clean)
        )
        self.assertEqual(
            evidence_moved_since_run(
                {"provenance": {"git_commit": "old", "worktree_clean": True}},
                {"git_commit": "new", "worktree_clean": True},
            ),
            "git_commit_changed_since_run",
        )
        self.assertEqual(
            evidence_moved_since_run(
                {"provenance": {"git_commit": "same", "worktree_clean": True}},
                {"git_commit": "same", "worktree_clean": False},
            ),
            "worktree_changed_since_run",
        )
        # The dev-loop case stays silent: dirty at run, dirty at completion.
        self.assertIsNone(
            evidence_moved_since_run(
                {"provenance": {"git_commit": "same", "worktree_clean": False}},
                {"git_commit": "same", "worktree_clean": False},
            )
        )
        self.assertEqual(
            evidence_moved_since_run(
                {"provenance": {"git_commit": "same", "worktree_clean": False, "worktree_digest": "one"}},
                {"git_commit": "same", "worktree_clean": False, "worktree_digest": "two"},
            ),
            "worktree_digest_changed_since_run",
        )
        # Off-git runs carry no commit and never claim movement.
        self.assertIsNone(
            evidence_moved_since_run(
                {"provenance": {"git_commit": None, "worktree_clean": None}},
                {"git_commit": None, "worktree_clean": None},
            )
        )

    def read_plan(self, slug):
        path = self.project / ".mythify" / "plans" / (slug + ".json")
        return json.loads(path.read_text(encoding="utf-8"))

    def write_plan(self, slug, plan):
        path = self.project / ".mythify" / "plans" / (slug + ".json")
        path.write_text(json.dumps(plan), encoding="utf-8")

    def test_step_gate_warns_when_the_world_moved_since_the_run(self):
        self.init_git_repo()
        self.assertEqual(self.run_cli("init").returncode, 0)
        subprocess.run(["git", "add", "-A"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "state"], cwd=self.project, check=True)
        self.run_cli(
            "plan", "create", "g", "--name", "p",
            "--steps", json.dumps([{"title": "a", "verify_command": "true"}]),
        )
        verify = self.run_cli("plan", "verify", "1")
        self.assertEqual(verify.returncode, 0, verify.stderr)
        (self.project / "tracked.txt").write_text("edited after the run\n", encoding="utf-8")
        done = self.run_cli("step", "1", "completed", "verify run exit 0")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("worktree_changed_since_run", done.stderr)

    def test_step_gate_refuses_moved_evidence_under_strict_context(self):
        self.init_git_repo()
        self.assertEqual(self.run_cli("init").returncode, 0)
        subprocess.run(["git", "add", "-A"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "state"], cwd=self.project, check=True)
        self.run_cli(
            "plan", "create", "g", "--name", "p",
            "--steps", json.dumps([{"title": "a", "verify_command": "true"}]),
        )
        plan = self.read_plan("p")
        plan["strict_context"] = True
        self.write_plan("p", plan)
        verify = self.run_cli("plan", "verify", "1")
        self.assertEqual(verify.returncode, 0, verify.stderr)
        (self.project / "tracked.txt").write_text("edited after the run\n", encoding="utf-8")
        refused = self.run_cli("step", "1", "completed", "verify run exit 0")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Stale evidence under strict context", refused.stderr)
        rerun = self.run_cli("plan", "verify", "1")
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        done = self.run_cli("step", "1", "completed", "verify run exit 0")
        self.assertEqual(done.returncode, 0, done.stderr)


class GitReadSnapshotTest(unittest.TestCase):
    """git_read_snapshot runs each distinct git read once and changes no result."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mythify-snapshot-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.root), True)
        for args in (
            ["init", "-q"],
            ["config", "user.email", "mythify@example.invalid"],
            ["config", "user.name", "Mythify Test"],
        ):
            subprocess.run(["git", *args], cwd=str(self.root), check=True)
        (self.root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=str(self.root), check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=str(self.root), check=True)
        (self.root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (self.root / "new.txt").write_text("new\n", encoding="utf-8")
        self.state = self.root / ".mythify"
        self.state.mkdir()

    def sample(self):
        """Provenance, changed paths, provenance again, and the git calls they ran."""
        real_run = subprocess.run
        calls = []

        def counting_run(args, *rest, **kwargs):
            calls.append(tuple(args))
            return real_run(args, *rest, **kwargs)

        with mock.patch.object(mythify_provenance.subprocess, "run", side_effect=counting_run):
            first = mythify_provenance.current_verification_provenance("v", state=self.state)
            paths = mythify_provenance.changed_paths(self.state)
            second = mythify_provenance.current_verification_provenance("v", state=self.state)
        return (first, paths, second), calls

    def test_a_snapshot_runs_each_distinct_read_once_with_the_same_results(self):
        plain, plain_calls = self.sample()
        with mythify_provenance.git_read_snapshot():
            cached, cached_calls = self.sample()
        self.assertEqual(cached, plain)
        self.assertEqual(plain[1], ["new.txt", "tracked.txt"])
        self.assertIsNotNone(plain[0]["worktree_digest"])
        self.assertEqual(sorted(cached_calls), sorted(set(plain_calls)))
        self.assertLess(len(cached_calls), len(plain_calls))

    def test_reads_after_the_snapshot_closes_see_new_writes(self):
        with mythify_provenance.git_read_snapshot():
            before = mythify_provenance.current_verification_provenance("v", state=self.state)
            with mythify_provenance.git_read_snapshot():
                nested = mythify_provenance.current_verification_provenance("v", state=self.state)
        self.assertEqual(nested, before)
        (self.root / "new.txt").write_text("edited\n", encoding="utf-8")
        after = mythify_provenance.current_verification_provenance("v", state=self.state)
        self.assertNotEqual(after["worktree_digest"], before["worktree_digest"])
        self.assertEqual(after["git_commit"], before["git_commit"])


if __name__ == "__main__":
    unittest.main()
