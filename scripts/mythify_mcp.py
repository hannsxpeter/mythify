"""Zero-dependency MCP stdio server for the Mythify CLI.

The server speaks newline-delimited JSON-RPC 2.0 over stdin and stdout. Every
tool call runs one `mythify.py` command in a subprocess whose working
directory is the project root, so the CLI stays the single implementation of
every rule and evidence gate. Typed tools are generated from the argparse tree
for the commands in TOOL_ALLOWLIST; the `mythify` tool runs any other CLI
command by argument list, except `mcp` itself.

Tool results carry the command's stdout, then its stderr when non-empty, then
a final `exit_code: N` line. Exit 2 is a recorded unverified verdict and is a
valid result; exit 1, usage errors, and timeouts set isError.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CLI_PATH = SCRIPT_DIR / "mythify.py"
SERVER_NAME = "mythify-mcp"
WORKSPACE_DIR_NAME = ".mythify"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2026-07-28",
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
FALLBACK_PROTOCOL_VERSION = "2025-11-25"
PROTOCOL_VERSION_META = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO_META = "io.modelcontextprotocol/serverInfo"
CALL_TIMEOUT_ENV = "MYTHIFY_MCP_CALL_TIMEOUT"
DEFAULT_CALL_TIMEOUT = 900.0
TIMEOUT_EXIT_CODE = 124
UNVERIFIED_EXIT_CODE = 2
ESCAPE_HATCH_TOOL = "mythify"
REFUSED_COMMANDS = ("mcp",)
USAGE_ERROR_PATTERN = re.compile(r"^mythify\.py(?: [^\n:]+)?: error: ", re.MULTILINE)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Leaf commands exposed as typed tools, in tools/list order. Tool name = the
# command path joined by "_" with "-" mapped to "_". Long-running drivers such
# as `outcome run` stay reachable through the `mythify` tool.
TOOL_ALLOWLIST = (
    ("init",),
    ("status",),
    ("report",),
    ("history",),
    ("summary",),
    ("route",),
    ("loop-fit",),
    ("prompt", "next"),
    ("prompt", "handoff"),
    ("prompt", "failure"),
    ("prompt", "review"),
    ("prompt", "map"),
    ("prompt", "product"),
    ("plan", "create"),
    ("plan", "import"),
    ("plan", "add-step"),
    ("plan", "verify"),
    ("plan", "list"),
    ("plan", "show"),
    ("plan", "switch"),
    ("plan", "archive"),
    ("step",),
    ("verify", "run"),
    ("verify", "claim"),
    ("reflect",),
    ("memory", "set"),
    ("memory", "get"),
    ("memory", "clear"),
    ("lesson", "add"),
    ("lesson", "list"),
    ("logs", "compact"),
    ("outcome", "start"),
    ("outcome", "check"),
    ("outcome", "status"),
    ("outcome", "results"),
    ("outcome", "stop"),
    ("map", "create"),
    ("map", "ticket"),
    ("map", "claim"),
    ("map", "verify"),
    ("map", "resolve"),
    ("map", "fog"),
    ("map", "scope-out"),
    ("map", "show"),
    ("map", "list"),
    ("map", "promote"),
    ("product", "create"),
    ("product", "outcome"),
    ("product", "non-goal"),
    ("product", "bet"),
    ("product", "risk"),
    ("product", "check"),
    ("product", "approve"),
    ("product", "decide"),
    ("product", "promote"),
    ("product", "measure"),
    ("product", "show"),
    ("review", "blast-radius"),
    ("review", "prove"),
    ("review", "show"),
    ("lineage", "attach"),
    ("lineage", "status"),
    ("protocol", "check"),
)

INSTRUCTIONS = (
    "Mythify records evidence for agent work in the project's .mythify "
    "directory. Each tool runs one Mythify CLI command in the project root and "
    "returns its output followed by an exit_code line. Exit 0 is success. Exit "
    "2 is a recorded unverified verdict, not a tool error. Exit 1 is a refusal "
    "or failure. Run verify_run before any completion claim: step with status "
    "completed is refused until a passing verify run is recorded since the step "
    "started. product_approve and product_decide are human decisions and are "
    "refused without human_input carrying the human's words. Use the mythify "
    "tool with an args array for commands that have no typed tool."
)


class ToolInputError(Exception):
    """Tool arguments that cannot become a CLI argument vector."""


def tool_name(path):
    return "_".join(path).replace("-", "_")


def _subparsers_action(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def find_leaf(parser, path):
    """Return (leaf parser, help text) for a command path, or raise ValueError."""
    current = parser
    help_text = ""
    for token in path:
        actions = _subparsers_action(current)
        if actions is None or token not in actions.choices:
            raise ValueError("Unknown command path: {0}".format(" ".join(path)))
        help_text = next(
            (item.help for item in actions._choices_actions if item.dest == token),
            "",
        ) or ""
        current = actions.choices[token]
    if _subparsers_action(current) is not None:
        raise ValueError("Command path is not a leaf: {0}".format(" ".join(path)))
    return current, help_text


def _help_text(action, prog):
    text = action.help or ""
    if not text or text == argparse.SUPPRESS:
        return ""
    if "%" in text:
        params = {key: value for key, value in vars(action).items() if value is not argparse.SUPPRESS}
        params["prog"] = prog
        if params.get("choices") is not None:
            params["choices"] = ", ".join(str(item) for item in params["choices"])
        try:
            text = text % params
        except (KeyError, TypeError, ValueError):
            pass
    return text


def _scalar_type(action):
    kind = action.type
    if kind is int or getattr(kind, "__name__", "").endswith("int"):
        return "integer"
    if kind is float:
        return "number"
    return "string"


def _item_schema(action):
    if action.choices is not None:
        choices = list(action.choices)
        schema = {"enum": choices}
        kinds = {type(item) for item in choices}
        if kinds == {int}:
            schema["type"] = "integer"
        elif kinds == {str}:
            schema["type"] = "string"
        return schema
    return {"type": _scalar_type(action)}


def _json_default(value):
    if value is None or value is argparse.SUPPRESS:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(item, (str, int, float)) for item in value):
        return list(value)
    return None


def _multiple(nargs):
    return nargs in ("*", "+", argparse.REMAINDER) or (isinstance(nargs, int) and nargs > 1)


def _flag(action):
    longs = [item for item in action.option_strings if item.startswith("--")]
    return (longs or action.option_strings)[0] if action.option_strings else None


def _arg_key(action):
    """Long option name for options, dest for positionals, with "-" as "_"."""
    flag = _flag(action)
    return (flag.lstrip("-") if flag else action.dest).replace("-", "_")


def _arg_spec(action, prog):
    """Describe one argparse action as a schema plus its argv conversion rule."""
    if isinstance(action, (argparse._HelpAction, argparse._VersionAction, argparse._SubParsersAction)):
        return None
    if action.help == argparse.SUPPRESS:
        return None
    flag = _flag(action)
    spec = {"key": _arg_key(action), "flag": flag, "required": False}
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        spec["kind"] = "flag"
        spec["emit_when"] = isinstance(action, argparse._StoreTrueAction)
        schema = {"type": "boolean", "default": not spec["emit_when"]}
    elif isinstance(action, (argparse._StoreConstAction, argparse._AppendConstAction)):
        spec["kind"] = "flag"
        spec["emit_when"] = True
        schema = {"type": "boolean"}
    elif isinstance(action, argparse._CountAction):
        spec["kind"] = "count"
        schema = {"type": "integer", "minimum": 0}
    elif isinstance(action, argparse._AppendAction):
        spec["kind"] = "append"
        schema = {"type": "array", "items": _item_schema(action)}
    elif not action.option_strings:
        spec["kind"] = "positional"
        spec["multiple"] = _multiple(action.nargs)
        spec["required"] = action.nargs not in ("?", "*", argparse.REMAINDER)
        schema = {"type": "array", "items": _item_schema(action)} if spec["multiple"] else _item_schema(action)
    else:
        spec["kind"] = "option"
        spec["multiple"] = _multiple(action.nargs)
        schema = {"type": "array", "items": _item_schema(action)} if spec["multiple"] else _item_schema(action)
    if action.option_strings and spec["kind"] != "flag":
        # Required options (append included) must be present in the call.
        spec["required"] = bool(action.required)
    default = _json_default(action.default)
    if default is not None and spec["kind"] != "flag" and default != []:
        schema["default"] = default
    description = _help_text(action, prog)
    if flag:
        description = "{0} (CLI {1})".format(description, flag).strip()
    if description:
        schema["description"] = description
    spec["schema"] = schema
    return spec


def build_tool(parser, path):
    """Build one MCP tool definition plus its argument specs for PATH."""
    leaf, help_text = find_leaf(parser, path)
    specs = []
    seen = set()
    for action in leaf._actions:
        spec = _arg_spec(action, leaf.prog)
        if spec is None:
            continue
        if spec["key"] in seen:
            raise ValueError("Duplicate tool argument {0} in {1}".format(spec["key"], " ".join(path)))
        seen.add(spec["key"])
        specs.append(spec)
    description = (leaf.description or help_text or "").strip()
    exclusive = []
    for group in leaf._mutually_exclusive_groups:
        names = [_arg_key(action) for action in group._group_actions if action.help != argparse.SUPPRESS]
        if names:
            exclusive.append(", ".join(names))
    if exclusive:
        description += " Mutually exclusive arguments: {0}.".format("; ".join(exclusive))
    description = "{0} CLI: mythify {1}.".format(description, " ".join(path)).strip()
    schema = {
        "type": "object",
        "properties": {spec["key"]: spec["schema"] for spec in specs},
        "additionalProperties": False,
    }
    required = [spec["key"] for spec in specs if spec["required"]]
    if required:
        schema["required"] = required
    tool = {"name": tool_name(path), "description": description, "inputSchema": schema}
    return tool, {"path": tuple(path), "specs": specs}


def escape_hatch_tool():
    return {
        "name": ESCAPE_HATCH_TOOL,
        "description": (
            "Run any Mythify CLI command by argument list, for commands without a "
            "typed tool, for example {\"args\": [\"outcome\", \"run\"]}. The mcp "
            "command is refused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "CLI arguments after mythify, such as [\"plan\", \"show\"].",
                },
            },
            "required": ["args"],
            "additionalProperties": False,
        },
    }


def build_tools(parser, allowlist=TOOL_ALLOWLIST):
    """Return (tool definitions, name -> call spec) for the allowlist."""
    tools = []
    specs = {}
    for path in allowlist:
        tool, spec = build_tool(parser, path)
        if tool["name"] in specs or tool["name"] == ESCAPE_HATCH_TOOL:
            raise ValueError("Duplicate tool name: {0}".format(tool["name"]))
        tools.append(tool)
        specs[tool["name"]] = spec
    tools.append(escape_hatch_tool())
    return tools, specs


def _as_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _as_list(value):
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _option_tokens(flag, value):
    if flag.startswith("--"):
        return ["{0}={1}".format(flag, value)]
    return [flag, value]


def build_argv(call_spec, arguments):
    """Turn MCP tool arguments into a CLI argument vector or raise ToolInputError."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolInputError("arguments must be a JSON object")
    specs = call_spec["specs"]
    known = {spec["key"] for spec in specs}
    unknown = sorted(key for key in arguments if key not in known)
    if unknown:
        raise ToolInputError(
            "Unknown argument {0} for tool {1}. Allowed: {2}.".format(
                ", ".join(repr(key) for key in unknown),
                tool_name(call_spec["path"]),
                ", ".join(sorted(known)) or "none",
            )
        )
    options = []
    positionals = []
    for spec in specs:
        value = arguments.get(spec["key"])
        if value is None or (spec["required"] and value == []):
            if spec["required"]:
                raise ToolInputError("Missing required argument {0!r}.".format(spec["key"]))
            continue
        kind = spec["kind"]
        if kind == "flag":
            if not isinstance(value, bool):
                raise ToolInputError("Argument {0!r} must be a boolean.".format(spec["key"]))
            if value == spec["emit_when"]:
                options.append(spec["flag"])
        elif kind == "count":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ToolInputError("Argument {0!r} must be a nonnegative integer.".format(spec["key"]))
            options.extend([spec["flag"]] * value)
        elif kind == "append":
            for item in _as_list(value):
                options.extend(_option_tokens(spec["flag"], _as_text(item)))
        elif kind == "option":
            if spec["multiple"]:
                options.append(spec["flag"])
                options.extend(_as_text(item) for item in _as_list(value))
            else:
                options.extend(_option_tokens(spec["flag"], _as_text(value)))
        elif spec["multiple"]:
            positionals.extend(_as_text(item) for item in _as_list(value))
        else:
            positionals.append(_as_text(value))
    argv = list(call_spec["path"]) + options
    if any(item.startswith("-") for item in positionals):
        argv.append("--")
    return argv + positionals


