import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import {
  buildFanoutTimelineView,
  buildPhaseView,
  buildReleaseReadinessView,
  configureViewStatusCore,
  formatFanoutTimelineView,
  formatPhaseView,
  formatReleaseReadinessView,
  gitStatusSummary,
} from "./view-status-core.js";

export {
  buildFanoutTimelineView,
  buildPhaseView,
  buildReleaseReadinessView,
  formatFanoutTimelineView,
  formatPhaseView,
  formatReleaseReadinessView,
  gitStatusSummary,
} from "./view-status-core.js";

const DEFAULT_REPORT_RECENT = 8;
const DEFAULT_REPORT_ATTENTION = 5;
const STEP_ICONS = {
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

let deps = {};

export function configureViewCore(nextDeps) {
  deps = nextDeps;
  configureViewStatusCore({
    resolveStateDir,
    readJsonl,
    verificationsPath,
    listFanoutSummaries,
    buildWorkflowDashboard,
    buildBackgroundView,
    countStatuses,
    compactLabel,
  });
}

function requireDep(name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`view-core requires deps.${name}`);
  }
  return value;
}

function resolveStateDir() { return requireDep("resolveStateDir")(); }
function readActiveSlug() { return requireDep("readActiveSlug")(); }
function planPath(slug) { return requireDep("planPath")(slug); }
function readJsonRecover(filePath, defaultFactory) { return requireDep("readJsonRecover")(filePath, defaultFactory); }
function listPlanSlugs() { return requireDep("listPlanSlugs")(); }
function loadMemory() { return requireDep("loadMemory")(); }
function readLessonsFrom(dir, scopeLabel) { return requireDep("readLessonsFrom")(dir, scopeLabel); }
function projectLessonsDir() { return requireDep("projectLessonsDir")(); }
function globalLessonsDir() { return requireDep("globalLessonsDir")(); }
function verificationsPath() { return requireDep("verificationsPath")(); }
function reflectionsPath() { return requireDep("reflectionsPath")(); }
function readJsonl(filePath) { return requireDep("readJsonl")(filePath); }
function readJsonlSince(filePath, lowerBound) { return requireDep("readJsonlSince")(filePath, lowerBound); }
function writeJsonAtomic(filePath, value) { return requireDep("writeJsonAtomic")(filePath, value); }
function isoNow() { return requireDep("isoNow")(); }
function timestampAfter(value, lowerBound) { return requireDep("timestampAfter")(value, lowerBound); }
function compareTimestampValues(leftValue, rightValue) { return requireDep("compareTimestampValues")(leftValue, rightValue); }
function slugify(text) { return requireDep("slugify")(text); }
function outcomesDir() { return requireDep("outcomesDir")(); }
function readActiveOutcomeSlug() { return requireDep("readActiveOutcomeSlug")(); }
function resolveOutcome(name) { return requireDep("resolveOutcome")(name); }
function outcomeGoalPath(slug) { return requireDep("outcomeGoalPath")(slug); }
function outcomeIterationsPath(slug) { return requireDep("outcomeIterationsPath")(slug); }
function readOutcomeIterations(slug) { return requireDep("readOutcomeIterations")(slug); }

export function currentInProgressStep(plan) {
  return (plan.steps || []).find((step) => step.status === "in_progress") || null;
}

export function recentRecords(records, limit) {
  if (limit <= 0) {
    return [];
  }
  return records.slice(Math.max(0, records.length - limit));
}

