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
SURFACE_MANIFEST = REPO_ROOT / "protocol" / "surface-manifest.json"
CLASSIFICATION_RULES = REPO_ROOT / "protocol" / "classification-rules.json"
WORKFLOW_ROUTER = REPO_ROOT / "protocol" / "workflow-router.json"

NO_WORKSPACE_MESSAGE = (
    "[FAIL] No .mythify workspace found. Run: python3 scripts/mythify.py init"
)
EVIDENCE_MESSAGE = (
    "[FAIL] Evidence required: pass a RESULT describing what proves this status."
)
VERIFIED_EVIDENCE_MESSAGE = (
    "[FAIL] Verified evidence required: strict evidence mode is enabled by "
    "default, but no passing 'verify run' was recorded since this step started. "
    "Run 'verify run' with a passing check first, or set "
    "MYTHIFY_REQUIRE_VERIFIED_STEP=0 to use legacy prose-only completion."
)
VERIFY_RUN_DISABLED_MESSAGE = (
    "[FAIL] verify run is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded. Unset it to enable execution, or use "
    "verify claim to record a self-reported attestation."
)
OUTCOME_CHECK_DISABLED_MESSAGE = (
    "[FAIL] outcome check is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded. Unset it to enable execution."
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
        env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
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
        self.assertEqual((self.project / ".gitignore").read_text(encoding="utf-8"), ".mythify/\n")

    def test_reinit_warns_and_exits_zero(self):
        self.init_workspace()
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[WARN]", result.stdout)
        self.assertEqual((self.project / ".gitignore").read_text(encoding="utf-8"), ".mythify/\n")

    def test_init_preserves_existing_gitignore_and_does_not_duplicate_state_entry(self):
        (self.project / ".gitignore").write_text("dist/\n", encoding="utf-8")
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.project / ".gitignore").read_text(encoding="utf-8"),
            "dist/\n.mythify/\n",
        )

        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.project / ".gitignore").read_text(encoding="utf-8"),
            "dist/\n.mythify/\n",
        )

    def test_init_with_mythify_dir_does_not_touch_project_gitignore(self):
        custom = self.project / "custom-state-dir"
        result = self.run_cli("init", env_extra={"MYTHIFY_DIR": str(custom)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((custom / "memory.json").is_file())
        self.assertFalse((self.project / ".gitignore").exists())

    def test_help_exits_zero(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        commands = self.read_json(SURFACE_MANIFEST)["surfaces"]["cli"]["commands"]
        for name in commands:
            self.assertIn(name, result.stdout)
        self.assertIn("Recommended front door:", result.stdout)
        self.assertIn('mythify route "TASK"', result.stdout)
        self.assertIn("Workflow primitives:", result.stdout)
        self.assertIn("Advanced surfaces:", result.stdout)
        self.assertIn("Labs surfaces:", result.stdout)
        self.assertIn("Strict evidence mode:", result.stdout)

    def test_version_exits_zero_without_workspace(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        help_result = self.run_cli("--help")
        self.assertIn(result.stdout.strip() + ":", help_result.stdout)
        self.assertEqual(result.stderr, "")


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
        shutil.copy2(
            CLASSIFICATION_RULES,
            self.project / "protocol" / "classification-rules.json",
        )
        shutil.copy2(
            WORKFLOW_ROUTER,
            self.project / "protocol" / "workflow-router.json",
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

    def test_classify_evaluate_and_assess_codebase_as_review(self):
        manifest = self.read_json(CLASSIFICATION_RULES)
        review_rules = next(
            entry for entry in manifest["task_types"] if entry["id"] == "review"
        )
        self.assertIn("evaluate", review_rules["terms"])
        self.assertIn("assess", review_rules["terms"])

        examples = (
            ("Evaluate the Mythify codebase and product", "evaluate"),
            ("Assess the Mythify codebase and product quality", "assess"),
        )
        for prompt, signal in examples:
            with self.subTest(prompt=prompt):
                result = self.run_cli("classify", prompt, "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["task_type"], "review")
                self.assertEqual(payload["risk"], "low")
                self.assertEqual(payload["ceremony"], "light")
                self.assertEqual(payload["execution_profile"], "fast")
                self.assertIn(signal, payload["signals"])

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

    def test_classify_defaults_workers_to_configured_host_platform(self):
        bin_dir = self.project / "bin"
        bin_dir.mkdir()
        for name in ("codex", "cursor-agent"):
            tool = bin_dir / name
            tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tool.chmod(0o755)

        base_env = {
            "PATH": str(bin_dir),
            "MYTHIFY_TRIAGE_ENGINE": "",
            "MYTHIFY_FANOUT_ENGINE": "",
            "MYTHIFY_HOST_PLATFORM": "codex-desktop",
            "CURSOR_SESSION_ID": "cursor-session-present",
        }
        codex = self.run_cli(
            "classify",
            "implement a feature",
            "--json",
            env_extra=base_env,
        )
        self.assertEqual(codex.returncode, 0, codex.stderr)
        codex_policy = json.loads(codex.stdout)["model_policy"]
        self.assertEqual(codex_policy["session"]["platform"], "codex-desktop")
        self.assertEqual(codex_policy["triage"]["engine"], "codex-cli")
        self.assertEqual(codex_policy["triage"]["engine_policy"], "platform_preferred")
        self.assertEqual(codex_policy["fanout_worker"]["engine"], "codex-cli")
        self.assertEqual(codex_policy["fanout_worker"]["engine_policy"], "platform_preferred")
        self.assertEqual(codex_policy["reviewer"]["engine"], "codex-cli")
        self.assertEqual(codex_policy["reviewer"]["engine_policy"], "platform_preferred")

        cursor_env = dict(base_env)
        cursor_env["MYTHIFY_HOST_PLATFORM"] = "cursor-desktop"
        cursor = self.run_cli(
            "classify",
            "implement a feature",
            "--json",
            env_extra=cursor_env,
        )
        self.assertEqual(cursor.returncode, 0, cursor.stderr)
        cursor_policy = json.loads(cursor.stdout)["model_policy"]
        self.assertEqual(cursor_policy["session"]["platform"], "cursor-desktop")
        self.assertEqual(cursor_policy["triage"]["engine"], "cursor-agent")
        self.assertEqual(cursor_policy["triage"]["engine_policy"], "platform_preferred")
        self.assertEqual(cursor_policy["fanout_worker"]["engine"], "cursor-agent")
        self.assertEqual(cursor_policy["fanout_worker"]["engine_policy"], "platform_preferred")

    def test_classify_keeps_explicit_fanout_engine_override(self):
        bin_dir = self.project / "bin"
        bin_dir.mkdir()
        codex = bin_dir / "codex"
        codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        codex.chmod(0o755)

        result = self.run_cli(
            "classify",
            "implement a feature",
            "--json",
            env_extra={
                "PATH": str(bin_dir),
                "MYTHIFY_TRIAGE_ENGINE": "",
                "MYTHIFY_HOST_PLATFORM": "codex-desktop",
                "MYTHIFY_FANOUT_ENGINE": "cursor-agent",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = json.loads(result.stdout)["model_policy"]
        self.assertEqual(policy["triage"]["engine"], "codex-cli")
        self.assertEqual(policy["triage"]["engine_policy"], "platform_preferred")
        self.assertEqual(policy["fanout_worker"]["engine"], "cursor-agent")
        self.assertEqual(policy["fanout_worker"]["engine_policy"], "env")

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
        provider_catalog = policy["provider_defaults"]["provider_catalog"]
        self.assertFalse(provider_catalog["api_provider"]["execution_enabled"])
        self.assertEqual(provider_catalog["api_provider"]["default_roles"], [])
        self.assertEqual(
            provider_catalog["host_cli"]["default_roles"],
            ["triage", "fanout_worker", "reviewer"],
        )
        self.assertEqual(
            provider_catalog["local_openai_compatible"]["evidence_status"],
            "model_output_not_verification",
        )
        adapter_interface = policy["provider_defaults"]["adapter_interface_contract"]
        self.assertEqual(adapter_interface["version"], 1)
        self.assertEqual(adapter_interface["status"], "metadata_supported")
        self.assertEqual(
            adapter_interface["execution_policy"],
            "metadata_shape_only_no_runtime_change",
        )
        self.assertEqual(
            adapter_interface["fallback_policy"],
            "no_implicit_cross_provider_fallback",
        )
        self.assertIn("execution_substrate", adapter_interface["lanes"])
        self.assertIn("agent_lifecycle", adapter_interface["lanes"])
        self.assertIn("evidence_status", adapter_interface["fields"])
        self.assertIn("guardrails", adapter_interface["fields"])
        role_assignment = policy["provider_defaults"]["role_assignment_contract"]
        self.assertEqual(role_assignment["version"], 1)
        self.assertEqual(role_assignment["status"], "metadata_supported")
        self.assertFalse(role_assignment["runtime_routing_changed"])
        self.assertEqual(
            role_assignment["fallback_policy"],
            "no_implicit_cross_provider_fallback",
        )
        self.assertEqual(
            role_assignment["execution_policy"],
            "metadata_shape_only_no_runtime_change",
        )
        self.assertEqual(
            role_assignment["roles"]["triage"]["eligible_adapter_lanes"],
            ["host", "model_provider", "custom_adapter"],
        )
        self.assertEqual(
            role_assignment["roles"]["reader"]["selected_provider"],
            "local_openai_compatible",
        )
        self.assertEqual(
            role_assignment["roles"]["reviewer"]["stronger_model_policy"],
            "explicit_opt_in_required",
        )
        self.assertTrue(
            role_assignment["roles"]["verifier"]["writes_state_allowed"]
        )
        self.assertFalse(
            role_assignment["roles"]["verifier"]["material_not_evidence_required"]
        )
        self.assertEqual(
            role_assignment["roles"]["remote_execution"]["execution_policy"],
            "guarded_explicit_acknowledgement_only",
        )
        self.assertIn(
            "execution_substrate",
            role_assignment["roles"]["remote_execution"]["eligible_adapter_lanes"],
        )
        self.assertEqual(
            role_assignment["roles"]["agent_lifecycle"]["execution_policy"],
            "probe_only_no_eval_or_deploy",
        )
        self.assertIn(
            "agent_lifecycle",
            role_assignment["roles"]["agent_lifecycle"]["eligible_adapter_lanes"],
        )
        api_contract = policy["provider_defaults"]["api_provider_contract"]
        custom_contract = policy["provider_defaults"]["custom_adapter_contract"]
        self.assertEqual(api_contract["status"], "metadata_supported")
        self.assertFalse(api_contract["execution_enabled"])
        self.assertTrue(api_contract["fanout_execution_enabled"])
        self.assertEqual(api_contract["fanout_engines"], ["anthropic", "openai"])
        self.assertEqual(
            api_contract["required_fanout_acknowledgements"],
            [
                "hosted_provider_billing_ack",
                "hosted_provider_data_ack",
                "hosted_provider_material_ack",
            ],
        )
        self.assertEqual(api_contract["fanout_audit_log"], ".mythify/provider-audit.jsonl")
        self.assertEqual(api_contract["fanout_output_material_status"], "material_not_verification")
        self.assertEqual(
            api_contract["billing_policy"],
            "explicit_provider_required",
        )
        self.assertIn("timeout_seconds", policy["provider_defaults"]["timeout_metadata_fields"])
        self.assertIn("cost_estimate_status", policy["provider_defaults"]["cost_metadata_fields"])
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
        self.assertEqual(custom_contract["execution_policy"], "explicit_only_no_hidden_fallback")
        self.assertTrue(custom_contract["command"]["execution_enabled"])
        self.assertEqual(
            custom_contract["command"]["command_env"],
            ["MYTHIFY_TRIAGE_COMMAND", "MYTHIFY_FANOUT_COMMAND"],
        )
        self.assertFalse(custom_contract["command"]["output_is_evidence"])
        self.assertFalse(custom_contract["http"]["execution_enabled"])
        self.assertEqual(
            custom_contract["http"]["base_url_env"],
            "MYTHIFY_CUSTOM_HTTP_BASE_URL",
        )
        self.assertIn("method_allowlist", custom_contract["http"]["required_before_execution"])
        self.assertEqual(providers["session"]["provider"], "host")
        self.assertEqual(providers["triage"]["provider"], "host_cli")
        self.assertEqual(providers["reader"]["provider"], "local_openai_compatible")
        self.assertEqual(providers["fanout_worker"]["provider"], "host_cli")
        self.assertEqual(providers["reviewer"]["provider"], "host_cli")
        self.assertEqual(providers["verifier"]["provider"], "local_command")
        self.assertEqual(
            providers["reviewer"]["provider_profile"]["control"],
            "bounded_worker",
        )
        self.assertEqual(
            providers["verifier"]["provider_profile"]["evidence_status"],
            "executed_verification",
        )
        self.assertEqual(policy["reader"]["provider"], "local_openai_compatible")
        self.assertEqual(policy["triage"]["timeout"]["timeout_seconds"], 120.0)
        self.assertEqual(
            policy["triage"]["timeout"]["timeout_source"],
            "triage_timeout_seconds_or_default",
        )
        self.assertEqual(policy["reader"]["timeout"]["timeout_seconds"], 30)
        self.assertEqual(policy["fanout_worker"]["timeout"]["timeout_seconds"], 600)
        self.assertEqual(policy["fanout_worker"]["cost"]["billing"], "host_cli_subscription_or_local_quota")
        self.assertEqual(policy["fanout_worker"]["cost"]["cost_estimate_status"], "not_estimated")
        self.assertIsNone(policy["fanout_worker"]["cost"]["cost_estimate_cents"])
        self.assertEqual(policy["verifier"]["cost"]["billing"], "local_compute")
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
        self.assertEqual(record["host_confirmation"]["requested_model"], "gpt-5.4")
        self.assertEqual(
            record["host_confirmation"]["user_reported_current_model"],
            "gpt-5.3-codex",
        )
        self.assertFalse(record["host_confirmation"]["current_model_confirmed"])
        self.assertEqual(record["host_confirmation"]["confirmed_current_model"], "")
        self.assertEqual(record["host_confirmation"]["confirmation_status"], "unsupported")
        self.assertEqual(record["host_confirmation"]["confirmation_source"], "none")
        self.assertEqual(
            record["host_confirmation"]["unsupported_reason"],
            "host_capability_cannot_confirm_current_model",
        )
        proof_paths = record["adapter_proof_scan"]["paths"]
        self.assertEqual(record["adapter_proof_scan"]["status"], "metadata_only")
        self.assertFalse(record["adapter_proof_scan"]["host_state_mutated"])
        self.assertFalse(record["adapter_proof_scan"]["verification_recorded"])
        self.assertTrue(record["adapter_proof_scan"]["material_not_evidence"])
        self.assertEqual(
            proof_paths["current_chat_model_apply"]["status"],
            "unsupported",
        )
        self.assertEqual(
            proof_paths["current_chat_model_confirm"]["status"],
            "unsupported",
        )
        self.assertEqual(proof_paths["new_thread_model_apply"]["status"], "supported")
        self.assertEqual(proof_paths["worker_model_apply"]["status"], "supported")
        self.assertEqual(proof_paths["thinking_apply"]["status"], "supported")
        self.assertTrue((state / "host-model.json").exists())

        status_json = self.run_cli("host-model", "status", "--json")
        self.assertEqual(status_json.returncode, 0, status_json.stderr)
        status_record = json.loads(status_json.stdout)
        self.assertEqual(status_record["switch_result"]["status"], "manual")
        self.assertEqual(status_record["host_confirmation"]["confirmation_status"], "unsupported")
        self.assertEqual(
            status_record["adapter_proof_scan"]["paths"]["current_chat_model_apply"]["status"],
            "unsupported",
        )
        status_text = self.run_cli("host-model", "status")
        self.assertEqual(status_text.returncode, 0, status_text.stderr)
        self.assertIn("switch status: manual", status_text.stdout)
        self.assertIn("current-chat confirmed: no", status_text.stdout)
        self.assertIn("host-confirmed model: unsupported", status_text.stdout)
        self.assertIn("confirmation source: none", status_text.stdout)
        self.assertIn("adapter proof scan: metadata_only", status_text.stdout)
        self.assertIn("current-chat apply proof: unsupported", status_text.stdout)
        self.assertIn("current-chat confirm proof: unsupported", status_text.stdout)
        self.assertIn("new-thread model proof: supported", status_text.stdout)
        self.assertIn("worker model proof: supported", status_text.stdout)
        self.assertIn("thinking proof: supported", status_text.stdout)
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
        self.assertEqual(record["host_confirmation"]["confirmation_status"], "unsupported")
        self.assertFalse(record["host_confirmation"]["current_model_confirmed"])
        self.assertEqual(record["adapter_proof_scan"]["status"], "metadata_only")
        self.assertEqual(
            record["adapter_proof_scan"]["paths"]["current_chat_model_apply"]["status"],
            "unsupported",
        )

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


class TestTraceAnalyze(CliTestCase):
    def write_jsonl(self, name, rows):
        path = self.project / name
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return path

    def test_trace_analyze_summarizes_session_action_and_scenario_rows(self):
        session = self.write_jsonl(
            "session.jsonl",
            [
                {
                    "session_id": "s1",
                    "harness": "claude_code",
                    "metadata": {
                        "model": "claude-fable-5",
                        "entrypoint": "cli",
                        "permission_mode": "bypassPermissions",
                    },
                    "num_tool_calls": 3,
                    "prompt": "Build a browser game and verify with screenshots.",
                    "messages": [
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "Bash",
                                        "arguments": {"command": "npm test && npm run build"},
                                    }
                                }
                            ],
                        }
                    ],
                }
            ],
        )
        actions = self.write_jsonl(
            "actions.jsonl",
            [
                {
                    "session": "s2",
                    "model": "claude-fable-5",
                    "output_type": "tool_use",
                    "context": "USER: Fix the React app.",
                    "completion": "I will run lint and inspect the error.",
                    "output": {
                        "tool": "Bash",
                        "input": {"command": "npm run lint && git status --short"},
                    },
                },
                {
                    "session": "s2",
                    "model": "claude-fable-5",
                    "output_type": "tool_use",
                    "context": "USER: Fix the React app.",
                    "output": {
                        "tool": "Edit",
                        "input": {"file_path": "src/App.tsx"},
                    },
                },
            ],
        )
        scenarios = self.write_jsonl(
            "scenarios.jsonl",
            [
                {
                    "instruction": "Deploy an AI application. Provide a practical plan.",
                    "input": "",
                    "output": "Containerize services, add logging, metrics, tests, and scaling notes.",
                    "prompt": "### Instruction: Deploy an AI application.",
                }
            ],
        )

        result = self.run_cli(
            "trace",
            "analyze",
            str(session),
            str(actions),
            str(scenarios),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["records_read"], 4)
        self.assertEqual(payload["format_counts"]["session_trace"], 1)
        self.assertEqual(payload["format_counts"]["action_row"], 2)
        self.assertEqual(payload["format_counts"]["scenario_row"], 1)
        tools = {item["name"]: item["count"] for item in payload["top_tools"]}
        self.assertEqual(tools["Bash"], 2)
        self.assertEqual(tools["Edit"], 1)
        self.assertEqual(payload["command_verification_hits"]["test"], 1)
        self.assertEqual(payload["command_verification_hits"]["build"], 1)
        self.assertEqual(payload["command_verification_hits"]["lint"], 1)
        self.assertEqual(payload["command_verification_hits"]["git"], 1)
        recommendation_ids = {item["id"] for item in payload["recommendations"]}
        self.assertIn("scenario-classifier-evals", recommendation_ids)
        self.assertIn("auto-evidence-detection", recommendation_ids)
        self.assertIn("action-first-runtime", recommendation_ids)

    def test_trace_analyze_text_output_works_without_workspace(self):
        path = self.write_jsonl(
            "vibe.jsonl",
            [
                {
                    "instruction": "Create a coding assistant. Provide a plan.",
                    "input": "",
                    "output": "Use project indexing, tests, and scaling considerations.",
                    "prompt": "### Instruction: Create a coding assistant.",
                }
            ],
        )
        result = self.run_cli("trace", "analyze", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Trace analysis", result.stdout)
        self.assertIn("scenario_row=1", result.stdout)
        self.assertIn("scenario rows", result.stdout)

    def test_trace_playbook_workflow_distills_compares_and_installs_skill(self):
        path = self.write_jsonl(
            "mixed-models.jsonl",
            [
                {
                    "session": "target-1",
                    "model": "claude-fable-5",
                    "output_type": "tool_use",
                    "output": {
                        "tool": "Read",
                        "input": {"file_path": "src/app.py"},
                    },
                    "completion": "Inspect the code before editing.",
                },
                {
                    "session": "target-1",
                    "model": "claude-fable-5",
                    "output_type": "tool_use",
                    "output": {
                        "tool": "Edit",
                        "input": {"file_path": "src/app.py"},
                    },
                    "completion": "Apply a focused fix.",
                },
                {
                    "session": "target-1",
                    "model": "claude-fable-5",
                    "output_type": "tool_use",
                    "output": {
                        "tool": "Bash",
                        "input": {"command": "pytest tests && npm run build"},
                    },
                    "completion": "Verify the edited surface.",
                },
                {
                    "session": "baseline-1",
                    "model": "opus-4.8",
                    "output_type": "tool_use",
                    "output": {
                        "tool": "Edit",
                        "input": {"file_path": "src/app.py"},
                    },
                    "completion": "Patch immediately.",
                },
                {
                    "session": "baseline-1",
                    "model": "opus-4.8",
                    "output_type": "tool_use",
                    "output": {
                        "tool": "Bash",
                        "input": {"command": "git status --short"},
                    },
                    "completion": "Check git status.",
                },
            ],
        )
        distill_path = self.project / "fable-profile.md"
        result = self.run_cli(
            "trace",
            "distill",
            str(path),
            "--model",
            "claude-fable-5",
            "--output",
            str(distill_path),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(distill_path.is_file())
        distill_text = distill_path.read_text(encoding="utf-8")
        self.assertIn("claude-fable-5", distill_text)
        self.assertIn("Read to edit ratio", distill_text)

        result = self.run_cli(
            "trace",
            "compare",
            str(path),
            "--target",
            "claude-fable-5",
            "--baseline",
            "opus-4.8",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["target"]["analysis"]["records_read"], 3)
        self.assertEqual(payload["baseline"]["analysis"]["records_read"], 2)
        self.assertIn("Target minus baseline", payload["markdown"])

        playbook_path = self.project / "MYTHIFY_FABLE_PLAYBOOK.md"
        result = self.run_cli(
            "trace",
            "playbook",
            str(path),
            "--target",
            "claude-fable-5",
            "--baseline",
            "opus-4.8",
            "--output",
            str(playbook_path),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        playbook_text = playbook_path.read_text(encoding="utf-8")
        self.assertIn("Trace-Derived Agent Playbook", playbook_text)
        self.assertIn("Completion requires an executed verifier", playbook_text)

        skill_root = self.project / "skills"
        result = self.run_cli(
            "trace",
            "install-playbook",
            str(playbook_path),
            "--skill",
            "mythify-fable",
            "--skill-root",
            str(skill_root),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        skill_file = skill_root / "mythify-fable" / "SKILL.md"
        self.assertTrue(skill_file.is_file())
        skill_text = skill_file.read_text(encoding="utf-8")
        self.assertIn("name: mythify-fable", skill_text)
        self.assertIn("Trace-Derived Agent Playbook", skill_text)

        result = self.run_cli(
            "trace",
            "install-playbook",
            str(playbook_path),
            "--skill",
            "mythify-fable",
            "--skill-root",
            str(skill_root),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("already exists", result.stderr)

        result = self.run_cli(
            "trace",
            "install-playbook",
            str(playbook_path),
            "--skill",
            "mythify-fable",
            "--skill-root",
            str(skill_root),
            "--force",
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class TestResearchWorkflow(CliTestCase):
    def test_research_records_sources_claims_questions_and_decision(self):
        state = self.init_workspace()
        result = self.run_cli(
            "research",
            "start",
            "Should Mythify add a research workflow?",
            "--name",
            "research-workflow",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id"], "research-workflow")
        self.assertEqual(payload["status"], "active")
        active = (state / "research" / "active").read_text(encoding="utf-8").strip()
        self.assertEqual(active, "research-workflow")

        result = self.run_cli(
            "research",
            "add-source",
            "Anthropic prompting notes",
            "--url",
            "https://example.test/prompting",
            "--note",
            "Shows source-backed behavior patterns.",
            "--credibility",
            "high",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("S1", result.stdout)

        result = self.run_cli(
            "research",
            "add-claim",
            "Research should distinguish material from verification.",
            "--evidence",
            "Source S1 describes guidance, not executed proof.",
            "--source",
            "S1",
            "--confidence",
            "high",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("C1", result.stdout)

        result = self.run_cli(
            "research",
            "add-question",
            "Should this become an MCP tool later?",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Q1", result.stdout)

        result = self.run_cli("research", "summary", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id"], "research-workflow")
        self.assertEqual(payload["sources"][0]["id"], "S1")
        self.assertEqual(payload["claims"][0]["source_id"], "S1")
        self.assertEqual(payload["open_questions"][0]["id"], "Q1")

        result = self.run_cli(
            "research",
            "close",
            "--decision",
            "Ship a CLI research surface first.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.read_json(state / "research" / "research-workflow.json")
        self.assertEqual(record["status"], "closed")
        self.assertEqual(record["decision"], "Ship a CLI research surface first.")
        self.assertFalse((state / "research" / "active").exists())

    def test_research_claim_rejects_unknown_source(self):
        self.init_workspace()
        result = self.run_cli("research", "start", "Check source validation")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "research",
            "add-claim",
            "Unsupported claim",
            "--evidence",
            "none",
            "--source",
            "S9",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Source not found", result.stderr)


class TestCampaignWorkflow(CliTestCase):
    def test_campaign_generates_tasks_advances_loop_and_records_learning(self):
        state = self.init_workspace()
        result = self.run_cli(
            "campaign",
            "start",
            "One shot a project",
            "--name",
            "one-shot-project",
            "--success",
            "All tasks complete with evidence.",
            "--verify",
            "python3 -m unittest discover -s tests",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id"], "one-shot-project")
        self.assertEqual(payload["current_task_id"], 1)
        self.assertGreaterEqual(len(payload["tasks"]), 5)
        self.assertEqual(payload["tasks"][0]["phase"], "understand")

        result = self.run_cli(
            "campaign",
            "add-task",
            "Polish the final report",
            "--criteria",
            "The report includes evidence and remaining risks.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Added task", result.stdout)

        for expected_phase in ("design", "build", "judge", "verify", "reflect"):
            result = self.run_cli(
                "campaign",
                "advance",
                "--result",
                "phase evidence for {0}".format(expected_phase),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            record = self.read_json(state / "campaigns" / "one-shot-project.json")
            self.assertEqual(record["tasks"][0]["phase"], expected_phase)

        result = self.run_cli(
            "campaign",
            "learn",
            "Prefer the smallest verifier before broad tests.",
            "--apply-next",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_cli(
            "campaign",
            "advance",
            "--result",
            "reflect evidence captured and next task can start",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.read_json(state / "campaigns" / "one-shot-project.json")
        self.assertEqual(record["tasks"][0]["status"], "completed")
        self.assertEqual(record["tasks"][1]["status"], "in_progress")
        self.assertEqual(record["tasks"][1]["phase"], "understand")
        self.assertEqual(record["learnings"][0]["lesson"], "Prefer the smallest verifier before broad tests.")

        result = self.run_cli("campaign", "status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id"], "one-shot-project")
        self.assertIn("next_action", payload)

    def test_campaign_task_completion_requires_evidence(self):
        self.init_workspace()
        tasks = json.dumps(["First task"])
        result = self.run_cli(
            "campaign",
            "start",
            "Evidence gated campaign",
            "--tasks",
            tasks,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("campaign", "task", "1", "completed")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Evidence required", result.stderr)
        result = self.run_cli(
            "campaign",
            "task",
            "1",
            "completed",
            "verify run exit 0",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Campaign", result.stdout)

    def test_campaign_prompt_and_watch_emit_next_host_prompt(self):
        state = self.init_workspace()
        tasks = json.dumps([
            {
                "title": "Build the first slice",
                "success_criteria": "A verified slice exists.",
            }
        ])
        result = self.run_cli(
            "campaign",
            "start",
            "One shot a useful project",
            "--name",
            "project-shot",
            "--tasks",
            tasks,
            "--success",
            "All work is verified.",
            "--verify",
            "python3 -m unittest discover -s tests",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_cli(
            "campaign",
            "learn",
            "Keep prompt output visible in chat.",
            "--apply-next",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        before = self.read_json(state / "campaigns" / "project-shot.json")
        result = self.run_cli("campaign", "prompt", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id"], "project-shot")
        self.assertEqual(payload["phase"], "understand")
        self.assertEqual(payload["current_task"]["title"], "Build the first slice")
        self.assertIn("Continue Mythify campaign: project-shot", payload["next_prompt"])
        self.assertIn("Current task 1: Build the first slice", payload["next_prompt"])
        self.assertIn("mythify campaign advance project-shot", payload["next_prompt"])
        self.assertIn("steering material", payload["guardrail"])

        result = self.run_cli(
            "campaign",
            "watch",
            "--max-iterations",
            "2",
            "--interval",
            "0",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        watch = json.loads(result.stdout)
        self.assertEqual(watch["campaign"], "project-shot")
        self.assertEqual(len(watch["iterations"]), 2)
        self.assertEqual(watch["iterations"][0]["next_prompt"], payload["next_prompt"])
        self.assertEqual(watch["iterations"][1]["phase"], "understand")

        after = self.read_json(state / "campaigns" / "project-shot.json")
        self.assertEqual(before["current_task_id"], after["current_task_id"])
        self.assertEqual(before["tasks"][0]["phase"], after["tasks"][0]["phase"])
        self.assertEqual(before["tasks"][0]["status"], after["tasks"][0]["status"])


class TestPromptPackets(CliTestCase):
    def test_prompt_packets_render_read_only_chat_workflows(self):
        state = self.init_workspace()
        result = self.run_cli(
            "research",
            "start",
            "How should prompt packets guide implementation?",
            "--name",
            "packet-direction",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "research",
            "add-source",
            "Trace notes",
            "--note",
            "Shows research to implementation transitions.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "research",
            "add-claim",
            "Prompt packets should be material for direction.",
            "--evidence",
            "Research records are not executable evidence.",
            "--source",
            "S1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "research",
            "add-question",
            "Which verifier should prove the implementation?",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "research",
            "close",
            "--decision",
            "Implement one shared prompt packet contract.",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        steps = json.dumps([
            {"title": "Design packet", "success_criteria": "packet shape is explicit"},
            {"title": "Verify packet", "success_criteria": "packet tests pass"},
        ])
        result = self.run_cli("plan", "create", "Ship packet workflow", "--steps", steps)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("step", "1", "in_progress")
        self.assertEqual(result.returncode, 0, result.stderr)

        tasks = json.dumps([
            {
                "title": "Build packet loop",
                "success_criteria": "Host prompt is visible.",
            }
        ])
        result = self.run_cli(
            "campaign",
            "start",
            "One shot packet work",
            "--name",
            "packet-campaign",
            "--tasks",
            tasks,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        before = self.state_snapshot(state)
        cases = [
            (
                ("prompt", "research", "packet-direction", "--goal", "Ship packet workflow", "--json"),
                "research",
                "research",
                "Research to implementation prompt packet",
                "Decision: Implement one shared prompt packet contract.",
            ),
            (
                ("prompt", "analysis", "--goal", "Ship packet workflow", "--json"),
                "analysis",
                "analysis",
                "Analysis prompt packet",
                "Produce or update a plan",
            ),
            (
                ("prompt", "handoff", "--json"),
                "handoff",
                "handoff",
                "Handoff prompt packet",
                "Resume from this packet",
            ),
            (
                ("prompt", "review", "--json"),
                "review",
                "review",
                "Review prompt packet",
                "Review changed files",
            ),
            (
                ("prompt", "campaign", "--json"),
                "campaign",
                "campaign",
                "Campaign prompt packet",
                "Continue Mythify campaign: packet-campaign",
            ),
        ]
        for args, kind, selected, title, expected in cases:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["kind"], kind)
            self.assertEqual(payload["selected_kind"], selected)
            self.assertEqual(payload["title"], title)
            self.assertIn(expected, payload["next_prompt"])
            self.assertIn("not verification evidence", payload["guardrail"])
        self.assertEqual(before, self.state_snapshot(state))

        result = self.run_cli(
            "verify",
            "run",
            shell_py("import sys; sys.exit(3)"),
            "--claim",
            "packet failure demo",
        )
        self.assertEqual(result.returncode, 2)
        result = self.run_cli("prompt", "failure", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], "failure")
        self.assertEqual(payload["context"]["failed_verification"]["exit_code"], 3)
        self.assertIn("packet failure demo", payload["next_prompt"])

        result = self.run_cli("prompt", "next", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], "next")
        self.assertEqual(payload["selected_kind"], "failure")

        result = self.run_cli(
            "verify",
            "run",
            shell_py("import sys; sys.exit(0)"),
            "--claim",
            "packet failure recovered",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("prompt", "next", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], "next")
        self.assertNotEqual(payload["selected_kind"], "failure")


class TestWorkflowRouter(CliTestCase):
    def test_route_selects_workflow_without_mutating_state(self):
        state = self.init_workspace()
        before = self.state_snapshot(state)
        cases = [
            ("what does Mythify do?", "direct", "analysis"),
            ("research latest agent routing patterns", "research", "research"),
            ("audit this project for issues", "review", "review"),
            ("address all issues in one go", "campaign", "campaign"),
            ("keep fixing until tests pass and verify command is green", "outcome", "handoff"),
            ("implement the router feature", "plan", "analysis"),
        ]
        for task, route, packet in cases:
            result = self.run_cli("route", task, "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["kind"], "workflow_route")
            self.assertEqual(payload["route"], route)
            self.assertEqual(payload["prompt_packet"]["kind"], packet)
            self.assertIn("next_command", payload)
            self.assertIn("verification_strategy", payload)
            self.assertEqual(payload["chat_policy"]["executor"], "initiating_host")
            self.assertFalse(payload["evidence"][-1]["mutates_state"])
            self.assertIn("not verification evidence", payload["guardrail"])
        self.assertEqual(before, self.state_snapshot(state))

    def test_route_resumes_active_plan_and_prioritizes_failed_verification(self):
        state = self.init_workspace()
        steps = json.dumps([
            {"title": "Build route", "success_criteria": "route is implemented"},
        ])
        result = self.run_cli("plan", "create", "Ship router", "--steps", steps)
        self.assertEqual(result.returncode, 0, result.stderr)

        before = self.state_snapshot(state)
        result = self.run_cli("route", "continue", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["route"], "handoff")
        self.assertEqual(payload["state"]["active_plan"]["id"], "ship-router")
        self.assertEqual(payload["prompt_packet"]["kind"], "handoff")
        self.assertEqual(before, self.state_snapshot(state))

        result = self.run_cli(
            "verify",
            "run",
            shell_py("import sys; sys.exit(5)"),
            "--claim",
            "router failure demo",
        )
        self.assertEqual(result.returncode, 2)

        before = self.state_snapshot(state)
        result = self.run_cli("route", "research a new direction", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["route"], "failure")
        self.assertEqual(payload["state"]["latest_executed_verification"]["exit_code"], 5)
        self.assertEqual(payload["prompt_packet"]["kind"], "failure")
        self.assertEqual(before, self.state_snapshot(state))


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
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        verified = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "first step verified",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
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
        self.assertEqual(self.run_cli("step", "1", "in_progress").returncode, 0)
        self.assertEqual(
            self.run_cli(
                "verify", "run", shell_py("raise SystemExit(0)"),
                "--claim", "step one verified",
            ).returncode,
            0,
        )
        self.run_cli("step", "1", "completed", "done one")
        self.assertEqual(self.run_cli("step", "2", "in_progress").returncode, 0)
        self.assertEqual(
            self.run_cli(
                "verify", "run", shell_py("raise SystemExit(0)"),
                "--claim", "step two verified",
            ).returncode,
            0,
        )
        result = self.run_cli("step", "2", "completed", "done two")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No pending steps remain.", result.stdout)

    def test_gate_opt_out_plain_result_still_completes(self):
        # Legacy compatibility: with the gate explicitly disabled, a non-empty
        # RESULT alone marks the step completed, exactly as before.
        state, plan_file = self.make_plan()
        result = self.run_cli(
            "step", "1", "completed", "all tests passed",
            env_extra={"MYTHIFY_REQUIRE_VERIFIED_STEP": "0"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "completed")

    def test_default_gate_without_verification_refused_and_plan_unmodified(self):
        state, plan_file = self.make_plan()
        before = plan_file.read_text(encoding="utf-8")
        result = self.run_cli(
            "step", "1", "completed", "I promise it works",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, result.stderr)
        self.assertEqual(plan_file.read_text(encoding="utf-8"), before)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "pending")

    def test_gate_on_rejects_pre_step_unbound_verification(self):
        state, plan_file = self.make_plan()
        verified = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "global pre-step verification",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertIsNone(record["plan"])
        self.assertIsNone(record["step_id"])

        before = plan_file.read_text(encoding="utf-8")
        result = self.run_cli(
            "step", "1", "completed", "global verification should not count",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, result.stderr)
        self.assertEqual(plan_file.read_text(encoding="utf-8"), before)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "pending")

    def test_gate_on_accepts_legacy_verification_without_context_keys(self):
        state, plan_file = self.make_plan()
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        plan = self.read_json(plan_file)
        legacy_record = {
            "kind": "executed",
            "claim": "legacy step verification",
            "command": "true",
            "exit_code": 0,
            "duration_seconds": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "verified": True,
            "timestamp": plan["steps"][0]["updated_at"],
        }
        with open(str(state / "verifications.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(legacy_record) + "\n")

        result = self.run_cli(
            "step", "1", "completed", "legacy verification record",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "completed")

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
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertIn("Next pending", result.stdout)

    def test_gate_on_accepts_cross_runtime_timestamp_formats_within_same_second(self):
        state, plan_file = self.make_plan()
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        plan = self.read_json(plan_file)
        plan["steps"][0]["updated_at"] = "2026-06-15T18:26:24.862Z"
        plan["last_updated"] = plan["steps"][0]["updated_at"]
        plan_file.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        record = {
            "kind": "executed",
            "claim": "python verifier format after node step format",
            "command": "true",
            "exit_code": 0,
            "duration_seconds": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "verified": True,
            "timestamp": "2026-06-15T18:26:24+00:00",
            "plan": "step-plan",
            "step_id": 1,
            "step_title": "Do first thing",
            "step_status": "in_progress",
        }
        with open(str(state / "verifications.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

        result = self.run_cli(
            "step", "1", "completed", "cross-runtime timestamp formats compare",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.read_json(plan_file)
        self.assertEqual(plan["steps"][0]["status"], "completed")

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
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, result.stderr)
        self.assertEqual(plan_file.read_text(encoding="utf-8"), before)

    def test_gate_on_does_not_block_failed_status(self):
        # The gate applies only to completed. Recording a failure is always
        # allowed, even with strict mode on and no passing verification.
        state, plan_file = self.make_plan()
        result = self.run_cli("step", "1", "failed", "ran out of time")
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

    def test_outcome_check_disabled_refuses_records_nothing_and_exits_two(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome",
            "start",
            "Disabled outcome check",
            "--success",
            "python exits zero",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--metric",
            shell_py("import sys; sys.stdout.write('99')"),
            "--name",
            "disabled-outcome",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        outcome_dir = state / "outcomes" / "disabled-outcome"
        goal_before = (outcome_dir / "goal.json").read_text(encoding="utf-8")
        self.assertFalse((outcome_dir / "iterations.jsonl").exists())
        self.assertFalse((state / "verifications.jsonl").exists())

        checked = self.run_cli(
            "outcome",
            "check",
            env_extra={"MYTHIFY_DISABLE_RUN": "1"},
        )
        self.assertEqual(checked.returncode, 2)
        self.assertIn(OUTCOME_CHECK_DISABLED_MESSAGE, checked.stderr)
        self.assertEqual((outcome_dir / "goal.json").read_text(encoding="utf-8"), goal_before)
        self.assertFalse((outcome_dir / "iterations.jsonl").exists())
        self.assertFalse((state / "verifications.jsonl").exists())

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
        self.run_cli("step", "1", "in_progress")
        self.run_cli("verify", "run", shell_py("raise SystemExit(0)"), "--claim", "slab inspected")
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
            "verifications 4, reflections 1",
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
            "Verifications: 3 executed (2 passed, 1 failed), 1 attested",
            result.stdout,
        )
        self.assertIn("Reflections: 1", result.stdout)

    def test_dashboard_includes_plan_evidence_and_reflections(self):
        self.populate()
        result = self.run_cli("dashboard")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Workflow dashboard", result.stdout)
        self.assertIn("Active plan: build-house (1/2 completed)", result.stdout)
        self.assertIn("Next pending: 2. Raise walls (criteria: walls up)", result.stdout)
        self.assertIn("Evidence: 3 executed (2 passed, 1 failed), 1 attested", result.stdout)
        self.assertIn("Recent verification:", result.stdout)
        self.assertIn("Recent reflection:", result.stdout)

        json_result = self.run_cli("dashboard", "--json", "--recent", "1")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["active_plan"]["slug"], "build-house")
        self.assertEqual(payload["verification_summary"]["executed_passed"], 2)
        self.assertEqual(len(payload["verification_summary"]["recent"]), 1)
        self.assertEqual(payload["reflection_summary"]["recent"][0]["next"], "frame walls")

    def test_history_shows_verification_records_without_mutation(self):
        self.populate()
        state = self.project / ".mythify"
        before = self.state_snapshot(state)

        result = self.run_cli("history", "--recent", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Verification history", result.stdout)
        self.assertIn("Evidence: 3 executed (2 passed, 1 failed), 1 attested, 4 total", result.stdout)
        self.assertIn("attested: permits filed", result.stdout)
        self.assertIn("failed:", result.stdout)
        self.assertIn("passed:", result.stdout)
        self.assertIn("Guardrail: history displays recorded evidence only", result.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        json_result = self.run_cli("history", "--json", "--recent", "3")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["counts"]["executed_passed"], 2)
        self.assertEqual(payload["counts"]["executed_failed"], 1)
        self.assertEqual(payload["counts"]["attested"], 1)
        self.assertEqual([row["verdict"] for row in payload["records"]], ["attested", "failed", "passed"])

    def test_report_shows_chat_updates_and_advances_cursor(self):
        self.populate()
        state = self.project / ".mythify"
        before = self.state_snapshot(state)

        peek = self.run_cli("report", "--since", "start", "--peek", "--recent", "10")
        self.assertEqual(peek.returncode, 0, peek.stderr)
        self.assertIn("[OK] Live work report", peek.stdout)
        self.assertIn("Plan created: build-house", peek.stdout)
        self.assertIn("Step completed: 1. Lay foundation", peek.stdout)
        self.assertIn("Attention:", peek.stdout)
        self.assertIn("issue: Verification failed:", peek.stdout)
        self.assertIn("warning: Verification attested: permits filed", peek.stdout)
        self.assertIn("Verification passed:", peek.stdout)
        self.assertIn("Reflection success:", peek.stdout)
        self.assertIn("Cursor unchanged: --peek", peek.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        first = self.run_cli("report", "--since", "last", "--cursor", "chat", "--recent", "10")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Cursor advanced: chat", first.stdout)
        self.assertTrue((state / "reports" / "chat.json").exists())

        second = self.run_cli("report", "--since", "last", "--cursor", "chat", "--peek")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("No new Mythify events to report.", second.stdout)

        marked = self.run_cli("report", "--cursor", "fresh-chat", "--mark")
        self.assertEqual(marked.returncode, 0, marked.stderr)
        self.assertIn("Scope: mark cursor fresh-chat, 0 new events", marked.stdout)
        self.assertIn("Cursor is ready. Future reports with --since last will show only new events.", marked.stdout)
        self.assertIn("Cursor marked at latest event: fresh-chat", marked.stdout)
        self.assertNotIn("No new Mythify events to report.", marked.stdout)

        marked_second = self.run_cli("report", "--since", "last", "--cursor", "fresh-chat", "--peek")
        self.assertEqual(marked_second.returncode, 0, marked_second.stderr)
        self.assertIn("No new Mythify events to report.", marked_second.stdout)

        invalid = self.run_cli("report", "--mark", "--peek")
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("--mark cannot be combined with --peek", invalid.stderr)

        invalid_since = self.run_cli("report", "--mark", "--since", "last")
        self.assertEqual(invalid_since.returncode, 1)
        self.assertIn("--mark cannot be combined with --since", invalid_since.stderr)

        json_result = self.run_cli(
            "report",
            "--since",
            "start",
            "--format",
            "json",
            "--peek",
            "--recent",
            "3",
        )
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["cursor"], "default")
        self.assertEqual(payload["shown_event_count"], 3)
        self.assertGreaterEqual(payload["new_event_count"], 6)
        self.assertEqual(payload["attention_event_count"], 2)
        self.assertEqual(
            [row["level"] for row in payload["attention_events"]],
            ["issue", "warning"],
        )

    def test_background_includes_outcomes_and_fanout_jobs_without_mutation(self):
        state = self.init_workspace()
        start = self.run_cli(
            "outcome",
            "start",
            "Ship the background view",
            "--success",
            "python exits zero",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--max-iterations",
            "2",
            "--name",
            "ship-background-view",
        )
        self.assertEqual(start.returncode, 0, start.stderr)
        checked = self.run_cli("outcome", "check", "ship-background-view")
        self.assertEqual(checked.returncode, 0, checked.stderr)

        job_id = "fo-20260613121212-abcd"
        job_dir = state / "fanout" / job_id
        job_dir.mkdir(parents=True)
        job = {
            "id": job_id,
            "created": "2026-06-13T12:12:12+00:00",
            "last_updated": "2026-06-13T12:12:13+00:00",
            "purpose": "Map existing background task state",
            "engine": "command",
            "model": "",
            "visibility": "summary",
            "tasks": [
                {
                    "id": 1,
                    "title": "Map fanout files",
                    "status": "completed",
                    "role": "worker",
                    "engine": "command",
                    "duration_seconds": 1.2,
                    "error": None,
                },
                {
                    "id": 2,
                    "title": "Watch outcome loop",
                    "status": "running",
                    "role": "worker",
                    "engine": "command",
                    "duration_seconds": 0,
                    "error": None,
                },
            ],
        }
        (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")

        before = self.state_snapshot(state)
        result = self.run_cli("background", "--recent", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Background tasks", result.stdout)
        self.assertIn("Outcomes: 1 total", result.stdout)
        self.assertIn("Active outcome: ship-background-view (succeeded, 1/2 iterations)", result.stdout)
        self.assertIn("Fanout jobs: 1 total; 1 active", result.stdout)
        self.assertIn(job_id, result.stdout)
        self.assertIn("Map fanout files", result.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        json_result = self.run_cli("background", "--json")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["active_outcome"]["id"], "ship-background-view")
        self.assertEqual(payload["counts"]["fanout_tasks"]["running"], 1)
        self.assertEqual(payload["fanout_jobs"][0]["id"], job_id)

    def test_progress_includes_outcome_iteration_details_without_mutation(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome",
            "start",
            "Ship the progress view",
            "--success",
            "python exits zero",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--metric",
            shell_py("import sys; sys.stdout.write('7.25')"),
            "--max-iterations",
            "2",
            "--name",
            "ship-progress-view",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        checked = self.run_cli("outcome", "check", "ship-progress-view")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        active = self.run_cli(
            "outcome",
            "start",
            "Watch progress budget",
            "--success",
            "manual follow-up",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--max-iterations",
            "3",
            "--name",
            "watch-progress-budget",
        )
        self.assertEqual(active.returncode, 0, active.stderr)

        before = self.state_snapshot(state)
        result = self.run_cli("progress", "--recent", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Outcome progress", result.stdout)
        self.assertIn("Outcomes: 2 total; 1 active, 1 succeeded, 0 failed, 0 stopped", result.stdout)
        self.assertIn("Active outcome: watch-progress-budget (active, 0/3 iterations, 3 remaining)", result.stdout)
        self.assertIn("ship-progress-view", result.stdout)
        self.assertIn("verifier: iteration 1, exit 0, verified=True", result.stdout)
        self.assertIn("metric: exit 0, score 7.25", result.stdout)
        self.assertIn("Guardrail: progress displays recorded outcome verifier results only", result.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        json_result = self.run_cli("progress", "--json", "--recent", "2")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["counts"]["active"], 1)
        self.assertEqual(payload["counts"]["succeeded"], 1)
        self.assertEqual(payload["active_outcome"]["id"], "watch-progress-budget")
        by_id = {row["id"]: row for row in payload["outcomes"]}
        self.assertEqual(by_id["ship-progress-view"]["last_check"]["metric_score"], 7.25)
        self.assertEqual(by_id["watch-progress-budget"]["iterations_remaining"], 3)
        self.assertEqual(self.state_snapshot(state), before)

    def test_readiness_maps_recorded_gates_without_mutation(self):
        state = self.init_workspace()
        (self.project / "roadmap.md").write_text(
            "## Active Now\n\n- [>] Release readiness view.\n",
            encoding="utf-8",
        )
        seeded = self.run_cli(
            "verify",
            "run",
            shell_py("raise SystemExit(0)"),
            "--claim",
            "Python suite passes for release readiness",
        )
        self.assertEqual(seeded.returncode, 0, seeded.stderr)

        before = self.state_snapshot(state)
        result = self.run_cli("readiness")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Release readiness", result.stdout)
        self.assertIn("Readiness: needs_evidence", result.stdout)
        self.assertIn("Python test suite: passed", result.stdout)
        self.assertIn("Node MCP suite: missing", result.stdout)
        self.assertIn("Project git: [~] unknown", result.stdout)
        self.assertIn("Roadmap: [x] present; - [>] Release readiness view.", result.stdout)
        self.assertIn("Guardrail: readiness summarizes recorded evidence", result.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        json_result = self.run_cli("readiness", "--json")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["status"], "needs_evidence")
        self.assertEqual(payload["counts"]["passed"], 1)
        self.assertEqual(payload["counts"]["missing"], 9)
        self.assertEqual(payload["project_state"]["roadmap"]["status"], "present")
        self.assertEqual(payload["project_state"]["git"]["status"], "unknown")
        self.assertEqual(self.state_snapshot(state), before)

    def test_timeline_includes_fanout_worker_events_without_mutation(self):
        state = self.init_workspace()
        job_id = "fo-20260613141414-abcd"
        job_dir = state / "fanout" / job_id
        job_dir.mkdir(parents=True)
        job = {
            "id": job_id,
            "created": "2026-06-13T14:14:14+00:00",
            "last_updated": "2026-06-13T14:14:20+00:00",
            "purpose": "Build a timeline",
            "engine": "command",
            "model": "",
            "visibility": "summary",
            "tasks": [
                {
                    "id": 1,
                    "title": "Write timeline",
                    "status": "completed",
                    "role": "worker",
                    "engine": "command",
                    "started_at": "2026-06-13T14:14:15+00:00",
                    "finished_at": "2026-06-13T14:14:18+00:00",
                    "duration_seconds": 3.0,
                    "error": None,
                    "output_file": "task-1-output.md",
                    "output_bytes": 42,
                },
                {
                    "id": 2,
                    "title": "Review timeline",
                    "status": "failed",
                    "role": "reviewer",
                    "engine": "command",
                    "started_at": "2026-06-13T14:14:16+00:00",
                    "finished_at": "2026-06-13T14:14:20+00:00",
                    "duration_seconds": 4.0,
                    "error": "review failed",
                    "output_file": "task-2-output.md",
                    "output_bytes": 0,
                },
                {
                    "id": 3,
                    "title": "Wait for follow-up",
                    "status": "pending",
                    "role": "worker",
                    "engine": "command",
                    "duration_seconds": 0,
                    "error": None,
                },
            ],
        }
        (job_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")

        before = self.state_snapshot(state)
        result = self.run_cli("timeline", "--recent", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Fanout timeline", result.stdout)
        self.assertIn("Fanout jobs: 1 total; 1 active, 0 completed, 0 failed", result.stdout)
        self.assertIn("job created (Build a timeline)", result.stdout)
        self.assertIn("Write timeline (completed; engine=command; duration=3.0s; output=42 bytes)", result.stdout)
        self.assertIn("Review timeline (failed; engine=command; duration=4.0s): review failed", result.stdout)
        self.assertIn("Wait for follow-up (pending; engine=command)", result.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        json_result = self.run_cli("timeline", "--json")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        event_names = [event["event"] for event in payload["events"]]
        self.assertIn("job_created", event_names)
        self.assertIn("task_started", event_names)
        self.assertIn("task_finished", event_names)
        self.assertIn("task_failed", event_names)
        self.assertIn("task_pending", event_names)
        self.assertEqual(payload["counts"]["timeline_events"], 6)

    def test_phase_groups_plan_steps_and_evidence_without_mutation(self):
        state = self.init_workspace()
        steps = json.dumps([
            {"title": "Map current state", "success_criteria": "inputs known"},
            {"title": "Design phase view", "success_criteria": "contract written"},
            {"title": "Implement phase view", "success_criteria": "command works"},
            {"title": "Review phase output", "success_criteria": "shape is honest"},
            {"title": "Verify phase view", "success_criteria": "tests pass"},
        ])
        created = self.run_cli("plan", "create", "Ship phase view", "--steps", steps)
        self.assertEqual(created.returncode, 0, created.stderr)
        in_progress_first = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress_first.returncode, 0, in_progress_first.stderr)
        verified_first = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "phase inputs mapped",
        )
        self.assertEqual(verified_first.returncode, 0, verified_first.stderr)
        completed = self.run_cli("step", "1", "completed", "inputs mapped")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        in_progress = self.run_cli("step", "2", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        self.assertEqual(self.run_cli("memory", "set", "surface", "phase").returncode, 0)
        self.assertEqual(
            self.run_cli("lesson", "add", "Keep views read-only", "Status views must not mutate").returncode,
            0,
        )
        self.assertEqual(self.run_cli("verify", "run", shell_py("raise SystemExit(0)")).returncode, 0)
        reflected = self.run_cli(
            "reflect",
            "--action", "reviewed phase view",
            "--outcome", "success",
            "--observation", "phase buckets are scan-friendly",
            "--next", "run focused tests",
        )
        self.assertEqual(reflected.returncode, 0, reflected.stderr)

        before = self.state_snapshot(state)
        result = self.run_cli("phase", "--recent", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Phase view", result.stdout)
        self.assertIn("Active plan: ship-phase-view (1/5 completed)", result.stdout)
        self.assertIn("[x] Understand: completed; 1 plan steps", result.stdout)
        self.assertIn("[>] Design: in_progress; 1 plan steps", result.stdout)
        self.assertIn("[ ] Build: pending; 1 plan steps", result.stdout)
        self.assertIn("[ ] Judge: pending; 1 plan steps", result.stdout)
        self.assertIn("[ ] Verify: pending; 1 plan steps", result.stdout)
        self.assertIn("Guardrail: phase view summarizes durable state only", result.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        json_result = self.run_cli("phase", "--json")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        phases = {phase["id"]: phase for phase in payload["phases"]}
        self.assertEqual(phases["understand"]["status"], "completed")
        self.assertEqual(phases["design"]["status"], "in_progress")
        self.assertEqual(phases["verify"]["step_counts"]["pending"], 1)
        self.assertEqual(payload["counts"]["verifications"], 2)

    def test_read_only_views_have_stable_empty_state_shapes(self):
        state = self.init_workspace()
        (self.project / "roadmap.md").write_text(
            "## Active Now\n\n- [x] No open roadmap items remain.\n",
            encoding="utf-8",
        )
        before = self.state_snapshot(state)
        views = [
            (
                "dashboard",
                ["[OK] Workflow dashboard", "Active plan: none", "Evidence: 0 executed"],
                ["state_dir", "active_plan", "active_outcome", "counts", "verification_summary"],
            ),
            (
                "history",
                [
                    "[OK] Verification history",
                    "No verification records found.",
                    "Guardrail: history displays recorded evidence only",
                ],
                ["state_dir", "records", "counts", "guardrail"],
            ),
            (
                "background",
                ["[OK] Background tasks", "Active outcome: none", "No background tasks found."],
                ["state_dir", "active_outcome", "outcomes", "fanout_jobs", "counts"],
            ),
            (
                "progress",
                [
                    "[OK] Outcome progress",
                    "Active outcome: none",
                    "Guardrail: progress displays recorded outcome verifier results only",
                ],
                ["state_dir", "active_outcome", "outcomes", "counts", "guardrail"],
            ),
            (
                "readiness",
                [
                    "[OK] Release readiness",
                    "Recorded gates:",
                    "Guardrail: readiness summarizes recorded evidence",
                ],
                ["state_dir", "status", "gates", "counts", "project_state", "guardrail"],
            ),
            (
                "timeline",
                [
                    "[OK] Fanout timeline",
                    "No fanout timeline events found.",
                    "Guardrail: timeline summarizes durable fanout state only",
                ],
                ["state_dir", "jobs", "events", "counts", "guardrail"],
            ),
            (
                "phase",
                [
                    "[OK] Phase view",
                    "Active plan: none",
                    "Phases:",
                    "Guardrail: phase view summarizes durable state only",
                ],
                ["state_dir", "active_plan", "active_outcome", "phases", "counts", "guardrail"],
            ),
        ]

        for command, expected_text, expected_json_keys in views:
            with self.subTest(command=command, mode="text"):
                result = self.run_cli(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                for expected in expected_text:
                    self.assertIn(expected, result.stdout)
                self.assertEqual(self.state_snapshot(state), before)

            with self.subTest(command=command, mode="json"):
                result = self.run_cli(command, "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                for key in expected_json_keys:
                    self.assertIn(key, payload)
                self.assertEqual(Path(payload["state_dir"]).resolve(), state.resolve())
                self.assertEqual(self.state_snapshot(state), before)


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
