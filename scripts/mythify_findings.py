"""Findings reviews with bounded rounds.

A findings review is the record of an independent reviewer's rounds over a
change: each round is stamped with the commit and worktree digest it saw,
carries the findings the reviewer raised, and either ends clean or leaves
findings to fix or dismiss. Three rounds that do not converge stop the loop
until the author says why the approach still stands. The record is material,
never verification evidence. It lives next to blast-radius reviews in
.mythify/reviews/<name>.json with kind "findings_review".
"""

import json
import re
import sys
from pathlib import Path

from mythify_io import read_json, write_json_atomic
from mythify_provenance import (
    changed_paths,
    commits_since,
    current_verification_provenance,
    project_root_for_state,
)
from mythify_runtime_helpers import now_iso, slugify

KIND = "findings_review"
MAX_ROUNDS = 3
SEVERITIES = ("high", "medium", "low")
RESOLUTIONS = ("fixed", "dismissed")
GUARDRAIL = "Guardrail: a findings review is material; only executed verification is proof."


def _fail(message):
    sys.stderr.write(message + "\n")


def review_path(state, slug):
    return Path(state) / "reviews" / (slug + ".json")


def is_findings_review(record):
    return isinstance(record, dict) and record.get("kind") == KIND


def source_stamp(state):
    """The commit and worktree digest a round or coverage check sees now."""
    provenance = current_verification_provenance("", state=state)
    return {
        "git_commit": provenance.get("git_commit"),
        "worktree_digest": provenance.get("worktree_digest"),
        "worktree_clean": provenance.get("worktree_clean"),
    }


def all_findings(record):
    return [finding for round_ in record.get("rounds", []) for finding in round_.get("findings", [])]


def open_findings(record):
    return [finding for finding in all_findings(record) if finding.get("status") == "open"]


def dismissed_findings(record):
    return [finding for finding in all_findings(record) if finding.get("status") == "dismissed"]


def latest_round(record):
    rounds = record.get("rounds") or []
    return rounds[-1] if rounds else None


def finding_key(summary, path):
    """Summary and path with case, punctuation, and spacing folded away."""
    words = " ".join(re.sub(r"[^\w\s]", " ", str(summary or "").lower()).split())
    location = str(path or "").strip().replace("\\", "/").lower()
    while location.startswith("./"):
        location = location[2:]
    return words, location


def round_covers(record, current):
    """True when the latest round is clean and saw exactly the CURRENT source."""
    last = latest_round(record)
    if not last or last.get("clean") is not True:
        return False
    seen = last.get("provenance") or {}
    return bool(
        seen.get("git_commit") and seen.get("worktree_digest")
        and current.get("git_commit") and current.get("worktree_digest")
        and seen["git_commit"] == current["git_commit"]
        and seen["worktree_digest"] == current["worktree_digest"]
    )


def coverage(state, record, current=None):
    """What the latest round covers: a dict with "covers" (bool) and "text"."""
    last = latest_round(record)
    if last is None:
        return {"covers": False, "text": "no round recorded yet", "commits_since": None}
    current = current or source_stamp(state)
    if last.get("clean") is not True:
        return {
            "covers": False,
            "text": "round {0} is still open; it does not cover the current source".format(last["number"]),
            "commits_since": None,
        }
    if round_covers(record, current):
        return {"covers": True, "text": "covers current source", "commits_since": 0}
    seen = last.get("provenance") or {}
    count = None
    if seen.get("git_commit"):
        count = commits_since(project_root_for_state(state), seen["git_commit"])
    if count is None:
        text = "cannot compare with round {0}: git history is unavailable".format(last["number"])
    else:
        parts = []
        if count:
            parts.append("{0} commit{1}".format(count, "" if count == 1 else "s"))
        if count == 0 or current.get("worktree_clean") is not True:
            parts.append("uncommitted changes")
        text = "{0} since round {1}".format(" and ".join(parts), last["number"])
    return {"covers": False, "text": text, "commits_since": count}


