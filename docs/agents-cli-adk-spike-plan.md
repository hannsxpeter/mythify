# Google Agents CLI and ADK Spike Plan

Date: 2026-06-13

Purpose: define the first safe Mythify slice for Google Agents CLI and Google
ADK CLI without scaffolding a project, running an agent, executing evals,
deploying, publishing, mutating cloud resources, or writing project state.

## Current Support

Mythify supports a probe-only agent lifecycle adapter through the MCP tool
`lifecycle_probe` with these adapters:

- `google-agents-cli`
- `google-adk-cli`

The probe is intentionally narrow:

- Resolve a CLI binary from `bin`, adapter-specific environment variables,
  PATH, or common install paths.
- Run `--version`.
- Run `--help`.
- Run `eval --help`.
- Return binary resolution, command output tails, status, and guard fields.
- Return `lifecycle_lane_contract` with allowed probe commands, disabled
  lifecycle actions, future guarded actions, eval and deployment prerequisites,
  mutation policy, and material-only evidence status.
- Mark the result as material, not verification evidence.

Environment variables:

- `MYTHIFY_AGENTS_CLI_BIN`
- `MYTHIFY_ADK_BIN`

The probe always reports:

- `material_not_evidence: true`
- `evidence_status: "lifecycle_probe_output_not_verification"`
- `writes_state: false`
- `verification_recorded: false`
- `eval_execution_enabled: false`
- `deployment_enabled: false`
- `scaffold_enabled: false`
- `run_enabled: false`
- `cloud_mutation_enabled: false`
- `project_mutation_enabled: false`
- `billing_guard: "probe_only_no_project_or_cloud_mutation"`

`lifecycle_lane_contract` always reports:

- `current_policy: "probe_only"`
- `allowed_probe_actions: ["probe_version", "probe_help", "probe_eval_help"]`
- `allowed_probe_commands: ["--version", "--help", "eval --help"]`
- `disabled_actions` including project scaffold, agent run, eval execution,
  deployment, publishing, cloud mutation, and project mutation.
- `future_guarded_actions: ["eval_execution", "deployment", "publishing"]`
- `required_before_eval_execution` fields for explicit user request, project
  path, eval data, timeout, credentials, mutation acknowledgement, material
  boundary, and artifact or report path.
- `required_before_deployment` fields for explicit user request, target
  platform, project id, region, billing, data movement, cloud mutation
  acknowledgement, rollback or teardown posture, and material boundary.

## Non-Goals

This slice does not:

- Run `agents-cli setup`.
- Run `agents-cli scaffold`, `agents-cli create`, or project enhancement
  commands.
- Run `agents-cli eval generate`, `agents-cli eval grade`, or any eval
  execution command.
- Run `adk create`, `adk run`, `adk eval`, `adk web`, or `adk deploy`.
- Deploy to Google Cloud, Agent Runtime, Cloud Run, GKE, or Agent Engine.
- Publish to Gemini Enterprise.
- Start local web servers or playgrounds.
- Authenticate with Google Cloud or AI Studio.
- Upload data, create cloud resources, or mutate existing cloud resources.
- Write Mythify state.
- Count as verification evidence.

## Future Lifecycle Contract

A later lifecycle adapter may run eval or deployment work only after a separate
design slice adds explicit contracts for project mutation, cloud mutation,
credentials, billing, data movement, and teardown.

Required evidence fields for future eval execution:

- Local command and full argument vector.
- Adapter name and lifecycle action.
- Working directory.
- Agent project path.
- Eval dataset path or eval set id.
- Command exit code.
- Start time, end time, and duration.
- Report path, trace path, or artifact ids.
- Metric summary.
- Cloud resource mutation flag.
- Project file mutation summary.
- Credential source summary.

Required evidence fields for future deployment:

- Local command and full argument vector.
- Target platform.
- Project id and region.
- Deployed resource ids.
- Command exit code.
- Start time, end time, and duration.
- Deployment logs or artifact ids.
- Billing acknowledgement.
- Data movement acknowledgement.
- Rollback or teardown posture.

## Guardrails

- Default to probe-only behavior.
- Require an explicit user request before any lifecycle action command.
- Require explicit confirmation before project mutation, cloud mutation,
  deployment, publishing, or authentication setup.
- Treat lifecycle logs and reports as material until an executed verifier
  consumes them.
- Never let `lifecycle_probe` become a hidden scaffold, eval runner, deployer,
  publisher, or cloud mutator.
