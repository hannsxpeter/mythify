// Smoke test for the Mythify MCP server.
// Spawns the real server over stdio with MYTHIFY_DIR and HOME pointed at
// fresh temp directories, exercises the tool surface through the SDK Client,
// then asserts the on-disk state formats byte-level field contracts.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { MEMORY_CLEAR_MCP_REFUSAL } from "../src/operation-registry.js";
import { CLI_TIERS, MCP_TIERS, MCP_TOOL_COUNT, MCP_TOOL_NAMES } from "../src/surface-manifest.js";

const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));
const CLASSIFICATION_RULES = JSON.parse(
  fs.readFileSync(new URL("../protocol/classification-rules.json", import.meta.url), "utf8")
);
const ROOT_CLASSIFICATION_RULES = JSON.parse(
  fs.readFileSync(new URL("../../protocol/classification-rules.json", import.meta.url), "utf8")
);
const WORKFLOW_ROUTER = JSON.parse(
  fs.readFileSync(new URL("../protocol/workflow-router.json", import.meta.url), "utf8")
);
const ROOT_WORKFLOW_ROUTER = JSON.parse(
  fs.readFileSync(new URL("../../protocol/workflow-router.json", import.meta.url), "utf8")
);
const OPERATION_REGISTRY = JSON.parse(
  fs.readFileSync(new URL("../protocol/operation-registry.json", import.meta.url), "utf8")
);
const ROOT_OPERATION_REGISTRY = JSON.parse(
  fs.readFileSync(new URL("../../protocol/operation-registry.json", import.meta.url), "utf8")
);
const SURFACE_MANIFEST = JSON.parse(
  fs.readFileSync(new URL("../protocol/surface-manifest.json", import.meta.url), "utf8")
);
const ROOT_SURFACE_MANIFEST = JSON.parse(
  fs.readFileSync(new URL("../../protocol/surface-manifest.json", import.meta.url), "utf8")
);
const PACKAGE_JSON = JSON.parse(
  fs.readFileSync(new URL("../package.json", import.meta.url), "utf8")
);

function textOf(result) {
  assert.ok(Array.isArray(result.content), "tool result has a content array");
  const texts = result.content.filter((c) => c.type === "text").map((c) => c.text);
  assert.ok(texts.length > 0, "tool result has at least one text block");
  return texts.join("\n");
}

function snapshotStateDir(root) {
  const snapshot = {};
  function walk(dir) {
    for (const name of fs.readdirSync(dir).sort()) {
      const itemPath = path.join(dir, name);
      const stat = fs.statSync(itemPath);
      if (stat.isDirectory()) {
        walk(itemPath);
      } else if (stat.isFile()) {
        const rel = path.relative(root, itemPath).split(path.sep).join("/");
        snapshot[rel] = crypto
          .createHash("sha256")
          .update(fs.readFileSync(itemPath))
          .digest("hex");
      }
    }
  }
  if (fs.existsSync(root)) {
    walk(root);
  }
  return snapshot;
}

function jsonlLockDir(stateDir, filePath) {
  const digest = crypto.createHash("sha256").update(path.resolve(filePath)).digest("hex").slice(0, 16);
  return path.join(stateDir, "locks", `jsonl-${digest}.lock`);
}

