// Fanout smoke tests for the Mythify MCP server (docs/design.md, "Fanout:
// parallel delegation"). Every scenario runs offline inside temp MYTHIFY_DIR
// and temp HOME directories: the command engine uses deterministic local node
// scripts, the claude-cli engine uses stub shell scripts, and the anthropic
// engine is only checked for its alias-to-ID mapping (no API calls).

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
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
  const client = new Client({ name: "mythify-fanout-test", version: "2.1.0" });
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
        ["created", "engine", "id", "last_updated", "model", "tasks", "timeout_seconds"],
        "job.json has the exact top-level contract fields"
      );
      assert.equal(job.id, threeTaskJobId);
      assert.equal(job.engine, "command");
      assert.equal(typeof job.model, "string");
      assert.equal(job.timeout_seconds, 600, "the per-worker timeout defaults to 600");
      assert.equal(typeof job.created, "string");
      assert.equal(typeof job.last_updated, "string");
      assert.ok(Array.isArray(job.tasks), "tasks is an array");
      assert.equal(job.tasks.length, 3);
      const titles = ["Alpha", "Beta", "Gamma"];
      for (const [index, task] of job.tasks.entries()) {
        assert.deepEqual(
          Object.keys(task).sort(),
          [
            "duration_seconds",
            "engine",
            "error",
            "finished_at",
            "id",
            "model",
            "output_bytes",
            "output_file",
            "started_at",
            "status",
            "title",
          ],
          "each task record has the exact contract fields"
        );
        assert.equal(task.id, index + 1);
        assert.equal(task.title, titles[index]);
        assert.equal(task.status, "completed");
        assert.equal(task.engine, "command");
        assert.equal(typeof task.model, "string");
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
function writeClaudeStub(filePath, expectedModel, contextMarker) {
  const script = `#!/bin/sh
PROMPT=$(cat)
args_ok=yes
case " $* " in *" -p "*) ;; *) args_ok=no ;; esac
case " $* " in *" --output-format json "*) ;; *) args_ok=no ;; esac
case " $* " in *" --model ${expectedModel} "*) ;; *) args_ok=no ;; esac
case " $* " in *" --max-turns "*) ;; *) args_ok=no ;; esac
ctx=no
case "$PROMPT" in *"${contextMarker}"*) ctx=yes ;; esac
printf '{"result":"STUB-CLAUDE args_ok=%s ctx=%s CLAUDECODE=%s ANTHROPIC_BASE_URL=%s CLAUDE_CODE_ENTRYPOINT=%s CLAUDE_CODE_OAUTH_TOKEN=%s MYTHIFY_FANOUT_DEPTH=%s MYTHIFY_DISABLE_FANOUT=%s TERM=%s","is_error":false}\\n' "$args_ok" "$ctx" "\${CLAUDECODE:-__unset__}" "\${ANTHROPIC_BASE_URL:-__unset__}" "\${CLAUDE_CODE_ENTRYPOINT:-__unset__}" "\${CLAUDE_CODE_OAUTH_TOKEN:-__unset__}" "\${MYTHIFY_FANOUT_DEPTH:-__unset__}" "\${MYTHIFY_DISABLE_FANOUT:-__unset__}" "\${TERM:-__unset__}"
`;
  fs.writeFileSync(filePath, script, { mode: 0o755 });
}

test("claude-cli engine drives a stub binary with the curated environment", async () => {
  const { root, projectRoot, stateDir, homeDir } = makeProject("mythify-fanout-stub-");
  const contextMarker = "CONTEXT-MARKER-claude-91b4";
  fs.writeFileSync(path.join(projectRoot, "ctx.txt"), `stub context body\n${contextMarker}\n`);
  const stubPath = path.join(root, "claude-stub.sh");
  writeClaudeStub(stubPath, "sonnet", contextMarker);
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
      "argv contains -p, --output-format json, --model sonnet (per-task beats per-job), and --max-turns"
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
