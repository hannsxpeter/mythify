"""Eval evolution loop for the Mythify CLI.

The local evaluation harness measures whether Mythify helps; this surface is
how the harness itself grows without an agent grading its own homework. The
loop is: durable failure signals become candidate scenarios (material), a
candidate must show a passing executed verify run, and adoption into the
scenario registry requires the human's words. An agent that invents an eval,
passes it, and adopts it alone has proved nothing; every gate here exists to
make that impossible by default.

Proposals live in .mythify/evals/proposals.json. Adopted scenarios live in
.mythify/evals/scenarios.json, which scripts/local_model_eval.py loads through
--scenario-file. This surface is CLI-only, like `map verify` and
`plan verify`.
"""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mythify_evidence_guard import ZERO_TEST_PATTERNS, noop_verifier_reason
from mythify_eval_scenarios import (
    SCENARIO_REGISTRY_SCHEMA_VERSION,
    load_scenario_file,
    validate_scenario,
)
from mythify_io import read_json, read_jsonl, write_json_atomic

EVAL_PROPOSAL_STATUSES = ("proposed", "adopted", "rejected")
EVAL_GUARDRAIL = (
    "Eval proposals are material, not verification. Adoption gates a scenario "
    "into the registry; only executed runs count as evidence."
)
EVAL_HUMAN_INPUT_MESSAGE = (
    "[FAIL] Human input required: adopting an eval scenario changes what the "
    "harness measures, so the agent cannot approve it from its own words. "
    "Hold the conversation, then pass --human-input with what the human "
    "actually decided. Set MYTHIFY_REQUIRE_HUMAN_INPUT=0 only for legacy "
    "self-approved adoptions."
)
EVAL_HUMAN_INPUT_WAIVED_WARNING = (
    "[WARN] Human-input gate waived: MYTHIFY_REQUIRE_HUMAN_INPUT=0 is set, so "
    "this scenario was adopted without the human's words. The waiver is "
    "stamped on the proposal as human_input_waived."
)
EVAL_RATIONALE_MESSAGE = (
    "[FAIL] Rationale required: pass --rationale with the observed failure or "
    "gap this scenario would catch."
)
# Mirror of the harness's built-in scenario names so propose can refuse a
# collision early. tests/test_mythify_evals.py pins this against
# local_model_eval.BUILTIN_SCENARIO_NAMES, the tuple frozen before any external
# merge, so drift fails the suite instead of shipping.
HARNESS_BUILTIN_SCENARIOS = (
    "word_count_bugfix",
    "query_parser_bugfix",
    "inventory_total_bugfix",
)
DEFAULT_BASELINE_TIMEOUT = 120.0
FALSE_ENV_VALUES = ("0", "false", "no", "off")
# Exit codes that mean the verifier never ran, so the scenario is untested
# rather than red: 126 not executable, 127 not found, 5 unittest collected
# nothing. A baseline that cannot distinguish these from a real failure would
# certify an eval that exercises nothing.
UNUSABLE_BASELINE_EXITS = {5: "collected no tests", 126: "is not executable", 127: "was not found"}
# The mirror of NOOP_COMMANDS. A verifier that always exits nonzero passes the
# red-baseline gate and then can never turn green, so it certifies an eval no
# amount of real work could ever satisfy.
ALWAYS_RED_COMMANDS = frozenset({"false", "exit 1"})


def _missing_dependency(*_args, **_kwargs):
    raise RuntimeError("mythify_evals dependencies are not configured")


now_iso = _missing_dependency
execute_verification = _missing_dependency
load_lessons = _missing_dependency
environ = None


def fail(message):
    print(message, file=sys.stderr)


def configure_evals_store(
    *,
    now_iso_func=None,
    fail_func=None,
    execute_verification_func=None,
    load_lessons_func=None,
    environ_map=None,
):
    global now_iso, fail, execute_verification, load_lessons, environ
    if now_iso_func is not None:
        now_iso = now_iso_func
    if fail_func is not None:
        fail = fail_func
    if execute_verification_func is not None:
        execute_verification = execute_verification_func
    if load_lessons_func is not None:
        load_lessons = load_lessons_func
    if environ_map is not None:
        environ = environ_map


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def evals_dir(state):
    return state / "evals"


def proposals_path(state):
    return evals_dir(state) / "proposals.json"


def registry_path(state):
    return evals_dir(state) / "scenarios.json"


