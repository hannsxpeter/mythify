"""End-to-end coverage of the wayfinding decision map surface.

A map is the surface before a plan exists. These tests pin the parts that make
it Mythify rather than a decision board: a human-in-the-loop ticket cannot be
resolved from the agent's own words, a task ticket with a verify command needs a
passing executed run scoped to the claim, one decision ticket is held at a time,
and a map only hands off to a plan once nothing is left to decide.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class MapCase(unittest.TestCase):
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
        env.pop("MYTHIFY_PLAN_HORIZON", None)
        env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        env.pop("MYTHIFY_REQUIRE_HUMAN_INPUT", None)
        env.pop("MYTHIFY_MAP_CLAIMANT", None)
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

    def chart(self):
        self.ok(
            "map",
            "create",
            "Ship a billing revamp spec",
            "--name",
            "billing",
            "--notes",
            "domain: billing",
            "--fog",
            "how do existing subscriptions migrate",
        )

    def map_json(self):
        result = self.ok("map", "show", "--json")
        return json.loads(result.stdout)

    def ticket(self, payload, ticket_id):
        for item in payload["tickets"]:
            if item["id"] == ticket_id:
                return item
        raise AssertionError("ticket {0} missing".format(ticket_id))


class TestChartTheMap(MapCase):
    def test_create_records_destination_notes_and_fog(self):
        self.chart()
        payload = self.map_json()
        self.assertEqual(payload["destination"], "Ship a billing revamp spec")
        self.assertEqual(payload["notes"], "domain: billing")
        self.assertEqual(payload["status"], "charting")
        self.assertEqual(len(payload["fog"]), 1)
        self.assertEqual(payload["fog"][0]["id"], "F1")
        self.assertEqual(payload["tickets"], [])
        self.assertFalse(payload["clear"])

    def test_ticket_type_fixes_the_human_in_the_loop_mode(self):
        self.chart()
        expected = {
            "research": "afk",
            "prototype": "hitl",
            "grilling": "hitl",
            "task": "afk",
        }
        for kind, mode in expected.items():
            self.ok("map", "ticket", "decide {0}".format(kind), "--type", kind)
        payload = self.map_json()
        actual = {item["type"]: item["mode"] for item in payload["tickets"]}
        self.assertEqual(actual, expected)

    def test_mode_override_is_refused_for_conversation_tickets(self):
        self.chart()
        result = self.run_cli(
            "map", "ticket", "Pick the proration model", "--type", "grilling", "--mode", "afk"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Mode is fixed for grilling tickets", result.stderr)
        self.assertEqual(self.map_json()["tickets"], [])

    def test_task_tickets_may_choose_their_mode(self):
        self.chart()
        self.ok("map", "ticket", "Sign up for the gateway", "--type", "task", "--mode", "hitl")
        self.assertEqual(self.ticket(self.map_json(), "T1")["mode"], "hitl")

    def test_blocking_edges_shape_the_frontier(self):
        self.chart()
        self.ok("map", "ticket", "Confirm refunds API", "--type", "research")
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--blocked-by", "T1")
        payload = self.map_json()
        self.assertEqual(payload["frontier"], ["T1"])
        self.assertEqual(self.ticket(payload, "T2")["blocked_by"], ["T1"])

    def test_unknown_blocker_is_refused(self):
        self.chart()
        result = self.run_cli("map", "ticket", "Provision sandbox", "--type", "task", "--blocked-by", "T9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Blocking ticket not found", result.stderr)

    def test_fog_graduates_into_exactly_one_ticket(self):
        self.chart()
        self.ok("map", "ticket", "Decide the cutover", "--type", "grilling", "--from-fog", "F1")
        payload = self.map_json()
        self.assertEqual(payload["fog"][0]["graduated_to"], "T1")
        again = self.run_cli("map", "ticket", "Decide it twice", "--type", "grilling", "--from-fog", "F1")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already graduated", again.stderr)


class TestClaimDiscipline(MapCase):
    def test_blocked_ticket_cannot_be_claimed(self):
        self.chart()
        self.ok("map", "ticket", "Confirm refunds API", "--type", "research")
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--blocked-by", "T1")
        result = self.run_cli("map", "claim", "T2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is blocked by", result.stderr)

    def test_one_decision_ticket_per_claimant_but_research_runs_parallel(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        self.ok("map", "ticket", "Pick the dunning policy", "--type", "grilling")
        self.ok("map", "ticket", "Confirm refunds API", "--type", "research")
        self.ok("map", "claim", "T1")
        blocked = self.run_cli("map", "claim", "T2")
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("only research tickets run in parallel", blocked.stderr)
        self.ok("map", "claim", "T3")

    def test_a_ticket_claimed_elsewhere_is_refused(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        self.ok("map", "claim", "T1", "--by", "session-a")
        result = self.run_cli("map", "claim", "T1", "--by", "session-b")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already claimed by session-a", result.stderr)

    def test_resolving_an_unclaimed_ticket_is_refused(self):
        self.chart()
        self.ok("map", "ticket", "Confirm refunds API", "--type", "research")
        result = self.run_cli("map", "resolve", "T1", "--answer", "the v3 API covers it")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is unclaimed", result.stderr)


class TestResolutionEvidence(MapCase):
    def test_hitl_ticket_refuses_a_self_authored_resolution(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        self.ok("map", "claim", "T1")
        result = self.run_cli("map", "resolve", "T1", "--answer", "daily proration")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Human input required", result.stderr)
        self.assertEqual(self.ticket(self.map_json(), "T1")["status"], "open")

    def test_hitl_ticket_resolves_with_the_human_answer(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        self.ok("map", "claim", "T1")
        self.ok(
            "map",
            "resolve",
            "T1",
            "--answer",
            "Daily proration with a monthly true-up",
            "--gist",
            "daily proration",
            "--human-input",
            "the human chose daily with a monthly true-up",
        )
        payload = self.map_json()
        ticket = self.ticket(payload, "T1")
        self.assertEqual(ticket["status"], "closed")
        self.assertEqual(ticket["human_input"], "the human chose daily with a monthly true-up")
        self.assertEqual(ticket["claimed_by"], "")
        self.assertEqual(payload["decisions"][0]["gist"], "daily proration")

    def test_human_input_gate_has_an_explicit_legacy_opt_out(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        self.ok("map", "claim", "T1")
        self.ok(
            "map",
            "resolve",
            "T1",
            "--answer",
            "daily proration",
            env={"MYTHIFY_REQUIRE_HUMAN_INPUT": "0"},
        )
        self.assertEqual(self.ticket(self.map_json(), "T1")["status"], "closed")

    def test_afk_ticket_needs_no_human_input(self):
        self.chart()
        self.ok("map", "ticket", "Confirm refunds API", "--type", "research")
        self.ok("map", "claim", "T1")
        self.ok("map", "resolve", "T1", "--answer", "the v3 refunds API covers partials")
        self.assertEqual(self.ticket(self.map_json(), "T1")["status"], "closed")

    def test_answer_is_required(self):
        self.chart()
        self.ok("map", "ticket", "Confirm refunds API", "--type", "research")
        self.ok("map", "claim", "T1")
        result = self.run_cli("map", "resolve", "T1", "--answer", "   ")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Answer required", result.stderr)

    def test_task_ticket_with_a_verifier_needs_a_passing_executed_run(self):
        self.chart()
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--verify", "true")
        self.ok("map", "claim", "T1")
        refused = self.run_cli("map", "resolve", "T1", "--answer", "provisioned")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Verified evidence required", refused.stderr)
        self.ok("map", "verify", "T1")
        self.ok("map", "resolve", "T1", "--answer", "sandbox provisioned")
        self.assertEqual(self.ticket(self.map_json(), "T1")["status"], "closed")

    def test_a_failing_verifier_does_not_satisfy_the_gate(self):
        self.chart()
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--verify", "false")
        self.ok("map", "claim", "T1")
        run = self.run_cli("map", "verify", "T1")
        self.assertEqual(run.returncode, 2)
        refused = self.run_cli("map", "resolve", "T1", "--answer", "provisioned")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Verified evidence required", refused.stderr)

    def test_evidence_recorded_before_the_claim_cannot_be_reused(self):
        self.chart()
        self.ok("verify", "run", "true", "--claim", "unrelated earlier green run")
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--verify", "true")
        self.ok("map", "claim", "T1")
        refused = self.run_cli("map", "resolve", "T1", "--answer", "provisioned")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Verified evidence required", refused.stderr)

    def test_map_verify_scopes_the_record_to_the_ticket(self):
        self.chart()
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--verify", "true")
        self.ok("map", "claim", "T1")
        self.ok("map", "verify", "T1")
        records = [
            json.loads(line)
            for line in (self.project / ".mythify" / "verifications.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        self.assertEqual(records[-1]["map"], "billing")
        self.assertEqual(records[-1]["ticket_id"], "T1")
        self.assertEqual(records[-1]["ticket_type"], "task")

    def test_map_verify_requires_a_claim_first(self):
        self.chart()
        self.ok("map", "ticket", "Provision sandbox", "--type", "task", "--verify", "true")
        result = self.run_cli("map", "verify", "T1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is unclaimed", result.stderr)


class TestScopeAndFog(MapCase):
    def test_out_of_scope_ticket_leaves_the_decision_index_alone(self):
        self.chart()
        self.ok("map", "ticket", "Rewrite the invoice renderer", "--type", "grilling")
        self.ok(
            "map",
            "resolve",
            "T1",
            "--answer",
            "the renderer sits past this destination",
            "--out-of-scope",
        )
        payload = self.map_json()
        self.assertEqual(self.ticket(payload, "T1")["status"], "out_of_scope")
        self.assertEqual(payload["decisions"], [])
        self.assertEqual(payload["out_of_scope"][0]["ticket_id"], "T1")

    def test_resolution_can_surface_fresh_fog_and_scope_boundaries(self):
        self.chart()
        self.ok("map", "ticket", "Confirm refunds API", "--type", "research")
        self.ok("map", "claim", "T1")
        self.ok(
            "map",
            "resolve",
            "T1",
            "--answer",
            "the v3 refunds API covers partials",
            "--fog",
            "how do chargebacks interact with partials",
            "--scope-out",
            "rewriting the invoice renderer",
        )
        payload = self.map_json()
        notes = [item["note"] for item in payload["fog"]]
        self.assertIn("how do chargebacks interact with partials", notes)
        self.assertIn(
            "rewriting the invoice renderer",
            [item["note"] for item in payload["out_of_scope"]],
        )

    def test_scope_out_command_records_a_standalone_boundary(self):
        self.chart()
        self.ok(
            "map",
            "scope-out",
            "multi-currency pricing",
            "--reason",
            "the destination is a single-currency spec",
        )
        entry = self.map_json()["out_of_scope"][0]
        self.assertEqual(entry["note"], "multi-currency pricing")
        self.assertEqual(entry["reason"], "the destination is a single-currency spec")

    def test_new_fog_reopens_a_clear_map(self):
        self.chart()
        self.ok("map", "ticket", "Decide the cutover", "--type", "grilling", "--from-fog", "F1")
        self.ok("map", "claim", "T1")
        self.ok("map", "resolve", "T1", "--answer", "one window", "--human-input", "the human agreed")
        self.assertTrue(self.map_json()["clear"])
        self.ok("map", "fog", "how do we back out mid-cutover")
        payload = self.map_json()
        self.assertFalse(payload["clear"])
        self.assertEqual(payload["status"], "charting")


class TestPromote(MapCase):
    def clear_map(self):
        self.chart()
        self.ok("map", "ticket", "Decide the cutover", "--type", "grilling", "--from-fog", "F1")
        self.ok("map", "claim", "T1")
        self.ok(
            "map",
            "resolve",
            "T1",
            "--answer",
            "Cut over in one maintenance window",
            "--gist",
            "one maintenance window",
            "--human-input",
            "the human picked one window",
            "--scope-out",
            "rewriting the invoice renderer",
        )

    def test_promote_is_refused_while_anything_is_undecided(self):
        self.chart()
        self.ok("map", "ticket", "Decide the cutover", "--type", "grilling")
        result = self.run_cli("map", "promote")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not clear yet", result.stderr)
        self.assertIn("open ticket", result.stderr)

    def test_promote_is_refused_while_fog_remains(self):
        self.chart()
        result = self.run_cli("map", "promote")
        self.assertEqual(result.returncode, 1)
        self.assertIn("fog patch", result.stderr)

    def test_promote_carries_decisions_and_scope_into_the_plan(self):
        self.clear_map()
        self.ok("map", "promote", "--plan", "billing-build", "--horizon", "2")
        plan = json.loads(
            (self.project / ".mythify" / "plans" / "billing-build.json").read_text(encoding="utf-8")
        )
        self.assertEqual(plan["goal"], "Ship a billing revamp spec")
        self.assertEqual(plan["source"]["kind"], "map")
        self.assertEqual(plan["source"]["map"], "billing")
        self.assertEqual(plan["source"]["decisions"][0]["gist"], "one maintenance window")
        self.assertEqual(
            plan["source"]["out_of_scope"][0]["note"], "rewriting the invoice renderer"
        )
        self.assertEqual(len(plan["steps"]), 2)
        show = self.ok("plan", "show", "billing-build").stdout
        self.assertIn("Source: map billing", show)
        self.assertIn("Out of scope for this plan:", show)

    def test_promote_accepts_explicit_steps(self):
        self.clear_map()
        self.ok(
            "map",
            "promote",
            "--steps",
            json.dumps([{"title": "Write the spec", "verify_command": "true"}]),
        )
        plan = json.loads(
            (self.project / ".mythify" / "plans" / "billing.json").read_text(encoding="utf-8")
        )
        self.assertEqual(plan["steps"][0]["verify_command"], "true")

    def test_promote_clears_the_active_pointer_and_refuses_twice(self):
        self.clear_map()
        self.ok("map", "promote")
        self.assertFalse((self.project / ".mythify" / "maps" / "active").exists())
        again = self.run_cli("map", "promote", "billing")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already promoted", again.stderr)


class TestMapSurfaces(MapCase):
    def test_status_reports_the_active_map(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        output = self.ok("status").stdout
        self.assertIn("Active map: billing", output)
        self.assertIn("Destination: Ship a billing revamp spec", output)
        self.assertIn("Map next:", output)

    def test_show_refers_to_tickets_by_name(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        output = self.ok("map", "show").stdout
        self.assertIn("Pick the proration model (T1)", output)
        self.assertIn("Frontier (1):", output)
        self.assertIn("Not yet specified (1):", output)

    def test_list_marks_the_active_map(self):
        self.chart()
        output = self.ok("map", "list").stdout
        self.assertIn("* billing", output)

    def test_prompt_map_renders_the_wayfinding_rules(self):
        self.chart()
        self.ok("map", "ticket", "Pick the proration model", "--type", "grilling")
        payload = json.loads(self.ok("prompt", "map", "--json").stdout)
        self.assertEqual(payload["kind"], "map")
        self.assertEqual(payload["source"], {"type": "map", "id": "billing"})
        prompt = payload["next_prompt"]
        self.assertIn("Plan, do not do", prompt)
        self.assertIn("Pick the proration model (T1)", prompt)
        self.assertIn("--human-input", prompt)
        self.assertIn("Stop after one decision ticket", prompt)

    def test_prompt_next_selects_the_map_while_one_is_active(self):
        self.chart()
        payload = json.loads(self.ok("prompt", "next", "--json").stdout)
        self.assertEqual(payload["selected_kind"], "map")

    def test_prompt_map_without_a_map_fails_cleanly(self):
        result = self.run_cli("prompt", "map")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Map not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
