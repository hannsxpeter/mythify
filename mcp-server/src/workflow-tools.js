import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { z } from "zod";
import { classifyTaskText, formatClassification } from "./classification.js";
import { godArtifactHasOpenTasks, godauditsSummary, godplansSummary } from "./godfiles-core.js";
import { buildModelPolicy, runModelTriage } from "./model-policy.js";
import { routePlanHorizon } from "./plan-horizon.js";
import {
  EFFORT_LEVELS,
  HOST_PLATFORMS as PLATFORMS,
  REVIEWER_STRENGTH_MODES,
  SPAWN_CEILINGS,
  SPEED_LEVELS,
  TRIAGE_ENGINES,
  TRIAGE_MODES,
} from "./capability-registry.js";

const WORKFLOW_ROUTER_PATH = new URL("../protocol/workflow-router.json", import.meta.url);
const TAIL_CHARS = 4000;
const REDACTED_SECRET = "[REDACTED]";
const DEFAULT_VERIFY_MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
const JSONL_LOCK_TIMEOUT_MS = 10000;
const JSONL_LOCK_POLL_MS = 50;
const JSONL_TAIL_CHUNK_BYTES = 64 * 1024;
const FALSE_ENV_VALUES = new Set(["0", "false", "no", "off"]);
const CAMPAIGN_PHASES = ["understand", "design", "build", "judge", "verify", "reflect"];
const CAMPAIGN_PHASE_GUIDANCE = {
  understand: "Read context, restate the task, and identify constraints.",
  design: "Choose the smallest useful approach and success check.",
  build: "Make the focused change or artifact.",
  judge: "Review the result against the task and campaign goal.",
  verify: "Run the nearest executable check, or record why only attestation is possible.",
  reflect: "Capture what improved the next task, then advance the frontier.",
};
const CAMPAIGN_PROMPT_GUARDRAIL =
  "Prompt output is steering material for the host agent, not verification evidence. " +
  "The host must do the work, run checks when available, and advance the campaign with evidence.";
const PROMPT_PACKET_KINDS = ["research", "analysis", "failure", "handoff", "review", "campaign", "next"];
const PROMPT_PACKET_GUARDRAIL =
  "Prompt packet output is steering material for the host agent, not verification evidence. " +
  "The host must do the work, run checks when available, report issues in chat, and record evidence.";
const WORKFLOW_ROUTE_GUARDRAIL =
  "Workflow route output is steering material for the host agent, not verification evidence. " +
  "The host must do the work, run checks when available, report issues in chat, and record evidence.";
const ROUTE_FULL_SEND_TERMS = [
  "one shot", "one-shot", "one go", "in one go", "all in one go",
  "address all", "fix all", "do all", "do everything", "execute all",
  "continuous run", "keep going", "keep going until done", "until no issues remain",
  "yolo", "full send", "ship it", "run it through",
];
const ROUTE_PROMPT_TERMS = [
  "prompt packet", "reprompt", "inject the next task", "next prompt",
  "steer the chat", "steering prompt", "handoff packet",
];
const ROUTE_RESEARCH_TERMS = [
  "research", "look up", "latest", "find sources", "source-backed",
  "online", "internet", "web search",
];
const ROUTE_REVIEW_TERMS = [
  "audit", "review", "assess", "evaluate", "find issues", "code review",
  "risks", "risk sweep",
];
const ROUTE_RESUME_TERMS = [
  "continue", "resume", "next", "keep going", "pick up", "carry on",
  "what is next",
];
const ROUTE_OUTCOME_TERMS = [
  "until", "success criteria", "when tests pass", "when it passes",
  "verifier", "verify command", "outcome loop",
];
const ROUTE_VERIFY_TERMS = [
  "verify", "test", "tests", "passes", "passing", "check", "build",
  "lint",
];
const ROUTE_GODPLANS_TERMS = ["godplans", "god plans"];
const ROUTE_GODAUDITS_TERMS = ["godaudits", "god audits"];
const DEFAULT_REPORT_RECENT = 8;
const DEFAULT_REPORT_ATTENTION = 5;
const STEP_ICONS = {
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
  failed: "[!]",
  skipped: "[~]",
};

function loadWorkflowRouter() {
  const manifest = JSON.parse(fs.readFileSync(WORKFLOW_ROUTER_PATH, "utf8"));
  const routes = manifest.routes || [];
  const seen = new Set();
  for (const entry of routes) {
    const routeId = String(entry?.id || "").trim();
    const promptPacket = String(entry?.prompt_packet || "").trim();
    if (!routeId || seen.has(routeId) || !promptPacket) {
      throw new Error("Invalid workflow router entry");
    }
    seen.add(routeId);
  }
  if (routes.length === 0) {
    throw new Error("Workflow router manifest is empty");
  }
  return manifest;
}

const WORKFLOW_ROUTER = loadWorkflowRouter();
const WORKFLOW_ROUTE_IDS = WORKFLOW_ROUTER.routes.map((route) => String(route.id));
const WORKFLOW_ROUTE_PROMPTS = Object.fromEntries(
  WORKFLOW_ROUTER.routes.map((route) => [String(route.id), String(route.prompt_packet || "next")])
);

const MCP_FRONT_DOOR_NOTE =
  " For broad or ambiguous user prompts, call workflow_route first; use this tool directly only after workflow_route selects this workflow or the user explicitly asks for this primitive.";
const MCP_WORKFLOW_ROUTE_NOTE =
  " This is the recommended first tool for broad, ambiguous, multi-step, review, research, one-shot, in-one-go, or recovery prompts.";

let deps = {};

function requireDep(name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`workflow-tools requires deps.${name}`);
  }
  return value;
}

function guarded(handler) { return requireDep("guarded")(handler); }
function resolveStateDir() { return requireDep("resolveStateDir")(); }
function readHostModelState() { return requireDep("readHostModelState")(); }
function readJsonRecover(filePath, defaultFactory) { return requireDep("readJsonRecover")(filePath, defaultFactory); }
function findExistingSlugByName(name, pathForSlug) { return requireDep("findExistingSlugByName")(name, pathForSlug); }
function readActiveSlug() { return requireDep("readActiveSlug")(); }
function planPath(slug) { return requireDep("planPath")(slug); }
function readActiveOutcomeSlug() { return requireDep("readActiveOutcomeSlug")(); }
function outcomeGoalPath(slug) { return requireDep("outcomeGoalPath")(slug); }
function verificationsPath() { return requireDep("verificationsPath")(); }
function reflectionsPath() { return requireDep("reflectionsPath")(); }
function readJsonl(filePath) { return requireDep("readJsonl")(filePath); }
function buildVerificationHistoryView(limit) { return requireDep("buildVerificationHistoryView")(limit); }
function verificationLabel(row) { return requireDep("verificationLabel")(row); }
function gitStatusSummary(root) { return requireDep("gitStatusSummary")(root); }
function compactReportDetail(text) { return requireDep("compactReportDetail")(text); }
function buildWorkReport(args) { return requireDep("buildWorkReport")(args); }

