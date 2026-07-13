#!/usr/bin/env python3
"""Enforce a stable size ceiling for first-party runtime source files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_LIMIT = 1500
RUNTIME_ROOTS = (("scripts", "*.py"), ("mcp-server/src", "*.js"))
# Generated dependency and cache directories are not first-party runtime code.
EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__", "node_modules", ".venv"})


def nonblank_line_count(path):
    """Count every non-whitespace physical line, including comments and docs."""
    return sum(
        1
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def runtime_source_paths(root):
    root = Path(root).resolve()
    paths = []
    for relative_root, pattern in RUNTIME_ROOTS:
        source_root = root / relative_root
        if not source_root.is_dir():
            continue
        paths.extend(
            path
            for path in source_root.rglob(pattern)
            if path.is_file()
            and not (EXCLUDED_DIRECTORY_NAMES & set(path.relative_to(source_root).parts))
        )
    return sorted(set(paths))


def check_runtime_sources(root, limit=DEFAULT_LIMIT):
    root = Path(root).resolve()
    files = []
    for path in runtime_source_paths(root):
        count = nonblank_line_count(path)
        files.append({
            "path": path.relative_to(root).as_posix(),
            "nonblank_lines": count,
            "limit": limit,
            "status": "passed" if count <= limit else "failed",
        })
    violations = [row for row in files if row["status"] == "failed"]
    return {
        "kind": "runtime_source_size",
        "rule": "non_whitespace_physical_lines_including_comments_and_docstrings",
        "exclusions": sorted(EXCLUDED_DIRECTORY_NAMES),
        "limit": limit,
        "status": "failed" if violations else "passed",
        "files": files,
        "violations": violations,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check first-party Python and Node runtime sources against a line ceiling."
    )
    parser.add_argument("--root", default=".", help="Repository root. Defaults to cwd.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    result = check_runtime_sources(args.root, args.limit)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["status"] == "passed":
        print(
            "[OK] Runtime source-size guard passed: {0} files at or below {1} nonblank lines.".format(
                len(result["files"]), result["limit"]
            )
        )
    else:
        print("[FAIL] Runtime source-size guard found oversized files:")
        for row in result["violations"]:
            print(
                "  {0}: {1} nonblank lines (limit {2})".format(
                    row["path"], row["nonblank_lines"], row["limit"]
                )
            )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
