import fs from "node:fs";

const registryUrl = new URL("../protocol/operation-registry.json", import.meta.url);

export const OPERATION_REGISTRY = JSON.parse(fs.readFileSync(registryUrl, "utf8"));
export const MEMORY_OPERATION_REGISTRY = OPERATION_REGISTRY.surfaces.memory;
export const MEMORY_CATEGORIES = MEMORY_OPERATION_REGISTRY.categories;
export const MEMORY_DEFAULT_CATEGORY = MEMORY_OPERATION_REGISTRY.default_category;
export const MEMORY_CLEAR_MCP_REFUSAL =
  MEMORY_OPERATION_REGISTRY.operations.memory_clear.mcp.refusal;

export function getOperation(surface, operation) {
  return OPERATION_REGISTRY.surfaces[surface]?.operations?.[operation] || null;
}
