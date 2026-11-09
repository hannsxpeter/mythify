"""Named checks: `.mythify/checks.json` and `verify check`.

A check is a shell command with a short name, defined once and run by name.
A step may store `@NAME` as its verify_command: `plan verify` runs the check,
and the step gate counts any passing record stamped `check: NAME`, because a
template with `{files}` expands to a different command each run. Running goes
through execute_recorded_verification, so a check is recorded, reusable, and
gated exactly like `verify run`.
"""

import json
import os
import re
import shlex
import sys
from contextlib import redirect_stdout
from pathlib import Path

from mythify_evidence_guard import noop_verifier_reason, run_disabled
from mythify_io import read_json, write_json_atomic
from mythify_provenance import changed_paths, project_root_for_state
from mythify_reuse import REUSE_HELP
from mythify_runtime_helpers import now_iso

CHECKS_FILE = "checks.json"
SCHEMA_VERSION = 1
NAME_PATTERN = re.compile(r"[a-z0-9-]+")
FILES_TOKEN = "{files}"

_deps = {}


def configure_checks(**deps):
    """Inject execute_func, fail_func, and disabled_message."""
    _deps.clear()
    _deps.update(deps)


def check_reference(verify_command):
    """The check name when VERIFY_COMMAND is `@NAME`, else None."""
    text = str(verify_command or "").strip()
    if text.startswith("@") and NAME_PATTERN.fullmatch(text[1:]):
        return text[1:]
    return None


def verifier_matches(stored, command, check=None):
    """True when a run of COMMAND (for check CHECK, if any) fits a step's STORED verifier."""
    stored = str(stored or "").strip()
    return bool(stored) and (stored == command or (check is not None and stored == "@" + check))


def files_placeholder_unsafe(template):
    """True when {files} sits inside quotes or right after a backslash.

    Mythify shell-quotes every path itself, so the placeholder must be a bare
    word. Inside double quotes a quoted path would still run `$(...)` or
    backticks from a file name, and inside single quotes the added quoting
    breaks out of them. A small quote-state walk, as the shell reads it:
    backslash escapes the next character except inside single quotes.
    """
    quote, index = None, 0
    while index < len(template):
        char = template[index]
        if template.startswith(FILES_TOKEN, index):
            if quote is not None:
                return True
            index += len(FILES_TOKEN)
            continue
        if char == "\\" and quote != "'":
            if template.startswith(FILES_TOKEN, index + 1):
                return True
            index += 2
            continue
        if quote is None and char in "'\"":
            quote = char
        elif char == quote:
            quote = None
        index += 1
    return False


def _unsafe_message(name):
    return (
        "[FAIL] Check '{0}' puts {1} inside quotes or after a backslash. {1} must be a bare "
        "word because Mythify quotes each path itself; write, for example, "
        "'ruff check {1}'.".format(name, FILES_TOKEN)
    )


def checks_path(state):
    return Path(state) / CHECKS_FILE


def load_checks(state, quarantine=False):
    """Defined checks as {name: entry}, tolerating a missing or damaged file.

    Read-only unless QUARANTINE is set, which lets a damaged file be moved
    aside (as every other state file is) before `--set` rewrites it.
    """
    path = checks_path(state)
    if quarantine:
        data = read_json(path, {})
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    entries = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {
        name: entry for name, entry in entries.items()
        if NAME_PATTERN.fullmatch(str(name)) and isinstance(entry, dict)
        and isinstance(entry.get("command"), str) and entry["command"].strip()
    }


def checks_prompt_line(state):
    checks = load_checks(state)
    if not checks:
        return ""
    return "Checks: " + ", ".join(
        "{0} = {1}".format(name, entry["command"]) for name, entry in sorted(checks.items()))


def add_checks_line(payload, state):
    """Put the defined checks into a prompt packet that does not carry them yet."""
    line = checks_prompt_line(state)
    prompt = payload.get("next_prompt") or ""
    if not line or payload.get("error") or "\nChecks: " in "\n" + prompt:
        return payload
    for marker in ("\n\nInstructions:", "\nGuardrail:"):
        index = prompt.rfind(marker)
        if index >= 0:
            prompt = prompt[:index] + "\n" + line + prompt[index:]
            break
    else:
        prompt = prompt.rstrip() + "\n" + line
    payload["next_prompt"] = prompt
    payload["checks"] = {name: entry["command"] for name, entry in load_checks(state).items()}
    return payload


