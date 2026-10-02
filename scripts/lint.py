#!/usr/bin/env python3
"""One maintainer lint for drift between Mythify's code, generated files, and docs.

    python3 scripts/lint.py [--check NAME ...] [--json] [--root PATH]

Exit 0 when every selected check is clean. Exit 1 with one line per finding,
`path:line: check: message`, otherwise. Standard library only.

The file set is the repository's tracked files plus untracked files git does
not ignore (so a new file is linted before it is committed). Outside a git
work tree, such as an unpacked copy, every file under --root is used except
.git and __pycache__.

tests/test_lint.py runs this lint on the real repository and proves every
check can fail by injecting one violation per check into a copy.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent

CHECKS = (
    "version",
    "generated",
    "protocol-budget",
    "text",
    "prose",
    "model-agnostic",
    "dependencies",
    "mcp-surface",
    "links",
    "source-size",
)

Finding = namedtuple("Finding", "path line check message")


def format_finding(finding):
    return "{0}:{1}: {2}: {3}".format(finding.path, finding.line, finding.check, finding.message)


# ---------------------------------------------------------------------------
# File set


def repo_files(root):
    """Sorted POSIX paths of the files the lint covers, relative to ROOT."""
    names = None
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(root), capture_output=True, text=True, check=True,
        ).stdout.strip()
        if top and Path(top).resolve() == root.resolve():
            listing = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=str(root), capture_output=True, check=True,
            ).stdout.decode("utf-8")
            names = [name for name in listing.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        names = None
    if names is None:
        names = []
        for directory, subdirs, files in os.walk(str(root)):
            subdirs[:] = sorted(name for name in subdirs if name not in (".git", "__pycache__"))
            for name in files:
                if name.endswith(".pyc"):
                    continue
                full = Path(directory) / name
                names.append(full.relative_to(root).as_posix())
    return sorted({name for name in names if (root / name).is_file()})


def read_text(root, relative):
    """Decoded UTF-8 text, or None for binary or undecodable files."""
    try:
        raw = (root / relative).read_bytes()
    except OSError:
        return None
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def run_python(root, args, timeout=300):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable] + list(args),
        cwd=str(root), capture_output=True, text=True, env=env, timeout=timeout,
    )


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


# ---------------------------------------------------------------------------
# version

VERSION_PATTERN = re.compile(r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)
CHANGELOG_HEADING = re.compile(r"^## \[([^\]]+)\]")
SECURITY_SUPPORTED = re.compile(r"^\|\s*([0-9]+)\.x\s*\|\s*yes\s*\|", re.IGNORECASE)
RELEASE_TARGET = re.compile(r"Current release target: `v([^`]+)`")


def line_of(text, index):
    return text.count("\n", 0, index) + 1


def check_version(root, files):
    cli_path = "scripts/mythify.py"
    cli = read_text(root, cli_path) or ""
    match = VERSION_PATTERN.search(cli)
    if not match:
        return [Finding(cli_path, 1, "version", 'no VERSION = "X.Y.Z" line')]
    version = match.group(1)
    major = version.split(".")[0]
    findings = []

    changelog = read_text(root, "CHANGELOG.md")
    top = None
    for number, line in enumerate((changelog or "").splitlines(), 1):
        heading = CHANGELOG_HEADING.match(line)
        if heading and heading.group(1) != "Unreleased":
            top = (number, heading.group(1))
            break
    if top is None:
        findings.append(Finding("CHANGELOG.md", 1, "version", "no release heading `## [X.Y.Z]`"))
    elif top[1] != version:
        findings.append(Finding(
            "CHANGELOG.md", top[0], "version",
            "top release heading is {0}, VERSION is {1}".format(top[1], version),
        ))

    security = read_text(root, "SECURITY.md")
    supported = {}
    for number, line in enumerate((security or "").splitlines(), 1):
        row = SECURITY_SUPPORTED.match(line)
        if row:
            supported[row.group(1)] = number
    if major not in supported:
        findings.append(Finding(
            "SECURITY.md", min(supported.values()) if supported else 1, "version",
            "supported versions table has no `| {0}.x | Yes |` row for VERSION {1}".format(major, version),
        ))

    checklist = read_text(root, "RELEASE-CHECKLIST.md")
    target = RELEASE_TARGET.search(checklist or "")
    if not target:
        findings.append(Finding("RELEASE-CHECKLIST.md", 1, "version", "no `Current release target: `vX.Y.Z`` line"))
    elif target.group(1) != version:
        findings.append(Finding(
            "RELEASE-CHECKLIST.md", line_of(checklist, target.start()), "version",
            "release target is v{0}, VERSION is {1}".format(target.group(1), version),
        ))
    return findings


# ---------------------------------------------------------------------------
# generated

GENERATORS = (
    ("scripts/build_variants.py", "AGENTS.md"),
    ("scripts/build_commands_doc.py", "docs/commands.md"),
)


def check_generated(root, files):
    findings = []
    for script, output in GENERATORS:
        if not (root / script).is_file():
            findings.append(Finding(script, 1, "generated", "generator is missing"))
            continue
        result = run_python(root, [script, "--check"])
        if result.returncode != 0:
            detail = first_line(result.stdout) or first_line(result.stderr) or "exit {0}".format(result.returncode)
            findings.append(Finding(
                output, 1, "generated",
                "`python3 {0} --check` failed: {1}".format(script, detail),
            ))
    return findings


# ---------------------------------------------------------------------------
# protocol-budget

PROTOCOL_BUDGET = 12000
SKILL_BUDGET = 16000


def check_protocol_budget(root, files):
    findings = []
    targets = [(path, PROTOCOL_BUDGET) for path in ("protocol/PROTOCOL.md", "AGENTS.md")]
    targets.extend(
        (path, SKILL_BUDGET) for path in files
        if fnmatch.fnmatch(path, "skills/*/SKILL.md") and path.count("/") == 2
    )
    for path, budget in targets:
        target = root / path
        if not target.is_file():
            findings.append(Finding(path, 1, "protocol-budget", "file is missing"))
            continue
        size = target.stat().st_size
        if size > budget:
            findings.append(Finding(
                path, 1, "protocol-budget",
                "{0} bytes exceeds the {1}-byte budget".format(size, budget),
            ))
    return findings


# ---------------------------------------------------------------------------
# text

BANNED_POINTS = {0x2013: "en dash", 0x2014: "em dash", 0xFE0F: "emoji variation selector"}
BANNED_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF))


def banned_name(codepoint):
    if codepoint in BANNED_POINTS:
        return BANNED_POINTS[codepoint]
    for low, high in BANNED_RANGES:
        if low <= codepoint <= high:
            return "emoji or symbol"
    return None


def check_text(root, files):
    findings = []
    for path in files:
        text = read_text(root, path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            seen = set()
            for char in line:
                codepoint = ord(char)
                if codepoint in seen:
                    continue
                name = banned_name(codepoint)
                if name:
                    seen.add(codepoint)
                    findings.append(Finding(path, number, "text", "U+{0:04X} ({1})".format(codepoint, name)))
    return findings


# ---------------------------------------------------------------------------
# prose


def check_prose(root, files):
    script = "scripts/check_prose_quality.py"
    if not (root / script).is_file():
        return [Finding(script, 1, "prose", "prose checker is missing")]
    result = run_python(root, [script, "--json"])
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        detail = first_line(result.stderr) or first_line(result.stdout) or "no output"
        return [Finding(script, 1, "prose", "checker failed: {0}".format(detail))]
    findings = []
    for item in payload.get("findings") or []:
        path = str(item.get("path") or script)
        if Path(path).is_absolute():
            try:
                path = Path(path).resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                pass
        message = "{0}: {1}".format(item.get("rule") or "finding", item.get("detail") or "")
        findings.append(Finding(path, item.get("line") or 1, "prose", message))
    if result.returncode != 0 and not findings:
        findings.append(Finding(script, 1, "prose", "checker exited {0}".format(result.returncode)))
    return findings


# ---------------------------------------------------------------------------
# model-agnostic
#
# This block is the single source of truth for the model-agnostic rule:
# tests import these tables instead of keeping their own lists.



def word(pattern):
    """PATTERN between letter-only boundaries, case-insensitive.

    `_`, digits, and `-` count as separators, so a name is caught inside an
    identifier (OPENAI_API_KEY, MYTHIFY_VLLM_MODEL) or a version (llama3.1).
    """
    return re.compile(r"(?<![A-Za-z])(?:" + pattern + r")(?![A-Za-z])", re.IGNORECASE)


# Model names. Forbidden in every file outside MODEL_AGNOSTIC_EXEMPT.
MODEL_NAMES = (
    ("haiku", word("haiku")),
    ("sonnet", word("sonnet")),
    ("opus", word("opus")),
    ("fable", word("fable")),
    ("gpt", word("(?:chat)?gpt")),
    ("gemini", word("gemini")),
    ("llama", word("llama")),
    ("mistral", word("mistral")),
    ("qwen", word("qwen")),
    ("deepseek", word("deepseek")),
    ("mixtral", word("mixtral")),
    ("codestral", word("codestral")),
    ("grok", word("grok")),
    # Removed provider-profile names. They collide with ordinary words in
    # lower case, so only the capitalized and suffix forms match.
    ("luna", re.compile(r"\bLuna\b|-luna\b")),
    ("terra", re.compile(r"\bTerra\b|-terra\b")),
    ("sol", re.compile(r"\bSol\b|-sol\b")),
)

# Identifiers of the deleted model-routing layer. Forbidden in every file
# outside MODEL_AGNOSTIC_EXEMPT. Tests that assert a key is absent check keys
# against this tuple instead of spelling the identifiers out.
ROUTING_IDENTIFIERS = (
    "model_policy",
    "model_router",
    "model_triage",
    "model_profile",
    "model-capabilities",
    "ultracode",
    "spawn_ceiling",
    "host-model",
    "host_model",
    "session_model",
    "reviewer_strength",
    "native_adapter",
    "execution_adapter",
    "fanout_visibility",
    "fanout_start",
    "fanout_status",
    "fanout_results",
    "MYTHIFY_TRIAGE",
    "MYTHIFY_FANOUT_",
    "MYTHIFY_SESSION_MODEL",
    "MYTHIFY_SPAWN_CEILING",
    "MYTHIFY_REVIEWER_STRENGTH",
    "MYTHIFY_FAILURE_COUNT",
    "MYTHIFY_MCP_TOOL_PROFILE",
    "MYTHIFY_DISABLE_FANOUT",
)


def identifier_pattern(name):
    """NAME as a prefix after a non-alphanumeric boundary, `_` and `-` alike.

    No trailing boundary: the removed layer spelled its names as prefixes of
    longer ones (MYTHIFY_TRIAGE_COMMAND, the host model switch tool), and its
    flags use `-` where its keys use `_`.
    """
    body = "".join("[-_]" if char in "-_" else re.escape(char) for char in name)
    return re.compile(r"(?<![A-Za-z0-9])" + body, re.IGNORECASE)


ROUTING_PATTERNS = tuple((name, identifier_pattern(name)) for name in ROUTING_IDENTIFIERS) + (
    ("mythify_model_*", re.compile(r"(?<![A-Za-z0-9])mythify[-_]model[-_]\w+", re.IGNORECASE)),
)

# Vendor and host names. Forbidden only in VENDOR_SCOPE. Lower-case `cursor`
# is an ordinary word here (report cursors), so only its vendor forms match.
VENDOR_NAMES = (
    ("claude", re.compile(r"claude", re.IGNORECASE)),
    ("codex", re.compile(r"codex", re.IGNORECASE)),
    ("cursor", re.compile(
        r"\bcursor[-_ ](?:agent|desktop|cli|ide|editor|workers?)\b|[/.]cursor/|cursorrules",
        re.IGNORECASE,
    )),
    ("kimi", word("kimi")),
    ("opencode", re.compile(r"opencode", re.IGNORECASE)),
    ("antigravity", re.compile(r"antigravity", re.IGNORECASE)),
    ("colab", word("colab")),
    ("ollama", re.compile(r"ollama", re.IGNORECASE)),
    ("anthropic", word("anthropic")),
    ("openai", word("openai")),
    ("lm studio", word("lm[-_ ]?studio")),
    ("vllm", word("vllm")),
    ("adk", re.compile(r"(?<![A-Za-z])ADK(?![A-Za-z])")),
    ("copilot", word("copilot")),
    ("windsurf", word("windsurf")),
    ("cline", word("cline")),
    ("aider", word("aider")),
)

# Exact text removed from a line before matching: file names and a license
# attribution, never a routing decision.
ALLOWED_TOKENS = (
    # The drop-in pointer file that `protocol check` reads.
    re.compile(re.escape("CLAUDE.md")),
    # The legacy drop-in file name that `protocol check` flags as a leftover.
    re.compile(re.escape(".cursorrules")),
    # MIT attributions for the adapted prose rules and blast-radius workflow.
    re.compile(re.escape("https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md")),
    re.compile(re.escape("https://github.com/cursor/plugins/blob/main/pstack/skills/blast-radius/SKILL.md")),
)
# Host configuration file locations, allowed only in the MCP setup table.
PATH_ALLOWED_TOKENS = {
    "docs/mcp.md": (re.compile(r"~/\.gemini/[\w./-]*"),),
}

# Historical records, plus this denylist and its self-test.
MODEL_AGNOSTIC_EXEMPT = (
    "CHANGELOG.md",
    "docs/DRIFT.md",
    "docs/evidence/",
    "scripts/lint.py",
    "tests/test_lint.py",
)

# Shipped product surfaces that must not name a vendor or host either: the
# runtime, the protocol, the skills, and every shipped doc. docs/mcp.md (host
# setup file locations) and the installer (host skill roots) are left out.
VENDOR_SCOPE = (
    "scripts/mythify*.py",
    "scripts/check_prose_quality.py",
    "protocol/*",
    "skills/*",
    "AGENTS.md",
    "README.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "MAINTAINING.md",
    "ROADMAP.md",
    "RELEASE-CHECKLIST.md",
    "CODE_OF_CONDUCT.md",
    "docs/*.md",
    "docs/assets/*.svg",
    "CLAUDE.md",
)
VENDOR_SCOPE_EXCLUDED = ("docs/mcp.md",)


def strip_allowed(line, path=None):
    for token in ALLOWED_TOKENS + PATH_ALLOWED_TOKENS.get(path, ()):
        line = token.sub("", line)
    return line


def model_agnostic_hits(line, vendors=True, path=None):
    """Labels of every denylist entry LINE matches after allowed tokens are removed."""
    line = strip_allowed(line, path)
    tables = MODEL_NAMES + ROUTING_PATTERNS + (VENDOR_NAMES if vendors else ())
    return [label for label, pattern in tables if pattern.search(line)]


def removed_identifier_keys(keys):
    """The KEYS (such as JSON field names) that contain a routing identifier."""
    names = [name.lower() for name in ROUTING_IDENTIFIERS]
    return sorted(str(key) for key in keys if any(name in str(key).lower() for name in names))


def is_exempt(path):
    return any(path == item or (item.endswith("/") and path.startswith(item)) for item in MODEL_AGNOSTIC_EXEMPT)


def in_vendor_scope(path):
    if path in VENDOR_SCOPE_EXCLUDED:
        return False
    return any(fnmatch.fnmatch(path, pattern) for pattern in VENDOR_SCOPE)


def check_model_agnostic(root, files):
    findings = []
    for path in files:
        if is_exempt(path):
            continue
        text = read_text(root, path)
        if text is None:
            continue
        vendors = in_vendor_scope(path)
        for number, line in enumerate(text.splitlines(), 1):
            for label in model_agnostic_hits(line, vendors=vendors, path=path):
                findings.append(Finding(path, number, "model-agnostic", "names `{0}`".format(label)))
    return findings


# ---------------------------------------------------------------------------
# dependencies

# The Python 3.9 standard library, used on every interpreter so a module added
# after 3.9 (tomllib, for example) is caught even when lint runs on a newer one.
STDLIB_FALLBACK = frozenset("""
__future__ abc aifc argparse array ast asynchat asyncio asyncore atexit audioop
base64 bdb binascii binhex bisect builtins bz2 cProfile calendar cgi cgitb chunk
cmath cmd code codecs codeop collections colorsys compileall concurrent
configparser contextlib contextvars copy copyreg crypt csv ctypes curses
dataclasses datetime dbm decimal difflib dis distutils doctest email encodings
ensurepip enum errno faulthandler fcntl filecmp fileinput fnmatch formatter
fractions ftplib functools gc genericpath getopt getpass gettext glob graphlib
grp gzip hashlib heapq hmac html http imaplib imghdr imp importlib inspect io
ipaddress itertools json keyword lib2to3 linecache locale logging lzma mailbox
mailcap marshal math mimetypes mmap modulefinder msilib msvcrt multiprocessing
netrc nis nntplib nt ntpath nturl2path numbers opcode operator optparse os
ossaudiodev parser pathlib pdb pickle pickletools pipes pkgutil platform
plistlib poplib posix posixpath pprint profile pstats pty pwd py_compile pyclbr
pydoc pydoc_data pyexpat queue quopri random re readline reprlib resource
rlcompleter runpy sched secrets select selectors shelve shlex shutil signal site
smtpd smtplib sndhdr socket socketserver spwd sqlite3 sre_compile sre_constants
sre_parse ssl stat statistics string stringprep struct subprocess sunau symbol
symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile termios
textwrap threading time timeit tkinter token tokenize trace traceback
tracemalloc tty types typing unicodedata unittest urllib uu uuid venv warnings
wave weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc zipapp zipfile
zipimport zlib zoneinfo
""".split())
STDLIB_MODULES = STDLIB_FALLBACK

DEPENDENCY_MANIFESTS = (
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "requirements*.txt",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
)
PYPROJECT_DEPENDENCIES = re.compile(
    r"^\s*(?:dependencies\s*=\s*\[\s*[^\]\s]|dependencies\s*=\s*\[\s*$|"
    r"\[[^\]]*dependencies[^\]]*\])",
    re.MULTILINE,
)


def import_roots(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module.split(".")[0]


def check_dependencies(root, files):
    findings = []
    for path in files:
        name = posixpath.basename(path)
        if "node_modules" in path.split("/"):
            findings.append(Finding(path, 1, "dependencies", "node_modules is tracked"))
        elif any(fnmatch.fnmatch(name, pattern) for pattern in DEPENDENCY_MANIFESTS):
            findings.append(Finding(path, 1, "dependencies", "dependency manifest is tracked"))
        elif posixpath.splitext(name)[1] in (".js", ".mjs", ".cjs", ".ts"):
            findings.append(Finding(path, 1, "dependencies", "JavaScript or TypeScript source is tracked; Mythify is one Python runtime"))
        elif name == "pyproject.toml":
            text = read_text(root, path) or ""
            match = PYPROJECT_DEPENDENCIES.search(text)
            if match:
                findings.append(Finding(
                    path, line_of(text, match.start()), "dependencies", "pyproject.toml declares dependencies",
                ))
    sources = [
        path for path in files
        if path.endswith(".py") and path.count("/") == 1 and path.split("/")[0] in ("scripts", "tests")
    ]
    local = {posixpath.splitext(posixpath.basename(path))[0] for path in sources}
    for path in sources:
        text = read_text(root, path)
        if text is None:
            continue
        try:
            tree = ast.parse(text, path)
        except SyntaxError as exc:
            findings.append(Finding(path, exc.lineno or 1, "dependencies", "cannot parse: {0}".format(exc.msg)))
            continue
        for number, module in import_roots(tree):
            if module not in STDLIB_MODULES and module not in local:
                findings.append(Finding(
                    path, number, "dependencies",
                    "imports `{0}`, which is neither standard library nor a scripts/ or tests/ module".format(module),
                ))
    return findings


# ---------------------------------------------------------------------------
# mcp-surface

MCP_PROBE = r"""
import json, sys
sys.path.insert(0, "scripts")
import mythify, mythify_mcp, mythify_parser
parser = mythify_parser.build_parser(vars(mythify))
error = ""
try:
    mythify_mcp.build_tools(parser)
