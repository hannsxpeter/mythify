import { z } from "zod";
import {
  MAX_PLAN_HORIZON,
  buildDefaultPlanSteps,
  envPlanHorizon,
  parsePlanHorizon,
} from "./plan-horizon.js";

export const PLAN_TOOL_NAMES = [
  "plan_create",
  "plan_add_step",
  "plan_update_step",
  "plan_status",
];

const STEP_STATUSES = ["pending", "in_progress", "completed", "failed", "skipped"];

function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerPlanTools requires deps.${name}`);
  }
  return value;
}

export function registerPlanTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const slugify = requireDep(deps, "slugify");
  const uniquePlanSlug = requireDep(deps, "uniquePlanSlug");
  const isoNow = requireDep(deps, "isoNow");
  const writeJsonAtomic = requireDep(deps, "writeJsonAtomic");
  const planPath = requireDep(deps, "planPath");
  const setActiveSlug = requireDep(deps, "setActiveSlug");
  const stepLine = requireDep(deps, "stepLine");
  const resolvePlan = requireDep(deps, "resolvePlan");
  const savePlan = requireDep(deps, "savePlan");
  const strictStepEvidenceEnabled = requireDep(deps, "strictStepEvidenceEnabled");
  const readJsonlSince = requireDep(deps, "readJsonlSince");
  const verificationsPath = requireDep(deps, "verificationsPath");
  const verificationRecordMatchesStep = requireDep(deps, "verificationRecordMatchesStep");
  const timestampAtOrAfter = requireDep(deps, "timestampAtOrAfter");
  const verificationRecordHasExplicitStepContext = requireDep(deps, "verificationRecordHasExplicitStepContext");
  const nextPendingText = requireDep(deps, "nextPendingText");
  const readActiveSlug = requireDep(deps, "readActiveSlug");
  const frontDoorNote = typeof deps.mcpFrontDoorNote === "string" ? deps.mcpFrontDoorNote : "";

  server.registerTool(
    "plan_create",
    {
      title: "Create a plan",
      description:
        "Create a new plan with a goal and optional initial steps, and set it as the active plan. " +
        "Use this at the start of any multi-step task so progress is tracked outside the context window; trivial single-edit tasks do not need a plan." +
        frontDoorNote,
      inputSchema: {
        goal: z.string().describe("What the plan accomplishes; shown in plan_status."),
        name: z.string().optional().describe("Optional plan name; slugified for the filename. Defaults to a slug of the goal."),
        steps: z
          .array(
            z.object({
              title: z.string().describe("Step title."),
              success_criteria: z.string().optional().describe("How to tell the step is done. Defaults to empty."),
            })
          )
          .optional()
          .describe("Initial steps; ids are auto-assigned starting at 1."),
        horizon: z
          .number()
          .int()
          .min(1)
          .max(MAX_PLAN_HORIZON)
          .optional()
          .describe("Create N default lookahead steps when steps is omitted."),
      },
    },
    guarded(({ goal, name, steps, horizon }) => {
      if (steps !== undefined && horizon !== undefined) {
        return "[FAIL] horizon can only be used when steps is omitted.";
      }
      let inputSteps = steps || [];
      if (steps === undefined) {
        let targetHorizon = null;
        try {
          targetHorizon = horizon !== undefined ? parsePlanHorizon(horizon, "horizon") : envPlanHorizon(null);
        } catch (error) {
          return `[FAIL] ${error.message}`;
        }
        if (targetHorizon !== null) {
          inputSteps = buildDefaultPlanSteps(targetHorizon);
        }
      }
      const base =
        slugify(name !== undefined && name !== null && String(name).trim() !== "" ? name : goal) || "plan";
      const slug = uniquePlanSlug(base);
      const now = isoNow();
      const planSteps = inputSteps.map((s, i) => ({
        id: i + 1,
        title: s.title,
        success_criteria: s.success_criteria || "",
        status: "pending",
        result: null,
      }));
      const plan = {
        name: slug,
        goal,
        steps: planSteps,
        created: now,
        last_updated: now,
      };
      writeJsonAtomic(planPath(slug), plan);
      setActiveSlug(slug);
      const lines = [
        `[OK] Created plan "${slug}" with ${planSteps.length} ${planSteps.length === 1 ? "step" : "steps"}; it is now the active plan.`,
        `Goal: ${goal}`,
      ];
      if (planSteps.length === 0) {
        lines.push("The plan has no steps yet; add them with plan_add_step.");
      } else {
        for (const step of planSteps) {
          lines.push(stepLine(step));
        }
      }
      return lines.join("\n");
    })
  );

  server.registerTool(
    "plan_add_step",
    {
      title: "Add a step to a plan",
      description:
        "Append a step to the named plan, or to the active plan when no name is given. The step id is assigned automatically. " +
        "Use this when new work is discovered mid-task or when fleshing out a plan created without steps.",
      inputSchema: {
        title: z.string().describe("Step title."),
        success_criteria: z.string().optional().describe("How to tell the step is done. Defaults to empty."),
        plan: z.string().optional().describe("Plan name; omit to use the active plan."),
      },
    },
    guarded(({ title, success_criteria, plan: planName }) => {
      const resolved = resolvePlan(planName);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, plan } = resolved;
      if (!Array.isArray(plan.steps)) {
        plan.steps = [];
      }
      const maxId = plan.steps.reduce((m, s) => (typeof s.id === "number" && s.id > m ? s.id : m), 0);
      const step = {
        id: maxId + 1,
        title,
        success_criteria: success_criteria || "",
        status: "pending",
        result: null,
      };
      plan.steps.push(step);
      savePlan(slug, plan);
      return `[OK] Added step ${step.id} to plan "${slug}": ${title}`;
    })
  );

  server.registerTool(
    "plan_update_step",
    {
      title: "Update a plan step's status",
      description:
        "Set a step's status (pending, in_progress, completed, failed, skipped) on the named or active plan. " +
        "Marking a step completed or failed REQUIRES a result describing the evidence; without it the plan is left unmodified. " +
        "By default, completed also requires a passing verify_run since the step started; set MYTHIFY_REQUIRE_VERIFIED_STEP=0 only for legacy prose-only completion. " +
        "Use this as you start, finish, fail, or skip each step of the active plan.",
      inputSchema: {
        step_id: z.number().int().describe("The 1-based id of the step to update."),
        status: z
          .enum(STEP_STATUSES)
          .describe("New status: pending, in_progress, completed, failed, or skipped."),
        result: z
          .string()
          .optional()
          .describe("Evidence or outcome description. Required for completed and failed."),
        plan: z.string().optional().describe("Plan name; omit to use the active plan."),
      },
    },
    guarded(({ step_id, status, result, plan: planName }) => {
      const needsEvidence = status === "completed" || status === "failed";
      const hasResult = typeof result === "string" && result.trim() !== "";
      if (needsEvidence && !hasResult) {
        return (
          "[FAIL] Evidence required: pass a RESULT describing what proves this status. " +
          "The plan was not modified."
        );
      }
      const resolved = resolvePlan(planName);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, plan } = resolved;
      const step = (plan.steps || []).find((s) => s.id === step_id);
      if (!step) {
        return `[FAIL] No step with id ${step_id} in plan "${slug}".`;
      }
      if (status === "completed" && strictStepEvidenceEnabled()) {
        const lowerBound =
          typeof step.updated_at === "string" && step.updated_at !== ""
            ? step.updated_at
            : plan.created;
        const verifications = readJsonlSince(verificationsPath(), lowerBound);
        const hasPassingRun = verifications.some(
          (record) =>
            record &&
            record.kind === "executed" &&
            record.verified === true &&
            typeof record.timestamp === "string" &&
            verificationRecordMatchesStep(record, slug, step_id) &&
            timestampAtOrAfter(
              record.timestamp,
              lowerBound,
              verificationRecordHasExplicitStepContext(record, slug, step_id)
            )
        );
        if (!hasPassingRun) {
          return (
            "[FAIL] Verified evidence required: strict evidence mode is enabled by default, but no passing 'verify run' " +
            "was recorded since this step started. Run 'verify run' with a passing check first, or set " +
            "MYTHIFY_REQUIRE_VERIFIED_STEP=0 to use legacy prose-only completion."
          );
        }
      }
      step.status = status;
      if (hasResult) {
        step.result = result;
      }
      step.updated_at = isoNow();
      savePlan(slug, plan);
      return [
        `[OK] Step ${step_id} of plan "${slug}" is now ${status}: ${step.title}`,
        nextPendingText(plan),
      ].join("\n");
    })
  );

  server.registerTool(
    "plan_status",
    {
      title: "Show plan status",
      description:
        "Show the named or active plan: its goal, progress count, and every step with a status icon, criteria, and result. " +
        "Use this to orient at session start, after each step update, and before deciding what to do next.",
      inputSchema: {
        plan: z.string().optional().describe("Plan name; omit to use the active plan."),
      },
    },
    guarded(({ plan: planName }) => {
      if (
        (planName === undefined || planName === null || String(planName).trim() === "") &&
        !readActiveSlug()
      ) {
        return "[OK] No active plan yet. Create one with plan_create.";
      }
      const resolved = resolvePlan(planName);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, plan } = resolved;
      const steps = Array.isArray(plan.steps) ? plan.steps : [];
      const done = steps.filter((s) => s.status === "completed").length;
      const lines = [
        `[OK] Plan "${slug}": ${plan.goal}`,
        `Progress: ${done}/${steps.length} steps completed.`,
      ];
      if (steps.length === 0) {
        lines.push("No steps yet; add them with plan_add_step.");
      } else {
        for (const step of steps) {
          lines.push(stepLine(step));
        }
        lines.push(nextPendingText(plan));
      }
      return lines.join("\n");
    })
  );
}