def read_review_readonly(path):
    """The parsed review at PATH, or None when it is missing or malformed.

    Unlike read_json it never moves a damaged file aside, so read-only
    callers (the verify gate) leave the state directory untouched.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_findings_reviews(state, readonly=False):
    """(name, record) of every findings review, most recently updated first.

    READONLY reads without quarantining a malformed file.
    """
    folder = Path(state) / "reviews"
    rows = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        record = read_review_readonly(path) if readonly else read_json(path, None)
        if is_findings_review(record):
            rows.append((path.stem, record))
    return sorted(rows, key=lambda row: row[1].get("updated", ""), reverse=True)


def _load(state, name):
    slug = slugify(name)
    if not slug:
        _fail("[FAIL] Review name must be non-empty.")
        return slug, None
    record = read_json(review_path(state, slug), None)
    if record is None:
        _fail("[FAIL] Review not found: {0}. Open one with: review round {0}".format(slug))
        return slug, None
    if not is_findings_review(record):
        _fail("[FAIL] Review is not a findings review: {0}".format(slug))
        return slug, None
    return slug, record


def _scope(state, record):
    """Files changed since the latest round's commit, for the next round."""
    last = latest_round(record)
    commit = ((last or {}).get("provenance") or {}).get("git_commit")
    if last is None:
        return {"since_round": None, "since_commit": None, "files": None}
    return {
        "since_round": last["number"],
        "since_commit": commit,
        "files": changed_paths(state, commit) if commit else None,
    }


def _dismissed_rows(record):
    return [
        {"id": f["id"], "summary": f["summary"], "path": f.get("path"), "reason": f.get("reason")}
        for f in dismissed_findings(record)
    ]


def _format_scope(scope):
    if scope["since_round"] is None:
        return ["Scope: first round, review the whole change."]
    head = "Re-review scope (changed since round {0}{1}):".format(
        scope["since_round"], ", commit " + scope["since_commit"][:12] if scope["since_commit"] else ""
    )
    if scope["files"] is None:
        return [head, "  unknown: git cannot list the changes; re-review the whole change."]
    return [head] + (["  " + path for path in scope["files"]] or ["  no files changed"])


def _format_dismissed(rows):
    if not rows:
        return []
    lines = ["Dismissed findings (do not raise them again):"]
    for row in rows:
        where = " ({0})".format(row["path"]) if row.get("path") else ""
        lines.append("  {0}: {1}{2}: {3}".format(row["id"], row["summary"], where, row["reason"]))
    return lines


def cmd_review_round(args, state):
    slug = slugify(args.name)
    if not slug:
        _fail("[FAIL] Review name must be non-empty.")
        return 1
    existing = read_json(review_path(state, slug), None)
    if existing is not None and not is_findings_review(existing):
        _fail("[FAIL] A review named {0} exists and is not a findings review.".format(slug))
        return 1
    stamp = now_iso()
    record = existing or {
        "schema_version": 1, "kind": KIND, "review_type": "findings", "name": slug,
        "rounds": [], "created": stamp, "updated": stamp,
        "evidence_status": "material_not_verification",
    }
    pending = open_findings(record)
    number = len(record["rounds"]) + 1
    if pending:
        _fail(
            "[FAIL] Round {0} cannot open: {1} finding{2} from earlier rounds still open ({3}). "
            "Resolve each with: review resolve {4} FINDING_ID --status fixed|dismissed "
            "[--reason TEXT].".format(
                number, len(pending), "" if len(pending) == 1 else "s",
                ", ".join(f["id"] for f in pending), slug)
        )
        return 1
    rethink = None
    if args.rethink is not None:
        rethink = args.rethink.strip()
        if not rethink:
            _fail("[FAIL] --rethink needs text: say why the approach still stands.")
            return 1
    if number > MAX_ROUNDS and not rethink:
        _fail(
            "[FAIL] Round {0} needs --rethink: three review rounds did not converge. "
            "Reconsider the approach, then say why it stands.".format(number)
        )
        return 1
    scope = _scope(state, record)
    dismissed = _dismissed_rows(record)
    seen = source_stamp(state)
    round_ = {
        "number": number,
        "opened_at": stamp,
        "provenance": {"git_commit": seen["git_commit"], "worktree_digest": seen["worktree_digest"]},
        "clean": bool(args.clean),
        "findings": [],
    }
    if args.note and args.note.strip():
        round_["note"] = args.note.strip()
    if rethink:
        round_["rethink"] = rethink
    if args.clean:
        round_["closed_at"] = stamp
    record["rounds"].append(round_)
    record["updated"] = stamp
    write_json_atomic(review_path(state, slug), record)
    if args.json_output:
        print(json.dumps({
            "review": slug, "round": number, "clean": round_["clean"],
            "scope": scope, "dismissed": dismissed,
        }, indent=2))
        return 0
    print("[OK] Findings review {0}: round {1} {2}".format(
        slug, number, "recorded clean at the current source" if args.clean else "opened"))
    for line in _format_scope(scope) + _format_dismissed(dismissed):
        print(line)
    if not args.clean:
        print('Record each finding with: review finding {0} "SUMMARY" [--path PATH] [--severity high|medium|low]'.format(slug))
    return 0


