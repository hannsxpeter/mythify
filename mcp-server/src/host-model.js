import {
  HOST_PLATFORMS as PLATFORMS,
  HOST_THINKING_LEVELS,
  SPEED_LEVELS,
  getHostCapability,
} from "./capability-registry.js";

export function normalizeHostPlatform(platform) {
  const value = (platform || "auto").trim();
  return PLATFORMS.includes(value) ? value : "auto";
}

export function normalizeHostThinking(thinking) {
  const value = (thinking || "auto").trim();
  return HOST_THINKING_LEVELS.includes(value) ? value : "auto";
}

export function normalizeHostSpeed(speed) {
  const value = (speed || "auto").trim();
  return SPEED_LEVELS.includes(value) ? value : "auto";
}

export function detectHostPlatform(platform, env = process.env) {
  const explicit = normalizeHostPlatform(platform);
  if (explicit !== "auto") {
    return explicit;
  }
  if ((env.CODEX_THREAD_ID || "").trim() !== "") {
    return "codex-desktop";
  }
  if ((env.CLAUDECODE || "").trim() !== "" || (env.CLAUDE_CODE_ENTRYPOINT || "").trim() !== "") {
    return "claude-code";
  }
  return "unknown";
}

export function hostSwitchActions(platform, targetModel, thinking, speed, env = process.env) {
  const actions = [];
  if (platform === "codex-desktop") {
    actions.push(
      "Use the Codex Desktop model picker for the current chat."
    );
    const threadId = (env.CODEX_THREAD_ID || "").trim();
    if (threadId !== "") {
      actions.push(
        `Codex app agents can continue this thread with model override: send_message_to_thread(threadId="${threadId}", model="${targetModel}"${thinking !== "auto" ? `, thinking="${thinking}"` : ""}).`
      );
    } else {
      actions.push(
        "Codex app agents can use send_message_to_thread with a model override when they know the target thread id."
      );
    }
  } else if (platform === "codex-cli") {
    actions.push(`Start or resume Codex with --model ${targetModel}.`);
    if (thinking !== "auto") {
      actions.push(`Use the host's reasoning effort control for ${thinking} when available.`);
    }
    if (speed !== "auto") {
      actions.push(`Use Codex speed ${speed} for spawned workers; host chat speed remains host-controlled.`);
    }
  } else if (platform === "claude-code") {
    actions.push(`In interactive Claude Code, run /model ${targetModel}.`);
    actions.push(`For a new Claude Code session, start with claude --model ${targetModel}.`);
  } else if (platform === "claude-desktop") {
    actions.push("Use the Claude Desktop model picker for the current chat.");
    actions.push("MCP servers cannot directly mutate Claude Desktop's active chat model.");
  } else if (platform === "cursor-desktop") {
    actions.push("Use the Cursor chat model picker for the current chat.");
    actions.push("For spawned Cursor Agent workers, pass model, effort, and speed through fanout_start.");
  } else if (platform === "cursor-agent") {
    actions.push(`Start or resume Cursor Agent with --model ${targetModel}.`);
    actions.push("For Mythify fanout workers, pass model per task or per job.");
  } else {
    actions.push("Use the host app's model picker or model command for the current chat.");
    actions.push("Mythify has recorded the target model for session policy and spawn ceiling checks.");
  }
  return actions;
}

export function hostCapabilityForRecord(platform) {
  const capability = getHostCapability(platform);
  return {
    kind: capability.kind,
    status: capability.status,
    can_switch_current_thread: capability.can_switch_current_thread,
    can_set_new_thread_model: capability.can_set_new_thread_model,
    can_set_worker_model: capability.can_set_worker_model,
    can_set_thinking: capability.can_set_thinking,
    can_list_models: capability.can_list_models,
    can_confirm_current_model: capability.can_confirm_current_model,
  };
}

export function hostAdapterProofStatus(capability, key) {
  if (capability.status === "unknown") {
    return "unknown";
  }
  return capability[key] ? "supported" : "unsupported";
}

