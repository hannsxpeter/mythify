import { ADAPTER_CANDIDATES } from "./capability-registry.js";

function tailText(text, limit = 4000) {
  const value = String(text || "");
  if (value.length <= limit) {
    return value;
  }
  return value.slice(value.length - limit);
}

function envValue(name) {
  return (process.env[name] || "").trim();
}

export const MODEL_PROVIDER_IDS = ["generic-openai-compatible", "ollama", "lm-studio", "llama-cpp", "vllm"];
export const DEFAULT_MODEL_PROVIDER = "generic-openai-compatible";
export const MODEL_PROVIDER_API_KEY_ENVS = ["MYTHIFY_OPENAI_COMPAT_API_KEY"];
const MODEL_PROVIDER_API_KEY_ENV_SET = new Set(MODEL_PROVIDER_API_KEY_ENVS);

function normalizeModelProvider(provider) {
  return MODEL_PROVIDER_IDS.includes(provider) ? provider : DEFAULT_MODEL_PROVIDER;
}

function modelProviderProfile(provider) {
  const name = normalizeModelProvider(provider);
  return {
    name,
    adapter: ADAPTER_CANDIDATES[name] || ADAPTER_CANDIDATES[DEFAULT_MODEL_PROVIDER] || {},
  };
}

function providerBaseUrl(input, provider) {
  const explicit = String(input || "").trim();
  if (explicit !== "") {
    return explicit;
  }
  const profile = modelProviderProfile(provider);
  if (profile.name !== DEFAULT_MODEL_PROVIDER) {
    const baseUrlEnv = profile.adapter.base_url_env || "";
    return (baseUrlEnv ? envValue(baseUrlEnv) : "") || profile.adapter.default_base_url || "";
  }
  return envValue("MYTHIFY_OPENAI_COMPAT_BASE_URL") || envValue("MYTHIFY_PROVIDER_BASE_URL");
}

function providerModel(input, provider) {
  const explicit = String(input || "").trim();
  if (explicit !== "") {
    return explicit;
  }
  const profile = modelProviderProfile(provider);
  if (profile.name !== DEFAULT_MODEL_PROVIDER) {
    const modelEnv = profile.adapter.model_env || "";
    return modelEnv ? envValue(modelEnv) : "";
  }
  return envValue("MYTHIFY_OPENAI_COMPAT_MODEL") || envValue("MYTHIFY_PROVIDER_MODEL");
}

function providerApiKeyEnv(input, provider) {
  if (input !== undefined && input !== null) {
    return String(input).trim();
  }
  const profile = modelProviderProfile(provider);
  if (profile.name !== DEFAULT_MODEL_PROVIDER) {
    return profile.adapter.api_key_env || "";
  }
  return "MYTHIFY_OPENAI_COMPAT_API_KEY";
}

function normalizeProviderBaseUrl(raw, toolName = "provider_probe") {
  const value = String(raw || "").trim();
  if (value === "") {
    return { ok: false, baseUrl: "", error: `${toolName} requires base_url or provider profile base URL env.` };
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return { ok: false, baseUrl: "", error: `Invalid provider base_url: ${value}` };
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    return { ok: false, baseUrl: "", error: `${toolName} base_url must use http or https.` };
  }
  return { ok: true, baseUrl: parsed.toString().replace(/\/+$/, ""), error: "" };
}

function isLocalProviderBaseUrl(baseUrl) {
  const parsed = new URL(baseUrl);
  return LOCAL_MODEL_HOSTS.has(parsed.hostname);
}

function normalizeProfileProviderBaseUrl(raw, provider, toolName) {
  const profile = modelProviderProfile(provider);
  const base = normalizeProviderBaseUrl(providerBaseUrl(raw, profile.name), toolName);
  if (!base.ok) {
    return base;
  }
  if (profile.adapter.local_only === true && !isLocalProviderBaseUrl(base.baseUrl)) {
    return {
      ok: false,
      baseUrl: base.baseUrl,
      error: `${toolName} provider ${profile.name} requires a localhost, 127.0.0.1, ::1, or 0.0.0.0 base_url.`,
    };
  }
  return base;
}

function providerEndpoint(baseUrl, pathSuffix) {
  return `${baseUrl}/${pathSuffix.replace(/^\/+/, "")}`;
}

