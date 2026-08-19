import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "5.8.0"


class ReleaseMetadataTests(unittest.TestCase):
    def text(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_runtime_versions_and_release_target_agree(self):
        cli = re.search(r'^VERSION = "([^"]+)"$', self.text("scripts/mythify.py"), re.MULTILINE)
        package = json.loads(self.text("mcp-server/package.json"))
        lock = json.loads(self.text("mcp-server/package-lock.json"))
        self.assertEqual(cli.group(1), VERSION)
        self.assertEqual(package["version"], VERSION)
        self.assertEqual(lock["version"], VERSION)
        self.assertIn("`v{0}`".format(VERSION), self.text("roadmap.md"))
        self.assertIn("## [{0}] - 2026-08-19".format(VERSION), self.text("CHANGELOG.md"))

    def test_public_surface_and_profile_mirrors_agree(self):
        surface = json.loads(self.text("protocol/surface-manifest.json"))
        package_surface = json.loads(self.text("mcp-server/protocol/surface-manifest.json"))
        profiles = json.loads(self.text("protocol/tool-profiles.json"))
        package_profiles = json.loads(self.text("mcp-server/protocol/tool-profiles.json"))
        self.assertEqual(surface, package_surface)
        self.assertEqual(profiles, package_profiles)
        self.assertEqual(surface["surfaces"]["cli"]["command_count"], 36)
        self.assertEqual(surface["surfaces"]["mcp"]["total_tools"], 63)
        self.assertEqual(profiles["default_profile"], "full")

    def test_release_gate_digest_and_tag_are_pinned(self):
        gates_path = ROOT / "protocol" / "release-gates.json"
        gates = json.loads(gates_path.read_text(encoding="utf-8"))
        commands = [command for gate in gates["gates"] for command in gate["commands"]]
        self.assertIn("python3 scripts/package_cli.py --check-release-tag v{0}".format(VERSION), commands)
        digest = hashlib.sha256(gates_path.read_bytes()).hexdigest()
        protocol_source = self.text("scripts/mythify_protocol.py")
        self.assertIn('RELEASE_GATES_SHA256 = "{0}"'.format(digest), protocol_source)

    def test_release_docs_name_current_assets_and_research(self):
        release = self.text("docs/release.md")
        self.assertIn("mythify-cli-{0}.tar.gz".format(VERSION), release)
        self.assertIn("mythify-mcp-{0}.tgz".format(VERSION), release)
        self.assertIn("docs/humanlayer-integration-research.md", self.text("README.md"))
        self.assertIn("docs/humanlayer-integration-research.md", self.text("scripts/package_cli.py"))
        self.assertIn("docs/prose-quality.md", self.text("README.md"))
        self.assertIn("docs/prose-quality.md", self.text("scripts/package_cli.py"))
        self.assertIn("docs/blast-radius.md", self.text("README.md"))
        self.assertIn("docs/blast-radius.md", self.text("scripts/package_cli.py"))


if __name__ == "__main__":
    unittest.main()