test("mythify MCP server smoke test", async (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-smoke-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-smoke-home-"));
  const triageStub = path.join(stateDir, "triage-stub.js");
  fs.writeFileSync(
    triageStub,
    [
      "process.stdin.resume();",
      "process.stdin.on('end', () => {",
      "  console.log(JSON.stringify({",
      "    primary_type: 'benchmark',",
      "    secondary_types: ['evaluation'],",
      "    ambiguity: 'low',",
      "    hidden_questions: [],",
      "    likely_files_or_surfaces: ['scripts/local_model_eval.py'],",
      "    verification_plan: ['run benchmark harness'],",
      "    fanout_plan: [],",
      "    risk_notes: [],",
      "    recommended_first_step: 'run the harness'",
      "  }));",
      "});",
      "",
    ].join("\n"),
    "utf8"
  );

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
      MYTHIFY_TRIAGE_ENGINE: "command",
      MYTHIFY_TRIAGE_COMMAND: `${process.execPath} ${triageStub}`,
      MYTHIFY_REQUIRE_VERIFIED_STEP: "",
      MYTHIFY_ROLE_READER_PROVIDER: "host",
      MYTHIFY_ROLE_REVIEWER_PROVIDER: "surprise-cloud",
    },
  });
  const client = new Client({ name: "mythify-smoke-test", version: "2.4.0" });
  await client.connect(transport);

  try {
    await t.test("tools/list returns exactly the manifest tools", async () => {
      const { tools } = await client.listTools();
      const names = tools.map((tool) => tool.name).sort();
      assert.equal(names.length, MCP_TOOL_COUNT);
      assert.deepEqual(names, [...MCP_TOOL_NAMES].sort());
      const toolMap = new Map(tools.map((tool) => [tool.name, tool]));
      assert.match(toolMap.get("workflow_route").description, /recommended first tool/);
      assert.match(toolMap.get("plan_create").description, /workflow_route first/);
      assert.match(toolMap.get("fanout_start").description, /workflow_route first/);
      assert.deepEqual(CLI_TIERS.front_door, ["route", "report", "verify", "status"]);
      assert.deepEqual(MCP_TIERS.front_door, [
        "workflow_route",
        "work_report",
        "verify_run",
        "workflow_status",
      ]);
      assert.ok(MCP_TIERS.labs.includes("host_model_switch"));
    });

    await t.test("packaged manifests mirror the root manifests", () => {
      assert.deepEqual(CLASSIFICATION_RULES, ROOT_CLASSIFICATION_RULES);
      assert.deepEqual(WORKFLOW_ROUTER, ROOT_WORKFLOW_ROUTER);
      assert.deepEqual(OPERATION_REGISTRY, ROOT_OPERATION_REGISTRY);
      assert.deepEqual(SURFACE_MANIFEST, ROOT_SURFACE_MANIFEST);
      assert.equal(CLASSIFICATION_RULES.schema_version, 2);
      assert.equal(CLASSIFICATION_RULES.thresholds.trivial_word_count, 12);
      assert.ok(CLASSIFICATION_RULES.question_prefixes.includes("what "));
      assert.ok(CLASSIFICATION_RULES.vague_request_terms.includes("better"));
      assert.ok(CLASSIFICATION_RULES.risk.high_task_types.includes("release"));
      assert.ok(CLASSIFICATION_RULES.ceremony.light_low_risk_task_types.includes("review"));
      assert.ok(CLASSIFICATION_RULES.fanout.recommended_task_types.includes("benchmark"));
      assert.ok(CLASSIFICATION_RULES.fanout.optional_terms.includes("multiple files"));
      assert.equal(CLASSIFICATION_RULES.fanout_visibility.default.visibility, "summary");
      assert.ok(CLASSIFICATION_RULES.execution_profile.fast_task_types.includes("bugfix"));
      assert.ok(Object.hasOwn(CLASSIFICATION_RULES.next_actions, "standard"));
      assert.ok(CLASSIFICATION_RULES.model_triage.recommended_task_types.includes("debugging"));
      assert.ok(Object.hasOwn(CLASSIFICATION_RULES.verification_hints, "feature"));
    });

    await t.test("classify_task recommends ceremony and verification", async () => {
      const classified = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: { task: "benchmark bare codex vs mythify across tasks" },
        })
      );
      assert.ok(classified.startsWith("[OK]"), `classification reports [OK]: ${classified}`);
      assert.match(classified, /type: benchmark/, "classification detects benchmark work");
      assert.match(classified, /ceremony: full/, "benchmark work gets full ceremony");
      assert.match(classified, /execution profile: full/, "benchmark work gets full execution profile");
      assert.match(classified, /fanout: recommended/, "benchmark work can use fanout");
      assert.match(classified, /model triage: recommended/, "benchmark work gets model triage");

      const jsonText = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: { task: "what does this project do?", format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.task_type, "question");
      assert.equal(parsed.ceremony, "none");
      assert.equal(parsed.execution_profile, "direct");
      assert.equal(parsed.fanout_visibility, "summary");
      assert.equal(parsed.model_policy.session.control, "host_selected");
      assert.equal(parsed.model_policy.fanout_worker.visibility, "summary");
      assert.equal(parsed.model_policy.verifier.engine, "local_command");
      assert.equal(parsed.model_policy.session.recommendation.target_profile, "fast");
      assert.equal(parsed.model_policy.provider_defaults.fallback_policy, "no_implicit_cross_provider_fallback");
      assert.equal(parsed.model_policy.provider_defaults.provider_catalog.api_provider.execution_enabled, false);
      assert.deepEqual(parsed.model_policy.provider_defaults.provider_catalog.api_provider.default_roles, []);
      assert.deepEqual(parsed.model_policy.provider_defaults.provider_catalog.host_cli.default_roles, [
        "triage",
        "fanout_worker",
        "reviewer",
      ]);
      assert.equal(
        parsed.model_policy.provider_defaults.provider_catalog.local_openai_compatible.evidence_status,
        "model_output_not_verification"
      );
      assert.equal(parsed.model_policy.provider_defaults.adapter_interface_contract.version, 1);
      assert.equal(
        parsed.model_policy.provider_defaults.adapter_interface_contract.execution_policy,
        "metadata_shape_only_no_runtime_change"
      );
      assert.ok(
        parsed.model_policy.provider_defaults.adapter_interface_contract.fields.includes("evidence_status")
      );
      assert.ok(
        parsed.model_policy.provider_defaults.adapter_interface_contract.fields.includes("guardrails")
      );
      assert.ok(
        parsed.model_policy.provider_defaults.adapter_interface_contract.lanes.includes("execution_substrate")
      );
      assert.equal(
        parsed.model_policy.provider_defaults.adapter_interface_contract.candidates.opencode.execution_enabled,
        true
      );
      assert.equal(
        parsed.model_policy.provider_defaults.adapter_interface_contract.candidates["openai-api"].execution_enabled,
        false
      );
      assert.equal(
        parsed.model_policy.provider_defaults.adapter_interface_contract.candidates["google-colab-cli"].writes_state,
        false
      );
      const roleAssignment = parsed.model_policy.provider_defaults.role_assignment_contract;
      assert.equal(roleAssignment.version, 1);
      assert.equal(roleAssignment.execution_policy, "metadata_shape_only_no_runtime_change");
      assert.equal(roleAssignment.runtime_routing_changed, false);
      assert.deepEqual(roleAssignment.roles.triage.eligible_adapter_lanes, [
        "host",
        "model_provider",
        "custom_adapter",
      ]);
      assert.ok(roleAssignment.roles.triage.eligible_candidate_ids.includes("opencode"));
      assert.ok(roleAssignment.roles.fanout_worker.eligible_candidate_ids.includes("openai-api"));
      assert.equal(roleAssignment.roles.reader.selected_provider, "host");
      assert.equal(roleAssignment.roles.verifier.writes_state_allowed, true);
      assert.equal(roleAssignment.roles.verifier.material_not_evidence_required, false);
      assert.ok(roleAssignment.roles.remote_execution.eligible_candidate_ids.includes("google-colab-cli"));
      assert.ok(
        roleAssignment.roles.remote_execution.execution_enabled_candidate_ids.includes("google-colab-cli")
      );
      assert.deepEqual(roleAssignment.roles.remote_execution.required_acknowledgements, [
        "billing_ack_required",
        "data_movement_ack_required",
        "cleanup_ack_required",
      ]);
      assert.ok(roleAssignment.roles.agent_lifecycle.eligible_candidate_ids.includes("google-agents-cli"));
      assert.ok(roleAssignment.roles.agent_lifecycle.eligible_candidate_ids.includes("google-adk-cli"));
      assert.equal(roleAssignment.roles.agent_lifecycle.execution_enabled_candidate_ids.length, 0);
      assert.equal(parsed.model_policy.provider_defaults.api_provider_contract.status, "metadata_supported");
      assert.equal(parsed.model_policy.provider_defaults.api_provider_contract.execution_enabled, false);
      assert.equal(parsed.model_policy.provider_defaults.api_provider_contract.fanout_execution_enabled, true);
      assert.deepEqual(parsed.model_policy.provider_defaults.api_provider_contract.fanout_engines, [
        "anthropic",
        "openai",
      ]);
      assert.deepEqual(
        parsed.model_policy.provider_defaults.api_provider_contract.required_fanout_acknowledgements,
        [
          "hosted_provider_billing_ack",
          "hosted_provider_data_ack",
          "hosted_provider_material_ack",
        ]
      );
      assert.equal(
        parsed.model_policy.provider_defaults.api_provider_contract.fanout_audit_log,
        ".mythify/provider-audit.jsonl"
      );
      assert.equal(
        parsed.model_policy.provider_defaults.api_provider_contract.fanout_output_material_status,
        "material_not_verification"
      );
      assert.equal(
        parsed.model_policy.provider_defaults.custom_adapter_contract.execution_policy,
        "explicit_only_no_hidden_fallback"
      );
      assert.equal(
        parsed.model_policy.provider_defaults.custom_adapter_contract.command.execution_enabled,
        true
      );
      assert.deepEqual(
        parsed.model_policy.provider_defaults.custom_adapter_contract.command.command_env,
        ["MYTHIFY_TRIAGE_COMMAND", "MYTHIFY_FANOUT_COMMAND"]
      );
      assert.equal(
        parsed.model_policy.provider_defaults.custom_adapter_contract.http.execution_enabled,
        false
      );
      assert.equal(
        parsed.model_policy.provider_defaults.custom_adapter_contract.http.base_url_env,
        "MYTHIFY_CUSTOM_HTTP_BASE_URL"
      );
      assert.ok(
        parsed.model_policy.provider_defaults.timeout_metadata_fields.includes("timeout_seconds")
      );
      assert.ok(
        parsed.model_policy.provider_defaults.cost_metadata_fields.includes("cost_estimate_status")
      );
      assert.equal(
        parsed.model_policy.provider_defaults.api_provider_contract.billing_policy,
        "explicit_provider_required"
      );
      assert.equal(
        parsed.model_policy.provider_defaults.api_provider_contract.providers["openai-api"].api_key_env,
        "OPENAI_API_KEY"
      );
      assert.equal(
        parsed.model_policy.provider_defaults.api_provider_contract.providers["anthropic-api"].auth_header,
        "x-api-key"
      );
      assert.equal(
        parsed.model_policy.provider_defaults.api_provider_contract.providers["openai-compatible-hosted"].base_url_env,
        "MYTHIFY_HOSTED_OPENAI_COMPAT_BASE_URL"
      );
      assert.equal(parsed.model_policy.provider_defaults.roles.reader.provider, "host");
      assert.equal(parsed.model_policy.provider_defaults.roles.reader.provider_source, "env:MYTHIFY_ROLE_READER_PROVIDER");
      assert.equal(parsed.model_policy.provider_defaults.roles.reviewer.provider, "host_cli");
      assert.equal(parsed.model_policy.provider_defaults.roles.reviewer.status, "invalid_env_ignored");
      assert.equal(parsed.model_policy.provider_defaults.roles.reviewer.provider_profile.control, "bounded_worker");
      assert.equal(parsed.model_policy.reader.provider, "host");
      assert.equal(parsed.model_policy.triage.timeout.timeout_seconds, 120);
      assert.equal(parsed.model_policy.reader.timeout.timeout_seconds, 30);
      assert.equal(parsed.model_policy.fanout_worker.timeout.timeout_seconds, 600);
      assert.equal(
        parsed.model_policy.fanout_worker.cost.billing,
        "host_cli_subscription_or_local_quota"
      );
      assert.equal(parsed.model_policy.fanout_worker.cost.cost_estimate_status, "not_estimated");
      assert.equal(parsed.model_policy.fanout_worker.cost.cost_estimate_cents, null);
      assert.equal(parsed.model_policy.verifier.cost.billing, "local_compute");
      assert.equal(parsed.model_policy.reader.evidence_status, "model_output_not_verification");
      assert.equal(parsed.model_policy.reviewer.stronger_model_policy, "same_or_lower");
      assert.equal(parsed.model_policy.reviewer.stronger_model_policy_source, "default");
      assert.equal(parsed.model_policy.reviewer.stronger_models_allowed, false);

      const directText = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: {
            task: "what is 1 + 1?",
            format: "json",
            platform: "codex-desktop",
            session_model: "gpt-5.5",
          },
        })
      );
      const direct = JSON.parse(directText.replace(/^\[OK\] /, ""));
      assert.equal(direct.execution_profile, "direct");
      assert.equal(direct.model_policy.session.recommendation.action, "downgrade");
      assert.equal(direct.model_policy.session.recommendation.target_profile, "fast");
      assert.equal(direct.model_policy.session.recommendation.target_model, "gpt-5.4-mini");
      assert.equal(direct.model_policy.session.recommendation.target_model_tier, "fast");
      assert.equal(direct.model_policy.session.recommendation.thinking, "low");
      assert.equal(direct.model_policy.session.recommendation.speed, "fast");

      const researchText = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: {
            task: "make me a research paper about memory consolidation in LLM agents",
            format: "json",
            platform: "claude-desktop",
            session_model: "haiku",
          },
        })
      );
      const research = JSON.parse(researchText.replace(/^\[OK\] /, ""));
      assert.equal(research.task_type, "research");
      assert.equal(research.model_policy.session.recommendation.action, "upgrade");
      assert.equal(research.model_policy.session.recommendation.target_profile, "strong");
      assert.equal(research.model_policy.session.recommendation.target_model, "opus");
      assert.equal(research.model_policy.session.recommendation.thinking, "high");
      assert.equal(research.model_policy.session.recommendation.speed, "standard");

      const strongerReviewerText = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: {
            task: "audit this release for hidden regressions",
            format: "json",
            session_model: "haiku",
            reviewer_strength: "allow_stronger",
          },
        })
      );
      const strongerReviewer = JSON.parse(strongerReviewerText.replace(/^\[OK\] /, ""));
      assert.equal(strongerReviewer.model_policy.reviewer.stronger_model_policy, "allow_stronger");
      assert.equal(strongerReviewer.model_policy.reviewer.stronger_model_policy_source, "explicit");
      assert.equal(strongerReviewer.model_policy.reviewer.stronger_models_allowed, true);
      assert.equal(
        strongerReviewer.model_policy.reviewer.model_relation_to_session,
        "may_exceed_session_with_reviewer_opt_in"
      );

      const reviewText = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: { task: "Evaluate the Mythify codebase and product", format: "json" },
        })
      );
      const reviewRules = CLASSIFICATION_RULES.task_types.find((entry) => entry.id === "review");
      assert.ok(reviewRules.terms.includes("evaluate"));
      const reviewParsed = JSON.parse(reviewText.replace(/^\[OK\] /, ""));
      assert.equal(reviewParsed.task_type, "review");
      assert.equal(reviewParsed.ceremony, "light");
      assert.equal(reviewParsed.execution_profile, "fast");
      assert.ok(reviewParsed.signals.includes("evaluate"));

      const triagedText = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: {
            task: "benchmark bare codex vs mythify across tasks",
            format: "json",
            triage: "auto",
            platform: "claude-desktop",
            effort: "auto",
            speed: "fast",
            session_model: "sonnet",
            spawn_ceiling: "same_or_lower",
          },
        })
      );
      const triaged = JSON.parse(triagedText.replace(/^\[OK\] /, ""));
      assert.equal(triaged.model_policy.session.platform, "claude-desktop");
      assert.equal(triaged.execution_profile, "full");
      assert.equal(triaged.model_policy.session.model, "sonnet");
      assert.equal(triaged.model_policy.session.model_source, "explicit");
      assert.equal(triaged.model_policy.session.model_tier, "strong");
      assert.equal(triaged.model_policy.spawn_ceiling.policy, "same_or_lower");
      assert.equal(triaged.model_policy.fanout_worker.model_relation_to_session, "same_or_lower");
      assert.equal(triaged.model_policy.triage.effort, "low");
      assert.equal(triaged.model_policy.triage.speed, "fast");
      assert.equal(triaged.model_policy.fanout_worker.effort, "high");
      assert.equal(triaged.model_policy.fanout_worker.speed, "fast");
      assert.equal(triaged.model_triage_run.attempted, true);
      assert.equal(triaged.model_triage_run.ok, true);
      assert.equal(triaged.model_triage_run.engine, "command");
      assert.equal(triaged.model_triage_run.model_policy, "command_default");
      assert.equal(triaged.model_triage_run.effort, "low");
      assert.equal(triaged.model_triage_run.speed, "fast");
      assert.equal(triaged.model_triage_run.parsed.primary_type, "benchmark");
    });

    await t.test("host_model_switch records a host model for later policy", async () => {
      const switched = textOf(
        await client.callTool({
          name: "host_model_switch",
          arguments: {
            platform: "codex-desktop",
            target_model: "gpt-5.4",
            current_model: "gpt-5.3-codex",
            thinking: "high",
            speed: "fast",
          },
        })
      );
      assert.ok(switched.startsWith("[OK]"), `host_model_switch reports [OK]: ${switched}`);
      assert.match(switched, /target model: gpt-5\.4/, "text includes the target model");
      assert.match(switched, /switch status: manual/, "text reports manual switch status");
      assert.match(switched, /current-chat confirmed: no/, "text does not claim confirmation");
      assert.match(switched, /host-confirmed model: unsupported/, "text reports unsupported confirmation");
      assert.match(switched, /confirmation source: none/, "text reports no confirmation source");
      assert.match(switched, /adapter proof scan: metadata_only/, "text reports adapter proof scan");
      assert.match(switched, /current-chat apply proof: unsupported/, "text reports unsupported current-chat apply");
      assert.match(switched, /current-chat confirm proof: unsupported/, "text reports unsupported current-chat confirm");
      assert.match(switched, /new-thread model proof: supported/, "text reports new-thread model proof");
      assert.match(switched, /worker model proof: supported/, "text reports worker model proof");
      assert.match(switched, /thinking proof: supported/, "text reports thinking proof");
      assert.match(switched, /current-chat switch: no/, "text does not claim current-chat switching");
      assert.match(switched, /new-thread model: yes/, "text exposes new-thread model capability");
      assert.match(switched, /worker model: yes/, "text exposes worker model capability");

      const statusText = textOf(
        await client.callTool({
          name: "host_model_switch",
          arguments: { action: "status", format: "json" },
        })
      );
      const status = JSON.parse(statusText.replace(/^\[OK\] /, ""));
      assert.equal(status.target_model, "gpt-5.4");
      assert.equal(status.platform, "codex-desktop");
      assert.equal(status.status, "recorded_requires_host_action");
      assert.equal(status.can_apply_current_chat, false);
      assert.equal(status.switch_result.status, "manual");
      assert.equal(status.switch_result.requested_model, "gpt-5.4");
      assert.equal(status.switch_result.requested_thinking, "high");
      assert.equal(status.switch_result.requested_speed, "fast");
      assert.equal(status.switch_result.current_chat_supported, false);
      assert.equal(status.switch_result.current_chat_confirmed, false);
      assert.equal(status.switch_result.manual_action_required, true);
      assert.equal(status.switch_result.applied_by, "none");
      assert.equal(status.host_confirmation.requested_model, "gpt-5.4");
      assert.equal(status.host_confirmation.user_reported_current_model, "gpt-5.3-codex");
      assert.equal(status.host_confirmation.current_model_confirmed, false);
      assert.equal(status.host_confirmation.confirmed_current_model, "");
      assert.equal(status.host_confirmation.confirmation_status, "unsupported");
      assert.equal(status.host_confirmation.confirmation_source, "none");
      assert.equal(
        status.host_confirmation.unsupported_reason,
        "host_capability_cannot_confirm_current_model"
      );
      assert.equal(status.adapter_proof_scan.status, "metadata_only");
      assert.equal(status.adapter_proof_scan.host_state_mutated, false);
      assert.equal(status.adapter_proof_scan.verification_recorded, false);
      assert.equal(status.adapter_proof_scan.material_not_evidence, true);
      assert.equal(status.adapter_proof_scan.paths.current_chat_model_apply.status, "unsupported");
      assert.equal(status.adapter_proof_scan.paths.current_chat_model_confirm.status, "unsupported");
      assert.equal(status.adapter_proof_scan.paths.new_thread_model_apply.status, "supported");
      assert.equal(status.adapter_proof_scan.paths.worker_model_apply.status, "supported");
      assert.equal(status.adapter_proof_scan.paths.thinking_apply.status, "supported");
      assert.equal(status.host_capability.kind, "host");
      assert.equal(status.host_capability.status, "supported");
      assert.equal(status.host_capability.can_switch_current_thread, false);
      assert.equal(status.host_capability.can_set_new_thread_model, true);
      assert.equal(status.host_capability.can_set_worker_model, true);
      assert.equal(status.host_capability.can_set_thinking, true);

      const classifiedText = textOf(
        await client.callTool({
          name: "classify_task",
          arguments: {
            task: "implement a follow-up feature",
            format: "json",
            platform: "codex-desktop",
          },
        })
      );
      const classified = JSON.parse(classifiedText.replace(/^\[OK\] /, ""));
      assert.equal(classified.model_policy.session.model, "gpt-5.4");
      assert.equal(classified.model_policy.session.model_source, "host_model_switch");
      assert.equal(classified.model_policy.session.model_tier, "frontier");
    });

    await t.test("workflow_route selects routes without mutating state", async () => {
      const before = snapshotStateDir(stateDir);
      const cases = [
        ["what does Mythify do?", "direct", "analysis"],
        ["research latest agent routing patterns", "research", "research"],
        ["audit this project for issues", "review", "review"],
        ["address all issues in one go", "campaign", "campaign"],
        ["keep fixing until tests pass and verify command is green", "outcome", "handoff"],
        ["implement the router feature", "plan", "analysis"],
      ];
      for (const [task, route, packet] of cases) {
        const routedText = textOf(
          await client.callTool({
            name: "workflow_route",
            arguments: { task, format: "json" },
          })
        );
        const payload = JSON.parse(routedText.replace(/^\[OK\] /, ""));
        assert.equal(payload.kind, "workflow_route");
        assert.equal(payload.route, route);
        assert.equal(payload.prompt_packet.kind, packet);
        assert.equal(payload.chat_policy.executor, "initiating_host");
        assert.equal(payload.evidence.at(-1).mutates_state, false);
        assert.match(payload.guardrail, /not verification evidence/);
      }
      assert.deepEqual(snapshotStateDir(stateDir), before, "workflow_route leaves state unchanged");
    });

    await t.test("memory_store then memory_recall round-trips a value", async () => {
      const stored = textOf(
        await client.callTool({
          name: "memory_store",
          arguments: { key: "color", value: "blue", category: "fact" },
        })
      );
      assert.ok(stored.startsWith("[OK]"), `store reports [OK]: ${stored}`);

      const recalled = textOf(
        await client.callTool({
          name: "memory_recall",
          arguments: { query: "blue" },
        })
      );
      assert.ok(recalled.startsWith("[OK]"), `recall reports [OK]: ${recalled}`);
      assert.match(recalled, /color/, "recall finds the stored key");
      assert.match(recalled, /blue/, "recall finds the stored value");
    });

    await t.test("plan_update_step enforces the evidence rule", async () => {
      const created = textOf(
        await client.callTool({
          name: "plan_create",
          arguments: {
            goal: "Smoke goal",
            steps: [{ title: "First step", success_criteria: "exit code is zero" }],
          },
        })
      );
      assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);

      const snapshotBeforeRefusal = snapshotStateDir(stateDir);
      const refused = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: { step_id: 1, status: "completed" },
        })
      );
      assert.ok(refused.startsWith("[FAIL]"), `refusal starts with [FAIL]: ${refused}`);
      assert.match(refused, /Evidence required/, "refusal explains the evidence rule");
      assert.deepEqual(
        snapshotStateDir(stateDir),
        snapshotBeforeRefusal,
        "refused plan_update_step leaves every state file unchanged"
      );

      const planAfterRefusal = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "smoke-goal.json"), "utf8")
      );
      assert.equal(
        planAfterRefusal.steps[0].status,
        "pending",
        "refused update leaves the step pending"
      );

      const strictRefused = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "command exited 0 as required",
          },
        })
      );
      assert.ok(strictRefused.startsWith("[FAIL]"), `strict completion refuses: ${strictRefused}`);
      assert.match(strictRefused, /Verified evidence required/);

      const inProgress = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: { step_id: 1, status: "in_progress" },
        })
      );
      assert.ok(inProgress.startsWith("[OK]"), `in_progress succeeds: ${inProgress}`);

      const verified = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(0)"', claim: "smoke step verified" },
        })
      );
      assert.ok(verified.startsWith("[OK]"), `verify_run succeeds: ${verified}`);

      const accepted = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "command exited 0 as required",
          },
        })
      );
      assert.ok(accepted.startsWith("[OK]"), `update with result succeeds: ${accepted}`);
    });

    await t.test("verify_run reports VERIFIED on exit 0 and UNVERIFIED otherwise", async () => {
      const passed = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(0)"', claim: "node can exit zero" },
        })
      );
      assert.ok(passed.startsWith("[OK]"), `passing run reports [OK]: ${passed}`);
      assert.match(passed, /VERIFIED/, "passing run is VERIFIED");
      assert.doesNotMatch(passed, /UNVERIFIED/, "passing run is not UNVERIFIED");
      const passedRecord = JSON.parse(
        fs.readFileSync(path.join(stateDir, "verifications.jsonl"), "utf8").trim().split(/\n/).at(-1)
      );
      assert.equal(passedRecord.plan, null);
      assert.equal(passedRecord.step_id, null);
      assert.equal(passedRecord.step_title, null);
      assert.equal(passedRecord.step_status, null);

      const failed = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(3)"' },
        })
      );
      assert.ok(failed.startsWith("[FAIL]"), `failing run reports [FAIL]: ${failed}`);
      assert.match(failed, /UNVERIFIED/, "failing run is UNVERIFIED");
      assert.match(failed, /exit 3/, "failing run reports the exit code");
    });

    await t.test("workflow_route prioritizes failed verification recovery", async () => {
      const before = snapshotStateDir(stateDir);
      const routedText = textOf(
        await client.callTool({
          name: "workflow_route",
          arguments: { task: "research a new direction", format: "json" },
        })
      );
      const payload = JSON.parse(routedText.replace(/^\[OK\] /, ""));
      assert.equal(payload.route, "failure");
      assert.equal(payload.prompt_packet.kind, "failure");
      assert.equal(payload.state.latest_executed_verification.exit_code, 3);
      assert.deepEqual(snapshotStateDir(stateDir), before, "workflow_route failure path leaves state unchanged");
    });

    await t.test("workflow_status shows read-only dashboard state", async () => {
      const before = snapshotStateDir(stateDir);
      const text = textOf(
        await client.callTool({
          name: "workflow_status",
          arguments: { recent: 1 },
        })
      );
      assert.ok(text.startsWith("[OK] Workflow dashboard"), `workflow_status reports [OK]: ${text}`);
      assert.match(text, /Active plan: smoke-goal/);
      assert.match(text, /Evidence:/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "workflow_status leaves state unchanged");

      const jsonText = textOf(
        await client.callTool({
          name: "workflow_status",
          arguments: { recent: 1, format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.active_plan.slug, "smoke-goal");
      assert.equal(parsed.verification_summary.recent.length, 1);
      assert.equal(parsed.counts.memory, 1);
    });

    await t.test("verification_history shows read-only evidence history", async () => {
      const before = snapshotStateDir(stateDir);
      const text = textOf(
        await client.callTool({
          name: "verification_history",
          arguments: { recent: 2 },
        })
      );
      assert.ok(text.startsWith("[OK] Verification history"), `verification_history reports [OK]: ${text}`);
      assert.match(text, /Evidence: 3 executed \(2 passed, 1 failed\), 0 attested, 3 total/);
      assert.match(text, /passed: node can exit zero/);
      assert.match(text, /failed: node -e "process.exit\(3\)"/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "verification_history leaves state unchanged");

      const jsonText = textOf(
        await client.callTool({
          name: "verification_history",
          arguments: { recent: 2, format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.counts.executed_passed, 2);
      assert.equal(parsed.counts.executed_failed, 1);
      assert.deepEqual(
        parsed.records.map((record) => record.verdict),
        ["failed", "passed"]
      );
    });

    await t.test("work_report shows chat-ready updates and supports cursors", async () => {
      const before = snapshotStateDir(stateDir);
      const peek = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { since: "start", recent: 10, peek: true },
        })
      );
      assert.ok(peek.startsWith("[OK] Live work report"), `work_report reports [OK]: ${peek}`);
      assert.match(peek, /Plan created: smoke-goal/);
      assert.match(peek, /Step completed: 1. First step/);
      assert.match(peek, /Attention:/);
      assert.match(peek, /issue: Verification failed: node -e "process\.exit\(3\)"/);
      assert.match(peek, /Verification passed: node can exit zero/);
      assert.match(peek, /Cursor unchanged: --peek/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "work_report peek leaves state unchanged");

      const first = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { since: "last", recent: 10, cursor: "chat" },
        })
      );
      assert.match(first, /Cursor advanced: chat/);
      assert.ok(fs.existsSync(path.join(stateDir, "reports", "chat.json")), "work_report writes cursor");

      const second = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { since: "last", cursor: "chat", peek: true },
        })
      );
      assert.match(second, /No new Mythify events to report/);

      const marked = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { cursor: "fresh-chat", mark: true },
        })
      );
      assert.match(marked, /Scope: mark cursor fresh-chat, 0 new events/);
      assert.match(marked, /Cursor is ready\. Future reports with --since last will show only new events\./);
      assert.match(marked, /Cursor marked at latest event: fresh-chat/);
      assert.doesNotMatch(marked, /No new Mythify events to report/);

      const markedSecond = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { since: "last", cursor: "fresh-chat", peek: true },
        })
      );
      assert.match(markedSecond, /No new Mythify events to report/);

      const invalidMark = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { mark: true, peek: true },
        })
      );
      assert.match(invalidMark, /^\[FAIL\] mark cannot be combined with peek/);

      const invalidMarkSince = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { mark: true, since: "last" },
        })
      );
      assert.match(invalidMarkSince, /^\[FAIL\] mark cannot be combined with since/);

      const jsonText = textOf(
        await client.callTool({
          name: "work_report",
          arguments: { since: "start", recent: 2, peek: true, format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.shown_event_count, 2);
      assert.ok(parsed.new_event_count >= 4);
      assert.equal(parsed.attention_event_count, 1);
      assert.equal(parsed.attention_events[0].level, "issue");
      assert.equal(parsed.cursor_updated, false);
    });

    await t.test("phase_status groups plan steps without mutation", async () => {
      const before = snapshotStateDir(stateDir);
      const text = textOf(
        await client.callTool({
          name: "phase_status",
          arguments: { recent: 1 },
        })
      );
      assert.ok(text.startsWith("[OK] Phase view"), `phase_status reports [OK]: ${text}`);
      assert.match(text, /Active plan: smoke-goal/);
      assert.match(text, /Build: completed; 1 plan steps/);
      assert.match(text, /Guardrail: phase view summarizes durable state only/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "phase_status leaves state unchanged");

      const jsonText = textOf(
        await client.callTool({
          name: "phase_status",
          arguments: { recent: 1, format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      const phases = Object.fromEntries(parsed.phases.map((phase) => [phase.id, phase]));
      assert.equal(phases.build.status, "completed");
      assert.equal(phases.build.step_counts.total, 1);
      assert.equal(parsed.counts.memory, 1);
    });

    await t.test("campaign_next_prompt returns host prompt without mutation", async () => {
      const campaignsDir = path.join(stateDir, "campaigns");
      fs.mkdirSync(campaignsDir, { recursive: true });
      const campaign = {
        id: "project-shot",
        goal: "One shot a useful project",
        success_criteria: "All work is verified.",
        verify_command: "python3 -m unittest discover -s tests",
        status: "active",
        current_task_id: 1,
        loop: ["understand", "design", "build", "judge", "verify", "reflect"],
        tasks: [
          {
            id: 1,
            title: "Build the first slice",
            success_criteria: "A verified slice exists.",
            status: "in_progress",
            phase: "understand",
            result: "",
            created: "2026-06-15T00:00:00Z",
            updated: "2026-06-15T00:00:00Z",
          },
        ],
        events: [],
        learnings: [
          {
            task_id: 1,
            lesson: "Keep prompt output visible in chat.",
            apply_next: true,
            created: "2026-06-15T00:00:00Z",
          },
        ],
        created: "2026-06-15T00:00:00Z",
        updated: "2026-06-15T00:00:00Z",
      };
      fs.writeFileSync(path.join(campaignsDir, "project-shot.json"), JSON.stringify(campaign, null, 2), "utf8");
      fs.writeFileSync(path.join(campaignsDir, "active"), "project-shot\n", "utf8");
      const before = snapshotStateDir(stateDir);

      const text = textOf(
        await client.callTool({
          name: "campaign_next_prompt",
          arguments: {},
        })
      );
      assert.ok(text.startsWith("[OK] Campaign prompt: project-shot"), `campaign_next_prompt reports [OK]: ${text}`);
      assert.match(text, /Current task 1: Build the first slice/);
      assert.match(text, /mythify campaign advance project-shot/);
      assert.match(text, /Guardrail: Prompt output is steering material/);

      const jsonText = textOf(
        await client.callTool({
          name: "campaign_next_prompt",
          arguments: { name: "project-shot", format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.id, "project-shot");
      assert.equal(parsed.phase, "understand");
      assert.equal(parsed.current_task.title, "Build the first slice");
      assert.equal(parsed.recent_learnings[0], "task 1: Keep prompt output visible in chat. [apply next]");
      assert.deepEqual(snapshotStateDir(stateDir), before, "campaign_next_prompt leaves state unchanged");
    });

    await t.test("prompt_packet returns workflow prompts without mutation", async () => {
      const researchDir = path.join(stateDir, "research");
      fs.mkdirSync(researchDir, { recursive: true });
      const research = {
        id: "packet-direction",
        question: "How should prompt packets guide implementation?",
        status: "closed",
        sources: [
          {
            id: "S1",
            title: "Trace notes",
            url: "",
            note: "Shows research to implementation transitions.",
            credibility: "medium",
            created: "2026-06-15T00:00:00Z",
          },
        ],
        claims: [
          {
            id: "C1",
            claim: "Prompt packets should be material for direction.",
            evidence: "Research records are not executable evidence.",
            source_id: "S1",
            confidence: "medium",
            created: "2026-06-15T00:00:00Z",
          },
        ],
        open_questions: [
          {
            id: "Q1",
            question: "Which verifier should prove the implementation?",
            created: "2026-06-15T00:00:00Z",
          },
        ],
        decision: "Implement one shared prompt packet contract.",
        created: "2026-06-15T00:00:00Z",
        updated: "2026-06-15T00:00:00Z",
      };
      fs.writeFileSync(
        path.join(researchDir, "packet-direction.json"),
        JSON.stringify(research, null, 2),
        "utf8"
      );
      fs.writeFileSync(path.join(researchDir, "active"), "packet-direction\n", "utf8");
      const before = snapshotStateDir(stateDir);

      const researchText = textOf(
        await client.callTool({
          name: "prompt_packet",
          arguments: { kind: "research", name: "packet-direction" },
        })
      );
      assert.ok(researchText.startsWith("[OK] Prompt packet research: research"), `prompt_packet reports [OK]: ${researchText}`);
      assert.match(researchText, /Decision: Implement one shared prompt packet contract\./);
      assert.match(researchText, /not verification evidence/);

      const failureJson = textOf(
        await client.callTool({
          name: "prompt_packet",
          arguments: { kind: "failure", format: "json" },
        })
      );
      const failure = JSON.parse(failureJson.replace(/^\[OK\] /, ""));
      assert.equal(failure.kind, "failure");
      assert.equal(failure.context.failed_verification.exit_code, 3);
      assert.match(failure.next_prompt, /Failure recovery prompt packet/);

      const nextJson = textOf(
        await client.callTool({
          name: "prompt_packet",
          arguments: { kind: "next", format: "json" },
        })
      );
      const next = JSON.parse(nextJson.replace(/^\[OK\] /, ""));
      assert.equal(next.kind, "next");
      assert.equal(next.selected_kind, "failure");

      const handoffText = textOf(
        await client.callTool({
          name: "prompt_packet",
          arguments: { kind: "handoff" },
        })
      );
      assert.match(handoffText, /Handoff prompt packet/);
      assert.match(handoffText, /Resume from this packet/);

      const reviewText = textOf(
        await client.callTool({
          name: "prompt_packet",
          arguments: { kind: "review" },
        })
      );
      assert.match(reviewText, /Review prompt packet/);
      assert.match(reviewText, /Review changed files/);

      const campaignText = textOf(
        await client.callTool({
          name: "prompt_packet",
          arguments: { kind: "campaign", name: "project-shot" },
        })
      );
      assert.match(campaignText, /Continue Mythify campaign: project-shot/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "prompt_packet leaves state unchanged");
    });

    await t.test("outcome tools track success and bounded failure", async () => {
      const passCommand = `${JSON.stringify(process.execPath)} -e "process.exit(0)"`;
      const metricCommand = `${JSON.stringify(process.execPath)} -e "process.stdout.write('9.5')"`;
      const startedText = textOf(
        await client.callTool({
          name: "outcome_start",
          arguments: {
            goal: "Make the smoke verifier pass",
            success: "node exits zero",
            verify_command: passCommand,
            metric_command: metricCommand,
            max_iterations: 2,
            allowed_paths: ["mcp-server/src", "mcp-server/test"],
            format: "json",
          },
        })
      );
      const started = JSON.parse(startedText.replace(/^\[OK\] /, ""));
      assert.equal(started.status, "active");
      assert.deepEqual(started.allowed_paths, ["mcp-server/src", "mcp-server/test"]);

      const checkedText = textOf(
        await client.callTool({
          name: "outcome_check",
          arguments: { format: "json" },
        })
      );
      const checked = JSON.parse(checkedText.replace(/^\[OK\] /, ""));
      assert.equal(checked.goal.status, "succeeded");
      assert.equal(checked.record.verified, true);
      assert.equal(checked.record.metric.score, 9.5);

      const statusText = textOf(
        await client.callTool({
          name: "outcome_status",
          arguments: {},
        })
      );
      assert.match(statusText, /status: succeeded/, "status reports success");
      assert.match(
        statusText,
        /allowed path hints \(advisory\): mcp-server\/src, mcp-server\/test/,
        "status labels allowed_paths as advisory hints"
      );

      const failCommand = `${JSON.stringify(process.execPath)} -e "process.stdout.write('nope'); process.exit(4)"`;
      const failStartedText = textOf(
        await client.callTool({
          name: "outcome_start",
          arguments: {
            goal: "Fail within one iteration",
            success: "node exits zero",
            verify_command: failCommand,
            max_iterations: 1,
            format: "json",
          },
        })
      );
      const failStarted = JSON.parse(failStartedText.replace(/^\[OK\] /, ""));
      const failChecked = textOf(
        await client.callTool({
          name: "outcome_check",
          arguments: {},
        })
      );
      assert.ok(failChecked.startsWith("[FAIL]"), `failing outcome reports [FAIL]: ${failChecked}`);
      assert.match(failChecked, /failed/, "failing outcome reaches failed status");
      const stopped = textOf(
        await client.callTool({
          name: "outcome_stop",
          arguments: { name: failStarted.id, reason: "test cleanup" },
        })
      );
      assert.ok(stopped.startsWith("[OK]"), `outcome_stop succeeds: ${stopped}`);
    });

    await t.test("outcome_progress shows read-only verifier progress", async () => {
      const before = snapshotStateDir(stateDir);
      const text = textOf(
        await client.callTool({
          name: "outcome_progress",
          arguments: { recent: 3 },
        })
      );
      assert.ok(text.startsWith("[OK] Outcome progress"), `outcome_progress reports [OK]: ${text}`);
      assert.match(text, /Outcomes:/);
      assert.match(text, /make-the-smoke-verifier-pass/);
      assert.match(text, /metric: exit 0, score 9.5/);
      assert.match(text, /Guardrail: progress displays recorded outcome verifier results only/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "outcome_progress leaves state unchanged");

      const jsonText = textOf(
        await client.callTool({
          name: "outcome_progress",
          arguments: { recent: 3, format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.counts.succeeded, 1);
      assert.equal(parsed.counts.stopped, 1);
      const byId = Object.fromEntries(parsed.outcomes.map((outcome) => [outcome.id, outcome]));
      assert.equal(byId["make-the-smoke-verifier-pass"].last_check.metric_score, 9.5);
      assert.equal(byId["fail-within-one-iteration"].status, "stopped");
      assert.deepEqual(snapshotStateDir(stateDir), before, "outcome_progress json leaves state unchanged");
    });

    await t.test("release_readiness shows recorded gates without mutation", async () => {
      const seeded = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: {
            command: `${JSON.stringify(process.execPath)} -e "process.exit(0)"`,
            claim: "Python suite passes for release readiness",
          },
        })
      );
      assert.ok(seeded.startsWith("[OK]"), `readiness seed verification passes: ${seeded}`);

      const before = snapshotStateDir(stateDir);
      const text = textOf(
        await client.callTool({
          name: "release_readiness",
          arguments: {},
        })
      );
      assert.ok(text.startsWith("[OK] Release readiness"), `release_readiness reports [OK]: ${text}`);
      assert.match(text, /Python test suite: passed/);
      assert.match(text, /Node MCP suite: missing/);
      assert.match(text, /Project git: \[(?:x|!|~)\] (?:clean|dirty|unknown)/);
      assert.match(text, /Guardrail: readiness summarizes recorded evidence/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "release_readiness leaves state unchanged");

      const jsonText = textOf(
        await client.callTool({
          name: "release_readiness",
          arguments: { format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.counts.passed, 1);
      assert.equal(parsed.counts.missing, 9);
      assert.ok(["clean", "dirty", "unknown"].includes(parsed.project_state.git.status));
      assert.deepEqual(snapshotStateDir(stateDir), before, "release_readiness json leaves state unchanged");
    });

    await t.test("background_status shows read-only outcome and fanout state", async () => {
      const jobId = "fo-20260613131313-abcd";
      const jobDir = path.join(stateDir, "fanout", jobId);
      fs.mkdirSync(jobDir, { recursive: true });
      fs.writeFileSync(
        path.join(jobDir, "job.json"),
        JSON.stringify(
          {
            id: jobId,
            created: "2026-06-13T13:13:13+00:00",
            last_updated: "2026-06-13T13:13:14+00:00",
            purpose: "Inspect background task state",
            engine: "command",
            model: "",
            visibility: "summary",
            tasks: [
              {
                id: 1,
                title: "Read fanout job",
                status: "completed",
                role: "worker",
                engine: "command",
                duration_seconds: 0.5,
                error: null,
              },
              {
                id: 2,
                title: "Wait for verifier",
                status: "pending",
                role: "worker",
                engine: "command",
                duration_seconds: 0,
                error: null,
              },
            ],
          },
          null,
          2
        )
      );

      const before = snapshotStateDir(stateDir);
      const text = textOf(
        await client.callTool({
          name: "background_status",
          arguments: { recent: 2 },
        })
      );
      assert.ok(text.startsWith("[OK] Background tasks"), `background_status reports [OK]: ${text}`);
      assert.match(text, /Outcomes:/);
      assert.match(text, /Fanout jobs: 1 total; 1 active/);
      assert.match(text, new RegExp(jobId));
      assert.match(text, /Read fanout job/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "background_status leaves state unchanged");

      const jsonText = textOf(
        await client.callTool({
          name: "background_status",
          arguments: { recent: 2, format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.counts.fanout_tasks.pending, 1);
      assert.equal(parsed.fanout_jobs[0].id, jobId);
      assert.ok(parsed.outcomes.length >= 1, "background_status includes outcome summaries");
    });

    await t.test("fanout_timeline shows read-only worker events", async () => {
      const jobId = "fo-20260613141414-abcd";
      const jobDir = path.join(stateDir, "fanout", jobId);
      fs.mkdirSync(jobDir, { recursive: true });
      fs.writeFileSync(
        path.join(jobDir, "job.json"),
        JSON.stringify(
          {
            id: jobId,
            created: "2026-06-13T14:14:14+00:00",
            last_updated: "2026-06-13T14:14:18+00:00",
            purpose: "Inspect fanout worker timing",
            engine: "command",
            model: "",
            visibility: "summary",
            tasks: [
              {
                id: 1,
                title: "Collect worker timing",
                status: "completed",
                role: "worker",
                engine: "command",
                started_at: "2026-06-13T14:14:15+00:00",
                finished_at: "2026-06-13T14:14:18+00:00",
                duration_seconds: 3,
                error: null,
                output_file: "task-1-output.md",
                output_bytes: 51,
              },
            ],
          },
          null,
          2
        )
      );

      const before = snapshotStateDir(stateDir);
      const text = textOf(
        await client.callTool({
          name: "fanout_timeline",
          arguments: { recent: 1 },
        })
      );
      assert.ok(text.startsWith("[OK] Fanout timeline"), `fanout_timeline reports [OK]: ${text}`);
      assert.match(text, /job created/);
      assert.match(text, /Collect worker timing/);
      assert.match(text, /duration=3s/);
      assert.match(text, /output=51 bytes/);
      assert.deepEqual(snapshotStateDir(stateDir), before, "fanout_timeline leaves state unchanged");

      const jsonText = textOf(
        await client.callTool({
          name: "fanout_timeline",
          arguments: { recent: 1, format: "json" },
        })
      );
      const parsed = JSON.parse(jsonText.replace(/^\[OK\] /, ""));
      assert.equal(parsed.events.length, 3);
      assert.equal(parsed.events[0].event, "job_created");
      assert.equal(parsed.events[2].event, "task_finished");
      assert.equal(parsed.counts.timeline_events, 3);
    });

    await t.test("memory_clear with no arguments refuses", async () => {
      const snapshotBeforeRefusal = snapshotStateDir(stateDir);
      const refusedResult = await client.callTool({ name: "memory_clear", arguments: {} });
      assert.equal(refusedResult.isError, true);
      const refused = textOf(refusedResult);
      assert.equal(refused, MEMORY_CLEAR_MCP_REFUSAL);
      assert.deepEqual(
        snapshotStateDir(stateDir),
        snapshotBeforeRefusal,
        "refused memory_clear leaves every state file unchanged"
      );

      const stillThere = textOf(
        await client.callTool({ name: "memory_recall", arguments: { query: "color" } })
      );
      assert.match(stillThere, /blue/, "refused clear left memory intact");
    });

    await t.test("on-disk formats match the shared contract field names", async () => {
      const memory = JSON.parse(
        fs.readFileSync(path.join(stateDir, "memory.json"), "utf8")
      );
      assert.deepEqual(Object.keys(memory).sort(), ["entries", "metadata"]);
      assert.ok(Array.isArray(memory.entries), "entries is an array");
      assert.equal(memory.entries.length, 1, "one memory entry persisted");
      const entry = memory.entries[0];
      assert.deepEqual(
        Object.keys(entry).sort(),
        ["category", "key", "timestamp", "value"],
        "memory entry has the exact contract fields"
      );
      assert.equal(entry.key, "color");
      assert.equal(entry.value, "blue");
      assert.equal(entry.category, "fact");
      assert.equal(typeof entry.timestamp, "string");
      assert.deepEqual(
        Object.keys(memory.metadata).sort(),
        ["created", "last_updated", "total_entries"],
        "memory metadata has the exact contract fields"
      );
      assert.equal(memory.metadata.total_entries, 1);

      const plan = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "smoke-goal.json"), "utf8")
      );
      assert.deepEqual(
        Object.keys(plan).sort(),
        ["created", "goal", "last_updated", "name", "steps"],
        "plan file has the exact contract fields"
      );
      assert.equal(plan.name, "smoke-goal");
      assert.equal(plan.goal, "Smoke goal");
      assert.ok(Array.isArray(plan.steps), "steps is an array");
      assert.equal(plan.steps.length, 1);
      const step = plan.steps[0];
      assert.deepEqual(
        Object.keys(step).sort(),
        ["id", "result", "status", "success_criteria", "title", "updated_at"],
        "updated step has the exact contract fields including updated_at"
      );
      assert.equal(step.id, 1);
      assert.equal(step.title, "First step");
      assert.equal(step.success_criteria, "exit code is zero");
      assert.equal(step.status, "completed");
      assert.equal(step.result, "command exited 0 as required");
      assert.equal(typeof step.updated_at, "string");

      const activeSlug = fs
        .readFileSync(path.join(stateDir, "plans", "active"), "utf8")
        .trim();
      assert.equal(activeSlug, "smoke-goal", "active pointer holds the plan slug");
    });
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});

test("explicit MCP names cannot escape state subdirectories", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-mcp-path-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-mcp-path-home-"));
  const outsideBase = `${path.basename(stateDir)}-outside`;
  const outsidePlan = path.resolve(stateDir, "plans", "..", "..", `${outsideBase}-plan.json`);
  const outsideOutcomeDir = path.resolve(stateDir, "outcomes", "..", "..", `${outsideBase}-outcome`);

  fs.mkdirSync(path.join(stateDir, "plans"), { recursive: true });
  fs.mkdirSync(path.join(stateDir, "outcomes"), { recursive: true });
  fs.writeFileSync(
    outsidePlan,
    JSON.stringify({
      name: `${outsideBase}-plan`,
      goal: "Outside MCP plan sentinel",
      steps: [],
    }),
    "utf8"
  );
  fs.mkdirSync(outsideOutcomeDir, { recursive: true });
  fs.writeFileSync(
    path.join(outsideOutcomeDir, "goal.json"),
    JSON.stringify({
      goal: "Outside MCP outcome sentinel",
      status: "active",
      success_criteria: "not loaded",
      verify_command: "true",
    }),
    "utf8"
  );

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
    },
  });
  const client = new Client({ name: "mythify-path-test", version: "3.6.14" });
  await client.connect(transport);

  try {
    const planText = textOf(
      await client.callTool({
        name: "plan_status",
        arguments: { plan: `../../${outsideBase}-plan` },
      })
    );
    assert.match(planText, /^\[FAIL\]/);
    assert.doesNotMatch(planText, /Outside MCP plan sentinel/);

    const outcomeText = textOf(
      await client.callTool({
        name: "outcome_status",
        arguments: { name: `../../${outsideBase}-outcome` },
      })
    );
    assert.match(outcomeText, /^\[FAIL\]/);
    assert.doesNotMatch(outcomeText, /Outside MCP outcome sentinel/);
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
    fs.rmSync(outsidePlan, { force: true });
    fs.rmSync(outsideOutcomeDir, { recursive: true, force: true });
  }
});

test("strict evidence gate on plan_update_step", async (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-gate-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-gate-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
      MYTHIFY_REQUIRE_VERIFIED_STEP: "",
    },
  });
  const client = new Client({ name: "mythify-gate-test", version: "2.5.0" });
  await client.connect(transport);

  try {
    await t.test("server reports the package version in serverInfo", () => {
      const info = client.getServerVersion();
      assert.ok(info, "server info is available after connect");
      assert.equal(info.version, PACKAGE_JSON.version, "serverInfo reports package version");
    });

    await t.test("pre-step verify_run does not satisfy completion", async () => {
      const created = textOf(
        await client.callTool({
          name: "plan_create",
          arguments: {
            goal: "Gate prestep",
            steps: [{ title: "Prestep gated step", success_criteria: "step-bound evidence" }],
          },
        })
      );
      assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);
      const passed = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(0)"', claim: "global pre-step verification" },
        })
      );
      assert.ok(passed.startsWith("[OK]"), `verify_run succeeds: ${passed}`);
      const verificationRecord = JSON.parse(
        fs.readFileSync(path.join(stateDir, "verifications.jsonl"), "utf8").trim().split(/\n/).at(-1)
      );
      assert.equal(verificationRecord.plan, null);
      assert.equal(verificationRecord.step_id, null);

      const refused = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "global verification should not count",
          },
        })
      );
      assert.ok(refused.startsWith("[FAIL]"), `pre-step evidence refuses: ${refused}`);
      assert.match(refused, /Verified evidence required/);
      const planAfterRefusal = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "gate-prestep.json"), "utf8")
      );
      assert.equal(planAfterRefusal.steps[0].status, "pending");
    });

    await t.test("legacy verification without context keys can satisfy completion", async () => {
      const created = textOf(
        await client.callTool({
          name: "plan_create",
          arguments: {
            goal: "Gate legacy",
            steps: [{ title: "Legacy gated step", success_criteria: "legacy evidence" }],
          },
        })
      );
      assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);
      const inProgress = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: { step_id: 1, status: "in_progress" },
        })
      );
      assert.ok(inProgress.startsWith("[OK]"), `in_progress succeeds: ${inProgress}`);
      const plan = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "gate-legacy.json"), "utf8")
      );
      const legacyRecord = {
        kind: "executed",
        claim: "legacy step verification",
        command: "true",
        exit_code: 0,
        duration_seconds: 0,
        stdout_tail: "",
        stderr_tail: "",
        verified: true,
        timestamp: plan.steps[0].updated_at,
      };
      fs.appendFileSync(
        path.join(stateDir, "verifications.jsonl"),
        `${JSON.stringify(legacyRecord)}\n`,
        "utf8"
      );

      const accepted = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "legacy verification record",
          },
        })
      );
      assert.ok(accepted.startsWith("[OK]"), `legacy evidence completes: ${accepted}`);
      const planAfterAccept = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "gate-legacy.json"), "utf8")
      );
      assert.equal(planAfterAccept.steps[0].status, "completed");
    });

    await t.test("completed is blocked until a passing verify_run is recorded", async () => {
      const created = textOf(
        await client.callTool({
          name: "plan_create",
          arguments: {
            goal: "Gate goal",
            steps: [{ title: "Gated step", success_criteria: "exit code is zero" }],
          },
        })
      );
      assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);

      const inProgress = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: { step_id: 1, status: "in_progress" },
        })
      );
      assert.ok(inProgress.startsWith("[OK]"), `in_progress succeeds: ${inProgress}`);

      const snapshotBeforeRefusal = snapshotStateDir(stateDir);
      const refused = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "I believe the command passed",
          },
        })
      );
      assert.ok(refused.startsWith("[FAIL]"), `refusal starts with [FAIL]: ${refused}`);
      assert.match(refused, /Verified evidence required/, "refusal explains the verified-step gate");
      assert.match(
        refused,
        /strict evidence mode is enabled by default, but no passing 'verify run' was recorded/,
        "refusal uses the exact spec text"
      );

      const planAfterRefusal = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "gate-goal.json"), "utf8")
      );
      assert.equal(
        planAfterRefusal.steps[0].status,
        "in_progress",
        "refused completion leaves the step not completed"
      );
      assert.deepEqual(
        snapshotStateDir(stateDir),
        snapshotBeforeRefusal,
        "verified-step refusal leaves every state file unchanged"
      );
    });

    await t.test("completed succeeds after a passing verify_run", async () => {
      const passed = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(0)"', claim: "node can exit zero" },
        })
      );
      assert.ok(passed.startsWith("[OK]"), `passing run reports [OK]: ${passed}`);
      assert.match(passed, /VERIFIED/, "passing run is VERIFIED");
      const verificationRecord = JSON.parse(
        fs.readFileSync(path.join(stateDir, "verifications.jsonl"), "utf8").trim().split(/\n/).at(-1)
      );
      assert.equal(verificationRecord.plan, "gate-goal");
      assert.equal(verificationRecord.step_id, 1);
      assert.equal(verificationRecord.step_title, "Gated step");
      assert.equal(verificationRecord.step_status, "in_progress");

      const accepted = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "node -e process.exit(0) passed",
          },
        })
      );
      assert.ok(accepted.startsWith("[OK]"), `completion now succeeds: ${accepted}`);

      const planAfterAccept = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "gate-goal.json"), "utf8")
      );
      assert.equal(
        planAfterAccept.steps[0].status,
        "completed",
        "completion with passing evidence marks the step completed"
      );
    });

    await t.test("cross-runtime timestamp formats satisfy completion within same second", async () => {
      const created = textOf(
        await client.callTool({
          name: "plan_create",
          arguments: {
            goal: "Gate mixed timestamps",
            steps: [{ title: "Mixed timestamp step", success_criteria: "mixed formats" }],
          },
        })
      );
      assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);
      const inProgress = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: { step_id: 1, status: "in_progress", plan: "gate-mixed-timestamps" },
        })
      );
      assert.ok(inProgress.startsWith("[OK]"), `in_progress succeeds: ${inProgress}`);
      const planPath = path.join(stateDir, "plans", "gate-mixed-timestamps.json");
      const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
      plan.steps[0].updated_at = "2026-06-15T18:26:24.862Z";
      plan.last_updated = plan.steps[0].updated_at;
      fs.writeFileSync(planPath, `${JSON.stringify(plan, null, 2)}\n`, "utf8");
      const verificationRecord = {
        kind: "executed",
        claim: "python verifier format after node step format",
        command: "true",
        exit_code: 0,
        duration_seconds: 0,
        stdout_tail: "",
        stderr_tail: "",
        verified: true,
        timestamp: "2026-06-15T18:26:24+00:00",
        plan: "gate-mixed-timestamps",
        step_id: 1,
        step_title: "Mixed timestamp step",
        step_status: "in_progress",
      };
      fs.appendFileSync(
        path.join(stateDir, "verifications.jsonl"),
        `${JSON.stringify(verificationRecord)}\n`,
        "utf8"
      );

      const accepted = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 1,
            status: "completed",
            result: "cross-runtime timestamp formats compare",
            plan: "gate-mixed-timestamps",
          },
        })
      );
      assert.ok(accepted.startsWith("[OK]"), `completion now succeeds: ${accepted}`);
      const planAfterAccept = JSON.parse(fs.readFileSync(planPath, "utf8"));
      assert.equal(planAfterAccept.steps[0].status, "completed");
    });

    await t.test("bound verification for one step cannot complete another step", async () => {
      const created = textOf(
        await client.callTool({
          name: "plan_create",
          arguments: {
            goal: "Gate mismatch",
            steps: [
              { title: "Step one", success_criteria: "first check passes" },
              { title: "Step two", success_criteria: "second check passes" },
            ],
          },
        })
      );
      assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);
      const inProgress = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: { step_id: 1, status: "in_progress" },
        })
      );
      assert.ok(inProgress.startsWith("[OK]"), `in_progress succeeds: ${inProgress}`);
      const passed = textOf(
        await client.callTool({
          name: "verify_run",
          arguments: { command: 'node -e "process.exit(0)"', claim: "step one only" },
        })
      );
      assert.ok(passed.startsWith("[OK]"), `verify_run succeeds: ${passed}`);
      const refused = textOf(
        await client.callTool({
          name: "plan_update_step",
          arguments: {
            step_id: 2,
            status: "completed",
            result: "must not borrow step one evidence",
          },
        })
      );
      assert.ok(refused.startsWith("[FAIL]"), `mismatched evidence refuses: ${refused}`);
      assert.match(refused, /Verified evidence required/);
      const planAfterRefusal = JSON.parse(
        fs.readFileSync(path.join(stateDir, "plans", "gate-mismatch.json"), "utf8")
      );
      assert.equal(planAfterRefusal.steps[1].status, "pending");
    });
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});