export function buildWorkflowDashboard(recent = 3) {
  const active = readActiveSlug();
  let activePlan = null;
  if (active && fs.existsSync(planPath(active))) {
    const plan = readJsonRecover(planPath(active), () => null);
    if (plan !== null) {
      const steps = Array.isArray(plan.steps) ? plan.steps : [];
      const completed = steps.filter((step) => step.status === "completed").length;
      activePlan = {
        slug: active,
        goal: plan.goal || "",
        completed_steps: completed,
        total_steps: steps.length,
        current_step: currentInProgressStep(plan),
        next_pending_step: steps.find((step) => step.status === "pending") || null,
        steps,
      };
    }
  }
  const activeOutcomeSlug = readActiveOutcomeSlug();
  let activeOutcome = null;
  if (activeOutcomeSlug) {
    const resolved = resolveOutcome(activeOutcomeSlug);
    if (!resolved.error) {
      const iterations = readOutcomeIterations(resolved.slug);
      activeOutcome = {
        slug: resolved.slug,
        goal: resolved.goal.goal || "",
        status: resolved.goal.status || "active",
        iteration_count: resolved.goal.iteration_count || 0,
        max_iterations: resolved.goal.max_iterations || 1,
        last_iteration: iterations.length > 0 ? iterations[iterations.length - 1] : null,
      };
    }
  }
  const memory = loadMemory();
  const projectLessons = readLessonsFrom(projectLessonsDir(), "project");
  const globalLessons = readLessonsFrom(globalLessonsDir(), "global");
  const verifications = readJsonl(verificationsPath());
  const executed = verifications.filter((record) => record.kind === "executed");
  const reflections = readJsonl(reflectionsPath());
  return {
    state_dir: resolveStateDir(),
    active_plan: activePlan,
    active_outcome: activeOutcome,
    counts: {
      memory: memory.entries.length,
      project_lessons: projectLessons.length,
      global_lessons: globalLessons.length,
      verifications: verifications.length,
      reflections: reflections.length,
    },
    verification_summary: {
      executed: executed.length,
      executed_passed: executed.filter((record) => record.verified === true).length,
      executed_failed: executed.filter((record) => record.verified === false).length,
      attested: verifications.filter((record) => record.kind === "attested").length,
      recent: recentRecords(verifications, recent),
    },
    reflection_summary: {
      total: reflections.length,
      recent: recentRecords(reflections, recent),
    },
  };
}

export function formatWorkflowDashboard(dashboard) {
  const lines = [`[OK] Workflow dashboard: ${dashboard.state_dir}`];
  const plan = dashboard.active_plan;
  if (plan) {
    lines.push(`Active plan: ${plan.slug} (${plan.completed_steps}/${plan.total_steps} completed)`);
    lines.push(`Goal: ${plan.goal}`);
    if (plan.current_step) {
      lines.push(`Current step: ${stepLine(plan.current_step)}`);
    }
    if (plan.next_pending_step) {
      lines.push(
        `Next pending: ${plan.next_pending_step.id}. ${plan.next_pending_step.title} ` +
          `(criteria: ${plan.next_pending_step.success_criteria || "none"})`
      );
    } else if (!plan.current_step) {
      lines.push("Next pending: none");
    }
  } else {
    lines.push("Active plan: none");
  }
  const outcome = dashboard.active_outcome;
  if (outcome) {
    lines.push(
      `Active outcome: ${outcome.slug} (${outcome.status}, ` +
        `${outcome.iteration_count}/${outcome.max_iterations} iterations)`
    );
  } else {
    lines.push("Active outcome: none");
  }
  const counts = dashboard.counts;
  lines.push(
    `Counts: memory ${counts.memory}, lessons ${counts.project_lessons} project + ` +
      `${counts.global_lessons} global, verifications ${counts.verifications}, ` +
      `reflections ${counts.reflections}`
  );
  const verification = dashboard.verification_summary;
  lines.push(
    `Evidence: ${verification.executed} executed (${verification.executed_passed} passed, ` +
      `${verification.executed_failed} failed), ${verification.attested} attested`
  );
  if (verification.recent.length > 0) {
    lines.push("Recent verification:");
    for (const record of verification.recent) {
      if (record.kind === "executed") {
        const verdict = record.verified === true ? "passed" : "failed";
        const label = record.claim || record.command || "executed check";
        lines.push(`  - ${verdict}: ${label} (exit ${record.exit_code})`);
      } else {
        lines.push(`  - attested: ${record.claim || "claim"}`);
      }
    }
  }
  const reflections = dashboard.reflection_summary;
  if (reflections.recent.length > 0) {
    lines.push("Recent reflection:");
    for (const record of reflections.recent) {
      lines.push(`  - ${record.outcome || "unknown"}: ${record.action || ""}; next ${record.next || ""}`);
    }
  }
  return lines.join("\n");
}

export const VERIFICATION_HISTORY_ICONS = {
  passed: "[x]",
  failed: "[!]",
  attested: "[~]",
  unknown: "[ ]",
};

export function verificationVerdict(record) {
  if (record.kind === "attested") {
    return "attested";
  }
  if (record.kind === "executed" && record.verified === true) {
    return "passed";
  }
  if (record.kind === "executed" && record.verified === false) {
    return "failed";
  }
  return "unknown";
}