def load_proposals(state):
    record = read_json(proposals_path(state), None)
    if not isinstance(record, dict) or not isinstance(record.get("proposals"), list):
        return {
            "schema_version": SCENARIO_REGISTRY_SCHEMA_VERSION,
            "proposals": [],
            "created": now_iso(),
        }
    # A corrupt entry must not crash every eval command. Drop what cannot be a
    # proposal and keep working with the rest.
    record["proposals"] = [item for item in record["proposals"] if isinstance(item, dict)]
    return record


def save_proposals(state, record):
    record["updated"] = now_iso()
    evals_dir(state).mkdir(parents=True, exist_ok=True)
    write_json_atomic(proposals_path(state), record)


def load_registry(state):
    record = read_json(registry_path(state), None)
    if not isinstance(record, dict) or not isinstance(record.get("scenarios"), dict):
        return {
            "schema_version": SCENARIO_REGISTRY_SCHEMA_VERSION,
            "scenarios": {},
        }
    return record


def save_registry(state, record):
    record["updated"] = now_iso()
    evals_dir(state).mkdir(parents=True, exist_ok=True)
    write_json_atomic(registry_path(state), record)


def next_eval_id(proposals):
    numbers = []
    for proposal in proposals:
        raw = str(proposal.get("id", ""))
        if raw.startswith("E"):
            try:
                numbers.append(int(raw[1:]))
            except ValueError:
                pass
    return "E{0}".format(max(numbers + [0]) + 1)


def find_proposal(record, proposal_id):
    wanted = str(proposal_id or "").strip().upper()
    for proposal in record.get("proposals") or []:
        if str(proposal.get("id", "")).upper() == wanted:
            return proposal
    return None


def proposal_name(proposal):
    return "{0} ({1})".format(proposal.get("title", ""), proposal.get("id", ""))


def require_human_input_enabled():
    raw = (environ or {}).get("MYTHIFY_REQUIRE_HUMAN_INPUT", "")
    return raw.strip().lower() not in FALSE_ENV_VALUES


def normalized_command(text):
    return " ".join(str(text or "").split())


def always_red_reason(command):
    """Why COMMAND can never pass, or None when a real fix could turn it green."""
    normalized = normalized_command(command).lower()
    if normalized in ALWAYS_RED_COMMANDS:
        return "the command always exits nonzero"
    parts = normalized.split()
    if len(parts) == 2 and parts[0] == "exit" and parts[1].isdigit() and parts[1] != "0":
        return "the command always exits {0}".format(parts[1])
    return None


def passing_proposal_verification(state, proposal):
    """A passing executed run this proposal's own `eval verify` produced.

    Scoping is by the `eval_proposal` stamp rather than by command text or a
    positional cursor. Only `eval verify` writes that stamp, and it refuses a
    proposal that is not open, so a matching record cannot predate the
    proposal, cannot be another proposal's evidence, and cannot be an
    unrelated routine run that happens to share a command string. Proposal ids
    never repeat within a workspace, so a rejected id cannot be recycled.
    """
    expected = normalized_command(proposal.get("verify_command"))
    proposal_id = str(proposal.get("id") or "").strip().upper()
    if not expected or not proposal_id:
        return None
    for record in read_jsonl(state / "verifications.jsonl"):
        if (
            record.get("kind") == "executed"
            and record.get("verified") is True
            and record.get("exit_code") == 0
            and str(record.get("eval_proposal") or "").strip().upper() == proposal_id
            and normalized_command(record.get("command")) == expected
        ):
            return record
    return None


def registry_view(state):
    """Adopted names split by whether they carry adoption provenance.

    Anything can be written into scenarios.json by hand. Reporting an entry
    without an `adoption` stamp as adopted would let a self-written scenario
    borrow the gate's credibility, so the two are always named separately.
    """
    scenarios = load_registry(state).get("scenarios", {})
    adopted = []
    unprovenanced = []
    for name in sorted(scenarios):
        entry = scenarios[name] if isinstance(scenarios[name], dict) else {}
        if isinstance(entry.get("adoption"), dict):
            adopted.append(name)
        else:
            unprovenanced.append(name)
    return adopted, unprovenanced