export function buildHostAdapterProofPath(capability, key, currentChat) {
  return {
    status: hostAdapterProofStatus(capability, key),
    proof_source: `host_capability.${key}`,
    current_chat_path: Boolean(currentChat),
    requires_executed_host_evidence: true,
  };
}

export function buildHostAdapterProofScan(platform, capability, checkedAt) {
  return {
    status: "metadata_only",
    platform,
    proof_source: "host_capability_registry",
    checked_at: checkedAt || "",
    host_state_mutated: false,
    writes_state: false,
    verification_recorded: false,
    material_not_evidence: true,
    guardrail: "current_chat_apply_or_confirm_requires_executed_host_evidence",
    paths: {
      current_chat_model_apply: buildHostAdapterProofPath(
        capability,
        "can_switch_current_thread",
        true
      ),
      current_chat_model_confirm: buildHostAdapterProofPath(
        capability,
        "can_confirm_current_model",
        true
      ),
      new_thread_model_apply: buildHostAdapterProofPath(
        capability,
        "can_set_new_thread_model",
        false
      ),
      worker_model_apply: buildHostAdapterProofPath(
        capability,
        "can_set_worker_model",
        false
      ),
      thinking_apply: buildHostAdapterProofPath(capability, "can_set_thinking", false),
    },
  };
}

export function buildHostSwitchResult(platform, targetModel, currentModel, thinking, speed, capability) {
  return {
    status: "manual",
    requested_model: targetModel,
    requested_thinking: thinking,
    requested_speed: speed,
    current_model: currentModel,
    current_thinking: "",
    current_chat_supported: Boolean(capability.can_switch_current_thread),
    current_chat_confirmed: false,
    manual_action_required: true,
    applied_by: "none",
    reason: "host_current_chat_unconfirmed",
  };
}

export function buildHostConfirmation(targetModel, currentModel, thinking, capability, checkedAt) {
  const canConfirm = Boolean(capability.can_confirm_current_model);
  return {
    requested_model: targetModel,
    user_reported_current_model: currentModel,
    user_reported_current_thinking: thinking !== "auto" ? thinking : "",
    current_model_confirmed: false,
    confirmed_current_model: "",
    confirmed_current_thinking: "",
    confirmation_status: canConfirm ? "unconfirmed" : "unsupported",
    confirmation_source: "none",
    confirmation_checked_at: checkedAt || "",
    confirmed_at: "",
    unsupported_reason: canConfirm
      ? "host_adapter_has_not_confirmed_current_model"
      : "host_capability_cannot_confirm_current_model",
  };
}

export function withHostCapability(record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return record;
  }
  const platform = String(record.platform || "unknown").trim() || "unknown";
  const capability = record.host_capability && typeof record.host_capability === "object"
    ? record.host_capability
    : hostCapabilityForRecord(platform);
  const thinking = normalizeHostThinking(record.thinking || "auto");
  const speed = normalizeHostSpeed(record.speed || "auto");
  const targetModel = String(record.target_model || "").trim();
  const currentModel = String(record.current_model || "").trim();
  return {
    ...record,
    host_capability: capability,
    can_apply_current_chat: false,
    switch_result:
      record.switch_result && typeof record.switch_result === "object"
        ? record.switch_result
        : buildHostSwitchResult(
            platform,
            targetModel,
            currentModel,
            thinking,
            speed,
            capability
          ),
    host_confirmation:
      record.host_confirmation && typeof record.host_confirmation === "object"
        ? record.host_confirmation
        : buildHostConfirmation(
            targetModel,
            currentModel,
            thinking,
            capability,
            String(record.updated || "")
          ),
    adapter_proof_scan:
      record.adapter_proof_scan && typeof record.adapter_proof_scan === "object"
        ? record.adapter_proof_scan
        : buildHostAdapterProofScan(platform, capability, String(record.updated || "")),
  };
}

