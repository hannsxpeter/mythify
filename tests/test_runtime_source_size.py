"""Tests for the first-party runtime source-size guard."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts" / "check_runtime_source_size.py"


class RuntimeSourceSizeTests(unittest.TestCase):
    def load_checker(self):
        spec = importlib.util.spec_from_file_location("check_runtime_source_size", CHECKER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_nonblank_line_count_ignores_blank_lines_only(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory(prefix="mythify-source-size-") as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("first\n\n# comment\n    \nsecond\n", encoding="utf-8")
            self.assertEqual(checker.nonblank_line_count(path), 3)

    def test_checker_reports_oversized_runtime_file(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory(prefix="mythify-source-size-") as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "large.py").write_text("value = 1\n" * 4, encoding="utf-8")
            result = checker.check_runtime_sources(root, limit=3)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["violations"][0]["path"], "scripts/large.py")
            self.assertEqual(result["violations"][0]["nonblank_lines"], 4)

    def test_checker_reports_nested_python_and_javascript_runtime_files(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory(prefix="mythify-source-size-") as tmp:
            root = Path(tmp)
            python_path = root / "scripts" / "nested" / "large.py"
            javascript_path = root / "mcp-server" / "src" / "nested" / "large.js"
            python_path.parent.mkdir(parents=True)
            javascript_path.parent.mkdir(parents=True)
            python_path.write_text("value = 1\n" * 4, encoding="utf-8")
            javascript_path.write_text("const value = 1;\n" * 4, encoding="utf-8")

            result = checker.check_runtime_sources(root, limit=3)

            violations = {row["path"] for row in result["violations"]}
            self.assertEqual(
                violations,
                {"scripts/nested/large.py", "mcp-server/src/nested/large.js"},
            )

    def test_repository_runtime_sources_fit_the_1500_line_limit(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--root", str(REPO_ROOT), "--json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["limit"], 1500)
        self.assertEqual(payload["status"], "passed")
        by_path = {row["path"]: row for row in payload["files"]}
        for relative in (
            "scripts/mythify.py",
            "mcp-server/src/fanout.js",
            "mcp-server/src/workflow-tools.js",
        ):
            self.assertIn(relative, by_path)
            self.assertLessEqual(by_path[relative]["nonblank_lines"], 1500)


if __name__ == "__main__":
    unittest.main()
