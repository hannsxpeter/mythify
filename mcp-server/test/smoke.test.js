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

const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));

const EXPECTED_TOOLS = [
  "classify_task",
  "host_model_switch",
  "provider_probe",
  "local_model_run",
  "host_cli_probe",
  "host_cli_run",
  "execution_probe",
  "lifecycle_probe",
  "memory_store",
  "memory_recall",
  "memory_clear",
  "lesson_record",
  "lesson_recall",
  "plan_create",
  "plan_add_step",
  "plan_update_step",
  "plan_status",
  "outcome_start",
  "outcome_check",
  "outcome_status",
  "outcome_results",
  "outcome_stop",
  "verify_run",
  "verify_claim",
  "reflect",
  "fanout_start",
  "fanout_status",
  "fanout_results",
];

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
      MYTHIFY_ROLE_READER_PROVIDER: "host",
      MYTHIFY_ROLE_REVIEWER_PROVIDER: "surprise-cloud",
    },
  });
  const client = new Client({ name: "mythify-smoke-test", version: "2.4.0" });
  await client.connect(transport);

  try {
    await t.test("tools/list returns exactly the 28 tools", async () => {
      const { tools } = await client.listTools();
      const names = tools.map((tool) => tool.name).sort();
      assert.deepEqual(names, [...EXPECTED_TOOLS].sort());
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
      assert.equal(parsed.model_policy.provider_defaults.api_provider_contract.status, "metadata_supported");
      assert.equal(parsed.model_policy.provider_defaults.api_provider_contract.execution_enabled, false);
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
      assert.equal(parsed.model_policy.reader.provider, "host");
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

    await t.test("memory_clear with no arguments refuses", async () => {
      const snapshotBeforeRefusal = snapshotStateDir(stateDir);
      const refused = textOf(
        await client.callTool({ name: "memory_clear", arguments: {} })
      );
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

test("MYTHIFY_REQUIRE_VERIFIED_STEP gate on plan_update_step", async (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-gate-state-"));
  const homeDir = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-gate-home-"));

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: {
      ...process.env,
      MYTHIFY_DIR: stateDir,
      HOME: homeDir,
      MYTHIFY_REQUIRE_VERIFIED_STEP: "1",
    },
  });
  const client = new Client({ name: "mythify-gate-test", version: "2.5.0" });
  await client.connect(transport);

  try {
    await t.test("server reports version 2.5.0 in serverInfo", () => {
      const info = client.getServerVersion();
      assert.ok(info, "server info is available after connect");
      assert.equal(info.version, "2.5.0", "serverInfo reports version 2.5.0");
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
        /MYTHIFY_REQUIRE_VERIFIED_STEP=1 but no passing 'verify run' was recorded/,
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
