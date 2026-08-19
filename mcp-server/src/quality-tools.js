import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { z } from "zod";

import { currentVerificationProvenanceForStateDir } from "./verification-provenance.js";
import { revisionDigest } from "./lineage-tools.js";


export const QUALITY_TOOL_NAMES = [
  "maintainability_review_create",
  "maintainability_review_status",
  "blast_radius_review_create",
  "blast_radius_review_prove",
  "blast_radius_review_status",
];

const RISK_DISPOSITIONS = ["confirmed", "cleared", "unproven"];


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
    if (review.kind !== "maintainability_review" || !["warn", "fail"].includes(review.status)) continue;
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


function readJson(target) {
  try { return JSON.parse(fs.readFileSync(target, "utf8")); } catch { return null; }
}


function readJsonl(target) {
  try {
    return fs.readFileSync(target, "utf8").split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}


function changeFingerprint(stateDir) {
  const provenance = currentVerificationProvenanceForStateDir(stateDir);
  return {
    git_commit: provenance.git_commit,
    worktree_clean: provenance.worktree_clean,
    worktree_digest: provenance.worktree_digest,
  };
}


function changeFreshness(stateDir, record) {
  const expected = record.change_fingerprint || {};
  const current = changeFingerprint(stateDir);
  if (!expected.git_commit || !expected.worktree_digest) {
    return { status: "unknown", reason: "review_change_fingerprint_unavailable", current };
  }
  if (!current.git_commit || !current.worktree_digest) {
    return { status: "unknown", reason: "current_change_fingerprint_unavailable", current };
  }
  if (expected.git_commit !== current.git_commit) {
    return { status: "stale", reason: "git_commit_mismatch", current };
  }
  if (expected.worktree_digest !== current.worktree_digest) {
    return { status: "stale", reason: "worktree_digest_mismatch", current };
  }
  return { status: "current", reason: "change_fingerprint_matches", current };
}


function blastReviewView(stateDir, record, verificationsPath) {
  const view = structuredClone(record);
  const freshness = changeFreshness(stateDir, record);
  const expectedRevision = revisionDigest(record);
  const fingerprint = record.change_fingerprint || {};
  const proofs = readJsonl(verificationsPath).filter((verification) => {
    const parents = verification.lineage?.parents || [];
    const matchesParent = parents.some((parent) =>
      parent.kind === "review" &&
      parent.id === record.name &&
      parent.revision === expectedRevision
    );
    const provenance = verification.provenance || {};
    const matchesChange = Boolean(fingerprint.git_commit && fingerprint.worktree_digest) &&
      provenance.git_commit === fingerprint.git_commit &&
      provenance.worktree_digest === fingerprint.worktree_digest;
    return verification.kind === "executed" && matchesParent && matchesChange;
  });
  const proof = proofs.at(-1) || null;
  if (proof) {
    view.safety_fact.proof_depth = proof.proof_mode === "runtime" ? 5 : 4;
    view.safety_fact.verification_id = proof.id;
    view.safety_fact.status = proof.verified === true && proof.exit_code === 0
      ? freshness.status === "current" ? "proven" : "stale"
      : "unproven";
    view.merge_gate.verification_id = proof.id;
    view.merge_gate.verified = proof.verified === true && proof.exit_code === 0;
  }
  view.change_freshness = freshness;
  return view;
}


function formatBlastReview(view) {
  const safety = view.safety_fact;
  const lines = [
    `[OK] Blast-radius review: ${view.name} (${view.status})`,
    `Change fingerprint: ${view.change_freshness.status} (${view.change_freshness.reason})`,
    `Safety fact: ${safety.claim} (depth ${safety.proof_depth}, ${safety.status})`,
  ];
  for (const disposition of RISK_DISPOSITIONS) {
    lines.push(`${disposition[0].toUpperCase()}${disposition.slice(1)}:`);
    const risks = (view.risks || []).filter((risk) => risk.disposition === disposition);
    if (risks.length === 0) lines.push("  none");
    for (const risk of risks) {
      lines.push(`  ${risk.path}:${risk.line}: ${risk.failure_mode} (likelihood ${risk.likelihood}, impact ${risk.impact})`);
    }
  }
  lines.push(`Before merge: ${view.merge_gate?.command || "unproven"}`);
  lines.push("Guardrail: the review remains material; only its linked executed verification is proof.");
  return lines.join("\n");
}


export function registerQualityTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const resolveStateDir = requireDep(deps, "resolveStateDir");
  const slugify = requireDep(deps, "slugify");
  const isoNow = requireDep(deps, "isoNow");
  const writeJsonAtomic = requireDep(deps, "writeJsonAtomic");
  const runShellCapture = requireDep(deps, "runShellCapture");
  const appendJsonl = requireDep(deps, "appendJsonl");
  const verificationsPath = requireDep(deps, "verificationsPath");
  const captureLineage = requireDep(deps, "captureLineage");
  const reviewPath = (slug) => path.join(resolveStateDir(), "reviews", `${slug}.json`);
  const requiredText = z.string().trim().min(1);
  const findingSchema = z.object({ path: requiredText, line: z.number().int().positive(), detail: requiredText });
  const riskSchema = z.object({
    failure_mode: requiredText,
    path: requiredText,
    line: z.number().int().positive(),
    likelihood: z.enum(["low", "medium", "high"]),
    impact: z.enum(["low", "medium", "high"]),
    disposition: z.enum(RISK_DISPOSITIONS).default("unproven"),
    check: z.string().optional(),
    evidence_id: z.string().optional(),
  });

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
      review_type: "maintainability",
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
    const record = readJson(reviewPath(slug));
    if (!record) return `[FAIL] Review not found: ${review}`;
    if (record.kind !== "maintainability_review") return `[FAIL] Review is not a maintainability review: ${slug}`;
    if (format === "json") return JSON.stringify(record, null, 2);
    return [
      `[OK] Maintainability review: ${slug} (${record.status})`,
      `Changed paths: ${record.changed_paths.join(", ")}`,
      ...record.findings.map((finding) => `  ${finding.path}:${finding.line}: ${finding.detail}`),
      "Guardrail: review judgment is material and cannot satisfy verification.",
    ].join("\n");
  }));

  server.registerTool("blast_radius_review_create", {
    title: "Create a blast-radius safety case",
    description: "Record one safety fact, exact change fingerprint, structured risks, cleared checks, and the cheapest merge verifier. The review remains material until linked executed proof passes.",
    inputSchema: {
      status: z.enum(["pass", "warn", "fail"]),
      changed_paths: z.array(requiredText).min(1),
      safety_fact: requiredText,
      proof_depth: z.number().int().min(1).max(3).default(1),
      risks: z.array(riskSchema).optional(),
      merge_command: z.string().optional(),
      name: z.string().optional(),
    },
  }, guarded((args) => {
    const stateDir = resolveStateDir();
    const slug = slugify(args.name || `blast-radius-${isoNow()}`) || "blast-radius-review";
    if (fs.existsSync(reviewPath(slug))) return `[FAIL] Review already exists: ${slug}`;
    const now = isoNow();
    const record = {
      schema_version: 1,
      kind: "blast_radius_review",
      review_type: "blast_radius",
      name: slug,
      status: args.status,
      changed_paths: [...new Set(args.changed_paths)],
      change_fingerprint: changeFingerprint(stateDir),
      safety_fact: {
        claim: args.safety_fact,
        proof_depth: args.proof_depth,
        status: "unproven",
        verification_id: null,
      },
      risks: args.risks || [],
      merge_gate: { command: String(args.merge_command || "").trim(), verification_id: null },
      created: now,
      updated: now,
      evidence_status: "material_not_verification",
    };
    writeJsonAtomic(reviewPath(slug), record);
    return `[OK] Blast-radius review "${slug}" (${args.status}, safety fact unproven).`;
  }));

  server.registerTool("blast_radius_review_prove", {
    title: "Prove a blast-radius safety fact",
    description: "Run the stored or supplied command against the exact reviewed change and append executed verification parented to the immutable review.",
    inputSchema: {
      review: requiredText,
      command: z.string().optional(),
      claim: z.string().optional(),
      proof_mode: z.enum(["executed", "runtime"]).default("executed"),
      timeout_seconds: z.number().positive().default(300),
    },
  }, guarded((args) => {
    const stateDir = resolveStateDir();
    const slug = slugify(args.review);
    const record = readJson(reviewPath(slug));
    if (!record) return `[FAIL] Review not found: ${args.review}`;
    if (record.kind !== "blast_radius_review") return `[FAIL] Review is not a blast-radius safety case: ${slug}`;
    if (process.env.MYTHIFY_DISABLE_RUN === "1") {
      return "[FAIL] blast_radius_review_prove is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was executed.";
    }
    const freshness = changeFreshness(stateDir, record);
    if (freshness.status !== "current") {
      return `[FAIL] Review change fingerprint is ${freshness.status}: ${freshness.reason}. Create a new review for the current change.`;
    }
    const command = String(args.command || record.merge_gate?.command || "").trim();
    if (!command) return "[FAIL] No proof command supplied and the review has no merge-gate command.";
    let lineage;
    try { lineage = captureLineage(stateDir, [{ kind: "review", id: slug }], isoNow); }
    catch (error) { return `[FAIL] Invalid lineage: ${error.message}.`; }
    const verificationId = `v-${isoNow().replace(/[^0-9]/g, "").slice(0, 17)}-${crypto.randomUUID().slice(0, 12)}`;
    const artifactDir = path.join(stateDir, "verification-artifacts", verificationId);
    const run = runShellCapture(command, args.timeout_seconds || 300, artifactDir);
    const verification = {
      id: verificationId,
      kind: "executed",
      claim: args.claim || record.safety_fact.claim,
      command,
      exit_code: run.exit_code,
      duration_seconds: run.duration_seconds,
      stdout_tail: run.stdout_tail,
      stderr_tail: run.stderr_tail,
      verified: run.verified,
      timestamp: isoNow(),
      provenance: currentVerificationProvenanceForStateDir(stateDir),
      plan: null,
      step_id: null,
      step_title: null,
      step_status: null,
      review: slug,
      proof_mode: args.proof_mode,
      lineage,
    };
    if (run.artifacts) {
      verification.artifacts = Object.fromEntries(
        Object.entries(run.artifacts).map(([channel, item]) => [channel, { ...item, path: path.relative(stateDir, item.path) }])
      );
    }
    if (run.artifact_error) verification.artifact_error = run.artifact_error;
    appendJsonl(verificationsPath(), verification);
    const label = verification.claim || command;
    const postRun = changeFreshness(stateDir, record);
    if (run.verified && postRun.status !== "current") {
      return `[FAIL] UNPROVEN: ${label} passed but changed the reviewed source (${postRun.reason}).`;
    }
    return `[${run.verified ? "OK" : "FAIL"}] ${run.verified ? "VERIFIED" : "UNVERIFIED"}: ${label} (exit ${run.exit_code}, ${run.duration_seconds.toFixed(2)}s)`;
  }));

  server.registerTool("blast_radius_review_status", {
    title: "Show a blast-radius safety case",
    description: "Show the immutable review plus proof depth, linked verification, risk dispositions, and exact-change freshness.",
    inputSchema: { review: requiredText, format: z.enum(["text", "json"]).optional() },
  }, guarded(({ review, format = "text" }) => {
    const slug = slugify(review);
    const record = readJson(reviewPath(slug));
    if (!record) return `[FAIL] Review not found: ${review}`;
    if (record.kind !== "blast_radius_review") return `[FAIL] Review is not a blast-radius safety case: ${slug}`;
    const view = blastReviewView(resolveStateDir(), record, verificationsPath());
    return format === "json" ? JSON.stringify(view, null, 2) : formatBlastReview(view);
  }));
}
