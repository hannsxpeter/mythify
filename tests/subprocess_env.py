"""Faster subprocesses for the suite, with the same behavior.

The suite starts Python and git subprocesses thousands of times: fresh CLI
runs (the installer, MCP, and lint tests, and every call warm_cli.py cannot
fork), the commands those runs verify, and git for every fixture and almost
every CLI call. Test modules that start subprocesses import this module once;
it adjusts this test process's environment, which every child inherits, in
two ways. Each one changes only how fast a child starts, never what it runs.

Bytecode. When PYTHONDONTWRITEBYTECODE is set, no child may write a .pyc
file, so every fresh CLI run compiled every Mythify module from source again
(about 45 ms per call). A system Python that keeps its standard-library cache
under $HOME also compiled the standard library again in every test that sets
a throwaway HOME (about 70 ms more). Children now get a PYTHONPYCACHEPREFIX in
a temporary directory owned by this process and removed at exit, and may
write there. The source tree and the user's own cache locations stay free of
.pyc files, which is what PYTHONDONTWRITEBYTECODE protects. A child that sets
the variable again itself (the lint, the installer tests) still writes
nothing. Without the variable children already cache bytecode as usual.

Git. On macOS /usr/bin/git is a trampoline that looks up the developer tools
on every call before it runs the real git (about 8 ms per call). When the git
on PATH is that trampoline, a temporary directory holding a link to the git
it would run (`xcrun --find git`) goes first on PATH, so the same git binary
runs without the lookup.
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile

GIT_TRAMPOLINE = "/usr/bin/git"


def _private_directory(prefix):
    folder = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, folder, True)
    return folder


def share_bytecode_cache(environ):
    """Give ENVIRON's children a private bytecode cache; return its path, or None."""
    if not environ.get("PYTHONDONTWRITEBYTECODE"):
        return None
    prefix = _private_directory("mythify-test-pycache-")
    environ["PYTHONPYCACHEPREFIX"] = prefix
    del environ["PYTHONDONTWRITEBYTECODE"]
    return prefix


def skip_git_trampoline(environ):
    """Put the git that /usr/bin/git would run first on PATH; return its folder, or None."""
    if sys.platform != "darwin":
        return None
    found = shutil.which("git", path=environ.get("PATH"))
    if not found or os.path.realpath(found) != GIT_TRAMPOLINE:
        return None
    try:
        lookup = subprocess.run(
            ["xcrun", "--find", "git"], capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    real = lookup.stdout.strip()
    if lookup.returncode != 0 or not os.path.isabs(real) or not os.access(real, os.X_OK):
        return None
    folder = _private_directory("mythify-test-git-")
    os.symlink(real, os.path.join(folder, "git"))
    environ["PATH"] = folder + os.pathsep + environ.get("PATH", "")
    return folder


CACHE_PREFIX = share_bytecode_cache(os.environ)
GIT_FOLDER = skip_git_trampoline(os.environ)
