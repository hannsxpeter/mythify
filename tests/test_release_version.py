import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionTest(unittest.TestCase):
    def test_release_identity_is_consistent(self):
        cli = (REPO_ROOT / "scripts" / "mythify.py").read_text(encoding="utf-8")
        version = re.search(r'^VERSION = "([^"]+)"$', cli, re.MULTILINE).group(1)
        package = json.loads(
            (REPO_ROOT / "mcp-server" / "package.json").read_text(encoding="utf-8")
        )
        lock = json.loads(
            (REPO_ROOT / "mcp-server" / "package-lock.json").read_text(encoding="utf-8")
        )
        release = (REPO_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
        roadmap = (REPO_ROOT / "roadmap.md").read_text(encoding="utf-8")
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        gates = json.loads(
            (REPO_ROOT / "protocol" / "release-gates.json").read_text(encoding="utf-8")
        )
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertEqual(package["version"], version)
        self.assertEqual(lock["version"], version)
        self.assertEqual(lock["packages"][""]["version"], version)
        self.assertIn("Current release target: `v{}`".format(version), release)
        self.assertIn("Current release target: `v{}`".format(version), roadmap)
        self.assertIn("## [{}] - 2026-07-13".format(version), changelog)
        self.assertIn("mythify-cli-{}.tar.gz".format(version), release)
        self.assertIn("mythify-mcp-{}.tgz".format(version), release)
        tag_commands = [
            command
            for gate in gates["gates"] if gate["id"] == "release_tag"
            for command in gate["commands"]
        ]
        self.assertEqual(
            tag_commands,
            ["python3 scripts/package_cli.py --check-release-tag v{}".format(version)],
        )

        self.assertIn("tags:", workflow)
        self.assertNotIn("types: [published]", workflow)
        release_index = workflow.index("gh release create")
        required_before_release = [
            "python3 -m unittest discover -s tests -v",
            "npm test --prefix mcp-server",
            "python3 -m unittest tests.test_interop -v",
            "python3 -m unittest tests.test_install_user tests.test_release_checksums tests.test_mcp_package tests.test_release_version -v",
            "node scripts/check_surface_manifest.mjs",
            "node scripts/check_classification_rules_manifest.mjs",
            "node scripts/build_registry_docs.mjs --check",
            "python3 scripts/check_runtime_source_size.py",
            "python3 scripts/mythify.py protocol check CLAUDE.md AGENTS.md .cursorrules",
            "git diff --check",
            "npm audit --prefix mcp-server --audit-level=moderate",
            "python3 scripts/build_release_checksums.py",
            "--check dist/release-assets/SHA256SUMS",
        ]
        for command in required_before_release:
            self.assertIn(command, workflow)
            self.assertLess(workflow.index(command), release_index, command)


if __name__ == "__main__":
    unittest.main()
