import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionTest(unittest.TestCase):
    def test_changelog_comparison_links_cover_every_release(self):
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        headings = re.findall(
            r"^## \[([^]]+)\](?:\s+-\s+.*)?$", changelog, re.MULTILINE
        )
        references = dict(
            re.findall(r"^\[([^]]+)\]:\s+(\S+)$", changelog, re.MULTILINE)
        )

        self.assertGreater(len(headings), 1)
        self.assertEqual(headings[0], "Unreleased")
        self.assertEqual(set(references), set(headings))

        releases = headings[1:]
        base = "https://github.com/hannsxpeter/mythify"
        self.assertEqual(
            references["Unreleased"],
            "{}/compare/v{}...HEAD".format(base, releases[0]),
        )
        for current, previous in zip(releases, releases[1:]):
            self.assertEqual(
                references[current],
                "{}/compare/v{}...v{}".format(base, previous, current),
            )
        self.assertEqual(
            references[releases[-1]],
            "{}/releases/tag/v{}".format(base, releases[-1]),
        )

    def test_release_identity_is_consistent(self):
        cli = (REPO_ROOT / "scripts" / "mythify.py").read_text(encoding="utf-8")
        version = re.search(r'^VERSION = "([^"]+)"$', cli, re.MULTILINE).group(1)
        release = (REPO_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
        roadmap = (REPO_ROOT / "roadmap.md").read_text(encoding="utf-8")
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("Current release target: `v{}`".format(version), release)
        self.assertIn("Current release target: `v{}`".format(version), roadmap)
        # Date as a pattern, not a literal: a pinned date breaks on every
        # release for reasons unrelated to what this test covers.
        self.assertRegex(
            changelog,
            re.compile(
                r"^## \[{}\] - \d{{4}}-\d{{2}}-\d{{2}}$".format(re.escape(version)),
                re.MULTILINE,
            ),
        )
        self.assertIn("mythify-cli-{}.tar.gz".format(version), release)
        self.assertNotIn("mythify-mcp-{}.tgz".format(version), release)

        self.assertIn("tags:", workflow)
        self.assertNotIn("types: [published]", workflow)
        release_index = workflow.index("gh release create")
        required_before_release = [
            'python3 scripts/package_cli.py --check-release-tag "$TAG"',
            "python3 -m unittest discover -s tests -v",
            "python3 -m unittest tests.test_install_user tests.test_release_checksums tests.test_mcp_server tests.test_release_version -v",
            "python3 scripts/check_prose_quality.py",
            "python3 scripts/check_runtime_source_size.py",
            "python3 scripts/mythify.py protocol check AGENTS.md CLAUDE.md",
            "git diff --check",
            "python3 scripts/build_release_checksums.py",
            "--check dist/release-assets/SHA256SUMS",
        ]
        for command in required_before_release:
            self.assertIn(command, workflow)
            self.assertLess(workflow.index(command), release_index, command)
        for retired in ("npm ", "node ", "mcp-server", "setup-node", "mythify-mcp-"):
            self.assertNotIn(retired, workflow)


if __name__ == "__main__":
    unittest.main()
