import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_ROOT = REPO_ROOT / "mcp-server"


class McpPackageTest(unittest.TestCase):
    def test_dry_run_package_has_public_docs_and_no_tests(self):
        result = subprocess.run(
            ["npm", "pack", "--dry-run", "--json"],
            cwd=MCP_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)[0]
        names = {item["path"] for item in payload["files"]}
        self.assertIn("README.md", names)
        self.assertIn("LICENSE", names)
        self.assertIn("src/index.js", names)
        self.assertIn("protocol/release-gates.json", names)
        self.assertFalse(any(name.startswith("test/") for name in names))

    def test_node_floor_matches_public_docs(self):
        package = json.loads((MCP_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["engines"]["node"], ">=20")
        public_files = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "design.md",
            REPO_ROOT / "docs" / "release.md",
            REPO_ROOT / "CONTRIBUTING.md",
            REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in public_files)
        self.assertIn("Node 20+", combined)
        self.assertIn("Node runtime: `>=20`", combined)
        for stale in ("Node 18+", "Node.js 18", "requires 18+", "node >= 18"):
            self.assertNotIn(stale, combined)

    def test_local_tarball_installs_as_documented(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts = root / "artifacts"
            consumer = root / "consumer"
            artifacts.mkdir()
            consumer.mkdir()
            packed = subprocess.run(
                ["npm", "pack", "--pack-destination", str(artifacts)],
                cwd=MCP_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(packed.returncode, 0, packed.stderr)
            tarballs = list(artifacts.glob("mythify-mcp-*.tgz"))
            self.assertEqual(len(tarballs), 1)
            installed = subprocess.run(
                [
                    "npm",
                    "install",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    str(tarballs[0]),
                ],
                cwd=consumer,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            package_root = consumer / "node_modules" / "mythify-mcp"
            metadata = json.loads((package_root / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["version"], "5.0.0")
            self.assertTrue((package_root / "README.md").is_file())
            self.assertTrue((package_root / "LICENSE").is_file())
            self.assertTrue((package_root / "protocol" / "release-gates.json").is_file())
            self.assertFalse((package_root / "test").exists())


if __name__ == "__main__":
    unittest.main()
