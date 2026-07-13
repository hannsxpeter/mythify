#!/usr/bin/env python3
"""Build or verify a flat GitHub release checksum manifest."""

import argparse
import hashlib
import os
import tempfile
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def build_manifest(paths, output):
    files = [Path(item) for item in paths]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise ValueError("Release asset is missing: {}".format(", ".join(missing)))
    names = [path.name for path in files]
    if len(names) != len(set(names)):
        raise ValueError("Release asset basenames must be unique")
    lines = ["{}  {}\n".format(digest(path), path.name) for path in sorted(files, key=lambda item: item.name)]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".SHA256SUMS-", dir=str(output.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify_manifest(manifest, directory):
    manifest = Path(manifest)
    directory = Path(directory)
    rows = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError("Malformed checksum line {}".format(number))
        name = parts[1].lstrip("*")
        if Path(name).name != name:
            raise ValueError("Checksum line {} must use a flat asset basename".format(number))
        rows.append((parts[0].lower(), name))
    if not rows:
        raise ValueError("Checksum manifest is empty")
    if len(rows) != len({name for _, name in rows}):
        raise ValueError("Checksum manifest contains duplicate asset names")
    for expected, name in rows:
        target = directory / name
        if not target.is_file():
            raise ValueError("Checksum asset is missing: {}".format(target))
        if digest(target) != expected:
            raise ValueError("Checksum mismatch: {}".format(name))
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", metavar="PATH", help="Write a manifest for ASSET paths.")
    mode.add_argument("--check", metavar="PATH", help="Verify a manifest against a flat directory.")
    parser.add_argument("--directory", default=".", help="Flat asset directory for --check.")
    parser.add_argument("assets", nargs="*", metavar="ASSET")
    args = parser.parse_args(argv)
    try:
        if args.output:
            if not args.assets:
                raise ValueError("At least one release asset is required")
            build_manifest(args.assets, args.output)
            print("[OK] Wrote flat release checksums: {}".format(args.output))
        else:
            if args.assets:
                raise ValueError("ASSET arguments are not valid with --check")
            count = verify_manifest(args.check, args.directory)
            print("[OK] Verified {} flat release asset checksum(s).".format(count))
    except (OSError, ValueError) as exc:
        print("[FAIL] {}".format(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
