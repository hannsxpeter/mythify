"""Read-only loop-fit decision support for the Mythify CLI."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path


LOOPFIT_VERIFY_TERMS = (
    "test", "tests", "build", "lint", "passes", "pass", "compile", "typecheck",
    "type check", "e2e", "ci", "coverage", "benchmark", "smoke", "exit 0",
    "assert", "regression", "check that", "verify",
)
LOOPFIT_RECUR_TERMS = (
    "every", "each", "recurring", "recur", "nightly", "daily", "weekly", "hourly",
    "regenerate", "re-run", "rerun", "keep going", "continuously", "watch",
    "monitor", "until", "per pr", "each pr", "batch", "for all", "sweep",
    "repeatedly", "for every", "on every",
)
LOOPFIT_JUDGMENT_TERMS = (
    "design", "ux", "aesthetic", "subjective", "judgment", "judgement", "decide",
    "tradeoff", "trade-off", "opinion", "creative", "wording", "prioritize",
    "which is better", "looks good", "beautiful", "brainstorm", "explore",
    "what should", "recommend", "advise",
)
LOOPFIT_CHECK_FILES = (
    "pyproject.toml", "setup.py", "tox.ini", "pytest.ini", "package.json",
    "Makefile", "makefile", "Cargo.toml", "go.mod", "build.gradle", "pom.xml",
    "tests", "test",
)


def _loopfit_has_any(text, terms):
    normalized = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())
    lowered = " {0} ".format(" ".join(normalized.split()))
    matches = []
    for term in terms:
        needle = " {0} ".format(" ".join(term.split()))
        if needle in lowered:
            matches.append(term)
    return matches


def project_has_runnable_check(root):
    return any((root / name).exists() for name in LOOPFIT_CHECK_FILES)


def loopfit_project_context():
    """Return the current project root and whether it is a Git repository."""
    try:
        run = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return Path.cwd(), False
    if run.returncode == 0 and run.stdout.strip():
        return Path(run.stdout.strip()), True
    return Path.cwd(), False


def assess_loop_fit(task, is_git_repo, has_check):
    """Return a pure and deterministic loop-fit recommendation."""
    verify_hits = _loopfit_has_any(task, LOOPFIT_VERIFY_TERMS)
    recur_hits = _loopfit_has_any(task, LOOPFIT_RECUR_TERMS)
    judgment_hits = _loopfit_has_any(task, LOOPFIT_JUDGMENT_TERMS)
    automated_verification = bool(verify_hits)
    reproduction_env = bool(is_git_repo)
    recurring = bool(recur_hits)
    needs_judgment = bool(judgment_hits)
    criteria = {
        "automated_verification": automated_verification,
        "recurring": recurring,
        "reproduction_env": reproduction_env,
        "needs_human_judgment": needs_judgment,
    }
    if needs_judgment and not (automated_verification and recurring):
        recommendation = "direct"
        reason = (
            "The goal leans on human judgment. Automate only the checkable parts; "
            "keep the judgment call in the chat."
        )
    elif not automated_verification:
        if has_check:
            recommendation = "supervised"
            reason = (
                "The task names no explicit check, but this repo has runnable "
                "checks. Wrap it in a verifier-gated plan (plan add-step "
                "--verify), not an unattended loop."
            )
        else:
            recommendation = "direct"
            reason = (
                "No machine-checkable done-condition is evident. A loop has "
                "nothing to stop on without an objective gate; do it directly "
                "and record evidence with verify run if a check exists, else "
                "verify claim."
            )
    elif recurring and reproduction_env and not needs_judgment:
        recommendation = "loop"
        reason = (
            "Recurring, machine-checkable, and runs in a reproduction environment: "
            "worth a bounded self-driving loop."
        )
    else:
        recommendation = "supervised"
        reason = (
            "Machine-checkable but one-off or judgment-adjacent: run a "
            "verifier-gated plan or a host-supervised outcome loop, not an "
            "unattended one."
        )
    quoted = shlex.quote(str(task or "").strip() or "task")
    if recommendation == "loop":
        suggested = (
            "mythify outcome start {0} --success DEFINE "
            "--verify DEFINE_CHECK --agent DEFINE_AGENT --max-iterations 5 "
            "--max-cost 100 --escalate-after 3, then outcome run".format(quoted)
        )
    elif recommendation == "supervised":
        suggested = (
            "mythify plan create {0} "
            "--steps '[{{\"title\": \"...\", \"verify_command\": \"DEFINE_CHECK\"}}]', "
            "then plan verify 1; or outcome start ... --verify ... then outcome check".format(quoted)
        )
    else:
        suggested = (
            "Do it directly in the chat. Run verify run if an executable check "
            "exists, else record a verify claim."
        )
    return {
        "kind": "loop_fit",
        "task": str(task or ""),
        "recommendation": recommendation,
        "reason": reason,
        "criteria": criteria,
        "signals": {
            "verify_terms": verify_hits,
            "recurring_terms": recur_hits,
            "judgment_terms": judgment_hits,
            "has_runnable_check": has_check,
            "is_git_repo": is_git_repo,
        },
        "suggested_next": suggested,
        "guardrail": (
            "loop-fit is read-only decision support; it does not run anything, "
            "start a loop, or record evidence."
        ),
    }


def format_loop_fit(payload):
    lines = [
        "[OK] Loop-fit: {0}".format(payload["recommendation"]),
        "Reason: {0}".format(payload["reason"]),
        "Criteria:",
    ]
    labels = {
        "automated_verification": "task names a machine-checkable done-condition",
        "recurring": "work recurs / repeats",
        "reproduction_env": "reproduction environment (git repo)",
        "needs_human_judgment": "needs human judgment",
    }
    for key, label in labels.items():
        mark = "[x]" if payload["criteria"][key] else "[ ]"
        lines.append("  {0} {1}".format(mark, label))
    if payload["signals"].get("has_runnable_check"):
        lines.append("  (note) the repo has runnable checks")
    lines.append("Suggested next: {0}".format(payload["suggested_next"]))
    lines.append("Guardrail: {0}".format(payload["guardrail"]))
    return "\n".join(lines)


def cmd_loop_fit(args, _state):
    root, is_git = loopfit_project_context()
    payload = assess_loop_fit(args.task, is_git, project_has_runnable_check(root))
    if args.json_output:
        print(json.dumps(payload, indent=2))
    else:
        print(format_loop_fit(payload))
    return 0
