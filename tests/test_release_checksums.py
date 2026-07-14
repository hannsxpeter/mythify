import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKSUMS = REPO_ROOT / "scripts" / "build_release_checksums.py"


class ReleaseChecksumTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mythify-release-checksums-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def run_tool(self, *args):
        return subprocess.run(
            [sys.executable, str(CHECKSUMS), *map(str, args)],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_flat_download_manifest_verifies(self):
        source = self.tmp / "source"
        flat = self.tmp / "download"
        (source / "dist").mkdir(parents=True)
        (source / "mcp-server").mkdir()
        flat.mkdir()
        assets = [
            source / "dist" / "mythify.skill",
            source / "dist" / "mythify-cli-5.0.0.tar.gz",
            source / "mcp-server" / "mythify-mcp-5.0.0.tgz",
        ]
        for index, asset in enumerate(assets, 1):
            asset.write_bytes(("asset-{}\n".format(index)).encode("ascii"))
        manifest = flat / "SHA256SUMS"
        built = self.run_tool("--output", manifest, *assets)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        for asset in assets:
            shutil.copy2(asset, flat / asset.name)

        text = manifest.read_text(encoding="utf-8")
        self.assertNotIn("dist/", text)
        self.assertNotIn("mcp-server/", text)
        checked = self.run_tool("--check", manifest, "--directory", flat)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_manifest_rejects_path_prefixed_and_changed_assets(self):
        flat = self.tmp / "download"
        flat.mkdir()
        asset = flat / "asset.tgz"
        asset.write_text("original\n", encoding="utf-8")
        manifest = flat / "SHA256SUMS"
        self.assertEqual(self.run_tool("--output", manifest, asset).returncode, 0)
        asset.write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(
            self.run_tool("--check", manifest, "--directory", flat).returncode,
            0,
        )
        manifest.write_text("0" * 64 + "  nested/asset.tgz\n", encoding="utf-8")
        prefixed = self.run_tool("--check", manifest, "--directory", flat)
        self.assertNotEqual(prefixed.returncode, 0)
        self.assertIn("flat asset basename", prefixed.stdout)


if __name__ == "__main__":
    unittest.main()
