"""Tests for scripts/local_model_eval.py.

These tests do not call real model CLIs. They use the harness' command engine
with a deterministic local Python worker that edits the temporary task project.
"""

import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "scripts" / "local_model_eval.py"

spec = importlib.util.spec_from_file_location("local_model_eval", HARNESS)
local_model_eval = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(local_model_eval)


class LocalModelEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mythify-local-eval-test-"))
        self.home = Path(tempfile.mkdtemp(prefix="mythify-local-eval-home-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.addCleanup(shutil.rmtree, str(self.home), True)

    def write_worker(self):
        worker = self.tmp / "worker.py"
        worker.write_text(
            textwrap.dedent(
                """
                import subprocess
                import sys
                from pathlib import Path

                prompt = sys.stdin.read()
                fast_profile = "fast profile" in prompt.lower()
                root = Path.cwd()
                if (root / "word_count.py").is_file():
                    (root / "word_count.py").write_text(
                        "def count_words(text):\\n"
                        "    if text is None:\\n"
                        "        return 0\\n"
                        "    return len(str(text).split())\\n",
                        encoding="utf-8",
                    )
                if (root / "query_parser.py").is_file():
                    (root / "query_parser.py").write_text(
                        "from urllib.parse import parse_qs\\n"
                        "\\n"
                        "def parse_query(query):\\n"
                        "    parsed = parse_qs(str(query), keep_blank_values=True)\\n"
                        "    result = {}\\n"
                        "    for key, values in parsed.items():\\n"
                        "        result[key] = values[0] if len(values) == 1 else values\\n"
                        "    return result\\n",
                        encoding="utf-8",
                    )
                if (root / "inventory.py").is_file():
                    (root / "inventory.py").write_text(
                        "def total_cost(items):\\n"
                        "    total = 0\\n"
                        "    for item in items:\\n"
                        "        total += float(item['price']) * float(item.get('quantity', 1))\\n"
                        "    return total\\n",
                        encoding="utf-8",
                    )
                if (root / "scripts" / "mythify.py").is_file():
                    if not fast_profile:
                        subprocess.run(
                            [
                                sys.executable,
                                "scripts/mythify.py",
                                "plan",
                                "create",
                                "Fix task",
                                "--steps",
                                '[{"title":"Run unit tests","success_criteria":"python3 -m unittest passes"}]',
                            ],
                            cwd=str(root),
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                    subprocess.run(
                        [
                            sys.executable,
                            "scripts/mythify.py",
                            "verify",
                            "run",
                            "python3 -m unittest",
                            "--claim",
                            "python3 -m unittest passes",
                        ],
                        cwd=str(root),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                print("worker fixed task; mythify=" + str("scripts/mythify.py" in prompt))
                """
            ),
            encoding="utf-8",
        )
        return worker

    def test_command_engine_compares_bare_and_mythify_runs(self):
        worker = self.write_worker()
        report_path = self.tmp / "report.json"
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MYTHIFY_LOCAL_EVAL_COMMAND"] = f'"{sys.executable}" "{worker}"'
        result = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--engine",
                "command",
                "--json-output",
                str(report_path),
                "--require-pass",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["engine"], "command")
        self.assertEqual(report["scenario"], "word_count_bugfix")
        self.assertEqual([run["mode"] for run in report["runs"]], ["bare", "mythify"])
        for run in report["runs"]:
            self.assertEqual(run["model_exit_code"], 0, run)
            self.assertEqual(run["verify_exit_code"], 0, run)
        bare, mythify = report["runs"]
        self.assertEqual(bare["mythify_records"]["verifications"], 0)
        self.assertEqual(mythify["mythify_profile"], "fast")
        self.assertGreaterEqual(mythify["mythify_records"]["verifications"], 1)
        self.assertEqual(mythify["mythify_records"]["plans"], 0)
        effect = report["verified_task_success"]
        self.assertEqual(effect["metric"], "verified_success_rate")
        self.assertEqual(effect["evidence_source"], "per-workspace python3 -m unittest exit code")
        self.assertEqual(effect["conclusion"], "no_change")
        self.assertEqual(effect["statistical_strength"], "local_smoke")

    def test_command_engine_runs_all_scenarios_with_summary(self):
        worker = self.write_worker()
        report_path = self.tmp / "all-report.json"
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MYTHIFY_LOCAL_EVAL_COMMAND"] = f'"{sys.executable}" "{worker}"'
        result = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--engine",
                "command",
                "--scenario",
                "all",
                "--json-output",
                str(report_path),
                "--require-pass",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["scenario"], "all")
        self.assertEqual(report["scenario_count"], 3)
        self.assertEqual(len(report["runs"]), 6)
        self.assertEqual(report["summary"]["bare"]["verified_success"], 3)
        self.assertEqual(report["summary"]["mythify"]["verified_success"], 3)
        self.assertEqual(report["summary"]["mythify"]["evidence_success"], 3)
        self.assertEqual(report["summary"]["winner_by_verified_success_rate"], "tie")
        self.assertEqual(report["verified_task_success"]["paired_task_count"], 3)
        self.assertEqual(report["verified_task_success"]["winner"], "tie")

    def test_verified_task_success_effect_uses_verifier_exit_codes(self):
        runs = [
            {
                "mode": "bare",
                "model_exit_code": 0,
                "verify_exit_code": 1,
                "model_duration_seconds": 1.0,
                "mythify_records": {"verifications": 0, "plans": 0},
            },
            {
                "mode": "mythify",
                "mythify_profile": "fast",
                "model_exit_code": 0,
                "verify_exit_code": 0,
                "model_duration_seconds": 2.0,
                "mythify_records": {"verifications": 1, "plans": 0},
            },
        ]
        summary = local_model_eval.summarize_runs(runs)
        effect = local_model_eval.verified_task_success_effect(summary)

        self.assertEqual(effect["winner"], "mythify")
        self.assertEqual(effect["conclusion"], "improved")
        self.assertEqual(effect["verified_success_rate_delta"], 1.0)
        self.assertEqual(effect["mythify_evidence_success_rate"], 1.0)
        self.assertEqual(effect["avg_model_duration_delta_seconds"], 1.0)

    def test_standard_profile_requires_plan_evidence(self):
        worker = self.write_worker()
        report_path = self.tmp / "standard-report.json"
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MYTHIFY_LOCAL_EVAL_COMMAND"] = f'"{sys.executable}" "{worker}"'
        result = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--engine",
                "command",
                "--mythify-profile",
                "standard",
                "--json-output",
                str(report_path),
                "--require-pass",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        mythify = [run for run in report["runs"] if run["mode"] == "mythify"][0]
        self.assertEqual(mythify["mythify_profile"], "standard")
        self.assertGreaterEqual(mythify["mythify_records"]["verifications"], 1)
        self.assertGreaterEqual(mythify["mythify_records"]["plans"], 1)

    def test_claude_engine_defaults_to_bypass_permissions(self):
        calls = []
        original_resolve_binary = local_model_eval.resolve_binary
        original_run_process = local_model_eval.run_process
        self.addCleanup(setattr, local_model_eval, "resolve_binary", original_resolve_binary)
        self.addCleanup(setattr, local_model_eval, "run_process", original_run_process)

        local_model_eval.resolve_binary = lambda names, env_names: "/tmp/claude"

        def fake_run_process(args, cwd, prompt, timeout, env=None, shell=False):
            calls.append(args)
            return {
                "exit_code": 0,
                "stdout_tail": '{"result":"ok"}',
                "stderr_tail": "",
                "timed_out": False,
                "duration_seconds": 0.1,
            }

        local_model_eval.run_process = fake_run_process

        local_model_eval.run_claude(self.tmp, "fix it", "", 10)

        self.assertEqual(calls[0][calls[0].index("--permission-mode") + 1], "bypassPermissions")

    def test_claude_permission_mode_can_be_overridden(self):
        calls = []
        original_resolve_binary = local_model_eval.resolve_binary
        original_run_process = local_model_eval.run_process
        previous = os.environ.get("MYTHIFY_LOCAL_EVAL_CLAUDE_PERMISSION_MODE")
        self.addCleanup(setattr, local_model_eval, "resolve_binary", original_resolve_binary)
        self.addCleanup(setattr, local_model_eval, "run_process", original_run_process)
        if previous is None:
            self.addCleanup(os.environ.pop, "MYTHIFY_LOCAL_EVAL_CLAUDE_PERMISSION_MODE", None)
        else:
            self.addCleanup(os.environ.__setitem__, "MYTHIFY_LOCAL_EVAL_CLAUDE_PERMISSION_MODE", previous)

        local_model_eval.resolve_binary = lambda names, env_names: "/tmp/claude"
        local_model_eval.run_process = lambda args, cwd, prompt, timeout, env=None, shell=False: calls.append(args) or {
            "exit_code": 0,
            "stdout_tail": '{"result":"ok"}',
            "stderr_tail": "",
            "timed_out": False,
            "duration_seconds": 0.1,
        }
        os.environ["MYTHIFY_LOCAL_EVAL_CLAUDE_PERMISSION_MODE"] = "dontAsk"

        local_model_eval.run_claude(self.tmp, "fix it", "", 10)

        self.assertEqual(calls[0][calls[0].index("--permission-mode") + 1], "dontAsk")

    def test_codex_fast_speed_adds_config_overrides(self):
        calls = []
        original_resolve_binary = local_model_eval.resolve_binary
        original_run_process = local_model_eval.run_process
        self.addCleanup(setattr, local_model_eval, "resolve_binary", original_resolve_binary)
        self.addCleanup(setattr, local_model_eval, "run_process", original_run_process)

        local_model_eval.resolve_binary = lambda names, env_names: "/tmp/codex"

        def fake_run_process(args, cwd, prompt, timeout, env=None, shell=False):
            calls.append(args)
            return {
                "exit_code": 0,
                "stdout_tail": "ok",
                "stderr_tail": "",
                "timed_out": False,
                "duration_seconds": 0.1,
            }

        local_model_eval.run_process = fake_run_process

        local_model_eval.run_codex(self.tmp, "fix it", "gpt-5.5", 10, "fast")

        self.assertIn("-c", calls[0])
        self.assertIn('service_tier="fast"', calls[0])
        self.assertIn("features.fast_mode=true", calls[0])


if __name__ == "__main__":
    unittest.main()
