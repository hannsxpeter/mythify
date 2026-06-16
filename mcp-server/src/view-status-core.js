import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

let deps = {};

export function configureViewStatusCore(nextDeps) {
  deps = nextDeps;
}

function requireDep(name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`view-status-core requires deps.${name}`);
  }
  return value;
}

function resolveStateDir() { return requireDep("resolveStateDir")(); }
function readJsonl(filePath) { return requireDep("readJsonl")(filePath); }
function verificationsPath() { return requireDep("verificationsPath")(); }
function listFanoutSummaries() { return requireDep("listFanoutSummaries")(); }
function buildWorkflowDashboard(recent) { return requireDep("buildWorkflowDashboard")(recent); }
function buildBackgroundView(recent) { return requireDep("buildBackgroundView")(recent); }
function countStatuses(items, statuses) { return requireDep("countStatuses")(items, statuses); }
function compactLabel(text, fallback) { return requireDep("compactLabel")(text, fallback); }

function containsAny(text, needles) {
  const lower = String(text || "").toLowerCase();
  return needles.filter((needle) => lower.includes(String(needle).toLowerCase()));
}

export const RELEASE_READINESS_GATES = [
  {
    id: "python_tests",
    label: "Python test suite",
    required: true,
    sources: ["tests/"],
    match_any: ["python3 -m unittest discover -s tests", "Python suite passes"],
  },
  {
    id: "node_mcp_tests",
    label: "Node MCP suite",
    required: true,
    sources: ["mcp-server/test/"],
    match_any: ["npm test --prefix mcp-server", "Node MCP suite passes"],
  },
  {
    id: "surface_manifest",
    label: "Surface manifest check",
    required: true,
    sources: ["protocol/surface-manifest.json", "scripts/check_surface_manifest.mjs"],
    match_any: ["node scripts/check_surface_manifest.mjs", "surface manifest"],
  },
  {
    id: "classification_rules_manifest",
    label: "Classification rules manifest check",
    required: true,
    sources: [
      "protocol/classification-rules.json",
      "mcp-server/protocol/classification-rules.json",
      "scripts/check_classification_rules_manifest.mjs",
    ],
    match_any: ["node scripts/check_classification_rules_manifest.mjs", "classification rules manifest"],
  },
  {
    id: "registry_docs",
    label: "Generated registry docs check",
    required: true,
    sources: ["scripts/build_registry_docs.mjs", "docs/adapter-candidates.md"],
    match_any: ["node scripts/build_registry_docs.mjs --check", "registry docs", "generated docs"],
  },
  {
    id: "protocol_check",
    label: "Protocol variants check",
    required: true,
    sources: ["protocol/PROTOCOL.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"],
    match_any: ["python3 scripts/mythify.py protocol check", "protocol check"],
  },
  {
    id: "variant_idempotence",
    label: "Generated variants idempotence",
    required: true,
    sources: ["scripts/build_variants.py", "AGENTS.md", "CLAUDE.md", ".cursorrules"],
    match_any: ["scripts/build_variants.py", "generated variants", "variant idempotence"],
  },
  {
    id: "whitespace",
    label: "Whitespace check",
    required: true,
    sources: ["git diff --check"],
    match_any: ["git diff --check", "whitespace"],
  },
  {
    id: "forbidden_dash_scan",
    label: "Forbidden dash scan",
    required: true,
    sources: ["AGENTS.md", "docs/design.md"],
    match_any: ["forbidden dash", "dash scan"],
  },
  {
    id: "emoji_scan",
    label: "Emoji scan",
    required: true,
    sources: ["AGENTS.md", "docs/design.md"],
    match_any: ["emoji scan", "emoji-like"],
  },
];

export const RELEASE_READINESS_ICONS = {
  passed: "[x]",
  failed: "[!]",
  missing: "[ ]",
  unknown: "[~]",
  clean: "[x]",
  dirty: "[!]",
  present: "[x]",
};

