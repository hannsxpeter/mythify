"""End-to-end run of the README quickstart.

The test installs Mythify with scripts/install_user.sh into a throwaway HOME,
XDG_DATA_HOME, and prefix, then drives the installed bin/mythify launcher
through the quickstart commands inside a throwaway git project. Every command
must exit 0, and step completion must be refused before plan verify runs.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install_user.sh"
VERIFY_COMMAND = "python3 -m unittest discover -s tests"
SMOKE_TEST = (
    "import unittest\n"
    "\n"
    "\n"
    "class SmokeTest(unittest.TestCase):\n"
    "    def test_passes(self):\n"
    "        self.assertEqual(1 + 1, 2)\n"
)
# Host variables that would send installed skills or state outside the
# throwaway directories, or weaken the step gate under test.
ISOLATED_VARIABLES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")


class QuickstartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mythify-quickstart-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "home"
        self.prefix = self.tmp / "prefix"
        self.project = self.tmp / "project"
        self.home.mkdir()
        (self.project / "tests").mkdir(parents=True)
        (self.project / "tests" / "test_smoke.py").write_text(SMOKE_TEST, encoding="utf-8")
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key not in ISOLATED_VARIABLES and not key.startswith("MYTHIFY_")
        }
        self.env["HOME"] = str(self.home)
        self.env["XDG_DATA_HOME"] = str(self.tmp / "data")
        self.run_ok(["git", "init", "-q"], cwd=self.project)

    def run_cmd(self, args, cwd):
        return subprocess.run(
            [str(arg) for arg in args],
            cwd=str(cwd),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def run_ok(self, args, cwd):
        result = self.run_cmd(args, cwd)
        self.assertEqual(
            result.returncode,
            0,
            "{0}\nstdout:\n{1}\nstderr:\n{2}".format(args, result.stdout, result.stderr),
        )
        return result

    def test_readme_quickstart_reaches_a_verified_completed_step(self):
        self.run_ok(
            ["sh", INSTALLER, "--prefix", self.prefix, "--project", self.project],
            cwd=REPO_ROOT,
        )
        mythify = self.prefix / "bin" / "mythify"
        self.assertTrue(mythify.is_file(), mythify)

        def cli(*args):
            return self.run_ok([mythify] + list(args), cwd=self.project)

        cli("init")
        cli(
            "plan",
            "create",
            "Fix the failing parser test",
            "--steps",
            '[{"title":"Reproduce and fix","success_criteria":"parser tests pass",'
            '"verify_command":"' + VERIFY_COMMAND + '"}]',
        )
        cli("step", "1", "in_progress")

        early = self.run_cmd(
            [mythify, "step", "1", "completed", "verify run exit 0: parser tests pass"],
            cwd=self.project,
        )
        self.assertEqual(early.returncode, 1, early.stdout + early.stderr)
        self.assertIn("Verified evidence required", early.stderr)

        cli("plan", "verify", "1")
        cli("step", "1", "completed", "verify run exit 0: parser tests pass")

        status = cli("status")
        self.assertIn("Fix the failing parser test", status.stdout)
        cli("report")
        summary = cli("summary")
        self.assertIn("1/1 completed", summary.stdout)
        self.assertIn("1 executed (1 passed, 0 failed)", summary.stdout)


if __name__ == "__main__":
    unittest.main()
