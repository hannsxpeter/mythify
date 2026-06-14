#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rootPath = path.join(repoRoot, "protocol", "classification-rules.json");
const packagePath = path.join(repoRoot, "mcp-server", "protocol", "classification-rules.json");
const operationRootPath = path.join(repoRoot, "protocol", "operation-registry.json");
const operationPackagePath = path.join(repoRoot, "mcp-server", "protocol", "operation-registry.json");

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

const rootManifest = readJson(rootPath);
const packageManifest = readJson(packagePath);
const operationRootManifest = readJson(operationRootPath);
const operationPackageManifest = readJson(operationPackagePath);

if (JSON.stringify(rootManifest) !== JSON.stringify(packageManifest)) {
  console.error("[FAIL] Classification rules manifest drift:");
  console.error("  root: " + rootPath);
  console.error("  package: " + packagePath);
  process.exit(1);
}

if (JSON.stringify(operationRootManifest) !== JSON.stringify(operationPackageManifest)) {
  console.error("[FAIL] Operation registry manifest drift:");
  console.error("  root: " + operationRootPath);
  console.error("  package: " + operationPackagePath);
  process.exit(1);
}

const ids = new Set();
for (const entry of rootManifest.task_types || []) {
  if (!entry.id || ids.has(entry.id) || !Array.isArray(entry.terms) || entry.terms.length === 0) {
    console.error("[FAIL] Invalid classification rule entry");
    process.exit(1);
  }
  ids.add(entry.id);
}

if (!ids.has("review")) {
  console.error("[FAIL] Missing review classification rule");
  process.exit(1);
}

console.log("[OK] Classification rules and operation registry manifests mirror package copies");