export function summarizeVerificationRecord(record, index) {
  const kind = record.kind || "unknown";
  const verdict = verificationVerdict(record);
  const summary = {
    index,
    kind,
    verdict,
    timestamp: record.timestamp || "",
    claim: record.claim,
    verified: record.verified,
    plan: record.plan,
    step_id: record.step_id,
    step_title: record.step_title,
    step_status: record.step_status,
  };
  if (kind === "executed") {
    return {
      ...summary,
      command: record.command || "",
      exit_code: record.exit_code,
      duration_seconds: record.duration_seconds || 0,
      stdout_tail_bytes: String(record.stdout_tail || "").length,
      stderr_tail_bytes: String(record.stderr_tail || "").length,
    };
  }
  if (kind === "attested") {
    return {
      ...summary,
      evidence: record.evidence || "",
    };
  }
  return summary;
}

export function buildVerificationHistoryView(recent = 10) {
  const rows = readJsonl(verificationsPath()).map((record, index) =>
    summarizeVerificationRecord(record, index + 1)
  );
  const executed = rows.filter((row) => row.kind === "executed");
  const recentRows = recent <= 0 ? [] : rows.slice(Math.max(0, rows.length - recent)).reverse();
  return {
    state_dir: resolveStateDir(),
    records: recentRows,
    counts: {
      total: rows.length,
      executed: executed.length,
      executed_passed: executed.filter((row) => row.verdict === "passed").length,
      executed_failed: executed.filter((row) => row.verdict === "failed").length,
      attested: rows.filter((row) => row.kind === "attested").length,
      unknown: rows.filter((row) => row.verdict === "unknown").length,
    },
    guardrail: "history displays recorded evidence only; it does not rerun checks or upgrade attested claims",
  };
}

export function verificationLabel(row) {
  return compactLabel(row.claim || row.command || row.evidence, "verification");
}

export function formatVerificationHistoryRow(row) {
  const icon = VERIFICATION_HISTORY_ICONS[row.verdict] || "[ ]";
  let line = `  ${icon} ${row.timestamp || "unknown-time"} #${row.index} ${row.verdict}: ${verificationLabel(row)}`;
  const details = [];
  if (row.kind === "executed") {
    details.push(`exit ${row.exit_code}`);
    details.push(`${row.duration_seconds || 0}s`);
    if (row.stdout_tail_bytes) {
      details.push(`stdout ${row.stdout_tail_bytes} bytes`);
    }
    if (row.stderr_tail_bytes) {
      details.push(`stderr ${row.stderr_tail_bytes} bytes`);
    }
  } else if (row.kind === "attested") {
    details.push("self-reported");
  }
  if (row.plan) {
    if (row.step_id !== null && row.step_id !== undefined) {
      details.push(`plan ${row.plan} step ${row.step_id}`);
    } else {
      details.push(`plan ${row.plan}`);
    }
  }
  if (details.length > 0) {
    line += ` (${details.join("; ")})`;
  }
  return line;
}

