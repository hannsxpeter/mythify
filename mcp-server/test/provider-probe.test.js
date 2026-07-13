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
  const client = new Client({ name: "mythify-provider-probe-test", version: "2.5.0" });
  await client.connect(transport);
  try {
    await fn(client);
  } finally {
    await client.close();
  }
}

async function startProviderServer() {
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
      if (req.method === "GET" && req.url === "/v1/models") {
        res.end(JSON.stringify({ data: [{ id: "local-test" }, { id: "other-model" }] }));
        return;
      }
      if (req.method === "POST" && req.url === "/v1/chat/completions") {
        res.end(JSON.stringify({ choices: [{ message: { content: "mythify-provider-probe-ok" } }] }));
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

test("provider_probe probes an OpenAI-compatible server without recording verification", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-probe-");
  const provider = await startProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_OPENAI_COMPAT_API_KEY: "local-secret",
      }),
      async (client) => {
        const probedText = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              base_url: provider.baseUrl,
              model: "local-test",
              format: "json",
            },
          })
        );
        assert.ok(probedText.startsWith("[OK]"), `provider_probe reports [OK]: ${probedText}`);
        const probed = JSON.parse(probedText.replace(/^\[OK\] /, ""));
        assert.equal(probed.status, "available");
        assert.equal(probed.provider, "generic-openai-compatible");
        assert.equal(probed.model, "local-test");
        assert.equal(probed.material_not_evidence, true);
        assert.equal(probed.evidence_status, "probe_only_not_verification");
        assert.equal(probed.can_answer_prompt, true);
        assert.equal(probed.checks.length, 2);
        assert.equal(probed.checks[0].name, "models");
        assert.equal(probed.checks[0].model_present, true);
        assert.equal(probed.checks[1].name, "chat");
        assert.equal(probed.checks[1].response_tail, "mythify-provider-probe-ok");
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        assert.equal(provider.requests.length, 2);
        assert.equal(provider.requests[0].authorization, "Bearer local-secret");
        assert.equal(provider.requests[1].authorization, "Bearer local-secret");
        const chatBody = JSON.parse(provider.requests[1].body);
        assert.equal(chatBody.model, "local-test");
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe cannot transmit a non-allowlisted environment variable", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-probe-env-guard-");
  const provider = await startProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: "private-home-value",
      }),
      async (client) => {
        const refusedText = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              base_url: provider.baseUrl,
              model: "local-test",
              check: "models",
              api_key_env: "HOME",
              format: "json",
            },
          })
        );
        assert.ok(refusedText.startsWith("[FAIL]"), `provider_probe reports [FAIL]: ${refusedText}`);
        const refused = JSON.parse(refusedText.replace(/^\[FAIL\] /, ""));
        assert.equal(refused.status, "blocked");
        assert.equal(refused.api_key_present, false);
        assert.match(refused.error, /api_key_env must be one of/);
        assert.equal(provider.requests.length, 0);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe uses the Ollama local profile without auth by default", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-ollama-");
  const provider = await startProviderServer();
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
        const probedText = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "ollama",
              format: "json",
            },
          })
        );
        assert.ok(probedText.startsWith("[OK]"), `provider_probe reports [OK]: ${probedText}`);
        const probed = JSON.parse(probedText.replace(/^\[OK\] /, ""));
        assert.equal(probed.status, "available");
        assert.equal(probed.provider, "ollama");
        assert.equal(probed.openai_compatible, true);
        assert.equal(probed.local_only, true);
        assert.equal(probed.base_url, provider.baseUrl);
        assert.equal(probed.default_base_url, "http://localhost:11434/v1");
        assert.equal(probed.model, "local-test");
        assert.equal(probed.api_key_env, "");
        assert.equal(probed.api_key_present, false);
        assert.equal(probed.material_not_evidence, true);
        assert.equal(probed.evidence_status, "probe_only_not_verification");
        assert.equal(probed.checks.length, 2);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        assert.equal(provider.requests.length, 2);
        assert.equal(provider.requests[0].authorization, "");
        assert.equal(provider.requests[1].authorization, "");
        const chatBody = JSON.parse(provider.requests[1].body);
        assert.equal(chatBody.model, "local-test");
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe refuses non-local Ollama base URLs without writing state", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-ollama-refuse-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_OLLAMA_MODEL: "local-test",
      }),
      async (client) => {
        const refused = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "ollama",
              base_url: "https://example.com/v1",
              format: "json",
            },
          })
        );
        assert.ok(refused.startsWith("[FAIL]"), `provider_probe refuses: ${refused}`);
        const parsed = JSON.parse(refused.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.provider, "ollama");
        assert.equal(parsed.material_not_evidence, true);
        assert.match(parsed.error, /requires a localhost/);
        assert.equal(parsed.checks.length, 0);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe uses the LM Studio local profile without auth by default", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-lm-studio-");
  const provider = await startProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_LM_STUDIO_BASE_URL: provider.baseUrl,
        MYTHIFY_LM_STUDIO_MODEL: "local-test",
        MYTHIFY_OPENAI_COMPAT_API_KEY: "generic-secret",
      }),
      async (client) => {
        const probedText = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "lm-studio",
              format: "json",
            },
          })
        );
        assert.ok(probedText.startsWith("[OK]"), `provider_probe reports [OK]: ${probedText}`);
        const probed = JSON.parse(probedText.replace(/^\[OK\] /, ""));
        assert.equal(probed.status, "available");
        assert.equal(probed.provider, "lm-studio");
        assert.equal(probed.openai_compatible, true);
        assert.equal(probed.local_only, true);
        assert.equal(probed.base_url, provider.baseUrl);
        assert.equal(probed.default_base_url, "http://localhost:1234/v1");
        assert.equal(probed.model, "local-test");
        assert.equal(probed.api_key_env, "");
        assert.equal(probed.api_key_present, false);
        assert.equal(probed.material_not_evidence, true);
        assert.equal(probed.evidence_status, "probe_only_not_verification");
        assert.equal(probed.checks.length, 2);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        assert.equal(provider.requests.length, 2);
        assert.equal(provider.requests[0].authorization, "");
        assert.equal(provider.requests[1].authorization, "");
        const chatBody = JSON.parse(provider.requests[1].body);
        assert.equal(chatBody.model, "local-test");
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe refuses non-local LM Studio base URLs without writing state", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-lm-studio-refuse-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_LM_STUDIO_MODEL: "local-test",
      }),
      async (client) => {
        const refused = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "lm-studio",
              base_url: "https://example.com/v1",
              format: "json",
            },
          })
        );
        assert.ok(refused.startsWith("[FAIL]"), `provider_probe refuses: ${refused}`);
        const parsed = JSON.parse(refused.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.provider, "lm-studio");
        assert.equal(parsed.material_not_evidence, true);
        assert.match(parsed.error, /requires a localhost/);
        assert.equal(parsed.checks.length, 0);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe uses the llama.cpp local profile without auth by default", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-llama-cpp-");
  const provider = await startProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_LLAMA_CPP_BASE_URL: provider.baseUrl,
        MYTHIFY_LLAMA_CPP_MODEL: "local-test",
        MYTHIFY_OPENAI_COMPAT_API_KEY: "generic-secret",
      }),
      async (client) => {
        const probedText = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "llama-cpp",
              format: "json",
            },
          })
        );
        assert.ok(probedText.startsWith("[OK]"), `provider_probe reports [OK]: ${probedText}`);
        const probed = JSON.parse(probedText.replace(/^\[OK\] /, ""));
        assert.equal(probed.status, "available");
        assert.equal(probed.provider, "llama-cpp");
        assert.equal(probed.openai_compatible, true);
        assert.equal(probed.local_only, true);
        assert.equal(probed.base_url, provider.baseUrl);
        assert.equal(probed.default_base_url, "http://localhost:8080/v1");
        assert.equal(probed.model, "local-test");
        assert.equal(probed.api_key_env, "");
        assert.equal(probed.api_key_present, false);
        assert.equal(probed.material_not_evidence, true);
        assert.equal(probed.evidence_status, "probe_only_not_verification");
        assert.equal(probed.checks.length, 2);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        assert.equal(provider.requests.length, 2);
        assert.equal(provider.requests[0].authorization, "");
        assert.equal(provider.requests[1].authorization, "");
        const chatBody = JSON.parse(provider.requests[1].body);
        assert.equal(chatBody.model, "local-test");
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe refuses non-local llama.cpp base URLs without writing state", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-llama-cpp-refuse-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_LLAMA_CPP_MODEL: "local-test",
      }),
      async (client) => {
        const refused = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "llama-cpp",
              base_url: "https://example.com/v1",
              format: "json",
            },
          })
        );
        assert.ok(refused.startsWith("[FAIL]"), `provider_probe refuses: ${refused}`);
        const parsed = JSON.parse(refused.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.provider, "llama-cpp");
        assert.equal(parsed.material_not_evidence, true);
        assert.match(parsed.error, /requires a localhost/);
        assert.equal(parsed.checks.length, 0);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe uses the vLLM local profile without auth by default", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-vllm-");
  const provider = await startProviderServer();
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_VLLM_BASE_URL: provider.baseUrl,
        MYTHIFY_VLLM_MODEL: "local-test",
        MYTHIFY_OPENAI_COMPAT_API_KEY: "generic-secret",
      }),
      async (client) => {
        const probedText = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "vllm",
              format: "json",
            },
          })
        );
        assert.ok(probedText.startsWith("[OK]"), `provider_probe reports [OK]: ${probedText}`);
        const probed = JSON.parse(probedText.replace(/^\[OK\] /, ""));
        assert.equal(probed.status, "available");
        assert.equal(probed.provider, "vllm");
        assert.equal(probed.openai_compatible, true);
        assert.equal(probed.local_only, true);
        assert.equal(probed.base_url, provider.baseUrl);
        assert.equal(probed.default_base_url, "http://localhost:8000/v1");
        assert.equal(probed.model, "local-test");
        assert.equal(probed.api_key_env, "");
        assert.equal(probed.api_key_present, false);
        assert.equal(probed.material_not_evidence, true);
        assert.equal(probed.evidence_status, "probe_only_not_verification");
        assert.equal(probed.checks.length, 2);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);

        assert.equal(provider.requests.length, 2);
        assert.equal(provider.requests[0].authorization, "");
        assert.equal(provider.requests[1].authorization, "");
        const chatBody = JSON.parse(provider.requests[1].body);
        assert.equal(chatBody.model, "local-test");
      }
    );
  } finally {
    await provider.close();
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe refuses non-local vLLM base URLs without writing state", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-vllm-refuse-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
        MYTHIFY_VLLM_MODEL: "local-test",
      }),
      async (client) => {
        const refused = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: {
              provider: "vllm",
              base_url: "https://example.com/v1",
              format: "json",
            },
          })
        );
        assert.ok(refused.startsWith("[FAIL]"), `provider_probe refuses: ${refused}`);
        const parsed = JSON.parse(refused.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.provider, "vllm");
        assert.equal(parsed.material_not_evidence, true);
        assert.match(parsed.error, /requires a localhost/);
        assert.equal(parsed.checks.length, 0);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("provider_probe refuses missing base URL without writing state", async () => {
  const { root, stateDir, homeDir } = makeProject("mythify-provider-probe-refuse-");
  try {
    await withClient(
      cleanEnv({
        MYTHIFY_DIR: stateDir,
        HOME: homeDir,
      }),
      async (client) => {
        const refused = textOf(
          await client.callTool({
            name: "provider_probe",
            arguments: { model: "local-test", format: "json" },
          })
        );
        assert.ok(refused.startsWith("[FAIL]"), `provider_probe refuses: ${refused}`);
        const parsed = JSON.parse(refused.replace(/^\[FAIL\] /, ""));
        assert.equal(parsed.status, "blocked");
        assert.equal(parsed.material_not_evidence, true);
        assert.match(parsed.error, /base_url/);
        assert.equal(fs.existsSync(path.join(stateDir, "verifications.jsonl")), false);
      }
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
