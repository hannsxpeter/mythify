import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  ARTIFACT_TOOL_NAMES,
  registerArtifactTools,
} from "../src/artifact-tools.js";
import { normalizeArtifactFindings } from "../src/artifact-hygiene.js";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("artifact hygiene manifest package copy matches the root contract", () => {
  const packaged = JSON.parse(
    fs.readFileSync(new URL("../protocol/artifact-hygiene.json", import.meta.url), "utf8")
  );
  const root = JSON.parse(
    fs.readFileSync(new URL("../../protocol/artifact-hygiene.json", import.meta.url), "utf8")
  );
  assert.deepEqual(packaged, root);
  assert.equal(packaged.api_key_env, "WATERMARKS_SERVER_API_KEY");
});

test("artifact tools probe and inspect without recording verification", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-artifact-tools-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const artifactPath = path.join(root, "sample.txt");
  fs.writeFileSync(artifactPath, "owned content\n", "utf8");
  const registered = [];
  const server = {
    registerTool(name, config, handler) {
      registered.push({ name, config, handler });
    },
  };
  registerArtifactTools(server, {
    guarded: (handler) => async (args) => handler(args || {}),
    resolveStateDir: () => path.join(root, ".mythify"),
  });
  assert.deepEqual(registered.map((entry) => entry.name), ARTIFACT_TOOL_NAMES);

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith("/health")) {
      return jsonResponse({ ok: true, version: "test-1" });
    }
    if (String(url).endsWith("/capabilities")) {
      return jsonResponse({
        ok: true,
        scorers: { synthid: false },
        pixel_backends: { ctrlregen: false },
      });
    }
    if (String(url).endsWith("/inspect") && options.method === "POST") {
      return jsonResponse({
        ok: true,
        kind: "text",
        suspicious: true,
        report: {
          findings: ["C2PA manifest present"],
          findings_confidence: ["confirmed"],
        },
      });
    }
    return jsonResponse({ ok: false, error: "not found" }, 404);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const probe = registered.find((entry) => entry.name === "artifact_probe");
  const probeText = await probe.handler({ format: "json" });
  assert.match(probeText, /^\[OK\] /);
  assert.equal(JSON.parse(probeText.slice(5)).verification_recorded, false);

  const inspect = registered.find((entry) => entry.name === "artifact_inspect");
  const inspectText = await inspect.handler({ artifact_path: artifactPath, format: "json" });
  assert.match(inspectText, /^\[OK\] /);
  const inspection = JSON.parse(inspectText.slice(5));
  assert.equal(inspection.status, "actionable");
  assert.equal(inspection.findings[0].classification, "deterministic");
  assert.equal(inspection.material_not_evidence, true);
  assert.equal(fs.existsSync(path.join(root, ".mythify")), false);
});

test("artifact tool registrar rejects missing dependencies", () => {
  assert.throws(
    () => registerArtifactTools({ registerTool() {} }, {}),
    /requires deps\.guarded/
  );
});

test("artifact findings allow prose frontmatter but keep provenance actionable", () => {
  const findings = normalizeArtifactFindings({
    findings: [
      "frontmatter value hit on description",
      "frontmatter value hit on generator",
      "stylometry score exceeded threshold",
    ],
    findings_confidence: ["probable", "probable", "probable"],
  });
  assert.deepEqual(
    findings.map((finding) => finding.disposition),
    ["allowed", "actionable", "advisory"]
  );
  const strict = normalizeArtifactFindings(
    { findings: ["frontmatter value hit on description"] },
    { use_default_allowlist: false }
  );
  assert.equal(strict[0].disposition, "actionable");
  const containerHits = normalizeArtifactFindings({
    suspicious_total: 1,
    layer_a_hits: [
      {
        codepoint: "U+200B",
        label: "zero width space",
        count: 1,
        kind: "strip",
        confidence: "probable",
      },
    ],
  });
  assert.equal(containerHits[0].disposition, "actionable");
});

