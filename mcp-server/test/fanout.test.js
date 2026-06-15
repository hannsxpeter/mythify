// Fanout smoke tests for the Mythify MCP server (docs/design.md, "Fanout:
// parallel delegation"). Every scenario runs offline inside temp MYTHIFY_DIR
// and temp HOME directories: the command engine uses deterministic local node
// scripts, the local subscription CLI engines use stub scripts, and the
// anthropic engine is only checked for its alias-to-ID mapping (no API calls).

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { ANTHROPIC_MODEL_ALIASES, resolveAnthropicModelId } from "../src/fanout.js";

const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));
const JOB_ID_PATTERN = /fo-\d{14}-[0-9a-f]{4}/;

// Deterministic command-engine worker: reads the whole prompt from stdin and
// echoes a marker plus a digest of what it received, followed by the prompt
// itself (so tests can assert exactly what reached the worker). A prompt
// containing PLEASE-FAIL makes it exit 2 with output on stderr; one
// containing PLEASE-DELAY delays the reply so fanout_start demonstrably
// returns before workers finish.
const ECHO_WORKER_SOURCE = [
  'const crypto = require("node:crypto");',
  'let data = "";',
  'process.stdin.setEncoding("utf8");',
  'process.stdin.on("data", (chunk) => { data += chunk; });',
  'process.stdin.on("end", () => {',
  '  if (data.includes("PLEASE-FAIL")) {',
  '    process.stderr.write("boom-stderr: deliberate worker failure for the test\\n");',
  "    process.exit(2);",
  "  }",
  '  const digest = crypto.createHash("sha256").update(data).digest("hex").slice(0, 12);',
  "  const emit = () => {",
  '    process.stdout.write("WORKER-MARKER digest:" + digest + " bytes:" + Buffer.byteLength(data, "utf8") + "\\n");',
  "    process.stdout.write(data);",
  "  };",
  '  if (data.includes("PLEASE-DELAY")) {',
  "    setTimeout(emit, 1200);",
  "  } else {",
  "    emit();",
  "  }",
  "});",
  "",
].join("\n");

// The fanout worker environment must not leak harness variables. Tests spawn
// the server with sentinel values for these and assert the stub claude binary
// never sees them, while CLAUDE_CODE_OAUTH_TOKEN does pass through.
function scrubbedEnv() {
  const env = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (key.startsWith("MYTHIFY_")) {
      continue;
    }
    if (key === "CLAUDECODE" || key.startsWith("CLAUDE_CODE_")) {
      continue;
    }
    if (key.startsWith("ANTHROPIC_")) {
      continue;
    }
    env[key] = value;
  }
  return env;
}

// Lays out <root>/proj/.mythify (the state dir) and <root>/home (a temp HOME)
// so the project root, the parent of .mythify, is a real directory that
// relative context_paths and worker cwd resolve against.
function makeProject(prefix) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  const projectRoot = path.join(root, "proj");
  const stateDir = path.join(projectRoot, ".mythify");
  const homeDir = path.join(root, "home");
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(homeDir, { recursive: true });
  return { root, projectRoot, stateDir, homeDir };
}

async function startServer(extraEnv, stateDir, homeDir) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env: { ...scrubbedEnv(), MYTHIFY_DIR: stateDir, HOME: homeDir, ...extraEnv },
  });
  const client = new Client({ name: "mythify-fanout-test", version: "2.4.0" });
  await client.connect(transport);
  return client;
}

function textOf(result) {
  assert.ok(Array.isArray(result.content), "tool result has a content array");
  const texts = result.content.filter((c) => c.type === "text").map((c) => c.text);
  assert.ok(texts.length > 0, "tool result has at least one text block");
  return texts.join("\n");
}

function jobIdOf(startText) {
  const match = startText.match(JOB_ID_PATTERN);
  assert.ok(match, `fanout_start response contains a job id: ${startText}`);
  return match[0];
}

function readJsonl(filePath) {
  return fs
    .readFileSync(filePath, "utf8")
    .trim()
    .split(/\r?\n/)
    .filter((line) => line !== "")
    .map((line) => JSON.parse(line));
}

function writeHostDefaultCodexStub(filePath, marker) {
  fs.writeFileSync(
    filePath,
    `#!/bin/sh
OUT=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    OUT="$1"
  fi
  shift
done
cat > /dev/null
printf '${marker}\\n' > "$OUT"
`,
    { mode: 0o755 }
  );
}

function writeHostDefaultCursorStub(filePath, marker) {
  fs.writeFileSync(
    filePath,
    `#!/bin/sh
printf '${marker}\\n'
`,
    { mode: 0o755 }
  );
}

