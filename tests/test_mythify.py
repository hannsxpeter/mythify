"""Unit and end-to-end tests for scripts/mythify.py.

Every test invokes the CLI as a subprocess with sys.executable and a scrubbed
environment: MYTHIFY_DIR removed and HOME pointed at a per-test temp directory
so the real global lessons store is never touched. The working directory is a
per-test temp project directory.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
OPERATION_REGISTRY = REPO_ROOT / "protocol" / "operation-registry.json"

NO_WORKSPACE_MESSAGE = (
    "[FAIL] No .mythify workspace found. Run: python3 scripts/mythify.py init"
)
EVIDENCE_MESSAGE = (
    "[FAIL] Evidence required: pass a RESULT describing what proves this status."
)
VERIFIED_EVIDENCE_MESSAGE = (
    "[FAIL] Verified evidence required: MYTHIFY_REQUIRE_VERIFIED_STEP=1 but no "
    "passing 'verify run' was recorded since this step started. Run 'verify run' "
    "with a passing check first."
)
VERIFY_RUN_DISABLED_MESSAGE = (
    "[FAIL] verify run is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded. Unset it to enable execution, or use "
    "verify claim to record a self-reported attestation."
)


def shell_py(code):
    """A shell command string that runs a small Python snippet."""
    return '"{0}" -c "{1}"'.format(sys.executable, code)


class CliTestCase(unittest.TestCase):
    """Base: temp project dir, temp HOME, scrubbed env, subprocess runner."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="mythify-proj-"))
        self.home = Path(tempfile.mkdtemp(prefix="mythify-home-"))
        self.addCleanup(shutil.rmtree, str(self.project), True)
        self.addCleanup(shutil.rmtree, str(self.home), True)

    def run_cli(self, *args, cwd=None, env_extra=None):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(cwd or self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def init_workspace(self):
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.project / ".mythify"

    def read_json(self, path):
        with open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def read_jsonl(self, path):
        records = []
        with open(str(path), "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def state_snapshot(self, state):
        snapshot = {}
        for path in sorted(state.rglob("*")):
            if path.is_file():
                snapshot[path.relative_to(state).as_posix()] = path.read_bytes()
        return snapshot


class TestInit(CliTestCase):
    def test_init_creates_documented_layout(self):
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK]", result.stdout)
        state = self.project / ".mythify"
        self.assertTrue(state.is_dir())
        self.assertTrue((state / "plans").is_dir())
        self.assertTrue((state / "plans" / "archive").is_dir())
        self.assertTrue((state / "lessons").is_dir())
        memory = self.read_json(state / "memory.json")
        self.assertEqual(memory["entries"], [])
        self.assertIn("created", memory["metadata"])
        self.assertIn("last_updated", memory["metadata"])
        self.assertEqual(memory["metadata"]["total_entries"], 0)

    def test_reinit_warns_and_exits_zero(self):
        self.init_workspace()
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[WARN]", result.stdout)

    def test_help_exits_zero(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        for name in ("init", "protocol", "status", "classify", "host-model", "outcome", "plan", "step",
                     "memory", "lesson", "logs", "verify", "reflect", "summary"):
            self.assertIn(name, result.stdout)


class TestProtocolHandshake(CliTestCase):
    def test_protocol_check_accepts_repo_protocol_and_generated_variants(self):
        result = self.run_cli("protocol", "check", cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Protocol handshake verified", result.stdout)

    def test_protocol_check_accepts_explicit_generated_copy(self):
        shutil.copy2(REPO_ROOT / "AGENTS.md", self.project / "AGENTS.md")
        result = self.run_cli("protocol", "check", "AGENTS.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AGENTS.md", result.stdout)

    def test_protocol_check_works_from_copied_drop_in_install(self):
        (self.project / "scripts").mkdir()
        (self.project / "protocol").mkdir()
        shutil.copy2(REPO_ROOT / "AGENTS.md", self.project / "AGENTS.md")
        shutil.copy2(CLI, self.project / "scripts" / "mythify.py")
        shutil.copy2(
            OPERATION_REGISTRY,
            self.project / "protocol" / "operation-registry.json",
        )
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        result = subprocess.run(
            [sys.executable, "scripts/mythify.py", "protocol", "check", "AGENTS.md"],
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Protocol handshake verified", result.stdout)

    def test_protocol_check_rejects_drifted_hash(self):
        text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        marker = "<!-- Mythify protocol-sha256: "
        start = text.index(marker) + len(marker)
        end = text.index(" -->", start)
        drifted = text[:start] + ("0" * 64) + text[end:]
        path = self.project / "AGENTS.md"
        path.write_text(drifted, encoding="utf-8")
        result = self.run_cli("protocol", "check", "AGENTS.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Protocol handshake drift", result.stderr)

    def test_protocol_check_json_failure_exits_nonzero(self):
        path = self.project / "AGENTS.md"
        path.write_text("# The Mythify Protocol\n", encoding="utf-8")
        result = self.run_cli("protocol", "check", "AGENTS.md", "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["checked"][0]["status"], "missing_header")

    def test_protocol_check_rejects_missing_hash_header(self):
        path = self.project / "AGENTS.md"
        path.write_text("# The Mythify Protocol\n", encoding="utf-8")
        result = self.run_cli("protocol", "check", "AGENTS.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Protocol handshake missing", result.stderr)


class TestWorkspaceResolution(CliTestCase):
    def test_commands_without_workspace_fail_with_message(self):
        for args in (["status"], ["memory", "get"], ["summary"], ["outcome", "status"],
                     ["plan", "list"], ["verify", "claim", "c", "e"]):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 1, repr(args))
            self.assertIn(NO_WORKSPACE_MESSAGE, result.stderr, repr(args))

    def test_discovery_walks_up_from_nested_subdirectory(self):
        state = self.init_workspace()
        nested = self.project / "a" / "b"
        nested.mkdir(parents=True)
        result = self.run_cli("memory", "set", "nested_key", "nested_value", cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = self.read_json(state / "memory.json")
        keys = [entry["key"] for entry in memory["entries"]]
        self.assertIn("nested_key", keys)

    def test_mythify_dir_overrides_discovery_and_is_created_on_demand(self):
        state = self.init_workspace()
        custom = self.project / "custom-state-dir"
        self.assertFalse(custom.exists())
        result = self.run_cli(
            "memory", "set", "override_key", "override_value",
            env_extra={"MYTHIFY_DIR": str(custom)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(custom.is_dir())
        memory = self.read_json(custom / "memory.json")
        self.assertEqual(memory["entries"][0]["key"], "override_key")
        project_memory = self.read_json(state / "memory.json")
        self.assertEqual(project_memory["entries"], [])


class TestClassification(CliTestCase):
    def test_classify_works_without_workspace(self):
        result = self.run_cli(
            "classify",
            "benchmark bare codex vs mythify across tasks",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Task classification", result.stdout)
        self.assertIn("type: benchmark", result.stdout)
        self.assertIn("ceremony: full", result.stdout)
        self.assertIn("execution profile: full", result.stdout)
        self.assertIn("fanout: recommended", result.stdout)
        self.assertIn("model triage: recommended", result.stdout)

    def test_classify_json_for_question(self):
        result = self.run_cli("classify", "what does this project do?", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task_type"], "question")
        self.assertEqual(payload["risk"], "low")
        self.assertEqual(payload["ceremony"], "none")
        self.assertEqual(payload["execution_profile"], "direct")
        self.assertEqual(payload["fanout"], "not_recommended")
        self.assertEqual(payload["model_triage"], "skip")
        self.assertEqual(payload["model_policy"]["session"]["control"], "host_selected")
        self.assertEqual(payload["model_policy"]["verifier"]["engine"], "local_command")

    def test_classify_recommends_fast_host_settings_for_direct_question(self):
        result = self.run_cli(
            "classify",
            "what is 1 + 1?",
            "--json",
            "--platform",
            "codex-desktop",
            "--session-model",
            "gpt-5.5",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        recommendation = payload["model_policy"]["session"]["recommendation"]
        self.assertEqual(payload["execution_profile"], "direct")
        self.assertEqual(recommendation["action"], "downgrade")
        self.assertEqual(recommendation["target_profile"], "fast")
        self.assertEqual(recommendation["target_model"], "gpt-5.4-mini")
        self.assertEqual(recommendation["target_model_tier"], "fast")
        self.assertEqual(recommendation["thinking"], "low")
        self.assertEqual(recommendation["speed"], "fast")

    def test_classify_recommends_strong_host_settings_for_research(self):
        result = self.run_cli(
            "classify",
            "make me a research paper about memory consolidation in LLM agents",
            "--json",
            "--platform",
            "codex-desktop",
            "--session-model",
            "gpt-5.4-mini",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        recommendation = payload["model_policy"]["session"]["recommendation"]
        self.assertEqual(payload["task_type"], "research")
        self.assertEqual(recommendation["action"], "upgrade")
        self.assertEqual(recommendation["target_profile"], "strong")
        self.assertEqual(recommendation["target_model"], "gpt-5.5")
        self.assertEqual(recommendation["thinking"], "high")
        self.assertEqual(recommendation["speed"], "standard")

    def test_classify_host_recommendation_respects_model_override(self):
        result = self.run_cli(
            "classify",
            "what is 1 + 1?",
            "--json",
            "--platform",
            "codex-desktop",
            env_extra={"MYTHIFY_HOST_FAST_MODEL": "gpt-fast-local"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        recommendation = payload["model_policy"]["session"]["recommendation"]
        self.assertEqual(recommendation["target_profile"], "fast")
        self.assertEqual(recommendation["target_model"], "gpt-fast-local")
        self.assertEqual(
            recommendation["target_model_source"],
            "env:MYTHIFY_HOST_FAST_MODEL",
        )

    def test_classify_model_policy_tracks_platform_model_and_effort(self):
        result = self.run_cli(
            "classify",
            "implement platform-aware model selection",
            "--json",
            "--platform",
            "codex-desktop",
            "--triage-engine",
            "codex-cli",
            "--effort",
            "high",
            "--speed",
            "fast",
            "--session-model",
            "gpt-5",
            "--spawn-ceiling",
            "same_or_lower",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        policy = payload["model_policy"]
        self.assertEqual(payload["execution_profile"], "standard")
        self.assertEqual(policy["session"]["platform"], "codex-desktop")
        self.assertEqual(policy["session"]["control"], "host_selected")
        self.assertEqual(policy["session"]["model"], "gpt-5")
        self.assertEqual(policy["session"]["model_source"], "explicit")
        self.assertEqual(policy["session"]["model_tier"], "frontier")
        self.assertEqual(policy["session"]["speed_policy"], "requested_fast")
        self.assertEqual(policy["spawn_ceiling"]["policy"], "same_or_lower")
        self.assertEqual(policy["spawn_ceiling"]["session_model_tier"], "frontier")
        self.assertEqual(policy["triage"]["engine"], "codex-cli")
        self.assertEqual(policy["triage"]["model_policy"], "platform_default")
        self.assertEqual(policy["triage"]["model_relation_to_session"], "lower_preferred")
        self.assertEqual(policy["fanout_worker"]["model_policy"], "per_task_over_job_over_env_over_engine_default")
        self.assertEqual(policy["fanout_worker"]["model_relation_to_session"], "same_or_lower")
        self.assertEqual(policy["fanout_worker"]["effort"], "high")
        self.assertEqual(policy["fanout_worker"]["effort_policy"], "explicit")
        self.assertEqual(policy["fanout_worker"]["speed"], "fast")
        self.assertEqual(policy["fanout_worker"]["speed_policy"], "explicit")

    def test_classify_includes_per_role_provider_defaults(self):
        result = self.run_cli(
            "classify",
            "summarize this codebase",
            "--json",
            "--platform",
            "codex-desktop",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        policy = payload["model_policy"]
        providers = policy["provider_defaults"]["roles"]
        self.assertEqual(
            policy["provider_defaults"]["fallback_policy"],
            "no_implicit_cross_provider_fallback",
        )
        api_contract = policy["provider_defaults"]["api_provider_contract"]
        self.assertEqual(api_contract["status"], "metadata_supported")
        self.assertFalse(api_contract["execution_enabled"])
        self.assertEqual(
            api_contract["billing_policy"],
            "explicit_provider_required",
        )
        self.assertIn("pricing_url", api_contract["cost_metadata_fields"])
        self.assertEqual(
            api_contract["providers"]["openai-api"]["api_key_env"],
            "OPENAI_API_KEY",
        )
        self.assertEqual(
            api_contract["providers"]["anthropic-api"]["auth_header"],
            "x-api-key",
        )
        self.assertEqual(
            api_contract["providers"]["openai-compatible-hosted"]["base_url_env"],
            "MYTHIFY_HOSTED_OPENAI_COMPAT_BASE_URL",
        )
        self.assertEqual(providers["session"]["provider"], "host")
        self.assertEqual(providers["triage"]["provider"], "host_cli")
        self.assertEqual(providers["reader"]["provider"], "local_openai_compatible")
        self.assertEqual(providers["fanout_worker"]["provider"], "host_cli")
        self.assertEqual(providers["reviewer"]["provider"], "host_cli")
        self.assertEqual(providers["verifier"]["provider"], "local_command")
        self.assertEqual(policy["reader"]["provider"], "local_openai_compatible")
        self.assertFalse(policy["reader"]["writes_state"])
        self.assertEqual(
            policy["reader"]["evidence_status"],
            "model_output_not_verification",
        )

    def test_classify_defaults_reviewers_to_same_or_lower(self):
        result = self.run_cli(
            "classify",
            "audit this release for hidden regressions",
            "--json",
            "--session-model",
            "haiku",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        reviewer = payload["model_policy"]["reviewer"]
        self.assertEqual(reviewer["spawn"], "recommended")
        self.assertEqual(reviewer["stronger_model_policy"], "same_or_lower")
        self.assertEqual(reviewer["stronger_model_policy_source"], "default")
        self.assertFalse(reviewer["stronger_models_allowed"])
        self.assertEqual(reviewer["model_relation_to_session"], "same_or_lower")

    def test_classify_accepts_explicit_stronger_reviewer_opt_in(self):
        result = self.run_cli(
            "classify",
            "audit this release for hidden regressions",
            "--json",
            "--session-model",
            "haiku",
            "--reviewer-strength",
            "allow_stronger",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        reviewer = payload["model_policy"]["reviewer"]
        self.assertEqual(reviewer["stronger_model_policy"], "allow_stronger")
        self.assertEqual(reviewer["stronger_model_policy_source"], "explicit")
        self.assertTrue(reviewer["stronger_models_allowed"])
        self.assertEqual(
            reviewer["model_relation_to_session"],
            "may_exceed_session_with_reviewer_opt_in",
        )

    def test_classify_accepts_env_stronger_reviewer_opt_in(self):
        result = self.run_cli(
            "classify",
            "audit this release for hidden regressions",
            "--json",
            "--session-model",
            "haiku",
            env_extra={"MYTHIFY_REVIEWER_STRENGTH": "allow_stronger"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        reviewer = payload["model_policy"]["reviewer"]
        self.assertEqual(reviewer["stronger_model_policy"], "allow_stronger")
        self.assertEqual(reviewer["stronger_model_policy_source"], "env")
        self.assertTrue(reviewer["stronger_models_allowed"])

    def test_classify_role_provider_env_override_and_invalid_guard(self):
        result = self.run_cli(
            "classify",
            "make this better",
            "--json",
            env_extra={
                "MYTHIFY_ROLE_TRIAGE_PROVIDER": "local_openai_compatible",
                "MYTHIFY_ROLE_REVIEWER_PROVIDER": "surprise-cloud",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        providers = payload["model_policy"]["provider_defaults"]["roles"]
        self.assertEqual(providers["triage"]["provider"], "local_openai_compatible")
        self.assertEqual(
            providers["triage"]["provider_source"],
            "env:MYTHIFY_ROLE_TRIAGE_PROVIDER",
        )
        self.assertEqual(providers["triage"]["status"], "selected")
        self.assertEqual(providers["reviewer"]["provider"], "host_cli")
        self.assertEqual(providers["reviewer"]["requested_provider"], "surprise-cloud")
        self.assertEqual(providers["reviewer"]["status"], "invalid_env_ignored")

    def test_host_model_switch_feeds_classify_session_model(self):
        state = self.init_workspace()
        switched = self.run_cli(
            "host-model",
            "switch",
            "gpt-5.4",
            "--platform",
            "codex-desktop",
            "--current-model",
            "gpt-5.3-codex",
            "--thinking",
            "high",
            "--speed",
            "fast",
            "--json",
        )
        self.assertEqual(switched.returncode, 0, switched.stderr)
        record = json.loads(switched.stdout)
        self.assertEqual(record["target_model"], "gpt-5.4")
        self.assertEqual(record["platform"], "codex-desktop")
        self.assertEqual(record["status"], "recorded_requires_host_action")
        self.assertEqual(record["host_capability"]["status"], "supported")
        self.assertFalse(record["host_capability"]["can_switch_current_thread"])
        self.assertTrue(record["host_capability"]["can_set_new_thread_model"])
        self.assertTrue(record["host_capability"]["can_set_worker_model"])
        self.assertTrue(record["host_capability"]["can_set_thinking"])
        self.assertFalse(record["can_apply_current_chat"])
        self.assertEqual(record["switch_result"]["status"], "manual")
        self.assertEqual(record["switch_result"]["requested_model"], "gpt-5.4")
        self.assertEqual(record["switch_result"]["requested_thinking"], "high")
        self.assertEqual(record["switch_result"]["requested_speed"], "fast")
        self.assertFalse(record["switch_result"]["current_chat_supported"])
        self.assertFalse(record["switch_result"]["current_chat_confirmed"])
        self.assertTrue(record["switch_result"]["manual_action_required"])
        self.assertEqual(record["switch_result"]["applied_by"], "none")
        self.assertTrue((state / "host-model.json").exists())

        status_json = self.run_cli("host-model", "status", "--json")
        self.assertEqual(status_json.returncode, 0, status_json.stderr)
        status_record = json.loads(status_json.stdout)
        self.assertEqual(status_record["switch_result"]["status"], "manual")
        status_text = self.run_cli("host-model", "status")
        self.assertEqual(status_text.returncode, 0, status_text.stderr)
        self.assertIn("switch status: manual", status_text.stdout)
        self.assertIn("current-chat confirmed: no", status_text.stdout)
        self.assertIn("current-chat switch: no", status_text.stdout)
        self.assertIn("new-thread model: yes", status_text.stdout)

        result = self.run_cli(
            "classify",
            "implement a follow-up feature",
            "--json",
            "--platform",
            "codex-desktop",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        policy = payload["model_policy"]
        self.assertEqual(policy["session"]["model"], "gpt-5.4")
        self.assertEqual(policy["session"]["model_source"], "host_model_switch")
        self.assertEqual(policy["session"]["model_tier"], "frontier")

        cleared = self.run_cli("host-model", "clear")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        status = self.run_cli("host-model", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("No host model switch", status.stdout)

    def test_host_model_status_enriches_legacy_records(self):
        state = self.init_workspace()
        legacy = {
            "platform": "codex-cli",
            "requested_platform": "codex-cli",
            "target_model": "gpt-5.4",
            "current_model": "",
            "target_model_tier": "frontier",
            "thinking": "auto",
            "speed": "auto",
            "reason": "",
            "status": "recorded_requires_host_action",
            "control": "host_selected",
            "can_apply_current_chat": False,
            "updated": "2026-06-13T00:00:00+00:00",
            "host_actions": [],
        }
        (state / "host-model.json").write_text(json.dumps(legacy) + "\n", encoding="utf-8")
        status = self.run_cli("host-model", "status", "--json")
        self.assertEqual(status.returncode, 0, status.stderr)
        record = json.loads(status.stdout)
        self.assertEqual(record["host_capability"]["status"], "supported")
        self.assertEqual(record["switch_result"]["status"], "manual")
        self.assertFalse(record["switch_result"]["current_chat_confirmed"])

    def test_classify_vague_short_request_recommends_model_triage(self):
        result = self.run_cli("classify", "make this better", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task_type"], "feature")
        self.assertEqual(payload["ambiguity"], "high")
        self.assertEqual(payload["execution_profile"], "standard")
        self.assertEqual(payload["model_triage"], "recommended")

    def test_classify_defaults_fanout_visibility_to_summary(self):
        result = self.run_cli(
            "classify",
            "compare these three implementation approaches",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["fanout_visibility"], "summary")
        self.assertEqual(payload["fanout_visibility_source"], "default")
        self.assertEqual(
            payload["model_policy"]["fanout_worker"]["visibility"],
            "summary",
        )

    def test_classify_infers_fanout_visibility_from_prompt(self):
        quiet = self.run_cli(
            "classify",
            "spawn workers quietly and do not show worker details",
            "--json",
        )
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        quiet_payload = json.loads(quiet.stdout)
        self.assertEqual(quiet_payload["fanout_visibility"], "quiet")
        self.assertEqual(quiet_payload["fanout_visibility_source"], "prompt")

        verbose = self.run_cli(
            "classify",
            "run subagents and show full worker output",
            "--json",
        )
        self.assertEqual(verbose.returncode, 0, verbose.stderr)
        verbose_payload = json.loads(verbose.stdout)
        self.assertEqual(verbose_payload["fanout_visibility"], "verbose")

        threaded = self.run_cli(
            "classify",
            "spawn visible subagent chats in separate threads",
            "--json",
        )
        self.assertEqual(threaded.returncode, 0, threaded.stderr)
        threaded_payload = json.loads(threaded.stdout)
        self.assertEqual(threaded_payload["fanout_visibility"], "threaded")

    def test_classify_focused_bugfix_uses_fast_profile(self):
        result = self.run_cli(
            "classify",
            "fix word_count.py so python3 -m unittest passes",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task_type"], "bugfix")
        self.assertEqual(payload["execution_profile"], "fast")
        self.assertIn("fast profile", payload["next_action"])

    def test_classify_auto_triage_skips_when_gate_skips(self):
        result = self.run_cli("classify", "what does this project do?", "--json", "--triage", "auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["model_triage_run"]["attempted"])

    def test_classify_runs_command_backed_model_triage(self):
        stub = self.project / "triage_stub.py"
        stub.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "sys.stdin.read()",
                    "print(json.dumps({",
                    "    'primary_type': 'benchmark',",
                    "    'secondary_types': ['evaluation'],",
                    "    'ambiguity': 'low',",
                    "    'hidden_questions': [],",
                    "    'likely_files_or_surfaces': ['scripts/local_model_eval.py'],",
                    "    'verification_plan': ['run benchmark harness'],",
                    "    'fanout_plan': [],",
                    "    'risk_notes': [],",
                    "    'recommended_first_step': 'run the harness'",
                    "}))",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = self.run_cli(
            "classify",
            "benchmark bare codex vs mythify across tasks",
            "--json",
            "--triage",
            "auto",
            env_extra={
                "MYTHIFY_TRIAGE_ENGINE": "command",
                "MYTHIFY_TRIAGE_COMMAND": '"{0}" "{1}"'.format(sys.executable, stub),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        run = payload["model_triage_run"]
        self.assertTrue(run["attempted"])
        self.assertTrue(run["ok"], run)
        self.assertEqual(run["engine"], "command")
        self.assertEqual(run["engine_policy"], "env")
        self.assertEqual(run["model_policy"], "command_default")
        self.assertEqual(run["effort"], "low")
        self.assertEqual(run["speed"], "auto")
        self.assertEqual(run["parsed"]["primary_type"], "benchmark")

    def test_classify_uses_word_boundaries_for_security_terms(self):
        result = self.run_cli("classify", "create author profile page", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertNotEqual(payload["task_type"], "security")
        self.assertNotEqual(payload["risk"], "high")

    def test_classify_security_authentication_work(self):
        result = self.run_cli(
            "classify",
            "audit authentication token permissions",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["task_type"], "security")
        self.assertEqual(payload["risk"], "high")
        self.assertEqual(payload["ceremony"], "full")
        self.assertEqual(payload["execution_profile"], "full")


class TestPlanLifecycle(CliTestCase):
    def test_create_with_steps(self):
        state = self.init_workspace()
        steps = json.dumps([
            {"title": "First step", "success_criteria": "first done"},
            {"title": "Second step"},
        ])
        result = self.run_cli("plan", "create", "Build the widget", "--steps", steps)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(state / "plans" / "build-the-widget.json")
        self.assertEqual(plan["name"], "build-the-widget")
        self.assertEqual(plan["goal"], "Build the widget")
        self.assertIn("created", plan)
        self.assertIn("last_updated", plan)
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(plan["steps"][0]["id"], 1)
        self.assertEqual(plan["steps"][0]["success_criteria"], "first done")
        self.assertEqual(plan["steps"][0]["status"], "pending")
        self.assertIsNone(plan["steps"][0]["result"])
        self.assertEqual(plan["steps"][1]["id"], 2)
        self.assertEqual(plan["steps"][1]["success_criteria"], "")
        active = (state / "plans" / "active").read_text(encoding="utf-8").strip()
        self.assertEqual(active, "build-the-widget")

    def test_create_without_steps_suggests_add_step(self):
        state = self.init_workspace()
        result = self.run_cli("plan", "create", "Empty goal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("add-step", result.stdout)
        plan = self.read_json(state / "plans" / "empty-goal.json")
        self.assertEqual(plan["steps"], [])

    def test_create_invalid_steps_json_fails(self):
        self.init_workspace()
        result = self.run_cli("plan", "create", "Bad steps", "--steps", "{not json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stderr)

    def test_create_with_name_and_slug_collision_appends_2(self):
        state = self.init_workspace()
        first = self.run_cli("plan", "create", "Goal one", "--name", "Shared Name")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_cli("plan", "create", "Goal two", "--name", "Shared Name")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue((state / "plans" / "shared-name.json").exists())
        self.assertTrue((state / "plans" / "shared-name-2.json").exists())
        self.assertIn("shared-name-2", second.stdout)

    def test_slug_keeps_hyphen_landing_on_truncation_boundary(self):
        # Shared slug contract: strip edge hyphens, THEN truncate to 40. A
        # name whose 40th slug character is a hyphen keeps it, so both
        # implementations (CLI and MCP server) produce the same filename.
        state = self.init_workspace()
        name = "a" * 39 + " b"
        expected_slug = "a" * 39 + "-"
        result = self.run_cli("plan", "create", "Boundary goal", "--name", name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((state / "plans" / (expected_slug + ".json")).exists())
        shown = self.run_cli("plan", "show", name)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Boundary goal", shown.stdout)

    def test_all_punctuation_name_falls_back_to_plan_slug(self):
        state = self.init_workspace()
        result = self.run_cli("plan", "create", "Degenerate goal", "--name", "!!!")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((state / "plans" / "plan.json").exists())
        shown = self.run_cli("plan", "show", "plan")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Degenerate goal", shown.stdout)

    def test_add_step_appends_with_next_id(self):
        state = self.init_workspace()
        steps = json.dumps([{"title": "A"}])
        self.run_cli("plan", "create", "Stepped goal", "--steps", steps)
        result = self.run_cli("plan", "add-step", "B step", "--criteria", "b passes")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(state / "plans" / "stepped-goal.json")
        self.assertEqual(len(plan["steps"]), 2)
        self.assertEqual(plan["steps"][1]["id"], 2)
        self.assertEqual(plan["steps"][1]["title"], "B step")
        self.assertEqual(plan["steps"][1]["success_criteria"], "b passes")

    def test_list_shows_active_marker_and_archived_count(self):
        self.init_workspace()
        self.run_cli("plan", "create", "Alpha goal")
        self.run_cli("plan", "create", "Beta goal")
        result = self.run_cli("plan", "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha-goal", result.stdout)
        self.assertIn("beta-goal", result.stdout)
        self.assertIn("(active)", result.stdout)
        self.assertIn("Archived plans: 0", result.stdout)

    def test_show_named_and_missing(self):
        self.init_workspace()
        steps = json.dumps([{"title": "Visible step", "success_criteria": "see it"}])
        self.run_cli("plan", "create", "Show goal", "--steps", steps)
        shown = self.run_cli("plan", "show", "show-goal")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("Show goal", shown.stdout)
        self.assertIn("Visible step", shown.stdout)
        missing = self.run_cli("plan", "show", "no-such-plan")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("[FAIL]", missing.stderr)

    def test_switch_changes_active_pointer(self):
        state = self.init_workspace()
        self.run_cli("plan", "create", "Plan one")
        self.run_cli("plan", "create", "Plan two")
        active = (state / "plans" / "active").read_text(encoding="utf-8").strip()
        self.assertEqual(active, "plan-two")
        result = self.run_cli("plan", "switch", "plan-one")
        self.assertEqual(result.returncode, 0, result.stderr)
        active = (state / "plans" / "active").read_text(encoding="utf-8").strip()
        self.assertEqual(active, "plan-one")
        missing = self.run_cli("plan", "switch", "no-such-plan")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("[FAIL]", missing.stderr)

    def test_archive_moves_file_and_clears_active_pointer(self):
        state = self.init_workspace()
        self.run_cli("plan", "create", "Archive me")
        result = self.run_cli("plan", "archive", "archive-me")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((state / "plans" / "archive-me.json").exists())
        self.assertTrue((state / "plans" / "archive" / "archive-me.json").exists())
        status = self.run_cli("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Active plan: none", status.stdout)
        listed = self.run_cli("plan", "list")
        self.assertIn("Archived plans: 1", listed.stdout)


class TestStepUpdates(CliTestCase):
    def make_plan(self):
        state = self.init_workspace()
        steps = json.dumps([
            {"title": "Do first thing", "success_criteria": "first verified"},
            {"title": "Do second thing", "success_criteria": "second verified"},
        ])
        result = self.run_cli("plan", "create", "Step plan", "--steps", steps)
        self.assertEqual(result.returncode, 0, result.stderr)
        return state, state / "plans" / "step-plan.json"

    def test_valid_transition_in_progress(self):
        state, plan_file = self.make_plan()
        result = self.run_cli("step", "1", "in_progress")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "in_progress")
        self.assertIn("updated_at", plan["steps"][0])

    def test_invalid_status_rejected(self):
        self.make_plan()
        result = self.run_cli("step", "1", "donezo")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stderr)

    def test_completed_without_result_rejected_and_plan_unmodified(self):
        state, plan_file = self.make_plan()
        before = plan_file.read_text(encoding="utf-8")
        result = self.run_cli("step", "1", "completed")
        self.assertEqual(result.returncode, 1)
        self.assertIn(EVIDENCE_MESSAGE, result.stderr)
        self.assertEqual(plan_file.read_text(encoding="utf-8"), before)

    def test_failed_without_result_rejected_and_plan_unmodified(self):
        state, plan_file = self.make_plan()
        before = plan_file.read_text(encoding="utf-8")
        result = self.run_cli("step", "1", "failed")
        self.assertEqual(result.returncode, 1)
        self.assertIn(EVIDENCE_MESSAGE, result.stderr)
        self.assertEqual(plan_file.read_text(encoding="utf-8"), before)

    def test_refusals_preserve_whole_state_snapshot(self):
        state, plan_file = self.make_plan()
        seeded_memory = self.run_cli("memory", "set", "keep", "yes")
        self.assertEqual(seeded_memory.returncode, 0, seeded_memory.stderr)
        seeded_lesson = self.run_cli("lesson", "add", "Snapshot lesson", "keep this")
        self.assertEqual(seeded_lesson.returncode, 0, seeded_lesson.stderr)
        seeded_outcome = self.run_cli(
            "outcome",
            "start",
            "Snapshot outcome",
            "--success",
            "python exits zero",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--name",
            "snapshot-outcome",
        )
        self.assertEqual(seeded_outcome.returncode, 0, seeded_outcome.stderr)

        before = self.state_snapshot(state)
        missing_result = self.run_cli("step", "1", "completed")
        self.assertEqual(missing_result.returncode, 1)
        self.assertIn(EVIDENCE_MESSAGE, missing_result.stderr)
        self.assertEqual(self.state_snapshot(state), before)

        before = self.state_snapshot(state)
        refused_clear = self.run_cli("memory", "clear")
        self.assertEqual(refused_clear.returncode, 1)
        self.assertIn("Refusing to clear memory", refused_clear.stderr)
        self.assertEqual(self.state_snapshot(state), before)

        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        before = self.state_snapshot(state)
        verified_gate = self.run_cli(
            "step", "1", "completed", "not verified",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "1"},
        )
        self.assertEqual(verified_gate.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, verified_gate.stderr)
        self.assertEqual(self.state_snapshot(state), before)

        before = self.state_snapshot(state)
        disabled_verify = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            env_extra={"MYTHIFY_DISABLE_RUN": "1"},
        )
        self.assertEqual(disabled_verify.returncode, 2)
        self.assertIn(VERIFY_RUN_DISABLED_MESSAGE, disabled_verify.stderr)
        self.assertEqual(self.state_snapshot(state), before)

    def test_completed_with_result_persists_and_prints_next_pending(self):
        state, plan_file = self.make_plan()
        result = self.run_cli("step", "1", "completed", "all tests passed")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertEqual(plan["steps"][0]["result"], "all tests passed")
        self.assertIn("updated_at", plan["steps"][0])
        self.assertIn("Next pending", result.stdout)
        self.assertIn("Do second thing", result.stdout)

    def test_no_pending_steps_message_after_last_completion(self):
        self.make_plan()
        self.run_cli("step", "1", "completed", "done one")
        result = self.run_cli("step", "2", "completed", "done two")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No pending steps remain.", result.stdout)

    def test_gate_unset_plain_result_still_completes(self):
        # Backward compatibility: with the gate unset, a non-empty RESULT alone
        # marks the step completed, exactly as before.
        state, plan_file = self.make_plan()
        result = self.run_cli("step", "1", "completed", "all tests passed")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "completed")

    def test_gate_on_without_verification_refused_and_plan_unmodified(self):
        state, plan_file = self.make_plan()
        before = plan_file.read_text(encoding="utf-8")
        result = self.run_cli(
            "step", "1", "completed", "I promise it works",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "1"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, result.stderr)
        self.assertEqual(plan_file.read_text(encoding="utf-8"), before)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "pending")

    def test_gate_on_with_passing_verification_completes(self):
        state, plan_file = self.make_plan()
        # ACT: move the step in_progress (sets the lower bound).
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        # VERIFY: record a passing executed verification after the step started.
        verified = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "step one verified",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        # COMPLETE: the gate is satisfied, so completion advances.
        result = self.run_cli(
            "step", "1", "completed", "verified green",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertIn("Next pending", result.stdout)

    def test_gate_on_requires_bound_verification_for_the_target_step(self):
        state, plan_file = self.make_plan()
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        verified = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "step one verified",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(record["plan"], "step-plan")
        self.assertEqual(record["step_id"], 1)
        self.assertEqual(record["step_title"], "Do first thing")
        self.assertEqual(record["step_status"], "in_progress")

        result = self.run_cli(
            "step", "2", "completed", "step two should not borrow step one evidence",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "1"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][1]["status"], "pending")

    def test_gate_on_with_only_failed_verification_refused(self):
        state, plan_file = self.make_plan()
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        # Only a FAILED verify run exists (verified is false): does not satisfy.
        failed = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(1)"),
            "--claim", "step one not green",
        )
        self.assertEqual(failed.returncode, 2)
        before = plan_file.read_text(encoding="utf-8")
        result = self.run_cli(
            "step", "1", "completed", "claims green",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "1"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, result.stderr)
        self.assertEqual(plan_file.read_text(encoding="utf-8"), before)

    def test_gate_on_does_not_block_failed_status(self):
        # The gate applies only to completed. Recording a failure is always
        # allowed, even with the gate on and no passing verification.
        state, plan_file = self.make_plan()
        result = self.run_cli(
            "step", "1", "failed", "ran out of time",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "failed")
        self.assertEqual(plan["steps"][0]["result"], "ran out of time")


class TestMemory(CliTestCase):
    def load_operation_registry(self):
        return self.read_json(OPERATION_REGISTRY)

    def test_set_and_get(self):
        self.init_workspace()
        result = self.run_cli("memory", "set", "color", "blue")
        self.assertEqual(result.returncode, 0, result.stderr)
        got = self.run_cli("memory", "get", "color")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("blue", got.stdout)

    def test_set_overwrites_existing_key(self):
        state = self.init_workspace()
        self.run_cli("memory", "set", "color", "blue")
        self.run_cli("memory", "set", "color", "red", "--category", "decision")
        memory = self.read_json(state / "memory.json")
        self.assertEqual(len(memory["entries"]), 1)
        self.assertEqual(memory["entries"][0]["value"], "red")
        self.assertEqual(memory["entries"][0]["category"], "decision")
        self.assertEqual(memory["metadata"]["total_entries"], 1)

    def test_get_with_query_and_category_filter(self):
        self.init_workspace()
        self.run_cli("memory", "set", "db_engine", "postgres", "--category", "decision")
        self.run_cli("memory", "set", "api_port", "8080", "--category", "fact")
        by_query = self.run_cli("memory", "get", "POSTGRES")
        self.assertIn("db_engine", by_query.stdout)
        self.assertNotIn("api_port", by_query.stdout)
        by_category = self.run_cli("memory", "get", "--category", "decision")
        self.assertIn("db_engine", by_category.stdout)
        self.assertNotIn("api_port", by_category.stdout)
        both = self.run_cli("memory", "get", "8080", "--category", "decision")
        self.assertEqual(both.returncode, 0)
        self.assertIn("No matching memory entries.", both.stdout)

    def test_clear_key_removes_single_entry(self):
        state = self.init_workspace()
        self.run_cli("memory", "set", "keep", "yes")
        self.run_cli("memory", "set", "drop", "no")
        result = self.run_cli("memory", "clear", "drop")
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = self.read_json(state / "memory.json")
        keys = [entry["key"] for entry in memory["entries"]]
        self.assertEqual(keys, ["keep"])

    def test_clear_without_args_fails(self):
        self.init_workspace()
        self.run_cli("memory", "set", "keep", "yes")
        result = self.run_cli("memory", "clear")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stderr)

    def test_memory_cli_uses_operation_registry_contract(self):
        state = self.init_workspace()
        registry = self.load_operation_registry()
        memory = registry["surfaces"]["memory"]
        categories = memory["categories"]
        self.assertEqual(memory["default_category"], "fact")

        for category in categories:
            result = self.run_cli("memory", "set", category, "value", "--category", category)
            self.assertEqual(result.returncode, 0, result.stderr)

        stored = self.read_json(state / memory["state_file"])
        self.assertEqual([entry["category"] for entry in stored["entries"]], categories)

        result = self.run_cli("memory", "clear")
        self.assertEqual(result.returncode, 1)
        self.assertIn(memory["operations"]["memory_clear"]["cli"]["refusal"], result.stderr)

    def test_clear_all_empties_store(self):
        state = self.init_workspace()
        self.run_cli("memory", "set", "one", "1")
        self.run_cli("memory", "set", "two", "2")
        result = self.run_cli("memory", "clear", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = self.read_json(state / "memory.json")
        self.assertEqual(memory["entries"], [])
        self.assertEqual(memory["metadata"]["total_entries"], 0)


class TestLessons(CliTestCase):
    def test_project_add_and_list(self):
        state = self.init_workspace()
        result = self.run_cli(
            "lesson", "add", "Pin the versions", "Unpinned deps broke the build",
            "--tags", "deps,build",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        files = list((state / "lessons").glob("pin-the-versions-*.json"))
        self.assertEqual(len(files), 1)
        record = self.read_json(files[0])
        self.assertEqual(record["title"], "Pin the versions")
        self.assertEqual(record["detail"], "Unpinned deps broke the build")
        self.assertEqual(record["tags"], ["deps", "build"])
        self.assertIn("created", record)
        listed = self.run_cli("lesson", "list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("(project) Pin the versions", listed.stdout)

    def test_global_add_and_list_under_temp_home(self):
        self.init_workspace()
        result = self.run_cli(
            "lesson", "add", "Global wisdom", "Applies everywhere", "--global",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        global_dir = self.home / ".mythify" / "lessons"
        files = list(global_dir.glob("global-wisdom-*.json"))
        self.assertEqual(len(files), 1)
        listed = self.run_cli("lesson", "list")
        self.assertIn("(global) Global wisdom", listed.stdout)

    def test_tag_filter(self):
        self.init_workspace()
        self.run_cli("lesson", "add", "Tagged lesson", "Has the tag", "--tags", "alpha")
        self.run_cli("lesson", "add", "Other lesson", "No alpha tag", "--tags", "beta")
        listed = self.run_cli("lesson", "list", "--tag", "alpha")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("Tagged lesson", listed.stdout)
        self.assertNotIn("Other lesson", listed.stdout)

    def test_scope_filter(self):
        self.init_workspace()
        self.run_cli("lesson", "add", "Project only", "Project scope detail")
        self.run_cli("lesson", "add", "Global only", "Global scope detail", "--global")
        project_only = self.run_cli("lesson", "list", "--scope", "project")
        self.assertIn("Project only", project_only.stdout)
        self.assertNotIn("Global only", project_only.stdout)
        global_only = self.run_cli("lesson", "list", "--scope", "global")
        self.assertIn("Global only", global_only.stdout)
        self.assertNotIn("Project only", global_only.stdout)
        both = self.run_cli("lesson", "list", "--scope", "all")
        self.assertIn("Project only", both.stdout)
        self.assertIn("Global only", both.stdout)


class TestOutcome(CliTestCase):
    def test_outcome_start_check_and_results_success(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome",
            "start",
            "Make verifier pass",
            "--success",
            "python exits zero",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--metric",
            shell_py("import sys; sys.stdout.write('42.5')"),
            "--max-iterations",
            "2",
            "--allowed-paths",
            "scripts,tests",
            "--json",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        goal = json.loads(started.stdout)
        self.assertEqual(goal["status"], "active")
        self.assertEqual(goal["allowed_paths"], ["scripts", "tests"])
        self.assertTrue((state / "outcomes" / goal["id"] / "goal.json").exists())

        checked = self.run_cli("outcome", "check", "--json")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        payload = json.loads(checked.stdout)
        self.assertEqual(payload["goal"]["status"], "succeeded")
        self.assertEqual(payload["goal"]["iteration_count"], 1)
        self.assertIs(payload["record"]["verified"], True)
        self.assertEqual(payload["record"]["metric"]["score"], 42.5)

        status = self.run_cli("outcome", "status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("status: succeeded", status.stdout)
        results = self.run_cli("outcome", "results")
        self.assertEqual(results.returncode, 0, results.stderr)
        self.assertIn("iteration 1: verified=True", results.stdout)
        verification = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(verification["outcome"], goal["id"])
        self.assertIs(verification["verified"], True)
        self.assertIsNone(verification["plan"])
        self.assertIsNone(verification["step_id"])

    def test_outcome_check_fails_after_iteration_budget(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome",
            "start",
            "Fail bounded verifier",
            "--success",
            "command exits zero",
            "--verify",
            shell_py("import sys; sys.stdout.write('nope'); raise SystemExit(7)"),
            "--max-iterations",
            "1",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        checked = self.run_cli("outcome", "check")
        self.assertEqual(checked.returncode, 2, checked.stderr)
        self.assertIn("failed", checked.stdout)
        self.assertIn("nope", checked.stdout)
        outcome_dirs = [path for path in (state / "outcomes").iterdir() if path.is_dir()]
        self.assertEqual(len(outcome_dirs), 1)
        goal = self.read_json(outcome_dirs[0] / "goal.json")
        self.assertEqual(goal["status"], "failed")
        self.assertEqual(goal["iteration_count"], 1)

    def test_outcome_stop_clears_active_pointer(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome",
            "start",
            "Stop me",
            "--success",
            "manual stop",
            "--verify",
            shell_py("raise SystemExit(0)"),
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        stopped = self.run_cli("outcome", "stop", "--reason", "user changed scope")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertFalse((state / "outcomes" / "active").exists())


class TestVerify(CliTestCase):
    def test_run_passing_command_verified_exit_zero(self):
        state = self.init_workspace()
        result = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "exit zero works",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] VERIFIED: exit zero works (exit 0,", result.stdout)
        records = self.read_jsonl(state / "verifications.jsonl")
        record = records[-1]
        self.assertEqual(
            set(record.keys()),
            {"kind", "claim", "command", "exit_code", "duration_seconds",
             "stdout_tail", "stderr_tail", "verified", "timestamp", "plan",
             "step_id", "step_title", "step_status"},
        )
        self.assertEqual(record["kind"], "executed")
        self.assertEqual(record["claim"], "exit zero works")
        self.assertEqual(record["exit_code"], 0)
        self.assertIs(record["verified"], True)
        self.assertIsInstance(record["duration_seconds"], float)
        self.assertIsNone(record["plan"])
        self.assertIsNone(record["step_id"])
        self.assertIsNone(record["step_title"])
        self.assertIsNone(record["step_status"])

    def test_run_without_disable_var_unchanged_passing(self):
        state = self.init_workspace()
        result = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "still works",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] VERIFIED:", result.stdout)
        records = self.read_jsonl(state / "verifications.jsonl")
        self.assertEqual(len(records), 1)
        self.assertIs(records[-1]["verified"], True)

    def test_run_disabled_refuses_records_nothing_and_exits_two(self):
        state = self.init_workspace()
        log = state / "verifications.jsonl"
        # Record one passing run so we can prove the disabled run adds no line.
        seed = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "seed",
        )
        self.assertEqual(seed.returncode, 0, seed.stderr)
        lines_before = log.read_text(encoding="utf-8")
        count_before = len(self.read_jsonl(log))
        result = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "should not run",
            env_extra={"MYTHIFY_DISABLE_RUN": "1"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(VERIFY_RUN_DISABLED_MESSAGE, result.stderr)
        # Nothing executed, nothing recorded: the log is byte-for-byte unchanged.
        self.assertEqual(log.read_text(encoding="utf-8"), lines_before)
        self.assertEqual(len(self.read_jsonl(log)), count_before)

    def test_run_disabled_creates_no_log_when_absent(self):
        state = self.init_workspace()
        log = state / "verifications.jsonl"
        self.assertFalse(log.exists())
        result = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            env_extra={"MYTHIFY_DISABLE_RUN": "1"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(VERIFY_RUN_DISABLED_MESSAGE, result.stderr)
        self.assertFalse(log.exists())

    def test_run_failing_command_unverified_exit_two(self):
        state = self.init_workspace()
        command = shell_py("import sys; sys.stdout.write('boom'); raise SystemExit(3)")
        result = self.run_cli("verify", "run", command)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[FAIL] UNVERIFIED:", result.stdout)
        self.assertIn("(exit 3,", result.stdout)
        self.assertIn("--- stdout (tail) ---", result.stdout)
        self.assertIn("boom", result.stdout)
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(record["exit_code"], 3)
        self.assertIs(record["verified"], False)
        self.assertIsNone(record["claim"])
        self.assertIn("boom", record["stdout_tail"])

    def test_run_timeout_records_minus_one_and_exits_two(self):
        state = self.init_workspace()
        command = shell_py("import time; time.sleep(5)")
        result = self.run_cli("verify", "run", command, "--timeout", "1")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[FAIL] UNVERIFIED:", result.stdout)
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(record["kind"], "executed")
        self.assertEqual(record["exit_code"], -1)
        self.assertIs(record["verified"], False)
        self.assertIn("(timed out after 1 seconds)", record["stderr_tail"])

    def test_claim_records_attested_with_verified_null(self):
        state = self.init_workspace()
        result = self.run_cli("verify", "claim", "docs updated", "read the diff")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "[WARN] ATTESTED: docs updated "
            "(self-reported, not machine-checked; prefer verify run)",
            result.stdout,
        )
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(
            set(record.keys()),
            {"kind", "claim", "evidence", "verified", "timestamp", "plan",
             "step_id", "step_title", "step_status"},
        )
        self.assertEqual(record["kind"], "attested")
        self.assertEqual(record["claim"], "docs updated")
        self.assertEqual(record["evidence"], "read the diff")
        self.assertIsNone(record["verified"])
        self.assertIsNone(record["plan"])
        self.assertIsNone(record["step_id"])
        self.assertIsNone(record["step_title"])
        self.assertIsNone(record["step_status"])


class TestReflect(CliTestCase):
    def test_json_form(self):
        state = self.init_workspace()
        payload = json.dumps({
            "action": "ran the suite",
            "outcome": "success",
            "observation": "all green",
            "next": "ship it",
        })
        result = self.run_cli("reflect", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.read_jsonl(state / "reflections.jsonl")[-1]
        self.assertEqual(
            set(record.keys()),
            {"action", "outcome", "observation", "root_cause", "next",
             "lesson", "timestamp"},
        )
        self.assertEqual(record["action"], "ran the suite")
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(record["next"], "ship it")
        self.assertIsNone(record["root_cause"])
        self.assertIsNone(record["lesson"])

    def test_flags_form_with_root_cause(self):
        state = self.init_workspace()
        result = self.run_cli(
            "reflect",
            "--action", "deployed",
            "--outcome", "partial",
            "--observation", "one node lagged",
            "--next", "drain and retry",
            "--root-cause", "stale cache",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.read_jsonl(state / "reflections.jsonl")[-1]
        self.assertEqual(record["outcome"], "partial")
        self.assertEqual(record["root_cause"], "stale cache")

    def test_missing_required_key_fails(self):
        self.init_workspace()
        result = self.run_cli(
            "reflect",
            "--action", "tried a thing",
            "--outcome", "success",
            "--next", "another thing",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stderr)

    def test_bad_outcome_fails(self):
        self.init_workspace()
        result = self.run_cli(
            "reflect",
            "--action", "tried",
            "--outcome", "great",
            "--observation", "looked fine",
            "--next", "continue",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL]", result.stderr)

    def test_lesson_auto_recorded_as_project_lesson(self):
        state = self.init_workspace()
        result = self.run_cli(
            "reflect",
            "--action", "debugged flaky test",
            "--outcome", "failure",
            "--observation", "race in setup",
            "--next", "serialize fixtures",
            "--lesson", "Never share fixtures across workers",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        files = list((state / "lessons").glob("*.json"))
        self.assertEqual(len(files), 1)
        record = self.read_json(files[0])
        self.assertEqual(record["title"], "Never share fixtures across workers")
        self.assertEqual(record["tags"], ["auto-reflected"])
        listed = self.run_cli("lesson", "list", "--tag", "auto-reflected")
        self.assertIn("Never share fixtures across workers", listed.stdout)


class TestLogsCompact(CliTestCase):
    def test_logs_compact_archives_and_keeps_recent_records(self):
        state = self.init_workspace()
        for index in range(5):
            result = self.run_cli(
                "verify", "claim", "claim-{0}".format(index), "evidence"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        for index in range(3):
            result = self.run_cli(
                "reflect",
                "--action", "action-{0}".format(index),
                "--outcome", "success",
                "--observation", "observed",
                "--next", "continue",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_cli("logs", "compact", "--keep", "2", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        by_log = {item["log"]: item for item in payload["logs"]}
        self.assertEqual(by_log["verifications.jsonl"]["status"], "compacted")
        self.assertEqual(by_log["reflections.jsonl"]["status"], "compacted")
        self.assertTrue(by_log["verifications.jsonl"]["archived"])
        self.assertTrue(by_log["reflections.jsonl"]["archived"])

        verifications = self.read_jsonl(state / "verifications.jsonl")
        reflections = self.read_jsonl(state / "reflections.jsonl")
        self.assertEqual([item["claim"] for item in verifications], ["claim-3", "claim-4"])
        self.assertEqual([item["action"] for item in reflections], ["action-1", "action-2"])

        verification_archive = Path(by_log["verifications.jsonl"]["archive_path"])
        reflection_archive = Path(by_log["reflections.jsonl"]["archive_path"])
        self.assertTrue(verification_archive.exists())
        self.assertTrue(reflection_archive.exists())
        self.assertIn("claim-0", verification_archive.read_text(encoding="utf-8"))
        self.assertIn("action-0", reflection_archive.read_text(encoding="utf-8"))

    def test_logs_compact_dry_run_writes_nothing(self):
        state = self.init_workspace()
        for index in range(3):
            result = self.run_cli(
                "verify", "claim", "claim-{0}".format(index), "evidence"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        before = self.state_snapshot(state)

        result = self.run_cli("logs", "compact", "--keep", "1", "--dry-run", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        by_log = {item["log"]: item for item in payload["logs"]}
        self.assertEqual(by_log["verifications.jsonl"]["status"], "would_compact")
        self.assertFalse(by_log["verifications.jsonl"]["archived"])
        self.assertEqual(self.state_snapshot(state), before)

    def test_logs_compact_rejects_invalid_keep(self):
        self.init_workspace()
        result = self.run_cli("logs", "compact", "--keep", "0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] logs compact requires --keep >= 1.", result.stderr)


class TestStatusAndSummary(CliTestCase):
    def populate(self):
        self.init_workspace()
        steps = json.dumps([
            {"title": "Lay foundation", "success_criteria": "slab poured"},
            {"title": "Raise walls", "success_criteria": "walls up"},
        ])
        self.run_cli("plan", "create", "Build house", "--steps", steps)
        self.run_cli("step", "1", "completed", "slab inspected")
        self.run_cli("memory", "set", "site", "lot 7")
        self.run_cli("lesson", "add", "Order early", "Lumber lead times are long")
        self.run_cli("verify", "run", shell_py("raise SystemExit(0)"))
        self.run_cli("verify", "run", shell_py("raise SystemExit(1)"))
        self.run_cli("verify", "claim", "permits filed", "saw the receipt")
        self.run_cli(
            "reflect",
            "--action", "poured slab",
            "--outcome", "success",
            "--observation", "level within tolerance",
            "--next", "frame walls",
        )

    def test_status_includes_plan_icons_next_step_and_counts(self):
        self.populate()
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK]", result.stdout)
        self.assertIn("Active plan: build-house (1/2 completed)", result.stdout)
        self.assertIn("[x] 1. Lay foundation", result.stdout)
        self.assertIn("[ ] 2. Raise walls", result.stdout)
        self.assertIn("Next pending: 2. Raise walls (criteria: walls up)", result.stdout)
        self.assertIn(
            "Counts: memory 1, lessons 1 project + 0 global, "
            "verifications 3, reflections 1",
            result.stdout,
        )

    def test_summary_includes_expected_counts(self):
        self.populate()
        result = self.run_cli("summary")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK]", result.stdout)
        self.assertIn("build-house (active): 1/2 completed", result.stdout)
        self.assertIn("Memory entries: 1", result.stdout)
        self.assertIn("Lessons: 1 project, 0 global", result.stdout)
        self.assertIn(
            "Verifications: 2 executed (1 passed, 1 failed), 1 attested",
            result.stdout,
        )
        self.assertIn("Reflections: 1", result.stdout)


class TestCorruptRecovery(CliTestCase):
    def test_corrupt_memory_json_is_quarantined_with_warning(self):
        state = self.init_workspace()
        (state / "memory.json").write_text("{this is not json", encoding="utf-8")
        result = self.run_cli("memory", "get")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[WARN]", result.stderr)
        corrupt_files = list(state.glob("memory.json.corrupt-*"))
        self.assertEqual(len(corrupt_files), 1)

    def test_corrupt_memory_does_not_block_subsequent_writes(self):
        state = self.init_workspace()
        (state / "memory.json").write_text("[[[", encoding="utf-8")
        result = self.run_cli("memory", "set", "fresh", "start")
        self.assertEqual(result.returncode, 0, result.stderr)
        memory = self.read_json(state / "memory.json")
        self.assertEqual(memory["entries"][0]["key"], "fresh")


if __name__ == "__main__":
    unittest.main()
