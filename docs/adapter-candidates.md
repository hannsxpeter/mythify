<!-- Generated from mcp-server/src/capability-registry.js by scripts/build_registry_docs.mjs. Edit the registry, then rebuild. -->

# Adapter Candidates

This file is generated from `mcp-server/src/capability-registry.js`. Do not edit it by hand.

| Adapter | Kind | Status | Local | OpenAI Compatible | Probe | Run Path | Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| antigravity | host | probe_supported | no | no | yes | none | probe material, not evidence |
| generic-openai-compatible | model_provider | local_backend_supported | yes | yes | yes | local roles: reader, triage | material, not evidence |
| google-adk-cli | agent_lifecycle | probe_supported | no | no | yes | eval probe; no eval run; no deploy | probe material, not evidence |
| google-agents-cli | agent_lifecycle | probe_supported | no | no | yes | eval probe; no eval run; no deploy | probe material, not evidence |
| google-colab-cli | execution_substrate | probe_supported | no | no | yes | no remote job | probe material, not evidence |
| kimi-code | host | worker_supported | no | no | yes | bounded worker | material, not evidence |
| llama-cpp | model_provider | local_profile_supported | yes | yes | yes | local roles: reader, triage | material, not evidence |
| lm-studio | model_provider | local_profile_supported | yes | yes | yes | local roles: reader, triage | material, not evidence |
| ollama | model_provider | local_profile_supported | yes | yes | yes | local roles: reader, triage | material, not evidence |
| opencode | host | worker_supported | no | no | yes | bounded worker | material, not evidence |
| vllm | model_provider | candidate | yes | yes | unknown | none | unknown |
