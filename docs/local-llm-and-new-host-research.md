# Local LLM and New Host Research

Date: 2026-06-12

This note records current research for adding local LLM, Kimi, Antigravity,
OpenCode, Google Colab CLI, and Google Agents CLI support to Mythify's future
adapter roadmap.

## Summary

The best product direction is not "support every model host." It is "support a
small adapter contract, then let each host declare what it can actually do."

Implementation status:

- First slice landed in `mcp-server/src/capability-registry.js`: researched
  local providers and new hosts are tracked as candidates, not accepted public
  inputs.
- Provider probe slice landed in `provider_probe`: a generic OpenAI-compatible
  endpoint can be checked through `/v1/models` and `/v1/chat/completions`, and
  the result is marked as material, not verification evidence.
- Host CLI probe slice landed in `host_cli_probe`: Kimi Code and OpenCode can
  be checked through version and help commands without executing prompts or
  enabling worker execution.
- Antigravity probe-guide slice landed in `host_cli_probe` and
  `docs/antigravity-mcp-setup.md`: `agy` can be checked through version and
  help commands, and MCP setup guidance is documented without enabling worker
  execution.
- Colab CLI spike-plan slice landed in `execution_probe` and
  `docs/colab-cli-spike-plan.md`: Google Colab CLI can be checked through
  version and help commands without provisioning runtimes, requesting
  accelerators, uploading data, or executing jobs.
- Agents CLI and ADK spike-plan slice landed in `lifecycle_probe` and
  `docs/agents-cli-adk-spike-plan.md`: Google Agents CLI and ADK CLI can be
  checked through version, help, and eval-help commands without scaffolding
  projects, running evals, deploying, publishing, mutating cloud resources, or
  writing project state.
- Local provider worker execution is not supported yet. The next slice should
  add a role-limited execution path before claiming reader or triage support.

Near-term fit:

- Local OpenAI-compatible servers are the cleanest first local-model path.
- Kimi Code CLI is a strong CLI adapter candidate because it exposes
  non-interactive prompts, model switching, provider configuration, MCP, ACP,
  subagents, hooks, and browser UI.
- OpenCode is a strong CLI adapter candidate because it has headless runs,
  explicit model selection, local provider config, MCP, agents, and a server
  surface.
- Antigravity is a strong roadmap candidate because it has a desktop app, TUI,
  MCP support, non-interactive prompts, skills, and model settings, but local
  probing is blocked until the installed `agy` shim is repaired.

Later fit:

- Google Colab CLI is not a model host. It is a remote execution substrate for
  GPU, TPU, notebook, file, and artifact workflows.
- Google Agents CLI is not a general coding agent. It is a lifecycle tool and
  skills package for building, evaluating, deploying, and observing ADK agents.
- Kimi Work is a desktop local-agent product, not just a coding host. It fits a
  later desktop workflow lane after CLI adapters are stable.

## Local LLM Options

### Ollama

Fit: best first local adapter.

Why:

- Runs locally on `localhost:11434`.
- Provides OpenAI-compatible `/v1/chat/completions`, `/v1/models`,
  `/v1/embeddings`, and `/v1/responses`.
- Accepts `model` per request.
- Supports tools and reasoning controls for compatible models.
- Easy for users to install and understand.

Mythify role fit:

- `triage`
- `reader`
- `summarizer`
- `doc_drafter`
- `test_idea_generator`
- low-risk `reviewer`

Limit:

- Model quality and tool reliability vary. Output must remain material, not
  evidence.

Sources:

- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)

### LM Studio

Fit: strong local desktop companion adapter.

Why:

- Runs a local API server on `localhost`.
- Supports REST, OpenAI-compatible, and Anthropic-compatible endpoints.
- OpenAI-compatible docs show changing only the base URL, usually
  `http://localhost:1234/v1`.
- Supports `/v1/models`, `/v1/responses`, `/v1/chat/completions`,
  `/v1/embeddings`, and `/v1/completions`.

Mythify role fit:

- privacy-preserving reading
- summarization
- documentation
- local review pass
- local model experiments for users who prefer GUI model management

Limit:

- Server must be running and the target model must be loaded or loadable.

Sources:

