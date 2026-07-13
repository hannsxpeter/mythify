import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_MODEL_PROVIDER,
  LOCAL_MODEL_ROLES,
  MODEL_PROVIDER_IDS,
  formatLocalModelRun,
  formatProviderProbe,
  probeOpenAICompatibleProvider,
  runLocalModelRole,
} from "../src/model-provider.js";

test("model provider module exposes stable ids and guarded failures", async () => {
  assert.equal(DEFAULT_MODEL_PROVIDER, "generic-openai-compatible");
  assert.deepEqual(LOCAL_MODEL_ROLES, ["reader", "triage"]);
  assert.ok(MODEL_PROVIDER_IDS.includes("ollama"));

  const missingProvider = await probeOpenAICompatibleProvider({
    provider: DEFAULT_MODEL_PROVIDER,
    base_url: "",
    model: "local-test",
    check: "chat",
  });
  assert.equal(missingProvider.status, "blocked");
  assert.match(missingProvider.error, /requires base_url/);
  assert.match(formatProviderProbe(missingProvider), /Provider probe blocked/);

  const refusedLocalRun = await runLocalModelRole({
    provider: "ollama",
    role: "reader",
    base_url: "https://example.com/v1",
    model: "local-test",
    prompt: "Summarize this.",
  });
  assert.equal(refusedLocalRun.status, "blocked");
  assert.match(refusedLocalRun.error, /requires a localhost/);
  assert.match(formatLocalModelRun(refusedLocalRun), /Local model run blocked/);
});

test("provider probe refuses arbitrary process environment variables as API keys", async () => {
  const previousHome = process.env.HOME;
  process.env.HOME = "private-home-value";
  try {
    const result = await probeOpenAICompatibleProvider({
      provider: DEFAULT_MODEL_PROVIDER,
      base_url: "https://example.com/v1",
      model: "remote-test",
      check: "models",
      api_key_env: "HOME",
    });
    assert.equal(result.status, "blocked");
    assert.equal(result.api_key_present, false);
    assert.match(result.error, /api_key_env must be one of/);
  } finally {
    if (previousHome === undefined) {
      delete process.env.HOME;
    } else {
      process.env.HOME = previousHome;
    }
  }
});