def cmd_review_finding(args, state):
    slug, record = _load(state, args.name)
    if record is None:
        return 1
    summary = " ".join(str(args.summary or "").split())
    if not summary:
        _fail("[FAIL] Finding summary must be non-empty.")
        return 1
    last = latest_round(record)
    if last is None:
        _fail("[FAIL] No round is open. Open one with: review round {0}".format(slug))
        return 1
    if last.get("clean") is True:
        _fail(
            "[FAIL] Round {0} is clean: a clean round has no findings. Open a new round "
            "with: review round {1}".format(last["number"], slug)
        )
        return 1
    path = str(args.path or "").strip()
    key = finding_key(summary, path)
    repeat_of = None
    for earlier in all_findings(record):
        if finding_key(earlier["summary"], earlier.get("path")) != key:
            continue
        if earlier["status"] == "dismissed":
            _fail(
                "[FAIL] Repeat of dismissed finding {0}: {1}. It was dismissed because: {2}. "
                "Do not raise it again.".format(earlier["id"], earlier["summary"], earlier.get("reason"))
            )
            return 1
        if earlier["status"] == "open":
            _fail("[FAIL] Finding {0} already records this and is still open.".format(earlier["id"]))
            return 1
        repeat_of = earlier["id"]
    finding = {
        "id": "F{0}".format(len(all_findings(record)) + 1),
        "summary": summary,
        "path": path or None,
        "severity": args.severity,
        "status": "open",
        "reason": None,
        "created_at": now_iso(),
        "resolved_at": None,
    }
    if repeat_of:
        finding["repeat_of"] = repeat_of
    last["findings"].append(finding)
    record["updated"] = finding["created_at"]
    write_json_atomic(review_path(state, slug), record)
    if repeat_of:
        _fail(
            "[WARN] Finding {0} repeats {1}, which was marked fixed. The fix may not have "
            "held; check it.".format(finding["id"], repeat_of)
        )
    if args.json_output:
        print(json.dumps(finding, indent=2))
    else:
        print("[OK] Finding {0} recorded in round {1} ({2}, open)".format(
            finding["id"], last["number"], args.severity))
    return 0


def cmd_review_resolve(args, state):
    slug, record = _load(state, args.name)
    if record is None:
        return 1
    wanted = str(args.finding_id).strip().upper()
    finding = next((f for f in all_findings(record) if f["id"] == wanted), None)
    if finding is None:
        _fail("[FAIL] Finding not found in {0}: {1}".format(slug, args.finding_id))
        return 1
    reason = str(args.reason or "").strip()
    if args.status == "dismissed" and not reason:
        _fail("[FAIL] Dismissing a finding needs --reason: say why it is not a problem.")
        return 1
    if finding["status"] != "open":
        _fail("[FAIL] Finding {0} is already {1}.".format(finding["id"], finding["status"]))
        return 1
    finding["status"] = args.status
    finding["reason"] = reason or None
    finding["resolved_at"] = record["updated"] = now_iso()
    write_json_atomic(review_path(state, slug), record)
    print("[OK] Finding {0} {1}".format(finding["id"], args.status))
    return 0


def render_findings_review(state, slug, record, json_output=False):
    """Print a findings review for `review show`; return the exit code."""
    cover = coverage(state, record)
    if json_output:
        view = dict(record)
        view["coverage"] = {"covers_current_source": cover["covers"], "text": cover["text"],
                            "commits_since": cover["commits_since"]}
        print(json.dumps(view, indent=2))
        return 0
    rounds = record.get("rounds") or []
    print("[OK] Findings review: {0} ({1} round{2})".format(slug, len(rounds), "" if len(rounds) == 1 else "s"))
    for round_ in rounds:
        commit = (round_.get("provenance") or {}).get("git_commit")
        found = round_.get("findings", [])
        state_word = "clean" if round_.get("clean") else (
            "findings resolved" if found and not any(f["status"] == "open" for f in found) else "open")
        print("Round {0} (commit {1}): {2}".format(
            round_["number"], commit[:12] if commit else "unknown", state_word))
        if round_.get("note"):
            print("  note: {0}".format(round_["note"]))
        if round_.get("rethink"):
            print("  rethink: {0}".format(round_["rethink"]))
        for finding in round_.get("findings", []):
            where = " {0}".format(finding["path"]) if finding.get("path") else ""
            tail = ": " + finding["reason"] if finding.get("reason") else ""
            repeat = " (repeat of {0})".format(finding["repeat_of"]) if finding.get("repeat_of") else ""
            print("  {0} [{1}]{2} {3}{4} - {5}{6}".format(
                finding["id"], finding.get("severity", "medium"), where, finding["summary"],
                repeat, finding["status"], tail))
    print("Coverage: {0}".format(cover["text"]))
    print(GUARDRAIL)
    return 0


