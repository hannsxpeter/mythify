"""Durable design records and plan-shape validation."""

import json
from pathlib import Path


PLAN_ARCHETYPES = ("direct", "rpi", "design-heavy")
PLAN_PHASES = ("understand", "product", "system", "program", "build", "judge", "verify")

_now_iso = None
_slugify = None
_write_json_atomic = None
_write_text_atomic = None
_read_json = None
_fail = None
_capture_lineage = None


def configure_design_store(*, now_iso_func, slugify_func, write_json_atomic_func, write_text_atomic_func, read_json_func, fail_func, capture_lineage_func=None):
    global _now_iso, _slugify, _write_json_atomic, _write_text_atomic, _read_json, _fail, _capture_lineage
    _now_iso = now_iso_func
    _slugify = slugify_func
    _write_json_atomic = write_json_atomic_func
    _write_text_atomic = write_text_atomic_func
    _read_json = read_json_func
    _fail = fail_func
    _capture_lineage = capture_lineage_func


def _string_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("{0} must be an array of strings".format(field))
    return [item.strip() for item in value if item.strip()]


def normalize_vertical_slice(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError("vertical_slice must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("vertical_slice must be an object")
    result = str(value.get("result", "")).strip()
    if not result:
        raise ValueError("vertical_slice.result is required")
    normalized = {
        "result": result,
        "files": _string_list(value.get("files"), "vertical_slice.files"),
        "automated_checks": _string_list(
            value.get("automated_checks"), "vertical_slice.automated_checks"
        ),
        "manual_checks": _string_list(value.get("manual_checks"), "vertical_slice.manual_checks"),
    }
    if not normalized["automated_checks"] and not normalized["manual_checks"]:
        raise ValueError("vertical_slice needs at least one automated or manual check")
    return normalized


def plan_step_extensions(item, archetype="direct"):
    phase = str(item.get("phase") or "").strip()
    if phase and phase not in PLAN_PHASES:
        raise ValueError("phase must be one of: " + ", ".join(PLAN_PHASES))
    vertical = normalize_vertical_slice(item.get("vertical_slice"))
    if archetype == "design-heavy" and phase == "build" and vertical is None:
        raise ValueError("design-heavy build steps require vertical_slice")
    fields = {}
    if phase:
        fields["phase"] = phase
    if vertical is not None:
        fields["vertical_slice"] = vertical
    return fields


def _designs_dir(state):
    return Path(state) / "designs"


def _design_path(state, slug):
    return _designs_dir(state) / (slug + ".json")


def _resolve_design(state, name):
    if not name:
        active = _designs_dir(state) / "active"
        try:
            name = active.read_text(encoding="utf-8").strip()
        except OSError:
            return None, None
    slug = _slugify(name)
    path = _design_path(state, slug)
    record = _read_json(path, None)
    return (slug, record) if isinstance(record, dict) else (None, None)


def cmd_design_create(args, state):
    slug = _slugify(args.name or args.title) or "design"
    if _design_path(state, slug).exists():
        _fail("[FAIL] Design already exists: {0}".format(slug))
        return 1
    stamp = _now_iso()
    record = {
        "schema_version": 1,
        "name": slug,
        "title": args.title,
        "problem": args.problem,
        "current_state": args.current_state or "",
        "desired_state": args.desired_state or "",
        "non_goals": args.non_goals or "",
        "product_decisions": args.product or "",
        "system_decisions": args.system or "",
        "program_decisions": args.program or "",
        "alternatives": [],
        "selected_alternative": None,
        "status": "draft",
        "created": stamp,
        "updated": stamp,
    }
    if args.parent:
        try:
            record["lineage"] = _capture_lineage(state, args.parent)
        except ValueError as exc:
            _fail("[FAIL] Invalid lineage: {0}.".format(exc))
            return 1
    _write_json_atomic(_design_path(state, slug), record)
    _write_text_atomic(_designs_dir(state) / "active", slug + "\n")
    print("[OK] Created design: {0} (draft)".format(slug))
    return 0


def cmd_design_add_alternative(args, state):
    slug, record = _resolve_design(state, args.name)
    if record is None:
        _fail("[FAIL] Design not found: {0}".format(args.name or "active"))
        return 1
    interface_key = " ".join(args.interface.lower().split())
    if any(
        " ".join(str(item.get("interface", "")).lower().split()) == interface_key
        for item in record.get("alternatives", [])
    ):
        _fail("[FAIL] Design alternatives must have materially different interface shapes.")
        return 1
    alternative = {
        "id": "A{0}".format(len(record.get("alternatives", [])) + 1),
        "title": args.title,
        "interface": args.interface,
        "call_sites": args.call_sites or "",
        "locality": args.locality or "",
        "migration_cost": args.migration_cost or "",
        "deletion_cost": args.deletion_cost or "",
        "reversal_evidence": args.reversal_evidence or "",
    }
    record.setdefault("alternatives", []).append(alternative)
    if args.select:
        record["selected_alternative"] = alternative["id"]
    record["updated"] = _now_iso()
    _write_json_atomic(_design_path(state, slug), record)
    print("[OK] Added design alternative {0} to {1}".format(alternative["id"], slug))
    return 0


def cmd_design_approve(args, state):
    slug, record = _resolve_design(state, args.name)
    if record is None:
        _fail("[FAIL] Design not found: {0}".format(args.name or "active"))
        return 1
    alternatives = record.get("alternatives", [])
    if alternatives and len(alternatives) < 2:
        _fail("[FAIL] A design comparison requires at least two alternatives.")
        return 1
    if alternatives and not record.get("selected_alternative"):
        _fail("[FAIL] Select one design alternative before approval.")
        return 1
    record["status"] = "approved"
    record["approval_note"] = args.note
    record["updated"] = _now_iso()
    _write_json_atomic(_design_path(state, slug), record)
    print("[OK] Approved design: {0}".format(slug))
    return 0


def cmd_design_show(args, state):
    slug, record = _resolve_design(state, args.name)
    if record is None:
        _fail("[FAIL] Design not found: {0}".format(args.name or "active"))
        return 1
    if args.json_output:
        print(json.dumps(record, indent=2))
    else:
        print("[OK] Design: {0} ({1})".format(slug, record.get("status", "draft")))
        print("Problem: {0}".format(record.get("problem", "")))
        print("Current: {0}".format(record.get("current_state", "")))
        print("Desired: {0}".format(record.get("desired_state", "")))
        print("Alternatives: {0}; selected: {1}".format(
            len(record.get("alternatives", [])), record.get("selected_alternative") or "none"
        ))
        print("Guardrail: design records are material, not verification evidence.")
    return 0


def add_design_parser(subparsers, symbols):
    design = subparsers.add_parser(
        "design",
        help="Create and review durable product, system, and program designs.",
        description="Design records are durable decision material, not verification evidence.",
    )
    actions = design.add_subparsers(dest="design_command", metavar="ACTION", required=True)
    create = actions.add_parser("create", help="Create a draft design record.")
    create.add_argument("title")
    create.add_argument("--problem", required=True)
    create.add_argument("--current-state")
    create.add_argument("--desired-state")
    create.add_argument("--non-goals")
    create.add_argument("--product")
    create.add_argument("--system")
    create.add_argument("--program")
    create.add_argument("--name")
    create.add_argument("--parent", action="append", default=[], help="Parent artifact kind:id. Repeat as needed.")
    create.set_defaults(handler=symbols["cmd_design_create"])

    alternative = actions.add_parser("alternative", help="Add a bounded interface alternative.")
    alternative.add_argument("title")
    alternative.add_argument("--interface", required=True)
    alternative.add_argument("--call-sites", required=True)
    alternative.add_argument("--locality", required=True)
    alternative.add_argument("--migration-cost", required=True)
    alternative.add_argument("--deletion-cost", required=True)
    alternative.add_argument("--reversal-evidence", required=True)
    alternative.add_argument("--select", action="store_true")
    alternative.add_argument("--name")
    alternative.set_defaults(handler=symbols["cmd_design_add_alternative"])

    approve = actions.add_parser("approve", help="Approve the active or named design.")
    approve.add_argument("name", nargs="?")
    approve.add_argument("--note", required=True)
    approve.set_defaults(handler=symbols["cmd_design_approve"])

    show = actions.add_parser("show", help="Show the active or named design.")
    show.add_argument("name", nargs="?")
    show.add_argument("--json", dest="json_output", action="store_true")
    show.set_defaults(handler=symbols["cmd_design_show"])
