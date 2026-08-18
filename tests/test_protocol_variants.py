import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from mythify_protocol_profiles import (  # noqa: E402
    load_profile_manifest,
    profile_metrics,
    select_loading_profile,
)


class ProtocolVariantTests(unittest.TestCase):
    def test_profiles_are_generated_idempotently_and_checked(self):
        tracked = [
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "CLAUDE.md",
            REPO_ROOT / ".cursorrules",
            REPO_ROOT / "protocol" / "variants" / "AGENTS.md.thin",
            REPO_ROOT / "protocol" / "variants" / "CLAUDE.md.thin",
            REPO_ROOT / "protocol" / "variants" / ".cursorrules.thin",
        ]
        first = subprocess.run(
            [sys.executable, "scripts/build_variants.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
        second = subprocess.run(
            [sys.executable, "scripts/build_variants.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in tracked}
        self.assertEqual(before, after)
        checked = subprocess.run(
            [
                sys.executable,
                "scripts/mythify.py",
                "protocol",
                "check",
                "AGENTS.md",
                "protocol/variants/AGENTS.md.thin",
                "--json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        rows = json.loads(checked.stdout)["checked"]
        copies = [row for row in rows if row["kind"] == "copy"]
        self.assertEqual([row["profile"] for row in copies], ["full", "thin"])

    def test_thin_profile_is_less_than_half_the_full_word_cost(self):
        manifest = load_profile_manifest(REPO_ROOT)
        protocol = (REPO_ROOT / "protocol" / "PROTOCOL.md").read_text(encoding="utf-8")
        metrics = profile_metrics(protocol, manifest)
        self.assertLess(metrics["thin"]["words"], metrics["full"]["words"] / 2)

    def test_auto_selection_fails_closed_without_capabilities(self):
        manifest = load_profile_manifest(REPO_ROOT)
        self.assertEqual(select_loading_profile(manifest), "full")
        self.assertEqual(
            select_loading_profile(
                manifest,
                capabilities={"skill_loading", "reference_loading"},
            ),
            "thin",
        )
        with self.assertRaises(ValueError):
            select_loading_profile(manifest, requested="thin", capabilities={"skill_loading"})

    def test_local_eval_reports_profile_footprint_without_quality_claim(self):
        spec = importlib.util.spec_from_file_location(
            "local_model_eval", SCRIPTS / "local_model_eval.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        footprint = module.instruction_footprint()
        self.assertEqual(footprint["quality_claim"], "none")
        self.assertIn("full", footprint["profiles"])
        self.assertIn("thin", footprint["profiles"])

    def test_installer_selects_explicit_thin_profile_and_defaults_to_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "HOME": str(root / "home"),
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
                "XDG_DATA_HOME": str(root / "data"),
            }
            (root / "home").mkdir()
            prefix = root / "prefix"
            installed = subprocess.run(
                [
                    "sh",
                    "scripts/install_user.sh",
                    "--prefix",
                    str(prefix),
                    "--skip-mcp",
                    "--skip-skills",
                    "--protocol-profile",
                    "thin",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            active_files = list((root / "data" / "mythify").glob("*/cli/protocol/ACTIVE.md"))
            self.assertEqual(len(active_files), 1)
            self.assertIn(
                "<!-- Mythify protocol-profile: thin -->",
                active_files[0].read_text(encoding="utf-8"),
            )
            manifest = json.loads(
                next((root / "data" / "mythify").glob("*/install-manifest.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["config"]["protocol_profile"], "thin")


if __name__ == "__main__":
    unittest.main()
