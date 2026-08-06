"""External eval scenario validation and loading for Mythify.

Shared by the `eval` CLI family and scripts/local_model_eval.py so both
surfaces apply the same fail-closed rules to scenario material. A scenario
that fails validation is refused everywhere; nothing degrades to a warning.

Two document shapes are accepted:

- candidate: a single scenario object carrying its own "name"
- registry: {"schema_version": 1, "scenarios": {name: scenario}}

The adopted registry written by `eval adopt` uses the registry shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SCENARIO_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SCENARIO_REQUIRED_KEYS = ("name", "title", "task_category", "task", "files")
SCENARIO_OPTIONAL_KEYS = (
    "local_model_roles",
    "local_model_fit_reason",
    "fanout_fit",
    "fanout_fit_reason",
    "fanout_merge_verifier",
    # Adoption provenance stamped by `eval adopt`. The harness ignores it; it
    # exists so a reader can tell an adopted scenario from one written straight
    # into the registry by hand.
    "adoption",
)
SCENARIO_REGISTRY_SCHEMA_VERSION = 1
# create_task_workspace writes TASK.md after the scenario files, so a files
# entry with that name would be silently overwritten instead of used.
SCENARIO_RESERVED_FILES = ("TASK.md",)


def bad_relative_path_reason(path_text):
    """Why PATH_TEXT is not a safe workspace-relative file path, or None."""
    if not isinstance(path_text, str) or not path_text.strip():
        return "is empty"
    if "\\" in path_text:
        return "contains a backslash"
    if path_text.startswith("/"):
        return "is absolute"
    if any(part in ("", ".", "..") for part in path_text.split("/")):
        return "contains a traversal or empty segment"
    if path_text in SCENARIO_RESERVED_FILES:
        return "collides with the reserved TASK.md file"
    return None


def validate_scenario(name, data):
    """Return a list of problems with the scenario. Empty means valid."""
    problems = []
    if not isinstance(name, str) or not SCENARIO_NAME_PATTERN.match(name or ""):
        problems.append(
            "scenario name {0!r} must match [a-z][a-z0-9_]* and stay under 64 "
            "characters".format(name)
        )
    if not isinstance(data, dict):
        problems.append("scenario {0!r} must be a JSON object".format(name))
        return problems
    for key in ("title", "task_category", "task"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append("scenario {0!r} needs a non-empty string {1}".format(name, key))
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        problems.append("scenario {0!r} needs a non-empty files object".format(name))
    else:
        for relative_path, content in files.items():
            reason = bad_relative_path_reason(relative_path)
            if reason is not None:
                problems.append(
                    "scenario {0!r} file path {1!r} {2}".format(name, relative_path, reason)
                )
            if not isinstance(content, str):
                problems.append(
                    "scenario {0!r} file {1!r} content must be a string".format(
                        name, relative_path
                    )
                )
    roles = data.get("local_model_roles", [])
    if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
        problems.append(
            "scenario {0!r} local_model_roles must be a list of strings".format(name)
        )
    for key in ("local_model_fit_reason", "fanout_fit", "fanout_fit_reason"):
        if key in data and not isinstance(data.get(key), str):
            problems.append("scenario {0!r} {1} must be a string".format(name, key))
    verifier = data.get("fanout_merge_verifier")
    if verifier is not None and (not isinstance(verifier, str) or not verifier.strip()):
        problems.append(
            "scenario {0!r} fanout_merge_verifier must be a non-empty string".format(name)
        )
    if "adoption" in data and not isinstance(data.get("adoption"), dict):
        problems.append("scenario {0!r} adoption must be an object".format(name))
    allowed = set(SCENARIO_REQUIRED_KEYS) | set(SCENARIO_OPTIONAL_KEYS)
    for key in data:
        if key not in allowed:
            problems.append("scenario {0!r} has unknown key {1!r}".format(name, key))
    return problems


def scenarios_from_document(document, source_label):
    """Extract {name: scenario} from a candidate or registry document.

    Raises ValueError with every problem found; never returns a partly
    validated set.
    """
    problems = []
    entries = {}
    if isinstance(document, dict) and isinstance(document.get("scenarios"), dict):
        schema_version = document.get("schema_version", SCENARIO_REGISTRY_SCHEMA_VERSION)
        if schema_version != SCENARIO_REGISTRY_SCHEMA_VERSION:
            problems.append(
                "{0}: unsupported schema_version {1!r}".format(source_label, schema_version)
            )
        for name, entry in document["scenarios"].items():
            candidate = dict(entry) if isinstance(entry, dict) else entry
            if isinstance(candidate, dict):
                stored_name = candidate.pop("name", name)
                if stored_name != name:
                    problems.append(
                        "{0}: scenario key {1!r} disagrees with its name field "
                        "{2!r}".format(source_label, name, stored_name)
                    )
            entries[name] = candidate
    elif isinstance(document, dict) and "name" in document:
        candidate = dict(document)
        name = candidate.pop("name")
        entries[name] = candidate
    else:
        problems.append(
            "{0}: expected a scenario object with a name, or a registry with a "
            "scenarios object".format(source_label)
        )
    for name, entry in entries.items():
        for problem in validate_scenario(name, entry):
            problems.append("{0}: {1}".format(source_label, problem))
    if problems:
        raise ValueError("; ".join(problems))
    return entries


def load_scenario_file(path_text):
    """Load and validate one scenario file. Raises ValueError on any problem."""
    path = Path(path_text)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("{0}: {1}".format(path_text, exc))
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("{0}: invalid JSON: {1}".format(path_text, exc))
    return scenarios_from_document(document, str(path_text))


def load_external_scenarios(paths, builtin_names):
    """Load every scenario file, refusing builtin and cross-file collisions."""
    merged = {}
    problems = []
    for path_text in paths:
        entries = load_scenario_file(path_text)
        for name, entry in entries.items():
            if name in builtin_names:
                problems.append(
                    "{0}: scenario {1!r} collides with a built-in scenario".format(
                        path_text, name
                    )
                )
            elif name in merged:
                problems.append(
                    "{0}: scenario {1!r} was already loaded from another "
                    "file".format(path_text, name)
                )
            else:
                merged[name] = entry
    if problems:
        raise ValueError("; ".join(problems))
    return merged


def scan_scenario_file_args(argv, environ):
    """Collect --scenario-file values before argparse choices are built.

    The --scenario choices come from the scenario table, so external files
    must load first. Falls back to MYTHIFY_EVAL_SCENARIOS when no flag is
    present.
    """
    paths = []
    argv = list(argv or [])
    for index, item in enumerate(argv):
        if item == "--scenario-file" and index + 1 < len(argv):
            paths.append(argv[index + 1])
        elif item.startswith("--scenario-file="):
            paths.append(item.split("=", 1)[1])
    if not paths:
        fallback = (environ or {}).get("MYTHIFY_EVAL_SCENARIOS", "").strip()
        if fallback:
            paths.append(fallback)
    return paths
