#!/usr/bin/env python3
"""Build a deterministic, self-contained Mythify CLI tar archive."""

import argparse
import gzip
import json
import os
import re
import tarfile
from pathlib import Path


VERSION_PATTERN = re.compile(r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)
SKILL_NAMES = ("mythify", "mythify-work", "mythify-route", "mythify-verify")
STANDALONE_DOCS = (
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/claude-integrations.md",
    "docs/design.md",
    "docs/desktop-tool-calls.md",
    "docs/start-here.md",
    "docs/research-report.md",
    "docs/evidence/codex-word-count-2026-07-13.json",
    "docs/evidence/efficacy-reproduction.md",
)


def read_version(repo_root):
    cli_source = (repo_root / "scripts" / "mythify.py").read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(cli_source)
    if not match:
        raise RuntimeError("Could not determine the Mythify CLI version")
    return match.group(1)


def check_release_tag(repo_root, tag):
    cli_version = read_version(repo_root)
    package_path = repo_root / "mcp-server" / "package.json"
    package_version = json.loads(package_path.read_text(encoding="utf-8"))["version"]
    if package_version != cli_version:
        raise RuntimeError(
            "CLI version {} does not match MCP package version {}".format(
                cli_version, package_version
            )
        )
    expected = "v" + cli_version
    if tag != expected:
        raise RuntimeError(
            "Release tag {} does not match package version {}".format(tag, expected)
        )
    return cli_version


def artifact_files(repo_root):
    files = {
        repo_root / "LICENSE",
        repo_root / "README.md",
        repo_root / "scripts" / "install_user.sh",
        repo_root / "scripts" / "mythify.py",
        repo_root / "scripts" / "mythify_chat_report_hook.sh",
    }
    files.update(repo_root / relative for relative in STANDALONE_DOCS)
    files.update((repo_root / "scripts").glob("mythify_*.py"))
    files.update(path for path in (repo_root / "protocol").rglob("*") if path.is_file())
    for skill_name in SKILL_NAMES:
        skill_dir = repo_root / "skills" / skill_name
        if not skill_dir.is_dir():
            raise RuntimeError("Missing required skill directory: {}".format(skill_dir))
        if not (skill_dir / "SKILL.md").is_file():
            raise RuntimeError("Missing required skill file: {}/SKILL.md".format(skill_dir))
        files.update(path for path in skill_dir.rglob("*") if path.is_file())

    missing = sorted(str(path) for path in files if not path.is_file())
    if missing:
        raise RuntimeError("Missing standalone CLI inputs: {}".format(", ".join(missing)))
    symlinks = sorted(str(path) for path in files if path.is_symlink())
    if symlinks:
        raise RuntimeError("Standalone CLI inputs must not be symlinks: {}".format(", ".join(symlinks)))
    return sorted(files, key=lambda path: path.relative_to(repo_root).as_posix())


def archive_mode(relative_path):
    if relative_path.as_posix() in (
        "scripts/install_user.sh",
        "scripts/mythify.py",
        "scripts/mythify_chat_report_hook.sh",
    ):
        return 0o755
    return 0o644


def build_archive(repo_root, output_dir):
    version = read_version(repo_root)
    archive_root = "mythify-cli-{}".format(version)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "{}.tar.gz".format(archive_root)
    temporary = output_dir / ".{}.tmp-{}".format(destination.name, os.getpid())

    try:
        with temporary.open("wb") as raw_output:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_output,
                mtime=0,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as archive:
                    for source in artifact_files(repo_root):
                        relative = source.relative_to(repo_root)
                        info = tarfile.TarInfo(
                            "{}/{}".format(archive_root, relative.as_posix())
                        )
                        info.size = source.stat().st_size
                        info.mode = archive_mode(relative)
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = "root"
                        info.gname = "root"
                        with source.open("rb") as source_handle:
                            archive.addfile(info, source_handle)
        os.replace(str(temporary), str(destination))
    finally:
        if temporary.exists():
            temporary.unlink()

    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build the self-contained Mythify CLI release artifact."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to dist under the repository root.",
    )
    parser.add_argument(
        "--check-release-tag",
        default="",
        metavar="TAG",
        help="Fail unless TAG is v plus the matching CLI and MCP package version.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    if args.check_release_tag:
        try:
            version = check_release_tag(repo_root, args.check_release_tag)
        except (OSError, KeyError, ValueError, RuntimeError) as exc:
            print("[FAIL] {}".format(exc))
            return 1
        print("[OK] Release tag v{} matches CLI and MCP package version.".format(version))
        return 0
    output_dir = args.output_dir or (repo_root / "dist")
    destination = build_archive(repo_root, output_dir.resolve())
    print("[OK] Built standalone CLI artifact: {}".format(destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
