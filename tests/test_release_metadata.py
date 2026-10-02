import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.0.0"


class ReleaseMetadataTests(unittest.TestCase):
    def text(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_runtime_versions_and_release_target_agree(self):
        cli = re.search(r'^VERSION = "([^"]+)"$', self.text("scripts/mythify.py"), re.MULTILINE)
        self.assertEqual(cli.group(1), VERSION)
        self.assertIn("## [{0}] - 2026-10-02".format(VERSION), self.text("CHANGELOG.md"))

    def test_roadmap_is_the_rendered_product_plan(self):
        # Exact-case listing: a case-insensitive filesystem would let a stale
        # roadmap.md satisfy a plain exists() check.
        names = os.listdir(ROOT)
        self.assertIn("ROADMAP.md", names)
        self.assertNotIn("roadmap.md", names)
        roadmap = self.text("ROADMAP.md")
        self.assertIn("mythify product show --markdown", roadmap)
        self.assertIn("- Product: `mythify`", roadmap)

    def test_release_gate_manifest_and_pin_are_gone(self):
        self.assertFalse((ROOT / "protocol" / "release-gates.json").exists())
        self.assertNotIn("RELEASE_GATES_SHA256", self.text("scripts/mythify_protocol.py"))

    def test_release_docs_name_current_assets(self):
        release = self.text("RELEASE-CHECKLIST.md")
        self.assertIn("mythify-cli-{0}.tar.gz".format(VERSION), release)
        self.assertNotIn("docs/humanlayer-integration-research.md", self.text("scripts/package_cli.py"))
        self.assertIn("docs/prose-quality.md", self.text("README.md"))
        self.assertIn("docs/prose-quality.md", self.text("scripts/package_cli.py"))
        self.assertIn("docs/blast-radius.md", self.text("README.md"))
        self.assertIn("docs/blast-radius.md", self.text("scripts/package_cli.py"))


if __name__ == "__main__":
    unittest.main()
