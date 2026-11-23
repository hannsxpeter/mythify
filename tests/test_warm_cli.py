"""tests/warm_cli.py gives the result a fresh interpreter gives."""

import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subprocess_env  # noqa: E402,F401  (faster subprocesses, same behavior)
import warm_cli  # noqa: E402  (runs the CLI forked from a warm interpreter)

CLI = Path(__file__).resolve().parent.parent / "scripts" / "mythify.py"
# Every call runs fresh without fork or when the suite is told to run fresh.
CAN_FORK = hasattr(os, "fork") and os.environ.get(warm_cli.FRESH_VARIABLE) != "1"


class WarmCliParityTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="mythify-warm-cli-test-")
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()

    def env(self, **extra):
        env = dict(os.environ)
        for name in ("MYTHIFY_DIR", "MYTHIFY_REQUIRE_VERIFIED_STEP", warm_cli.FRESH_VARIABLE):
            env.pop(name, None)
        env["HOME"] = str(self.home)
        env.update(extra)
        return env

    def fresh(self, args, cwd, env, timeout=120):
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        )

    def project(self, name):
        folder = self.base / name
        folder.mkdir()
        return folder

    def assert_same(self, args, cwd, env, mode="warm"):
        warm = warm_cli.run(args, cwd=str(cwd), env=env, timeout=120)
        self.assertEqual(warm_cli._state["last"], mode if CAN_FORK else "fresh")
        fresh = self.fresh(args, cwd, env)
        self.assertEqual(
            (warm.args, warm.returncode, warm.stdout, warm.stderr),
            (fresh.args, fresh.returncode, fresh.stdout, fresh.stderr),
            args,
        )
        return warm

    def test_exit_codes_and_output_match_a_fresh_run(self):
        cwd = self.project("plain")
        env = self.env()
        cases = (
            (["--version"], 0),
            (["--help"], 0),
            (["status"], 1),
            (["no-such-command"], 64),
            (["route", "fix the typo in README.md", "--json"], 0),
            (["plan", "show"], 1),
        )
        for args, code in cases:
            with self.subTest(args=args):
                self.assertEqual(self.assert_same(args, cwd, env).returncode, code)

    def test_each_call_takes_its_working_directory_and_environment(self):
        env = self.env()
        warm_project = self.project("warm")
        fresh_project = self.project("fresh")
        warm = warm_cli.run(["init"], cwd=str(warm_project), env=env, timeout=120)
        self.assertEqual(warm_cli._state["last"], "warm" if CAN_FORK else "fresh")
        fresh = self.fresh(["init"], fresh_project, env)
        self.assertEqual(warm.returncode, 0, warm.stderr)
        self.assertEqual(
            warm.stdout.replace(str(warm_project), "PROJECT"),
            fresh.stdout.replace(str(fresh_project), "PROJECT"),
        )
        self.assertTrue((warm_project / ".mythify" / "memory.json").is_file())
        state = warm_project / ".mythify"
        other = self.project("other")
        self.assert_same(["status"], other, self.env(MYTHIFY_DIR=str(state)))
        self.assert_same(["status"], other, env)

    def test_a_timeout_raises_like_subprocess_run(self):
        cwd = self.project("slow")
        env = self.env(MYTHIFY_DIR=str(cwd / ".mythify"))
        self.assertEqual(warm_cli.run(["init"], cwd=str(cwd), env=env, timeout=120).returncode, 0)
        command = "{0} -c 'import time; time.sleep(5)'".format(sys.executable)
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            warm_cli.run(["verify", "run", command], cwd=str(cwd), env=env, timeout=1)
        self.assertEqual(warm_cli._state["last"], "warm" if CAN_FORK else "fresh")
        self.assertEqual(raised.exception.timeout, 1)
        self.assertEqual(raised.exception.cmd, [sys.executable, str(CLI), "verify", "run", command])

    def test_a_call_with_other_startup_variables_runs_fresh(self):
        cwd = self.project("seed")
        env = self.env(PYTHONHASHSEED="123")
        self.assert_same(["--version"], cwd, env, mode="fresh")
        self.assert_same(["--version"], cwd, self.env())

    def test_an_empty_directory_fails_like_subprocess_run(self):
        env = self.env()
        for cwd in ("", b""):
            for forced_fresh in (False, True):
                with self.subTest(cwd=cwd, forced_fresh=forced_fresh), mock.patch.dict(os.environ):
                    os.environ.pop(warm_cli.FRESH_VARIABLE, None)
                    if forced_fresh:
                        os.environ[warm_cli.FRESH_VARIABLE] = "1"
                    with self.assertRaises(FileNotFoundError) as warm:
                        warm_cli.run(["--version"], cwd=cwd, env=env, timeout=120)
                    with self.assertRaises(FileNotFoundError) as fresh:
                        subprocess.run([sys.executable, str(CLI), "--version"], cwd=cwd, env=env,
                                       capture_output=True, text=True, timeout=120)
                    self.assertEqual(warm.exception.errno, fresh.exception.errno)

    def test_startup_variables_decide_whether_a_call_may_fork(self):
        view = warm_cli._startup_view({
            "PYTHONHASHSEED": "1", "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
            "HOME": "/x", "PATH": "/y", "MYTHIFY_DIR": "/z",
        })
        self.assertEqual(view, {"PYTHONHASHSEED": "1", "LC_ALL": "C", "LANG": "C", "TZ": "UTC"})