def findings_attention(state):
    """Status attention items for findings reviews (dicts shaped like attention_item)."""
    items = []
    current = None
    for position, (name, record) in enumerate(list_findings_reviews(state)):
        last = latest_round(record)
        stamp = record.get("updated", "")
        pending = open_findings(record)
        if pending:
            items.append({
                "level": "issue", "source": "review", "timestamp": stamp,
                "summary": "findings review {0} has {1} open finding{2}".format(
                    name, len(pending), "" if len(pending) == 1 else "s"),
                "detail": "{0}; fix or dismiss each with review resolve".format(
                    ", ".join(f["id"] for f in pending)),
            })
        if last and last.get("clean") is not True and last["number"] >= MAX_ROUNDS:
            items.append({
                "level": "warning", "source": "review", "timestamp": stamp,
                "summary": "findings review {0} reached round {1} without a clean pass".format(
                    name, last["number"]),
                "detail": "three rounds did not converge; rethink the approach before another round",
            })
        elif last and last.get("clean") is True and position == 0:
            # Only the newest review is the one being worked on; an older clean
            # review that no longer covers the source is history, not a warning.
            current = current or source_stamp(state)
            if not round_covers(record, current):
                items.append({
                    "level": "warning", "source": "review", "timestamp": stamp,
                    "summary": "findings review {0} no longer covers the current source".format(name),
                    "detail": coverage(state, record, current)["text"],
                })
    return items


def findings_prompt_context(state):
    """The active findings review for a review prompt, or None.

    Active means the most recently updated review whose latest round is not a
    clean pass over the current source.
    """
    current = None
    for name, record in list_findings_reviews(state):
        current = current or source_stamp(state)
        if round_covers(record, current):
            continue
        last = latest_round(record)
        return {
            "name": name,
            "round": last["number"] + 1 if last.get("clean") else last["number"],
            "latest_round": last["number"],
            "open_findings": [f["id"] for f in open_findings(record)],
            "scope": _scope(state, record),
            "dismissed": _dismissed_rows(record),
        }
    return None


def findings_prompt_lines(context):
    if not context:
        return []
    lines = ["Findings review: {0}, round {1}".format(context["name"], context["round"])]
    if context["open_findings"]:
        lines.append("Open findings to resolve first: {0}".format(", ".join(context["open_findings"])))
    lines.extend(_format_scope(context["scope"]))
    lines.extend(_format_dismissed(context["dismissed"]))
    return lines


def add_findings_parser(actions):
    """Register the findings-review leaves under the `review` parser."""
    p = actions.add_parser(
        "round",
        help="Open a findings-review round (at most three before a rethink) or record a clean pass.",
    )
    p.add_argument("name", help="Review name; the first round creates the review.")
    p.add_argument("--note", help="Note stored on the round.")
    p.add_argument(
        "--clean", action="store_true",
        help="Record a completed round in which the reviewer found nothing at the current source.",
    )
    p.add_argument(
        "--rethink", metavar="TEXT",
        help="Required for round 4 and later: why the approach still stands after three rounds.",
    )
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_review_round)
    p = actions.add_parser("finding", help="Record a finding in the latest findings-review round.")
    p.add_argument("name", help="Review name.")
    p.add_argument("summary", help="One-line statement of the finding.")
    p.add_argument("--path", help="File the finding concerns; part of repeat detection.")
    p.add_argument("--severity", choices=SEVERITIES, default="medium", help="Finding severity.")
    p.add_argument("--json", dest="json_output", action="store_true", help="Print JSON.")
    p.set_defaults(handler=cmd_review_finding)
    p = actions.add_parser("resolve", help="Mark a findings-review finding fixed or dismissed.")
    p.add_argument("name", help="Review name.")
    p.add_argument("finding_id", help="Finding id such as F1.")
    p.add_argument("--status", choices=RESOLUTIONS, required=True, help="Resolution.")
    p.add_argument("--reason", help="Why; required to dismiss, which bars the finding from returning.")
    p.set_defaults(handler=cmd_review_resolve)
