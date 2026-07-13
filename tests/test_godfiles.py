"""Unit tests for scripts/mythify_godfiles.py against committed MDX fixtures,
plus CLI tests for plan import and the strict step-context evidence gate."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "scripts" / "mythify_godfiles.py"
CLI = REPO_ROOT / "scripts" / "mythify.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "godfiles"


def load_module():
    spec = importlib.util.spec_from_file_location("mythify_godfiles", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


godfiles = load_module()


class TestPlanParsing(unittest.TestCase):
    def setUp(self):
        self.digest = godfiles.load_god_artifact(FIXTURES / "PLAN.mdx", "godplans")

    def test_frontmatter_digest(self):
        self.assertEqual(self.digest["status"], "executing")
        self.assertEqual(self.digest["name"], "taskboard")
        self.assertEqual(self.digest["plan_version"], 2)

    def test_counts_exclude_superseded_tasks(self):
        self.assertEqual(self.digest["counts"], {"tasks_total": 5, "tasks_done": 2, "tasks_open": 3})
        self.assertFalse(self.digest["counter_drift"])

    def test_next_task_is_first_unchecked_in_document_order(self):
        self.assertEqual(self.digest["next_task"]["id"], "GP-201")

    def test_task_fields(self):
        by_id = {task["id"]: task for task in self.digest["tasks"]}
        task = by_id["GP-201"]
        self.assertEqual(task["verify_command"], "npm run db:migrate && npm run db:check")
        self.assertEqual(task["depends_on"], ["GP-102"])
        self.assertEqual(task["wave"], "2.1")
        self.assertEqual(task["phase_number"], "2")
        self.assertIn("email column has unique index", task["acceptance"])
        self.assertTrue(by_id["GP-102"]["parallel"])
        self.assertTrue(by_id["GP-202"]["superseded"])
        self.assertEqual(by_id["GP-301"]["files"], [])

    def test_md_fallback_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(godfiles.find_godplans_file(root))
            plan_dir = root / ".godplans"
            plan_dir.mkdir()
            shutil.copy(FIXTURES / "PLAN.mdx", plan_dir / "PLAN.md")
            found = godfiles.find_godplans_file(root)
            self.assertEqual(found.name, "PLAN.md")
            shutil.copy(FIXTURES / "PLAN.mdx", plan_dir / "PLAN.mdx")
            self.assertEqual(godfiles.find_godplans_file(root).name, "PLAN.mdx")


class TestAuditParsing(unittest.TestCase):
    def setUp(self):
        self.digest = godfiles.load_god_artifact(FIXTURES / "AUDIT.mdx", "godaudits")

    def test_frontmatter_digest(self):
        self.assertEqual(self.digest["status"], "reported")
        self.assertEqual(self.digest["overall_score"], 64)
        self.assertEqual(self.digest["verdict"], "at risk")
        self.assertTrue(self.digest["plan_aware"])

    def test_findings_and_open_severity_counts(self):
        by_id = {finding["id"]: finding for finding in self.digest["findings"]}
        self.assertEqual(len(by_id), 3)
        self.assertEqual(by_id["F-SEC-1"]["severity"], "Critical")
        self.assertEqual(by_id["F-SEC-1"]["status"], "open")
        self.assertEqual(by_id["F-SEC-1"]["remediation"], "GA-101")
        self.assertEqual(by_id["F-CODE-1"]["status"], "resolved")
        self.assertEqual(self.digest["open_critical"], 1)
        self.assertEqual(self.digest["open_high"], 1)

    def test_remediation_tasks(self):
        by_id = {task["id"]: task for task in self.digest["tasks"]}
        self.assertEqual(by_id["GA-101"]["fixes"], ["F-SEC-1"])
        self.assertEqual(by_id["GA-101"]["verify_command"], "npm test -- tests/security/isolation.test.ts")
        self.assertEqual(by_id["GA-301"]["depends_on"], ["GA-101", "GA-201"])
        self.assertEqual(self.digest["next_task"]["id"], "GA-101")


class TestParserHardening(unittest.TestCase):
    def test_counter_drift_flagged(self):
        text = "\n".join([
            "---",
            "name: drifty",
            "status: executing",
            "progress:",
            "  tasks_total: 9",
            "  tasks_done: 9",
            "---",
            "## Phase 1: Only",
            "### Wave 1.1",
            "- [ ] GP-101 [W1.1] Lone task",
            "  - Verify: `true`",
        ])
        parsed = godfiles.parse_god_document(text)
        self.assertTrue(godfiles._digest_counter_drift(parsed["frontmatter"], parsed["counts"]))

    def test_fenced_task_lines_are_ignored(self):
        text = "\n".join([
            "---",
            "name: fenced",
            "status: planning",
            "---",
            "```bash",
            "- [ ] GP-999 [W9.9] Not a real task",
            "```",
            "- [ ] GP-101 Real task",
            "  - Verify: `true`",
        ])
        parsed = godfiles.parse_god_document(text)
        ids = [task["id"] for task in parsed["tasks"]]
        self.assertEqual(ids, ["GP-101"])

    def test_opaque_ids_are_not_decomposed(self):
        text = "- [ ] GP-1101 [W11.1] Phase eleven task\n  - Verify: `true`\n"
        parsed = godfiles.parse_god_document(text)
        self.assertEqual(parsed["tasks"][0]["id"], "GP-1101")
        self.assertEqual(parsed["tasks"][0]["wave"], "11.1")

    def test_unrecognized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.mdx"
            path.write_text("just prose, no digest, no tasks\n", encoding="utf-8")
            digest = godfiles.load_god_artifact(path, "godplans")
            self.assertEqual(digest["status"], "unrecognized")
            self.assertTrue(digest["load_error"])

    def test_unreadable_file(self):
        digest = godfiles.load_god_artifact(Path("/nonexistent/PLAN.mdx"), "godplans")
        self.assertEqual(digest["status"], "unreadable")
        self.assertTrue(digest["load_error"])

    def test_non_utf8_bytes_do_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PLAN.mdx"
            path.write_bytes(
                b"---\nname: caf\xe9\nstatus: executing\n---\n- [ ] GP-101 x\n  - Verify: `t`\n"
            )
            digest = godfiles.load_god_artifact(path, "godplans")
            self.assertEqual(digest["status"], "executing")
            self.assertNotIn("load_error", digest)

    def test_author_status_colliding_with_sentinel_still_surfaces(self):
        for sentinel in ("unrecognized", "unreadable", "missing"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".godplans").mkdir()
                (root / ".godplans" / "PLAN.mdx").write_text(
                    "---\nname: x\nstatus: {0}\n---\n- [ ] GP-101 Task\n  - Verify: `t`\n".format(sentinel),
                    encoding="utf-8",
                )
                summary = godfiles.godplans_summary(root)
                self.assertTrue(summary["present"], sentinel)
                self.assertEqual(summary["tasks_total"], 1, sentinel)

    def test_bom_prefixed_frontmatter_parses(self):
        text = "\ufeff---\nname: withbom\nstatus: executing\n---\n- [ ] GP-101 x\n  - Verify: `t`\n"
        self.assertEqual(godfiles.parse_god_frontmatter(text).get("name"), "withbom")
        self.assertEqual(godfiles.parse_god_document(text)["counts"]["tasks_total"], 1)

    def test_bracket_token_not_a_wave_flag_stays_in_title(self):
        for token in ("[Windows]", "[WIP]", "[W]"):
            text = "### Wave 3.1\n- [ ] GP-310 {0} Title\n  - Verify: `t`\n".format(token)
            task = godfiles.parse_god_document(text)["tasks"][0]
            self.assertEqual(task["wave"], "3.1", token)
            self.assertTrue(task["title"].startswith(token), token)

    def test_tilde_and_long_fences_hide_quoted_tasks(self):
        text = (
            "~~~\n- [ ] GP-901 phantom\n~~~\n"
            "````\n- [ ] GP-902 phantom\n````\n"
            "- [ ] GP-101 real\n  - Verify: `t`\n"
        )
        self.assertEqual([t["id"] for t in godfiles.parse_god_document(text)["tasks"]], ["GP-101"])

    def test_int_scalar_parity_gate(self):
        fm = godfiles.parse_god_frontmatter("---\na: +5\nb: 1_000\nc: 42\n---\n")
        self.assertEqual(fm["a"], "+5")
        self.assertEqual(fm["b"], "1_000")
        self.assertEqual(fm["c"], 42)

    def test_boolean_counter_does_not_flag_drift(self):
        parsed = godfiles.parse_god_document(
            "---\nprogress:\n  tasks_total: true\n---\n- [ ] GP-101 x\n  - Verify: `t`\n"
        )
        self.assertFalse(godfiles._digest_counter_drift(parsed["frontmatter"], parsed["counts"]))

    def test_tab_indented_field_is_captured(self):
        text = "- [ ] GP-101 Task\n\t- Verify: `npm test`\n"
        self.assertEqual(
            godfiles.parse_god_document(text)["tasks"][0]["verify_command"], "npm test"
        )

    def test_edge_fixture(self):
        digest = godfiles.load_god_artifact(FIXTURES / "EDGE.mdx", "godplans")
        by_id = {task["id"]: task for task in digest["tasks"]}
        self.assertEqual(sorted(by_id), ["GP-101", "GP-102"])
        self.assertTrue(by_id["GP-101"]["title"].startswith("[Windows]"))
        self.assertEqual(by_id["GP-102"]["verify_command"], "npm test -- b")


class TestSummaries(unittest.TestCase):
    def test_summaries_from_fixture_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".godplans").mkdir()
            (root / ".godaudits").mkdir()
            shutil.copy(FIXTURES / "PLAN.mdx", root / ".godplans" / "PLAN.mdx")
            shutil.copy(FIXTURES / "AUDIT.mdx", root / ".godaudits" / "AUDIT.mdx")
            plan = godfiles.godplans_summary(root)
            self.assertEqual(plan["status"], "executing")
            self.assertEqual(plan["tasks_done"], 2)
            self.assertEqual(plan["next_task_id"], "GP-201")
            self.assertIn("2/5 tasks done", plan["detail"])
            audit = godfiles.godaudits_summary(root)
            self.assertEqual(audit["status"], "reported")
            self.assertEqual(audit["open_critical"], 1)
            self.assertIn("score 64 (at risk)", audit["detail"])
            self.assertIn("1 open Critical", audit["detail"])

    def test_summaries_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(godfiles.godplans_summary(Path(tmp))["status"], "missing")
            self.assertEqual(godfiles.godaudits_summary(Path(tmp))["status"], "missing")


class TestPlanImportCli(unittest.TestCase):
    """CLI tests for plan import; subprocess with scrubbed env like test_mythify."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env.pop("MYTHIFY_PLAN_HORIZON", None)
        env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        env["HOME"] = str(self.home)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def init_with_artifacts(self, plan=True, audit=False):
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)
        if plan:
            plan_dir = self.project / ".godplans"
            plan_dir.mkdir()
            shutil.copy(FIXTURES / "PLAN.mdx", plan_dir / "PLAN.mdx")
        if audit:
            audit_dir = self.project / ".godaudits"
            audit_dir.mkdir()
            shutil.copy(FIXTURES / "AUDIT.mdx", audit_dir / "AUDIT.mdx")

    def load_plan_json(self, slug):
        path = self.project / ".mythify" / "plans" / (slug + ".json")
        with open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_import_discovers_plan_and_maps_fields(self):
        self.init_with_artifacts(plan=True)
        result = self.run_cli("plan", "import")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Imported 5 tasks from PLAN.mdx", result.stdout)
        self.assertIn("2 already completed", result.stdout)
        self.assertIn("Next verify: npm run db:migrate && npm run db:check", result.stdout)
        plan = self.load_plan_json("taskboard-godplans")
        self.assertTrue(plan["strict_context"])
        self.assertEqual(plan["source"]["kind"], "godplans")
        self.assertEqual(plan["source"]["version"], 2)
        step = plan["steps"][2]
        self.assertEqual(step["source_id"], "GP-201")
        self.assertEqual(step["verify_command"], "npm run db:migrate && npm run db:check")
        self.assertEqual(step["wave"], "2.1")
        self.assertEqual(step["phase"], "Auth and boards")
        self.assertEqual(step["depends_on"], ["GP-102"])
        self.assertEqual(plan["steps"][0]["status"], "completed")

    def test_import_audit_maps_fixes(self):
        self.init_with_artifacts(plan=False, audit=True)
        result = self.run_cli("plan", "import", "--source", "godaudits")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.load_plan_json("taskboard-godaudits")
        self.assertEqual(plan["steps"][0]["fixes"], ["F-SEC-1"])

    def test_import_requires_choice_when_both_artifacts_exist(self):
        self.init_with_artifacts(plan=True, audit=True)
        result = self.run_cli("plan", "import")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Pass a PATH or --source", result.stderr)

    def test_reimport_guard(self):
        self.init_with_artifacts(plan=True)
        self.assertEqual(self.run_cli("plan", "import").returncode, 0)
        result = self.run_cli("plan", "import")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already imported as plan taskboard-godplans", result.stderr)

    def test_import_missing_artifact_fails(self):
        self.init_with_artifacts(plan=False)
        result = self.run_cli("plan", "import")
        self.assertEqual(result.returncode, 1)
        self.assertIn("No godplans or godaudits artifact found", result.stderr)

    def test_strict_context_gate_end_to_end(self):
        self.init_with_artifacts(plan=True)
        self.assertEqual(self.run_cli("plan", "import").returncode, 0)
        no_evidence = self.run_cli("step", "3", "completed", "prose only")
        self.assertEqual(no_evidence.returncode, 1)
        self.assertIn("strict step context", no_evidence.stderr)
        run = self.run_cli("verify", "run", "true", "--claim", "context-free")
        self.assertEqual(run.returncode, 0, run.stderr)
        context_free = self.run_cli("step", "3", "completed", "verify run exit 0")
        self.assertEqual(context_free.returncode, 1)
        self.assertEqual(self.run_cli("step", "3", "in_progress").returncode, 0)
        plan = self.load_plan_json("taskboard-godplans")
        plan["steps"][2]["verify_command"] = "true"
        (self.project / ".mythify" / "plans" / "taskboard-godplans.json").write_text(
            json.dumps(plan, indent=2) + "\n",
            encoding="utf-8",
        )
        scoped = self.run_cli("verify", "run", "true", "--claim", "GP-201 verify")
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        completed = self.run_cli("step", "3", "completed", "verify run exit 0: GP-201")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_import_from_explicit_md_path(self):
        self.init_with_artifacts(plan=False)
        target = self.project / "PLAN.md"
        shutil.copy(FIXTURES / "PLAN.mdx", target)
        result = self.run_cli("plan", "import", str(target), "--name", "explicit")
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = self.load_plan_json("explicit")
        self.assertEqual(len(plan["steps"]), 5)