test("MYTHIFY_REQUIRE_VERIFIED_STEP=0 allows legacy prose completion", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-legacy-gate-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-legacy-gate-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
      MYTHIFY_REQUIRE_VERIFIED_STEP: "0",
    },
  });
  const client = new Client({ name: "mythify-legacy-gate-test", version: "3.6.3" });
  await client.connect(transport);

  try {
    const created = textOf(
      await client.callTool({
        name: "plan_create",
        arguments: {
          goal: "Legacy gate goal",
          steps: [{ title: "Legacy step", success_criteria: "prose result accepted" }],
        },
      })
    );
    assert.ok(created.startsWith("[OK]"), `plan_create reports [OK]: ${created}`);

    const accepted = textOf(
      await client.callTool({
        name: "plan_update_step",
        arguments: {
          step_id: 1,
          status: "completed",
          result: "legacy prose-only completion",
        },
      })
    );
    assert.ok(accepted.startsWith("[OK]"), `legacy completion succeeds: ${accepted}`);

    const plan = JSON.parse(
      fs.readFileSync(path.join(stateDir, "plans", "legacy-gate-goal.json"), "utf8")
    );
    assert.equal(plan.steps[0].status, "completed");
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});

test("MCP verify_run disabled refusal preserves whole state", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-disabled-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-disabled-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
      MYTHIFY_DISABLE_RUN: "1",
    },
  });
  const client = new Client({ name: "mythify-disabled-verify-test", version: "2.5.0" });
  await client.connect(transport);

  try {
    const stored = textOf(
      await client.callTool({
        name: "memory_store",
        arguments: { key: "seed", value: "kept" },
      })
    );
    assert.ok(stored.startsWith("[OK]"), `memory_store succeeds: ${stored}`);

    const snapshotBeforeRefusal = snapshotStateDir(stateDir);
    const refused = textOf(
      await client.callTool({
        name: "verify_run",
        arguments: { command: 'node -e "process.exit(0)"', claim: "disabled should not run" },
      })
    );
    assert.ok(refused.startsWith("[FAIL]"), `verify_run disabled refuses: ${refused}`);
    assert.match(refused, /MYTHIFY_DISABLE_RUN/);
    assert.deepEqual(
      snapshotStateDir(stateDir),
      snapshotBeforeRefusal,
      "disabled verify_run leaves every state file unchanged"
    );
    assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});

