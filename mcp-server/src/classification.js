import fs from "node:fs";

const CLASSIFICATION_RULES_PATH = new URL("../protocol/classification-rules.json", import.meta.url);

function loadClassificationRules() {
  const manifest = JSON.parse(fs.readFileSync(CLASSIFICATION_RULES_PATH, "utf8"));
  const seen = new Set();
  for (const entry of manifest.task_types || []) {
    const taskType = String(entry?.id || "").trim();
    const terms = entry?.terms;
    if (!taskType || seen.has(taskType) || !Array.isArray(terms) || terms.length === 0) {
      throw new Error("Invalid classification rule entry");
    }
    seen.add(taskType);
  }
  if (seen.size === 0) {
    throw new Error("Classification rules manifest is empty");
  }
  const requiredSections = [
    "thresholds",
    "risk",
    "ceremony",
    "fanout",
    "fanout_visibility",
    "execution_profile",
    "next_actions",
    "model_triage",
    "verification_hints",
  ];
  for (const section of requiredSections) {
    if (!manifest[section] || typeof manifest[section] !== "object" || Array.isArray(manifest[section])) {
      throw new Error("Invalid classification policy section");
    }
  }
  if (!manifest.verification_hints.feature) {
    throw new Error("Classification verification hints are missing feature fallback");
  }
  return manifest;
}

function classificationTaskRules(manifest) {
  return (manifest.task_types || []).map((entry) => [
    String(entry.id),
    (entry.terms || []).map(String),
  ]);
}

const CLASSIFICATION_MANIFEST = loadClassificationRules();
const CLASSIFICATION_RULES = classificationTaskRules(CLASSIFICATION_MANIFEST);
const CLASSIFICATION_THRESHOLDS = CLASSIFICATION_MANIFEST.thresholds;
const TRIVIAL_WORD_COUNT = Number(CLASSIFICATION_THRESHOLDS.trivial_word_count);
const HIGH_AMBIGUITY_WORD_COUNT = Number(CLASSIFICATION_THRESHOLDS.high_ambiguity_word_count);
const MEDIUM_AMBIGUITY_WORD_COUNT = Number(CLASSIFICATION_THRESHOLDS.medium_ambiguity_word_count);
const QUESTION_PREFIXES = CLASSIFICATION_MANIFEST.question_prefixes.map(String);
const VAGUE_REQUEST_TERMS = CLASSIFICATION_MANIFEST.vague_request_terms.map(String);
const RISK_POLICY = CLASSIFICATION_MANIFEST.risk;
const HIGH_RISK_TERMS = RISK_POLICY.high_terms.map(String);
const HIGH_RISK_TASK_TYPES = RISK_POLICY.high_task_types.map(String);
const MEDIUM_RISK_TERMS = RISK_POLICY.medium_terms.map(String);
const MEDIUM_RISK_TASK_TYPES = RISK_POLICY.medium_task_types.map(String);
const CEREMONY_POLICY = CLASSIFICATION_MANIFEST.ceremony;
const FANOUT_POLICY = CLASSIFICATION_MANIFEST.fanout;
const FANOUT_VISIBILITY_POLICY = CLASSIFICATION_MANIFEST.fanout_visibility;
const EXECUTION_PROFILE_POLICY = CLASSIFICATION_MANIFEST.execution_profile;
const NEXT_ACTIONS = CLASSIFICATION_MANIFEST.next_actions;
const MODEL_TRIAGE_POLICY = CLASSIFICATION_MANIFEST.model_triage;
const VERIFICATION_HINTS = CLASSIFICATION_MANIFEST.verification_hints;

const TRIAGE_OUTPUT_SHAPE = {
  primary_type: "string",
  secondary_types: ["string"],
  ambiguity: "low|medium|high",
  hidden_questions: ["string"],
  likely_files_or_surfaces: ["string"],
  verification_plan: ["string"],
  fanout_plan: ["string"],
  risk_notes: ["string"],
  recommended_first_step: "string",
};

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

