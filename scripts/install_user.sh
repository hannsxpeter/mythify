#!/usr/bin/env sh
set -eu

usage() {
  cat <<'USAGE'
Usage: scripts/install_user.sh [--prefix PATH] [--project PATH] [--skills-root PATH]... [--skip-skills] [--uninstall]

Installs a versioned, self-contained Mythify CLI runtime and user-local
launchers: mythify, mythify-mcp (the zero-dependency MCP stdio server), and
mythify-uninstall. Mythify chat skills are copied into each skills root;
invoke them the way your host runs skills.

Options:
  --prefix PATH       Install launchers under PATH/bin. Default: $HOME/.local
  --project PATH      Initialize Mythify state for that project.
  --skills-root PATH  Install chat skills under PATH. Repeat for several roots.
                      Default: DIR/skills for each existing DIR among
                      $CLAUDE_CONFIG_DIR or ~/.claude, $CODEX_HOME or ~/.codex,
                      ~/.cursor, and ~/.agents; ~/.agents/skills when none exist.
  --skip-skills       Do not install Mythify chat skills.
  --skip-mcp          Accepted for older install commands; has no effect.
  --uninstall         Remove installed Mythify runtime files and launchers. Project .mythify state is preserved.
  --help              Show this help.
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

# Skill roots are kept as one newline-separated list of absolute paths.
newline='
'

add_skills_root() {
  asr_path="$1"
  [ -n "$asr_path" ] || fail "--skills-root requires a path"
  case "$asr_path" in
    *"$newline"*) fail "Skills root must not contain a newline: $asr_path" ;;
    /*) ;;
    *) asr_path="$PWD/$asr_path" ;;
  esac
  case "$newline$skills_roots$newline" in
    *"$newline$asr_path$newline"*) return 0 ;;
  esac
  skills_roots="${skills_roots:+$skills_roots$newline}$asr_path"
}

detect_default_skills_roots() {
  for dsr_base in \
    "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" \
    "${CODEX_HOME:-$HOME/.codex}" \
    "$HOME/.cursor" \
    "$HOME/.agents"; do
    if [ -d "$dsr_base" ]; then
      add_skills_root "$dsr_base/skills"
    fi
  done
  if [ -z "$skills_roots" ]; then
    add_skills_root "$HOME/.agents/skills"
  fi
}

for_each_skills_root() {
  fe_callback="$1"
  fe_rest="$skills_roots"
  while [ -n "$fe_rest" ]; do
    case "$fe_rest" in
      *"$newline"*)
        fe_root=${fe_rest%%"$newline"*}
        fe_rest=${fe_rest#*"$newline"}
        ;;
      *)
        fe_root=$fe_rest
        fe_rest=""
        ;;
    esac
    "$fe_callback" "$fe_root"
  done
}

preflight_skills_root() {
  preflight_directory "Skill destination" "$1"
  for skill_name in $mythify_skill_names; do
    preflight_directory "Skill destination" "$1/$skill_name"
    # Replace only a folder that holds this Mythify skill or an install marker;
    # never delete someone else's skill that happens to share the name.
    if [ -e "$1/$skill_name" ] && [ ! -e "$1/$skill_name/.mythify-owned" ] \
      && ! grep -qx "name: $skill_name" "$1/$skill_name/SKILL.md" 2>/dev/null; then
      fail "Skill destination holds a different skill (no 'name: $skill_name' in SKILL.md): $1/$skill_name. Move it away or pass --skills-root."
    fi
  done
}

install_skills_into() {
  sk_root="$1"
  mkdir -p "$sk_root"
  for skill_name in $mythify_skill_names; do
    destination="$sk_root/$skill_name"
    rm -rf "$destination"
    cp -R "$repo_root/skills/$skill_name" "$destination"
    printf '%s\n' "[OK] Installed Mythify chat skill: $destination"
    if [ "${MYTHIFY_INSTALL_TEST_FAIL_AFTER_SKILL_COPY:-0}" = "1" ] && [ "$skill_failure_injected" -eq 0 ]; then
      skill_failure_injected=1
      fail "injected failure after skill copy"
    fi
  done
}

# One Python helper owns every ownership-manifest and transaction rule, so the
# launcher list, the owned-directory list, and the digest are written once.
install_helper() {
  "$python_bin" - "$@" <<'PY'
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

LAUNCHERS = ("mythify", "mythify-mcp", "mythify-uninstall")
MANIFEST_NAME = "install-manifest.json"
MARKER_NAME = ".mythify-owned"
VERSION_DIR = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def fail(message):
    raise SystemExit("[FAIL] Ownership manifest " + message)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def unique(items):
    seen = set()
    result = []
    for item in items:
        if str(item) not in seen:
            seen.add(str(item))
            result.append(item)
    return result


def split_roots(text):
    return unique(line for line in text.split("\n") if line)


def launcher_files(prefix):
    return [prefix / "bin" / name for name in LAUNCHERS]


def owned_directories(install_root, skill_roots, skill_names):
    directories = [install_root / "cli"]
    for root in skill_roots:
        directories.extend(Path(root) / name for name in skill_names)
    return unique(directories)


def manifest_skill_roots(config):
    """Skill roots a manifest owns. Schema 1 (5.x) kept two host-specific keys."""
    if "skills_roots" in config:
        return [str(root) for root in config.get("skills_roots") or []]
    if config.get("skip_skills"):
        return []
    roots = [config.get("skills_root")]
    if not config.get("skip_claude_skills"):
        roots.append(config.get("claude_skills_root"))
    return [str(root) for root in roots if root]


def load_manifest(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("is missing or unsafe: {}".format(path))
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("is unreadable: {}".format(error))
    if manifest.get("schema") not in (1, 2) or not isinstance(manifest.get("config"), dict):
        raise ValueError("has an unknown schema: {}".format(path))
    return manifest


def file_problem(manifest, path):
    if path.is_symlink() or not path.is_file():
        return "file target is missing or unsafe: {}".format(path)
    if manifest.get("files", {}).get(str(path.resolve())) != digest(path):
        return "file content does not match: {}".format(path)
    return None


def directory_problem(manifest, path):
    marker = path / MARKER_NAME
    recorded = set(manifest.get("directories", []))
    if path.is_symlink() or not path.is_dir() or str(path.resolve()) not in recorded:
        return "directory target is missing or unsafe: {}".format(path)
    if marker.is_symlink() or not marker.is_file():
        return "directory marker is missing or unsafe: {}".format(path)
    if marker.read_text(encoding="utf-8").strip() != manifest.get("token", ""):
        return "directory marker does not match: {}".format(path)
    return None


def atomic_write(path, text, mode):
    temporary = path.with_name(".{}.tmp-{}".format(path.name, os.getpid()))
    try:
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def begin_transaction(backup_root, install_root, prefix, roots_text, names, project):
    backup_root = Path(backup_root)
    install_root = Path(os.path.abspath(install_root))
    prefix = Path(os.path.abspath(prefix))
    roots = [os.path.abspath(root) for root in split_roots(roots_text)]
    directories = owned_directories(install_root, roots, names.split())
    targets = [install_root] + launcher_files(prefix) + directories[1:]
    if project:
        project_dir = Path(os.path.abspath(project))
        targets.append(project_dir / ".gitignore")
        if not (project_dir / ".mythify").exists():
            targets.append(project_dir / ".mythify")
    entries_dir = backup_root / "entries"
    entries_dir.mkdir()
    entries = []
    missing_parents = set()
    for path in unique(Path(os.path.abspath(str(item))) for item in targets):
        parent = path.parent
        while not parent.exists() and not parent.is_symlink():
            missing_parents.add(str(parent))
            if parent == parent.parent:
                break
            parent = parent.parent
        if path.is_symlink():
            raise SystemExit("[FAIL] Transaction target must not be a symlink: {}".format(path))
        entry = {"path": str(path), "existed": path.exists(), "kind": None, "backup": None}
        if entry["existed"]:
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
    transaction = {
        "entries": entries,
        "missing_parents": sorted(
            missing_parents, key=lambda value: (len(Path(value).parts), value), reverse=True
        ),
    }
    (backup_root / "transaction.json").write_text(
        json.dumps(transaction, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def remove_path(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def rollback_transaction(backup_root):
    backup_root = Path(backup_root)
    transaction = json.loads((backup_root / "transaction.json").read_text(encoding="utf-8"))
    for entry in transaction["entries"]:
        path = Path(entry["path"])
        if os.path.lexists(path):
            remove_path(path)
    for entry in transaction["entries"]:
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
    for raw_path in transaction["missing_parents"]:
        try:
            Path(raw_path).rmdir()
        except OSError:
            pass


def write_manifest(install_root, prefix, roots_text, names):
    install_root = Path(install_root).resolve()
    prefix = Path(prefix).resolve()
    skill_names = names.split()
    roots = unique(str(Path(root).resolve()) for root in split_roots(roots_text))
    files = launcher_files(prefix)
    directories = owned_directories(install_root, roots, skill_names)
    token = secrets.token_hex(16)
    for directory in directories:
        (directory / MARKER_NAME).write_text(token + "\n", encoding="utf-8")
    manifest = {
        "schema": 2,
        "token": token,
        "skill_names": skill_names,
        "config": {
            "install_root": str(install_root),
            "prefix": str(prefix),
            "skills_roots": roots,
        },
        "files": {str(path.resolve()): digest(path) for path in files},
        "directories": [str(path.resolve()) for path in directories],
    }
    atomic_write(
        install_root / MANIFEST_NAME,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        0o600,
    )


def uninstall(install_root, prefix, roots_text, explicit):
    install_root = Path(install_root).resolve()
    prefix = Path(prefix).resolve()
    manifest_path = install_root / MANIFEST_NAME
    try:
        manifest = load_manifest(manifest_path)
    except ValueError as error:
        fail(str(error))
    config = manifest["config"]
    if config.get("install_root") != str(install_root) or config.get("prefix") != str(prefix):
        fail("does not match this uninstall request")
    recorded = unique(str(Path(root).resolve()) for root in manifest_skill_roots(config))
    if explicit == "1":
        requested = unique(str(Path(root).resolve()) for root in split_roots(roots_text))
        if sorted(requested) != sorted(recorded):
            fail("does not match this uninstall request")
    files = launcher_files(prefix)
    directories = owned_directories(install_root, recorded, manifest.get("skill_names", []))
    for path in files:
        problem = file_problem(manifest, path)
        if problem:
            fail(problem)
    for path in directories:
        problem = directory_problem(manifest, path)
        if problem:
            fail(problem)
    for path in files:
        path.unlink()
    for path in directories:
        shutil.rmtree(path)
    manifest_path.unlink()
    for directory in (install_root, install_root.parent):
        try:
            directory.rmdir()
        except OSError:
            pass


def find_previous(data_base, install_root, prefix):
    """Print `remove ROOT` or `keep ROOT REASON` for older installs at PREFIX.

    An older install is removable only when every launcher its manifest owns
    still matches the recorded content hash, which proves the launchers about
    to be replaced are that install's own. Installs at other prefixes are in
    use and are not listed.
    """
    base = Path(data_base)
    if not base.is_dir():
        return
    current = Path(install_root).resolve()
    prefix = Path(prefix).resolve()
    for candidate in sorted(base.iterdir()):
        if not VERSION_DIR.match(candidate.name) or candidate.is_symlink():
            continue
        if not candidate.is_dir() or candidate.resolve() == current:
            continue
        root = candidate.resolve()
        manifest_path = root / MANIFEST_NAME
        if not os.path.lexists(manifest_path):
            print("keep\t{}\thas no ownership manifest".format(root))
            continue
        try:
            manifest = load_manifest(manifest_path)
        except ValueError as error:
            print("keep\t{}\townership manifest {}".format(root, error))
            continue
        config = manifest["config"]
        if config.get("prefix") != str(prefix) or config.get("install_root") != str(root):
            continue
        problems = [file_problem(manifest, path) for path in launcher_files(prefix)]
        problems = [problem for problem in problems if problem]
        if problems:
            print("keep\t{}\t{}".format(root, problems[0]))
        else:
            print("remove\t{}".format(root))


def cleanup_previous(listing):
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        root = Path(parts[1])
        if parts[0] != "remove":
            print("[WARN] Left previous Mythify install in place: {} ({}).".format(
                root, parts[2] if len(parts) > 2 else "not verified"))
            continue
        try:
            manifest = load_manifest(root / MANIFEST_NAME)
        except ValueError as error:
            print("[WARN] Left previous Mythify install in place: {} (manifest {}).".format(root, error))
            continue
        problem = directory_problem(manifest, root / "cli")
        if problem:
            print("[WARN] Left previous Mythify install in place: {} ({}).".format(root, problem))
            continue
        shutil.rmtree(root / "cli")
        (root / MANIFEST_NAME).unlink()
        leftovers = sorted(path.name for path in root.iterdir())
        if leftovers:
            print("[WARN] Removed the previous Mythify runtime from {}, but left files Mythify does not own: {}.".format(
                root, ", ".join(leftovers)))
        else:
            root.rmdir()
            print("[OK] Removed previous Mythify install: {}".format(root))
        skill_roots = manifest_skill_roots(manifest["config"])
        for directory in owned_directories(root, skill_roots, manifest.get("skill_names", []))[1:]:
            marker = directory / MARKER_NAME
            if marker.is_file() and not marker.is_symlink() and (
                marker.read_text(encoding="utf-8").strip() == manifest.get("token")
            ):
                print("[WARN] Left previous-version skill directory: {} (this install did not replace it; remove it if no longer wanted).".format(directory))


def ignore_state(project):
    """Add .mythify/ to PROJECT/.gitignore unless it already lists the state."""
    path = Path(project) / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = {line.strip() for line in existing.splitlines()}
    if ".mythify" in entries or ".mythify/" in entries:
        return
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    separator = "" if existing == "" or existing.endswith("\n") else "\n"
    atomic_write(path, existing + separator + ".mythify/\n", mode)


ACTIONS = {
    "begin": begin_transaction,
    "ignore-state": ignore_state,
    "rollback": rollback_transaction,
    "manifest": write_manifest,
    "uninstall": uninstall,
    "find-previous": find_previous,
    "cleanup-previous": cleanup_previous,
}
ACTIONS[sys.argv[1]](*sys.argv[2:])
PY
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
  for module in "$repo_root"/scripts/mythify_*.py; do
    [ -f "$module" ] || continue
    cp "$module" "$cli_stage/scripts/$(basename "$module")"
  done
  cp -R "$repo_root/protocol" "$cli_stage/protocol"
  chmod 755 "$cli_stage/scripts/mythify.py"
  chmod 755 "$cli_stage/scripts/install_user.sh"

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

begin_install_transaction() {
  transaction_backup_dir=$(mktemp -d "${TMPDIR:-/tmp}/mythify-install-rollback.XXXXXX")
  install_helper begin \
    "$transaction_backup_dir" \
    "$install_root" \
    "$prefix" \
    "$skills_roots" \
    "$mythify_skill_names" \
    "$project_dir"
  transaction_active=1
}

commit_install_transaction() {
  transaction_active=0
  if rm -rf "$transaction_backup_dir"; then
    transaction_backup_dir=""
  else
    printf '%s\n' "[WARN] Install committed, but the transaction backup could not be removed: $transaction_backup_dir" >&2
  fi
}

cli_stage=""
cli_backup=""
cli_dir=""
transaction_active=0
transaction_backup_dir=""
skill_failure_injected=0
cleanup_temporary_dirs() {
  cleanup_status=$?
  trap - EXIT
  set +e
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
  if [ "$transaction_active" -eq 1 ] && [ -n "$transaction_backup_dir" ]; then
    if install_helper rollback "$transaction_backup_dir"; then
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
skip_skills=0
skills_roots=""
uninstall=0
data_root=""
mythify_skill_names="mythify mythify-work mythify-route mythify-verify"

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
      # Accepted for older install commands; the MCP launcher needs only Python.
      shift
      ;;
    --skip-skills)
      skip_skills=1
      shift
      ;;
    --skills-root)
      [ "$#" -ge 2 ] || fail "--skills-root requires a path"
      add_skills_root "$2"
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

explicit_skills_roots=0
if [ "$skip_skills" -eq 1 ]; then
  skills_roots=""
  explicit_skills_roots=1
elif [ -n "$skills_roots" ]; then
  explicit_skills_roots=1
fi

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
  # Without --skills-root or --skip-skills, remove the skill roots the
  # ownership manifest recorded, including the two roots a 5.x manifest kept.
  install_helper uninstall "$install_root" "$prefix" "$skills_roots" "$explicit_skills_roots"
  printf '%s\n' "[OK] Removed Mythify user installation."
  if [ -n "$project" ]; then
    printf '%s\n' "[OK] Preserved project state: $project/.mythify"
  else
    printf '%s\n' "[OK] Project .mythify state was not modified."
  fi
  exit 0
fi

if [ "$skip_skills" -eq 0 ] && [ -z "$skills_roots" ]; then
  detect_default_skills_roots
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
preflight_directory "Install prefix" "$prefix"
preflight_directory "Binary destination" "$bin_dir"
preflight_file "Mythify launcher" "$bin_dir/mythify"
preflight_file "MCP launcher" "$bin_dir/mythify-mcp"
preflight_file "Mythify uninstaller" "$bin_dir/mythify-uninstall"
preflight_directory "Data destination" "$data_home"
preflight_directory "Versioned data destination" "$install_root"
preflight_directory "CLI data destination" "$cli_dir"
preflight_file "Ownership manifest" "$install_root/install-manifest.json"
for_each_skills_root preflight_skills_root

# Decide which older installs this one replaces before their launchers are
# overwritten: only then can their recorded content hashes still be checked.
previous_installs=$(install_helper find-previous "$data_home/mythify" "$install_root" "$prefix")

begin_install_transaction

if [ -n "$project_dir" ]; then
  # MYTHIFY_DIR pins init to this project. Without it, an exported MYTHIFY_DIR
  # or a .mythify in an ancestor directory sends init outside the transaction.
  MYTHIFY_DIR="$project_dir/.mythify" "$python_bin" "$repo_root/scripts/mythify.py" init >/dev/null
  install_helper ignore-state "$project_dir"
  [ -f "$project_dir/.mythify/memory.json" ] ||
    fail "Project init did not create $project_dir/.mythify/memory.json"
fi

mkdir -p "$bin_dir"
install_cli_runtime

write_exec_launcher "$bin_dir/mythify" "$python_bin" "$cli_dir/scripts/mythify.py"
write_exec_launcher "$bin_dir/mythify-mcp" "$python_bin" "$cli_dir/scripts/mythify.py" mcp
set -- \
  sh \
  "$cli_dir/scripts/install_user.sh" \
  --uninstall \
  --data-root "$install_root" \
  --prefix "$prefix"
if [ "$skip_skills" -eq 1 ]; then
  set -- "$@" --skip-skills
else
  launcher_rest="$skills_roots"
  while [ -n "$launcher_rest" ]; do
    case "$launcher_rest" in
      *"$newline"*)
        launcher_root=${launcher_rest%%"$newline"*}
        launcher_rest=${launcher_rest#*"$newline"}
        ;;
      *)
        launcher_root=$launcher_rest
        launcher_rest=""
        ;;
    esac
    set -- "$@" --skills-root "$launcher_root"
  done
fi
if [ -n "$project_dir" ]; then
  set -- "$@" --project "$project_dir"
fi
write_exec_launcher "$bin_dir/mythify-uninstall" "$@"

printf '%s\n' "[OK] Installed mythify CLI: $bin_dir/mythify"
printf '%s\n' "[OK] Installed mythify MCP server: $bin_dir/mythify-mcp"
printf '%s\n' "[OK] Installed CLI runtime: $cli_dir"
printf '%s\n' "[OK] Installed uninstaller: $bin_dir/mythify-uninstall"

if [ "$skip_skills" -eq 0 ]; then
  [ -d "$repo_root/skills" ] || fail "Missing skills directory"
  for_each_skills_root install_skills_into
fi

install_helper manifest "$install_root" "$prefix" "$skills_roots" "$mythify_skill_names"

commit_install_transaction

if [ -n "$previous_installs" ]; then
  install_helper cleanup-previous "$previous_installs" ||
    printf '%s\n' "[WARN] Could not finish removing the previous Mythify install; see the messages above." >&2
fi

if [ -n "$project_dir" ]; then
  printf '%s\n' "[OK] Initialized project state: $project_dir/.mythify"
  mcp_state_dir="$project_dir/.mythify"
else
  mcp_state_dir="/path/to/your/project/.mythify"
fi
cat <<EOF
[OK] MCP setup: register this stdio server in your host's MCP configuration:
  command: $bin_dir/mythify-mcp
  env: MYTHIFY_DIR=$mcp_state_dir
EOF

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *)
    printf '%s\n' "[WARN] Add $bin_dir to PATH if your shell cannot find mythify."
    ;;
esac
