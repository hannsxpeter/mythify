"""Interoperability tests for the Python CLI and Node MCP server.

Stdlib only. Skips (unittest skip, not failure) unless node is on PATH and
mcp-server/node_modules exists. The tests run both runtimes against one temp
.mythify directory and cover the shared mutating state surface.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
SERVER = REPO_ROOT / "mcp-server" / "src" / "index.js"
NODE_MODULES = REPO_ROOT / "mcp-server" / "node_modules"
NODE = shutil.which("node")

RESPONSE_TIMEOUT_SECONDS = 30


def shell_py(code):
    return "{0} -c {1}".format(json.dumps(sys.executable), json.dumps(code))


class McpStdioClient:
    """Minimal newline-delimited JSON-RPC 2.0 client for an MCP stdio server."""

    def __init__(self, command, env, cwd):
        self.process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.next_id = 1
        self.messages = queue.Queue()
        self.stderr_lines = []
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
                self.stderr_lines.append("non-JSON stdout line: " + line)

    def _read_stderr(self):
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip("\n"))

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        return self._wait_for(request_id)

    def _wait_for(self, request_id):
        while True:
            try:
                message = self.messages.get(timeout=RESPONSE_TIMEOUT_SECONDS)
            except queue.Empty:
                raise AssertionError(
                    "No JSON-RPC response for id {0} within {1}s. "
                    "Server stderr:\n{2}".format(
                        request_id,
                        RESPONSE_TIMEOUT_SECONDS,
                        "\n".join(self.stderr_lines),
                    )
                )
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise AssertionError(
                    "JSON-RPC error for id {0}: {1}".format(
                        request_id, json.dumps(message["error"])
                    )
                )
            return message.get("result")

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        for stream in (self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass


@unittest.skipUnless(NODE, "node is not on PATH")
@unittest.skipUnless(
    NODE_MODULES.is_dir(),
    "mcp-server/node_modules does not exist; run npm install inside mcp-server/",
)
class TestCliMcpInterop(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="mythify-interop-proj-"))
        self.home = Path(tempfile.mkdtemp(prefix="mythify-interop-home-"))
        self.addCleanup(shutil.rmtree, str(self.project), True)
        self.addCleanup(shutil.rmtree, str(self.home), True)

    def run_cli(self, *args):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def read_jsonl(self, relative_path):
        records = []
        path = self.project / ".mythify" / relative_path
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def tool_text(self, result):
        self.assertIsInstance(result, dict, "tools/call returns a result object")
        content = result.get("content")
        self.assertIsInstance(content, list, "tool result has a content array")
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        self.assertTrue(texts, "tool result has at least one text block")
        return "\n".join(texts)

    def call_tool(self, client, name, arguments=None):
        return self.tool_text(
            client.request(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
            )
        )

    def start_mcp(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["MYTHIFY_DIR"] = str(self.project / ".mythify")
        client = McpStdioClient([NODE, str(SERVER)], env=env, cwd=self.project)
        init_result = client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mythify-interop-test",
                    "version": "2.0.0",
                },
            },
        )
        self.assertIn("protocolVersion", init_result)
        client.notify("notifications/initialized")
        return client

    def test_cli_and_mcp_server_share_mutating_state(self):
        # 1. Seed every shared state family that has a CLI writer.
        init = self.run_cli("init")
        self.assertEqual(init.returncode, 0, init.stderr)
        switched = self.run_cli(
            "host-model",
            "switch",
            "cli-model",
            "--platform",
            "codex-cli",
            "--reason",
            "interop seed",
        )
        self.assertEqual(switched.returncode, 0, switched.stderr)
        steps = json.dumps([
            {"title": "A", "success_criteria": "x"},
            {"title": "B", "success_criteria": "y"},
        ])
        plan = self.run_cli("plan", "create", "Interop goal", "--steps", steps)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        in_progress = self.run_cli("step", "1", "in_progress")
        self.assertEqual(in_progress.returncode, 0, in_progress.stderr)
        memory = self.run_cli("memory", "set", "color", "blue")
        self.assertEqual(memory.returncode, 0, memory.stderr)
        lesson = self.run_cli(
            "lesson",
            "add",
            "CLI Lesson",
            "from cli",
            "--tags",
            "cli-interop",
        )
        self.assertEqual(lesson.returncode, 0, lesson.stderr)
        outcome = self.run_cli(
            "outcome",
            "start",
            "CLI outcome",
            "--success",
            "python exits zero",
            "--verify",
            shell_py("raise SystemExit(0)"),
            "--name",
            "cli-outcome",
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)

        # 2. MCP reads CLI writes, then writes the same state families back.
        client = self.start_mcp()
        try:
            model_status = self.call_tool(
                client,
                "host_model_switch",
                {"action": "status", "format": "json"},
            )
            self.assertIn("cli-model", model_status)

            status_text = self.call_tool(client, "plan_status")
            self.assertIn("Interop goal", status_text)

            recall_text = self.call_tool(client, "memory_recall", {"query": "blue"})
            self.assertIn("color", recall_text)

            lesson_text = self.call_tool(
                client,
                "lesson_recall",
                {"tag": "cli-interop", "scope": "project"},
            )
            self.assertIn("CLI Lesson", lesson_text)

            outcome_status = self.call_tool(
                client,
                "outcome_status",
                {"name": "cli-outcome", "format": "json"},
            )
            self.assertIn("CLI outcome", outcome_status)

            checked = self.call_tool(
                client,
                "outcome_check",
                {"name": "cli-outcome", "format": "json"},
            )
            self.assertIn("[OK]", checked)
            self.assertIn('"status": "succeeded"', checked)

            store_text = self.call_tool(
                client,
                "memory_store",
                {"key": "from_mcp", "value": "written by the MCP server"},
            )
            self.assertIn("[OK]", store_text)

            cleared_memory = self.call_tool(client, "memory_clear", {"key": "color"})
            self.assertIn("[OK]", cleared_memory)

            mcp_lesson = self.call_tool(
                client,
                "lesson_record",
                {
                    "title": "MCP Lesson",
                    "detail": "from mcp",
                    "tags": ["mcp-interop"],
                    "scope": "project",
                },
            )
            self.assertIn("[OK]", mcp_lesson)

            added_step = self.call_tool(
                client,
                "plan_add_step",
                {
                    "title": "MCP added step",
                    "success_criteria": "mcp criteria",
                    "plan": "interop-goal",
                },
            )
            self.assertIn("[OK]", added_step)

            completed_step = self.call_tool(
                client,
                "plan_update_step",
                {
                    "step_id": 2,
                    "status": "completed",
                    "result": "completed by MCP",
                    "plan": "interop-goal",
                },
            )
            self.assertIn("[OK]", completed_step)

            verified = self.call_tool(
                client,
                "verify_run",
                {
                    "command": shell_py("raise SystemExit(0)"),
                    "claim": "mcp verified",
                },
            )
            self.assertIn("[OK]", verified)

            attested = self.call_tool(
                client,
                "verify_claim",
                {"claim": "mcp attested", "evidence": "mcp evidence"},
            )
            self.assertIn("[WARN] ATTESTED", attested)

            reflected = self.call_tool(
                client,
                "reflect",
                {
                    "action_taken": "mcp reflected",
                    "outcome": "success",
                    "observation": "interop reflection",
                    "next_action": "continue",
                },
            )
            self.assertIn("[OK]", reflected)

            mcp_switch = self.call_tool(
                client,
                "host_model_switch",
                {
                    "action": "switch",
                    "target_model": "mcp-model",
                    "platform": "codex-cli",
                    "reason": "mcp write",
                    "format": "json",
                },
            )
            self.assertIn("mcp-model", mcp_switch)
            cli_status = self.run_cli("host-model", "status", "--json")
            self.assertEqual(cli_status.returncode, 0, cli_status.stderr)
            self.assertEqual(json.loads(cli_status.stdout)["target_model"], "mcp-model")

            cli_clear = self.run_cli("host-model", "clear")
            self.assertEqual(cli_clear.returncode, 0, cli_clear.stderr)
            cleared_status = self.call_tool(
                client,
                "host_model_switch",
                {"action": "status", "format": "json"},
            )
            self.assertIn('"status": "unset"', cleared_status)

            final_switch = self.call_tool(
                client,
                "host_model_switch",
                {
                    "action": "switch",
                    "target_model": "mcp-model-final",
                    "platform": "codex-cli",
                    "reason": "final mcp write",
                    "format": "json",
                },
            )
            self.assertIn("mcp-model-final", final_switch)

            started = self.call_tool(
                client,
                "outcome_start",
                {
                    "goal": "MCP outcome",
                    "success": "manual stop is visible",
                    "verify_command": shell_py("raise SystemExit(0)"),
                    "max_iterations": 1,
                    "name": "mcp-outcome",
                    "format": "json",
                },
            )
            self.assertIn("mcp-outcome", started)
            stopped = self.call_tool(
                client,
                "outcome_stop",
                {"name": "mcp-outcome", "reason": "interop complete"},
            )
            self.assertIn("[OK]", stopped)
        finally:
            client.close()

        # 3. The CLI reads the server's writes from the same state directory.
        got = self.run_cli("memory", "get", "from_mcp")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("from_mcp", got.stdout)
        self.assertIn("written by the MCP server", got.stdout)

        missing_color = self.run_cli("memory", "get", "color")
        self.assertEqual(missing_color.returncode, 0, missing_color.stderr)
        self.assertNotIn("blue", missing_color.stdout)

        mcp_lessons = self.run_cli("lesson", "list", "--tag", "mcp-interop")
        self.assertEqual(mcp_lessons.returncode, 0, mcp_lessons.stderr)
        self.assertIn("MCP Lesson", mcp_lessons.stdout)

        shown_plan = self.run_cli("plan", "show", "interop-goal")
        self.assertEqual(shown_plan.returncode, 0, shown_plan.stderr)
        self.assertIn("MCP added step", shown_plan.stdout)
        self.assertIn("completed", shown_plan.stdout)

        cli_results = self.run_cli("outcome", "results", "cli-outcome")
        self.assertEqual(cli_results.returncode, 0, cli_results.stderr)
        self.assertIn("iteration 1: verified=True", cli_results.stdout)

        mcp_outcome_status = self.run_cli("outcome", "status", "mcp-outcome")
        self.assertEqual(mcp_outcome_status.returncode, 0, mcp_outcome_status.stderr)
        self.assertIn("status: stopped", mcp_outcome_status.stdout)

        final_model = self.run_cli("host-model", "status", "--json")
        self.assertEqual(final_model.returncode, 0, final_model.stderr)
        self.assertEqual(
            json.loads(final_model.stdout)["target_model"],
            "mcp-model-final",
        )

        verifications = self.read_jsonl("verifications.jsonl")
        self.assertTrue(
            any(item.get("claim") == "mcp verified" for item in verifications),
            "CLI can read MCP verify_run evidence from disk",
        )
        self.assertTrue(
            any(item.get("claim") == "mcp attested" for item in verifications),
            "CLI can read MCP verify_claim evidence from disk",
        )
        reflections = self.read_jsonl("reflections.jsonl")
        self.assertTrue(
            any(item.get("action") == "mcp reflected" for item in reflections),
            "CLI can read MCP reflection records from disk",
        )


if __name__ == "__main__":
    unittest.main()
