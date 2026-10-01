"""Model-agnostic guard for the shipped runtime.

Mythify never names, ranks, routes to, switches, or spawns a model, provider,
or vendor CLI; the host picks any subagent it likes. These tests scan the
shipped runtime (scripts/mythify*.py and protocol/*.json) for model and vendor
names and for the identifiers of the removed model-routing layer, prove the
scanner can fail, and check that the removed commands and flags are rejected.

scripts/install_user.sh is exempt for now: host names there are file-system
install locations for skills, and a later stage makes the installer host
neutral.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
RUNTIME_GLOBS = ("scripts/mythify*.py", "protocol/*.json")

# (label, pattern). Distinctive names match case-insensitively; names that
# collide with ordinary words (Sol, Luna, Terra, ADK, cursor) match only in
# their vendor forms.
DENYLIST = (
    ("Haiku", re.compile(r"\bhaiku\b", re.IGNORECASE)),
    ("Sonnet", re.compile(r"\bsonnet\b", re.IGNORECASE)),
    ("Opus", re.compile(r"\bopus\b", re.IGNORECASE)),
    ("Fable", re.compile(r"\bfable\b", re.IGNORECASE)),
    ("Luna", re.compile(r"\bLuna\b|-luna\b")),
    ("Terra", re.compile(r"\bTerra\b|-terra\b")),
    ("Sol", re.compile(r"\bSol\b|-sol\b")),
    ("gpt", re.compile(r"gpt", re.IGNORECASE)),
    ("Claude", re.compile(r"claude", re.IGNORECASE)),
    ("Codex", re.compile(r"codex", re.IGNORECASE)),
    (
        "Cursor",
        re.compile(
            r"\bcursor[-_ ](?:agent|desktop|cli|ide|editor|workers?)\b|[/.]cursor/|cursorrules",
            re.IGNORECASE,
        ),
    ),
    ("Kimi", re.compile(r"\bkimi\b", re.IGNORECASE)),
    ("OpenCode", re.compile(r"opencode", re.IGNORECASE)),
    ("Antigravity", re.compile(r"antigravity", re.IGNORECASE)),
    ("Colab", re.compile(r"\bcolab\b", re.IGNORECASE)),
    ("ADK", re.compile(r"\bADK\b")),
    ("ultracode", re.compile(r"ultracode", re.IGNORECASE)),
    ("Ollama", re.compile(r"ollama", re.IGNORECASE)),
    ("LM Studio", re.compile(r"\blm ?studio\b", re.IGNORECASE)),
    ("llama.cpp", re.compile(r"\bllama\b", re.IGNORECASE)),
    ("vLLM", re.compile(r"\bvllm\b", re.IGNORECASE)),
    ("OpenAI", re.compile(r"\bopenai\b", re.IGNORECASE)),
    ("Anthropic", re.compile(r"\banthropic\b", re.IGNORECASE)),
    ("Gemini", re.compile(r"\bgemini\b", re.IGNORECASE)),
)

# Exact substrings removed from a line before scanning. Each is a file name or
# a license attribution, not a model or vendor routing decision.
ALLOWED_TOKENS = (
    # Protocol drop-in file names that `protocol check` reads.
    "CLAUDE.md",
    ".cursorrules",
    # MIT attribution for the adapted prose rules in protocol/prose-quality.json.
    "https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md",
)

# Identifiers of the deleted model-routing layer. No runtime file may read or
# emit them.
REMOVED_IDENTIFIERS = re.compile(
    r"host-model|host_model|model_policy|model_router|model_triage|model_profile|"
    r"execution_adapter|native_adapter|spawn_ceiling|session_model|"
    r"reviewer_strength|failure_count|fanout_visibility|mythify_model_|"
    r"MYTHIFY_TRIAGE|MYTHIFY_SESSION_MODEL|MYTHIFY_SPAWN_CEILING|"
    r"MYTHIFY_FAILURE_COUNT|MYTHIFY_REVIEWER_STRENGTH"
)

DELETED_PATHS = (
    "scripts/mythify_model_policy.py",
    "scripts/mythify_model_routing.py",
    "scripts/mythify_model_triage.py",
    "scripts/mythify_host_model.py",
    "protocol/model-capabilities.json",
)


def runtime_files():
    files = set()
    for pattern in RUNTIME_GLOBS:
        files.update(REPO_ROOT.glob(pattern))
    return sorted(path for path in files if path.is_file())


def denylist_hits(line):
    for token in ALLOWED_TOKENS:
        line = line.replace(token, "")
    return [label for label, pattern in DENYLIST if pattern.search(line)]


class TestRuntimeIsModelAgnostic(unittest.TestCase):
    def test_scan_covers_the_shipped_runtime(self):
        names = {path.relative_to(REPO_ROOT).as_posix() for path in runtime_files()}
        for expected in (
            "scripts/mythify.py",
            "scripts/mythify_router.py",
            "scripts/mythify_classification.py",
            "scripts/mythify_parser.py",
            "protocol/classification-rules.json",
            "protocol/workflow-router.json",
        ):
            self.assertIn(expected, names)

    def test_runtime_names_no_model_or_vendor(self):
        hits = []
        for path in runtime_files():
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                for label in denylist_hits(line):
                    hits.append(
                        "{0}:{1}: {2}: {3}".format(
                            path.relative_to(REPO_ROOT).as_posix(),
                            number,
                            label,
                            line.strip()[:120],
                        )
                    )
        self.assertEqual(hits, [], "\n".join(hits))

    def test_runtime_has_no_model_routing_identifiers(self):
        hits = []
        for path in runtime_files():
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                if REMOVED_IDENTIFIERS.search(line):
                    hits.append(
                        "{0}:{1}: {2}".format(
                            path.relative_to(REPO_ROOT).as_posix(), number, line.strip()[:120]
                        )
                    )
        self.assertEqual(hits, [], "\n".join(hits))

    def test_model_modules_and_manifest_are_deleted(self):
        for relative in DELETED_PATHS:
            self.assertFalse((REPO_ROOT / relative).exists(), relative)


class TestDenylistCanFail(unittest.TestCase):
    """The scanner is only evidence if each entry can actually fire."""

    SAMPLES = {
        "Haiku": "route triage to haiku",
        "Sonnet": "use Sonnet for workers",
        "Opus": "escalate to Opus",
        "Fable": "Fable-style session traces",
        "Luna": "target gpt-5.6-luna",
        "Terra": "the Terra profile",
        "Sol": "upgrade to Sol",
        "gpt": "session model gpt-5.4",
        "Claude": "claude-cli worker",
        "Codex": "codex fast mode",
        "Cursor": "cursor-agent engine",
        "Kimi": "Kimi Code worker",
        "OpenCode": "OpenCode run",
        "Antigravity": "Antigravity CLI",
        "Colab": "Google Colab job",
        "ADK": "ADK eval help",
        "ultracode": "ultracode: implement this",
        "Ollama": "Ollama local server",
        "LM Studio": "LM Studio endpoint",
        "llama.cpp": "llama.cpp server",
        "vLLM": "vLLM endpoint",
        "OpenAI": "OpenAI-compatible provider",
        "Anthropic": "anthropic engine",
        "Gemini": "Gemini model",
    }

    def test_every_entry_fires_on_its_sample(self):
        self.assertEqual(set(self.SAMPLES), {label for label, _ in DENYLIST})
        for label, sample in self.SAMPLES.items():
            with self.subTest(label=label):
                self.assertIn(label, denylist_hits(sample))

    def test_ordinary_words_and_allowed_tokens_do_not_fire(self):
        for line in (
            "Cursor advanced: chat",
            "report --since last --cursor chat",
            "step verification_cursor",
            "a solution for Terraform state",
            "protocol check CLAUDE.md AGENTS.md .cursorrules",
            "https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md",
        ):
            with self.subTest(line=line):
                self.assertEqual(denylist_hits(line), [])

    def test_removed_identifier_pattern_fires(self):
        for sample in (
            "read_host_model_state(state)",
            "classification['model_policy']",
            "args.failure_count",
            "from mythify_model_policy import build_model_policy",
        ):
            with self.subTest(sample=sample):
                self.assertIsNotNone(REMOVED_IDENTIFIERS.search(sample))


class TestRemovedModelSurface(unittest.TestCase):
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
                self.assertNotEqual(result.returncode, 0)
                self.assertRegex(result.stderr, r"invalid choice|unrecognized arguments")

    def test_route_still_works_with_only_task_and_json(self):
        result = self.run_cli("route", "fix the failing parser test", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"chooser": "host"', result.stdout)


if __name__ == "__main__":
    unittest.main()