async function waitForAllFinished(client, jobId, deadlineMs = 60000) {
  const deadline = Date.now() + deadlineMs;
  for (;;) {
    const text = textOf(
      await client.callTool({ name: "fanout_status", arguments: { job_id: jobId } })
    );
    if (text.includes("All tasks finished")) {
      return text;
    }
    assert.ok(
      Date.now() < deadline,
      `job ${jobId} did not finish before the deadline; last status:\n${text}`
    );
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

test("fanout with the command engine", async (t) => {
  const { root, projectRoot, stateDir, homeDir } = makeProject("mythify-fanout-cmd-");
  const workerPath = path.join(root, "echo-worker.cjs");
  fs.writeFileSync(workerPath, ECHO_WORKER_SOURCE);
  const contextMarker = "CONTEXT-MARKER-7f3a90c2";
  fs.mkdirSync(path.join(projectRoot, "notes"), { recursive: true });
  fs.writeFileSync(
    path.join(projectRoot, "notes", "context.txt"),
    `Shared background for the worker.\n${contextMarker}\n`
  );
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "command",
      MYTHIFY_FANOUT_COMMAND: `"${process.execPath}" "${workerPath}"`,
    },
    stateDir,
    homeDir
  );
  let threeTaskJobId = null;

  try {
    await t.test("a 3-task job starts non-blocking, completes, and returns outputs", async () => {
      const before = Date.now();
      const started = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            tasks: [
              { title: "Alpha", prompt: "PLEASE-DELAY alpha deliverable 111" },
              { title: "Beta", prompt: "PLEASE-DELAY beta deliverable 222" },
              { title: "Gamma", prompt: "PLEASE-DELAY gamma deliverable 333" },
            ],
          },
        })
      );
      const startElapsedMs = Date.now() - before;
      assert.ok(started.startsWith("[OK]"), `fanout_start reports [OK]: ${started}`);
      assert.match(started, /visibility summary/, "fanout_start defaults to summary visibility");
      assert.match(started, /Chat visibility: summary/, "fanout_start explains summary visibility");
      threeTaskJobId = jobIdOf(started);
      assert.ok(
        startElapsedMs < 1200,
        `fanout_start returned in ${startElapsedMs}ms, before the 1200ms workers finished`
      );

      const firstStatus = textOf(
        await client.callTool({ name: "fanout_status", arguments: { job_id: threeTaskJobId } })
      );
      assert.ok(firstStatus.startsWith("[OK]"), `status reports [OK]: ${firstStatus}`);
      assert.match(firstStatus, /engine: command/, "status reports the engine");
      assert.match(firstStatus, /visibility: summary/, "status reports summary visibility");

      const finalStatus = await waitForAllFinished(client, threeTaskJobId);
      assert.match(finalStatus, /3 completed, 0 failed/, "all three tasks completed");
      assert.ok(finalStatus.includes("[x] 1. Alpha"), "task 1 shows the completed icon");
      assert.ok(finalStatus.includes("[x] 2. Beta"), "task 2 shows the completed icon");
      assert.ok(finalStatus.includes("[x] 3. Gamma"), "task 3 shows the completed icon");

      const results = textOf(
        await client.callTool({ name: "fanout_results", arguments: { job_id: threeTaskJobId } })
      );
      assert.ok(results.startsWith("[OK]"), `results report [OK]: ${results}`);
      assert.equal(
        results.split("WORKER-MARKER").length - 1,
        3,
        "every task's worker output is returned"
      );
      assert.ok(results.includes("=== Task 1: Alpha (completed"), "task 1 header present");
      assert.ok(results.includes("alpha deliverable 111"), "task 1 prompt reached its worker");
      assert.ok(results.includes("beta deliverable 222"), "task 2 prompt reached its worker");
      assert.ok(results.includes("gamma deliverable 333"), "task 3 prompt reached its worker");
      assert.match(results, /delegated worker/, "the fixed preamble reached the workers");
      const job = JSON.parse(
        fs.readFileSync(path.join(stateDir, "fanout", threeTaskJobId, "job.json"), "utf8")
      );
      assert.equal(job.visibility, "summary");
      assert.equal(job.visibility_source, "default");
      const auditPath = path.join(stateDir, "provider-audit.jsonl");
      const audit = readJsonl(auditPath).filter((row) => row.job_id === threeTaskJobId);
      assert.equal(audit.length, 6, "each task writes start and finish audit events");
      const startedEvents = audit.filter((row) => row.event === "fanout_task_started");
      const finishedEvents = audit.filter((row) => row.event === "fanout_task_finished");
      assert.equal(startedEvents.length, 3);
      assert.equal(finishedEvents.length, 3);
      for (const row of audit) {
        assert.equal(row.surface, "fanout_worker");
        assert.equal(row.provider, "custom_command");
        assert.equal(row.provider_execution_scope, "fanout_worker_only");
        assert.equal(row.billing, "user_defined");
        assert.deepEqual(row.cost_metadata_fields, [
          "billing",
          "cost_estimate_cents",
          "cost_estimate_status",
          "cost_tracking",
          "pricing_url",
        ]);
        assert.equal(row.cost_metadata.cost_estimate_status, "not_estimated");
        assert.equal(row.hosted_provider_acknowledgements.required, false);
        assert.equal(row.hosted_provider_acknowledgements.billing_acknowledged, false);
        assert.equal(row.hosted_provider_acknowledgements.data_acknowledged, false);
        assert.equal(row.hosted_provider_acknowledgements.material_acknowledged, false);
        assert.equal(row.output_material_status, "material_not_verification");
        assert.equal(row.records_verification_evidence, false);
        assert.match(row.verification_boundary, /verify_run or outcome_check/);
        assert.equal(row.request_metadata.prompt_redacted, true);
        assert.equal(row.request_metadata.prompt_sha256.length, 64);
        assert.ok(row.request_metadata.prompt_bytes > 0);
        assert.equal(JSON.stringify(row).includes("alpha deliverable 111"), false);
        assert.equal(JSON.stringify(row).includes("WORKER-MARKER"), false);
      }
      for (const row of finishedEvents) {
        assert.equal(row.output_metadata.output_redacted, true);
        assert.ok(row.output_metadata.output_bytes > 0);
        assert.equal(typeof row.duration_seconds, "number");
      }
    });

    await t.test("fanout visibility can infer quiet or use explicit verbose", async () => {
      const quietStarted = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            purpose: "Run these workers quietly in the background and do not show worker details.",
            tasks: [
              { title: "Quiet Alpha", prompt: "alpha quiet deliverable" },
              { title: "Quiet Beta", prompt: "beta quiet deliverable" },
            ],
          },
        })
      );
      assert.match(quietStarted, /visibility quiet/, "purpose infers quiet visibility");
      assert.match(quietStarted, /Worker list suppressed/, "quiet start suppresses the worker list");
      assert.doesNotMatch(quietStarted, /Quiet Alpha/, "quiet start omits individual task titles");
      const quietJobId = jobIdOf(quietStarted);
      await waitForAllFinished(client, quietJobId);
      const quietStatus = textOf(
        await client.callTool({ name: "fanout_status", arguments: { job_id: quietJobId } })
      );
      assert.match(quietStatus, /visibility: quiet/, "quiet status reports the mode");
      assert.doesNotMatch(quietStatus, /Quiet Alpha/, "quiet status omits individual task titles");
      const quietJob = JSON.parse(
        fs.readFileSync(path.join(stateDir, "fanout", quietJobId, "job.json"), "utf8")
      );
      assert.equal(quietJob.visibility, "quiet");
      assert.equal(quietJob.visibility_source, "prompt");

      const verboseStarted = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            purpose: "Run this quietly.",
            visibility: "verbose",
            tasks: [{ title: "Verbose override", prompt: "show details for this worker" }],
          },
        })
      );
      assert.match(verboseStarted, /visibility verbose/, "explicit visibility beats purpose inference");
      const verboseJobId = jobIdOf(verboseStarted);
      await waitForAllFinished(client, verboseJobId);
      const verboseJob = JSON.parse(
        fs.readFileSync(path.join(stateDir, "fanout", verboseJobId, "job.json"), "utf8")
      );
      assert.equal(verboseJob.visibility, "verbose");
      assert.equal(verboseJob.visibility_source, "explicit");
    });

    await t.test("context_paths content demonstrably reaches the worker prompt", async () => {
      const started = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            tasks: [
              {
                title: "Context check",
                prompt: "Summarize the supplied context.",
                context_paths: ["notes/context.txt"],
              },
            ],
          },
        })
      );
      const jobId = jobIdOf(started);
      await waitForAllFinished(client, jobId);
      const results = textOf(
        await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
      );
      assert.ok(results.includes(contextMarker), "context file content reached the worker stdin");
      assert.ok(
        results.includes("Context file: notes/context.txt"),
        "the context block is labeled with the given path"
      );
      assert.ok(
        results.includes("Summarize the supplied context."),
        "the task prompt follows the context block"
      );
    });

    await t.test("per-task effort overrides job effort and reaches the worker prompt", async () => {
      const started = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            effort: "low",
            speed: "standard",
            tasks: [
              {
                title: "Effort check",
                prompt: "Show the effort marker.",
                effort: "high",
                speed: "fast",
              },
            ],
          },
        })
      );
      const jobId = jobIdOf(started);
      await waitForAllFinished(client, jobId);
      const job = JSON.parse(
        fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
      );
      assert.equal(job.effort, "low", "the job effort records the job default");
      assert.equal(job.effort_source, "job");
      assert.equal(job.speed, "standard", "the job speed records the job default");
      assert.equal(job.speed_source, "job");
      assert.equal(job.tasks[0].effort, "high", "the task effort records the override");
      assert.equal(job.tasks[0].effort_source, "task");
      assert.equal(job.tasks[0].speed, "fast", "the task speed records the override");
      assert.equal(job.tasks[0].speed_source, "task");
      const results = textOf(
        await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
      );
      assert.match(results, /Requested effort: high/, "worker prompt includes the task effort");
      assert.match(results, /effort: high/, "result header includes the task effort");
      assert.match(results, /Requested speed: fast/, "worker prompt includes the task speed");
      assert.match(results, /speed: fast/, "result header includes the task speed");
    });

    await t.test("spawn ceiling refuses stronger explicit worker models by default", async () => {
      const refused = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            session_model: "haiku",
            tasks: [
              {
                title: "Too strong",
                prompt: "This should not run.",
                model: "opus",
              },
            ],
          },
        })
      );
      assert.ok(refused.startsWith("[FAIL]"), `stronger worker refuses: ${refused}`);
      assert.match(refused, /exceeds session model/, "the refusal explains the ceiling");
      assert.match(refused, /allow_stronger/, "the refusal names the opt-in");

      const allowed = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            session_model: "haiku",
            spawn_ceiling: "allow_stronger",
            tasks: [
              {
                title: "Allowed strong",
                prompt: "This stronger worker is explicitly allowed.",
                model: "opus",
              },
            ],
          },
        })
      );
      assert.ok(allowed.startsWith("[OK]"), `allow_stronger starts the job: ${allowed}`);
      const jobId = jobIdOf(allowed);
      await waitForAllFinished(client, jobId);
      const job = JSON.parse(
        fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
      );
      assert.equal(job.session_model, "haiku");
      assert.equal(job.session_model_tier, "fast");
      assert.equal(job.spawn_ceiling, "allow_stronger");
      assert.equal(job.tasks[0].model, "opus");
      assert.equal(job.tasks[0].model_tier, "frontier");
      assert.equal(job.tasks[0].model_ceiling_status, "allowed_stronger");
    });

    await t.test("stronger reviewer opt-in is explicit and scoped", async () => {
      const refusedReviewer = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            session_model: "haiku",
            tasks: [
              {
                title: "Reviewer without opt-in",
                role: "reviewer",
                prompt: "This reviewer should not run without opt-in.",
                model: "opus",
              },
            ],
          },
        })
      );
      assert.ok(refusedReviewer.startsWith("[FAIL]"), `stronger reviewer refuses: ${refusedReviewer}`);
      assert.match(refusedReviewer, /reviewer_allow_stronger/, "the refusal names the reviewer opt-in");

      const refusedWorker = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            session_model: "haiku",
            reviewer_allow_stronger: true,
            tasks: [
              {
                title: "Worker cannot borrow reviewer opt-in",
                prompt: "This worker should not run.",
                model: "opus",
              },
            ],
          },
        })
      );
      assert.ok(refusedWorker.startsWith("[FAIL]"), `stronger worker still refuses: ${refusedWorker}`);
      assert.match(refusedWorker, /spawn_ceiling/, "worker refusal keeps the normal ceiling opt-in");

      const allowedReviewer = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            session_model: "haiku",
            reviewer_allow_stronger: true,
            tasks: [
              {
                title: "Reviewer with opt-in",
                role: "reviewer",
                prompt: "This reviewer is explicitly allowed to use a stronger model.",
                model: "opus",
              },
            ],
          },
        })
      );
      assert.ok(allowedReviewer.startsWith("[OK]"), `stronger reviewer starts: ${allowedReviewer}`);
      assert.match(allowedReviewer, /Reviewer stronger opt-in: enabled/);
      const jobId = jobIdOf(allowedReviewer);
      await waitForAllFinished(client, jobId);
      const job = JSON.parse(
        fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
      );
      assert.equal(job.spawn_ceiling, "same_or_lower");
      assert.equal(job.reviewer_allow_stronger, true);
      assert.equal(job.tasks[0].role, "reviewer");
      assert.equal(job.tasks[0].model, "opus");
      assert.equal(job.tasks[0].model_tier, "frontier");
      assert.equal(job.tasks[0].model_ceiling_status, "reviewer_stronger_opt_in");
      assert.equal(job.tasks[0].stronger_reviewer_opt_in, true);
    });

    await t.test("recorded host model is used as the session model by default", async () => {
      const hostModelFile = path.join(stateDir, "host-model.json");
      fs.writeFileSync(
        hostModelFile,
        JSON.stringify(
          {
            platform: "claude-desktop",
            target_model: "haiku",
            target_model_tier: "fast",
            status: "recorded_requires_host_action",
          },
          null,
          2
        )
      );
      try {
        const refused = textOf(
          await client.callTool({
            name: "fanout_start",
            arguments: {
              tasks: [
                {
                  title: "Recorded host ceiling",
                  prompt: "This should not run.",
                  model: "opus",
                },
              ],
            },
          })
        );
        assert.ok(refused.startsWith("[FAIL]"), `recorded host ceiling refuses: ${refused}`);
        assert.match(refused, /session model "haiku"/, "the recorded host model is used");
        assert.match(refused, /allow_stronger/, "the refusal names the opt-in");
      } finally {
        fs.rmSync(hostModelFile, { force: true });
      }
    });

    await t.test("an unreadable context path refuses at validation time", async () => {
      const refused = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            tasks: [
              {
                title: "Bad context",
                prompt: "irrelevant",
                context_paths: ["notes/does-not-exist.txt"],
              },
            ],
          },
        })
      );
      assert.ok(refused.startsWith("[FAIL]"), `unreadable context refuses: ${refused}`);
      assert.match(refused, /does-not-exist\.txt/, "the refusal names the unreadable path");
      assert.match(refused, /No job was started/, "no job is created on validation failure");
    });

    await t.test("a failing command produces a failed task with captured stderr", async () => {
      const started = textOf(
        await client.callTool({
          name: "fanout_start",
          arguments: {
            tasks: [{ title: "Doomed", prompt: "PLEASE-FAIL this task on purpose" }],
          },
        })
      );
      const jobId = jobIdOf(started);
      const finalStatus = await waitForAllFinished(client, jobId);
      assert.ok(finalStatus.includes("[!] 1. Doomed"), "the failed task shows the failed icon");
      assert.match(finalStatus, /1 failed/, "the counts report one failure");

      const results = textOf(
        await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
      );
      assert.match(results, /failed/, "results label the task failed");
      assert.match(results, /exited 2/, "results include the worker exit code");
      assert.match(results, /boom-stderr/, "results include the captured stderr");
    });

    await t.test("job.json matches the format contract field by field", async () => {
      assert.ok(threeTaskJobId, "the 3-task job ran first");
      const jobPath = path.join(stateDir, "fanout", threeTaskJobId, "job.json");
      const job = JSON.parse(fs.readFileSync(jobPath, "utf8"));
      assert.deepEqual(
        Object.keys(job).sort(),
        [
          "billing",
          "cost_estimate_cents",
          "cost_estimate_status",
          "cost_tracking",
          "created",
          "effort",
          "effort_source",
          "engine",
          "hosted_provider_billing_acknowledged",
          "hosted_provider_data_acknowledged",
          "hosted_provider_engines",
          "hosted_provider_material_acknowledged",
          "id",
          "last_updated",
          "model",
          "model_ceiling_status",
          "model_source",
          "model_tier",
          "pricing_url",
          "purpose",
          "reviewer_allow_stronger",
          "session_model",
          "session_model_source",
          "session_model_tier",
          "spawn_ceiling",
          "spawn_ceiling_source",
          "speed",
          "speed_source",
          "tasks",
          "timeout_seconds",
          "timeout_source",
          "visibility",
          "visibility_reason",
          "visibility_requested",
          "visibility_source",
        ],
        "job.json has the exact top-level contract fields"
      );
      assert.equal(job.id, threeTaskJobId);
      assert.equal(job.engine, "command");
      assert.equal(job.billing, "user_defined");
      assert.equal(job.cost_tracking, "metadata_only_no_estimate");
      assert.equal(job.cost_estimate_status, "not_estimated");
      assert.equal(job.cost_estimate_cents, null);
      assert.equal(job.pricing_url, "");
      assert.equal(typeof job.model, "string");
      assert.equal(job.model_tier, "unknown");
      assert.equal(job.model_ceiling_status, "uncheckable");
      assert.equal(job.model_source, "command_default");
      assert.equal(job.session_model, "");
      assert.equal(job.session_model_source, "unknown");
      assert.equal(job.session_model_tier, "unknown");
      assert.equal(job.spawn_ceiling, "same_or_lower");
      assert.equal(job.spawn_ceiling_source, "default");
      assert.equal(job.effort, "medium");
      assert.equal(job.effort_source, "model_default");
      assert.equal(job.speed, "auto");
      assert.equal(job.speed_source, "platform_default");
      assert.equal(job.visibility, "summary");
      assert.equal(job.visibility_source, "default");
      assert.equal(job.visibility_requested, "auto");
      assert.equal(typeof job.visibility_reason, "string");
      assert.equal(job.purpose, "");
      assert.equal(job.reviewer_allow_stronger, false);
      assert.deepEqual(job.hosted_provider_engines, []);
      assert.equal(job.hosted_provider_billing_acknowledged, false);
      assert.equal(job.hosted_provider_data_acknowledged, false);
      assert.equal(job.hosted_provider_material_acknowledged, false);
      assert.equal(job.timeout_seconds, 600, "the per-worker timeout defaults to 600");
      assert.equal(job.timeout_source, "default");
      assert.equal(typeof job.created, "string");
      assert.equal(typeof job.last_updated, "string");
      assert.ok(Array.isArray(job.tasks), "tasks is an array");
      assert.equal(job.tasks.length, 3);
      const titles = ["Alpha", "Beta", "Gamma"];
      for (const [index, task] of job.tasks.entries()) {
        assert.deepEqual(
          Object.keys(task).sort(),
          [
            "billing",
            "cost_estimate_cents",
            "cost_estimate_status",
            "cost_tracking",
            "duration_seconds",
            "effort",
            "effort_source",
            "engine",
            "error",
            "finished_at",
            "id",
            "model",
            "model_ceiling_status",
            "model_source",
            "model_tier",
            "output_bytes",
            "output_file",
            "pricing_url",
            "role",
            "speed",
            "speed_source",
            "started_at",
            "status",
            "stronger_reviewer_opt_in",
            "timeout_seconds",
            "timeout_source",
            "title",
          ],
          "each task record has the exact contract fields"
        );
        assert.equal(task.id, index + 1);
        assert.equal(task.title, titles[index]);
        assert.equal(task.role, "worker");
        assert.equal(task.status, "completed");
        assert.equal(task.engine, "command");
        assert.equal(task.billing, "user_defined");
        assert.equal(task.cost_tracking, "metadata_only_no_estimate");
        assert.equal(task.cost_estimate_status, "not_estimated");
        assert.equal(task.cost_estimate_cents, null);
        assert.equal(task.pricing_url, "");
        assert.equal(typeof task.model, "string");
        assert.equal(task.model_tier, "unknown");
        assert.equal(task.model_ceiling_status, "uncheckable");
        assert.equal(task.stronger_reviewer_opt_in, false);
        assert.equal(task.model_source, "command_default");
        assert.equal(task.effort, "medium");
        assert.equal(task.effort_source, "model_default");
        assert.equal(task.speed, "auto");
        assert.equal(task.speed_source, "platform_default");
        assert.equal(task.timeout_seconds, 600);
        assert.equal(task.timeout_source, "default");
        assert.equal(typeof task.started_at, "string");
        assert.equal(typeof task.finished_at, "string");
        assert.ok(
          typeof task.duration_seconds === "number" && task.duration_seconds > 0,
          "duration_seconds is a positive number"
        );
        assert.equal(task.error, null);
        assert.equal(task.output_file, `task-${index + 1}-output.md`);
        assert.ok(task.output_bytes > 0, "output_bytes counts the written output");
        const output = fs.readFileSync(
          path.join(stateDir, "fanout", threeTaskJobId, task.output_file),
          "utf8"
        );
        assert.match(output, /WORKER-MARKER/, "the output file holds the worker output");
        assert.equal(Buffer.byteLength(output, "utf8"), task.output_bytes);
      }
    });
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the kill switch refuses all three fanout tools", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-kill-");
  const client = await startServer(
    {
      MYTHIFY_DISABLE_FANOUT: "1",
      MYTHIFY_FANOUT_ENGINE: "command",
      MYTHIFY_FANOUT_COMMAND: "cat",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: { tasks: [{ title: "Blocked", prompt: "never runs" }] },
      })
    );
    assert.ok(started.startsWith("[FAIL]"), `fanout_start refuses: ${started}`);
    assert.match(started, /MYTHIFY_DISABLE_FANOUT/, "the refusal names the kill switch");

    const status = textOf(await client.callTool({ name: "fanout_status", arguments: {} }));
    assert.ok(status.startsWith("[FAIL]"), `fanout_status refuses: ${status}`);
    assert.match(status, /MYTHIFY_DISABLE_FANOUT/);

    const results = textOf(await client.callTool({ name: "fanout_results", arguments: {} }));
    assert.ok(results.startsWith("[FAIL]"), `fanout_results refuses: ${results}`);
    assert.match(results, /MYTHIFY_DISABLE_FANOUT/);

    assert.ok(
      !fs.existsSync(path.join(stateDir, "fanout")),
      "the refused fanout_start created no job on disk"
    );
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("the depth guard refuses nested fanout_start", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-depth-");
  const client = await startServer(
    {
      MYTHIFY_FANOUT_DEPTH: "1",
      MYTHIFY_FANOUT_ENGINE: "command",
      MYTHIFY_FANOUT_COMMAND: "cat",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: { tasks: [{ title: "Nested", prompt: "never runs" }] },
      })
    );
    assert.ok(started.startsWith("[FAIL]"), `nested fanout_start refuses: ${started}`);
    assert.match(started, /MYTHIFY_FANOUT_DEPTH/, "the refusal names the depth variable");
    assert.match(started, /depth/i, "the refusal explains the depth limit");
    assert.ok(
      !fs.existsSync(path.join(stateDir, "fanout")),
      "the refused fanout_start created no job on disk"
    );
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

