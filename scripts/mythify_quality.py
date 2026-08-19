"""Material-only maintainability reviews and evidence-linked blast-radius safety cases."""

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path


REVIEW_STATUSES = ("pass", "warn", "fail")
RISK_LEVELS = ("low", "medium", "high")
RISK_DISPOSITIONS = ("confirmed", "cleared", "unproven")
PROOF_MODES = ("executed", "runtime")

_now_iso = None
_slugify = None
_write_json_atomic = None
_read_json = None
_read_jsonl = None
_execute_verification = None
_current_provenance = None
_revision_digest = None
_fail = None
_environ = os.environ


def configure_quality_store(
    *, now_iso_func, slugify_func, write_json_atomic_func, read_json_func,
    fail_func, read_jsonl_func=None, execute_verification_func=None,
    current_provenance_func=None, revision_digest_func=None, environ_map=None
):
    global _now_iso, _slugify, _write_json_atomic, _read_json, _read_jsonl
    global _execute_verification, _current_provenance, _revision_digest, _fail, _environ
    _now_iso = now_iso_func
    _slugify = slugify_func
    _write_json_atomic = write_json_atomic_func
    _read_json = read_json_func
    _read_jsonl = read_jsonl_func
    _execute_verification = execute_verification_func
    _current_provenance = current_provenance_func
    _revision_digest = revision_digest_func
    _fail = fail_func
    _environ = environ_map if environ_map is not None else os.environ


def parse_finding(value):
    match = re.match(r"^(.+?):([1-9][0-9]*):\s*(.+)$", str(value or "").strip())
    if not match or not match.group(1).strip() or not match.group(3).strip():
        raise ValueError("finding must use path:line: detail")
    return {"path": match.group(1).strip(), "line": int(match.group(2)), "detail": match.group(3).strip()}


def parse_risk(value, forced_disposition=None):
    try:
        risk = json.loads(str(value or ""))
    except ValueError:
        raise ValueError("risk must be a JSON object")
    if not isinstance(risk, dict):
        raise ValueError("risk must be a JSON object")
    required = ("failure_mode", "path", "line", "likelihood", "impact")
    missing = [
        key for key in required
        if risk.get(key) is None or (isinstance(risk.get(key), str) and not risk.get(key).strip())
    ]
    if missing:
        raise ValueError("risk is missing: {0}".format(", ".join(missing)))
    try:
        line = int(risk["line"])
    except (TypeError, ValueError):
        raise ValueError("risk line must be a positive integer")
    if line < 1:
        raise ValueError("risk line must be a positive integer")
    likelihood = str(risk["likelihood"]).strip().lower()
    impact = str(risk["impact"]).strip().lower()
    disposition = str(forced_disposition or risk.get("disposition") or "unproven").strip().lower()
    if likelihood not in RISK_LEVELS:
        raise ValueError("risk likelihood must be low, medium, or high")
    if impact not in RISK_LEVELS:
        raise ValueError("risk impact must be low, medium, or high")
    if disposition not in RISK_DISPOSITIONS:
        raise ValueError("risk disposition must be confirmed, cleared, or unproven")
    return {
        "failure_mode": str(risk["failure_mode"]).strip(),
        "path": str(risk["path"]).strip(),
        "line": line,
        "likelihood": likelihood,
        "impact": impact,
        "disposition": disposition,
        "check": str(risk.get("check") or "").strip(),
        "evidence_id": str(risk.get("evidence_id") or "").strip() or None,
    }


def _review_path(state, slug):
    return Path(state) / "reviews" / (slug + ".json")


def _finding_class(detail):
    return " ".join(re.findall(r"[a-z0-9]+", str(detail).lower()))


def _recurring_eval_candidates(state, findings):
    prior = {}
    for path in (Path(state) / "reviews").glob("*.json"):
        review = _read_json(path, None)
        if not isinstance(review, dict) or review.get("kind") != "maintainability_review":
            continue
        if review.get("status") not in ("warn", "fail"):
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


def _change_fingerprint(state):
    provenance = _current_provenance(state) if _current_provenance else {}
    return {
        "git_commit": provenance.get("git_commit"),
        "worktree_clean": provenance.get("worktree_clean"),
        "worktree_digest": provenance.get("worktree_digest"),
    }


def _change_freshness(state, record):
    expected = record.get("change_fingerprint") or {}
    current = _change_fingerprint(state)
    if not expected.get("git_commit") or not expected.get("worktree_digest"):
        return {"status": "unknown", "reason": "review_change_fingerprint_unavailable", "current": current}
    if not current.get("git_commit") or not current.get("worktree_digest"):
        return {"status": "unknown", "reason": "current_change_fingerprint_unavailable", "current": current}
    if expected.get("git_commit") != current.get("git_commit"):
        return {"status": "stale", "reason": "git_commit_mismatch", "current": current}
    if expected.get("worktree_digest") != current.get("worktree_digest"):
        return {"status": "stale", "reason": "worktree_digest_mismatch", "current": current}
    return {"status": "current", "reason": "change_fingerprint_matches", "current": current}


