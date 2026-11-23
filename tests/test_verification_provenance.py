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

import mythify_step_gate  # noqa: E402
from mythify import VERSION  # noqa: E402
from mythify_provenance import evidence_moved_since_run  # noqa: E402
from mythify_step_gate import configure_step_gate, evaluate_step_evidence  # noqa: E402


class VerificationProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="mythify-provenance-proj-"))
        self.home = Path(tempfile.mkdtemp(prefix="mythify-provenance-home-"))
        self.addCleanup(shutil.rmtree, str(self.project), True)
        self.addCleanup(shutil.rmtree, str(self.home), True)

    def fixture_env(self):
        """The environment of every CLI call, fixture git call, and in-process judgment."""
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        return env

    def run_cli(self, *args):
        return warm_cli.run(list(args), cwd=str(self.project), env=self.fixture_env(), timeout=120)

    def git(self, *args):
        """git in the project with the fixture environment, bounded, stdout stripped."""
        return subprocess.run(
            ["git", *args], cwd=self.project, env=self.fixture_env(), check=True,
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()

    def init_git_repo(self):
        self.git("init", "-q")
        self.git("config", "user.email", "mythify@example.invalid")
        self.git("config", "user.name", "Mythify Test")
        (self.project / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-qm", "initial")
        return self.git("rev-parse", "HEAD")

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

    def strict_step_with_passing_evidence(self):
        """A strict step whose verifier passed on a clean tree, ready to be judged."""
        self.init_git_repo()
        self.assertEqual(self.run_cli("init").returncode, 0)
        self.git("add", "-A")
        self.git("commit", "-qm", "state")
        self.run_cli(
            "plan", "create", "g", "--name", "p",
            "--steps", json.dumps([{"title": "a", "verify_command": "true"}]),
        )
        plan = self.read_plan("p")
        plan["strict_context"] = True
        self.write_plan("p", plan)
        verify = self.run_cli("plan", "verify", "1")
        self.assertEqual(verify.returncode, 0, verify.stderr)

    def judge(self, provenance):
        """evaluate_step_evidence for step 1, with the provenance sample done by PROVENANCE."""
        plan = self.read_plan("p")
        configure_step_gate(VERSION)
        # The same environment and physical project path as the CLI call that
        # recorded the stamp, so the two digests cover the same ignore files.
        env = self.fixture_env()
        for name in ("MYTHIFY_RISK_GATE", "MYTHIFY_REQUIRE_VERIFIED_STEP"):
            env.pop(name, None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            mythify_step_gate, "current_verification_provenance", side_effect=provenance
        ):
            return evaluate_step_evidence(
                self.project.resolve() / ".mythify", "p", plan, plan["steps"][0])

    def assert_high_risk_path_refused(self, verdict):
        self.assertFalse(verdict["ok"])
        self.assertFalse(verdict["risk"]["ok"])
        self.assertEqual(verdict["risk"]["level"], "high")
        self.assertEqual(verdict["risk"]["paths"], ["auth.py"])
        self.assertIsNone(verdict["risk"]["review"])

    def test_the_path_risk_check_looks_at_the_tree_after_the_provenance_sample(self):
        self.strict_step_with_passing_evidence()
        sampled = mythify_step_gate.current_verification_provenance

        def sample_then_add_a_high_risk_file(*args, **kwargs):
            before = sampled(*args, **kwargs)
            (self.project / "auth.py").write_text("x = 1\n", encoding="utf-8")
            return before

        samples = []

        def record_sample(*args, **kwargs):
            # check-tests: allow test_a_snapshot_runs_each_distinct_read_once_with_the_same_results covered the retired cache
            samples.append(sample_then_add_a_high_risk_file(*args, **kwargs))
            return samples[-1]

        verdict = self.judge(record_sample)
        fields = ("git_commit", "worktree_clean", "worktree_digest", "mythify_version")
        stamp = self.latest_record()["provenance"]
        self.assertEqual(
            {name: samples[0][name] for name in fields}, {name: stamp[name] for name in fields},
            "the in-process sample differs from the recorded stamp, so the two runs did not see "
            "the same environment or tree")
        self.assertIsNone(verdict["moved"])  # the sample saw the tree before the file existed
        self.assert_high_risk_path_refused(verdict)

    # check-tests: allow test_reads_after_the_snapshot_closes_see_new_writes now has gate-level freshness coverage
    def test_a_failed_provenance_sample_does_not_decide_the_path_risk_check(self):
        self.strict_step_with_passing_evidence()
        (self.project / "auth.py").write_text("x = 1\n", encoding="utf-8")
        real_run, listings = subprocess.run, []

        def run(argv, *args, **kwargs):
            # The first untracked listing is the provenance digest's, and it times out once.
            if "ls-files" in argv and "--others" in argv and "--ignored" not in argv:
                listings.append(argv)
                if len(listings) == 1:
                    raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
            return real_run(argv, *args, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=run):
            verdict = self.judge(mythify_step_gate.current_verification_provenance)
        self.assertEqual(len(listings), 2)  # the path-risk listing ran again, for real
        self.assertEqual(listings[0], listings[1])
        self.assertEqual(verdict["moved"], "worktree_changed_since_run")  # the digest read failed, the clean flag did not
        self.assert_high_risk_path_refused(verdict)


if __name__ == "__main__":
    unittest.main()
