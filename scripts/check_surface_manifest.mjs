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
const RELEASE_GATES_PATH = path.join(REPO_ROOT, "protocol", "release-gates.json");
const PACKAGE_RELEASE_GATES_PATH = path.join(
  REPO_ROOT,
  "mcp-server",
  "protocol",
  "release-gates.json"
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

function tierValues(label, tiers) {
  if (!tiers || typeof tiers !== "object") {
    fail(label + " missing tiers");
    return [];
  }
  const values = [];
  for (const [tier, names] of Object.entries(tiers)) {
    if (!Array.isArray(names)) {
      fail(label + " tier " + tier + " must be an array");
      continue;
    }
    requireUnique(label + " tier " + tier, names);
    values.push(...names);
  }
  return values;
}

function requireTierPartition(label, tiers, allNames) {
  const values = tierValues(label, tiers);
  requireUnique(label + " tiers", values);
  requireArrayEqual(label + " tier partition", values, allNames);
}

function requireIncludes(relativePath, needle) {
  const text = readText(relativePath);
  if (!text.includes(needle)) {
    fail(relativePath + " missing required text: " + needle);
  }
}

function requireExcludes(relativePath, needle) {
  const text = readText(relativePath);
  if (text.includes(needle)) {
    fail(relativePath + " contains forbidden stale text: " + needle);
  }
}

function requireMatches(relativePath, pattern, label) {
  const text = readText(relativePath);
  if (!pattern.test(text)) {
    fail(relativePath + " missing required contract: " + label);
  }
}

function registeredTools(relativePaths) {
  const paths = Array.isArray(relativePaths) ? relativePaths : [relativePaths];
  const names = [];
  const pattern = /server\.registerTool\(\s*["']([^"']+)["']/g;
  for (const relativePath of paths) {
    const text = readText(relativePath);
    let match = pattern.exec(text);
    while (match !== null) {
      names.push(match[1]);
      match = pattern.exec(text);
    }
  }
  return names;
}

function main() {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  const packageManifest = JSON.parse(fs.readFileSync(PACKAGE_MANIFEST_PATH, "utf8"));
  if (JSON.stringify(manifest) !== JSON.stringify(packageManifest)) {
    fail("Surface manifest package mirror drifted");
  }
  const releaseGates = JSON.parse(fs.readFileSync(RELEASE_GATES_PATH, "utf8"));
  const packageReleaseGates = JSON.parse(
    fs.readFileSync(PACKAGE_RELEASE_GATES_PATH, "utf8")
  );
  if (JSON.stringify(releaseGates) !== JSON.stringify(packageReleaseGates)) {
    fail("Release gates package mirror drifted");
  }
  const cli = manifest.surfaces.cli;
  const mcp = manifest.surfaces.mcp;
  const coreTools = mcp.core_tools;
  const fanoutTools = mcp.fanout_tools;
  const allTools = [...coreTools, ...fanoutTools];
  const packageJson = JSON.parse(readText("mcp-server/package.json"));
  const releaseMajor = packageJson.version.split(".")[0] + ".x";
  const cliVersionMatch = readText("scripts/mythify.py").match(
    /^VERSION = "([^"]+)"$/m
  );

  if (!cliVersionMatch) {
    fail("scripts/mythify.py missing VERSION constant");
  } else {
    requireEqual("CLI and MCP version", cliVersionMatch[1], packageJson.version);
  }

  requireUnique("CLI commands", cli.commands);
  requireUnique("MCP tools", allTools);
  requireEqual("CLI command count", cli.commands.length, cli.command_count);
  requireEqual("MCP core tool count", coreTools.length, mcp.core_tool_count);
  requireEqual("MCP fanout tool count", fanoutTools.length, mcp.fanout_tool_count);
  requireEqual("MCP total tool count", allTools.length, mcp.total_tools);
  requireTierPartition("CLI", cli.tiers, cli.commands);
  requireTierPartition("MCP", mcp.tiers, allTools);

  requireArrayEqual(
    "MCP core runtime registrations",
    registeredTools([
      "mcp-server/src/index.js",
      "mcp-server/src/adapter-tools.js",
      "mcp-server/src/view-tools.js",
      "mcp-server/src/memory-tools.js",
      "mcp-server/src/plan-tools.js",
      "mcp-server/src/outcome-tools.js",
      "mcp-server/src/verification-tools.js",
      "mcp-server/src/workflow-tools.js",
    ]),
    coreTools
  );
  requireArrayEqual(
    "MCP fanout runtime registrations",
    registeredTools("mcp-server/src/fanout-registration.js"),
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
  requireMatches(
    "docs/design.md",
    new RegExp(
      "package\\.json: name `mythify-mcp`, version `" +
        packageJson.version.replaceAll(".", "\\.") +
        "`"
    ),
    "MCP package version row matches package.json"
  );
  requireIncludes("SECURITY.md", "| " + releaseMajor + " | Yes |");
  requireIncludes(
    "roadmap.md",
    "Current release target: `v" + packageJson.version + "`"
  );
  requireIncludes(
    "roadmap.md",
    "Release gate: pending for `v" + packageJson.version + "`"
  );
  requireExcludes(
    "roadmap.md",
    "Release gate: passed for `v" + packageJson.version + "`"
  );
  requireMatches(
    "docs/design.md",
    /\| `outcome start GOAL[^\n]+--agent COMMAND[^\n]+--max-cost N[^\n]+--escalate-after N[^\n]+--allowed-paths CSV/,
    "CLI outcome start row binds agent, cost, escalation, and path controls"
  );
  requireMatches(
    "docs/design.md",
    /\| `outcome run \[NAME\][^\n]+\| Drive a self-driving loop/,
    "CLI outcome run row"
  );
  requireMatches(
    "docs/design.md",
    /\| `plan create GOAL \[--steps JSON\][^\n]+\| Create plan[^\n]+"verify_command": str \(optional\)/,
    "CLI plan create verify_command schema"
  );
  requireMatches(
    "docs/design.md",
    /\| `plan add-step TITLE \[--criteria TEXT\] \[--verify COMMAND\] \[--plan NAME\]`/,
    "CLI plan add-step verify command"
  );
  requireMatches(
    "docs/design.md",
    /\| `plan_create` \| `\{[^\n]+steps\?: \[\{title: string, success_criteria\?: string, verify_command\?: string\}\]/,
    "MCP plan_create verify_command schema"
  );
  requireMatches(
    "docs/design.md",
    /\| `plan_add_step` \| `\{title: string, success_criteria\?: string, verify_command\?: string, plan\?: string\}`/,
    "MCP plan_add_step verify_command schema"
  );
  requireMatches(
    "docs/design.md",
    /result alone does not satisfy strict completion; a passing step-scoped[\s\S]{0,160}recorded after the step starts/,
    "strict completion requires step-scoped verification after start"
  );
  requireMatches(
    "docs/design.md",
    /`init`, `protocol check`, `trace` analysis commands, `classify`, and\s+`loop-fit` do not require a workspace\. `route` treats the workspace as optional/,
    "workspace-free command list and optional route workspace"
  );
  requireMatches(
    "docs/design.md",
    /the full self-contained instruction for this worker; the worker sees only that prompt plus readable `context_paths` content/,
    "fanout prompt isolation contract"
  );
  requireMatches(
    "docs/design.md",
    /The canonical behavioral protocol is generated and hash-checked[\s\S]{0,240}no stale fixed line ceiling overrides contract\s+completeness/,
    "generated protocol has no stale fixed line ceiling"
  );
  requireMatches(
    "docs/design.md",
    /A passing record satisfies a\s+gate only when `verified` is true, `exit_code` is zero,[\s\S]{0,180}`provenance\.worktree_clean`[\s\S]{0,100}`provenance\.mythify_version` match the clean current checkout/,
    "release readiness binds evidence to command result and clean provenance"
  );
  requireMatches(
    "docs/design.md",
    /Legacy records[\s\S]{0,180}freshness `legacy`[\s\S]{0,120}gate status\s+`stale`/,
    "legacy evidence remains readable but cannot satisfy readiness"
  );
  requireMatches(
    "docs/design.md",
    /top-level `current_provenance`[\s\S]{0,160}gate counts including `stale`/,
    "readiness reports current provenance and stale counts"
  );
  requireMatches(
    "docs/design.md",
    /current Git commit is unavailable[\s\S]{0,80}`current_git_commit_unavailable`/,
    "readiness fails closed when current Git provenance is unavailable"
  );
  requireIncludes(
    "docs/design.md",
    '"provenance": {"git_commit": "hex string or null", "worktree_clean": "boolean or null", "mythify_version": "semver string"}'
  );
  requireIncludes("docs/design.md", "scripts/package_cli.py");
  requireIncludes("docs/design.md", "mythify-uninstall");
  requireIncludes("docs/design.md", "passing_expected_verifications");
  requireMatches(
    "docs/design.md",
    /`allowed_paths` are not a sandbox[\s\S]{0,260}`outcome run`\s+enforces/,
    "outcome allowed-path enforcement distinction"
  );
  requireMatches(
    "docs/design.md",
    /"verify_command": "str \(optional and absent unless supplied\)"[\s\S]{0,900}`plan create` and\s+`plan add-step` both persist/,
    "normal plan steps persist optional verify_command"
  );
  requireMatches(
    "docs/design.md",
    /`verification_cursor`[\s\S]{0,220}prevents old same-second evidence from being reused/,
    "step completion uses an append-order verification cursor"
  );
  requireMatches(
    "docs/design.md",
    /isolation\?: enum\(none, worktree\)[\s\S]{0,1600}changed work is committed for host merge/,
    "fanout worktree isolation is part of the public schema"
  );
  requireIncludes("README.md", "docs/evidence/efficacy-reproduction.md");
  requireExcludes("README.md", "so they always agree");
  requireIncludes(".github/workflows/release.yml", "python3 scripts/package_cli.py");
  requireIncludes(".github/workflows/release.yml", "dist/mythify-cli-*.tar.gz");
  requireIncludes(".github/workflows/release.yml", "SHA256SUMS");
  requireIncludes(".github/workflows/release.yml", "--check-release-tag");
  requireIncludes(".github/workflows/release.yml", "scripts/build_release_checksums.py");
  requireIncludes(
    "docs/release.md",
    "dist/mythify-cli-" + packageJson.version + ".tar.gz"
  );
  requireIncludes("docs/release.md", "SHA256SUMS");
  requireIncludes("docs/release.md", "scripts/build_release_checksums.py");
  requireIncludes("roadmap.md", "standalone CLI tarball");
  requireIncludes("roadmap.md", "`SHA256SUMS`");
  requireIncludes(
    "docs/design.md",
    "the " + mcp.core_tool_count + " core tools plus"
  );
  requireMatches(
    "docs/design.md",
    new RegExp(
      "### Smoke coverage[\\s\\S]{0,700}" + mcp.total_tools + "-tool set equality"
    ),
    "smoke coverage tool count matches the manifest"
  );
  requireIncludes(
    "docs/design.md",
    "### Tools (" + mcp.fanout_tool_count + ", total " + mcp.total_tools + ")"
  );

  // The beginner README names the tool count and points to design.md; the
  // exhaustive per-tool reference (and drift guard) lives in design.md.
  for (const tool of allTools) {
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