// Stub claude binary: asserts its own argv, reports whether the context block
// reached stdin, and dumps selected environment variables into the claude-style
// JSON result so the test can assert the curated worker environment.
function writeClaudeStub(filePath, expectedModel, expectedEffort, contextMarker) {
  const script = `#!/bin/sh
PROMPT=$(cat)
args_ok=yes
case " $* " in *" -p "*) ;; *) args_ok=no ;; esac
case " $* " in *" --output-format json "*) ;; *) args_ok=no ;; esac
case " $* " in *" --model ${expectedModel} "*) ;; *) args_ok=no ;; esac
case " $* " in *" --effort ${expectedEffort} "*) ;; *) args_ok=no ;; esac
case " $* " in *" --max-turns "*) ;; *) args_ok=no ;; esac
ctx=no
case "$PROMPT" in *"${contextMarker}"*) ctx=yes ;; esac
printf '{"result":"STUB-CLAUDE args_ok=%s ctx=%s CLAUDECODE=%s ANTHROPIC_BASE_URL=%s CLAUDE_CODE_ENTRYPOINT=%s CLAUDE_CODE_OAUTH_TOKEN=%s USER=%s MYTHIFY_FANOUT_DEPTH=%s MYTHIFY_DISABLE_FANOUT=%s TERM=%s","is_error":false}\\n' "$args_ok" "$ctx" "\${CLAUDECODE:-__unset__}" "\${ANTHROPIC_BASE_URL:-__unset__}" "\${CLAUDE_CODE_ENTRYPOINT:-__unset__}" "\${CLAUDE_CODE_OAUTH_TOKEN:-__unset__}" "\${USER:-__unset__}" "\${MYTHIFY_FANOUT_DEPTH:-__unset__}" "\${MYTHIFY_DISABLE_FANOUT:-__unset__}" "\${TERM:-__unset__}"
`;
  fs.writeFileSync(filePath, script, { mode: 0o755 });
}

