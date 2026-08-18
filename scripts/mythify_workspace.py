"""Shared and local multi-repository workspace configuration."""

import hashlib
import json
from pathlib import Path


ISOLATION_STRENGTH = {"none": 0, "worktree": 1}
SHARED_NAME = "workspace.json"
LOCAL_NAME = "workspace.local.json"


def _read_object(path):
    if not path.exists():
        return {}, False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid workspace configuration {0}: {1}".format(path, exc))
    if not isinstance(value, dict):
        raise ValueError("workspace configuration must be a JSON object: {0}".format(path))
    return value, True


def _digest(path, exists):
    if not exists:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _merge(shared, local):
    for label, value in (("shared", shared), ("local", local)):
        if not isinstance(value.get("authorization", {}), dict):
            raise ValueError("{0} authorization must be an object".format(label))
        if not isinstance(value.get("repositories", []), list):
            raise ValueError("{0} repositories must be an array".format(label))
        frozen = value.get("frozen_paths", [])
        if not isinstance(frozen, list) or any(not isinstance(item, str) for item in frozen):
            raise ValueError("{0} frozen_paths must be an array of strings".format(label))
    merged = dict(shared)
    shared_isolation = shared.get("task_isolation", "none")
    local_isolation = local.get("task_isolation", shared_isolation)
    if shared_isolation not in ISOLATION_STRENGTH or local_isolation not in ISOLATION_STRENGTH:
        raise ValueError("task_isolation must be none or worktree")
    if ISOLATION_STRENGTH[local_isolation] < ISOLATION_STRENGTH[shared_isolation]:
        raise ValueError("local workspace configuration may not weaken task_isolation")
    merged["task_isolation"] = local_isolation

    shared_frozen = list(shared.get("frozen_paths", []))
    local_frozen = list(local.get("frozen_paths", []))
    merged["frozen_paths"] = list(dict.fromkeys(shared_frozen + local_frozen))

    shared_auth = dict(shared.get("authorization", {}))
    local_auth = dict(local.get("authorization", {}))
    for key, shared_value in shared_auth.items():
        if shared_value is True and local_auth.get(key) is False:
            raise ValueError("local workspace configuration may not weaken authorization.{0}".format(key))
    merged["authorization"] = {**shared_auth, **local_auth}

    repositories = {}
    order = []
    for repo in shared.get("repositories", []):
        if not isinstance(repo, dict):
            raise ValueError("shared repositories must contain objects")
        repo_id = str(repo.get("id", "")).strip()
        if not repo_id or repo_id in repositories:
            raise ValueError("shared repository ids must be non-empty and unique")
        repositories[repo_id] = dict(repo)
        order.append(repo_id)
    for override in local.get("repositories", []):
        if not isinstance(override, dict):
            raise ValueError("local repositories must contain objects")
        if set(override) - {"id", "path"}:
            raise ValueError("local repository overrides may contain only id and path")
        repo_id = str(override.get("id", "")).strip()
        if repo_id not in repositories:
            raise ValueError("local repository override references unknown id: {0}".format(repo_id))
        repositories[repo_id]["path"] = override.get("path")
    merged["repositories"] = [repositories[repo_id] for repo_id in order]
    return merged


def load_workspace_config(state):
    state = Path(state)
    project_root = state.parent.resolve()
    shared_path = state / SHARED_NAME
    local_path = state / LOCAL_NAME
    shared, shared_exists = _read_object(shared_path)
    local, local_exists = _read_object(local_path)
    if not shared_exists:
        raise ValueError("shared workspace configuration not found: {0}".format(shared_path))
    merged = _merge(shared, local)
    repositories = merged.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ValueError("workspace configuration requires at least one repository")
    primary_count = sum(1 for repo in repositories if repo.get("primary") is True)
    if primary_count > 1:
        raise ValueError("workspace configuration permits at most one primary repository")

    resolved_repositories = []
    for repo in repositories:
        repo_id = str(repo.get("id", "")).strip()
        raw_path = repo.get("path")
        if not repo_id or not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("each repository requires non-empty id and path")
        resolved = (project_root / raw_path).resolve()
        if not _inside(resolved, project_root):
            raise ValueError("repository path escapes workspace root: {0}".format(repo_id))
        if not resolved.is_dir():
            raise ValueError("repository path does not exist: {0}".format(resolved))
        if not (resolved / ".git").exists():
            raise ValueError("repository path is not a Git checkout: {0}".format(resolved))
        allowed = []
        for raw_allowed in repo.get("allowed_paths", []):
            allowed_path = (resolved / str(raw_allowed)).resolve()
            if not _inside(allowed_path, resolved):
                raise ValueError("allowed path escapes repository {0}: {1}".format(repo_id, raw_allowed))
            allowed.append(str(allowed_path))
        resolved_repositories.append({**repo, "resolved_path": str(resolved), "resolved_allowed_paths": allowed})

    resolved_frozen = []
    for raw_frozen in merged.get("frozen_paths", []):
        frozen = (project_root / str(raw_frozen)).resolve()
        if not _inside(frozen, project_root):
            raise ValueError("frozen path escapes workspace root: {0}".format(raw_frozen))
        resolved_frozen.append(str(frozen))
    merged["repositories"] = resolved_repositories
    merged["resolved_frozen_paths"] = resolved_frozen
    return {
        "kind": "workspace_configuration",
        "status": "valid",
        "workspace_root": str(project_root),
        "configuration": merged,
        "sources": {
            "shared": {"path": str(shared_path), "exists": shared_exists, "sha256": _digest(shared_path, shared_exists)},
            "local": {"path": str(local_path), "exists": local_exists, "sha256": _digest(local_path, local_exists)},
        },
        "mutation": "none",
        "quality_claim": "none",
    }


def cmd_workspace_show(args, state):
    try:
        result = load_workspace_config(state)
    except ValueError as exc:
        print("[FAIL] {0}".format(exc))
        return 1
    if args.json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    config = result["configuration"]
    print("[OK] Workspace configuration valid: {0}".format(result["workspace_root"]))
    print("Repositories: {0}; task isolation: {1}".format(len(config["repositories"]), config["task_isolation"]))
    print("Sources: shared={0}, local={1}".format(result["sources"]["shared"]["exists"], result["sources"]["local"]["exists"]))
    print("Guardrail: inspection is read-only and does not create worktrees or mutate repositories.")
    return 0


def add_workspace_parser(sub, _symbols):
    workspace = sub.add_parser("workspace", help="Inspect merged shared and local workspace configuration.")
    actions = workspace.add_subparsers(dest="workspace_command", metavar="ACTION", required=True)
    parser = actions.add_parser("show", help="Validate and show the merged workspace configuration.")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    parser.set_defaults(handler=cmd_workspace_show)
