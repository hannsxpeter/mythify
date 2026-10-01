"""Unit and end-to-end tests for scripts/mythify.py.

Every test invokes the CLI as a subprocess with sys.executable and a scrubbed
environment: MYTHIFY_DIR removed and HOME pointed at a per-test temp directory
so the real global lessons store is never touched. The working directory is a
per-test temp project directory.
"""

import json
import hashlib
import importlib.util
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
PY_CLASSIFICATION = REPO_ROOT / "scripts" / "mythify_classification.py"
PY_GODFILES = REPO_ROOT / "scripts" / "mythify_godfiles.py"
PY_IO = REPO_ROOT / "scripts" / "mythify_io.py"
PY_MEMORY = REPO_ROOT / "scripts" / "mythify_memory.py"
PY_OUTCOMES = REPO_ROOT / "scripts" / "mythify_outcomes.py"
PY_PARSER = REPO_ROOT / "scripts" / "mythify_parser.py"
PY_ROUTER = REPO_ROOT / "scripts" / "mythify_router.py"
PY_VIEWS = REPO_ROOT / "scripts" / "mythify_views.py"
CLASSIFICATION_RULES = REPO_ROOT / "protocol" / "classification-rules.json"
WORKFLOW_ROUTER = REPO_ROOT / "protocol" / "workflow-router.json"

