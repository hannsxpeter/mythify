import path from "node:path";
import { z } from "zod";
import {
  ARTIFACT_API_KEY_ENV,
  DEFAULT_ARTIFACT_SERVICE_URL,
  cleanArtifact,
  formatArtifactClean,
  formatArtifactInspection,
  formatArtifactProbe,
  inspectArtifact,
  probeArtifactService,
} from "./artifact-hygiene.js";

export const ARTIFACT_TOOL_NAMES = ["artifact_probe", "artifact_inspect", "artifact_clean"];

function requireDep(deps, name) {
  const value = deps[name];
  if (typeof value !== "function") {
    throw new Error(`registerArtifactTools requires deps.${name}`);
  }
  return value;
}

function serviceSchema() {
  return {
    service_url: z
      .string()
      .optional()
      .describe(
        `Artifact hygiene service URL. Defaults to WATERMARKS_SERVICE_URL or ${DEFAULT_ARTIFACT_SERVICE_URL}.`
      ),
    api_key_env: z
      .string()
      .optional()
      .describe(
        `Allowlisted environment variable containing bearer auth. Only ${ARTIFACT_API_KEY_ENV} or an empty value is accepted.`
      ),
    expected_version: z
      .string()
      .optional()
      .describe("Require an exact version from the service health response."),
    allow_remote: z
      .boolean()
      .optional()
      .describe("Allow a non-loopback artifact service URL. Defaults to false."),
    timeout_seconds: z
      .number()
      .positive()
      .optional()
      .describe("HTTP timeout per request in seconds."),
    format: z.enum(["text", "json"]).optional().describe("Return text or JSON."),
  };
}

export function registerArtifactTools(server, deps) {
  const guarded = requireDep(deps, "guarded");
  const resolveStateDir = requireDep(deps, "resolveStateDir");

  server.registerTool(
    "artifact_probe",
    {
      title: "Probe an artifact hygiene service",
      description:
        "Probe the optional watermarks-remover service for health, version, capabilities, and heavy-backend licensing warnings. " +
        "Loopback is the default trust boundary. The result is material, not Mythify verification evidence.",
      inputSchema: serviceSchema(),
    },
    guarded(async (args) => {
      const result = await probeArtifactService(args);
      return args.format === "json"
        ? `${result.status === "available" ? "[OK]" : "[FAIL]"} ${JSON.stringify(result, null, 2)}`
        : formatArtifactProbe(result);
    })
  );

  server.registerTool(
    "artifact_inspect",
    {
      title: "Inspect an artifact for watermark signals",
      description:
        "Inspect one local artifact through the optional watermarks-remover service without changing it. " +
        "Deterministic findings are separated from heuristic advisory signals, and known prose-frontmatter false positives are downgraded by default. " +
        "Remote uploads require both allow_remote and acknowledge_data_upload. The result is material, not verification evidence.",
      inputSchema: {
        artifact_path: z.string().min(1).describe("Path to the local artifact."),
        allow_findings: z
          .array(z.string())
          .optional()
          .describe("Exact service finding strings to downgrade to allowed."),
        use_default_allowlist: z
          .boolean()
          .optional()
          .describe("Use Mythify's prose-frontmatter false-positive allowlist. Defaults to true."),
        acknowledge_data_upload: z
          .boolean()
          .optional()
          .describe("Acknowledge that artifact bytes will be sent to a remote service."),
        ...serviceSchema(),
      },
    },
    guarded(async (args) => {
      const result = await inspectArtifact({
        ...args,
        default_cwd: path.dirname(resolveStateDir()),
      });
      return args.format === "json"
        ? `${result.status === "blocked" ? "[FAIL]" : "[OK]"} ${JSON.stringify(result, null, 2)}`
        : formatArtifactInspection(result);
    })
  );

  server.registerTool(
    "artifact_clean",
    {
      title: "Clean an authorized artifact",
      description:
        "Clean one owned or authorized artifact through the optional watermarks-remover service. " +
        "Requires explicit authorization and a separate output path, refuses symbolic-link outputs, inspects before cleaning, re-inspects the returned bytes, and writes atomically. " +
        "Remote uploads require both allow_remote and acknowledge_data_upload. The result is material, not verification evidence.",
      inputSchema: {
        artifact_path: z.string().min(1).describe("Path to the local input artifact."),
        output_path: z.string().min(1).describe("Separate output path for cleaned bytes."),
        confirm_authorized: z
          .boolean()
          .describe("Must be true to confirm ownership of or authorization to process the artifact."),
        overwrite: z
          .boolean()
          .optional()
          .describe("Replace an existing regular output file atomically. Defaults to false."),
        keep_non_ai_metadata: z
          .boolean()
          .optional()
          .describe("Ask the service to preserve metadata not classified as AI-related."),
        nfkc: z
          .boolean()
          .optional()
          .describe("Ask the service to apply Unicode NFKC normalization."),
        aggressive_homoglyphs: z
          .boolean()
          .optional()
          .describe("Ask the service to normalize suspicious homoglyphs aggressively."),
        allow_findings: z
          .array(z.string())
          .optional()
          .describe("Exact post-clean finding strings to downgrade to allowed."),
        use_default_allowlist: z
          .boolean()
          .optional()
          .describe("Use Mythify's prose-frontmatter false-positive allowlist. Defaults to true."),
        acknowledge_data_upload: z
          .boolean()
          .optional()
          .describe("Acknowledge that artifact bytes will be sent to a remote service."),
        ...serviceSchema(),
      },
    },
    guarded(async (args) => {
      const result = await cleanArtifact({
        ...args,
        default_cwd: path.dirname(resolveStateDir()),
      });
      return args.format === "json"
        ? `${result.status === "blocked" ? "[FAIL]" : "[OK]"} ${JSON.stringify(result, null, 2)}`
        : formatArtifactClean(result);
    })
  );
}
