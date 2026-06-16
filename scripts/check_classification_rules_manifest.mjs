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

const requiredSections = [
  "thresholds",
  "risk",
  "ceremony",
  "fanout",
  "fanout_visibility",
  "execution_profile",
  "next_actions",
  "model_triage",
  "verification_hints",
];
for (const section of requiredSections) {
  if (!rootManifest[section] || typeof rootManifest[section] !== "object" || Array.isArray(rootManifest[section])) {
    console.error("[FAIL] Missing shared classification policy section: " + section);
    process.exit(1);
  }
}

const thresholds = rootManifest.thresholds;
for (const key of ["trivial_word_count", "high_ambiguity_word_count", "medium_ambiguity_word_count"]) {
  if (!Number.isInteger(thresholds[key]) || thresholds[key] <= 0) {
    console.error("[FAIL] Invalid classification threshold: " + key);
    process.exit(1);
  }
}

if (!Array.isArray(rootManifest.question_prefixes) || rootManifest.question_prefixes.length === 0) {
  console.error("[FAIL] Missing question prefixes in classification policy");
  process.exit(1);
}

if (!Array.isArray(rootManifest.vague_request_terms) || rootManifest.vague_request_terms.length === 0) {
  console.error("[FAIL] Missing vague request terms in classification policy");
  process.exit(1);
}

for (const [section, keys] of Object.entries({
  risk: ["high_terms", "high_task_types", "medium_terms", "medium_task_types"],
  ceremony: ["none_low_risk_task_types", "light_low_risk_task_types", "full_task_types"],
  fanout: ["recommended_task_types", "recommended_terms", "optional_task_types", "optional_terms"],
  model_triage: ["high_impact_terms", "recommended_task_types", "optional_task_types"],
  execution_profile: ["fast_task_types", "fast_focused_task_types", "focused_terms"],
})) {
  for (const key of keys) {
    if (!Array.isArray(rootManifest[section][key]) || rootManifest[section][key].length === 0) {
      console.error("[FAIL] Invalid classification policy list: " + section + "." + key);
      process.exit(1);
    }
  }
}

if (!rootManifest.verification_hints.feature || !rootManifest.next_actions.standard) {
  console.error("[FAIL] Classification policy is missing fallback text");
  process.exit(1);
}

console.log("[OK] Classification policy and operation registry manifests mirror package copies");
