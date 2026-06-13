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

async function withClient(env, fn) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_PATH],
    env,
  });
  const client = new Client({ name: "mythify-lifecycle-probe-test", version: "2.5.0" });
  await client.connect(transport);
  try {
    await fn(client);
  } finally {
    await client.close();
  }
}

function assertProbeOnlyLifecycle(parsed) {
  assert.equal(parsed.material_not_evidence, true);
  assert.equal(parsed.evidence_status, "lifecycle_probe_output_not_verification");
  assert.equal(parsed.writes_state, false);
  assert.equal(parsed.verification_recorded, false);
  assert.equal(parsed.eval_execution_enabled, false);
  assert.equal(parsed.deployment_enabled, false);
  assert.equal(parsed.scaffold_enabled, false);
  assert.equal(parsed.run_enabled, false);
  assert.equal(parsed.cloud_mutation_enabled, false);
  assert.equal(parsed.project_mutation_enabled, false);
  assert.equal(parsed.billing_guard, "probe_only_no_project_or_cloud_mutation");
  assert.deepEqual(parsed.allowed_probe_actions, [
    "probe_version",
    "probe_help",
    "probe_eval_help",
  ]);
  assert.deepEqual(parsed.allowed_probe_commands, ["--version", "--help", "eval --help"]);
  assert.ok(parsed.disabled_lifecycle_actions.includes("eval_execution"));
  assert.ok(parsed.disabled_lifecycle_actions.includes("deployment"));
  assert.ok(parsed.disabled_lifecycle_actions.includes("cloud_mutation"));
  assert.ok(parsed.future_guarded_actions.includes("eval_execution"));
  assert.ok(parsed.future_guarded_actions.includes("deployment"));
  assert.equal(parsed.lifecycle_lane_contract.version, 1);
  assert.equal(parsed.lifecycle_lane_contract.lane, "agent_lifecycle");
  assert.equal(parsed.lifecycle_lane_contract.current_policy, "probe_only");
  assert.equal(parsed.lifecycle_lane_contract.material_not_evidence, true);
  assert.equal(parsed.lifecycle_lane_contract.writes_state, false);
  assert.equal(parsed.lifecycle_lane_contract.verification_recorded, false);
  assert.ok(
    parsed.lifecycle_lane_contract.required_before_eval_execution.includes(
      "eval_dataset_or_eval_set"
    )
  );
  assert.ok(parsed.lifecycle_lane_contract.required_before_deployment.includes("billing_ack"));
}

