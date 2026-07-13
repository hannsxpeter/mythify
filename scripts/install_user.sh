#!/usr/bin/env sh
set -eu

usage() {
  cat <<'USAGE'
Usage: scripts/install_user.sh [--prefix PATH] [--project PATH] [--skip-mcp] [--skip-skills] [--skills-root PATH] [--skip-claude-skills] [--claude-skills-root PATH] [--install-chat-hook] [--hook-root PATH] [--uninstall]

Installs a versioned, self-contained Mythify CLI runtime and user-local
launchers. Mythify chat skills are installed for both runtimes: invoke them
with $skill in Codex and /skill in Claude Code.

Options:
  --prefix PATH               Install launchers under PATH/bin. Default: $HOME/.local
  --project PATH              Initialize Mythify state for that project and print MCP setup.
  --skip-mcp                  Install only the mythify CLI wrapper.
  --skip-skills               Do not install Mythify chat skills (Codex or Claude).
  --skills-root PATH          Install Codex chat skills under PATH. Default: $CODEX_HOME/skills or $HOME/.codex/skills
  --skip-claude-skills        Do not install the Claude Code copy of the chat skills.
  --claude-skills-root PATH   Install Claude chat skills under PATH. Default: $CLAUDE_HOME/skills or $HOME/.claude/skills
  --install-chat-hook         Install the optional report hook helper script.
  --hook-root PATH            Install hook helpers under PATH. Default: $CODEX_HOME/hooks or $HOME/.codex/hooks
  --uninstall                 Remove installed Mythify runtime files and launchers. Project .mythify state is preserved.
  --help                      Show this help.
USAGE
}