function campaignsDir() {
  return path.join(resolveStateDir(), "campaigns");
}

function campaignPath(slug) {
  return path.join(campaignsDir(), `${slug}.json`);
}

function activeCampaignPath() {
  return path.join(campaignsDir(), "active");
}

function getActiveCampaignSlug() {
  let value = "";
  try {
    value = fs.readFileSync(activeCampaignPath(), "utf8").trim();
  } catch {
    return null;
  }
  if (value && fs.existsSync(campaignPath(value))) {
    return value;
  }
  return null;
}

function findCampaignSlug(name) {
  const raw = String(name || "").trim();
  if (raw) {
    return findExistingSlugByName(raw, campaignPath);
  }
  return getActiveCampaignSlug();
}

function loadCampaign(name) {
  const slug = findCampaignSlug(name);
  if (!slug) {
    return [null, null];
  }
  const record = readJsonRecover(campaignPath(slug), () => null);
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return [slug, null];
  }
  return [slug, record];
}

function researchDir() {
  return path.join(resolveStateDir(), "research");
}

function researchPath(slug) {
  return path.join(researchDir(), `${slug}.json`);
}

function activeResearchPath() {
  return path.join(researchDir(), "active");
}

function getActiveResearchSlug() {
  let value = "";
  try {
    value = fs.readFileSync(activeResearchPath(), "utf8").trim();
  } catch {
    return null;
  }
  if (value && fs.existsSync(researchPath(value))) {
    return value;
  }
  return null;
}

function findResearchSlug(name) {
  const raw = String(name || "").trim();
  if (raw) {
    return findExistingSlugByName(raw, researchPath);
  }
  return getActiveResearchSlug();
}

function loadResearch(name) {
  const slug = findResearchSlug(name);
  if (!slug) {
    return [null, null];
  }
  const record = readJsonRecover(researchPath(slug), () => null);
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return [slug, null];
  }
  return [slug, record];
}

function currentCampaignTask(record) {
  const currentId = record?.current_task_id;
  for (const task of record?.tasks || []) {
    if (task?.id === currentId) {
      return task;
    }
  }
  return null;
}

function campaignProgress(record) {
  const tasks = Array.isArray(record?.tasks) ? record.tasks : [];
  const completed = tasks.filter((task) => task?.status === "completed").length;
  return [completed, tasks.length];
}

function campaignNextAction(record) {
  if (record?.status === "completed") {
    return "Campaign complete. Review lessons and final verification evidence.";
  }
  if (record?.status === "stopped") {
    return "Campaign stopped. Resume by creating a new campaign or updating the existing record manually.";
  }
  const task = currentCampaignTask(record);
  if (!task) {
    return "No current task. Add a task or complete the campaign.";
  }
  if (task.status === "failed") {
    return (
      `Task ${task.id} failed: diagnose the failure, record a reflection, then ` +
      `retry it with campaign task ${task.id} in_progress or skip it.`
    );
  }
  const phase = CAMPAIGN_PHASES.includes(task.phase) ? task.phase : CAMPAIGN_PHASES[0];
  return `Task ${task.id} ${phase}: ${CAMPAIGN_PHASE_GUIDANCE[phase] || "Continue the workflow."}`;
}

function campaignRecentLearningLines(record, limit = 5) {
  const learnings = Array.isArray(record?.learnings) ? record.learnings : [];
  return learnings
    .slice(-limit)
    .map((item) => {
      const lesson = String(item?.lesson || "").trim();
      if (!lesson) {
        return "";
      }
      const prefix = item?.task_id ? `task ${item.task_id}: ` : "";
      const suffix = item?.apply_next ? " [apply next]" : "";
      return `${prefix}${lesson}${suffix}`;
    })
    .filter(Boolean);
}

function buildCampaignPromptPayload(slug, record) {
  const [completed, total] = campaignProgress(record);
  const task = currentCampaignTask(record);
  const status = record?.status || "active";
  const verifyCommand = record?.verify_command || "";
  const learningLines = campaignRecentLearningLines(record);
  let phase = "";
  let phaseGuidance = "";
  let nextCommand = "";
  const lines = [
    `Continue Mythify campaign: ${slug}`,
    `Goal: ${record?.goal || ""}`,
    `Status: ${status}`,
    `Progress: ${completed}/${total} tasks completed`,
  ];
  if (record?.success_criteria) {
    lines.push(`Campaign success: ${record.success_criteria}`);
  }
  if (verifyCommand) {
    lines.push(`Campaign verifier: ${verifyCommand}`);
  }
  if (status === "completed") {
    lines.push("");
    lines.push("No current task remains. Review the final evidence, summarize risks, and archive related state when appropriate.");
  } else if (status === "stopped") {
    lines.push("");
    lines.push("This campaign is stopped. Do not continue it until the host or user explicitly resumes or creates a new campaign.");
  } else if (!task) {
    lines.push("");
    lines.push("No current task is selected. Add a task, set a task in progress, or close the campaign if it is complete.");
  } else {
    if (task.status === "failed") {
      phase = "failed";
      phaseGuidance =
        `Diagnose the failure, record a reflection, then retry the task ` +
        `with campaign task ${task.id} in_progress or skip it.`;
    } else {
      phase = CAMPAIGN_PHASES.includes(task.phase) ? task.phase : CAMPAIGN_PHASES[0];
      phaseGuidance = CAMPAIGN_PHASE_GUIDANCE[phase] || "Continue the workflow.";
    }
    nextCommand = `mythify campaign advance ${slug} --result "<phase evidence>"`;
    lines.push("");
    lines.push(`Current task ${task.id}: ${task.title || ""}`);
    lines.push(`Task status: ${task.status || ""}`);
    lines.push(`Task criteria: ${task.success_criteria || "not specified"}`);
    lines.push(`Phase: ${phase}`);
    lines.push(`Phase guidance: ${phaseGuidance}`);
    if (learningLines.length > 0) {
      lines.push("");
      lines.push("Recent learnings:");
      for (const learning of learningLines) {
        lines.push(`- ${learning}`);
      }
    }
    lines.push("");
    lines.push("Instructions:");
    lines.push("- Work only on this current phase unless the host has already completed it.");
    lines.push("- Bring findings, failed checks, and uncertainty into the chat as they happen.");
    lines.push("- When this phase reaches verify, run the nearest executable check.");
    lines.push(`- When the phase is done, advance the durable frontier with: ${nextCommand}`);
  }
  lines.push("");
  lines.push(`Guardrail: ${CAMPAIGN_PROMPT_GUARDRAIL}`);
  return {
    id: slug,
    goal: record?.goal || "",
    status,
    progress: { completed, total },
    success_criteria: record?.success_criteria || "",
    verify_command: verifyCommand,
    current_task: task ? { ...task } : null,
    phase,
    phase_guidance: phaseGuidance,
    recent_learnings: learningLines,
    next_action: campaignNextAction(record),
    next_command: nextCommand,
    next_prompt: lines.join("\n"),
    guardrail: CAMPAIGN_PROMPT_GUARDRAIL,
  };
}

