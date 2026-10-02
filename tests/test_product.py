"""End-to-end coverage of product planning (mythify product).

A product record is material, but these tests pin the parts that make it
Mythify rather than a planning template: approval and bet verdicts are refused
without a human's words, approval needs a clean readiness check, editing an
approved product returns it to draft, promote only turns an approved bet into
a plan with lineage back to the product, and measure records executed evidence
through the same path as verify run, failing closed under MYTHIFY_DISABLE_RUN.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mythify_product  # noqa: E402

PASS = "{0} -c {1}".format(json.dumps(sys.executable), json.dumps("raise SystemExit(0)"))
FAIL = "{0} -c {1}".format(json.dumps(sys.executable), json.dumps("raise SystemExit(3)"))


class ProductCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.project = base / "project"
        self.home = base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.state = self.project / ".mythify"
        self.addCleanup(self._tmp.cleanup)
        self.ok("init")

    def run_cli(self, *args, env=None):
        environment = dict(os.environ)
        for key in (
            "MYTHIFY_DIR",
            "MYTHIFY_REQUIRE_VERIFIED_STEP",
            "MYTHIFY_REQUIRE_HUMAN_INPUT",
            "MYTHIFY_DISABLE_RUN",
        ):
            environment.pop(key, None)
        environment["HOME"] = str(self.home)
        environment.update(env or {})
        return subprocess.run(
            [sys.executable, str(CLI)] + list(args),
            cwd=str(self.project),
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def ok(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, "{0}\n{1}{2}".format(args, result.stdout, result.stderr))
        return result

    def refused(self, *args, code=1, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, code, "{0}\n{1}{2}".format(args, result.stdout, result.stderr))
        return result

    def record(self, name="onboarding"):
        return json.loads((self.state / "products" / (name + ".json")).read_text(encoding="utf-8"))

    def write_record(self, record, name="onboarding"):
        (self.state / "products" / (name + ".json")).write_text(json.dumps(record), encoding="utf-8")

    def verifications(self):
        path = self.state / "verifications.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def create(self, stage="prototype"):
        self.ok(
            "product", "create", "Activation onboarding",
            "--problem", "New teams never invite a teammate and churn in week one",
            "--user", "Team admin setting up a workspace",
            "--appetite", "six weeks",
            "--stage", stage,
            "--name", "onboarding",
        )

    def ready_product(self, measure=PASS, decide_by=""):
        """A prototype product that passes check: one outcome, one non-goal, one bet."""
        self.create()
        self.ok(
            "product", "outcome", "More teams invite a teammate in week one",
            "--metric", "week-one invite rate", "--baseline", "18%", "--target", "35%",
            "--target-source", "decided", "--measure", measure,
        )
        self.ok(
            "product", "non-goal", "Enterprise SSO",
            "--reason", "No enterprise buyers in the segment yet",
            "--revisit-when", "first enterprise deal",
        )
        bet = [
            "product", "bet", "An invite step inside setup lifts invites",
            "--outcome", "O1", "--kill", "invite rate under 22% after two weeks",
            "--decider", "Dana",
        ]
        if decide_by:
            bet += ["--decide-by", decide_by]
        self.ok(*bet)

    def approved_product(self, **kwargs):
        self.ready_product(**kwargs)
        self.ok("product", "approve", "--human-input", "Dana: approved, ship the invite step")

    def show_json(self):
        return json.loads(self.ok("product", "show", "--json").stdout)


class TestAuthoring(ProductCase):
    def test_create_sets_the_active_draft_record(self):
        self.create()
        record = self.record()
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["name"], "onboarding")
        self.assertEqual(record["status"], "draft")
        self.assertEqual(record["stage"], "prototype")
        self.assertEqual(record["appetite"], "six weeks")
        self.assertIsNone(record["approval"])
        for key in ("outcomes", "non_goals", "bets", "risks"):
            self.assertEqual(record[key], [])
        self.assertEqual((self.state / "active_product").read_text(encoding="utf-8").strip(), "onboarding")
        self.ok("product", "create", "Activation onboarding", "--problem", "p", "--user", "u", "--name", "onboarding")
        self.assertTrue((self.state / "products" / "onboarding-2.json").exists())

    def test_create_refuses_blank_problem_or_user(self):
        result = self.refused("product", "create", "T", "--problem", "   ", "--user", "u")
        self.assertIn("Problem required", result.stderr)
        result = self.refused("product", "create", "T", "--problem", "p", "--user", " ")
        self.assertIn("User required", result.stderr)
        self.assertFalse((self.state / "products").exists())

    def test_commands_without_a_product_are_refused(self):
        result = self.refused("product", "show")
        self.assertIn("product create", result.stderr)
        result = self.refused("product", "outcome", "x", "--metric", "m", "--target", "t", "--product", "nope")
        self.assertIn("Product not found: nope", result.stderr)

    def test_outcome_ids_fields_and_the_five_outcome_cap(self):
        self.create()
        self.ok(
            "product", "outcome", "Invites rise", "--metric", "invite rate",
            "--target", "35%", "--baseline", "18%", "--target-source", "cited",
            "--measure", PASS,
        )
        outcome = self.record()["outcomes"][0]
        self.assertEqual(outcome["id"], "O1")
        self.assertEqual(outcome["metric"], "invite rate")
        self.assertEqual(outcome["baseline"], "18%")
        self.assertEqual(outcome["target_source"], "cited")
        self.assertEqual(outcome["measure"], PASS)
        for index in range(2, 6):
            self.ok("product", "outcome", "Outcome {0}".format(index), "--metric", "m", "--target", "t")
        self.assertEqual([o["id"] for o in self.record()["outcomes"]], ["O1", "O2", "O3", "O4", "O5"])
        self.assertIsNone(self.record()["outcomes"][1]["target_source"])
        self.assertIsNone(self.record()["outcomes"][1]["measure"])
        result = self.refused("product", "outcome", "Sixth", "--metric", "m", "--target", "t")
        self.assertIn("already has 5 outcomes", result.stderr)
        self.assertEqual(len(self.record()["outcomes"]), 5)

    def test_outcome_refuses_blank_fields_and_noop_measure(self):
        self.create()
        self.refused("product", "outcome", "x", "--metric", " ", "--target", "t")
        self.refused("product", "outcome", "x", "--metric", "m", "--target", "")
        for noop in ("true", "echo 35%", "exit 0"):
            result = self.refused("product", "outcome", "x", "--metric", "m", "--target", "t", "--measure", noop)
            self.assertIn("no-op", result.stderr)
        self.assertEqual(self.record()["outcomes"], [])

    def test_non_goal_bet_and_risk(self):
        self.create()
        self.ok("product", "outcome", "a", "--metric", "m", "--target", "t")
        self.ok("product", "outcome", "b", "--metric", "m", "--target", "t")
        self.ok("product", "non-goal", "SSO", "--reason", "no buyers", "--revisit-when", "first deal")
        entry = self.record()["non_goals"][0]
        self.assertEqual(
            (entry["id"], entry["text"], entry["reason"], entry["revisit_when"]),
            ("N1", "SSO", "no buyers", "first deal"),
        )
        self.refused("product", "non-goal", "Mobile", "--reason", " ")
        self.ok(
            "product", "bet", "Invite step", "--outcome", "O1,o2", "--kill", "flat after two weeks",
            "--decider", "Dana", "--decide-by", "2999-12-31", "--priority", "5",
        )
        self.ok("product", "bet", "Checklist", "--outcome", "O2", "--kill", "tickets flat")
        bets = self.record()["bets"]
        self.assertEqual(bets[0]["id"], "B1")
        self.assertEqual(bets[0]["outcomes"], ["O1", "O2"])
        self.assertEqual(bets[0]["status"], "proposed")
        self.assertEqual(bets[0]["verdict"], "pending")
        self.assertIsNone(bets[0]["plan"])
        self.assertEqual(bets[0]["priority"], 5)
        self.assertEqual(bets[1]["priority"], 6)
        result = self.refused("product", "bet", "Ghost", "--outcome", "O9", "--kill", "k")
        self.assertIn("Outcome O9 not found", result.stderr)
        self.refused("product", "bet", "No kill", "--outcome", "O1", "--kill", "  ")
        warned = self.ok("product", "bet", "Vague date", "--outcome", "O1", "--kill", "k", "--decide-by", "next spring")
        self.assertIn("not an ISO date", warned.stdout)
        self.ok("product", "risk", "Admins skip it", "--kind", "value", "--mitigation", "visible", "--validation", "interviews")
        risk = self.record()["risks"][0]
        self.assertEqual((risk["id"], risk["kind"], risk["mitigation"]), ("R1", "value", "visible"))
        bad = self.run_cli("product", "risk", "x", "--kind", "legal")
        self.assertNotEqual(bad.returncode, 0)

    def test_list_marks_the_active_product(self):
        self.create()
        self.ok("product", "create", "Second", "--problem", "p", "--user", "u", "--name", "second")
        rows = json.loads(self.ok("product", "list", "--json").stdout)
        self.assertEqual([row["id"] for row in rows], ["onboarding", "second"])
        self.assertEqual([row["active"] for row in rows], [False, True])
        text = self.ok("product", "list").stdout
        self.assertIn("[OK] Products (2):", text)
        self.assertIn("* second: draft", text)


class TestReadinessRules(unittest.TestCase):
    """Every check rule, per stage, through the deterministic lint."""

    def base(self, stage):
        return {
            "stage": stage,
            "problem": "Teams churn in week one",
            "user": "Team admin",
            "outcomes": [{
                "id": "O1", "metric": "invite rate", "target": "35%",
                "target_source": "decided", "measure": "python3 -m unittest",
            }],
            "non_goals": [{"id": "N1", "text": "SSO", "reason": "no buyers"}],
            "bets": [{
                "id": "B1", "outcomes": ["O1"], "kill": "flat", "decider": "Dana",
                "decide_by": "2999-12-31",
            }],
            "risks": [
                {"id": "R{0}".format(index + 1), "kind": kind, "text": kind, "mitigation": "m"}
                for index, kind in enumerate(mythify_product.RISK_KINDS)
            ],
        }

    def codes(self, record):
        return sorted(gap["code"] for gap in mythify_product.readiness_gaps(record)[1])

    def test_a_complete_record_is_ready_at_every_stage(self):
        for stage in mythify_product.PRODUCT_STAGES:
            self.assertEqual(self.codes(self.base(stage)), [], stage)

    def test_prototype_rules(self):
        record = self.base("prototype")
        record.update(problem=" ", user="", non_goals=[])
        record["outcomes"][0].update(metric="", target="")
        record["bets"].append({"id": "B2", "outcomes": [], "kill": "k"})
        record["bets"].append({"id": "B3", "outcomes": ["O7"], "kill": "k"})
        self.assertEqual(self.codes(record), sorted([
            "problem_missing", "user_missing", "outcome_metric_missing",
            "outcome_target_missing", "non_goal_missing", "bet_outcome_missing",
            "bet_outcome_unknown",
        ]))
        record["outcomes"] = []
        self.assertIn("outcome_missing", self.codes(record))

    def test_pre_launch_adds_target_source_and_a_value_risk(self):
        record = self.base("pre-launch")
        record["outcomes"][0]["target_source"] = None
        record["risks"] = [risk for risk in record["risks"] if risk["kind"] != "value"]
        self.assertEqual(self.codes(record), ["outcome_target_source_missing", "value_risk_missing"])
        record["stage"] = "prototype"
        self.assertEqual(self.codes(record), [])

    def test_growth_adds_measure_commands_and_deciders(self):
        record = self.base("growth")
        record["outcomes"][0]["measure"] = None
        record["outcomes"].append({"id": "O2", "metric": "m", "target": "t", "target_source": "user", "measure": "true"})
        record["bets"][0]["decider"] = ""
        record["bets"][0]["outcomes"] = ["O1", "O2"]
        self.assertEqual(
            self.codes(record),
            ["bet_decider_missing", "outcome_measure_missing", "outcome_measure_noop"],
        )
        record["stage"] = "pre-launch"
        self.assertEqual(self.codes(record), [])

    def test_enterprise_adds_dates_mitigations_and_all_four_risk_kinds(self):
        record = self.base("enterprise")
        record["bets"][0]["decide_by"] = ""
        record["risks"] = [{"id": "R1", "kind": "value", "text": "v", "mitigation": ""}]
        gaps = mythify_product.readiness_gaps(record)[1]
        self.assertEqual(
            sorted(gap["code"] for gap in gaps),
            ["bet_decide_by_missing", "risk_kind_missing", "risk_kind_missing",
             "risk_kind_missing", "risk_mitigation_missing"],
        )
        missing_kinds = sorted(gap["item"] for gap in gaps if gap["code"] == "risk_kind_missing")
        self.assertEqual(missing_kinds, ["feasibility", "usability", "viability"])
        record["stage"] = "growth"
        self.assertEqual(self.codes(record), [])

    def test_every_gap_message_names_its_field(self):
        record = self.base("enterprise")
        record.update(problem="", non_goals=[], risks=[])
        record["outcomes"][0].update(target_source=None, measure=None)
        for gap in mythify_product.readiness_gaps(record)[1]:
            self.assertEqual(sorted(gap), ["code", "item", "message"])
            field = gap["message"].split(":", 1)[0]
            self.assertTrue(field and " " not in field, gap)


class TestCheckCommand(ProductCase):
    def test_check_exits_two_with_gaps_and_zero_when_ready(self):
        self.create(stage="pre-launch")
        result = self.refused("product", "check", code=2)
        self.assertIn("gap(s) at stage pre-launch", result.stdout)
        self.assertIn("- outcomes:", result.stdout)
        self.assertIn("- non_goals:", result.stdout)
        self.assertIn("- risks.value:", result.stdout)
        payload = json.loads(self.refused("product", "check", "--json", code=2).stdout)
        self.assertEqual(sorted(payload), ["gaps", "ready", "stage"])
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["stage"], "pre-launch")
        self.assertEqual(
            [gap["code"] for gap in payload["gaps"]],
            ["outcome_missing", "non_goal_missing", "value_risk_missing"],
        )
        for gap in payload["gaps"]:
            self.assertEqual(sorted(gap), ["code", "item", "message"])
        self.ok("product", "outcome", "a", "--metric", "m", "--target", "t", "--target-source", "user")
        self.ok("product", "non-goal", "SSO", "--reason", "no buyers")
        self.ok("product", "risk", "they may not care", "--kind", "value")
        ready = self.ok("product", "check", "onboarding")
        self.assertIn("is ready at stage pre-launch", ready.stdout)
        payload = json.loads(self.ok("product", "check", "--json").stdout)
        self.assertEqual(payload, {"ready": True, "stage": "pre-launch", "gaps": []})


class TestHumanGates(ProductCase):
    def test_approve_without_human_input_is_refused(self):
        self.ready_product()
        for argv in (("product", "approve"), ("product", "approve", "--human-input", "   ")):
            result = self.refused(*argv)
            self.assertIn("Human input required", result.stderr)
            self.assertIn("cannot approve it from its own words", result.stderr)
        record = self.record()
        self.assertEqual(record["status"], "draft")
        self.assertIsNone(record["approval"])

    def test_approve_with_gaps_is_refused_even_with_human_input(self):
        self.create()
        result = self.refused("product", "approve", "--human-input", "Dana: go")
        self.assertIn("readiness gap(s)", result.stderr)
        self.assertIn("- outcomes:", result.stderr)
        self.assertEqual(self.record()["status"], "draft")

    def test_approve_records_the_human_words(self):
        self.ready_product()
        result = self.ok("product", "approve", "--human-input", "  Dana: approved  ")
        self.assertIn("Human input: Dana: approved", result.stdout)
        record = self.record()
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["approval"]["human_input"], "Dana: approved")
        self.assertEqual(record["approval"]["stage"], "prototype")
        self.assertTrue(record["approval"]["at"])
        again = self.ok("product", "approve", "--human-input", "again")
        self.assertIn("already approved", again.stdout)
        self.assertEqual(self.record()["approval"]["human_input"], "Dana: approved")

    def test_editing_an_approved_product_returns_it_to_draft(self):
        self.approved_product()
        approved_words = "Dana: approved, ship the invite step"
        for index, argv in enumerate((
            ("product", "outcome", "Fewer tickets", "--metric", "tickets", "--target", "2"),
            ("product", "non-goal", "Mobile", "--reason", "web first"),
            ("product", "bet", "Checklist", "--outcome", "O1", "--kill", "flat"),
            ("product", "risk", "Too slow", "--kind", "feasibility"),
        )):
            result = self.ok(*argv)
            self.assertIn("back to draft", result.stderr, argv)
            record = self.record()
            self.assertEqual(record["status"], "draft")
            self.assertIsNone(record["approval"])
            self.assertEqual(len(record["approval_history"]), index + 1)
            self.assertEqual(record["approval_history"][-1]["human_input"], approved_words)
            self.assertIn(argv[1], record["approval_history"][-1]["revoked_by"])
            approved_words = "Dana: approved round {0}".format(index + 2)
            self.ok("product", "approve", "--human-input", approved_words)

    def test_new_bet_cannot_ride_an_older_approval_into_a_plan(self):
        self.approved_product()
        self.ok("product", "bet", "Unseen bet", "--outcome", "O1", "--kill", "flat")
        result = self.refused("product", "promote", "B2")
        self.assertIn("not approved", result.stderr)
        self.assertFalse((self.state / "plans" / "onboarding-b2.json").exists())

    def test_decide_without_human_input_is_refused(self):
        self.ready_product()
        result = self.refused("product", "decide", "B1", "--verdict", "stop")
        self.assertIn("Human input required", result.stderr)
        bet = self.record()["bets"][0]
        self.assertEqual((bet["status"], bet["verdict"]), ("proposed", "pending"))

    def test_decide_records_verdicts_and_stop_is_final(self):
        self.ready_product()
        self.ok("product", "decide", "b1", "--verdict", "pivot", "--human-input", "Dana: pivot to email")
        bet = self.record()["bets"][0]
        self.assertEqual((bet["verdict"], bet["status"]), ("pivot", "proposed"))
        self.assertEqual(bet["verdict_human_input"], "Dana: pivot to email")
        self.ok("product", "decide", "B1", "--verdict", "stop", "--human-input", "Dana: stop it")
        bet = self.record()["bets"][0]
        self.assertEqual((bet["verdict"], bet["status"]), ("stop", "stopped"))
        result = self.refused("product", "decide", "B1", "--verdict", "continue", "--human-input", "Dana: revive")
        self.assertIn("stopped bet is final", result.stderr)
        self.refused("product", "decide", "B9", "--verdict", "stop", "--human-input", "x")
        self.assertNotEqual(self.run_cli("product", "decide", "B1", "--verdict", "maybe").returncode, 0)

    def test_human_input_gate_has_the_map_legacy_opt_out(self):
        self.ready_product()
        waived = {"MYTHIFY_REQUIRE_HUMAN_INPUT": "0"}
        result = self.ok("product", "approve", env=waived)
        self.assertIn("Human-input gate waived", result.stderr)
        approval = self.record()["approval"]
        self.assertTrue(approval["human_input_waived"])
        self.assertEqual(approval["human_input"], "")
        result = self.ok("product", "decide", "B1", "--verdict", "continue", env=waived)
        self.assertIn("Human-input gate waived", result.stderr)
        self.assertTrue(self.record()["bets"][0]["human_input_waived"])
        status = json.loads(self.ok("status", "--json", env=waived).stdout)
        self.assertTrue(any("MYTHIFY_REQUIRE_HUMAN_INPUT" in item["summary"] for item in status["attention"]))


class TestPromote(ProductCase):
    def test_promote_requires_an_approved_product(self):
        self.ready_product()
        result = self.refused("product", "promote", "B1")
        self.assertIn("is draft, not approved", result.stderr)
        self.assertEqual(list((self.state / "plans").glob("*.json")), [])

    def test_promote_creates_a_plan_with_product_ref_and_lineage(self):
        self.approved_product()
        steps = json.dumps([{"title": "Add invite step", "verify_command": PASS}])
        result = self.ok("product", "promote", "B1", "--steps", steps)
        self.assertIn("Promoted bet B1 of product onboarding to plan onboarding-b1", result.stdout)
        plan = json.loads((self.state / "plans" / "onboarding-b1.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["goal"], "An invite step inside setup lifts invites")
        self.assertEqual(plan["product_ref"], {"product": "onboarding", "bet": "B1", "outcomes": ["O1"]})
        self.assertEqual(plan["steps"][0]["verify_command"], PASS)
        parents = plan["lineage"]["parents"]
        self.assertEqual([(p["kind"], p["id"]) for p in parents], [("product", "onboarding")])
        lineage = json.loads(self.ok("lineage", "status", "plan", "onboarding-b1", "--json").stdout)
        self.assertEqual(lineage["status"], "current")
        bet = self.record()["bets"][0]
        self.assertEqual((bet["status"], bet["plan"]), ("in_flight", "onboarding-b1"))
        self.assertEqual((self.state / "plans" / "active").read_text(encoding="utf-8").strip(), "onboarding-b1")
        shown = self.ok("plan", "show").stdout
        self.assertIn("Product: onboarding bet B1 (outcomes: O1)", shown)

    def test_second_promote_is_refused_while_the_plan_exists(self):
        self.approved_product()
        self.ok("product", "promote", "B1", "--plan", "invite-work")
        result = self.refused("product", "promote", "B1")
        self.assertIn("already promoted to plan invite-work", result.stderr)
        self.assertEqual(sorted(p.stem for p in (self.state / "plans").glob("*.json")), ["invite-work"])
        (self.state / "plans" / "invite-work.json").unlink()
        self.ok("product", "promote", "B1", "--plan", "invite-work-2")

    def test_promote_refuses_unknown_stopped_and_bad_steps(self):
        self.approved_product()
        self.assertIn("not found", self.refused("product", "promote", "B7").stderr)
        self.assertIn("Invalid --steps JSON", self.refused("product", "promote", "B1", "--steps", "{").stderr)
        self.assertIn("JSON array", self.refused("product", "promote", "B1", "--steps", "{}").stderr)
        self.assertIn("title", self.refused("product", "promote", "B1", "--steps", "[{}]").stderr)
        self.assertEqual(self.record()["bets"][0]["status"], "proposed")
        self.ok("product", "decide", "B1", "--verdict", "stop", "--human-input", "Dana: stop")
        self.assertIn("never becomes a plan", self.refused("product", "promote", "B1").stderr)


class TestMeasure(ProductCase):
    def test_measure_records_executed_evidence_with_the_product_parent(self):
        self.ready_product()
        result = self.ok("product", "measure", "O1")
        self.assertIn("MEASURED outcome O1", result.stdout)
        records = self.verifications()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["kind"], "executed")
        self.assertIs(record["verified"], True)
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["command"], PASS)
        self.assertEqual(record["claim"], "O1 measured: week-one invite rate")
        self.assertEqual(
            [(p["kind"], p["id"]) for p in record["lineage"]["parents"]],
            [("product", "onboarding")],
        )
        self.assertIn("provenance", record)
        self.assertIn("stdout", record["artifacts"])
        self.assertIsNone(record["plan"])
        latest = self.show_json()["measurements"]["O1"]
        self.assertTrue(latest["verified"])
        self.assertEqual(latest["verification_id"], record["id"])

    def test_failing_measure_is_recorded_and_exits_two(self):
        self.ready_product(measure=FAIL)
        result = self.refused("product", "measure", "O1", code=2)
        self.assertIn("UNMEASURED outcome O1", result.stdout)
        record = self.verifications()[-1]
        self.assertIs(record["verified"], False)
        self.assertEqual(record["exit_code"], 3)
        self.assertIn("failed (exit 3", self.ok("product", "show").stdout)

    def test_measure_refuses_missing_and_noop_commands(self):
        self.create()
        self.ok("product", "outcome", "a", "--metric", "m", "--target", "t")
        result = self.refused("product", "measure", "O1")
        self.assertIn("has no measure command", result.stderr)
        record = self.record()
        record["outcomes"][0]["measure"] = "true"
        self.write_record(record)
        result = self.refused("product", "measure", "O1")
        self.assertIn("no-op", result.stderr)
        self.refused("product", "measure", "O4")
        self.assertEqual(self.verifications(), [])

    def test_measure_fails_closed_when_runs_are_disabled(self):
        self.ready_product()
        result = self.refused("product", "measure", "O1", code=2, env={"MYTHIFY_DISABLE_RUN": "1"})
        self.assertIn("MYTHIFY_DISABLE_RUN=1", result.stderr)
        self.assertEqual(self.verifications(), [])

    def test_measure_does_not_satisfy_an_in_progress_step(self):
        self.ok("plan", "create", "Other work", "--steps", json.dumps([{"title": "s1"}]))
        self.ok("step", "1", "in_progress")
        self.ready_product()
        self.ok("product", "measure", "O1")
        result = self.refused("step", "1", "completed", "measured", code=1)
        self.assertIn("Verified evidence required", result.stderr)

    def test_verify_run_cannot_pose_as_a_measurement(self):
        self.ready_product(measure=FAIL)
        self.ok(
            "product", "outcome", "Fewer setup tickets", "--metric", "setup tickets",
            "--target", "2", "--measure", PASS,
        )
        self.ok("product", "bet", "Checklist", "--outcome", "O2", "--kill", "flat", "--decider", "Dana")
        self.ok("product", "approve", "--human-input", "Dana: go")
        steps = json.dumps([{"title": "Add invite step", "verify_command": PASS}])
        self.ok("product", "promote", "B1", "--steps", steps)
        spoof = self.ok(
            "verify", "run", PASS, "--claim", "O1 measured: week-one invite rate",
            "--parent", "product:onboarding",
        )
        self.assertIn("VERIFIED", spoof.stdout)
        self.assertNotIn("O1", self.show_json()["measurements"])
        self.assertIn("never measured", self.ok("product", "show").stdout)
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "verify run exit 0")
        flags = [(item["code"], item["item"]) for item in self.show_json()["flags"]]
        self.assertIn(("completed_plan_unmeasured", "B1"), flags)
        self.ok("product", "measure", "O2")
        self.assertEqual(self.record()["bets"][0]["status"], "in_flight")
        self.assertTrue(self.show_json()["measurements"]["O2"]["verified"])
        record = self.record()
        record["outcomes"][1]["measure"] = FAIL
        self.write_record(record)
        self.assertNotIn("O2", self.show_json()["measurements"])

    def test_measure_respects_the_timeout(self):
        slow = "{0} -c {1}".format(json.dumps(sys.executable), json.dumps("import time; time.sleep(5)"))
        self.ready_product(measure=slow)
        result = self.refused("product", "measure", "O1", "--timeout", "0.5", code=2)
        self.assertIn("timed out", result.stdout)
        self.assertEqual(self.verifications()[-1]["exit_code"], -1)


class TestShowAndFlags(ProductCase):
    def flags(self):
        return [(item["code"], item["item"]) for item in self.show_json()["flags"]]

    def test_traceability_flags(self):
        self.ready_product(decide_by="2020-01-01")
        self.ok("product", "outcome", "Fewer tickets", "--metric", "tickets", "--target", "2")
        self.assertEqual(
            self.flags(),
            [("outcome_without_bet", "O2"), ("verdict_overdue", "B1")],
        )
        self.ok("product", "bet", "Checklist", "--outcome", "O2", "--kill", "flat", "--decide-by", "2999-12-31")
        self.ok("product", "approve", "--human-input", "Dana: go")
        self.assertEqual(
            self.flags(),
            [("approved_bet_without_plan", "B1"), ("verdict_overdue", "B1"),
             ("approved_bet_without_plan", "B2")],
        )
        steps = json.dumps([{"title": "Add invite step", "verify_command": PASS}])
        self.ok("product", "promote", "B1", "--steps", steps)
        self.ok("product", "decide", "B1", "--verdict", "continue", "--human-input", "Dana: continue")
        self.ok("product", "decide", "B2", "--verdict", "stop", "--human-input", "Dana: stop")
        self.assertEqual(self.flags(), [("outcome_without_bet", "O2")])
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "verify run exit 0")
        self.assertIn(("completed_plan_unmeasured", "B1"), self.flags())
        self.ok("product", "measure", "O1")
        self.assertNotIn(("completed_plan_unmeasured", "B1"), self.flags())
        bet = self.record()["bets"][0]
        self.assertEqual(bet["status"], "shipped")

    def test_flags_unit_dates(self):
        record = {
            "status": "draft",
            "outcomes": [{"id": "O1"}],
            "bets": [
                {"id": "B1", "outcomes": ["O1"], "verdict": "pending", "decide_by": "2026-09-30"},
                {"id": "B2", "outcomes": ["O1"], "verdict": "pending", "decide_by": "2026-10-01"},
                {"id": "B3", "outcomes": ["O1"], "verdict": "pending", "decide_by": "soon"},
                {"id": "B4", "outcomes": ["O1"], "verdict": "continue", "decide_by": "2020-01-01"},
                {"id": "B5", "outcomes": ["O1"], "verdict": "pending", "decide_by": "2026-09-01T10:00:00Z"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            flags = mythify_product.traceability_flags(
                Path(tmp), "p", record, measured={}, today=date(2026, 10, 1)
            )
        self.assertEqual(
            [(item["code"], item["item"]) for item in flags],
            [("verdict_overdue", "B1"), ("verdict_overdue", "B5")],
        )

    def test_show_text_json_and_markdown(self):
        self.approved_product(decide_by="2999-12-31")
        self.ok("product", "risk", "Admins skip it", "--kind", "value", "--mitigation", "visible")
        self.ok("product", "bet", "Second | bet", "--outcome", "O1", "--kill", "flat", "--priority", "0")
        text = self.ok("product", "show").stdout
        for expected in (
            "[OK] Product onboarding: Activation onboarding",
            "Status: draft; not approved",
            "Stage: prototype; appetite: six weeks",
            "Problem: New teams never invite a teammate",
            "User: Team admin setting up a workspace",
            "metric: week-one invite rate; baseline: 18%; target: 35% (source: decided)",
            "last measurement: never measured",
            "N1: Enterprise SSO (why: No enterprise buyers in the segment yet; revisit when: first enterprise deal)",
            "decider: Dana; decide by: 2999-12-31; no plan",
            "value (will the user want it): 1 recorded",
            "viability (does it work for the business): none recorded",
            "Traceability (0):",
            "Guardrail:",
        ):
            self.assertIn(expected, text)
        self.assertLess(text.index("- B2 "), text.index("- B1 "), "bets sort by priority, then id")
        payload = self.show_json()
        for key in ("name", "title", "status", "stage", "problem", "user", "outcomes", "non_goals", "bets", "risks"):
            self.assertIn(key, payload)
        for key in ("id", "active", "ready", "gaps", "flags", "measurements", "plans", "next_action"):
            self.assertIn(key, payload)
        self.assertTrue(payload["active"])
        markdown = self.ok("product", "show", "--markdown").stdout
        markdown.encode("ascii")
        self.assertTrue(markdown.startswith("# Activation onboarding\n"))
        for heading in ("## Problem", "## Primary user", "## Outcomes", "## Non-goals", "## Bets",
                        "## Risks", "### Value (will the user want it)", "## Traceability"):
            self.assertIn("\n" + heading + "\n", markdown)
        self.assertIn("| O1 | More teams invite a teammate in week one | week-one invite rate | 18% | 35% | decided | never measured |", markdown)
        self.assertIn("Second \\| bet", markdown)
        self.assertNotEqual(self.run_cli("product", "show", "--json", "--markdown").returncode, 0)


class TestStatusSummaryAndRoute(ProductCase):
    def test_status_and_summary_show_products(self):
        status = self.ok("status").stdout
        self.assertIn("Active product: none", status)
        self.assertIsNone(json.loads(self.ok("status", "--json").stdout)["active_product"])
        self.create()
        status = self.ok("status").stdout
        self.assertIn("Active product: onboarding (draft, 2 gap(s), 0 flag(s)) - Activation onboarding", status)
        view = json.loads(self.ok("status", "--json").stdout)["active_product"]
        self.assertEqual(
            {key: view[key] for key in ("id", "status", "stage", "ready", "gap_count", "flag_count")},
            {"id": "onboarding", "status": "draft", "stage": "prototype", "ready": False,
             "gap_count": 2, "flag_count": 0},
        )
        summary = json.loads(self.ok("summary", "--json").stdout)
        self.assertEqual(summary["products"][0]["id"], "onboarding")
        self.assertTrue(summary["products"][0]["active"])
        self.assertIn("Products (1):", self.ok("summary").stdout)

    def route(self, task):
        return json.loads(self.ok("route", task, "--json").stdout)

    def test_product_prompts_route_to_product(self):
        for task in (
            "write a product roadmap and PRD for onboarding",
            "write a product plan for onboarding",
            "what should we build next quarter",
            "define the north star and kill criteria for search",
        ):
            with self.subTest(task=task):
                payload = self.route(task)
                self.assertEqual(payload["classification"]["task_type"], "product")
                self.assertNotEqual(payload["classification"]["task_type"], "trivial")
                self.assertEqual(payload["route"], "product")
                self.assertEqual(payload["prompt_packet"]["kind"], "product")
                self.assertEqual(
                    payload["next_command"],
                    'mythify product create "TITLE" --problem "..." --user "..."',
                )

    def test_product_route_follows_the_active_product(self):
        self.create()
        payload = self.route("prioritize the onboarding roadmap")
        self.assertEqual(payload["route"], "product")
        self.assertEqual(payload["next_command"], "mythify product check")
        self.assertEqual(payload["state"]["active_product"]["id"], "onboarding")
        self.assertIn("active_product", [item["type"] for item in payload["evidence"]])
        self.ok("product", "outcome", "a", "--metric", "m", "--target", "t")
        self.ok("product", "non-goal", "SSO", "--reason", "no buyers")
        self.assertEqual(self.route("prioritize the onboarding roadmap")["next_command"], "mythify product show")

    def test_product_prompt_packet(self):
        empty = json.loads(self.ok("prompt", "product", "--json").stdout)
        self.assertEqual(empty["kind"], "product")
        self.assertIsNone(empty["context"]["product"])
        for phrase in (
            "in the user's words without naming the solution",
            "one primary user",
            "at most five outcomes",
            "revisit",
            "kill criterion",
            "human decider",
            "value (will the user want it)",
            "usability",
            "feasibility",
            "viability",
            "Only the human approves",
            "product measure",
        ):
            self.assertIn(phrase, empty["next_prompt"])
        self.create()
        packet = json.loads(self.ok("prompt", "product", "--json").stdout)
        self.assertEqual(packet["source"], {"type": "product", "id": "onboarding"})
        self.assertFalse(packet["context"]["product"]["ready"])
        self.assertIn("Readiness gaps (2):", packet["next_prompt"])
        self.refused("prompt", "product", "missing")


PRODUCT_TOOLS = (
    "product_create", "product_outcome", "product_non_goal", "product_bet",
    "product_risk", "product_check", "product_approve", "product_decide",
    "product_promote", "product_measure", "product_show",
)


class TestProductThroughMcp(ProductCase):
    """The MCP server exposes the product tools and keeps the human gate."""

    def mcp(self, calls):
        messages = [{
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "product-test", "version": "1"}},
        }, {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        for index, (name, arguments) in enumerate(calls, start=3):
            messages.append({
                "jsonrpc": "2.0", "id": index, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })
        environment = dict(os.environ)
        for key in ("MYTHIFY_DIR", "MYTHIFY_REQUIRE_HUMAN_INPUT", "MYTHIFY_MCP_CALL_TIMEOUT"):
            environment.pop(key, None)
        environment["HOME"] = str(self.home)
        result = subprocess.run(
            [sys.executable, str(CLI), "mcp"],
            cwd=str(self.project),
            env=environment,
            input="".join(json.dumps(message) + "\n" for message in messages),
            capture_output=True,
            text=True,
            timeout=300,
        )
        responses = {}
        for line in result.stdout.splitlines():
            if line.strip():
                message = json.loads(line)
                responses[message.get("id")] = message
        return responses

    def test_tools_list_and_the_approval_gate(self):
        responses = self.mcp([
            ("product_create", {
                "title": "Activation onboarding", "problem": "Teams churn in week one",
                "user": "Team admin", "name": "onboarding",
            }),
            ("product_outcome", {"statement": "Invites rise", "metric": "invite rate", "target": "35%"}),
            ("product_non_goal", {"text": "SSO", "reason": "no buyers"}),
            ("product_check", {"json": True}),
            ("product_approve", {}),
            ("product_approve", {"human_input": "   "}),
        ])
        names = [tool["name"] for tool in responses[2]["result"]["tools"]]
        for name in PRODUCT_TOOLS + ("prompt_product",):
            self.assertIn(name, names)
        self.assertNotIn("product_list", names)
        created = responses[3]["result"]
        self.assertFalse(created["isError"], created)
        check = responses[6]["result"]
        self.assertFalse(check["isError"], check)
        self.assertIn("exit_code: 0", check["content"][0]["text"])
        for request_id in (7, 8):
            refused = responses[request_id]["result"]
            text = refused["content"][0]["text"]
            self.assertTrue(refused["isError"], text)
            self.assertIn("Human input required", text)
            self.assertIn("exit_code: 1", text)
        record = self.record()
        self.assertEqual(record["status"], "draft")
        self.assertIsNone(record["approval"])


if __name__ == "__main__":
    unittest.main()
