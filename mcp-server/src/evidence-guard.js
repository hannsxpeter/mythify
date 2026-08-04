// Advisory evidence-quality guards, mirroring scripts/mythify_evidence_guard.py.
//
// The strict gate proves that a command exited 0. These helpers watch the
// cheap ways that proof detaches from the work it stands for: a verifier that
// always passes, a test runner that collected nothing, and a session running
// with a legacy gate opt-out active. Everything here is advisory material for
// warnings and attention items; nothing blocks completion, and nothing
// upgrades or downgrades recorded evidence.

import crypto from "node:crypto";

const NOOP_COMMANDS = new Set(["true", ":", "exit 0"]);
const NOOP_PRINT_PREFIXES = new Set(["echo", "printf"]);
const SHELL_OPERATORS = ["|", "&&", "||", ";", ">", "<"];
const ZERO_TEST_PATTERNS = [
  "ran 0 tests",
  "no tests ran",
  "collected 0 items",
  "no tests collected",
  "no test files",
  "0 passing",
];
const FALSE_ENV_VALUES = new Set(["0", "false", "no", "off"]);
export const LEGACY_OPT_OUTS = [
  ["MYTHIFY_REQUIRE_VERIFIED_STEP", "step completion accepts prose-only evidence"],
  ["MYTHIFY_REQUIRE_HUMAN_INPUT", "HITL tickets resolve without the human's words"],
];

function normalized(text) {
  return String(text || "").split(/\s+/).filter(Boolean).join(" ");
}

export function noopVerifierReason(command) {
  const text = normalized(command).toLowerCase();
  if (!text) {
    return null;
  }
  if (NOOP_COMMANDS.has(text)) {
    return "the command always exits 0";
  }
  const firstWord = text.split(" ", 1)[0];
  if (
    NOOP_PRINT_PREFIXES.has(firstWord) &&
    !SHELL_OPERATORS.some((operator) => text.includes(operator))
  ) {
    return "the command only prints and exits 0";
  }
  return null;
}

export function trivialPassReason(record) {
  if (!record || record.kind !== "executed" || record.verified !== true) {
    return null;
  }
  const noop = noopVerifierReason(record.command);
  if (noop) {
    return noop;
  }
  const output = `${record.stdout_tail || ""}\n${record.stderr_tail || ""}`.toLowerCase();
  for (const pattern of ZERO_TEST_PATTERNS) {
    if (output.includes(pattern)) {
      return `the run reported '${pattern}'`;
    }
  }
  return null;
}

// 1-based indexes of chained records whose prev_sha256 does not match. Each
// chained record carries the sha256 of the raw line before it, so an edited,
// inserted, or deleted line breaks the next record's link. The first line is
// never judged (its predecessor may live in a compaction archive), and
// records without prev_sha256 are legacy and stay silent.
export function ledgerChainBreaks(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "");
  const breaks = [];
  for (let index = 1; index < lines.length; index += 1) {
    let record;
    try {
      record = JSON.parse(lines[index]);
    } catch {
      continue;
    }
    const prev = record && typeof record === "object" ? record.prev_sha256 : null;
    if (prev === null || prev === undefined) {
      continue;
    }
    const expected = crypto.createHash("sha256").update(lines[index - 1], "utf8").digest("hex");
    if (prev !== expected) {
      breaks.push(index + 1);
    }
  }
  return breaks;
}

export function activeLegacyOptOuts(environ) {
  const env = environ || {};
  const active = [];
  for (const [name, effect] of LEGACY_OPT_OUTS) {
    const raw = String(env[name] || "").trim().toLowerCase();
    if (FALSE_ENV_VALUES.has(raw)) {
      active.push({ name, effect });
    }
  }
  if (String(env.MYTHIFY_DISABLE_RUN || "").trim() === "1") {
    active.push({
      name: "MYTHIFY_DISABLE_RUN",
      effect: "verify run refuses to execute; only attested claims can be recorded",
    });
  }
  return active;
}