export function buildHostModelRecord(
  { platform, target_model, current_model, thinking, speed, reason },
  { now = () => new Date().toISOString(), classifyModelTier = () => "unknown", env = process.env } = {}
) {
  const targetModel = String(target_model || "").trim();
  const resolvedPlatform = detectHostPlatform(platform || "auto", env);
  const resolvedThinking = normalizeHostThinking(thinking || "auto");
  const resolvedSpeed = normalizeHostSpeed(speed || "auto");
  const currentModel = String(current_model || "").trim();
  const actions = hostSwitchActions(
    resolvedPlatform,
    targetModel,
    resolvedThinking,
    resolvedSpeed,
    env
  );
  const capability = hostCapabilityForRecord(resolvedPlatform);
  const updated = now();
  return {
    platform: resolvedPlatform,
    requested_platform: normalizeHostPlatform(platform || "auto"),
    target_model: targetModel,
    current_model: currentModel,
    target_model_tier: classifyModelTier(targetModel),
    thinking: resolvedThinking,
    speed: resolvedSpeed,
    reason: String(reason || "").trim(),
    status: "recorded_requires_host_action",
    control: "host_selected",
    can_apply_current_chat: false,
    host_capability: capability,
    switch_result: buildHostSwitchResult(
      resolvedPlatform,
      targetModel,
      currentModel,
      resolvedThinking,
      resolvedSpeed,
      capability
    ),
    host_confirmation: buildHostConfirmation(
      targetModel,
      currentModel,
      resolvedThinking,
      capability,
      updated
    ),
    adapter_proof_scan: buildHostAdapterProofScan(resolvedPlatform, capability, updated),
    updated,
    host_actions: actions,
  };
}

export function formatBool(value) {
  return value ? "yes" : "no";
}

export function formatHostModelRecord(record) {
  const withCapability = withHostCapability(record);
  const capability = withCapability.host_capability || hostCapabilityForRecord(withCapability.platform);
  const switchResult = withCapability.switch_result || {};
  const confirmation = withCapability.host_confirmation || {};
  const proof = withCapability.adapter_proof_scan || {};
  const proofPaths = proof.paths || {};
  const lines = [
    `[OK] Host model switch ${withCapability.status}.`,
    `platform: ${withCapability.platform}`,
    `target model: ${withCapability.target_model} (tier ${withCapability.target_model_tier})`,
    `current model: ${withCapability.current_model || "unknown"}`,
    `host-confirmed model: ${confirmation.confirmed_current_model || confirmation.confirmation_status || "unsupported"}`,
    `confirmation source: ${confirmation.confirmation_source || "none"}`,
    `adapter proof scan: ${proof.status || "metadata_only"}`,
    `current-chat apply proof: ${proofPaths.current_chat_model_apply?.status || "unknown"}`,
    `current-chat confirm proof: ${proofPaths.current_chat_model_confirm?.status || "unknown"}`,
    `new-thread model proof: ${proofPaths.new_thread_model_apply?.status || "unknown"}`,
    `worker model proof: ${proofPaths.worker_model_apply?.status || "unknown"}`,
    `thinking proof: ${proofPaths.thinking_apply?.status || "unknown"}`,
    `thinking: ${withCapability.thinking}`,
    `speed: ${withCapability.speed}`,
    `switch status: ${switchResult.status || "manual"}`,
    `current-chat confirmed: ${formatBool(switchResult.current_chat_confirmed)}`,
    `manual action required: ${formatBool(switchResult.manual_action_required !== false)}`,
    `current-chat switch: ${formatBool(capability.can_switch_current_thread)}`,
    `new-thread model: ${formatBool(capability.can_set_new_thread_model)}`,
    `worker model: ${formatBool(capability.can_set_worker_model)}`,
    `thinking control: ${formatBool(capability.can_set_thinking)}`,
    "scope: Mythify recorded the requested host model for model_policy and spawn ceiling checks.",
    "host action required:",
  ];
  for (const action of withCapability.host_actions || []) {
    lines.push(`- ${action}`);
  }
  return lines.join("\n");
}
