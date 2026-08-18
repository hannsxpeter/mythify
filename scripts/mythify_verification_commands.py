"""CLI handlers and parser wiring for verification and reflection commands."""

import json
import os
import re


_deps = {}


TEST_COUNT_PATTERNS = (
    re.compile(r"(?m)^Ran\s+(\d+)\s+tests?\b"),
    re.compile(r"(?m)^#\s*tests\s+(\d+)\s*$"),
    re.compile(r"(?m)(?:^|\s)(\d+)\s+passed(?:\s|,|$)"),
    re.compile(r"(?m)^Tests:\s+.*?\b(\d+)\s+total\b"),
)


def extract_test_count(*texts):
    """Extract a display-only test count without affecting the verdict."""
    combined = "\n".join(str(text or "") for text in texts)
    for pattern in TEST_COUNT_PATTERNS:
        matches = pattern.findall(combined)
        if matches:
            return int(matches[-1])
    return None


def configure_verification_commands(**deps):
    _deps.clear()
    _deps.update(deps)


def _print_artifacts(state, record):
    for channel in ("stdout", "stderr"):
        item = (record.get("artifacts") or {}).get(channel) or {}
        relative = item.get("path")
        if not relative:
            continue
        try:
            text = (state / relative).read_text(encoding="utf-8")
        except OSError:
            continue
        print("--- {0} (full artifact) ---".format(channel))
        print(text, end="" if text.endswith("\n") else "\n")


def cmd_verify_run(args, state):
    if os.environ.get("MYTHIFY_DISABLE_RUN") == "1":
        _deps["fail_func"](_deps["disabled_message"])
        return 2
    try:
        record = _deps["execute_recorded_verification_func"](
            state, args.command, args.claim, args.timeout, parents=args.parent
        )
    except ValueError as exc:
        _deps["fail_func"]("[FAIL] Invalid lineage: {0}.".format(exc))
        return 1
    label = args.claim or args.command
    count = record.get("test_count")
    count_label = ", {0} tests".format(count) if isinstance(count, int) else ""
    print(
        "[{0}] {1}: {2} (exit {3}, {4:.2f}s{5})".format(
            "OK" if record["verified"] else "FAIL",
            "VERIFIED" if record["verified"] else "UNVERIFIED",
            label,
            record["exit_code"],
            record["duration_seconds"],
            count_label,
        )
    )
    if not record["verified"]:
        for channel in ("stdout", "stderr"):
            text = record.get(channel + "_tail")
            if text:
                print("--- {0} (tail) ---".format(channel))
                print(text)
        artifacts = record.get("artifacts") or {}
        paths = [item.get("path") for item in artifacts.values() if item.get("path")]
        if paths:
            print("Artifacts: {0}".format(", ".join(paths)))
    if args.output == "full":
        _print_artifacts(state, record)
    return 0 if record["verified"] else 2


def cmd_verify_claim(args, state):
    record = {
        "kind": "attested",
        "claim": args.claim,
        "evidence": args.evidence,
        "verified": None,
        "timestamp": _deps["now_iso_func"](),
    }
    record.update(_deps["verification_step_context_func"](state))
    _deps["append_chained_jsonl_func"](state / "verifications.jsonl", record)
    print("[WARN] ATTESTED: {0} (self-reported, not machine-checked; prefer verify run)".format(args.claim))
    return 0


def cmd_reflect(args, state):
    if args.json:
        try:
            payload = json.loads(args.json)
        except ValueError:
            _deps["fail_func"]("[FAIL] Invalid JSON for reflect: pass a single JSON object.")
            return 1
        if not isinstance(payload, dict):
            _deps["fail_func"]("[FAIL] Invalid reflect payload: expected a JSON object.")
            return 1
    else:
        payload = {
            key: value for key, value in {
                "action": args.action,
                "outcome": args.outcome,
                "observation": args.observation,
                "next": args.next,
                "root_cause": args.root_cause,
                "lesson": args.lesson,
            }.items() if value is not None
        }
    missing = [key for key in ("action", "outcome", "observation", "next") if not payload.get(key)]
    if missing:
        _deps["fail_func"]("[FAIL] Missing required reflection keys: {0}.".format(", ".join(missing)))
        return 1
    if payload["outcome"] not in _deps["reflect_outcomes"]:
        _deps["fail_func"]("[FAIL] Invalid outcome: {0}. Use one of: {1}.".format(
            payload["outcome"], ", ".join(_deps["reflect_outcomes"])))
        return 1
    lesson = payload.get("lesson") or None
    record = {
        "action": str(payload["action"]),
        "outcome": payload["outcome"],
        "observation": str(payload["observation"]),
        "root_cause": str(payload["root_cause"]) if payload.get("root_cause") else None,
        "next": str(payload["next"]),
        "lesson": str(lesson) if lesson else None,
        "timestamp": _deps["now_iso_func"](),
    }
    _deps["append_jsonl_func"](state / "reflections.jsonl", record)
    print("[OK] Reflection recorded ({0}).".format(record["outcome"]))
    if record["lesson"]:
        detail = "Auto-recorded from a reflection (outcome: {0}). Action: {1}".format(record["outcome"], record["action"])
        _deps["write_lesson_func"](state / "lessons", record["lesson"], detail, ["auto-reflected"])
        print("[OK] Lesson recorded (project): {0}".format(record["lesson"]))
    return 0


def add_verification_parsers(sub, symbols):
    verify = sub.add_parser("verify", help="Verification: run a command (executed) or record a claim (attested).")
    actions = verify.add_subparsers(dest="verify_command", metavar="ACTION", required=True)
    parser = actions.add_parser("run", help="Execute COMMAND and record an executed verification.")
    parser.add_argument("command", help="Shell command to execute.")
    parser.add_argument("--claim", help="What this command verifies.")
    parser.add_argument("--parent", action="append", default=[], help="Parent artifact kind:id. Repeat as needed.")
    parser.add_argument("--output", choices=("compact", "full"), default="compact", help="Print compact verdicts or retained full output.")
    parser.add_argument("--timeout", type=float, default=symbols["DEFAULT_VERIFY_TIMEOUT"], metavar="N", help="Timeout in seconds.")
    parser.set_defaults(handler=cmd_verify_run)
    parser = actions.add_parser("claim", help="Record a self-reported attestation that never counts as verified.")
    parser.add_argument("claim", help="The claim being attested.")
    parser.add_argument("evidence", help="Why you believe the claim holds.")
    parser.set_defaults(handler=cmd_verify_claim)

    parser = sub.add_parser("reflect", help="Record a structured reflection as JSON or flags.")
    parser.add_argument("json", nargs="?", help="Reflection as a JSON object.")
    parser.add_argument("--action", help="What was attempted.")
    parser.add_argument("--outcome", help="One of: success, partial, failure.")
    parser.add_argument("--observation", help="What actually happened.")
    parser.add_argument("--next", help="The next action to take.")
    parser.add_argument("--root-cause", dest="root_cause", help="Root cause, if known.")
    parser.add_argument("--lesson", help="Lesson to auto-record as a project lesson.")
    parser.set_defaults(handler=cmd_reflect)
