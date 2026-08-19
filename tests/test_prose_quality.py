import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_prose_quality.py"
FIXTURES = ROOT / "tests" / "fixtures" / "prose-quality"

sys.path.insert(0, str(ROOT / "scripts"))

from check_prose_quality import inspect_paths, inspect_text, load_manifest


class ProseQualityContractTests(unittest.TestCase):
    def test_manifest_separates_mechanical_and_advisory_rules(self):
        manifest = load_manifest()
        package_manifest = json.loads(
            (ROOT / "mcp-server" / "protocol" / "prose-quality.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest, package_manifest)
        self.assertEqual(manifest["evidence_status"], "mechanical_check_only_subjective_judgment_is_material")
        self.assertIn("forbidden_characters", manifest["mechanical_rules"])
        self.assertIn("Preserve precise domain terms", " ".join(manifest["advisory_rules"]))
        self.assertEqual(manifest["source"]["license"], "MIT")

    def test_packaged_skill_links_the_communication_quality_reference(self):
        skill = (ROOT / "skills" / "mythify" / "SKILL.md").read_text(encoding="utf-8")
        reference = ROOT / "skills" / "mythify" / "references" / "communication-quality.md"
        self.assertTrue(reference.is_file())
        self.assertIn("references/communication-quality.md", skill)
        self.assertIn("Keep those judgments material", reference.read_text(encoding="utf-8"))

    def test_prompt_packet_runtimes_share_the_instruction(self):
        expected = (
            "Before delivering user-facing prose, remove boilerplate and vague claims; "
            "name the actor, action, evidence, or measurement, and preserve exact technical terms."
        )
        python_source = (ROOT / "scripts" / "mythify_router.py").read_text(encoding="utf-8")
        node_source = (ROOT / "mcp-server" / "src" / "prompt-packets.js").read_text(encoding="utf-8")
        self.assertIn(expected, python_source)
        self.assertIn(expected, node_source)

    def test_release_gate_runs_the_mechanical_checker(self):
        gates = json.loads((ROOT / "protocol" / "release-gates.json").read_text(encoding="utf-8"))
        package_gates = json.loads(
            (ROOT / "mcp-server" / "protocol" / "release-gates.json").read_text(encoding="utf-8")
        )
        self.assertEqual(gates, package_gates)
        prose_gate = next(gate for gate in gates["gates"] if gate["id"] == "prose_quality")
        self.assertEqual(prose_gate["commands"], ["python3 scripts/check_prose_quality.py"])
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertLess(
            workflow.index("python3 scripts/check_prose_quality.py"),
            workflow.index("gh release create"),
        )


class ProseQualityCheckerTests(unittest.TestCase):
    def test_clean_fixture_passes(self):
        result = inspect_paths(ROOT, load_manifest(), requested=[FIXTURES / "clean.md"])
        self.assertEqual(result["findings"], [])

    def test_violation_fixture_reports_forbidden_phrases(self):
        result = inspect_paths(ROOT, load_manifest(), requested=[FIXTURES / "violations.md"])
        rules = {finding["rule"] for finding in result["findings"]}
        self.assertEqual(rules, {"forbidden_phrase"})

    def test_unicode_rules_report_dash_and_emoji_classes(self):
        sample = "result {0} detail {1}".format(chr(0x2014), chr(0x1F680))
        findings = inspect_text(sample, "sample.md", load_manifest())
        self.assertEqual(
            {finding["rule"] for finding in findings},
            {"forbidden_character", "emoji"},
        )

    def test_cli_returns_machine_readable_failure(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(FIXTURES / "violations.md"), "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(len(payload["findings"]), 2)

    def test_repository_default_scope_passes(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("subjective prose quality remains material judgment", result.stdout)


if __name__ == "__main__":
    unittest.main()
