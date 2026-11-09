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


# Production regression corpus: every prompt from the review rounds, the policy
# owner's recall change, and earlier tests, with its expected policy step. Step 1
# prefers recall (policy owner: step 1 prefers recall; ambiguous goes to step 1):
# a clause whose operative verb is an operation verb is production with or
# without an environment marker. Only clear code work on a component stays out:
# a coding verb whose object is a code noun that the operation word modifies.
# "production" means classify_policy_step names the production step; None means
# it names no step, so route falls through to build.
PRODUCTION_CORPUS = (
    # operation verbs alone
    ("deploy the app", "production"),
    ("deploy keyvra", "production"),
    ("ship it", "production"),
    ("release v6.3.0", "production"),
    ("publish the package to npm", "production"),
    ("roll back the last release", "production"),
    ("restart the inhouse service", "production"),
    ("stop the service and start the service", "production"),
    ("go live with the new site", "production"),
    # migrations against an environment
    ("run the production migration", "production"),
    ("apply the migration to the live database", "production"),
    ("run the migration in production", "production"),
    ("run the migration on production", "production"),
    # remote access to any host, named or not
    ("ssh into hxp-linux and check the logs", "production"),
    ("ssh into the server and check the logs", "production"),
    ("ssh into the prod box and restart nginx", "production"),
    ("push main to origin on the keyvra server", "production"),
    ("scp the build to the staging box", "production"),
    ("rsync the build to the server", "production"),
    ("ssh to prod and tail the logs", "production"),
    ("log into the server and check the disk", "production"),
    # with an environment marker
    ("deploy the app to the staging server", "production"),
    ("Deploy my app to staging", "production"),
    ("Deploy our service to staging", "production"),
    ("Deploy the latest production build to staging", "production"),
    ("deploy to staging", "production"),
    ("deploy the payments service to staging", "production"),
    ("deploy the api to production", "production"),
    ("Fix the bug and deploy the api to production", "production"),
    ("release to production on Friday", "production"),
    ("push my latest changes to production", "production"),
    ("restart the production server", "production"),
    ("roll back production", "production"),
    ("ship my app to staging", "production"),
    # review round 5: documentation words cancel only with a coding verb
    ("publish the documentation", "production"),
    ("deploy docs to production", "production"),
    # ssh, scp, and rsync are operations whatever follows
    ("ssh hxp-linux and check the logs", "production"),
    ("scp build.tar.gz hxp-linux:/srv/app", "production"),
    # service control, and "and" before an operation verb starts a new clause
    ("stop the nginx service", "production"),
    ("start the inhouse service", "production"),
    ("fix the bug and deploy api to production", "production"),
    # migrations with optional determiners
    ("apply a migration to the live database", "production"),
    ("run a migration against prod", "production"),
    # money
    ("buy a domain", "production"),
    ("upgrade the plan to pro", "production"),
    ("buy more credits for the account", "production"),
    ("spend money on ads", "production"),
    ("upgrade our plan to the larger tier", "production"),
    # clauses: an earlier coding clause does not cancel a later operation
    ("Fix the failing test, then run the deploy script on the production server", "production"),
    ("Fix the bug, then ssh into the server and test it", "production"),
    ("Fix the test, then spend money on the monitoring tool", "production"),
    ("fix the failing test and then deploy the app to the server", "production"),
    ("Fix the bug; after that deploy the app to the server", "production"),
    ("Fix the parser before you ssh into the staging server", "production"),
    ("fix the bug, then rsync the build to the server, then write the wrapper", "production"),
    # policy owner: step 1 prefers recall; ambiguous goes to step 1
    ("then deploy", "production"),
    ("please deploy", "production"),
    ("Fix the bug and deploy", "production"),
    ("I want to deploy", "production"),
    ("run the deploy script", "production"),
    ("Fix the deploy", "production"),
    ("write a deploy", "production"),
    ("Rename the function to deploy", "production"),
    ("Implement a button to deploy", "production"),
    ("Fix deploy errors in app.py", "production"),
    ("Add SSH to the API", "production"),
    ("explain the deploy process and the rollback", "production"),
    ("Deploy command parser needs a fix", "production"),
    ("add a deploy to production button to the admin page", "production"),
    # clear code work: a coding verb, and a code noun the operation word modifies
    ("Implement an SSH client with mocked connection tests", None),
    ("implement an ssh client", None),
    ("Fix the deploy command argument parser", None),
    ("write a deploy script", None),
    ("add a --deploy flag", None),
    ("test the ssh connection helper", None),
    ("fix the release notes generator", None),
    ("Fix the rsync, scp, and ssh wrapper", None),
    ("Implement an scp tool", None),
    ("document the deploy process", None),
    ("fix the deploy docs typo", None),
    ("update the release notes for production", None),
    ("refactor the rsync wrapper", None),
    ("debug the scp upload helper", None),
    ("write tests for the deploy handler", None),
    ("fix the deploy config", None),
    ("add an option to the ssh library", None),
    ("write a production deploy script", None),
    ("fix the server restart handler", None),
    ("refactor the scp client", None),
    # not an operation at all
    ("add payment processing to checkout", None),
    ("push the branch to origin", None),
    ("write payment code for the checkout service", None),
)