class TestGodRouting(TestPlanImportCli):
    """Route and classification awareness of god artifacts."""

    def test_route_godaudits_prompts_import(self):
        self.init_with_artifacts(plan=False, audit=True)
        result = self.run_cli("route", "audit this project with godaudits")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Workflow route: review", result.stdout)
        self.assertIn("godaudits audit exists", result.stdout)
        self.assertIn("plan import --source godaudits", result.stdout)

    def test_route_godplans_prompts_import(self):
        self.init_with_artifacts(plan=True)
        result = self.run_cli("route", "execute the godplans plan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Workflow route: plan", result.stdout)
        self.assertIn("plan import --source godplans", result.stdout)

    def test_route_works_without_workspace(self):
        plan_dir = self.project / ".godplans"
        plan_dir.mkdir()
        shutil.copy(FIXTURES / "PLAN.mdx", plan_dir / "PLAN.mdx")
        result = self.run_cli("route", "plan this project with godplans")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plan import --source godplans", result.stdout)

    def test_route_output_unchanged_without_artifacts(self):
        self.init_with_artifacts(plan=False)
        result = self.run_cli("route", "plan this project")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Workflow route: plan", result.stdout)
        self.assertIn(
            "Reason: Classification says this is multi-step work", result.stdout
        )
        self.assertIn("plan create 'plan this project' --horizon 20", result.stdout)
        self.assertNotIn("godplans", result.stdout)
        self.assertNotIn("godaudits", result.stdout)

    def test_classification_terms(self):
        self.init_with_artifacts(plan=False)
        design = self.run_cli("classify", "godplans", "--json")
        self.assertEqual(design.returncode, 0, design.stderr)
        self.assertEqual(json.loads(design.stdout)["task_type"], "design")
        review = self.run_cli("classify", "godaudits", "--json")
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(json.loads(review.stdout)["task_type"], "review")

    def test_review_prompt_packet_includes_audit(self):
        self.init_with_artifacts(plan=False, audit=True)
        result = self.run_cli("prompt", "review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Godaudits audit:", result.stdout)
        self.assertIn("1 open Critical", result.stdout)