fail() {
  printf '%s\n' "[FAIL] $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

preflight_directory() {
  pf_label="$1"
  pf_path="$2"
  if [ -e "$pf_path" ]; then
    [ -d "$pf_path" ] || fail "$pf_label must be a directory: $pf_path"
    [ -w "$pf_path" ] || fail "$pf_label is not writable: $pf_path"
    return 0
  fi
  pf_parent=$(dirname "$pf_path")
  while [ ! -e "$pf_parent" ]; do
    pf_next=$(dirname "$pf_parent")
    [ "$pf_next" != "$pf_parent" ] || break
    pf_parent="$pf_next"
  done
  [ -d "$pf_parent" ] || fail "$pf_label has no directory parent: $pf_path"
  [ -w "$pf_parent" ] || fail "$pf_label parent is not writable: $pf_parent"
}

preflight_file() {
  pf_label="$1"
  pf_path="$2"
  if [ -e "$pf_path" ]; then
    [ -f "$pf_path" ] || fail "$pf_label must be a file: $pf_path"
    [ -w "$pf_path" ] || fail "$pf_label is not writable: $pf_path"
  else
    preflight_directory "$pf_label parent" "$(dirname "$pf_path")"
  fi
}

install_skills_into() {
  sk_label="$1"
  sk_root="$2"
  mkdir -p "$sk_root"
  for skill_name in $mythify_skill_names; do
    skill_dir="$repo_root/skills/$skill_name"
    destination="$sk_root/$skill_name"
    rm -rf "$destination"
    cp -R "$skill_dir" "$destination"
    printf '%s\n' "[OK] Installed $sk_label Mythify chat skill: $destination"
    if [ "${MYTHIFY_INSTALL_TEST_FAIL_AFTER_SKILL_COPY:-0}" = "1" ] && [ "$skill_failure_injected" -eq 0 ]; then
      skill_failure_injected=1
      fail "injected failure after skill copy"
    fi
  done
}

remove_skills_from() {
  sk_label="$1"
  sk_root="$2"
  [ -d "$sk_root" ] || return 0
  for skill_name in $mythify_skill_names; do
    skill_dir="$sk_root/$skill_name"
    [ -e "$skill_dir" ] || continue
    rm -rf "$skill_dir"
    printf '%s\n' "[OK] Removed $sk_label Mythify chat skill: $skill_dir"
  done
}

write_exec_launcher() {
  launcher_path="$1"
  shift
  "$python_bin" - "$launcher_path" "$@" <<'PY'
import shlex
import os
import sys

destination = sys.argv[1]
command = sys.argv[2:]
temporary = "{}.tmp-{}".format(destination, os.getpid())
try:
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("#!/usr/bin/env sh\n")
        handle.write("set -eu\n")
        handle.write(
            "exec {} \"$@\"\n".format(
                " ".join(shlex.quote(item) for item in command)
            )
        )
    os.chmod(temporary, 0o755)
    os.replace(temporary, destination)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

install_cli_runtime() {
  mkdir -p "$install_root"
  cli_stage=$(mktemp -d "$install_root/.cli-stage.XXXXXX")
  mkdir -p "$cli_stage/scripts"
  cp "$repo_root/scripts/mythify.py" "$cli_stage/scripts/mythify.py"
  cp "$repo_root/scripts/install_user.sh" "$cli_stage/scripts/install_user.sh"
  cp "$repo_root/scripts/mythify_chat_report_hook.sh" "$cli_stage/scripts/mythify_chat_report_hook.sh"
  for module in "$repo_root"/scripts/mythify_*.py; do
    [ -f "$module" ] || continue
    cp "$module" "$cli_stage/scripts/$(basename "$module")"
  done
  cp -R "$repo_root/protocol" "$cli_stage/protocol"
  chmod 755 "$cli_stage/scripts/mythify.py"
  chmod 755 "$cli_stage/scripts/install_user.sh"
  chmod 755 "$cli_stage/scripts/mythify_chat_report_hook.sh"

  cli_backup="$install_root/.cli-backup.$$"
  rm -rf "$cli_backup"
  if [ -d "$cli_dir" ]; then
    mv "$cli_dir" "$cli_backup"
  fi
  if ! mv "$cli_stage" "$cli_dir"; then
    cli_stage=""
    if [ -d "$cli_backup" ]; then
      mv "$cli_backup" "$cli_dir"
      cli_backup=""
    fi
    fail "Could not replace the installed Mythify CLI runtime"
  fi
  cli_stage=""
  rm -rf "$cli_backup"
  cli_backup=""
}

write_ownership_manifest() {
  "$python_bin" - \
    "$install_root/install-manifest.json" \
    "$install_root" \
    "$prefix" \
    "$skills_root" \
    "$claude_skills_root" \
    "$hook_root" \
    "$skip_mcp" \
    "$skip_skills" \
    "$skip_claude_skills" \
    "$install_chat_hook" \
    "$mythify_skill_names" \
    "$project_dir" <<'PY'
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


manifest_path = Path(sys.argv[1])
install_root = Path(sys.argv[2]).resolve()
prefix = Path(sys.argv[3]).resolve()
skills_root = Path(sys.argv[4]).resolve()
claude_skills_root = Path(sys.argv[5]).resolve()
hook_root = Path(sys.argv[6]).resolve()
skip_mcp, skip_skills, skip_claude, install_hook = (
    value == "1" for value in sys.argv[7:11]
)
skill_names = sys.argv[11].split()
project_dir = Path(os.path.abspath(sys.argv[12])) if sys.argv[12] else None
token = secrets.token_hex(16)

files = [prefix / "bin" / "mythify", prefix / "bin" / "mythify-uninstall"]
directories = [install_root / "cli"]
if not skip_mcp:
    files.append(prefix / "bin" / "mythify-mcp")
    directories.append(install_root / "mcp-server")
if not skip_skills:
    directories.extend(skills_root / name for name in skill_names)
    if not skip_claude:
        directories.extend(claude_skills_root / name for name in skill_names)
if install_hook:
    files.append(hook_root / "mythify-chat-report-hook.sh")

for directory in directories:
    marker = directory / ".mythify-owned"
    marker.write_text(token + "\n", encoding="utf-8")

manifest = {
    "schema": 1,
    "token": token,
    "skill_names": skill_names,
    "config": {
        "install_root": str(install_root),
        "prefix": str(prefix),
        "skills_root": str(skills_root),
        "claude_skills_root": str(claude_skills_root),
        "hook_root": str(hook_root),
        "skip_mcp": skip_mcp,
        "skip_skills": skip_skills,
        "skip_claude_skills": skip_claude,
        "install_chat_hook": install_hook,
    },
    "files": {str(path.resolve()): digest(path) for path in files},
    "directories": [str(path.resolve()) for path in directories],
}

temporary = manifest_path.with_name(".install-manifest.tmp-{}".format(os.getpid()))
try:
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, manifest_path)
finally:
    if temporary.exists():
        temporary.unlink()
PY
}

prepare_mcp_runtime() {
  pack_dir=$(mktemp -d "${TMPDIR:-/tmp}/mythify-pack.XXXXXX")
  mcp_stage=$(mktemp -d "${TMPDIR:-/tmp}/mythify-mcp-stage.XXXXXX")
  tarball=$(CDPATH= cd -- "$repo_root/mcp-server" && npm pack --silent --pack-destination "$pack_dir")
  tar -xzf "$pack_dir/$tarball" -C "$mcp_stage" --strip-components=1
  npm install --prefix "$mcp_stage" --omit=dev --ignore-scripts >/dev/null
}

install_mcp_runtime() {
  mcp_dir="$install_root/mcp-server"
  mcp_commit_stage=$(mktemp -d "$install_root/.mcp-commit.XXXXXX")
  cp -R "$mcp_stage/." "$mcp_commit_stage"
  mcp_backup="$install_root/.mcp-backup.$$"
  rm -rf "$mcp_backup"
  if [ -d "$mcp_dir" ]; then
    mv "$mcp_dir" "$mcp_backup"
  fi
  if ! mv "$mcp_commit_stage" "$mcp_dir"; then
    mcp_commit_stage=""
    if [ -d "$mcp_backup" ]; then
      mv "$mcp_backup" "$mcp_dir"
      mcp_backup=""
    fi
    fail "Could not replace the installed Mythify MCP runtime"
  fi
  mcp_commit_stage=""
  rm -rf "$mcp_backup"
  mcp_backup=""
}

begin_install_transaction() {
  transaction_backup_dir=$(mktemp -d "${TMPDIR:-/tmp}/mythify-install-rollback.XXXXXX")
  "$python_bin" - \
    "$transaction_backup_dir" \
    "$install_root" \
    "$bin_dir" \
    "$skills_root" \
    "$claude_skills_root" \
    "$hook_root" \
    "$skip_mcp" \
    "$skip_skills" \
    "$skip_claude_skills" \
    "$install_chat_hook" \
    "$mythify_skill_names" \
    "$project_dir" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path


backup_root = Path(sys.argv[1])
install_root = Path(os.path.abspath(sys.argv[2]))
bin_dir = Path(os.path.abspath(sys.argv[3]))
skills_root = Path(os.path.abspath(sys.argv[4]))
claude_skills_root = Path(os.path.abspath(sys.argv[5]))
hook_root = Path(os.path.abspath(sys.argv[6]))
skip_mcp, skip_skills, skip_claude, install_hook = (
    value == "1" for value in sys.argv[7:11]
)
skill_names = sys.argv[11].split()
project_dir = Path(os.path.abspath(sys.argv[12])) if sys.argv[12] else None

targets = [install_root, bin_dir / "mythify", bin_dir / "mythify-uninstall"]
if not skip_mcp:
    targets.append(bin_dir / "mythify-mcp")
if not skip_skills:
    targets.extend(skills_root / name for name in skill_names)
    if not skip_claude:
        targets.extend(claude_skills_root / name for name in skill_names)
if install_hook:
    targets.append(hook_root / "mythify-chat-report-hook.sh")
if project_dir is not None:
    targets.append(project_dir / ".gitignore")
    project_state = project_dir / ".mythify"
    if not project_state.exists():
        targets.append(project_state)

entries_dir = backup_root / "entries"
entries_dir.mkdir()
entries = []
missing_parents = set()
seen = set()
for raw_path in targets:
    path = Path(os.path.abspath(str(raw_path)))
    key = str(path)
    if key in seen:
        continue
    seen.add(key)
    parent = path.parent
    while not parent.exists() and not parent.is_symlink():
        missing_parents.add(str(parent))
        if parent == parent.parent:
            break
        parent = parent.parent
    if path.is_symlink():
        raise SystemExit("[FAIL] Transaction target must not be a symlink: {}".format(path))
    existed = path.exists()
    entry = {
        "path": str(path),
        "existed": existed,
        "kind": None,
        "backup": None,
    }
    if existed:
        backup = entries_dir / str(len(entries))
        if path.is_dir():
            entry["kind"] = "directory"
            shutil.copytree(path, backup, symlinks=True, copy_function=shutil.copy2)
        elif path.is_file():
            entry["kind"] = "file"
            shutil.copy2(path, backup)
        else:
            raise SystemExit("[FAIL] Unsupported transaction target: {}".format(path))
        entry["backup"] = str(backup.relative_to(backup_root))
    entries.append(entry)

manifest = {
    "entries": entries,
    "missing_parents": sorted(
        missing_parents,
        key=lambda value: (len(Path(value).parts), value),
        reverse=True,
    ),
}
(backup_root / "transaction.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
  transaction_active=1
}

rollback_install_transaction() {
  "$python_bin" - "$transaction_backup_dir" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


backup_root = Path(sys.argv[1])
manifest = json.loads(
    (backup_root / "transaction.json").read_text(encoding="utf-8")
)

for entry in manifest["entries"]:
    path = Path(entry["path"])
    if os.path.lexists(path):
        remove_path(path)

for entry in manifest["entries"]:
    if not entry["existed"]:
        continue
    path = Path(entry["path"])
    backup = backup_root / entry["backup"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if entry["kind"] == "directory":
        shutil.copytree(backup, path, symlinks=True, copy_function=shutil.copy2)
    elif entry["kind"] == "file":
        shutil.copy2(backup, path)
    else:
        raise SystemExit("Invalid transaction entry kind")

for raw_path in manifest["missing_parents"]:
    path = Path(raw_path)
    try:
        path.rmdir()
    except OSError:
        pass
PY
}

commit_install_transaction() {
  transaction_active=0
  if rm -rf "$transaction_backup_dir"; then
    transaction_backup_dir=""
  else
    printf '%s\n' "[WARN] Install committed, but the transaction backup could not be removed: $transaction_backup_dir" >&2
  fi
}

pack_dir=""
cli_stage=""
cli_backup=""
cli_dir=""
mcp_stage=""
mcp_commit_stage=""
mcp_backup=""
transaction_active=0
transaction_backup_dir=""
skill_failure_injected=0
cleanup_temporary_dirs() {
  cleanup_status=$?
  trap - EXIT
  set +e
  if [ -n "$pack_dir" ] && [ -d "$pack_dir" ]; then
    rm -rf "$pack_dir"
  fi
  if [ -n "$cli_stage" ] && [ -d "$cli_stage" ]; then
    rm -rf "$cli_stage"
  fi
  if [ -n "$cli_backup" ] && [ -d "$cli_backup" ]; then
    if [ -n "$cli_dir" ] && [ ! -d "$cli_dir" ]; then
      mv "$cli_backup" "$cli_dir"
    else
      rm -rf "$cli_backup"
    fi
  fi
  if [ -n "$mcp_stage" ] && [ -d "$mcp_stage" ]; then
    rm -rf "$mcp_stage"
  fi
  if [ -n "$mcp_commit_stage" ] && [ -d "$mcp_commit_stage" ]; then
    rm -rf "$mcp_commit_stage"
  fi
  if [ -n "$mcp_backup" ] && [ -d "$mcp_backup" ]; then
    if [ -n "${mcp_dir:-}" ] && [ ! -d "$mcp_dir" ]; then
      mv "$mcp_backup" "$mcp_dir"
    else
      rm -rf "$mcp_backup"
    fi
  fi
  if [ "$transaction_active" -eq 1 ] && [ -n "$transaction_backup_dir" ]; then
    if rollback_install_transaction; then
      rm -rf "$transaction_backup_dir"
      transaction_backup_dir=""
      transaction_active=0
    else
      printf '%s\n' "[FAIL] Could not restore the prior Mythify installation; rollback backup preserved at $transaction_backup_dir" >&2
      cleanup_status=1
    fi
  elif [ -n "$transaction_backup_dir" ] && [ -d "$transaction_backup_dir" ]; then
    rm -rf "$transaction_backup_dir"
  fi
  exit "$cleanup_status"
}
trap cleanup_temporary_dirs EXIT

prefix="${PREFIX:-$HOME/.local}"
project=""
project_dir=""
skip_mcp=0
skip_skills=0
skip_claude_skills=0
install_chat_hook=0
uninstall=0
data_root=""
mythify_skill_names="mythify mythify-work mythify-route mythify-verify"
codex_home="${CODEX_HOME:-$HOME/.codex}"
skills_root="${MYTHIFY_SKILLS_ROOT:-$codex_home/skills}"
claude_home="${CLAUDE_HOME:-$HOME/.claude}"
claude_skills_root="${MYTHIFY_CLAUDE_SKILLS_ROOT:-$claude_home/skills}"
hook_root="${MYTHIFY_HOOK_ROOT:-$codex_home/hooks}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix)
      [ "$#" -ge 2 ] || fail "--prefix requires a path"
      prefix="$2"
      shift 2
      ;;
    --project)
      [ "$#" -ge 2 ] || fail "--project requires a path"
      project="$2"
      shift 2
      ;;
    --skip-mcp)
      skip_mcp=1
      shift
      ;;
    --skip-skills)
      skip_skills=1
      shift
      ;;
    --skills-root)
      [ "$#" -ge 2 ] || fail "--skills-root requires a path"
      skills_root="$2"
      shift 2
      ;;
    --skip-claude-skills)
      skip_claude_skills=1
      shift
      ;;
    --claude-skills-root)
      [ "$#" -ge 2 ] || fail "--claude-skills-root requires a path"
      claude_skills_root="$2"
      shift 2
      ;;
    --install-chat-hook)
      install_chat_hook=1
      shift
      ;;
    --hook-root)
      [ "$#" -ge 2 ] || fail "--hook-root requires a path"
      hook_root="$2"
      shift 2
      ;;
    --uninstall)
      uninstall=1
      shift
      ;;
    --data-root)
      [ "$#" -ge 2 ] || fail "--data-root requires a path"
      data_root="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