except Exception as exc:
    error = "{0}: {1}".format(type(exc).__name__, exc)
print(json.dumps({
    "leaves": [list(path) for path in mythify_mcp.leaf_paths(parser)],
    "allowlist": [list(path) for path in mythify_mcp.TOOL_ALLOWLIST],
    "hatch": mythify_mcp.escape_hatch_tool(parser)["description"],
    "refused": list(mythify_mcp.REFUSED_COMMANDS),
    "error": error,
}))
"""
HATCH_LIST = re.compile(r"Commands without a typed tool: (.*?)\.\s*$")


def allowlist_line(text, path):
    needle = "(" + ", ".join('"{0}"'.format(token) for token in path) + ("," if len(path) == 1 else "") + ")"
    index = text.find(needle)
    return line_of(text, index) if index >= 0 else 1


def check_mcp_surface(root, files):
    module = "scripts/mythify_mcp.py"
    result = run_python(root, ["-c", MCP_PROBE])
    try:
        probe = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail = first_line(result.stderr.splitlines()[-1] if result.stderr.strip() else "") or "no output"
        return [Finding(module, 1, "mcp-surface", "cannot load the parser and MCP server: {0}".format(detail))]
    source = read_text(root, module) or ""
    findings = []
    leaves = [tuple(path) for path in probe["leaves"]]
    allowlist = [tuple(path) for path in probe["allowlist"]]
    leaf_set = set(leaves)
    seen = set()
    for path in allowlist:
        line = allowlist_line(source, path)
        if path in seen:
            findings.append(Finding(module, line, "mcp-surface", "TOOL_ALLOWLIST repeats `{0}`".format(" ".join(path))))
        seen.add(path)
        if path not in leaf_set:
            findings.append(Finding(
                module, line, "mcp-surface",
                "TOOL_ALLOWLIST entry `{0}` is not a parser leaf".format(" ".join(path)),
            ))
        if path and path[0] in probe["refused"]:
            findings.append(Finding(module, line, "mcp-surface", "refused command `{0}` has a typed tool".format(path[0])))
    if probe["error"]:
        findings.append(Finding(module, 1, "mcp-surface", "build_tools failed: {0}".format(probe["error"])))
    expected = [" ".join(path) for path in leaves if path not in seen and path[0] not in probe["refused"]]
    listed_match = HATCH_LIST.search(probe["hatch"])
    listed = listed_match.group(1).split(", ") if listed_match else []
    hatch_line = line_of(source, source.find("def escape_hatch_tool")) if "def escape_hatch_tool" in source else 1
    for command in expected:
        if command not in listed:
            findings.append(Finding(
                module, hatch_line, "mcp-surface",
                "escape-hatch tool description does not list `{0}`".format(command),
            ))
    for command in listed:
        if command not in expected:
            findings.append(Finding(
                module, hatch_line, "mcp-surface",
                "escape-hatch tool description lists `{0}`, which is typed, refused, or not a leaf".format(command),
            ))
    findings.extend(mcp_doc_table_findings(root, allowlist))
    return findings


MCP_DOC = "docs/mcp.md"
MCP_DOC_TABLE = re.compile(r"^\|\s*Group\s*\|\s*Typed tools\s*\|")


def mcp_doc_table_findings(root, allowlist):
    """docs/mcp.md's typed-tool table must name exactly the allowlisted tools."""
    text = read_text(root, MCP_DOC)
    if text is None:
        return [Finding(MCP_DOC, 1, "mcp-surface", "docs/mcp.md is missing")]
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if MCP_DOC_TABLE.match(line)), None)
    if start is None:
        return [Finding(MCP_DOC, 1, "mcp-surface", "docs/mcp.md has no `| Group | Typed tools |` table")]
    documented = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        documented.extend(re.findall(r"`([a-z0-9_]+)`", line.split("|")[2] if line.count("|") >= 3 else ""))
    expected = ["_".join(path).replace("-", "_") for path in allowlist]
    findings = []
    for name in expected:
        if name not in documented:
            findings.append(Finding(MCP_DOC, start + 1, "mcp-surface", "typed tool `{0}` is missing from the table".format(name)))
    for name in documented:
        if name not in expected:
            findings.append(Finding(MCP_DOC, start + 1, "mcp-surface", "table lists `{0}`, which is not a typed tool".format(name)))
    return findings