def warn_undefined_check(state, step_id, verify_command):
    """Advisory for a step that names a check which is not defined yet."""
    name = check_reference(verify_command)
    if name is not None and name not in load_checks(state):
        _deps["fail_func"](
            "[WARN] Step {0} verify command @{1} names a check that is not defined yet. "
            "Define it with: mythify verify check {1} --set COMMAND".format(step_id, name)
        )


def _unknown_message(name, checks):
    return (
        "[FAIL] Unknown check '{0}'. Defined checks: {1}. Define one with: "
        "mythify verify check NAME --set COMMAND".format(name, ", ".join(sorted(checks)) or "none")
    )


def changed_files(state):
    """Changed files that still exist, relative to the working directory.

    Working-tree changes against HEAD plus untracked files, without the state
    directory. None when git cannot answer.
    """
    root = project_root_for_state(state)
    paths = changed_paths(state, root=root)
    if paths is None:
        return None
    return [
        os.path.relpath(str(root / path), os.getcwd())
        for path in paths if os.path.lexists(str(root / path))
    ]


def run_check(state, name, claim=None, timeout=None, context=None, reuse=False, files=None):
    """Run check NAME and record it. Returns (record, exit_code).

    A record of None means nothing was recorded; the exit code is then what the
    caller returns (0 when there was nothing to run, 1 for a refusal, 2 when
    execution is disabled). CONTEXT is a step context for `plan verify`.
    """
    fail = _deps["fail_func"]
    checks = load_checks(state)
    if name not in checks:
        fail(_unknown_message(name, checks))
        return None, 1
    if run_disabled(os.environ):
        fail(_deps["disabled_message"])
        return None, 2
    template = checks[name]["command"]
    if files_placeholder_unsafe(template):
        fail(_unsafe_message(name))
        return None, 1
    command = template
    if FILES_TOKEN in template:
        listed = list(files) if files else changed_files(state)
        if listed is None:
            fail("[FAIL] Check {0} uses {1}, but git cannot list changed files here. "
                 "Pass --files PATH ... instead.".format(name, FILES_TOKEN))
            return None, 1
        if not listed:
            print("[OK] No changed files for check {0}; nothing to run.".format(name))
            if context:
                fail("[WARN] Nothing was recorded, so step {0} of plan {1} still has no "
                     "evidence.".format(context.get("step_id"), context.get("plan")))
            return None, 0
        command = template.replace(FILES_TOKEN, " ".join(shlex.quote(item) for item in listed))
    elif files:
        fail("[WARN] --files was ignored: check {0} has no {1} placeholder.".format(name, FILES_TOKEN))
    record = _deps["execute_func"](
        state, command, claim or "check " + name, timeout, context, reuse=reuse,
        extra={"check": name, "check_template": template},
    )
    return record, 0 if record["verified"] else 2


def _json_result(name, record):
    keys = ("exit_code", "duration_seconds", "test_count", "reused_from", "plan", "step_id")
    result = {"check": name, "ran": True, "ok": record["verified"], "command": record["command"],
              "record_id": record["id"]}
    result.update({key: record.get(key) for key in keys})
    return result


def _print_result(name, record, label):
    count = record.get("test_count")
    detail = ", {0} tests".format(count) if isinstance(count, int) else ""
    detail += ", reused" if record.get("reused_from") else ""
    print("[{0}] {1}: {2} (exit {3}, {4:.2f}s{5})".format(
        "OK" if record["verified"] else "FAIL", "VERIFIED" if record["verified"] else "UNVERIFIED",
        label, record["exit_code"], record["duration_seconds"], detail))
    if not record["verified"]:
        for channel in ("stdout", "stderr"):
            if record.get(channel + "_tail"):
                print("--- {0} (tail) ---".format(channel))
                print(record[channel + "_tail"])


