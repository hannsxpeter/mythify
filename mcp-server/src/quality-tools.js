import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { z } from "zod";


export const QUALITY_TOOL_NAMES = ["maintainability_review_create", "maintainability_review_status"];


function findingClass(detail) {
  return String(detail || "").toLowerCase().match(/[a-z0-9]+/g)?.join(" ") || "";
}


function recurringEvalCandidates(stateDir, findings) {
  const prior = new Map();
  const reviewsDir = path.join(stateDir, "reviews");
  for (const name of fs.existsSync(reviewsDir) ? fs.readdirSync(reviewsDir) : []) {
    if (!name.endsWith(".json")) continue;
    let review;
    try { review = JSON.parse(fs.readFileSync(path.join(reviewsDir, name), "utf8")); }
    catch { continue; }
    if (!["warn", "fail"].includes(review.status)) continue;
    for (const finding of review.findings || []) {
      const key = findingClass(finding.detail);
      if (!key) continue;
      prior.set(key, [...new Set([...(prior.get(key) || []), review.name || name.slice(0, -5)])]);
    }
  }
  return (findings || []).flatMap((finding) => {
    const key = findingClass(finding.detail);
    const sourceReviews = prior.get(key) || [];
    if (!key || sourceReviews.length === 0) return [];
    const digest = crypto.createHash("sha256").update(key).digest("hex").slice(0, 10);
    return [{
      title: `maintainability-regression-${digest}`,
      finding_class: key,
      rationale: "The same concrete maintainability finding recurred and should become an executable eval when a fail-pass scenario can be written.",
      source_reviews: sourceReviews,
    }];
  });
}


function requireDep(deps, name) {
  if (typeof deps[name] !== "function") throw new Error(`registerQualityTools requires deps.${name}`);
  return deps[name];
}


export function registerQualityTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const resolveStateDir = requireDep(deps, "resolveStateDir");
  const slugify = requireDep(deps, "slugify");
  const isoNow = requireDep(deps, "isoNow");
  const writeJsonAtomic = requireDep(deps, "writeJsonAtomic");
  const reviewPath = (slug) => path.join(resolveStateDir(), "reviews", `${slug}.json`);
  const requiredText = z.string().trim().min(1);
  const findingSchema = z.object({ path: requiredText, line: z.number().int().positive(), detail: requiredText });

  server.registerTool("maintainability_review_create", {
    title: "Create a maintainability review",
    description: "Record advisory review of changed seams. The result remains material and cannot satisfy verification.",
    inputSchema: {
      status: z.enum(["pass", "warn", "fail"]),
      changed_paths: z.array(requiredText).min(1),
      interface_depth: requiredText,
      locality: requiredText,
      seam_count: requiredText,
      deletion_cost: requiredText,
      invalid_state_exclusion: requiredText,
      test_validity: requiredText,
      findings: z.array(findingSchema).optional(),
      name: z.string().optional(),
    },
  }, guarded((args) => {
    const stateDir = resolveStateDir();
    const slug = slugify(args.name || `maintainability-${isoNow()}`) || "maintainability-review";
    if (fs.existsSync(reviewPath(slug))) return `[FAIL] Review already exists: ${slug}`;
    const now = isoNow();
    const candidates = ["warn", "fail"].includes(args.status)
      ? recurringEvalCandidates(stateDir, args.findings || [])
      : [];
    const record = {
      schema_version: 1,
      kind: "maintainability_review",
      name: slug,
      status: args.status,
      changed_paths: [...new Set(args.changed_paths)],
      dimensions: {
        interface_depth: args.interface_depth,
        locality: args.locality,
        seam_count: args.seam_count,
        deletion_cost: args.deletion_cost,
        invalid_state_exclusion: args.invalid_state_exclusion,
        test_validity: args.test_validity,
      },
      findings: args.findings || [],
      created: now,
      updated: now,
      evidence_status: "material_not_verification",
      eval_scenario_candidates: candidates,
      eval_proposal_recommended: candidates.length > 0,
    };
    writeJsonAtomic(reviewPath(slug), record);
    return `[OK] Maintainability review "${slug}" (${args.status}, material only).`;
  }));

  server.registerTool("maintainability_review_status", {
    title: "Show a maintainability review",
    description: "Show a material-only maintainability review without mutation.",
    inputSchema: { review: z.string(), format: z.enum(["text", "json"]).optional() },
  }, guarded(({ review, format = "text" }) => {
    const slug = slugify(review);
    let record;
    try { record = JSON.parse(fs.readFileSync(reviewPath(slug), "utf8")); }
    catch { return `[FAIL] Review not found: ${review}`; }
    if (format === "json") return JSON.stringify(record, null, 2);
    return [
      `[OK] Maintainability review: ${slug} (${record.status})`,
      `Changed paths: ${record.changed_paths.join(", ")}`,
      ...record.findings.map((finding) => `  ${finding.path}:${finding.line}: ${finding.detail}`),
      "Guardrail: review judgment is material and cannot satisfy verification.",
    ].join("\n");
  }));
}
