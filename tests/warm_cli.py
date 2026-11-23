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
is in flight (concurrent calls stay concurrent), when the request could not
be delivered to the server, or when MYTHIFY_TEST_FRESH_CLI=1 is set, which
runs every call fresh.

The protocol is one request and one reply per call, each a length-prefixed
JSON message. The server alone owns the child: it enforces the call's timeout
(SIGTERM, which the CLI's ChildTerminationGuard turns into a clean stop of
its verify subprocess tree, then SIGKILL after a bounded grace) and reaps it
before it answers, so no PID ever reaches the client and no signal can reach
a PID that was reaped and reused. Every wait on either side is bounded
except the caller's own timeout=None. Once a request was fully delivered, a
lost or late reply means the call may have changed state, so run() abandons
the server and raises WarmCliError; it never runs the call again.

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

# Seconds. The server gives a child that must stop TERM_GRACE to exit after
# SIGTERM, then KILL_GRACE after SIGKILL. IO_BOUND bounds delivering a request,
# reading the rest of a message once it started, and the server's reply write.
# REPLY_SLACK is how far past the call's timeout the client waits for the reply.
TERM_GRACE = 5.0
KILL_GRACE = 10.0
IO_BOUND = 30.0
REPLY_SLACK = TERM_GRACE + KILL_GRACE + IO_BOUND


class WarmCliError(RuntimeError):
    """The warm server failed in a way that leaves a call's outcome unknown."""


_lock = threading.Lock()
# "last" says how the latest call ran ("warm" or "fresh"), for the parity tests.
_state = {
    "server": None, "env": None, "broken": False, "last": None,
    "scratch": None,  # a tempfile.TemporaryDirectory, cleaned up by _stop_server
    "registered": False,  # whether _stop_server is registered with atexit
}


# ---------------------------------------------------------------------------
# Wire format: a 4-byte big-endian length, then that many bytes of JSON.
# ---------------------------------------------------------------------------

def _send(fd, message):
    """Write MESSAGE to FD within IO_BOUND seconds, else raise WarmCliError.

    FD is made non-blocking before the first byte and stays so, because a
    failure to put it back would follow a complete delivery. The last thing a
    send does is the write that completes it, so any exception raised here
    means the message was not delivered whole.
    """
    data = json.dumps(message).encode("utf-8")
    data = HEADER.pack(len(data)) + data
    deadline = time.monotonic() + IO_BOUND
    os.set_blocking(fd, False)
    while data:
        try:
            data = data[os.write(fd, data):]
        except BlockingIOError:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([], [fd], [], left)[1]:
                raise WarmCliError("timed out delivering a message to the warm CLI") from None


def _read_exact(fd, size, deadline):
    """SIZE bytes from FD, None when DEADLINE (monotonic, or None) passes first.

    A None result can leave a message half read, so the caller abandons the pipe.
    """
    chunks = []
    while size:
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                return None
        chunk = os.read(fd, size)
        if not chunk:
            raise EOFError("warm CLI pipe closed")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _receive(fd, deadline=None):
    """The next message; None when DEADLINE passes before one starts.

    EOFError on a closed pipe, WarmCliError when a message that started does
    not finish within IO_BOUND seconds.
    """
    header = _read_exact(fd, HEADER.size, deadline)
    if header is None:
        return None
    body = _read_exact(fd, HEADER.unpack(header)[0], time.monotonic() + IO_BOUND)
    if body is None:
        raise WarmCliError("timed out reading a message from the warm CLI")
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


def _poll_exit(pid, until):
    """The wait status of child PID once it exits before UNTIL (monotonic), else None."""
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return status
        if time.monotonic() >= until:
            return None
        time.sleep(0.0005)