- [LM Studio local server](https://lmstudio.ai/docs/developer/core/server)
- [LM Studio OpenAI compatibility](https://lmstudio.ai/docs/developer/openai-compat)

### llama.cpp and llama-cpp-python server

Fit: power-user and headless local adapter.

Why:

- `llama-cpp-python` offers an OpenAI-compatible web server.
- It can serve GGUF models with `python3 -m llama_cpp.server --model <path>`.
- Config files can map request `model` values to `model_alias` entries.
- It supports function calling for compatible model and chat format setups.

Mythify role fit:

- offline reading
- repo summarization
- narrow classification
- constrained review
- users who already manage GGUF models

Limit:

- Setup and performance tuning are more advanced than Ollama or LM Studio.

Sources:

- [llama-cpp-python OpenAI compatible server](https://llama-cpp-python.readthedocs.io/en/latest/server/)

### vLLM

Fit: team, workstation, and server-grade local or private adapter.

Why:

- Provides an OpenAI-compatible HTTP server.
- Starts with `vllm serve`.
- Supports OpenAI-style completions, chat completions, responses, embeddings,
  and more.
- Works well when users have one or more GPUs and want higher throughput.

Mythify role fit:

- batch fanout readers
- evaluation runs
- private hosted inference
- team-local model service

Limit:

- Heavier dependency and hardware profile. Better as a later adapter than the
  first local path.

Sources:

- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/v0.18.0/serving/openai_compatible_server/)

## Host Adapter Candidates

### Kimi Code CLI

Fit: high-priority future CLI adapter.

Why:

- Kimi Code CLI is a terminal AI coding agent from Moonshot AI.
- It can read and edit code, run shell commands, search files, fetch web pages,
  and choose next steps from feedback.
- It supports an interactive TUI through `kimi`.
- It supports a single non-interactive instruction through `kimi -p`.
- It supports current-session model switching through `/model`.
- It stores long-term configuration in `~/.kimi-code/config.toml`, including
  `default_model`, `default_thinking`, providers, models, permission rules,
  loop control, background task settings, and hooks.
- It supports Kimi OAuth or Kimi Platform API keys, and can also configure
  Anthropic, OpenAI, Google, and other providers.
- It has MCP client support with user-level `~/.kimi-code/mcp.json` and
  project-level `.kimi-code/mcp.json`, plus `/mcp-config` and `/mcp` commands.
- It supports ACP through `kimi acp`, so compatible IDEs can drive sessions.
- The current Kimi Code CLI docs and repository describe subagents, plugins,
  skills, lifecycle hooks, provider catalog commands, and a browser UI.

Local probe:

- `kimi` is not installed in this environment.
- `kimi-cli`, `k2`, and `moonshot` commands are also not installed.
- The adapter should start with installation/probe docs, not assumed support.

Mythify implication:

- Add a `kimi-code` adapter near OpenCode.
- First operation can be `run_prompt` with `kimi -p`, a selected working
  directory, timeout, model or provider override, permission mode, and a clear
  capture of output.
- Treat `/model` as `manual` for an already-open TUI session unless Mythify is
  driving that process directly.
- Treat config-file model defaults or `kimi -p` launch options as `applied`
  only after a local probe confirms the installed version supports the chosen
  flag or config field.
- ACP is promising for IDE integration, but it is a separate adapter surface
  from the basic CLI worker.

Sources:

- [Kimi Code CLI getting started](https://moonshotai.github.io/kimi-code/en/guides/getting-started)
- [Kimi Code overview](https://www.kimi.com/code/docs/en/)
- [Kimi Code GitHub repository](https://github.com/MoonshotAI/kimi-code)
- [Kimi Code configuration files](https://moonshotai.github.io/kimi-code/en/configuration/config-files.html)
- [Kimi Code MCP](https://moonshotai.github.io/kimi-code/en/customization/mcp)
- [Kimi command reference](https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html)
- [Kimi K2.7 Code agent support](https://platform.kimi.ai/docs/guide/agent-support)

### Kimi Work Desktop

Fit: later desktop local-agent surface.

Why:

- Kimi Work is presented as an AI desktop for knowledge work.
- It connects to local files, supports browser automation through WebBridge,
  runs Python code in the background, and executes scheduled tasks.
- It has an Ask before acting safeguard for file modification, overwrites, and
  code execution.
- It is available as a macOS Apple silicon download and a Windows download.

Mythify implication:

- Do not treat Kimi Work as a simple coding CLI.
- Track it as a desktop workflow surface once Mythify has a desktop adapter
  story for current-session capabilities, permissions, local files, browser
  automation, scheduled tasks, and verification evidence.
- Until an automation contract is documented or locally probeable, Kimi Work
  should be `manual` for model switching and `manual` for spawning.

Sources:

- [Kimi Work](https://www.kimi.com/products/kimi-work)
- [Kimi products](https://www.kimi.com/products/)

### OpenCode

Fit: high-priority future CLI adapter.

Why:

- Official docs say OpenCode supports 75+ providers and local models through
  AI SDK and Models.dev.
- Config supports `provider`, `model`, and `small_model`.
- Local providers can use OpenAI-compatible base URLs, including llama.cpp,
  LM Studio, Ollama, and local NVIDIA NIM.
- MCP supports local and remote servers.
- Agents can have custom prompts, models, and tool access.
- The installed CLI exposes `opencode run -m provider/model`, `--variant`,
  `--agent`, `serve`, `web`, `mcp`, `models`, and provider auth commands.

Local probe:

- Installed version: `opencode 1.15.13`.
- `opencode run --help` confirms model, variant, agent, file attachment, JSON
  event format, server attach, and permission flags.
- `opencode serve --help` confirms a headless server surface.
- `opencode web --help` confirms a web interface surface.
- `opencode mcp --help` confirms MCP add, list, auth, logout, and debug.

Mythify implication:

- Add an `opencode` adapter after the generic command adapter.
- First operation can be `run_prompt` with `model`, `variant`, `agent`,
  `files`, `timeout`, `working_directory`, and `json_events`.
- Treat OpenCode Desktop as a later UI target. The immediate product value is
  CLI and server automation.

Sources:

- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [OpenCode config](https://opencode.ai/docs/config/)
- [OpenCode models](https://opencode.ai/docs/models/)
- [OpenCode providers](https://opencode.ai/docs/providers/)
- [OpenCode MCP servers](https://opencode.ai/docs/mcp-servers/)
- [OpenCode agents](https://opencode.ai/docs/agents/)
- [OpenCode download](https://opencode.ai/download)

### Google Antigravity

Fit: high-potential roadmap adapter, but local probe is currently blocked.

Why:

- Antigravity has a desktop app, IDE, and CLI/TUI surface.
- Google codelabs describe Antigravity CLI as bringing multi-step reasoning,
  multi-file editing, tool calling, and conversation history to the terminal.
- Antigravity CLI installation exposes `agy`.
- Codelabs show non-interactive mode through `agy -p "prompt"`.
- Antigravity supports MCP servers in desktop, IDE, and CLI.
- Codelabs show desktop and CLI MCP config paths:
  `~/.gemini/config/mcp_config.json`,
  `~/.gemini/antigravity-cli/mcp_config.json`, and `.agents/mcp_config.json`.
- Official docs expose CLI slash commands such as `/mcp` and `/model`.
- Antigravity settings include a model value in
  `~/.gemini/antigravity-cli/settings.json`.

Local probe:

- `/opt/homebrew/bin/agy` exists, but it execs
  `/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity`.
- That target does not exist in this environment, so `agy --help` and
  `agy --version` currently fail before Mythify can probe real flags.

Mythify implication:

- Do not claim Antigravity support until the adapter can run `agy --help`,
  `agy --version`, and one non-interactive dry run.
- Plan Antigravity as `manual` for current-session model switching unless a
  confirmed API or CLI flag can apply the change.
- Plan Antigravity workers as `applied` only if `agy -p` can accept model,
  profile, workspace, and permission controls in a verified local probe.
- MCP install docs can arrive earlier than a full worker adapter.

Sources:

- [Antigravity CLI hands-on codelab](https://codelabs.developers.google.com/antigravity-cli-hands-on)
- [Antigravity MCP codelab](https://codelabs.developers.google.com/google-workspace-mcp-antigravity)
- [Antigravity CLI MCP codelab](https://codelabs.developers.google.com/genai-for-dev-antigravity-cli)
- [Antigravity docs: CLI reference](https://antigravity.google/docs/cli-reference)
- [Antigravity docs: MCP](https://antigravity.google/docs/mcp)
- [Gemini CLI to Antigravity CLI transition](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)

## Google Execution and Agent Lifecycle Tools

### Google Colab CLI

Fit: later execution adapter, not a model adapter.

Why:

- Google announced Colab CLI on 2026-06-05.
- It bridges a local terminal to remote Colab runtimes.
- It can provision GPU and TPU runtimes, execute local Python scripts and
  notebooks remotely, retrieve artifacts, and export logs.
- It is explicitly positioned as usable by terminal-based AI agents.
- The official repository supports Linux and macOS, with Windows unsupported
  at the time of this research.

Mythify role fit:

- `executor`
- `trainer`
- `notebook_runner`
- `artifact_collector`
- heavy verification jobs that need accelerators

Mythify implication:

- Keep this outside model assignment.
- Add a separate `execution_adapter` lane after model and host adapters exist.
- Verification should capture remote command exit, log path, output artifacts,
  session id, accelerator type, and teardown status.
- Billing and data movement must be explicit.

Sources:

- [Google Developers Blog: Introducing the Google Colab CLI](https://developers.googleblog.com/introducing-the-google-colab-cli/)
- [Google Colab CLI GitHub repository](https://github.com/googlecolab/google-colab-cli)

### Google Agents CLI and ADK

Fit: later agent lifecycle adapter, not a general coding host.

Why:

- Agents CLI is a CLI and skills package for building, evaluating, deploying,
  and observing ADK agents on Google Cloud.
- Official docs say it works with coding agents such as Antigravity, Gemini
  CLI, Claude Code, Codex, and others.
- It can be installed with `uvx google-agents-cli setup`.
- It exposes commands for scaffold, run, eval, deploy, publish, infrastructure,
  data ingestion, and project info.
- The GitHub FAQ says it is not an alternative to Gemini CLI, Claude Code, or
  Codex. It is a tool for coding agents.
- ADK itself has CLI tools such as `adk create`, `adk run`, `adk eval`,
  `adk deploy`, `adk test`, and `adk web`.

Mythify role fit:

- `agent_project_scaffold`
- `agent_eval_runner`
- `agent_deployer`
- `observability_setup`

Mythify implication:

- Treat Agents CLI as a specialist workflow integration.
- It can be valuable for users building production agents, but it should not
  distract from Mythify's core local and API model adapter path.
- Any future integration should preserve Mythify's evidence doctrine by
  recording `agents-cli eval` and deploy command results as verifications.

Sources:

- [Agents CLI getting started](https://google.github.io/agents-cli/guide/getting-started/)
- [Agents CLI GitHub repository](https://github.com/google/agents-cli)
- [Google Cloud ADK with Agents CLI quickstart](https://docs.cloud.google.com/gemini-enterprise-agent-platform/agents/quickstart-adk)
- [ADK CLI reference](https://adk.dev/api-reference/cli/)
- [ADK coding with AI](https://adk.dev/tutorials/coding-with-ai/)

## Suggested Roadmap Order

1. Add a provider capability registry with no behavior change.
2. Add OpenAI-compatible local provider support first:
   Ollama, LM Studio, generic base URL, then llama.cpp and vLLM.
3. Add Kimi Code CLI and OpenCode CLI adapter spikes because both expose
   headless or non-interactive prompt paths and explicit model/provider
   configuration.
4. Repair and probe Antigravity CLI locally, then decide whether it belongs as
   a worker adapter, MCP setup guide, or both.
5. Add Colab CLI as a separate execution adapter only after local/API worker
   orchestration is stable.
6. Add Agents CLI/ADK as a specialist lifecycle workflow after Mythify has a
   clear story for agent eval evidence.

## Adapter Contract Implications

The adapter contract should separate these capabilities:

- `model_provider`: can answer one prompt with a selected model.
- `coding_host`: can read, edit, run commands, and optionally expose MCP.
- `execution_substrate`: can run jobs somewhere else and return logs and
  artifacts.
- `agent_lifecycle`: can scaffold, test, evaluate, deploy, and observe agents.

This prevents Colab CLI and Agents CLI from being squeezed into the wrong box.

## Next Experiments

1. Probe Ollama and LM Studio if installed, including `/v1/models` and one tiny
   completion request.
2. Done 2026-06-12: prototype a generic OpenAI-compatible provider probe
   against a fake server.
3. Done 2026-06-12: add Kimi Code and OpenCode CLI probe support with offline
   stub tests.
4. Add a real Kimi Code adapter spike:
   `kimi -p "summarize this repository"`.
5. Add a real OpenCode adapter spike:
   `opencode run --format json --model <provider/model>`.
6. Done 2026-06-12: add Antigravity CLI probe and MCP setup guide.
7. Done 2026-06-12: create a Colab CLI spike plan without running billable
   accelerator work.
8. Done 2026-06-13: create an Agents CLI and ADK spike plan around eval-help
   probes, not deployment.