function providerHeaders(apiKeyEnv) {
  const headers = { accept: "application/json" };
  if (apiKeyEnv !== "") {
    if (!MODEL_PROVIDER_API_KEY_ENV_SET.has(apiKeyEnv)) {
      return {
        ok: false,
        headers,
        error: `api_key_env must be one of: ${MODEL_PROVIDER_API_KEY_ENVS.join(", ")}.`,
      };
    }
    const apiKey = envValue(apiKeyEnv);
    if (apiKey !== "") {
      headers.authorization = `Bearer ${apiKey}`;
    }
  }
  return { ok: true, headers, error: "" };
}

async function fetchProviderJson(url, options, timeoutSeconds) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.round(timeoutSeconds * 1000));
  const startedAt = process.hrtime.bigint();
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const text = await response.text();
    let json = null;
    if (text.trim() !== "") {
      try {
        json = JSON.parse(text);
      } catch {
        json = null;
      }
    }
    return {
      ok: response.ok,
      status_code: response.status,
      duration_seconds: Number((Number(process.hrtime.bigint() - startedAt) / 1e9).toFixed(3)),
      json,
      body_tail: tailText(text),
      error: response.ok ? "" : `HTTP ${response.status}`,
      timed_out: false,
    };
  } catch (err) {
    const timedOut = err && err.name === "AbortError";
    return {
      ok: false,
      status_code: 0,
      duration_seconds: Number((Number(process.hrtime.bigint() - startedAt) / 1e9).toFixed(3)),
      json: null,
      body_tail: "",
      error: timedOut ? `timed out after ${timeoutSeconds} seconds` : err && err.message ? err.message : String(err),
      timed_out: timedOut,
    };
  } finally {
    clearTimeout(timer);
  }
}

function modelNamesFromList(json) {
  const data = json && Array.isArray(json.data) ? json.data : [];
  return data.map((item) => String(item && item.id ? item.id : "")).filter(Boolean);
}

function chatContentFromCompletion(json) {
  const choice = json && Array.isArray(json.choices) ? json.choices[0] : null;
  const message = choice && choice.message ? choice.message : null;
  return String(message && message.content ? message.content : "");
}

export async function probeOpenAICompatibleProvider({ provider, base_url, model, timeout_seconds, api_key_env, check, prompt }) {
  const selectedProvider = normalizeModelProvider(provider);
  const selectedCheck = check || "both";
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 10;
  const base = normalizeProfileProviderBaseUrl(base_url, selectedProvider, "provider_probe");
  const selectedModel = providerModel(model, selectedProvider);
  const keyEnv = providerApiKeyEnv(api_key_env, selectedProvider);
  const adapter = ADAPTER_CANDIDATES[selectedProvider] || {};
  const result = {
    provider: selectedProvider,
    provider_kind: adapter.kind || "model_provider",
    status: "blocked",
    openai_compatible: adapter.openai_compatible === true,
    local_only: adapter.local_only === true,
    base_url: base.baseUrl,
    default_base_url: adapter.default_base_url || "",
    model: selectedModel,
    check: selectedCheck,
    api_key_env: keyEnv,
    api_key_present: MODEL_PROVIDER_API_KEY_ENV_SET.has(keyEnv) && envValue(keyEnv) !== "",
    material_not_evidence: true,
    evidence_status: "probe_only_not_verification",
    can_answer_prompt: false,
    checks: [],
    error: "",
  };
  if (!base.ok) {
    result.error = base.error;
    return result;
  }
  if (!["models", "chat", "both"].includes(selectedCheck)) {
    result.error = "provider_probe check must be models, chat, or both.";
    return result;
  }
  if (["chat", "both"].includes(selectedCheck) && selectedModel === "") {
    const modelEnv = adapter.model_env || "MYTHIFY_OPENAI_COMPAT_MODEL";
    result.error = selectedProvider === DEFAULT_MODEL_PROVIDER
      ? "provider_probe check=chat or both requires model or MYTHIFY_OPENAI_COMPAT_MODEL."
      : `provider_probe provider=${selectedProvider} check=chat or both requires model or ${modelEnv}.`;
    return result;
  }
  const headersResult = providerHeaders(keyEnv);
  if (!headersResult.ok) {
    result.error = headersResult.error;
    return result;
  }

  if (["models", "both"].includes(selectedCheck)) {
    const models = await fetchProviderJson(
      providerEndpoint(base.baseUrl, "models"),
      { method: "GET", headers: headersResult.headers },
      timeoutSeconds
    );
    const names = modelNamesFromList(models.json);
    result.checks.push({
      name: "models",
      ok: models.ok,
      status_code: models.status_code,
      duration_seconds: models.duration_seconds,
      models_count: names.length,
      model_present: selectedModel === "" ? null : names.includes(selectedModel),
      error: models.error,
      timed_out: models.timed_out,
    });
  }

  if (["chat", "both"].includes(selectedCheck)) {
    const body = {
      model: selectedModel,
      messages: [
        {
          role: "user",
          content: String(prompt || "").trim() || "Reply with exactly: mythify-provider-probe-ok",
        },
      ],
      max_tokens: 32,
      temperature: 0,
    };
    const chat = await fetchProviderJson(
      providerEndpoint(base.baseUrl, "chat/completions"),
      {
        method: "POST",
        headers: { ...headersResult.headers, "content-type": "application/json" },
        body: JSON.stringify(body),
      },
      timeoutSeconds
    );
    const content = chatContentFromCompletion(chat.json);
    result.checks.push({
      name: "chat",
      ok: chat.ok && content !== "",
      status_code: chat.status_code,
      duration_seconds: chat.duration_seconds,
      response_tail: tailText(content, 1000),
      error: chat.ok && content !== "" ? "" : chat.error || "empty chat completion content",
      timed_out: chat.timed_out,
    });
  }

  result.can_answer_prompt = result.checks.some((item) => item.name === "chat" && item.ok);
  result.status = result.checks.length > 0 && result.checks.every((item) => item.ok) ? "available" : "blocked";
  result.error = result.status === "available"
    ? ""
    : result.checks.find((item) => !item.ok)?.error || "provider probe failed";
  return result;
}