# ---------------------------------------------------------------------------
# links

FENCE = re.compile(r"^\s{0,3}(```|~~~)")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
REFERENCE_LINK = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")
HTML_LINK = re.compile(r"<(?:img|a|source)\b[^>]*?\s(?:src|href)=[\"']([^\"']+)[\"']", re.IGNORECASE)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def link_targets(text):
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        reference = REFERENCE_LINK.match(line)
        if reference:
            yield number, reference.group(1)
        stripped = INLINE_CODE.sub("", line)
        for pattern in (INLINE_LINK, HTML_LINK):
            for match in pattern.finditer(stripped):
                yield number, match.group(1)


def check_links(root, files):
    file_set = set(files)
    directories = set()
    for path in files:
        parts = path.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
    findings = []
    for path in files:
        if not path.endswith(".md"):
            continue
        text = read_text(root, path)
        if text is None:
            continue
        base = posixpath.dirname(path)
        for number, target in link_targets(text):
            if target.startswith(("#", "//")) or SCHEME.match(target):
                continue
            clean = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not clean:
                continue
            joined = clean.lstrip("/") if clean.startswith("/") else posixpath.join(base, clean)
            resolved = posixpath.normpath(joined)
            if resolved.startswith("../") or resolved == "..":
                findings.append(Finding(path, number, "links", "`{0}` points outside the repository".format(target)))
            elif resolved not in file_set and resolved.rstrip("/") not in directories:
                findings.append(Finding(path, number, "links", "`{0}` does not resolve to a tracked file".format(target)))
    return findings