WAIT = 30  # seconds; the bound on every real wait in the classes below


def exits_with_three(_writer, ready):
    os.write(ready, b"x")
    return 3


def waits_for_a_signal(_writer, ready):
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.write(ready, b"x")
    while True:
        signal.pause()


def ignores_term_and_waits(_writer, ready):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(ready, b"x")
    while True:
        signal.pause()


def closes_its_pipe_and_waits(writer, ready):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.close(writer)
    os.write(ready, b"x")
    while True:
        signal.pause()


@unittest.skipUnless(hasattr(os, "fork"), "needs os.fork")  # check-tests: allow these cases require a real fork
class SuperviseTest(unittest.TestCase):
    """The server stops only its own unreaped child, TERM before KILL, within bounds."""

    def spawn(self, behavior):
        """Fork a child like the server's: it holds the write end of FINISHED."""
        finished, writer = os.pipe()
        ready, ready_writer = os.pipe()
        pid = os.fork()
        if pid == 0:
            status = 1
            try:
                os.close(finished)
                os.close(ready)
                signal.alarm(WAIT)  # fixture guard: SIGALRM ends a child a test left waiting
                status = behavior(writer, ready_writer)
            finally:
                os._exit(status)
        os.close(writer)
        os.close(ready_writer)
        self.addCleanup(self.reap, pid, finished, ready)
        self.assertTrue(select.select([ready], [], [], WAIT)[0], "timed out waiting for the child to start")
        self.assertEqual(os.read(ready, 1), b"x")
        return pid, finished

    def reap(self, pid, *fds):
        """Close FDS, and kill and reap child PID unless _supervise already did."""
        try:
            for fd in fds:
                os.close(fd)
        finally:
            try:
                done, _ = os.waitpid(pid, os.WNOHANG)
                if not done:  # still ours and unreaped, so the number is not reused
                    os.kill(pid, signal.SIGKILL)
                    self.assertIsNotNone(
                        warm_cli._poll_exit(pid, time.monotonic() + WAIT),
                        "timed out after {0} s waiting for child {1} to exit after SIGKILL".format(WAIT, pid),
                    )
            except ChildProcessError:
                pass  # _supervise reaped it

    def exit_code(self, status):
        return os.waitstatus_to_exitcode(status)

    def test_a_child_that_exits_reports_its_own_status(self):
        pid, finished = self.spawn(exits_with_three)
        status, outcome = warm_cli._supervise(pid, finished, WAIT)
        self.assertEqual((self.exit_code(status), outcome), (3, "exited"))

    def test_a_child_that_finished_wins_over_a_deadline_that_already_passed(self):
        pid, finished = self.spawn(exits_with_three)
        self.assertTrue(select.select([finished], [], [], WAIT)[0], "timed out waiting for the child to exit")
        with mock.patch.object(warm_cli.os, "kill", side_effect=AssertionError("signaled a finished child")):
            status, outcome = warm_cli._supervise(pid, finished, 0)
        self.assertEqual((self.exit_code(status), outcome), (3, "exited"))

    def test_a_passed_deadline_stops_the_child_with_term(self):
        pid, finished = self.spawn(waits_for_a_signal)
        status, outcome = warm_cli._supervise(pid, finished, 0)
        self.assertEqual((self.exit_code(status), outcome), (-signal.SIGTERM, "timeout"))

    def test_a_child_that_ignores_term_is_killed_after_the_grace(self):
        pid, finished = self.spawn(ignores_term_and_waits)
        with mock.patch.object(warm_cli, "TERM_GRACE", 0.05):
            status, outcome = warm_cli._supervise(pid, finished, 0)
        self.assertEqual((self.exit_code(status), outcome), (-signal.SIGKILL, "timeout"))

    def test_a_child_that_closed_its_pipe_but_keeps_running_is_stopped(self):
        pid, finished = self.spawn(closes_its_pipe_and_waits)
        with mock.patch.object(warm_cli, "TERM_GRACE", 0.05):
            status, outcome = warm_cli._supervise(pid, finished, None)
        self.assertEqual((self.exit_code(status), outcome), (-signal.SIGKILL, "timeout"))

    def test_a_client_that_went_away_stops_the_child(self):
        pid, finished = self.spawn(waits_for_a_signal)
        gone, gone_writer = os.pipe()
        self.addCleanup(os.close, gone)
        os.close(gone_writer)  # EOF: the client's end of the server's stdin closed
        status, outcome = warm_cli._supervise(pid, finished, None, gone=gone)
        self.assertEqual((self.exit_code(status), outcome), (-signal.SIGTERM, "client_gone"))

    def test_a_child_that_outlives_kill_is_signaled_in_order_and_reported_unreaped(self):
        finished, writer = os.pipe()  # nothing closes the write end: the child never finishes
        self.addCleanup(os.close, finished)
        self.addCleanup(os.close, writer)
        with mock.patch.object(warm_cli, "TERM_GRACE", 0.01), \
                mock.patch.object(warm_cli, "KILL_GRACE", 0.01), \
                mock.patch.object(warm_cli.os, "kill") as kill, \
                mock.patch.object(warm_cli.os, "waitpid", return_value=(0, 0)) as waitpid:
            result = warm_cli._supervise(424242, finished, 0)
        self.assertEqual(result, (None, "timeout"))
        self.assertEqual(
            kill.call_args_list,
            [mock.call(424242, signal.SIGTERM), mock.call(424242, signal.SIGKILL)],
        )
        self.assertEqual({call.args for call in waitpid.call_args_list}, {(424242, os.WNOHANG)})