test("lifecycle_probe detects Google Agents CLI without running lifecycle actions", async () => {
  const { root, stateDir, homeDir, binDir } = makeProject("mythify-life-agents-");
  const logPath = path.join(root, "agents-cli-args.jsonl");
  const agentsBin = path.join(binDir, "agents-cli");
  writeStub(
    agentsBin,
    [
      "#!/usr/bin/env node",
      'const fs = require("node:fs");',
      `fs.appendFileSync(${JSON.stringify(logPath)}, JSON.stringify(process.argv.slice(2)) + "\\n");`,
      'const args = process.argv.slice(2);',
      'if (args.length === 1 && args[0] === "--version") { console.log("agents-cli 0.3.0"); process.exit(0); }',
      'if (args.length === 1 && args[0] === "--help") { console.log("usage: agents-cli [setup|scaffold|eval|deploy]"); process.exit(0); }',
      'if (args.length === 2 && args[0] === "eval" && args[1] === "--help") { console.log("usage: agents-cli eval [generate|grade]"); process.exit(0); }',
      'console.error("unexpected args: " + args.join(" "));',
      "process.exit(3);",
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
        const probedText = textOf(
          await client.callTool({
            name: "lifecycle_probe",
            arguments: { adapter: "google-agents-cli", bin: agentsBin, format: "json" },
          })
        );
        assert.ok(probedText.startsWith("[OK]"), `lifecycle_probe reports [OK]: ${probedText}`);
        const probed = JSON.parse(probedText.replace(/^\[OK\] /, ""));
        assert.equal(probed.adapter, "google-agents-cli");
        assert.equal(probed.adapter_kind, "agent_lifecycle");
        assert.equal(probed.status, "available");
        assert.equal(probed.can_probe_eval, true);
        assertProbeOnlyLifecycle(probed);
        assert.ok(
          probed.lifecycle_lane_contract.adapter_specific_disabled_actions.includes("setup")
        );
        assert.ok(
          probed.lifecycle_lane_contract.adapter_specific_disabled_actions.includes("scaffold")
        );
        assert.ok(probed.disabled_lifecycle_actions.includes("setup"));
        assert.ok(probed.disabled_lifecycle_actions.includes("scaffold"));
        assert.match(probed.feature_evidence, /no scaffold/);
        assert.deepEqual(probed.checks.map((item) => item.args), [["--version"], ["--help"], ["eval", "--help"]]);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        const logged = fs.readFileSync(logPath, "utf8").trim().split(/\n/).map((line) => JSON.parse(line));
        assert.deepEqual(logged, [["--version"], ["--help"], ["eval", "--help"]]);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("lifecycle_probe detects ADK CLI eval help without running evals", async () => {
  const { root, stateDir, homeDir, binDir } = makeProject("mythify-life-adk-");
  const logPath = path.join(root, "adk-args.jsonl");
  const adkBin = path.join(binDir, "adk");
  writeStub(
    adkBin,
    [
      "#!/usr/bin/env node",
      'const fs = require("node:fs");',
      `fs.appendFileSync(${JSON.stringify(logPath)}, JSON.stringify(process.argv.slice(2)) + "\\n");`,
      'const args = process.argv.slice(2);',
      'if (args.length === 1 && args[0] === "--version") { console.log("adk 2.0.0"); process.exit(0); }',
      'if (args.length === 1 && args[0] === "--help") { console.log("usage: adk [create|run|eval|deploy]"); process.exit(0); }',
      'if (args.length === 2 && args[0] === "eval" && args[1] === "--help") { console.log("usage: adk eval [OPTIONS] AGENT_MODULE_FILE_PATH"); process.exit(0); }',
      'console.error("unexpected args: " + args.join(" "));',
      "process.exit(3);",
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
        const probedText = textOf(
          await client.callTool({
            name: "lifecycle_probe",
            arguments: { adapter: "google-adk-cli", bin: adkBin, format: "json" },
          })
        );
        assert.ok(probedText.startsWith("[OK]"), `lifecycle_probe reports [OK]: ${probedText}`);
        const probed = JSON.parse(probedText.replace(/^\[OK\] /, ""));
        assert.equal(probed.adapter, "google-adk-cli");
        assert.equal(probed.adapter_kind, "agent_lifecycle");
        assert.equal(probed.status, "available");
        assert.equal(probed.can_probe_eval, true);
        assertProbeOnlyLifecycle(probed);
        assert.ok(
          probed.lifecycle_lane_contract.adapter_specific_disabled_actions.includes("create")
        );
        assert.ok(
          probed.lifecycle_lane_contract.adapter_specific_disabled_actions.includes("web")
        );
        assert.ok(probed.disabled_lifecycle_actions.includes("create"));
        assert.ok(probed.disabled_lifecycle_actions.includes("web"));
        assert.match(probed.feature_evidence, /no create/);
        assert.deepEqual(probed.checks.map((item) => item.args), [["--version"], ["--help"], ["eval", "--help"]]);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        const logged = fs.readFileSync(logPath, "utf8").trim().split(/\n/).map((line) => JSON.parse(line));
        assert.deepEqual(logged, [["--version"], ["--help"], ["eval", "--help"]]);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("lifecycle_probe reports missing binaries without writing verification evidence", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-life-missing-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        PATH: "/usr/bin:/bin",
      }),
      async (client) => {
        const missing = textOf(
          await client.callTool({
            name: "lifecycle_probe",
            arguments: {
              adapter: "google-agents-cli",
              bin: path.join(root, "missing-agents-cli"),
              format: "json",
            },
          })
        );
        assert.ok(missing.startsWith("[FAIL]"), `lifecycle_probe refuses: ${missing}`);
        const parsed = JSON.parse(missing.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.adapter, "google-agents-cli");
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.binary_source, "explicit");
        assertProbeOnlyLifecycle(parsed);
        assert.equal(parsed.checks.length, 0);
        assert.match(parsed.error, /not executable/);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