# ---------------------------------------------------------------------------
# source-size


def check_source_size(root, files):
    script = "scripts/check_runtime_source_size.py"
    if not (root / script).is_file():
        return [Finding(script, 1, "source-size", "size checker is missing")]
    result = run_python(root, [script, "--json", "--root", "."])
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        detail = first_line(result.stderr) or "no output"
        return [Finding(script, 1, "source-size", "checker failed: {0}".format(detail))]
    findings = [
        Finding(
            row["path"], 1, "source-size",
            "{0} nonblank lines exceeds the {1}-line ceiling".format(row["nonblank_lines"], row["limit"]),
        )
        for row in payload.get("violations") or []
    ]
    if result.returncode != 0 and not findings:
        findings.append(Finding(script, 1, "source-size", "checker exited {0}".format(result.returncode)))
    return findings


# ---------------------------------------------------------------------------

RUNNERS = {
    "version": check_version,
    "generated": check_generated,
    "protocol-budget": check_protocol_budget,
    "text": check_text,
    "prose": check_prose,
    "model-agnostic": check_model_agnostic,
    "dependencies": check_dependencies,
    "mcp-surface": check_mcp_surface,
    "links": check_links,
    "source-size": check_source_size,
}


def run_checks(root, names=CHECKS):
    root = Path(root).resolve()
    files = repo_files(root)
    findings = []
    for name in names:
        findings.extend(RUNNERS[name](root, files))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Mythify's maintainer drift checks.",
        epilog="Checks: " + ", ".join(CHECKS) + ".",
    )
    parser.add_argument(
        "--check",
        action="append",
        nargs="+",
        metavar="NAME",
        choices=CHECKS,
        help="Run only these checks. Repeatable. Defaults to every check.",
    )
    parser.add_argument("--json", action="store_true", help="Print findings as JSON.")
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="Repository root to lint. Defaults to the checkout this script is in.",
    )
    args = parser.parse_args(argv)
    names = [name for group in args.check for name in group] if args.check else list(CHECKS)
    names = [name for name in CHECKS if name in names]
    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error("--root is not a directory: {0}".format(args.root))
    findings = run_checks(root, names)
    if args.json:
        print(json.dumps({
            "status": "fail" if findings else "pass",
            "checks": names,
            "findings": [finding._asdict() for finding in findings],
        }, indent=2))
    else:
        for finding in findings:
            print(format_finding(finding))
        if not findings:
            print("[OK] lint passed: {0}.".format(", ".join(names)))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
