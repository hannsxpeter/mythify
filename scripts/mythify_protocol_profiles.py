"""Render and select checked protocol loading profiles."""

import json
from pathlib import Path


PROFILE_PREFIX = "<!-- Mythify protocol-profile: "
PROFILE_BODY_PREFIX = "<!-- Mythify protocol-body-sha256: "
THIN_PREAMBLE = """# The Mythify Protocol

This is the thin host bootstrap. Follow every rule below. Load the installed
Mythify skill or `protocol/PROTOCOL.md` before using commands not described here.
The full protocol remains authoritative when this bootstrap omits detail.
"""


def load_profile_manifest(repo_root):
    path = Path(repo_root) / "protocol" / "loading-profiles.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported protocol loading profile schema")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Protocol loading profiles must be a non-empty object")
    default = data.get("default_profile")
    if default not in profiles:
        raise ValueError("Default protocol loading profile is not defined")
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ValueError("Protocol loading profile must be an object: " + name)
        sections = profile.get("sections")
        requires = profile.get("requires")
        if not isinstance(sections, list) or not sections:
            raise ValueError("Protocol loading profile needs sections: " + name)
        if not isinstance(requires, list):
            raise ValueError("Protocol loading profile needs requirements: " + name)
    return data


def markdown_h2_sections(text):
    sections = {}
    current = None
    lines = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).rstrip() + "\n"
            current = line[3:].strip()
            lines = [line]
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).rstrip() + "\n"
    return sections


def render_profile_body(protocol_text, profile_name, manifest):
    profiles = manifest["profiles"]
    if profile_name not in profiles:
        raise ValueError("Unknown protocol loading profile: " + profile_name)
    selected = profiles[profile_name]["sections"]
    if selected == ["*"]:
        return protocol_text
    sections = markdown_h2_sections(protocol_text)
    missing = [name for name in selected if name not in sections]
    if missing:
        raise ValueError("Protocol profile sections not found: " + ", ".join(missing))
    parts = [THIN_PREAMBLE.rstrip()]
    parts.extend(sections[name].rstrip() for name in selected)
    parts.append(
        "## Progressive disclosure\n\n"
        "Use the installed Mythify skill for the selected workflow. Read "
        "`protocol/PROTOCOL.md` when the skill or command reference is unavailable. "
        "If a required detail cannot be loaded, use the full protocol profile.\n"
    )
    return "\n\n".join(parts).rstrip() + "\n"


def select_loading_profile(manifest, requested="auto", capabilities=None):
    capabilities = set(capabilities or [])
    profiles = manifest["profiles"]
    if requested != "auto":
        if requested not in profiles:
            raise ValueError("Unknown protocol loading profile: " + requested)
        missing = sorted(set(profiles[requested]["requires"]) - capabilities)
        if missing:
            raise ValueError(
                "Protocol loading profile {0} requires: {1}".format(
                    requested, ", ".join(missing)
                )
            )
        return requested
    thin = profiles.get("thin", {})
    if set(thin.get("requires", [])) <= capabilities:
        return "thin"
    return manifest["default_profile"]


def profile_metrics(protocol_text, manifest):
    rows = {}
    for name in sorted(manifest["profiles"]):
        body = render_profile_body(protocol_text, name, manifest)
        rows[name] = {
            "bytes": len(body.encode("utf-8")),
            "words": len(body.split()),
            "lines": len(body.splitlines()),
        }
    return rows
