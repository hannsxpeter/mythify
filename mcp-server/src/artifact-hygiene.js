import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const MANIFEST_PATH = new URL("../protocol/artifact-hygiene.json", import.meta.url);

function loadManifest() {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  if (manifest.version !== 1 || manifest.adapter !== "watermarks-remover") {
    throw new Error("Invalid artifact hygiene manifest");
  }
  return manifest;
}

export const ARTIFACT_HYGIENE_MANIFEST = loadManifest();
export const DEFAULT_ARTIFACT_SERVICE_URL = ARTIFACT_HYGIENE_MANIFEST.default_service_url;
export const ARTIFACT_API_KEY_ENV = ARTIFACT_HYGIENE_MANIFEST.api_key_env;
export const MAX_ARTIFACT_BYTES = ARTIFACT_HYGIENE_MANIFEST.max_input_bytes;
export const ARTIFACT_EVIDENCE_STATUS = ARTIFACT_HYGIENE_MANIFEST.evidence_status;
const MAX_RESPONSE_BYTES = MAX_ARTIFACT_BYTES * 2 + 1024 * 1024;
const LOCAL_SERVICE_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);

function baseResult(serviceUrl) {
  return {
    adapter: "watermarks-remover",
    status: "blocked",
    service_url: serviceUrl,
    material_not_evidence: true,
    evidence_status: ARTIFACT_EVIDENCE_STATUS,
    verification_recorded: false,
    writes_mythify_state: false,
    error: "",
  };
}

function normalizeServiceUrl(value, allowRemote = false) {
  const raw = String(value || "").trim() ||
    String(process.env.WATERMARKS_SERVICE_URL || "").trim() ||
    DEFAULT_ARTIFACT_SERVICE_URL;
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return { ok: false, serviceUrl: raw, local: false, error: "artifact service URL is invalid" };
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    return {
      ok: false,
      serviceUrl: raw,
      local: false,
      error: "artifact service URL must use http or https and include a host",
    };
  }
  if (parsed.username || parsed.password) {
    return {
      ok: false,
      serviceUrl: raw,
      local: false,
      error: "artifact service URL must not include credentials",
    };
  }
  if (parsed.search || parsed.hash) {
    return {
      ok: false,
      serviceUrl: raw,
      local: false,
      error: "artifact service URL must not include a query or fragment",
    };
  }
  const local = LOCAL_SERVICE_HOSTS.has(parsed.hostname.toLowerCase());
  if (!local && !allowRemote) {
    return {
      ok: false,
      serviceUrl: raw,
      local,
      error: "remote artifact service requires allow_remote=true",
    };
  }
  const serviceUrl = parsed.toString().replace(/\/+$/, "");
  return { ok: true, serviceUrl, local, error: "" };
}

function requestHeaders(apiKeyEnv, includeJson = false) {
  const selected = apiKeyEnv === undefined || apiKeyEnv === null
    ? ARTIFACT_API_KEY_ENV
    : String(apiKeyEnv).trim();
  const headers = { accept: "application/json" };
  if (includeJson) {
    headers["content-type"] = "application/json";
  }
  if (!["", ARTIFACT_API_KEY_ENV].includes(selected)) {
    return {
      ok: false,
      headers,
      apiKeyEnv: selected,
      error: `api_key_env must be ${ARTIFACT_API_KEY_ENV} or empty`,
    };
  }
  if (selected && String(process.env[selected] || "").trim()) {
    headers.authorization = `Bearer ${String(process.env[selected]).trim()}`;
  }
  return { ok: true, headers, apiKeyEnv: selected, error: "" };
}

function endpoint(serviceUrl, name) {
  const suffix = ARTIFACT_HYGIENE_MANIFEST.endpoints[name];
  return `${serviceUrl.replace(/\/+$/, "")}/${suffix.replace(/^\/+/, "")}`;
}

async function boundedResponseText(response) {
  const declared = response.headers.get("content-length") || "";
  if (/^\d+$/.test(declared) && Number(declared) > MAX_RESPONSE_BYTES) {
    throw new Error("artifact service response exceeds the size limit");
  }
  if (!response.body) {
    return "";
  }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    total += value.byteLength;
    if (total > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      throw new Error("artifact service response exceeds the size limit");
    }
    chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks, total).toString("utf8");
}

