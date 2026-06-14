import fs from "node:fs";

const manifestUrl = new URL("../protocol/surface-manifest.json", import.meta.url);

export const SURFACE_MANIFEST = JSON.parse(fs.readFileSync(manifestUrl, "utf8"));
export const CLI_COMMANDS = SURFACE_MANIFEST.surfaces.cli.commands;
export const MCP_CORE_TOOLS = SURFACE_MANIFEST.surfaces.mcp.core_tools;
export const MCP_FANOUT_TOOLS = SURFACE_MANIFEST.surfaces.mcp.fanout_tools;
export const MCP_TOOL_NAMES = [...MCP_CORE_TOOLS, ...MCP_FANOUT_TOOLS];
export const MCP_TOOL_COUNT = SURFACE_MANIFEST.surfaces.mcp.total_tools;
