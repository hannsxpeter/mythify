"""Regression tests for bugs reproduced against the v6 runtime.

Each class pins one reproduced defect: the failing scenario is driven through
the real CLI (or the MCP module) so a regression shows up as the original
symptom, not as a changed implementation detail.
"""

import argparse
import ast
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CLI = SCRIPTS_DIR / "mythify.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import mythify_mcp  # noqa: E402
import mythify_provenance  # noqa: E402
from test_mcp_server import McpClient, exit_code, result_text  # noqa: E402

USAGE_EXIT_CODE = 64


def shell_py(code):
    return "{0} -c {1}".format(json.dumps(sys.executable), json.dumps(code))


class CliCase(unittest.TestCase):
    """A temp project and HOME with no MYTHIFY_DIR unless a test sets one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mythify-bugs-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.project = self.base / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.state = self.project / ".mythify"
        self.extra_env = {}

    def env(self, **extra):
        env = dict(os.environ)
        for name in (
            "MYTHIFY_DIR",
            "MYTHIFY_REQUIRE_VERIFIED_STEP",
            "MYTHIFY_REQUIRE_HUMAN_INPUT",
            "MYTHIFY_MAP_CLAIMANT",
            "MYTHIFY_DISABLE_RUN",
            "MYTHIFY_MCP_CALL_TIMEOUT",
        ):
            env.pop(name, None)
        env["HOME"] = str(self.home)
        env.update(self.extra_env)
        env.update(extra)
        return env

    def run_cli(self, *args, cwd=None, **extra):
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(cwd or self.project),
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=120,
        )

    def ok(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd or self.project),
            check=True,
            capture_output=True,
            text=True,
        )

    def init_git_repo(self, files=None):
        self.git("init", "-q")
        self.git("config", "user.email", "mythify@example.invalid")
        self.git("config", "user.name", "Mythify Test")
        (self.project / ".gitignore").write_text(".mythify/\n", encoding="utf-8")
        for relative, text in (files or {"tracked.txt": "tracked\n"}).items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "baseline")

    def records(self, name="verifications.jsonl"):
        path = self.state / name
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestGlobalLessonsRootIsNotAWorkspace(CliCase):
    """Bug 1: ~/.mythify (global lessons) was discovered as a project workspace."""

    def setUp(self):
        super().setUp()
        self.other = self.base / "other"
        self.other.mkdir()
        self.ok("init", cwd=self.other)
        self.ok("lesson", "add", "Global lesson", "applies everywhere", "--global", cwd=self.other)
        self.global_root = self.home / ".mythify"
        self.assertTrue((self.global_root / "lessons").is_dir())
        self.nested = self.home / "work" / "project"
        self.nested.mkdir(parents=True)

    def test_init_under_home_creates_a_project_workspace(self):
        result = self.run_cli("init", cwd=self.nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Already inside a Mythify workspace", result.stdout)
        self.assertIn("Initialized Mythify workspace", result.stdout)
        self.assertTrue((self.nested / ".mythify" / "memory.json").is_file())
        self.assertFalse((self.global_root / "memory.json").exists())
        listed = self.ok("lesson", "list", "--scope", "global", cwd=self.nested)
        self.assertIn("Global lesson", listed.stdout)

    def test_uninitialized_project_under_home_has_no_workspace(self):
        result = self.run_cli("memory", "set", "k", "v", cwd=self.nested)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.global_root / "memory.json").exists())

    def test_init_in_home_itself_does_not_claim_the_global_root(self):
        result = self.run_cli("init", cwd=self.home)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("global lessons root", result.stderr)
        self.assertFalse((self.global_root / "memory.json").exists())

    def test_explicit_mythify_dir_still_wins(self):
        result = self.run_cli(
            "memory", "set", "k", "v", cwd=self.nested, MYTHIFY_DIR=str(self.global_root)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.global_root / "memory.json").is_file())

    def test_mcp_project_root_skips_the_global_root(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("MYTHIFY_DIR", None)
            with mock.patch.object(Path, "home", return_value=self.home):
                self.assertEqual(mythify_mcp.project_root(self.nested), self.nested.resolve())


class TestUsageErrorsExit64(CliCase):
    """Bug 2: argparse usage errors exited 2, the 'unverified' verdict code."""

    def test_cli_usage_errors_exit_64(self):
        cases = (
            (),
            ("bogus-command",),
            ("plan", "create"),
            ("status", "--recent", "not-a-number"),
            ("verify", "run", "true", "--output", "loud"),
            ("route", "x", "--triage", "auto"),
        )
        for argv in cases:
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, USAGE_EXIT_CODE, result.stdout + result.stderr)
                self.assertIn("error:", result.stderr)

    def test_help_and_version_still_exit_0(self):
        self.assertEqual(self.run_cli("--version").returncode, 0)
        self.assertEqual(self.run_cli("plan", "create", "--help").returncode, 0)

    def test_mcp_exit_2_is_never_an_error_and_64_always_is(self):
        self.assertFalse(mythify_mcp.is_error_exit(0))
        self.assertFalse(mythify_mcp.is_error_exit(2))
        self.assertTrue(mythify_mcp.is_error_exit(1))
        self.assertTrue(mythify_mcp.is_error_exit(USAGE_EXIT_CODE))
        self.assertTrue(mythify_mcp.is_error_exit(124))

    def test_mcp_usage_error_reports_exit_64(self):
        self.ok("init")
        client = McpClient(self.project, self.env())
        self.addCleanup(client.close)
        usage = client.call("mythify", {"args": ["plan", "create"]})
        self.assertTrue(usage["isError"], result_text(usage))
        self.assertEqual(exit_code(usage), USAGE_EXIT_CODE)


class TestCompactionKeepsVerificationCursors(CliCase):
    """Bug 3: logs compact invalidated index-based verification cursors."""

    def setUp(self):
        super().setUp()
        self.extra_env = {"MYTHIFY_DIR": str(self.state)}
        self.ok("init")

    def three_runs(self, command="test -d ."):
        for index in range(3):
            self.ok("verify", "run", command, "--claim", "earlier run {0}".format(index))

    def test_step_completes_after_compaction_with_new_passing_run(self):
        self.ok("plan", "create", "Ship", "--name", "ship", "--steps", json.dumps([{"title": "Build"}]))
        self.three_runs()
        self.ok("step", "1", "in_progress")
        self.ok("logs", "compact", "--keep", "1")
        self.ok("verify", "run", "test -d .", "--claim", "build passes")
        self.ok("step", "1", "completed", "verify run exit 0: build passes")

    def test_compaction_does_not_resurrect_runs_from_before_the_step(self):
        self.ok("plan", "create", "Ship", "--name", "ship", "--steps", json.dumps([{"title": "Build"}]))
        self.three_runs()
        self.ok("step", "1", "in_progress")
        self.ok("logs", "compact", "--keep", "1")
        refused = self.run_cli("step", "1", "completed", "earlier runs passed")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("Verified evidence required", refused.stderr)

    def test_anchor_line_trimmed_by_compaction_still_counts_newer_runs(self):
        self.ok("plan", "create", "Ship", "--name", "ship", "--steps", json.dumps([{"title": "Build"}]))
        self.three_runs()
        self.ok("step", "1", "in_progress")
        self.run_cli("verify", "run", "false", "--claim", "red attempt")
        self.ok("verify", "run", "test -d .", "--claim", "build passes")
        self.ok("logs", "compact", "--keep", "1")
        self.ok("step", "1", "completed", "verify run exit 0: build passes")

    def test_legacy_integer_cursor_is_still_read(self):
        self.ok("plan", "create", "Ship", "--name", "ship", "--steps", json.dumps([{"title": "Build"}]))
        self.three_runs()
        self.ok("step", "1", "in_progress")
        path = self.state / "plans" / "ship.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        step = plan["steps"][0]
        step.pop("verification_anchor", None)
        step["verification_cursor"] = 3
        path.write_text(json.dumps(plan), encoding="utf-8")
        refused = self.run_cli("step", "1", "completed", "earlier runs passed")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.ok("verify", "run", "test -d .", "--claim", "build passes")
        self.ok("step", "1", "completed", "verify run exit 0: build passes")

    def test_map_ticket_resolves_after_compaction_with_new_passing_run(self):
        self.ok("map", "create", "Ship billing", "--name", "billing")
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--verify", "test -d .")
        self.three_runs()
        self.ok("map", "claim", "T1")
        self.ok("logs", "compact", "--keep", "1")
        refused = self.run_cli("map", "resolve", "T1", "--answer", "provisioned")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.ok("map", "verify", "T1")
        self.ok("map", "resolve", "T1", "--answer", "sandbox provisioned")


class TestReportSameSecondEvents(CliCase):
    """Bug 4: report --since last dropped events in the cursor's second."""

    T0 = "2026-01-01T00:00:00+00:00"
    T1 = "2026-01-01T00:00:05+00:00"

    def setUp(self):
        super().setUp()
        self.extra_env = {"MYTHIFY_DIR": str(self.state)}
        self.ok("init")

    def append(self, name, record):
        with (self.state / name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def verification(self, ident, timestamp):
        return {
            "id": ident,
            "kind": "executed",
            "claim": "check " + ident,
            "command": "test -d .",
            "exit_code": 0,
            "verified": True,
            "timestamp": timestamp,
        }

    def reflection(self, action, timestamp):
        return {
            "action": action,
            "outcome": "success",
            "observation": "ok",
            "next": "continue",
            "timestamp": timestamp,
        }

    def report(self):
        result = self.ok("report", "--since", "last", "--format", "json")
        return [event["summary"] for event in json.loads(result.stdout)["events"]]

    def test_same_second_verification_is_reported_once(self):
        self.append("verifications.jsonl", self.verification("v-x", self.T0))
        self.append("verifications.jsonl", self.verification("v-a", self.T1))
        self.assertEqual(len(self.report()), 2)
        self.append("verifications.jsonl", self.verification("v-b", self.T1))
        second = self.report()
        self.assertEqual(len(second), 1, second)
        self.assertIn("check v-b", second[0])
        self.assertEqual(self.report(), [])

    def test_same_second_reflection_without_id_is_reported_once(self):
        self.append("reflections.jsonl", self.reflection("first", self.T0))
        self.append("reflections.jsonl", self.reflection("second", self.T1))
        self.assertEqual(len(self.report()), 2)
        self.append("reflections.jsonl", self.reflection("third", self.T1))
        second = self.report()
        self.assertEqual(len(second), 1, second)
        self.assertIn("third", second[0])
        self.assertEqual(self.report(), [])

    def test_new_event_sorting_before_the_cursor_in_its_second_is_reported(self):
        self.append("reflections.jsonl", self.reflection("reflected", self.T1))
        self.assertEqual(len(self.report()), 1)
        self.append("verifications.jsonl", self.verification("v-late", self.T1))
        second = self.report()
        self.assertEqual(len(second), 1, second)
        self.assertIn("check v-late", second[0])
        self.assertEqual(self.report(), [])


class TestReviewProveMergeGate(CliCase):
    """Bug 5: review prove --command could replace the merge gate, even with true."""

    def setUp(self):
        super().setUp()
        self.extra_env = {"MYTHIFY_DIR": str(self.state)}
        self.init_git_repo()

    def create(self, name, merge_command):
        return self.run_cli(
            "review", "blast-radius", "--status", "warn", "--path", "tracked.txt",
            "--safety-fact", "the change is safe", "--merge-command", merge_command,
            "--name", name,
        )

    def view(self, name):
        return json.loads(self.ok("review", "show", name, "--json").stdout)

    def test_noop_prove_command_is_refused(self):
        self.assertEqual(self.create("gate", "test -f tracked.txt").returncode, 0)
        refused = self.run_cli("review", "prove", "gate", "--command", "true")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("no-op", refused.stderr)
        view = self.view("gate")
        self.assertFalse(view["merge_gate"].get("verified"))
        self.assertEqual(view["safety_fact"]["status"], "unproven")

    def test_noop_merge_gate_cannot_be_proven(self):
        created = self.create("noop-gate", "true")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertIn("no-op", created.stderr)
        refused = self.run_cli("review", "prove", "noop-gate")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertFalse(self.view("noop-gate")["merge_gate"].get("verified"))

    def test_different_command_records_evidence_but_not_the_merge_gate(self):
        self.assertEqual(self.create("gate", "test -f tracked.txt").returncode, 0)
        proved = self.run_cli("review", "prove", "gate", "--command", "test -d .")
        self.assertEqual(proved.returncode, 0, proved.stdout + proved.stderr)
        self.assertIn("does not verify the merge gate", proved.stdout + proved.stderr)
        view = self.view("gate")
        self.assertFalse(view["merge_gate"]["verified"])
        self.assertIsNone(view["merge_gate"]["verification_id"])
        self.assertEqual(view["safety_fact"]["status"], "proven")
        gated = self.ok("review", "prove", "gate")
        self.assertNotIn("does not verify the merge gate", gated.stdout + gated.stderr)
        view = self.view("gate")
        self.assertTrue(view["merge_gate"]["verified"])
        self.assertTrue(view["merge_gate"]["verification_id"].startswith("v-"))


class TestLineageCannotRewriteReviews(CliCase):
    """Bug 6: lineage attach rewrote immutable blast-radius reviews."""

    def test_attach_refuses_review_kind(self):
        self.extra_env = {"MYTHIFY_DIR": str(self.state)}
        self.ok("init")
        self.ok("plan", "create", "Parent plan", "--name", "parent")
        self.ok(
            "review", "blast-radius", "--status", "pass", "--path", "a.py",
            "--safety-fact", "safe", "--name", "safety",
        )
        review_path = self.state / "reviews" / "safety.json"
        before = review_path.read_bytes()
        refused = self.run_cli("lineage", "attach", "review", "safety", "--parent", "plan:parent")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("immutable", refused.stderr)
        self.assertEqual(review_path.read_bytes(), before)


class TestHostSupervisedFrozenBaseline(CliCase):
    """Bug 7: host-supervised outcome check had no frozen-path baseline."""

    def setUp(self):
        super().setUp()
        self.init_git_repo({"tracked.txt": "tracked\n", "tests/test_x.py": "assert True\n"})
        self.ok("init")

    def start(self):
        self.ok(
            "outcome", "start", "Fix the parser", "--success", "parser works",
            "--verify", "test -f tracked.txt", "--frozen-paths", "tests/", "--name", "parser",
        )

    def goal(self):
        return json.loads((self.state / "outcomes" / "parser" / "goal.json").read_text(encoding="utf-8"))

    def test_committed_change_to_a_frozen_path_is_caught(self):
        self.start()
        (self.project / "tests" / "test_x.py").write_text("assert 1\n", encoding="utf-8")
        self.git("commit", "-qam", "rewrite the verifier")
        checked = self.run_cli("outcome", "check")
        self.assertEqual(checked.returncode, 2, checked.stdout + checked.stderr)
        goal = self.goal()
        self.assertEqual(goal["status"], "stopped")
        self.assertIn("frozen-path violation", goal["stop_reason"])

    def test_frozen_file_dirty_before_start_does_not_trip(self):
        (self.project / "tests" / "test_x.py").write_text("assert 2\n", encoding="utf-8")
        self.start()
        checked = self.run_cli("outcome", "check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(self.goal()["status"], "succeeded")

    def test_frozen_file_dirty_before_start_and_edited_after_trips(self):
        (self.project / "tests" / "test_x.py").write_text("assert 2\n", encoding="utf-8")
        self.start()
        (self.project / "tests" / "test_x.py").write_text("assert 3\n", encoding="utf-8")
        checked = self.run_cli("outcome", "check")
        self.assertEqual(checked.returncode, 2, checked.stdout + checked.stderr)
        self.assertIn("frozen-path violation", self.goal()["stop_reason"])

    def test_new_untracked_file_under_a_frozen_path_trips(self):
        self.start()
        (self.project / "tests" / "test_new.py").write_text("assert True\n", encoding="utf-8")
        checked = self.run_cli("outcome", "check")
        self.assertEqual(checked.returncode, 2, checked.stdout + checked.stderr)
        self.assertIn("tests/test_new.py", self.goal()["stop_reason"])


class TestMcpArgvKeepsPositionals(unittest.TestCase):
    """Bug 8: multi-value options swallowed positionals in build_argv."""

    def build(self):
        parser = argparse.ArgumentParser(prog="mythify.py")
        sub = parser.add_subparsers(dest="command")
        leaf = sub.add_parser("demo")
        leaf.add_argument("first")
        leaf.add_argument("maybe", nargs="?")
        leaf.add_argument("--files", nargs="+")
        leaf.add_argument("--pair", nargs=2)
        leaf.add_argument("--any", nargs="*")
        leaf.add_argument("--name")
        _tool, spec = mythify_mcp.build_tool(parser, ("demo",))
        return parser, spec

    def test_positional_after_trailing_multi_value_option(self):
        parser, spec = self.build()
        for arguments in (
            {"first": "pos", "files": ["a", "b"]},
            {"first": "pos", "maybe": "opt", "pair": ["x", "y"]},
            {"first": "pos", "any": ["a"]},
        ):
            with self.subTest(arguments=arguments):
                parsed = parser.parse_args(mythify_mcp.build_argv(spec, arguments))
                self.assertEqual(parsed.first, "pos")
                self.assertEqual(parsed.maybe, arguments.get("maybe"))
                for key in ("files", "pair", "any"):
                    self.assertEqual(getattr(parsed, key), arguments.get(key))

    def test_multi_value_option_plus_positional_round_trips(self):
        parser, spec = self.build()
        argv = mythify_mcp.build_argv(
            spec,
            {"first": "pos", "maybe": "opt", "files": ["a", "b"], "pair": ["x", "y"], "any": [], "name": "n"},
        )
        parsed = parser.parse_args(argv)
        self.assertEqual(parsed.first, "pos")
        self.assertEqual(parsed.maybe, "opt")
        self.assertEqual(parsed.files, ["a", "b"])
        self.assertEqual(parsed.pair, ["x", "y"])
        self.assertEqual(parsed.name, "n")

    def test_dash_positional_after_multi_value_option(self):
        parser, spec = self.build()
        argv = mythify_mcp.build_argv(spec, {"first": "-dash", "files": ["a"]})
        parsed = parser.parse_args(argv)
        self.assertEqual(parsed.first, "-dash")
        self.assertEqual(parsed.files, ["a"])

    def test_multi_value_item_that_looks_like_a_flag_is_rejected(self):
        _parser, spec = self.build()
        with self.assertRaisesRegex(mythify_mcp.ToolInputError, "'files'"):
            mythify_mcp.build_argv(spec, {"first": "pos", "files": ["-a"]})


def _process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


@unittest.skipUnless(os.name == "posix", "process groups are POSIX-only")
class TestMcpTimeoutKillsVerifyChild(CliCase):
    """Bug 9: an MCP timeout left the verify command running in its own session."""

    def test_timed_out_verify_run_leaves_no_child(self):
        self.ok("init")
        pid_file = self.project / "child.pid"
        code = (
            "import os, pathlib, time; "
            "pathlib.Path('child.pid').write_text(str(os.getpid())); "
            "time.sleep(30)"
        )
        client = McpClient(self.project, self.env(MYTHIFY_MCP_CALL_TIMEOUT="3"))
        self.addCleanup(client.close)
        result = client.call("verify_run", {"command": shell_py(code), "claim": "slow"})
        self.assertTrue(result["isError"], result_text(result))
        self.assertEqual(exit_code(result), 124)
        self.assertTrue(pid_file.is_file(), "the verify child never started")
        pid = int(pid_file.read_text(encoding="utf-8"))

        def cleanup():
            if _process_alive(pid):
                os.kill(pid, signal.SIGKILL)

        self.addCleanup(cleanup)
        deadline = time.monotonic() + 8
        while _process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(_process_alive(pid), "verify child {0} survived the MCP timeout".format(pid))

    def test_signalled_cli_kills_its_verify_child_and_exits(self):
        self.extra_env = {"MYTHIFY_DIR": str(self.state)}
        self.ok("init")
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                pid_file = self.project / "child-{0}.pid".format(int(signum))
                code = (
                    "import os, pathlib, time; "
                    "pathlib.Path({0!r}).write_text(str(os.getpid())); "
                    "time.sleep(30)"
                ).format(pid_file.name)
                cli = subprocess.Popen(
                    [sys.executable, str(CLI), "verify", "run", shell_py(code)],
                    cwd=str(self.project),
                    env=self.env(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                deadline = time.monotonic() + 20
                while not pid_file.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(pid_file.is_file(), "the verify child never started")
                pid = int(pid_file.read_text(encoding="utf-8"))
                self.addCleanup(lambda pid=pid: _process_alive(pid) and os.kill(pid, signal.SIGKILL))
                cli.send_signal(signum)
                self.assertEqual(cli.wait(timeout=10), 128 + int(signum))
                deadline = time.monotonic() + 5
                while _process_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(_process_alive(pid), "verify child survived signal {0}".format(signum))


class TestPromptNextWithoutPlan(CliCase):
    """Bug 10: prompt next with no active plan printed 'workflow_state None'."""

    def test_source_line_names_no_none(self):
        self.extra_env = {"MYTHIFY_DIR": str(self.state)}
        self.ok("init")
        result = self.ok("prompt", "next")
        source = [line for line in result.stdout.splitlines() if line.startswith("Source:")]
        self.assertEqual(len(source), 1, result.stdout)
        self.assertNotIn("None", source[0])
        payload = json.loads(self.ok("prompt", "next", "--json").stdout)
        self.assertIsNotNone(payload["source"].get("id") or payload["source"].get("detail"))


def unreferenced_definitions(paths):
    """Top-level functions and classes that nothing in PATHS references."""
    defined = {}
    used = set()
    for path in paths:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), str(path))
        local = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                local.add(node.id)
            elif isinstance(node, ast.Attribute):
                local.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                local.add(node.value)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) in local:
                        used.add(alias.name)
        used |= local
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.setdefault(node.name, Path(path).name)
    return sorted("{0}.{1}".format(defined[name], name) for name in defined if name not in used)


class TestNoDeadRuntimeFunctions(unittest.TestCase):
    """Bug 11: dead helpers survived in the runtime modules."""

    def test_every_runtime_function_is_referenced(self):
        paths = sorted(SCRIPTS_DIR.glob("mythify*.py"))
        self.assertEqual(unreferenced_definitions(paths), [])


class TestWorktreeDigestBatchesHashing(unittest.TestCase):
    """Bug 12: git_worktree_digest spawned one git hash-object per untracked file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mythify-digest-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        for args in (
            ["init", "-q"],
            ["config", "user.email", "mythify@example.invalid"],
            ["config", "user.name", "Mythify Test"],
        ):
            subprocess.run(["git"] + args, cwd=self.root, check=True)
        (self.root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        (self.root / "tracked.txt").write_text("edited\n", encoding="utf-8")
        names = [
            "a.txt", "b with space.txt", "nested/c.bin", "\"quoted.txt", "line\nbreak.txt",
            "trailing space .txt", "z.txt",
        ]
        for index, name in enumerate(names):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("content {0}\n".format(index)).encode("utf-8") + bytes([0, 255]))

    def reference_digest(self):
        """The pre-batching algorithm: one hash-object call per untracked file."""
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        diff = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
            cwd=self.root, capture_output=True, env=env, check=True,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=self.root, capture_output=True, env=env, check=True,
        )
        digest = hashlib.sha256()
        digest.update(b"tracked\0")
        digest.update(diff.stdout)
        for raw_path in sorted(item for item in untracked.stdout.split(b"\0") if item):
            blob = subprocess.run(
                ["git", "hash-object", "--no-filters", "--", os.fsdecode(raw_path)],
                cwd=self.root, capture_output=True, env=env, check=True,
            )
            digest.update(b"untracked\0")
            digest.update(raw_path)
            digest.update(b"\0")
            digest.update(blob.stdout.strip())
            digest.update(b"\0")
        return digest.hexdigest()

    def test_digest_is_unchanged_and_hashing_is_batched(self):
        real_run = subprocess.run
        calls = []

        def counting_run(args, *rest, **kwargs):
            calls.append(list(args))
            return real_run(args, *rest, **kwargs)

        with mock.patch.object(mythify_provenance.subprocess, "run", side_effect=counting_run):
            digest = mythify_provenance.git_worktree_digest(self.root)
        self.assertEqual(digest, self.reference_digest())
        hash_calls = [args for args in calls if "hash-object" in args]
        # One batched call for the ordinary names; the leading-quote and the
        # newline names each need their own call, because --stdin-paths
        # unquotes C-style quoted lines and splits on line breaks.
        self.assertEqual(len(hash_calls), 3, hash_calls)


class TestCompactionKeepsArchivedArtifacts(CliCase):
    """Bug 13: logs compact deleted artifacts that archived records still reference."""

    def test_archived_records_keep_their_artifacts(self):
        self.extra_env = {"MYTHIFY_DIR": str(self.state)}
        self.ok("init")
        for label in ("first", "second"):
            self.ok("verify", "run", shell_py("print('{0}')".format(label)))
        first = self.records()[0]
        first_stdout = self.state / first["artifacts"]["stdout"]["path"]
        self.assertTrue(first_stdout.is_file())
        self.ok("logs", "compact", "--keep", "1")
        self.assertEqual(len(self.records()), 1)
        archives = list((self.state / "logs" / "archive").glob("verifications-*.jsonl"))
        self.assertEqual(len(archives), 1)
        archived = [json.loads(line) for line in archives[0].read_text(encoding="utf-8").splitlines()]
        self.assertEqual(archived[0]["id"], first["id"])
        self.assertTrue(first_stdout.is_file(), "archived record lost its artifact")
        self.assertIn("first", first_stdout.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
