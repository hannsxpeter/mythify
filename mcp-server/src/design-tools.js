import fs from "node:fs";
import path from "node:path";
import { z } from "zod";


export const DESIGN_TOOL_NAMES = [
  "design_create",
  "design_add_alternative",
  "design_approve",
  "design_status",
];


function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerDesignTools requires deps.${name}`);
  }
  return value;
}


export function registerDesignTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const resolveStateDir = requireDep(deps, "resolveStateDir");
  const slugify = requireDep(deps, "slugify");
  const isoNow = requireDep(deps, "isoNow");
  const writeJsonAtomic = requireDep(deps, "writeJsonAtomic");
  const writeTextAtomic = requireDep(deps, "writeTextAtomic");
  const readJsonRecover = requireDep(deps, "readJsonRecover");
  const captureLineage = requireDep(deps, "captureLineage");

  const designsDir = () => path.join(resolveStateDir(), "designs");
  const designPath = (slug) => path.join(designsDir(), `${slug}.json`);
  const activePath = () => path.join(designsDir(), "active");
  const resolveDesign = (name) => {
    let slug = String(name || "").trim();
    if (!slug) {
      try {
        slug = fs.readFileSync(activePath(), "utf8").trim();
      } catch {
        return { error: "[FAIL] No active design. Create one with design_create." };
      }
    }
    slug = slugify(slug);
    const record = readJsonRecover(designPath(slug), () => null);
    if (!record) {
      return { error: `[FAIL] Design not found: ${name || slug}.` };
    }
    return { slug, record };
  };

  server.registerTool(
    "design_create",
    {
      title: "Create a design record",
      description: "Create durable product, system, and program design material. It is not verification evidence.",
      inputSchema: {
        title: z.string(),
        problem: z.string(),
        current_state: z.string().optional(),
        desired_state: z.string().optional(),
        non_goals: z.string().optional(),
        product_decisions: z.string().optional(),
        system_decisions: z.string().optional(),
        program_decisions: z.string().optional(),
        name: z.string().optional(),
        parents: z.array(z.object({ kind: z.string(), id: z.string() })).optional(),
      },
    },
    guarded((args) => {
      const slug = slugify(args.name || args.title) || "design";
      if (fs.existsSync(designPath(slug))) {
        return `[FAIL] Design already exists: ${slug}`;
      }
      const now = isoNow();
      const record = {
        schema_version: 1,
        name: slug,
        title: args.title,
        problem: args.problem,
        current_state: args.current_state || "",
        desired_state: args.desired_state || "",
        non_goals: args.non_goals || "",
        product_decisions: args.product_decisions || "",
        system_decisions: args.system_decisions || "",
        program_decisions: args.program_decisions || "",
        alternatives: [],
        selected_alternative: null,
        status: "draft",
        created: now,
        updated: now,
      };
      if (args.parents && args.parents.length > 0) {
        try { record.lineage = captureLineage(resolveStateDir(), args.parents, isoNow); }
        catch (error) { return `[FAIL] Invalid lineage: ${error.message}.`; }
      }
      writeJsonAtomic(designPath(slug), record);
      writeTextAtomic(activePath(), slug + "\n");
      return `[OK] Created design "${slug}" (draft).`;
    })
  );

  server.registerTool(
    "design_add_alternative",
    {
      title: "Add a design alternative",
      description: "Add one bounded interface alternative to a design record.",
      inputSchema: {
        title: z.string(),
        interface: z.string(),
        call_sites: z.string(),
        locality: z.string(),
        migration_cost: z.string(),
        deletion_cost: z.string(),
        reversal_evidence: z.string(),
        select: z.boolean().optional(),
        design: z.string().optional(),
      },
    },
    guarded((args) => {
      const resolved = resolveDesign(args.design);
      if (resolved.error) return resolved.error;
      const { slug, record } = resolved;
      const interfaceKey = args.interface.trim().toLowerCase().replace(/\s+/g, " ");
      if ((record.alternatives || []).some((item) => String(item.interface || "").trim().toLowerCase().replace(/\s+/g, " ") === interfaceKey)) {
        return "[FAIL] Design alternatives must have materially different interface shapes.";
      }
      const alternative = {
        id: `A${(record.alternatives || []).length + 1}`,
        title: args.title,
        interface: args.interface,
        call_sites: args.call_sites || "",
        locality: args.locality || "",
        migration_cost: args.migration_cost || "",
        deletion_cost: args.deletion_cost || "",
        reversal_evidence: args.reversal_evidence || "",
      };
      record.alternatives = record.alternatives || [];
      record.alternatives.push(alternative);
      if (args.select) record.selected_alternative = alternative.id;
      record.updated = isoNow();
      writeJsonAtomic(designPath(slug), record);
      return `[OK] Added design alternative ${alternative.id} to "${slug}".`;
    })
  );

  server.registerTool(
    "design_approve",
    {
      title: "Approve a design",
      description: "Approve a design decision record. Approval remains material, not verification.",
      inputSchema: {
        design: z.string().optional(),
        note: z.string(),
      },
    },
    guarded(({ design, note }) => {
      const resolved = resolveDesign(design);
      if (resolved.error) return resolved.error;
      const { slug, record } = resolved;
      if ((record.alternatives || []).length > 0 && (record.alternatives || []).length < 2) {
        return "[FAIL] A design comparison requires at least two alternatives.";
      }
      if ((record.alternatives || []).length > 0 && !record.selected_alternative) {
        return "[FAIL] Select one design alternative before approval.";
      }
      record.status = "approved";
      record.approval_note = note;
      record.updated = isoNow();
      writeJsonAtomic(designPath(slug), record);
      return `[OK] Approved design "${slug}".`;
    })
  );

  server.registerTool(
    "design_status",
    {
      title: "Show a design",
      description: "Show the active or named durable design record without mutation.",
      inputSchema: {
        design: z.string().optional(),
        format: z.enum(["text", "json"]).optional(),
      },
    },
    guarded(({ design, format = "text" }) => {
      const resolved = resolveDesign(design);
      if (resolved.error) return resolved.error;
      if (format === "json") return JSON.stringify(resolved.record, null, 2);
      return [
        `[OK] Design: ${resolved.slug} (${resolved.record.status || "draft"})`,
        `Problem: ${resolved.record.problem || ""}`,
        `Alternatives: ${(resolved.record.alternatives || []).length}; selected: ${resolved.record.selected_alternative || "none"}`,
        "Guardrail: design records are material, not verification evidence.",
      ].join("\n");
    })
  );
}
