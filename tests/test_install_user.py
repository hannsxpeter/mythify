import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install_user.sh"


class TestUserInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mythify-install-test-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def run_cmd(self, args, cwd=None, env=None):
        merged_env = dict(os.environ)
        merged_env["HOME"] = str(self.tmp / "home")
        if env:
            merged_env.update(env)
        return subprocess.run(
            args,
            cwd=str(cwd or REPO_ROOT),
            env=merged_env,
            capture_output=True,
            text=True,
        )

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