function classifyAmbiguity(text, words, signals, scores, taskType) {
  if (["question", "trivial"].includes(taskType)) {
    return "low";
  }
  if (
    containsAny(text, VAGUE_REQUEST_TERMS).length > 0 ||
    (signals.length === 0 && words.length <= HIGH_AMBIGUITY_WORD_COUNT)
  ) {
    return "high";
  }
  if (Object.keys(scores).length > 1 || words.length > MEDIUM_AMBIGUITY_WORD_COUNT) {
    return "medium";
  }
  return "low";
}

function modelTriageGate(taskType, risk, ceremony, ambiguity, text) {
  if (ceremony === "none") {
    return [
      "skip",
      MODEL_TRIAGE_POLICY.none_reason,
    ];
  }
  const highImpactTerms = MODEL_TRIAGE_POLICY.high_impact_terms.map(String);
  if (risk === "high" && ambiguity === "high" && containsAny(text, highImpactTerms).length > 0) {
    return [
      "required",
      MODEL_TRIAGE_POLICY.high_impact_required_reason,
    ];
  }
  if (ambiguity === "high") {
    return [
      "recommended",
      MODEL_TRIAGE_POLICY.high_ambiguity_reason,
    ];
  }
  if (MODEL_TRIAGE_POLICY.recommended_task_types.map(String).includes(taskType)) {
    return [
      "recommended",
      MODEL_TRIAGE_POLICY.recommended_reason,
    ];
  }
  if (MODEL_TRIAGE_POLICY.optional_task_types.map(String).includes(taskType) || risk === "medium") {
    return [
      "optional",
      MODEL_TRIAGE_POLICY.optional_reason,
    ];
  }
  return [
    "skip",
    MODEL_TRIAGE_POLICY.skip_reason,
  ];
}

function inferFanoutVisibility(text) {
  const normalized = String(text || "").toLowerCase().split(/\s+/).join(" ");
  for (const mode of FANOUT_VISIBILITY_POLICY.modes) {
    if (containsAny(normalized, mode.terms.map(String)).length > 0) {
      return {
        visibility: String(mode.visibility),
        source: String(mode.source),
        reason: String(mode.reason),
      };
    }
  }
  const defaultMode = FANOUT_VISIBILITY_POLICY.default;
  return {
    visibility: String(defaultMode.visibility),
    source: String(defaultMode.source),
    reason: String(defaultMode.reason),
  };
}

function executionProfileFor(taskType, risk, ceremony, ambiguity, text) {
  if (ceremony === "none") {
    return [
      "direct",
      EXECUTION_PROFILE_POLICY.direct_reason,
    ];
  }
  if (ceremony === "full" || risk === "high") {
    return [
      "full",
      EXECUTION_PROFILE_POLICY.full_reason,
    ];
  }
  if (ambiguity === "high") {
    return [
      "standard",
      EXECUTION_PROFILE_POLICY.ambiguous_reason,
    ];
  }
  const focusedTerms = EXECUTION_PROFILE_POLICY.focused_terms.map(String);
  const fastTaskTypes = EXECUTION_PROFILE_POLICY.fast_task_types.map(String);
  const fastFocusedTaskTypes = EXECUTION_PROFILE_POLICY.fast_focused_task_types.map(String);
  if (
    fastTaskTypes.includes(taskType) ||
    (fastFocusedTaskTypes.includes(taskType) && containsAny(text, focusedTerms).length > 0)
  ) {
    return [
      "fast",
      EXECUTION_PROFILE_POLICY.fast_reason,
    ];
  }
  if (ceremony === "light") {
    return [
      "fast",
      EXECUTION_PROFILE_POLICY.light_reason,
    ];
  }
  return [
    "standard",
    EXECUTION_PROFILE_POLICY.standard_reason,
  ];
}

