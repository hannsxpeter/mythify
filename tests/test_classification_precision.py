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

from mythify_classification import classify_policy_step, classify_task_text  # noqa: E402
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


class TestProductionNeedsOperationalWording(unittest.TestCase):
    """A command name in code work is not an operation: production stays rare."""

    def step(self, task):
        hit = classify_policy_step(task)
        return hit["id"] if hit else None

    def test_code_work_named_after_an_operation_is_not_production(self):
        for task in (
            "Implement an SSH client with mocked connection tests",
            "Fix the deploy command argument parser",
            "write a deploy script",
            "add a --deploy flag",
            "test the ssh connection helper",
            "fix the release notes generator",
            "refactor the rsync wrapper",
            "debug the scp upload helper",
            "add a deploy to production button to the admin page",
            "write tests for the deploy handler",
            "fix the deploy config",
            "add an option to the ssh library",
            "Fix the deploy",
            "explain the deploy process and the rollback",
            "Deploy command parser needs a fix",
            "push the branch to origin",
            "write a deploy",
        ):
            with self.subTest(task=task):
                self.assertIsNone(self.step(task))

    def test_operational_wording_stays_production(self):
        for task in (
            "deploy the app to the staging server",
            "ssh into the prod box and restart nginx",
            "deploy to staging",
            "run the deploy script",
            "release to production on Friday",
            "run the migration on production",
            "restart the production server",
            "roll back production",
            "buy more credits for the account",
            "rsync the build to the server",
            "fix the failing test and then deploy the app to the server",
            # determiners, possessives, and up to two words before the object
            "Deploy my app to staging",
            "Deploy our service to staging",
            "deploy the payments service to staging",
            "ship my app to staging",
            "push my latest changes to production",
            # an earlier coding clause does not cancel a separate operation
            "Fix the failing test, then run the deploy script",
            "Fix the bug, then ssh into the server and test it",
            "Fix the test, then spend money on the monitoring tool",
            "Fix the bug; after that deploy the app to the server",
            "Fix the parser before you ssh into the staging server",
            # a bare operation word is the verb of its clause
            "rename all the functions across the repo then deploy",
            "Fix the bug and deploy",
            "please deploy",
            "I want to deploy",
        ):
            with self.subTest(task=task):
                self.assertEqual(self.step(task), "production")

    def test_the_code_noun_must_attach_to_the_operation_word(self):
        # The same words, but the coding verb and the noun sit with the operation.
        for task in (
            "implement an ssh into helper",
            "fix the rsync wrapper",
            "refactor the scp client",
        ):
            with self.subTest(task=task):
                self.assertIsNone(self.step(task))
        # A noun after the operation, in another clause, does not cancel it.
        self.assertEqual(self.step("fix the bug, then rsync the build, then write the wrapper"), "production")

    def test_clauses_split_at_punctuation_and_connectives(self):
        from mythify_classification import CLAUSE_BREAK, clause_tokens
        self.assertEqual(
            clause_tokens("fix it, then run it; after that deploy before lunch. Done"),
            ["fix", "it", CLAUSE_BREAK, "run", "it", CLAUSE_BREAK, "deploy", CLAUSE_BREAK,
             "lunch", CLAUSE_BREAK, "done"],
        )


if __name__ == "__main__":
    unittest.main()