bin_dir="$prefix/bin"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
require_command python3
python_bin=$(command -v python3)

if [ "$uninstall" -eq 1 ]; then
  if [ -n "$data_root" ]; then
    install_root="$data_root"
  else
    case "$repo_root" in
      */mythify/*/cli) install_root=${repo_root%/cli} ;;
      *)
        version=$(sed -n 's/^VERSION = "\([0-9][0-9.]*\)"$/\1/p' "$repo_root/scripts/mythify.py")
        [ -n "$version" ] || fail "Could not determine the installed Mythify version"
        install_root="$data_home/mythify/$version"
        ;;
    esac
  fi
  case "$install_root" in
    /*) ;;
    *) fail "Unsafe Mythify data root: $install_root" ;;
  esac
  case "$install_root" in
    */../*|*/..|*/./*|*/.) fail "Unsafe Mythify data root: $install_root" ;;
  esac
  if [ -d "$install_root" ]; then
    install_root=$(CDPATH= cd -- "$install_root" && pwd -P)
  fi
  install_base=${install_root%/*}
  install_version=${install_root##*/}
  [ "${install_base##*/}" = "mythify" ] || fail "Unsafe Mythify data root: $install_root"
  version_major=${install_version%%.*}
  version_rest=${install_version#*.}
  [ "$version_rest" != "$install_version" ] || fail "Unsafe Mythify data root: $install_root"
  version_minor=${version_rest%%.*}
  version_patch=${version_rest#*.}
  [ "$version_patch" != "$version_rest" ] || fail "Unsafe Mythify data root: $install_root"
  case "$version_major:$version_minor:$version_patch" in
    *[!0-9:]*|:*|*::|*:) fail "Unsafe Mythify data root: $install_root" ;;
  esac
  "$python_bin" - \
    "$install_root/install-manifest.json" \
    "$install_root" \
    "$prefix" \
    "$skills_root" \
    "$claude_skills_root" \
    "$hook_root" \
    "$skip_mcp" \
    "$skip_skills" \
    "$skip_claude_skills" \
    "$install_chat_hook" <<'PY'
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


def fail(message):
    raise SystemExit("[FAIL] Ownership manifest " + message)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


manifest_path = Path(sys.argv[1])
install_root = Path(sys.argv[2]).resolve()
prefix = Path(sys.argv[3]).resolve()
skills_root = Path(sys.argv[4]).resolve()
claude_skills_root = Path(sys.argv[5]).resolve()
hook_root = Path(sys.argv[6]).resolve()
skip_mcp, skip_skills, skip_claude, install_hook = (
    value == "1" for value in sys.argv[7:11]
)

if not manifest_path.is_file() or manifest_path.is_symlink():
    fail("is missing or unsafe: {}".format(manifest_path))
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, ValueError) as error:
    fail("is unreadable: {}".format(error))