async function fetchJson(serviceUrl, endpointName, method, headers, timeoutSeconds, payload = null) {
  const controller = new AbortController();
  const timeout = Number.isFinite(timeoutSeconds) && timeoutSeconds > 0 ? timeoutSeconds : 10;
  const timer = setTimeout(() => controller.abort(), Math.round(timeout * 1000));
  try {
    const response = await fetch(endpoint(serviceUrl, endpointName), {
      method,
      headers,
      body: payload === null ? undefined : JSON.stringify(payload),
      redirect: "error",
      signal: controller.signal,
    });
    const text = await boundedResponseText(response);
    let json = null;
    try {
      json = JSON.parse(text);
    } catch {
      return {
        ok: false,
        status_code: response.status,
        json: null,
        error: "artifact service returned invalid JSON",
      };
    }
    if (!json || typeof json !== "object" || Array.isArray(json)) {
      return {
        ok: false,
        status_code: response.status,
        json: null,
        error: "artifact service returned a non-object JSON response",
      };
    }
    const ok = response.ok && json.ok !== false;
    return {
      ok,
      status_code: response.status,
      json,
      error: ok ? "" : String(json.error || `HTTP ${response.status}`),
    };
  } catch (error) {
    const timedOut = error && error.name === "AbortError";
    return {
      ok: false,
      status_code: 0,
      json: null,
      error: timedOut
        ? `timed out after ${timeout} seconds`
        : String(error && error.message ? error.message : error),
    };
  } finally {
    clearTimeout(timer);
  }
}

function valueAtPath(value, dottedPath) {
  let current = value;
  for (const part of dottedPath.split(".")) {
    if (!current || typeof current !== "object" || Array.isArray(current)) {
      return undefined;
    }
    current = current[part];
  }
  return current;
}

function capabilityWarnings(capabilities) {
  const warnings = [];
  for (const [dottedPath, message] of Object.entries(
    ARTIFACT_HYGIENE_MANIFEST.heavy_backend_warnings
  )) {
    if (valueAtPath(capabilities, dottedPath) === true) {
      warnings.push(message);
    }
  }
  return warnings;
}

export async function probeArtifactService({
  service_url,
  api_key_env,
  expected_version,
  allow_remote,
  timeout_seconds,
} = {}) {
  const normalized = normalizeServiceUrl(service_url, allow_remote === true);
  const result = {
    ...baseResult(normalized.serviceUrl || String(service_url || DEFAULT_ARTIFACT_SERVICE_URL)),
    local_service: normalized.local,
    remote_service: !normalized.local,
    version: "",
    expected_version: String(expected_version || "").trim(),
    capabilities: {},
    license_warnings: [],
    checks: [],
  };
  if (!normalized.ok) {
    result.error = normalized.error;
    return result;
  }
  const headerResult = requestHeaders(api_key_env);
  result.api_key_env = headerResult.apiKeyEnv;
  result.api_key_present = Boolean(
    headerResult.apiKeyEnv && String(process.env[headerResult.apiKeyEnv] || "").trim()
  );
  if (!headerResult.ok) {
    result.error = headerResult.error;
    return result;
  }
  const health = await fetchJson(
    normalized.serviceUrl,
    "health",
    "GET",
    headerResult.headers,
    timeout_seconds || 10
  );
  result.checks.push({
    name: "health",
    ok: health.ok,
    status_code: health.status_code,
    error: health.error,
  });
  if (!health.ok) {
    result.error = health.error;
    return result;
  }
  result.version = String(health.json.version || "");
  if (result.expected_version && result.version !== result.expected_version) {
    result.error = `artifact service version mismatch: expected ${result.expected_version}, found ${result.version || "unknown"}`;
    return result;
  }
  const capabilities = await fetchJson(
    normalized.serviceUrl,
    "capabilities",
    "GET",
    headerResult.headers,
    timeout_seconds || 10
  );
  result.checks.push({
    name: "capabilities",
    ok: capabilities.ok,
    status_code: capabilities.status_code,
    error: capabilities.error,
  });
  if (!capabilities.ok) {
    result.error = capabilities.error;
    return result;
  }
  result.capabilities = capabilities.json;
  result.license_warnings = capabilityWarnings(capabilities.json);
  result.status = "available";
  return result;
}

function findingConfidence(message, supplied) {
  if (["confirmed", "probable", "informational", "likely_false_positive"].includes(supplied)) {
    return supplied;
  }
  const lowered = message.toLowerCase();
  if (lowered.includes("c2pa") || lowered.includes("jumbf")) {
    return "confirmed";
  }
  if (lowered.includes("stylometry")) {
    return "informational";
  }
  return "probable";
}

