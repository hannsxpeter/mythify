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

// Changed paths under a frozen prefix. A deny-list, enforced everywhere: the
// held-out set the loop must never touch, tests being the canonical case, so
// a scoped agent cannot rewrite its own verifier. The .mythify/ exemption
// does not apply: the user named these prefixes.
function frozenPathViolations(changed, frozen) {
  if (!Array.isArray(frozen) || frozen.length === 0 || !Array.isArray(changed)) {
    return [];
  }
  const prefixes = frozen
    .map((item) => String(item).replace(/\/+$/, ""))
    .filter((item) => item !== "");
  return changed.filter((path) => {
    const normalized = String(path).replace(/\/+$/, "");
    return prefixes.some(
      (prefix) => normalized === prefix || normalized.startsWith(`${prefix}/`)
    );
  });
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
  const changedPaths = requireDep(deps, "changedPaths");
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
        metric_floor: z
          .number()
          .optional()
          .describe("Minimum metric score required for success. Requires metric_command."),
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
        frozen_paths: z
          .array(z.string())
          .optional()
          .describe("Paths the loop must never touch (e.g. tests/). Enforced in every mode."),
        supersede: z
          .string()
          .optional()
          .describe("Retire the currently active outcome into this one, recording the reason and lineage."),
        visibility: z
          .enum(FANOUT_VISIBILITY_MODES)
          .optional()
          .describe("How much loop progress the host should surface: auto, quiet, summary, verbose, or threaded."),
        name: z.string().optional().describe("Outcome name; defaults to a slug of the goal."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ goal, success, verify_command, metric_command, metric_floor, max_iterations, allowed_paths, frozen_paths, supersede, visibility, name, format }) => {
      if (metric_floor !== undefined && metric_floor !== null && !metric_command) {
        return "[FAIL] outcome_start requires metric_command when metric_floor is set.";
      }
      // Two live loops fight each other and neither owns the trade-off, so a
      // second start needs an explicit supersession instead of silently
      // stealing the active pointer.
      const supersedeReason = String(supersede || "").trim();
      let superseded = null;
      const activeResolved = resolveOutcome(undefined);
      if (!activeResolved.error && activeResolved.goal && activeResolved.goal.status === "active") {
        if (!supersedeReason) {
          return (
            `[FAIL] Outcome ${activeResolved.slug} is still active. Stop it with outcome_stop, ` +
            "or pass supersede REASON to retire it into this one."
          );
        }
        superseded = activeResolved;
      }
      const base = slugify(name || goal) || "outcome";
      const slug = uniqueOutcomeSlug(base);
      const now = isoNow();
      const record = {
        id: slug,
        goal,
        success_criteria: success,
        verify_command,
        metric_command: metric_command || "",
        metric_floor: metric_floor === undefined ? null : metric_floor,
        agent_command: "",
        max_iterations: max_iterations || 3,
        iteration_count: 0,
        max_cost: null,
        cost_spent: 0.0,
        escalate_after: null,
        allowed_paths: Array.isArray(allowed_paths) ? allowed_paths : [],
        frozen_paths: Array.isArray(frozen_paths) ? frozen_paths : [],
        visibility: visibility || "summary",
        status: "active",
        created: now,
        updated: now,
        last_verified: null,
        best_metric_score: null,
        stop_reason: null,
        supersedes: superseded ? superseded.slug : null,
      };
      if (superseded) {
        superseded.goal.status = "stopped";
        superseded.goal.stop_reason = `superseded by ${slug}: ${supersedeReason}`;
        superseded.goal.superseded_by = slug;
        superseded.goal.updated = now;
        saveOutcome(superseded.slug, superseded.goal);
      }
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
        record.metric_floor !== null ? `metric floor: ${record.metric_floor}` : null,
        record.frozen_paths.length > 0
          ? `frozen (never touched, enforced): ${record.frozen_paths.join(", ")}`
          : null,
        superseded ? `superseded: ${superseded.slug} (${supersedeReason})` : null,
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
        audit: z
          .boolean()
          .optional()
          .describe(
            "Re-run a finished outcome's verifier without mutating its history; a red run marks the evidence stale."
          ),
        timeout_seconds: z
          .number()
          .positive()
          .default(300)
          .describe("Kill each command after this many seconds. Defaults to 300."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ name, notes, audit, timeout_seconds, format }) => {
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
      if (audit === true) {
        // The audit loop's only job is checking that the recorded result
        // still touches reality: iteration_count and status stay untouched.
        if (goal.status === "active") {
          return (
            `[FAIL] audit re-checks a finished outcome, but ${slug} is still active. ` +
            "Call outcome_check without audit."
          );
        }
        const auditVerify = runShellCapture(goal.verify_command, timeout_seconds || 300);
        const stamp = isoNow();
        const auditRecord = {
          iteration: Number.parseInt(goal.iteration_count || 0, 10),
          timestamp: stamp,
          notes: notes || "",
          audit: true,
          verify: {
            command: auditVerify.command,
            exit_code: auditVerify.exit_code,
            duration_seconds: auditVerify.duration_seconds,
            stdout_tail: auditVerify.stdout_tail,
            stderr_tail: auditVerify.stderr_tail,
            verified: auditVerify.verified,
          },
          verified: auditVerify.verified,
          status_after: goal.status,
          next_action: auditVerify.verified
            ? "Audit green: the recorded result still reproduces."
            : "Audit red: the recorded result no longer reproduces. " +
              "Evidence marked stale; investigate before trusting this outcome.",
        };
        appendJsonl(outcomeIterationsPath(slug), auditRecord);
        goal.evidence_stale = !auditVerify.verified;
        goal.last_audit = stamp;
        goal.updated = stamp;
        saveOutcome(slug, goal);
        appendJsonl(verificationsPath(), {
          kind: "executed",
          claim: `Outcome ${slug} audit: ${goal.success_criteria || ""}`,
          command: goal.verify_command,
          exit_code: auditVerify.exit_code,
          duration_seconds: auditVerify.duration_seconds,
          stdout_tail: auditVerify.stdout_tail,
          stderr_tail: auditVerify.stderr_tail,
          verified: auditVerify.verified,
          timestamp: stamp,
          outcome: slug,
          audit: true,
          provenance: currentVerificationProvenanceForStateDir(
            path.dirname(verificationsPath())
          ),
          ...verificationStepContext(),
        });
        if (format === "json") {
          const prefix = auditVerify.verified ? "[OK]" : "[FAIL]";
          return `${prefix} ${JSON.stringify({ goal, record: auditRecord }, null, 2)}`;
        }
        const prefix = auditVerify.verified ? "[OK]" : "[FAIL]";
        return [
          `${prefix} Outcome ${slug} audit: verify exit ${auditVerify.exit_code} against recorded status ${goal.status}.`,
          `next: ${auditRecord.next_action}`,
        ].join("\n");
      }
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
      const metricFloor = goal.metric_floor === undefined ? null : goal.metric_floor;
      const floorUnmet =
        metricFloor !== null && (metricScore === null || metricScore < Number(metricFloor));
      const bestBefore = goal.best_metric_score;
      const metricRegressed =
        metricScore !== null &&
        bestBefore !== null &&
        bestBefore !== undefined &&
        metricScore < bestBefore;
      const frozen = Array.isArray(goal.frozen_paths) ? goal.frozen_paths : [];
      const frozenHits = frozen.length > 0 ? frozenPathViolations(changedPaths(), frozen) : [];
      const verified = Boolean(
        verify.verified && metricOk && !floorUnmet && frozenHits.length === 0
      );
      const violations = scopeViolations(goal.allowed_paths || []);
      const nextIteration = iterationCount + 1;
      let statusAfter;
      let nextAction;
      if (frozenHits.length > 0) {
        statusAfter = "stopped";
        nextAction =
          "Frozen-path violation detected: the loop changed paths it must never touch " +
          `(${frozenHits.slice(0, 5).join(", ")}). Stop and revert them.`;
      } else if (verified) {
        statusAfter = "succeeded";
        nextAction = "Outcome met. Report the evidence and stop.";
        if (nextIteration === 1) {
          nextAction +=
            " Caution: the verifier passed before any recorded failed attempt; " +
            "confirm the verifier can fail.";
        }
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
      if (floorUnmet && verify.verified && statusAfter === "active") {
        nextAction = `Metric floor not met (score ${metricScore}, floor ${metricFloor}). ${nextAction}`;
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
        metric_regressed: metricRegressed,
        metric_floor_unmet: floorUnmet,
        verified,
        scope_violations: violations,
        frozen_violations: frozenHits,
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
      if (statusAfter === "stopped" && frozenHits.length > 0) {
        goal.stop_reason = `frozen-path violation: ${frozenHits.slice(0, 5).join(", ")}`;
      }
      if (statusAfter === "succeeded") {
        goal.stop_reason = "success criteria verified";
      }
      saveOutcome(slug, goal);
      let combinedExitCode = verify.verified && metricRecord && !metricOk
        ? metricRecord.exit_code
        : verify.exit_code;
      const combinedDuration = verify.duration_seconds +
        (metricRecord ? metricRecord.duration_seconds : 0);
      let verificationStderr = verify.stderr_tail;
      if (frozenHits.length > 0) {
        combinedExitCode = -1;
        verificationStderr =
          verificationStderr +
          (verificationStderr ? "\n" : "") +
          `(frozen-path violation: ${frozenHits.slice(0, 5).join(", ")})`;
      }
      appendJsonl(verificationsPath(), {
        kind: "executed",
        claim: `Outcome ${slug}: ${goal.success_criteria || ""}`,
        command: goal.verify_command,
        exit_code: combinedExitCode,
        duration_seconds: combinedDuration,
        stdout_tail: verify.stdout_tail,
        stderr_tail: verificationStderr,
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
