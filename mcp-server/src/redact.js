// Single secret-redaction choke point for every recorded output surface
// (verify_run, execution runs, host-cli, fanout worker output). Keep this the
// only definition; import it everywhere output is captured or persisted, so a
// gap on one surface cannot leak what another surface scrubs. Behavioral mirror
// of redact_sensitive_output in scripts/mythify.py.

export const REDACTED_SECRET = "[REDACTED]";

export function redactSensitiveOutput(text) {
  let value = String(text == null ? "" : text);
  if (value === "") {
    return "";
  }
  value = value.replace(
    /\b(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/\-=]+)/gi,
    `$1${REDACTED_SECRET}`
  );
  value = value.replace(
    /\b([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)[A-Za-z0-9_-]*\s*=\s*)([^\s,;]+)/gi,
    `$1${REDACTED_SECRET}`
  );
  value = value.replace(
    /(["']?[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|credential)[A-Za-z0-9_-]*["']?\s*:\s*)(["'])([^"']+)(["'])/gi,
    `$1$2${REDACTED_SECRET}$4`
  );
  value = value.replace(
    /\b((?:authorization|x-api-key|api-key|api_key|token|secret|password|passwd|credential)\s*:\s*)([^\s,;}]+)/gi,
    `$1${REDACTED_SECRET}`
  );
  value = value.replace(
    /\b(sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|npm_[A-Za-z0-9_-]{20,})\b/g,
    REDACTED_SECRET
  );
  return value;
}