test("MCP verify_run records signal termination as shared verifier failure", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-signal-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-signal-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
    },
  });
  const client = new Client({ name: "mythify-signal-test", version: "3.6.20" });
  await client.connect(transport);

  try {
    const killed = textOf(
      await client.callTool({
        name: "verify_run",
        arguments: { command: "kill -9 $$", claim: "signal kill" },
      })
    );
    assert.ok(killed.startsWith("[FAIL]"), `signal kill reports [FAIL]: ${killed}`);
    assert.match(killed, /exit -1/, "signal kill records the shared no-exit code");
    const record = JSON.parse(
      fs.readFileSync(path.join(stateDir, "verifications.jsonl"), "utf8").trim().split(/\n/).at(-1)
    );
    assert.equal(record.exit_code, -1);
    assert.equal(record.verified, false);
    assert.match(record.stderr_tail, /terminated by signal SIGKILL/);
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});

test("MCP verify_run records output cap as shared verifier failure", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-output-cap-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-output-cap-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
      MYTHIFY_VERIFY_MAX_OUTPUT_BYTES: "1024",
    },
  });
  const client = new Client({ name: "mythify-output-cap-test", version: "3.6.20" });
  await client.connect(transport);

  try {
    const capped = textOf(
      await client.callTool({
        name: "verify_run",
        arguments: {
          command: 'node -e "process.stdout.write(\\"x\\".repeat(2048))"',
          claim: "too much output",
        },
      })
    );
    assert.ok(capped.startsWith("[FAIL]"), `output cap reports [FAIL]: ${capped}`);
    assert.match(capped, /exit -1/, "output cap records the shared no-exit code");
    const record = JSON.parse(
      fs.readFileSync(path.join(stateDir, "verifications.jsonl"), "utf8").trim().split(/\n/).at(-1)
    );
    assert.equal(record.exit_code, -1);
    assert.equal(record.verified, false);
    assert.match(record.stderr_tail, /output exceeded 1024 bytes/);
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});

