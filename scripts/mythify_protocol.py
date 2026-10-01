"""Protocol handshake check for the Mythify CLI.

The protocol text is a frozen node: a rule the optimizer being graded must
never tune silently. It is pinned to a digest embedded here; `protocol check`
compares the source protocol and every drop-in against it and fails loudly on
drift. AGENTS.md is the canonical full copy. CLAUDE.md is accepted as a
pointer whose `@AGENTS.md` line imports AGENTS.md, or as a legacy full copy. A
leftover .cursorrules is a legacy full copy Mythify no longer generates; it is
checked and labeled as such. scripts/build_variants.py rewrites
PROTOCOL_SOURCE_SHA256 when the protocol source legitimately changes.
"""

import hashlib
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PROTOCOL_SOURCE_SHA256 = "851bd9826d6cd7ea7cd820923438ae5e4befca1c17b95acf4496c20ba79926cd"
PROTOCOL_HASH_PREFIX = "<!-- Mythify protocol-sha256: "
CANONICAL_COPY_NAME = "AGENTS.md"
POINTER_IMPORT_LINE = "@" + CANONICAL_COPY_NAME
PROTOCOL_COPY_CANDIDATES = (CANONICAL_COPY_NAME, "CLAUDE.md")
LEGACY_COPY_NAMES = (".cursorrules",)


def fail(message):
    sys.stderr.write(message + "\n")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(digest):
    if not digest:
        return "missing"
    return digest[:12]


def extract_protocol_copy_hash(text):
    for line in text.splitlines()[:8]:
        stripped = line.strip()
        if stripped.startswith(PROTOCOL_HASH_PREFIX) and stripped.endswith("-->"):
            return stripped[len(PROTOCOL_HASH_PREFIX):-3].strip()
    return None


def extract_protocol_body(text):
    """Return the protocol body that follows the generated header block."""
    marker = "\n\n"
    if marker not in text:
        return ""
    return text.split(marker, 1)[1]


def source_protocol_path():
    return REPO_ROOT / "protocol" / "PROTOCOL.md"


def default_protocol_check_paths():
    cwd = Path.cwd()
    names = PROTOCOL_COPY_CANDIDATES + LEGACY_COPY_NAMES
    return [cwd / name for name in names if (cwd / name).is_file()]


def is_pointer(text):
    return any(line.strip() == POINTER_IMPORT_LINE for line in text.splitlines())


def protocol_source_check():
    path = source_protocol_path()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    actual = sha256_text(text)
    return {
        "kind": "source",
        "path": str(path),
        "expected": PROTOCOL_SOURCE_SHA256,
        "actual": actual,
        "status": "ok" if actual == PROTOCOL_SOURCE_SHA256 else "drift",
    }


def protocol_pointer_check(path, result):
    """Check a pointer drop-in through the AGENTS.md it imports."""
    target = path.parent / CANONICAL_COPY_NAME
    result["kind"] = "pointer"
    result["target"] = str(target)
    target_result = protocol_copy_check(target)
    result["actual"] = target_result["actual"]
    result["target_status"] = target_result["status"]
    if target_result["status"] == "missing_file":
        result["status"] = "missing_target"
    elif target_result["status"] != "ok":
        result["status"] = "target_drift"
    return result


def protocol_copy_check(path):
    path = Path(path)
    legacy = path.name in LEGACY_COPY_NAMES
    result = {
        "kind": "legacy_copy" if legacy else "copy",
        "path": str(path),
        "expected": PROTOCOL_SOURCE_SHA256,
        "actual": None,
        "status": "ok",
    }
    if not path.is_file():
        result["status"] = "missing_file"
        return result
    text = path.read_text(encoding="utf-8")
    actual = extract_protocol_copy_hash(text)
    result["actual"] = actual
    if actual is None and not legacy and path.name != CANONICAL_COPY_NAME and is_pointer(text):
        return protocol_pointer_check(path, result)
    if actual is None:
        result["status"] = "missing_header"
    elif actual != PROTOCOL_SOURCE_SHA256:
        result["status"] = "drift"
    elif sha256_text(extract_protocol_body(text)) != PROTOCOL_SOURCE_SHA256:
        # The header matches but the body was edited or truncated.
        result["status"] = "body_drift"
    return result


