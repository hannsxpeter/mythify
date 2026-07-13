import { z } from "zod";
import { FANOUT_VISIBILITY_MODES } from "./capability-registry.js";
import path from "node:path";

import { currentVerificationProvenanceForStateDir } from "./verification-provenance.js";

export const OUTCOME_TOOL_NAMES = [
  "outcome_start",
  "outcome_check",
  "outcome_status",
  "outcome_results",
  "outcome_stop",
];

function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerOutcomeTools requires deps.${name}`);
  }
  return value;
}

export function registerOutcomeTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const slugify = requireDep(deps, "slugify");
  const uniqueOutcomeSlug = requireDep(deps, "uniqueOutcomeSlug");
  const isoNow = requireDep(deps, "isoNow");
  const saveOutcome = requireDep(deps, "saveOutcome");
  const setActiveOutcomeSlug = requireDep(deps, "setActiveOutcomeSlug");
  const resolveOutcome = requireDep(deps, "resolveOutcome");
  const readOutcomeIterations = requireDep(deps, "readOutcomeIterations");
  const formatOutcomeStatus = requireDep(deps, "formatOutcomeStatus");
  const runShellCapture = requireDep(deps, "runShellCapture");
  const parseMetricScore = requireDep(deps, "parseMetricScore");
  const appendJsonl = requireDep(deps, "appendJsonl");
  const outcomeIterationsPath = requireDep(deps, "outcomeIterationsPath");
  const verificationsPath = requireDep(deps, "verificationsPath");
  const verificationStepContext = requireDep(deps, "verificationStepContext");
  const clearActiveOutcomeSlug = requireDep(deps, "clearActiveOutcomeSlug");
  const scopeViolations = requireDep(deps, "scopeViolations");
  const frontDoorNote = typeof deps.mcpFrontDoorNote === "string" ? deps.mcpFrontDoorNote : "";

  server.registerTool(
    "outcome_start",
    {
      title: "Start an outcome loop",
      description:
        "Start a supervised outcome loop: define the desired outcome, the success criteria, the verifier command, and the iteration budget. " +
        "The host agent performs bounded attempts between outcome_check calls; Mythify records evidence and decides whether to retry, stop, or report success." +
        frontDoorNote,
      inputSchema: {
        goal: z.string().describe("Outcome goal."),
        success: z.string().describe("Human-readable success criteria."),
        verify_command: z.string().describe("Shell command that verifies the outcome."),
        metric_command: z.string().optional().describe("Optional shell command that emits a metric."),
        max_iterations: z
          .number()
          .int()
          .positive()
          .default(3)
          .describe("Maximum verifier iterations before the outcome fails."),
        allowed_paths: z
          .array(z.string())
          .optional()
          .describe("Optional advisory path hints for host edits; recorded for policy, not enforced as a sandbox."),
        visibility: z
          .enum(FANOUT_VISIBILITY_MODES)
          .optional()
          .describe("How much loop progress the host should surface: auto, quiet, summary, verbose, or threaded."),
        name: z.string().optional().describe("Outcome name; defaults to a slug of the goal."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ goal, success, verify_command, metric_command, max_iterations, allowed_paths, visibility, name, format }) => {
      const base = slugify(name || goal) || "outcome";
      const slug = uniqueOutcomeSlug(base);
      const now = isoNow();
      const record = {
        id: slug,
        goal,
        success_criteria: success,
        verify_command,
        metric_command: metric_command || "",
        agent_command: "",
        max_iterations: max_iterations || 3,
        iteration_count: 0,
        max_cost: null,
        cost_spent: 0.0,
        escalate_after: null,
        allowed_paths: Array.isArray(allowed_paths) ? allowed_paths : [],
        visibility: visibility || "summary",
        status: "active",
        created: now,
        updated: now,
        last_verified: null,
        best_metric_score: null,
        stop_reason: null,
      };
      saveOutcome(slug, record);
      setActiveOutcomeSlug(slug);
      if (format === "json") {
        return `[OK] ${JSON.stringify(record, null, 2)}`;
      }
      return [
        `[OK] Outcome started: ${slug}`,
        `goal: ${goal}`,
        `success: ${success}`,
        `verify: ${verify_command}`,
        metric_command ? `metric: ${metric_command}` : null,
        `iterations: 0/${record.max_iterations}`,
        "next: make a bounded attempt, then call outcome_check.",
      ].filter(Boolean).join("\n");
    })
  );

  server.registerTool(
    "outcome_check",
    {
      title: "Run an outcome verifier iteration",
      description:
        "Run the verifier and optional metric for the active or named outcome, record the iteration, and return whether the host should retry, stop, or report success.",
      inputSchema: {
        name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
        notes: z.string().optional().describe("Notes for this iteration."),
        timeout_seconds: z
          .number()
          .positive()
          .default(300)
          .describe("Kill each command after this many seconds. Defaults to 300."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ name, notes, timeout_seconds, format }) => {
      if (process.env.MYTHIFY_DISABLE_RUN === "1") {
        return (
          "[FAIL] outcome_check is disabled: the server environment sets MYTHIFY_DISABLE_RUN=1. " +
          "No command was executed and nothing was recorded."
        );
      }
      const resolved = resolveOutcome(name);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, goal } = resolved;
      if (["succeeded", "failed", "stopped"].includes(goal.status)) {
        if (format === "json") {
          return `[OK] ${JSON.stringify({ goal, record: null }, null, 2)}`;
        }
        return `[OK] Outcome ${slug} is already ${goal.status}.`;
      }
      const iterationCount = Number.parseInt(goal.iteration_count || 0, 10);
      const maxIterations = Number.parseInt(goal.max_iterations || 1, 10);
      if (iterationCount >= maxIterations) {
        goal.status = "failed";
        goal.stop_reason = "iteration budget exhausted before check";
        saveOutcome(slug, goal);
        if (format === "json") {
          return `[FAIL] ${JSON.stringify({ goal, record: null }, null, 2)}`;
        }
        return `[FAIL] Outcome ${slug} failed: iteration budget exhausted.`;
      }
      const timeout = timeout_seconds || 300;
      const verify = runShellCapture(goal.verify_command, timeout);
      let metricRecord = null;
      let metricOk = true;
      let metricScore = null;
      if (goal.metric_command) {
        const metric = runShellCapture(goal.metric_command, timeout);
        metricOk = metric.verified;
        metricScore = parseMetricScore(metric.stdout_tail);
        metricRecord = {
          command: metric.command,
          exit_code: metric.exit_code,
          duration_seconds: metric.duration_seconds,
          stdout_tail: metric.stdout_tail,
          stderr_tail: metric.stderr_tail,
          verified: metric.verified,
          score: metricScore,
        };
      }
      const verified = Boolean(verify.verified && metricOk);
      const violations = scopeViolations(goal.allowed_paths || []);
      const nextIteration = iterationCount + 1;
      let statusAfter;
      let nextAction;
      if (verified) {
        statusAfter = "succeeded";
        nextAction = "Outcome met. Report the evidence and stop.";
      } else if (nextIteration >= maxIterations) {
        statusAfter = "failed";
        nextAction = "Iteration budget exhausted. Summarize the blocker and stop.";
      } else {
        statusAfter = "active";
        nextAction = "Outcome not met. Inspect verifier output, make another bounded attempt, then call outcome_check again.";
      }
      if (violations.length > 0) {
        nextAction =
          `Scope note: ${violations.length} file(s) changed outside scope ` +
          `(${violations.slice(0, 5).join(", ")}). ${nextAction}`;
      }
      const record = {
        iteration: nextIteration,
        timestamp: isoNow(),
        notes: notes || "",
        agent: null,
        cost: 0.0,
        cost_spent: Number(goal.cost_spent || 0),
        verify: {
          command: verify.command,
          exit_code: verify.exit_code,
          duration_seconds: verify.duration_seconds,
          stdout_tail: verify.stdout_tail,
          stderr_tail: verify.stderr_tail,
          verified: verify.verified,
        },
        metric: metricRecord,
        verified,
        scope_violations: violations,
        status_after: statusAfter,
        next_action: nextAction,
      };
      appendJsonl(outcomeIterationsPath(slug), record);
      goal.iteration_count = nextIteration;
      goal.status = statusAfter;
      goal.last_verified = verified;
      if (metricScore !== null) {
        const best = goal.best_metric_score;
        if (best === null || best === undefined || metricScore > best) {
          goal.best_metric_score = metricScore;
        }
      }
      if (statusAfter === "failed") {
        goal.stop_reason = "iteration budget exhausted";
      }
      if (statusAfter === "succeeded") {
        goal.stop_reason = "success criteria verified";
      }
      saveOutcome(slug, goal);
      const combinedExitCode = verify.verified && metricRecord && !metricOk
        ? metricRecord.exit_code
        : verify.exit_code;
      const combinedDuration = verify.duration_seconds +
        (metricRecord ? metricRecord.duration_seconds : 0);
      appendJsonl(verificationsPath(), {
        kind: "executed",
        claim: `Outcome ${slug}: ${goal.success_criteria || ""}`,
        command: goal.verify_command,
        exit_code: combinedExitCode,
        duration_seconds: combinedDuration,
        stdout_tail: verify.stdout_tail,
        stderr_tail: verify.stderr_tail,
        verified,
        outcome_verify: record.verify,
        outcome_metric: metricRecord,
        timestamp: record.timestamp,
        outcome: slug,
        iteration: nextIteration,
        provenance: currentVerificationProvenanceForStateDir(
          path.dirname(verificationsPath())
        ),
        ...verificationStepContext(),
      });
      if (format === "json") {
        const prefix = verified ? "[OK]" : "[FAIL]";
        return `${prefix} ${JSON.stringify({ goal, record }, null, 2)}`;
      }
      const prefix = verified ? "[OK]" : "[FAIL]";
      const lines = [
        `${prefix} Outcome ${slug} iteration ${nextIteration}/${maxIterations}: ${statusAfter}`,
        `verify exit: ${verify.exit_code}`,
      ];
      if (metricRecord) {
        lines.push(`metric exit: ${metricRecord.exit_code}`);
        if (metricScore !== null) {
          lines.push(`metric score: ${metricScore}`);
        }
      }
      lines.push(`next: ${nextAction}`);
      if (verify.stdout_tail) {
        lines.push("--- verify stdout (tail) ---");
        lines.push(verify.stdout_tail);
      }
      if (verify.stderr_tail) {
        lines.push("--- verify stderr (tail) ---");
        lines.push(verify.stderr_tail);
      }
      return lines.join("\n");
    })
  );

  server.registerTool(
    "outcome_status",
    {
      title: "Show outcome loop status",
      description:
        "Show the active or named outcome loop: status, verifier, iteration budget, and next action.",
      inputSchema: {
        name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ name, format }) => {
      const resolved = resolveOutcome(name);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, goal } = resolved;
      const iterations = readOutcomeIterations(slug);
      if (format === "json") {
        return `[OK] ${JSON.stringify({ goal, iterations }, null, 2)}`;
      }
      return formatOutcomeStatus(slug, goal, iterations);
    })
  );

  server.registerTool(
    "outcome_results",
    {
      title: "Show outcome loop results",
      description:
        "Show all verifier iterations for the active or named outcome, including verifier exits, metric exits, final status, and next action.",
      inputSchema: {
        name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ name, format }) => {
      const resolved = resolveOutcome(name);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, goal } = resolved;
      const iterations = readOutcomeIterations(slug);
      if (format === "json") {
        return `[OK] ${JSON.stringify({ goal, iterations }, null, 2)}`;
      }
      const lines = [formatOutcomeStatus(slug, goal, iterations)];
      for (const item of iterations) {
        lines.push("");
        lines.push(`iteration ${item.iteration}: verified=${item.verified}, status=${item.status_after}`);
        lines.push(`  verify exit: ${item.verify?.exit_code}`);
        if (item.metric) {
          lines.push(`  metric exit: ${item.metric.exit_code}`);
          if (item.metric.score !== null && item.metric.score !== undefined) {
            lines.push(`  metric score: ${item.metric.score}`);
          }
        }
      }
      return lines.join("\n");
    })
  );

  server.registerTool(
    "outcome_stop",
    {
      title: "Stop an outcome loop",
      description:
        "Mark the active or named outcome loop stopped and clear the active pointer when it matches.",
      inputSchema: {
        name: z.string().optional().describe("Outcome name; omit to use the active outcome."),
        reason: z.string().describe("Why the outcome loop is being stopped."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ name, reason, format }) => {
      const resolved = resolveOutcome(name);
      if (resolved.error) {
        return resolved.error;
      }
      const { slug, goal } = resolved;
      goal.status = "stopped";
      goal.stop_reason = reason;
      saveOutcome(slug, goal);
      clearActiveOutcomeSlug(slug);
      if (format === "json") {
        return `[OK] ${JSON.stringify(goal, null, 2)}`;
      }
      return `[OK] Outcome ${slug} stopped: ${reason}`;
    })
  );
}
