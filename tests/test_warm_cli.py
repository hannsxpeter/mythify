"""tests/warm_cli.py gives the result a fresh interpreter gives."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subprocess_env  # noqa: E402,F401  (faster subprocesses, same behavior)
import warm_cli  # noqa: E402  (runs the CLI forked from a warm interpreter)

CLI = Path(__file__).resolve().parent.parent / "scripts" / "mythify.py"
# Every call runs fresh without fork or when the suite is told to run fresh.
CAN_FORK = hasattr(os, "fork") and os.environ.get(warm_cli.FRESH_VARIABLE) != "1"


class WarmCliParityTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mythify-warm-cli-test-")).resolve()
        self.addCleanup(shutil.rmtree, str(self.base), True)
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

    def test_startup_variables_decide_whether_a_call_may_fork(self):
        view = warm_cli._startup_view({
            "PYTHONHASHSEED": "1", "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
            "HOME": "/x", "PATH": "/y", "MYTHIFY_DIR": "/z",
        })
        self.assertEqual(view, {"PYTHONHASHSEED": "1", "LC_ALL": "C", "LANG": "C", "TZ": "UTC"})


if __name__ == "__main__":
    unittest.main()
