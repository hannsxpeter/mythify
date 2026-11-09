"""`mythify route` reports the user's agent routing policy as advisory text.

Every fixture policy uses invented tool and model names: Mythify names no
model or vendor, and the lint rejects real ones in the repository. The tests
cover the lookup order (MYTHIFY_AGENT_POLICY, then HOME), the byte-for-byte
unchanged output without a policy, the warning and fallback for a bad file, the
step each kind of task falls under, the risk-to-tier mapping, the tricky effort,
the banned-model and effort-ceiling guards, and generic extra tier keys.
"""

import ast
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "mythify.py"
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import mythify_classification  # noqa: E402
from mythify_classification import classify_policy_step  # noqa: E402

POLICY = {
    "version": 1,
    "description": "ignored",
    "source": "ignored",
    "quarterback": [{"started_in": "ignored"}],
    "routing": [
        {"step": 1, "id": "production", "when": "Production, deploys, servers, or money?",
         "role": None, "action": "stop_and_ask"},
        {"step": 2, "id": "question", "when": "A question or quick lookup?", "role": "lead",
         "action": "answer", "note": "Use a scout for wide searches."},
        {"step": 3, "id": "design", "when": "A design or approach decision?", "role": "lead",
         "action": "decide"},
        {"step": 4, "id": "image", "when": "An image?", "role": "artists", "action": "delegate"},
        {"step": 5, "id": "bulk", "when": "Bulk work?", "role": "mover", "action": "delegate"},
        {"step": 6, "id": "browser_check", "when": "Checking a UI in a real browser?",
         "role": "clicker", "action": "delegate"},
        {"step": 7, "id": "build", "when": "Anything else that writes code?",
         "role": "maker", "action": "delegate"},
    ],
    "roles": {
        "maker": {
            "options": [
                {"tool": "Toolone", "model": "Zorblat 3", "text": "ignored"},
                {"tool": "Tooltwo", "model": "Quixel Pro", "text": "ignored"},
            ],
            "effort": {"default": "medium", "tricky": "high"},
            "effort_text": "medium; high for tricky code",
        },
        "reviewer": {
            "options": [{"tool": "Toolone", "model": "Zorblat 3"}],
            "effort": {"by_tier": True},
            "effort_text": "By tier",
        },
        "artists": {
            "options": [{"tool": "Toolthree", "model": "Pixelo"}],
            "effort": {"default": None},
            "effort_text": "n/a",
        },
        "mover": {
            "options": [{"tool": "Toolfour", "model": "Haulbot"}],
            "effort": {"default": None},
            "effort_text": "Haulbot decides",
        },
        "clicker": {
            "options": [{"tool": "Toolfive", "model": "Clickbot"}],
            "effort": {"default": None},
            "effort_text": "n/a",
        },
    },
    "review_tiers": [
        {"id": "low", "reviewer_effort": None, "user_review": False, "zeta_key": "no review"},
        {"id": "normal", "reviewer_effort": "high", "user_review": False, "zeta_key": "one review"},
        {"id": "high", "reviewer_effort": "xhigh", "user_review": True, "zeta_key": "two reviews",
         "alpha_list": ["a", "b"], "beta_flag": True},
    ],
    "review_rounds_max": 3,
    "start_commands": {"x": "ignored"},
    "hard_rules": {
        "banned_models": ["Badmodel", "Evilone"],
        "effort_ceiling": "xhigh",
        "never_efforts": ["max", "ultra"],
    },
}

TASKS = {
    "question": "What does the status command show?",
    "design_product": "write a product roadmap and PRD for onboarding",
    "design_map": "map the work for onboarding",
    "production": "deploy the app to the staging server",
    "image": "generate a logo for the landing page",
    "bulk": "rename all the functions across the repo",
    "browser_check": "open the page in a real browser and click through the signup flow",
    "build": "Add an export endpoint with validation, tests, and docs",
    "review": "audit the parser module",
}
LOW_RISK = "update the footer text"
MEDIUM_RISK = "Add a retry to the http client"
HIGH_RISK = "write payment code for the checkout service"


def policy_with(**changes):
    policy = copy.deepcopy(POLICY)
    for key, value in changes.items():
        policy[key] = value
    return policy


class PolicyCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.project = base / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.missing = str(base / "does-not-exist.json")

    def write_policy(self, data, name="policy.json"):
        path = Path(self._tmp.name) / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return str(path)

    def put_home_policy(self, data):
        folder = self.home / ".agents"
        folder.mkdir(exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data)
        (folder / "policy.json").write_text(text, encoding="utf-8")

    def run_route(self, task, *flags, policy=None):
        """Run `route`. POLICY sets MYTHIFY_AGENT_POLICY; with neither it is unset."""
        env = dict(os.environ)
        env.pop("MYTHIFY_DIR", None)
        env.pop("MYTHIFY_AGENT_POLICY", None)
        env["HOME"] = str(self.home)
        if policy is not None:
            env["MYTHIFY_AGENT_POLICY"] = policy
        return subprocess.run(
            [sys.executable, str(CLI), "route", task] + list(flags),
            cwd=str(self.project),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def delegation(self, task, data=None):
        path = self.write_policy(POLICY if data is None else data)
        result = self.run_route(task, "--json", policy=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)["delegation"]

    def text(self, task, data=None):
        path = self.write_policy(POLICY if data is None else data)
        result = self.run_route(task, policy=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout


class TestNoPolicyIsUnchanged(PolicyCase):
    def test_output_is_identical_without_a_policy_file(self):
        for task in list(TASKS.values()) + [LOW_RISK, MEDIUM_RISK, HIGH_RISK]:
            for flags in ((), ("--json",)):
                with self.subTest(task=task, flags=flags):
                    home_only = self.run_route(task, *flags)
                    missing_env = self.run_route(task, *flags, policy=self.missing)
                    for result in (home_only, missing_env):
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stderr, "")
                        self.assertNotIn("Delegation", result.stdout)
                        self.assertNotIn("delegation", result.stdout)
                    self.assertEqual(home_only.stdout, missing_env.stdout)

    def test_a_policy_only_appends_it_never_changes_the_classification(self):
        path = self.write_policy(POLICY)
        for task in list(TASKS.values()) + [LOW_RISK, MEDIUM_RISK, HIGH_RISK]:
            with self.subTest(task=task):
                plain = json.loads(self.run_route(task, "--json", policy=self.missing).stdout)
                with_policy = json.loads(self.run_route(task, "--json", policy=path).stdout)
                delegation = with_policy.pop("delegation")
                self.assertTrue(delegation["advisory"])
                self.assertEqual(plain, with_policy)
                plain_text = self.run_route(task, policy=self.missing).stdout
                policy_text = self.run_route(task, policy=path).stdout
                self.assertTrue(policy_text.startswith(plain_text))

    def test_no_new_task_type_leaks_into_the_classifier(self):
        types = set(entry["id"] for entry in mythify_classification.CLASSIFICATION_MANIFEST["task_types"])
        for step in mythify_classification.POLICY_STEPS:
            self.assertNotIn(step["id"], types)


class TestLookup(PolicyCase):
    def test_env_override_wins_over_the_home_policy(self):
        home_policy = policy_with()
        home_policy["roles"]["maker"]["options"] = [{"tool": "Homeapp", "model": "Homemodel"}]
        self.put_home_policy(home_policy)
        env_policy = policy_with()
        env_policy["roles"]["maker"]["options"] = [{"tool": "Envapp", "model": "Envmodel"}]
        path = self.write_policy(env_policy)
        result = self.run_route(TASKS["build"], "--json", policy=path)
        options = json.loads(result.stdout)["delegation"]["role"]["options"]
        self.assertEqual(options, [{"tool": "Envapp", "model": "Envmodel"}])

    def test_home_policy_is_the_default(self):
        self.put_home_policy(POLICY)
        result = self.run_route(TASKS["build"], "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        delegation = json.loads(result.stdout)["delegation"]
        self.assertEqual(delegation["source"], str(self.home / ".agents" / "policy.json"))
        self.assertEqual(delegation["matched"]["id"], "build")

    def test_missing_env_path_does_not_fall_back_to_home(self):
        self.put_home_policy(POLICY)
        result = self.run_route(TASKS["build"], "--json", policy=self.missing)
        self.assertNotIn("delegation", json.loads(result.stdout))
        self.assertEqual(result.stderr, "")

    def test_empty_env_value_is_unset(self):
        self.put_home_policy(POLICY)
        result = self.run_route(TASKS["build"], "--json", policy="")
        self.assertIn("delegation", json.loads(result.stdout))

    def test_text_output_adds_a_delegation_section_at_the_end(self):
        out = self.text(TASKS["build"])
        self.assertIn("\nDelegation (advisory", out)
        self.assertLess(out.index("Guardrail:"), out.index("Delegation (advisory"))
        self.assertIn("- Matched: step 7 build: Anything else that writes code? -> delegate", out)
        self.assertIn("Toolone / Zorblat 3", out)


class TestBadPolicyFile(PolicyCase):
    def assert_ignored(self, policy_path, task=None):
        task = task or TASKS["build"]
        plain = self.run_route(task, "--json", policy=self.missing)
        for flags in ((), ("--json",)):
            result = self.run_route(task, *flags, policy=policy_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stderr.splitlines()
            self.assertEqual(len(lines), 1, result.stderr)
            self.assertTrue(lines[0].startswith("[WARN] "), lines[0])
            self.assertNotIn("elegation", result.stdout)
        self.assertEqual(
            self.run_route(task, "--json", policy=policy_path).stdout, plain.stdout
        )

    def test_invalid_json(self):
        self.assert_ignored(self.write_policy("{not json"))

    def test_empty_file(self):
        self.assert_ignored(self.write_policy(""))

    def test_unknown_version(self):
        for version in (2, 0, "1", True, None):
            with self.subTest(version=version):
                self.assert_ignored(self.write_policy(policy_with(version=version)))

    def test_wrong_shape(self):
        broken = {
            "list at top level": [],
            "routing is not a list": policy_with(routing={}),
            "routing is empty": policy_with(routing=[]),
            "routing entry lacks an id": policy_with(routing=[{"step": 1, "when": "x", "action": "answer"}]),
            "roles is a list": policy_with(roles=[]),
            "role without options": policy_with(roles={"maker": {"effort": {}}}),
            "option without a model": policy_with(
                roles={"maker": {"options": [{"tool": "t"}], "effort": {}}}),
            "tiers is not a list": policy_with(review_tiers={}),
            "banned models is text": policy_with(hard_rules={"banned_models": "Badmodel"}),
            "ceiling is not an effort": policy_with(hard_rules={"effort_ceiling": "loud"}),
            "rounds is text": policy_with(review_rounds_max="3"),
        }
        for label, data in broken.items():
            with self.subTest(label):
                self.assert_ignored(self.write_policy(data))

    def test_unreadable_path_is_a_directory(self):
        self.assert_ignored(str(self.home))

    def test_bad_home_policy_warns_once_too(self):
        self.put_home_policy("{")
        result = self.run_route(TASKS["build"], "--json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertNotIn("delegation", json.loads(result.stdout))


class TestStepSelection(PolicyCase):
    def test_each_step_id_comes_from_a_representative_task(self):
        for label, task, step_id, step in (
            ("question", TASKS["question"], "question", 2),
            ("question about a command", "what does verify gate do?", "question", 2),
            ("trivial edit is build", "update the footer text", "build", 7),
            ("design from product", TASKS["design_product"], "design", 3),
            ("design from map", TASKS["design_map"], "design", 3),
            ("production", TASKS["production"], "production", 1),
            ("image", TASKS["image"], "image", 4),
            ("bulk", TASKS["bulk"], "bulk", 5),
            ("browser check", TASKS["browser_check"], "browser_check", 6),
            ("build", TASKS["build"], "build", 7),
            ("review", TASKS["review"], "build", 7),
        ):
            with self.subTest(label):
                matched = self.delegation(task)["matched"]
                self.assertEqual((matched["id"], matched["step"]), (step_id, step))

    def test_direct_route_is_a_question_only_when_no_file_changes(self):
        for task, step_id, phrase in (
            ("what does verify gate do?", "question", "task type question: no file changes"),
            ("update the footer text", "build", "task type trivial: may change files"),
            ("continue the work", "build", "task type trivial: may change files"),
        ):
            with self.subTest(task=task):
                path = self.write_policy(POLICY)
                payload = json.loads(self.run_route(task, "--json", policy=path).stdout)
                self.assertEqual(payload["route"], "direct")
                delegation = payload["delegation"]
                self.assertEqual(delegation["matched"]["id"], step_id)
                self.assertIn(phrase, delegation["basis"])

    def test_action_when_and_note_come_from_the_policy(self):
        matched = self.delegation(TASKS["question"])["matched"]
        self.assertEqual(matched["action"], "answer")
        self.assertEqual(matched["when"], "A question or quick lookup?")
        self.assertEqual(matched["note"], "Use a scout for wide searches.")
        self.assertNotIn("note", self.delegation(TASKS["build"])["matched"])

    def test_route_mapping_for_the_plan_family(self):
        for task, route in (
            ("Keep going until the tests pass", "outcome"),
            ("Fix the parser until python3 -m unittest passes", "outcome"),
            (MEDIUM_RISK, "plan"),
        ):
            with self.subTest(task=task):
                path = self.write_policy(POLICY)
                payload = json.loads(self.run_route(task, "--json", policy=path).stdout)
                self.assertEqual(payload["route"], route)
                self.assertEqual(payload["delegation"]["matched"]["id"], "build")

    def test_production_wins_over_later_steps(self):
        for task in (
            "deploy the app and take screenshots of every page",
            "generate a logo and push it to production",
            "rename all the functions across the repo then deploy",
        ):
            with self.subTest(task=task):
                self.assertEqual(self.delegation(task)["matched"]["id"], "production")

    def test_payment_code_is_build_at_the_high_tier_not_production(self):
        delegation = self.delegation(HIGH_RISK)
        self.assertEqual(delegation["matched"]["id"], "build")
        self.assertEqual(delegation["review_tier"]["id"], "high")
        self.assertEqual(delegation["role"]["effort"], "high")

    def test_production_has_no_role(self):
        delegation = self.delegation(TASKS["production"])
        self.assertIsNone(delegation["role"])
        self.assertEqual(delegation["matched"]["action"], "stop_and_ask")
        self.assertIn("Role: not set", self.text(TASKS["production"]))

    def test_a_policy_without_the_step_says_so(self):
        routing = [entry for entry in POLICY["routing"] if entry["id"] != "image"]
        delegation = self.delegation(TASKS["image"], policy_with(routing=routing))
        self.assertIsNone(delegation["matched"])
        self.assertEqual(delegation["missing_step"], "image")
        self.assertIn("no step with id image", self.text(TASKS["image"], policy_with(routing=routing)))

    def test_review_route_uses_the_reviewer_role_and_the_tier_effort(self):
        delegation = self.delegation("audit the auth and login module")
        self.assertEqual(delegation["role"]["name"], "reviewer")
        self.assertEqual(delegation["role"]["effort"], "xhigh")
        self.assertEqual(delegation["role"]["effort_basis"], "review tier")
        low = self.delegation(TASKS["review"])
        self.assertEqual(low["role"]["name"], "reviewer")
        self.assertIsNone(low["role"]["effort"])

    def test_a_role_with_no_entry_prints_its_name_only(self):
        delegation = self.delegation(TASKS["question"])
        self.assertEqual(delegation["role"], {"name": "lead", "in_policy": False})
        self.assertIn("Role: lead (no entry in the policy roles)", self.text(TASKS["question"]))


class TestPolicyStepPrecision(unittest.TestCase):
    def step(self, task):
        hit = classify_policy_step(task)
        return hit["id"] if hit else None

    def test_positive_examples_per_step(self):
        for step_id, tasks in (
            ("production", (
                "deploy the app to staging", "release to production on Friday",
                "ssh into the box and restart the service", "run the migration on production",
                "push the fix to production", "migrate production data to the new schema",
                "buy more credits for the account", "spend money on ads",
                "upgrade our plan to the larger tier", "update billing details for the team",
                "rsync the build to the server", "go live with the new site",
            )),
            ("image", (
                "generate a logo for the landing page", "create an illustration for the blog post",
                "design a new logo for the app", "draw a mascot for the docs site",
                "generate images for the hero section",
            )),
            ("bulk", (
                "rename all the functions across the repo", "run a codemod over the services",
                "bump all dependencies", "fix lint errors across the codebase",
                "reformat the whole repository", "replace the old logger in every file",
            )),
            ("browser_check", (
                "check the signup page in a real browser", "take screenshots of the dashboard",
                "click through the checkout flow", "do a screen recording of the onboarding",
                "open the page in the browser and verify the layout",
            )),
        ):
            for task in tasks:
                with self.subTest(task=task):
                    self.assertEqual(self.step(task), step_id)

    def test_near_misses_are_not_policy_steps(self):
        for task in (
            "fix the deploy docs typo",
            "update the release notes for production",
            "write payment code for the checkout service",
            "add a billing page to the account settings",
            "implement a purchase flow in the shop",
            "add a screenshot to the README",
            "add a screen recording feature to the editor",
            "implement visual regression testing in CI",
            "rename this variable",
            "update the dependency in package.json",
            "explain how auth works across the codebase",
            "create an image upload endpoint",
            "resize the image on the profile page",
            "add an image to the README",
            "draw a sequence diagram in mermaid",
            "fix the parser",
            "What does the status command show?",
        ):
            with self.subTest(task=task):
                self.assertIsNone(self.step(task))

    def test_text_diagrams_are_judged_not_an_image(self):
        # A diagram written as mermaid or ASCII is text that lives in the
        # repository and is reviewed like code, so it goes to the builder.
        self.assertIsNone(self.step("draw a sequence diagram in mermaid"))
        self.assertIsNone(self.step("draw an architecture diagram for the docs"))

    def test_precedence_is_manifest_order(self):
        ids = [step["id"] for step in mythify_classification.POLICY_STEPS]
        self.assertEqual(ids, ["production", "image", "bulk", "browser_check"])
        self.assertEqual(self.step("deploy and take screenshots of the app"), "production")
        self.assertEqual(self.step("generate a logo and click through the site in a real browser"), "image")

    def test_manifest_shape_is_validated(self):
        validate = mythify_classification.validate_policy_steps
        good = copy.deepcopy(mythify_classification.CLASSIFICATION_MANIFEST["policy_steps"])
        validate(good)
        for label, mutate in (
            ("missing window", lambda m: m.pop("guard_window")),
            ("no steps", lambda m: m.update(steps=[])),
            ("duplicate id", lambda m: m["steps"].append(copy.deepcopy(m["steps"][0]))),
            ("empty step", lambda m: m["steps"][0].update(terms=[], pairs=[])),
            ("pair without then", lambda m: m["steps"][0]["pairs"][0].pop("then")),
            ("term not text", lambda m: m["steps"][0].update(terms=[1])),
        ):
            with self.subTest(label):
                broken = copy.deepcopy(good)
                mutate(broken)
                with self.assertRaises(ValueError):
                    validate(broken)
        with self.assertRaises(ValueError):
            validate(None)

    def test_schema_version_was_bumped(self):
        path = REPO_ROOT / "protocol" / "classification-rules.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 6)


class TestRiskAndEffort(PolicyCase):
    def test_mythify_risk_maps_to_the_policy_tier(self):
        for task, risk, tier, effort in (
            (LOW_RISK, "low", "low", None),
            (MEDIUM_RISK, "medium", "normal", "high"),
            (HIGH_RISK, "high", "high", "xhigh"),
        ):
            with self.subTest(risk=risk):
                delegation = self.delegation(task)
                self.assertEqual(delegation["review_tier"]["risk"], risk)
                self.assertEqual(delegation["review_tier"]["id"], tier)
                self.assertEqual(delegation["review_tier"]["reviewer_effort"], effort)
                self.assertEqual(delegation["review_tier"]["review_rounds_max"], 3)

    def test_tier_text_shows_reviewer_effort_user_review_and_rounds(self):
        out = self.text(HIGH_RISK)
        self.assertIn("Review tier: high (risk high); reviewer_effort: xhigh; user_review: true;", out)
        self.assertIn("review_rounds_max: 3", out)
        out = self.text(LOW_RISK)
        self.assertIn("Review tier: low (risk low); reviewer_effort: not set; user_review: false;", out)

    def test_extra_tier_keys_print_generically(self):
        out = self.text(HIGH_RISK)
        self.assertIn("zeta_key: two reviews", out)
        self.assertIn('alpha_list: ["a", "b"]', out)
        self.assertIn("beta_flag: true", out)
        self.assertEqual(
            self.delegation(HIGH_RISK)["review_tier"]["extra"],
            {"zeta_key": "two reviews", "alpha_list": ["a", "b"], "beta_flag": True},
        )

    def test_tricky_effort_applies_only_on_high_risk(self):
        self.assertEqual(self.delegation(HIGH_RISK)["role"]["effort"], "high")
        self.assertEqual(self.delegation(HIGH_RISK)["role"]["effort_basis"], "tricky effort, risk high")
        for task in (MEDIUM_RISK, TASKS["build"]):
            with self.subTest(task=task):
                self.assertEqual(self.delegation(task)["role"]["effort"], "medium")
        self.assertIn("Effort: high (tricky effort, risk high)", self.text(HIGH_RISK))

    def test_a_role_without_tricky_uses_the_default_on_high_risk(self):
        policy = policy_with()
        del policy["roles"]["maker"]["effort"]["tricky"]
        self.assertEqual(self.delegation(HIGH_RISK, policy)["role"]["effort"], "medium")

    def test_effort_text_is_shown(self):
        self.assertIn("Effort note: medium; high for tricky code", self.text(TASKS["build"]))

    def test_null_effort_reads_as_not_set(self):
        self.assertIsNone(self.delegation(TASKS["bulk"])["role"]["effort"])
        self.assertIn("Effort: not set", self.text(TASKS["bulk"]))


class TestGuards(PolicyCase):
    def test_banned_models_are_withheld_by_whole_word_or_substring(self):
        policy = policy_with()
        policy["roles"]["maker"]["options"] = [
            {"tool": "Toolone", "model": "Zorblat 3"},
            {"tool": "Tooltwo", "model": "badmodel"},
            {"tool": "Toolthree", "model": "The EVILONE Deluxe"},
            {"tool": "Toolfour", "model": "Super-Badmodel-2"},
        ]
        delegation = self.delegation(TASKS["build"], policy)
        self.assertEqual(delegation["role"]["options"], [{"tool": "Toolone", "model": "Zorblat 3"}])
        self.assertEqual(delegation["role"]["options_withheld"], 3)
        self.assertEqual(delegation["guards"]["banned_models_withheld"], 3)
        out = self.text(TASKS["build"], policy)
        self.assertIn("3 option(s) withheld", out)
        for name in ("badmodel", "evilone", "Super-Badmodel"):
            self.assertNotIn(name.lower(), out.lower())

    def test_every_option_banned_leaves_none_listed(self):
        policy = policy_with()
        policy["roles"]["maker"]["options"] = [{"tool": "T", "model": "Badmodel"}]
        out = self.text(TASKS["build"], policy)
        self.assertIn("1 option(s) withheld", out)
        self.assertNotIn("Badmodel", out)

    def test_an_effort_above_the_ceiling_is_clamped(self):
        for raw in ("max", "ultra", "MAX"):
            with self.subTest(raw=raw):
                policy = policy_with()
                policy["roles"]["maker"]["effort"] = {"default": raw, "tricky": raw}
                delegation = self.delegation(TASKS["build"], policy)
                self.assertEqual(delegation["role"]["effort"], "xhigh")
                self.assertTrue(delegation["guards"]["effort_clamped"])
                out = self.text(TASKS["build"], policy)
                self.assertIn("Effort: xhigh", out)
                self.assertIn("lowered to the highest allowed level", out)
                self.assertIsNone(re.search(r"\b(max|ultra)\b", out, re.IGNORECASE))

    def test_a_tier_reviewer_effort_above_the_ceiling_is_clamped(self):
        policy = policy_with()
        policy["review_tiers"][2]["reviewer_effort"] = "ultra"
        delegation = self.delegation(HIGH_RISK, policy)
        self.assertEqual(delegation["review_tier"]["reviewer_effort"], "xhigh")
        out = self.text(HIGH_RISK, policy)
        self.assertIsNone(re.search(r"\b(max|ultra)\b", out, re.IGNORECASE))

    def test_a_lower_ceiling_clamps_lower_levels_too(self):
        policy = policy_with()
        policy["hard_rules"]["effort_ceiling"] = "medium"
        delegation = self.delegation(HIGH_RISK, policy)
        self.assertEqual(delegation["role"]["effort"], "medium")
        self.assertEqual(delegation["review_tier"]["reviewer_effort"], "medium")

    def test_never_efforts_without_a_ceiling_withhold_the_effort(self):
        policy = policy_with()
        policy["hard_rules"] = {"never_efforts": ["max"]}
        policy["roles"]["maker"]["effort"] = {"default": "max"}
        delegation = self.delegation(TASKS["build"], policy)
        self.assertIsNone(delegation["role"]["effort"])
        self.assertTrue(delegation["guards"]["effort_withheld"])

    def test_policy_text_naming_a_forbidden_effort_is_replaced(self):
        policy = policy_with()
        policy["roles"]["maker"]["effort_text"] = "medium; max for tricky code"
        policy["review_tiers"][1]["note_text"] = "use ultra here"
        out = self.text(MEDIUM_RISK, policy)
        self.assertIsNone(re.search(r"\b(max|ultra)\b", out, re.IGNORECASE))
        self.assertIn("note_text: use [withheld] here", out)
        self.assertIn("Effort note: medium; [withheld] for tricky code", out)
        self.assertIn("replaced with [withheld]", out)

    def all_output(self, task, policy):
        """Text and JSON output of a route run, lower-cased, to search for leaks."""
        path = self.write_policy(policy)
        text = self.run_route(task, policy=path).stdout
        data = self.run_route(task, "--json", policy=path).stdout
        return text.lower(), data.lower(), json.loads(data)["delegation"]

    def test_a_banned_model_is_replaced_in_every_emitted_field(self):
        policy = policy_with()
        policy["hard_rules"] = {"banned_models": ["Badmodel", "Evilone"]}
        policy["routing"][6].update(when="Use Badmodel for code", note="Prefer EVILONE here")
        policy["roles"]["badmodel_maker"] = policy["roles"].pop("maker")
        policy["routing"][6]["role"] = "badmodel_maker"
        role = policy["roles"]["badmodel_maker"]
        role["options"] = [
            {"tool": "Badmodel CLI", "model": "Zorblat 3"},
            {"tool": "Toolone", "model": "Badmodel 2"},
        ]
        role["effort_text"] = "run on Evilone at medium"
        for tier in policy["review_tiers"]:
            tier["Evilone"] = "Badmodel is fine"
            tier["list"] = ["x badmodel y"]
        text, data, delegation = self.all_output(TASKS["build"], policy)
        for name in ("badmodel", "evilone"):
            self.assertNotIn(name, text)
            self.assertNotIn(name, data)
        self.assertIn("[withheld]", text)
        self.assertEqual(delegation["matched"]["when"], "Use [withheld] for code")
        self.assertEqual(delegation["matched"]["note"], "Prefer [withheld] here")
        self.assertEqual(delegation["role"]["name"], "[withheld]_maker")
        self.assertEqual(delegation["role"]["options"], [{"tool": "[withheld] CLI", "model": "Zorblat 3"}])
        self.assertEqual(delegation["role"]["options_withheld"], 1)
        self.assertTrue(delegation["guards"]["text_withheld"])
        self.assertEqual(
            delegation["review_tier"]["extra"]["list"], ["x [withheld] y"]
        )
        self.assertEqual(delegation["review_tier"]["extra"]["[withheld]"], "[withheld] is fine")

    def test_a_forbidden_effort_is_replaced_in_every_emitted_field(self):
        policy = policy_with()
        policy["hard_rules"] = {"never_efforts": ["ultra"], "effort_ceiling": "xhigh"}
        policy["routing"][6].update(when="Use ultra for code", note="max effort is off")
        policy["roles"]["ultra"] = policy["roles"].pop("maker")
        policy["routing"][6]["role"] = "ultra"
        policy["roles"]["ultra"]["effort_text"] = "ultra, then medium"
        for tier in policy["review_tiers"]:
            tier["ultra"] = "ultra value"
        text, data, delegation = self.all_output(TASKS["build"], policy)
        for pattern in (r"\bultra\b", r"\bmax\b(?!_)"):
            self.assertIsNone(re.search(pattern, text), pattern)
            self.assertIsNone(re.search(pattern, data), pattern)
        self.assertEqual(delegation["matched"]["when"], "Use [withheld] for code")
        self.assertEqual(delegation["role"]["name"], "[withheld]")
        self.assertEqual(delegation["review_tier"]["extra"].get("[withheld]"), "[withheld] value")
        self.assertIn("review_rounds_max", delegation["review_tier"])

    def test_text_and_json_carry_the_same_cleaned_strings(self):
        policy = policy_with()
        policy["hard_rules"] = {"banned_models": ["Zorblat"]}
        policy["routing"][6]["note"] = "Zorblat is great"
        text, data, delegation = self.all_output(TASKS["build"], policy)
        self.assertIn("policy note: [withheld] is great", text)
        self.assertEqual(delegation["matched"]["note"], "[withheld] is great")

    def test_a_null_value_prints_without_an_effort_word(self):
        policy = policy_with()
        policy["hard_rules"] = {"never_efforts": ["none"], "effort_ceiling": "xhigh"}
        for tier in policy["review_tiers"]:
            tier["nothing_here"] = None
        out = self.text(LOW_RISK, policy)
        block = out[out.index("Delegation (advisory"):]
        self.assertIsNone(re.search(r"\bnone\b", block, re.IGNORECASE), block)
        self.assertIn("reviewer_effort: not set", out)
        self.assertIn("nothing_here: not set", out)
        extra = self.delegation(LOW_RISK, policy)["review_tier"]
        self.assertIsNone(extra["reviewer_effort"])
        self.assertIsNone(extra["extra"]["nothing_here"])

    def test_clamping_never_lands_on_a_forbidden_level(self):
        policy = policy_with()
        policy["hard_rules"] = {"effort_ceiling": "xhigh", "never_efforts": ["xhigh"]}
        policy["roles"]["maker"]["effort"] = {"default": "ultra"}
        delegation = self.delegation(TASKS["build"], policy)
        self.assertEqual(delegation["role"]["effort"], "high")
        self.assertTrue(delegation["guards"]["effort_clamped"])
        policy["review_tiers"][0]["reviewer_effort"] = "xhigh"
        self.assertEqual(self.delegation(HIGH_RISK, policy)["review_tier"]["reviewer_effort"], "high")
        text, data, _ = self.all_output(TASKS["build"], policy)
        self.assertNotIn("xhigh", text)
        self.assertNotIn("xhigh", data)

    def test_a_forbidden_level_below_the_ceiling_steps_down_not_up(self):
        policy = policy_with()
        policy["hard_rules"] = {"effort_ceiling": "xhigh", "never_efforts": ["high"]}
        policy["roles"]["maker"]["effort"] = {"default": "high"}
        self.assertEqual(self.delegation(TASKS["build"], policy)["role"]["effort"], "medium")

    def test_no_allowed_level_withholds_the_effort(self):
        policy = policy_with()
        policy["hard_rules"] = {
            "effort_ceiling": "xhigh",
            "never_efforts": ["none", "low", "medium", "high", "xhigh"],
        }
        policy["roles"]["maker"]["effort"] = {"default": "ultra"}
        delegation = self.delegation(TASKS["build"], policy)
        self.assertIsNone(delegation["role"]["effort"])
        self.assertTrue(delegation["guards"]["effort_withheld"])
        self.assertFalse(delegation["guards"]["effort_clamped"])
        self.assertIn("withheld", self.text(TASKS["build"], policy))

    def test_a_policy_without_hard_rules_applies_no_guard(self):
        policy = policy_with()
        del policy["hard_rules"]
        policy["roles"]["maker"]["options"] = [{"tool": "T", "model": "Badmodel"}]
        self.assertEqual(
            self.delegation(TASKS["build"], policy)["role"]["options"],
            [{"tool": "T", "model": "Badmodel"}],
        )

    def test_guards_apply_to_the_other_roles_too(self):
        policy = policy_with()
        policy["roles"]["artists"]["options"].append({"tool": "T", "model": "Evilone XL"})
        delegation = self.delegation(TASKS["image"], policy)
        self.assertEqual(delegation["role"]["options_withheld"], 1)


class TestOnlyDocumentedKeysAreRead(PolicyCase):
    def test_ignored_keys_never_reach_the_output(self):
        policy = policy_with(
            description="never printed marker one",
            source="never printed marker two",
            quarterback=[{"started_in": "never printed marker three"}],
            start_commands={"x": "never printed marker four"},
        )
        policy["roles"]["maker"]["options"][0]["text"] = "never printed marker five"
        policy["roles"]["maker"]["options"][0]["note"] = "never printed marker six"
        policy["roles"]["maker"]["roster_row"] = "never printed marker seven"
        path = self.write_policy(policy)
        for flags in ((), ("--json",)):
            out = self.run_route(TASKS["build"], *flags, policy=path).stdout
            self.assertNotIn("never printed marker", out)


class TestCompatibility(unittest.TestCase):
    def test_policy_module_parses_as_python_39(self):
        for name in ("mythify_agent_policy.py", "mythify_classification.py", "mythify_router.py"):
            with self.subTest(name):
                source = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
                ast.parse(source, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
