"""Run the CLI in a process forked from a warm interpreter.

Most tests drive `python3 scripts/mythify.py ARGS` as a subprocess, thousands
of times per suite, and each fresh interpreter spends most of its time
starting up and importing the same modules. run() gives the same result as

    subprocess.run([sys.executable, CLI, *ARGS], cwd=CWD, env=ENV,
                   capture_output=True, text=True, timeout=TIMEOUT)

from a cheaper process. One server per test process imports every module
mythify.py imports at its top level, the way a fresh run would, and then
forks a child per call. The child is a separate process like a fresh run:
it takes the call's working directory, environment, and arguments, gets
stdin from /dev/null and stdout and stderr in files, puts the scripts folder
first on sys.path, executes mythify.py as __main__ from source compiled
once, and exits through normal interpreter shutdown, so the exit code,
output, and exit handlers are those of a fresh run. Nothing a test does in
this process reaches the server: it never runs test code.

A call runs as a fresh subprocess instead when fork is unavailable, when the
call's environment differs from the server's in a variable an interpreter
reads only at startup (PYTHON*, LC_*, LANG, LANGUAGE, TZ), when another call
is in flight (concurrent calls stay concurrent), or when
MYTHIFY_TEST_FRESH_CLI=1 is set, which runs every call fresh.

What a forked child shares with its siblings, unlike fresh runs: the
server's string hash seed and the modules already imported, so module
import-time code runs once per server. The runtime keeps no import-time state
that reads the environment, the working directory, or the clock; tests that
cover a real interpreter start (the installer, the quickstart, the MCP
server, the packaged CLI) still start one.
"""

import ast
import atexit
import json
import locale
import os
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "scripts" / "mythify.py"
FRESH_VARIABLE = "MYTHIFY_TEST_FRESH_CLI"
STARTUP_PREFIXES = ("PYTHON", "LC_")
STARTUP_NAMES = ("LANG", "LANGUAGE", "TZ")
HEADER = struct.Struct(">I")

_lock = threading.Lock()
# "last" says how the latest call ran ("warm" or "fresh"), for the parity tests.
_state = {"server": None, "env": None, "scratch": None, "broken": False, "last": None}


# ---------------------------------------------------------------------------
# Wire format: a 4-byte big-endian length, then that many bytes of JSON.
# ---------------------------------------------------------------------------

def _send(fd, message):
    data = json.dumps(message).encode("utf-8")
    data = HEADER.pack(len(data)) + data
    while data:
        data = data[os.write(fd, data):]


def _read_exact(fd, size, deadline):
    chunks = []
    while size:
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                return None
        chunk = os.read(fd, size)
        if not chunk:
            raise EOFError("warm CLI server closed its pipe")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _receive(fd, deadline=None):
    """The next message, None when DEADLINE passes first, EOFError on a closed pipe."""
    header = _read_exact(fd, HEADER.size, deadline)
    if header is None:
        return None
    body = _read_exact(fd, HEADER.unpack(header)[0], None)
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Server: runs as `python warm_cli.py CLI MODULE...`.
# ---------------------------------------------------------------------------

def _become_cli(request, code, cli):
    """In the forked child: take the call's process state, then run mythify.py."""
    import builtins
    import importlib.machinery
    import types

    os.chdir(request["cwd"])
    os.environ.clear()
    os.environ.update(request["env"])
    for fd, path, flags in (
        (0, os.devnull, os.O_RDONLY),
        (1, request["stdout"], os.O_WRONLY),
        (2, request["stderr"], os.O_WRONLY),
    ):
        opened = os.open(path, flags)
        os.dup2(opened, fd)
        os.close(opened)
    sys.argv = [cli] + list(request["args"])
    main = types.ModuleType("__main__")
    main.__file__ = cli
    main.__cached__ = None
    main.__builtins__ = builtins
    main.__loader__ = importlib.machinery.SourceFileLoader("__main__", cli)
    sys.modules["__main__"] = main
    exec(code, main.__dict__)