export function projectRootFromState(stateDir) {
  return path.basename(stateDir) === ".mythify" ? path.dirname(stateDir) : process.cwd();
}

export function verificationSearchText(record) {
  return ["claim", "command", "stdout_tail", "stderr_tail"]
    .map((key) => String(record[key] || ""))
    .join("\n")
    .toLowerCase();
}

export function latestMatchingVerification(records, gate) {
  const needles = gate.match_any.map((item) => item.toLowerCase());
  const matches = records.filter(
    (record) =>
      record.kind === "executed" &&
      needles.some((needle) => verificationSearchText(record).includes(needle))
  );
  return matches.length > 0 ? matches[matches.length - 1] : null;
}

export function summarizeReleaseGate(gate, records) {
  const record = latestMatchingVerification(records, gate);
  const status = record ? (record.verified === true ? "passed" : "failed") : "missing";
  return {
    id: gate.id,
    label: gate.label,
    required: gate.required,
    sources: [...gate.sources],
    status,
    latest_record: record
      ? {
          timestamp: record.timestamp || "",
          claim: record.claim,
          command: record.command || "",
          exit_code: record.exit_code,
          verified: record.verified,
          plan: record.plan,
          step_id: record.step_id,
        }
      : null,
  };
}

export function gitStatusSummary(root) {
  const result = spawnSync("git", ["--no-optional-locks", "status", "--short", "--branch"], {
    cwd: root,
    encoding: "utf8",
    timeout: 10000,
    env: { ...process.env, GIT_OPTIONAL_LOCKS: "0" },
  });
  if (result.error) {
    return {
      status: "unknown",
      branch: "",
      clean: null,
      detail: result.error.message,
    };
  }
  const output = result.stdout || "";
  if (result.status !== 0) {
    return {
      status: "unknown",
      branch: "",
      clean: null,
      detail: String(result.stderr || output || "git status failed").trim(),
    };
  }
  const lines = output.split(/\r?\n/).filter((line) => line.trim() !== "");
  const branch = lines.length > 0 && lines[0].startsWith("## ") ? lines[0].slice(3).trim() : "";
  const changedPaths = lines.filter((line) => !line.startsWith("## "));
  const clean = changedPaths.length === 0;
  return {
    status: clean ? "clean" : "dirty",
    branch,
    clean,
    detail: clean ? "working tree clean" : `${changedPaths.length} changed paths`,
    changed_paths: changedPaths.slice(0, 20),
  };
}