config = {
    "install_root": str(install_root),
    "prefix": str(prefix),
    "skills_root": str(skills_root),
    "claude_skills_root": str(claude_skills_root),
    "hook_root": str(hook_root),
    "skip_mcp": skip_mcp,
    "skip_skills": skip_skills,
    "skip_claude_skills": skip_claude,
    "install_chat_hook": install_hook,
}
if manifest.get("schema") != 1 or manifest.get("config") != config:
    fail("does not match this uninstall request")

files = [prefix / "bin" / "mythify", prefix / "bin" / "mythify-uninstall"]
directories = [install_root / "cli"]
if not skip_mcp:
    files.append(prefix / "bin" / "mythify-mcp")
    directories.append(install_root / "mcp-server")
if not skip_skills:
    directories.extend(skills_root / name for name in manifest["skill_names"])
    if not skip_claude:
        directories.extend(claude_skills_root / name for name in manifest["skill_names"])
if install_hook:
    files.append(hook_root / "mythify-chat-report-hook.sh")

recorded_files = manifest.get("files", {})
for path in files:
    resolved = str(path.resolve())
    if path.is_symlink() or not path.is_file():
        fail("file target is missing or unsafe: {}".format(path))
    if recorded_files.get(resolved) != digest(path):
        fail("file content does not match: {}".format(path))