export function formatProviderProbe(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Provider probe ${result.status}.`,
    `provider: ${result.provider}`,
    `base_url: ${result.base_url || "unset"}`,
    `model: ${result.model || "unset"}`,
    `check: ${result.check}`,
    `api key env: ${result.api_key_env || "none"} (${result.api_key_present ? "set" : "unset"})`,
    "evidence: probe output is material, not verification evidence.",
  ];
  for (const item of result.checks || []) {
    const details = [`${item.name}: ${item.ok ? "ok" : "failed"}`, `status=${item.status_code}`];
    if (typeof item.models_count === "number") {
      details.push(`models=${item.models_count}`);
    }
    if (item.model_present !== null && item.model_present !== undefined) {
      details.push(`model_present=${item.model_present}`);
    }
    if (item.response_tail) {
      details.push(`response=${item.response_tail}`);
    }
    if (item.error) {
      details.push(`error=${item.error}`);
    }
    lines.push(details.join("; "));
  }
  if (result.error && (!result.checks || result.checks.length === 0)) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

export const LOCAL_MODEL_ROLES = ["reader", "triage"];
const LOCAL_MODEL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);

function normalizeLocalProviderBaseUrl(raw, provider) {
  const base = normalizeProfileProviderBaseUrl(raw, provider, "local_model_run");
  if (!base.ok) {
    return base;
  }
  if (!isLocalProviderBaseUrl(base.baseUrl)) {
    return {
      ok: false,
      baseUrl: base.baseUrl,
      error: "local_model_run requires a localhost, 127.0.0.1, ::1, or 0.0.0.0 base_url.",
    };
  }
  return base;
}

function localModelSystemPrompt(role) {
  if (role === "triage") {
    return [
      "You are a local triage model helping Mythify frame a task before planning.",
      "Return concise material for the orchestrator to inspect.",
      "Do not claim verification, run commands, edit files, or decide completion.",
    ].join(" ");
  }
  return [
    "You are a local read-only model helping Mythify inspect supplied material.",
    "Summarize, extract facts, and note uncertainty for the orchestrator to inspect.",
    "Do not claim verification, run commands, edit files, or decide completion.",
  ].join(" ");
}

export async function runLocalModelRole({ provider, role, base_url, model, api_key_env, timeout_seconds, prompt, max_tokens }) {
  const selectedProvider = normalizeModelProvider(provider);
  const selectedRole = role || "reader";
  const timeoutSeconds =
    typeof timeout_seconds === "number" && timeout_seconds > 0 ? timeout_seconds : 30;
  const selectedMaxTokens =
    typeof max_tokens === "number" && max_tokens > 0 ? Math.min(Math.floor(max_tokens), 2048) : 512;
  const base = normalizeLocalProviderBaseUrl(base_url, selectedProvider);
  const selectedModel = providerModel(model, selectedProvider);
  const keyEnv = providerApiKeyEnv(api_key_env, selectedProvider);
  const adapter = ADAPTER_CANDIDATES[selectedProvider] || {};
  const result = {
    provider: selectedProvider,
    provider_kind: adapter.kind || "model_provider",
    role: selectedRole,
    status: "blocked",
    openai_compatible: adapter.openai_compatible === true,
    base_url: base.baseUrl,
    default_base_url: adapter.default_base_url || "",
    model: selectedModel,
    local_only: true,
    material_not_evidence: true,
    evidence_status: "model_output_not_verification",
    writes_state: false,
    verification_recorded: false,
    can_answer_prompt: false,
    max_tokens: selectedMaxTokens,
    output_tail: "",
    checks: [],
    error: "",
  };
  if (!LOCAL_MODEL_ROLES.includes(selectedRole)) {
    result.error = "local_model_run role must be reader or triage.";
    return result;
  }
  if (!base.ok) {
    result.error = base.error;
    return result;
  }
  if (selectedModel === "") {
    const modelEnv = adapter.model_env || "MYTHIFY_OPENAI_COMPAT_MODEL";
    result.error = selectedProvider === DEFAULT_MODEL_PROVIDER
      ? "local_model_run requires model or MYTHIFY_OPENAI_COMPAT_MODEL."
      : `local_model_run provider=${selectedProvider} requires model or ${modelEnv}.`;
    return result;
  }
  const userPrompt = String(prompt || "").trim();
  if (userPrompt === "") {
    result.error = "local_model_run requires prompt.";
    return result;
  }
  const headersResult = providerHeaders(keyEnv);
  if (!headersResult.ok) {
    result.error = headersResult.error;
    return result;
  }
  const body = {
    model: selectedModel,
    messages: [
      { role: "system", content: localModelSystemPrompt(selectedRole) },
      { role: "user", content: userPrompt },
    ],
    max_tokens: selectedMaxTokens,
    temperature: 0,
  };
  const chat = await fetchProviderJson(
    providerEndpoint(base.baseUrl, "chat/completions"),
    {
      method: "POST",
      headers: { ...headersResult.headers, "content-type": "application/json" },
      body: JSON.stringify(body),
    },
    timeoutSeconds
  );
  const content = chatContentFromCompletion(chat.json);
  result.checks.push({
    name: "chat",
    ok: chat.ok && content !== "",
    status_code: chat.status_code,
    duration_seconds: chat.duration_seconds,
    error: chat.ok && content !== "" ? "" : chat.error || "empty chat completion content",
    timed_out: chat.timed_out,
  });
  result.output_tail = tailText(content, 4000);
  result.can_answer_prompt = chat.ok && content !== "";
  result.status = result.can_answer_prompt ? "available" : "blocked";
  result.error = result.status === "available"
    ? ""
    : result.checks.find((item) => !item.ok)?.error || "local model run failed";
  return result;
}

export function formatLocalModelRun(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Local model run ${result.status}.`,
    `role: ${result.role}`,
    `provider: ${result.provider}`,
    `base_url: ${result.base_url || "unset"}`,
    `model: ${result.model || "unset"}`,
    `local only: ${result.local_only ? "yes" : "no"}`,
    `writes state: ${result.writes_state ? "yes" : "no"}`,
    `verification recorded: ${result.verification_recorded ? "yes" : "no"}`,
    "evidence: model output is material, not verification evidence.",
  ];
  if (result.output_tail) {
    lines.push(`output: ${result.output_tail}`);
  }
  for (const item of result.checks || []) {
    const details = [
      `${item.name}: ${item.ok ? "ok" : "failed"}`,
      `status=${item.status_code}`,
    ];
    if (item.error) {
      details.push(`error=${item.error}`);
    }
    lines.push(details.join("; "));
  }
  if (result.error && (!result.checks || result.checks.length === 0)) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}
