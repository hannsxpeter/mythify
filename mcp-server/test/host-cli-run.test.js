import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const SERVER_PATH = fileURLToPath(new URL("../src/index.js", import.meta.url));

function cleanEnv(extra = {}) {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (key.startsWith("MYTHIFY_")) {
      delete env[key];
    }
  }
  return { ...env, ...extra };
}

function makeProject(prefix) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  const stateDir = path.join(root, ".mythify");
  const homeDir = path.join(root, "home");
  const binDir = path.join(root, "bin");
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(homeDir, { recursive: true });
  fs.mkdirSync(binDir, { recursive: true });
  return { root, stateDir, homeDir, binDir };
}

function writeStub(filePath, source) {
  fs.writeFileSync(filePath, source, "utf8");
  fs.chmodSync(filePath, 0o755);
}

function textOf(result) {
  assert.ok(Array.isArray(result.content), "tool result has a content array");
  const texts = result.content.filter((c) => c.type === "text").map((c) => c.text);
  assert.ok(texts.length > 0, "tool result has at least one text block");
  return texts.join("\n");
}

function assertMaterialOnly(parsed) {
  assert.equal(parsed.material_not_evidence, true);
  assert.equal(parsed.evidence_status, "worker_output_not_verification");
  assert.equal(parsed.writes_state, false);
  assert.equal(parsed.verification_recorded, false);
  assert.equal(parsed.worker_output_is_evidence, false);
}

async function withClient(env, fn) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env,
  });
  const client = new Client({ name: "mythify-host-cli-run-test", version: "2.5.0" });
  await client.connect(transport);
  try {
    await fn(client);
  } finally {
    await client.close();
  }
}