function formatCampaignPromptPayload(payload) {
  return `[OK] Campaign prompt: ${payload.id}\n${payload.next_prompt || ""}`;
}

// ---------------------------------------------------------------------------
// Prompt packets
// ---------------------------------------------------------------------------

function activePlanPacketContext() {
  const slug = readActiveSlug();
  if (!slug || !fs.existsSync(planPath(slug))) {
    return null;
  }
  const plan = readJsonRecover(planPath(slug), () => null);
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) {
    return null;
  }
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const completed = steps.filter((step) => step.status === "completed").length;
  return {
    slug,
    goal: plan.goal || "",
    progress: { completed, total: steps.length },
    current_step: steps.find((step) => step.status === "in_progress") || null,
    next_pending: steps.find((step) => step.status === "pending") || null,
    steps,
  };
}

function latestFailedVerification() {
  const records = readJsonl(verificationsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.kind === "executed" && record.verified === false) {
      return [index + 1, record];
    }
  }
  return [null, null];
}

function latestExecutedVerification() {
  const records = readJsonl(verificationsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.kind === "executed") {
      return [index + 1, record];
    }
  }
  return [null, null];
}

function latestFailureReflection() {
  const records = readJsonl(reflectionsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.outcome === "failure") {
      return [index + 1, record];
    }
  }
  return [null, null];
}

function promptRecentEvidence(limit = 5) {
  const rows = buildVerificationHistoryView(limit).records || [];
  return rows.map((row) => ({
    verdict: row.verdict,
    label: verificationLabel(row),
    exit_code: row.exit_code,
    timestamp: row.timestamp || "",
  }));
}

function promptPlanLines(planContext) {
  if (!planContext) {
    return ["Active plan: none"];
  }
  const lines = [
    `Active plan: ${planContext.slug}`,
    `Plan goal: ${planContext.goal || "not specified"}`,
    `Plan progress: ${planContext.progress.completed}/${planContext.progress.total} steps completed`,
  ];
  const current = planContext.current_step;
  const pending = planContext.next_pending;
  if (current) {
    lines.push(`Current step: ${current.id}. ${current.title || ""}`);
    if (current.success_criteria) {
      lines.push(`Current criteria: ${current.success_criteria}`);
    }
  } else if (pending) {
    lines.push(`Next pending step: ${pending.id}. ${pending.title || ""}`);
    if (pending.success_criteria) {
      lines.push(`Next criteria: ${pending.success_criteria}`);
    }
  } else {
    lines.push("Next pending step: none");
  }
  return lines;
}

function promptGitContext() {
  const gitState = gitStatusSummary(process.cwd());
  const lines = [
    `Git branch: ${gitState.branch || "unknown"}`,
    `Git status: ${gitState.status || "unknown"}`,
    `Git detail: ${gitState.detail || ""}`,
  ];
  for (const changedPath of gitState.changed_paths || []) {
    lines.push(`Changed path: ${changedPath}`);
  }
  return [gitState, lines];
}

function buildPromptPacket(kind, { name = "", goal = "", verifyCommand = "" } = {}) {
  if (kind === "next") {
    const selected = selectNextPromptPacketKind();
    const payload = buildPromptPacket(selected, { name, goal, verifyCommand });
    if (payload.error) {
      return payload;
    }
    return {
      ...payload,
      kind: "next",
      selected_kind: selected,
      title: "Next workflow prompt packet",
      next_prompt: `Selected next packet: ${selected}\n\n${payload.next_prompt || ""}`,
    };
  }
  if (kind === "campaign") {
    const [slug, record] = loadCampaign(name);
    if (!record) {
      return { error: "[FAIL] Campaign not found. Start one with: campaign start GOAL" };
    }
    const campaignPayload = buildCampaignPromptPayload(slug, record);
    return {
      kind: "campaign",
      selected_kind: "campaign",
      title: "Campaign prompt packet",
      source: { type: "campaign", id: slug },
      context: campaignPayload,
      next_prompt: campaignPayload.next_prompt || "",
      guardrail: PROMPT_PACKET_GUARDRAIL,
    };
  }
  if (kind === "research") {
    return buildResearchPromptPacket({ name, goal, verifyCommand });
  }
  if (kind === "analysis") {
    return buildAnalysisPromptPacket({ goal, verifyCommand });
  }
  if (kind === "failure") {
    return buildFailurePromptPacket({ verifyCommand });
  }
  if (kind === "handoff") {
    return buildHandoffPromptPacket({ goal, verifyCommand });
  }
  if (kind === "review") {
    return buildReviewPromptPacket({ goal, verifyCommand });
  }
  return { error: `[FAIL] Unknown prompt packet kind: ${kind}` };
}