REFRESH_HINT = (
    "Copy the AGENTS.md that matches this CLI, or regenerate it with "
    "scripts/build_variants.py."
)
LEGACY_NOTE = (
    "Mythify no longer generates this file; AGENTS.md is the canonical "
    "drop-in. Delete it, or replace it with the current AGENTS.md."
)


def format_protocol_check_failure(result):
    path = result["path"]
    status = result["status"]
    if status == "missing_file":
        return "[FAIL] Protocol file not found: {0}".format(path)
    if result.get("kind") == "legacy_copy":
        return "[FAIL] Legacy protocol copy {0} is stale ({1}). {2}".format(
            path, status, LEGACY_NOTE
        )
    if status == "missing_target":
        return (
            "[FAIL] {0} imports AGENTS.md, but {1} does not exist. {2}"
        ).format(path, result["target"], REFRESH_HINT)
    if status == "target_drift":
        return (
            "[FAIL] {0} imports {1}, which failed its own check ({2}). {3}"
        ).format(path, result["target"], result["target_status"], REFRESH_HINT)
    if status == "missing_header":
        shape = "a full protocol copy"
        if Path(path).name != CANONICAL_COPY_NAME:
            shape += " or a pointer with an {0} line".format(POINTER_IMPORT_LINE)
        return "[FAIL] Protocol handshake missing from {0}: it is not {1}. {2}".format(
            path, shape, REFRESH_HINT
        )
    if status == "body_drift":
        return (
            "[FAIL] Protocol body drift in {0}: the header matches but the body "
            "differs from the protocol it names. {1}"
        ).format(path, REFRESH_HINT)
    if status == "drift":
        return (
            "[FAIL] Protocol handshake drift in {0}: expected {1}, found {2}. {3}"
        ).format(path, short_hash(result["expected"]), short_hash(result["actual"]), REFRESH_HINT)
    return "[FAIL] Protocol check failed for {0}: {1}".format(path, status)


def cmd_protocol_check(args, _state):
    explicit_paths = [Path(item) for item in args.paths]
    results = []
    if explicit_paths:
        results.extend(protocol_copy_check(path) for path in explicit_paths)
    else:
        source_result = protocol_source_check()
        if source_result is not None:
            results.append(source_result)
        results.extend(protocol_copy_check(path) for path in default_protocol_check_paths())

    if not results:
        output = {
            "status": "no_files",
            "expected": PROTOCOL_SOURCE_SHA256,
            "checked": [],
        }
        if args.json_output:
            print(json.dumps(output, indent=2))
        else:
            fail(
                "[FAIL] No protocol files found. Pass PATH or run from a directory "
                "containing AGENTS.md or CLAUDE.md."
            )
        return 1

    failures = [item for item in results if item["status"] != "ok"]
    output = {
        "status": "ok" if not failures else "failed",
        "expected": PROTOCOL_SOURCE_SHA256,
        "checked": results,
    }
    if args.json_output:
        print(json.dumps(output, indent=2))
        if failures:
            return 1
    elif failures:
        for failure in failures:
            fail(format_protocol_check_failure(failure))
        return 1
    else:
        names = ", ".join(result["path"] for result in results)
        print(
            "[OK] Protocol handshake verified ({0}) for: {1}".format(
                short_hash(PROTOCOL_SOURCE_SHA256), names
            )
        )
        for result in results:
            if result["kind"] == "legacy_copy":
                fail(
                    "[WARN] Legacy protocol copy {0} is current, but Mythify no "
                    "longer generates it; AGENTS.md is the canonical drop-in, so "
                    "this file will drift on the next protocol change.".format(result["path"])
                )
    return 0