test("MCP verify_run and outcome_check redact verifier output tails", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-redact-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-redact-home-"));
  const apiSecret = "sk-test-secret-value-1234567890";
  const bearerSecret = "bearer-secret-value-1234567890";
  const stderrSecret = "stderr-secret-value-1234567890";
  const verifySecret = "verify-secret-value-1234567890";
  const metricSecret = "metric-secret-value-1234567890";

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
      MYTHIFY_TEST_API_KEY: apiSecret,
      MYTHIFY_TEST_BEARER: bearerSecret,
      MYTHIFY_TEST_STDERR_TOKEN: stderrSecret,
      MYTHIFY_TEST_VERIFY_SECRET: verifySecret,
      MYTHIFY_TEST_METRIC_SECRET: metricSecret,
    },
  });
  const client = new Client({ name: "mythify-redact-test", version: "3.6.21" });
  await client.connect(transport);

  try {
    const failingCode = [
      "process.stdout.write('OPENAI_API_KEY=' + process.env.MYTHIFY_TEST_API_KEY + '\\n');",
      "process.stdout.write('Authorization: Bearer ' + process.env.MYTHIFY_TEST_BEARER + '\\nplain ok\\n');",
      "process.stderr.write('token: ' + process.env.MYTHIFY_TEST_STDERR_TOKEN + '\\n');",
      "process.exit(3);",
    ].join("");
    const failed = textOf(
      await client.callTool({
        name: "verify_run",
        arguments: {
          command: `${JSON.stringify(process.execPath)} -e ${JSON.stringify(failingCode)}`,
          claim: "redact verifier tails",
        },
      })
    );
    assert.ok(failed.startsWith("[FAIL]"), `redaction verify_run reports [FAIL]: ${failed}`);
    for (const secret of [apiSecret, bearerSecret, stderrSecret]) {
      assert.doesNotMatch(failed, new RegExp(secret), "tool response does not leak the secret");
    }
    assert.match(failed, /\[REDACTED\]/, "tool response includes redaction markers");
    assert.match(failed, /plain ok/, "non-secret output remains visible");
    const verifyRecord = JSON.parse(
      fs.readFileSync(path.join(stateDir, "verifications.jsonl"), "utf8").trim().split(/\n/).at(-1)
    );
    const verifyCombined = `${verifyRecord.stdout_tail}\n${verifyRecord.stderr_tail}`;
    for (const secret of [apiSecret, bearerSecret, stderrSecret]) {
      assert.doesNotMatch(verifyCombined, new RegExp(secret), "stored verify_run tail does not leak the secret");
    }
    assert.match(verifyCombined, /\[REDACTED\]/);

    const outcomeVerifyCode =
      "process.stdout.write('VERIFY_TOKEN=' + process.env.MYTHIFY_TEST_VERIFY_SECRET + '\\n');";
    const outcomeMetricCode =
      "process.stdout.write('7.5 METRIC_SECRET=' + process.env.MYTHIFY_TEST_METRIC_SECRET + '\\n');";
    const startedText = textOf(
      await client.callTool({
        name: "outcome_start",
        arguments: {
          goal: "Redact MCP outcome tails",
          success: "verifier succeeds",
          verify_command: `${JSON.stringify(process.execPath)} -e ${JSON.stringify(outcomeVerifyCode)}`,
          metric_command: `${JSON.stringify(process.execPath)} -e ${JSON.stringify(outcomeMetricCode)}`,
          format: "json",
        },
      })
    );
    const started = JSON.parse(startedText.replace(/^\[OK\] /, ""));
    const checkedText = textOf(
      await client.callTool({
        name: "outcome_check",
        arguments: { name: started.id, format: "json" },
      })
    );
    const checked = JSON.parse(checkedText.replace(/^\[OK\] /, ""));
    assert.equal(checked.record.metric.score, 7.5);
    const iteration = JSON.parse(
      fs.readFileSync(path.join(stateDir, "outcomes", started.id, "iterations.jsonl"), "utf8")
        .trim()
        .split(/\n/)
        .at(-1)
    );
    const outcomeRecord = JSON.parse(
      fs.readFileSync(path.join(stateDir, "verifications.jsonl"), "utf8").trim().split(/\n/).at(-1)
    );
    const outcomeCombined = JSON.stringify({
      iteration_verify: iteration.verify,
      iteration_metric: iteration.metric,
      verification: outcomeRecord,
    });
    for (const secret of [verifySecret, metricSecret]) {
      assert.doesNotMatch(outcomeCombined, new RegExp(secret), "stored outcome tail does not leak the secret");
    }
    assert.match(outcomeCombined, /\[REDACTED\]/);
  } finally {
    await client.close();
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});

