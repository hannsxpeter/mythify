"""Flow metrics: `report --metrics` reads plans, the ledger, and findings reviews.

Everything here is read-only. It never writes state and never touches the
report cursor. Numbers come from records already on disk; nothing is guessed.
"""

import json
import sys

from mythify_findings import all_findings, list_findings_reviews
from mythify_io import read_jsonl
from mythify_plans import list_plan_slugs, load_plan
from mythify_runtime_helpers import parse_iso_timestamp


def _seconds_between(start, end):
    first, last = parse_iso_timestamp(start), parse_iso_timestamp(end)
    if first is None or last is None:
        return None
    return round(max((last - first).total_seconds(), 0.0), 1)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _step_row(plan_slug, step, records):
    ordered = sorted(records, key=lambda r: str(r.get("timestamp", "")))
    failed = [r for r in ordered if not r.get("verified")]
    passed = [r for r in ordered if r.get("verified")]
    reused = [r for r in ordered if r.get("reused_from")]
    started = step.get("started_at")
    return {
        "plan": plan_slug,
        "step_id": step.get("id"),
        "title": step.get("title", ""),
        "status": step.get("status", ""),
        "runs": len(ordered),
        "failures": len(failed),
        "reused": len(reused),
        "seconds_saved": round(sum(_number(r.get("reused_duration_seconds")) for r in reused), 1),
        "executed_seconds": round(
            sum(_number(r.get("duration_seconds")) for r in ordered if not r.get("reused_from")), 1),
        "seconds_to_first_failure": _seconds_between(started, failed[0].get("timestamp")) if failed and started else None,
        "seconds_to_first_pass": _seconds_between(started, passed[0].get("timestamp")) if passed and started else None,
        "test_count_drops": sum(1 for r in ordered if isinstance(r.get("test_count_drop"), dict)),
    }


def _review_rows(state):
    rows = []
    for name, record in list_findings_reviews(state):
        counts = {"open": 0, "fixed": 0, "dismissed": 0}
        for finding in all_findings(record):
            if finding.get("status") in counts:
                counts[finding["status"]] += 1
        rows.append({"review": name, "rounds": len(record.get("rounds") or []), **counts})
    return rows


def build_metrics(state, plan=None):
    """The metrics view, or None when PLAN names no plan."""
    slugs = list_plan_slugs(state)
    if plan is not None:
        if plan not in slugs:
            return None
        slugs = [plan]
    ledger = read_jsonl(state / "verifications.jsonl")
    steps = []
    for slug in slugs:
        loaded = load_plan(state, slug)
        for step in (loaded or {}).get("steps", []):
            mine = [r for r in ledger if r.get("plan") == slug and r.get("step_id") == step.get("id")]
            if mine:
                steps.append(_step_row(slug, step, mine))
    scoped = [r for r in ledger if r.get("plan") in slugs] if plan is not None else ledger
    totals = {
        "runs": len(scoped),
        "failures": sum(1 for r in scoped if not r.get("verified")),
        "reused": sum(1 for r in scoped if r.get("reused_from")),
        "seconds_saved": round(sum(_number(r.get("reused_duration_seconds")) for r in scoped), 1),
        "test_count_drops": sum(1 for r in scoped if isinstance(r.get("test_count_drop"), dict)),
    }
    reviews = _review_rows(state)
    totals["findings_reviews"] = len(reviews)
    totals["review_rounds"] = sum(r["rounds"] for r in reviews)
    for key in ("open", "fixed", "dismissed"):
        totals["findings_" + key] = sum(r[key] for r in reviews)
    return {"plans": slugs, "steps": steps, "reviews": reviews, "totals": totals}


def _cell(value):
    return "n/a" if value is None else "{0:g}s".format(value)


def format_metrics(view):
    lines = ["Flow metrics (read-only; report cursor unchanged)"]
    if not view["plans"]:
        lines.append("No plans yet.")
    elif not view["steps"]:
        lines.append("No verify runs are stamped to a plan step yet.")
    for row in view["steps"]:
        lines.append(
            "{plan}#{step_id} {status}: runs {runs}, failed {failures}, reused {reused} "
            "(saved {saved}), executed {executed}, first fail {fail}, first pass {ok}".format(
                saved=_cell(row["seconds_saved"]), executed=_cell(row["executed_seconds"]),
                fail=_cell(row["seconds_to_first_failure"]), ok=_cell(row["seconds_to_first_pass"]),
                **row))
    for row in view["reviews"]:
        lines.append("review {review}: {rounds} rounds, findings open {open}, fixed {fixed}, "
                     "dismissed {dismissed}".format(**row))
    t = view["totals"]
    lines.append(
        "Totals: runs {runs}, failures {failures}, reused {reused} (saved {saved}), "
        "test-count drops {test_count_drops}, findings reviews {findings_reviews} "
        "({review_rounds} rounds; open {findings_open}, fixed {findings_fixed}, "
        "dismissed {findings_dismissed})".format(saved=_cell(t["seconds_saved"]), **t))
    return "\n".join(lines)


def cmd_report_metrics(args, state):
    """Print the metrics view; exit 1 for an unknown plan."""
    view = build_metrics(state, plan=args.plan)
    if view is None:
        sys.stderr.write("[FAIL] No plan named {0}.\n".format(args.plan))
        return 1
    if args.json_output:
        print(json.dumps(view, indent=2))
    else:
        print(format_metrics(view))
    return 0
