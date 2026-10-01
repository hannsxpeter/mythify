#!/usr/bin/env python3
"""Generate the protocol drop-in files from protocol/PROTOCOL.md.

AGENTS.md is the canonical drop-in: a generated header line, a
protocol-sha256 handshake line, a blank line, then the full protocol body.
CLAUDE.md is a short pointer: the generated header line, a blank line, an
`@AGENTS.md` import line, and one sentence naming AGENTS.md as the home of the
protocol. The embedded PROTOCOL_SOURCE_SHA256 constant in
scripts/mythify_protocol.py is rewritten to the new digest in the same run, so
a protocol edit cannot leave the handshake stale.

The script is idempotent: running it twice produces byte-identical output.
With --check it writes nothing and exits 1 when any generated file is out of
date. Standard library only.
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

HEADER = (
    "<!-- Generated from protocol/PROTOCOL.md by scripts/build_variants.py. "
    "Edit the source, then rebuild. -->"
)
HASH_HEADER = "<!-- Mythify protocol-sha256: {0} -->"
POINTER_LINE = "@AGENTS.md"
POINTER_SENTENCE = (
    "The Mythify protocol lives in AGENTS.md; the line above imports it, so "
    "edit protocol/PROTOCOL.md and rebuild instead of editing either file."
)
CLI_HASH_PATTERN = re.compile(r'^PROTOCOL_SOURCE_SHA256 = "[0-9a-f]{64}"$', re.M)
CLI_MODULE = Path("scripts") / "mythify_protocol.py"


def full_copy(body, digest):
    return HEADER + "\n" + HASH_HEADER.format(digest) + "\n\n" + body


def pointer_copy():
    return HEADER + "\n\n" + POINTER_LINE + "\n\n" + POINTER_SENTENCE + "\n"


def expected_outputs(repo_root):
    """Map each generated path to its expected text, or raise ValueError."""
    source = repo_root / "protocol" / "PROTOCOL.md"
    if not source.is_file():
        raise ValueError("Protocol source not found: " + str(source))
    body = source.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    cli_path = repo_root / CLI_MODULE
    cli_text = cli_path.read_text(encoding="utf-8")
    replacement = 'PROTOCOL_SOURCE_SHA256 = "{0}"'.format(digest)
    cli_updated, count = CLI_HASH_PATTERN.subn(replacement, cli_text, count=1)
    if count != 1:
        raise ValueError("PROTOCOL_SOURCE_SHA256 constant not found in " + CLI_MODULE.as_posix())
    return {
        repo_root / "AGENTS.md": full_copy(body, digest),
        repo_root / "CLAUDE.md": pointer_copy(),
        cli_path: cli_updated,
    }


def stale_paths(outputs):
    stale = []
    for path, text in outputs.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            stale.append(path)
    return stale


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Write nothing; exit 1 when any generated file is out of date.",
    )
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    try:
        outputs = expected_outputs(repo_root)
    except (OSError, ValueError) as exc:
        print("[FAIL] {0}".format(exc), file=sys.stderr)
        return 1
    stale = stale_paths(outputs)
    names = [path.relative_to(repo_root).as_posix() for path in stale]
    if args.check:
        if stale:
            print(
                "[FAIL] Generated protocol files are out of date: {0}. Run "
                "python3 scripts/build_variants.py and commit the result.".format(", ".join(names)),
                file=sys.stderr,
            )
            return 1
        print("[OK] Generated protocol files match protocol/PROTOCOL.md")
        return 0
    for path in stale:
        path.write_text(outputs[path], encoding="utf-8")
    if names:
        print("[OK] Wrote " + ", ".join(names) + " from protocol/PROTOCOL.md")
    else:
        print("[OK] Generated protocol files already match protocol/PROTOCOL.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