test("MCP JSONL append waits for the shared lock directory", async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-lock-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-lock-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
    },
  });
  const client = new Client({ name: "mythify-lock-test", version: "3.6.20" });
  await client.connect(transport);

  const logPath = path.join(stateDir, "verifications.jsonl");
  const lockDir = jsonlLockDir(stateDir, logPath);
  fs.mkdirSync(path.dirname(lockDir), { recursive: true });
  fs.mkdirSync(lockDir);
  const started = Date.now();
  const releaseTimer = setTimeout(() => {
    fs.rmdirSync(lockDir);
  }, 150);

  try {
    const attested = textOf(
      await client.callTool({
        name: "verify_claim",
        arguments: { claim: "waited for lock", evidence: "shared lock released" },
      })
    );
    assert.ok(attested.startsWith("[WARN] ATTESTED"), `verify_claim succeeds: ${attested}`);
    assert.ok(Date.now() - started >= 100, "append waited for the held JSONL lock");
    const record = JSON.parse(fs.readFileSync(logPath, "utf8").trim().split(/\n/).at(-1));
    assert.equal(record.claim, "waited for lock");
  } finally {
    clearTimeout(releaseTimer);
    await client.close();
    if (fs.existsSync(lockDir)) {
      fs.rmdirSync(lockDir);
    }
    fs.rmSync(stateDir, { recursive: true, force: true });
    fs.rmSync(homeDir, { recursive: true, force: true });
  }
});
