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
from unittest import mock

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

    def write_sanitization_input(self, path):
        raw = {
            "engine": "codex-cli",
            "scenario": "word_count_bugfix",
            "repeat": 1,
            "mythify_profile": "auto",
            "runs": [
                {
                    "scenario": "word_count_bugfix",
                    "iteration": 1,
                    "mode": mode,
                    "mythify_profile": "fast" if mode == "mythify" else "",
                    "speed": "auto",
                    "model_exit_code": 0,
                    "model_duration_seconds": 1.0,
                    "verify_exit_code": 0,
                    "mythify_records": {
                        "verifications": 1 if mode == "mythify" else 0,
                        "plans": 0,
                    },
                }
                for mode in ("bare", "mythify")
            ],
        }
        raw_bytes = json.dumps(raw, sort_keys=True).encode("utf-8")
        path.write_bytes(raw_bytes)
        return raw_bytes

    def assert_sanitize_identity_refused(self, input_path, output_path, raw_path, raw_bytes, cwd=None):
        result = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--sanitize-existing-report",
                str(input_path),
                "--summary-output",
                str(output_path),
            ],
            cwd=str(cwd or REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined_output = result.stderr + result.stdout

        with self.subTest(requirement="nonzero exit"):
            self.assertNotEqual(
                result.returncode,
                0,
                "sanitizer returned exit code {0}".format(result.returncode),
            )
        with self.subTest(requirement="raw input preserved"):
            actual_bytes = raw_path.read_bytes()
            self.assertEqual(
                actual_bytes,
                raw_bytes,
                "raw input changed from {0} bytes to {1} bytes".format(
                    len(raw_bytes), len(actual_bytes)
                ),
            )
        with self.subTest(requirement="clear identity error"):
            expected_error = "input and output must refer to different files"
            self.assertTrue(
                expected_error in combined_output.lower(),
                "missing identity error; exit code was {0}".format(result.returncode),
            )

    def sanitize_cost_metadata(self):
        return local_model_eval.build_cost_metadata(
            "subscription_included_authentication",
            "not_measured",
            None,
            "not_measured",
        )

    def assert_sanitize_write_failure_preserves_files(self, operation, message):
        raw_path = self.tmp / "raw-report.json"
        raw_bytes = self.write_sanitization_input(raw_path)
        output_path = self.tmp / "summary.json"
        prior_output = b"prior published summary\n"
        output_path.write_bytes(prior_output)

        with mock.patch.object(local_model_eval.os, operation, side_effect=OSError(message)):
            with self.assertRaisesRegex(OSError, message):
                local_model_eval.sanitize_existing_report(
                    raw_path,
                    output_path,
                    self.sanitize_cost_metadata(),
                )

        self.assertEqual(raw_path.read_bytes(), raw_bytes)
        self.assertEqual(output_path.read_bytes(), prior_output)
        self.assertEqual(list(self.tmp.glob(".mythify-sanitized-*.tmp")), [])

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
                "--billing-posture",
                "subscription_included_authentication",
                "--monetary-cost-status",
                "not_measured",
                "--subscription-quota-status",
                "not_measured",
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
        self.assertEqual(
            report["cost_metadata"]["billing_posture"],
            "subscription_included_authentication",
        )
        self.assertIsNone(report["cost_metadata"]["monetary_cost"]["value_dollars"])
        self.assertEqual(report["scenario"], "word_count_bugfix")
        self.assertEqual([run["mode"] for run in report["runs"]], ["bare", "mythify"])
        for run in report["runs"]:
            self.assertEqual(run["model_exit_code"], 0, run)
            self.assertEqual(run["verify_exit_code"], 0, run)
        bare, mythify = report["runs"]
        self.assertEqual(bare["mythify_records"]["verifications"], 0)
        self.assertEqual(mythify["mythify_profile"], "fast")
        self.assertGreaterEqual(mythify["mythify_records"]["verifications"], 1)
        self.assertGreaterEqual(
            mythify["mythify_records"]["passing_expected_verifications"],
            1,
        )
        self.assertEqual(mythify["mythify_records"]["plans"], 0)
        effect = report["verified_task_success"]
        self.assertEqual(effect["metric"], "verified_success_rate")
        self.assertEqual(effect["evidence_source"], "per-workspace python3 -m unittest exit code")
        self.assertEqual(effect["conclusion"], "no_change")
        self.assertEqual(effect["statistical_strength"], "local_smoke")
        false_claims = report["false_completion_claims"]
        self.assertEqual(false_claims["metric"], "false_completion_rate")
        self.assertEqual(false_claims["completion_signal"], "model_exit_code_0")
        self.assertEqual(false_claims["conclusion"], "no_change")
        self.assertEqual(false_claims["modes"]["bare"]["false_completion_claims"], 0)
        self.assertEqual(false_claims["modes"]["mythify"]["false_completion_claims"], 0)
        overhead = report["profile_overhead"]
        self.assertEqual(overhead["metric"], "avg_model_duration_seconds")
        self.assertEqual(overhead["evidence_source"], "measured model process duration_seconds from local harness subprocess runs")
        self.assertEqual(overhead["bare_speed"], "auto")
        self.assertEqual(overhead["mythify_speed"], "auto")
        self.assertIn("fast", overhead["profiles"])
        benefit = report["local_model_benefit"]
        self.assertEqual(benefit["metric"], "local_model_candidate_task_categories")
        self.assertEqual(benefit["supported_roles"], ["reader", "triage"])
        self.assertIn("string_processing_bugfix", benefit["candidate_categories"])
        self.assertEqual(benefit["scenarios"][0]["local_model_candidate_roles"], ["reader", "triage"])
        self.assertEqual(benefit["scenarios"][0]["observed_benefit"], "neutral")
        fanout = report["fanout_value"]
        self.assertEqual(fanout["metric"], "fanout_value_fit")
        self.assertEqual(fanout["conclusion"], "built_in_scenarios_do_not_justify_fanout")
        self.assertIn("independent_surface_mapping", fanout["helps_when"])
        self.assertIn("single_focused_bugfix", fanout["wastes_when"])
        self.assertEqual(fanout["observed_waste_candidate_count"], 1)
        self.assertEqual(fanout["single_worker_sufficient_count"], 1)
        self.assertEqual(fanout["scenarios"][0]["fanout_fit"], "waste_candidate")
        self.assertTrue(fanout["scenarios"][0]["single_worker_sufficient"])
        strength = report["role_strength"]
        self.assertEqual(strength["metric"], "stronger_model_role_requirement")
        self.assertEqual(strength["required_stronger_roles"], [])
        self.assertEqual(strength["scoped_stronger_opt_in_roles"], ["reviewer"])
        self.assertIn("fanout_worker", strength["broad_stronger_opt_in_roles"])
        self.assertEqual(strength["conclusion"], "no_role_requires_stronger_by_default")

    def test_mythify_evidence_requires_passing_executed_expected_command(self):
        workspace = self.tmp / "evidence-workspace"
        state = workspace / ".mythify"
        state.mkdir(parents=True)
        records = [
            {
                "kind": "attested",
                "verified": False,
                "exit_code": None,
                "command": "python3 -m unittest",
            },
            {
                "kind": "executed",
                "verified": False,
                "exit_code": 1,
                "command": "python3 -m unittest",
            },
            {
                "kind": "executed",
                "verified": True,
                "exit_code": 0,
                "command": "python3 -m compileall .",
            },
        ]
        (state / "verifications.jsonl").write_text(
            "\n".join(json.dumps(record) for record in records) + "\nnot-json\n",
            encoding="utf-8",
        )

        inspected = local_model_eval.count_mythify_records(
            workspace,
            "python3 -m unittest",
        )
        self.assertEqual(inspected["verifications"], 4)
        self.assertEqual(inspected["passing_expected_verifications"], 0)
        self.assertFalse(
            local_model_eval.mythify_evidence_ok(
                {
                    "mode": "mythify",
                    "mythify_profile": "fast",
                    "mythify_records": inspected,
                }
            )
        )

        with (state / "verifications.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "kind": "executed",
                        "verified": True,
                        "exit_code": 0,
                        "command": "python3 -m unittest",
                    }
                )
                + "\n"
            )
        inspected = local_model_eval.count_mythify_records(
            workspace,
            "python3 -m unittest",
        )
        self.assertEqual(inspected["passing_expected_verifications"], 1)
        self.assertTrue(
            local_model_eval.mythify_evidence_ok(
                {
                    "mode": "mythify",
                    "mythify_profile": "fast",
                    "mythify_records": inspected,
                }
            )
        )

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
        self.assertEqual(report["false_completion_claims"]["winner_by_lower_false_completion_rate"], "tie")
        self.assertEqual(report["profile_overhead"]["profiles"]["fast"]["attempted"], 3)
        self.assertEqual(report["local_model_benefit"]["scenario_count"], 3)
        self.assertEqual(report["fanout_value"]["scenario_count"], 3)
        self.assertEqual(report["fanout_value"]["observed_waste_candidate_count"], 3)
        self.assertEqual(report["fanout_value"]["single_worker_sufficient_count"], 3)
        self.assertEqual(
            sorted(report["local_model_benefit"]["candidate_categories"]),
            ["numeric_data_bugfix", "standard_library_bugfix", "string_processing_bugfix"],
        )
        self.assertEqual(report["role_strength"]["observed_harness"]["mythify_attempted"], 3)
        self.assertEqual(report["role_strength"]["observed_harness"]["observed_profiles"], ["fast"])

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
                "mythify_records": {
                    "verifications": 1,
                    "passing_expected_verifications": 1,
                    "plans": 0,
                },
            },
        ]
        summary = local_model_eval.summarize_runs(runs)
        effect = local_model_eval.verified_task_success_effect(summary)

        self.assertEqual(effect["winner"], "mythify")
        self.assertEqual(effect["conclusion"], "improved")
        self.assertEqual(effect["verified_success_rate_delta"], 1.0)
        self.assertEqual(effect["mythify_evidence_success_rate"], 1.0)
        self.assertEqual(effect["avg_model_duration_delta_seconds"], 1.0)

    def test_false_completion_claims_effect_uses_verifier_exit_codes(self):
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
                "mythify_records": {
                    "verifications": 1,
                    "passing_expected_verifications": 1,
                    "plans": 0,
                },
            },
        ]
        effect = local_model_eval.false_completion_claims_effect(runs)

        self.assertEqual(effect["winner_by_lower_false_completion_rate"], "mythify")
        self.assertEqual(effect["conclusion"], "improved")
        self.assertEqual(effect["bare_false_completion_rate"], 1.0)
        self.assertEqual(effect["mythify_false_completion_rate"], 0.0)
        self.assertEqual(effect["false_completion_rate_delta"], -1.0)
        self.assertEqual(effect["modes"]["bare"]["false_completion_claims"], 1)
        self.assertEqual(effect["modes"]["mythify"]["verifier_backed_claims"], 1)

    def test_profile_overhead_effect_uses_measured_durations(self):
        runs = [
            {
                "mode": "bare",
                "model_exit_code": 0,
                "verify_exit_code": 0,
                "model_duration_seconds": 1.0,
                "mythify_records": {"verifications": 0, "plans": 0},
            },
            {
                "mode": "mythify",
                "mythify_profile": "fast",
                "model_exit_code": 0,
                "verify_exit_code": 0,
                "model_duration_seconds": 2.5,
                "mythify_records": {"verifications": 1, "plans": 0},
            },
        ]
        summary = local_model_eval.summarize_runs(runs)
        effect = local_model_eval.profile_overhead_effect(summary, runs)

        self.assertEqual(effect["inference"], "inconclusive")
        self.assertEqual(effect["conclusion"], "no_claim")
        self.assertEqual(effect["observed_lower_duration_mode"], "bare")
        self.assertNotIn("winner_by_lower_avg_duration", effect)
        self.assertEqual(effect["avg_model_duration_delta_seconds"], 1.5)
        self.assertEqual(effect["avg_model_duration_ratio"], 2.5)
        self.assertEqual(effect["profiles"]["fast"]["delta_vs_bare_avg_seconds"], 1.5)
        self.assertEqual(effect["profiles"]["fast"]["ratio_vs_bare_avg"], 2.5)

    def test_cost_metadata_is_explicit_without_inventing_cost(self):
        metadata = local_model_eval.build_cost_metadata(
            "subscription_included_authentication",
            "not_measured",
            None,
            "not_measured",
        )

        self.assertEqual(
            metadata["billing_posture"],
            "subscription_included_authentication",
        )
        self.assertEqual(metadata["monetary_cost"]["measurement_status"], "not_measured")
        self.assertIsNone(metadata["monetary_cost"]["value_dollars"])
        self.assertEqual(metadata["subscription_quota"]["measurement_status"], "not_measured")

        with self.assertRaises(ValueError):
            local_model_eval.build_cost_metadata("unknown", "measured", None, "unknown")

    def test_cli_rejects_measured_cost_without_value(self):
        result = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--engine",
                "command",
                "--monetary-cost-status",
                "measured",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("measured monetary cost requires a dollar value", result.stderr)

    def test_cli_sanitizes_existing_report_with_explicit_cost_metadata(self):
        raw_path = self.tmp / "raw-report.json"
        summary_path = self.tmp / "sanitized-summary.json"
        raw = {
            "engine": "codex-cli",
            "scenario": "word_count_bugfix",
            "scenario_count": 1,
            "repeat": 2,
            "mythify_profile": "auto",
            "summary": {},
            "verified_task_success": {},
            "false_completion_claims": {},
            "profile_overhead": {
                "avg_model_duration_delta_seconds": -1.0,
                "winner_by_lower_avg_duration": "mythify",
                "conclusion": "faster",
            },
            "local_model_benefit": {},
            "fanout_value": {},
            "role_strength": {},
            "runs": [
                {
                    "scenario": "word_count_bugfix",
                    "iteration": iteration,
                    "mode": mode,
                    "mythify_profile": "fast" if mode == "mythify" else "",
                    "speed": "auto",
                    "model_exit_code": 0,
                    "model_duration_seconds": 1.0,
                    "verify_exit_code": 0,
                    "mythify_records": {
                        "verifications": 1 if mode == "mythify" else 0,
                        "plans": 0,
                    },
                }
                for iteration in (1, 2)
                for mode in ("bare", "mythify")
            ],
        }
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(HARNESS),
                "--sanitize-existing-report",
                str(raw_path),
                "--summary-output",
                str(summary_path),
                "--billing-posture",
                "subscription_included_authentication",
                "--monetary-cost-status",
                "not_measured",
                "--subscription-quota-status",
                "not_measured",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        cost = local_model_eval.build_cost_metadata(
            "subscription_included_authentication", "not_measured", None, "not_measured"
        )
        expected = local_model_eval.build_sanitized_summary({**raw, "cost_metadata": cost})
        expected_bytes = (json.dumps(expected, indent=2) + "\n").encode("utf-8")
        self.assertEqual(summary_path.read_bytes(), expected_bytes)

    def test_cli_refuses_direct_same_path_when_sanitizing_existing_report(self):
        raw_path = self.tmp / "raw-report.json"
        raw_bytes = self.write_sanitization_input(raw_path)

        self.assert_sanitize_identity_refused(
            raw_path,
            raw_path,
            raw_path,
            raw_bytes,
        )

    def test_cli_refuses_relative_alias_when_sanitizing_existing_report(self):
        raw_path = self.tmp / "raw-report.json"
        raw_bytes = self.write_sanitization_input(raw_path)
        (self.tmp / "alias-parent").mkdir()

        self.assert_sanitize_identity_refused(
            "raw-report.json",
            "alias-parent/../raw-report.json",
            raw_path,
            raw_bytes,
            cwd=self.tmp,
        )

    def test_cli_refuses_symlink_alias_when_sanitizing_existing_report(self):
        raw_path = self.tmp / "raw-report.json"
        raw_bytes = self.write_sanitization_input(raw_path)
        output_path = self.tmp / "summary-alias.json"
        output_path.symlink_to(raw_path)

        self.assert_sanitize_identity_refused(
            raw_path,
            output_path,
            raw_path,
            raw_bytes,
        )

    def test_cli_refuses_hardlink_alias_when_sanitizing_existing_report(self):
        raw_path = self.tmp / "raw-report.json"
        raw_bytes = self.write_sanitization_input(raw_path)
        output_path = self.tmp / "summary-alias.json"
        os.link(raw_path, output_path)

        self.assert_sanitize_identity_refused(
            raw_path,
            output_path,
            raw_path,
            raw_bytes,
        )

    def test_sanitized_atomic_write_orders_file_and_directory_durability(self):
        raw_path = self.tmp / "raw-report.json"
        self.write_sanitization_input(raw_path)
        output_path = self.tmp / "summary.json"
        events = []
        directory_fds = set()
        real_fsync = local_model_eval.os.fsync
        real_chmod = local_model_eval.os.chmod
        real_replace = local_model_eval.os.replace
        real_open = local_model_eval.os.open
        real_close = local_model_eval.os.close

        def wrapped_fsync(descriptor):
            events.append("parent_fsync" if descriptor in directory_fds else "file_fsync")
            return real_fsync(descriptor)

        def wrapped_chmod(path, mode):
            events.append("chmod")
            return real_chmod(path, mode)

        def wrapped_replace(source, target):
            events.append("replace")
            return real_replace(source, target)

        def wrapped_open(path, flags, *args, **kwargs):
            descriptor = real_open(path, flags, *args, **kwargs)
            if Path(path) == self.tmp.resolve():
                directory_fds.add(descriptor)
                events.append("parent_open")
            return descriptor

        def wrapped_close(descriptor):
            if descriptor in directory_fds:
                events.append("parent_close")
            return real_close(descriptor)

        with mock.patch.object(local_model_eval.os, "fsync", side_effect=wrapped_fsync), \
             mock.patch.object(local_model_eval.os, "chmod", side_effect=wrapped_chmod), \
             mock.patch.object(local_model_eval.os, "replace", side_effect=wrapped_replace), \
             mock.patch.object(local_model_eval.os, "open", side_effect=wrapped_open), \
             mock.patch.object(local_model_eval.os, "close", side_effect=wrapped_close):
            local_model_eval.sanitize_existing_report(
                raw_path,
                output_path,
                self.sanitize_cost_metadata(),
            )

        self.assertEqual(
            events,
            [
                "file_fsync",
                "chmod",
                "replace",
                "parent_open",
                "parent_fsync",
                "parent_close",
            ],
        )

    def test_sanitized_atomic_write_preserves_prior_output_on_file_fsync_failure(self):
        self.assert_sanitize_write_failure_preserves_files("fsync", "file fsync failed")

    def test_sanitized_atomic_write_preserves_prior_output_on_chmod_failure(self):
        self.assert_sanitize_write_failure_preserves_files("chmod", "chmod failed")

    def test_sanitized_atomic_write_preserves_prior_output_on_replace_failure(self):
        self.assert_sanitize_write_failure_preserves_files("replace", "replace failed")

    def test_sanitized_atomic_write_does_not_mask_replace_failure_with_cleanup_failure(self):
        raw_path = self.tmp / "raw-report.json"
        raw_bytes = self.write_sanitization_input(raw_path)
        output_path = self.tmp / "summary.json"
        prior_output = b"prior published summary\n"
        output_path.write_bytes(prior_output)

        with mock.patch.object(
            local_model_eval.os,
            "replace",
            side_effect=OSError("replace failed"),
        ), mock.patch.object(Path, "unlink", side_effect=OSError("cleanup failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                local_model_eval.sanitize_existing_report(
                    raw_path,
                    output_path,
                    self.sanitize_cost_metadata(),
                )

        self.assertEqual(raw_path.read_bytes(), raw_bytes)
        self.assertEqual(output_path.read_bytes(), prior_output)
        for temporary_path in self.tmp.glob(".mythify-sanitized-*.tmp"):
            temporary_path.unlink()

    def test_parent_directory_fsync_failure_is_best_effort_after_publication(self):
        raw_path = self.tmp / "raw-report.json"
        raw_bytes = self.write_sanitization_input(raw_path)
        output_path = self.tmp / "summary.json"

        with mock.patch.object(
            local_model_eval.os,
            "fsync",
            side_effect=(None, OSError("directory fsync failed")),
        ) as fsync:
            summary = local_model_eval.sanitize_existing_report(
                raw_path,
                output_path,
                self.sanitize_cost_metadata(),
            )

        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(raw_path.read_bytes(), raw_bytes)
        self.assertEqual(
            output_path.read_bytes(),
            (json.dumps(summary, indent=2) + "\n").encode("utf-8"),
        )
        self.assertEqual(list(self.tmp.glob(".mythify-sanitized-*.tmp")), [])

    def test_local_model_benefit_effect_uses_scenarios_and_verifiers(self):
        runs = [
            {
                "scenario": "word_count_bugfix",
                "mode": "bare",
                "model_exit_code": 0,
                "verify_exit_code": 1,
                "model_duration_seconds": 1.0,
                "mythify_records": {"verifications": 0, "plans": 0},
            },
            {
                "scenario": "word_count_bugfix",
                "mode": "mythify",
                "mythify_profile": "fast",
                "model_exit_code": 0,
                "verify_exit_code": 0,
                "model_duration_seconds": 2.0,
                "mythify_records": {"verifications": 1, "plans": 0},
            },
        ]
        effect = local_model_eval.local_model_benefit_effect(runs, ["word_count_bugfix"])

        self.assertEqual(effect["candidate_categories"], ["string_processing_bugfix"])
        self.assertEqual(effect["scenarios"][0]["local_model_candidate_roles"], ["reader", "triage"])
        self.assertEqual(effect["scenarios"][0]["observed_benefit"], "positive")
        self.assertEqual(effect["scenarios"][0]["verified_success_rate_delta"], 1.0)
        self.assertEqual(effect["categories"][0]["mythify_verified_success_rate"], 1.0)
        self.assertIn("provider-specific benefit requires rerunning", effect["caveat"])

    def test_fanout_value_effect_uses_policy_and_verifier_outcomes(self):
        runs = [
            {
                "scenario": "word_count_bugfix",
                "mode": "bare",
                "model_exit_code": 0,
                "verify_exit_code": 1,
                "model_duration_seconds": 1.0,
                "mythify_records": {"verifications": 0, "plans": 0},
            },
            {
                "scenario": "word_count_bugfix",
                "mode": "mythify",
                "mythify_profile": "fast",
                "model_exit_code": 0,
                "verify_exit_code": 0,
                "model_duration_seconds": 2.0,
                "mythify_records": {
                    "verifications": 1,
                    "passing_expected_verifications": 1,
                    "plans": 0,
                },
            },
        ]
        summary = local_model_eval.summarize_runs(runs)
        effect = local_model_eval.fanout_value_effect(summary, runs, ["word_count_bugfix"])

        self.assertEqual(effect["metric"], "fanout_value_fit")
        self.assertEqual(effect["comparison"], "fanout_policy_plus_harness_outcomes")
        self.assertTrue(effect["requires_independent_tasks"])
        self.assertIn("parallel_research_or_comparison", effect["helps_when"])
        self.assertIn("dependent_sequence", effect["wastes_when"])
        self.assertEqual(effect["observed_waste_candidate_count"], 1)
        self.assertEqual(effect["observed_help_candidate_count"], 0)
        self.assertEqual(effect["single_worker_sufficient_count"], 1)
        self.assertEqual(effect["conclusion"], "built_in_scenarios_do_not_justify_fanout")
        self.assertEqual(effect["scenarios"][0]["observed_value_signal"], "single_worker_sufficient")
        self.assertIn("merged artifact", effect["caveat"])

    def test_role_strength_effect_uses_policy_and_verifier_outcomes(self):
        runs = [
            {
                "scenario": "word_count_bugfix",
                "mode": "bare",
                "model_exit_code": 0,
                "verify_exit_code": 1,
                "model_duration_seconds": 1.0,
                "mythify_records": {"verifications": 0, "plans": 0},
            },
            {
                "scenario": "word_count_bugfix",
                "mode": "mythify",
                "mythify_profile": "fast",
                "model_exit_code": 0,
                "verify_exit_code": 0,
                "model_duration_seconds": 2.0,
                "mythify_records": {"verifications": 1, "plans": 0},
            },
        ]
        summary = local_model_eval.summarize_runs(runs)
        effect = local_model_eval.role_strength_effect(summary, runs)

        self.assertEqual(effect["required_stronger_roles"], [])
        self.assertEqual(effect["scoped_stronger_opt_in_roles"], ["reviewer"])
        self.assertEqual(effect["default_spawn_ceiling"], "same_or_lower")
        by_role = {row["role"]: row for row in effect["roles"]}
        self.assertEqual(
            by_role["reviewer"]["stronger_model_requirement"],
            "conditional_not_default",
        )
        self.assertEqual(by_role["verifier"]["stronger_model_allowed"], "no")
        self.assertEqual(effect["observed_harness"]["mythify_verified_success_rate"], 1.0)

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

    def test_bare_condition_has_no_mythify_steering(self):
        prompt = local_model_eval.prompt_for("bare", "word_count_bugfix")
        conditions = local_model_eval.comparison_conditions()

        self.assertNotIn("Mythify", prompt)
        self.assertNotIn("AGENTS.md", prompt)
        self.assertNotIn("scripts/mythify.py", prompt)
        self.assertEqual(conditions["bare"]["steering"], "task_prompt_only")
        self.assertFalse(conditions["bare"]["mythify_installed"])
        self.assertEqual(conditions["mythify"]["steering"], "mythify_protocol")
        self.assertTrue(conditions["mythify"]["mythify_installed"])

    def test_sanitized_summary_omits_workspaces_output_and_secrets(self):
        secret = "SECRET-SHOULD-NOT-BE-PUBLISHED"
        report = {
            "engine": "codex-cli",
            "scenario": "word_count_bugfix",
            "scenario_count": 1,
            "repeat": 2,
            "mythify_profile": "fast",
            "bare_speed": "auto",
            "mythify_speed": "auto",
            "workspaces_root": "/private/tmp/raw-workspaces",
            "summary": {"winner_by_verified_success_rate": "tie"},
            "verified_task_success": {"conclusion": "no_change"},
            "false_completion_claims": {"conclusion": "no_change"},
            "profile_overhead": {"conclusion": "overhead"},
            "local_model_benefit": {"conclusion": "insufficient_evidence"},
            "fanout_value": {"conclusion": "insufficient_evidence"},
            "role_strength": {"conclusion": "no_role_requires_stronger_by_default"},
            "cost_metadata": {
                "billing_posture": "subscription_included_authentication",
                "monetary_cost": {
                    "measurement_status": "not_measured",
                    "currency": "USD",
                    "value_dollars": None,
                },
                "subscription_quota": {"measurement_status": "not_measured"},
            },
            "runs": [
                {
                    "scenario": "word_count_bugfix",
                    "task_category": "string_processing_bugfix",
                    "iteration": 1,
                    "mode": "bare",
                    "mythify_profile": "",
                    "speed": "auto",
                    "workspace": "/private/tmp/raw-workspaces/bare",
                    "model_exit_code": 0,
                    "model_duration_seconds": 1.25,
                    "model_stdout_tail": secret,
                    "model_stderr_tail": secret,
                    "verify_exit_code": 0,
                    "verify_stdout_tail": secret,
                    "verify_stderr_tail": secret,
                    "mythify_records": {"verifications": 0, "plans": 0},
                }
            ],
        }
        template = report["runs"][0]
        report["runs"] = [
            {
                **template,
                "iteration": iteration,
                "mode": mode,
                "mythify_profile": "fast" if mode == "mythify" else "",
                "mythify_records": {
                    "verifications": 1 if mode == "mythify" else 0,
                    "plans": 0,
                },
            }
            for iteration in (1, 2)
            for mode in ("bare", "mythify")
        ]
        attack = {
            "prompt": secret,
            "workspace": "/private/tmp/attack",
            "nested": {"model_output_tail": secret, "token": secret},
        }
        for container in (
            "summary",
            "verified_task_success",
            "false_completion_claims",
            "profile_overhead",
            "local_model_benefit",
            "fanout_value",
            "role_strength",
            "cost_metadata",
        ):
            report[container].update(attack)
            report[container]["conclusion"] = secret
            report[container]["metric"] = secret
        report["summary"]["winner_by_verified_success_rate"] = secret
        for run in report["runs"]:
            run["mythify_records"].update(attack)

        summary = local_model_eval.build_sanitized_summary(report)
        encoded = json.dumps(summary, sort_keys=True)

        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["kind"], "mythify_efficacy_summary")
        self.assertEqual(summary["evidence_status"], "available_repeated_trials")
        self.assertEqual(summary["trial_design"]["paired_trials"], 2)
        self.assertEqual(summary["conditions"], local_model_eval.comparison_conditions())
        self.assertEqual(
            summary["cost_metadata"],
            local_model_eval.build_cost_metadata(
                "subscription_included_authentication",
                "not_measured",
                None,
                "not_measured",
            ),
        )
        self.assertEqual(summary["profile_overhead"]["inference"], "inconclusive")
        self.assertEqual(summary["profile_overhead"]["conclusion"], "no_claim")
        self.assertNotIn("winner_by_lower_avg_duration", summary["profile_overhead"])
        self.assertIn("2 completed pairs", " ".join(summary["caveats"]).lower())
        self.assertNotIn(secret, encoded)
        self.assertNotIn("/private/tmp", encoded)
        def collect_keys(value):
            if isinstance(value, dict):
                keys = set(value)
                for child in value.values():
                    keys.update(collect_keys(child))
                return keys
            if isinstance(value, list):
                keys = set()
                for child in value:
                    keys.update(collect_keys(child))
                return keys
            return set()

        keys = collect_keys(summary)
        for forbidden in (
            "workspace",
            "workspaces_root",
            "model_stdout_tail",
            "model_stderr_tail",
            "verify_stdout_tail",
            "verify_stderr_tail",
            "prompt",
        ):
            self.assertNotIn(forbidden, keys)
        self.assertEqual(summary["runs"][0]["model_exit_code"], 0)
        self.assertEqual(summary["runs"][0]["verify_exit_code"], 0)

    def test_sanitizer_rejects_secret_values_in_allowed_fields(self):
        report = {
            "engine": "codex-cli",
            "scenario": "word_count_bugfix",
            "scenario_count": 1,
            "repeat": 2,
            "mythify_profile": "auto",
            "cost_metadata": local_model_eval.build_cost_metadata(
                "unknown", "unknown", None, "unknown"
            ),
            "runs": [],
        }

        attacks = []
        attacks.append({**report, "engine": "SECRET-TOKEN-VALUE"})
        attacks.append({
            **report,
            "cost_metadata": {
                "billing_posture": "SECRET-TOKEN-VALUE",
                "monetary_cost": {"measurement_status": "unknown", "value_dollars": None},
                "subscription_quota": {"measurement_status": "unknown"},
            },
        })
        attacks.append({
            **report,
            "runs": [{
                "scenario": "word_count_bugfix",
                "iteration": 1,
                "mode": "bare",
                "mythify_profile": "",
                "speed": "auto",
                "model_exit_code": 0,
                "model_duration_seconds": 1.0,
                "verify_exit_code": 0,
                "mythify_records": {"verifications": "SECRET-TOKEN-VALUE", "plans": 0},
            }],
        })

        for attack_report in attacks:
            with self.assertRaises(ValueError):
                local_model_eval.build_sanitized_summary(attack_report)

    def test_single_trial_summary_is_explicitly_insufficient(self):
        report = {
            "engine": "command",
            "scenario": "word_count_bugfix",
            "scenario_count": 1,
            "repeat": 1,
            "mythify_profile": "fast",
            "bare_speed": "auto",
            "mythify_speed": "auto",
            "summary": {},
            "verified_task_success": {},
            "false_completion_claims": {},
            "profile_overhead": {},
            "local_model_benefit": {},
            "fanout_value": {},
            "role_strength": {},
            "runs": [],
        }

        summary = local_model_eval.build_sanitized_summary(report)

        self.assertEqual(summary["evidence_status"], "insufficient_single_trial")
        self.assertIn("single trial", " ".join(summary["caveats"]).lower())


if __name__ == "__main__":
    unittest.main()
