import { z } from "zod";
import path from "node:path";
import crypto from "node:crypto";

import { currentVerificationProvenanceForStateDir } from "./verification-provenance.js";

export const VERIFICATION_TOOL_NAMES = [
  "verify_run",
  "verify_claim",
  "reflect",
];

function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerVerificationTools requires deps.${name}`);
  }
  return value;
}

const TEST_COUNT_PATTERNS = [
  /^Ran\s+(\d+)\s+tests?\b/gm,
  /^#\s*tests\s+(\d+)\s*$/gm,
  /(?:^|\s)(\d+)\s+passed(?:\s|,|$)/gm,
  /^Tests:\s+.*?\b(\d+)\s+total\b/gm,
];

function extractTestCount(...texts) {
  const combined = texts.map((text) => String(text || "")).join("\n");
  for (const pattern of TEST_COUNT_PATTERNS) {
    const matches = [...combined.matchAll(pattern)];
    if (matches.length > 0) return Number.parseInt(matches.at(-1)[1], 10);
  }
  return null;
}

export function registerVerificationTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const runShellCapture = requireDep(deps, "runShellCapture");
  const isoNow = requireDep(deps, "isoNow");
  const verificationStepContext = requireDep(deps, "verificationStepContext");
  const appendJsonl = requireDep(deps, "appendJsonl");
  const verificationsPath = requireDep(deps, "verificationsPath");
  const reflectionsPath = requireDep(deps, "reflectionsPath");
  const recordLesson = requireDep(deps, "recordLesson");
  const captureLineage = requireDep(deps, "captureLineage");

  server.registerTool(
    "verify_run",
    {
      title: "Run a command as executed verification",
      description:
        "Execute a shell command, record the exit code, duration, and output tails as an executed verification, and return a VERIFIED or UNVERIFIED verdict. " +
        "Use this to ground every completion claim in machine-checked evidence (tests, builds, linters, curl, file checks). Executed verification always beats self-reported attestation.",
      inputSchema: {
        command: z.string().describe("Shell command to execute."),
        claim: z.string().optional().describe("The claim this command verifies; shown in the verdict."),
        timeout_seconds: z
          .number()
          .positive()
          .default(300)
          .describe("Kill the command after this many seconds. Defaults to 300."),
        output: z.enum(["compact", "full"]).optional().describe("Compact verdict or retained full output."),
        parents: z.array(z.object({ kind: z.string(), id: z.string() })).optional().describe("Typed parent artifacts captured with this run."),
      },
    },
    guarded(({ command, claim, timeout_seconds, output = "compact", parents }) => {
      if (process.env.MYTHIFY_DISABLE_RUN === "1") {
        return (
          "[FAIL] verify_run is disabled: the server environment sets MYTHIFY_DISABLE_RUN=1. " +
          "No command was executed and nothing was recorded. " +
          "Unset MYTHIFY_DISABLE_RUN to enable command execution, or use verify_claim to record a self-reported attestation."
        );
      }
      const timeoutSeconds = timeout_seconds || 300;
      let lineage = null;
      if (parents && parents.length > 0) {
        try { lineage = captureLineage(path.dirname(verificationsPath()), parents, isoNow); }
        catch (error) { return `[FAIL] Invalid lineage: ${error.message}.`; }
      }
      const verificationId = `v-${isoNow().replace(/[^0-9]/g, "").slice(0, 17)}-${crypto.randomUUID().slice(0, 12)}`;
      const stateDir = path.dirname(verificationsPath());
      const artifactDir = path.join(stateDir, "verification-artifacts", verificationId);
      const run = runShellCapture(command, timeoutSeconds, artifactDir);
      const stdoutTail = run.stdout_tail;
      const stderrTail = run.stderr_tail;
      const exitCode = run.exit_code;
      const verified = run.verified;
      const record = {
        id: verificationId,
        kind: "executed",
        claim: claim !== undefined && claim !== null ? claim : null,
        command,
        exit_code: exitCode,
        duration_seconds: run.duration_seconds,
        stdout_tail: stdoutTail,
        stderr_tail: stderrTail,
        verified,
        timestamp: isoNow(),
        provenance: currentVerificationProvenanceForStateDir(
          path.dirname(verificationsPath())
        ),
        ...verificationStepContext(),
      };
      if (run.artifacts) {
        record.artifacts = Object.fromEntries(
          Object.entries(run.artifacts).map(([channel, item]) => [
            channel,
            { ...item, path: path.relative(stateDir, item.path) },
          ])
        );
      }
      if (run.artifact_error) record.artifact_error = run.artifact_error;
      const testCount = extractTestCount(run.stdout_full, run.stderr_full, stdoutTail, stderrTail);
      if (testCount !== null) record.test_count = testCount;
      if (lineage) record.lineage = lineage;
      appendJsonl(verificationsPath(), record);
      const label = record.claim !== null ? record.claim : command;
      const countLabel = Number.isInteger(record.test_count) ? `, ${record.test_count} tests` : "";
      const timing = `(exit ${exitCode}, ${run.duration_seconds.toFixed(2)}s${countLabel})`;
      if (verified) {
        const lines = [`[OK] VERIFIED: ${label} ${timing}`];
        if (output === "full") {
          lines.push("--- stdout (full artifact) ---", run.stdout_full || "");
          lines.push("--- stderr (full artifact) ---", run.stderr_full || "");
        }
        return lines.join("\n");
      }
      const lines = [`[FAIL] UNVERIFIED: ${label} ${timing}`];
      if (stdoutTail !== "") {
        lines.push("--- stdout (tail) ---");
        lines.push(stdoutTail);
      }
      if (stderrTail !== "") {
        lines.push("--- stderr (tail) ---");
        lines.push(stderrTail);
      }
      const artifactPaths = Object.values(record.artifacts || {}).map((item) => item.path);
      if (artifactPaths.length > 0) lines.push(`Artifacts: ${artifactPaths.join(", ")}`);
      if (output === "full") {
        lines.push("--- stdout (full artifact) ---", run.stdout_full || "");
        lines.push("--- stderr (full artifact) ---", run.stderr_full || "");
      }
      return lines.join("\n");
    })
  );

  server.registerTool(
    "verify_claim",
    {
      title: "Record a self-reported attestation",
      description:
        "Record a claim with self-reported evidence as an attested verification entry. It is never marked verified. " +
        "Use this only when nothing executable exists to check the claim; whenever a command can check it, use verify_run instead.",
      inputSchema: {
        claim: z.string().describe("The claim being attested."),
        evidence: z.string().describe("The self-reported evidence supporting the claim."),
      },
    },
    guarded(({ claim, evidence }) => {
      const record = {
        kind: "attested",
        claim,
        evidence,
        verified: null,
        timestamp: isoNow(),
        ...verificationStepContext(),
      };
      appendJsonl(verificationsPath(), record);
      return `[WARN] ATTESTED: ${claim} (self-reported, not machine-checked; prefer verify run)`;
    })
  );

  server.registerTool(
    "reflect",
    {
      title: "Record a structured reflection",
      description:
        "Record a structured reflection: what was done, the outcome, what was observed, the root cause when known, and the next action. A provided lesson is auto-recorded as a project lesson tagged auto-reflected. " +
        "Use this after each significant action or failure, so course corrections are grounded in recorded observations rather than guesswork.",
      inputSchema: {
        action_taken: z.string().describe("What was just attempted."),
        outcome: z.enum(["success", "partial", "failure"]).describe("How it went: success, partial, or failure."),
        observation: z.string().describe("What was actually observed (output, behavior, evidence)."),
        root_cause: z.string().optional().describe("Root cause of a partial or failed outcome, when known."),
        next_action: z.string().describe("The concrete next action to take."),
        lesson: z.string().optional().describe("Optional reusable lesson; auto-recorded to the project lesson store."),
      },
    },
    guarded(({ action_taken, outcome, observation, root_cause, next_action, lesson }) => {
      const record = {
        action: action_taken,
        outcome,
        observation,
        root_cause: root_cause !== undefined && root_cause !== null ? root_cause : null,
        next: next_action,
        lesson: lesson !== undefined && lesson !== null ? lesson : null,
        timestamp: isoNow(),
      };
      appendJsonl(reflectionsPath(), record);
      const lines = [
        `[OK] Reflection recorded (outcome: ${outcome}).`,
        `Next action: ${next_action}`,
      ];
      if (record.lesson !== null && record.lesson.trim() !== "") {
        const detail = `Auto-recorded from a reflection (outcome: ${record.outcome}). Action: ${record.action}`;
        const fileName = recordLesson(record.lesson, detail, ["auto-reflected"], "project");
        lines.push(`Lesson auto-recorded as a project lesson tagged auto-reflected (${fileName}).`);
      }
      return lines.join("\n");
    })
  );
}
