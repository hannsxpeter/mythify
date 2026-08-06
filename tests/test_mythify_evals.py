"""End-to-end coverage of the eval evolution loop.

The loop grows the local eval harness without an agent grading its own
homework. These tests pin the parts that make it Mythify rather than a
suggestion box: a candidate scenario is validated fail-closed, the default
verify command refuses a green baseline, adoption requires the human's words
plus a passing executed run recorded since the proposal was created, and the
harness only loads scenarios that pass the same validation.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
HARNESS = REPO_ROOT / "scripts" / "local_model_eval.py"
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RED_SCENARIO = {
    "name": "empty_input_guard_bugfix",
    "title": "Empty input guard bug fix",
    "task_category": "string_processing_bugfix",
    "task": "Fix guard.py so python3 -m unittest passes. Do not edit the test file.",
    "files": {
        "guard.py": "def first_word(text):\n    return str(text).split()[0]",
        "test_guard.py": "\n".join(
            [
                "import unittest",
                "from guard import first_word",
                "",
                "class TestGuard(unittest.TestCase):",
                "    def test_first(self):",
                "        self.assertEqual(first_word('alpha beta'), 'alpha')",
                "    def test_empty(self):",
                "        self.assertEqual(first_word(''), '')",
                "",
                "if __name__ == '__main__':",
                "    unittest.main()",
            ]
        ),
    },
    "local_model_roles": ["reader", "triage"],
    "fanout_fit": "waste_candidate",
    "fanout_merge_verifier": "python3 -m unittest",
}

GREEN_SCENARIO = {
    "name": "already_green_case",
    "title": "Scenario whose verifier already passes",
    "task_category": "string_processing_bugfix",
    "task": "Nothing to fix.",
    "files": {
        "ok.py": "VALUE = 1",
        "test_ok.py": "\n".join(
            [
                "import unittest",
                "from ok import VALUE",
                "",
                "class TestOk(unittest.TestCase):",
                "    def test_value(self):",
                "        self.assertEqual(VALUE, 1)",
            ]
        ),
    },
}


class EvalCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self.assertEqual(self.run_cli("init").returncode, 0)

    def run_cli(self, *args, **kwargs):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env.pop("MYTHIFY_REQUIRE_HUMAN_INPUT", None)
        env.pop("MYTHIFY_DISABLE_RUN", None)
        env.pop("MYTHIFY_EVAL_SCENARIOS", None)
        env["HOME"] = str(self.home)
        env.update(kwargs.pop("env", {}))
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def ok(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result

    def write_scenario(self, payload, filename="candidate.json"):
        path = self.project / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def propose(self, payload=None, **kwargs):
        path = self.write_scenario(payload or RED_SCENARIO)
        return self.run_cli(
            "eval",
            "propose",
            "Guard empty input",
            "--scenario-file",
            str(path),
            "--rationale",
            "reflection recorded a crash on empty input",
            "--source",
            "reflections.jsonl",
            **kwargs,
        )

    def proposals_doc(self):
        return json.loads(
            (self.project / ".mythify" / "evals" / "proposals.json").read_text(
                encoding="utf-8"
            )
        )


class TestPropose(EvalCase):
    def test_propose_records_material_and_its_own_baseline_verifier(self):
        result = self.propose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Proposed eval scenario", result.stdout)
        doc = self.proposals_doc()
        proposal = doc["proposals"][0]
        self.assertEqual(proposal["id"], "E1")
        self.assertEqual(proposal["status"], "proposed")
        self.assertEqual(proposal["scenario_name"], "empty_input_guard_bugfix")
        self.assertEqual(proposal["sources"], ["reflections.jsonl"])
        self.assertIn("eval baseline E1", proposal["verify_command"])
        self.assertNotIn("name", proposal["scenario"])

    def test_propose_refuses_a_verifier_that_can_never_fail(self):
        payload = dict(RED_SCENARIO)
        payload["fanout_merge_verifier"] = "true"
        result = self.propose(payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("proves nothing", result.stderr)
        self.assertFalse((self.project / ".mythify" / "evals").exists())

    def test_corrupt_proposal_entries_do_not_crash_the_surface(self):
        self.assertEqual(self.propose().returncode, 0)
        path = self.project / ".mythify" / "evals" / "proposals.json"
        path.write_text(
            json.dumps({"schema_version": 1, "proposals": ["junk", 42]}),
            encoding="utf-8",
        )
        result = self.ok("eval", "list")
        self.assertIn("Eval proposals: 0", result.stdout)
        result = self.run_cli("eval", "show", "E1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)

    def test_propose_refuses_invalid_scenarios(self):
        bad = dict(RED_SCENARIO)
        bad["name"] = "Bad Name"
        bad["files"] = {"../escape.py": "x", "/etc/abs.py": "y", "TASK.md": 3}
        result = self.propose(bad)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Invalid scenario file", result.stderr)
        self.assertIn("traversal", result.stderr)
        self.assertIn("absolute", result.stderr)
        self.assertIn("TASK.md", result.stderr)
        self.assertFalse((self.project / ".mythify" / "evals").exists())

    def test_propose_refuses_missing_rationale_and_collisions(self):
        path = self.write_scenario(RED_SCENARIO)
        result = self.run_cli(
            "eval", "propose", "t", "--scenario-file", str(path), "--rationale", "  "
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Rationale required", result.stderr)
        builtin = dict(RED_SCENARIO)
        builtin["name"] = "word_count_bugfix"
        result = self.propose(builtin)
        self.assertEqual(result.returncode, 1)
        self.assertIn("built-in", result.stderr)
        self.assertEqual(self.propose().returncode, 0)
        result = self.propose()
        self.assertEqual(result.returncode, 1)
        self.assertIn("already carried by proposal", result.stderr)

    def test_rejection_frees_the_scenario_name(self):
        self.ok_propose()
        result = self.run_cli("eval", "reject", "E1")
        self.assertEqual(result.returncode, 2)
        self.ok("eval", "reject", "E1", "--reason", "duplicate of a built-in")
        doc = self.proposals_doc()
        self.assertEqual(doc["proposals"][0]["status"], "rejected")
        self.assertEqual(
            doc["proposals"][0]["reject_reason"], "duplicate of a built-in"
        )
        self.assertEqual(self.propose().returncode, 0)
        self.assertEqual(self.proposals_doc()["proposals"][1]["id"], "E2")

    def ok_propose(self):
        result = self.propose()
        self.assertEqual(result.returncode, 0, result.stderr)


class TestBaseline(EvalCase):
    def propose_named(self, payload, filename, title):
        path = self.write_scenario(payload, filename)
        return self.run_cli(
            "eval", "propose", title, "--scenario-file", str(path),
            "--rationale", "baseline coverage",
        )

    def test_baseline_passes_only_when_the_scenario_starts_red(self):
        self.assertEqual(self.propose().returncode, 0)
        result = self.ok("eval", "baseline", "E1")
        self.assertIn("Baseline red as required", result.stdout)
        self.assertEqual(
            self.propose_named(GREEN_SCENARIO, "green.json", "Green scenario").returncode, 0
        )
        result = self.run_cli("eval", "baseline", "E2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Baseline green", result.stderr)

    def test_baseline_refuses_a_verifier_that_never_ran(self):
        missing = dict(RED_SCENARIO)
        missing["name"] = "missing_tool_case"
        missing["fanout_merge_verifier"] = "definitely-not-a-real-tool-xyz -q"
        self.assertEqual(
            self.propose_named(missing, "missing.json", "Missing tool").returncode, 0
        )
        result = self.run_cli("eval", "baseline", "E1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Baseline unusable", result.stderr)
        self.assertIn("was not found", result.stderr)

    def test_baseline_refuses_a_run_that_collected_no_tests(self):
        empty = {
            "name": "no_tests_case",
            "title": "Scenario with no tests at all",
            "task_category": "string_processing_bugfix",
            "task": "Nothing here runs.",
            "files": {"only.py": "VALUE = 1"},
        }
        self.assertEqual(
            self.propose_named(empty, "empty.json", "No tests").returncode, 0
        )
        result = self.run_cli("eval", "baseline", "E1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Baseline unusable", result.stderr)

    def test_disable_run_blocks_baseline(self):
        self.assertEqual(self.propose().returncode, 0)
        result = self.run_cli(
            "eval", "baseline", "E1", env={"MYTHIFY_DISABLE_RUN": "1"}
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("disabled", result.stderr)


class TestAdoptionGates(EvalCase):
    def adopt(self, *extra, **kwargs):
        return self.run_cli("eval", "adopt", "E1", *extra, **kwargs)

    def test_adopt_requires_the_humans_words(self):
        self.assertEqual(self.propose().returncode, 0)
        self.ok("eval", "verify", "E1")
        result = self.adopt()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Human input required", result.stderr)

    def test_waiver_is_stamped_not_silent(self):
        self.assertEqual(self.propose().returncode, 0)
        self.ok("eval", "verify", "E1")
        result = self.adopt(env={"MYTHIFY_REQUIRE_HUMAN_INPUT": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Human-input gate waived", result.stderr)
        proposal = self.proposals_doc()["proposals"][0]
        self.assertTrue(proposal["human_input_waived"])
        registry = json.loads(
            (self.project / ".mythify" / "evals" / "scenarios.json").read_text(
                encoding="utf-8"
            )
        )
        entry = registry["scenarios"]["empty_input_guard_bugfix"]
        self.assertTrue(entry["adoption"]["human_input_waived"])

    def test_adopt_refuses_a_stored_scenario_corrupted_after_proposing(self):
        self.assertEqual(self.propose().returncode, 0)
        self.ok("eval", "verify", "E1")
        path = self.project / ".mythify" / "evals" / "proposals.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["proposals"][0]["scenario"]["files"] = {"../escape.py": "x"}
        path.write_text(json.dumps(doc), encoding="utf-8")
        result = self.adopt("--human-input", "ok")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Invalid stored scenario", result.stderr)
        self.assertFalse(
            (self.project / ".mythify" / "evals" / "scenarios.json").exists()
        )

    def test_adopt_requires_passing_evidence_for_this_proposal(self):
        self.assertEqual(self.propose().returncode, 0)
        result = self.adopt("--human-input", "ship it")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Verified evidence required", result.stderr)

    def test_another_proposals_run_cannot_stand_in(self):
        self.assertEqual(self.propose().returncode, 0)
        second = dict(RED_SCENARIO)
        second["name"] = "second_case"
        path = self.write_scenario(second, "second.json")
        self.ok(
            "eval", "propose", "Second", "--scenario-file", str(path),
            "--rationale", "another gap",
        )
        self.ok("eval", "verify", "E1")
        result = self.run_cli("eval", "adopt", "E2", "--human-input", "ok")
        self.assertEqual(result.returncode, 1)
        self.assertIn("matched by proposal", result.stderr)

    def test_an_unrelated_run_of_the_same_command_cannot_stand_in(self):
        self.assertEqual(self.propose().returncode, 0)
        command = self.proposals_doc()["proposals"][0]["verify_command"]
        self.ok("verify", "run", command, "--claim", "routine work")
        result = self.adopt("--human-input", "ok")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Verified evidence required", result.stderr)

    def test_log_compaction_does_not_strand_a_verified_proposal(self):
        for index in range(4):
            self.ok("verify", "run", "true", "--claim", "warmup {0}".format(index))
        self.assertEqual(self.propose().returncode, 0)
        self.ok("eval", "verify", "E1")
        self.ok("logs", "compact", "--keep", "2")
        result = self.adopt("--human-input", "adopt after compaction")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_verified_human_adoption_writes_the_registry(self):
        self.assertEqual(self.propose().returncode, 0)
        verify = self.ok("eval", "verify", "E1")
        self.assertIn("VERIFIED proposal E1", verify.stdout)
        result = self.adopt("--human-input", "yes, adopt the empty-input eval")
        self.assertEqual(result.returncode, 0, result.stderr)
        registry = json.loads(
            (self.project / ".mythify" / "evals" / "scenarios.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("empty_input_guard_bugfix", registry["scenarios"])
        entry = registry["scenarios"]["empty_input_guard_bugfix"]
        self.assertEqual(entry["title"], RED_SCENARIO["title"])
        self.assertEqual(entry["adoption"]["proposal"], "E1")
        self.assertEqual(
            entry["adoption"]["human_input"], "yes, adopt the empty-input eval"
        )
        self.assertFalse(entry["adoption"]["human_input_waived"])
        proposal = self.proposals_doc()["proposals"][0]
        self.assertEqual(proposal["status"], "adopted")
        self.assertEqual(
            proposal["human_input"], "yes, adopt the empty-input eval"
        )
        self.assertIn("eval baseline E1", proposal["verified_command"])
        result = self.adopt("--human-input", "again")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already adopted", result.stderr)

    def test_verify_records_scoped_executed_evidence(self):
        self.assertEqual(self.propose().returncode, 0)
        self.ok("eval", "verify", "E1")
        lines = (
            (self.project / ".mythify" / "verifications.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        record = json.loads(lines[-1])
        self.assertEqual(record["kind"], "executed")
        self.assertTrue(record["verified"])
        self.assertEqual(record["eval_proposal"], "E1")
        self.assertEqual(record["eval_scenario"], "empty_input_guard_bugfix")

    def test_disable_run_blocks_verify_without_recording(self):
        self.assertEqual(self.propose().returncode, 0)
        result = self.run_cli(
            "eval", "verify", "E1", env={"MYTHIFY_DISABLE_RUN": "1"}
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("disabled", result.stderr)


class TestScan(EvalCase):
    def test_scan_is_read_only_material(self):
        self.ok(
            "reflect",
            "--action",
            "ran parser tests",
            "--outcome",
            "failure",
            "--observation",
            "2 of 14 failed on empty input",
            "--root-cause",
            "no guard for empty string",
            "--next",
            "add guard",
        )
        self.run_cli("verify", "run", "false", "--claim", "will fail")
        result = self.ok("eval", "scan", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["failure_reflections"]), 1)
        self.assertEqual(
            payload["failure_reflections"][0]["root_cause"],
            "no guard for empty string",
        )
        self.assertEqual(len(payload["failing_verifications"]), 1)
        self.assertIn("material", payload["guardrail"])
        self.assertFalse(
            (self.project / ".mythify" / "evals" / "proposals.json").exists()
        )


class TestRegistryProvenance(EvalCase):
    def test_hand_written_registry_entries_are_named_as_unprovenanced(self):
        evals = self.project / ".mythify" / "evals"
        evals.mkdir(parents=True, exist_ok=True)
        (evals / "scenarios.json").write_text(
            json.dumps({
                "schema_version": 1,
                "scenarios": {"self_written": dict(
                    (k, v) for k, v in RED_SCENARIO.items() if k != "name"
                )},
            }),
            encoding="utf-8",
        )
        result = self.ok("eval", "list", "--json")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["adopted_scenarios"], [])
        self.assertEqual(payload["unprovenanced_scenarios"], ["self_written"])
        text = self.ok("eval", "list").stdout
        self.assertIn("Adopted scenarios: none", text)
        self.assertIn("without adoption provenance", text)


class TestHarnessLoading(EvalCase):
    def run_harness(self, *args, **kwargs):
        env = dict(os.environ)
        env.pop("MYTHIFY_EVAL_SCENARIOS", None)
        env["HOME"] = str(self.home)
        env.update(kwargs.pop("env", {}))
        return subprocess.run(
            [sys.executable, str(HARNESS)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_adopted_registry_loads_into_the_harness(self):
        self.assertEqual(self.propose().returncode, 0)
        self.ok("eval", "verify", "E1")
        self.ok("eval", "adopt", "E1", "--human-input", "adopt it")
        registry = self.project / ".mythify" / "evals" / "scenarios.json"
        result = self.run_harness(
            "--scenario-file", str(registry), "--list-scenarios"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("empty_input_guard_bugfix", result.stdout)
        self.assertIn("word_count_bugfix", result.stdout)

    def test_env_fallback_loads_scenarios(self):
        path = self.write_scenario(RED_SCENARIO)
        result = self.run_harness(
            "--list-scenarios", env={"MYTHIFY_EVAL_SCENARIOS": str(path)}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("empty_input_guard_bugfix", result.stdout)

    def test_abbreviated_scenario_file_flag_is_refused_not_ignored(self):
        path = self.write_scenario(RED_SCENARIO)
        result = self.run_harness("--scenario-fil", str(path), "--list-scenarios")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("empty_input_guard_bugfix", result.stdout)

    def test_loaded_scenarios_do_not_leak_between_in_process_calls(self):
        harness = load_module("local_model_eval_leak", HARNESS)
        path = self.write_scenario(RED_SCENARIO, "leak.json")
        self.assertEqual(
            harness.main(["--scenario-file", str(path), "--list-scenarios"]), 0
        )
        self.assertNotIn("empty_input_guard_bugfix", harness.SCENARIOS)
        self.assertEqual(
            sorted(harness.SCENARIOS), sorted(harness.BUILTIN_SCENARIO_NAMES)
        )

    def test_invalid_and_colliding_files_are_refused(self):
        bad = self.project / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        result = self.run_harness("--scenario-file", str(bad), "--list-scenarios")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Could not load eval scenarios", result.stderr)
        collide = dict(RED_SCENARIO)
        collide["name"] = "word_count_bugfix"
        path = self.write_scenario(collide, "collide.json")
        result = self.run_harness("--scenario-file", str(path), "--list-scenarios")
        self.assertEqual(result.returncode, 1)
        self.assertIn("built-in", result.stderr)


class TestBuiltinMirror(unittest.TestCase):
    def test_builtin_scenario_mirror_matches_the_harness(self):
        evals = load_module("mythify_evals_mirror", REPO_ROOT / "scripts" / "mythify_evals.py")
        harness = load_module("local_model_eval_mirror", HARNESS)
        self.assertEqual(
            tuple(harness.BUILTIN_SCENARIO_NAMES),
            evals.HARNESS_BUILTIN_SCENARIOS,
        )

    def test_auto_profile_stays_pinned_to_builtins(self):
        harness = load_module("local_model_eval_profile", HARNESS)
        self.assertEqual(
            harness.resolve_mythify_profile("auto", "word_count_bugfix"), "fast"
        )
        self.assertEqual(
            harness.resolve_mythify_profile("auto", "empty_input_guard_bugfix"),
            "standard",
        )


if __name__ == "__main__":
    unittest.main()
