"""Protocol handshake check for the Mythify CLI.

The protocol text is a frozen node: a rule the optimizer being graded must
never tune silently. It is pinned to a digest embedded here; `protocol check`
compares the source protocol and every generated drop-in copy against it and
fails loudly on drift. scripts/build_variants.py rewrites
PROTOCOL_SOURCE_SHA256 when the protocol source legitimately changes.
"""

import hashlib
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PROTOCOL_SOURCE_SHA256 = "6b2a8d9fd34e0ed4f2c3d5bbba5e3473719b70e65da3fa206ada4751e27514b8"
PROTOCOL_HASH_PREFIX = "<!-- Mythify protocol-sha256: "
PROTOCOL_COPY_CANDIDATES = ("CLAUDE.md", "AGENTS.md", ".cursorrules")


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
    return [cwd / name for name in PROTOCOL_COPY_CANDIDATES if (cwd / name).is_file()]


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


def protocol_copy_check(path):
    path = Path(path)
    result = {
        "kind": "copy",
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
    if actual is None:
        result["status"] = "missing_header"
    elif actual != PROTOCOL_SOURCE_SHA256:
        result["status"] = "drift"
    elif sha256_text(extract_protocol_body(text)) != PROTOCOL_SOURCE_SHA256:
        # The header matches but the body was edited or truncated.
        result["status"] = "body_drift"
    return result


def format_protocol_check_failure(result):
    path = result["path"]
    status = result["status"]
    if status == "missing_file":
        return "[FAIL] Protocol file not found: {0}".format(path)
    if status == "missing_header":
        return (
            "[FAIL] Protocol handshake missing from {0}. Regenerate with "
            "scripts/build_variants.py or copy a current protocol variant."
        ).format(path)
    if status == "body_drift":
        return (
            "[FAIL] Protocol body drift in {0}: the header matches but the body "
            "differs from the protocol it names. Regenerate with "
            "scripts/build_variants.py."
        ).format(path)
    if status == "drift":
        return (
            "[FAIL] Protocol handshake drift in {0}: expected {1}, found {2}. "
            "Regenerate variants and copy the matching CLI."
        ).format(path, short_hash(result["expected"]), short_hash(result["actual"]))
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
                "containing CLAUDE.md, AGENTS.md, or .cursorrules."
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
    return 0
