#!/usr/bin/env python3
"""Generate CLAUDE.md, AGENTS.md, and .cursorrules from protocol/PROTOCOL.md.

Each generated file is the canonical protocol body prefixed with a header line
marking it as generated, followed by a blank line. The script is idempotent:
running it twice produces byte-identical output. Standard library only.
"""

import hashlib
import sys
from pathlib import Path

HEADER = (
    "<!-- Generated from protocol/PROTOCOL.md by scripts/build_variants.py. "
    "Edit the source, then rebuild. -->"
)
HASH_HEADER = "<!-- Mythify protocol-sha256: {0} -->"

TARGETS = ("CLAUDE.md", "AGENTS.md", ".cursorrules")


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
    print("[OK] Wrote " + ", ".join(written) + " from protocol/PROTOCOL.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
