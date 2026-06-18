"""Default planning horizon helpers shared by CLI routing and plan creation."""

import os


PLAN_HORIZON_ENV = "MYTHIFY_PLAN_HORIZON"
DEFAULT_ROUTE_PLAN_HORIZON = 20
MAX_PLAN_HORIZON = 20

PLAN_HORIZON_TEMPLATE = [
    {
        "title": "Confirm goal, done criteria, and non-goals",
        "success_criteria": "Goal, done criteria, and non-goals are explicit",
    },
    {
        "title": "Inspect repo state and recent changes",
        "success_criteria": "Branch, dirty files, and relevant recent commits are known",
    },
    {
        "title": "Read relevant docs, issues, tests, and acceptance criteria",
        "success_criteria": "Existing context and acceptance criteria are summarized",
    },
    {
        "title": "Map affected files, commands, env vars, services, and user flows",
        "success_criteria": "Likely impact surface and commands are identified",
    },
    {
        "title": "Identify highest-risk unknowns",
        "success_criteria": "Key risks and unknowns have a first check or mitigation",
    },
    {
        "title": "Prove the path with the smallest useful change",
        "success_criteria": "A minimal slice demonstrates the approach or exposes the blocker",
    },
    {
        "title": "Run the narrowest relevant verifier",
        "success_criteria": "A focused executable check records the first result",
    },
    {
        "title": "Fix failures from the first verifier",
        "success_criteria": "Focused verifier failure causes are corrected or recorded",
    },
    {
        "title": "Implement the main happy path",
        "success_criteria": "Requested happy-path behavior is present",
    },
    {
        "title": "Add or update happy-path tests",
        "success_criteria": "Happy-path coverage exercises the requested behavior",
    },
    {
        "title": "Implement edge cases and error states",
        "success_criteria": "Expected failure and boundary states are handled",
    },
    {
        "title": "Add regression tests for edge cases",
        "success_criteria": "Edge-case coverage proves the boundary behavior",
    },
    {
        "title": "Check auth, permissions, secrets, and data safety",
        "success_criteria": "Security and data-safety assumptions are verified or documented",
    },
    {
        "title": "Check UX, accessibility, performance, and compatibility",
        "success_criteria": "User-facing and operational quality checks are complete where relevant",
    },
    {
        "title": "Validate env and deployment assumptions",
        "success_criteria": "Required environment and deployment assumptions are known without exposing secrets",
    },
    {
        "title": "Run full relevant verification",
        "success_criteria": "The broadest relevant executable checks pass",
    },
    {
        "title": "Review diff for unrelated changes and style drift",
        "success_criteria": "Diff contains only intended changes and matches project style",
    },
    {
        "title": "Update docs, changelog, or handoff notes",
        "success_criteria": "User-facing or maintainer-facing notes are updated when needed",
    },
    {
        "title": "Package, commit, push, or prepare release",
        "success_criteria": "The completed work is ready for the requested delivery path",
    },
    {
        "title": "Report outcome, evidence, risks, and follow-up work",
        "success_criteria": "Final report cites verification evidence and any remaining risks",
    },
]


def parse_plan_horizon(value, source="--horizon"):
    if value is None or str(value).strip() == "":
        return None
    try:
        horizon = int(str(value), 10)
    except ValueError:
        raise ValueError("{0} must be an integer from 1 to {1}".format(source, MAX_PLAN_HORIZON))
    if horizon < 1 or horizon > MAX_PLAN_HORIZON:
        raise ValueError("{0} must be an integer from 1 to {1}".format(source, MAX_PLAN_HORIZON))
    return horizon


def env_plan_horizon(default=None):
    raw = os.environ.get(PLAN_HORIZON_ENV)
    if raw is None or raw.strip() == "":
        return default
    return parse_plan_horizon(raw, PLAN_HORIZON_ENV)


def route_plan_horizon():
    try:
        return env_plan_horizon(DEFAULT_ROUTE_PLAN_HORIZON)
    except ValueError:
        return DEFAULT_ROUTE_PLAN_HORIZON


def build_default_plan_steps(horizon):
    parsed = parse_plan_horizon(horizon)
    return [
        {
            "title": item["title"],
            "success_criteria": item["success_criteria"],
        }
        for item in PLAN_HORIZON_TEMPLATE[:parsed]
    ]