def _list_checks(state, as_json):
    checks = load_checks(state)
    if as_json:
        print(json.dumps({"checks": checks}, indent=2))
        return 0
    if not checks:
        print("[OK] No checks defined. Add one with: mythify verify check NAME --set COMMAND")
        return 0
    print("[OK] Checks ({0}):".format(len(checks)))
    for name, entry in sorted(checks.items()):
        note = " - " + entry["description"] if entry.get("description") else ""
        print("  {0}: {1}{2}".format(name, entry["command"], note))
    return 0


def _define_check(args, state):
    fail = _deps["fail_func"]
    name, command = args.name, args.set_command.strip()
    if not NAME_PATTERN.fullmatch(name):
        fail("[FAIL] Invalid check name '{0}': use lowercase letters, digits, and hyphens.".format(name))
        return 1
    if files_placeholder_unsafe(command):
        fail(_unsafe_message(name))
        return 1
    reason = noop_verifier_reason(command) if command else "empty command"
    if reason:
        fail("[FAIL] Check '{0}' not defined: the command looks like a no-op ({1}). "
             "A check that cannot fail proves nothing.".format(name, reason))
        return 1
    checks = load_checks(state, quarantine=True)
    previous = checks.get(name)
    checks[name] = {
        "command": command,
        "description": args.description if args.description is not None else (previous or {}).get("description", ""),
        "updated": now_iso(),
    }
    write_json_atomic(checks_path(state), {"schema_version": SCHEMA_VERSION, "checks": checks})
    if args.json_output:
        print(json.dumps({"check": name, "defined": True, "replaced": previous is not None,
                          "command": command}, indent=2))
    else:
        print("[OK] {0} check {1}: {2}".format("Replaced" if previous else "Defined", name, command))
    return 0


def cmd_verify_check(args, state):
    if args.name is None:
        if args.set_command is not None or args.description is not None or args.files:
            _deps["fail_func"]("[FAIL] Name the check: mythify verify check NAME [--set COMMAND].")
            return 1
        return _list_checks(state, args.json_output)
    if args.set_command is not None:
        return _define_check(args, state)
    if args.description is not None:
        _deps["fail_func"]("[FAIL] --description only applies with --set.")
        return 1
    # With --json stdout carries only the JSON; progress lines go to stderr.
    with redirect_stdout(sys.stderr) if args.json_output else redirect_stdout(sys.stdout):
        record, code = run_check(
            state, args.name, args.claim, args.timeout, reuse=args.reuse, files=args.files)
    if record is None:
        if code == 0 and args.json_output:
            print(json.dumps({"check": args.name, "ran": False, "ok": True,
                              "reason": "no changed files"}, indent=2))
        return code
    if args.json_output:
        print(json.dumps(_json_result(args.name, record), indent=2))
    else:
        _print_result(args.name, record, args.claim or "check " + args.name)
    return code


def add_check_parsers(actions, symbols):
    """Register `verify check` under the `verify` action parsers."""
    p = actions.add_parser(
        "check",
        help="List, define, or run a named check from .mythify/checks.json.",
        description=(
            "Without NAME, list the defined checks. With NAME and --set COMMAND, "
            "define or replace the check without running it. With NAME alone, "
            "run it and record an executed verification stamped with the check "
            "name. {files} in the command expands to the shell-quoted changed "
            "files (--files, else working-tree changes against HEAD plus "
            "untracked files); with no changed files nothing runs and nothing "
            "is recorded. A step whose verify_command is @NAME runs this check "
            "through plan verify. Exits 0 verified, 2 failed or disabled, 1 refused."
        ),
    )
    p.add_argument("name", nargs="?", help="Check name: lowercase letters, digits, hyphens.")
    p.add_argument("--set", dest="set_command", metavar="COMMAND",
                   help="Define or replace the check with COMMAND; nothing runs.")
    p.add_argument("--description", help="With --set: what the check covers.")
    p.add_argument("--files", nargs="+", metavar="PATH", help="Files for {files}, instead of the changed files.")
    p.add_argument("--reuse", action="store_true", help=REUSE_HELP)
    p.add_argument("--timeout", type=float, default=symbols["DEFAULT_VERIFY_TIMEOUT"], metavar="N",
                   help="Timeout in seconds.")
    p.add_argument("--claim", help="What this run verifies; default 'check NAME'.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_verify_check)
