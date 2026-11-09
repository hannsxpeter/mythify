"""Evidence reuse: let an identical, passing run stand for a new run.

`verify run --reuse` and `plan verify --reuse` skip re-executing a command when
the ledger already holds a passing run of the same command at the same commit,
worktree digest, Mythify version, and environment digest. The reused record is
a new ledger row that points at the original, so the step gate counts it like
a fresh run. The digest does not cover files ignored by .gitignore, services,
or the network, which is why reuse is opt-in; MYTHIFY_REUSE_ENV names extra
environment variables to fold into the digest.
"""

import hashlib
import os
import platform
import shlex
import shutil

from mythify_evidence_guard import noop_verifier_reason, trivial_pass_reason

REUSE_ENV_NAME = "MYTHIFY_REUSE_ENV"
# Provenance fields that must all be equal, and non-null where noted, for reuse.
KEY_FIELDS = ("git_commit", "worktree_digest", "mythify_version", "environment_digest")
REQUIRED_FIELDS = ("git_commit", "worktree_digest")


REUSE_HELP = (
    "Reuse an earlier passing run of the same command at the same commit, "
    "worktree digest, version, and environment instead of executing it again. "
    "Files ignored by .gitignore, services, and the network are not covered; "
    "name extra environment inputs in MYTHIFY_REUSE_ENV."
)


def _first_word(command):
    try:
        words = shlex.split(str(command or ""))
    except ValueError:
        words = str(command or "").split()
    return words[0] if words else ""


def environment_digest(command, environ=None):
    """sha256 over the machine, PATH, the command's program, and declared env.

    Missing pieces hash as empty strings, so the digest is always defined.
    """
    environ = os.environ if environ is None else environ
    program, size, mtime = "", "", ""
    word = _first_word(command)
    found = shutil.which(word, path=environ.get("PATH")) if word else None
    if found:
        try:
            program = os.path.realpath(found)
            info = os.stat(program)
            size, mtime = str(info.st_size), str(info.st_mtime_ns)
        except OSError:
            program, size, mtime = "", "", ""
    names = sorted({
        name.strip() for name in str(environ.get(REUSE_ENV_NAME, "")).split(",")
        if name.strip()
    })
    parts = [
        platform.system(), platform.release(), platform.machine(),
        environ.get("PATH", ""), program, size, mtime,
    ] + ["{0}={1}".format(name, environ.get(name, "")) for name in names]
    return hashlib.sha256("\0".join(parts).encode("utf-8", "replace")).hexdigest()


def _passed(record):
    return record.get("verified") is True and record.get("exit_code") == 0


def find_reusable_run(records, command, provenance):
    """Return (original, reason): the passing run to reuse, or None and why not.

    RECORDS is the parsed verification ledger and PROVENANCE the current one
    (including environment_digest). The newest executed record whose key
    matches decides: a reused record is followed to its original, and a
    failing, no-op, or zero-test run is never reused. Pure: reads only.
    """
    provenance = provenance or {}
    for field in REQUIRED_FIELDS:
        if not provenance.get(field):
            return None, "the current {0} is unavailable".format(field)
    latest = None
    for record in records:
        recorded = record.get("provenance")
        if (
            record.get("kind") == "executed"
            and record.get("command") == command
            and isinstance(recorded, dict)
            and all(recorded.get(f) == provenance.get(f) for f in KEY_FIELDS)
        ):
            latest = record
    if latest is None:
        return None, "no earlier run matches the command, commit, worktree, and environment"
    if latest.get("reused_from"):
        wanted = latest["reused_from"]
        latest = next((r for r in records if r.get("id") == wanted), None)
        if latest is None:
            return None, "the original run {0} is missing from the ledger".format(wanted)
    if not _passed(latest):
        return None, "the latest matching run did not pass"
    if not latest.get("id"):
        return None, "the matching run has no id to point at"
    trivial = noop_verifier_reason(command) or trivial_pass_reason(latest)
    if trivial:
        return None, "the matching run proves nothing ({0})".format(trivial)
    return latest, ""


def reused_run_fields(original):
    """The run-derived fields a reused record copies from ORIGINAL."""
    fields = {
        "reused_from": original["id"],
        "exit_code": original["exit_code"],
        "verified": original["verified"],
        "duration_seconds": 0,
        "reused_duration_seconds": original.get("duration_seconds", 0),
        "stdout_tail": original.get("stdout_tail", ""),
        "stderr_tail": original.get("stderr_tail", ""),
    }
    if original.get("test_count") is not None:
        fields["test_count"] = original["test_count"]
    return fields


def reuse_hit_message(original):
    return (
        "[OK] Reused passing run {0} (same command, commit, worktree digest, "
        "and environment); not re-executed.".format(original["id"])
    )


def reuse_miss_message(reason):
    return "[INFO] Running the command: no matching passing run ({0}).".format(reason)