function defaultAllowedFinding(message) {
  const prefix = "frontmatter value hit on ";
  const lowered = message.trim().toLowerCase();
  if (!lowered.startsWith(prefix)) {
    return false;
  }
  const field = lowered.slice(prefix.length).trim();
  return ARTIFACT_HYGIENE_MANIFEST.prose_frontmatter_fields.includes(field);
}

export function normalizeArtifactFindings(
  report,
  { allow_findings = [], use_default_allowlist = true } = {}
) {
  const exactAllowlist = new Set(allow_findings.map((item) => String(item)).filter(Boolean));
  const normalized = [];
  const rawFindings = Array.isArray(report.findings) ? report.findings : [];
  const confidences = Array.isArray(report.findings_confidence)
    ? report.findings_confidence
    : [];
  rawFindings.forEach((value, index) => {
    const message = String(value);
    const confidence = findingConfidence(message, confidences[index]);
    const classification = message.toLowerCase().includes("stylometry")
      ? "heuristic"
      : "deterministic";
    const allowed = exactAllowlist.has(message) ||
      (use_default_allowlist && defaultAllowedFinding(message));
    let disposition = allowed ? "allowed" : "advisory";
    if (
      classification === "deterministic" &&
      ["confirmed", "probable"].includes(confidence) &&
      !allowed
    ) {
      disposition = "actionable";
    }
    normalized.push({ message, classification, confidence, disposition });
  });
  const hits = [
    ...(Array.isArray(report.hits) ? report.hits : []),
    ...(Array.isArray(report.layer_a_hits) ? report.layer_a_hits : []),
  ];
  for (const hit of hits) {
    if (!hit || typeof hit !== "object" || Array.isArray(hit)) {
      continue;
    }
    const message = `layer-a [${hit.kind || "unknown"}] ${hit.label || hit.codepoint || "suspicious Unicode"} x${hit.count || 1}`;
    const confidence = findingConfidence(message, hit.confidence);
    normalized.push({
      message,
      classification: "deterministic",
      confidence,
      disposition: ["confirmed", "probable"].includes(confidence) ? "actionable" : "advisory",
    });
  }
  const stylometry = report.stylometry && typeof report.stylometry === "object"
    ? report.stylometry
    : {};
  if (typeof stylometry.score === "number" && stylometry.score >= 0.65) {
    normalized.push({
      message: `stylometry score ${stylometry.score.toFixed(2)}`,
      classification: "heuristic",
      confidence: String(stylometry.confidence_level || "informational"),
      disposition: "advisory",
    });
  }
  if (normalized.length === 0 && report.has_c2pa === true) {
    normalized.push({
      message: "C2PA provenance detected",
      classification: "deterministic",
      confidence: "confirmed",
      disposition: "actionable",
    });
  }
  if (normalized.length === 0 && report.has_ai_metadata === true) {
    normalized.push({
      message: "AI metadata detected",
      classification: "deterministic",
      confidence: "probable",
      disposition: "actionable",
    });
  }
  return normalized;
}

async function inspectArtifactBytes({
  artifactBytes,
  artifactName,
  probe,
  api_key_env,
  timeout_seconds,
  allow_findings,
  use_default_allowlist,
}) {
  const headerResult = requestHeaders(api_key_env, true);
  if (!headerResult.ok) {
    return { status: "blocked", error: headerResult.error };
  }
  const inspected = await fetchJson(
    probe.service_url,
    "inspect",
    "POST",
    headerResult.headers,
    timeout_seconds || 30,
    {
      file: artifactBytes.toString("base64"),
      name: artifactName,
    }
  );
  if (!inspected.ok) {
    return { status: "blocked", error: inspected.error };
  }
  const report = inspected.json.report;
  if (!report || typeof report !== "object" || Array.isArray(report)) {
    return { status: "blocked", error: "artifact service inspect response is missing report" };
  }
  const findings = normalizeArtifactFindings(report, {
    allow_findings: Array.isArray(allow_findings) ? allow_findings : [],
    use_default_allowlist: use_default_allowlist !== false,
  });
  const actionable = findings.some((finding) => finding.disposition === "actionable");
  return {
    status: actionable ? "actionable" : "clear",
    error: "",
    kind: String(inspected.json.kind || "unknown"),
    raw_report: report,
    raw_suspicious: Boolean(inspected.json.suspicious),
    findings,
    actionable,
  };
}

