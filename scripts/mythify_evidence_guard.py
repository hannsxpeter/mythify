"""Advisory evidence-quality guards for Mythify.

The strict gate proves that a command exited 0. These helpers watch the cheap
ways that proof detaches from the work it stands for: a verifier that always
passes, a test runner that collected nothing, a session running with a legacy
gate opt-out active, and a ledger whose chained lines no longer hash together.
Most of it is advisory material for warnings and attention items. The one
exception is noop_verifier_reason: review prove and product measure refuse a
command it flags. Nothing here upgrades or downgrades recorded evidence.
"""

import hashlib
import json
import shlex

# noop_verifier_reason reads a command the way /bin/sh would run it, without
# running it. It is a heuristic: it catches common forms of a command that
# cannot fail (`true;`, `exit 0`, `pytest || true`, `sh -c true`, `/bin/echo
# ok`) and answers "not a no-op" for anything it cannot read with confidence,
# such as groups, conditionals, or `set -e`.
ALWAYS_ZERO = "zero"
UNKNOWN = "unknown"
NOOP_WORDS = ("true", ":")
PRINT_WORDS = ("echo", "printf")
SHELL_NAMES = ("sh", "bash", "dash", "zsh", "ksh")
SHELL_PUNCTUATION = "();<>|&\n"
TWO_CHAR_OPERATORS = ("&&", "||", ">>", "<<", ">&", "<&", "&>", "|&", ";;")
LIST_SEPARATORS = (";", "\n", "&&", "||")
SEGMENT_OPERATORS = ("|", ">", "<", ">>", "<<", ">&", "<&", "&>")
# Tokens and leading words after which control flow cannot be read statically:
# groups, background jobs, compound commands, and builtins that change how a
# failure propagates. A command containing any of them is never flagged.
UNREADABLE_TOKENS = ("(", ")", "{", "}", "&", "|&", ";;")
UNREADABLE_WORDS = frozenset({
    "if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "select", "function", "[[", "set", "exec", "trap", "eval",
    "source", ".", "return", "kill", "shopt",
})
MAX_SHELL_DEPTH = 3
# Test-runner output that means the green exit code exercised zero tests.
ZERO_TEST_PATTERNS = (
    "ran 0 tests",
    "no tests ran",
    "collected 0 items",
    "no tests collected",
    "no test files",
    "0 passing",
)
FALSE_ENV_VALUES = ("0", "false", "no", "off")
# Gate opt-outs an agent can set for itself. MYTHIFY_DISABLE_RUN is listed even
# though it fails closed: a session that cannot execute checks is worth naming.
LEGACY_OPT_OUTS = (
    (
        "MYTHIFY_REQUIRE_VERIFIED_STEP",
        "step completion accepts prose-only evidence",
    ),
    (
        "MYTHIFY_REQUIRE_HUMAN_INPUT",
        "HITL tickets, product approvals, and bet verdicts proceed without the human's words",
    ),
)


def _shell_tokens(command):
    """Words and operators of COMMAND, or None when it cannot be split.

    Quoted words keep their quotes, so a quoted `;` is never read as an
    operator. A `$( ... )` command substitution is folded into one word. The
    lexer does not process backslash escapes, so a backslash outside single
    quotes makes the split untrustworthy and returns None.
    """
    lexer = shlex.shlex(command, posix=False, punctuation_chars=SHELL_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        raw = list(lexer)
    except ValueError:
        return None
    if any("\\" in token and not token.startswith("'") for token in raw):
        return None
    tokens = []
    for token in raw:
        if token[0] not in SHELL_PUNCTUATION:
            tokens.append(token)
            continue
        index = 0
        while index < len(token):
            pair = token[index:index + 2]
            step = 2 if pair in TWO_CHAR_OPERATORS else 1
            tokens.append(token[index:index + step])
            index += step
    folded = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.endswith("$") and tokens[index + 1:index + 2] == ["("]:
            depth = 0
            end = index + 1
            while end < len(tokens):
                depth += {"(": 1, ")": -1}.get(tokens[end], 0)
                if depth == 0:
                    break
                end += 1
            if end == len(tokens):
                return None
            folded.append(" ".join(tokens[index:end + 1]))
            index = end + 1
            continue
        folded.append(token)
        index += 1
    return folded


def _strip_wrapping_group(tokens):
    """Drop parentheses or braces that wrap the whole command."""
    while len(tokens) >= 2 and (tokens[0], tokens[-1]) in (("(", ")"), ("{", "}")):
        opener, closer = tokens[0], tokens[-1]
        depth = 0
        for index, token in enumerate(tokens):
            depth += 1 if token == opener else -1 if token == closer else 0
            if depth == 0:
                break
        if index != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return tokens


def _unquote(word):
    if len(word) >= 2 and word[0] == word[-1] and word[0] in "'\"":
        return word[1:-1]
    return word


def _command_name(word):
    return word.rsplit("/", 1)[-1].lower()


def _segment_kind(words, depth):
    """Classify one simple command: noop, print, exit forms, or unknown."""
    if any(word in SEGMENT_OPERATORS for word in words):
        return "unknown", None
    words = [_unquote(word) for word in words]
    while words:
        name = _command_name(words[0])
        if name == "env":
            words = words[1:]
            while words and (words[0].startswith("-") or "=" in words[0]):
                words = words[1:]
        elif name == "command" and len(words) > 1 and not words[1].startswith("-"):
            words = words[1:]
        else:
            break
    if not words:
        return "unknown", None
    name = _command_name(words[0])
    if name in UNREADABLE_WORDS:
        return "unreadable", None
    if name in SHELL_NAMES and len(words) == 3 and words[1] == "-c":
        status, _ = _script_status(words[2], depth + 1)
        return "noop" if status == ALWAYS_ZERO else "unknown", None
    if name in NOOP_WORDS:
        return "noop", None
    if name in PRINT_WORDS:
        return "print", None
    if name == "exit":
        if len(words) == 1:
            return "exit", None
        return "exit", ALWAYS_ZERO if words[1:] == ["0"] else UNKNOWN
    return "unknown", None


def _script_status(command, depth=0):
    """(ALWAYS_ZERO or UNKNOWN, kinds) for COMMAND run by /bin/sh.

    Walks the `;`, newline, `&&`, and `||` list left to right, tracking whether
    `$?` is certainly 0 and whether the next command certainly runs. An `exit`
    that certainly runs ends the walk with its status; one that may run with
    a status other than 0 makes the result UNKNOWN.
    """
    if depth > MAX_SHELL_DEPTH:
        return UNKNOWN, []
    tokens = _shell_tokens(str(command or ""))
    if tokens is None:
        return UNKNOWN, []
    tokens = _strip_wrapping_group(tokens)
    if any(token in UNREADABLE_TOKENS for token in tokens):
        return UNKNOWN, []
    items = []
    operator, words = None, []
    for token in tokens + [";"]:
        if token not in LIST_SEPARATORS:
            words.append(token)
            continue
        if words:
            items.append((operator, words))
        elif operator in ("&&", "||") or token in ("&&", "||"):
            # `&&` or `||` with nothing on one side is a syntax error.
            return UNKNOWN, []
        operator, words = token, []
    if not items:
        return UNKNOWN, []
    status = ALWAYS_ZERO
    kinds = []
    for operator, words in items:
        kind, exit_status = _segment_kind(words, depth)
        if kind == "unreadable":
            return UNKNOWN, []
        kinds.append(kind)
        if kind == "exit" and exit_status is None:
            # A bare `exit` keeps $?: 0 after `&&` ran it, unknown after `||`.
            exit_status = {"&&": ALWAYS_ZERO, "||": UNKNOWN}.get(operator, status)
        own = exit_status if kind == "exit" else (
            ALWAYS_ZERO if kind in ("noop", "print") else UNKNOWN
        )
        if operator == "&&":
            certain = status == ALWAYS_ZERO
            status = own if certain else UNKNOWN
        elif operator == "||":
            certain = False
            if status != ALWAYS_ZERO:
                if kind == "exit" and own != ALWAYS_ZERO:
                    return UNKNOWN, []
                status = own
            continue
        else:
            certain = True
            status = own
        if kind == "exit":
            if certain:
                return own, kinds
            if own != ALWAYS_ZERO:
                return UNKNOWN, []
    return status, kinds


def noop_verifier_reason(command):
    """Why COMMAND can never fail, or None when it looks like a real check.

    A heuristic, not a guarantee: it reads the command's `;`, `&&`, and `||`
    list and flags it when the exit status is 0 on every path. See
    _script_status for what it reads and what it leaves alone.
    """
    status, kinds = _script_status(command)
    if status != ALWAYS_ZERO or not kinds:
        return None
    if all(kind == "print" for kind in kinds):
        return "the command only prints and exits 0"
    return "the command always exits 0"


def trivial_pass_reason(record):
    """Why a passing executed RECORD proves nothing, or None when it holds."""
    if record.get("kind") != "executed" or record.get("verified") is not True:
        return None
    noop = noop_verifier_reason(record.get("command"))
    if noop:
        return noop
    output = "{0}\n{1}".format(
        record.get("stdout_tail") or "", record.get("stderr_tail") or ""
    ).lower()
    for pattern in ZERO_TEST_PATTERNS:
        if pattern in output:
            return "the run reported '{0}'".format(pattern)
    return None


def ledger_chain_breaks(text):
    """1-based indexes of chained records whose prev_sha256 does not match.

    Each chained record carries the sha256 of the raw line before it, so an
    edited, inserted, or deleted line breaks the next record's link. The first
    line is never judged (its predecessor may live in a compaction archive),
    and records without prev_sha256 are legacy and stay silent.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    breaks = []
    for index in range(1, len(lines)):
        try:
            record = json.loads(lines[index])
        except ValueError:
            continue
        prev = record.get("prev_sha256") if isinstance(record, dict) else None
        if prev is None:
            continue
        expected = hashlib.sha256(lines[index - 1].encode("utf-8")).hexdigest()
        if prev != expected:
            breaks.append(index + 1)
    return breaks


def run_disabled(environ):
    """True when MYTHIFY_DISABLE_RUN asks every command runner to refuse.

    The kill switch fails closed: any value other than empty or a false word
    (0, false, no, off; case and surrounding space ignored) disables runs.
    Every runner and the status report share this one parse, so status can
    never name the switch while a runner still executes.
    """
    raw = str((environ or {}).get("MYTHIFY_DISABLE_RUN", "") or "").strip().lower()
    return bool(raw) and raw not in FALSE_ENV_VALUES


def active_legacy_opt_outs(environ):
    """Legacy gate opt-outs currently active in ENVIRON, oldest contract first."""
    active = []
    for name, effect in LEGACY_OPT_OUTS:
        raw = str((environ or {}).get(name, "")).strip().lower()
        if raw in FALSE_ENV_VALUES:
            active.append({"name": name, "effect": effect})
    if run_disabled(environ):
        active.append({
            "name": "MYTHIFY_DISABLE_RUN",
            "effect": "verify run refuses to execute; only attested claims can be recorded",
        })
    return active
