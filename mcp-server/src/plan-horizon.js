export const PLAN_HORIZON_ENV = "MYTHIFY_PLAN_HORIZON";
export const DEFAULT_ROUTE_PLAN_HORIZON = 20;
export const MAX_PLAN_HORIZON = 20;

const PLAN_HORIZON_TEMPLATE = [
  {
    title: "Confirm goal, done criteria, and non-goals",
    success_criteria: "Goal, done criteria, and non-goals are explicit",
  },
  {
    title: "Inspect repo state and recent changes",
    success_criteria: "Branch, dirty files, and relevant recent commits are known",
  },
  {
    title: "Read relevant docs, issues, tests, and acceptance criteria",
    success_criteria: "Existing context and acceptance criteria are summarized",
  },
  {
    title: "Map affected files, commands, env vars, services, and user flows",
    success_criteria: "Likely impact surface and commands are identified",
  },
  {
    title: "Identify highest-risk unknowns",
    success_criteria: "Key risks and unknowns have a first check or mitigation",
  },
  {
    title: "Prove the path with the smallest useful change",
    success_criteria: "A minimal slice demonstrates the approach or exposes the blocker",
  },
  {
    title: "Run the narrowest relevant verifier",
    success_criteria: "A focused executable check records the first result",
  },
  {
    title: "Fix failures from the first verifier",
    success_criteria: "Focused verifier failure causes are corrected or recorded",
  },
  {
    title: "Implement the main happy path",
    success_criteria: "Requested happy-path behavior is present",
  },
  {
    title: "Add or update happy-path tests",
    success_criteria: "Happy-path coverage exercises the requested behavior",
  },
  {
    title: "Implement edge cases and error states",
    success_criteria: "Expected failure and boundary states are handled",
  },
  {
    title: "Add regression tests for edge cases",
    success_criteria: "Edge-case coverage proves the boundary behavior",
  },
  {
    title: "Check auth, permissions, secrets, and data safety",
    success_criteria: "Security and data-safety assumptions are verified or documented",
  },
  {
    title: "Check UX, accessibility, performance, and compatibility",
    success_criteria: "User-facing and operational quality checks are complete where relevant",
  },
  {
    title: "Validate env and deployment assumptions",
    success_criteria: "Required environment and deployment assumptions are known without exposing secrets",
  },
  {
    title: "Run full relevant verification",
    success_criteria: "The broadest relevant executable checks pass",
  },
  {
    title: "Review diff for unrelated changes and style drift",
    success_criteria: "Diff contains only intended changes and matches project style",
  },
  {
    title: "Update docs, changelog, or handoff notes",
    success_criteria: "User-facing or maintainer-facing notes are updated when needed",
  },
  {
    title: "Package, commit, push, or prepare release",
    success_criteria: "The completed work is ready for the requested delivery path",
  },
  {
    title: "Report outcome, evidence, risks, and follow-up work",
    success_criteria: "Final report cites verification evidence and any remaining risks",
  },
];

export function parsePlanHorizon(value, source = "horizon") {
  if (value === undefined || value === null || String(value).trim() === "") {
    return null;
  }
  const raw = String(value).trim();
  if (!/^[0-9]+$/.test(raw)) {
    throw new Error(`${source} must be an integer from 1 to ${MAX_PLAN_HORIZON}`);
  }
  const horizon = Number.parseInt(raw, 10);
  if (horizon < 1 || horizon > MAX_PLAN_HORIZON) {
    throw new Error(`${source} must be an integer from 1 to ${MAX_PLAN_HORIZON}`);
  }
  return horizon;
}

export function envPlanHorizon(defaultValue = null) {
  const raw = process.env[PLAN_HORIZON_ENV];
  if (raw === undefined || String(raw).trim() === "") {
    return defaultValue;
  }
  return parsePlanHorizon(raw, PLAN_HORIZON_ENV);
}

export function routePlanHorizon() {
  try {
    return envPlanHorizon(DEFAULT_ROUTE_PLAN_HORIZON);
  } catch {
    return DEFAULT_ROUTE_PLAN_HORIZON;
  }
}

export function buildDefaultPlanSteps(horizon) {
  const parsed = parsePlanHorizon(horizon);
  return PLAN_HORIZON_TEMPLATE.slice(0, parsed).map((item) => ({
    title: item.title,
    success_criteria: item.success_criteria,
  }));
}
