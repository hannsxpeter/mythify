import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install_user.sh"
CLI_PACKAGER = REPO_ROOT / "scripts" / "package_cli.py"
SKILLS = ("mythify", "mythify-work", "mythify-route", "mythify-verify")

if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
# The model, vendor, and host denylist lives in scripts/lint.py.
import lint  # noqa: E402


class TestUserInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mythify-install-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def run_cmd(self, args, cwd=None, env=None, input_text=None):
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
            input=input_text,
            timeout=300,
        )

    def mcp_tool_names(self, launcher, cwd, env=None):
        """Handshake with an installed mythify-mcp launcher and list its tools."""
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "mythify-install-test", "version": "1.0.0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        result = self.run_cmd(
            [str(launcher)],
            cwd=cwd,
            env=env,
            input_text="".join(json.dumps(item) + "\n" for item in requests),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = {}
        for line in result.stdout.splitlines():
            message = json.loads(line)
            responses[message["id"]] = message
        self.assertEqual(responses[1]["result"]["serverInfo"]["name"], "mythify-mcp")
        return [tool["name"] for tool in responses[2]["result"]["tools"]]

    def build_cli_artifact(self, output_dir):
        result = self.run_cmd(
            [sys.executable, str(CLI_PACKAGER), "--output-dir", str(output_dir)]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        archives = list(output_dir.glob("mythify-cli-*.tar.gz"))
        self.assertEqual(len(archives), 1, result.stdout)
        return archives[0]

    def install_args(self, installer, prefix, project, *skills_roots):
        args = ["sh", str(installer), "--prefix", str(prefix), "--project", str(project)]
        for root in skills_roots:
            args.extend(["--skills-root", str(root)])
        return args

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
            root + "/scripts/mythify_mcp.py",
            root + "/scripts/check_prose_quality.py",
            root + "/scripts/mythify_classification.py",
            root + "/protocol/PROTOCOL.md",
            root + "/protocol/prose-quality.json",
            root + "/protocol/classification-rules.json",
            root + "/protocol/workflow-router.json",
            root + "/skills/mythify/SKILL.md",
            root + "/AGENTS.md",
            root + "/CLAUDE.md",
            root + "/CHANGELOG.md",
            root + "/CONTRIBUTING.md",
            root + "/ROADMAP.md",
            root + "/docs/architecture.md",
            root + "/docs/commands.md",
            root + "/docs/mcp.md",
            root + "/docs/product-planning.md",
            root + "/docs/start-here.md",
            root + "/docs/prose-quality.md",
            root + "/docs/evidence/efficacy-reproduction.md",
            root + "/docs/evidence/efficacy-smoke-2026-07-13.json",
        }
        self.assertTrue(required.issubset(names), sorted(required - names))
        removed = {
            root + "/scripts/mythify_chat_report_hook.sh",
            root + "/protocol/release-gates.json",
            root + "/protocol/loading-profiles.json",
            root + "/protocol/operation-registry.json",
            root + "/protocol/artifact-hygiene.json",
            root + "/docs/artifact-hygiene.md",
            root + "/docs/design.md",
            root + "/docs/release.md",
            root + "/docs/research-report.md",
            root + "/docs/humanlayer-integration-research.md",
        }
        self.assertEqual(removed & names, set())
        self.assertFalse(any("/protocol/variants/" in name for name in names))
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

    def test_release_tag_must_match_cli_version(self):
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
        second_skills_root = self.tmp / "second-skills"
        project = self.tmp / "project"
        project_state = project / ".mythify"
        project_state.mkdir(parents=True)
        sentinel = project_state / "preserve-me.txt"
        sentinel.write_text("project-owned state\n", encoding="utf-8")
        personal_skills = []
        for root in (skills_root, second_skills_root):
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
            second_skills_root,
        )
        result = self.run_cmd(args, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("command: {}".format(prefix / "bin" / "mythify-mcp"), result.stdout)
        self.assertIn("env: MYTHIFY_DIR={}".format(project.resolve() / ".mythify"), result.stdout)

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
        mcp_bin = prefix / "bin" / "mythify-mcp"
        mcp_launcher_text = mcp_bin.read_text(encoding="utf-8")
        self.assertIn(str(cli_root / "scripts" / "mythify.py"), mcp_launcher_text)
        self.assertIn(' mcp "$@"', mcp_launcher_text)

        shutil.rmtree(extract_root)
        tool_names = self.mcp_tool_names(mcp_bin, cwd=project, env=env)
        self.assertIn("verify_run", tool_names)
        self.assertIn("mythify", tool_names)
        route_result = self.run_cmd(
            [str(mythify_bin), "route", "fix failing parser", "--json"],
            cwd=project,
            env=env,
        )
        self.assertEqual(route_result.returncode, 0, route_result.stderr)
        self.assertIn('"task_type": "bugfix"', route_result.stdout)
        self.assertIn('"chooser": "host"', route_result.stdout)

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
            second_skills_root,
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
        for skill in SKILLS:
            self.assertFalse((skills_root / skill).exists())
            self.assertFalse((second_skills_root / skill).exists())
        for personal_skill in personal_skills:
            self.assertEqual(personal_skill.read_text(encoding="utf-8"), "user-owned skill\n")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "project-owned state\n")

    def test_invalid_project_fails_before_installation_mutation(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        second_skills_root = self.tmp / "second-skills"
        missing_project = self.tmp / "missing-project"

        result = self.run_cmd(
            self.install_args(
                INSTALLER,
                prefix,
                missing_project,
                skills_root,
                second_skills_root,
            ),
            env={"XDG_DATA_HOME": str(data_home)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Project directory does not exist", result.stderr)
        self.assertFalse(prefix.exists())
        self.assertFalse((data_home / "mythify").exists())
        self.assertFalse(skills_root.exists())
        self.assertFalse(second_skills_root.exists())

    def test_mcp_launcher_works_from_checkout_path_with_apostrophe(self):
        source_root = self.tmp / "source's-checkout"
        shutil.copytree(
            REPO_ROOT / "scripts",
            source_root / "scripts",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copytree(REPO_ROOT / "protocol", source_root / "protocol")
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
        mcp_bin = prefix / "bin" / "mythify-mcp"
        self.assertTrue(os.access(mcp_bin, os.X_OK))
        project = self.tmp / "apostrophe-project"
        project.mkdir()
        tool_names = self.mcp_tool_names(
            mcp_bin, cwd=project, env={"XDG_DATA_HOME": str(data_home)}
        )
        self.assertIn("step", tool_names)

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
                "--skills-root",
                str(skills_root),
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
        second_skills_root = self.tmp / "second-skills"
        project = self.tmp / "project"
        project.mkdir()
        env = {"XDG_DATA_HOME": str(data_home)}
        args = self.install_args(
            INSTALLER,
            prefix,
            project,
            skills_root,
            second_skills_root,
        )
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
        ]
        for skill in SKILLS:
            tracked.append(("first-" + skill, skills_root / skill))
            tracked.append(("second-" + skill, second_skills_root / skill))
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
        second_skills_root = self.tmp / "second-skills"
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
            second_skills_root,
        )
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
        for skill in SKILLS:
            self.assertFalse((skills_root / skill).exists())
            self.assertFalse((second_skills_root / skill).exists())
        self.assertFalse(second_skills_root.exists())
        self.assertEqual(personal.read_text(encoding="utf-8"), "unrelated skill\n")
        self.assertFalse((project / ".mythify").exists())
        self.assertFalse((project / ".gitignore").exists())

    def test_uninstall_without_ownership_manifest_preserves_artifacts(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        skills_root = self.tmp / "skills"
        second_skills_root = self.tmp / "second-skills"
        sentinels = []
        for path in (
            prefix / "bin" / "mythify",
            prefix / "bin" / "mythify-mcp",
            skills_root / "mythify" / "SKILL.md",
            second_skills_root / "mythify" / "SKILL.md",
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
                "--skills-root",
                str(second_skills_root),
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
        second_skills_root = self.tmp / "second-skills"
        preserved = []
        for path in (
            skills_root / "mythify" / "SKILL.md",
            second_skills_root / "mythify" / "SKILL.md",
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
                "--skip-skills",
                "--skills-root",
                str(skills_root),
                "--skills-root",
                str(second_skills_root),
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
        unowned_sentinel = install_root / "unowned" / "preserve.txt"
        unowned_sentinel.parent.mkdir(parents=True)
        unowned_sentinel.write_text("pre-existing data\n", encoding="utf-8")

        uninstall_result = self.run_cmd(
            [str(uninstall_bin)], env={"XDG_DATA_HOME": None}
        )
        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
        self.assertFalse(mythify_bin.exists())
        self.assertFalse(uninstall_bin.exists())
        self.assertFalse((prefix / "bin" / "mythify-mcp").exists())
        self.assertEqual(unowned_sentinel.read_text(encoding="utf-8"), "pre-existing data\n")
        for path in preserved:
            self.assertEqual(path.read_text(encoding="utf-8"), "pre-existing artifact\n")

    def test_skip_mcp_is_an_accepted_no_op(self):
        prefix = self.tmp / "prefix"
        data_home = self.tmp / "xdg-data"
        env = {"XDG_DATA_HOME": str(data_home)}
        result = self.run_cmd(
            ["sh", str(INSTALLER), "--prefix", str(prefix), "--skip-mcp", "--skip-skills"],
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        mcp_bin = prefix / "bin" / "mythify-mcp"
        self.assertTrue(os.access(mcp_bin, os.X_OK))
        self.assertNotIn("--skip-mcp", (prefix / "bin" / "mythify-uninstall").read_text(encoding="utf-8"))
        uninstall_result = self.run_cmd(
            [str(prefix / "bin" / "mythify-uninstall")], env={"XDG_DATA_HOME": None}
        )
        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
        self.assertFalse(mcp_bin.exists())

    def test_installs_chat_skills(self):
        prefix = self.tmp / "prefix"
        skills_root = self.tmp / "skills"
        second_skills_root = self.tmp / "second-skills"

        result = self.run_cmd(
            [
                "sh",
                str(INSTALLER),
                "--prefix",
                str(prefix),
                "--skills-root",
                str(skills_root),
                "--skills-root",
                str(second_skills_root),
            ],
            env={"XDG_DATA_HOME": str(self.tmp / "xdg-data")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        mythify_bin = prefix / "bin" / "mythify"
        self.assertTrue(mythify_bin.is_file())
        self.assertTrue(os.access(mythify_bin, os.X_OK))

        for skill in SKILLS:
            for root in (skills_root, second_skills_root):
                skill_file = root / skill / "SKILL.md"
                self.assertTrue(skill_file.is_file(), skill_file)
                self.assertFalse((root / skill / "agents").exists())

        help_result = self.run_cmd([str(mythify_bin), "--help"])
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Mythify v", help_result.stdout)
        cli_roots = list((self.tmp / "xdg-data").glob("mythify/*/cli"))
        self.assertEqual(len(cli_roots), 1, cli_roots)
        self.assertFalse((cli_roots[0] / "protocol" / "ACTIVE.md").exists())
        self.assertFalse((cli_roots[0] / "scripts" / "mythify_chat_report_hook.sh").exists())

    def test_removed_installer_options_are_rejected(self):
        for option in (
            "--install-chat-hook",
            "--protocol-profile",
            "--hook-root",
            "--claude-skills-root",
            "--skip-claude-skills",
        ):
            with self.subTest(option=option):
                args = ["sh", str(INSTALLER), "--prefix", str(self.tmp / "prefix"), option]
                if option not in ("--install-chat-hook", "--skip-claude-skills"):
                    args.append("value")
                result = self.run_cmd(args, env={"XDG_DATA_HOME": str(self.tmp / "xdg")})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unknown option: " + option, result.stderr)
                self.assertFalse((self.tmp / "prefix").exists())


    # -- Host-neutral skill roots, legacy manifests, and upgrade cleanup -----

    def snapshot_tree(self, root):
        """Every path under ROOT with file bytes, so any write shows up."""
        snapshot = {}
        for path in sorted(root.rglob("*")):
            key = path.relative_to(root).as_posix()
            snapshot[key] = path.read_bytes() if path.is_file() else None
        return snapshot

    def make_host_home(self, *names):
        home = self.tmp / "home"
        for name in names:
            (home / name).mkdir(parents=True)
            (home / name / "settings.txt").write_text("host-owned\n", encoding="utf-8")
        home.mkdir(exist_ok=True)
        return home

    def neutral_env(self, **extra):
        env = {
            "XDG_DATA_HOME": str(self.tmp / "xdg-data"),
            "CLAUDE_CONFIG_DIR": None,
            "CODEX_HOME": None,
        }
        env.update(extra)
        return env

    def make_legacy_install(self, data_home, prefix, roots, version="5.0.0"):
        """Write a 5.x-shaped install: schema 1 manifest with two skill roots."""
        install_root = data_home / "mythify" / version
        (install_root / "cli" / "scripts").mkdir(parents=True)
        (install_root / "cli" / "scripts" / "mythify.py").write_text("# legacy\n", encoding="utf-8")
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        token = "legacy-token-5x"
        files = {}
        for name in ("mythify", "mythify-mcp", "mythify-uninstall"):
            launcher = prefix / "bin" / name
            launcher.write_text("#!/usr/bin/env sh\nexec legacy-{}\n".format(name), encoding="utf-8")
            files[str(launcher.resolve())] = hashlib.sha256(launcher.read_bytes()).hexdigest()
        directories = [install_root / "cli"]
        for root in roots:
            for skill in SKILLS:
                directory = root / skill
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text("legacy skill\n", encoding="utf-8")
                directories.append(directory)
        for directory in directories:
            (directory / ".mythify-owned").write_text(token + "\n", encoding="utf-8")
        manifest = {
            "schema": 1,
            "token": token,
            "skill_names": list(SKILLS),
            "config": {
                "install_root": str(install_root.resolve()),
                "prefix": str(prefix.resolve()),
                "skills_root": str(roots[0].resolve()),
                "claude_skills_root": str(roots[1].resolve()),
                "skip_skills": False,
                "skip_claude_skills": False,
            },
            "files": files,
            "directories": [str(directory.resolve()) for directory in directories],
        }
        (install_root / "install-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return install_root

    def test_repeated_skills_root_writes_nothing_outside_the_given_roots(self):
        home = self.make_host_home(".claude", ".codex", ".cursor", ".agents")
        before = self.snapshot_tree(home)
        prefix = self.tmp / "prefix"
        roots = [self.tmp / "roots" / "one", self.tmp / "roots" / "two", self.tmp / "roots" / "three"]
        args = ["sh", str(INSTALLER), "--prefix", str(prefix)]
        for root in roots + roots[:1]:
            args.extend(["--skills-root", str(root)])
        result = self.run_cmd(args, env=self.neutral_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        for root in roots:
            for skill in SKILLS:
                self.assertTrue((root / skill / "SKILL.md").is_file(), root / skill)
        self.assertEqual(sorted(path.name for path in (self.tmp / "roots").iterdir()), ["one", "three", "two"])
        self.assertEqual(self.snapshot_tree(home), before)
        launcher = (prefix / "bin" / "mythify-uninstall").read_text(encoding="utf-8")
        self.assertEqual(launcher.count("--skills-root"), 3)

        uninstall = self.run_cmd([str(prefix / "bin" / "mythify-uninstall")], env=self.neutral_env())
        self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
        for root in roots:
            self.assertEqual(list(root.iterdir()), [])
        self.assertEqual(self.snapshot_tree(home), before)

    def test_default_skill_roots_follow_existing_host_directories(self):
        home = self.make_host_home(".claude", ".cursor", ".codex")
        codex_home = self.tmp / "codex-home"
        codex_home.mkdir()
        claude_config = self.tmp / "claude-config"
        claude_config.mkdir()
        prefix = self.tmp / "prefix"
        result = self.run_cmd(
            ["sh", str(INSTALLER), "--prefix", str(prefix)],
            env=self.neutral_env(
                CODEX_HOME=str(codex_home), CLAUDE_CONFIG_DIR=str(claude_config)
            ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for root in (claude_config / "skills", codex_home / "skills", home / ".cursor" / "skills"):
            for skill in SKILLS:
                self.assertTrue((root / skill / "SKILL.md").is_file(), root / skill)
        for untouched in (home / ".claude" / "skills", home / ".codex" / "skills", home / ".agents"):
            self.assertFalse(untouched.exists(), untouched)

        uninstall = self.run_cmd([str(prefix / "bin" / "mythify-uninstall")], env=self.neutral_env())
        self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
        self.assertFalse((home / ".cursor" / "skills" / "mythify").exists())
        self.assertFalse((codex_home / "skills" / "mythify").exists())

    def test_default_skill_root_without_host_directories_is_agents(self):
        home = self.make_host_home()
        prefix = self.tmp / "prefix"
        result = self.run_cmd(["sh", str(INSTALLER), "--prefix", str(prefix)], env=self.neutral_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(path.name for path in home.iterdir()), [".agents"])
        for skill in SKILLS:
            self.assertTrue((home / ".agents" / "skills" / skill / "SKILL.md").is_file())

    def test_uninstall_reads_a_legacy_5x_manifest(self):
        for explicit in (False, True):
            with self.subTest(explicit_roots=explicit):
                base = self.tmp / ("explicit" if explicit else "manifest")
                data_home = base / "xdg-data"
                prefix = base / "prefix"
                roots = [base / "first-skills", base / "second-skills"]
                install_root = self.make_legacy_install(data_home, prefix, roots)
                personal = roots[0] / "mythify-personal" / "SKILL.md"
                personal.parent.mkdir(parents=True)
                personal.write_text("user-owned\n", encoding="utf-8")
                args = [
                    "sh", str(INSTALLER), "--uninstall",
                    "--data-root", str(install_root), "--prefix", str(prefix),
                ]
                if explicit:
                    for root in roots:
                        args.extend(["--skills-root", str(root)])
                result = self.run_cmd(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(install_root.exists())
                self.assertEqual(list((prefix / "bin").iterdir()), [])
                for root in roots:
                    for skill in SKILLS:
                        self.assertFalse((root / skill).exists())
                self.assertEqual(personal.read_text(encoding="utf-8"), "user-owned\n")

    def test_legacy_uninstall_refuses_mismatched_explicit_roots(self):
        data_home = self.tmp / "xdg-data"
        prefix = self.tmp / "prefix"
        roots = [self.tmp / "first-skills", self.tmp / "second-skills"]
        install_root = self.make_legacy_install(data_home, prefix, roots)
        result = self.run_cmd([
            "sh", str(INSTALLER), "--uninstall", "--data-root", str(install_root),
            "--prefix", str(prefix), "--skills-root", str(roots[0]),
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match this uninstall request", result.stderr)
        self.assertTrue((install_root / "cli").is_dir())
        self.assertTrue((roots[1] / "mythify" / "SKILL.md").is_file())

    def test_upgrade_removes_a_verified_previous_version(self):
        data_home = self.tmp / "xdg-data"
        prefix = self.tmp / "prefix"
        shared_root = self.tmp / "shared-skills"
        dropped_root = self.tmp / "dropped-skills"
        previous = self.make_legacy_install(data_home, prefix, [shared_root, dropped_root])
        other_prefix_install = self.make_legacy_install(
            data_home, self.tmp / "other-prefix", [self.tmp / "o1", self.tmp / "o2"], version="5.1.0"
        )
        result = self.run_cmd(
            ["sh", str(INSTALLER), "--prefix", str(prefix), "--skills-root", str(shared_root)],
            env={"XDG_DATA_HOME": str(data_home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(previous.exists())
        self.assertIn("Removed previous Mythify install: {}".format(previous.resolve()), result.stdout)
        self.assertIn(
            "Left previous-version skill directory: {}".format((dropped_root / "mythify").resolve()),
            result.stdout,
        )
        self.assertTrue((dropped_root / "mythify" / "SKILL.md").is_file())
        self.assertNotEqual(
            (shared_root / "mythify" / "SKILL.md").read_text(encoding="utf-8"), "legacy skill\n"
        )
        self.assertTrue((other_prefix_install / "cli").is_dir())
        self.assertNotIn(str(other_prefix_install.resolve()), result.stdout)
        version = self.run_cmd([str(prefix / "bin" / "mythify"), "--version"])
        self.assertEqual(version.returncode, 0, version.stderr)

    def test_upgrade_keeps_a_previous_version_whose_launchers_changed(self):
        data_home = self.tmp / "xdg-data"
        prefix = self.tmp / "prefix"
        roots = [self.tmp / "first-skills", self.tmp / "second-skills"]
        previous = self.make_legacy_install(data_home, prefix, roots)
        (prefix / "bin" / "mythify").write_text("user edit\n", encoding="utf-8")
        result = self.run_cmd(
            ["sh", str(INSTALLER), "--prefix", str(prefix), "--skip-skills"],
            env={"XDG_DATA_HOME": str(data_home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((previous / "cli" / "scripts" / "mythify.py").is_file())
        self.assertTrue((previous / "install-manifest.json").is_file())
        self.assertIn("Left previous Mythify install in place: {}".format(previous.resolve()), result.stdout)
        self.assertIn("file content does not match", result.stdout)


class TestSkillsAreHostNeutral(unittest.TestCase):
    """Chat skills run on any host: no host invocation syntax, no vendor names."""

    HOST_INVOCATION = re.compile(r"(?<![\w./-])[/$]mythify(?:-work|-route|-verify)?\b")

    def skill_files(self):
        return sorted(
            path for path in (REPO_ROOT / "skills").rglob("*")
            if path.is_file() and path.suffix in (".md", ".mdx")
        )

    def test_patterns_can_fire(self):
        self.assertIsNotNone(self.HOST_INVOCATION.search("type `/mythify-work`"))
        self.assertIsNotNone(self.HOST_INVOCATION.search("or $mythify in chat"))
        self.assertIsNone(self.HOST_INVOCATION.search("python3 scripts/mythify.py status"))
        self.assertIsNone(self.HOST_INVOCATION.search("state in .mythify/"))
        self.assertTrue(lint.model_agnostic_hits("spawn a Codex worker"))
        self.assertTrue(lint.model_agnostic_hits("the Cursor agent"))
        self.assertEqual(lint.model_agnostic_hits("report --cursor chat"), [])
        self.assertEqual(lint.model_agnostic_hits("mark a chat cursor first"), [])

    def test_skills_name_no_host_syntax_or_vendor(self):
        hits = []
        for path in self.skill_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.HOST_INVOCATION.search(line) or lint.model_agnostic_hits(line):
                    hits.append("{}:{}: {}".format(path.relative_to(REPO_ROOT), number, line.strip()))
        self.assertEqual(hits, [])

    def test_each_skill_has_matching_frontmatter_and_no_host_manifest(self):
        for skill in SKILLS:
            directory = REPO_ROOT / "skills" / skill
            text = (directory / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\nname: {}\n".format(skill)), skill)
            self.assertTrue("the way your host runs skills" in text, skill)
            self.assertFalse((directory / "agents").exists(), directory)

    def test_main_skill_is_a_short_spine_that_links_existing_references(self):
        skill = REPO_ROOT / "skills" / "mythify" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.split()), 1600)
        links = re.findall(r"references/[\w.-]+", text)
        self.assertTrue(links)
        for link in links:
            self.assertTrue((skill.parent / link).is_file(), link)
        on_disk = {
            "references/" + path.name
            for path in (skill.parent / "references").iterdir() if path.is_file()
        }
        self.assertEqual(on_disk - set(links), set())


if __name__ == "__main__":
    unittest.main()