export function classifyTaskText(taskText) {
  const text = String(taskText || "").toLowerCase().split(/\s+/).join(" ");
  const words = text.replaceAll("/", " ").replaceAll("_", " ").split(/\s+/).filter(Boolean);
  const signals = [];
  const scores = {};
  for (const [taskType, terms] of CLASSIFICATION_RULES) {
    const matches = containsAny(text, terms);
    if (matches.length > 0) {
      scores[taskType] = matches.length;
      signals.push(...matches);
    }
  }
  let taskType;
  const scoreEntries = Object.entries(scores).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (scoreEntries.length > 0) {
    taskType = scoreEntries[0][0];
  } else if (
    text.endsWith("?") ||
    QUESTION_PREFIXES.some((prefix) => text.startsWith(prefix))
  ) {
    taskType = "question";
  } else if (containsAny(text, VAGUE_REQUEST_TERMS).length > 0) {
    taskType = "feature";
  } else if (words.length <= TRIVIAL_WORD_COUNT) {
    taskType = "trivial";
  } else {
    taskType = "feature";
  }

  let risk;
  if (containsAny(text, HIGH_RISK_TERMS).length > 0 || HIGH_RISK_TASK_TYPES.includes(taskType)) {
    risk = "high";
  } else if (
    containsAny(text, MEDIUM_RISK_TERMS).length > 0 ||
    MEDIUM_RISK_TASK_TYPES.includes(taskType)
  ) {
    risk = "medium";
  } else {
    risk = "low";
  }

  const ambiguity = classifyAmbiguity(text, words, signals, scores, taskType);

  let ceremony;
  if (CEREMONY_POLICY.none_low_risk_task_types.map(String).includes(taskType) && risk === "low") {
    ceremony = "none";
  } else if (risk === "low" && CEREMONY_POLICY.light_low_risk_task_types.map(String).includes(taskType)) {
    ceremony = "light";
  } else if (risk === "high" || CEREMONY_POLICY.full_task_types.map(String).includes(taskType)) {
    ceremony = "full";
  } else {
    ceremony = "standard";
  }

  let fanout;
  let fanoutReason;
  if (
    FANOUT_POLICY.recommended_task_types.map(String).includes(taskType) ||
    containsAny(text, FANOUT_POLICY.recommended_terms.map(String)).length > 0
  ) {
    fanout = "recommended";
    fanoutReason = FANOUT_POLICY.recommended_reason;
  } else if (
    FANOUT_POLICY.optional_task_types.map(String).includes(taskType) ||
    containsAny(text, FANOUT_POLICY.optional_terms.map(String)).length > 0
  ) {
    fanout = "optional";
    fanoutReason = FANOUT_POLICY.optional_reason;
  } else {
    fanout = "not_recommended";
    fanoutReason = FANOUT_POLICY.not_recommended_reason;
  }

  const [executionProfile, executionProfileReason] = executionProfileFor(
    taskType,
    risk,
    ceremony,
    ambiguity,
    text
  );

  let nextAction;
  if (executionProfile === "direct") {
    nextAction = NEXT_ACTIONS.direct;
  } else if (executionProfile === "fast") {
    nextAction = NEXT_ACTIONS.fast;
  } else if (executionProfile === "standard") {
    nextAction = NEXT_ACTIONS.standard;
  } else {
    nextAction = NEXT_ACTIONS.full;
  }

  const [modelTriage, modelTriageReason] = modelTriageGate(taskType, risk, ceremony, ambiguity, text);
  const fanoutVisibility = inferFanoutVisibility(text);

  return {
    task_type: taskType,
    risk,
    ambiguity,
    ceremony,
    execution_profile: executionProfile,
    execution_profile_reason: executionProfileReason,
    verification: VERIFICATION_HINTS[taskType] || VERIFICATION_HINTS.feature,
    fanout,
    fanout_reason: fanoutReason,
    fanout_visibility: fanoutVisibility.visibility,
    fanout_visibility_source: fanoutVisibility.source,
    fanout_visibility_reason: fanoutVisibility.reason,
    model_triage: modelTriage,
    model_triage_reason: modelTriageReason,
    signals: [...new Set(signals)].sort().slice(0, 10),
    next_action: nextAction,
  };
}

export function shouldRunModelTriage(result, mode) {
  if (mode === "never") {
    return false;
  }
  if (mode === "always") {
    return true;
  }
  return ["recommended", "required"].includes(result.model_triage);
}

