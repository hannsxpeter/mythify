"""Which review, if any, covers the source as it is right now."""

from pathlib import Path

from mythify_findings import list_findings_reviews, read_review_readonly, round_covers, source_stamp
from mythify_quality import blast_review_view


def unreadable_reviews(state):
    """Names of review files that cannot be parsed. They never cover anything."""
    folder = Path(state) / "reviews"
    return [
        path.stem for path in (sorted(folder.glob("*.json")) if folder.is_dir() else [])
        if not isinstance(read_review_readonly(path), dict)
    ]


def review_covers_current_source(state):
    """Name of a review that covers the current source, or None.

    A findings review covers it when its latest round is clean and was stamped
    at the current commit and worktree digest. A blast-radius review covers it
    when its merge gate is verified and its change fingerprint is still
    current. Read-only: nothing is written or run, and a malformed review file
    is skipped where it lies, not moved aside.
    """
    current = None
    for name, record in list_findings_reviews(state, readonly=True):
        current = current or source_stamp(state)
        if round_covers(record, current):
            return name
    folder = Path(state) / "reviews"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        record = read_review_readonly(path)
        if not isinstance(record, dict) or record.get("kind") != "blast_radius_review":
            continue
        view = blast_review_view(state, record)
        if (view.get("merge_gate") or {}).get("verified") is True and (
            view["change_freshness"]["status"] == "current"
        ):
            return path.stem
    return None
