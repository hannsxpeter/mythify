"""Plan storage with validated identifiers and contained filesystem paths."""

import json
import sys

from mythify_io import _write_text_atomic, read_json, write_json_atomic
from mythify_runtime_helpers import slugify


class UnsafePlanPath(ValueError):
    """A plan path would leave its state directory or use an invalid slug."""


def plans_dir(state):
    path = state / "plans"
    try:
        if path.resolve() != state.resolve() / "plans":
            raise UnsafePlanPath("Plan directory must stay inside the resolved state directory.")
    except (OSError, RuntimeError) as exc:
        raise UnsafePlanPath("Cannot resolve the plan directory safely.") from exc
    return path


def checked_plan_path(state, path):
    try:
        if plans_dir(state).resolve() not in path.resolve().parents:
            raise UnsafePlanPath("Plan path must stay inside the plan directory.")
    except (OSError, RuntimeError) as exc:
        raise UnsafePlanPath("Cannot resolve the plan path safely.") from exc
    return path


def plan_path(state, slug):
    # Slugs may exceed 40 characters after a collision suffix is appended.
    if not isinstance(slug, str) or not slug or any(
        ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in slug
    ):
        raise UnsafePlanPath("Invalid plan identifier; use a lowercase alphanumeric slug with hyphens.")
    return checked_plan_path(state, plans_dir(state) / (slug + ".json"))


def plan_slug_taken(state, slug):
    """Reserve live and archived slugs so new plans cannot inherit evidence."""
    if plan_path(state, slug).exists():
        return True
    archive = checked_plan_path(state, plans_dir(state) / "archive")
    if (archive / (slug + ".json")).exists():
        return True
    for path in archive.glob(slug + "-*.json") if archive.is_dir() else ():
        stamp = path.stem[len(slug) + 1:]
        if len(stamp) == 14 and stamp.isdigit():
            return True
    return False


def active_pointer_path(state):
    return checked_plan_path(state, plans_dir(state) / "active")


def list_plan_slugs(state):
    directory = plans_dir(state)
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def count_archived(state):
    directory = checked_plan_path(state, plans_dir(state) / "archive")
    if not directory.is_dir():
        return 0
    return len(list(directory.glob("*.json")))


def get_active_slug(state):
    pointer = active_pointer_path(state)
    if not pointer.is_file():
        return None
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if name and plan_path(state, name).exists():
        return name
    return None


def set_active_slug(state, slug):
    plan_path(state, slug)
    _write_text_atomic(active_pointer_path(state), slug + "\n")


def clear_active_slug(state):
    pointer = active_pointer_path(state)
    if pointer.exists():
        try:
            pointer.unlink()
        except OSError:
            pass


def load_plan(state, slug, readonly=True):
    path = plan_path(state, slug)
    if not path.exists():
        return None
    if readonly:
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            sys.stderr.write("[WARN] Could not read plan JSON; file left unchanged: {0}\n".format(path))
            return None
    else:
        plan = read_json(path, None)
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return None
    return plan


def save_plan(state, slug, plan):
    write_json_atomic(plan_path(state, slug), plan)


def find_existing_slug_by_name(state, name, path_func):
    candidate = slugify(name)
    if candidate and path_func(state, candidate).exists():
        return candidate
    return None


def find_plan_slug(state, name):
    return find_existing_slug_by_name(state, name, plan_path)


def target_plan_slug(state, name):
    if name:
        return find_plan_slug(state, name)
    return get_active_slug(state)