export function buildTriagePrompt(taskText, classification) {
  return [
    "You are a fast triage model helping Mythify frame a task before the main agent plans.",
    "Do not edit files, run commands, or ask questions.",
    "Return only valid JSON with this exact shape:",
    JSON.stringify(TRIAGE_OUTPUT_SHAPE, null, 2),
    "",
    "User task:",
    String(taskText || ""),
    "",
    "Deterministic classification:",
    JSON.stringify(classification, null, 2),
    "",
    "Focus on the problem shape, likely hidden requirements, verification, risk, and whether independent fanout would help.",
  ].join("\n");
}

export function formatClassification(result) {
  const lines = [
    "[OK] Task classification",
    `type: ${result.task_type}`,
    `risk: ${result.risk}`,
    `ambiguity: ${result.ambiguity}`,
    `ceremony: ${result.ceremony}`,
    `execution profile: ${result.execution_profile} (${result.execution_profile_reason})`,
    `verification: ${result.verification}`,
    `fanout: ${result.fanout} (${result.fanout_reason})`,
    `fanout visibility: ${result.fanout_visibility || "summary"} (${result.fanout_visibility_reason || "Summary visibility is the default."})`,
    `model triage: ${result.model_triage} (${result.model_triage_reason})`,
    `next: ${result.next_action}`,
  ];
  if (result.signals.length > 0) {
    lines.push(`signals: ${result.signals.join(", ")}`);
  }
  const policy = result.model_policy;
  if (policy) {
    const recommendation = policy.session?.recommendation || {};
    const roles = policy.provider_defaults?.roles || {};
    if (Object.keys(roles).length > 0) {
      lines.push(
        `providers: session=${roles.session?.provider || "host"}; ` +
        `triage=${roles.triage?.provider || "host_cli"}; ` +
        `reader=${roles.reader?.provider || "local_openai_compatible"}; ` +
        `worker=${roles.fanout_worker?.provider || "host_cli"}; ` +
        `reviewer=${roles.reviewer?.provider || "host_cli"}; ` +
        `verifier=${roles.verifier?.provider || "local_command"}`
      );
    }
    lines.push(
      `model policy: session=${policy.session?.control || "host_selected"}/${policy.session?.model_tier || "unknown"}; ` +
      `ceiling=${policy.spawn_ceiling?.policy || "same_or_lower"}; ` +
      `triage=${policy.triage?.engine || "auto"}/${policy.triage?.model_policy || "engine_default"}/${policy.triage?.effort || "low"}/${policy.triage?.speed || "auto"}; ` +
      `fanout=${policy.fanout_worker?.engine_policy || "local_first"}/${policy.fanout_worker?.effort || "medium"}/${policy.fanout_worker?.speed || "auto"}/${policy.fanout_worker?.visibility || "summary"}; ` +
      `verifier=${policy.verifier?.engine || "local_command"}`
    );
    lines.push(
      `reviewer opt-in: ${policy.reviewer?.stronger_model_policy || "same_or_lower"} ` +
      `(${policy.reviewer?.stronger_model_policy_source || "default"})`
    );
    lines.push(
      `host recommendation: ${recommendation.action || "recommend_set"} to ` +
      `${recommendation.target_profile || "standard"}/${recommendation.target_model || ""} ` +
      `thinking=${recommendation.thinking || "medium"} speed=${recommendation.speed || "auto"}`
    );
  }
  const run = result.model_triage_run;
  if (run) {
    if (!run.attempted) {
      lines.push(`fast triage run: skipped (${run.reason || ""})`);
    } else if (run.ok) {
      lines.push(`fast triage run: [OK] ${run.engine} model=${run.model || ""} duration=${run.duration_seconds}s`);
      if (run.parsed !== null && run.parsed !== undefined) {
        lines.push(`fast triage json: ${JSON.stringify(run.parsed)}`);
      } else if (run.output_tail) {
        lines.push(`fast triage output: ${run.output_tail}`);
      }
    } else {
      lines.push(`fast triage run: [FAIL] ${run.error || "triage worker failed"}`);
    }
  }
  return lines.join("\n");
}