def cli_invocation():
    """How to re-invoke this CLI from any working directory."""
    return "{0} {1}".format(
        shlex.quote(sys.executable), shlex.quote(str(Path(sys.argv[0]).resolve()))
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_eval_scan(args, state):
    """Read-only gap signals from durable state. Writes nothing."""
    recent = max(1, int(args.recent or 10))
    reflections = [
        {
            "action": record.get("action", ""),
            "observation": record.get("observation", ""),
            "root_cause": record.get("root_cause"),
            "timestamp": record.get("timestamp", ""),
        }
        for record in read_jsonl(state / "reflections.jsonl")
        if record.get("outcome") == "failure"
    ][-recent:]
    failing = [
        record
        for record in read_jsonl(state / "verifications.jsonl")
        if record.get("kind") == "executed" and record.get("verified") is False
    ]
    failing_view = [
        {
            "command": record.get("command", ""),
            "exit_code": record.get("exit_code"),
            "claim": record.get("claim", ""),
            "timestamp": record.get("timestamp", ""),
        }
        for record in failing[-recent:]
    ]
    counts = {}
    for record in failing:
        key = normalized_command(record.get("command"))
        counts[key] = counts.get(key, 0) + 1
    repeated = sorted(
        (
            {"command": command, "failures": count}
            for command, count in counts.items()
            if count >= 2
        ),
        key=lambda item: item["failures"],
        reverse=True,
    )[:5]
    lessons = sorted(
        load_lessons(state / "lessons", "project"),
        key=lambda lesson: lesson.get("created", ""),
        reverse=True,
    )[:recent]
    lesson_view = [
        {"title": lesson.get("title", ""), "tags": lesson.get("tags", [])}
        for lesson in lessons
    ]
    proposals = load_proposals(state).get("proposals", [])
    open_proposals = [p for p in proposals if p.get("status") == "proposed"]
    adopted, unprovenanced = registry_view(state)
    guidance = (
        "Signals are material, not evidence. Draft a candidate scenario file, "
        "then register it with: mythify eval propose TITLE --scenario-file "
        "PATH --rationale WHY"
    )
    if args.json_output:
        print(json.dumps({
            "failure_reflections": reflections,
            "failing_verifications": failing_view,
            "repeated_failing_commands": repeated,
            "recent_lessons": lesson_view,
            "open_proposals": [proposal_name(p) for p in open_proposals],
            "adopted_scenarios": adopted,
            "unprovenanced_scenarios": unprovenanced,
            "guardrail": EVAL_GUARDRAIL,
            "guidance": guidance,
        }, indent=2))
        return 0
    print("[OK] Eval gap signals (read-only, last {0} per source)".format(recent))
    print("Failure reflections: {0}".format(len(reflections)))
    for item in reflections:
        line = "- {0}: {1}".format(item["action"], item["observation"])
        if item.get("root_cause"):
            line += " (root cause: {0})".format(item["root_cause"])
        print(line)
    print("Failing executed verifications: {0}".format(len(failing_view)))
    for item in failing_view:
        print("- exit {0}: {1}".format(item["exit_code"], item["command"]))
    if repeated:
        print("Repeated failing commands:")
        for item in repeated:
            print("- {0}x: {1}".format(item["failures"], item["command"]))
    print("Recent lessons: {0}".format(len(lesson_view)))
    for item in lesson_view:
        print("- {0}".format(item["title"]))
    if open_proposals:
        print("Open proposals: {0}".format(
            ", ".join(proposal_name(p) for p in open_proposals)
        ))
    if adopted:
        print("Adopted scenarios: {0}".format(", ".join(adopted)))
    if unprovenanced:
        print(
            "[WARN] Registry scenarios without adoption provenance: {0}".format(
                ", ".join(unprovenanced)
            )
        )
    print(guidance)
    return 0


def cmd_eval_propose(args, state):
    rationale = (args.rationale or "").strip()
    if not rationale:
        fail(EVAL_RATIONALE_MESSAGE)
        return 1
    try:
        entries = load_scenario_file(args.scenario_file)
    except ValueError as exc:
        fail("[FAIL] Invalid scenario file: {0}".format(exc))
        return 1
    if len(entries) != 1:
        fail(
            "[FAIL] A proposal carries exactly one scenario; {0} has "
            "{1}.".format(args.scenario_file, len(entries))
        )
        return 1
    name, entry = next(iter(entries.items()))
    if name in HARNESS_BUILTIN_SCENARIOS:
        fail(
            "[FAIL] Scenario name {0!r} collides with a built-in harness "
            "scenario.".format(name)
        )
        return 1
    record = load_proposals(state)
    for proposal in record.get("proposals", []):
        if proposal.get("scenario_name") == name and proposal.get("status") != "rejected":
            fail(
                "[FAIL] Scenario name {0!r} is already carried by proposal "
                "{1}.".format(name, proposal_name(proposal))
            )
            return 1
    if name in load_registry(state).get("scenarios", {}):
        fail("[FAIL] Scenario name {0!r} is already adopted.".format(name))
        return 1
    verifier = entry.get("fanout_merge_verifier", "python3 -m unittest")
    unusable_reason = noop_verifier_reason(verifier) or always_red_reason(verifier)
    if unusable_reason:
        fail(
            "[FAIL] Scenario verifier {0!r} proves nothing: {1}. An eval needs "
            "a verifier that fails on the broken files and passes on a real "
            "fix.".format(verifier, unusable_reason)
        )
        return 1
    proposal_id = next_eval_id(record.get("proposals", []))
    # The verify command is always the proposal's own red-baseline check. An
    # override would let a proposal swap in an unrelated green command and
    # adopt a scenario the baseline had already refused.
    verify_command = "{0} eval baseline {1}".format(cli_invocation(), proposal_id)
    proposal = {
        "id": proposal_id,
        "title": args.title,
        "rationale": rationale,
        "sources": list(args.source or []),
        "scenario_name": name,
        "scenario": entry,
        "verify_command": verify_command,
        "status": "proposed",
        "created": now_iso(),
    }
    record.setdefault("proposals", []).append(proposal)
    save_proposals(state, record)
    if args.json_output:
        print(json.dumps(proposal, indent=2))
        return 0
    print("[OK] Proposed eval scenario {0!r} as {1}".format(name, proposal_id))
    print("Rationale: {0}".format(rationale))
    print("Verify: {0}".format(verify_command))
    print("Run it with: mythify eval verify {0}".format(proposal_id))
    print(
        "Then adopt with the human's words: mythify eval adopt {0} "
        "--human-input \"...\"".format(proposal_id)
    )
    print(EVAL_GUARDRAIL)
    return 0


def cmd_eval_baseline(args, state):
    """Exit 0 only when the candidate scenario genuinely starts red.

    An eval whose verifier already passes on the unmodified files cannot
    detect the failure it claims to test, so a green baseline is a refusal.
    A nonzero exit is not enough either: a verifier that was not found, is not
    executable, collected no tests, or reports zero tests in its output never
    exercised the scenario, so it proves nothing and is refused as well. This
    is the verify_command target for every proposal.
    """
    if (environ or {}).get("MYTHIFY_DISABLE_RUN") == "1":
        fail(
            "[FAIL] eval baseline is disabled: MYTHIFY_DISABLE_RUN=1 is set. No "
            "command was executed and nothing was recorded."
        )
        return 2
    record = load_proposals(state)
    proposal = find_proposal(record, args.id)
    if proposal is None:
        fail("[FAIL] Proposal {0} not found.".format(args.id))
        return 1
    name = proposal.get("scenario_name", "")
    entry = proposal.get("scenario") or {}
    problems = validate_scenario(name, entry)
    if problems:
        fail("[FAIL] Invalid stored scenario: {0}".format("; ".join(problems)))
        return 1
    verifier = entry.get("fanout_merge_verifier", "python3 -m unittest")
    tmp_root = state / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="eval-baseline-", dir=str(tmp_root)))
    try:
        for relative_path, content in entry["files"].items():
            path = workspace / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content + "\n", encoding="utf-8")
        timeout = args.timeout if args.timeout is not None else DEFAULT_BASELINE_TIMEOUT
        try:
            run = subprocess.run(
                verifier,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            fail(
                "[FAIL] Baseline timed out after {0:.0f}s running: {1}".format(
                    timeout, verifier
                )
            )
            return 1
        # Zero-test output is checked before the green refusal: on Python
        # older than 3.12, unittest exits 0 when it collects nothing, and a
        # run that exercised no tests is unusable regardless of exit code.
        output = "{0}\n{1}".format(run.stdout or "", run.stderr or "").lower()
        for pattern in ZERO_TEST_PATTERNS:
            if pattern in output:
                fail(
                    "[FAIL] Baseline unusable: {0!r} reported '{1}', so it "
                    "exercised nothing in scenario {2!r} despite exiting "
                    "{3}.".format(verifier, pattern, name, run.returncode)
                )
                return 1
        if run.returncode == 0:
            fail(
                "[FAIL] Baseline green: {0!r} passes on the unmodified files, "
                "so scenario {1!r} cannot detect the failure it claims to "
                "test.".format(verifier, name)
            )
            return 1
        unusable = UNUSABLE_BASELINE_EXITS.get(run.returncode)
        if unusable:
            fail(
                "[FAIL] Baseline unusable: {0!r} exits {1} because it {2}, so "
                "it never exercised scenario {3!r}. A red exit only counts "
                "when the verifier actually ran the scenario.".format(
                    verifier, run.returncode, unusable, name
                )
            )
            return 1
        print(
            "[OK] Baseline red as required: {0!r} exits {1} on the unmodified "
            "files of scenario {2!r}.".format(verifier, run.returncode, name)
        )
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def cmd_eval_verify(args, state):
    """Run a proposal's verify command and scope the evidence to it."""
    if (environ or {}).get("MYTHIFY_DISABLE_RUN") == "1":
        fail(
            "[FAIL] eval verify is disabled: MYTHIFY_DISABLE_RUN=1 is set. No "
            "command was executed and nothing was recorded."
        )
        return 2
    record = load_proposals(state)
    proposal = find_proposal(record, args.id)
    if proposal is None:
        fail("[FAIL] Proposal {0} not found.".format(args.id))
        return 1
    if proposal.get("status") != "proposed":
        fail(
            "[FAIL] Proposal {0} is {1}, not proposed.".format(
                proposal_name(proposal), proposal.get("status")
            )
        )
        return 1
    command = (proposal.get("verify_command") or "").strip()
    if not command:
        fail(
            "[FAIL] Proposal {0} has no verify_command.".format(proposal_name(proposal))
        )
        return 1
    context = {
        "eval_proposal": proposal.get("id"),
        "eval_title": proposal.get("title"),
        "eval_scenario": proposal.get("scenario_name"),
    }
    claim = "eval proposal {0}: {1}".format(proposal.get("id"), proposal.get("title", ""))
    result = execute_verification(state, command, claim, args.timeout, context)
    if result["verified"]:
        print(
            "[OK] VERIFIED proposal {0}: {1} (exit 0, {2:.2f}s)".format(
                proposal.get("id"), command, result["duration_seconds"]
            )
        )
        print(
            "Next: mythify eval adopt {0} --human-input \"...\"".format(
                proposal.get("id")
            )
        )
        return 0
    print(
        "[FAIL] UNVERIFIED proposal {0}: {1} (exit {2}, {3:.2f}s)".format(
            proposal.get("id"), command, result["exit_code"], result["duration_seconds"]
        )
    )
    if result["stdout_tail"]:
        print("--- stdout (tail) ---")
        print(result["stdout_tail"])
    if result["stderr_tail"]:
        print("--- stderr (tail) ---")
        print(result["stderr_tail"])
    return 2


def cmd_eval_adopt(args, state):
    record = load_proposals(state)
    proposal = find_proposal(record, args.id)
    if proposal is None:
        fail("[FAIL] Proposal {0} not found.".format(args.id))
        return 1
    if proposal.get("status") != "proposed":
        fail(
            "[FAIL] Proposal {0} is already {1}.".format(
                proposal_name(proposal), proposal.get("status")
            )
        )
        return 1
    human_input = (args.human_input or "").strip()
    if not human_input:
        if require_human_input_enabled():
            fail(EVAL_HUMAN_INPUT_MESSAGE)
            return 1
        proposal["human_input_waived"] = True
        fail(EVAL_HUMAN_INPUT_WAIVED_WARNING)
    evidence = passing_proposal_verification(state, proposal)
    if evidence is None:
        fail(
            "[FAIL] Verified evidence required: no passing executed run of "
            "proposal {0}'s own baseline check was recorded. Evidence is "
            "matched by proposal, so another proposal's run or an unrelated "
            "run of the same command cannot stand in. Run: mythify eval verify "
            "{1}".format(proposal_name(proposal), proposal.get("id"))
        )
        return 1
    name = proposal.get("scenario_name", "")
    entry = proposal.get("scenario") or {}
    problems = validate_scenario(name, entry)
    if problems:
        fail(
            "[FAIL] Invalid stored scenario: {0}. Refusing to write it into "
            "the registry, where it would break every load.".format(
                "; ".join(problems)
            )
        )
        return 1
    registry = load_registry(state)
    if name in registry.get("scenarios", {}):
        fail("[FAIL] Scenario name {0!r} is already adopted.".format(name))
        return 1
    stamp = now_iso()
    adopted = dict(entry)
    # Provenance rides on the registry entry so a reader can tell an adopted
    # scenario from one hand-written straight into the file, and can see when
    # the human gate was waived.
    adopted["adoption"] = {
        "proposal": proposal.get("id"),
        "adopted_at": stamp,
        "human_input": human_input,
        "human_input_waived": bool(proposal.get("human_input_waived")),
        "verified_command": evidence.get("command", ""),
    }
    registry.setdefault("scenarios", {})[name] = adopted
    save_registry(state, registry)
    proposal["status"] = "adopted"
    proposal["adopted_at"] = stamp
    proposal["human_input"] = human_input
    proposal["verified_command"] = evidence.get("command", "")
    save_proposals(state, record)
    if args.json_output:
        print(json.dumps(proposal, indent=2))
        return 0
    print("[OK] Adopted eval scenario {0!r} from {1}".format(name, proposal.get("id")))
    if human_input:
        print("Human input: {0}".format(human_input))
    print("Registry: {0}".format(registry_path(state)))
    harness = Path(sys.argv[0]).resolve().parent / "local_model_eval.py"
    print(
        "Run it with: {0} {1} --scenario-file {2} --scenario {3}".format(
            shlex.quote(sys.executable), shlex.quote(str(harness)),
            registry_path(state), name
        )
    )
    print(EVAL_GUARDRAIL)
    return 0


def cmd_eval_reject(args, state):
    reason = (args.reason or "").strip()
    if not reason:
        fail("[FAIL] Reason required: pass --reason for why this scenario is refused.")
        return 1
    record = load_proposals(state)
    proposal = find_proposal(record, args.id)
    if proposal is None:
        fail("[FAIL] Proposal {0} not found.".format(args.id))
        return 1
    if proposal.get("status") != "proposed":
        fail(
            "[FAIL] Proposal {0} is already {1}.".format(
                proposal_name(proposal), proposal.get("status")
            )
        )
        return 1
    proposal["status"] = "rejected"
    proposal["rejected_at"] = now_iso()
    proposal["reject_reason"] = reason
    save_proposals(state, record)
    print("[OK] Rejected proposal {0}".format(proposal_name(proposal)))
    print("Why: {0}".format(reason))
    return 0


def cmd_eval_list(args, state):
    record = load_proposals(state)
    proposals = record.get("proposals", [])
    adopted, unprovenanced = registry_view(state)
    if args.json_output:
        print(json.dumps({
            "proposals": proposals,
            "adopted_scenarios": adopted,
            "unprovenanced_scenarios": unprovenanced,
            "guardrail": EVAL_GUARDRAIL,
        }, indent=2))
        return 0
    print("[OK] Eval proposals: {0}".format(len(proposals)))
    for proposal in proposals:
        print(
            "- {0} [{1}] scenario {2!r}".format(
                proposal_name(proposal),
                proposal.get("status"),
                proposal.get("scenario_name"),
            )
        )
    print("Adopted scenarios: {0}".format(", ".join(adopted) if adopted else "none"))
    if unprovenanced:
        print(
            "[WARN] Registry scenarios without adoption provenance: {0}".format(
                ", ".join(unprovenanced)
            )
        )
    return 0


def cmd_eval_show(args, state):
    record = load_proposals(state)
    proposal = find_proposal(record, args.id)
    if proposal is None:
        fail("[FAIL] Proposal {0} not found.".format(args.id))
        return 1
    if args.json_output:
        print(json.dumps(proposal, indent=2))
        return 0
    print("[OK] Proposal {0}".format(proposal_name(proposal)))
    print("Status: {0}".format(proposal.get("status")))
    print("Scenario: {0}".format(proposal.get("scenario_name")))
    print("Rationale: {0}".format(proposal.get("rationale", "")))
    for source in proposal.get("sources", []):
        print("Source: {0}".format(source))
    print("Verify: {0}".format(proposal.get("verify_command", "")))
    if proposal.get("human_input"):
        print("Human input: {0}".format(proposal["human_input"]))
    if proposal.get("reject_reason"):
        print("Reject reason: {0}".format(proposal["reject_reason"]))
    return 0
