import path from "node:path";
import { z } from "zod";
import {
  buildHostModelRecord,
  formatHostModelRecord,
  withHostCapability,
} from "./host-model.js";
import {
  formatHostCliProbe,
  formatHostCliRun,
  probeHostCli,
  runHostCliWorker,
} from "./host-cli.js";
import {
  formatExecutionProbe,
  formatExecutionRun,
  probeExecutionAdapter,
  runExecutionAdapter,
} from "./execution-adapter.js";
import {
  formatLifecycleProbe,
  probeLifecycleAdapter,
} from "./lifecycle-adapter.js";
import {
  DEFAULT_MODEL_PROVIDER,
  LOCAL_MODEL_ROLES,
  MODEL_PROVIDER_API_KEY_ENVS,
  MODEL_PROVIDER_IDS,
  formatLocalModelRun,
  formatProviderProbe,
  probeOpenAICompatibleProvider,
  runLocalModelRole,
} from "./model-provider.js";
import { classifyModelTier } from "./model-policy.js";
import {
  HOST_PLATFORMS as PLATFORMS,
  HOST_THINKING_LEVELS,
  SPEED_LEVELS,
} from "./capability-registry.js";

export const ADAPTER_TOOL_NAMES = [
  "host_model_switch",
  "provider_probe",
  "local_model_run",
  "host_cli_probe",
  "host_cli_run",
  "execution_probe",
  "execution_run",
  "lifecycle_probe",
];

