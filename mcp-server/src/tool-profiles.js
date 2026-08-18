import fs from "node:fs";

const PROFILE_MANIFEST_URL = new URL("../protocol/tool-profiles.json", import.meta.url);
const SURFACE_MANIFEST_URL = new URL("../protocol/surface-manifest.json", import.meta.url);

function readJson(url) {
  return JSON.parse(fs.readFileSync(url, "utf8"));
}

function canonicalTools(surfaceManifest) {
  const mcp = surfaceManifest.surfaces.mcp;
  return [...mcp.core_tools, ...mcp.fanout_tools];
}

function expandProfile(name, profiles, allTools, visiting = new Set()) {
  const profile = profiles[name];
  if (!profile) {
    throw new Error(`Unknown MCP tool profile: ${name}`);
  }
  if (visiting.has(name)) {
    throw new Error(`Cyclic MCP tool profile include: ${name}`);
  }
  if (profile.all_tools === true) {
    return new Set(allTools);
  }
  const nextVisiting = new Set(visiting);
  nextVisiting.add(name);
  const expanded = new Set();
  for (const included of profile.includes || []) {
    for (const tool of expandProfile(included, profiles, allTools, nextVisiting)) {
      expanded.add(tool);
    }
  }
  for (const tool of profile.tools || []) {
    expanded.add(tool);
  }
  return expanded;
}

export function resolveToolProfile(env = process.env) {
  const profileManifest = readJson(PROFILE_MANIFEST_URL);
  const surfaceManifest = readJson(SURFACE_MANIFEST_URL);
  const allTools = canonicalTools(surfaceManifest);
  const envName = profileManifest.selection_env;
  const selected = String(env[envName] || profileManifest.default_profile).trim();
  const allowed = expandProfile(selected, profileManifest.profiles, allTools);
  const canonical = new Set(allTools);
  for (const tool of allowed) {
    if (!canonical.has(tool)) {
      throw new Error(`MCP tool profile ${selected} references unknown tool: ${tool}`);
    }
  }
  return {
    selected,
    description: profileManifest.profiles[selected].description,
    selection_env: envName,
    allowed,
    available_profiles: Object.keys(profileManifest.profiles),
    full_tool_count: allTools.length,
  };
}

export function createProfiledRegistrar(server, selection) {
  const registered = [];
  let descriptionBytes = 0;
  return {
    registerTool(name, config, handler) {
      if (!selection.allowed.has(name)) {
        return;
      }
      registered.push(name);
      descriptionBytes += Buffer.byteLength(String(config?.description || ""), "utf8");
      server.registerTool(name, config, handler);
    },
    stats() {
      return {
        registered_tools: [...registered],
        registered_tool_count: registered.length,
        description_bytes: descriptionBytes,
      };
    },
  };
}

export function registerToolProfileStatus(server, { guarded, selection }) {
  server.registerTool(
    "tool_profile_status",
    {
      description: "Report the selected MCP capability profile and its deterministic tool-description budget.",
      inputSchema: {},
    },
    guarded(async () => {
      const stats = server.stats();
      return JSON.stringify(
        {
          selected_profile: selection.selected,
          description: selection.description,
          selection_env: selection.selection_env,
          available_profiles: selection.available_profiles,
          full_tool_count: selection.full_tool_count,
          registered_tool_count: stats.registered_tool_count,
          description_bytes: stats.description_bytes,
          registered_tools: stats.registered_tools,
          quality_claim: "none",
        },
        null,
        2
      );
    })
  );
}
