"""Material-only maintainability review records."""

import json
import hashlib
import re
from pathlib import Path


REVIEW_STATUSES = ("pass", "warn", "fail")

_now_iso = None
_slugify = None
_write_json_atomic = None
_read_json = None
_fail = None


def configure_quality_store(*, now_iso_func, slugify_func, write_json_atomic_func, read_json_func, fail_func):
    global _now_iso, _slugify, _write_json_atomic, _read_json, _fail
    _now_iso = now_iso_func
    _slugify = slugify_func
    _write_json_atomic = write_json_atomic_func
    _read_json = read_json_func
    _fail = fail_func


def parse_finding(value):
    match = re.match(r"^(.+?):([1-9][0-9]*):\s*(.+)$", str(value or "").strip())
    if not match or not match.group(1).strip() or not match.group(3).strip():
        raise ValueError("finding must use path:line: detail")
    return {"path": match.group(1).strip(), "line": int(match.group(2)), "detail": match.group(3).strip()}


def _review_path(state, slug):
    return Path(state) / "reviews" / (slug + ".json")


def _finding_class(detail):
    return " ".join(re.findall(r"[a-z0-9]+", str(detail).lower()))


def _recurring_eval_candidates(state, findings):
    prior = {}
    for path in (Path(state) / "reviews").glob("*.json"):
        review = _read_json(path, None)
        if not isinstance(review, dict) or review.get("status") not in ("warn", "fail"):
            continue
        for finding in review.get("findings") or []:
            finding_class = _finding_class(finding.get("detail"))
            if finding_class:
                prior.setdefault(finding_class, []).append(review.get("name", path.stem))
    candidates = []
    for finding in findings:
        finding_class = _finding_class(finding.get("detail"))
        source_reviews = list(dict.fromkeys(prior.get(finding_class, [])))
        if not finding_class or not source_reviews:
            continue
        digest = hashlib.sha256(finding_class.encode("utf-8")).hexdigest()[:10]
        candidates.append({
            "title": "maintainability-regression-" + digest,
            "finding_class": finding_class,
            "rationale": "The same concrete maintainability finding recurred and should become an executable eval when a fail-pass scenario can be written.",
            "source_reviews": source_reviews,
        })
    return candidates


def cmd_quality_review_create(args, state):
    slug = _slugify(args.name or ("maintainability-" + _now_iso())) or "maintainability-review"
    if _review_path(state, slug).exists():
        _fail("[FAIL] Review already exists: {0}".format(slug))
        return 1
    try:
        findings = [parse_finding(item) for item in args.finding]
    except ValueError as exc:
        _fail("[FAIL] {0}".format(exc))
        return 1
    dimensions = {
        "interface_depth": args.interface_depth,
        "locality": args.locality,
        "seam_count": args.seam_count,
        "deletion_cost": args.deletion_cost,
        "invalid_state_exclusion": args.invalid_state_exclusion,
        "test_validity": args.test_validity,
    }
    if any(not str(value).strip() for value in dimensions.values()):
        _fail("[FAIL] Every maintainability review dimension requires a non-empty assessment.")
        return 1
    changed_paths = [str(value).strip() for value in args.path]
    if any(not value for value in changed_paths):
        _fail("[FAIL] Changed paths must be non-empty.")
        return 1
    stamp = _now_iso()
    candidates = _recurring_eval_candidates(state, findings) if args.status in ("warn", "fail") else []
    record = {
        "schema_version": 1,
        "kind": "maintainability_review",
        "name": slug,
        "status": args.status,
        "changed_paths": list(dict.fromkeys(changed_paths)),
        "dimensions": {key: str(value).strip() for key, value in dimensions.items()},
        "findings": findings,
        "created": stamp,
        "updated": stamp,
        "evidence_status": "material_not_verification",
        "eval_scenario_candidates": candidates,
        "eval_proposal_recommended": bool(candidates),
    }
    _write_json_atomic(_review_path(state, slug), record)
    print("[OK] Maintainability review: {0} ({1}, material only)".format(slug, args.status))
    return 0


def cmd_quality_review_show(args, state):
    slug = _slugify(args.name)
    record = _read_json(_review_path(state, slug), None)
    if not isinstance(record, dict):
        _fail("[FAIL] Review not found: {0}".format(args.name))
        return 1
    if args.json_output:
        print(json.dumps(record, indent=2))
    else:
        print("[OK] Maintainability review: {0} ({1})".format(slug, record["status"]))
        print("Changed paths: {0}".format(", ".join(record["changed_paths"])))
        for finding in record["findings"]:
            print("  {0}:{1}: {2}".format(finding["path"], finding["line"], finding["detail"]))
        print("Guardrail: review judgment is material and cannot satisfy verification.")
    return 0


def add_quality_parser(subparsers, symbols):
    review = subparsers.add_parser("review", help="Record advisory maintainability reviews.")
    actions = review.add_subparsers(dest="review_command", metavar="ACTION", required=True)
    create = actions.add_parser("create", help="Create a material-only maintainability review.")
    create.add_argument("--status", choices=REVIEW_STATUSES, required=True)
    create.add_argument("--path", action="append", required=True, help="Changed path. Repeat as needed.")
    create.add_argument("--interface-depth", required=True)
    create.add_argument("--locality", required=True)
    create.add_argument("--seam-count", required=True)
    create.add_argument("--deletion-cost", required=True)
    create.add_argument("--invalid-state-exclusion", required=True)
    create.add_argument("--test-validity", required=True)
    create.add_argument("--finding", action="append", default=[], help="Concrete path:line: detail finding.")
    create.add_argument("--name")
    create.set_defaults(handler=symbols["cmd_quality_review_create"])
    show = actions.add_parser("show", help="Show a maintainability review.")
    show.add_argument("name")
    show.add_argument("--json", dest="json_output", action="store_true")
    show.set_defaults(handler=symbols["cmd_quality_review_show"])
