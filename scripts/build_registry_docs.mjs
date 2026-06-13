#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ADAPTER_CANDIDATES } from "../mcp-server/src/capability-registry.js";

const SCRIPT_PATH = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(SCRIPT_PATH), "..");
const TARGET_PATH = path.join(REPO_ROOT, "docs", "adapter-candidates.md");
const TARGET_LABEL = path.relative(REPO_ROOT, TARGET_PATH);

function yesNo(value) {
  if (value === true) {
    return "yes";
  }
  if (value === false) {
    return "no";
  }
  return "unknown";
}

function escapeCell(value) {
  return String(value).replaceAll("|", "\\|");
}

function runPath(candidate) {
  const parts = [];
  if (candidate.metadata_only) {
    parts.push("metadata only");
  }
  if (candidate.can_run_local_roles) {
    const roles = Array.isArray(candidate.local_roles) ? candidate.local_roles.join(", ") : "configured";
    parts.push("local roles: " + roles);
  }
  if (candidate.can_run_api_worker === true) {
    parts.push("API worker");
  } else if (candidate.can_run_api_worker === false) {
    parts.push("no API worker");
  }
  if (candidate.can_run_bounded_worker) {
    parts.push("bounded worker");
  } else if (candidate.can_run_noninteractive_prompt === true) {
    parts.push("noninteractive prompt");
  }
  if (candidate.can_run_remote_job === true) {
    parts.push("remote job");
  } else if (candidate.can_run_remote_job === false) {
    parts.push("no remote job");
  }
  if (candidate.can_probe_eval) {
    parts.push("eval probe");
  }
  if (candidate.can_run_eval === true) {
    parts.push("eval run");
  } else if (candidate.can_run_eval === false) {
    parts.push("no eval run");
  }
  if (candidate.can_deploy === true) {
    parts.push("deploy");
  } else if (candidate.can_deploy === false) {
    parts.push("no deploy");
  }
  return parts.length ? parts.join("; ") : "none";
}

function evidenceStatus(candidate) {
  if (candidate.metadata_only) {
    return "metadata only, not evidence";
  }
  if (candidate.output_is_evidence === false || candidate.worker_output_is_evidence === false) {
    return "material, not evidence";
  }
  if (candidate.can_probe || candidate.non_billable_probe) {
    return "probe material, not evidence";
  }
  return "unknown";
}

export function renderAdapterCandidatesDoc(candidates = ADAPTER_CANDIDATES) {
  const rows = Object.entries(candidates)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, candidate]) => [
      name,
      candidate.kind,
      candidate.status,
      yesNo(candidate.local),
      yesNo(candidate.openai_compatible),
      yesNo(candidate.can_probe),
      runPath(candidate),
      evidenceStatus(candidate),
    ]);
  const lines = [
    "<!-- Generated from mcp-server/src/capability-registry.js by scripts/build_registry_docs.mjs. Edit the registry, then rebuild. -->",
    "",
    "# Adapter Candidates",
    "",
    "This file is generated from `mcp-server/src/capability-registry.js`. Do not edit it by hand.",
    "",
    "| Adapter | Kind | Status | Local | OpenAI Compatible | Probe | Run Path | Evidence |",
    "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
  ];
  for (const row of rows) {
    lines.push("| " + row.map(escapeCell).join(" | ") + " |");
  }
  lines.push("");
  return lines.join("\n");
}

function writeDoc() {
  fs.writeFileSync(TARGET_PATH, renderAdapterCandidatesDoc(), "utf8");
  console.log("[OK] Wrote " + TARGET_LABEL + " from mcp-server/src/capability-registry.js");
  return 0;
}

function checkDoc() {
  const expected = renderAdapterCandidatesDoc();
  const actual = fs.existsSync(TARGET_PATH) ? fs.readFileSync(TARGET_PATH, "utf8") : "";
  if (actual !== expected) {
    console.error("[FAIL] " + TARGET_LABEL + " is stale. Run: node scripts/build_registry_docs.mjs");
    return 1;
  }
  console.log("[OK] " + TARGET_LABEL + " is in sync with capability registry");
  return 0;
}

function main(args) {
  if (args.includes("--check")) {
    return checkDoc();
  }
  return writeDoc();
}

if (process.argv[1] && path.resolve(process.argv[1]) === SCRIPT_PATH) {
  process.exitCode = main(process.argv.slice(2));
}