def _supervise(pid, finished, timeout, gone=None):
    """Wait for the unreaped child PID, stopping it when it must stop.

    FINISHED is the read end of a pipe whose write end only the child holds,
    so it reads EOF when the child exits. GONE is an optional fd that becomes
    readable when the client went away. Returns (wait status or None, outcome):
    "exited" (the child finished on its own), "timeout" (TIMEOUT seconds passed,
    or the child closed its pipe and kept running), or "client_gone". A child
    that must stop gets SIGTERM, then SIGKILL after TERM_GRACE; the status is
    None only when it outlives KILL_GRACE after SIGKILL. The caller is the only
    reaper, so PID is always unreaped when signaled: it is the child or its
    zombie, never a reused number.
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    watched = [finished] if gone is None else [finished, gone]
    outcome = "exited"
    while True:
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        ready = select.select(watched, [], [], left)[0]
        if finished in ready:
            if os.read(finished, 1):
                continue  # the child never writes; ignore it
            break
        outcome = "client_gone" if ready else "timeout"
        break
    if outcome == "exited":
        status = _poll_exit(pid, time.monotonic() + TERM_GRACE)
        if status is not None:
            return status, outcome
        outcome = "timeout"
    for signum, grace in ((signal.SIGTERM, TERM_GRACE), (signal.SIGKILL, KILL_GRACE)):
        os.kill(pid, signum)
        status = _poll_exit(pid, time.monotonic() + grace)
        if status is not None:
            return status, outcome
    return None, outcome


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
        except (EOFError, WarmCliError):
            os._exit(0)
        finished, writer = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(finished)
            # The child keeps WRITER open until it exits, which is how the
            # server learns that it finished.
            _become_cli(request, code, cli)
            # A script that ends without sys.exit exits 0; unwinding from here
            # leaves through normal interpreter shutdown, like a fresh run.
            raise SystemExit(0)
        os.close(writer)
        status, outcome = _supervise(pid, finished, request.get("timeout"), gone=0)
        os.close(finished)
        if outcome == "client_gone":
            os._exit(0)
        try:
            _send(1, {
                "returncode": None if status is None else os.waitstatus_to_exitcode(status),
                "timed_out": outcome == "timeout",
            })
        except (OSError, WarmCliError):
            os._exit(0)


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


def _abandon_server():
    """Stop the server and forget it; the next call starts a new one.

    Closing its stdin makes a server that is supervising a child stop that
    child (SIGTERM, then SIGKILL, each with a grace) and exit, and an idle
    server exit. A server that has not exited by then is killed.
    """
    server = _state["server"]
    _state["server"] = None
    if server is None:
        return
    for pipe in (server.stdin, server.stdout):
        try:
            pipe.close()
        except OSError:
            pass
    try:
        server.wait(timeout=TERM_GRACE + KILL_GRACE)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=KILL_GRACE)


def _stop_server():
    """Stop the server, then remove the scratch folder even when stopping failed."""
    try:
        _abandon_server()
    finally:
        scratch, _state["scratch"] = _state["scratch"], None
        if scratch is not None:
            scratch.cleanup()


def _server():
    """The running server, started on first use; None when it cannot run."""
    server = _state["server"]
    if server is not None and server.poll() is None:
        return server
    if _state["broken"] or not hasattr(os, "fork"):
        return None
    if _state["scratch"] is None:
        # The TemporaryDirectory owns the folder: _stop_server() calls its cleanup().
        _state["scratch"] = tempfile.TemporaryDirectory(prefix="mythify-warm-cli-")
        if not _state.get("registered"):
            _state["registered"] = True
            atexit.register(_stop_server)
    env = dict(os.environ)
    # An empty HOME keeps user site-packages and their .pth hooks out of the
    # server, as the throwaway HOME of most tests keeps them out of a fresh run.
    home = os.path.join(_state["scratch"].name, "home")
    os.makedirs(home, exist_ok=True)
    env["HOME"] = home
    try:
        server = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), str(CLI)] + _top_level_imports(CLI),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
            cwd=_state["scratch"].name,
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
    cwd = os.fsdecode(cwd) if cwd is not None else os.getcwd()  # str, Path, or bytes, like subprocess.run
    # The server starts in its scratch folder, so a relative directory is
    # joined to this process's directory. A plain join, never normpath or
    # resolve: the server's chdir then follows "link/.." as a fresh run does.
    # An empty directory is left empty, so a fresh run reports it as subprocess.run does.
    target = cwd if not cwd or os.path.isabs(cwd) else os.path.join(os.getcwd(), cwd)
    if os.environ.get(FRESH_VARIABLE) == "1" or not _lock.acquire(blocking=False):
        return _fresh(argv, cwd, env, timeout)
    try:
        server = _server()
        # A missing working directory raises in this process, as subprocess.run does.
        if server is None or _startup_view(env) != _state["env"] or not os.path.isdir(target):
            return _fresh(argv, cwd, env, timeout)
        fd_out, out_path = tempfile.mkstemp(dir=_state["scratch"].name)
        fd_err, err_path = tempfile.mkstemp(dir=_state["scratch"].name)
        os.close(fd_out)
        os.close(fd_err)
        try:
            try:
                _send(server.stdin.fileno(), {
                    "args": argv[2:], "cwd": target, "env": env, "timeout": timeout,
                    "stdout": out_path, "stderr": err_path,
                })
            except (OSError, ValueError, WarmCliError):
                # The frame did not go out whole, and the server is abandoned
                # before anything else runs, so it can never complete it:
                # the call did not run, and running it fresh is not a replay.
                _abandon_server()
                return _fresh(argv, cwd, env, timeout)
            _state["last"] = "warm"
            # From here the call may have changed state, so nothing below
            # runs it again.
            deadline = None if timeout is None else time.monotonic() + timeout + REPLY_SLACK
            try:
                reply = _receive(server.stdout.fileno(), deadline)
                if reply is None:
                    raise WarmCliError("no reply from the warm CLI within {0} s".format(
                        timeout + REPLY_SLACK))
                code, timed_out = reply["returncode"], reply["timed_out"]
                if code is None:
                    raise WarmCliError("the warm CLI could not stop the call's process")
            except (OSError, EOFError, ValueError, KeyError, TypeError, WarmCliError) as exc:
                _abandon_server()
                raise WarmCliError(
                    "the warm CLI did not report how {0} ended ({1}); it may have run, "
                    "so it is not run again".format(argv[2:], exc)) from exc
            raw_stdout = Path(out_path).read_bytes()
            raw_stderr = Path(err_path).read_bytes()
        finally:
            os.unlink(out_path)
            os.unlink(err_path)
        if timed_out:
            # As subprocess.run leaves them: the raw bytes read so far, and
            # None for a stream that produced nothing.
            raise subprocess.TimeoutExpired(
                argv, timeout, output=raw_stdout or None, stderr=raw_stderr or None)
        return subprocess.CompletedProcess(argv, code, _decode(raw_stdout), _decode(raw_stderr))
    finally:
        _lock.release()


if __name__ == "__main__":
    serve(sys.argv[1], sys.argv[2:])