export function formatVerificationHistoryView(view) {
  const lines = [`[OK] Verification history: ${view.state_dir}`];
  const counts = view.counts;
  lines.push(
    `Evidence: ${counts.executed} executed (${counts.executed_passed} passed, ` +
      `${counts.executed_failed} failed), ${counts.attested} attested, ${counts.total} total`
  );
  if (view.records.length > 0) {
    lines.push("Recent verification:");
    for (const row of view.records) {
      lines.push(formatVerificationHistoryRow(row));
    }
  } else {
    lines.push("No verification records found.");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

export function reportsDir() {
  return path.join(resolveStateDir(), "reports");
}

export function reportCursorName(name) {
  return slugify(name || "default") || "default";
}

export function reportCursorPath(cursor) {
  return path.join(reportsDir(), `${reportCursorName(cursor)}.json`);
}

export function reportEventSortKey(event) {
  return [event.timestamp || "", event.order || 0, event.key || ""];
}

export function compareReportEvents(left, right) {
  const leftKey = reportEventSortKey(left);
  const rightKey = reportEventSortKey(right);
  const timestampOrder = compareTimestampValues(leftKey[0], rightKey[0]);
  if (timestampOrder !== 0) {
    return timestampOrder;
  }
  for (let index = 1; index < leftKey.length; index += 1) {
    if (leftKey[index] < rightKey[index]) {
      return -1;
    }
    if (leftKey[index] > rightKey[index]) {
      return 1;
    }
  }
  return 0;
}

export function compactReportDetail(text) {
  const value = String(text || "").trim();
  return value.length <= 140 ? value : `${value.slice(0, 137)}...`;
}

export function reportAttentionLevel(event) {
  if (
    event.verified === false ||
    event.kind === "step_failed" ||
    event.kind === "reflection_failure"
  ) {
    return "issue";
  }
  if (event.kind === "verification_attested") {
    return "warning";
  }
  return "";
}

export function buildReportAttentionEvents(events) {
  const items = [];
  for (const event of events) {
    const level = reportAttentionLevel(event);
    if (!level) {
      continue;
    }
    items.push({
      level,
      key: event.key || "",
      timestamp: event.timestamp || "",
      kind: event.kind || "",
      summary: event.summary || "Event recorded",
      detail: event.detail || "",
      plan: event.plan,
      step_id: event.step_id,
      verified: event.verified,
    });
  }
  return items;
}

export function buildReportEvents(logLowerBound = "") {
  const events = [];
  for (const slug of listPlanSlugs()) {
    const plan = readJsonRecover(planPath(slug), () => null);
    if (!plan || !Array.isArray(plan.steps)) {
      continue;
    }
    const steps = plan.steps || [];
    const created = plan.created || plan.last_updated || "";
    events.push({
      key: `plan:${slug}:created`,
      timestamp: created,
      order: 10,
      kind: "plan_created",
      summary: `Plan created: ${slug} (${steps.length} steps)`,
      detail: plan.goal || "",
      plan: slug,
      step_id: null,
      verified: null,
    });
    for (const step of steps) {
      const updated = step.updated_at;
      if (!updated) {
        continue;
      }
      const status = step.status || "pending";
      events.push({
        key: `step:${slug}:${step.id}:${status}:${updated}`,
        timestamp: updated,
        order: 20,
        kind: `step_${status}`,
        summary: `Step ${status}: ${step.id}. ${step.title}`,
        detail: step.result || step.success_criteria || "",
        plan: slug,
        step_id: step.id,
        verified: null,
      });
    }
  }
  const verifications = readJsonlSince(verificationsPath(), logLowerBound);
  verifications.forEach((record, index) => {
    let summary;
    let detail;
    let verified = null;
    if (record.kind === "executed") {
      verified = record.verified === true;
      const verdict = verified ? "passed" : "failed";
      const label = record.claim || record.command || "executed check";
      summary = `Verification ${verdict}: ${compactReportDetail(label)}`;
      detail = `exit ${record.exit_code}`;
    } else if (record.kind === "attested") {
      const label = record.claim || "claim";
      summary = `Verification attested: ${compactReportDetail(label)}`;
      detail = "self-reported, not machine-checked";
    } else {
      summary = "Verification recorded";
      detail = "";
    }
    events.push({
      key: `verification:${index + 1}:${record.timestamp || ""}`,
      timestamp: record.timestamp || "",
      order: 30,
      kind: `verification_${verificationVerdict(record)}`,
      summary,
      detail,
      plan: record.plan,
      step_id: record.step_id,
      verified,
    });
  });
  const reflections = readJsonlSince(reflectionsPath(), logLowerBound);
  reflections.forEach((record, index) => {
    events.push({
      key: `reflection:${index + 1}:${record.timestamp || ""}`,
      timestamp: record.timestamp || "",
      order: 40,
      kind: `reflection_${record.outcome || "unknown"}`,
      summary: `Reflection ${record.outcome || "unknown"}: ${compactReportDetail(record.action || "action")}`,
      detail: `next: ${record.next || ""}`,
      plan: null,
      step_id: null,
      verified: null,
    });
  });
  return events.sort(compareReportEvents);
}

export function eventsAfterMarker(events, marker) {
  const lastEvent = marker && typeof marker === "object" ? marker.last_event : null;
  if (!lastEvent || typeof lastEvent !== "object") {
    return events;
  }
  if (lastEvent.key) {
    const index = events.findIndex((event) => event.key === lastEvent.key);
    if (index >= 0) {
      return events.slice(index + 1);
    }
  }
  if (lastEvent.timestamp) {
    return events.filter((event) => timestampAfter(event.timestamp || "", lastEvent.timestamp));
  }
  return events;
}

export function buildWorkReport({
  since = "last",
  recent = DEFAULT_REPORT_RECENT,
  cursor = "default",
  peek = false,
  mark = false,
} = {}) {
  if (!Number.isInteger(recent) || recent < 0) {
    return { error: "[FAIL] Invalid recent: use 0 or a positive integer." };
  }
  if (mark && peek) {
    return { error: "[FAIL] mark cannot be combined with peek." };
  }
  const cursorName = reportCursorName(cursor);
  const marker = readJsonRecover(reportCursorPath(cursorName), () => ({}));
  const lastEvent = marker && typeof marker === "object" ? marker.last_event : null;
  const logLowerBound =
    since === "last" && !mark && lastEvent && typeof lastEvent === "object"
      ? lastEvent.timestamp || ""
      : "";
  const allEvents = buildReportEvents(logLowerBound);
  const candidateEvents = mark ? [] : since === "last" ? eventsAfterMarker(allEvents, marker) : allEvents;
  const visibleEvents = recent === 0 ? [] : candidateEvents.slice(Math.max(0, candidateEvents.length - recent));
  const attentionCandidates = buildReportAttentionEvents(candidateEvents);
  const attentionEvents = attentionCandidates.slice(Math.max(0, attentionCandidates.length - DEFAULT_REPORT_ATTENTION));
  if (mark || !peek) {
    writeJsonAtomic(reportCursorPath(cursorName), {
      cursor: cursorName,
      updated_at: isoNow(),
      last_event: allEvents.length > 0 ? allEvents[allEvents.length - 1] : marker.last_event,
    });
  }
  return {
    state_dir: resolveStateDir(),
    cursor: cursorName,
    since,
    format: "chat",
    peek,
    mark,
    events: visibleEvents,
    new_event_count: candidateEvents.length,
    shown_event_count: visibleEvents.length,
    omitted_new_events: Math.max(0, candidateEvents.length - visibleEvents.length),
    attention_events: attentionEvents,
    attention_event_count: attentionCandidates.length,
    omitted_attention_events: Math.max(0, attentionCandidates.length - attentionEvents.length),
    cursor_updated: !peek,
    last_event: allEvents.length > 0 ? allEvents[allEvents.length - 1] : null,
    guardrail:
      "report summarizes durable Mythify state only; it does not rerun checks or prove work beyond recorded evidence",
  };
}

export function formatWorkReport(view) {
  const lines = [`[OK] Live work report: ${view.state_dir}`];
  if (view.mark) {
    lines.push(
      `Scope: mark cursor ${view.cursor}, ${view.new_event_count} new events ` +
        `(${view.shown_event_count} shown, ${view.omitted_new_events} omitted)`
    );
  } else {
    lines.push(
      `Scope: since ${view.since}, cursor ${view.cursor}, ${view.new_event_count} new events ` +
        `(${view.shown_event_count} shown, ${view.omitted_new_events} omitted)`
    );
  }
  if (view.attention_event_count > 0) {
    lines.push("Attention:");
    for (const event of view.attention_events || []) {
      let line = `- ${event.level || "notice"}: ${event.summary || "Event recorded"}`;
      if (event.detail) {
        line += `, ${compactReportDetail(event.detail)}`;
      }
      lines.push(line);
    }
    if (view.omitted_attention_events > 0) {
      lines.push(`- ${view.omitted_attention_events} older attention events omitted`);
    }
  } else {
    lines.push("Attention: none in this report window.");
  }
  if (view.events.length > 0) {
    for (const event of view.events) {
      let line = `- ${event.summary || "Event recorded"}`;
      if (event.detail) {
        line += `, ${compactReportDetail(event.detail)}`;
      }
      lines.push(line);
    }
  } else if (view.mark) {
    lines.push("Cursor is ready. Future reports with --since last will show only new events.");
  } else {
    lines.push("No new Mythify events to report.");
  }
  if (view.mark) {
    lines.push(`Cursor marked at latest event: ${view.cursor}`);
  } else {
    lines.push(view.cursor_updated ? `Cursor advanced: ${view.cursor}` : "Cursor unchanged: --peek");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}

export const BACKGROUND_STATUS_ICONS = {
  active: "[>]",
  running: "[>]",
  pending: "[ ]",
  completed: "[x]",
  succeeded: "[x]",
  failed: "[!]",
  interrupted: "[~]",
  stopped: "[~]",
  empty: "[ ]",
};

export function backgroundRecent(items, limit) {
  if (limit <= 0) {
    return [];
  }
  return items.slice(Math.max(0, items.length - limit)).reverse();
}

export function fanoutRootDir() {
  return path.join(resolveStateDir(), "fanout");
}

export function countStatuses(items, statuses) {
  const counts = Object.fromEntries(statuses.map((status) => [status, 0]));
  for (const item of items) {
    const status = item.status || "unknown";
    counts[status] = (counts[status] || 0) + 1;
  }
  return counts;
}

export function summarizeFanoutJob(job) {
  const tasks = Array.isArray(job.tasks) ? job.tasks : [];
  const counts = countStatuses(tasks, ["pending", "running", "completed", "failed", "interrupted"]);
  let status;
  if (counts.pending > 0 || counts.running > 0) {
    status = "active";
  } else if (counts.failed > 0) {
    status = "failed";
  } else if (counts.interrupted > 0) {
    status = "interrupted";
  } else if (tasks.length > 0) {
    status = "completed";
  } else {
    status = "empty";
  }
  return {
    id: job.id || "",
    status,
    created: job.created || "",
    last_updated: job.last_updated || "",
    purpose: job.purpose || "",
    engine: job.engine || "",
    model: job.model || "",
    visibility: job.visibility || "summary",
    task_counts: counts,
    task_total: tasks.length,
    tasks: tasks.map((task) => ({
      id: task.id,
      title: task.title || "",
      status: task.status || "pending",
      role: task.role || "worker",
      engine: task.engine || "",
      model: task.model || "",
      started_at: task.started_at || "",
      finished_at: task.finished_at || "",
      duration_seconds: task.duration_seconds || 0,
      error: task.error || null,
      output_file: task.output_file || null,
      output_bytes: task.output_bytes || 0,
    })),
  };
}

export function listFanoutSummaries() {
  let names;
  try {
    names = fs.readdirSync(fanoutRootDir());
  } catch {
    return [];
  }
  const jobs = [];
  for (const name of names.sort()) {
    if (!/^fo-\d{14}-[0-9a-f]{4}$/.test(name)) {
      continue;
    }
    const job = readJsonRecover(path.join(fanoutRootDir(), name, "job.json"), () => null);
    if (job && typeof job === "object") {
      const summary = summarizeFanoutJob(job);
      if (!summary.id) {
        summary.id = name;
      }
      jobs.push(summary);
    }
  }
  return jobs.sort((left, right) =>
    `${left.created || ""}${left.id || ""}`.localeCompare(`${right.created || ""}${right.id || ""}`)
  );
}

export function summarizeOutcome(slug, goal) {
  const iterations = readOutcomeIterations(slug);
  const lastIteration = iterations.length > 0 ? iterations[iterations.length - 1] : null;
  return {
    id: slug,
    goal: goal.goal || "",
    status: goal.status || "active",
    iteration_count: goal.iteration_count || 0,
    max_iterations: goal.max_iterations || 1,
    visibility: goal.visibility || "summary",
    created: goal.created || "",
    updated: goal.updated || "",
    last_verified: goal.last_verified,
    last_iteration: lastIteration,
    next_action: lastIteration ? lastIteration.next_action : "make a bounded attempt, then call outcome_check",
  };
}

export function listOutcomeSummaries() {
  let names;
  try {
    names = fs.readdirSync(outcomesDir());
  } catch {
    return [];
  }
  const outcomes = [];
  for (const name of names.sort()) {
    const goalPath = outcomeGoalPath(name);
    if (!fs.existsSync(goalPath)) {
      continue;
    }
    const goal = readJsonRecover(goalPath, () => null);
    if (goal && typeof goal === "object") {
      outcomes.push(summarizeOutcome(name, goal));
    }
  }
  return outcomes.sort((left, right) =>
    `${left.updated || left.created || ""}${left.id || ""}`.localeCompare(
      `${right.updated || right.created || ""}${right.id || ""}`
    )
  );
}

export function buildBackgroundView(recent = 5) {
  const outcomes = listOutcomeSummaries();
  const fanoutJobs = listFanoutSummaries();
  const activeOutcomeSlug = readActiveOutcomeSlug();
  const outcomeCounts = countStatuses(outcomes, ["active", "succeeded", "failed", "stopped"]);
  const fanoutCounts = countStatuses(fanoutJobs, ["active", "completed", "failed", "interrupted", "empty"]);
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
  return {
    state_dir: resolveStateDir(),
    active_outcome: outcomes.find((outcome) => outcome.id === activeOutcomeSlug) || null,
    outcomes: backgroundRecent(outcomes, recent),
    fanout_jobs: backgroundRecent(fanoutJobs, recent),
    counts: {
      outcomes: { total: outcomes.length, ...outcomeCounts },
      fanout_jobs: { total: fanoutJobs.length, ...fanoutCounts },
      fanout_tasks: taskCounts,
    },
  };
}

export function compactLabel(text, fallback) {
  const value = String(text || "").trim();
  if (value === "") {
    return fallback;
  }
  return value.length <= 80 ? value : `${value.slice(0, 77)}...`;
}

export function formatBackgroundView(view) {
  const lines = [`[OK] Background tasks: ${view.state_dir}`];
  const outcomes = view.counts.outcomes;
  lines.push(
    `Outcomes: ${outcomes.total} total; ${outcomes.active || 0} active, ` +
      `${outcomes.succeeded || 0} succeeded, ${outcomes.failed || 0} failed, ` +
      `${outcomes.stopped || 0} stopped`
  );
  if (view.active_outcome) {
    lines.push(
      `Active outcome: ${view.active_outcome.id} (${view.active_outcome.status}, ` +
        `${view.active_outcome.iteration_count}/${view.active_outcome.max_iterations} iterations)`
    );
  } else {
    lines.push("Active outcome: none");
  }
  if (view.outcomes.length > 0) {
    lines.push("Recent outcomes:");
    for (const outcome of view.outcomes) {
      const icon = BACKGROUND_STATUS_ICONS[outcome.status] || "[ ]";
      lines.push(
        `  ${icon} ${outcome.id}: ${compactLabel(outcome.goal, "outcome")} ` +
          `(${outcome.status}, ${outcome.iteration_count}/${outcome.max_iterations} iterations, ` +
          `last verified=${outcome.last_verified})`
      );
      if (outcome.next_action) {
        lines.push(`      next: ${outcome.next_action}`);
      }
    }
  }
  const fanout = view.counts.fanout_jobs;
  const tasks = view.counts.fanout_tasks;
  lines.push(
    `Fanout jobs: ${fanout.total} total; ${fanout.active || 0} active, ` +
      `${fanout.completed || 0} completed, ${fanout.failed || 0} failed, ` +
      `${fanout.interrupted || 0} interrupted`
  );
  lines.push(
    `Fanout tasks: ${tasks.running || 0} running, ${tasks.pending || 0} pending, ` +
      `${tasks.completed || 0} completed, ${tasks.failed || 0} failed, ` +
      `${tasks.interrupted || 0} interrupted`
  );
  if (view.fanout_jobs.length > 0) {
    lines.push("Recent fanout jobs:");
    for (const job of view.fanout_jobs) {
      const icon = BACKGROUND_STATUS_ICONS[job.status] || "[ ]";
      const taskCounts = job.task_counts;
      lines.push(
        `  ${icon} ${job.id}: ${compactLabel(job.purpose, "fanout job")} ` +
          `(${job.status}; ${job.task_total} tasks, ${taskCounts.completed || 0} completed, ` +
          `${taskCounts.failed || 0} failed, ${taskCounts.running || 0} running, ` +
          `${taskCounts.pending || 0} pending)`
      );
      lines.push(
        `      visibility: ${job.visibility || "summary"}; engine: ${job.engine || "unknown"}; ` +
          `created: ${job.created || "unknown"}`
      );
      for (const task of job.tasks) {
        const taskIcon = BACKGROUND_STATUS_ICONS[task.status] || "[ ]";
        let detail = `      ${taskIcon} ${task.id}. ${compactLabel(task.title, "task")} (${task.status})`;
        if (task.error) {
          detail += `: ${compactLabel(task.error, "error")}`;
        }
        lines.push(detail);
      }
    }
  }
  if (view.outcomes.length === 0 && view.fanout_jobs.length === 0) {
    lines.push("No background tasks found.");
  }
  return lines.join("\n");
}

export function summarizeOutcomeProgress(slug, goal) {
  const iterations = readOutcomeIterations(slug);
  const lastIteration = iterations.length > 0 ? iterations[iterations.length - 1] : null;
  const iterationCount = Number(goal.iteration_count || 0);
  const maxIterations = Number(goal.max_iterations || 1);
  const metric = lastIteration && lastIteration.metric ? lastIteration.metric : null;
  const verify = lastIteration && lastIteration.verify ? lastIteration.verify : {};
  const lastCheck = lastIteration
    ? {
        iteration: lastIteration.iteration,
        timestamp: lastIteration.timestamp || "",
        verified: lastIteration.verified,
        status_after: lastIteration.status_after || "",
        notes: lastIteration.notes || "",
        verify_exit_code: verify.exit_code,
        verify_duration_seconds: verify.duration_seconds || 0,
        verify_verified: verify.verified,
        metric_exit_code: metric ? metric.exit_code : null,
        metric_score: metric ? metric.score : null,
        metric_verified: metric ? metric.verified : null,
      }
    : null;
  return {
    id: slug,
    goal: goal.goal || "",
    success_criteria: goal.success_criteria || "",
    status: goal.status || "active",
    iteration_count: iterationCount,
    max_iterations: maxIterations,
    iterations_remaining: Math.max(0, maxIterations - iterationCount),
    progress_percent: maxIterations ? Math.round((iterationCount / maxIterations) * 1000) / 10 : 0,
    visibility: goal.visibility || "summary",
    created: goal.created || "",
    updated: goal.updated || "",
    last_verified: goal.last_verified,
    last_check: lastCheck,
    next_action: lastIteration
      ? lastIteration.next_action
      : "make a bounded attempt, then call outcome_check",
    verify_command: goal.verify_command || "",
    metric_command: goal.metric_command || "",
    best_metric_score: goal.best_metric_score,
    allowed_paths: Array.isArray(goal.allowed_paths) ? goal.allowed_paths : [],
    stop_reason: goal.stop_reason,
  };
}

export function listOutcomeProgressRows() {
  let names;
  try {
    names = fs.readdirSync(outcomesDir());
  } catch {
    return [];
  }
  const rows = [];
  for (const name of names.sort()) {
    const goalPath = outcomeGoalPath(name);
    if (!fs.existsSync(goalPath)) {
      continue;
    }
    const goal = readJsonRecover(goalPath, () => null);
    if (goal && typeof goal === "object") {
      rows.push(summarizeOutcomeProgress(name, goal));
    }
  }
  return rows.sort((left, right) =>
    `${left.updated || left.created || ""}${left.id || ""}`.localeCompare(
      `${right.updated || right.created || ""}${right.id || ""}`
    )
  );
}

export function buildOutcomeProgressView(recent = 5) {
  const outcomes = listOutcomeProgressRows();
  const activeOutcomeSlug = readActiveOutcomeSlug();
  const counts = countStatuses(outcomes, ["active", "succeeded", "failed", "stopped"]);
  return {
    state_dir: resolveStateDir(),
    active_outcome: outcomes.find((outcome) => outcome.id === activeOutcomeSlug) || null,
    outcomes: backgroundRecent(outcomes, recent),
    counts: { total: outcomes.length, ...counts },
    guardrail:
      "progress displays recorded outcome verifier results only; it does not run checks, make attempts, stop loops, or treat notes as verification",
  };
}

export function formatOutcomeProgressRow(row) {
  const icon = BACKGROUND_STATUS_ICONS[row.status] || "[ ]";
  const lines = [
    `  ${icon} ${row.id}: ${compactLabel(row.goal, "outcome")} ` +
      `(${row.status}, ${row.iteration_count}/${row.max_iterations} iterations, ` +
      `${row.iterations_remaining} remaining)`,
  ];
  const last = row.last_check;
  if (last) {
    lines.push(
      `      verifier: iteration ${last.iteration}, exit ${last.verify_exit_code}, ` +
        `verified=${last.verify_verified}, at ${last.timestamp || "unknown-time"}`
    );
    if (last.metric_exit_code !== null && last.metric_exit_code !== undefined) {
      let metricLine = `      metric: exit ${last.metric_exit_code}`;
      if (last.metric_score !== null && last.metric_score !== undefined) {
        metricLine += `, score ${last.metric_score}`;
      }
      lines.push(metricLine);
    }
  } else {
    lines.push("      verifier: no recorded iterations yet");
  }
  if (row.next_action) {
    lines.push(`      next: ${row.next_action}`);
  }
  return lines;
}

export function formatOutcomeProgressView(view) {
  const lines = [`[OK] Outcome progress: ${view.state_dir}`];
  const counts = view.counts;
  lines.push(
    `Outcomes: ${counts.total} total; ${counts.active || 0} active, ` +
      `${counts.succeeded || 0} succeeded, ${counts.failed || 0} failed, ` +
      `${counts.stopped || 0} stopped`
  );
  const active = view.active_outcome;
  if (active) {
    lines.push(
      `Active outcome: ${active.id} (${active.status}, ` +
        `${active.iteration_count}/${active.max_iterations} iterations, ` +
        `${active.iterations_remaining} remaining)`
    );
  } else {
    lines.push("Active outcome: none");
  }
  if (view.outcomes.length > 0) {
    lines.push("Recent outcomes:");
    for (const row of view.outcomes) {
      lines.push(...formatOutcomeProgressRow(row));
    }
  } else {
    lines.push("No outcome loops found.");
  }
  lines.push(`Guardrail: ${view.guardrail}.`);
  return lines.join("\n");
}
