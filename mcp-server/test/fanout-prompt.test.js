import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { assembleWorkerPrompt } from "../src/fanout-prompt.js";

test("worker prompt keeps steering and context ordering stable", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-prompt-"));
  fs.writeFileSync(path.join(root, "context.txt"), "context marker\n");
  try {
    const result = assembleWorkerPrompt(
      {
        prompt: "deliverable marker",
        effort: "high",
        speed: "fast",
        context_paths: ["context.txt"],
      },
      root,
      1024
    );
    assert.equal(result.error, undefined);
    assert.ok(result.prompt.indexOf("Requested effort: high") < result.prompt.indexOf("Context file"));
    assert.ok(result.prompt.indexOf("context marker") < result.prompt.indexOf("Task:\ndeliverable marker"));
    assert.match(result.prompt, /Requested speed: fast/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("worker prompt rejects context outside the project root", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-prompt-root-"));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-prompt-outside-"));
  const outsideFile = path.join(outside, "secret.txt");
  fs.writeFileSync(outsideFile, "must not be read\n");
  try {
    const result = assembleWorkerPrompt(
      { prompt: "task", context_paths: [outsideFile] },
      root,
      1024
    );
    assert.match(result.error, /outside the project root/);
    assert.equal(Object.hasOwn(result, "prompt"), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(outside, { recursive: true, force: true });
  }
});

test("worker prompt marks context truncated at the byte cap", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-prompt-cap-"));
  fs.writeFileSync(path.join(root, "large.txt"), "abcdefghij");
  try {
    const result = assembleWorkerPrompt(
      { prompt: "task", context_paths: ["large.txt"] },
      root,
      4
    );
    assert.match(result.prompt, /abcd\n\[WARN\] Context truncated/);
    assert.doesNotMatch(result.prompt, /abcdefghij/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
