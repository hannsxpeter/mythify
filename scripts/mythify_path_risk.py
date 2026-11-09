"""Risk from the paths a step changed.

When a step starts, the commit it started from is stamped on it. When it
completes, the paths changed since that commit are matched against the
classification rules' path_risk globs. A high-risk change needs a review that
covers the current source and the person's own words approving it. Like stale
evidence, a missing piece warns by default and refuses under strict context.
judge_path_risk only reads: it never prints and never changes the plan or the
step, so a caller stamps the result.
"""

import os
from pathlib import Path

from mythify_classification import RISK_RANK, classify_paths
from mythify_provenance import changed_paths, git_commit, project_root_for_state
from mythify_review_cover import review_covers_current_source
from mythify_runtime_helpers import now_iso

RISK_GATE_VARIABLE = "MYTHIFY_RISK_GATE"
FALSE_ENV_VALUES = ("0", "false", "no", "off")
STAMPED_KEYS = ("risk", "human_input", "risk_gate_waived")
HUMAN_INPUT_HELP = (
    "What the human actually said approving this high-risk change. Never write "
    "these words yourself; ask the person and record what they said."
)
MESSAGE_PATHS = 5
STAMP_PATHS = 50


def risk_gate_waived(environ=None):
    raw = (environ if environ is not None else os.environ).get(RISK_GATE_VARIABLE, "")
    return raw.strip().lower() in FALSE_ENV_VALUES


def stamp_step_start(state, step):
    """Record the commit and time a step started, for its changed-path check.

    Called before the step's status changes. A step already in progress keeps
    its first start, so `plan verify` and a repeated `step ... in_progress`
    cannot move the baseline past work already done.
    """
    if step.get("status") == "in_progress" and step.get("started_at"):
        return
    root = project_root_for_state(state)
    step["started_commit"] = git_commit(root)
    step["started_at"] = now_iso()


def judge_path_risk(state, plan, step, human_input=None, environ=None):
    """Judge the high-risk rule for STEP at completion. Pure: reads only.

    Returns a dict: level ("high", "medium", "low", or None when git cannot
    list the changes), paths (the high-risk paths), review (name of a covering
    review or None), human_input (bool), waived (bool), ok (False means
    refuse), messages (list of (level, text) in print order, "fail" meaning
    stderr), and stamp (what to record on the step as "risk", or None).
    """
    verdict = {"level": None, "paths": [], "review": None, "human_input": False,
               "waived": False, "ok": True, "messages": [], "stamp": None}
    changed = changed_paths(state, step.get("started_commit"))
    if changed is None:
        return verdict
    classified = classify_paths(changed)
    verdict["level"] = classified["risk"]
    if classified["risk"] != "high":
        return verdict
    paths = [match["path"] for match in classified["matches"] if match["risk"] == "high"]
    review = review_covers_current_source(state)
    given = bool(str(human_input or "").strip())
    verdict.update(paths=paths, review=review, human_input=given)
    verdict["stamp"] = {"level": "high", "paths": paths[:STAMP_PATHS],
                        "review": review, "human_input": given}
    missing = []
    if not review:
        missing.append("a review that covers the current source")
    if not given:
        missing.append("--human-input")
    shown = ", ".join(paths[:MESSAGE_PATHS]) + (
        " (+{0} more)".format(len(paths) - MESSAGE_PATHS) if len(paths) > MESSAGE_PATHS else "")
    if risk_gate_waived(environ):
        verdict["waived"] = True
        verdict["messages"].append((
            "fail",
            "[WARN] Risk gate waived: {0}=0 is set, so high-risk paths ({1}) complete without "
            "a covering review or the person's approval. The waiver is stamped on the step as "
            "risk_gate_waived.".format(RISK_GATE_VARIABLE, shown),
        ))
        return verdict
    if not missing:
        return verdict
    strict = bool(plan.get("strict_context"))
    verdict["ok"] = not strict
    verdict["messages"].append((
        "fail",
        "{0} High-risk paths changed in this step: {1}. Missing: {2}. Run an independent "
        "review and record it (review round NAME --clean at the current source, or review "
        "blast-radius), then complete with: step {3} completed RESULT --human-input "
        "\"WHAT THE PERSON SAID\".{4}".format(
            "[FAIL]" if strict else "[WARN]", shown, " and ".join(missing), step.get("id"),
            "" if not strict else " Set {0}=0 only when the user asks.".format(RISK_GATE_VARIABLE)),
    ))
    return verdict


def stamp_completion_risk(step, risk, human_input):
    """Write the risk verdict and human input onto a step being completed."""
    for key in STAMPED_KEYS:
        step.pop(key, None)
    if risk and risk.get("stamp"):
        step["risk"] = risk["stamp"]
        if risk.get("waived"):
            step["risk_gate_waived"] = True
    if str(human_input or "").strip():
        step["human_input"] = human_input.strip()


def format_step_risk(step):
    """Lines for `plan show`: the stamped risk of a completed step, or []."""
    risk = step.get("risk")
    if not isinstance(risk, dict):
        return []
    return ["        risk: {0} (human input: {1}; covering review: {2}{3}) paths: {4}".format(
        risk.get("level", "unknown"),
        "yes" if risk.get("human_input") else "no",
        risk.get("review") or "none",
        "; gate waived" if step.get("risk_gate_waived") else "",
        ", ".join(risk.get("paths") or []) or "none",
    )]


def diff_risk(state):
    """classify_paths over the current uncommitted and untracked changes.

    None outside git or when nothing changed. STATE may be None, in which
    case the current directory is the project.
    """
    root = project_root_for_state(state) if state is not None else Path.cwd()
    changed = changed_paths(state, None, root=root)
    return classify_paths(changed) if changed else None


def apply_diff_risk(classification, diff):
    """Add diff_risk to a route classification and raise its review advisory.

    The existing "risk" field keeps its meaning (the task wording). When the
    changed paths are riskier than the wording, the review advisory becomes
    independent and names the paths.
    """
    classification["diff_risk"] = diff
    if diff and RISK_RANK[diff["risk"]] > RISK_RANK.get(classification.get("risk"), 0):
        named = ", ".join(match["path"] for match in diff["matches"][:MESSAGE_PATHS])
        classification["review"] = {
            "independent": True,
            "reason": "The changed paths carry {0} risk, higher than the task wording ({1}): {2}.".format(
                diff["risk"], classification.get("risk"), named),
        }
    return classification