function writeCodexStub(filePath, expectedModel, contextMarker, expectedSpeed = "auto") {
  const speedChecks =
    expectedSpeed === "fast"
      ? [
          'args.includes("-c") && args.includes(\'service_tier="fast"\')',
          'args.includes("-c") && args.includes("features.fast_mode=true")',
        ]
      : expectedSpeed === "standard"
        ? ['args.includes("-c") && args.includes("features.fast_mode=false")']
        : [];
  const script = `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
let prompt = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { prompt += chunk; });
process.stdin.on("end", () => {
  const outputIndex = args.indexOf("--output-last-message");
  const outputFile = outputIndex >= 0 ? args[outputIndex + 1] : "";
  const checks = [
    args[0] === "--ask-for-approval",
    args[1] === "never",
    args[2] === "exec",
    args.includes("--cd"),
    args.includes("--sandbox") && args[args.indexOf("--sandbox") + 1] === "read-only",
    args.includes("--skip-git-repo-check"),
    args.includes("--ephemeral"),
    args.includes("--color") && args[args.indexOf("--color") + 1] === "never",
    outputFile !== "",
    args.includes("--model") && args[args.indexOf("--model") + 1] === "${expectedModel}",
    ${speedChecks.length > 0 ? speedChecks.join(",\n    ") + "," : ""}
    args[args.length - 1] === "-",
  ];
  const argsOk = checks.every(Boolean) ? "yes" : "no";
  const ctx = prompt.includes("${contextMarker}") ? "yes" : "no";
  const result = "STUB-CODEX args_ok=" + argsOk +
    " ctx=" + ctx +
    " OPENAI_API_KEY=" + (process.env.OPENAI_API_KEY || "__unset__") +
    " CODEX_HOME=" + (process.env.CODEX_HOME || "__unset__") +
    " MYTHIFY_FANOUT_DEPTH=" + (process.env.MYTHIFY_FANOUT_DEPTH || "__unset__") +
    " MYTHIFY_DISABLE_FANOUT=" + (process.env.MYTHIFY_DISABLE_FANOUT || "__unset__") +
    " TERM=" + (process.env.TERM || "__unset__") + "\\n";
  if (outputFile !== "") {
    fs.writeFileSync(outputFile, result);
  } else {
    process.stdout.write(result);
  }
});
`;
  fs.writeFileSync(filePath, script, { mode: 0o755 });
}