def serve(cli, modules):
    """Warm up, then fork one child per request until the client goes away."""
    sys.path[0] = os.path.dirname(cli)
    with open(cli, "rb") as handle:
        code = compile(handle.read(), cli, "exec")
    for name in modules:
        try:
            __import__(name)
        except Exception:  # noqa: BLE001  (a fresh run meets the same error in the child)
            pass
    while True:
        try:
            request = _receive(0)
        except EOFError:
            os._exit(0)
        pid = os.fork()
        if pid == 0:
            _become_cli(request, code, cli)
            # A script that ends without sys.exit exits 0; unwinding from here
            # leaves through normal interpreter shutdown, like a fresh run.
            raise SystemExit(0)
        _send(1, {"pid": pid})
        _, status = os.waitpid(pid, 0)
        _send(1, {"returncode": os.waitstatus_to_exitcode(status)})


# ---------------------------------------------------------------------------
# Client.
# ---------------------------------------------------------------------------

def _startup_view(env):
    return {
        key: value for key, value in env.items()
        if key.startswith(STARTUP_PREFIXES) or key in STARTUP_NAMES
    }


def _top_level_imports(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
    return list(dict.fromkeys(names))


def _stop_server():
    server = _state["server"]
    if server is not None and server.poll() is None:
        server.stdin.close()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    if _state["scratch"]:
        shutil.rmtree(_state["scratch"], True)


def _server():
    """The running server, started on first use; None when it cannot run."""
    server = _state["server"]
    if server is not None and server.poll() is None:
        return server
    if _state["broken"] or not hasattr(os, "fork"):
        return None
    if _state["scratch"] is None:
        _state["scratch"] = tempfile.mkdtemp(prefix="mythify-warm-cli-")
        atexit.register(_stop_server)
    env = dict(os.environ)
    # An empty HOME keeps user site-packages and their .pth hooks out of the
    # server, as the throwaway HOME of most tests keeps them out of a fresh run.
    home = os.path.join(_state["scratch"], "home")
    os.makedirs(home, exist_ok=True)
    env["HOME"] = home
    try:
        server = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), str(CLI)] + _top_level_imports(CLI),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
            cwd=_state["scratch"],
        )
    except OSError:
        _state["broken"] = True
        return None
    _state["server"] = server
    _state["env"] = _startup_view(env)
    return server


def _fresh(argv, cwd, env, timeout):
    _state["last"] = "fresh"
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def _decode(data):
    # What subprocess.run(text=True) does with captured output.
    text = data.decode(locale.getpreferredencoding(False))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run(args, cwd=None, env=None, timeout=None):
    """subprocess.run of the CLI with ARGS, captured as text; see the module docstring."""
    argv = [sys.executable, str(CLI)] + [str(arg) for arg in args]
    env = dict(os.environ if env is None else env)
    cwd = str(cwd) if cwd is not None else os.getcwd()
    if os.environ.get(FRESH_VARIABLE) == "1" or not _lock.acquire(blocking=False):
        return _fresh(argv, cwd, env, timeout)
    try:
        server = _server()
        # A missing working directory raises in this process, as subprocess.run does.
        if server is None or _startup_view(env) != _state["env"] or not os.path.isdir(cwd):
            return _fresh(argv, cwd, env, timeout)
        fd_out, out_path = tempfile.mkstemp(dir=_state["scratch"])
        fd_err, err_path = tempfile.mkstemp(dir=_state["scratch"])
        os.close(fd_out)
        os.close(fd_err)
        try:
            try:
                _send(server.stdin.fileno(), {
                    "args": argv[2:], "cwd": cwd, "env": env,
                    "stdout": out_path, "stderr": err_path,
                })
                started = _receive(server.stdout.fileno())
            except (OSError, EOFError):
                # The server died before forking, so nothing ran: run it fresh.
                _state["server"] = None
                return _fresh(argv, cwd, env, timeout)
            _state["last"] = "warm"
            deadline = None if timeout is None else time.monotonic() + timeout
            finished = _receive(server.stdout.fileno(), deadline)
            if finished is None:
                try:
                    os.kill(started["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _receive(server.stdout.fileno())
            stdout = _decode(Path(out_path).read_bytes())
            stderr = _decode(Path(err_path).read_bytes())
        finally:
            os.unlink(out_path)
            os.unlink(err_path)
        if finished is None:
            raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(argv, finished["returncode"], stdout, stderr)
    finally:
        _lock.release()


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2:])
