"""Findings reviews with bounded rounds, through the real CLI."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subprocess_env  # noqa: E402,F401  (faster subprocesses, same behavior)
import warm_cli  # noqa: E402  (runs the CLI forked from a warm interpreter)

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"


class FindingsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.project = base / "project"
        self.project.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        self.state = self.project / ".mythify"
        self.git("init", "-q")
        self.git("config", "user.email", "mythify@example.invalid")
        self.git("config", "user.name", "Mythify Test")
        (self.project / "app.py").write_text("print('v1')\n", encoding="utf-8")
        self.ok("init")
        self.git("add", "-A")
        self.git("commit", "-qm", "initial")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=str(self.project), check=True, capture_output=True, text=True
        ).stdout.strip()

    def run_cli(self, *args, env_extra=None):
        env = dict(os.environ)
        for name in ("MYTHIFY_DIR", "MYTHIFY_REQUIRE_VERIFIED_STEP", "MYTHIFY_DISABLE_RUN",
                     "MYTHIFY_RISK_GATE"):
            env.pop(name, None)
        env["HOME"] = str(self.home)
        env.update(env_extra or {})
        return warm_cli.run(list(args), cwd=str(self.project), env=env, timeout=120)

    def ok(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def refused(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        return result

    def review(self, name="rv"):
        path = self.state / "reviews" / (name + ".json")
        return json.loads(path.read_text(encoding="utf-8"))

    def fixed_round(self, summary, name="rv"):
        """Open a round, record one finding, and fix it."""
        self.ok("review", "round", name)
        found = json.loads(self.ok("review", "finding", name, summary, "--json").stdout)
        self.ok("review", "resolve", name, found["id"], "--status", "fixed")
        return found["id"]


class RoundTests(FindingsCase):
    def test_first_round_creates_the_review_and_stamps_the_source(self):
        result = self.ok("review", "round", "rv", "--note", "first pass")
        self.assertIn("round 1 opened", result.stdout)
        self.assertIn("first round", result.stdout)
        record = self.review()
        self.assertEqual(record["kind"], "findings_review")
        self.assertEqual(record["review_type"], "findings")
        self.assertEqual(record["evidence_status"], "material_not_verification")
        opened = record["rounds"][0]
        self.assertEqual(opened["number"], 1)
        self.assertEqual(opened["note"], "first pass")
        self.assertFalse(opened["clean"])
        self.assertNotIn("closed_at", opened)
        self.assertEqual(opened["provenance"]["git_commit"], self.git("rev-parse", "HEAD"))
        self.assertEqual(len(opened["provenance"]["worktree_digest"]), 64)
        self.assertTrue(opened["opened_at"])

    def test_new_round_is_refused_while_a_finding_is_open(self):
        self.ok("review", "round", "rv")
        self.ok("review", "finding", "rv", "Null deref")
        refused = self.refused("review", "round", "rv")
        self.assertIn("still open (F1)", refused.stderr)
        self.assertEqual(len(self.review()["rounds"]), 1)
        self.ok("review", "resolve", "rv", "F1", "--status", "fixed")
        self.ok("review", "round", "rv")
        self.assertEqual(len(self.review()["rounds"]), 2)

    def test_round_four_needs_a_rethink(self):
        for index in range(3):
            self.fixed_round("finding number {0}".format(index))
        refused = self.refused("review", "round", "rv")
        self.assertIn(
            "[FAIL] Round 4 needs --rethink: three review rounds did not converge. "
            "Reconsider the approach, then say why it stands.", refused.stderr)
        self.assertEqual(len(self.review()["rounds"]), 3)
        blank = self.refused("review", "round", "rv", "--rethink", "   ")
        self.assertIn("--rethink needs text", blank.stderr)
        self.ok("review", "round", "rv", "--rethink", "The design is sound; the findings were style.")
        fourth = self.review()["rounds"][3]
        self.assertEqual(fourth["number"], 4)
        self.assertEqual(fourth["rethink"], "The design is sound; the findings were style.")

    def test_clean_round_is_closed_and_takes_no_findings(self):
        result = self.ok("review", "round", "rv", "--clean")
        self.assertIn("clean at the current source", result.stdout)
        stored = self.review()["rounds"][0]
        self.assertTrue(stored["clean"])
        self.assertTrue(stored["closed_at"])
        refused = self.refused("review", "finding", "rv", "Too late")
        self.assertIn("Round 1 is clean", refused.stderr)

    def test_finding_without_a_round_is_refused(self):
        missing = self.refused("review", "finding", "nope", "Anything")
        self.assertIn("Review not found: nope", missing.stderr)

    def test_round_scope_lists_files_changed_since_the_previous_round(self):
        self.ok("review", "round", "rv")
        (self.project / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (self.project / "extra.py").write_text("x = 1\n", encoding="utf-8")
        result = self.ok("review", "round", "rv")
        self.assertIn("Re-review scope (changed since round 1", result.stdout)
        self.assertIn("  app.py", result.stdout)
        self.assertIn("  extra.py", result.stdout)
        payload = json.loads(self.ok("review", "round", "rv", "--json").stdout)
        self.assertEqual(payload["round"], 3)
        self.assertEqual(payload["scope"]["since_round"], 2)
        self.assertEqual(payload["scope"]["files"], ["app.py", "extra.py"])

    def test_round_lists_dismissed_findings_not_to_raise_again(self):
        self.ok("review", "round", "rv")
        self.ok("review", "finding", "rv", "Magic number", "--path", "app.py")
        self.ok("review", "resolve", "rv", "F1", "--status", "dismissed", "--reason", "it is a constant")
        result = self.ok("review", "round", "rv")
        self.assertIn("Dismissed findings (do not raise them again):", result.stdout)
        self.assertIn("F1: Magic number (app.py): it is a constant", result.stdout)
        payload = json.loads(self.ok("review", "round", "rv", "--json").stdout)
        self.assertEqual(payload["dismissed"][0]["id"], "F1")

    def test_name_clash_with_a_blast_radius_review_is_refused(self):
        self.ok("review", "blast-radius", "--status", "pass", "--path", "app.py",
                "--safety-fact", "safe", "--name", "taken")
        refused = self.refused("review", "round", "taken")
        self.assertIn("not a findings review", refused.stderr)


class FindingTests(FindingsCase):
    def test_findings_number_across_rounds(self):
        self.ok("review", "round", "rv")
        first = json.loads(self.ok("review", "finding", "rv", "One", "--severity", "high", "--json").stdout)
        self.assertEqual((first["id"], first["severity"], first["status"]), ("F1", "high", "open"))
        self.ok("review", "resolve", "rv", "F1", "--status", "fixed")
        self.ok("review", "round", "rv")
        second = json.loads(self.ok("review", "finding", "rv", "Two", "--path", "app.py", "--json").stdout)
        self.assertEqual((second["id"], second["severity"], second["path"]), ("F2", "medium", "app.py"))
        stored = self.review()
        self.assertEqual([len(r["findings"]) for r in stored["rounds"]], [1, 1])

    def test_repeat_of_a_dismissed_finding_is_refused(self):
        self.ok("review", "round", "rv")
        self.ok("review", "finding", "rv", "Missing guard clause", "--path", "app.py")
        self.ok("review", "resolve", "rv", "F1", "--status", "dismissed", "--reason", "callers validate")
        self.ok("review", "round", "rv")
        refused = self.refused("review", "finding", "rv", "  missing GUARD clause!! ", "--path", "./app.py")
        self.assertIn("Repeat of dismissed finding F1", refused.stderr)
        self.assertIn("callers validate", refused.stderr)
        self.assertEqual(self.review()["rounds"][1]["findings"], [])
        different_path = self.ok("review", "finding", "rv", "Missing guard clause", "--path", "other.py")
        self.assertIn("F2", different_path.stdout)

    def test_repeat_of_a_fixed_finding_is_recorded_with_a_warning(self):
        self.fixed_round("Off by one in loop")
        self.ok("review", "round", "rv")
        result = self.ok("review", "finding", "rv", "off-by-one in loop")
        self.assertIn("[WARN] Finding F2 repeats F1", result.stderr)
        finding = self.review()["rounds"][1]["findings"][0]
        self.assertEqual(finding["repeat_of"], "F1")
        self.assertEqual(finding["status"], "open")

    def test_duplicate_of_an_open_finding_is_refused(self):
        self.ok("review", "round", "rv")
        self.ok("review", "finding", "rv", "Race on write")
        refused = self.refused("review", "finding", "rv", "race on write")
        self.assertIn("F1 already records this", refused.stderr)

    def test_resolve_rules(self):
        self.ok("review", "round", "rv")
        self.ok("review", "finding", "rv", "A problem")
        no_reason = self.refused("review", "resolve", "rv", "F1", "--status", "dismissed")
        self.assertIn("needs --reason", no_reason.stderr)
        self.assertEqual(self.review()["rounds"][0]["findings"][0]["status"], "open")
        missing = self.refused("review", "resolve", "rv", "F9", "--status", "fixed")
        self.assertIn("Finding not found", missing.stderr)
        self.ok("review", "resolve", "rv", "f1", "--status", "dismissed", "--reason", "not real")
        stored = self.review()["rounds"][0]["findings"][0]
        self.assertEqual((stored["status"], stored["reason"]), ("dismissed", "not real"))
        self.assertTrue(stored["resolved_at"])
        again = self.refused("review", "resolve", "rv", "F1", "--status", "fixed")
        self.assertIn("already dismissed", again.stderr)


class ShowAndCoverageTests(FindingsCase):
    def test_show_covers_current_source_then_goes_stale(self):
        self.fixed_round("Leaky handle")
        self.ok("review", "round", "rv", "--clean")
        shown = self.ok("review", "show", "rv").stdout
        self.assertIn("Findings review: rv (2 rounds)", shown)
        self.assertIn("Round 1 (commit", shown)
        self.assertIn("findings resolved", shown)
        self.assertIn("F1 [medium] Leaky handle - fixed", shown)
        self.assertIn("Round 2 (commit", shown)
        self.assertIn("Coverage: covers current source", shown)
        as_json = json.loads(self.ok("review", "show", "rv", "--json").stdout)
        self.assertTrue(as_json["coverage"]["covers_current_source"])

        (self.project / "app.py").write_text("print('v2')\n", encoding="utf-8")
        dirty = self.ok("review", "show", "rv").stdout
        self.assertIn("Coverage: uncommitted changes since round 2", dirty)
        self.assertNotIn("covers current source", dirty)

        self.git("add", "-A")
        self.git("commit", "-qm", "change")
        committed = self.ok("review", "show", "rv").stdout
        self.assertIn("Coverage: 1 commit since round 2", committed)
        (self.project / "app.py").write_text("print('v3')\n", encoding="utf-8")
        both = self.ok("review", "show", "rv").stdout
        self.assertIn("Coverage: 1 commit and uncommitted changes since round 2", both)
        self.assertFalse(json.loads(self.ok("review", "show", "rv", "--json").stdout)
                         ["coverage"]["covers_current_source"])

    def test_open_latest_round_does_not_cover(self):
        self.ok("review", "round", "rv")
        self.assertIn("round 1 is still open", self.ok("review", "show", "rv").stdout)

    def test_review_prove_refuses_a_findings_review(self):
        self.ok("review", "round", "rv", "--clean")
        refused = self.refused("review", "prove", "rv", "--command", "test -f app.py")
        self.assertIn("not a blast-radius safety case", refused.stderr)

    def test_blast_radius_review_show_is_unchanged(self):
        self.ok("review", "blast-radius", "--status", "pass", "--path", "app.py",
                "--safety-fact", "the payload stays parseable", "--name", "blast")
        shown = self.ok("review", "show", "blast").stdout
        self.assertIn("[OK] Blast-radius review: blast (pass)", shown)
        self.assertIn("Safety fact: the payload stays parseable (depth 1, unproven)", shown)
        self.assertNotIn("Findings review", shown)


class AttentionAndPromptTests(FindingsCase):
    def attention(self):
        return json.loads(self.ok("status", "--json").stdout)["attention"]

    def test_open_findings_are_an_issue(self):
        self.ok("review", "round", "rv")
        self.ok("review", "finding", "rv", "One")
        self.ok("review", "finding", "rv", "Two")
        items = [i for i in self.attention() if i["source"] == "review"]
        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["level"], "issue")
        self.assertIn("2 open findings", items[0]["summary"])
        self.assertIn("F1, F2", items[0]["detail"])
        self.assertIn("findings review rv has 2 open findings", self.ok("status").stdout)
        self.ok("review", "resolve", "rv", "F1", "--status", "fixed")
        self.ok("review", "resolve", "rv", "F2", "--status", "fixed")
        self.assertEqual([i for i in self.attention() if i["source"] == "review"], [])

    def test_round_three_without_a_clean_pass_asks_for_a_rethink(self):
        self.fixed_round("a")
        self.fixed_round("b")
        self.ok("review", "round", "rv")
        items = [i for i in self.attention() if i["source"] == "review"]
        self.assertEqual([i["level"] for i in items], ["warning"])
        self.assertIn("round 3 without a clean pass", items[0]["summary"])
        self.assertIn("rethink", items[0]["detail"])

    def test_clean_round_that_no_longer_covers_the_source_is_a_warning(self):
        self.ok("review", "round", "rv", "--clean")
        self.assertEqual([i for i in self.attention() if i["source"] == "review"], [])
        (self.project / "app.py").write_text("print('v2')\n", encoding="utf-8")
        items = [i for i in self.attention() if i["source"] == "review"]
        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["level"], "warning")
        self.assertIn("no longer covers the current source", items[0]["summary"])
        self.assertIn("since round 1", items[0]["detail"])

    def test_only_the_newest_review_warns_that_it_no_longer_covers_the_source(self):
        self.ok("review", "round", "old", "--clean")
        self.ok("review", "round", "new", "--clean")
        path = self.state / "reviews" / "old.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["updated"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(record), encoding="utf-8")
        (self.project / "app.py").write_text("print('v2')\n", encoding="utf-8")
        items = [i for i in self.attention() if i["source"] == "review"]
        self.assertEqual(len(items), 1, items)
        self.assertIn("findings review new no longer covers", items[0]["summary"])
        # Open findings and the round-3 warning still apply to an older review.
        self.ok("review", "round", "old")
        self.ok("review", "finding", "old", "Still open")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["updated"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(record), encoding="utf-8")
        summaries = [i["summary"] for i in self.attention() if i["source"] == "review"]
        self.assertTrue(any("findings review old has 1 open finding" in s for s in summaries), summaries)
        self.assertFalse(any("old no longer covers" in s for s in summaries), summaries)

    def test_review_prompt_packet_carries_round_scope_and_dismissed_findings(self):
        self.ok("review", "round", "rv")
        self.ok("review", "finding", "rv", "Unused branch", "--path", "app.py")
        self.ok("review", "resolve", "rv", "F1", "--status", "dismissed", "--reason", "kept for symmetry")
        (self.project / "app.py").write_text("print('v2')\n", encoding="utf-8")
        packet = json.loads(self.ok("prompt", "review", "--json").stdout)
        text = packet["next_prompt"]
        self.assertIn("Findings review: rv, round 1", text)
        self.assertIn("Re-review scope (changed since round 1", text)
        self.assertIn("  app.py", text)
        self.assertIn("F1: Unused branch (app.py): kept for symmetry", text)
        self.assertEqual(packet["context"]["findings_review"]["name"], "rv")

    def test_review_prompt_packet_skips_a_review_that_covers_the_source(self):
        self.ok("review", "round", "rv", "--clean")
        packet = json.loads(self.ok("prompt", "review", "--json").stdout)
        self.assertNotIn("Findings review:", packet["next_prompt"])
        self.assertIsNone(packet["context"]["findings_review"])


if __name__ == "__main__":
    unittest.main()