token = manifest.get("token", "")
recorded_directories = set(manifest.get("directories", []))
for path in directories:
    resolved = str(path.resolve())
    marker = path / ".mythify-owned"
    if path.is_symlink() or not path.is_dir() or resolved not in recorded_directories:
        fail("directory target is missing or unsafe: {}".format(path))
    if marker.is_symlink() or not marker.is_file():
        fail("directory marker is missing or unsafe: {}".format(path))
    if marker.read_text(encoding="utf-8").strip() != token:
        fail("directory marker does not match: {}".format(path))

for path in files:
    path.unlink()
for path in directories:
    shutil.rmtree(path)
manifest_path.unlink()
try:
    install_root.rmdir()
except OSError:
    pass
try:
    install_root.parent.rmdir()
except OSError:
    pass
PY
  printf '%s\n' "[OK] Removed Mythify user installation."
  if [ -n "$project" ]; then
    printf '%s\n' "[OK] Preserved project state: $project/.mythify"
  else
    printf '%s\n' "[OK] Project .mythify state was not modified."
  fi
  exit 0
fi

if [ -n "$project" ]; then
  [ -d "$project" ] || fail "Project directory does not exist: $project"
  project_dir=$(CDPATH= cd -- "$project" && pwd -P)
fi

[ -f "$repo_root/scripts/mythify.py" ] || fail "Run this from a Mythify checkout or extracted CLI artifact"
[ -d "$repo_root/protocol" ] || fail "Missing protocol directory"

