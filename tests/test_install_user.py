import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install_user.sh"
CLI_PACKAGER = REPO_ROOT / "scripts" / "package_cli.py"


class TestUserInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mythify-install-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def run_cmd(self, args, cwd=None, env=None):
        merged_env = dict(os.environ)
        merged_env["HOME"] = str(self.tmp / "home")
        if env:
            for key, value in env.items():
                if value is None:
                    merged_env.pop(key, None)
                else:
                    merged_env[key] = value
        return subprocess.run(
            args,
            cwd=str(cwd or REPO_ROOT),
            env=merged_env,
            capture_output=True,
            text=True,
        )

    def build_cli_artifact(self, output_dir):
        result = self.run_cmd(
            [sys.executable, str(CLI_PACKAGER), "--output-dir", str(output_dir)]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        archives = list(output_dir.glob("mythify-cli-*.tar.gz"))
        self.assertEqual(len(archives), 1, result.stdout)
        return archives[0]

    def install_args(
        self,
        installer,
        prefix,
        project,
        skills_root,
        claude_skills_root,
        hook_root,
    ):
        return [
            "sh",
            str(installer),
            "--prefix",
            str(prefix),
            "--project",
            str(project),
            "--skip-mcp",
            "--skills-root",
            str(skills_root),
            "--claude-skills-root",
            str(claude_skills_root),
            "--install-chat-hook",
            "--hook-root",
            str(hook_root),
        ]

    def snapshot_paths(self, paths):
        snapshot = {}
        for label, root in paths:
            if root.is_file():
                snapshot[(label, ".")] = root.read_bytes()
                continue
            if root.is_dir():
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        snapshot[(label, path.relative_to(root).as_posix())] = path.read_bytes()
        return snapshot

    def test_cli_artifact_is_reproducible_and_complete(self):
        first = self.build_cli_artifact(self.tmp / "artifact-one")
        second = self.build_cli_artifact(self.tmp / "artifact-two")
        self.assertEqual(
            hashlib.sha256(first.read_bytes()).hexdigest(),
            hashlib.sha256(second.read_bytes()).hexdigest(),
        )

        version = first.name.removeprefix("mythify-cli-").removesuffix(".tar.gz")
        root = "mythify-cli-{}".format(version)
        with tarfile.open(first, "r:gz") as archive:
            names = set(archive.getnames())
            markdown = {
                name: archive.extractfile(name).read().decode("utf-8")
                for name in names if name.endswith((".md", ".mdx"))
            }

        required = {
            root + "/LICENSE",
            root + "/README.md",
            root + "/scripts/install_user.sh",
            root + "/scripts/mythify.py",
            root + "/scripts/mythify_classification.py",
            root + "/protocol/PROTOCOL.md",
            root + "/protocol/classification-rules.json",
            root + "/protocol/model-capabilities.json",
            root + "/protocol/operation-registry.json",
            root + "/protocol/workflow-router.json",
            root + "/skills/mythify/SKILL.md",
            root + "/CHANGELOG.md",
            root + "/CONTRIBUTING.md",
            root + "/docs/design.md",
            root + "/docs/start-here.md",
            root + "/docs/evidence/efficacy-reproduction.md",
            root + "/docs/evidence/codex-word-count-2026-07-13.json",
        }
        self.assertTrue(required.issubset(names), sorted(required - names))
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        local_targets = {
            target.split("#", 1)[0]
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", readme)
            if not target.startswith(("http://", "https://", "#"))
        }
        missing_targets = {
            target for target in local_targets if root + "/" + target not in names
        }
        self.assertEqual(missing_targets, set())
        broken = []
        for source, text in markdown.items():
            source_dir = PurePosixPath(source).parent
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                clean = target.split("#", 1)[0]
                if not clean or clean.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (source_dir / clean).as_posix()
                parts = []
                for part in PurePosixPath(resolved).parts:
                    if part == "..":
                        if parts:
                            parts.pop()
                    elif part != ".":
                        parts.append(part)
                normalized = "/".join(parts)
                if normalized not in names:
                    broken.append((source, target, normalized))
        self.assertEqual(broken, [])
        self.assertFalse(any("__pycache__" in name for name in names))
        self.assertFalse(any(name.endswith(".pyc") for name in names))

    def test_release_tag_must_match_cli_and_mcp_version(self):
        current = self.run_cmd([sys.executable, str(REPO_ROOT / "scripts" / "mythify.py"), "--version"])
        expected = current.stdout.strip().removeprefix("Mythify ")
        result = self.run_cmd(
            [sys.executable, str(CLI_PACKAGER), "--check-release-tag", expected]
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        wrong = self.run_cmd(
            [sys.executable, str(CLI_PACKAGER), "--check-release-tag", "v0.0.0"]
        )
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("does not match package version", wrong.stdout + wrong.stderr)

    def test_packaged_skill_references_use_installed_launcher(self):
        references = REPO_ROOT / "skills" / "mythify" / "references"
        for path in references.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    "python3 scripts/mythify.py plan",
                    text,
                    str(path),
                )
                self.assertNotIn(
                    "python3 scripts/mythify.py verify",
                    text,
                    str(path),
                )

    def test_cli_artifact_rejects_missing_required_skill(self):
        broken_root = self.tmp / "broken-source"
        for directory in ("scripts", "protocol", "skills"):
            shutil.copytree(
                REPO_ROOT / directory,
                broken_root / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        shutil.copy2(REPO_ROOT / "LICENSE", broken_root / "LICENSE")
        shutil.copy2(REPO_ROOT / "README.md", broken_root / "README.md")
        shutil.rmtree(broken_root / "skills" / "mythify-work")

        result = self.run_cmd(
            [
                sys.executable,
                str(broken_root / "scripts" / "package_cli.py"),
                "--output-dir",
                str(self.tmp / "broken-output"),
            ],
            cwd=broken_root,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required skill directory", result.stderr)

    def test_cli_artifact_rejects_missing_required_skill_file(self):
        broken_root = self.tmp / "broken-file-source"
        for directory in ("scripts", "protocol", "skills"):
            shutil.copytree(
                REPO_ROOT / directory,
                broken_root / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        shutil.copy2(REPO_ROOT / "LICENSE", broken_root / "LICENSE")
        shutil.copy2(REPO_ROOT / "README.md", broken_root / "README.md")
        (broken_root / "skills" / "mythify-work" / "SKILL.md").unlink()

        result = self.run_cmd(
            [
                sys.executable,
                str(broken_root / "scripts" / "package_cli.py"),
                "--output-dir",
                str(self.tmp / "broken-file-output"),
            ],
            cwd=broken_root,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required skill file", result.stderr)

    def test_self_contained_cli_install_update_and_uninstall_lifecycle(self):
        artifact = self.build_cli_artifact(self.tmp / "artifact")
        extract_root = self.tmp / "extracted"
        extract_root.mkdir()
        shutil.unpack_archive(str(artifact), str(extract_root))
        artifact_root = next(extract_root.iterdir())

        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        claude_skills_root = self.tmp / "claude-skills"
        hook_root = self.tmp / "hooks"
        project = self.tmp / "project"
        project_state = project / ".mythify"
        project_state.mkdir(parents=True)
        sentinel = project_state / "preserve-me.txt"
        sentinel.write_text("project-owned state\n", encoding="utf-8")
        personal_skills = []
        for root in (skills_root, claude_skills_root):
            personal_skill = root / "mythify-personal" / "SKILL.md"
            personal_skill.parent.mkdir(parents=True)
            personal_skill.write_text("user-owned skill\n", encoding="utf-8")
            personal_skills.append(personal_skill)
        env = {"XDG_DATA_HOME": str(data_home)}

        installer = artifact_root / "scripts" / "install_user.sh"
        args = self.install_args(
            installer,
            prefix,
            project,
            skills_root,
            claude_skills_root,
            hook_root,
        )
        result = self.run_cmd(args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)

        mythify_bin = prefix / "bin" / "mythify"
        uninstall_bin = prefix / "bin" / "mythify-uninstall"
        version_result = self.run_cmd([str(mythify_bin), "--version"], env=env)
        self.assertEqual(version_result.returncode, 0, version_result.stderr)
        version = version_result.stdout.strip().removeprefix("Mythify v")
        cli_root = data_home / "mythify" / version / "cli"
        retained_version = data_home / "mythify" / "retained-version" / "keep.txt"
        retained_version.parent.mkdir(parents=True)
        retained_version.write_text("separate install\n", encoding="utf-8")
        self.assertTrue((cli_root / "scripts" / "mythify.py").is_file())
        self.assertTrue((cli_root / "protocol" / "PROTOCOL.md").is_file())
        self.assertTrue(uninstall_bin.is_file())

        launcher_text = mythify_bin.read_text(encoding="utf-8")
        self.assertNotIn(str(artifact_root), launcher_text)
        self.assertIn(str(cli_root), launcher_text)

        shutil.rmtree(extract_root)
        classify_result = self.run_cmd(
            [str(mythify_bin), "classify", "fix failing parser", "--json"],
            cwd=project,
            env=env,
        )
        self.assertEqual(classify_result.returncode, 0, classify_result.stderr)
        self.assertIn('"task_type": "bugfix"', classify_result.stdout)

        obsolete = cli_root / "obsolete-from-previous-install.txt"
        obsolete.write_text("remove on update\n", encoding="utf-8")
        update_root = self.tmp / "update-extracted"
        update_root.mkdir()
        shutil.unpack_archive(str(artifact), str(update_root))
        update_source = next(update_root.iterdir())
        update_args = self.install_args(
            update_source / "scripts" / "install_user.sh",
            prefix,
            project,
            skills_root,
            claude_skills_root,
            hook_root,
        )
        update_result = self.run_cmd(update_args, env=env)
        self.assertEqual(update_result.returncode, 0, update_result.stderr)
        self.assertFalse(obsolete.exists())
        shutil.rmtree(update_root)

        post_update = self.run_cmd([str(mythify_bin), "--version"], env=env)
        self.assertEqual(post_update.returncode, 0, post_update.stderr)
        uninstall_result = self.run_cmd(
            [str(uninstall_bin)], env={"XDG_DATA_HOME": None}
        )
        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)

        self.assertFalse(mythify_bin.exists())
        self.assertFalse(uninstall_bin.exists())
        self.assertFalse((prefix / "bin" / "mythify-mcp").exists())
        self.assertFalse(cli_root.parent.exists())
        self.assertEqual(retained_version.read_text(encoding="utf-8"), "separate install\n")
        for skill in ("mythify", "mythify-work", "mythify-route", "mythify-verify"):
            self.assertFalse((skills_root / skill).exists())
            self.assertFalse((claude_skills_root / skill).exists())
        for personal_skill in personal_skills:
            self.assertEqual(personal_skill.read_text(encoding="utf-8"), "user-owned skill\n")
        self.assertFalse((hook_root / "mythify-chat-report-hook.sh").exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "project-owned state\n")

    def test_invalid_project_fails_before_installation_mutation(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        claude_skills_root = self.tmp / "claude-skills"
        hook_root = self.tmp / "hooks"
        missing_project = self.tmp / "missing-project"

        result = self.run_cmd(
            self.install_args(
                INSTALLER,
                prefix,
                missing_project,
                skills_root,
                claude_skills_root,
                hook_root,
            ),
            env={"XDG_DATA_HOME": str(data_home)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Project directory does not exist", result.stderr)
        self.assertFalse(prefix.exists())
        self.assertFalse((data_home / "mythify").exists())
        self.assertFalse(skills_root.exists())
        self.assertFalse(claude_skills_root.exists())
        self.assertFalse(hook_root.exists())

    def test_installer_rejects_node_below_package_floor_before_mutation(self):
        fake_bin = self.tmp / "fake-bin"
        fake_bin.mkdir()
        fake_node = fake_bin / "node"
        fake_node.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = \"-p\" ]; then printf '%s\\n' '18.20.0'; exit 0; fi\n"
            "exit 99\n",
            encoding="utf-8",
        )
        fake_node.chmod(0o755)
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        result = self.run_cmd(
            [
                "sh",
                str(INSTALLER),
                "--prefix",
                str(prefix),
                "--skip-skills",
            ],
            env={
                "XDG_DATA_HOME": str(data_home),
                "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Node.js 20 or newer", result.stderr)
        self.assertFalse(prefix.exists())
        self.assertFalse((data_home / "mythify").exists())

    def test_installer_reads_mcp_version_from_checkout_path_with_apostrophe(self):
        source_root = self.tmp / "source's-checkout"
        shutil.copytree(REPO_ROOT / "scripts", source_root / "scripts")
        shutil.copytree(REPO_ROOT / "protocol", source_root / "protocol")
        shutil.copytree(
            REPO_ROOT / "mcp-server",
            source_root / "mcp-server",
            ignore=shutil.ignore_patterns("node_modules", "*.tgz"),
        )
        prefix = self.tmp / "apostrophe-prefix"
        data_home = self.tmp / "apostrophe-data"

        result = self.run_cmd(
            [
                "sh",
                str(source_root / "scripts" / "install_user.sh"),
                "--prefix",
                str(prefix),
                "--skip-skills",
            ],
            env={"XDG_DATA_HOME": str(data_home)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((prefix / "bin" / "mythify-mcp").is_file())

    def test_destination_type_failure_leaves_no_partial_install(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills-file"
        skills_root.write_text("not a directory\n", encoding="utf-8")

        result = self.run_cmd(
            [
                "sh",
                str(INSTALLER),
                "--prefix",
                str(prefix),
                "--skip-mcp",
                "--skills-root",
                str(skills_root),
                "--skip-claude-skills",
            ],
            env={"XDG_DATA_HOME": str(data_home)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Skill destination must be a directory", result.stderr)
        self.assertFalse(prefix.exists())
        self.assertFalse((data_home / "mythify").exists())
        self.assertEqual(skills_root.read_text(encoding="utf-8"), "not a directory\n")

    def test_failed_update_restores_complete_prior_install(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        claude_skills_root = self.tmp / "claude-skills"
        hook_root = self.tmp / "hooks"
        project = self.tmp / "project"
        project.mkdir()
        env = {"XDG_DATA_HOME": str(data_home)}
        args = self.install_args(
            INSTALLER,
            prefix,
            project,
            skills_root,
            claude_skills_root,
            hook_root,
        )
        args.remove("--skip-mcp")
        first = self.run_cmd(args, env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        mythify_bin = prefix / "bin" / "mythify"
        uninstall_bin = prefix / "bin" / "mythify-uninstall"
        version_result = self.run_cmd([str(mythify_bin), "--version"])
        self.assertEqual(version_result.returncode, 0, version_result.stderr)
        version = version_result.stdout.strip().removeprefix("Mythify v")
        install_root = data_home / "mythify" / version
        tracked = [
            ("install", install_root),
            ("mythify", mythify_bin),
            ("uninstall", uninstall_bin),
            ("mcp", prefix / "bin" / "mythify-mcp"),
            ("hook", hook_root / "mythify-chat-report-hook.sh"),
        ]
        for skill in ("mythify", "mythify-work", "mythify-route", "mythify-verify"):
            tracked.append(("codex-" + skill, skills_root / skill))
            tracked.append(("claude-" + skill, claude_skills_root / skill))
        before = self.snapshot_paths(tracked)

        failed = self.run_cmd(
            args,
            env={
                "XDG_DATA_HOME": str(data_home),
                "MYTHIFY_INSTALL_TEST_FAIL_AFTER_SKILL_COPY": "1",
            },
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("injected failure after skill copy", failed.stderr)
        self.assertEqual(self.snapshot_paths(tracked), before)

        uninstall_result = self.run_cmd(
            [str(uninstall_bin)], env={"XDG_DATA_HOME": None}
        )
        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
        self.assertFalse(install_root.exists())

    def test_failed_first_install_removes_new_artifacts_only(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        claude_skills_root = self.tmp / "claude-skills"
        hook_root = self.tmp / "hooks"
        personal = skills_root / "mythify-personal" / "SKILL.md"
        personal.parent.mkdir(parents=True)
        personal.write_text("unrelated skill\n", encoding="utf-8")
        project = self.tmp / "project"
        project.mkdir()

        args = self.install_args(
            INSTALLER,
            prefix,
            project,
            skills_root,
            claude_skills_root,
            hook_root,
        )
        args.remove("--skip-mcp")
        failed = self.run_cmd(
            args,
            env={
                "XDG_DATA_HOME": str(data_home),
                "MYTHIFY_INSTALL_TEST_FAIL_AFTER_SKILL_COPY": "1",
            },
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("injected failure after skill copy", failed.stderr)
        self.assertFalse((prefix / "bin" / "mythify").exists())
        self.assertFalse((prefix / "bin" / "mythify-uninstall").exists())
        self.assertFalse((prefix / "bin" / "mythify-mcp").exists())
        self.assertFalse(prefix.exists())
        self.assertFalse((data_home / "mythify").exists())
        for skill in ("mythify", "mythify-work", "mythify-route", "mythify-verify"):
            self.assertFalse((skills_root / skill).exists())
            self.assertFalse((claude_skills_root / skill).exists())
        self.assertFalse((hook_root / "mythify-chat-report-hook.sh").exists())
        self.assertFalse(hook_root.exists())
        self.assertFalse(claude_skills_root.exists())
        self.assertEqual(personal.read_text(encoding="utf-8"), "unrelated skill\n")
        self.assertFalse((project / ".mythify").exists())
        self.assertFalse((project / ".gitignore").exists())

    def test_uninstall_without_ownership_manifest_preserves_artifacts(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        claude_skills_root = self.tmp / "claude-skills"
        hook_root = self.tmp / "hooks"
        sentinels = []
        for path in (
            prefix / "bin" / "mythify",
            prefix / "bin" / "mythify-mcp",
            skills_root / "mythify" / "SKILL.md",
            claude_skills_root / "mythify" / "SKILL.md",
            hook_root / "mythify-chat-report-hook.sh",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unowned sentinel\n", encoding="utf-8")
            sentinels.append(path)

        result = self.run_cmd(
            [
                "sh",
                str(INSTALLER),
                "--uninstall",
                "--data-root",
                str(data_home / "mythify" / "4.2.0"),
                "--prefix",
                str(prefix),
                "--skills-root",
                str(skills_root),
                "--claude-skills-root",
                str(claude_skills_root),
                "--install-chat-hook",
                "--hook-root",
                str(hook_root),
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ownership manifest", result.stderr)
        for path in sentinels:
            self.assertEqual(path.read_text(encoding="utf-8"), "unowned sentinel\n")

    def test_uninstall_with_content_mismatch_preserves_entire_install(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        result = self.run_cmd(
            [
                "sh",
                str(INSTALLER),
                "--prefix",
                str(prefix),
                "--skip-mcp",
                "--skip-skills",
            ],
            env={"XDG_DATA_HOME": str(data_home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        mythify_bin = prefix / "bin" / "mythify"
        uninstall_bin = prefix / "bin" / "mythify-uninstall"
        version_result = self.run_cmd([str(mythify_bin), "--version"])
        self.assertEqual(version_result.returncode, 0, version_result.stderr)
        version = version_result.stdout.strip().removeprefix("Mythify v")
        install_root = data_home / "mythify" / version
        mythify_bin.write_text("user replacement\n", encoding="utf-8")

        uninstall_result = self.run_cmd(
            [str(uninstall_bin)], env={"XDG_DATA_HOME": None}
        )
        self.assertNotEqual(uninstall_result.returncode, 0)
        self.assertIn("file content does not match", uninstall_result.stderr)
        self.assertEqual(mythify_bin.read_text(encoding="utf-8"), "user replacement\n")
        self.assertTrue(uninstall_bin.is_file())
        self.assertTrue((install_root / "cli" / "scripts" / "mythify.py").is_file())
        self.assertTrue((install_root / "install-manifest.json").is_file())

    def test_uninstaller_preserves_artifacts_skipped_during_install(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        claude_skills_root = self.tmp / "claude-skills"
        hook_root = self.tmp / "hooks"
        preserved = []
        for path in (
            skills_root / "mythify" / "SKILL.md",
            claude_skills_root / "mythify" / "SKILL.md",
            hook_root / "mythify-chat-report-hook.sh",
            prefix / "bin" / "mythify-mcp",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("pre-existing artifact\n", encoding="utf-8")
            preserved.append(path)

        result = self.run_cmd(
            [
                "sh",
                str(INSTALLER),
                "--prefix",
                str(prefix),
                "--skip-mcp",
                "--skip-skills",
                "--skills-root",
                str(skills_root),
                "--claude-skills-root",
                str(claude_skills_root),
                "--hook-root",
                str(hook_root),
            ],
            env={"XDG_DATA_HOME": str(data_home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        mythify_bin = prefix / "bin" / "mythify"
        uninstall_bin = prefix / "bin" / "mythify-uninstall"
        version_result = self.run_cmd([str(mythify_bin), "--version"])
        self.assertEqual(version_result.returncode, 0, version_result.stderr)
        version = version_result.stdout.strip().removeprefix("Mythify v")
        install_root = data_home / "mythify" / version
        mcp_sentinel = install_root / "mcp-server" / "preserve.txt"
        mcp_sentinel.parent.mkdir(parents=True)
        mcp_sentinel.write_text("pre-existing MCP\n", encoding="utf-8")

        uninstall_result = self.run_cmd(
            [str(uninstall_bin)], env={"XDG_DATA_HOME": None}
        )
        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
        self.assertFalse(mythify_bin.exists())
        self.assertFalse(uninstall_bin.exists())
        self.assertEqual(mcp_sentinel.read_text(encoding="utf-8"), "pre-existing MCP\n")
        for path in preserved:
            self.assertEqual(path.read_text(encoding="utf-8"), "pre-existing artifact\n")

    def test_installs_chat_skills_and_hook_helper(self):
        prefix = self.tmp / "prefix"
        skills_root = self.tmp / "skills"
        claude_skills_root = self.tmp / "claude-skills"
        hook_root = self.tmp / "hooks"

        result = self.run_cmd(
            [
                "sh",
                str(INSTALLER),
                "--prefix",
                str(prefix),
                "--skip-mcp",
                "--skills-root",
                str(skills_root),
                "--claude-skills-root",
                str(claude_skills_root),
                "--install-chat-hook",
                "--hook-root",
                str(hook_root),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        mythify_bin = prefix / "bin" / "mythify"
        self.assertTrue(mythify_bin.is_file())
        self.assertTrue(os.access(mythify_bin, os.X_OK))

        for skill in ("mythify", "mythify-work", "mythify-route", "mythify-verify"):
            for root in (skills_root, claude_skills_root):
                skill_file = root / skill / "SKILL.md"
                self.assertTrue(skill_file.is_file(), skill_file)

        hook = hook_root / "mythify-chat-report-hook.sh"
        self.assertTrue(hook.is_file())
        self.assertTrue(hook.stat().st_mode & stat.S_IXUSR)

        help_result = self.run_cmd([str(mythify_bin), "--help"])
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Mythify v", help_result.stdout)

        project = self.tmp / "project"
        project.mkdir()
        init_result = self.run_cmd([str(mythify_bin), "init"], cwd=project)
        self.assertEqual(init_result.returncode, 0, init_result.stderr)
        mark_result = self.run_cmd(
            [str(mythify_bin), "report", "--cursor", "chat", "--mark"],
            cwd=project,
        )
        self.assertEqual(mark_result.returncode, 0, mark_result.stderr)
        claim_result = self.run_cmd(
            [
                str(mythify_bin),
                "verify",
                "claim",
                "chat hook attestation",
                "installer test evidence",
            ],
            cwd=project,
        )
        self.assertEqual(claim_result.returncode, 0, claim_result.stderr)

        hook_result = self.run_cmd(
            [str(hook)],
            cwd=project,
            env={"MYTHIFY_BIN": str(mythify_bin)},
        )
        self.assertEqual(hook_result.returncode, 0, hook_result.stderr)
        self.assertIn("chat hook attestation", hook_result.stdout)


class TestSkillInvocationParity(unittest.TestCase):
    """Every Mythify chat skill must advertise both runtime invocations.

    Claude Code invokes a skill as /<name>; Codex invokes it as $<name>. A
    single SKILL.md serves both runtimes, so it must document both forms in its
    body and its frontmatter description.
    """

    SKILLS = ("mythify", "mythify-route", "mythify-verify", "mythify-work")

    def test_each_skill_documents_both_invocations(self):
        for skill in self.SKILLS:
            skill_md = REPO_ROOT / "skills" / skill / "SKILL.md"
            self.assertTrue(skill_md.is_file(), skill_md)
            text = skill_md.read_text(encoding="utf-8")
            self.assertIn(
                "/" + skill,
                text,
                "{} is missing the Claude /{} invocation".format(skill_md, skill),
            )
            self.assertIn(
                "$" + skill,
                text,
                "{} is missing the Codex ${} invocation".format(skill_md, skill),
            )


if __name__ == "__main__":
    unittest.main()