test("host_cli_run executes Kimi Code print mode as material only", async () => {
  const { root, stateDir, homeDir, binDir } = makeProject("mythify-host-run-kimi-");
  const logPath = path.join(root, "kimi-run.json");
  const kimiBin = path.join(binDir, "kimi");
  writeStub(
    kimiBin,
    [
      "#!/usr/bin/env node",
      'const fs = require("node:fs");',
      'const args = process.argv.slice(2);',
      `fs.writeFileSync(${JSON.stringify(logPath)}, JSON.stringify({args, cwd: process.cwd()}));`,
      'if (JSON.stringify(args) !== JSON.stringify(["--print", "-p", "Summarize repo", "--final-message-only"])) {',
      '  console.error("unexpected args: " + args.join(" "));',
      "  process.exit(3);",
      "}",
      'console.log("kimi worker material");',
      "",
    ].join("\n")
  );
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
      }),
      async (client) => {
        const runText = textOf(
          await client.callTool({
            name: "host_cli_run",
            arguments: {
              host: "kimi-code",
              bin: kimiBin,
              prompt: "Summarize repo",
              cwd: root,
              model: "kimi-k2",
              agent: "reviewer",
              format: "json",
            },
          })
        );
        assert.ok(runText.startsWith("[OK]"), `host_cli_run reports [OK]: ${runText}`);
        const parsed = JSON.parse(runText.replace(/^\[OK\] /, ""));
        assert.equal(parsed.host, "kimi-code");
        assert.equal(parsed.status, "available");
        assert.equal(parsed.can_run_noninteractive_prompt, true);
        assert.deepEqual(parsed.args, ["--print", "-p", "Summarize repo", "--final-message-only"]);
        assert.equal(parsed.output_tail, "kimi worker material\n");
        assert.equal(parsed.model, "kimi-k2");
        assert.equal(parsed.agent, "reviewer");
        assert.equal(parsed.model_applied, false);
        assert.equal(parsed.agent_applied, false);
        assertMaterialOnly(parsed);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        const logged = JSON.parse(fs.readFileSync(logPath, "utf8"));
        assert.deepEqual(logged.args, ["--print", "-p", "Summarize repo", "--final-message-only"]);
        assert.equal(fs.realpathSync(logged.cwd), fs.realpathSync(root));
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("host_cli_run executes OpenCode run with model and agent as material only", async () => {
  const { root, stateDir, homeDir, binDir } = makeProject("mythify-host-run-opencode-");
  const logPath = path.join(root, "opencode-run.json");
  const opencodeBin = path.join(binDir, "opencode");
  writeStub(
    opencodeBin,
    [
      "#!/usr/bin/env node",
      'const fs = require("node:fs");',
      'const args = process.argv.slice(2);',
      `fs.writeFileSync(${JSON.stringify(logPath)}, JSON.stringify({args, cwd: process.cwd()}));`,
      'const expected = ["run", "--format", "json", "--model", "anthropic/sonnet", "--agent", "reviewer", "Review repo"];',
      "if (JSON.stringify(args) !== JSON.stringify(expected)) {",
      '  console.error("unexpected args: " + args.join(" "));',
      "  process.exit(3);",
      "}",
      'console.log(JSON.stringify({message: "opencode worker material"}));',
      "",
    ].join("\n")
  );
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
      }),
      async (client) => {
        const runText = textOf(
          await client.callTool({
            name: "host_cli_run",
            arguments: {
              host: "opencode",
              bin: opencodeBin,
              prompt: "Review repo",
              cwd: root,
              model: "anthropic/sonnet",
              agent: "reviewer",
              format: "json",
            },
          })
        );
        assert.ok(runText.startsWith("[OK]"), `host_cli_run reports [OK]: ${runText}`);
        const parsed = JSON.parse(runText.replace(/^\[OK\] /, ""));
        assert.equal(parsed.host, "opencode");
        assert.equal(parsed.status, "available");
        assert.equal(parsed.can_run_noninteractive_prompt, true);
        assert.deepEqual(parsed.args, [
          "run",
          "--format",
          "json",
          "--model",
          "anthropic/sonnet",
          "--agent",
          "reviewer",
          "Review repo",
        ]);
        assert.equal(parsed.model_applied, true);
        assert.equal(parsed.agent_applied, true);
        assert.match(parsed.output_tail, /opencode worker material/);
        assertMaterialOnly(parsed);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        const logged = JSON.parse(fs.readFileSync(logPath, "utf8"));
        assert.equal(fs.realpathSync(logged.cwd), fs.realpathSync(root));
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("host_cli_run reports timeouts without recording verification", async () => {
  const { root, stateDir, homeDir, binDir } = makeProject("mythify-host-run-timeout-");
  const opencodeBin = path.join(binDir, "opencode");
  writeStub(
    opencodeBin,
    [
      "#!/usr/bin/env node",
      "setTimeout(() => {}, 1000);",
      "",
    ].join("\n")
  );
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
      }),
      async (client) => {
        const runText = textOf(
          await client.callTool({
            name: "host_cli_run",
            arguments: {
              host: "opencode",
              bin: opencodeBin,
              prompt: "Slow task",
              cwd: root,
              timeout_seconds: 0.01,
              format: "json",
            },
          })
        );
        assert.ok(runText.startsWith("[FAIL]"), `host_cli_run refuses slow run: ${runText}`);
        const parsed = JSON.parse(runText.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.host, "opencode");
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.timed_out, true);
        assert.match(parsed.error, /timed out/);
        assertMaterialOnly(parsed);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("host_cli_run reports missing binary without recording verification", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-host-run-missing-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        PATH: "/usr/bin:/bin",
      }),
      async (client) => {
        const runText = textOf(
          await client.callTool({
            name: "host_cli_run",
            arguments: {
              host: "kimi-code",
              bin: path.join(root, "missing-kimi"),
              prompt: "Summarize repo",
              cwd: root,
              format: "json",
            },
          })
        );
        assert.ok(runText.startsWith("[FAIL]"), `host_cli_run refuses: ${runText}`);
        const parsed = JSON.parse(runText.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.host, "kimi-code");
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.binary_source, "explicit");
        assert.match(parsed.error, /not executable/);
        assert.deepEqual(parsed.args, []);
        assertMaterialOnly(parsed);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