def _review_proof_records(state, record):
    if not _read_jsonl or not _revision_digest:
        return []
    revision = _revision_digest(record)
    fingerprint = record.get("change_fingerprint") or {}
    rows = []
    for verification in _read_jsonl(Path(state) / "verifications.jsonl"):
        lineage = verification.get("lineage") or {}
        parents = lineage.get("parents") or []
        matches_parent = any(
            parent.get("kind") == "review"
            and parent.get("id") == record.get("name")
            and parent.get("revision") == revision
            for parent in parents
        )
        provenance = verification.get("provenance") or {}
        matches_change = (
            provenance.get("git_commit") == fingerprint.get("git_commit")
            and provenance.get("worktree_digest") == fingerprint.get("worktree_digest")
            and bool(fingerprint.get("git_commit"))
            and bool(fingerprint.get("worktree_digest"))
        )
        if matches_parent and matches_change and verification.get("kind") == "executed":
            rows.append(verification)
    return rows


def blast_review_view(state, record):
    view = deepcopy(record)
    freshness = _change_freshness(state, record)
    proofs = _review_proof_records(state, record)
    proof = proofs[-1] if proofs else None
    safety = dict(view.get("safety_fact") or {})
    merge_gate = dict(view.get("merge_gate") or {})
    if proof:
        depth = 5 if proof.get("proof_mode") == "runtime" else 4
        safety["proof_depth"] = depth
        safety["verification_id"] = proof.get("id")
        if proof.get("verified") is True and proof.get("exit_code") == 0:
            safety["status"] = "proven" if freshness["status"] == "current" else "stale"
        else:
            safety["status"] = "unproven"
        merge_gate["verification_id"] = proof.get("id")
        merge_gate["verified"] = proof.get("verified") is True and proof.get("exit_code") == 0
    view["safety_fact"] = safety
    view["merge_gate"] = merge_gate
    view["change_freshness"] = freshness
    return view


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
        "review_type": "maintainability",
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


def cmd_blast_radius_review_create(args, state):
    slug = _slugify(args.name or ("blast-radius-" + _now_iso())) or "blast-radius-review"
    if _review_path(state, slug).exists():
        _fail("[FAIL] Review already exists: {0}".format(slug))
        return 1
    changed_paths = [str(value).strip() for value in args.path]
    if any(not value for value in changed_paths):
        _fail("[FAIL] Changed paths must be non-empty.")
        return 1
    if not str(args.safety_fact or "").strip():
        _fail("[FAIL] Safety fact must be non-empty.")
        return 1
    try:
        risks = [parse_risk(item) for item in args.risk]
        risks.extend(parse_risk(item, "cleared") for item in args.cleared)
    except ValueError as exc:
        _fail("[FAIL] {0}".format(exc))
        return 1
    stamp = _now_iso()
    record = {
        "schema_version": 1,
        "kind": "blast_radius_review",
        "review_type": "blast_radius",
        "name": slug,
        "status": args.status,
        "changed_paths": list(dict.fromkeys(changed_paths)),
        "change_fingerprint": _change_fingerprint(state),
        "safety_fact": {
            "claim": args.safety_fact.strip(),
            "proof_depth": args.proof_depth,
            "status": "unproven",
            "verification_id": None,
        },
        "risks": risks,
        "merge_gate": {"command": str(args.merge_command or "").strip(), "verification_id": None},
        "created": stamp,
        "updated": stamp,
        "evidence_status": "material_not_verification",
    }
    _write_json_atomic(_review_path(state, slug), record)
    print("[OK] Blast-radius review: {0} ({1}, safety fact unproven)".format(slug, args.status))
    return 0


