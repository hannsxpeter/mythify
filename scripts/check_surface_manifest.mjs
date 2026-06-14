#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const SCRIPT_PATH = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(SCRIPT_PATH), "..");
const MANIFEST_PATH = path.join(REPO_ROOT, "protocol", "surface-manifest.json");
const PACKAGE_MANIFEST_PATH = path.join(
  REPO_ROOT,
  "mcp-server",
  "protocol",
  "surface-manifest.json"
);

function readText(relativePath) {
  return fs.readFileSync(path.join(REPO_ROOT, relativePath), "utf8");
}

function fail(message) {
  console.error("[FAIL] " + message);
  process.exitCode = 1;
}

function requireUnique(label, values) {
  const seen = new Set();
  for (const value of values) {
    if (seen.has(value)) {
      fail(label + " contains duplicate value: " + value);
    }
    seen.add(value);
  }
}

function requireEqual(label, actual, expected) {
  if (actual !== expected) {
    fail(label + " expected " + expected + " but found " + actual);
  }
}

function requireArrayEqual(label, actual, expected) {
  const left = [...actual].sort();
  const right = [...expected].sort();
  if (JSON.stringify(left) !== JSON.stringify(right)) {
    fail(label + " drifted.\nExpected: " + right.join(", ") + "\nActual: " + left.join(", "));
  }
}

function requireIncludes(relativePath, needle) {
  const text = readText(relativePath);
  if (!text.includes(needle)) {
    fail(relativePath + " missing required text: " + needle);
  }
}

function registeredTools(relativePath) {
  const text = readText(relativePath);
  const names = [];
  const pattern = /server\.registerTool\(\s*["']([^"']+)["']/g;
  let match = pattern.exec(text);
  while (match !== null) {
    names.push(match[1]);
    match = pattern.exec(text);
  }
  return names;
}

function main() {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  const packageManifest = JSON.parse(fs.readFileSync(PACKAGE_MANIFEST_PATH, "utf8"));
  if (JSON.stringify(manifest) !== JSON.stringify(packageManifest)) {
    fail("Surface manifest package mirror drifted");
  }
  const cli = manifest.surfaces.cli;
  const mcp = manifest.surfaces.mcp;
  const coreTools = mcp.core_tools;
  const fanoutTools = mcp.fanout_tools;
  const allTools = [...coreTools, ...fanoutTools];

  requireUnique("CLI commands", cli.commands);
  requireUnique("MCP tools", allTools);
  requireEqual("CLI command count", cli.commands.length, cli.command_count);
  requireEqual("MCP core tool count", coreTools.length, mcp.core_tool_count);
  requireEqual("MCP fanout tool count", fanoutTools.length, mcp.fanout_tool_count);
  requireEqual("MCP total tool count", allTools.length, mcp.total_tools);

  requireArrayEqual(
    "MCP core runtime registrations",
    registeredTools("mcp-server/src/index.js"),
    coreTools
  );
  requireArrayEqual(
    "MCP fanout runtime registrations",
    registeredTools("mcp-server/src/fanout.js"),
    fanoutTools
  );

  requireIncludes("README.md", "through " + mcp.total_tools + " MCP tools");
  requireIncludes(
    "docs/design.md",
    "Exactly " +
      mcp.total_tools +
      " tools: the " +
      mcp.core_tool_count +
      " core tools below plus the " +
      mcp.fanout_tool_count +
      " fanout tools"
  );
  requireIncludes("protocol/PROTOCOL.md", "through exactly " + mcp.total_tools + " tools");
  requireIncludes(
    "mcp-server/src/index.js",
    "as " + mcp.core_tool_count + " core MCP tools"
  );
  requireIncludes(
    "mcp-server/src/index.js",
    mcp.total_tools + " tools in total"
  );

  for (const tool of allTools) {
    requireIncludes("README.md", tool);
    requireIncludes("docs/design.md", tool);
  }

  const help = spawnSync("python3", ["scripts/mythify.py", "--help"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  if (help.status !== 0) {
    fail("CLI help failed: " + (help.stderr || help.stdout || "no output"));
  }
  for (const command of cli.commands) {
    if (!help.stdout.includes(command)) {
      fail("CLI help missing manifest command: " + command);
    }
  }

  if (process.exitCode) {
    return process.exitCode;
  }
  console.log("[OK] Surface manifest matches runtime registrations and public docs");
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === SCRIPT_PATH) {
  process.exitCode = main();
}