function writeCursorStub(filePath, expectedModel, contextMarker) {
  const script = `#!/usr/bin/env node
const fs = require("node:fs");
const args = process.argv.slice(2);
if (args[0] === "models") {
  process.stdout.write("Available models\\n\\ngpt-5.3-codex - Codex 5.3\\n${expectedModel} - Expected Model\\n");
  process.exit(0);
}
const instruction = args[args.length - 1] || "";
const match = instruction.match(/file: ([^\\n]+)/);
const promptFile = match ? match[1] : "";
const prompt = promptFile ? fs.readFileSync(promptFile, "utf8") : "";
const checks = [
  args.includes("--print"),
  args.includes("--output-format") && args[args.indexOf("--output-format") + 1] === "text",
  args.includes("--trust"),
  args.includes("--workspace"),
  args.includes("--mode") && args[args.indexOf("--mode") + 1] === "ask",
  args.includes("--model") && args[args.indexOf("--model") + 1] === "${expectedModel}",
  promptFile !== "",
];
const argsOk = checks.every(Boolean) ? "yes" : "no";
const ctx = prompt.includes("${contextMarker}") ? "yes" : "no";
process.stdout.write("STUB-CURSOR args_ok=" + argsOk +
  " ctx=" + ctx +
  " CURSOR_API_KEY=" + (process.env.CURSOR_API_KEY || "__unset__") +
  " MYTHIFY_FANOUT_DEPTH=" + (process.env.MYTHIFY_FANOUT_DEPTH || "__unset__") +
  " MYTHIFY_DISABLE_FANOUT=" + (process.env.MYTHIFY_DISABLE_FANOUT || "__unset__") +
  " TERM=" + (process.env.TERM || "__unset__") + "\\n");
`;
  fs.writeFileSync(filePath, script, { mode: 0o755 });
}

