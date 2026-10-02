"""Tests for the zero-dependency Python MCP stdio server (mythify mcp).

Stdlib only. Each test spawns `python3 scripts/mythify.py mcp` and talks
newline-delimited JSON-RPC 2.0 to it, so the transport, the generated tool
schemas, and the evidence gates are exercised through the real CLI.
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mythify_mcp  # noqa: E402

RESPONSE_TIMEOUT_SECONDS = 60
JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
CUT_COMMAND_TOOLS = (
    "classify",
    "dashboard",
    "harness",
    "background",
    "progress",
    "readiness",
    "timeline",
    "phase",
    "campaign_start",
    "research_start",
    "design_create",
    "workspace_show",
    "host_model_switch",
    "artifact_probe",
    "trace_analyze",
    "eval_scan",
    "review_create",
    "prompt_research",
    "prompt_campaign",
    "prompt_analysis",
    "mcp",
)


def shell_py(code):
    return "{0} -c {1}".format(json.dumps(sys.executable), json.dumps(code))


class McpClient:
    """Minimal newline-delimited JSON-RPC 2.0 client for the stdio server."""

    def __init__(self, cwd, env):
        self.process = subprocess.Popen(
            [sys.executable, str(CLI), "mcp"],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.next_id = 1
        self.messages = queue.Queue()
        self.stashed = []
        self.stderr_lines = []
        self.bad_lines = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                self.bad_lines.append(line)

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip("\n"))

    def send_raw(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def send(self, message):
        self.send_raw(json.dumps(message))

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def start_request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        return request_id

    def request(self, method, params=None):
        """Send a request and return the whole response message."""
        return self.wait_for(self.start_request(method, params))

    def wait_for(self, request_id, timeout=RESPONSE_TIMEOUT_SECONDS):
        for index, message in enumerate(self.stashed):
            if message.get("id") == request_id:
                return self.stashed.pop(index)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    "No JSON-RPC response for id {0!r} within {1}s. Server stderr:\n{2}".format(
                        request_id, timeout, "\n".join(self.stderr_lines)
                    )
                )
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                continue
            if message.get("id") == request_id:
                return message
            self.stashed.append(message)

    def drain(self, wait=0.3):
        """Return every message that arrives within WAIT seconds."""
        found = list(self.stashed)
        self.stashed = []
        deadline = time.monotonic() + wait
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return found
            try:
                found.append(self.messages.get(timeout=remaining))
            except queue.Empty:
                return found

    def call(self, name, arguments=None, meta=None):
        params = {"name": name, "arguments": arguments if arguments is not None else {}}
        if meta is not None:
            params["_meta"] = meta
        message = self.request("tools/call", params)
        if "error" in message:
            raise AssertionError("tools/call {0} failed: {1}".format(name, message["error"]))
        return message["result"]

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass


def result_text(result):
    blocks = result["content"]
    assert len(blocks) == 1 and blocks[0]["type"] == "text", blocks
    return blocks[0]["text"]


def exit_code(result):
    match = re.search(r"^exit_code: (-?\d+)$", result_text(result), re.MULTILINE)
    assert match, result_text(result)
    return int(match.group(1))


class McpServerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mythify-mcp-test-")
        base = Path(self._tmp.name).resolve()
        self.project = base / "project"
        self.home = base / "home"
        self.elsewhere = base / "elsewhere"
        for path in (self.project, self.home, self.elsewhere):
            path.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def env(self, **extra):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env.pop("MYTHIFY_MCP_CALL_TIMEOUT", None)
        env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        env["HOME"] = str(self.home)
        env.update(extra)
        return env

    def start(self, cwd=None, **env_extra):
        client = McpClient(cwd or self.project, self.env(**env_extra))
        self.addCleanup(client.close)
        return client

    def initialize(self, client, version="2025-11-25"):
        message = client.request(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "mythify-mcp-test", "version": "1.0.0"},
            },
        )
        client.notify("notifications/initialized")
        return message

    def init_project(self):
        result = subprocess.run(
            [sys.executable, str(CLI), "init"],
            cwd=str(self.project),
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.project / ".mythify"


class TestHandshake(McpServerCase):
    def test_classic_initialize_echoes_supported_version(self):
        client = self.start()
        message = self.initialize(client, "2025-06-18")
        result = message["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"]["name"], "mythify-mcp")
        self.assertRegex(result["serverInfo"]["version"], r"^\d+\.\d+\.\d+$")
        self.assertIn("verify_run", result["instructions"])
        self.assertNotIn("_meta", result)

    def test_initialize_falls_back_for_unsupported_version(self):
        client = self.start()
        message = self.initialize(client, "1999-01-01")
        self.assertEqual(message["result"]["protocolVersion"], "2025-11-25")

    def test_server_discover_and_stateless_requests(self):
        client = self.start()
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
        discovered = client.request("server/discover", {"_meta": meta})["result"]
        self.assertEqual(
            discovered["supportedVersions"],
            ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"],
        )
        self.assertEqual(discovered["capabilities"], {"tools": {}})
        self.assertTrue(discovered["instructions"])
        server_info = discovered["_meta"]["io.modelcontextprotocol/serverInfo"]
        self.assertEqual(server_info["name"], "mythify-mcp")

        listed = client.request("tools/list", {"_meta": meta})["result"]
        self.assertIn("verify_run", [tool["name"] for tool in listed["tools"]])
        self.assertEqual(listed["_meta"]["io.modelcontextprotocol/serverInfo"], server_info)

        called = client.call("route", {"task": "fix the parser", "json": True}, meta=meta)
        self.assertFalse(called["isError"], result_text(called))
        self.assertEqual(called["_meta"]["io.modelcontextprotocol/serverInfo"], server_info)

    def test_ping_and_notifications(self):
        client = self.start()
        client.notify("notifications/initialized")
        client.notify("notifications/cancelled", {"requestId": 999, "reason": "test"})
        client.notify("some/unknown_notification", {})
        pong = client.request("ping")
        self.assertEqual(pong["result"], {})
        self.assertEqual(client.drain(), [])

    def test_malformed_and_invalid_messages(self):
        client = self.start()
        client.send_raw("{not json")
        parsed = client.messages.get(timeout=RESPONSE_TIMEOUT_SECONDS)
        self.assertIsNone(parsed["id"])
        self.assertEqual(parsed["error"]["code"], -32700)

        client.send({"jsonrpc": "2.0", "id": 41})
        invalid = client.wait_for(41)
        self.assertEqual(invalid["error"]["code"], -32600)

        client.send({"jsonrpc": "1.0", "id": 42, "method": "ping"})
        self.assertEqual(client.wait_for(42)["error"]["code"], -32600)

        client.send_raw("[]")
        batch = client.messages.get(timeout=RESPONSE_TIMEOUT_SECONDS)
        self.assertEqual(batch["error"]["code"], -32600)

        unknown = client.request("resources/list")
        self.assertEqual(unknown["error"]["code"], -32601)

        bad_params = client.request("tools/call", ["status"])
        self.assertEqual(bad_params["error"]["code"], -32602)
        self.assertEqual(client.bad_lines, [])


class TestToolList(McpServerCase):
    def check_property(self, prop, where):
        self.assertIsInstance(prop, dict, where)
        if "description" in prop:
            self.assertIsInstance(prop["description"], str, where)
        kind = prop.get("type")
        if "enum" in prop:
            self.assertIsInstance(prop["enum"], list, where)
            self.assertTrue(prop["enum"], where)
        else:
            self.assertIn(kind, JSON_TYPES, where)
        if kind == "array":
            self.check_property(prop["items"], where + ".items")
        if kind is not None and "enum" in prop:
            for value in prop["enum"]:
                self.assertTrue(self.matches_type(value, kind), (where, value))
        if "default" in prop and kind is not None:
            self.assertTrue(self.matches_type(prop["default"], kind), (where, prop["default"]))

    @staticmethod
    def matches_type(value, kind):
        if kind == "string":
            return isinstance(value, str)
        if kind == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if kind == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if kind == "boolean":
            return isinstance(value, bool)
        if kind == "array":
            return isinstance(value, list)
        return isinstance(value, dict)

    def test_tools_list_is_allowlist_plus_escape_hatch_with_valid_schemas(self):
        client = self.start()
        self.initialize(client)
        tools = client.request("tools/list")["result"]["tools"]
        names = [tool["name"] for tool in tools]
        expected = [mythify_mcp.tool_name(path) for path in mythify_mcp.TOOL_ALLOWLIST]
        self.assertEqual(names, expected + ["mythify"])
        self.assertEqual(len(names), len(set(names)))
        for cut in CUT_COMMAND_TOOLS:
            self.assertNotIn(cut, names)
        for tool in tools:
            name = tool["name"]
            self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
            self.assertTrue(tool["description"].strip(), name)
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object", name)
            self.assertIs(schema["additionalProperties"], False, name)
            self.assertIsInstance(schema["properties"], dict, name)
            for key, prop in schema["properties"].items():
                self.assertRegex(key, r"^[a-z][a-z0-9_]*$", name)
                self.check_property(prop, "{0}.{1}".format(name, key))
            if "required" in schema:
                self.assertTrue(schema["required"], name)
                for key in schema["required"]:
                    self.assertIn(key, schema["properties"], name)
            json.dumps(tool)

        by_name = {tool["name"]: tool["inputSchema"] for tool in tools}
        step = by_name["step"]
        self.assertEqual(step["required"], ["id", "status"])
        self.assertEqual(step["properties"]["result"]["type"], "string")
        verify = by_name["verify_run"]
        self.assertEqual(verify["required"], ["command"])
        self.assertEqual(verify["properties"]["timeout"]["type"], "number")
        self.assertEqual(verify["properties"]["parent"]["type"], "array")
        self.assertEqual(verify["properties"]["output"]["enum"], ["compact", "full"])
        # Commands reached through the escape hatch still generate valid
        # schemas, so promoting one to a typed tool is a one-line change.
        import mythify
        import mythify_parser

        parser = mythify_parser.build_parser(vars(mythify))
        extra = {}
        for path in (("review", "blast-radius"), ("memory", "clear"), ("protocol", "check")):
            tool, _spec = mythify_mcp.build_tool(parser, path)
            extra[tool["name"]] = tool["inputSchema"]
        blast = extra["review_blast_radius"]
        self.assertEqual(blast["properties"]["proof_depth"]["enum"], [1, 2, 3])
        self.assertEqual(blast["properties"]["proof_depth"]["type"], "integer")
        self.assertEqual(extra["memory_clear"]["properties"]["all"]["type"], "boolean")
        self.assertEqual(extra["protocol_check"]["properties"]["paths"]["type"], "array")
        self.assertNotIn("required", extra["protocol_check"])
        self.assertEqual(by_name["mythify"]["required"], ["args"])
        # Model-agnostic: route takes only the task and --json, and outcome
        # start has no visibility knob.
        self.assertEqual(sorted(by_name["route"]["properties"]), ["json", "task"])
        self.assertNotIn("visibility", by_name["outcome_start"]["properties"])


class TestSchemaGeneration(unittest.TestCase):
    """Generator coverage for argparse shapes the current CLI does not use."""

    def build(self):
        import argparse

        parser = argparse.ArgumentParser(prog="mythify.py")
        sub = parser.add_subparsers(dest="command")
        leaf = sub.add_parser("demo", help="Demo command.")
        leaf.add_argument("first")
        leaf.add_argument("rest", nargs="+", type=int)
        leaf.add_argument("--quiet", action="store_false", help="Disable output.")
        leaf.add_argument("--level", type=int, choices=(1, 2), default=1)
        leaf.add_argument("--ratio", type=float)
        leaf.add_argument("--tag", action="append")
        leaf.add_argument("--verbose", "-v", action="count")
        group = leaf.add_mutually_exclusive_group()
        group.add_argument("--fast", action="store_true")
        group.add_argument("--slow", action="store_true")
        tool, spec = mythify_mcp.build_tool(parser, ("demo",))
        return parser, tool, spec

    def test_schema_covers_argparse_shapes(self):
        _parser, tool, _spec = self.build()
        props = tool["inputSchema"]["properties"]
        self.assertEqual(tool["inputSchema"]["required"], ["first", "rest"])
        self.assertEqual(props["rest"], {"type": "array", "items": {"type": "integer"}})
        self.assertEqual(props["quiet"]["type"], "boolean")
        self.assertIs(props["quiet"]["default"], True)
        self.assertEqual(props["level"]["enum"], [1, 2])
        self.assertEqual(props["level"]["default"], 1)
        self.assertEqual(props["ratio"]["type"], "number")
        self.assertEqual(props["tag"]["type"], "array")
        self.assertEqual(props["verbose"]["type"], "integer")
        self.assertIn("Mutually exclusive arguments: fast, slow.", tool["description"])

    def test_argv_round_trips_through_argparse(self):
        parser, _tool, spec = self.build()
        argv = mythify_mcp.build_argv(
            spec,
            {
                "first": "-dash",
                "rest": [3, 4],
                "quiet": False,
                "level": 2,
                "ratio": 0.5,
                "tag": ["a", "-b"],
                "verbose": 2,
                "fast": True,
            },
        )
        parsed = parser.parse_args(argv)
        self.assertEqual(parsed.first, "-dash")
        self.assertEqual(parsed.rest, [3, 4])
        self.assertIs(parsed.quiet, False)
        self.assertEqual(parsed.level, 2)
        self.assertEqual(parsed.ratio, 0.5)
        self.assertEqual(parsed.tag, ["a", "-b"])
        self.assertEqual(parsed.verbose, 2)
        self.assertIs(parsed.fast, True)

    def test_unknown_and_missing_keys_are_rejected(self):
        _parser, _tool, spec = self.build()
        with self.assertRaisesRegex(mythify_mcp.ToolInputError, "'bogus'"):
            mythify_mcp.build_argv(spec, {"first": "x", "rest": [1], "bogus": 1})
        with self.assertRaisesRegex(mythify_mcp.ToolInputError, "'rest'"):
            mythify_mcp.build_argv(spec, {"first": "x"})
        with self.assertRaisesRegex(mythify_mcp.ToolInputError, "boolean"):
            mythify_mcp.build_argv(spec, {"first": "x", "rest": [1], "quiet": "no"})


class TestToolCalls(McpServerCase):
    def test_strict_step_gate_through_mcp(self):
        client = self.start()
        self.initialize(client)
        created = client.call("init")
        self.assertFalse(created["isError"], result_text(created))
        self.assertTrue((self.project / ".mythify").is_dir())

        plan = client.call(
            "plan_create",
            {
                "goal": "Ship the parser",
                "steps": [{"title": "Fix parser", "success_criteria": "tests pass"}],
                "name": "parser",
            },
        )
        self.assertFalse(plan["isError"], result_text(plan))
        self.assertEqual(exit_code(plan), 0)

        started = client.call("step", {"id": "1", "status": "in_progress"})
        self.assertFalse(started["isError"], result_text(started))

        refused = client.call("step", {"id": "1", "status": "completed", "result": "looks done"})
        self.assertTrue(refused["isError"], result_text(refused))
        self.assertEqual(exit_code(refused), 1)
        self.assertIn("Verified evidence required", result_text(refused))

        verified = client.call(
            "verify_run",
            {"command": shell_py("raise SystemExit(0)"), "claim": "parser tests pass"},
        )
        self.assertFalse(verified["isError"], result_text(verified))
        self.assertEqual(exit_code(verified), 0)

        completed = client.call(
            "step",
            {"id": "1", "status": "completed", "result": "verify run exit 0: parser tests pass"},
        )
        self.assertFalse(completed["isError"], result_text(completed))
        self.assertEqual(exit_code(completed), 0)
        stored = json.loads((self.project / ".mythify" / "plans" / "parser.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["steps"][0]["status"], "completed")

    def test_unverified_verdict_is_a_result_not_an_error(self):
        self.init_project()
        client = self.start()
        failed = client.call(
            "verify_run",
            {"command": shell_py("raise SystemExit(3)"), "claim": "expected failure"},
        )
        self.assertFalse(failed["isError"], result_text(failed))
        self.assertEqual(exit_code(failed), 2)

        usage = client.call("mythify", {"args": ["plan", "create"]})
        self.assertTrue(usage["isError"], result_text(usage))
        self.assertIn("error:", result_text(usage))
        self.assertEqual(exit_code(usage), 64)

    def test_verify_run_executes_in_project_root(self):
        state = self.init_project()
        marker_code = "import os, pathlib; pathlib.Path('cwd-marker.txt').write_text(os.getcwd())"
        client = self.start(cwd=self.elsewhere, MYTHIFY_DIR=str(state))
        result = client.call("verify_run", {"command": shell_py(marker_code), "claim": "cwd marker"})
        self.assertFalse(result["isError"], result_text(result))
        self.assertTrue((self.project / "cwd-marker.txt").is_file())
        self.assertFalse((self.elsewhere / "cwd-marker.txt").exists())

        nested = self.project / "src" / "deep"
        nested.mkdir(parents=True)
        (self.project / "cwd-marker.txt").unlink()
        walker = self.start(cwd=nested)
        result = walker.call("verify_run", {"command": shell_py(marker_code), "claim": "walk up"})
        self.assertFalse(result["isError"], result_text(result))
        self.assertTrue((self.project / "cwd-marker.txt").is_file())
        self.assertFalse((nested / "cwd-marker.txt").exists())

    def test_escape_hatch_runs_commands_and_refuses_mcp(self):
        self.init_project()
        client = self.start()
        stored = client.call("mythify", {"args": ["memory", "set", "build", "python3 -m unittest"]})
        self.assertFalse(stored["isError"], result_text(stored))
        found = client.call("memory_get", {"query": "build"})
        self.assertIn("python3 -m unittest", result_text(found))

        for args in (["mcp"], ["--", "mcp"]):
            refused = client.call("mythify", {"args": args})
            self.assertTrue(refused["isError"])
            self.assertIn("mcp command cannot run", result_text(refused))
        bad_type = client.call("mythify", {"args": "status"})
        self.assertTrue(bad_type["isError"])
        extra = client.call("mythify", {"args": ["status"], "cwd": "/tmp"})
        self.assertTrue(extra["isError"])
        self.assertIn("'cwd'", result_text(extra))

    def test_unknown_tool_and_bad_argument_keys(self):
        client = self.start()
        unknown = client.request("tools/call", {"name": "fanout_start", "arguments": {}})
        self.assertEqual(unknown["error"]["code"], -32602)
        self.assertIn("fanout_start", unknown["error"]["message"])

        bad_key = client.call("verify_run", {"command": "true", "cmd": "true"})
        self.assertTrue(bad_key["isError"])
        self.assertIn("'cmd'", result_text(bad_key))

        missing = client.call("verify_run", {})
        self.assertTrue(missing["isError"])
        self.assertIn("'command'", result_text(missing))

        refused = client.call("memory_get", {})
        self.assertTrue(refused["isError"], "no workspace is a refusal")

    def test_call_timeout_terminates_the_command(self):
        self.init_project()
        client = self.start(MYTHIFY_MCP_CALL_TIMEOUT="1")
        started = time.monotonic()
        result = client.call(
            "verify_run",
            {"command": shell_py("import time; time.sleep(4)"), "claim": "slow"},
        )
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(result["isError"])
        self.assertIn("timed out after 1 seconds", result_text(result))
        self.assertEqual(exit_code(result), 124)

    def test_cancelled_call_gets_no_response(self):
        self.init_project()
        client = self.start()
        slow_id = client.start_request(
            "tools/call",
            {
                "name": "verify_run",
                "arguments": {"command": shell_py("import time; time.sleep(4)"), "claim": "slow"},
            },
        )
        time.sleep(0.5)
        client.notify("notifications/cancelled", {"requestId": slow_id})
        started = time.monotonic()
        status = client.call("status")
        self.assertLess(time.monotonic() - started, 3.5)
        self.assertFalse(status["isError"], result_text(status))
        self.assertNotIn(slow_id, [message.get("id") for message in client.drain()])


if __name__ == "__main__":
    unittest.main()
