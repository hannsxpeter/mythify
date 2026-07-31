import fs from "node:fs";
import { godauditsSummary } from "./godfiles-core.js";
import {
  frontierTickets as mapFrontierTickets,
  mapIsClear,
  mapNextAction as mapNextActionText,
  openTickets as mapOpenTickets,
  ticketLine as mapTicketLine,
  ticketName as mapTicketName,
  ungraduatedFog as mapUngraduatedFog,
} from "./map-tools.js";

export const PROMPT_PACKET_KINDS = [
  "research",
  "analysis",
  "failure",
  "handoff",
  "review",
  "campaign",
  "map",
  "next",
];
export const PROMPT_PACKET_GUARDRAIL =
  "Prompt packet output is steering material for the host agent, not verification evidence. " +
  "The host must do the work, run checks when available, report issues in chat, and record evidence.";

// Same dependency-injection shape workflow-tools.js uses: the packet builders
// read durable state through helpers the server wires in at registration time.
let deps = {};

export function configurePromptPackets(nextDeps) {
  deps = { ...deps, ...nextDeps };
}

function requireDep(name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`prompt-packets requires deps.${name}`);
  }
  return value;
}

function readActiveSlug() { return requireDep("readActiveSlug")(); }
function planPath(slug) { return requireDep("planPath")(slug); }
function readJsonRecover(filePath, defaultFactory) { return requireDep("readJsonRecover")(filePath, defaultFactory); }
function readJsonl(filePath) { return requireDep("readJsonl")(filePath); }
function verificationsPath() { return requireDep("verificationsPath")(); }
function reflectionsPath() { return requireDep("reflectionsPath")(); }
function buildVerificationHistoryView(limit) { return requireDep("buildVerificationHistoryView")(limit); }
function verificationLabel(row) { return requireDep("verificationLabel")(row); }
function gitStatusSummary(root) { return requireDep("gitStatusSummary")(root); }
function compactReportDetail(text) { return requireDep("compactReportDetail")(text); }
function buildWorkReport(args) { return requireDep("buildWorkReport")(args); }
function artifactProjectRoot() { return requireDep("artifactProjectRoot")(); }
function loadCampaign(name) { return requireDep("loadCampaign")(name); }
function loadResearch(name) { return requireDep("loadResearch")(name); }
function loadMapRecord(name) { return requireDep("loadMapRecord")(name); }
function getActiveCampaignSlug() { return requireDep("getActiveCampaignSlug")(); }
function getActiveResearchSlug() { return requireDep("getActiveResearchSlug")(); }
function getActiveMapSlug() { return requireDep("getActiveMapSlug")(); }
function campaignNextAction(record) { return requireDep("campaignNextAction")(record); }
function buildCampaignPromptPayload(slug, record) { return requireDep("buildCampaignPromptPayload")(slug, record); }

export function activePlanPacketContext() {
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

export function latestFailedVerification() {
  const records = readJsonl(verificationsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.kind === "executed" && record.verified === false) {
      return [index + 1, record];
    }
  }
  return [null, null];
}

export function latestExecutedVerification() {
  const records = readJsonl(verificationsPath());
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (record.kind === "executed") {
      return [index + 1, record];
    }
  }
  return [null, null];
}