class TestGodViews(TestPlanImportCli):
    """Readiness, harness, and phase views surface god artifacts."""

    def test_readiness_surfaces_artifacts(self):
        self.init_with_artifacts(plan=True, audit=True)
        result = self.run_cli("readiness")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Godplans plan: [~] executing; 2/5 tasks done", result.stdout)
        self.assertIn("Godaudits audit: [~] reported; score 64 (at risk)", result.stdout)

    def test_readiness_silent_without_artifacts(self):
        self.init_with_artifacts(plan=False)
        result = self.run_cli("readiness")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Godplans", result.stdout)
        self.assertNotIn("Godaudits", result.stdout)

    def test_harness_attention_and_next_action(self):
        self.init_with_artifacts(plan=False, audit=True)
        result = self.run_cli("harness")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("open Critical finding(s) in the godaudits audit", result.stdout)

    def test_harness_next_action_suggests_import(self):
        self.init_with_artifacts(plan=True)
        result = self.run_cli("harness", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        view = json.loads(result.stdout)
        self.assertIn("plan import --source godplans", view["next_action"])

    def test_phase_view_uses_imported_phase_fields(self):
        self.init_with_artifacts(plan=True)
        self.assertEqual(self.run_cli("plan", "import").returncode, 0)
        result = self.run_cli("phase", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        view = json.loads(result.stdout)
        by_phase = {phase["id"]: phase for phase in view["phases"]}
        verify_titles = [step["title"] for step in by_phase["verify"]["steps"]]
        self.assertTrue(any("GP-301" in title for title in verify_titles), verify_titles)


if __name__ == "__main__":
    unittest.main()