class TestProductionRecallFirst(unittest.TestCase):
    """Step 1 prefers recall: an operation verb, remote access, or money is production."""

    def step(self, task):
        hit = classify_policy_step(task)
        return hit["id"] if hit else None

    def test_corpus(self):
        for task, expected in PRODUCTION_CORPUS:
            with self.subTest(task=task):
                self.assertEqual(self.step(task), expected)

    def test_a_coding_verb_in_the_same_clause_cancels_only_with_a_code_noun(self):
        # No coding verb: the api is the thing being deployed.
        self.assertEqual(self.step("deploy the api to production"), "production")
        # A coding verb and a code noun right after the operation word: code work.
        self.assertIsNone(self.step("write a production deploy script"))
        self.assertIsNone(self.step("fix the server restart handler"))
        # The coding verb is in an earlier clause, so it does not cancel.
        self.assertEqual(self.step("write the handler, then restart the production server"), "production")

    def test_environment_words_work_in_either_order_within_six_words(self):
        self.assertEqual(self.step("on production push it now"), "production")
        self.assertEqual(self.step("push the build of the app to prod"), "production")
        self.assertIsNone(self.step("push the build of the whole app to prod"))
        # the clause break between them keeps them apart
        self.assertIsNone(self.step("tidy up on production; push it now"))

    def test_ssh_scp_and_rsync_are_operations_whatever_follows(self):
        for task in (
            "ssh onto the host", "ssh to anything", "ssh into", "scp the build",
            "ssh hxp-linux and check the logs", "scp build.tar.gz hxp-linux:/srv/app",
            "rsync -av dist/ deploy@box:/srv/app", "ssh user@10.0.0.5 uptime",
        ):
            with self.subTest(task=task):
                self.assertEqual(self.step(task), "production")
        # only the code-work cancel keeps them build
        for task in ("implement an ssh client", "Fix the rsync, scp, and ssh wrapper"):
            with self.subTest(task=task):
                self.assertIsNone(self.step(task))

    def test_a_near_miss_word_cancels_only_with_a_coding_verb(self):
        self.assertIsNone(self.step("fix the deploy docs typo"))
        self.assertIsNone(self.step("write the deploy docs"))
        self.assertEqual(self.step("publish the documentation"), "production")
        self.assertEqual(self.step("deploy docs to production"), "production")
        self.assertEqual(self.step("release notes for v6.3.0"), "production")

    def test_and_before_an_operation_verb_starts_a_new_clause(self):
        from mythify_classification import CLAUSE_BREAK, clause_tokens
        self.assertEqual(
            clause_tokens("fix the bug and deploy api to production"),
            ["fix", "the", "bug", CLAUSE_BREAK, "deploy", "api", "to", "production"],
        )
        # a list is not split: ssh, scp, and rsync are not and_starters
        self.assertEqual(
            clause_tokens("fix the ssh and scp wrapper"), ["fix", "the", "ssh", "and", "scp", "wrapper"]
        )
        self.assertEqual(self.step("fix the bug and deploy api to production"), "production")

    def test_service_control_verbs_within_three_words_of_a_service_noun(self):
        for task in (
            "stop the nginx service", "start the inhouse service", "reload the worker pool daemon",
            "kill the stuck container", "restart the staging cluster", "stop all three api workers",
        ):
            with self.subTest(task=task):
                self.assertEqual(self.step(task), "production")
        for task in ("start the project", "stop the presses", "write a restart handler for the worker"):
            with self.subTest(task=task):
                self.assertIsNone(self.step(task))

    def test_migrations_with_optional_determiners_and_an_environment(self):
        for task in (
            "apply a migration to the live database", "run a migration against prod",
            "execute the pending migrations on production", "run all the pending migrations on production",
            "apply any new migrations to the live database", "migrate prod", "migrate production",
        ):
            with self.subTest(task=task):
                self.assertEqual(self.step(task), "production")
        for task in ("run the migration locally", "write a migration to add a column", "apply the migration"):
            with self.subTest(task=task):
                self.assertIsNone(self.step(task))

    def test_high_tier_terms_match_only_whole_words(self):
        from mythify_classification import high_tier_term
        for task, term in (
            ("fix the cache race", "race"), ("remove the lock", "lock"),
            ("end the session early", "session"), ("check auth", "auth"),
        ):
            with self.subTest(task=task):
                self.assertEqual(high_tier_term(task), term)
        for task in (
            "trace the call", "embrace change", "grace under pressure",
            "block the user", "check the clock", "an obsession with details",
            "the author page", "authority matters", "many obsessions",
        ):
            with self.subTest(task=task):
                self.assertIsNone(high_tier_term(task))

    def test_the_operation_word_must_modify_the_code_noun_to_be_cancelled(self):
        # "script" follows the operation word directly: a modifier.
        self.assertIsNone(self.step("write a deploy script"))
        # a stop word between them makes the operation the verb of its own object.
        self.assertEqual(self.step("write a script to deploy"), "production")
        self.assertEqual(self.step("write the script and deploy the api"), "production")
        # no coding verb earlier in the clause: nothing to cancel.
        self.assertEqual(self.step("the deploy script is broken"), "production")

    def test_clauses_split_at_punctuation_and_connectives(self):
        from mythify_classification import CLAUSE_BREAK, clause_tokens
        self.assertEqual(
            clause_tokens("fix it, then run it; after that deploy before lunch. Done"),
            ["fix", "it", CLAUSE_BREAK, "run", "it", CLAUSE_BREAK, "deploy", CLAUSE_BREAK,
             "lunch", CLAUSE_BREAK, "done"],
        )

    def test_a_comma_splits_only_before_a_new_verb_phrase(self):
        from mythify_classification import CLAUSE_BREAK, clause_tokens
        self.assertEqual(
            clause_tokens("fix the rsync, scp, and ssh wrapper"),
            ["fix", "the", "rsync", "scp", "and", "ssh", "wrapper"],
        )
        self.assertEqual(
            clause_tokens("write tests, fix bugs, and please deploy"),
            ["write", "tests", CLAUSE_BREAK, "fix", "bugs", "and", "please", "deploy"],
        )
        self.assertEqual(clause_tokens("see app.py, the deploy file")[:3], ["see", "app", "py"])


if __name__ == "__main__":
    unittest.main()
