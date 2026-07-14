import { z } from "zod";
import {
  EFFORT_LEVELS,
  ENGINES,
  FANOUT_VISIBILITY_MODES,
  SPAWN_CEILINGS,
  SPEED_LEVELS,
  TASK_ROLES,
} from "./fanout-policy.js";

const FRONT_DOOR_NOTE =
  " For broad or ambiguous user prompts, call workflow_route first; use fanout_start directly only after the route or plan identifies independent parallel subtasks.";

export function registerFanoutToolHandlers(server, deps, handlers) {
  const { handleFanoutStart, handleFanoutStatus, handleFanoutResults } = handlers;

  server.registerTool(
    "fanout_start",
    {
      title: "Start a parallel delegation job",
      description:
        "Start a one-shot parallel delegation job: declare a list of tasks once and the server spawns, sequences, and collects background workers for you, returning a job id immediately. " +
        "Every task MUST be fully independent and self-contained: each one is a fresh model invocation with no memory of this conversation and no access to other tasks' outputs, and each one costs real money or subscription quota. " +
        "Visibility defaults to summary, can be quiet, verbose, or threaded, and can be inferred from the purpose or task prompts when set to auto. Threaded means request visible host threads only when the host supports them. " +
        "When workflow_route recommends its native UltraCode adapter, pass engine claude-ultracode with exactly one task. Mythify then launches one Claude dynamic workflow, monitors it through fanout_status, and ingests its final material through fanout_results. " +
        "Use this to parallelize independent subtasks (drafting sections, analyzing separate files, generating variants) during long work; afterwards merge the results yourself and verify the merged work with verify_run, because fanout results are material, not verification." +
        FRONT_DOOR_NOTE,
      inputSchema: {
        tasks: z
          .array(
            z.object({
              title: z.string().describe("Short task label shown in status and results."),
              prompt: z
                .string()
                .describe(
                  "The full self-contained instruction for this worker. The worker sees only this prompt plus any context_paths content; include everything it needs."
                ),
              context_paths: z
                .array(z.string())
                .optional()
                .describe(
                  "Files to inline into the worker prompt as labeled fenced blocks. Relative paths resolve against the project root (the parent of .mythify); absolute paths are allowed only when they resolve inside that root. Total inlined context per task is capped at MYTHIFY_FANOUT_CONTEXT_BYTES."
                ),
              role: z
                .enum(TASK_ROLES)
                .optional()
                .describe(
                  "Task role for model ceiling policy: worker by default, or reviewer for independent review tasks."
                ),
              isolation: z
                .enum(["none", "worktree"])
                .optional()
                .describe(
                  "Set worktree to run this writing worker in its own git worktree on a fresh branch so parallel writers cannot collide. A worker that changed files leaves its branch for the host to merge; one that changed nothing is cleaned up. Falls back to the shared project root off git."
                ),
              model: z
                .string()
                .optional()
                .describe("Per-task model override; beats the job model and MYTHIFY_FANOUT_MODEL."),
              engine: z
                .string()
                .optional()
                .describe(
                  "Per-task engine override (claude-cli, claude-ultracode, codex-cli, cursor-agent, anthropic, openai, or command); beats the job engine and MYTHIFY_FANOUT_ENGINE. claude-ultracode requires exactly one task in the job and invokes native Claude workflow orchestration."
                ),
              effort: z
                .enum(EFFORT_LEVELS)
                .optional()
                .describe(
                  "Per-task effort override: auto, low, medium, or high. Beats the job effort and MYTHIFY_FANOUT_EFFORT. claude-ultracode ignores this field and forces native ultracode effort."
                ),
              speed: z
                .enum(SPEED_LEVELS)
                .optional()
                .describe(
                  "Per-task speed override: auto, standard, or fast. Beats the job speed and MYTHIFY_FANOUT_SPEED."
                ),
            })
          )
          .describe(
            "1 to MYTHIFY_FANOUT_MAX_TASKS fully independent tasks. claude-ultracode requires exactly one task because that task becomes the objective for one native dynamic workflow. Each task is a fresh model call that costs real money or subscription quota."
          ),
        purpose: z
          .string()
          .optional()
          .describe(
            "Optional original user request or reason for spawning workers. Used only to infer visibility when visibility is auto or omitted."
          ),
        model: z
          .string()
          .optional()
          .describe("Default model for every task; per-task model overrides it."),
        engine: z
          .string()
          .optional()
          .describe(
            "Default engine for every task (claude-cli, claude-ultracode, codex-cli, cursor-agent, anthropic, openai, or command); per-task engine overrides it. Omit to use codex-cli when available, then other detected engines. claude-ultracode requires Claude Code 2.1.203 or newer and exactly one task."
          ),
        effort: z
          .enum(EFFORT_LEVELS)
          .optional()
          .describe(
            "Default effort for every task: auto, low, medium, or high. Per-task effort overrides it. Defaults to MYTHIFY_FANOUT_EFFORT or a model-derived default. claude-ultracode forces native ultracode effort."
          ),
        speed: z
          .enum(SPEED_LEVELS)
          .optional()
          .describe(
            "Default speed for every task: auto, standard, or fast. Per-task speed overrides it. Auto preserves platform defaults; fast enables Codex fast mode where supported."
          ),
        visibility: z
          .enum(FANOUT_VISIBILITY_MODES)
          .optional()
          .describe(
            "How much worker activity the host should surface in the user chat: auto, quiet, summary, verbose, or threaded. Omit for auto inference, which defaults to summary unless the purpose or task prompts ask otherwise."
          ),
        session_model: z
          .string()
          .optional()
          .describe(
            "Current host session model used to enforce spawn_ceiling. Defaults to MYTHIFY_SESSION_MODEL."
          ),
        spawn_ceiling: z
          .enum(SPAWN_CEILINGS)
          .optional()
          .describe(
            "Maximum spawned model tier relative to session_model: auto, lower_only, same_or_lower, or allow_stronger. Defaults to MYTHIFY_SPAWN_CEILING or same_or_lower."
          ),
        reviewer_allow_stronger: z
          .boolean()
          .optional()
          .describe(
            'Reviewer-only opt-in that permits tasks with role: "reviewer" to exceed session_model under same_or_lower. It does not affect worker tasks or lower_only.'
          ),
        hosted_provider_billing_ack: z
          .boolean()
          .optional()
          .describe(
            "Required true before any anthropic or openai fanout task can run, acknowledging hosted providers can bill a metered external account."
          ),
        hosted_provider_data_ack: z
          .boolean()
          .optional()
          .describe(
            "Required true before any anthropic or openai fanout task can run, acknowledging prompts and inlined context are sent to a remote provider."
          ),
        hosted_provider_material_ack: z
          .boolean()
          .optional()
          .describe(
            "Required true before any anthropic or openai fanout task can run, acknowledging provider output is material and not verification evidence."
          ),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe(
            "Per-worker timeout in seconds; a worker is killed and its task fails on expiry. Defaults to MYTHIFY_FANOUT_TIMEOUT_SECONDS (600)."
          ),
      },
    },
    deps.guarded(handleFanoutStart)
  );

  server.registerTool(
    "fanout_status",
    {
      title: "Show fanout job status",
      description:
        "Show a fanout job's progress: per-task status icons with engine, model, model tier, effort, speed, and elapsed time, plus overall counts. Defaults to the most recent job. " +
        "Use this after fanout_start to monitor the background workers and to decide when fanout_results is worth calling. " +
        "For claude-ultracode jobs, this is the native adapter monitoring surface and reports workflow mode plus elapsed runtime. " +
        "If the server restarted since the job was started, its unfinished tasks are reported as interrupted, because background workers die with the server process.",
      inputSchema: {
        job_id: z.string().optional().describe("The job id from fanout_start; omit for the most recent job."),
      },
    },
    deps.guarded(handleFanoutStatus)
  );

  server.registerTool(
    "fanout_results",
    {
      title: "Collect fanout job results",
      description:
        "Return the outputs of a fanout job's completed and failed tasks (failures include the error and any remediation), optionally limited to one task by id. Defaults to the most recent job. " +
        "Per-task text is capped at 20000 characters with a pointer to the task output file on disk; tasks still running are flagged with a warning. " +
        "For claude-ultracode jobs, this ingests the native workflow's final response while keeping it explicitly outside Mythify's executable verification evidence. " +
        "Use this once fanout_status shows tasks finished, then merge the material and verify the merged work with verify_run.",
      inputSchema: {
        job_id: z.string().optional().describe("The job id from fanout_start; omit for the most recent job."),
        task_id: z.number().int().optional().describe("Return only this task's result; omit for all finished tasks."),
      },
    },
    deps.guarded(handleFanoutResults)
  );
}