version_output=$("$python_bin" "$repo_root/scripts/mythify.py" --version)
case "$version_output" in
  "Mythify v"*) version=${version_output#Mythify v} ;;
  *) fail "Could not determine the Mythify CLI version" ;;
esac
case "$version" in
  *[!0-9.]*|.*|*.) fail "Invalid Mythify CLI version: $version" ;;
esac
install_root="$data_home/mythify/$version"
cli_dir="$install_root/cli"

if [ "$skip_skills" -eq 0 ]; then
  for skill_name in $mythify_skill_names; do
    [ -d "$repo_root/skills/$skill_name" ] || fail "Missing skill directory: skills/$skill_name"
  done
fi
if [ "$install_chat_hook" -eq 1 ]; then
  [ -f "$repo_root/scripts/mythify_chat_report_hook.sh" ] || fail "Missing scripts/mythify_chat_report_hook.sh"
fi
if [ "$skip_mcp" -eq 0 ]; then
  [ -f "$repo_root/mcp-server/package.json" ] || fail "Missing mcp-server/package.json; use --skip-mcp with the standalone CLI artifact"
  require_command node
  require_command npm
  require_command tar
  node_bin=$(command -v node)
  node_version=$(node -p "process.versions.node")
  node_major=${node_version%%.*}
  case "$node_major" in
    ''|*[!0-9]*) fail "Could not determine Node.js major version: $node_version" ;;
  esac
  [ "$node_major" -ge 20 ] || fail "Mythify MCP requires Node.js 20 or newer; found $node_version"
  mcp_version=$(node -p "require(process.argv[1]).version" "$repo_root/mcp-server/package.json")
  [ "$mcp_version" = "$version" ] || fail "CLI version $version does not match MCP version $mcp_version"
