import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
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
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(homeDir, { recursive: true });
  return { root, stateDir, homeDir };
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
  const client = new Client({ name: "mythify-local-model-run-test", version: "2.5.0" });
  await client.connect(transport);
  try {
    await fn(client);
  } finally {
    await client.close();
  }
}

async function startLocalProviderServer() {
  const requests = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      requests.push({
        method: req.method,
        url: req.url,
        authorization: req.headers.authorization || "",
        body,
      });
      res.setHeader("content-type", "application/json");
      if (req.method === "POST" && req.url === "/v1/chat/completions") {
        res.end(JSON.stringify({ choices: [{ message: { content: "local-reader-material" } }] }));
        return;
      }
      res.statusCode = 404;
      res.end(JSON.stringify({ error: "not found" }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  return {
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    requests,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

function assertMaterialOnly(parsed) {
  assert.equal(parsed.material_not_evidence, true);
  assert.equal(parsed.evidence_status, "model_output_not_verification");
  assert.equal(parsed.local_only, true);
  assert.equal(parsed.writes_state, false);
  assert.equal(parsed.verification_recorded, false);
}

test("local_model_run runs reader role against localhost without recording verification", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-local-model-reader-");
  const provider = await startLocalProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_OPENAI_COMPAT_API_KEY: "local-secret",
      }),
      async (client) => {
        const runText = textOf(
          await client.callTool({
            name: "local_model_run",
            arguments: {
              role: "reader",
              base_url: provider.baseUrl,
              model: "local-test",
              prompt: "Summarize this fixture.",
              format: "json",
            },
          })
        );
        assert.ok(runText.startsWith("[OK]"), `local_model_run reports [OK]: ${runText}`);
        const parsed = JSON.parse(runText.replace(/^\[OK\] /, ""));
        assert.equal(parsed.status, "available");
        assert.equal(parsed.role, "reader");
        assert.equal(parsed.provider, "generic-openai-compatible");
        assert.equal(parsed.model, "local-test");
        assert.equal(parsed.output_tail, "local-reader-material");
        assert.equal(parsed.can_answer_prompt, true);
        assertMaterialOnly(parsed);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        assert.equal(provider.requests.length, 1);
        assert.equal(provider.requests[0].authorization, "Bearer local-secret");
        const chatBody = JSON.parse(provider.requests[0].body);
        assert.equal(chatBody.model, "local-test");
        assert.equal(chatBody.max_tokens, 512);
        assert.match(chatBody.messages[0].content, /read-only model/);
        assert.equal(chatBody.messages[1].content, "Summarize this fixture.");
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("local_model_run runs triage role with capped max tokens", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-local-model-triage-");
  const provider = await startLocalProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
      }),
      async (client) => {
        const runText = textOf(
          await client.callTool({
            name: "local_model_run",
            arguments: {
              role: "triage",
              base_url: provider.baseUrl,
              model: "local-test",
              prompt: "Frame this task.",
              max_tokens: 9000,
              format: "json",
            },
          })
        );
        assert.ok(runText.startsWith("[OK]"), `local_model_run reports [OK]: ${runText}`);
        const parsed = JSON.parse(runText.replace(/^\[OK\] /, ""));
        assert.equal(parsed.status, "available");
        assert.equal(parsed.role, "triage");
        assert.equal(parsed.max_tokens, 2048);
        assertMaterialOnly(parsed);

        const chatBody = JSON.parse(provider.requests[0].body);
        assert.equal(chatBody.max_tokens, 2048);
        assert.match(chatBody.messages[0].content, /local triage model/);
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("local_model_run uses the Ollama profile without auth by default", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-local-model-ollama-");
  const provider = await startLocalProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_OLLAMA_BASE_URL: provider.baseUrl,
        MYTHIFY_OLLAMA_MODEL: "local-test",
        MYTHIFY_OPENAI_COMPAT_API_KEY: "generic-secret",
      }),
      async (client) => {
        const runText = textOf(
          await client.callTool({
            name: "local_model_run",
            arguments: {
              provider: "ollama",
              role: "reader",
              prompt: "Summarize this with Ollama.",
              format: "json",
            },
          })
        );
        assert.ok(runText.startsWith("[OK]"), `local_model_run reports [OK]: ${runText}`);
        const parsed = JSON.parse(runText.replace(/^\[OK\] /, ""));
        assert.equal(parsed.status, "available");
        assert.equal(parsed.provider, "ollama");
        assert.equal(parsed.openai_compatible, true);
        assert.equal(parsed.base_url, provider.baseUrl);
        assert.equal(parsed.default_base_url, "http://localhost:11434/v1");
        assert.equal(parsed.model, "local-test");
        assert.equal(parsed.output_tail, "local-reader-material");
        assertMaterialOnly(parsed);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        assert.equal(provider.requests.length, 1);
        assert.equal(provider.requests[0].authorization, "");
        const chatBody = JSON.parse(provider.requests[0].body);
        assert.equal(chatBody.model, "local-test");
        assert.equal(chatBody.messages[1].content, "Summarize this with Ollama.");
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("local_model_run refuses non-local base URLs without writing state", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-local-model-refuse-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
      }),
      async (client) => {
        const refused = textOf(
          await client.callTool({
            name: "local_model_run",
            arguments: {
              role: "reader",
              base_url: "https://api.example.com/v1",
              model: "remote-test",
              prompt: "Summarize this.",
              format: "json",
            },
          })
        );
        assert.ok(refused.startsWith("[FAIL]"), `local_model_run refuses: ${refused}`);
        const parsed = JSON.parse(refused.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.role, "reader");
        assertMaterialOnly(parsed);
        assert.match(parsed.error, /requires a localhost/);
        assert.equal(parsed.checks.length, 0);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