export async function inspectArtifact({
  artifact_path,
  default_cwd,
  service_url,
  api_key_env,
  expected_version,
  allow_remote,
  acknowledge_data_upload,
  timeout_seconds,
  allow_findings,
  use_default_allowlist,
} = {}) {
  const probe = await probeArtifactService({
    service_url,
    api_key_env,
    expected_version,
    allow_remote,
    timeout_seconds,
  });
  const result = {
    ...baseResult(probe.service_url),
    path: String(artifact_path || ""),
    probe,
    raw_report: {},
    raw_suspicious: false,
    findings: [],
    actionable: false,
    license_warnings: probe.license_warnings || [],
  };
  if (probe.status !== "available") {
    result.error = probe.error;
    return result;
  }
  if (probe.remote_service && acknowledge_data_upload !== true) {
    result.error = "remote artifact inspection requires acknowledge_data_upload=true";
    return result;
  }
  const input = String(artifact_path || "").trim();
  const resolved = path.resolve(default_cwd || process.cwd(), input);
  let stat;
  try {
    stat = fs.statSync(resolved);
  } catch {
    result.error = `artifact path is not a file: ${resolved}`;
    return result;
  }
  if (!stat.isFile()) {
    result.error = `artifact path is not a file: ${resolved}`;
    return result;
  }
  if (stat.size > MAX_ARTIFACT_BYTES) {
    result.error = `artifact exceeds the ${MAX_ARTIFACT_BYTES}-byte size limit`;
    return result;
  }
  const inspection = await inspectArtifactBytes({
    artifactBytes: fs.readFileSync(resolved),
    artifactName: path.basename(resolved),
    probe,
    api_key_env,
    timeout_seconds,
    allow_findings,
    use_default_allowlist,
  });
  if (inspection.status === "blocked") {
    result.error = inspection.error;
    return result;
  }
  return {
    ...result,
    path: resolved,
    ...inspection,
  };
}

function validateCleanPaths(inputPath, outputPath, defaultCwd, overwrite) {
  const sourceCandidate = path.resolve(defaultCwd || process.cwd(), String(inputPath || ""));
  let source;
  let sourceStat;
  try {
    source = fs.realpathSync(sourceCandidate);
    sourceStat = fs.statSync(source);
  } catch {
    return { ok: false, error: `artifact path is not a file: ${sourceCandidate}` };
  }
  if (!sourceStat.isFile()) {
    return { ok: false, error: `artifact path is not a file: ${source}` };
  }
  if (sourceStat.size > MAX_ARTIFACT_BYTES) {
    return { ok: false, error: `artifact exceeds the ${MAX_ARTIFACT_BYTES}-byte size limit` };
  }
  const destination = path.resolve(defaultCwd || process.cwd(), String(outputPath || ""));
  if (destination === source) {
    return { ok: false, error: "artifact cleaning requires a separate output path" };
  }
  let destinationStat = null;
  try {
    const linkStat = fs.lstatSync(destination);
    if (linkStat.isSymbolicLink()) {
      return { ok: false, error: "artifact output must not be a symbolic link" };
    }
    destinationStat = fs.statSync(destination);
  } catch (error) {
    if (!error || error.code !== "ENOENT") {
      return { ok: false, error: String(error && error.message ? error.message : error) };
    }
  }
  if (destinationStat) {
    if (destinationStat.dev === sourceStat.dev && destinationStat.ino === sourceStat.ino) {
      return { ok: false, error: "artifact cleaning requires a separate output file" };
    }
    if (!destinationStat.isFile()) {
      return { ok: false, error: "artifact output exists and is not a file" };
    }
    if (overwrite !== true) {
      return { ok: false, error: "artifact output already exists; set overwrite=true to replace it" };
    }
  }
  let parentStat;
  try {
    parentStat = fs.statSync(path.dirname(destination));
  } catch {
    return { ok: false, error: `artifact output directory does not exist: ${path.dirname(destination)}` };
  }
  if (!parentStat.isDirectory()) {
    return { ok: false, error: `artifact output directory does not exist: ${path.dirname(destination)}` };
  }
  return { ok: true, source, sourceStat, destination, error: "" };
}

function decodeBase64Strict(value) {
  if (typeof value !== "string" || value.length % 4 !== 0) {
    return null;
  }
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    return null;
  }
  return Buffer.from(value, "base64");
}