function buildResearchPromptPacket({ name = "", goal = "", verifyCommand = "" } = {}) {
  const [slug, record] = loadResearch(name);
  if (!record) {
    return { error: "[FAIL] Research not found. Start one with: research start QUESTION" };
  }
  const sources = Array.isArray(record.sources) ? record.sources : [];
  const claims = Array.isArray(record.claims) ? record.claims : [];
  const questions = Array.isArray(record.open_questions) ? record.open_questions : [];
  const decision = record.decision || "";
  const lines = [
    `Research to implementation prompt packet: ${slug}`,
    `Question: ${record.question || ""}`,
    `Status: ${record.status || "active"}`,
    `Sources: ${sources.length}; claims: ${claims.length}; open questions: ${questions.length}`,
  ];
  if (goal) {
    lines.push(`Implementation goal: ${goal}`);
  }
  if (decision) {
    lines.push(`Decision: ${decision}`);
  }
  if (claims.length > 0) {
    lines.push("Key claims:");
    for (const claim of claims.slice(-5)) {
      const source = claim.source_id ? ` source=${claim.source_id}` : "";
      lines.push(`- ${claim.id}: ${claim.claim || ""}${source}`);
      lines.push(`  evidence: ${claim.evidence || ""}`);
    }
  }
  if (questions.length > 0) {
    lines.push("Open questions:");
    for (const item of questions.slice(-5)) {
      lines.push(`- ${item.id}: ${item.question || ""}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Treat this research as material for direction, not proof of completion.");
  lines.push("- If a decision exists, implement the smallest next step consistent with it.");
  lines.push("- If open questions block implementation, answer those first and update the research record.");
  lines.push("- Convert implementation work into a plan, campaign, or outcome loop before claiming done.");
  if (verifyCommand) {
    lines.push(`- Suggested verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "research",
    selected_kind: "research",
    title: "Research to implementation prompt packet",
    source: { type: "research", id: slug },
    context: {
      question: record.question || "",
      status: record.status || "active",
      decision,
      sources: sources.slice(-5),
      claims: claims.slice(-5),
      open_questions: questions.slice(-5),
      goal,
      verify_command: verifyCommand,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildAnalysisPromptPacket({ goal = "", verifyCommand = "" } = {}) {
  const planContext = activePlanPacketContext();
  const recent = promptRecentEvidence(3);
  const lines = [
    "Analysis prompt packet",
    `Goal: ${goal || planContext?.goal || "infer from current project context"}`,
  ];
  lines.push(...promptPlanLines(planContext));
  if (recent.length > 0) {
    lines.push("Recent evidence:");
    for (const item of recent) {
      const exitText = item.exit_code === undefined || item.exit_code === null ? "" : ` exit ${item.exit_code}`;
      lines.push(`- ${item.verdict}: ${item.label}${exitText}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Read the smallest useful project context before editing.");
  lines.push("- Identify likely files, constraints, hidden risks, and the first reversible step.");
  lines.push("- Produce or update a plan with checkable success criteria.");
  lines.push("- Do not implement until the first step and verifier are explicit.");
  if (verifyCommand) {
    lines.push(`- Candidate verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "analysis",
    selected_kind: "analysis",
    title: "Analysis prompt packet",
    source: { type: "workflow_state", id: planContext?.slug || null },
    context: {
      goal,
      active_plan: planContext,
      recent_evidence: recent,
      verify_command: verifyCommand,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildFailurePromptPacket({ verifyCommand = "" } = {}) {
  const [index, record] = latestFailedVerification();
  const [reflectionIndex, reflection] = latestFailureReflection();
  const context = {
    failed_verification_index: index,
    failed_verification: record,
    failure_reflection_index: reflectionIndex,
    failure_reflection: reflection,
    verify_command: verifyCommand,
  };
  const lines = ["Failure recovery prompt packet"];
  if (record) {
    lines.push(`Failed verification #${index}: ${record.claim || record.command || ""}`);
    lines.push(`Command: ${record.command || ""}`);
    lines.push(`Exit code: ${record.exit_code}`);
    const stdoutTail = String(record.stdout_tail || "").trim();
    const stderrTail = String(record.stderr_tail || "").trim();
    if (stdoutTail) {
      lines.push(`Stdout tail: ${compactReportDetail(stdoutTail)}`);
    }
    if (stderrTail) {
      lines.push(`Stderr tail: ${compactReportDetail(stderrTail)}`);
    }
  } else {
    lines.push("No failed executed verification was found.");
  }
  if (reflection) {
    lines.push(`Latest failure reflection: ${reflection.action || ""}`);
    if (reflection.root_cause) {
      lines.push(`Recorded root cause: ${reflection.root_cause}`);
    }
    if (reflection.next) {
      lines.push(`Recorded next action: ${reflection.next}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Reproduce or inspect the failure before changing code.");
  lines.push("- Fix the smallest likely root cause.");
  lines.push("- Rerun the failed verifier, or the provided verifier if it is more specific.");
  lines.push("- Report the failure, fix, and verification evidence in chat.");
  lines.push("- If the fix is hard to reverse, first lay out 2-3 labeled approaches with tradeoffs, then recommend one.");
  if (verifyCommand) {
    lines.push(`- Verifier to run: ${verifyCommand}`);
  } else if (record?.command) {
    lines.push(`- Verifier to rerun: ${record.command}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "failure",
    selected_kind: "failure",
    title: "Failure recovery prompt packet",
    source: { type: "verification", id: index },
    context,
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildHandoffPromptPacket({ goal = "", verifyCommand = "" } = {}) {
  const planContext = activePlanPacketContext();
  const [campaignSlug, campaignRecord] = loadCampaign("");
  const [researchSlug, researchRecord] = loadResearch("");
  const report = buildWorkReport({
    since: "start",
    recent: 5,
    cursor: "handoff-prompt",
    peek: true,
    mark: false,
  });
  const lines = [
    "Handoff prompt packet",
    `Goal: ${goal || planContext?.goal || "continue current Mythify work"}`,
  ];
  lines.push(...promptPlanLines(planContext));
  if (campaignRecord) {
    lines.push(`Active campaign: ${campaignSlug}`);
    lines.push(`Campaign next action: ${campaignNextAction(campaignRecord)}`);
  }
  if (researchRecord) {
    lines.push(`Active research: ${researchSlug}`);
    lines.push(`Research question: ${researchRecord.question || ""}`);
  }
  if ((report.attention_events || []).length > 0) {
    lines.push("Attention items:");
    for (const event of (report.attention_events || []).slice(-5)) {
      lines.push(`- ${event.level}: ${event.summary}`);
    }
  }
  if ((report.events || []).length > 0) {
    lines.push("Recent events:");
    for (const event of (report.events || []).slice(-5)) {
      lines.push(`- ${event.summary}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Resume from this packet without assuming hidden chat context.");
  lines.push("- Re-read files before editing if the packet mentions uncertainty.");
  lines.push("- Continue the current step or campaign phase, then verify before claiming completion.");
  lines.push("- Surface any failed checks or warnings in chat.");
  if (verifyCommand) {
    lines.push(`- Suggested verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "handoff",
    selected_kind: "handoff",
    title: "Handoff prompt packet",
    source: { type: "workflow_state", id: planContext?.slug || null },
    context: {
      goal,
      active_plan: planContext,
      active_campaign: campaignRecord
        ? { id: campaignSlug, next_action: campaignNextAction(campaignRecord) }
        : null,
      active_research: researchRecord
        ? { id: researchSlug, question: researchRecord.question || "" }
        : null,
      recent_report: report,
      verify_command: verifyCommand,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function buildReviewPromptPacket({ goal = "", verifyCommand = "" } = {}) {
  const planContext = activePlanPacketContext();
  const [gitState, gitLines] = promptGitContext();
  const recent = promptRecentEvidence(5);
  const godAudit = godauditsSummary(artifactProjectRoot());
  const lines = [
    "Review prompt packet",
    `Goal: ${goal || "review current changes and risks"}`,
  ];
  lines.push(...gitLines);
  lines.push(...promptPlanLines(planContext));
  if (godAudit.present) {
    lines.push(`Godaudits audit: ${godAudit.path} (${godAudit.detail})`);
  }
  if (recent.length > 0) {
    lines.push("Recent evidence:");
    for (const item of recent) {
      const exitText = item.exit_code === undefined || item.exit_code === null ? "" : ` exit ${item.exit_code}`;
      lines.push(`- ${item.verdict}: ${item.label}${exitText}`);
    }
  }
  lines.push("");
  lines.push("Instructions:");
  lines.push("- Review changed files and relevant surrounding code.");
  lines.push("- Lead with actionable findings, with file and line references when possible.");
  lines.push("- Separate verified issues, warnings, open questions, and test gaps.");
  lines.push("- If fixes are requested, address findings one by one and verify the result.");
  lines.push("- For any hard-to-reverse fix, lay out 2-3 labeled approaches with tradeoffs before recommending one.");
  if (verifyCommand) {
    lines.push(`- Suggested verifier: ${verifyCommand}`);
  }
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "review",
    selected_kind: "review",
    title: "Review prompt packet",
    source: { type: "git", id: gitState.branch || null },
    context: {
      goal,
      git: gitState,
      active_plan: planContext,
      recent_evidence: recent,
      verify_command: verifyCommand,
      godaudits_audit: godAudit.present ? godAudit : null,
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
}

function selectNextPromptPacketKind() {
  const [, latest] = latestExecutedVerification();
  if (latest && latest.verified === false) {
    return "failure";
  }
  if (getActiveCampaignSlug()) {
    return "campaign";
  }
  if (getActiveResearchSlug()) {
    return "research";
  }
  if (readActiveSlug()) {
    return "handoff";
  }
  return "analysis";
}

function formatPromptPacket(payload) {
  const lines = [
    `[OK] Prompt packet ${payload.kind || "unknown"}: ${payload.selected_kind || payload.kind || "unknown"}`,
  ];
  if (payload.source) {
    lines.push(`Source: ${payload.source.type || ""} ${payload.source.id || ""}`);
  }
  lines.push("Next prompt:");
  lines.push(payload.next_prompt || "");
  lines.push(`Guardrail: ${payload.guardrail || PROMPT_PACKET_GUARDRAIL}`);
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Workflow router
// ---------------------------------------------------------------------------

function shellQuote(value) {
  const text = String(value || "task").trim() || "task";
  return `'${text.replaceAll("'", "'\"'\"'")}'`;
}

function artifactProjectRoot() {
  const stateDir = resolveStateDir();
  return path.basename(stateDir) === ".mythify" ? path.dirname(stateDir) : process.cwd();
}

function workflowRouteState() {
  const activePlanSlug = readActiveSlug();
  let activePlan = null;
  if (activePlanSlug && fs.existsSync(planPath(activePlanSlug))) {
    activePlan = readJsonRecover(planPath(activePlanSlug), () => null);
  }
  const activeOutcomeSlug = readActiveOutcomeSlug();
  let activeOutcome = null;
  if (activeOutcomeSlug && fs.existsSync(outcomeGoalPath(activeOutcomeSlug))) {
    activeOutcome = readJsonRecover(outcomeGoalPath(activeOutcomeSlug), () => null);
  }
  const [activeCampaignSlug, activeCampaign] = loadCampaign();
  const [activeResearchSlug, activeResearch] = loadResearch();
  const [latestIndex, latest] = latestExecutedVerification();
  let latestView = null;
  if (latest) {
    latestView = {
      index: latestIndex,
      verified: latest.verified,
      claim: latest.claim || "",
      command: latest.command || "",
      exit_code: latest.exit_code,
      timestamp: latest.timestamp || "",
    };
  }
  let planView = null;
  if (activePlan) {
    const steps = Array.isArray(activePlan.steps) ? activePlan.steps : [];
    const completed = steps.filter((step) => step?.status === "completed").length;
    const pending = steps.find((step) => step?.status === "pending") || null;
    planView = {
      id: activePlanSlug,
      goal: activePlan.goal || "",
      progress: { completed, total: steps.length },
      next_pending: pending
        ? {
            id: pending.id,
            title: pending.title || "",
            success_criteria: pending.success_criteria || "",
          }
        : null,
    };
  }
  let outcomeView = null;
  // Only an active outcome steers routing; a finished loop stays visible in
  // status and background views but must not be a routing target.
  if (activeOutcome && activeOutcome.status === "active") {
    outcomeView = {
      id: activeOutcomeSlug,
      goal: activeOutcome.goal || "",
      status: activeOutcome.status || "",
      iteration_count: activeOutcome.iteration_count || 0,
      max_iterations: activeOutcome.max_iterations || 0,
    };
  }
  let campaignView = null;
  if (activeCampaign) {
    const [completed, total] = campaignProgress(activeCampaign);
    campaignView = {
      id: activeCampaignSlug,
      goal: activeCampaign.goal || "",
      status: activeCampaign.status || "",
      phase: currentCampaignTask(activeCampaign)?.phase || "",
      progress: { completed, total },
    };
  }
  let researchView = null;
  if (activeResearch) {
    researchView = {
      id: activeResearchSlug,
      question: activeResearch.question || "",
      status: activeResearch.status || "",
      claim_count: Array.isArray(activeResearch.claims) ? activeResearch.claims.length : 0,
      source_count: Array.isArray(activeResearch.sources) ? activeResearch.sources.length : 0,
    };
  }
  const root = artifactProjectRoot();
  const godplansView = godplansSummary(root);
  const godauditsView = godauditsSummary(root);
  return {
    active_plan: planView,
    active_outcome: outcomeView,
    active_campaign: campaignView,
    active_research: researchView,
    latest_executed_verification: latestView,
    godplans_plan: godplansView.present ? godplansView : null,
    godaudits_audit: godauditsView.present ? godauditsView : null,
  };
}

function wordish(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]+/g, " ");
}

function containsAny(text, terms) {
  const haystack = ` ${wordish(text).trim().split(/\s+/).filter(Boolean).join(" ")} `;
  return terms.filter((term) => {
    const needle = wordish(term).trim().split(/\s+/).filter(Boolean).join(" ");
    return needle !== "" && haystack.includes(` ${needle} `);
  });
}

function routeHas(text, terms) {
  return containsAny(text, terms).length > 0;
}

function routeCommandFor(route, task, stateView) {
  const quotedTask = shellQuote(task);
  const packet = WORKFLOW_ROUTE_PROMPTS[route] || "next";
  if (route === "failure") {
    return "mythify prompt failure";
  }
  if (route === "campaign") {
    if (stateView.active_campaign) {
      return "mythify campaign prompt";
    }
    return `mythify campaign start ${quotedTask} --success ${shellQuote("done criteria are verified")}`;
  }
  if (route === "outcome") {
    if (stateView.active_outcome) {
      return "mythify outcome status";
    }
    return (
      `mythify outcome start ${quotedTask} ` +
      `--success ${shellQuote("DEFINE SUCCESS")} --verify ${shellQuote("DEFINE VERIFIER")}`
    );
  }
  if (route === "research") {
    if (stateView.active_research) {
      return "mythify prompt research";
    }
    return `mythify research start ${quotedTask}`;
  }
  if (route === "review") {
    if (godArtifactHasOpenTasks(stateView.godaudits_audit)) {
      return "mythify plan import --source godaudits";
    }
    return `mythify prompt review --goal ${quotedTask}`;
  }
  if (route === "handoff") {
    return `mythify prompt handoff --goal ${quotedTask}`;
  }
  if (route === "plan") {
    if (godArtifactHasOpenTasks(stateView.godplans_plan)) {
      return "mythify plan import --source godplans";
    }
    return `mythify plan create ${quotedTask} --horizon ${routePlanHorizon()}`;
  }
  if (route === "prompt") {
    return `mythify prompt ${packet}`;
  }
  return "Answer directly in the initiating chat; run verify run if an executable completion check exists.";
}

function routeStateWrites(route, stateView) {
  if (route === "failure") {
    return [
      "record reflection after diagnosing the red check",
      "record verify run after the recovery attempt",
      "update the active step with evidence when fixed",
    ];
  }
  if (route === "campaign") {
    if (stateView.active_campaign) {
      return [
        "campaign advance after the host completes the current task with evidence",
        "campaign learn when the next task should improve",
      ];
    }
    return ["campaign start when the host accepts the route"];
  }
  if (route === "outcome") {
    if (stateView.active_outcome) {
      return ["outcome check after each bounded attempt"];
    }
    return ["outcome start with explicit success criteria and verifier"];
  }
  if (route === "research") {
    if (stateView.active_research) {
      return ["research add-source", "research add-claim", "research close"];
    }
    return ["research start before implementation"];
  }
  if (route === "review") {
    if (godArtifactHasOpenTasks(stateView.godaudits_audit)) {
      return [
        "plan import --source godaudits when remediation is accepted",
        "step updates and verify run per remediation task",
        "report findings in chat",
      ];
    }
    return ["report findings in chat", "verify run supporting checks when fixes are made"];
  }
  if (route === "handoff") {
    return ["step updates and verify run as the active plan advances"];
  }
  if (route === "plan") {
    if (godArtifactHasOpenTasks(stateView.godplans_plan)) {
      return [
        "plan import --source godplans",
        "step updates",
        "verify run per imported task",
        "reflect on failures",
      ];
    }
    return ["plan create", "step updates", "verify run", "reflect on failures"];
  }
  return [];
}

function workflowRouteEvidence(route, stateView, classification) {
  const evidence = [
    {
      type: "router_manifest",
      version: WORKFLOW_ROUTER.version,
      routes: WORKFLOW_ROUTE_IDS,
    },
    {
      type: "classification",
      task_type: classification.task_type,
      risk: classification.risk,
      execution_profile: classification.execution_profile,
    },
  ];
  if (stateView.latest_executed_verification) {
    evidence.push({ type: "latest_executed_verification", ...stateView.latest_executed_verification });
  }
  for (const key of [
    "active_plan",
    "active_outcome",
    "active_campaign",
    "active_research",
    "godplans_plan",
    "godaudits_audit",
  ]) {
    if (stateView[key]) {
      evidence.push({ type: key, ...stateView[key] });
    }
  }
  evidence.push({ type: "route_decision", route, mutates_state: false });
  return evidence;
}

function selectWorkflowRoute(task, stateView, classification) {
  const text = String(task || "").toLowerCase().split(/\s+/).join(" ");
  const latest = stateView.latest_executed_verification;
  if (latest && latest.verified === false) {
    return [
      "failure",
      "The latest executed verification is red, so recover that failure before advancing unrelated work.",
    ];
  }
  if (routeHas(text, ROUTE_FULL_SEND_TERMS)) {
    return [
      "campaign",
      "The prompt uses full-send language, so route to a durable campaign loop with evidence-gated advancement.",
    ];
  }
  if (stateView.active_campaign && routeHas(text, ROUTE_RESUME_TERMS)) {
    return ["campaign", "An active campaign exists and the prompt asks to continue."];
  }
  if (routeHas(text, ROUTE_PROMPT_TERMS)) {
    return ["prompt", "The prompt asks for steering material rather than immediate execution."];
  }
  if (stateView.active_outcome && (routeHas(text, ROUTE_RESUME_TERMS) || routeHas(text, ROUTE_OUTCOME_TERMS))) {
    return ["outcome", "An active outcome loop exists and the prompt asks to continue or check it."];
  }
  if (routeHas(text, ROUTE_OUTCOME_TERMS) && routeHas(text, ROUTE_VERIFY_TERMS)) {
    return ["outcome", "The prompt names success or verification conditions, so use a bounded outcome loop."];
  }
  if (routeHas(text, ROUTE_GODAUDITS_TERMS)) {
    return ["review", "The prompt names godaudits, so route to review work around the .godaudits audit artifact."];
  }
  if (routeHas(text, ROUTE_GODPLANS_TERMS)) {
    return ["plan", "The prompt names godplans, so route to plan work around the .godplans plan artifact."];
  }
  if (classification.task_type === "research" || routeHas(text, ROUTE_RESEARCH_TERMS)) {
    return ["research", "The task depends on external, uncertain, or source-backed information."];
  }
  if (classification.task_type === "review" || routeHas(text, ROUTE_REVIEW_TERMS)) {
    return ["review", "The task asks for audit, review, evaluation, or issue finding."];
  }
  if (stateView.active_research && routeHas(text, ROUTE_RESUME_TERMS)) {
    return ["research", "An active research record exists and the prompt asks to continue."];
  }
  if (stateView.active_plan && routeHas(text, ROUTE_RESUME_TERMS)) {
    return ["handoff", "An active plan exists and the prompt asks to continue from durable state."];
  }
  if (classification.execution_profile === "direct") {
    return ["direct", "Classification says this is a simple question or single reversible action."];
  }
  return ["plan", "Classification says this is multi-step work that should be planned and verified."];
}

function buildWorkflowRoute(task, classification) {
  const stateView = workflowRouteState();
  let [route, reason] = selectWorkflowRoute(task, stateView, classification);
  if (!WORKFLOW_ROUTE_IDS.includes(route)) {
    route = "plan";
    reason = "Router returned an unknown route, so Mythify fell back to a verifiable plan.";
  }
  const godPlan = stateView.godplans_plan;
  const godAudit = stateView.godaudits_audit;
  if (route === "plan" && godArtifactHasOpenTasks(godPlan)) {
    reason +=
      ` A godplans plan exists at ${godPlan.path} (${godPlan.detail}); ` +
      "import it with plan import instead of drafting a new plan.";
  }
  if (route === "review" && godAudit) {
    reason += ` A godaudits audit exists at ${godAudit.path} (${godAudit.detail}).`;
  }
  const packetKind = WORKFLOW_ROUTE_PROMPTS[route] || "next";
  return {
    kind: "workflow_route",
    route,
    reason,
    input: String(task || ""),
    classification,
    state: stateView,
    next_command: routeCommandFor(route, task, stateView),
    prompt_packet: {
      kind: packetKind,
      command: `mythify prompt ${packetKind}`,
    },
    verification_strategy: classification.verification || "",
    chat_policy: {
      executor: "initiating_host",
      surface: "chat",
      report_issues: true,
      progress_command: "mythify report --since last --cursor chat --format chat",
      host_boundary: "Run the next step in the chat or host that initiated Mythify unless the user explicitly hands it elsewhere.",
    },
    pause_rules: [
      "destructive or irreversible actions",
      "real scope changes",
      "missing credentials, secrets, or billing acknowledgements",
      "decisions only the user can make",
    ],
    state_writes: routeStateWrites(route, stateView),
    evidence: workflowRouteEvidence(route, stateView, classification),
    guardrail: WORKFLOW_ROUTE_GUARDRAIL,
  };
}

function formatWorkflowRoute(payload) {
  const lines = [
    `[OK] Workflow route: ${payload.route || "unknown"}`,
    `Reason: ${payload.reason || ""}`,
    `Next command: ${payload.next_command || ""}`,
    `Prompt packet: ${payload.prompt_packet?.kind || ""} (${payload.prompt_packet?.command || ""})`,
    `Verification strategy: ${payload.verification_strategy || ""}`,
  ];
  const policy = payload.chat_policy || {};
  lines.push(
    `Chat policy: executor=${policy.executor || "initiating_host"}; ` +
      `surface=${policy.surface || "chat"}; report_issues=${String(policy.report_issues !== false)}`
  );
  if (payload.state_writes?.length > 0) {
    lines.push("Expected state writes:");
    for (const item of payload.state_writes) {
      lines.push(`- ${item}`);
    }
  }
  if (payload.pause_rules?.length > 0) {
    lines.push("Pause for:");
    for (const item of payload.pause_rules) {
      lines.push(`- ${item}`);
    }
  }
  lines.push(`Guardrail: ${payload.guardrail || WORKFLOW_ROUTE_GUARDRAIL}`);
  return lines.join("\n");
}

export function registerWorkflowTools(server, nextDeps) {
  deps = nextDeps;
  // ---------------------------------------------------------------------------
  // Classification tool
  // ---------------------------------------------------------------------------

  server.registerTool(
    "classify_task",
    {
      title: "Classify a task before planning",
      description:
        "Classify a user request when you only need task type, risk, recommended Mythify ceremony level, execution profile, verification strategy, or fanout fit. " +
        "For broad or ambiguous user prompts, call workflow_route first so Mythify can choose the full workflow path before this lower-level classification primitive is used.",
      inputSchema: {
        task: z.string().describe("The user request or problem statement to classify."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable routing."),
        triage: z
          .enum(TRIAGE_MODES)
          .optional()
          .describe("Run a fast model triage pass: never by default, auto when the gate recommends it, or always."),
        triage_engine: z
          .enum(TRIAGE_ENGINES)
          .optional()
          .describe("Fast triage engine. Defaults to MYTHIFY_TRIAGE_ENGINE, then codex-cli when available, then local auto-detection."),
        triage_model: z
          .string()
          .optional()
          .describe("Fast triage model. Defaults to MYTHIFY_TRIAGE_MODEL or the engine default."),
        triage_timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("Fast triage timeout in seconds. Defaults to 120."),
        platform: z
          .enum(PLATFORMS)
          .optional()
          .describe(
            "Host platform for model policy. Use codex-desktop, claude-desktop, or cursor-desktop when known; defaults to auto."
          ),
        effort: z
          .enum(EFFORT_LEVELS)
          .optional()
          .describe(
            "Overall effort preference for spawned model roles. Auto keeps triage cheap and scales workers or reviewers by risk."
          ),
        speed: z
          .enum(SPEED_LEVELS)
          .optional()
          .describe(
            "Overall speed preference for spawned model roles. Auto preserves host defaults; fast enables Codex fast mode where supported."
          ),
        session_model: z
          .string()
          .optional()
          .describe("Current host session model for spawn ceiling policy. Defaults to MYTHIFY_SESSION_MODEL."),
        spawn_ceiling: z
          .enum(SPAWN_CEILINGS)
          .optional()
          .describe(
            "Maximum spawned model tier relative to the session model. Auto uses MYTHIFY_SPAWN_CEILING or same_or_lower."
          ),
        reviewer_strength: z
          .enum(REVIEWER_STRENGTH_MODES)
          .optional()
          .describe(
            "Reviewer model strength relative to the session. Auto uses MYTHIFY_REVIEWER_STRENGTH or same_or_lower; allow_stronger is a reviewer-only opt-in."
          ),
      },
    },
    guarded(({
      task,
      format,
      triage,
      triage_engine,
      triage_model,
      triage_timeout_seconds,
      platform,
      effort,
      speed,
      session_model,
      spawn_ceiling,
      reviewer_strength,
    }) => {
      const result = classifyTaskText(task);
      result.model_policy = buildModelPolicy(result, {
        triage_engine: triage_engine || "",
        triage_model: triage_model || "",
        triage_timeout_seconds,
        platform: platform || "auto",
        effort: effort || "auto",
        speed: speed || "auto",
        session_model: session_model || "",
        host_model_record: readHostModelState(),
        spawn_ceiling: spawn_ceiling || "auto",
        reviewer_strength: reviewer_strength || "auto",
      });
      if ((triage || "never") !== "never") {
        result.model_triage_run = runModelTriage(task, result, {
          triage: triage || "never",
          triage_engine: triage_engine || "",
          triage_model: triage_model || "",
          triage_timeout_seconds,
          platform: platform || "auto",
          effort: effort || "auto",
          speed: speed || "auto",
          session_model: session_model || "",
          spawn_ceiling: spawn_ceiling || "auto",
          cwd: path.dirname(resolveStateDir()),
        });
      }
      if (format === "json") {
        return "[OK] " + JSON.stringify(result, null, 2);
      }
      return formatClassification(result);
    })
  );

  server.registerTool(
    "campaign_next_prompt",
    {
      title: "Render campaign next prompt",
      description:
        "Render a chat-ready next prompt for the active or named campaign's current task and phase. " +
        "Use this when a host wants Mythify campaign guidance inside the chat without mutating state, running checks, or treating prompt material as verification evidence." +
        MCP_FRONT_DOOR_NOTE,
      inputSchema: {
        name: z.string().optional().describe("Campaign name. Defaults to the active campaign."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ name, format }) => {
      const [slug, record] = loadCampaign(name || "");
      if (!record) {
        return "[FAIL] Campaign not found. Start one with: campaign start GOAL";
      }
      const payload = buildCampaignPromptPayload(slug, record);
      if (format === "json") {
        return `[OK] ${JSON.stringify(payload, null, 2)}`;
      }
      return formatCampaignPromptPayload(payload);
    })
  );

  server.registerTool(
    "prompt_packet",
    {
      title: "Render workflow prompt packet",
      description:
        "Render a chat-ready prompt packet for research, analysis, failure recovery, handoff, review, campaign, or the next useful workflow move. " +
        "Use this when a host wants Mythify guidance inside the chat without mutating state, running checks, or treating prompt material as verification evidence." +
        MCP_FRONT_DOOR_NOTE,
      inputSchema: {
        kind: z
          .enum(PROMPT_PACKET_KINDS)
          .default("next")
          .describe("Packet kind: research, analysis, failure, handoff, review, campaign, or next."),
        name: z.string().optional().describe("Research or campaign name. Defaults to the active record."),
        goal: z.string().optional().describe("Optional host goal to include in the packet."),
        verify_command: z.string().optional().describe("Optional verifier command to include in the packet."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
      },
    },
    guarded(({ kind, name, goal, verify_command, format }) => {
      const payload = buildPromptPacket(kind || "next", {
        name: name || "",
        goal: goal || "",
        verifyCommand: verify_command || "",
      });
      if (payload.error) {
        return payload.error;
      }
      if (format === "json") {
        return `[OK] ${JSON.stringify(payload, null, 2)}`;
      }
      return formatPromptPacket(payload);
    })
  );

  server.registerTool(
    "workflow_route",
    {
      title: "Choose workflow route",
      description:
        "Read-only workflow quarterback. Classify a prompt, inspect durable Mythify state, and choose direct, plan, research, review, outcome, campaign, failure recovery, handoff, or prompt packet routing. " +
        "Use this when the host wants Mythify to steer the next chat-native workflow move without mutating state or treating route output as verification evidence." +
        MCP_WORKFLOW_ROUTE_NOTE,
      inputSchema: {
        task: z.string().describe("The user request or problem statement to route."),
        format: z.enum(["text", "json"]).optional().describe("Return text or JSON. Defaults to text."),
        triage: z
          .enum(TRIAGE_MODES)
          .optional()
          .describe("Run a fast model triage pass: never by default, auto when the gate recommends it, or always."),
        triage_engine: z
          .enum(TRIAGE_ENGINES)
          .optional()
          .describe("Fast triage engine. Defaults to MYTHIFY_TRIAGE_ENGINE, then codex-cli when available, then local auto-detection."),
        triage_model: z
          .string()
          .optional()
          .describe("Fast triage model. Defaults to MYTHIFY_TRIAGE_MODEL or the engine default."),
        triage_timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("Fast triage timeout in seconds. Defaults to 120."),
        platform: z
          .enum(PLATFORMS)
          .optional()
          .describe("Host platform for model policy. Defaults to auto."),
        effort: z
          .enum(EFFORT_LEVELS)
          .optional()
          .describe("Overall effort preference for spawned model roles."),
        speed: z
          .enum(SPEED_LEVELS)
          .optional()
          .describe("Overall speed preference for spawned model roles."),
        session_model: z
          .string()
          .optional()
          .describe("Current host session model for spawn ceiling policy. Defaults to MYTHIFY_SESSION_MODEL."),
        spawn_ceiling: z
          .enum(SPAWN_CEILINGS)
          .optional()
          .describe("Maximum spawned model tier relative to the session model."),
        reviewer_strength: z
          .enum(REVIEWER_STRENGTH_MODES)
          .optional()
          .describe("Reviewer model strength relative to the session."),
      },
    },
    guarded(({
      task,
      format,
      triage,
      triage_engine,
      triage_model,
      triage_timeout_seconds,
      platform,
      effort,
      speed,
      session_model,
      spawn_ceiling,
      reviewer_strength,
    }) => {
      const classification = classifyTaskText(task);
      classification.model_policy = buildModelPolicy(classification, {
        triage_engine: triage_engine || "",
        triage_model: triage_model || "",
        triage_timeout_seconds,
        platform: platform || "auto",
        effort: effort || "auto",
        speed: speed || "auto",
        session_model: session_model || "",
        host_model_record: readHostModelState(),
        spawn_ceiling: spawn_ceiling || "auto",
        reviewer_strength: reviewer_strength || "auto",
      });
      if ((triage || "never") !== "never") {
        classification.model_triage_run = runModelTriage(task, classification, {
          triage: triage || "never",
          triage_engine: triage_engine || "",
          triage_model: triage_model || "",
          triage_timeout_seconds,
          platform: platform || "auto",
          effort: effort || "auto",
          speed: speed || "auto",
          session_model: session_model || "",
          spawn_ceiling: spawn_ceiling || "auto",
          cwd: path.dirname(resolveStateDir()),
        });
      }
      const payload = buildWorkflowRoute(task, classification);
      if (format === "json") {
        return `[OK] ${JSON.stringify(payload, null, 2)}`;
      }
      return formatWorkflowRoute(payload);
    })
  );
}
