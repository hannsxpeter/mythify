import { z } from "zod";

const REPORT_SINCE_MODES = ["last", "start"];
const REPORT_FORMATS = ["chat", "json"];
const DEFAULT_REPORT_RECENT = 8;

export const VIEW_TOOL_NAMES = [
  "workflow_status",
  "verification_history",
  "work_report",
  "background_status",
  "outcome_progress",
  "release_readiness",
  "fanout_timeline",
  "phase_status",
];

function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerViewTools requires deps.${name}`);
  }
  return value;
}

export function registerViewTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const buildWorkflowDashboard = requireDep(deps, "buildWorkflowDashboard");
  const formatWorkflowDashboard = requireDep(deps, "formatWorkflowDashboard");
  const buildVerificationHistoryView = requireDep(deps, "buildVerificationHistoryView");
  const formatVerificationHistoryView = requireDep(deps, "formatVerificationHistoryView");
  const buildWorkReport = requireDep(deps, "buildWorkReport");
  const formatWorkReport = requireDep(deps, "formatWorkReport");
  const buildBackgroundView = requireDep(deps, "buildBackgroundView");
  const formatBackgroundView = requireDep(deps, "formatBackgroundView");
  const buildOutcomeProgressView = requireDep(deps, "buildOutcomeProgressView");
  const formatOutcomeProgressView = requireDep(deps, "formatOutcomeProgressView");
  const buildReleaseReadinessView = requireDep(deps, "buildReleaseReadinessView");
  const formatReleaseReadinessView = requireDep(deps, "formatReleaseReadinessView");
  const buildFanoutTimelineView = requireDep(deps, "buildFanoutTimelineView");
  const formatFanoutTimelineView = requireDep(deps, "formatFanoutTimelineView");
  const buildPhaseView = requireDep(deps, "buildPhaseView");
  const formatPhaseView = requireDep(deps, "formatPhaseView");

  server.registerTool(
    "workflow_status",
    {
      title: "Show workflow dashboard",
      description:
        "Show a read-only dashboard of the active plan, current step, next step, active outcome, evidence counts, recent verification records, and recent reflections. " +
        "Use this to orient without mutating state or treating model confidence as evidence.",
      inputSchema: {
        recent: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe("Number of recent verification and reflection records to include. Defaults to 3."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ recent, format }) => {
      const dashboard = buildWorkflowDashboard(typeof recent === "number" ? recent : 3);
      if (format === "json") {
        return `[OK] ${JSON.stringify(dashboard, null, 2)}`;
      }
      return formatWorkflowDashboard(dashboard);
    })
  );

  server.registerTool(
    "verification_history",
    {
      title: "Show verification history",
      description:
        "Show a read-only history of executed and attested verification records, including verdict, command or evidence, exit code, duration, and plan or step context. " +
        "Use this to inspect recorded evidence without rerunning checks or upgrading self-reported attestations.",
      inputSchema: {
        recent: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe("Number of recent verification records to include. Defaults to 10."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ recent, format }) => {
      const view = buildVerificationHistoryView(typeof recent === "number" ? recent : 10);
      if (format === "json") {
        return `[OK] ${JSON.stringify(view, null, 2)}`;
      }
      return formatVerificationHistoryView(view);
    })
  );

  server.registerTool(
    "work_report",
    {
      title: "Show chat-ready work report",
      description:
        "Show a chat-ready live work report from durable Mythify events: plan creation, step updates, verification records, and reflections. " +
        "Use this during multi-step work to narrate what happened since the last report; set peek true to avoid advancing the cursor.",
      inputSchema: {
        since: z
          .enum(REPORT_SINCE_MODES)
          .optional()
          .describe("Report events since the last cursor or from the start. Defaults to last."),
        recent: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe(`Maximum events to include. Defaults to ${DEFAULT_REPORT_RECENT}.`),
        cursor: z.string().optional().describe("Report cursor name. Defaults to default."),
        peek: z.boolean().optional().describe("When true, leave the report cursor unchanged."),
        mark: z
          .boolean()
          .optional()
          .describe("When true, advance the cursor to the latest event without returning old events."),
        format: z.enum(REPORT_FORMATS).optional().describe("Return chat text or JSON. Defaults to chat."),
      },
    },
    guarded(({ since, recent, cursor, peek, mark, format }) => {
      if (mark && typeof since === "string") {
        return "[FAIL] mark cannot be combined with since. Use mark to set a cursor, then call work_report with since last to read new events.";
      }
      const view = buildWorkReport({
        since: since || "last",
        recent: typeof recent === "number" ? recent : DEFAULT_REPORT_RECENT,
        cursor: cursor || "default",
        peek: Boolean(peek),
        mark: Boolean(mark),
      });
      if (view.error) {
        return view.error;
      }
      if (format === "json") {
        return `[OK] ${JSON.stringify({ ...view, format: "json" }, null, 2)}`;
      }
      return formatWorkReport(view);
    })
  );

  server.registerTool(
    "background_status",
    {
      title: "Show background task state",
      description:
        "Show a read-only background task view of durable outcome loops and fanout jobs, including task counts, statuses, and next actions. " +
        "Use this to orient on long-running delegated work without mutating state or treating model confidence as progress.",
      inputSchema: {
        recent: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe("Number of recent outcomes and fanout jobs to include. Defaults to 5."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ recent, format }) => {
      const view = buildBackgroundView(typeof recent === "number" ? recent : 5);
      if (format === "json") {
        return `[OK] ${JSON.stringify(view, null, 2)}`;
      }
      return formatBackgroundView(view);
    })
  );

  server.registerTool(
    "outcome_progress",
    {
      title: "Show outcome loop progress",
      description:
        "Show a read-only progress view of active and recent outcome loops, including iteration budget, verifier exit details, metric score when present, and next action from durable state. " +
        "Use this to inspect verifier-backed outcome progress without running checks, making attempts, stopping loops, or treating notes as verification.",
      inputSchema: {
        recent: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe("Number of recent outcomes to include. Defaults to 5."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ recent, format }) => {
      const view = buildOutcomeProgressView(typeof recent === "number" ? recent : 5);
      if (format === "json") {
        return `[OK] ${JSON.stringify(view, null, 2)}`;
      }
      return formatOutcomeProgressView(view);
    })
  );

  server.registerTool(
    "release_readiness",
    {
      title: "Show release readiness",
      description:
        "Show a read-only release readiness view from recorded verification gates, project git state, and roadmap state. " +
        "Use this before tagging or publishing to see which expected gates have recorded evidence without rerunning gates or declaring the release safe.",
      inputSchema: {
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ format }) => {
      const view = buildReleaseReadinessView();
      if (format === "json") {
        return `[OK] ${JSON.stringify(view, null, 2)}`;
      }
      return formatReleaseReadinessView(view);
    })
  );

  server.registerTool(
    "fanout_timeline",
    {
      title: "Show fanout worker timeline",
      description:
        "Show a read-only timeline of fanout worker job creation, task starts, task finishes, duration, status, errors, and output metadata. " +
        "Use this to inspect durable delegated-worker history without mutating state or treating worker output as verification evidence.",
      inputSchema: {
        recent: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe("Number of recent fanout jobs to include. Defaults to 5."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ recent, format }) => {
      const view = buildFanoutTimelineView(typeof recent === "number" ? recent : 5);
      if (format === "json") {
        return `[OK] ${JSON.stringify(view, null, 2)}`;
      }
      return formatFanoutTimelineView(view);
    })
  );

  server.registerTool(
    "phase_status",
    {
      title: "Show workflow phase state",
      description:
        "Show a read-only Understand, Design, Build, Judge, Verify phase view of active plan steps and supporting durable evidence counts. " +
        "Use this to orient on where the current work sits without mutating state or treating model confidence as evidence.",
      inputSchema: {
        recent: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe("Number of recent verification and reflection records to consider. Defaults to 3."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ recent, format }) => {
      const view = buildPhaseView(typeof recent === "number" ? recent : 3);
      if (format === "json") {
        return `[OK] ${JSON.stringify(view, null, 2)}`;
      }
      return formatPhaseView(view);
    })
  );
}
