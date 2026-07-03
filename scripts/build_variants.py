#!/usr/bin/env python3
"""Generate CLAUDE.md, AGENTS.md, and .cursorrules from protocol/PROTOCOL.md.

Each generated file is the canonical protocol body prefixed with a header line
marking it as generated, followed by a blank line. The embedded
PROTOCOL_SOURCE_SHA256 constant in scripts/mythify.py is rewritten to the new
digest in the same run, so a protocol edit cannot leave the handshake stale.
The script is idempotent: running it twice produces byte-identical output.
Standard library only.
"""

import hashlib
import re
import sys
from pathlib import Path

HEADER = (
    "<!-- Generated from protocol/PROTOCOL.md by scripts/build_variants.py. "
    "Edit the source, then rebuild. -->"
)
HASH_HEADER = "<!-- Mythify protocol-sha256: {0} -->"
CLI_HASH_PATTERN = re.compile(r'^PROTOCOL_SOURCE_SHA256 = "[0-9a-f]{64}"$', re.M)

TARGETS = ("CLAUDE.md", "AGENTS.md", ".cursorrules")


def sync_cli_hash_constant(repo_root, digest):
    cli_path = repo_root / "scripts" / "mythify.py"
    text = cli_path.read_text(encoding="utf-8")
    replacement = 'PROTOCOL_SOURCE_SHA256 = "{0}"'.format(digest)
    updated, count = CLI_HASH_PATTERN.subn(replacement, text, count=1)
    if count != 1:
        print(
            "[FAIL] PROTOCOL_SOURCE_SHA256 constant not found in scripts/mythify.py",
            file=sys.stderr,
        )
        return False
    if updated != text:
        cli_path.write_text(updated, encoding="utf-8")
        print("[OK] Updated PROTOCOL_SOURCE_SHA256 in scripts/mythify.py")
    return True


def main():
    repo_root = Path(__file__).resolve().parent.parent
    source = repo_root / "protocol" / "PROTOCOL.md"
    if not source.is_file():
        print("[FAIL] Protocol source not found: " + str(source), file=sys.stderr)
        return 1
    body = source.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    content = HEADER + "\n" + HASH_HEADER.format(digest) + "\n\n" + body
    written = []
    for name in TARGETS:
        target = repo_root / name
        target.write_text(content, encoding="utf-8")
        written.append(name)
    if not sync_cli_hash_constant(repo_root, digest):
        return 1
    print("[OK] Wrote " + ", ".join(written) + " from protocol/PROTOCOL.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