fi

preflight_directory "Install prefix" "$prefix"
preflight_directory "Binary destination" "$bin_dir"
preflight_file "Mythify launcher" "$bin_dir/mythify"
preflight_file "Mythify uninstaller" "$bin_dir/mythify-uninstall"
preflight_directory "Data destination" "$data_home"
preflight_directory "Versioned data destination" "$install_root"
preflight_directory "CLI data destination" "$cli_dir"
preflight_file "Ownership manifest" "$install_root/install-manifest.json"
if [ "$skip_skills" -eq 0 ]; then
  preflight_directory "Skill destination" "$skills_root"
  for skill_name in $mythify_skill_names; do
    preflight_directory "Skill destination" "$skills_root/$skill_name"
  done
  if [ "$skip_claude_skills" -eq 0 ]; then
    preflight_directory "Claude skill destination" "$claude_skills_root"
    for skill_name in $mythify_skill_names; do
      preflight_directory "Claude skill destination" "$claude_skills_root/$skill_name"
    done
  fi
fi
if [ "$install_chat_hook" -eq 1 ]; then
  preflight_directory "Hook destination" "$hook_root"
  preflight_file "Hook destination" "$hook_root/mythify-chat-report-hook.sh"
fi
if [ "$skip_mcp" -eq 0 ]; then
  preflight_directory "MCP data destination" "$install_root/mcp-server"
  preflight_file "MCP launcher" "$bin_dir/mythify-mcp"
  prepare_mcp_runtime
