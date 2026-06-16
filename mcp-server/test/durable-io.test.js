import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const INDEX_SOURCE = fs.readFileSync(new URL("../src/index.js", import.meta.url), "utf8");

function functionSlice(name, nextName) {
  const start = INDEX_SOURCE.indexOf(`function ${name}`);
  const end = INDEX_SOURCE.indexOf(`function ${nextName}`, start + 1);
  assert.notEqual(start, -1, `${name} exists`);
  assert.notEqual(end, -1, `${nextName} exists after ${name}`);
  return INDEX_SOURCE.slice(start, end);
}

test("MCP atomic text writes fsync before rename and fsync parent directory after", () => {
  const body = functionSlice("writeTextAtomic", "writeJsonAtomic");
  const openIndex = body.indexOf('fs.openSync(tmp, "w")');
  const writeIndex = body.indexOf('fs.writeFileSync(fd, text, "utf8")');
  const fileFsyncIndex = body.indexOf("fs.fsyncSync(fd)");
  const renameIndex = body.indexOf("fs.renameSync(tmp, filePath)");
  const dirFsyncIndex = body.indexOf("fsyncDirectoryBestEffort(path.dirname(filePath))");

  assert.ok(openIndex >= 0, "temp file is opened explicitly");
  assert.ok(writeIndex > openIndex, "temp file is written after open");
  assert.ok(fileFsyncIndex > writeIndex, "temp file is fsynced after write");
  assert.ok(renameIndex > fileFsyncIndex, "rename happens after file fsync");
  assert.ok(dirFsyncIndex > renameIndex, "parent directory fsync happens after rename");
});

test("MCP directory fsync helper is best effort", () => {
  const body = functionSlice("fsyncDirectoryBestEffort", "writeTextAtomic");
  assert.match(body, /fs\.openSync\(dirPath, "r"\)/);
  assert.match(body, /fs\.fsyncSync\(fd\)/);
  assert.match(body, /catch \{/);
});

test("MCP bounded JSONL reader is used by report and strict step gates", () => {
  assert.match(INDEX_SOURCE, /const JSONL_TAIL_CHUNK_BYTES = 64 \* 1024;/);
  assert.match(INDEX_SOURCE, /function readJsonlSince\(filePath, lowerBound\)/);
  assert.match(INDEX_SOURCE, /function buildReportEvents\(logLowerBound = ""\)/);
  assert.match(INDEX_SOURCE, /readJsonlSince\(verificationsPath\(\), logLowerBound\)/);
  assert.match(INDEX_SOURCE, /readJsonlSince\(reflectionsPath\(\), logLowerBound\)/);
  assert.match(INDEX_SOURCE, /readJsonlSince\(verificationsPath\(\), lowerBound\)/);
});
