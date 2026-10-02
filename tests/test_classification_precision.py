"""Classification precision: short prompts and incidental risk words.

Two v6 fixes keep the deterministic classifier from misreading wording. A
prompt of twelve words or fewer is trivial only when it matches no task, risk,
or route-selecting term. A destructive verb is high risk unless the words
after it name a code-local object (an import, a comment, a typo) and the
prompt names no destructive object anywhere, and nouns such as "token" or "JSON schema" no longer make a low-risk edit a
security or migration task, while genuinely risky prompts stay high.
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
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mythify_classification import classify_task_text  # noqa: E402
from mythify_router import ROUTE_SELECTING_TERMS  # noqa: E402


def classify(task):
    return classify_task_text(task, route_terms=ROUTE_SELECTING_TERMS)


class TestTrivialNeedsNoSignals(unittest.TestCase):
    def test_short_prompts_with_task_route_or_risk_terms_are_not_trivial(self):
        for task in (
            "write a product roadmap and PRD for onboarding",
            "delete production data",
            "wayfind the onboarding revamp",
            "map the work for onboarding",
            "look up the wire format",
            "find issues in the parser",
            "rotate credentials",
            "drop the users table",
        ):
            with self.subTest(task=task):
                self.assertLessEqual(len(task.split()), 12)
                self.assertNotEqual(classify(task)["task_type"], "trivial")

    def test_short_prompts_without_signals_stay_trivial(self):
        for task in (
            "update the footer text",
            "remove an unused import in utils.py",
            "show token count in the report footer",
            "bump the copyright year",
        ):
            with self.subTest(task=task):
                payload = classify(task)
                self.assertEqual(payload["task_type"], "trivial")
                self.assertEqual(payload["risk"], "low")
                self.assertEqual(payload["execution_profile"], "direct")

    def test_route_terms_are_optional_for_direct_callers(self):
        self.assertEqual(classify_task_text("map the work for onboarding")["task_type"], "trivial")
        self.assertEqual(classify("map the work for onboarding")["task_type"], "feature")


class TestRiskTermsAreTight(unittest.TestCase):
    def test_incidental_nouns_are_not_high_risk(self):
        for task in (
            "remove an unused import in utils.py",
            "show token count in the report footer",
            "drop the trailing comma in the config parser",
            "delete a stale comment in the router",
            "fix a typo in the JSON schema description",
            "remove the else branch in parse",
            "remove dead code in the router",
            "drop the debug print in main.py",
            "remove unused imports from the file",
            "remove all unused imports",
        ):
            with self.subTest(task=task):
                payload = classify(task)
                self.assertNotEqual(payload["risk"], "high")
                self.assertNotEqual(payload["task_type"], "security")
                self.assertNotEqual(payload["task_type"], "migration")

    def test_genuinely_risky_prompts_stay_high(self):
        for task, task_type in (
            ("delete production data", None),
            ("rotate credentials", "security"),
            ("rotate the production credential", "security"),
            ("migrate the database", "migration"),
            ("drop the users table", None),
            ("wipe the customer records", None),
            ("purge old backups from the bucket", None),
            ("delete it from production", None),
            ("change the database schema for orders", "migration"),
            ("leak an access token in the logs", "security"),
            ("force push over main", None),
            ("delete the user account", None),
            ("delete everything in the home folder", None),
            ("remove the password check", "security"),
            ("store the JWT token in localStorage", "security"),
            ("delete all files in the uploads directory", None),
            ("delete customer emails", None),
            ("remove all user sessions", None),
            ("drop the orders collection", None),
            ("delete the branch main", None),
            ("update the schema for the orders table", "migration"),
            ("leak of the session token", "security"),
            ("remove the unused import and delete the user account", None),
            ("delete unused user accounts", None),
            ("delete the comment column from the users table", None),
            ("remove all comments posted by spam users", None),
            ("remove the imports from the s3 bucket", None),
            ("drop the comment column", None),
        ):
            with self.subTest(task=task):
                payload = classify(task)
                self.assertEqual(payload["risk"], "high")
                if task_type:
                    self.assertEqual(payload["task_type"], task_type)

    def test_destructive_prompts_route_to_a_plan_not_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.pop("MYTHIFY_DIR", None)
            env["HOME"] = tmp
            for task, route in (
                ("delete the user account", "plan"),
                ("delete the comment column from the users table", "plan"),
                ("remove an unused import in utils.py", "direct"),
            ):
                with self.subTest(task=task):
                    result = subprocess.run(
                        [sys.executable, str(CLI), "route", task, "--json"],
                        cwd=tmp,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["route"], route)

    def test_route_classification_uses_the_route_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.pop("MYTHIFY_DIR", None)
            env["HOME"] = tmp
            result = subprocess.run(
                [sys.executable, str(CLI), "route", "look up the wire format", "--json"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(json.loads(result.stdout)["classification"]["task_type"], "trivial")


class TestPhraseTieBreak(unittest.TestCase):
    def test_longer_phrase_wins_a_tie(self):
        self.assertEqual(classify("write a product plan for onboarding")["task_type"], "product")
        self.assertEqual(classify("what should we build next quarter")["task_type"], "product")

    def test_product_terms_win_ties_against_generic_verbs(self):
        self.assertEqual(classify("build a roadmap for next quarter")["task_type"], "product")
        self.assertEqual(classify("create a roadmap for the app")["task_type"], "product")
        self.assertEqual(classify("fix the absolute positioning of the tooltip")["task_type"], "bugfix")

    def test_ties_without_a_longer_phrase_stay_alphabetical(self):
        self.assertEqual(classify("fix the absolute positioning of the tooltip")["task_type"], "bugfix")
        self.assertEqual(classify("adjust css positioning of the modal")["task_type"], "frontend_ui")


if __name__ == "__main__":
    unittest.main()