function assertSafeDestination(destination, sourceStat, overwrite) {
  let destinationStat;
  try {
    const linkStat = fs.lstatSync(destination);
    if (linkStat.isSymbolicLink()) {
      throw new Error("artifact output became a symbolic link before replacement");
    }
    destinationStat = fs.statSync(destination);
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return;
    }
    throw error;
  }
  if (destinationStat.dev === sourceStat.dev && destinationStat.ino === sourceStat.ino) {
    throw new Error("artifact output became the input file before replacement");
  }
  if (!destinationStat.isFile()) {
    throw new Error("artifact output became a non-file before replacement");
  }
  if (overwrite !== true) {
    throw new Error("artifact output appeared before replacement; set overwrite=true to replace it");
  }
}

function writeAtomic(destination, payload, sourceStat, overwrite) {
  const temporary = path.join(
    path.dirname(destination),
    `.${path.basename(destination)}.${process.pid}.${crypto.randomBytes(8).toString("hex")}.tmp`
  );
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, "wx", sourceStat.mode & 0o777);
    fs.writeFileSync(descriptor, payload);
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    assertSafeDestination(destination, sourceStat, overwrite);
    fs.renameSync(temporary, destination);
    let parentDescriptor;
    try {
      parentDescriptor = fs.openSync(path.dirname(destination), "r");
      fs.fsyncSync(parentDescriptor);
    } finally {
      if (parentDescriptor !== undefined) {
        fs.closeSync(parentDescriptor);
      }
    }
  } finally {
    if (descriptor !== undefined) {
      fs.closeSync(descriptor);
    }
    try {
      fs.unlinkSync(temporary);
    } catch (error) {
      if (!error || error.code !== "ENOENT") {
        throw error;
      }
    }
  }
}

export async function cleanArtifact({
  artifact_path,
  output_path,
  default_cwd,
  service_url,
  api_key_env,
  expected_version,
  allow_remote,
  acknowledge_data_upload,
  timeout_seconds,
  allow_findings,
  use_default_allowlist,
  confirm_authorized,
  overwrite,
  keep_non_ai_metadata,
  nfkc,
  aggressive_homoglyphs,
} = {}) {
  const result = {
    ...baseResult(String(service_url || DEFAULT_ARTIFACT_SERVICE_URL)),
    input_path: String(artifact_path || ""),
    output_path: String(output_path || ""),
    input_inspection: {},
    post_clean_inspection: {},
    clean_report: {},
    written: false,
    actionable: false,
    service_clean_called: false,
  };
  if (confirm_authorized !== true) {
    result.error = "artifact cleaning requires confirm_authorized=true";
    return result;
  }
  const paths = validateCleanPaths(artifact_path, output_path, default_cwd, overwrite);
  if (!paths.ok) {
    result.error = paths.error;
    return result;
  }
  const probe = await probeArtifactService({
    service_url,
    api_key_env,
    expected_version,
    allow_remote,
    timeout_seconds,
  });
  result.service_url = probe.service_url;
  result.probe = probe;
  result.license_warnings = probe.license_warnings || [];
  if (probe.status !== "available") {
    result.error = probe.error;
    return result;
  }
  if (probe.remote_service && acknowledge_data_upload !== true) {
    result.error = "remote artifact cleaning requires acknowledge_data_upload=true";
    return result;
  }
  const sourceBytes = fs.readFileSync(paths.source);
  const inputInspection = await inspectArtifactBytes({
    artifactBytes: sourceBytes,
    artifactName: path.basename(paths.source),
    probe,
    api_key_env,
    timeout_seconds,
    allow_findings,
    use_default_allowlist,
  });
  result.input_inspection = inputInspection;
  if (inputInspection.status === "blocked") {
    result.error = inputInspection.error;
    return result;
  }
  if (!inputInspection.actionable) {
    const postInspection = await inspectArtifactBytes({
      artifactBytes: sourceBytes,
      artifactName: path.basename(paths.destination),
      probe,
      api_key_env,
      timeout_seconds,
      allow_findings,
      use_default_allowlist,
    });
    result.post_clean_inspection = postInspection;
    result.clean_report = {
      skipped: true,
      reason: "no actionable deterministic findings after normalization",
    };
    if (postInspection.status === "blocked") {
      result.error = postInspection.error;
      return result;
    }
    try {
      writeAtomic(paths.destination, sourceBytes, paths.sourceStat, overwrite);
    } catch (error) {
      result.error = String(error && error.message ? error.message : error);
      return result;
    }
    return {
      ...result,
      status: "clean",
      input_path: paths.source,
      output_path: paths.destination,
      written: true,
      actionable: false,
    };
  }
  const headerResult = requestHeaders(api_key_env, true);
  if (!headerResult.ok) {
    result.error = headerResult.error;
    return result;
  }
  result.service_clean_called = true;
  const cleaned = await fetchJson(
    probe.service_url,
    "clean",
    "POST",
    headerResult.headers,
    timeout_seconds || 30,
    {
      file: sourceBytes.toString("base64"),
      name: path.basename(paths.source),
      options: {
        keep_non_ai_metadata: keep_non_ai_metadata === true,
        nfkc: nfkc === true,
        aggressive_homoglyphs: aggressive_homoglyphs === true,
      },
    }
  );
  if (!cleaned.ok) {
    result.error = cleaned.error;
    return result;
  }
  const cleanedBytes = decodeBase64Strict(cleaned.json.cleaned);
  if (cleanedBytes === null) {
    result.error = "artifact service clean response contains invalid base64";
    return result;
  }
  if (cleanedBytes.byteLength > MAX_ARTIFACT_BYTES) {
    result.error = `cleaned artifact exceeds the ${MAX_ARTIFACT_BYTES}-byte size limit`;
    return result;
  }
  const postInspection = await inspectArtifactBytes({
    artifactBytes: cleanedBytes,
    artifactName: path.basename(paths.destination),
    probe,
    api_key_env,
    timeout_seconds,
    allow_findings,
    use_default_allowlist,
  });
  result.post_clean_inspection = postInspection;
  result.clean_report = cleaned.json.report && typeof cleaned.json.report === "object"
    ? cleaned.json.report
    : {};
  if (postInspection.status === "blocked") {
    result.error = postInspection.error;
    return result;
  }
  try {
    writeAtomic(paths.destination, cleanedBytes, paths.sourceStat, overwrite);
  } catch (error) {
    result.error = String(error && error.message ? error.message : error);
    return result;
  }
  return {
    ...result,
    status: postInspection.actionable ? "residual" : "clean",
    input_path: paths.source,
    output_path: paths.destination,
    written: true,
    actionable: postInspection.actionable,
  };
}