function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerAdapterTools requires deps.${name}`);
  }
  return value;
}

export function registerAdapterTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const isoNow = requireDep(deps, "isoNow");
  const readHostModelState = requireDep(deps, "readHostModelState");
  const clearHostModelState = requireDep(deps, "clearHostModelState");
  const writeHostModelState = requireDep(deps, "writeHostModelState");
  const resolveStateDir = requireDep(deps, "resolveStateDir");

  server.registerTool(
    "host_model_switch",
    {
      title: "Record a host chat model switch request",
      description:
        "Record the intended host chat model and return platform-specific switch guidance. " +
        "This updates Mythify session model policy for later classify_task and fanout_start calls, but the current chat model remains owned by the host app unless that host exposes a native switch action.",
      inputSchema: {
        action: z
          .enum(["switch", "status", "clear"])
          .optional()
          .describe("switch records a target model, status shows the recorded model, clear removes it."),
        platform: z
          .enum(PLATFORMS)
          .optional()
          .describe("Host platform. Use codex-desktop, claude-desktop, claude-code, cursor-desktop, or cursor-agent when known."),
        target_model: z
          .string()
          .optional()
          .describe("Target host model for action=switch."),
        current_model: z
          .string()
          .optional()
          .describe("Current host model when known, recorded for audit only."),
        thinking: z
          .enum(HOST_THINKING_LEVELS)
          .optional()
          .describe("Requested host reasoning effort when the host supports it."),
        speed: z
          .enum(SPEED_LEVELS)
          .optional()
          .describe("Requested host speed preference when the host supports it."),
        reason: z
          .string()
          .optional()
          .describe("Why this host switch was requested."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable host integrations."),
      },
    },
    guarded(({ action, platform, target_model, current_model, thinking, speed, reason, format }) => {
      const selectedAction = action || "switch";
      if (selectedAction === "status") {
        const record = readHostModelState();
        if (record === null) {
          const empty = { status: "unset", target_model: "", source: "unknown" };
          return format === "json" ? "[OK] " + JSON.stringify(empty, null, 2) : "[OK] No host model switch is recorded.";
        }
        const enriched = withHostCapability(record);
        return format === "json" ? "[OK] " + JSON.stringify(enriched, null, 2) : formatHostModelRecord(enriched);
      }
      if (selectedAction === "clear") {
        clearHostModelState();
        const cleared = { status: "cleared", target_model: "" };
        return format === "json" ? "[OK] " + JSON.stringify(cleared, null, 2) : "[OK] Host model switch record cleared.";
      }
      if (String(target_model || "").trim() === "") {
        return "[FAIL] host_model_switch action=switch requires target_model.";
      }
      const record = buildHostModelRecord(
        {
          platform: platform || "auto",
          target_model,
          current_model: current_model || "",
          thinking: thinking || "auto",
          speed: speed || "auto",
          reason: reason || "",
        },
        { now: isoNow, classifyModelTier }
      );
      writeHostModelState(record);
      return format === "json" ? "[OK] " + JSON.stringify(record, null, 2) : formatHostModelRecord(record);
    })
  );

  server.registerTool(
    "provider_probe",
    {
      title: "Probe an OpenAI-compatible model provider",
      description:
        "Probe a configured OpenAI-compatible provider by calling /v1/models and, when requested, /v1/chat/completions. The ollama, lm-studio, llama-cpp, and vllm profiles default to their local /v1 endpoints and send no auth header by default. " +
        "Use this before assigning local reader or triage roles to a provider. The result is material, not verification evidence, and does not enable worker execution.",
      inputSchema: {
        provider: z
          .enum(MODEL_PROVIDER_IDS)
          .optional()
          .describe("Provider adapter to probe. Defaults to generic-openai-compatible; ollama, lm-studio, llama-cpp, and vllm use local /v1 profiles."),
        base_url: z
          .string()
          .optional()
          .describe("OpenAI-compatible /v1 base URL. Generic defaults to MYTHIFY_OPENAI_COMPAT_BASE_URL; ollama defaults to MYTHIFY_OLLAMA_BASE_URL or http://localhost:11434/v1; lm-studio defaults to MYTHIFY_LM_STUDIO_BASE_URL or http://localhost:1234/v1; llama-cpp defaults to MYTHIFY_LLAMA_CPP_BASE_URL or http://localhost:8080/v1; vllm defaults to MYTHIFY_VLLM_BASE_URL or http://localhost:8000/v1."),
        model: z
          .string()
          .optional()
          .describe("Model id for chat probes. Generic defaults to MYTHIFY_OPENAI_COMPAT_MODEL; ollama defaults to MYTHIFY_OLLAMA_MODEL; lm-studio defaults to MYTHIFY_LM_STUDIO_MODEL; llama-cpp defaults to MYTHIFY_LLAMA_CPP_MODEL; vllm defaults to MYTHIFY_VLLM_MODEL."),
        check: z
          .enum(["models", "chat", "both"])
          .optional()
          .describe("Probe /models, /chat/completions, or both. Defaults to both."),
        api_key_env: z
          .string()
          .optional()
          .describe(`Allowlisted environment variable containing the API key. Allowed: ${MODEL_PROVIDER_API_KEY_ENVS.join(", ")}. Defaults to MYTHIFY_OPENAI_COMPAT_API_KEY.`),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("HTTP timeout per request in seconds. Defaults to 10."),
        prompt: z
          .string()
          .optional()
          .describe("Optional tiny prompt for check=chat or both."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable probes."),
      },
    },
    guarded(async ({ provider, base_url, model, check, api_key_env, timeout_seconds, prompt, format }) => {
      const result = await probeOpenAICompatibleProvider({
        provider: provider || DEFAULT_MODEL_PROVIDER,
        base_url,
        model,
        check: check || "both",
        api_key_env,
        timeout_seconds,
        prompt,
      });
      const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
      return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatProviderProbe(result);
    })
  );

  server.registerTool(
    "local_model_run",
    {
      title: "Run a role-limited local model",
      description:
        "Run a reader or triage prompt against a localhost OpenAI-compatible model provider. The ollama, lm-studio, llama-cpp, and vllm profiles default to their local /v1 endpoints and send no auth header by default. " +
        "Use this for low-risk local model material before the orchestrator verifies claims with commands. The result is material, not verification evidence, and the tool writes no Mythify state.",
      inputSchema: {
        provider: z
          .enum(MODEL_PROVIDER_IDS)
          .optional()
          .describe("Local provider profile. Defaults to generic-openai-compatible; ollama, lm-studio, llama-cpp, and vllm use local /v1 profiles."),
        role: z
          .enum(LOCAL_MODEL_ROLES)
          .optional()
          .describe("Role to run. Defaults to reader. Allowed roles are reader and triage."),
        base_url: z
          .string()
          .optional()
          .describe("Local OpenAI-compatible /v1 base URL. Generic defaults to MYTHIFY_OPENAI_COMPAT_BASE_URL; ollama defaults to MYTHIFY_OLLAMA_BASE_URL or http://localhost:11434/v1; lm-studio defaults to MYTHIFY_LM_STUDIO_BASE_URL or http://localhost:1234/v1; llama-cpp defaults to MYTHIFY_LLAMA_CPP_BASE_URL or http://localhost:8080/v1; vllm defaults to MYTHIFY_VLLM_BASE_URL or http://localhost:8000/v1. Must be localhost, 127.0.0.1, ::1, or 0.0.0.0."),
        model: z
          .string()
          .optional()
          .describe("Local model id. Generic defaults to MYTHIFY_OPENAI_COMPAT_MODEL; ollama defaults to MYTHIFY_OLLAMA_MODEL; lm-studio defaults to MYTHIFY_LM_STUDIO_MODEL; llama-cpp defaults to MYTHIFY_LLAMA_CPP_MODEL; vllm defaults to MYTHIFY_VLLM_MODEL."),
        prompt: z
          .string()
          .describe("Prompt or material for the local model."),
        api_key_env: z
          .string()
          .optional()
          .describe(`Allowlisted environment variable containing an optional local provider API key. Allowed: ${MODEL_PROVIDER_API_KEY_ENVS.join(", ")}. Defaults to MYTHIFY_OPENAI_COMPAT_API_KEY.`),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("HTTP timeout in seconds. Defaults to 30."),
        max_tokens: z
          .number()
          .positive()
          .optional()
          .describe("Maximum requested completion tokens, capped at 2048. Defaults to 512."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable local model runs."),
      },
    },
    guarded(async ({ provider, role, base_url, model, prompt, api_key_env, timeout_seconds, max_tokens, format }) => {
      const result = await runLocalModelRole({
        provider: provider || DEFAULT_MODEL_PROVIDER,
        role: role || "reader",
        base_url,
        model,
        prompt,
        api_key_env,
        timeout_seconds,
        max_tokens,
      });
      const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
      return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatLocalModelRun(result);
    })
  );

  server.registerTool(
    "host_cli_probe",
    {
      title: "Probe a host CLI adapter",
      description:
        "Probe Kimi Code, OpenCode, or Antigravity CLI availability by running only version and help commands. " +
        "Use this before enabling a host CLI adapter. The result is material, not verification evidence, and does not execute a prompt or start workers.",
      inputSchema: {
        host: z
          .enum(["kimi-code", "opencode", "antigravity"])
          .optional()
          .describe("Host CLI to probe. Defaults to opencode."),
        bin: z
          .string()
          .optional()
          .describe("Explicit CLI binary path. The basename must match the selected host family. Defaults to MYTHIFY_KIMI_BIN, MYTHIFY_OPENCODE_BIN, or MYTHIFY_ANTIGRAVITY_BIN, then PATH and common install paths."),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("Timeout per version or help command in seconds. Defaults to 10."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable probes."),
      },
    },
    guarded(({ host, bin, timeout_seconds, format }) => {
      const result = probeHostCli({
        host: host || "opencode",
        bin: bin || "",
        timeout_seconds,
      });
      const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
      return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatHostCliProbe(result);
    })
  );

  server.registerTool(
    "host_cli_run",
    {
      title: "Run a bounded host CLI worker",
      description:
        "Run a bounded non-interactive prompt through Kimi Code, OpenCode, or Antigravity. " +
        "Use this only for worker material that the orchestrator will inspect and then verify with commands. The result is material, not verification evidence, and the tool writes no Mythify state.",
      inputSchema: {
        host: z
          .enum(["kimi-code", "opencode", "antigravity"])
          .optional()
          .describe("Host CLI worker to run. Defaults to opencode."),
        bin: z
          .string()
          .optional()
          .describe("Explicit CLI binary path. The basename must match the selected host family. Defaults to MYTHIFY_KIMI_BIN, MYTHIFY_OPENCODE_BIN, or MYTHIFY_ANTIGRAVITY_BIN, then PATH and common install paths."),
        prompt: z
          .string()
          .describe("Prompt to pass to the host CLI non-interactive runner."),
        cwd: z
          .string()
          .optional()
          .describe("Working directory for the worker. Defaults to the project root inferred from MYTHIFY_DIR. Antigravity requires this to be explicit."),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("Prompt run timeout in seconds. Defaults to 120."),
        model: z
          .string()
          .optional()
          .describe("Optional OpenCode or Antigravity model id. Kimi Code does not receive a model flag in this adapter."),
        agent: z
          .string()
          .optional()
          .describe("Optional OpenCode agent id. Kimi Code and Antigravity do not receive an agent flag in this adapter."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable host CLI runs."),
      },
    },
    guarded(({ host, bin, prompt, cwd, timeout_seconds, model, agent, format }) => {
      const result = runHostCliWorker({
        host: host || "opencode",
        bin: bin || "",
        prompt,
        cwd,
        default_cwd: path.dirname(resolveStateDir()),
        timeout_seconds,
        model,
        agent,
      });
      const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
      return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatHostCliRun(result);
    })
  );

  server.registerTool(
    "execution_probe",
    {
      title: "Probe an execution adapter",
      description:
        "Probe Google Colab CLI availability by running only version and help commands. " +
        "Use this before planning remote execution work. The result is material, not verification evidence, and does not provision runtimes, request accelerators, upload data, execute jobs, or retrieve artifacts.",
      inputSchema: {
        adapter: z
          .enum(["google-colab-cli"])
          .optional()
          .describe("Execution adapter to probe. Defaults to google-colab-cli."),
        bin: z
          .string()
          .optional()
          .describe("Explicit CLI binary path. Defaults to MYTHIFY_COLAB_BIN, then PATH and common install paths."),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("Timeout per version or help command in seconds. Defaults to 10."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable probes."),
      },
    },
    guarded(({ adapter, bin, timeout_seconds, format }) => {
      const result = probeExecutionAdapter({
        adapter: adapter || "google-colab-cli",
        bin: bin || "",
        timeout_seconds,
      });
      const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
      return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatExecutionProbe(result);
    })
  );

  server.registerTool(
    "execution_run",
    {
      title: "Run a guarded execution adapter job",
      description:
        "Run a guarded Google Colab CLI ephemeral job through colab run. " +
        "Use this only after the user explicitly accepts billing, data movement, and cleanup. The result is material, not verification evidence, writes no Mythify state, and does not use persistent sessions, Drive mounting, artifact download, or notebook log export.",
      inputSchema: {
        adapter: z
          .enum(["google-colab-cli"])
          .optional()
          .describe("Execution adapter to run. Defaults to google-colab-cli."),
        bin: z
          .string()
          .optional()
          .describe("Explicit CLI binary path. Defaults to MYTHIFY_COLAB_BIN, then PATH and common install paths."),
        cwd: z
          .string()
          .optional()
          .describe("Working directory for resolving relative script_path. Defaults to the project root inferred from MYTHIFY_DIR."),
        script_path: z
          .string()
          .describe("Local script path to pass to colab run. Relative paths resolve from cwd."),
        script_args: z
          .array(z.string())
          .optional()
          .describe("Optional arguments forwarded after the script path."),
        accelerator_type: z
          .enum(["cpu", "gpu", "tpu"])
          .optional()
          .describe("Remote runtime accelerator class. Defaults to cpu."),
        accelerator: z
          .enum(["T4", "L4", "G4", "H100", "A100", "v5e1", "v6e1"])
          .optional()
          .describe("Required for gpu or tpu runs. GPUs: T4, L4, G4, H100, A100. TPUs: v5e1, v6e1."),
        billing_ack: z
          .boolean()
          .optional()
          .describe("Must be true to acknowledge Colab remote execution can consume compute units or quota."),
        data_movement_ack: z
          .boolean()
          .optional()
          .describe("Must be true to acknowledge the local script is transmitted to a remote Colab runtime."),
        cleanup_ack: z
          .boolean()
          .optional()
          .describe("Must be true to acknowledge this adapter relies on colab run ephemeral teardown and never passes --keep."),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("Remote job timeout in seconds. Defaults to 600."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable execution records."),
      },
    },
    guarded(({
      adapter,
      bin,
      script_path,
      cwd,
      timeout_seconds,
      accelerator_type,
      accelerator,
      script_args,
      billing_ack,
      data_movement_ack,
      cleanup_ack,
      format,
    }) => {
      const result = runExecutionAdapter({
        adapter: adapter || "google-colab-cli",
        bin: bin || "",
        script_path,
        cwd,
        default_cwd: path.dirname(resolveStateDir()),
        timeout_seconds,
        accelerator_type,
        accelerator,
        script_args,
        billing_ack,
        data_movement_ack,
        cleanup_ack,
      });
      const prefix = result.status === "succeeded" ? "[OK] " : "[FAIL] ";
      return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatExecutionRun(result);
    })
  );

  server.registerTool(
    "lifecycle_probe",
    {
      title: "Probe an agent lifecycle adapter",
      description:
        "Probe Google Agents CLI or ADK CLI availability by running only version, help, and eval-help commands. " +
        "Use this before planning agent lifecycle work. The result is material, not verification evidence, and does not scaffold projects, run agents, execute evals, deploy, publish, mutate cloud resources, or write project state.",
      inputSchema: {
        adapter: z
          .enum(["google-agents-cli", "google-adk-cli"])
          .optional()
          .describe("Lifecycle adapter to probe. Defaults to google-agents-cli."),
        bin: z
          .string()
          .optional()
          .describe("Explicit CLI binary path. Defaults to MYTHIFY_AGENTS_CLI_BIN or MYTHIFY_ADK_BIN, then PATH and common install paths."),
        timeout_seconds: z
          .number()
          .positive()
          .optional()
          .describe("Timeout per version, help, or eval-help command in seconds. Defaults to 10."),
        format: z
          .enum(["text", "json"])
          .optional()
          .describe("Return text by default, or JSON for machine-readable probes."),
      },
    },
    guarded(({ adapter, bin, timeout_seconds, format }) => {
      const result = probeLifecycleAdapter({
        adapter: adapter || "google-agents-cli",
        bin: bin || "",
        timeout_seconds,
      });
      const prefix = result.status === "available" ? "[OK] " : "[FAIL] ";
      return format === "json" ? prefix + JSON.stringify(result, null, 2) : formatLifecycleProbe(result);
    })
  );
}