NO_WORKSPACE_MESSAGE = (
    "[FAIL] No .mythify workspace found. Run: mythify init"
)
EVIDENCE_MESSAGE = (
    "[FAIL] Evidence required: pass a RESULT describing what proves this status."
)
VERIFIED_EVIDENCE_MESSAGE = (
    "[FAIL] Verified evidence required: strict evidence mode is enabled by "
    "default, but no passing executed 'verify run' with exit code 0 was recorded "
    "since this step started. When the step stores a verify_command, the recorded "
    "command must match it. Run the step's verifier first, or set "
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


def load_cli_module():
    spec = importlib.util.spec_from_file_location("mythify_cli_under_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        for kept in ("outcomes", "maps", "reports", "reviews", "verification-artifacts"):
            self.assertTrue((state / kept).is_dir(), kept)
        for removed in ("research", "campaigns", "designs", "evals", "fanout"):
            self.assertFalse((state / removed).exists(), removed)
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
        spec = importlib.util.spec_from_file_location("mythify_cli_help_under_test", CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parser = module.build_cli_parser(vars(module))
        commands = next(
            action.choices for action in parser._actions if getattr(action, "choices", None)
        )
        self.assertIn("mcp", commands)
        for name in commands:
            self.assertIn(name, result.stdout)
        self.assertIn("Recommended front door:", result.stdout)
        self.assertIn('mythify route "TASK"', result.stdout)
        self.assertIn("Workflow primitives:", result.stdout)
        self.assertIn("Advanced surfaces:", result.stdout)
        self.assertNotIn("Labs surfaces:", result.stdout)
        self.assertIn("Strict evidence mode:", result.stdout)

    def test_version_exits_zero_without_workspace(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        help_result = self.run_cli("--help")
        self.assertIn(result.stdout.strip() + ":", help_result.stdout)
        self.assertEqual(result.stderr, "")


class TestDurableIo(unittest.TestCase):
    def load_io_module(self):
        spec = importlib.util.spec_from_file_location(
            "mythify_io_under_test",
            PY_IO,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_atomic_text_write_fsyncs_file_before_replace_and_parent_after(self):
        mythify = self.load_io_module()
        tmp = Path(tempfile.mkdtemp(prefix="mythify-atomic-test-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        target = tmp / "state.json"
        events = []
        real_fsync = mythify.os.fsync
        real_replace = mythify.os.replace

        def fake_fsync(fd):
            events.append("fsync")

        def wrapped_replace(src, dst):
            events.append("replace")
            real_replace(src, dst)

        mythify.os.fsync = fake_fsync
        mythify.os.replace = wrapped_replace
        try:
            mythify._write_text_atomic(target, "{\"ok\": true}\n")
        finally:
            mythify.os.fsync = real_fsync
            mythify.os.replace = real_replace

        self.assertEqual(target.read_text(encoding="utf-8"), "{\"ok\": true}\n")
        self.assertEqual(events[:3], ["fsync", "replace", "fsync"])

    def test_read_jsonl_since_uses_tail_window_for_recent_records(self):
        mythify = self.load_io_module()
        mythify.configure_durable_io(
            timestamp_at_or_after_func=lambda value, lower, allow=False: value >= lower
        )
        tmp = Path(tempfile.mkdtemp(prefix="mythify-tail-test-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        log = tmp / "verifications.jsonl"
        old = {"timestamp": "2020-01-01T00:00:00+00:00", "kind": "executed"}
        new = {"timestamp": "2026-01-01T00:00:00+00:00", "kind": "executed"}
        log.write_text(
            "{bad-json" + ("x" * (mythify.JSONL_TAIL_CHUNK_BYTES + 1024)) + "\n"
            + json.dumps(old) + "\n"
            + json.dumps(new) + "\n",
            encoding="utf-8",
        )

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            records = mythify.read_jsonl_since(log, "2025-01-01T00:00:00+00:00")

        self.assertEqual(records, [new])
        self.assertEqual(stderr.getvalue(), "")


class TestMemoryStore(unittest.TestCase):
    def load_memory_module(self):
        scripts_dir = str(REPO_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        spec = importlib.util.spec_from_file_location(
            "mythify_memory_under_test",
            PY_MEMORY,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_memory_and_lesson_helpers_import_directly(self):
        mythify = self.load_memory_module()
        timestamp = "2026-06-16T00:00:00+00:00"
        mythify.configure_memory_store(
            now_iso_func=lambda: timestamp,
            now_stamp_func=lambda: "20260616000000",
            slugify_func=lambda text: str(text).strip().lower().replace(" ", "-"),
        )
        state = Path(tempfile.mkdtemp(prefix="mythify-memory-test-"))
        self.addCleanup(shutil.rmtree, str(state), True)

        memory = mythify.default_memory()
        self.assertEqual(memory["metadata"]["created"], timestamp)
        mythify.save_memory(state, memory)
        loaded = mythify.load_memory(state)
        self.assertEqual(loaded["metadata"]["total_entries"], 0)

        args = SimpleNamespace(
            key="project",
            value="Mythify",
            category=mythify.MEMORY_DEFAULT_CATEGORY,
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = mythify.cmd_memory_set(args, state)
        self.assertEqual(result, 0)
        self.assertIn("[OK] Stored memory entry: project", stdout.getvalue())
        self.assertEqual(mythify.load_memory(state)["entries"][0]["value"], "Mythify")

        path = mythify.write_lesson(state / "lessons", "Useful Lesson", "Keep it small", ["test"])
        self.assertTrue(path.name.startswith("useful-lesson-"))
        lessons = mythify.load_lessons(state / "lessons", "project")
        self.assertEqual(lessons[0][1]["detail"], "Keep it small")


class TestOutcomeStore(unittest.TestCase):
    def load_outcomes_module(self):
        scripts_dir = str(REPO_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        spec = importlib.util.spec_from_file_location(
            "mythify_outcomes_under_test",
            PY_OUTCOMES,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_outcome_helpers_import_directly(self):
        mythify = self.load_outcomes_module()
        timestamp = "2026-06-16T00:00:00+00:00"

        def find_by_name(state, name, path_func):
            return name if path_func(state, name).exists() else None

        mythify.configure_outcome_loops(
            find_existing_slug_by_name_func=find_by_name,
            now_iso_func=lambda: timestamp,
            slugify_func=lambda text: str(text).strip().lower().replace(" ", "-"),
            run_shell_capture_func=lambda command, timeout: {
                "command": command,
                "exit_code": 0,
                "duration_seconds": 0.01,
                "stdout_tail": "42",
                "stderr_tail": "",
                "verified": True,
            },
            verification_step_context_func=lambda state: {"plan": None, "step_id": None},
        )
        state = Path(tempfile.mkdtemp(prefix="mythify-outcomes-test-"))
        self.addCleanup(shutil.rmtree, str(state), True)
        goal = {
            "id": "ship-outcome",
            "goal": "Ship outcome helper",
            "success_criteria": "tests pass",
            "verify_command": "python3 -m unittest",
            "metric_command": "",
            "max_iterations": 2,
            "iteration_count": 0,
            "allowed_paths": [],
            "status": "active",
            "created": timestamp,
            "updated": timestamp,
        }
        mythify.save_outcome(state, "ship-outcome", goal)
        mythify.set_active_outcome_slug(state, "ship-outcome")
        slug, loaded = mythify.load_outcome(state)
        self.assertEqual(slug, "ship-outcome")
        self.assertEqual(loaded["goal"], "Ship outcome helper")
        self.assertEqual(mythify.parse_allowed_paths("a,b, c "), ["a", "b", "c"])
        self.assertEqual(mythify.parse_metric_score("score=41.5"), 41.5)
        self.assertIn("Outcome ship-outcome", mythify.format_outcome_status(slug, loaded))


class TestPromptRouter(unittest.TestCase):
    def load_router_module(self):
        scripts_dir = str(REPO_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        spec = importlib.util.spec_from_file_location(
            "mythify_router_under_test",
            PY_ROUTER,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_handoff_packet_and_route_selection_import_directly(self):
        mythify = self.load_router_module()
        plan = {
            "goal": "Ship router module",
            "steps": [
                {"id": 1, "title": "Design", "status": "completed"},
                {
                    "id": 2,
                    "title": "Verify",
                    "status": "pending",
                    "success_criteria": "tests pass",
                },
            ],
        }

        mythify.configure_prompt_router(
            get_active_slug_func=lambda state: "ship-router-module",
            load_plan_func=lambda state, slug: plan,
            plan_progress_func=lambda value: (1, 2),
            next_pending_step_func=lambda value: value["steps"][1],
            read_jsonl_func=lambda path: [],
            build_verification_history_view_func=lambda state, recent=5: {
                "records": [
                    {
                        "verdict": "verified",
                        "claim": "router tests pass",
                        "exit_code": 0,
                        "timestamp": "2026-06-16T00:00:00+00:00",
                    }
                ]
            },
            verification_label_func=lambda row: row.get("claim", ""),
            git_status_summary_func=lambda root: {
                "branch": "main",
                "status": "clean",
                "detail": "working tree clean",
                "changed_paths": [],
            },
            compact_report_detail_func=lambda text: text,
            build_work_report_func=lambda *args, **kwargs: {"events": [], "attention_events": []},
            load_outcome_func=lambda state: (None, None),
        )

        packet = mythify.build_prompt_packet("handoff", Path("."), goal="Ship router module")
        self.assertEqual(packet["kind"], "handoff")
        self.assertIn("Active plan: ship-router-module", packet["next_prompt"])
        self.assertIn("Continue the current step", packet["next_prompt"])
        for removed in ("analysis", "research", "campaign"):
            self.assertIn("error", mythify.build_prompt_packet(removed, Path(".")))
        route, reason = mythify.select_workflow_route(
            "continue",
            {
                "active_plan": {"id": "ship-router-module"},
                "latest_executed_verification": None,
            },
            {"execution_profile": "standard", "task_type": "feature"},
        )
        self.assertEqual(route, "handoff")
        self.assertIn("active plan", reason)

class TestReadOnlyViews(unittest.TestCase):
    def load_views_module(self):
        scripts_dir = str(REPO_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        spec = importlib.util.spec_from_file_location(
            "mythify_views_under_test",
            PY_VIEWS,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_status_and_report_import_directly(self):
        mythify = self.load_views_module()
        timestamp = "2026-06-16T00:00:00+00:00"
        plan = {
            "goal": "Ship views module",
            "created": timestamp,
            "steps": [
                {
                    "id": 1,
                    "title": "Design views",
                    "status": "completed",
                    "success_criteria": "module boundary is clear",
                    "result": "done",
                    "updated_at": timestamp,
                },
                {
                    "id": 2,
                    "title": "Verify views",
                    "status": "in_progress",
                    "success_criteria": "tests pass",
                    "updated_at": timestamp,
                },
            ],
        }
        mythify.configure_views(
            get_active_slug_func=lambda state: "views-module",
            load_plan_func=lambda state, slug: plan,
            plan_progress_func=lambda value: (1, 2),
            next_pending_step_func=lambda value: None,
            describe_next_pending_func=lambda value: "No pending steps remain.",
            load_memory_func=lambda state: {"entries": [{"key": "views", "value": "ready"}]},
            load_lessons_func=lambda directory, scope: [{"title": scope}],
            global_lessons_dir_func=lambda: Path("/tmp/mythify-global-lessons"),
            list_plan_slugs_func=lambda state: ["views-module"],
            format_step_line_func=lambda step, indent="  ": (
                "{0}{1}. {2}".format(indent, step.get("id"), step.get("title"))
            ),
            timestamp_sort_key_func=lambda value: value or "",
            timestamp_after_func=lambda value, lower: str(value or "") > str(lower or ""),
            now_iso_func=lambda: timestamp,
            slugify_func=lambda text: str(text).strip().lower().replace(" ", "-"),
        )
        state = Path(tempfile.mkdtemp(prefix="mythify-views-test-"))
        self.addCleanup(shutil.rmtree, str(state), True)
        (state / "verifications.jsonl").write_text(
            json.dumps(
                {
                    "kind": "executed",
                    "verified": True,
                    "claim": "views tests pass",
                    "command": "python3 -m unittest discover -s tests",
                    "exit_code": 0,
                    "timestamp": timestamp,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        status = mythify.build_status_view(state, recent=1)
        self.assertEqual(status["active_plan"]["id"], "views-module")
        self.assertEqual(status["active_plan"]["current_step"]["title"], "Verify views")
        self.assertEqual(status["active_plan"]["lineage"]["status"], "unknown")
        self.assertEqual(status["evidence"]["executed_passed"], 1)
        self.assertEqual(len(status["evidence"]["recent"]), 1)
        self.assertIn("Verify views", status["next_action"])
        self.assertIn("Status:", mythify.format_status_view(status))

        report = mythify.build_work_report(
            state,
            since="start",
            recent=5,
            cursor="views",
            peek=True,
        )
        self.assertTrue(any(event["kind"] == "verification_passed" for event in report["events"]))
        self.assertFalse((state / "reports" / "views.json").exists())

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
        shutil.copy2(
            REPO_ROOT / "protocol" / "PROTOCOL.md",
            self.project / "protocol" / "PROTOCOL.md",
        )
        for module in sorted((REPO_ROOT / "scripts").glob("mythify*.py")):
            shutil.copy2(module, self.project / "scripts" / module.name)
        for manifest in sorted((REPO_ROOT / "protocol").glob("*.json")):
            shutil.copy2(manifest, self.project / "protocol" / manifest.name)
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
    """Deterministic classification core, reached through `route --json`."""

    def classify(self, task):
        result = self.run_cli("route", task, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["classification"]

    def test_classification_module_imports_directly(self):
        spec = importlib.util.spec_from_file_location(
            "mythify_classification_under_test",
            PY_CLASSIFICATION,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        payload = module.classify_task_text("audit authentication token permissions")
        self.assertEqual(payload["task_type"], "security")
        self.assertEqual(payload["risk"], "high")
        self.assertEqual(payload["ceremony"], "full")
        self.assertEqual(payload["execution_profile"], "full")
        self.assertEqual(payload["framing"]["level"], "full")
        self.assertEqual(payload["parallelism"]["chooser"], "host")
        self.assertTrue(payload["review"]["independent"])
        for removed in (
            "fanout",
            "fanout_reason",
            "fanout_visibility",
            "model_triage",
            "model_triage_reason",
            "model_policy",
        ):
            self.assertNotIn(removed, payload)

    def test_classification_policy_manifest_contains_shared_decision_facts(self):
        manifest = self.read_json(CLASSIFICATION_RULES)
        self.assertEqual(manifest["schema_version"], 4)
        self.assertEqual(manifest["thresholds"]["trivial_word_count"], 12)
        self.assertIn("what ", manifest["question_prefixes"])
        self.assertIn("better", manifest["vague_request_terms"])
        self.assertIn("release", manifest["risk"]["high_task_types"])
        self.assertIn("review", manifest["ceremony"]["light_low_risk_task_types"])
        self.assertIn("benchmark", manifest["parallelism"]["strong_task_types"])
        self.assertIn("multiple files", manifest["parallelism"]["possible_terms"])
        self.assertIn("debugging", manifest["framing"]["full_task_types"])
        self.assertIn("bugfix", manifest["framing"]["light_task_types"])
        self.assertIn("independent_reason", manifest["review"])
        self.assertIn("at the level of", manifest["quality_climb"]["terms"])
        self.assertIn("bugfix", manifest["execution_profile"]["fast_task_types"])
        self.assertIn("standard", manifest["next_actions"])
        self.assertIn("feature", manifest["verification_hints"])
        for removed in ("fanout", "fanout_visibility", "model_triage", "plan_archetype"):
            self.assertNotIn(removed, manifest)

    def test_route_text_reports_classification_without_workspace(self):
        result = self.run_cli(
            "route",
            "benchmark the bare agent against mythify across tasks",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Workflow route", result.stdout)
        self.assertIn("Classification: type=benchmark; risk=medium", result.stdout)
        self.assertIn("profile=full", result.stdout)
        self.assertIn(
            "Advisories: framing=full; parallelism=strong (chooser: host); "
            "independent review=yes",
            result.stdout,
        )
        self.assertFalse((self.project / ".mythify").exists())

    def test_classification_json_for_question(self):
        payload = self.classify("what does this project do?")
        self.assertEqual(payload["task_type"], "question")
        self.assertEqual(payload["risk"], "low")
        self.assertEqual(payload["ceremony"], "none")
        self.assertEqual(payload["execution_profile"], "direct")
        self.assertEqual(payload["framing"]["level"], "none")
        self.assertEqual(
            payload["parallelism"],
            {
                "fit": "none",
                "reason": payload["parallelism"]["reason"],
                "chooser": "host",
            },
        )
        self.assertFalse(payload["review"]["independent"])

    def test_classification_quality_climb_advisory(self):
        task = "Build the landing page at the level of Linear, utterly perfect, AAA polish"
        payload = self.classify(task)
        self.assertEqual(payload["quality_climb"], "detected")
        self.assertIn("harsh critic", payload["quality_climb_protocol"])
        self.assertIn("brake", payload["quality_climb_protocol"])
        self.assertIn("whatever subagents the host offers", payload["quality_climb_protocol"])
        text = self.run_cli("route", task)
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("Quality climb: ", text.stdout)
        plain = self.classify("Fix the failing parser test")
        self.assertEqual(plain["quality_climb"], "not_detected")
        self.assertEqual(plain["quality_climb_protocol"], "")

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
                payload = self.classify(prompt)
                self.assertEqual(payload["task_type"], "review")
                self.assertEqual(payload["risk"], "low")
                self.assertEqual(payload["ceremony"], "light")
                self.assertEqual(payload["execution_profile"], "fast")
                self.assertEqual(payload["parallelism"]["fit"], "strong")
                self.assertIn(signal, payload["signals"])

    def test_vague_short_request_needs_full_framing(self):
        payload = self.classify("make this better")
        self.assertEqual(payload["task_type"], "feature")
        self.assertEqual(payload["ambiguity"], "high")
        self.assertEqual(payload["execution_profile"], "standard")
        self.assertEqual(payload["framing"]["level"], "full")
        self.assertIn("underspecified", payload["framing"]["reason"])

    def test_high_impact_ambiguous_work_names_the_high_impact_reason(self):
        payload = self.classify("delete it from production")
        self.assertEqual(payload["risk"], "high")
        self.assertEqual(payload["ambiguity"], "high")
        self.assertEqual(payload["framing"]["level"], "full")
        self.assertIn("High-impact", payload["framing"]["reason"])

    def test_parallelism_fit_levels_leave_the_choice_to_the_host(self):
        cases = (
            ("compare these three implementation approaches", "strong"),
            ("run the lint checks in parallel", "strong"),
            ("implement the export feature with validation", "possible"),
            ("fix word_count.py so python3 -m unittest passes", "none"),
        )
        for task, fit in cases:
            with self.subTest(task=task):
                parallelism = self.classify(task)["parallelism"]
                self.assertEqual(parallelism["fit"], fit)
                self.assertEqual(parallelism["chooser"], "host")
                self.assertTrue(parallelism["reason"])

    def test_independent_review_only_for_high_risk_or_full_ceremony(self):
        self.assertTrue(self.classify("rotate the production credential")["review"]["independent"])
        self.assertTrue(self.classify("benchmark the parser")["review"]["independent"])
        quiet = self.classify("fix word_count.py so python3 -m unittest passes")["review"]
        self.assertFalse(quiet["independent"])
        self.assertIn("self-check", quiet["reason"])

    def test_classify_focused_bugfix_uses_fast_profile(self):
        payload = self.classify("fix word_count.py so python3 -m unittest passes")
        self.assertEqual(payload["task_type"], "bugfix")
        self.assertEqual(payload["execution_profile"], "fast")
        self.assertNotIn("plan_archetype", payload)
        self.assertEqual(payload["framing"]["level"], "light")
        self.assertIn("fast profile", payload["next_action"])

    def test_classify_uses_word_boundaries_for_security_terms(self):
        payload = self.classify("create author profile page")
        self.assertNotEqual(payload["task_type"], "security")
        self.assertNotEqual(payload["risk"], "high")

    def test_classify_security_authentication_work(self):
        payload = self.classify("audit authentication token permissions")
        self.assertEqual(payload["task_type"], "security")
        self.assertEqual(payload["risk"], "high")
        self.assertEqual(payload["ceremony"], "full")
        self.assertEqual(payload["execution_profile"], "full")


class TestPromptPackets(CliTestCase):
    def test_prompt_packets_render_read_only_chat_workflows(self):
        state = self.init_workspace()
        no_plan = self.run_cli("prompt", "handoff", "--goal", "Ship packet workflow", "--json")
        self.assertEqual(no_plan.returncode, 0, no_plan.stderr)
        planning_prompt = json.loads(no_plan.stdout)["next_prompt"]
        # Without an active plan, the handoff packet carries the planning
        # guidance, including the hard-to-reverse labeled-approaches trap.
        self.assertIn("Produce or update a plan", planning_prompt)
        self.assertIn("2-3 labeled approaches with tradeoffs", planning_prompt)
        self.assertIn("looks good but is not", planning_prompt)

        steps = json.dumps([
            {"title": "Design packet", "success_criteria": "packet shape is explicit"},
            {"title": "Verify packet", "success_criteria": "packet tests pass"},
        ])
        result = self.run_cli("plan", "create", "Ship packet workflow", "--steps", steps)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("step", "1", "in_progress")
        self.assertEqual(result.returncode, 0, result.stderr)

        before = self.state_snapshot(state)
        cases = [
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
                "Read the diff, the changed symbols",
            ),
            (
                ("prompt", "next", "--json"),
                "next",
                "handoff",
                "Next workflow prompt packet",
                "Selected next packet: handoff",
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
            self.assertIn(
                "Before delivering user-facing prose, remove boilerplate and vague claims",
                payload["next_prompt"],
            )
            self.assertIn("not verification evidence", payload["guardrail"])
        self.assertEqual(before, self.state_snapshot(state))
        for removed in ("research", "analysis", "campaign"):
            result = self.run_cli("prompt", removed)
            self.assertEqual(result.returncode, 2, removed)
            self.assertIn("invalid choice", result.stderr)

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
        # High-stakes guidance: hard-to-reverse fixes ask for labeled variants.
        self.assertIn("2-3 labeled approaches with tradeoffs", payload["next_prompt"])

        review = self.run_cli("prompt", "review", "--json")
        self.assertEqual(review.returncode, 0, review.stderr)
        review_prompt = json.loads(review.stdout)["next_prompt"]
        self.assertIn("2-3 labeled approaches with tradeoffs", review_prompt)
        # Critic protocol: blind comparison against a named reference, graded
        # on the integrated deliverable, with verdicts kept as material.
        self.assertIn("judge blind", review_prompt)
        self.assertIn("integrated deliverable", review_prompt)
        self.assertIn(
            "Critic verdicts are material, not verification evidence",
            review_prompt,
        )

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
            ("what does Mythify do?", "direct", "next"),
            ("research latest agent routing patterns", "direct", "next"),
            ("audit this project for issues", "review", "review"),
            ("address all issues in one go", "plan", "handoff"),
            ("keep fixing until tests pass and verify command is green", "outcome", "handoff"),
            ("implement the router feature", "plan", "handoff"),
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
            if route == "plan":
                self.assertNotIn("--horizon", payload["next_command"])
                self.assertNotIn("--archetype", payload["next_command"])
            self.assertEqual(payload["loop_fit"]["kind"], "loop_fit")
            self.assertNotIn("execution_adapter", payload)
            self.assertNotIn("model_policy", payload["classification"])
            self.assertEqual(payload["classification"]["parallelism"]["chooser"], "host")
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
        self.assertEqual(payload["route"], "failure_recovery")
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

    def test_explicit_lookup_names_cannot_escape_state_subdirectories(self):
        state = self.init_workspace()
        for dirname in ("research", "campaigns", "outcomes"):
            (state / dirname).mkdir(parents=True, exist_ok=True)

        (self.project / "outside-plan.json").write_text(
            json.dumps({
                "name": "outside-plan",
                "goal": "Outside plan sentinel",
                "steps": [],
            }),
            encoding="utf-8",
        )
        (self.project / "outside-research.json").write_text(
            json.dumps({
                "question": "Outside research sentinel",
                "status": "active",
                "sources": [],
                "claims": [],
                "open_questions": [],
            }),
            encoding="utf-8",
        )
        (self.project / "outside-campaign.json").write_text(
            json.dumps({
                "goal": "Outside campaign sentinel",
                "status": "active",
                "tasks": [],
            }),
            encoding="utf-8",
        )
        outside_outcome = self.project / "outside-outcome"
        outside_outcome.mkdir()
        (outside_outcome / "goal.json").write_text(
            json.dumps({
                "goal": "Outside outcome sentinel",
                "status": "active",
                "success_criteria": "not loaded",
                "verify_command": "true",
            }),
            encoding="utf-8",
        )

        cases = [
            (("plan", "show", "../../outside-plan"), "Outside plan sentinel"),
            (("outcome", "status", "../../outside-outcome"), "Outside outcome sentinel"),
        ]
        for args, sentinel in cases:
            with self.subTest(command=" ".join(args)):
                result = self.run_cli(*args)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 1, output)
                self.assertIn("[FAIL]", output)
                self.assertNotIn(sentinel, output)

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

    def test_stored_verify_command_rejects_unrelated_and_inconsistent_records(self):
        state = self.init_workspace()
        steps = json.dumps([{"title": "Bound step", "verify_command": "true"}])
        created = self.run_cli("plan", "create", "Bound plan", "--steps", steps)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertEqual(self.run_cli("step", "1", "in_progress").returncode, 0)
        unrelated = self.run_cli("verify", "run", "printf unrelated")
        self.assertEqual(unrelated.returncode, 0, unrelated.stderr)
        refused = self.run_cli("step", "1", "completed", "wrong verifier")
        self.assertEqual(refused.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, refused.stderr)

        plan = self.read_json(state / "plans" / "bound-plan.json")
        inconsistent = {
            "kind": "executed",
            "command": "true",
            "exit_code": 9,
            "verified": True,
            "timestamp": plan["steps"][0]["updated_at"],
            "plan": "bound-plan",
            "step_id": 1,
        }
        with open(str(state / "verifications.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(inconsistent) + "\n")
        refused = self.run_cli("step", "1", "completed", "inconsistent verifier")
        self.assertEqual(refused.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, refused.stderr)

        expected = self.run_cli("verify", "run", "true")
        self.assertEqual(expected.returncode, 0, expected.stderr)
        completed = self.run_cli("step", "1", "completed", "expected verifier")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_restarting_step_invalidates_prior_same_second_verification(self):
        self.init_workspace()
        steps = json.dumps([{"title": "Restarted", "verify_command": "true"}])
        self.assertEqual(
            self.run_cli("plan", "create", "Restart cursor", "--steps", steps).returncode,
            0,
        )
        self.assertEqual(self.run_cli("step", "1", "in_progress").returncode, 0)
        self.assertEqual(self.run_cli("verify", "run", "true").returncode, 0)
        self.assertEqual(self.run_cli("step", "1", "pending").returncode, 0)
        self.assertEqual(self.run_cli("step", "1", "in_progress").returncode, 0)
        refused = self.run_cli("step", "1", "completed", "old evidence")
        self.assertEqual(refused.returncode, 1)
        self.assertIn(VERIFIED_EVIDENCE_MESSAGE, refused.stderr)
        self.assertEqual(self.run_cli("verify", "run", "true").returncode, 0)
        self.assertEqual(
            self.run_cli("step", "1", "completed", "fresh evidence").returncode,
            0,
        )

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

    def test_memory_categories_default_and_clear_refusal(self):
        state = self.init_workspace()
        categories = ["fact", "decision", "discovery", "state"]
        for category in categories:
            result = self.run_cli("memory", "set", category, "value", "--category", category)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("memory", "set", "plain", "value")
        self.assertEqual(result.returncode, 0, result.stderr)

        stored = self.read_json(state / "memory.json")
        self.assertEqual(
            [entry["category"] for entry in stored["entries"]], categories + ["fact"]
        )
        result = self.run_cli("memory", "set", "bad", "value", "--category", "opinion")
        self.assertEqual(result.returncode, 2)

        result = self.run_cli("memory", "clear")
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "[FAIL] Refusing to clear memory: pass KEY to remove a single entry, or "
            "--all to clear every entry.",
            result.stderr,
        )
        self.assertFalse((REPO_ROOT / "protocol" / "operation-registry.json").exists())

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
    def init_clean_git_workspace(self, tracked=None):
        tracked = tracked or {}
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.email", "mythify@example.invalid"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Mythify Test"], cwd=self.project, check=True)
        (self.project / ".gitignore").write_text(".mythify/\n", encoding="utf-8")
        for name, content in tracked.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.project, check=True)
        return self.init_workspace()

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
        self.assertIn("scope (enforced post-hoc via git): scripts, tests", status.stdout)
        results = self.run_cli("outcome", "results")
        self.assertEqual(results.returncode, 0, results.stderr)
        self.assertIn("iteration 1: verified=True", results.stdout)
        verification = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(verification["outcome"], goal["id"])
        self.assertIs(verification["verified"], True)
        self.assertIsNone(verification["plan"])
        self.assertIsNone(verification["step_id"])
        self.assertEqual(
            sorted(verification["provenance"]),
            ["git_commit", "mythify_version", "worktree_clean", "worktree_digest"],
        )
        self.assertIsNone(verification["provenance"]["git_commit"])
        self.assertRegex(
            verification["provenance"]["mythify_version"],
            r"^\d+\.\d+\.\d+$",
        )

    def test_supervised_outcome_check_keeps_scope_hints_advisory(self):
        state = self.init_clean_git_workspace()
        started = self.run_cli(
            "outcome", "start", "Supervised scope hint", "--success", "verifier passes",
            "--verify", shell_py("raise SystemExit(0)"),
            "--allowed-paths", "src", "--name", "supervised-scope-hint",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        (self.project / "outside.txt").write_text("advisory\n", encoding="utf-8")

        checked = self.run_cli("outcome", "check", "--json")

        self.assertEqual(checked.returncode, 0, checked.stderr)
        payload = json.loads(checked.stdout)
        self.assertIn("outside.txt", payload["record"]["scope_violations"])
        self.assertIs(payload["record"]["verified"], True)
        self.assertEqual(payload["record"]["status_after"], "succeeded")
        verification = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertIs(verification["verified"], True)
        self.assertEqual(verification["exit_code"], 0)

    def test_outcome_check_redacts_verifier_and_metric_output_tails(self):
        state = self.init_workspace()
        verify_secret = "verify-secret-value-1234567890"
        metric_secret = "metric-secret-value-1234567890"
        started = self.run_cli(
            "outcome",
            "start",
            "Redact outcome tails",
            "--success",
            "verifier succeeds",
            "--verify",
            shell_py(
                "import os, sys; "
                "sys.stdout.write('VERIFY_TOKEN=' + os.environ['MYTHIFY_TEST_VERIFY_SECRET'] + '\\n')"
            ),
            "--metric",
            shell_py(
                "import os, sys; "
                "sys.stdout.write('7.5 METRIC_SECRET=' + os.environ['MYTHIFY_TEST_METRIC_SECRET'] + '\\n')"
            ),
            "--json",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        goal = json.loads(started.stdout)
        checked = self.run_cli(
            "outcome",
            "check",
            "--json",
            env_extra={
                "MYTHIFY_TEST_VERIFY_SECRET": verify_secret,
                "MYTHIFY_TEST_METRIC_SECRET": metric_secret,
            },
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        payload = json.loads(checked.stdout)
        self.assertEqual(payload["record"]["metric"]["score"], 7.5)
        iteration = self.read_jsonl(
            state / "outcomes" / goal["id"] / "iterations.jsonl"
        )[-1]
        verification = self.read_jsonl(state / "verifications.jsonl")[-1]
        combined = json.dumps(
            {
                "iteration_verify": iteration["verify"],
                "iteration_metric": iteration["metric"],
                "verification": verification,
            },
            sort_keys=True,
        )
        self.assertNotIn(verify_secret, combined)
        self.assertNotIn(metric_secret, combined)
        self.assertIn("[REDACTED]", combined)

    def test_outcome_start_rejects_nonfinite_cost_and_invalid_escalation_without_mutation(self):
        state = self.init_workspace()
        before = self.state_snapshot(state)
        for value in ("nan", "inf", "-inf", "0", "-1"):
            result = self.run_cli(
                "outcome", "start", "Invalid cost", "--success", "never",
                "--verify", "false", "--max-cost={0}".format(value),
            )
            self.assertEqual(result.returncode, 1, value)
            self.assertIn("finite and greater than 0", result.stdout + result.stderr)
            self.assertEqual(self.state_snapshot(state), before)
        for value in ("0", "-1"):
            result = self.run_cli(
                "outcome", "start", "Invalid escalation", "--success", "never",
                "--verify", "false", "--escalate-after={0}".format(value),
            )
            self.assertEqual(result.returncode, 1, value)
            self.assertIn("--escalate-after >= 1", result.stdout + result.stderr)
            self.assertEqual(self.state_snapshot(state), before)

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

    def test_outcome_metric_failure_records_combined_unverified_evidence(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome",
            "start",
            "Require verifier and metric",
            "--success",
            "verifier and metric pass",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--metric",
            shell_py("raise SystemExit(9)"),
            "--max-iterations",
            "1",
        )
        self.assertEqual(started.returncode, 0, started.stderr)

        checked = self.run_cli("outcome", "check", "--json")
        self.assertEqual(checked.returncode, 2, checked.stderr)
        payload = json.loads(checked.stdout)
        self.assertIs(payload["record"]["verify"]["verified"], True)
        self.assertIs(payload["record"]["metric"]["verified"], False)
        self.assertIs(payload["record"]["verified"], False)

        verification = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertIs(verification["verified"], False)
        self.assertEqual(verification["exit_code"], 9)
        self.assertIs(verification["outcome_verify"]["verified"], True)
        self.assertIs(verification["outcome_metric"]["verified"], False)
        self.assertEqual(verification["outcome_metric"]["exit_code"], 9)

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

    def test_scoped_outcome_run_refuses_non_git_before_agent_execution(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome", "start", "Scoped non-git", "--success", "never",
            "--verify", "false", "--agent",
            shell_py("from pathlib import Path; Path('forbidden.txt').write_text('x')"),
            "--allowed-paths", "src", "--max-iterations", "1",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        run = self.run_cli("outcome", "run")
        self.assertEqual(run.returncode, 2)
        self.assertIn("stopped before agent execution", run.stderr)
        self.assertFalse((self.project / "forbidden.txt").exists())
        goal = self.read_json(next((state / "outcomes").glob("*/goal.json")))
        self.assertIn("scope inspection unavailable", goal["stop_reason"])

    def test_scoped_outcome_run_detects_agent_committed_outside_path(self):
        state = self.init_clean_git_workspace()
        agent = shell_py(
            "from pathlib import Path; import subprocess; "
            "Path('forbidden.txt').write_text('x'); "
            "subprocess.run(['git','add','forbidden.txt'], check=True); "
            "subprocess.run(['git','commit','-qm','agent commit'], check=True)"
        )
        started = self.run_cli(
            "outcome", "start", "Committed bypass", "--success", "never",
            "--verify", "false", "--agent", agent, "--allowed-paths", "src",
            "--max-iterations", "1", "--name", "committed-bypass",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        run = self.run_cli("outcome", "run")
        self.assertEqual(run.returncode, 2)
        rows = self.read_jsonl(state / "outcomes" / "committed-bypass" / "iterations.jsonl")
        self.assertIn("forbidden.txt", rows[-1]["scope_violations"])
        goal = self.read_json(state / "outcomes" / "committed-bypass" / "goal.json")
        self.assertEqual(goal["status"], "stopped")
        self.assertIn("forbidden.txt", goal["stop_reason"])

    def test_scoped_outcome_run_never_verifies_passing_out_of_scope_attempt(self):
        state = self.init_clean_git_workspace()
        agent = shell_py(
            "from pathlib import Path; Path('outside.txt').write_text('x')"
        )
        started = self.run_cli(
            "outcome", "start", "Passing scope bypass", "--success", "verifier passes",
            "--verify", shell_py("raise SystemExit(0)"), "--agent", agent,
            "--allowed-paths", "src", "--max-iterations", "1",
            "--name", "passing-scope-bypass",
        )
        self.assertEqual(started.returncode, 0, started.stderr)

        run = self.run_cli("outcome", "run")

        self.assertEqual(run.returncode, 2, run.stderr)
        iteration = self.read_jsonl(
            state / "outcomes" / "passing-scope-bypass" / "iterations.jsonl"
        )[-1]
        self.assertIs(iteration["verify"]["verified"], True)
        self.assertIs(iteration["verified"], False)
        self.assertEqual(iteration["status_after"], "stopped")
        self.assertIn("outside.txt", iteration["scope_violations"])
        verification = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertIs(verification["verified"], False)
        self.assertEqual(verification["exit_code"], -1)
        self.assertIn("scope violation", verification["stderr_tail"])
        goal = self.read_json(
            state / "outcomes" / "passing-scope-bypass" / "goal.json"
        )
        self.assertEqual(goal["status"], "stopped")
        self.assertIs(goal["last_verified"], False)

    def test_scoped_outcome_run_checks_both_rename_paths(self):
        state = self.init_clean_git_workspace({"forbidden.txt": "outside\n"})
        agent = shell_py(
            "from pathlib import Path; import subprocess; Path('src').mkdir(); "
            "subprocess.run(['git','mv','forbidden.txt','src/moved.txt'], check=True)"
        )
        started = self.run_cli(
            "outcome", "start", "Rename bypass", "--success", "never",
            "--verify", "false", "--agent", agent, "--allowed-paths", "src",
            "--max-iterations", "1", "--name", "rename-bypass",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        run = self.run_cli("outcome", "run")
        self.assertEqual(run.returncode, 2)
        rows = self.read_jsonl(state / "outcomes" / "rename-bypass" / "iterations.jsonl")
        self.assertIn("forbidden.txt", rows[-1]["scope_violations"])
        self.assertNotIn("src/moved.txt", rows[-1]["scope_violations"])


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
             "step_id", "step_title", "step_status", "provenance",
             "prev_sha256", "id", "artifacts"},
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
        self.assertTrue(record["id"].startswith("v-"))
        self.assertEqual(set(record["artifacts"]), {"stdout", "stderr"})
        self.assertEqual(
            sorted(record["provenance"]),
            ["git_commit", "mythify_version", "worktree_clean", "worktree_digest"],
        )

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

    def test_run_redacts_secret_patterns_from_output_tails(self):
        state = self.init_workspace()
        api_secret = "sk-test-secret-value-1234567890"
        bearer_secret = "bearer-secret-value-1234567890"
        stderr_secret = "stderr-secret-value-1234567890"
        command = shell_py(
            "import os, sys; "
            "sys.stdout.write('OPENAI_API_KEY=' + os.environ['MYTHIFY_TEST_API_KEY'] + '\\n'); "
            "sys.stdout.write('Authorization: Bearer ' + os.environ['MYTHIFY_TEST_BEARER'] + '\\nplain ok\\n'); "
            "sys.stderr.write('token: ' + os.environ['MYTHIFY_TEST_STDERR_TOKEN'] + '\\n'); "
            "raise SystemExit(3)"
        )
        result = self.run_cli(
            "verify",
            "run",
            command,
            env_extra={
                "MYTHIFY_TEST_API_KEY": api_secret,
                "MYTHIFY_TEST_BEARER": bearer_secret,
                "MYTHIFY_TEST_STDERR_TOKEN": stderr_secret,
            },
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn(api_secret, result.stdout)
        self.assertNotIn(bearer_secret, result.stdout)
        self.assertNotIn(stderr_secret, result.stdout)
        self.assertIn("[REDACTED]", result.stdout)
        self.assertIn("plain ok", result.stdout)
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        combined = record["stdout_tail"] + "\n" + record["stderr_tail"]
        self.assertNotIn(api_secret, combined)
        self.assertNotIn(bearer_secret, combined)
        self.assertNotIn(stderr_secret, combined)
        self.assertIn("[REDACTED]", combined)
        self.assertIn("plain ok", record["stdout_tail"])

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

    def test_run_timeout_kills_descendant_process_tree(self):
        self.init_workspace()
        marker = self.project / "late-descendant-output.txt"
        child_script = self.project / "late-descendant.py"
        child_script.write_text(
            "import pathlib, time\n"
            "time.sleep(0.7)\n"
            f"pathlib.Path({str(marker)!r}).write_text('leaked', encoding='utf-8')\n",
            encoding="utf-8",
        )
        command = shell_py(
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, {str(child_script)!r}]); "
            "time.sleep(5)"
        )

        result = self.run_cli("verify", "run", command, "--timeout", "0.1")

        self.assertEqual(result.returncode, 2, result.stderr)
        time.sleep(0.9)
        self.assertFalse(marker.exists(), "timed-out verifier descendant still ran")

    def test_windows_taskkill_failure_falls_back_and_reports_uncontained_tree(self):
        mythify = load_cli_module()
        descendant = SimpleNamespace(alive=True)

        class FakeProcess:
            pid = 4242
            killed = False

            def kill(self):
                self.killed = True

        process = FakeProcess()
        failed_taskkill = SimpleNamespace(returncode=1)
        with mock.patch.object(mythify.os, "name", "nt"), mock.patch.object(
            mythify.subprocess, "run", return_value=failed_taskkill
        ):
            contained = mythify.terminate_process_tree(process)

        self.assertFalse(contained)
        self.assertTrue(process.killed, "parent fallback was not killed")
        self.assertTrue(descendant.alive, "test must model an uncontained descendant")

        real_terminate = mythify.terminate_process_tree

        def unconfirmed_termination(real_process):
            real_terminate(real_process)
            return False

        with mock.patch.object(
            mythify, "terminate_process_tree", side_effect=unconfirmed_termination
        ):
            run = mythify.run_shell_capture(
                shell_py("import time; time.sleep(5)"),
                0.05,
            )
        self.assertTrue(run["containment_failed"])
        self.assertIn("containment could not be confirmed", run["stderr_tail"])

    def test_run_output_limit_records_minus_one_and_exits_two(self):
        state = self.init_workspace()
        command = shell_py("import sys; sys.stdout.write('x' * 2048)")
        result = self.run_cli(
            "verify",
            "run",
            command,
            "--claim",
            "too much output",
            env_extra={"MYTHIFY_VERIFY_MAX_OUTPUT_BYTES": "1024"},
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[FAIL] UNVERIFIED: too much output (exit -1,", result.stdout)
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(record["exit_code"], -1)
        self.assertIs(record["verified"], False)
        self.assertIn("(output exceeded 1024 bytes)", record["stderr_tail"])

    def test_run_signal_kill_records_minus_one_and_exits_two(self):
        state = self.init_workspace()
        result = self.run_cli("verify", "run", "kill -9 $$", "--claim", "signal kill")
        self.assertEqual(result.returncode, 2, result.stderr)
        record = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(record["exit_code"], -1)
        self.assertIs(record["verified"], False)
        self.assertIn("(terminated by signal SIGKILL)", record["stderr_tail"])

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
             "step_id", "step_title", "step_status", "prev_sha256"},
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
    def jsonl_lock_dir(self, state, path):
        digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
        return state / "locks" / ("jsonl-" + digest + ".lock")

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

    def test_jsonl_append_recovers_lock_owned_by_dead_process(self):
        state = self.init_workspace()
        log = state / "verifications.jsonl"
        lock_dir = self.jsonl_lock_dir(state, log)
        lock_dir.mkdir(parents=True)
        (lock_dir / "owner.json").write_text(
            json.dumps({"pid": 99999999, "created_unix": 0}) + "\n",
            encoding="utf-8",
        )
        result = self.run_cli("verify", "claim", "recovered", "stale lock removed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(lock_dir.exists())
        self.assertEqual(self.read_jsonl(log)[-1]["claim"], "recovered")

    def test_jsonl_append_recovers_ownerless_and_malformed_locks_after_grace(self):
        state = self.init_workspace()
        log = state / "verifications.jsonl"
        lock_dir = self.jsonl_lock_dir(state, log)
        for index, owner_text in enumerate((None, "{truncated")):
            with self.subTest(owner_text=owner_text):
                lock_dir.mkdir(parents=True)
                if owner_text is not None:
                    (lock_dir / "owner.json").write_text(owner_text, encoding="utf-8")
                started = time.monotonic()
                result = self.run_cli(
                    "verify", "claim", "recovered-{0}".format(index), "stale lock removed"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(time.monotonic() - started, 5)
                self.assertFalse(lock_dir.exists())

    def test_logs_compact_lock_preserves_concurrent_append(self):
        state = self.init_workspace()
        log = state / "verifications.jsonl"
        for index in range(3):
            result = self.run_cli(
                "verify", "claim", "claim-{0}".format(index), "evidence"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        lock_dir = self.jsonl_lock_dir(state, log)
        lock_dir.parent.mkdir(parents=True, exist_ok=True)
        lock_dir.mkdir()
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        compact = subprocess.Popen(
            [sys.executable, str(CLI), "logs", "compact", "--keep", "2", "--json"],
            cwd=str(self.project),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        append = subprocess.Popen(
            [
                sys.executable,
                str(CLI),
                "verify",
                "claim",
                "concurrent-append",
                "evidence",
            ],
            cwd=str(self.project),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            time.sleep(0.2)
            lock_dir.rmdir()
            compact_stdout, compact_stderr = compact.communicate(timeout=10)
            append_stdout, append_stderr = append.communicate(timeout=10)
        finally:
            for process in (compact, append):
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            if lock_dir.exists():
                lock_dir.rmdir()
        self.assertEqual(compact.returncode, 0, compact_stderr)
        self.assertEqual(append.returncode, 0, append_stderr)
        self.assertIn("[WARN] ATTESTED", append_stdout)
        self.assertIn("status", compact_stdout)
        records = self.read_jsonl(log)
        self.assertIn("concurrent-append", [item.get("claim") for item in records])

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
        self.assertIn("Outcomes (0):", result.stdout)
        self.assertIn("Maps (0):", result.stdout)
        self.assertIn("Memory entries: 1", result.stdout)
        self.assertIn("Lessons: 1 project, 0 global", result.stdout)
        self.assertIn(
            "Verifications: 3 executed (2 passed, 1 failed), 1 attested",
            result.stdout,
        )
        self.assertIn("Reflections: 1", result.stdout)

    def test_summary_json_includes_outcomes_and_maps(self):
        state = self.project / ".mythify"
        self.populate()
        started = self.run_cli(
            "outcome", "start", "Green suite", "--success", "tests pass",
            "--verify", shell_py("raise SystemExit(0)"), "--name", "green",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        charted = self.run_cli("map", "create", "Ship a spec", "--name", "spec")
        self.assertEqual(charted.returncode, 0, charted.stderr)
        before = self.state_snapshot(state)
        result = self.run_cli("summary", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(Path(payload["state_dir"]).resolve(), state.resolve())
        self.assertEqual(payload["plans"][0]["id"], "build-house")
        self.assertTrue(payload["plans"][0]["active"])
        self.assertEqual(payload["plans"][0]["completed_steps"], 1)
        self.assertEqual(payload["outcomes"][0]["id"], "green")
        self.assertTrue(payload["outcomes"][0]["active"])
        self.assertEqual(payload["outcomes"][0]["status"], "active")
        self.assertEqual(payload["maps"][0]["id"], "spec")
        self.assertTrue(payload["maps"][0]["active"])
        self.assertEqual(payload["memory_entries"], 1)
        self.assertEqual(payload["lessons"], {"project": 1, "global": 0})
        self.assertEqual(
            payload["verifications"],
            {"executed": 3, "executed_passed": 2, "executed_failed": 1, "attested": 1},
        )
        self.assertEqual(payload["reflections"], 1)
        self.assertEqual(self.state_snapshot(state), before)
        text = self.run_cli("summary")
        self.assertIn("green (active): active, 0/3 iterations - Green suite", text.stdout)
        self.assertIn("spec (active): charting, 0 open, 0 decided - Ship a spec", text.stdout)

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

    def test_status_summarizes_evidence_and_attention_without_mutation(self):
        state = self.init_workspace()
        steps = json.dumps([
            {"title": "Map evidence surface", "success_criteria": "inputs known"},
            {"title": "Verify evidence status", "success_criteria": "tests pass"},
        ])
        created = self.run_cli("plan", "create", "Ship evidence status", "--steps", steps)
        self.assertEqual(created.returncode, 0, created.stderr)
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        passed = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(0)"),
            "--claim", "status inputs mapped",
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        failed = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(1)"),
            "--claim", "status negative control",
        )
        self.assertEqual(failed.returncode, 2, failed.stderr)
        attested = self.run_cli("verify", "claim", "worker said done", "worker transcript")
        self.assertEqual(attested.returncode, 0, attested.stderr)

        before = self.state_snapshot(state)
        result = self.run_cli("status", "--recent", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[OK] Status", result.stdout)
        self.assertIn("Active plan: ship-evidence-status (0/2 completed)", result.stdout)
        self.assertIn("Evidence: 2 executed (1 passed, 1 failed), 1 attested", result.stdout)
        self.assertIn("Recent verification:", result.stdout)
        self.assertIn("failed: status negative control", result.stdout)
        self.assertIn("Attention (2):", result.stdout)
        self.assertIn("issue: failed verification: status negative control", result.stdout)
        self.assertIn("warning: attested claim: worker said done", result.stdout)
        self.assertIn("Next: resolve attention item: failed verification", result.stdout)
        self.assertEqual(self.state_snapshot(state), before)

        json_result = self.run_cli("status", "--json", "--recent", "5")
        self.assertEqual(json_result.returncode, 0, json_result.stderr)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["status"], "needs_attention")
        self.assertEqual(payload["active_plan"]["id"], "ship-evidence-status")
        self.assertEqual(payload["evidence"]["executed_passed"], 1)
        self.assertEqual(payload["evidence"]["executed_failed"], 1)
        self.assertEqual(payload["evidence"]["attested"], 1)
        self.assertEqual(
            [row["verdict"] for row in payload["evidence"]["recent"]],
            ["attested", "failed", "passed"],
        )
        self.assertIn("status summarizes durable state only", payload["guardrail"])
        self.assertEqual(self.state_snapshot(state), before)

    def test_status_attention_puts_issues_before_warnings_then_truncates(self):
        state = self.init_workspace()
        for index in range(4):
            result = self.run_cli("verify", "claim", "worker done {0}".format(index), "transcript")
            self.assertEqual(result.returncode, 0, result.stderr)
        red = self.run_cli(
            "verify", "run", shell_py("raise SystemExit(1)"), "--claim", "late red check"
        )
        self.assertEqual(red.returncode, 2, red.stderr)
        log = state / "verifications.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("worker done 0", "forged claim")
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self.run_cli("status", "--json", "--recent", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        attention = payload["attention"]
        self.assertEqual(len(attention), 3)
        self.assertGreater(payload["attention_total"], 3)
        self.assertEqual(payload["attention_omitted"], payload["attention_total"] - 3)
        self.assertEqual([item["level"] for item in attention[:2]], ["issue", "issue"])
        sources = {item["source"] for item in attention[:2]}
        self.assertEqual(sources, {"verification", "ledger"})
        levels = [item["level"] for item in attention]
        self.assertEqual(levels, sorted(levels, key=lambda level: level != "issue"))

        full = json.loads(self.run_cli("status", "--json", "--recent", "50").stdout)
        full_levels = [item["level"] for item in full["attention"]]
        first_warning = full_levels.index("warning")
        self.assertNotIn("issue", full_levels[first_warning:])
        self.assertEqual(full["attention_omitted"], 0)

        text = self.run_cli("status", "--recent", "3")
        self.assertIn("more omitted; raise --recent to see them", text.stdout)
        rejected = self.run_cli("status", "--recent", "-1")
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("Invalid --recent", rejected.stderr)

    def test_status_attention_flags_stale_and_drifted_outcome_evidence(self):
        state = self.init_workspace()
        started = self.run_cli(
            "outcome", "start", "Green suite", "--success", "tests pass",
            "--verify", shell_py("raise SystemExit(0)"), "--name", "drifty",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        goal_path = state / "outcomes" / "drifty" / "goal.json"
        goal = self.read_json(goal_path)
        goal["evidence_stale"] = True
        goal_path.write_text(json.dumps(goal), encoding="utf-8")
        iterations = state / "outcomes" / "drifty" / "iterations.jsonl"
        iterations.write_text(
            json.dumps({"iteration": 1, "verify": {"command": "true"}}) + "\n",
            encoding="utf-8",
        )
        payload = json.loads(self.run_cli("status", "--json").stdout)
        summaries = [item["summary"] for item in payload["attention"]]
        self.assertIn("audit recheck failed for outcome: drifty", summaries)
        self.assertIn("outcome verifier changed mid-loop: drifty", summaries)
        self.assertEqual(payload["active_outcome"]["id"], "drifty")
        text = self.run_cli("status")
        self.assertIn("Active outcome: drifty (active, 0/3 iterations)", text.stdout)

    def test_status_flags_attested_drift_and_stale_executed(self):
        self.init_workspace()
        for index in range(8):
            self.assertEqual(
                self.run_cli(
                    "verify", "claim", "worker done {0}".format(index), "transcript"
                ).returncode,
                0,
            )
        result = self.run_cli("status", "--recent", "20", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        summaries = [item["summary"] for item in json.loads(result.stdout)["attention"]]
        self.assertTrue(
            any("verification drift" in summary for summary in summaries), summaries
        )
        self.assertTrue(
            any("without an executed verify" in summary for summary in summaries),
            summaries,
        )

    def test_status_reminders_silent_on_executed_evidence(self):
        self.init_workspace()
        for index in range(3):
            self.assertEqual(
                self.run_cli(
                    "verify", "run", shell_py("raise SystemExit(0)"),
                    "--claim", "check {0}".format(index),
                ).returncode,
                0,
            )
        result = self.run_cli("status", "--recent", "20", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        summaries = [item["summary"] for item in json.loads(result.stdout)["attention"]]
        self.assertFalse(
            any("verification drift" in summary for summary in summaries), summaries
        )
        self.assertFalse(
            any("without an executed verify" in summary for summary in summaries),
            summaries,
        )

    def test_read_only_views_have_stable_empty_state_shapes(self):
        state = self.init_workspace()
        before = self.state_snapshot(state)
        views = [
            (
                ("status",),
                [
                    "[OK] Status",
                    "Active plan: none",
                    "Active outcome: none",
                    "Active map: none",
                    "Evidence: 0 executed",
                    "Attention: none",
                    "Next: run the nearest verify run",
                ],
                [
                    "state_dir", "status", "active_plan", "active_outcome", "active_map",
                    "counts", "evidence", "attention", "attention_total", "next_action",
                    "guardrail",
                ],
            ),
            (
                ("history",),
                [
                    "[OK] Verification history",
                    "No verification records found.",
                    "Guardrail: history displays recorded evidence only",
                ],
                ["state_dir", "records", "counts", "guardrail"],
            ),
            (
                ("summary",),
                ["[OK] Summary", "Plans (0):", "Outcomes (0):", "Maps (0):"],
                ["state_dir", "plans", "outcomes", "maps", "verifications", "reflections"],
            ),
        ]

        for command, expected_text, expected_json_keys in views:
            with self.subTest(command=command, mode="text"):
                result = self.run_cli(*command)
                self.assertEqual(result.returncode, 0, result.stderr)
                for expected in expected_text:
                    self.assertIn(expected, result.stdout)
                self.assertEqual(self.state_snapshot(state), before)

            with self.subTest(command=command, mode="json"):
                result = self.run_cli(*(command + ("--json",)))
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                for key in expected_json_keys:
                    self.assertIn(key, payload)
                self.assertEqual(Path(payload["state_dir"]).resolve(), state.resolve())
                self.assertEqual(self.state_snapshot(state), before)

        empty = json.loads(self.run_cli("status", "--json").stdout)
        self.assertEqual(empty["status"], "needs_evidence")
        self.assertEqual(empty["attention"], [])
        self.assertIsNone(empty["active_plan"])

    def test_history_warns_on_malformed_jsonl_records(self):
        state = self.init_workspace()
        log = state / "verifications.jsonl"
        log.write_text(
            json.dumps({
                "kind": "executed",
                "timestamp": "2026-06-16T00:00:00+00:00",
                "claim": "kept record",
                "command": "true",
                "exit_code": 0,
                "duration_seconds": 0.01,
                "stdout_tail": "",
                "stderr_tail": "",
                "verified": True,
            })
            + "\n"
            + "{\"kind\":\"executed\",\"claim\":\"torn record\"\n",
            encoding="utf-8",
        )

        result = self.run_cli("history")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kept record", result.stdout)
        self.assertNotIn("torn record", result.stdout)
        self.assertIn("[WARN] Skipping malformed JSONL record", result.stderr)
        self.assertIn("verifications.jsonl at line 2", result.stderr)


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


class TestOutcomeGraphHardening(CliTestCase):
    """Loop-topology guards: supersession lineage, metric floors, frozen
    paths, audit rechecks, and the first-pass vacuity caution."""

    def start(self, *extra, name, verify=None):
        result = self.run_cli(
            "outcome", "start", "Goal {0}".format(name),
            "--success", "verifier passes",
            "--verify", verify or shell_py("raise SystemExit(0)"),
            "--name", name, *extra,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result

    def goal_json(self, state, slug):
        return self.read_json(state / "outcomes" / slug / "goal.json")

    def test_second_active_outcome_requires_supersede(self):
        state = self.init_workspace()
        self.start(name="loop-a")
        refused = self.run_cli(
            "outcome", "start", "Goal loop-b", "--success", "s",
            "--verify", shell_py("raise SystemExit(0)"), "--name", "loop-b",
        )
        self.assertEqual(refused.returncode, 1)
        self.assertIn("still active", refused.stdout)
        self.assertFalse((state / "outcomes" / "loop-b").exists())
        superseding = self.start(
            "--supersede", "loop-a targeted the wrong module", name="loop-b"
        )
        self.assertIn("superseded: loop-a", superseding.stdout)
        old = self.goal_json(state, "loop-a")
        self.assertEqual(old["status"], "stopped")
        self.assertEqual(old["superseded_by"], "loop-b")
        self.assertIn("superseded by loop-b", old["stop_reason"])
        self.assertEqual(self.goal_json(state, "loop-b")["supersedes"], "loop-a")

    def test_metric_floor_requires_metric_and_gates_success(self):
        self.init_workspace()
        missing = self.run_cli(
            "outcome", "start", "Floored", "--success", "s",
            "--verify", shell_py("raise SystemExit(0)"),
            "--metric-floor", "5", "--name", "floored",
        )
        self.assertEqual(missing.returncode, 1)
        self.assertIn("--metric", missing.stdout)
        self.start(
            "--metric", shell_py("import sys; sys.stdout.write('3.0')"),
            "--metric-floor", "5", name="floored",
        )
        low = self.run_cli("outcome", "check", "--json")
        self.assertEqual(low.returncode, 2)
        payload = json.loads(low.stdout)
        self.assertIs(payload["record"]["verified"], False)
        self.assertIs(payload["record"]["metric_floor_unmet"], True)
        self.assertIn("Metric floor not met", payload["record"]["next_action"])
        self.assertEqual(payload["goal"]["status"], "active")
        self.start(
            "--metric", shell_py("import sys; sys.stdout.write('3.0')"),
            "--metric-floor", "2", "--supersede", "lower the floor",
            name="floored-two",
        )
        met = self.run_cli("outcome", "check", "--json")
        self.assertEqual(met.returncode, 0, met.stderr)
        self.assertEqual(json.loads(met.stdout)["goal"]["status"], "succeeded")

    def test_first_pass_success_carries_a_vacuity_caution(self):
        self.init_workspace()
        self.start(name="instant")
        checked = self.run_cli("outcome", "check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("confirm the verifier can fail", checked.stdout)

    def test_frozen_paths_are_enforced_in_supervised_checks(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "config", "user.email", "mythify@example.invalid"],
            cwd=self.project, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Mythify Test"],
            cwd=self.project, check=True,
        )
        (self.project / ".gitignore").write_text(".mythify/\n", encoding="utf-8")
        (self.project / "tests").mkdir()
        (self.project / "tests" / "test_x.py").write_text("# held out\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.project, check=True)
        state = self.init_workspace()
        self.start("--frozen-paths", "tests", name="frozen")
        (self.project / "tests" / "test_x.py").write_text("# tampered\n", encoding="utf-8")
        checked = self.run_cli("outcome", "check", "--json")
        self.assertEqual(checked.returncode, 2)
        payload = json.loads(checked.stdout)
        self.assertIs(payload["record"]["verified"], False)
        self.assertIn("tests/test_x.py", payload["record"]["frozen_violations"])
        self.assertEqual(payload["goal"]["status"], "stopped")
        self.assertIn("frozen-path violation", payload["goal"]["stop_reason"])
        verification = self.read_jsonl(state / "verifications.jsonl")[-1]
        self.assertEqual(verification["exit_code"], -1)
        self.assertIs(verification["verified"], False)

    def test_audit_recheck_marks_stale_evidence_without_mutating_history(self):
        state = self.init_workspace()
        flag = self.project / "flag.txt"
        flag.write_text("present\n", encoding="utf-8")
        self.start(verify="test -f flag.txt", name="audited")
        active_refused = self.run_cli("outcome", "check", "--audit")
        self.assertEqual(active_refused.returncode, 1)
        self.assertIn("still active", active_refused.stderr)
        checked = self.run_cli("outcome", "check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        flag.unlink()
        red = self.run_cli("outcome", "check", "--audit")
        self.assertEqual(red.returncode, 2)
        goal = self.goal_json(state, "audited")
        self.assertIs(goal["evidence_stale"], True)
        self.assertEqual(goal["status"], "succeeded")
        self.assertEqual(goal["iteration_count"], 1)
        iterations = self.read_jsonl(state / "outcomes" / "audited" / "iterations.jsonl")
        self.assertIs(iterations[-1]["audit"], True)
        self.assertIs(iterations[-1]["verified"], False)
        status = self.run_cli("outcome", "status")
        self.assertIn("evidence: STALE", status.stdout)
        flag.write_text("restored\n", encoding="utf-8")
        green = self.run_cli("outcome", "check", "--audit")
        self.assertEqual(green.returncode, 0, green.stderr)
        self.assertIs(self.goal_json(state, "audited")["evidence_stale"], False)
