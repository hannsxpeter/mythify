#!/usr/bin/env python3
"""Generate CLAUDE.md, AGENTS.md, and .cursorrules from protocol/PROTOCOL.md.

Each generated file is the canonical protocol body prefixed with a header line
marking it as generated, followed by a blank line. The embedded
PROTOCOL_SOURCE_SHA256 constant in scripts/mythify_protocol.py is rewritten to
the new digest in the same run, so a protocol edit cannot leave the handshake
stale.
The script is idempotent: running it twice produces byte-identical output.
Standard library only.
"""

import hashlib
import re
import sys
from pathlib import Path

from mythify_protocol_profiles import load_profile_manifest, render_profile_body

HEADER = (
    "<!-- Generated from protocol/PROTOCOL.md by scripts/build_variants.py. "
    "Edit the source, then rebuild. -->"
)
HASH_HEADER = "<!-- Mythify protocol-sha256: {0} -->"
PROFILE_HEADER = "<!-- Mythify protocol-profile: {0} -->"
PROFILE_BODY_HEADER = "<!-- Mythify protocol-body-sha256: {0} -->"
CLI_HASH_PATTERN = re.compile(r'^PROTOCOL_SOURCE_SHA256 = "[0-9a-f]{64}"$', re.M)

TARGETS = ("CLAUDE.md", "AGENTS.md", ".cursorrules")


def sync_cli_hash_constant(repo_root, digest):
    cli_path = repo_root / "scripts" / "mythify_protocol.py"
    text = cli_path.read_text(encoding="utf-8")
    replacement = 'PROTOCOL_SOURCE_SHA256 = "{0}"'.format(digest)
    updated, count = CLI_HASH_PATTERN.subn(replacement, text, count=1)
    if count != 1:
        print(
            "[FAIL] PROTOCOL_SOURCE_SHA256 constant not found in scripts/mythify_protocol.py",
            file=sys.stderr,
        )
        return False
    if updated != text:
        cli_path.write_text(updated, encoding="utf-8")
        print("[OK] Updated PROTOCOL_SOURCE_SHA256 in scripts/mythify_protocol.py")
    return True


def main():
    repo_root = Path(__file__).resolve().parent.parent
    source = repo_root / "protocol" / "PROTOCOL.md"
    if not source.is_file():
        print("[FAIL] Protocol source not found: " + str(source), file=sys.stderr)
        return 1
    body = source.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest = load_profile_manifest(repo_root)
    written = []
    for name in TARGETS:
        target = repo_root / name
        profile_body = render_profile_body(body, "full", manifest)
        body_digest = hashlib.sha256(profile_body.encode("utf-8")).hexdigest()
        content = (
            HEADER
            + "\n"
            + HASH_HEADER.format(digest)
            + "\n"
            + PROFILE_HEADER.format("full")
            + "\n"
            + PROFILE_BODY_HEADER.format(body_digest)
            + "\n\n"
            + profile_body
        )
        target.write_text(content, encoding="utf-8")
        written.append(name)
    variants_dir = repo_root / "protocol" / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    thin_body = render_profile_body(body, "thin", manifest)
    thin_digest = hashlib.sha256(thin_body.encode("utf-8")).hexdigest()
    thin_content = (
        HEADER
        + "\n"
        + HASH_HEADER.format(digest)
        + "\n"
        + PROFILE_HEADER.format("thin")
        + "\n"
        + PROFILE_BODY_HEADER.format(thin_digest)
        + "\n\n"
        + thin_body
    )
    for name in TARGETS:
        target = variants_dir / (name + ".thin")
        target.write_text(thin_content, encoding="utf-8")
        written.append(str(target.relative_to(repo_root)))
    if not sync_cli_hash_constant(repo_root, digest):
        return 1
    print("[OK] Wrote " + ", ".join(written) + " from protocol/PROTOCOL.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