class FakeServer:
    """Stands in for the server's Popen; the test holds the far end of both pipes."""

    def __init__(self):
        request_reader, request_writer = os.pipe()
        reply_reader, reply_writer = os.pipe()
        self.stdin = os.fdopen(request_writer, "wb", buffering=0)
        self.stdout = os.fdopen(reply_reader, "rb", buffering=0)
        self.requests = request_reader
        self.replies = reply_writer
        self.exits_when_stdin_closes = True
        self.dead = False
        self.killed = False

    def poll(self):
        return 0 if self.dead else None

    def wait(self, timeout=None):
        if self.dead or self.exits_when_stdin_closes:
            self.dead = True
            return 0
        raise subprocess.TimeoutExpired("fake warm server", timeout)

    def kill(self):
        self.killed = self.dead = True

    def close_request_reader(self):
        os.close(self.requests)
        self.requests = None

    def close_reply_writer(self):
        os.close(self.replies)
        self.replies = None

    def close(self):
        """Close what the test still owns (a closed end is None, never a reusable number)."""
        for name in ("requests", "replies"):
            if getattr(self, name) is not None:
                os.close(getattr(self, name))
                setattr(self, name, None)
        self.stdin.close()
        self.stdout.close()


@unittest.skipUnless(hasattr(os, "fork"), "needs os.fork")  # check-tests: allow select() over real pipes is unsupported on no-fork Windows
class ClientTest(unittest.TestCase):
    """run() never runs a call twice once the server may have started it, and never waits unbounded."""

    def setUp(self):
        self.scratch_dir = tempfile.TemporaryDirectory(prefix="mythify-warm-cli-fake-")
        self.addCleanup(self.scratch_dir.cleanup)
        self.scratch = Path(self.scratch_dir.name).resolve()
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop(warm_cli.FRESH_VARIABLE, None)
        self.env = dict(os.environ)
        state = {
            "server": self.server, "env": warm_cli._startup_view(self.env),
            "scratch": self.scratch_dir, "broken": False, "last": None, "registered": True,
        }
        patched = mock.patch.object(warm_cli, "_state", state)
        patched.start()
        self.addCleanup(patched.stop)
        self.fresh_result = subprocess.CompletedProcess([], 0, "fresh", "")

    def call(self, timeout=60, env=None, cwd=None):
        return warm_cli.run(
            ["status"], cwd=str(self.scratch) if cwd is None else cwd, env=env or self.env, timeout=timeout)

    def capture_files(self, stdout=b"", stderr=b""):
        """Patch mkstemp so the call's two capture files already hold STDOUT and STDERR."""
        contents = [stdout, stderr]
        real = tempfile.mkstemp

        def mkstemp(*args, **kwargs):
            opened, path = real(*args, **kwargs)
            os.write(opened, contents.pop(0))
            return opened, path

        return mock.patch.object(warm_cli.tempfile, "mkstemp", side_effect=mkstemp)

    def reply(self, message):
        warm_cli._send(self.server.replies, message)

    def delivered(self):
        return warm_cli._receive(self.server.requests, time.monotonic() + WAIT)

    def never_fresh(self):
        return mock.patch.object(warm_cli, "_fresh", side_effect=AssertionError("the call was run again"))

    def assert_no_leftovers(self):
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_a_request_that_cannot_be_delivered_in_time_runs_fresh_once(self):
        env = dict(self.env, BIG="x" * (8 * 1024 * 1024))  # fills the pipe; nobody reads it
        with mock.patch.object(warm_cli, "IO_BOUND", 0.05), \
                mock.patch.object(warm_cli, "_fresh", return_value=self.fresh_result) as fresh:
            result = self.call(env=env)
        self.assertIs(result, self.fresh_result)
        self.assertEqual(fresh.call_count, 1)
        self.assertIsNone(warm_cli._state["server"])
        self.assertTrue(self.server.stdin.closed)
        self.assert_no_leftovers()

    def test_a_request_sent_to_a_dead_server_runs_fresh_once(self):
        self.server.close_request_reader()  # the server's read end is gone: EPIPE
        with mock.patch.object(warm_cli, "_fresh", return_value=self.fresh_result) as fresh:
            result = self.call()
        self.assertIs(result, self.fresh_result)
        self.assertEqual(fresh.call_count, 1)
        self.assertIsNone(warm_cli._state["server"])
        self.assert_no_leftovers()

    def test_a_reply_lost_after_delivery_raises_and_is_never_run_again(self):
        self.server.close_reply_writer()  # the server died after reading the request
        with self.never_fresh(), self.assertRaises(warm_cli.WarmCliError):
            self.call(timeout=7)
        request = self.delivered()
        self.assertEqual(
            (request["args"], request["cwd"], request["timeout"]),
            (["status"], str(self.scratch), 7),
        )
        with self.assertRaises(EOFError):  # exactly one request went out
            self.delivered()
        self.assertIsNone(warm_cli._state["server"])
        self.assertTrue(self.server.stdin.closed)
        self.assert_no_leftovers()

    def test_a_missing_reply_is_bounded_and_the_stuck_server_is_killed(self):
        self.server.exits_when_stdin_closes = False
        with mock.patch.object(warm_cli, "REPLY_SLACK", 0.05), self.never_fresh(), \
                self.assertRaises(warm_cli.WarmCliError):
            self.call(timeout=0)
        self.assertTrue(self.server.killed)
        self.assertIsNone(warm_cli._state["server"])
        self.assert_no_leftovers()

    def test_a_reply_that_stops_after_its_header_is_bounded(self):
        os.write(self.server.replies, warm_cli.HEADER.pack(100))
        with mock.patch.object(warm_cli, "IO_BOUND", 0.05), self.never_fresh(), \
                self.assertRaises(warm_cli.WarmCliError):
            self.call()
        self.assertIsNone(warm_cli._state["server"])
        self.assert_no_leftovers()

    def assert_unusable_reply_is_an_error(self):
        with self.never_fresh(), self.assertRaises(warm_cli.WarmCliError):
            self.call()
        self.assertIsNone(warm_cli._state["server"])
        self.assertTrue(self.server.stdin.closed)
        self.assert_no_leftovers()

    def test_a_reply_that_is_not_json_raises_and_is_never_run_again(self):
        garbled = b"not json"
        os.write(self.server.replies, warm_cli.HEADER.pack(len(garbled)) + garbled)
        self.assert_unusable_reply_is_an_error()

    def test_a_reply_without_a_status_raises_and_is_never_run_again(self):
        self.reply({"timed_out": False})
        self.assert_unusable_reply_is_an_error()

    def test_a_reply_for_a_child_that_could_not_be_stopped_raises_and_is_never_run_again(self):
        self.reply({"returncode": None, "timed_out": False})
        self.assert_unusable_reply_is_an_error()

    def test_a_reported_timeout_raises_and_the_client_signals_nothing(self):
        self.reply({"returncode": -signal.SIGTERM, "timed_out": True})
        with mock.patch.object(warm_cli.os, "kill", side_effect=AssertionError("the client signaled a pid")), \
                self.assertRaises(subprocess.TimeoutExpired) as raised:
            self.call(timeout=7)
        self.assertEqual(raised.exception.timeout, 7)
        self.assertIs(warm_cli._state["server"], self.server)
        self.assertFalse(self.server.stdin.closed)
        self.assert_no_leftovers()

    def test_a_relative_directory_is_joined_without_collapsing_it(self):
        (self.scratch / "real" / "deep").mkdir(parents=True)
        os.symlink(str(self.scratch / "real" / "deep"), str(self.scratch / "link"))
        for cwd in ("link/..", Path("link") / ".."):
            with self.subTest(type(cwd).__name__):
                self.reply({"returncode": 0, "timed_out": False})
                with mock.patch.object(warm_cli.os, "getcwd", return_value=str(self.scratch)):
                    self.call(cwd=cwd)
                joined = self.delivered()["cwd"]
                self.assertEqual(joined, os.path.join(str(self.scratch), "link/.."))
                # The server's chdir follows the link first, so ".." is the link target's parent.
                self.assertEqual(os.path.realpath(joined), str(self.scratch / "real"))

    def test_a_timeout_keeps_the_raw_bytes_and_none_for_a_silent_stream(self):
        self.reply({"returncode": -signal.SIGTERM, "timed_out": True})
        with self.capture_files(b"part\r\n\xff", b""), self.assertRaises(subprocess.TimeoutExpired) as raised:
            self.call(timeout=7)
        self.assertEqual((raised.exception.output, raised.exception.stderr), (b"part\r\n\xff", None))
        self.assert_no_leftovers()

    def test_a_finished_call_returns_decoded_text_with_universal_newlines(self):
        self.reply({"returncode": 0, "timed_out": False})
        with self.capture_files(b"a\r\nb\rc", b"e\r\n"):
            result = self.call()
        self.assertEqual((result.stdout, result.stderr), ("a\nb\nc", "e\n"))
        self.assert_no_leftovers()

    def test_nothing_follows_the_write_that_completes_a_delivery(self):
        events = []
        real_write, real_set_blocking = os.write, os.set_blocking

        def write(fd, data):
            events.append("write")
            return real_write(fd, data)

        def set_blocking(fd, blocking):
            events.append("set_blocking")
            return real_set_blocking(fd, blocking)

        with mock.patch.object(warm_cli.os, "write", side_effect=write), \
                mock.patch.object(warm_cli.os, "set_blocking", side_effect=set_blocking):
            warm_cli._send(self.server.replies, {"returncode": 0, "timed_out": False})
        self.assertEqual(events, ["set_blocking", "write"])

    def test_a_failure_before_the_first_byte_runs_fresh_and_delivers_nothing(self):
        with mock.patch.object(warm_cli.os, "set_blocking", side_effect=OSError("no descriptor flags")), \
                mock.patch.object(warm_cli, "_fresh", return_value=self.fresh_result) as fresh:
            result = self.call()
        self.assertIs(result, self.fresh_result)
        self.assertEqual(fresh.call_count, 1)
        with self.assertRaises(EOFError):  # the pipe holds no byte and the server was abandoned
            self.delivered()
        self.assert_no_leftovers()

    def test_a_reported_exit_returns_its_code_and_keeps_the_server(self):
        self.reply({"returncode": 3, "timed_out": False})
        result = self.call()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (3, "", ""))
        self.assertEqual(warm_cli._state["last"], "warm")
        self.assertIs(warm_cli._state["server"], self.server)
        self.assert_no_leftovers()


if __name__ == "__main__":
    unittest.main()
