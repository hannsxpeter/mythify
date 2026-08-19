#!/usr/bin/env python3
"""Check unambiguous prose rules without claiming to judge writing quality."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "protocol" / "prose-quality.json"
TEXT_SUFFIXES = frozenset((".md", ".mdx"))


def load_manifest(path=MANIFEST_PATH):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("name") != "prose-quality":
        raise ValueError("invalid prose-quality manifest header")
    rules = manifest.get("mechanical_rules")
    if not isinstance(rules, dict):
        raise ValueError("prose-quality manifest is missing mechanical_rules")
    for key in ("forbidden_characters", "emoji_ranges", "forbidden_phrases"):
        if not isinstance(rules.get(key), list):
            raise ValueError("prose-quality mechanical rule must be a list: {0}".format(key))
    return manifest


def _relative_key(path, root):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _is_excluded(path, root, excluded):
    key = _relative_key(path, root)
    return any(key == prefix or key.startswith(prefix.rstrip("/") + "/") for prefix in excluded)


def collect_paths(root, manifest, requested=None):
    selected = list(requested or manifest.get("default_paths") or [])
    excluded = tuple(str(value).strip().rstrip("/") for value in manifest.get("excluded_paths") or [])
    files = set()
    for value in selected:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            if candidate.suffix.lower() in TEXT_SUFFIXES and not _is_excluded(candidate, root, excluded):
                files.add(candidate.resolve())
            continue
        if candidate.is_dir():
            for path in candidate.rglob("*"):
                if (
                    path.is_file()
                    and path.suffix.lower() in TEXT_SUFFIXES
                    and not _is_excluded(path, root, excluded)
                ):
                    files.add(path.resolve())
            continue
        raise FileNotFoundError("prose-quality path does not exist: {0}".format(candidate))
    return sorted(files, key=lambda path: _relative_key(path, root))


def _emoji_ranges(manifest):
    ranges = []
    for item in manifest["mechanical_rules"]["emoji_ranges"]:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("emoji range must contain two hexadecimal values")
        ranges.append((int(item[0], 16), int(item[1], 16)))
    return ranges


def inspect_text(text, path, manifest):
    findings = []
    rules = manifest["mechanical_rules"]
    forbidden_characters = {
        item["value"]: item["name"] for item in rules["forbidden_characters"]
    }
    phrases = tuple(str(value) for value in rules["forbidden_phrases"])
    ranges = _emoji_ranges(manifest)
    for line_number, line in enumerate(text.splitlines(), 1):
        lowered = line.casefold()
        for character, name in forbidden_characters.items():
            if character in line:
                findings.append(
                    {
                        "path": str(path),
                        "line": line_number,
                        "rule": "forbidden_character",
                        "detail": name,
                    }
                )
        for phrase in phrases:
            if phrase.casefold() in lowered:
                findings.append(
                    {
                        "path": str(path),
                        "line": line_number,
                        "rule": "forbidden_phrase",
                        "detail": phrase,
                    }
                )
        for character in line:
            codepoint = ord(character)
            if any(start <= codepoint <= end for start, end in ranges):
                findings.append(
                    {
                        "path": str(path),
                        "line": line_number,
                        "rule": "emoji",
                        "detail": "U+{0:04X}".format(codepoint),
                    }
                )
    return findings


def inspect_paths(root, manifest, requested=None):
    findings = []
    files = collect_paths(root, manifest, requested=requested)
    for path in files:
        text = path.read_text(encoding="utf-8")
        display = _relative_key(path, root)
        findings.extend(inspect_text(text, display, manifest))
    return {"files_checked": len(files), "findings": findings}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check mechanical prose rules. Subjective prose judgment remains advisory."
    )
    parser.add_argument("paths", nargs="*", help="Markdown files or directories. Defaults to the manifest paths.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        result = inspect_paths(REPO_ROOT, manifest, requested=args.paths or None)
    except (OSError, UnicodeError, ValueError) as exc:
        print("[FAIL] Prose-quality check could not run: {0}".format(exc), file=sys.stderr)
        return 2
    result.update(
        {
            "status": "pass" if not result["findings"] else "fail",
            "evidence_status": manifest["evidence_status"],
        }
    )
    if args.json_output:
        print(json.dumps(result, indent=2))
    elif result["findings"]:
        print("[FAIL] Prose-quality check found {0} mechanical violation(s).".format(len(result["findings"])))
        for finding in result["findings"]:
            print(
                "{0}:{1}: {2}: {3}".format(
                    finding["path"],
                    finding["line"],
                    finding["rule"],
                    finding["detail"],
                )
            )
        print("Guardrail: this check does not score voice, originality, or human authorship.")
    else:
        print("[OK] Prose-quality mechanical check passed ({0} files).".format(result["files_checked"]))
        print("Guardrail: subjective prose quality remains material judgment.")
    return 0 if not result["findings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