fi

begin_install_transaction

if [ -n "$project_dir" ]; then
  (cd "$project_dir" && "$python_bin" "$repo_root/scripts/mythify.py" init >/dev/null)
fi

mkdir -p "$bin_dir"
install_cli_runtime

write_exec_launcher "$bin_dir/mythify" "$python_bin" "$cli_dir/scripts/mythify.py"
set -- \
  sh \
  "$cli_dir/scripts/install_user.sh" \
  --uninstall \
  --data-root "$install_root" \
  --prefix "$prefix" \
  --skills-root "$skills_root" \
  --claude-skills-root "$claude_skills_root" \
  --hook-root "$hook_root"
if [ "$skip_mcp" -eq 1 ]; then
  set -- "$@" --skip-mcp
fi
if [ "$skip_skills" -eq 1 ]; then
  set -- "$@" --skip-skills
fi
if [ "$skip_claude_skills" -eq 1 ]; then
  set -- "$@" --skip-claude-skills
fi
if [ "$install_chat_hook" -eq 1 ]; then
  set -- "$@" --install-chat-hook
fi
if [ -n "$project_dir" ]; then
  set -- "$@" --project "$project_dir"
fi
write_exec_launcher "$bin_dir/mythify-uninstall" "$@"

printf '%s\n' "[OK] Installed mythify CLI: $bin_dir/mythify"
printf '%s\n' "[OK] Installed CLI runtime: $cli_dir"
printf '%s\n' "[OK] Installed uninstaller: $bin_dir/mythify-uninstall"

if [ "$install_chat_hook" -eq 1 ]; then
  [ -f "$repo_root/scripts/mythify_chat_report_hook.sh" ] || fail "Missing scripts/mythify_chat_report_hook.sh"
  mkdir -p "$hook_root"
  cp "$repo_root/scripts/mythify_chat_report_hook.sh" "$hook_root/mythify-chat-report-hook.sh"
  chmod 755 "$hook_root/mythify-chat-report-hook.sh"
  printf '%s\n' "[OK] Installed chat report hook helper: $hook_root/mythify-chat-report-hook.sh"
fi

if [ "$skip_mcp" -eq 0 ]; then
  install_mcp_runtime
  write_exec_launcher "$bin_dir/mythify-mcp" "$node_bin" "$mcp_dir/src/index.js"

  printf '%s\n' "[OK] Installed mythify MCP: $bin_dir/mythify-mcp"
  printf '%s\n' "[OK] Installed MCP package: $mcp_dir"
fi

if [ "$skip_skills" -eq 0 ]; then
  [ -d "$repo_root/skills" ] || fail "Missing skills directory"
  install_skills_into "Codex" "$skills_root"
  if [ "$skip_claude_skills" -eq 0 ]; then
    install_skills_into "Claude" "$claude_skills_root"
  fi
fi

write_ownership_manifest

if [ -n "$project" ]; then
  printf '%s\n' "[OK] Initialized project state: $project_dir/.mythify"

  if [ "$skip_mcp" -eq 0 ]; then
    cat <<EOF
[OK] Codex MCP setup command:
codex mcp add mythify \\
  --env MYTHIFY_DIR=$project_dir/.mythify \\
  --env MYTHIFY_HOST_PLATFORM=codex-desktop \\
  -- $bin_dir/mythify-mcp
EOF
  fi
fi

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *)
    printf '%s\n' "[WARN] Add $bin_dir to PATH if your shell cannot find mythify."
    ;;
esac

commit_install_transaction
