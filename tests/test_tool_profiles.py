import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ToolProfileManifestTests(unittest.TestCase):
    def setUp(self):
        self.profiles_path = ROOT / "protocol" / "tool-profiles.json"
        self.package_profiles_path = ROOT / "mcp-server" / "protocol" / "tool-profiles.json"
        self.surface_path = ROOT / "protocol" / "surface-manifest.json"
        self.profiles = json.loads(self.profiles_path.read_text())
        self.surface = json.loads(self.surface_path.read_text())

    def test_package_mirror_is_identical(self):
        self.assertEqual(self.profiles_path.read_bytes(), self.package_profiles_path.read_bytes())

    def test_profiles_are_declared_and_full_is_default(self):
        self.assertEqual(self.profiles["default_profile"], "full")
        self.assertEqual(
            set(self.profiles["profiles"]),
            {"core", "workflow", "execution", "quality", "lifecycle", "full"},
        )
        self.assertTrue(self.profiles["profiles"]["full"]["all_tools"])

    def test_declared_tools_exist_on_canonical_surface(self):
        mcp = self.surface["surfaces"]["mcp"]
        canonical = set(mcp["core_tools"] + mcp["fanout_tools"])
        for name, profile in self.profiles["profiles"].items():
            with self.subTest(profile=name):
                self.assertLessEqual(set(profile.get("tools", [])), canonical)


if __name__ == "__main__":
    unittest.main()
