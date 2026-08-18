"""Typed artifact lineage capture and staleness inspection."""

import hashlib
import json
from pathlib import Path


ARTIFACT_KINDS = ("research", "map", "design", "plan", "outcome", "verification")
PRECEDENCE = (
    "live_code_current_behavior",
    "approved_design_desired_behavior",
    "latest_linked_plan_implementation_order",
    "executed_verification_completion",
)

_now_iso = None
_slugify = None
_read_json = None
_read_jsonl = None
_write_json_atomic = None
_fail = None


def configure_lineage_store(*, now_iso_func, slugify_func, read_json_func, read_jsonl_func, write_json_atomic_func, fail_func):
    global _now_iso, _slugify, _read_json, _read_jsonl, _write_json_atomic, _fail
    _now_iso = now_iso_func
    _slugify = slugify_func
    _read_json = read_json_func
    _read_jsonl = read_jsonl_func
    _write_json_atomic = write_json_atomic_func
    _fail = fail_func


def parse_parent_spec(value):
    text = str(value or "").strip()
    if ":" not in text:
        raise ValueError("parent must use kind:id")
    kind, artifact_id = text.split(":", 1)
    kind = kind.strip().lower()
    artifact_id = _slugify(artifact_id)
    if kind not in ARTIFACT_KINDS or not artifact_id:
        raise ValueError("parent kind must be one of {0} and id must be non-empty".format(", ".join(ARTIFACT_KINDS)))
    return kind, artifact_id


def artifact_path(state, kind, artifact_id):
    state = Path(state)
    if kind == "research":
        return state / "research" / (artifact_id + ".json")
    if kind == "map":
        return state / "maps" / (artifact_id + ".json")
    if kind == "design":
        return state / "designs" / (artifact_id + ".json")
    if kind == "plan":
        return state / "plans" / (artifact_id + ".json")
    if kind == "outcome":
        return state / "outcomes" / artifact_id / "goal.json"
    return state / "verifications.jsonl"


def artifact_record(state, kind, artifact_id):
    if kind == "verification":
        for record in reversed(_read_jsonl(artifact_path(state, kind, artifact_id))):
            if record.get("id") == artifact_id:
                return record
        return None
    record = _read_json(artifact_path(state, kind, artifact_id), None)
    return record if isinstance(record, dict) else None


def revision_digest(record):
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_updated(record):
    for key in ("updated", "last_updated", "timestamp", "created"):
        if record.get(key):
            return str(record[key])
    return ""


def capture_lineage(state, parent_specs):
    parents = []
    for raw in parent_specs or []:
        kind, artifact_id = parse_parent_spec(raw)
        record = artifact_record(state, kind, artifact_id)
        if record is None:
            raise ValueError("parent artifact not found: {0}:{1}".format(kind, artifact_id))
        parents.append({
            "kind": kind,
            "id": artifact_id,
            "revision": revision_digest(record),
            "observed_updated": record_updated(record),
        })
    if not parents:
        return None
    return {"captured_at": _now_iso(), "parents": parents}


def inspect_lineage(state, lineage):
    if not isinstance(lineage, dict) or not isinstance(lineage.get("parents"), list):
        return {"status": "unknown", "parents": [], "precedence": list(PRECEDENCE)}
    rows = []
    for parent in lineage["parents"]:
        kind = parent.get("kind")
        artifact_id = parent.get("id")
        if kind not in ARTIFACT_KINDS or not artifact_id:
            rows.append({**parent, "status": "unknown"})
            continue
        live = artifact_record(state, kind, artifact_id)
        if live is None:
            rows.append({**parent, "status": "missing"})
            continue
        current = revision_digest(live)
        rows.append({
            **parent,
            "status": "current" if current == parent.get("revision") else "stale",
            "current_revision": current,
            "current_updated": record_updated(live),
        })
    statuses = {row["status"] for row in rows}
    overall = "missing" if "missing" in statuses else "stale" if "stale" in statuses else "unknown" if "unknown" in statuses else "current"
    return {"status": overall, "parents": rows, "precedence": list(PRECEDENCE)}


def _save_artifact(state, kind, artifact_id, record):
    if kind == "verification":
        raise ValueError("verification lineage is append-only and must be captured by verify run")
    record["lineage_updated"] = _now_iso()
    _write_json_atomic(artifact_path(state, kind, artifact_id), record)


def cmd_lineage_attach(args, state):
    artifact_id = _slugify(args.id)
    record = artifact_record(state, args.kind, artifact_id)
    if record is None:
        _fail("[FAIL] Artifact not found: {0}:{1}".format(args.kind, artifact_id))
        return 1
    try:
        lineage = capture_lineage(state, args.parent)
    except ValueError as exc:
        _fail("[FAIL] {0}".format(exc))
        return 1
    if any(parent["kind"] == args.kind and parent["id"] == artifact_id for parent in lineage["parents"]):
        _fail("[FAIL] An artifact cannot be its own lineage parent.")
        return 1
    record["lineage"] = lineage
    _save_artifact(state, args.kind, artifact_id, record)
    print("[OK] Attached {0} parent reference(s) to {1}:{2}".format(len(lineage["parents"]), args.kind, artifact_id))
    return 0


def cmd_lineage_status(args, state):
    artifact_id = _slugify(args.id)
    record = artifact_record(state, args.kind, artifact_id)
    if record is None:
        _fail("[FAIL] Artifact not found: {0}:{1}".format(args.kind, artifact_id))
        return 1
    result = {
        "kind": "artifact_lineage",
        "artifact": {"kind": args.kind, "id": artifact_id},
        **inspect_lineage(state, record.get("lineage")),
    }
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print("[OK] Lineage {0}:{1}: {2}".format(args.kind, artifact_id, result["status"]))
        for parent in result["parents"]:
            print("  {0}:{1} {2}".format(parent.get("kind"), parent.get("id"), parent.get("status")))
        print("Guardrail: lineage status is advisory; executable evidence owns completion.")
    return 0


def add_lineage_parser(subparsers, symbols):
    lineage = subparsers.add_parser("lineage", help="Attach and inspect typed artifact lineage.")
    actions = lineage.add_subparsers(dest="lineage_command", metavar="ACTION", required=True)
    attach = actions.add_parser("attach", help="Attach current parent revisions to an artifact.")
    attach.add_argument("kind", choices=ARTIFACT_KINDS[:-1])
    attach.add_argument("id")
    attach.add_argument("--parent", action="append", required=True, help="Parent reference kind:id. Repeat as needed.")
    attach.set_defaults(handler=symbols["cmd_lineage_attach"])
    status = actions.add_parser("status", help="Inspect current, stale, missing, or unknown parents.")
    status.add_argument("kind", choices=ARTIFACT_KINDS)
    status.add_argument("id")
    status.add_argument("--json", dest="json_output", action="store_true")
    status.set_defaults(handler=symbols["cmd_lineage_status"])