export function latestFailureReflection() {
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

export function buildPromptPacket(kind, { name = "", goal = "", verifyCommand = "" } = {}) {
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
  if (kind === "map") {
    return buildMapPromptPacket({ name, goal });
  }
  return { error: `[FAIL] Unknown prompt packet kind: ${kind}` };
}

function buildMapPromptPacket({ name = "", goal = "" } = {}) {
  const [slug, record] = loadMapRecord(name);
  if (!record) {
    return { error: "[FAIL] Map not found. Create one with: map create DESTINATION" };
  }
  const frontier = mapFrontierTickets(record);
  const claimed = mapOpenTickets(record).filter((ticket) => ticket.claimed_by);
  const fog = mapUngraduatedFog(record);
  const decisions = Array.isArray(record.decisions) ? record.decisions : [];
  const outOfScope = Array.isArray(record.out_of_scope) ? record.out_of_scope : [];
  const lines = [
    `Wayfinding map prompt packet: ${slug}`,
    `Destination: ${record.destination || ""}`,
    `Status: ${record.status || "charting"}`,
  ];
  if (goal) {
    lines.push(`Session goal: ${goal}`);
  }
  if (record.notes) {
    lines.push(`Notes: ${record.notes}`);
  }
  lines.push(`Decisions so far (${decisions.length}):`);
  for (const decision of decisions.slice(-8)) {
    lines.push(`- ${decision.title || ""} (${decision.ticket_id || ""}) - ${decision.gist || ""}`);
  }
  lines.push(`Frontier (${frontier.length}):`);
  for (const ticket of frontier) {
    lines.push(mapTicketLine(record, ticket));
  }
  if (claimed.length > 0) {
    lines.push(`Already claimed (${claimed.length}):`);
    for (const ticket of claimed) {
      lines.push(mapTicketLine(record, ticket));
    }
  }
  if (fog.length > 0) {
    lines.push(`Not yet specified (${fog.length}):`);
    for (const item of fog.slice(-8)) {
      lines.push(`- ${item.id}: ${item.note || ""}`);
    }
  }
  if (outOfScope.length > 0) {
    lines.push(`Out of scope (${outOfScope.length}):`);
    for (const item of outOfScope.slice(-8)) {
      lines.push(`- ${item.id}: ${item.note || ""}`);
    }
  }
  lines.push(
    "",
    "Instructions:",
    "- Plan, do not do: every ticket resolves a decision, not a slice of the build.",
    "- Refer to the map and its tickets by name; a bare id reads as noise.",
    "- Claim one frontier ticket with map claim before any work; only research tickets run in parallel.",
    "- Resolve a HITL ticket only after the human answers, and pass their words as human input.",
    "- Resolve a task ticket that stores a verify command only after map verify passes.",
    "- Record the answer with map resolve, then ticket what the answer made specifiable and fog what it did not.",
    "- If the answer shows a ticket sits past the destination, rule it out of scope instead of resolving it.",
    "- Stop after one decision ticket; the map survives the session, the context window does not.",
    "- When no ticket and no fog remain, hand off with map promote.",
    `Next action: ${mapNextActionText(record)}`
  );
  lines.push(`Guardrail: ${PROMPT_PACKET_GUARDRAIL}`);
  return {
    kind: "map",
    selected_kind: "map",
    title: "Wayfinding map prompt packet",
    source: { type: "map", id: slug },
    context: {
      destination: record.destination || "",
      status: record.status || "charting",
      notes: record.notes || "",
      goal,
      decisions: decisions.slice(-8),
      frontier: frontier.map((ticket) => ({
        id: ticket.id,
        name: mapTicketName(ticket),
        type: ticket.type,
        mode: ticket.mode,
      })),
      claimed: claimed.map(mapTicketName),
      fog: fog.slice(-8),
      out_of_scope: outOfScope.slice(-8),
      clear: mapIsClear(record),
      next_action: mapNextActionText(record),
    },
    next_prompt: lines.join("\n"),
    guardrail: PROMPT_PACKET_GUARDRAIL,
  };
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
  lines.push("- For any hard-to-reverse fix, lay out 2-3 labeled approaches with tradeoffs, name the one that looks good but is not and why, then recommend one."); lines.push("- Produce or update a plan with checkable success criteria.");
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

export function selectNextPromptPacketKind() {
  const [, latest] = latestExecutedVerification();
  if (latest && latest.verified === false) {
    return "failure";
  }
  if (getActiveCampaignSlug()) {
    return "campaign";
  }
  if (getActiveMapSlug()) {
    return "map";
  }
  if (getActiveResearchSlug()) {
    return "research";
  }
  if (readActiveSlug()) {
    return "handoff";
  }
  return "analysis";
}

export function formatPromptPacket(payload) {
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