def cmd_quality_review_prove(args, state):
    slug = _slugify(args.name)
    record = _read_json(_review_path(state, slug), None)
    if not isinstance(record, dict):
        _fail("[FAIL] Review not found: {0}".format(args.name))
        return 1
    if record.get("kind") != "blast_radius_review":
        _fail("[FAIL] Review is not a blast-radius safety case: {0}".format(slug))
        return 1
    if _environ.get("MYTHIFY_DISABLE_RUN") == "1":
        _fail("[FAIL] review prove is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was executed.")
        return 2
    freshness = _change_freshness(state, record)
    if freshness["status"] != "current":
        _fail("[FAIL] Review change fingerprint is {0}: {1}. Create a new review for the current change.".format(
            freshness["status"], freshness["reason"]
        ))
        return 1
    command = str(args.command or (record.get("merge_gate") or {}).get("command") or "").strip()
    if not command:
        _fail("[FAIL] No proof command supplied and the review has no merge-gate command.")
        return 1
    context = {
        "plan": None,
        "step_id": None,
        "step_title": None,
        "step_status": None,
        "review": slug,
        "proof_mode": args.mode,
    }
    verification = _execute_verification(
        state,
        command,
        args.claim or record["safety_fact"]["claim"],
        args.timeout,
        context=context,
        parents=["review:" + slug],
    )
    label = verification.get("claim") or command
    print("[{0}] {1}: {2} (exit {3}, {4:.2f}s)".format(
        "OK" if verification.get("verified") else "FAIL",
        "VERIFIED" if verification.get("verified") else "UNVERIFIED",
        label,
        verification.get("exit_code"),
        verification.get("duration_seconds", 0.0),
    ))
    if not verification.get("verified"):
        for channel in ("stdout_tail", "stderr_tail"):
            if verification.get(channel):
                print("--- {0} ---".format(channel.replace("_tail", "")))
                print(verification[channel])
    post_run = _change_freshness(state, record)
    if verification.get("verified") and post_run["status"] != "current":
        _fail("[FAIL] Proof command passed but changed the reviewed source: {0}. The safety fact remains unproven.".format(
            post_run["reason"]
        ))
        return 2
    return 0 if verification.get("verified") else 2


def cmd_quality_review_show(args, state):
    slug = _slugify(args.name)
    record = _read_json(_review_path(state, slug), None)
    if not isinstance(record, dict):
        _fail("[FAIL] Review not found: {0}".format(args.name))
        return 1
    if record.get("kind") == "blast_radius_review":
        view = blast_review_view(state, record)
        if args.json_output:
            print(json.dumps(view, indent=2))
            return 0
        safety = view["safety_fact"]
        print("[OK] Blast-radius review: {0} ({1})".format(slug, record["status"]))
        print("Change fingerprint: {0} ({1})".format(
            view["change_freshness"]["status"], view["change_freshness"]["reason"]
        ))
        print("Safety fact: {0} (depth {1}, {2})".format(
            safety["claim"], safety["proof_depth"], safety["status"]
        ))
        for disposition in RISK_DISPOSITIONS:
            rows = [risk for risk in view.get("risks", []) if risk.get("disposition") == disposition]
            print("{0}:".format(disposition.capitalize()))
            if not rows:
                print("  none")
            for risk in rows:
                print("  {0}:{1}: {2} (likelihood {3}, impact {4})".format(
                    risk["path"], risk["line"], risk["failure_mode"], risk["likelihood"], risk["impact"]
                ))
        print("Before merge: {0}".format(view.get("merge_gate", {}).get("command") or "unproven"))
        print("Guardrail: the review remains material; only its linked executed verification is proof.")
        return 0
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
    review = subparsers.add_parser("review", help="Record maintainability reviews and blast-radius safety cases.")
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
    blast = actions.add_parser("blast-radius", help="Create a material-only blast-radius safety case.")
    blast.add_argument("--status", choices=REVIEW_STATUSES, required=True)
    blast.add_argument("--path", action="append", required=True, help="Changed path. Repeat as needed.")
    blast.add_argument("--safety-fact", required=True)
    blast.add_argument("--proof-depth", type=int, choices=(1, 2, 3), default=1)
    blast.add_argument("--risk", action="append", default=[], help="Risk JSON object; repeat as needed.")
    blast.add_argument("--cleared", action="append", default=[], help="Cleared-risk JSON object; repeat as needed.")
    blast.add_argument("--merge-command", help="Cheapest executable proof to run before merge.")
    blast.add_argument("--name")
    blast.set_defaults(handler=symbols["cmd_blast_radius_review_create"])
    prove = actions.add_parser("prove", help="Run executable proof linked to a blast-radius review.")
    prove.add_argument("name")
    prove.add_argument("--command", help="Command to run; defaults to the review merge gate.")
    prove.add_argument("--claim", help="Claim label; defaults to the safety fact.")
    prove.add_argument("--mode", choices=PROOF_MODES, default="executed")
    prove.add_argument("--timeout", type=float, default=300.0)
    prove.set_defaults(handler=symbols["cmd_quality_review_prove"])
    show = actions.add_parser("show", help="Show a maintainability review or blast-radius safety case.")
    show.add_argument("name")
    show.add_argument("--json", dest="json_output", action="store_true")
    show.set_defaults(handler=symbols["cmd_quality_review_show"])