export function roadmapSummary(root) {
  const roadmapPath = path.join(root, "roadmap.md");
  if (!fs.existsSync(roadmapPath)) {
    return {
      status: "unknown",
      path: roadmapPath,
      active_now: "",
      detail: "roadmap.md not found",
    };
  }
  const text = fs.readFileSync(roadmapPath, "utf8");
  const match = text.match(/^## Active Now\n\n([\s\S]*?)(?:\n## |\n?$)/m);
  let activeNow = "";
  if (match) {
    activeNow = (match[1].split(/\r?\n/).find((line) => line.trim().startsWith("- [")) || "").trim();
  }
  return {
    status: activeNow ? "present" : "unknown",
    path: roadmapPath,
    active_now: activeNow,
    detail: activeNow ? "active slice found" : "no active slice found",
  };
}

export function releaseReadinessStatus(gates, gitState) {
  const failed = gates.filter((gate) => gate.status === "failed").length;
  const missing = gates.filter((gate) => gate.status === "missing").length;
  if (failed > 0 || gitState.status === "dirty") {
    return "blocked";
  }
  if (missing > 0) {
    return "needs_evidence";
  }
  if (gitState.status === "unknown") {
    return "needs_review";
  }
  return "ready_for_release_review";
}

export function buildReleaseReadinessView() {
  const stateDir = resolveStateDir();
  const records = readJsonl(verificationsPath());
  const gates = RELEASE_READINESS_GATES.map((gate) => summarizeReleaseGate(gate, records));
  const root = projectRootFromState(stateDir);
  const gitState = gitStatusSummary(root);
  const roadmap = roadmapSummary(root);
  const counts = countStatuses(gates, ["passed", "failed", "missing", "unknown"]);
  return {
    state_dir: stateDir,
    project_root: root,
    status: releaseReadinessStatus(gates, gitState),
    gates,
    counts: { total: gates.length, ...counts },
    project_state: {
      git: gitState,
      roadmap,
    },
    guardrail:
      "readiness summarizes recorded evidence and project state only; it does not rerun gates or declare a release safe",
  };
}

export function formatReleaseGate(row) {
  const icon = RELEASE_READINESS_ICONS[row.status] || "[ ]";
  let line = `  ${icon} ${row.label}: ${row.status}`;
  const record = row.latest_record;
  if (record) {
    line += ` (exit ${record.exit_code}, ${record.timestamp || "unknown-time"})`;
  } else {
    line += " (no recorded executed verifier)";
  }
  line += `; sources: ${row.sources.join(", ")}`;
  return line;
}

export function formatReleaseReadinessView(view) {
  const lines = [`[OK] Release readiness: ${view.state_dir}`];
  const counts = view.counts;
  lines.push(`Readiness: ${view.status}`);
  lines.push(
    `Recorded gates: ${counts.total} total; ${counts.passed || 0} passed, ` +
      `${counts.failed || 0} failed, ${counts.missing || 0} missing`
  );
  lines.push("Gates:");
  for (const gate of view.gates) {
    lines.push(formatReleaseGate(gate));
  }
  const gitState = view.project_state.git;
  const gitIcon = RELEASE_READINESS_ICONS[gitState.status] || "[~]";
  lines.push(
    `Project git: ${gitIcon} ${gitState.status}; branch=${gitState.branch || "unknown"}; ` +
      compactLabel(gitState.detail, "no detail")
  );
  const roadmap = view.project_state.roadmap;
  const roadmapIcon = RELEASE_READINESS_ICONS[roadmap.status] || "[~]";
  lines.push(
    `Roadmap: ${roadmapIcon} ${roadmap.status}; ` +
      compactLabel(roadmap.active_now, roadmap.detail)
  );
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

export const TIMELINE_EVENT_ICONS = {
  job_created: "[ ]",
  task_started: "[>]",
  task_pending: "[ ]",
  task_finished: "[x]",
  task_failed: "[!]",
  task_interrupted: "[~]",
};

export function selectedRecentFanoutJobs(fanoutJobs, recent) {
  if (recent <= 0) {
    return [];
  }
  return fanoutJobs.slice(Math.max(0, fanoutJobs.length - recent)).reverse();
}

export function timelineEventTime(job, task, event) {
  if (event === "task_started") {
    return task.started_at || job.created || "";
  }
  if (["task_finished", "task_failed", "task_interrupted"].includes(event)) {
    return task.finished_at || job.last_updated || "";
  }
  return job.created || "";
}

export function addTimelineEvent(events, job, task, event) {
  const status = task ? task.status || "pending" : job.status || "unknown";
  events.push({
    time: timelineEventTime(job, task || {}, event),
    event,
    job_id: job.id || "",
    job_purpose: job.purpose || "",
    task_id: task ? task.id : null,
    task_title: task ? task.title || "" : "",
    status,
    engine: (task ? task.engine : null) || job.engine || "",
    model: (task ? task.model : null) || job.model || "",
    duration_seconds: task ? task.duration_seconds || 0 : 0,
    error: task ? task.error || null : null,
    output_file: task ? task.output_file || null : null,
    output_bytes: task ? task.output_bytes || 0 : 0,
  });
}

export function buildFanoutTimelineEvents(job) {
  const events = [
    {
      time: job.created || "",
      event: "job_created",
      job_id: job.id || "",
      job_purpose: job.purpose || "",
      task_id: null,
      task_title: "",
      status: job.status || "unknown",
      engine: job.engine || "",
      model: job.model || "",
      duration_seconds: 0,
      error: null,
      output_file: null,
      output_bytes: 0,
    },
  ];
  for (const task of job.tasks || []) {
    const status = task.status || "pending";
    if (status === "pending" && !task.started_at) {
      addTimelineEvent(events, job, task, "task_pending");
      continue;
    }
    addTimelineEvent(events, job, task, "task_started");
    if (status === "failed") {
      addTimelineEvent(events, job, task, "task_failed");
    } else if (status === "interrupted") {
      addTimelineEvent(events, job, task, "task_interrupted");
    } else if (status === "completed") {
      addTimelineEvent(events, job, task, "task_finished");
    }
  }
  return events;
}

export function sortTimelineEvents(events) {
  return [...events].sort((left, right) => {
    const leftKey = `${left.time || "9999-12-31T23:59:59Z"}${left.job_id || ""}${left.task_id || 0}${left.event || ""}`;
    const rightKey = `${right.time || "9999-12-31T23:59:59Z"}${right.job_id || ""}${right.task_id || 0}${right.event || ""}`;
    return leftKey.localeCompare(rightKey);
  });
}

export function buildFanoutTimelineView(recent = 5) {
  const fanoutJobs = listFanoutSummaries();
  const selectedJobs = selectedRecentFanoutJobs(fanoutJobs, recent);
  const selectedIds = new Set(selectedJobs.map((job) => job.id));
  let events = [];
  for (const job of fanoutJobs) {
    if (selectedIds.has(job.id)) {
      events = events.concat(buildFanoutTimelineEvents(job));
    }
  }
  const taskCounts = {
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
    interrupted: 0,
  };
  for (const job of fanoutJobs) {
    for (const [status, count] of Object.entries(job.task_counts || {})) {
      taskCounts[status] = (taskCounts[status] || 0) + count;
    }
  }
  const jobCounts = countStatuses(fanoutJobs, ["active", "completed", "failed", "interrupted", "empty"]);
  return {
    state_dir: resolveStateDir(),
    jobs: selectedJobs,
    events: sortTimelineEvents(events),
    counts: {
      fanout_jobs: { total: fanoutJobs.length, ...jobCounts },
      fanout_tasks: taskCounts,
      timeline_events: events.length,
    },
    guardrail: "timeline summarizes durable fanout state only; worker output is material, not verification evidence",
  };
}

export function formatTimelineEvent(event) {
  const icon = TIMELINE_EVENT_ICONS[event.event] || "[ ]";
  const stamp = event.time || "unknown-time";
  const jobId = event.job_id || "unknown-job";
  if (event.event === "job_created") {
    return `  ${icon} ${stamp} ${jobId}: job created (${compactLabel(event.job_purpose, "fanout job")})`;
  }
  let detail =
    `  ${icon} ${stamp} ${jobId} task ${event.task_id}: ` +
    `${compactLabel(event.task_title, "task")} (${event.status || "unknown"}; ` +
    `engine=${event.engine || "unknown"}`;
  if (event.model) {
    detail += `; model=${event.model}`;
  }
  if (event.duration_seconds) {
    detail += `; duration=${event.duration_seconds}s`;
  }
  if (event.output_bytes) {
    detail += `; output=${event.output_bytes} bytes`;
  }
  detail += ")";
  if (event.error) {
    detail += `: ${compactLabel(event.error, "error")}`;
  }
  return detail;
}

export function formatFanoutTimelineView(view) {
  const lines = [`[OK] Fanout timeline: ${view.state_dir}`];
  const jobs = view.counts.fanout_jobs;
  const tasks = view.counts.fanout_tasks;
  lines.push(
    `Fanout jobs: ${jobs.total} total; ${jobs.active || 0} active, ` +
      `${jobs.completed || 0} completed, ${jobs.failed || 0} failed, ` +
      `${jobs.interrupted || 0} interrupted`
  );
  lines.push(
    `Fanout tasks: ${tasks.running || 0} running, ${tasks.pending || 0} pending, ` +
      `${tasks.completed || 0} completed, ${tasks.failed || 0} failed, ` +
      `${tasks.interrupted || 0} interrupted`
  );
  if (view.events.length > 0) {
    lines.push("Timeline events:");
    for (const event of view.events) {
      lines.push(formatTimelineEvent(event));
    }
  } else {
    lines.push("No fanout timeline events found.");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

export const PHASE_CONFIG = [
  {
    id: "understand",
    label: "Understand",
    keywords: [
      "understand",
      "map",
      "inspect",
      "research",
      "audit",
      "classify",
      "discover",
      "probe",
      "investigate",
      "analyze",
      "orient",
    ],
  },
  {
    id: "design",
    label: "Design",
    keywords: ["design", "plan", "spec", "contract", "architecture", "outline", "docs design"],
  },
  {
    id: "build",
    label: "Build",
    keywords: ["implement", "build", "add", "create", "update", "write", "edit", "refactor", "wire"],
  },
  {
    id: "judge",
    label: "Judge",
    keywords: ["judge", "review", "evaluate", "assess", "reflect", "decide"],
  },
  {
    id: "verify",
    label: "Verify",
    keywords: ["verify", "test", "check", "gate", "lint", "suite"],
  },
];

export const PHASE_STATUS_ICONS = {
  empty: "[ ]",
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

export function phaseIdForStep(step) {
  const title = step.title || "";
  for (const phase of PHASE_CONFIG) {
    if (containsAny(title, phase.keywords).length > 0) {
      return phase.id;
    }
  }
  const criteria = step.success_criteria || "";
  for (const phase of PHASE_CONFIG) {
    if (containsAny(criteria, phase.keywords).length > 0) {
      return phase.id;
    }
  }
  return "build";
}

export function summarizePhaseStep(step) {
  return {
    id: step.id,
    title: step.title || "",
    status: step.status || "pending",
    success_criteria: step.success_criteria || "",
    result: step.result,
  };
}

export function phaseStepCounts(steps) {
  return {
    total: steps.length,
    pending: steps.filter((step) => step.status === "pending").length,
    in_progress: steps.filter((step) => step.status === "in_progress").length,
    completed: steps.filter((step) => step.status === "completed").length,
    failed: steps.filter((step) => step.status === "failed").length,
    skipped: steps.filter((step) => step.status === "skipped").length,
  };
}

export function phaseStatus(steps) {
  if (steps.length === 0) {
    return "empty";
  }
  const statuses = steps.map((step) => step.status || "pending");
  if (statuses.includes("in_progress")) {
    return "in_progress";
  }
  if (statuses.includes("failed")) {
    return "failed";
  }
  if (statuses.every((status) => status === "completed")) {
    return "completed";
  }
  if (statuses.every((status) => status === "skipped")) {
    return "skipped";
  }
  return "pending";
}

export function phaseNextAction(steps) {
  for (const status of ["in_progress", "pending"]) {
    const step = steps.find((candidate) => candidate.status === status);
    if (step) {
      return `continue step ${step.id}: ${step.title}`;
    }
  }
  return null;
}

export function buildPhaseEvidence(phaseId, dashboard, background) {
  const plan = dashboard.active_plan;
  const counts = dashboard.counts;
  const verification = dashboard.verification_summary;
  const reflections = dashboard.reflection_summary;
  const evidence = [];
  if (phaseId === "understand") {
    evidence.push(plan ? `active plan goal: ${plan.goal || ""}` : "active plan: none");
    evidence.push(
      `memory ${counts.memory}, lessons ${counts.project_lessons} project + ${counts.global_lessons} global`
    );
  } else if (phaseId === "design") {
    if (plan) {
      evidence.push(`plan progress ${plan.completed_steps}/${plan.total_steps} completed`);
      if (plan.next_pending_step) {
        evidence.push(`next pending step ${plan.next_pending_step.id}: ${plan.next_pending_step.title || ""}`);
      }
    } else {
      evidence.push("no active plan");
    }
  } else if (phaseId === "build") {
    const outcomes = background.counts.outcomes;
    const tasks = background.counts.fanout_tasks;
    evidence.push(`outcomes ${outcomes.total} total, ${outcomes.active || 0} active`);
    evidence.push(
      `fanout tasks ${tasks.running || 0} running, ${tasks.pending || 0} pending, ` +
        `${tasks.completed || 0} completed`
    );
  } else if (phaseId === "judge") {
    evidence.push(`reflections ${reflections.total} total`);
    if (reflections.recent.length > 0) {
      const latest = reflections.recent[reflections.recent.length - 1];
      evidence.push(`latest reflection: ${latest.outcome || "unknown"}; next ${latest.next || ""}`);
    }
  } else if (phaseId === "verify") {
    evidence.push(
      `executed checks ${verification.executed} total, ${verification.executed_passed} passed, ` +
        `${verification.executed_failed} failed`
    );
    evidence.push(`attested claims ${verification.attested}`);
    if (dashboard.active_outcome) {
      evidence.push(`active outcome ${dashboard.active_outcome.slug} is ${dashboard.active_outcome.status}`);
    }
  }
  return evidence;
}

export function buildPhaseView(recent = 3) {
  const dashboard = buildWorkflowDashboard(recent);
  const background = buildBackgroundView(recent);
  const stepBuckets = Object.fromEntries(PHASE_CONFIG.map((phase) => [phase.id, []]));
  if (dashboard.active_plan) {
    for (const step of dashboard.active_plan.steps || []) {
      stepBuckets[phaseIdForStep(step)].push(summarizePhaseStep(step));
    }
  }
  const phases = PHASE_CONFIG.map((phase) => {
    const steps = stepBuckets[phase.id];
    return {
      id: phase.id,
      label: phase.label,
      status: phaseStatus(steps),
      steps,
      step_counts: phaseStepCounts(steps),
      evidence: buildPhaseEvidence(phase.id, dashboard, background),
      next_action: phaseNextAction(steps),
    };
  });
  return {
    state_dir: resolveStateDir(),
    active_plan: dashboard.active_plan,
    active_outcome: dashboard.active_outcome,
    phases,
    counts: {
      memory: dashboard.counts.memory,
      project_lessons: dashboard.counts.project_lessons,
      global_lessons: dashboard.counts.global_lessons,
      verifications: dashboard.counts.verifications,
      reflections: dashboard.counts.reflections,
      outcomes: background.counts.outcomes,
      fanout_jobs: background.counts.fanout_jobs,
      fanout_tasks: background.counts.fanout_tasks,
    },
    guardrail: "phase view summarizes durable state only; verification still requires executed checks",
  };
}

export function formatPhaseView(view) {
  const lines = [`[OK] Phase view: ${view.state_dir}`];
  const plan = view.active_plan;
  if (plan) {
    lines.push(`Active plan: ${plan.slug} (${plan.completed_steps}/${plan.total_steps} completed)`);
    lines.push(`Goal: ${plan.goal || ""}`);
  } else {
    lines.push("Active plan: none");
  }
  lines.push("Phases:");
  for (const phase of view.phases) {
    const counts = phase.step_counts;
    const icon = PHASE_STATUS_ICONS[phase.status] || "[ ]";
    lines.push(
      `  ${icon} ${phase.label}: ${phase.status}; ${counts.total} plan steps ` +
        `(${counts.completed} completed, ${counts.in_progress} in progress, ${counts.pending} pending)`
    );
    for (const item of phase.evidence) {
      lines.push(`      evidence: ${item}`);
    }
    for (const step of phase.steps) {
      lines.push(`      step: ${PHASE_STATUS_ICONS[step.status] || "[ ]"} ${step.id}. ${step.title}`);
    }
    if (phase.next_action) {
      lines.push(`      next: ${phase.next_action}`);
    }
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}
