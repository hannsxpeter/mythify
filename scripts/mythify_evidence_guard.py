"""Advisory evidence-quality guards for Mythify.

The strict gate proves that a command exited 0. These helpers watch the cheap
ways that proof detaches from the work it stands for: a verifier that always
passes, a test runner that collected nothing, a session running with a legacy
gate opt-out active, and a ledger whose chained lines no longer hash together.
Everything here is advisory material for warnings and attention items; nothing
blocks completion, and nothing upgrades or downgrades recorded evidence.
"""

import hashlib
import json

# Commands whose whole normalized text always exits 0 without checking anything.
NOOP_COMMANDS = frozenset({"true", ":", "exit 0"})
# Leading words that only print. They flag a no-op only when the command has no
# shell operator, so `echo start && pytest` or `echo x | grep y` never trips.
NOOP_PRINT_PREFIXES = ("echo", "printf")
SHELL_OPERATORS = ("|", "&&", "||", ";", ">", "<")
# Test-runner output that means the green exit code exercised zero tests.
ZERO_TEST_PATTERNS = (
    "ran 0 tests",
    "no tests ran",
    "collected 0 items",
    "no tests collected",
    "no test files",
    "0 passing",
)
FALSE_ENV_VALUES = ("0", "false", "no", "off")
# Gate opt-outs an agent can set for itself. MYTHIFY_DISABLE_RUN is listed even
# though it fails closed: a session that cannot execute checks is worth naming.
LEGACY_OPT_OUTS = (
    (
        "MYTHIFY_REQUIRE_VERIFIED_STEP",
        "step completion accepts prose-only evidence",
    ),
    (
        "MYTHIFY_REQUIRE_HUMAN_INPUT",
        "HITL tickets resolve without the human's words",
    ),
)


def _normalized(text):
    return " ".join(str(text or "").split())


def noop_verifier_reason(command):
    """Why COMMAND can never fail, or None when it looks like a real check."""
    normalized = _normalized(command).lower()
    if not normalized:
        return None
    if normalized in NOOP_COMMANDS:
        return "the command always exits 0"
    first_word = normalized.split(" ", 1)[0]
    if first_word in NOOP_PRINT_PREFIXES and not any(
        operator in normalized for operator in SHELL_OPERATORS
    ):
        return "the command only prints and exits 0"
    return None


def trivial_pass_reason(record):
    """Why a passing executed RECORD proves nothing, or None when it holds."""
    if record.get("kind") != "executed" or record.get("verified") is not True:
        return None
    noop = noop_verifier_reason(record.get("command"))
    if noop:
        return noop
    output = "{0}\n{1}".format(
        record.get("stdout_tail") or "", record.get("stderr_tail") or ""
    ).lower()
    for pattern in ZERO_TEST_PATTERNS:
        if pattern in output:
            return "the run reported '{0}'".format(pattern)
    return None


def ledger_chain_breaks(text):
    """1-based indexes of chained records whose prev_sha256 does not match.

    Each chained record carries the sha256 of the raw line before it, so an
    edited, inserted, or deleted line breaks the next record's link. The first
    line is never judged (its predecessor may live in a compaction archive),
    and records without prev_sha256 are legacy and stay silent.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    breaks = []
    for index in range(1, len(lines)):
        try:
            record = json.loads(lines[index])
        except ValueError:
            continue
        prev = record.get("prev_sha256") if isinstance(record, dict) else None
        if prev is None:
            continue
        expected = hashlib.sha256(lines[index - 1].encode("utf-8")).hexdigest()
        if prev != expected:
            breaks.append(index + 1)
    return breaks


def active_legacy_opt_outs(environ):
    """Legacy gate opt-outs currently active in ENVIRON, oldest contract first."""
    active = []
    for name, effect in LEGACY_OPT_OUTS:
        raw = str((environ or {}).get(name, "")).strip().lower()
        if raw in FALSE_ENV_VALUES:
            active.append({"name": name, "effect": effect})
    if str((environ or {}).get("MYTHIFY_DISABLE_RUN", "")).strip() == "1":
        active.append({
            "name": "MYTHIFY_DISABLE_RUN",
            "effect": "verify run refuses to execute; only attested claims can be recorded",
        })
    return active