test("claude-cli engine drives a stub binary with the curated environment", async () => {
  const { root, projectRoot, stateDir, homeDir } = makeProject("mythify-fanout-stub-");
  const contextMarker = "CONTEXT-MARKER-claude-91b4";
  fs.writeFileSync(path.join(projectRoot, "ctx.txt"), `stub context body\n${contextMarker}\n`);
  const stubPath = path.join(root, "claude-stub.sh");
  writeClaudeStub(stubPath, "sonnet", "high", contextMarker);
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "claude-cli",
      MYTHIFY_FANOUT_CLAUDE_BIN: stubPath,
      // Sentinels that must NOT reach the worker.
      CLAUDECODE: "1",
      CLAUDE_CODE_ENTRYPOINT: "should-not-pass",
      ANTHROPIC_BASE_URL: "http://127.0.0.1:9/should-not-pass",
      // The one credential that MUST pass through.
      CLAUDE_CODE_OAUTH_TOKEN: "stub-oauth-token-123",
      USER: "stub-user",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: {
          model: "haiku",
          tasks: [
            {
              title: "Stub task",
              prompt: "Do the stub thing.",
              context_paths: ["ctx.txt"],
              model: "sonnet",
              effort: "high",
            },
          ],
        },
      })
    );
    assert.ok(started.startsWith("[OK]"), `fanout_start reports [OK]: ${started}`);
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);

    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("=== Task 1: Stub task (completed"), "the stub task completed");
    assert.ok(results.includes("STUB-CLAUDE"), "the parsed claude JSON result is returned");
    assert.ok(
      results.includes("args_ok=yes"),
      "argv contains -p, --output-format json, --model sonnet, --effort high, and --max-turns"
    );
    assert.ok(results.includes("ctx=yes"), "the context block reached the worker over stdin");
    assert.ok(results.includes("CLAUDECODE=__unset__"), "CLAUDECODE is not passed through");
    assert.ok(
      results.includes("ANTHROPIC_BASE_URL=__unset__"),
      "ANTHROPIC_BASE_URL is not passed through"
    );
    assert.ok(
      results.includes("CLAUDE_CODE_ENTRYPOINT=__unset__"),
      "CLAUDE_CODE_* harness variables are not passed through"
    );
    assert.ok(
      results.includes("CLAUDE_CODE_OAUTH_TOKEN=stub-oauth-token-123"),
      "CLAUDE_CODE_OAUTH_TOKEN passes through for subscription auth"
    );
    assert.ok(results.includes("USER=stub-user"), "USER passes through for desktop auth");
    assert.ok(results.includes("MYTHIFY_FANOUT_DEPTH=1"), "the depth guard is set on the worker");
    assert.ok(
      results.includes("MYTHIFY_DISABLE_FANOUT=1"),
      "the kill switch is set on the worker"
    );
    assert.ok(results.includes("TERM=dumb"), "TERM=dumb is set on the worker");

    const job = JSON.parse(
      fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
    );
    assert.equal(job.tasks[0].engine, "claude-cli");
    assert.equal(job.tasks[0].model, "sonnet", "the per-task model overrides the job model");
    assert.equal(job.tasks[0].effort, "high", "the per-task effort is recorded");
    assert.equal(job.model, "haiku", "the job-level model is recorded");
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("claude-cli auth failure reports the login remediation", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-auth-");
  const stubPath = path.join(root, "claude-stub-auth.sh");
  fs.writeFileSync(
    stubPath,
    '#!/bin/sh\ncat > /dev/null\nprintf \'{"result":"Not logged in. Run /login to authenticate.","is_error":true}\\n\'\n',
    { mode: 0o755 }
  );
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "claude-cli",
      MYTHIFY_FANOUT_CLAUDE_BIN: stubPath,
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: { tasks: [{ title: "Auth check", prompt: "Trigger the auth failure." }] },
      })
    );
    const jobId = jobIdOf(started);
    const finalStatus = await waitForAllFinished(client, jobId);
    assert.ok(finalStatus.includes("[!] 1. Auth check"), "the task failed");

    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.match(results, /Not logged in/, "the failure carries the claude output");
    assert.match(results, /claude \/login/, "the remediation names claude /login");
    assert.match(
      results,
      /CLAUDE_CODE_OAUTH_TOKEN/,
      "the remediation names CLAUDE_CODE_OAUTH_TOKEN"
    );
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("codex-cli engine drives a stub binary with local-login environment", async () => {
  const { root, projectRoot, stateDir, homeDir } = makeProject("mythify-fanout-codex-");
  const contextMarker = "CONTEXT-MARKER-codex-4d15";
  fs.writeFileSync(path.join(projectRoot, "ctx.txt"), `codex context body\n${contextMarker}\n`);
  const stubPath = path.join(root, "codex-stub.js");
  const codexHome = path.join(root, "codex-home");
  fs.mkdirSync(codexHome, { recursive: true });
  writeCodexStub(stubPath, "gpt-5", contextMarker, "fast");
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "codex-cli",
      MYTHIFY_FANOUT_CODEX_BIN: stubPath,
      CODEX_HOME: codexHome,
      OPENAI_API_KEY: "should-not-pass",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: {
          tasks: [
            {
              title: "Codex stub task",
              prompt: "Do the codex stub thing.",
              context_paths: ["ctx.txt"],
              model: "gpt-5",
              speed: "fast",
            },
          ],
        },
      })
    );
    assert.ok(started.startsWith("[OK]"), `fanout_start reports [OK]: ${started}`);
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);

    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("STUB-CODEX"), "the codex stub output is returned");
    assert.ok(results.includes("args_ok=yes"), "codex exec argv matches the contract");
    assert.ok(results.includes("ctx=yes"), "the context block reached codex over stdin");
    assert.ok(results.includes("OPENAI_API_KEY=__unset__"), "API key env does not pass through");
    assert.ok(results.includes(`CODEX_HOME=${codexHome}`), "CODEX_HOME passes through for local auth");
    assert.ok(results.includes("MYTHIFY_FANOUT_DEPTH=1"), "the depth guard is set on the worker");
    assert.ok(
      results.includes("MYTHIFY_DISABLE_FANOUT=1"),
      "the kill switch is set on the worker"
    );

    const job = JSON.parse(
      fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
    );
    assert.equal(job.tasks[0].engine, "codex-cli");
    assert.equal(job.tasks[0].model, "gpt-5");
    assert.equal(job.tasks[0].speed, "fast");
    assert.equal(job.tasks[0].speed_source, "task");
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("cursor-agent engine drives a stub binary with local-login environment", async () => {
  const { root, projectRoot, stateDir, homeDir } = makeProject("mythify-fanout-cursor-");
  const contextMarker = "CONTEXT-MARKER-cursor-b261";
  fs.writeFileSync(path.join(projectRoot, "ctx.txt"), `cursor context body\n${contextMarker}\n`);
  const stubPath = path.join(root, "cursor-agent-stub.js");
  writeCursorStub(stubPath, "gpt-5.3-codex-high-fast", contextMarker);
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "cursor-agent",
      MYTHIFY_FANOUT_CURSOR_BIN: stubPath,
      MYTHIFY_FANOUT_CURSOR_MODELS:
        "gpt-5.3-codex gpt-5.3-codex-high gpt-5.3-codex-high-fast",
      CURSOR_API_KEY: "should-not-pass",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: {
          tasks: [
            {
              title: "Cursor stub task",
              prompt: "Do the cursor stub thing.",
              context_paths: ["ctx.txt"],
              model: "gpt-5.3-codex",
              effort: "high",
              speed: "fast",
            },
          ],
        },
      })
    );
    assert.ok(started.startsWith("[OK]"), `fanout_start reports [OK]: ${started}`);
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);

    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("STUB-CURSOR"), "the cursor stub output is returned");
    assert.ok(results.includes("args_ok=yes"), "cursor-agent argv matches the contract");
    assert.ok(results.includes("ctx=yes"), "the prompt file contains the assembled prompt");
    assert.ok(results.includes("CURSOR_API_KEY=__unset__"), "API key env does not pass through");
    assert.ok(results.includes("MYTHIFY_FANOUT_DEPTH=1"), "the depth guard is set on the worker");
    assert.ok(
      results.includes("MYTHIFY_DISABLE_FANOUT=1"),
      "the kill switch is set on the worker"
    );

    const job = JSON.parse(
      fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
    );
    assert.equal(job.tasks[0].engine, "cursor-agent");
    assert.equal(job.tasks[0].model, "gpt-5.3-codex-high-fast");
    assert.equal(job.tasks[0].effort, "high");
    assert.equal(job.tasks[0].speed, "fast");
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("cursor-agent resolver prefers user-local agent before stale PATH agent", async () => {
  const { root, projectRoot, stateDir, homeDir } = makeProject("mythify-fanout-cursor-prefer-");
  const contextMarker = "CONTEXT-MARKER-cursor-prefer-6a84";
  fs.writeFileSync(path.join(projectRoot, "ctx.txt"), `cursor context body\n${contextMarker}\n`);
  const userLocalBin = path.join(homeDir, ".local", "bin");
  fs.mkdirSync(userLocalBin, { recursive: true });
  const preferredStub = path.join(userLocalBin, "cursor-agent");
  writeCursorStub(preferredStub, "gpt-5.3-codex-low-fast", contextMarker);
  const staleBin = path.join(root, "stale-bin");
  fs.mkdirSync(staleBin, { recursive: true });
  fs.writeFileSync(
    path.join(staleBin, "cursor-agent"),
    "#!/bin/sh\necho stale cursor-agent >&2\nexit 42\n",
    { mode: 0o755 }
  );
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "cursor-agent",
      PATH: `${staleBin}${path.delimiter}${process.env.PATH || ""}`,
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: {
          tasks: [
            {
              title: "Cursor preferred binary task",
              prompt: "Do the cursor preferred binary thing.",
              context_paths: ["ctx.txt"],
              model: "gpt-5.3-codex",
              effort: "low",
              speed: "fast",
            },
          ],
        },
      })
    );
    assert.ok(started.startsWith("[OK]"), `fanout_start reports [OK]: ${started}`);
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);

    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("STUB-CURSOR"), "the user-local cursor-agent stub ran");
    assert.ok(!results.includes("stale cursor-agent"), "the stale PATH cursor-agent was not used");

    const job = JSON.parse(
      fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
    );
    assert.equal(job.tasks[0].model, "gpt-5.3-codex-low-fast");
    assert.equal(job.tasks[0].effort, "low");
    assert.equal(job.tasks[0].speed, "fast");
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("auto-detection prefers local CLIs before API engines", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-auto-");
  const binDir = path.join(root, "bin");
  fs.mkdirSync(binDir, { recursive: true });
  const codexPath = path.join(binDir, "codex");
  fs.writeFileSync(
    codexPath,
    `#!/bin/sh
OUT=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output-last-message" ]; then
    shift
    OUT="$1"
  fi
  shift
done
cat > /dev/null
printf 'AUTO-CODEX\\n' > "$OUT"
`,
    { mode: 0o755 }
  );
  const client = await startServer(
    {
      PATH: binDir,
      MYTHIFY_FANOUT_CLAUDE_BIN: path.join(root, "missing-claude"),
      ANTHROPIC_API_KEY: "should-not-use-when-codex-is-local",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: { tasks: [{ title: "Auto", prompt: "Use the local engine." }] },
      })
    );
    assert.ok(started.includes("engine: codex-cli"), "auto-detection chose local codex");
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);
    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("AUTO-CODEX"), "the local codex stub produced the result");
    const job = JSON.parse(
      fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
    );
    assert.equal(job.engine, "codex-cli");
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("auto-detection prefers codex when Codex is the initiating host", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-codex-host-");
  const binDir = path.join(root, "bin");
  fs.mkdirSync(binDir, { recursive: true });
  writeHostDefaultCodexStub(path.join(binDir, "codex"), "HOST-CODEX");
  writeHostDefaultCursorStub(path.join(binDir, "cursor-agent"), "HOST-CURSOR");
  const client = await startServer(
    {
      PATH: binDir,
      MYTHIFY_HOST_PLATFORM: "codex-desktop",
      MYTHIFY_FANOUT_CLAUDE_BIN: path.join(root, "missing-claude"),
      CURSOR_SESSION_ID: "cursor-session-present",
    },
    stateDir,
    homeDir
  );
  try {
    const classified = textOf(
      await client.callTool({
        name: "classify_task",
        arguments: { task: "implement a feature", format: "json" },
      })
    );
    const policy = JSON.parse(classified.replace(/^\[OK\] /, "")).model_policy;
    assert.equal(policy.session.platform, "codex-desktop");
    assert.equal(policy.fanout_worker.engine, "codex-cli");
    assert.equal(policy.fanout_worker.engine_policy, "platform_preferred");

    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: { tasks: [{ title: "Host default", prompt: "Use the host engine." }] },
      })
    );
    assert.ok(started.includes("engine: codex-cli"), `codex host chose codex: ${started}`);
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);
    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("HOST-CODEX"), "the Codex stub produced the result");
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8"))
        .engine,
      "codex-cli"
    );
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("auto-detection prefers cursor when Cursor is the initiating host", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-cursor-host-");
  const binDir = path.join(root, "bin");
  fs.mkdirSync(binDir, { recursive: true });
  writeHostDefaultCodexStub(path.join(binDir, "codex"), "HOST-CODEX");
  writeHostDefaultCursorStub(path.join(binDir, "cursor-agent"), "HOST-CURSOR");
  const client = await startServer(
    {
      PATH: binDir,
      MYTHIFY_HOST_PLATFORM: "cursor-desktop",
      MYTHIFY_FANOUT_CLAUDE_BIN: path.join(root, "missing-claude"),
      CODEX_THREAD_ID: "codex-thread-present",
    },
    stateDir,
    homeDir
  );
  try {
    const classified = textOf(
      await client.callTool({
        name: "classify_task",
        arguments: { task: "implement a feature", format: "json" },
      })
    );
    const policy = JSON.parse(classified.replace(/^\[OK\] /, "")).model_policy;
    assert.equal(policy.session.platform, "cursor-desktop");
    assert.equal(policy.fanout_worker.engine, "cursor-agent");
    assert.equal(policy.fanout_worker.engine_policy, "platform_preferred");

    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: { tasks: [{ title: "Host default", prompt: "Use the host engine." }] },
      })
    );
    assert.ok(started.includes("engine: cursor-agent"), `cursor host chose cursor: ${started}`);
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);
    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("HOST-CURSOR"), "the Cursor stub produced the result");
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8"))
        .engine,
      "cursor-agent"
    );
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("hosted provider fanout requires explicit acknowledgements", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-hosted-guard-");
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "anthropic",
      ANTHROPIC_API_KEY: "stub-key-that-must-not-run",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: {
          model: "haiku",
          tasks: [{ title: "Hosted guard", prompt: "This should not reach a provider." }],
        },
      })
    );
    assert.ok(started.startsWith("[FAIL]"), `fanout_start refuses: ${started}`);
    assert.match(started, /hosted_provider_billing_ack=true/);
    assert.match(started, /hosted_provider_data_ack=true/);
    assert.match(started, /hosted_provider_material_ack=true/);
    assert.equal(fs.existsSync(path.join(stateDir, "fanout")), false);
    assert.equal(fs.existsSync(path.join(stateDir, "provider-audit.jsonl")), false);
  } finally {
    await client.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("openai fanout runs only after hosted provider acknowledgements and audits redacted metadata", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-fanout-openai-guard-");
  const seen = [];
  const apiServer = http.createServer((req, res) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      seen.push({ url: req.url, authorization: req.headers.authorization || "", body });
      const parsed = JSON.parse(body);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(
        JSON.stringify({
          choices: [
            {
              message: {
                content: `OPENAI-STUB model=${parsed.model}`,
              },
            },
          ],
        })
      );
    });
  });
  await new Promise((resolve) => apiServer.listen(0, "127.0.0.1", resolve));
  const address = apiServer.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const client = await startServer(
    {
      MYTHIFY_FANOUT_ENGINE: "openai",
      MYTHIFY_FANOUT_BASE_URL: baseUrl,
      MYTHIFY_FANOUT_API_KEY: "stub-secret-token",
    },
    stateDir,
    homeDir
  );
  try {
    const started = textOf(
      await client.callTool({
        name: "fanout_start",
        arguments: {
          model: "stub-model",
          hosted_provider_billing_ack: true,
          hosted_provider_data_ack: true,
          hosted_provider_material_ack: true,
          tasks: [{ title: "OpenAI stub", prompt: "Return the hosted stub marker." }],
        },
      })
    );
    assert.ok(started.startsWith("[OK]"), `fanout_start reports [OK]: ${started}`);
    assert.match(started, /Hosted provider guard: acknowledged for openai/);
    const jobId = jobIdOf(started);
    await waitForAllFinished(client, jobId);

    const results = textOf(
      await client.callTool({ name: "fanout_results", arguments: { job_id: jobId } })
    );
    assert.ok(results.includes("OPENAI-STUB model=stub-model"), "the OpenAI-compatible stub ran");
    assert.equal(seen.length, 1);
    assert.equal(seen[0].url, "/chat/completions");
    assert.equal(seen[0].authorization, "Bearer stub-secret-token");

    const job = JSON.parse(
      fs.readFileSync(path.join(stateDir, "fanout", jobId, "job.json"), "utf8")
    );
    assert.deepEqual(job.hosted_provider_engines, ["openai"]);
    assert.equal(job.hosted_provider_billing_acknowledged, true);
    assert.equal(job.hosted_provider_data_acknowledged, true);
    assert.equal(job.hosted_provider_material_acknowledged, true);

    const audit = readJsonl(path.join(stateDir, "provider-audit.jsonl")).filter(
      (row) => row.job_id === jobId
    );
    assert.equal(audit.length, 2);
    for (const row of audit) {
      assert.equal(row.provider, "api_provider");
      assert.equal(row.engine, "openai");
      assert.equal(row.billing, "metered_external_account");
      assert.equal(row.hosted_provider_acknowledgements.required, true);
      assert.equal(row.hosted_provider_acknowledgements.billing_acknowledged, true);
      assert.equal(row.hosted_provider_acknowledgements.data_acknowledged, true);
      assert.equal(row.hosted_provider_acknowledgements.material_acknowledged, true);
      assert.equal(row.output_material_status, "material_not_verification");
      assert.equal(JSON.stringify(row).includes("stub-secret-token"), false);
      assert.equal(JSON.stringify(row).includes("Return the hosted stub marker"), false);
      assert.equal(JSON.stringify(row).includes("OPENAI-STUB"), false);
    }
  } finally {
    await client.close();
    await new Promise((resolve) => apiServer.close(resolve));
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("anthropic engine model alias mapping matches the spec table", () => {
  assert.deepEqual(ANTHROPIC_MODEL_ALIASES, {
    haiku: "claude-haiku-4-5",
    sonnet: "claude-sonnet-4-6",
    opus: "claude-opus-4-8",
    fable: "claude-fable-5",
  });
  assert.equal(resolveAnthropicModelId("haiku"), "claude-haiku-4-5");
  assert.equal(resolveAnthropicModelId("sonnet"), "claude-sonnet-4-6");
  assert.equal(resolveAnthropicModelId("opus"), "claude-opus-4-8");
  assert.equal(resolveAnthropicModelId("fable"), "claude-fable-5");
  assert.equal(
    resolveAnthropicModelId("claude-haiku-4-5"),
    "claude-haiku-4-5",
    "full model IDs pass through unchanged"
  );
  assert.equal(
    resolveAnthropicModelId("claude-custom-model-1"),
    "claude-custom-model-1",
    "unknown model IDs pass through unchanged"
  );
});