test("artifact clean requires authorization and writes only after post-clean inspection", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-artifact-clean-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const artifactPath = path.join(root, "sample.txt");
  const outputPath = path.join(root, "cleaned.txt");
  fs.writeFileSync(artifactPath, "owned content\n", "utf8");
  const registered = [];
  registerArtifactTools(
    {
      registerTool(name, config, handler) {
        registered.push({ name, config, handler });
      },
    },
    {
      guarded: (handler) => async (args) => handler(args || {}),
      resolveStateDir: () => path.join(root, ".mythify"),
    }
  );
  const clean = registered.find((entry) => entry.name === "artifact_clean");
  let fetchCalls = 0;
  const unauthorized = await clean.handler({
    artifact_path: artifactPath,
    output_path: outputPath,
    format: "json",
  });
  assert.match(unauthorized, /^\[FAIL\] /);
  assert.equal(fs.existsSync(outputPath), false);
  assert.equal(fetchCalls, 0);

  const protectedPath = path.join(root, "protected.txt");
  const symlinkPath = path.join(root, "linked.txt");
  fs.writeFileSync(protectedPath, "protected\n", "utf8");
  fs.symlinkSync(protectedPath, symlinkPath);
  const linked = await clean.handler({
    artifact_path: artifactPath,
    output_path: symlinkPath,
    confirm_authorized: true,
    format: "json",
  });
  assert.match(linked, /^\[FAIL\] /);
  assert.match(JSON.parse(linked.slice(7)).error, /symbolic link/);
  assert.equal(fs.readFileSync(protectedPath, "utf8"), "protected\n");
  assert.equal(fetchCalls, 0);

  const originalFetch = globalThis.fetch;
  let inspectCalls = 0;
  let invalidClean = false;
  globalThis.fetch = async (url, options = {}) => {
    fetchCalls += 1;
    if (String(url).endsWith("/health")) {
      return jsonResponse({ ok: true, version: "test-1" });
    }
    if (String(url).endsWith("/capabilities")) {
      return jsonResponse({
        ok: true,
        scorers: { synthid: false },
        pixel_backends: { ctrlregen: false },
      });
    }
    if (String(url).endsWith("/inspect") && options.method === "POST") {
      inspectCalls += 1;
      const request = JSON.parse(options.body);
      const content = Buffer.from(request.file, "base64").toString("utf8");
      const allowedOnly = content.startsWith("cleaned") || content.startsWith("frontmatter");
      return jsonResponse({
        ok: true,
        kind: "text",
        suspicious: true,
        report: allowedOnly
          ? {
              findings: ["frontmatter value hit on description"],
              findings_confidence: ["probable"],
            }
          : { findings: ["C2PA manifest present"], findings_confidence: ["confirmed"] },
      });
    }
    if (String(url).endsWith("/clean") && options.method === "POST") {
      return jsonResponse({
        ok: true,
        cleaned: invalidClean
          ? "***"
          : Buffer.from("cleaned content\n", "utf8").toString("base64"),
        report: { removed: 1 },
      });
    }
    return jsonResponse({ ok: false, error: "not found" }, 404);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const cleanedText = await clean.handler({
    artifact_path: artifactPath,
    output_path: outputPath,
    confirm_authorized: true,
    format: "json",
  });
  assert.match(cleanedText, /^\[OK\] /);
  const result = JSON.parse(cleanedText.slice(5));
  assert.equal(result.status, "clean");
  assert.equal(result.written, true);
  assert.equal(result.post_clean_inspection.findings[0].disposition, "allowed");
  assert.equal(inspectCalls, 2);
  assert.equal(fs.readFileSync(outputPath, "utf8"), "cleaned content\n");
  assert.equal(fs.readFileSync(artifactPath, "utf8"), "owned content\n");

  const allowedPath = path.join(root, "allowed.md");
  const allowedOutput = path.join(root, "allowed.cleaned.md");
  fs.writeFileSync(allowedPath, "frontmatter content\n", "utf8");
  const allowedCleanCalls = fetchCalls;
  const allowedText = await clean.handler({
    artifact_path: allowedPath,
    output_path: allowedOutput,
    confirm_authorized: true,
    format: "json",
  });
  assert.match(allowedText, /^\[OK\] /);
  const allowedResult = JSON.parse(allowedText.slice(5));
  assert.equal(allowedResult.service_clean_called, false);
  assert.equal(allowedResult.clean_report.skipped, true);
  assert.equal(fs.readFileSync(allowedOutput, "utf8"), "frontmatter content\n");
  assert.equal(fetchCalls, allowedCleanCalls + 4);

  const collisionCalls = fetchCalls;
  const collision = await clean.handler({
    artifact_path: artifactPath,
    output_path: outputPath,
    confirm_authorized: true,
    format: "json",
  });
  assert.match(collision, /^\[FAIL\] /);
  assert.match(JSON.parse(collision.slice(7)).error, /overwrite/);
  assert.equal(fetchCalls, collisionCalls);

  invalidClean = true;
  const invalidOutput = path.join(root, "invalid.txt");
  const invalid = await clean.handler({
    artifact_path: artifactPath,
    output_path: invalidOutput,
    confirm_authorized: true,
    format: "json",
  });
  assert.match(invalid, /^\[FAIL\] /);
  assert.match(JSON.parse(invalid.slice(7)).error, /invalid base64/);
  assert.equal(fs.existsSync(invalidOutput), false);
});
