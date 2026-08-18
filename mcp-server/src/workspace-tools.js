import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { z } from "zod";

const ISOLATION_STRENGTH = { none: 0, worktree: 1 };

function readObject(target) {
  if (!fs.existsSync(target)) return { value: {}, exists: false, sha256: null };
  const bytes = fs.readFileSync(target);
  const value = JSON.parse(bytes.toString("utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`workspace configuration must be a JSON object: ${target}`);
  }
  return { value, exists: true, sha256: crypto.createHash("sha256").update(bytes).digest("hex") };
}

function inside(target, root) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== "..");
}


function canonicalPath(target) {
  const suffix = [];
  let existing = path.resolve(target);
  while (!fs.existsSync(existing)) {
    const parent = path.dirname(existing);
    if (parent === existing) break;
    suffix.unshift(path.basename(existing));
    existing = parent;
  }
  const canonicalBase = fs.realpathSync.native(existing);
  return path.resolve(canonicalBase, ...suffix);
}


function mergedConfig(shared, local) {
  for (const [label, value] of [["shared", shared], ["local", local]]) {
    if (!value.authorization || typeof value.authorization !== "object" || Array.isArray(value.authorization)) {
      if (value.authorization !== undefined) throw new Error(`${label} authorization must be an object`);
    }
    if (value.repositories !== undefined && !Array.isArray(value.repositories)) {
      throw new Error(`${label} repositories must be an array`);
    }
    if (value.frozen_paths !== undefined && (
      !Array.isArray(value.frozen_paths) || value.frozen_paths.some((item) => typeof item !== "string")
    )) {
      throw new Error(`${label} frozen_paths must be an array of strings`);
    }
  }
  const sharedIsolation = shared.task_isolation || "none";
  const localIsolation = local.task_isolation || sharedIsolation;
  if (!(sharedIsolation in ISOLATION_STRENGTH) || !(localIsolation in ISOLATION_STRENGTH)) {
    throw new Error("task_isolation must be none or worktree");
  }
  if (ISOLATION_STRENGTH[localIsolation] < ISOLATION_STRENGTH[sharedIsolation]) {
    throw new Error("local workspace configuration may not weaken task_isolation");
  }
  const authorization = { ...(shared.authorization || {}) };
  for (const [key, value] of Object.entries(local.authorization || {})) {
    if (authorization[key] === true && value === false) {
      throw new Error(`local workspace configuration may not weaken authorization.${key}`);
    }
    authorization[key] = value;
  }
  const byId = new Map();
  const order = [];
  for (const repository of shared.repositories || []) {
    if (!repository || typeof repository !== "object" || Array.isArray(repository)) {
      throw new Error("shared repositories must contain objects");
    }
    const id = String(repository.id || "").trim();
    if (!id || byId.has(id)) throw new Error("shared repository ids must be non-empty and unique");
    byId.set(id, { ...repository });
    order.push(id);
  }
  for (const override of local.repositories || []) {
    if (!override || typeof override !== "object" || Array.isArray(override)) {
      throw new Error("local repositories must contain objects");
    }
    if (Object.keys(override).some((key) => !["id", "path"].includes(key))) {
      throw new Error("local repository overrides may contain only id and path");
    }
    const id = String(override.id || "").trim();
    if (!byId.has(id)) throw new Error(`local repository override references unknown id: ${id}`);
    byId.get(id).path = override.path;
  }
  return {
    ...shared,
    task_isolation: localIsolation,
    frozen_paths: [...new Set([...(shared.frozen_paths || []), ...(local.frozen_paths || [])])],
    authorization,
    repositories: order.map((id) => byId.get(id)),
  };
}

export function loadWorkspaceConfig(stateDir) {
  const workspaceRoot = canonicalPath(path.resolve(stateDir, ".."));
  const sharedPath = path.join(stateDir, "workspace.json");
  const localPath = path.join(stateDir, "workspace.local.json");
  const shared = readObject(sharedPath);
  const local = readObject(localPath);
  if (!shared.exists) throw new Error(`shared workspace configuration not found: ${sharedPath}`);
  const configuration = mergedConfig(shared.value, local.value);
  if (!Array.isArray(configuration.repositories) || configuration.repositories.length === 0) {
    throw new Error("workspace configuration requires at least one repository");
  }
  if (configuration.repositories.filter((repository) => repository.primary === true).length > 1) {
    throw new Error("workspace configuration permits at most one primary repository");
  }
  configuration.repositories = configuration.repositories.map((repository) => {
    const id = String(repository.id || "").trim();
    if (!id || typeof repository.path !== "string" || !repository.path.trim()) {
      throw new Error("each repository requires non-empty id and path");
    }
    const resolvedPath = canonicalPath(path.resolve(workspaceRoot, repository.path));
    if (!inside(resolvedPath, workspaceRoot)) throw new Error(`repository path escapes workspace root: ${id}`);
    if (!fs.statSync(resolvedPath, { throwIfNoEntry: false })?.isDirectory()) {
      throw new Error(`repository path does not exist: ${resolvedPath}`);
    }
    if (!fs.existsSync(path.join(resolvedPath, ".git"))) throw new Error(`repository path is not a Git checkout: ${resolvedPath}`);
    const resolvedAllowedPaths = (repository.allowed_paths || []).map((entry) => {
      const resolved = canonicalPath(path.resolve(resolvedPath, String(entry)));
      if (!inside(resolved, resolvedPath)) throw new Error(`allowed path escapes repository ${id}: ${entry}`);
      return resolved;
    });
    return { ...repository, resolved_path: resolvedPath, resolved_allowed_paths: resolvedAllowedPaths };
  });
  configuration.resolved_frozen_paths = (configuration.frozen_paths || []).map((entry) => {
    const resolved = canonicalPath(path.resolve(workspaceRoot, String(entry)));
    if (!inside(resolved, workspaceRoot)) throw new Error(`frozen path escapes workspace root: ${entry}`);
    return resolved;
  });
  return {
    kind: "workspace_configuration",
    status: "valid",
    workspace_root: workspaceRoot,
    configuration,
    sources: {
      shared: { path: sharedPath, exists: shared.exists, sha256: shared.sha256 },
      local: { path: localPath, exists: local.exists, sha256: local.sha256 },
    },
    mutation: "none",
    quality_claim: "none",
  };
}

export function registerWorkspaceTools(server, deps) {
  server.registerTool("workspace_status", {
    title: "Inspect workspace configuration",
    description: "Validate and report merged shared and local repository configuration without mutation.",
    inputSchema: { format: z.enum(["text", "json"]).optional() },
  }, deps.guarded(({ format = "text" }) => {
    let result;
    try { result = loadWorkspaceConfig(deps.resolveStateDir()); } catch (error) { return `[FAIL] ${error.message}`; }
    if (format === "json") return JSON.stringify(result, null, 2);
    return [
      `[OK] Workspace configuration valid: ${result.workspace_root}`,
      `Repositories: ${result.configuration.repositories.length}; task isolation: ${result.configuration.task_isolation}`,
      `Sources: shared=${result.sources.shared.exists}, local=${result.sources.local.exists}`,
      "Guardrail: inspection is read-only and does not create worktrees or mutate repositories.",
    ].join("\n");
  }));
}
