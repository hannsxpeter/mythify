#!/usr/bin/env python3
"""Generate docs/commands.md from the Mythify argparse tree.

The reference is rendered from `mythify_parser.build_parser`, so every
command, usage line, and option it lists exists in the CLI. Usage lines are
rendered here rather than by argparse's formatter, whose wrapping depends on
the terminal width and whose layout differs between Python versions; the
output is byte-identical on every supported interpreter.

    python3 scripts/build_commands_doc.py          write docs/commands.md
    python3 scripts/build_commands_doc.py --check  exit 1 when it is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
OUTPUT = Path("docs") / "commands.md"
PROG = "mythify"


def load_parser():
    """Build the real CLI parser from the scripts beside this file."""
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import mythify  # noqa: E402
    import mythify_parser  # noqa: E402

    return mythify_parser.build_parser(vars(mythify))


def subparsers_action(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def choice_help(actions, name):
    for item in actions._choices_actions:
        if item.dest == name:
            return clean(item.help)
    return ""


def clean(text):
    if not text or text == argparse.SUPPRESS:
        return ""
    return " ".join(str(text).split())


def expand_help(action):
    text = action.help or ""
    if not text or text == argparse.SUPPRESS:
        return ""
    if "%" in text:
        params = {key: value for key, value in vars(action).items() if value is not argparse.SUPPRESS}
        params["prog"] = PROG
        if params.get("choices") is not None:
            params["choices"] = ", ".join(str(item) for item in params["choices"])
        try:
            text = text % params
        except (KeyError, TypeError, ValueError):
            pass
    return clean(text)


def is_documented(action):
    if isinstance(action, (argparse._HelpAction, argparse._VersionAction, argparse._SubParsersAction)):
        return False
    return action.help != argparse.SUPPRESS


def metavar_for(action):
    if action.metavar is not None:
        metavar = action.metavar
        return " ".join(metavar) if isinstance(metavar, tuple) else str(metavar)
    if action.choices is not None:
        return "{" + ",".join(str(item) for item in action.choices) + "}"
    return action.dest if not action.option_strings else action.dest.upper()


def value_form(action):
    """The value part of an argument as argparse would show it."""
    if action.nargs == 0:
        return ""
    metavar = metavar_for(action)
    nargs = action.nargs
    if nargs is None:
        return metavar
    if nargs == argparse.OPTIONAL:
        return "[{0}]".format(metavar)
    if nargs == argparse.ZERO_OR_MORE:
        return "[{0} ...]".format(metavar)
    if nargs == argparse.ONE_OR_MORE:
        return "{0} [{0} ...]".format(metavar)
    if nargs == argparse.REMAINDER:
        return "..."
    if isinstance(nargs, int):
        return " ".join([metavar] * nargs)
    return metavar


def option_flag(action):
    longs = [item for item in action.option_strings if item.startswith("--")]
    return (longs or action.option_strings)[0]


def usage_term(action):
    if not action.option_strings:
        return value_form(action)
    value = value_form(action)
    return option_flag(action) + (" " + value if value else "")


def usage_line(path, parser):
    actions = [action for action in parser._actions if is_documented(action)]
    grouped = {}
    for group in parser._mutually_exclusive_groups:
        members = [action for action in group._group_actions if is_documented(action)]
        if members:
            for action in members:
                grouped[id(action)] = (group, members)
    parts = []
    seen_groups = set()
    for action in actions:
        if not action.option_strings:
            continue
        if id(action) in grouped:
            group, members = grouped[id(action)]
            if id(group) in seen_groups:
                continue
            seen_groups.add(id(group))
            body = " | ".join(usage_term(member) for member in members)
            parts.append(("({0})" if group.required else "[{0}]").format(body))
            continue
        term = usage_term(action)
        parts.append(term if action.required else "[{0}]".format(term))
    for action in actions:
        if not action.option_strings:
            parts.append(usage_term(action))
    return " ".join([PROG] + list(path) + parts)


def argument_line(action):
    if action.option_strings:
        value = value_form(action)
        names = ", ".join(
            "{0} {1}".format(flag, value) if value else flag for flag in action.option_strings
        )
    else:
        names = value_form(action)
    notes = []
    if action.option_strings and action.required:
        notes.append("Required.")
    if isinstance(action, argparse._AppendAction):
        notes.append("Repeatable.")
    if action.choices is not None and action.metavar is not None:
        notes.append("Choices: {0}.".format(", ".join("`{0}`".format(item) for item in action.choices)))
    help_text = expand_help(action)
    default = action.default
    if (
        action.option_strings
        and action.nargs != 0
        and default not in (None, "", [], argparse.SUPPRESS)
        and not isinstance(default, bool)
        and "default" not in help_text.lower()
    ):
        notes.append("Default: `{0}`.".format(default))
    text = " ".join(part for part in [help_text] + notes if part)
    return "- `{0}`{1}".format(names, ": " + text if text else "")


def leaf_section(path, parser, short_help, level):
    lines = ["{0} {1}".format("#" * level, " ".join(path)), ""]
    lines.extend(["```text", usage_line(path, parser), "```", ""])
    description = clean(parser.description) or short_help
    if description:
        lines.extend([description, ""])
    arguments = [action for action in parser._actions if is_documented(action)]
    positionals = [action for action in arguments if not action.option_strings]
    options = [action for action in arguments if action.option_strings]
    if positionals:
        lines.extend(["Arguments:", ""])
        lines.extend(argument_line(action) for action in positionals)
        lines.append("")
    if options:
        lines.extend(["Options:", ""])
        lines.extend(argument_line(action) for action in options)
        lines.append("")
    return lines


def leaves(parser, prefix=()):
    """Yield (path, parser, short help) for every runnable command."""
    actions = subparsers_action(parser)
    if actions is None:
        return
    for name, child in actions.choices.items():
        path = prefix + (name,)
        if subparsers_action(child) is None:
            yield path, child, choice_help(actions, name)
        else:
            for item in leaves(child, path):
                yield item


def render(parser):
    top = subparsers_action(parser)
    lines = [
        "# Command reference",
        "",
        "Generated from the argparse tree in `scripts/mythify_parser.py` by",
        "`python3 scripts/build_commands_doc.py`. Do not edit this file by hand:",
        "change the parser, regenerate, and commit both. `python3 scripts/lint.py`",
        "fails when this file is stale.",
        "",
        "`mythify` is the installed launcher. From a clone, run",
        "`python3 scripts/mythify.py` with the same arguments.",
        "",
        "Global options: `mythify --version` prints the version, and `-h` or",
        "`--help` after any command prints its help. Exit codes: 0 success, 1",
        "refusal or failure, 2 a recorded unverified verdict, 64 a usage error.",
        "",
        "## Commands",
        "",
    ]
    for name in top.choices:
        lines.append("- `{0}`: {1}".format(name, choice_help(top, name)))
    lines.append("")
    for name, child in top.choices.items():
        short_help = choice_help(top, name)
        if subparsers_action(child) is None:
            lines.extend(leaf_section((name,), child, short_help, 2))
            continue
        lines.extend(["## {0}".format(name), ""])
        description = clean(child.description) or short_help
        if description:
            lines.extend([description, ""])
        for path, leaf, leaf_help in leaves(child, (name,)):
            lines.extend(leaf_section(path, leaf, leaf_help, 3))
    while lines and lines[-1] == "":
        lines.pop()
    text = "\n".join(lines) + "\n"
    bad = sorted({char for char in text if ord(char) > 127})
    if bad:
        raise ValueError(
            "Parser help text has non-ASCII characters: {0}".format(
                ", ".join("U+{0:04X}".format(ord(char)) for char in bad)
            )
        )
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate docs/commands.md from the CLI parser.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 without writing when docs/commands.md is missing or stale.",
    )
    args = parser.parse_args(argv)
    text = render(load_parser())
    target = REPO_ROOT / OUTPUT
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current != text:
            print("[FAIL] {0} is stale. Run: python3 scripts/build_commands_doc.py".format(OUTPUT.as_posix()))
            return 1
        print("[OK] {0} matches the parser.".format(OUTPUT.as_posix()))
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(str(target), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("[OK] Wrote {0}.".format(OUTPUT.as_posix()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
