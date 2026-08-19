import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { z } from "zod";


export const ARTIFACT_KINDS = ["research", "map", "design", "plan", "outcome", "review", "verification"];
export const LINEAGE_TOOL_NAMES = ["lineage_attach", "lineage_status"];
export const PRECEDENCE = [
  "live_code_current_behavior",
  "approved_design_desired_behavior",
  "latest_linked_plan_implementation_order",
  "executed_verification_completion",
];


function normalizeArtifactId(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}


function artifactPath(stateDir, kind, id) {
  const artifactId = normalizeArtifactId(id);
  if (!artifactId) throw new Error("artifact id must be non-empty");
  if (kind === "research") return path.join(stateDir, "research", `${artifactId}.json`);
  if (kind === "map") return path.join(stateDir, "maps", `${artifactId}.json`);
  if (kind === "design") return path.join(stateDir, "designs", `${artifactId}.json`);
  if (kind === "plan") return path.join(stateDir, "plans", `${artifactId}.json`);
  if (kind === "outcome") return path.join(stateDir, "outcomes", artifactId, "goal.json");
  if (kind === "review") return path.join(stateDir, "reviews", `${artifactId}.json`);
  return path.join(stateDir, "verifications.jsonl");
}


function readJson(target) {
  try { return JSON.parse(fs.readFileSync(target, "utf8")); } catch { return null; }
}


function artifactRecord(stateDir, kind, id) {
  const target = artifactPath(stateDir, kind, id);
  if (kind !== "verification") return readJson(target);
  try {
    const rows = fs.readFileSync(target, "utf8").split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    return rows.reverse().find((row) => row.id === id) || null;
  } catch {
    return null;
  }
}


export function revisionDigest(record) {
  const canonicalize = (value) => {
    if (Array.isArray(value)) return value.map(canonicalize);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.keys(value).sort().map((key) => [key, canonicalize(value[key])])
      );
    }
    return value;
  };
  return crypto.createHash("sha256").update(JSON.stringify(canonicalize(record))).digest("hex");
}


function recordUpdated(record) {
  return String(record.updated || record.last_updated || record.timestamp || record.created || "");
}


export function captureLineage(stateDir, parentSpecs, isoNow) {
  if (!Array.isArray(parentSpecs) || parentSpecs.length === 0) return null;
  const parents = parentSpecs.map((raw) => {
    const kind = String(typeof raw === "string" ? raw.split(":", 1)[0] : raw.kind || "").trim();
    const rawId = String(typeof raw === "string" ? raw.slice(raw.indexOf(":") + 1) : raw.id || "").trim();
    const id = normalizeArtifactId(rawId);
    if (!ARTIFACT_KINDS.includes(kind) || !id) throw new Error("parent must name a supported kind and id");
    const record = artifactRecord(stateDir, kind, id);
    if (!record) throw new Error(`parent artifact not found: ${kind}:${id}`);
    return { kind, id, revision: revisionDigest(record), observed_updated: recordUpdated(record) };
  });
  return { captured_at: isoNow(), parents };
}


export function inspectLineage(stateDir, lineage) {
  if (!lineage || !Array.isArray(lineage.parents)) {
    return { status: "unknown", parents: [], precedence: PRECEDENCE };
  }
  const parents = lineage.parents.map((parent) => {
    if (!ARTIFACT_KINDS.includes(parent.kind) || !parent.id) return { ...parent, status: "unknown" };
    const current = artifactRecord(stateDir, parent.kind, parent.id);
    if (!current) return { ...parent, status: "missing" };
    const currentRevision = revisionDigest(current);
    return {
      ...parent,
      status: currentRevision === parent.revision ? "current" : "stale",
      current_revision: currentRevision,
      current_updated: recordUpdated(current),
    };
  });
  const statuses = new Set(parents.map((parent) => parent.status));
  const status = statuses.has("missing") ? "missing" : statuses.has("stale") ? "stale" : statuses.has("unknown") ? "unknown" : "current";
  return { status, parents, precedence: PRECEDENCE };
}


function requireDep(deps, name) {
  if (typeof deps[name] !== "function") throw new Error(`registerLineageTools requires deps.${name}`);
  return deps[name];
}


export function registerLineageTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const resolveStateDir = requireDep(deps, "resolveStateDir");
  const writeJsonAtomic = requireDep(deps, "writeJsonAtomic");
  const isoNow = requireDep(deps, "isoNow");
  const parentSchema = z.object({ kind: z.enum(ARTIFACT_KINDS), id: z.string() });

  server.registerTool("lineage_attach", {
    title: "Attach artifact lineage",
    description: "Capture current parent revisions on a mutable artifact. Verification lineage must be supplied to verify_run.",
    inputSchema: {
      kind: z.enum(ARTIFACT_KINDS.filter((kind) => kind !== "verification")),
      id: z.string(),
      parents: z.array(parentSchema).min(1),
    },
  }, guarded(({ kind, id, parents }) => {
    const stateDir = resolveStateDir();
    const artifactId = normalizeArtifactId(id);
    const record = artifactRecord(stateDir, kind, artifactId);
    if (!record) return `[FAIL] Artifact not found: ${kind}:${artifactId || id}`;
    let lineage;
    try { lineage = captureLineage(stateDir, parents, isoNow); } catch (error) { return `[FAIL] ${error.message}`; }
    if (lineage.parents.some((parent) => parent.kind === kind && parent.id === artifactId)) {
      return "[FAIL] An artifact cannot be its own lineage parent.";
    }
    record.lineage = lineage;
    record.lineage_updated = isoNow();
    writeJsonAtomic(artifactPath(stateDir, kind, artifactId), record);
    return `[OK] Attached ${parents.length} parent reference(s) to ${kind}:${artifactId}`;
  }));

  server.registerTool("lineage_status", {
    title: "Inspect artifact lineage",
    description: "Report current, stale, missing, or unknown parent revisions without mutation.",
    inputSchema: { kind: z.enum(ARTIFACT_KINDS), id: z.string(), format: z.enum(["text", "json"]).optional() },
  }, guarded(({ kind, id, format = "text" }) => {
    const stateDir = resolveStateDir();
    const artifactId = normalizeArtifactId(id);
    const record = artifactRecord(stateDir, kind, artifactId);
    if (!record) return `[FAIL] Artifact not found: ${kind}:${artifactId || id}`;
    const result = { kind: "artifact_lineage", artifact: { kind, id: artifactId }, ...inspectLineage(stateDir, record.lineage) };
    if (format === "json") return JSON.stringify(result, null, 2);
    return [
      `[OK] Lineage ${kind}:${artifactId}: ${result.status}`,
      ...result.parents.map((parent) => `  ${parent.kind}:${parent.id} ${parent.status}`),
      "Guardrail: lineage status is advisory; executable evidence owns completion.",
    ].join("\n");
  }));
}