def escape_hatch_argv(arguments):
    if not isinstance(arguments, dict):
        raise ToolInputError("arguments must be a JSON object")
    unknown = sorted(key for key in arguments if key != "args")
    if unknown:
        raise ToolInputError(
            "Unknown argument {0} for tool mythify. Allowed: args.".format(
                ", ".join(repr(key) for key in unknown)
            )
        )
    args = arguments.get("args")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ToolInputError("Argument 'args' must be an array of strings.")
    command = next((item for item in args if item != "--" and not item.startswith("-")), None)
    if command in REFUSED_COMMANDS:
        raise ToolInputError("The {0} command cannot run through the MCP server.".format(command))
    return list(args)


def project_root(cwd=None):
    """Parent of the resolved state directory, else the server working directory."""
    env_dir = os.environ.get("MYTHIFY_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve().parent
    current = Path(cwd or os.getcwd()).resolve()
    global_root = (Path.home() / WORKSPACE_DIR_NAME).resolve()
    for base in [current] + list(current.parents):
        candidate = base / WORKSPACE_DIR_NAME
        if candidate.is_dir() and candidate.resolve() != global_root:
            return base
    return current


def call_timeout():
    raw = os.environ.get(CALL_TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_CALL_TIMEOUT
    except ValueError:
        return DEFAULT_CALL_TIMEOUT
    return value if value > 0 else DEFAULT_CALL_TIMEOUT


def is_error_exit(code, stderr):
    if code == 0:
        return False
    if code == UNVERIFIED_EXIT_CODE:
        return bool(USAGE_ERROR_PATTERN.search(stderr or ""))
    return True


def format_result(stdout, stderr, code, is_error):
    parts = []
    if stdout and stdout.strip():
        parts.append(stdout.rstrip("\n"))
    if stderr and stderr.strip():
        parts.append(stderr.rstrip("\n"))
    parts.append("exit_code: {0}".format(code))
    return {"content": [{"type": "text", "text": "\n".join(parts)}], "isError": bool(is_error)}


def error_result(message):
    return {"content": [{"type": "text", "text": "[FAIL] {0}".format(message)}], "isError": True}


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass


class McpServer:
    """Stdio JSON-RPC loop; tool calls run one at a time on a worker thread."""

    def __init__(self, parser, version, output):
        self.version = version
        self.tools, self.call_specs = build_tools(parser)
        self._output = output
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._calls = queue.Queue()
        self._pending = set()
        self._cancelled = set()
        self._running = None

    def send(self, message):
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self._output.write(data)
                self._output.flush()
            except (BrokenPipeError, ValueError, OSError):
                pass

    def run(self, stream):
        worker = threading.Thread(target=self._work, daemon=True)
        worker.start()
        while True:
            line = stream.readline()
            if not line:
                break
            if not line.strip():
                continue
            response = self.handle_line(line)
            if response is not None:
                self.send(response)
        self._calls.put(None)
        worker.join()
        return 0

    def server_info(self):
        return {"name": SERVER_NAME, "version": self.version}

    def handle_line(self, line):
        try:
            text = line.decode("utf-8") if isinstance(line, bytes) else line
            message = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            return _error(None, PARSE_ERROR, "Parse error")
        if isinstance(message, list):
            return _error(None, INVALID_REQUEST, "Batch requests are not supported")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(_valid_id(message), INVALID_REQUEST, "Invalid Request")
        method = message.get("method")
        if method is None and "id" in message and ("result" in message or "error" in message):
            return None
        if not isinstance(method, str):
            return _error(_valid_id(message), INVALID_REQUEST, "Invalid Request")
        if "id" not in message:
            self._handle_notification(method, message.get("params"))
            return None
        request_id = message["id"]
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int, float)):
            return _error(None, INVALID_REQUEST, "Invalid Request: id must be a string or number")
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")
        try:
            return self._handle_request(request_id, method, params)
        except Exception as exc:  # noqa: BLE001 - report server faults as JSON-RPC errors
            sys.stderr.write("[FAIL] mythify-mcp internal error: {0}\n".format(exc))
            return _error(request_id, INTERNAL_ERROR, "Internal error: {0}".format(exc))

    def _with_meta(self, result, params):
        meta = params.get("_meta")
        if isinstance(meta, dict) and PROTOCOL_VERSION_META in meta:
            result = dict(result)
            result["_meta"] = {SERVER_INFO_META: self.server_info()}
        return result

    def _handle_request(self, request_id, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else FALLBACK_PROTOCOL_VERSION
            result = {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": self.server_info(),
                "instructions": INSTRUCTIONS,
            }
        elif method == "server/discover":
            result = {
                "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self.tools}
        elif method == "tools/call":
            return self._handle_call(request_id, params)
        else:
            return _error(request_id, METHOD_NOT_FOUND, "Method not found: {0}".format(method))
        return _response(request_id, self._with_meta(result, params))

    def _handle_call(self, request_id, params):
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if name != ESCAPE_HATCH_TOOL and name not in self.call_specs:
            return _error(request_id, INVALID_PARAMS, "Unknown tool: {0}".format(name))
        try:
            if name == ESCAPE_HATCH_TOOL:
                argv = escape_hatch_argv(arguments)
            else:
                argv = build_argv(self.call_specs[name], arguments)
        except ToolInputError as exc:
            return _response(request_id, self._with_meta(error_result(str(exc)), params))
        with self._state_lock:
            self._pending.add(request_id)
        self._calls.put((request_id, argv, params))
        return None

    def _handle_notification(self, method, params):
        if method != "notifications/cancelled" or not isinstance(params, dict):
            return
        request_id = params.get("requestId")
        with self._state_lock:
            if request_id not in self._pending:
                return
            self._cancelled.add(request_id)
            running = self._running
        if running is not None and running[0] == request_id:
            _terminate(running[1])

    def _finish(self, request_id):
        """Forget a call; True when it was cancelled and must get no response."""
        with self._state_lock:
            cancelled = request_id in self._cancelled
            self._cancelled.discard(request_id)
            self._pending.discard(request_id)
        return cancelled

    def _work(self):
        while True:
            item = self._calls.get()
            if item is None:
                return
            request_id, argv, params = item
            with self._state_lock:
                skip = request_id in self._cancelled
            if skip:
                self._finish(request_id)
                continue
            result = self._execute(request_id, argv)
            if not self._finish(request_id):
                self.send(_response(request_id, self._with_meta(result, params)))

    def _execute(self, request_id, argv):
        root = project_root()
        env = dict(os.environ)
        if env.get("MYTHIFY_DIR"):
            env["MYTHIFY_DIR"] = str(Path(env["MYTHIFY_DIR"]).expanduser().resolve())
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            process = subprocess.Popen(
                [sys.executable, str(CLI_PATH)] + argv,
                cwd=str(root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            return error_result("Could not start mythify: {0}".format(exc))
        with self._state_lock:
            self._running = (request_id, process)
            cancelled_early = request_id in self._cancelled
        if cancelled_early:
            _terminate(process)
        timeout = call_timeout()
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _terminate(process)
            stdout, stderr = process.communicate()
            timed_out = True
        finally:
            with self._state_lock:
                self._running = None
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if timed_out:
            notice = "[FAIL] mythify {0} timed out after {1:g} seconds ({2}); the process was terminated.".format(
                " ".join(argv[:2]), timeout, CALL_TIMEOUT_ENV
            )
            err = (err.rstrip("\n") + "\n" + notice) if err.strip() else notice
            return format_result(out, err, TIMEOUT_EXIT_CODE, True)
        code = process.returncode
        return format_result(out, err, code, is_error_exit(code, err))


def _response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _valid_id(message):
    if isinstance(message, dict):
        value = message.get("id")
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return value
    return None


def serve(parser, version, stream=None, output=None):
    """Run the stdio server until stdin closes; returns the process exit code."""
    server = McpServer(parser, version, output or sys.stdout.buffer)
    return server.run(stream or sys.stdin.buffer)