export function formatArtifactProbe(result) {
  const prefix = result.status === "available" ? "[OK]" : "[FAIL]";
  const lines = [
    `${prefix} Artifact service probe ${result.status}.`,
    `service: ${result.service_url || "unset"}`,
    `version: ${result.version || "unknown"}`,
    `remote service: ${result.remote_service ? "yes" : "no"}`,
    "evidence: service output is material, not verification evidence.",
  ];
  for (const warning of result.license_warnings || []) {
    lines.push(`warning: ${warning}`);
  }
  if (result.error) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

export function formatArtifactInspection(result) {
  const prefix = result.status === "blocked" ? "[FAIL]" : "[OK]";
  const lines = [
    `${prefix} Artifact inspection ${result.status}.`,
    `path: ${result.path || "unset"}`,
    `actionable: ${result.actionable ? "yes" : "no"}`,
    "evidence: service output is material until a caller records this command through verify_run.",
  ];
  for (const finding of result.findings || []) {
    lines.push(
      `${finding.disposition}: [${finding.classification}/${finding.confidence}] ${finding.message}`
    );
  }
  for (const warning of result.license_warnings || []) {
    lines.push(`warning: ${warning}`);
  }
  if (result.error) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}

export function formatArtifactClean(result) {
  const prefix = result.status === "blocked" ? "[FAIL]" : "[OK]";
  const lines = [
    `${prefix} Artifact clean ${result.status}.`,
    `input: ${result.input_path || "unset"}`,
    `output: ${result.output_path || "unset"}`,
    `written: ${result.written ? "yes" : "no"}`,
    `residual actionable findings: ${result.actionable ? "yes" : "no"}`,
    "evidence: service output is material until a caller records this command through verify_run.",
  ];
  for (const finding of result.post_clean_inspection?.findings || []) {
    lines.push(
      `${finding.disposition}: [${finding.classification}/${finding.confidence}] ${finding.message}`
    );
  }
  for (const warning of result.license_warnings || []) {
    lines.push(`warning: ${warning}`);
  }
  if (result.error) {
    lines.push(`error: ${result.error}`);
  }
  return lines.join("\n");
}
