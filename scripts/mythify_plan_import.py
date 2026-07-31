"""godplans and godaudits plan import for the Mythify CLI.

Reads a `.godplans/PLAN.mdx` or `.godaudits/AUDIT.mdx` artifact and converts its
live checkbox tasks into a Mythify plan whose steps keep each task's exact
verify command under strict step-scoped evidence. Mythify never writes those
artifacts; checkbox flips stay with the executing agent per the artifact's own
embedded rules.
"""

from pathlib import Path

from mythify_godfiles import (
    GODAUDITS_DIR_NAME,
    GODPLANS_DIR_NAME,
    find_godaudits_file,
    find_godplans_file,
    load_god_artifact,
)

WORKSPACE_DIR_NAME = ".mythify"


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_plan_import dependencies are not configured")


now_iso = _missing_dependency
slugify = _missing_dependency
list_plan_slugs = _missing_dependency
load_plan = _missing_dependency
plan_path = _missing_dependency
save_plan = _missing_dependency
set_active_slug = _missing_dependency
describe_next_pending = _missing_dependency
fail = _missing_dependency


def configure_plan_import(
    *,
    now_iso_func,
    slugify_func,
    list_plan_slugs_func,
    load_plan_func,
    plan_path_func,
    save_plan_func,
    set_active_slug_func,
    describe_next_pending_func,
    fail_func,
):
    global now_iso, slugify, list_plan_slugs, load_plan, plan_path
    global save_plan, set_active_slug, describe_next_pending, fail
    now_iso = now_iso_func
    slugify = slugify_func
    list_plan_slugs = list_plan_slugs_func
    load_plan = load_plan_func
    plan_path = plan_path_func
    save_plan = save_plan_func
    set_active_slug = set_active_slug_func
    describe_next_pending = describe_next_pending_func
    fail = fail_func


def project_root_for_workspace(state):
    return state.parent if state.name == WORKSPACE_DIR_NAME else Path.cwd()


def _existing_import_slug(state, source_kind, source_path):
    for slug in list_plan_slugs(state):
        plan = load_plan(state, slug)
        if plan is None:
            continue
        source = plan.get("source")
        if (
            isinstance(source, dict)
            and source.get("kind") == source_kind
            and source.get("path") == source_path
        ):
            return slug
    return None


def _resolve_import_artifact(args, root):
    """Resolve (path, source_kind) for plan import; returns (None, error)."""
    source = args.source
    if args.path:
        path = Path(args.path).expanduser()
        if not path.is_file():
            return None, "[FAIL] Artifact not found: {0}".format(path)
    else:
        plan_file = find_godplans_file(root)
        audit_file = find_godaudits_file(root)
        if source == "godplans":
            path = plan_file
        elif source == "godaudits":
            path = audit_file
        elif plan_file is not None and audit_file is not None:
            return None, (
                "[FAIL] Found both a godplans plan and a godaudits audit. "
                "Pass a PATH or --source to choose one."
            )
        else:
            path = plan_file or audit_file
            source = "godplans" if plan_file is not None else source
            source = "godaudits" if plan_file is None and audit_file is not None else source
        if path is None:
            return None, (
                "[FAIL] No godplans or godaudits artifact found under {0}. Run "
                "the /godplans or /godaudits skill first, or pass a PATH.".format(root)
            )
    if source is None:
        lowered = str(path).lower()
        if "plan" in path.name.lower() or GODPLANS_DIR_NAME in lowered:
            source = "godplans"
        elif "audit" in path.name.lower() or GODAUDITS_DIR_NAME in lowered:
            source = "godaudits"
        else:
            return None, (
                "[FAIL] Cannot infer the artifact kind from {0}. Pass --source "
                "godplans or --source godaudits.".format(path)
            )
    return (path, source), None


def cmd_plan_import(args, state):
    root = project_root_for_workspace(state)
    resolved, error = _resolve_import_artifact(args, root)
    if error:
        fail(error)
        return 1
    path, source = resolved
    digest = load_god_artifact(path, source)
    if digest["status"] in ("unreadable", "unrecognized"):
        fail(
            "[FAIL] Cannot import {0}: {1} ({2}).".format(
                path, digest["status"], digest.get("detail", "")
            )
        )
        return 1
    live_tasks = [task for task in digest["tasks"] if not task["superseded"]]
    if not live_tasks:
        fail("[FAIL] No importable tasks found in {0}.".format(path))
        return 1
    existing = _existing_import_slug(state, source, str(path))
    if existing and not args.name:
        fail(
            "[FAIL] {0} was already imported as plan {1}. Archive that plan "
            "first, or pass --name to import a fresh copy.".format(path.name, existing)
        )
        return 1
    base = slugify(args.name) if args.name else (
        (slugify(digest.get("name") or "") or "imported") + "-" + source
    )
    slug = base or "imported-" + source
    suffix = 2
    while plan_path(state, slug).exists():
        slug = "{0}-{1}".format(base, suffix)
        suffix += 1
    stamp = now_iso()
    steps = []
    for index, task in enumerate(live_tasks):
        step = {
            "id": index + 1,
            "title": "{0} {1}".format(task["id"], task["title"]).strip(),
            "success_criteria": task.get("acceptance") or "verify command passes",
            "status": "completed" if task["checked"] else "pending",
            "result": (
                "imported: checkbox already checked in {0}".format(path.name)
                if task["checked"]
                else None
            ),
            "source_id": task["id"],
            "verify_command": task.get("verify_command", ""),
            "wave": task.get("wave", ""),
            "phase": task.get("phase_title", ""),
            "updated_at": stamp,
        }
        if task.get("depends_on"):
            step["depends_on"] = task["depends_on"]
        if task.get("fixes"):
            step["fixes"] = task["fixes"]
        steps.append(step)
    plan = {
        "name": slug,
        "goal": "Execute {0} tasks from {1}".format(source, path.name),
        "steps": steps,
        "created": stamp,
        "last_updated": stamp,
        "strict_context": True,
        "source": {
            "kind": source,
            "path": str(path),
            "version": digest.get("plan_version") or digest.get("audit_version"),
            "imported_at": stamp,
        },
    }
    save_plan(state, slug, plan)
    set_active_slug(state, slug)
    done = sum(1 for step in steps if step["status"] == "completed")
    print(
        "[OK] Imported {0} tasks from {1} into plan {2} ({3} already completed). "
        "Active plan set to {2}.".format(len(steps), path.name, slug, done)
    )
    if digest.get("counter_drift"):
        print(
            "[WARN] Frontmatter counters disagree with the checkboxes in {0}; "
            "the checkboxes were trusted.".format(path.name)
        )
    print(
        "Checkbox flips in the artifact stay with the executing agent per its "
        "embedded rules; Mythify holds the evidence trail."
    )
    print(describe_next_pending(plan))
    return 0
