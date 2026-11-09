"""Risk from the changed paths: classify_paths and the high-risk completion check."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "mythify.py"
sys.path.insert(0, str(SCRIPTS_DIR))

import mythify_classification  # noqa: E402
from mythify_classification import classify_paths  # noqa: E402
from mythify_step_gate import configure_step_gate, evaluate_step_evidence  # noqa: E402

HIGH_RISK_STEP_MESSAGE = "High-risk paths changed in this step"


class ClassifyPathsTests(unittest.TestCase):
    def test_high_medium_and_low(self):
        high = classify_paths(["src/auth/login.py", "README.md"])
        self.assertEqual(high["risk"], "high")
        self.assertEqual([m["path"] for m in high["matches"]], ["src/auth/login.py"])
        self.assertEqual(high["matches"][0]["risk"], "high")
        self.assertEqual(high["matches"][0]["glob"], "*auth*")
        medium = classify_paths(["docs/guide.md", "app/settings.py"])
        self.assertEqual(medium["risk"], "medium")
        self.assertEqual(medium["matches"], [{"path": "app/settings.py", "glob": "*settings*", "risk": "medium"}])
        low = classify_paths(["docs/guide.md", "src/render.py"])
        self.assertEqual(low, {"risk": "low", "matches": []})
        self.assertEqual(classify_paths([]), {"risk": "low", "matches": []})

    def test_highest_tier_wins_across_paths_and_within_one_path(self):
        mixed = classify_paths(["app/settings.py", "db/migrations/001_add_users.sql"])
        self.assertEqual(mixed["risk"], "high")
        self.assertEqual([m["risk"] for m in mixed["matches"]], ["medium", "high"])
        # config.token matches a medium and a high glob; the high one is reported.
        self.assertEqual(classify_paths(["config/token.yaml"])["matches"][0]["risk"], "high")

    def test_basename_globs_match_at_any_depth_and_case_is_ignored(self):
        for path in ("Dockerfile", "services/api/Dockerfile", "ci/DOCKERFILE"):
            self.assertEqual(classify_paths([path])["risk"], "high", path)
        for path in ("package.json", "web/package.json", "tools/pyproject.toml"):
            self.assertEqual(classify_paths([path])["risk"], "medium", path)
        self.assertEqual(classify_paths([".github/workflows/ci.yml"])["risk"], "high")
        self.assertEqual(classify_paths(["infra/main.tf"])["risk"], "high")
        self.assertEqual(classify_paths(["./sub/Dockerfile"])["matches"][0]["path"], "sub/Dockerfile")
        self.assertEqual(classify_paths(["a\\b\\payment.py"])["risk"], "high")

    def test_rules_file_carries_the_path_risk_section(self):
        manifest = json.loads(mythify_classification.CLASSIFICATION_RULES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 5)
        self.assertIn("*auth*", manifest["path_risk"]["high"])
        self.assertIn("pyproject.toml", manifest["path_risk"]["medium"])

    def test_loader_rejects_a_missing_or_malformed_path_risk(self):
        manifest = json.loads(mythify_classification.CLASSIFICATION_RULES_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            for broken in (None, {"high": ["*auth*"]}, {"high": [], "medium": ["*x*"]},
                           {"high": ["*auth*"], "medium": [3]}):
                candidate = dict(manifest)
                if broken is None:
                    candidate.pop("path_risk")
                else:
                    candidate["path_risk"] = broken
                path = Path(tmp) / "rules.json"
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with mock.patch.object(mythify_classification, "CLASSIFICATION_RULES_PATH", path):
                    with self.assertRaises(ValueError):
                        mythify_classification.load_classification_rules()


class RiskProject(unittest.TestCase):
    use_git = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.project = base / "project"
        self.project.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        self.state = self.project / ".mythify"
        if self.use_git:
            self.git("init", "-q")
            self.git("config", "user.email", "mythify@example.invalid")
            self.git("config", "user.name", "Mythify Test")
        (self.project / "check.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        (self.project / "README.md").write_text("docs\n", encoding="utf-8")
        self.ok("init")
        if self.use_git:
            self.git("add", "-A")
            self.git("commit", "-qm", "initial")
        self.command = "{0} check.py".format(sys.executable)

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=str(self.project), check=True, capture_output=True, text=True
        ).stdout.strip()

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        for name in ("MYTHIFY_DIR", "MYTHIFY_REQUIRE_VERIFIED_STEP", "MYTHIFY_DISABLE_RUN",
                     "MYTHIFY_RISK_GATE"):
            env.pop(name, None)
        env["HOME"] = str(self.home)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args), cwd=str(self.project), env=env,
            capture_output=True, text=True, timeout=120,
        )

    def ok(self, *args, env_extra=None):
        result = self.run_cli(*args, env_extra=env_extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def plan(self):
        return json.loads((self.state / "plans" / "p.json").read_text(encoding="utf-8"))

    def step(self):
        return self.plan()["steps"][0]

    def start_step(self, strict=False):
        self.ok("plan", "create", "goal", "--name", "p", "--steps",
                json.dumps([{"title": "only step", "verify_command": self.command}]))
        if strict:
            self.ok("campaign", "start", "goal", "--plan", "p")
        self.ok("step", "1", "in_progress", "--plan", "p")

    def write(self, name, text="x = 1\n"):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def verify_and_complete(self, *extra, env_extra=None):
        self.ok("plan", "verify", "1", "--plan", "p")
        return self.run_cli("step", "1", "completed", "checked", "--plan", "p", *extra,
                            env_extra=env_extra)

    def clean_review(self, name="rv"):
        self.ok("review", "round", name, "--clean")


class StepStartStampTests(RiskProject):
    def test_in_progress_stamps_started_commit_and_time(self):
        self.start_step()
        step = self.step()
        self.assertEqual(step["started_commit"], self.git("rev-parse", "HEAD"))
        self.assertRegex(step["started_at"], r"^\d{4}-\d\d-\d\dT")

    def test_a_step_already_in_progress_keeps_its_first_start(self):
        self.start_step()
        first = self.step()
        self.write("README.md", "more docs\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "during the step")
        self.ok("plan", "verify", "1", "--plan", "p")
        self.ok("step", "1", "in_progress", "--plan", "p")
        again = self.step()
        self.assertEqual(again["started_commit"], first["started_commit"])
        self.assertEqual(again["started_at"], first["started_at"])
        self.assertNotEqual(again["started_commit"], self.git("rev-parse", "HEAD"))

    def test_plan_verify_start_stamps_too(self):
        self.ok("plan", "create", "goal", "--name", "p", "--steps",
                json.dumps([{"title": "only step", "verify_command": self.command}]))
        self.ok("plan", "verify", "1", "--plan", "p")
        self.assertEqual(self.step()["started_commit"], self.git("rev-parse", "HEAD"))


class CompletionGateTests(RiskProject):
    def test_default_warns_and_stamps_when_review_and_human_input_are_missing(self):
        self.start_step()
        self.write("auth.py")
        result = self.verify_and_complete()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[WARN] " + HIGH_RISK_STEP_MESSAGE + ": auth.py", result.stderr)
        self.assertIn("a review that covers the current source and --human-input", result.stderr)
        self.assertIn("step 1 completed RESULT --human-input", result.stderr)
        step = self.step()
        self.assertEqual(step["status"], "completed")
        self.assertEqual(step["risk"], {"level": "high", "paths": ["auth.py"],
                                        "review": None, "human_input": False})
        self.assertNotIn("human_input", step)
        self.assertNotIn("risk_gate_waived", step)

    def test_review_and_human_input_pass_without_a_warning(self):
        self.start_step()
        self.write("auth.py")
        self.clean_review()
        result = self.verify_and_complete("--human-input", "Sam: go ahead, I read the diff")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(HIGH_RISK_STEP_MESSAGE, result.stderr)
        step = self.step()
        self.assertEqual(step["human_input"], "Sam: go ahead, I read the diff")
        self.assertEqual(step["risk"]["review"], "rv")
        self.assertTrue(step["risk"]["human_input"])

    def test_blast_radius_review_with_a_verified_merge_gate_covers(self):
        self.start_step()
        self.write("auth.py")
        self.ok("review", "blast-radius", "--status", "pass", "--path", "auth.py",
                "--safety-fact", "login still works", "--merge-command", "test -f auth.py",
                "--name", "blast")
        self.ok("review", "prove", "blast")
        result = self.verify_and_complete("--human-input", "approved")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(HIGH_RISK_STEP_MESSAGE, result.stderr)
        self.assertEqual(self.step()["risk"]["review"], "blast")

    def test_stale_review_does_not_cover(self):
        self.start_step()
        self.write("auth.py")
        self.clean_review()
        self.write("auth.py", "x = 2\n")
        result = self.verify_and_complete("--human-input", "approved")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Missing: a review that covers the current source.", result.stderr)

    def test_strict_context_refuses_each_missing_piece(self):
        self.start_step(strict=True)
        self.write("migrations/001_users.sql", "create table t(x int);\n")
        self.ok("plan", "verify", "1", "--plan", "p")
        for extra, missing in (
            ((), "a review that covers the current source and --human-input"),
            (("--human-input", "approved"), "a review that covers the current source."),
        ):
            result = self.run_cli("step", "1", "completed", "checked", "--plan", "p", *extra)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("[FAIL] " + HIGH_RISK_STEP_MESSAGE, result.stderr)
            self.assertIn("migrations/001_users.sql", result.stderr)
            self.assertIn("Missing: " + missing, result.stderr)
            self.assertEqual(self.step()["status"], "in_progress")
        self.clean_review()
        result = self.run_cli("step", "1", "completed", "checked", "--plan", "p")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Missing: --human-input.", result.stderr)
        self.assertEqual(self.step()["status"], "in_progress")
        done = self.run_cli("step", "1", "completed", "checked", "--plan", "p",
                            "--human-input", "approved by Sam")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.step()["human_input"], "approved by Sam")

    def test_campaign_advance_accepts_human_input(self):
        self.start_step(strict=True)
        self.write(".github/workflows/ci.yml", "name: ci\n")
        self.ok("plan", "verify", "1", "--plan", "p")
        self.clean_review()
        refused = self.run_cli("campaign", "advance", "p", "--result", "checked")
        self.assertEqual(refused.returncode, 1, refused.stderr)
        self.assertIn("Missing: --human-input.", refused.stderr)
        advanced = self.run_cli("campaign", "advance", "p", "--result", "checked",
                                "--human-input", "ship it, said Sam")
        self.assertEqual(advanced.returncode, 0, advanced.stderr)
        self.assertEqual(self.step()["human_input"], "ship it, said Sam")

    def test_risk_gate_zero_waives_even_under_strict_context(self):
        self.start_step(strict=True)
        self.write("password_reset.py")
        result = self.verify_and_complete(env_extra={"MYTHIFY_RISK_GATE": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[WARN] Risk gate waived: MYTHIFY_RISK_GATE=0", result.stderr)
        step = self.step()
        self.assertTrue(step["risk_gate_waived"])
        self.assertEqual(step["risk"]["level"], "high")

    def test_blank_human_input_is_refused(self):
        self.start_step()
        self.ok("plan", "verify", "1", "--plan", "p")
        result = self.run_cli("step", "1", "completed", "checked", "--plan", "p", "--human-input", "  ")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--human-input needs the person's own words", result.stderr)
        self.assertEqual(self.step()["status"], "in_progress")

    def check_quiet_paths(self, name):
        self.start_step(strict=True)
        self.write(name)
        result = self.verify_and_complete()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(HIGH_RISK_STEP_MESSAGE, result.stderr)
        self.assertNotIn("risk", self.step())

    def test_medium_paths_do_not_trigger_the_check(self):
        self.check_quiet_paths("settings.py")

    def test_low_paths_do_not_trigger_the_check(self):
        self.check_quiet_paths("notes.txt")

    def test_human_input_is_stamped_even_when_the_step_is_not_high_risk(self):
        self.start_step()
        self.write("notes.txt")
        result = self.verify_and_complete("--human-input", "fine by me")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.step()["human_input"], "fine by me")
        self.assertNotIn("risk", self.step())

    def test_committed_work_during_the_step_counts_but_earlier_commits_do_not(self):
        self.write("token.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "before the step")
        self.start_step()
        self.write("README.md", "more docs\n")
        quiet = self.verify_and_complete()
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertNotIn(HIGH_RISK_STEP_MESSAGE, quiet.stderr)

    def test_work_committed_after_the_step_started_still_counts(self):
        self.start_step()
        self.write("billing.py")
        self.git("add", "-A")
        self.git("commit", "-qm", "during the step")
        result = self.verify_and_complete()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("billing.py", result.stderr)
        self.assertEqual(self.step()["risk"]["paths"], ["billing.py"])

    def test_plan_show_prints_the_stamped_risk(self):
        self.start_step()
        self.write("auth.py")
        self.clean_review()
        self.verify_and_complete("--human-input", "approved")
        shown = self.ok("plan", "show", "p").stdout
        self.assertIn("risk: high (human input: yes; covering review: rv) paths: auth.py", shown)

    def test_strict_evidence_waiver_still_applies_the_risk_check(self):
        self.start_step(strict=False)
        self.write("auth.py")
        result = self.run_cli("step", "1", "completed", "checked", "--plan", "p",
                              env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Strict gate waived", result.stderr)
        self.assertIn("[WARN] " + HIGH_RISK_STEP_MESSAGE, result.stderr)
        self.assertEqual(self.step()["risk"]["level"], "high")


class NoGitTests(RiskProject):
    use_git = False

    def test_outside_git_the_check_is_skipped_and_start_is_null(self):
        self.start_step(strict=True)
        self.assertIsNone(self.step()["started_commit"])
        self.write("auth.py")
        result = self.verify_and_complete()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(HIGH_RISK_STEP_MESSAGE, result.stderr)
        self.assertNotIn("risk", self.step())

    def test_route_diff_risk_is_null_outside_git(self):
        self.write("auth.py")
        payload = json.loads(self.ok("route", "tidy the wording", "--json").stdout)
        self.assertIsNone(payload["classification"]["diff_risk"])


class RouteDiffRiskTests(RiskProject):
    def route(self, task="tidy the wording in the readme"):
        return json.loads(self.ok("route", task, "--json").stdout)["classification"]

    def test_no_changes_means_null(self):
        self.assertIsNone(self.route()["diff_risk"])

    def test_low_risk_changes_report_low_without_touching_the_review_advisory(self):
        baseline = self.route()
        self.write("notes.txt")
        low = self.route()
        self.assertEqual(low["diff_risk"], {"risk": "low", "matches": []})
        self.assertEqual(low["review"], baseline["review"])

    def test_high_risk_paths_raise_the_review_advisory_but_not_the_task_risk(self):
        baseline = self.route()
        self.assertNotEqual(baseline["risk"], "high")
        self.write("auth/login.py")
        self.write("app/config.yaml")
        classified = self.route()
        self.assertEqual(classified["risk"], baseline["risk"])
        self.assertEqual(classified["diff_risk"]["risk"], "high")
        self.assertEqual(
            sorted(m["path"] for m in classified["diff_risk"]["matches"]),
            ["app/config.yaml", "auth/login.py"])
        self.assertTrue(classified["review"]["independent"])
        self.assertIn("auth/login.py", classified["review"]["reason"])
        text = self.ok("route", "tidy the wording in the readme").stdout
        self.assertIn("Diff risk: high (", text)

    def test_the_state_directory_is_never_a_changed_path(self):
        self.ok("memory", "set", "k", "v")
        self.assertIsNone(self.route()["diff_risk"])


class EvaluatorPurityTests(RiskProject):
    def snapshot(self):
        digest = {}
        for path in sorted(self.state.rglob("*")):
            if path.is_file():
                digest[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return digest

    def test_evaluator_returns_the_risk_verdict_without_printing_or_writing(self):
        self.start_step()
        self.write("auth.py")
        self.ok("plan", "verify", "1", "--plan", "p")
        plan, step = self.plan(), self.step()
        before_plan, before_state = json.dumps(plan, sort_keys=True), self.snapshot()
        from mythify import VERSION
        configure_step_gate(VERSION)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            missing = evaluate_step_evidence(self.state, "p", plan, step)
            given = evaluate_step_evidence(self.state, "p", plan, step, human_input="approved")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
        self.assertEqual(json.dumps(plan, sort_keys=True), before_plan)
        self.assertEqual(self.snapshot(), before_state)
        self.assertNotIn("risk", step)
        self.assertTrue(missing["ok"])
        self.assertEqual(missing["risk"]["level"], "high")
        self.assertEqual(missing["risk"]["paths"], ["auth.py"])
        self.assertIsNone(missing["risk"]["review"])
        self.assertFalse(missing["risk"]["human_input"])
        self.assertTrue(any(HIGH_RISK_STEP_MESSAGE in text for _level, text in missing["messages"]))
        self.assertTrue(given["risk"]["human_input"])

    def test_strict_context_verdict_is_not_ok_without_the_pieces(self):
        self.start_step(strict=True)
        self.write("auth.py")
        self.ok("plan", "verify", "1", "--plan", "p")
        from mythify import VERSION
        configure_step_gate(VERSION)
        verdict = evaluate_step_evidence(self.state, "p", self.plan(), self.step())
        self.assertFalse(verdict["ok"])
        self.assertFalse(verdict["risk"]["ok"])
        self.assertTrue(verdict["strict_context"])


if __name__ == "__main__":
    unittest.main()
