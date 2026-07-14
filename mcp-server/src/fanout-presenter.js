import { visibilityGuidance } from "./fanout-policy.js";

const TASK_STATUS_ICONS = {
  pending: "[ ]",
  running: "[>]",
  completed: "[x]",
  failed: "[!]",
  interrupted: "[~]",
};

export function formatFanoutStatus(job, interruptedNote) {
  const counts = { pending: 0, running: 0, completed: 0, failed: 0, interrupted: 0 };
  for (const task of job.tasks) {
    counts[task.status] = (counts[task.status] || 0) + 1;
  }
  const lines = [
    `[OK] Fanout job ${job.id} (engine: ${job.engine}${job.model ? `, model: ${job.model}` : ""}, mode: ${job.execution_mode || "standard"}, effort: ${job.effort || "medium"}, speed: ${job.speed || "auto"}, visibility: ${job.visibility || "summary"}, ceiling ${job.spawn_ceiling || "same_or_lower"}, timeout ${job.timeout_seconds}s per worker, created ${job.created}).`,
  ];
  if (interruptedNote) {
    lines.push(interruptedNote);
  }
  lines.push(
    `Reviewer stronger opt-in: ${job.reviewer_allow_stronger ? "enabled" : "disabled"}.`
  );
  lines.push(visibilityGuidance(job.visibility || "summary"));
  lines.push(
    `Tasks: ${job.tasks.length} total; ${counts.completed} completed, ${counts.failed} failed, ${counts.running} running, ${counts.pending} pending, ${counts.interrupted} interrupted.`
  );
  if ((job.visibility || "summary") === "quiet") {
    const failedTasks = job.tasks.filter((task) => task.status === "failed" && task.error);
    for (const task of failedTasks) {
      lines.push(`[!] ${task.id}. ${task.title} failed: ${String(task.error).slice(0, 500)}`);
    }
  } else {
    for (const task of job.tasks) {
      const icon = TASK_STATUS_ICONS[task.status] || "[ ]";
      let line = `${icon} ${task.id}. ${task.title} (${task.status}; role: ${task.role || "worker"}; engine: ${task.engine}`;
      if (task.model) {
        line += `, model: ${task.model}`;
      }
      if (task.model_tier) {
        line += `, tier: ${task.model_tier}`;
      }
      if (task.effort) {
        line += `, effort: ${task.effort}`;
      }
      if (task.speed) {
        line += `, speed: ${task.speed}`;
      }
      if (task.status === "running" && task.started_at) {
        const elapsed = Math.max(0, (Date.now() - Date.parse(task.started_at)) / 1000);
        line += `, elapsed ${elapsed.toFixed(1)}s`;
      } else if (typeof task.duration_seconds === "number" && task.duration_seconds > 0) {
        line += `, ${task.duration_seconds.toFixed(1)}s`;
      }
      line += ")";
      if (task.status === "failed" && task.error) {
        line += `\n    error: ${String(task.error).slice(0, 500)}`;
      }
      lines.push(line);
    }
  }
  if (counts.pending + counts.running === 0) {
    lines.push("All tasks finished. Collect outputs with fanout_results.");
  } else {
    lines.push("Workers are still running; call fanout_status again to refresh.");
  }
  return lines.join("\n");
}
