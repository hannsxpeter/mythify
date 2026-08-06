"""Read-only loop-fit decision support for the Mythify CLI."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from mythify_classification import QUALITY_CLIMB_POLICY


LOOPFIT_VERIFY_TERMS = (
    # Bare "build" is deliberately absent: as an imperative verb ("build a
    # site at the level of X") it names work, not a done-condition. Only
    # build-outcome phrases count as checkable.
    "test", "tests", "build passes", "build succeeds", "build is green",
    "green build", "lint", "passes", "pass", "compile", "typecheck",
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
LOOPFIT_QUALITY_TERMS = tuple(str(term) for term in QUALITY_CLIMB_POLICY["terms"])
LOOPFIT_BRAKE_LINE = (
    "This loop has no self-stop: the reference bar stays out of reach by design. "
    "State a budget (rounds, minutes, or cost) up front, or you are the brake."
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
    quality_hits = _loopfit_has_any(task, LOOPFIT_QUALITY_TERMS)
    automated_verification = bool(verify_hits)
    reproduction_env = bool(is_git_repo)
    recurring = bool(recur_hits)
    needs_judgment = bool(judgment_hits)
    quality_climb = bool(quality_hits) and not automated_verification
    criteria = {
        "automated_verification": automated_verification,
        "recurring": recurring,
        "reproduction_env": reproduction_env,
        "needs_human_judgment": needs_judgment,
        "open_ended_quality_climb": quality_climb,
    }
    if quality_climb:
        recommendation = "quality_loop"
        reason = (
            "Open-ended quality climb toward a reference bar, with no "
            "machine-checkable done-condition. A checkable loop has nothing to "
            "stop on, but a builder fan-out with one separate harsh critic that "
            "blind-compares the integrated deliverable side by side with the "
            "named reference keeps quality climbing; an explicit budget or the "
            "human is the brake."
        )
    elif needs_judgment and not (automated_verification and recurring):
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
    if recommendation == "quality_loop":
        suggested = (
            "State a round budget in chat, then fan out builder workers on {0}. "
            "Run a separate harsh-critic pass that blind-compares the integrated "
            "deliverable side by side with the named reference (pick one if the "
            "task names none) and says which is better. Record critic verdicts "
            "with verify claim (material, second-class), keep any executable "
            "checks as verify run gates, and stop on the budget or the human "
            "brake, never on the critic's satisfaction.".format(quoted)
        )
    elif recommendation == "loop":
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
            "quality_terms": quality_hits,
            "has_runnable_check": has_check,
            "is_git_repo": is_git_repo,
        },
        "suggested_next": suggested,
        "brake": LOOPFIT_BRAKE_LINE if recommendation == "quality_loop" else None,
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
        "open_ended_quality_climb": "open-ended quality climb toward a reference",
    }
    for key, label in labels.items():
        mark = "[x]" if payload["criteria"].get(key) else "[ ]"
        lines.append("  {0} {1}".format(mark, label))
    if payload["signals"].get("has_runnable_check"):
        lines.append("  (note) the repo has runnable checks")
    lines.append("Suggested next: {0}".format(payload["suggested_next"]))
    if payload.get("brake"):
        lines.append("Brake: {0}".format(payload["brake"]))
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
