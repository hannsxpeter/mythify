"""scripts/lint.py: the real repository passes, and every check can fail.

The self-test copies the lint's file set into a temporary directory, injects
one violation for one check, and runs the lint with --root on the copy. A
clean copy must pass first, so a finding in an injected copy comes from the
injection and not from the copy itself.

This file is exempt from the model-agnostic check: it holds the samples that
prove each denylist entry fires, and it runs the removed model-routing
commands and flags to prove the CLI rejects them.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LINT = REPO_ROOT / "scripts" / "lint.py"
CLI = REPO_ROOT / "scripts" / "mythify.py"

if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
import lint  # noqa: E402


def run_lint(*args):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(LINT)] + [str(arg) for arg in args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def append(path, text):
    with open(str(path), "a", encoding="utf-8") as handle:
        handle.write(text)


def replace(path, old, new):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError("{0} does not contain {1!r}".format(path, old))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# One injection per check. Each takes the copy's root and leaves exactly the
# violations its check must report.
def inject_version(root):
    # Mark every supported line unsupported, whatever the current major is.
    path = root / "SECURITY.md"
    text = path.read_text(encoding="utf-8")
    changed = re.sub(r"(?im)^(\|\s*[0-9]+\.x\s*\|\s*)yes(\s*\|)", r"\1No\2", text)
    if changed == text:
        raise AssertionError("SECURITY.md has no supported-version row")
    path.write_text(changed, encoding="utf-8")


def inject_generated(root):
    append(root / "docs" / "commands.md", "\nHand-edited line.\n")


def inject_protocol_budget(root):
    append(root / "protocol" / "PROTOCOL.md", "padding line\n" * 1000)


def inject_text(root):
    append(root / "README.md", "\nA dash " + chr(0x2014) + " here.\n")


def inject_prose(root):
    append(root / "docs" / "start-here.md", "\nI hope this helps.\n")


def inject_model_agnostic(root):
    append(root / "docs" / "start-here.md", "\nSend the review to Sonnet.\n")
    append(root / "skills" / "mythify" / "SKILL.md", "\nSpawn a codex worker.\n")
    # Shipped docs are in the vendor scope (review round 1).
    append(root / "README.md", "\nTested in a codex terminal.\n")
    # docs/mcp.md documents host setup, so this line is not a finding.
    append(root / "docs" / "mcp.md", "\nTested in a codex terminal.\n")


def inject_dependencies(root):
    append(root / "scripts" / "mythify_io.py", "\nimport yaml  # noqa: E402\n")
    (root / "package.json").write_text("{}\n", encoding="utf-8")


def inject_mcp_surface(root):
    replace(root / "scripts" / "mythify_mcp.py", '    ("status",),\n', '    ("status",),\n    ("no-such", "leaf"),\n')


def inject_links(root):
    append(root / "README.md", "\nSee [the missing page](docs/missing.md).\n")


def inject_source_size(root):
    (root / "scripts" / "mythify_oversized.py").write_text("x = 1\n" * 1600, encoding="utf-8")


INJECTIONS = {
    "version": (inject_version, ["SECURITY.md"]),
    "generated": (inject_generated, ["docs/commands.md"]),
    "protocol-budget": (inject_protocol_budget, ["protocol/PROTOCOL.md"]),
    "text": (inject_text, ["README.md"]),
    "prose": (inject_prose, ["docs/start-here.md"]),
    "model-agnostic": (inject_model_agnostic, ["README.md", "docs/start-here.md", "skills/mythify/SKILL.md"]),
    "dependencies": (inject_dependencies, ["package.json", "scripts/mythify_io.py"]),
    "mcp-surface": (inject_mcp_surface, ["scripts/mythify_mcp.py"]),
    "links": (inject_links, ["README.md"]),
    "source-size": (inject_source_size, ["scripts/mythify_oversized.py"]),
}


class LintTestCase(unittest.TestCase):
    def copy_repo(self):
        root = Path(tempfile.mkdtemp(prefix="mythify-lint-"))
        self.addCleanup(shutil.rmtree, str(root), True)
        for relative in lint.repo_files(REPO_ROOT):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(REPO_ROOT / relative), str(target))
        return root

    def findings(self, root, *checks):
        args = ["--root", root, "--json"]
        for check in checks:
            args.extend(["--check", check])
        result = run_lint(*args)
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1 if payload["findings"] else 0, result.stdout + result.stderr)
        return payload["findings"]


class TestLintOnRepository(LintTestCase):
    def test_repository_is_clean(self):
        result = run_lint()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[OK] lint passed", result.stdout)

    def test_findings_print_one_line_each_and_exit_1(self):
        root = self.copy_repo()
        inject_links(root)
        result = run_lint("--root", root, "--check", "links")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stdout.strip().splitlines(),
            ["README.md:{0}: links: `docs/missing.md` does not resolve to a tracked file".format(
                len((root / "README.md").read_text(encoding="utf-8").splitlines())
            )],
        )

    def test_unknown_check_is_a_usage_error(self):
        result = run_lint("--check", "no-such-check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


class TestEveryCheckCanFail(LintTestCase):
    def test_injections_cover_every_check(self):
        self.assertEqual(set(INJECTIONS), set(lint.CHECKS))

    def test_clean_copy_passes_and_each_injection_fails_its_check(self):
        clean = self.copy_repo()
        self.assertEqual(self.findings(clean), [])
        for check in lint.CHECKS:
            with self.subTest(check=check):
                root = self.copy_repo()
                inject, paths = INJECTIONS[check]
                inject(root)
                findings = self.findings(root, check)
                self.assertTrue(findings, check)
                self.assertEqual({item["check"] for item in findings}, {check})
                self.assertEqual({item["path"] for item in findings}, set(paths))


class TestDenylistEntriesFire(unittest.TestCase):
    """Each denylist entry fires on its sample, and ordinary words do not."""

    MODEL_SAMPLES = {
        "haiku": "route triage to haiku",
        "sonnet": "use Sonnet for workers",
        "opus": "escalate to Opus",
        "fable": "Fable-style session traces",
        "gpt": "session model gpt-5.4",
        "gemini": "Gemini model",
        "llama": "llama.cpp server",
        "mistral": "a Mistral endpoint",
        "qwen": "Qwen coder",
        "deepseek": "DeepSeek reasoning",
        "luna": "target the Luna profile",
        "terra": "the Terra profile",
        "sol": "upgrade to Sol",
    }
    VENDOR_SAMPLES = {
        "claude": "claude-cli worker",
        "codex": "codex fast mode",
        "cursor": "cursor-agent engine",
        "kimi": "Kimi Code worker",
        "opencode": "OpenCode run",
        "antigravity": "Antigravity CLI",
        "colab": "Google Colab job",
        "ollama": "Ollama local server",
        "anthropic": "anthropic engine",
        "openai": "OpenAI-compatible provider",
        "lm studio": "LM Studio endpoint",
        "vllm": "vLLM endpoint",
        "adk": "ADK eval help",
    }

    def test_every_model_and_vendor_entry_fires(self):
        self.assertEqual(set(self.MODEL_SAMPLES), {label for label, _ in lint.MODEL_NAMES})
        self.assertEqual(set(self.VENDOR_SAMPLES), {label for label, _ in lint.VENDOR_NAMES})
        for label, sample in list(self.MODEL_SAMPLES.items()) + list(self.VENDOR_SAMPLES.items()):
            with self.subTest(label=label):
                self.assertIn(label, lint.model_agnostic_hits(sample))

    def test_vendor_names_fire_only_in_vendor_scope(self):
        self.assertEqual(lint.model_agnostic_hits("codex fast mode", vendors=False), [])
        self.assertTrue(lint.in_vendor_scope("scripts/mythify_router.py"))
        self.assertTrue(lint.in_vendor_scope("skills/mythify/references/chat-experience.md"))
        for path in (
            "README.md", "SECURITY.md", "CONTRIBUTING.md", "MAINTAINING.md", "ROADMAP.md",
            "RELEASE-CHECKLIST.md", "CODE_OF_CONDUCT.md", "docs/start-here.md",
            "docs/architecture.md", "scripts/check_prose_quality.py",
        ):
            self.assertTrue(lint.in_vendor_scope(path), path)
        self.assertFalse(lint.in_vendor_scope("docs/mcp.md"))
        self.assertFalse(lint.in_vendor_scope("scripts/install_user.sh"))

    def test_every_routing_identifier_fires(self):
        for name in lint.ROUTING_IDENTIFIERS:
            with self.subTest(name=name):
                self.assertIn(name, lint.model_agnostic_hits("read {0} here".format(name), vendors=False))
        self.assertIn("mythify_model_*", lint.model_agnostic_hits("from mythify_model_policy import x"))
        self.assertEqual(
            lint.removed_identifier_keys(["model_triage_reason", "framing", "MODEL_POLICY"]),
            ["MODEL_POLICY", "model_triage_reason"],
        )

    def test_ordinary_words_and_allowed_tokens_do_not_fire(self):
        for line in (
            "Cursor advanced: chat",
            "report --since last --cursor chat",
            "step verification_cursor",
            "a cursory read",
            "a solution for Terraform state",
            "the console prints a summary",
            "protocol check CLAUDE.md AGENTS.md .cursorrules",
            "https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md",
            "https://github.com/cursor/plugins/blob/main/pstack/skills/blast-radius/SKILL.md",
            "a gptq-free path",
        ):
            with self.subTest(line=line):
                self.assertEqual(lint.model_agnostic_hits(line), [])

    def test_exemptions_are_historical_records_and_the_lint_itself(self):
        for path in ("CHANGELOG.md", "docs/DRIFT.md", "docs/evidence/notes.md", "scripts/lint.py", "tests/test_lint.py"):
            self.assertTrue(lint.is_exempt(path), path)
        for path in ("README.md", "docs/mcp.md", "tests/test_routes.py", "docs/evidence.md"):
            self.assertFalse(lint.is_exempt(path), path)


class TestRemovedModelSurface(unittest.TestCase):
    """The removed model-routing commands, flags, and modules stay removed."""

    DELETED_PATHS = (
        "scripts/mythify_model_policy.py",
        "scripts/mythify_model_routing.py",
        "scripts/mythify_model_triage.py",
        "scripts/mythify_host_model.py",
        "protocol/model-capabilities.json",
    )

    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "home").mkdir()
            (root / "project").mkdir()
            env = dict(os.environ)
            env.pop("MYTHIFY_DIR", None)
            env["HOME"] = str(root / "home")
            return subprocess.run(
                [sys.executable, str(CLI)] + list(args),
                cwd=str(root / "project"),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

    def test_model_modules_and_manifest_are_deleted(self):
        for relative in self.DELETED_PATHS:
            self.assertFalse((REPO_ROOT / relative).exists(), relative)

    def test_removed_commands_and_model_flags_are_rejected(self):
        cases = (
            ("classify", "fix the parser"),
            ("host-model", "status"),
            ("route", "x", "--triage", "auto"),
            ("route", "x", "--triage-engine", "command"),
            ("route", "x", "--triage-model", "m"),
            ("route", "x", "--triage-timeout", "5"),
            ("route", "x", "--platform", "auto"),
            ("route", "x", "--effort", "low"),
            ("route", "x", "--speed", "fast"),
            ("route", "x", "--session-model", "m"),
            ("route", "x", "--model-profile", "max"),
            ("route", "x", "--failure-count", "1"),
            ("route", "x", "--spawn-ceiling", "auto"),
            ("route", "x", "--reviewer-strength", "auto"),
            (
                "outcome", "start", "goal", "--success", "ok", "--verify", "true",
                "--visibility", "quiet",
            ),
        )
        for argv in cases:
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.returncode, 64, result.stdout + result.stderr)
                self.assertRegex(result.stderr, r"invalid choice|unrecognized arguments")

    def test_route_still_works_with_only_task_and_json(self):
        result = self.run_cli("route", "fix the failing parser test", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"chooser": "host"', result.stdout)


if __name__ == "__main__":
    unittest.main()
