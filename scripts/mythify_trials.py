"""Pinned evidence trials: compare an instruction edit with a pinned evaluator.

The trusted operator supplies the manifest, one pinned evaluator script, and
raw per-run evidence. Mythify validates and stages the exact pinned bytes, runs
the evaluator through the ordinary recorded verification path, and judges only
the evaluator's per-task output. It starts no agent and promotes nothing.
"""

import difflib
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile

from mythify_evidence_guard import run_disabled

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = MAX_EVALUATOR_BYTES = MAX_OUTPUT_BYTES = 256 * 1024
MAX_INSTRUCTION_BYTES = 64 * 1024
MAX_INSTRUCTION_LINES = 2000
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_RUNS = 50
MAX_FILES = 256
MAX_TERMS = 32
MAX_EDIT_LINES = 6
MAX_EDIT_BYTES = 1024
TRIAL_TIMEOUT = 300.0
NO_STEP = {"plan": None, "step_id": None, "step_title": None, "step_status": None}
DISABLED_MESSAGE = (
    "[FAIL] trial compare is disabled: MYTHIFY_DISABLE_RUN=1 is set. No command was "
    "executed and nothing was recorded."
)
# Conservative screen over added instruction lines. Semantic judging belongs to
# the evaluator and a human, not to this list.
SHORTCUT_PATTERNS = tuple(re.compile(p) for p in (
    r"\bbenchmark", r"\b(?:grader|grading|oracle)\b", r"\bhard[\s-]?cod",
    r"\bskip(?:ping)?\s+(?:the\s+|all\s+)?(?:tests?|verif|checks?)",
    r"\b(?:answer\s+key|reference\s+answers?|expected\s+answers?)\b",
))
SHA256 = re.compile(r"[0-9a-f]{64}")
REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_store = {}


def configure_trial_store(**dependencies):
    _store.update(dependencies)


class TrialError(ValueError):
    """The trial inputs are invalid; nothing was executed."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise TrialError("duplicate JSON key: " + repr(key))
        out[key] = value
    return out


def _non_finite(name):
    raise TrialError("non-finite JSON number: " + name)


def parse_json(data, label):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates,
                          parse_constant=_non_finite)
    except TrialError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise TrialError(label + " is not valid JSON") from None


def read_bounded(path, limit, label):
    """Read a regular file of at most LIMIT bytes; return its resolved path and bytes."""
    real = os.path.realpath(path)
    try:
        fd = os.open(real, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise TrialError("cannot read {0}: {1}".format(label, exc.strerror)) from None
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise TrialError(label + " is not a regular file")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise TrialError("{0} exceeds {1} bytes".format(label, limit))
    return real, data


def _obj(value, keys, label, optional=()):
    if not isinstance(value, dict):
        raise TrialError(label + " must be an object")
    if set(keys) - set(value) or set(value) - set(keys) - set(optional):
        raise TrialError("{0} keys must be {1}".format(label, ", ".join(list(keys) + [k + "?" for k in optional])))
    return value


def _text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise TrialError("{0} must be non-empty text of at most {1} characters".format(label, limit))
    return value


def _ref(value, label):
    _obj(value, ("path", "sha256"), label)
    _text(value["path"], label + " path", 4096)
    if not isinstance(value["sha256"], str) or not SHA256.fullmatch(value["sha256"]):
        raise TrialError(label + " sha256 must be 64 lowercase hex characters")
    return value


def _revision(value, label):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise TrialError(label + " must be a full lowercase hex Git revision")
    return value


def _runs(side, label):
    runs = side.get("runs")
    if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_RUNS:
        raise TrialError("{0} runs must be a list of 1 to {1} runs".format(label, MAX_RUNS))
    for run in runs:
        _run(run, label + " run")
    ids = [run["task_id"] for run in runs]
    if len(set(ids)) != len(ids):
        raise TrialError(label + " task ids must be unique")
    return {run["task_id"]: run for run in runs}


def _run(run, label, extra=()):
    _obj(run, ("task_id", "task", "evidence", "workspace") + tuple(extra), label)
    _text(run["task_id"], label + " task_id", 128)
    _ref(run["task"], label + " task")
    _ref(run["evidence"], label + " evidence")
    _text(run["workspace"], label + " workspace", 4096)
    if not os.path.isabs(run["workspace"]):
        raise TrialError(label + " workspace must be an absolute path")
    for key in extra:
        _ref(run[key], label + " " + key)


def validate_manifest(raw):
    top = _obj(raw, ("schema_version", "baseline_revision", "runner", "baseline", "candidate"),
               "manifest", ("replay", "forbidden_terms"))
    if type(top["schema_version"]) is not int or top["schema_version"] != SCHEMA_VERSION:
        raise TrialError("schema_version must be 1")
    _revision(top["baseline_revision"], "baseline_revision")
    _obj(top["runner"], ("model", "environment"), "runner")
    _text(top["runner"]["model"], "runner model", 256)
    _text(top["runner"]["environment"], "runner environment", 256)
    baseline = _obj(top["baseline"], ("instructions", "runs"), "baseline")
    candidate = _obj(top["candidate"], ("instructions", "parent_revision", "rationale", "runs"),
                     "candidate", ("predicted_gain",))
    _ref(baseline["instructions"], "baseline instructions")
    _ref(candidate["instructions"], "candidate instructions")
    if _revision(candidate["parent_revision"], "parent_revision") != top["baseline_revision"]:
        raise TrialError("parent_revision must equal baseline_revision")
    _text(candidate["rationale"], "rationale")
    if "predicted_gain" in candidate:
        _text(candidate["predicted_gain"], "predicted_gain")
    base_runs, cand_runs = _runs(baseline, "baseline"), _runs(candidate, "candidate")
    if set(base_runs) != set(cand_runs):
        raise TrialError("baseline and candidate must cover the same task ids")
    for task_id, run in base_runs.items():
        if run["task"]["sha256"] != cand_runs[task_id]["task"]["sha256"]:
            raise TrialError("task {0!r} has different specifications in baseline and candidate".format(task_id))
    evidence = [r["evidence"]["sha256"] for r in list(base_runs.values()) + list(cand_runs.values())]
    if "replay" in top:
        replay = top["replay"]
        _run(replay, "replay", ("runbook",))
        evidence.append(replay["evidence"]["sha256"])
        if replay["task_id"] in base_runs:
            raise TrialError("replay task_id must differ from calibration task ids")
        if replay["task"]["sha256"] in {r["task"]["sha256"] for r in base_runs.values()}:
            raise TrialError("replay task must differ from calibration tasks")
        for run in list(base_runs.values()) + list(cand_runs.values()):
            if _overlap(os.path.realpath(run["workspace"]), os.path.realpath(replay["workspace"])):
                raise TrialError("replay workspace must not overlap a calibration workspace")
    if len(set(evidence)) != len(evidence):
        raise TrialError("every run needs its own raw evidence")
    terms = top.get("forbidden_terms", [])
    if not isinstance(terms, list) or len(terms) > MAX_TERMS:
        raise TrialError("forbidden_terms must be a list of at most {0} names".format(MAX_TERMS))
    for term in terms:
        _text(term, "forbidden term", 64)
    return top


def inside(path, space):
    """PATH is SPACE or below it, by resolved name or by filesystem identity.

    Identity covers aliases a name comparison misses (for example case-folded
    names). Paths that no longer exist are compared by name only.
    """
    if os.path.commonpath([path, space]) == space:
        return True
    try:
        if not os.path.exists(space):
            return False
        chain = [path]
        while os.path.dirname(chain[-1]) != chain[-1]:
            chain.append(os.path.dirname(chain[-1]))
        return any(os.path.exists(item) and os.path.samefile(item, space) for item in chain)
    except OSError:
        return True


def _overlap(a, b):
    return inside(a, b) or inside(b, a)


def workspaces(top):
    paths = [r["workspace"] for side in ("baseline", "candidate") for r in top[side]["runs"]]
    if "replay" in top:
        paths.append(top["replay"]["workspace"])
    return sorted({os.path.realpath(p) for p in paths})


def require_outside(spaces, controller, nested=True):
    """Refuse a controller path inside a workspace (and, with NESTED, containing one)."""
    for label, path in controller.items():
        real = os.path.realpath(path)
        for space in spaces:
            if inside(real, space) or (nested and inside(space, real)):
                raise TrialError("{0} must be outside every declared workspace: {1}".format(label, space))


def edit_summary(before, after, top):
    try:
        text = before.decode("utf-8"), after.decode("utf-8")
    except UnicodeDecodeError:
        raise TrialError("instructions must be UTF-8 text") from None
    if "\x00" in text[0] + text[1]:
        raise TrialError("instructions must not contain NUL")
    # Line separators count as content: a wholesale line-ending change is a big edit.
    old, new = (t.splitlines(keepends=True) for t in text)
    if max(len(old), len(new)) > MAX_INSTRUCTION_LINES:
        raise TrialError("instructions must be text of at most {0} lines".format(MAX_INSTRUCTION_LINES))
    if [line.strip() for line in old] == [line.strip() for line in new]:
        raise TrialError("the candidate instructions make no real change")
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            removed += old[i1:i2]
            added += new[j1:j2]
    lines = len(removed) + len(added)
    size = sum(len(line.encode("utf-8")) for line in removed + added)
    if lines > MAX_EDIT_LINES or size > MAX_EDIT_BYTES:
        raise TrialError("edit changes {0} lines and {1} bytes; the budget is {2} lines and {3} bytes".format(
            lines, size, MAX_EDIT_LINES, MAX_EDIT_BYTES))
    text = "".join(added).lower()
    ids = [run["task_id"] for side in ("baseline", "candidate") for run in top[side]["runs"]]
    ids += [top["replay"]["task_id"]] if "replay" in top else []
    for term in top.get("forbidden_terms", []):
        if term.lower() in text:
            raise TrialError("added instructions contain forbidden term {0!r}".format(term))
    for task_id in ids:
        if re.search(r"(?<![a-z0-9])" + re.escape(task_id.lower()) + r"(?![a-z0-9])", text):
            raise TrialError("added instructions name task id {0!r}".format(task_id))
    for pattern in SHORTCUT_PATTERNS:
        match = pattern.search(text)
        if match:
            raise TrialError("added instructions contain shortcut language: {0!r}".format(match.group(0)))
    return {"changed_lines": lines, "changed_bytes": size}


class Staging:
    """Exact pinned bytes copied once per digest into protected scratch space."""

    def __init__(self, root, base):
        self.root, self.base, self.paths, self.total = root, base, {}, 0
        self.known = {}
        self.files = os.path.join(root, "files")
        os.mkdir(self.files, 0o700)

    def put(self, ref, limit, label):
        _, data = read_bounded(os.path.join(self.base, ref["path"]), limit, label)
        if digest(data) != ref["sha256"]:
            raise TrialError(label + " does not match its pinned sha256")
        if ref["sha256"] not in self.paths:
            self.total += len(data)
            if len(self.paths) >= MAX_FILES or self.total > MAX_TOTAL_BYTES:
                raise TrialError("trial inputs exceed the file or byte budget")
            self.paths[ref["sha256"]] = self.write(os.path.join(self.files, ref["sha256"]), data)
        ref["path"] = self.paths[ref["sha256"]]
        return data

    def write(self, path, data):
        with open(path, "xb") as handle:
            handle.write(data)
        os.chmod(path, 0o400)
        self.known[path] = (digest(data), len(data))
        return path


def read_regular(path, limit):
    """Read up to LIMIT + 1 bytes of a regular file without following a final symlink."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("not a regular file")
        return handle.read(limit + 1)


def staged_intact(known):
    """Reread each known staged path, bounded by its staged size; any change is drift."""
    for path, (want, size) in known.items():
        try:
            data = read_regular(path, size)
        except OSError:
            return False
        if len(data) != size or digest(data) != want:
            return False
    return True


def stage(top, base, scratch, evaluator):
    staging = Staging(scratch, base)
    texts = {}
    for side in ("baseline", "candidate"):
        texts[side] = staging.put(top[side]["instructions"], MAX_INSTRUCTION_BYTES, side + " instructions")
        for run in top[side]["runs"]:
            staging.put(run["task"], MAX_ARTIFACT_BYTES, "task " + run["task_id"])
            staging.put(run["evidence"], MAX_ARTIFACT_BYTES, "evidence for " + run["task_id"])
    replay = top.get("replay")
    if replay:
        for key in ("task", "evidence", "runbook"):
            staging.put(replay[key], MAX_ARTIFACT_BYTES, "replay " + key)
    manifest = json.dumps(top, sort_keys=True, indent=2).encode("utf-8")
    return (texts, staging.write(os.path.join(scratch, "manifest.json"), manifest),
            staging.write(os.path.join(scratch, "evaluator.py"), evaluator), digest(manifest),
            staging.known)


def _rows(rows, ids, label, extra=()):
    if not isinstance(rows, list) or len(rows) > MAX_RUNS:
        raise TrialError(label + " must be a bounded list of rows")
    seen, flags = {}, ("claimed_complete", "verified") + tuple(extra)
    for row in rows:
        _obj(row, ("task_id",) + flags, label + " row")
        if row["task_id"] not in ids or row["task_id"] in seen:
            raise TrialError("{0} has an unknown or repeated task_id".format(label))
        if any(type(row[flag]) is not bool for flag in flags):
            raise TrialError(label + " values must be exact booleans")
        seen[row["task_id"]] = row
    if set(seen) != set(ids):
        raise TrialError(label + " does not score exactly the manifest tasks")
    return list(seen.values())


def parse_scores(output, top):
    obj = _obj(output, ("baseline", "candidate"), "evaluator output", ("replay",))
    if ("replay" in obj) != ("replay" in top):
        raise TrialError("evaluator output must include replay exactly when the manifest supplies one")
    scores = {}
    for side in ("baseline", "candidate"):
        rows = _rows(obj[side], [r["task_id"] for r in top[side]["runs"]], side)
        scores[side] = {
            "tasks": len(rows),
            "verified_completions": sum(r["claimed_complete"] and r["verified"] for r in rows),
            "false_completions": sum(r["claimed_complete"] and not r["verified"] for r in rows),
        }
    replay = None
    if "replay" in obj:
        replay, = _rows([obj["replay"]], [top["replay"]["task_id"]], "replay",
                        ("fresh_checkout", "runbook_executed"))
    return scores, replay


def decide(scores, replay):
    base, cand = scores["baseline"], scores["candidate"]
    reasons = []
    if cand["false_completions"] >= base["false_completions"]:
        reasons.append("candidate false completions are not strictly lower")
    if cand["verified_completions"] < base["verified_completions"]:
        reasons.append("candidate verified completions are lower")
    if replay is not None:
        bad = [k for k in ("claimed_complete", "verified", "fresh_checkout", "runbook_executed") if not replay[k]]
        if bad:
            reasons.append("replay is not a verified fresh run: " + ", ".join(bad))
    if reasons:
        return "reject", reasons
    if replay is None:
        return "replay_required", ["calibration passed; a qualifying replay is still required"]
    return "retain", []


def execute(state, evaluator, manifest, receipt, capture_dir):
    command = "{0} -I {1} {2}".format(shlex.quote(sys.executable), shlex.quote(evaluator), shlex.quote(manifest))
    try:
        return _store["execute"](state, command, "pinned evidence trial evaluator", TRIAL_TIMEOUT,
                                 context=dict(NO_STEP), extra={"trial": receipt}, capture_dir=capture_dir)
    except OSError as exc:
        raise TrialError("could not run the evaluator: " + str(exc)) from None


def evaluator_output(state, record):
    """Return (status, bytes): this run's exact, lossless, bounded stdout artifact."""
    item = (record.get("artifacts") or {}).get("stdout")
    if isinstance(item, dict) and item.get("truncated") is True:
        return "truncated", None
    if record.get("exit_code") != 0 or not record.get("verified"):
        return "nonzero_exit", None
    relative = "verification-artifacts/{0}/stdout.txt".format(record["id"])
    if not isinstance(item, dict) or record.get("artifact_error"):
        return "no_output", None
    numbers = [item.get(key) for key in ("bytes", "source_bytes")]
    flags = [item.get(key) for key in ("truncated", "redacted", "invalid_utf8")]
    hashes = [item.get(key) for key in ("sha256", "raw_sha256")]
    if (item.get("path") != relative or any(type(n) is not int for n in numbers)
            or any(type(f) is not bool for f in flags) or any(not SHA256.fullmatch(str(h)) for h in hashes)):
        return "lossy", None
    if flags[0] or numbers[1] > MAX_OUTPUT_BYTES:
        return "truncated", None
    if flags[1] or flags[2] or numbers[0] != numbers[1]:
        return "lossy", None
    path = os.path.join(os.path.realpath(state), *relative.split("/"))
    try:
        if os.path.realpath(path) != path:
            return "lossy", None
        data = read_regular(path, MAX_OUTPUT_BYTES)
    except FileNotFoundError:
        return "no_output", None
    except OSError:
        return "lossy", None
    if len(data) != numbers[0] or digest(data) != hashes[0] or hashes[0] != hashes[1]:
        return "evidence_drift", None
    return ("ok", data) if data.strip() else ("no_output", None)


def persist(state, result):
    """Write the final verdict beside the run's artifacts, or refuse; never claim without it."""
    folder = os.path.join(os.path.realpath(state), "verification-artifacts", result["verification_id"])
    try:
        if os.path.realpath(folder) != folder:
            raise TrialError("the trial receipt path leaves the state directory")
        result["receipt_path"] = "verification-artifacts/{0}/trial.json".format(result["verification_id"])
        _store["write_json"](os.path.join(folder, "trial.json"), result)
    except (OSError, ValueError) as exc:
        raise TrialError("could not persist the trial receipt: {0}".format(exc)) from None


def finish(state, result, code):
    persist(state, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


def compare(args, state):
    pin = args.evaluator_sha256.strip().lower()
    if not SHA256.fullmatch(pin):
        raise TrialError("--evaluator-sha256 must be 64 hex characters")
    manifest_path, source = read_bounded(args.manifest, MAX_MANIFEST_BYTES, "manifest")
    evaluator_path, evaluator = read_bounded(args.evaluator, MAX_EVALUATOR_BYTES, "evaluator")
    if digest(evaluator) != pin:
        raise TrialError("evaluator does not match --evaluator-sha256")
    top = validate_manifest(parse_json(source, "manifest"))
    spaces = workspaces(top)
    state_real = os.path.realpath(state)
    require_outside(spaces, {"working directory": os.getcwd(), "state directory": state_real,
                             "manifest": manifest_path, "evaluator": evaluator_path})
    artifacts = os.path.join(state_real, "verification-artifacts")
    if os.path.realpath(artifacts) != artifacts:
        raise TrialError("verification-artifacts must stay inside the state directory")
    # Scratch and capture space live under the verified controller state, never a default temp parent.
    with tempfile.TemporaryDirectory(prefix=".mythify-trial-", dir=state_real) as tmp:
        scratch = os.path.realpath(tmp)
        require_outside(spaces, {"staging directory": scratch}, nested=False)
        texts, manifest, staged_evaluator, staged_digest, known = stage(
            top, os.path.dirname(manifest_path), scratch, evaluator)
        edit = edit_summary(texts["baseline"], texts["candidate"], top)
        replay = top.get("replay")
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "binding": {"baseline_revision": top["baseline_revision"], "runner": top["runner"],
                        "source_manifest_sha256": digest(source), "staged_manifest_sha256": staged_digest},
            "pins": {"evaluator_sha256": pin,
                     "baseline_instructions_sha256": top["baseline"]["instructions"]["sha256"],
                     "candidate_instructions_sha256": top["candidate"]["instructions"]["sha256"],
                     "replay_task_sha256": replay["task"]["sha256"] if replay else None,
                     "replay_evidence_sha256": replay["evidence"]["sha256"] if replay else None,
                     "replay_runbook_sha256": replay["runbook"]["sha256"] if replay else None},
            "policy": {"forbidden_terms": top.get("forbidden_terms", []),
                       "max_edit_lines": MAX_EDIT_LINES, "max_edit_bytes": MAX_EDIT_BYTES},
            "parent": {"revision": top["candidate"]["parent_revision"],
                       "rationale": top["candidate"]["rationale"],
                       "predicted_gain": top["candidate"].get("predicted_gain")},
            "edit": edit,
        }
        record = execute(state, staged_evaluator, manifest, receipt, state_real)
        drifted = not staged_intact(known)
    result = dict(receipt, verification_id=record["id"])
    status, data = ("evidence_drift", None) if drifted else evaluator_output(state, record)
    if data is not None:
        try:
            scores, replay_row = parse_scores(parse_json(data, "evaluator output"), top)
        except TrialError as exc:
            status, result["detail"] = "malformed", str(exc)
    if status != "ok":
        result.update(decision="evaluator_failed", status=status)
        return finish(state, result, 1 if status in ("malformed", "evidence_drift", "lossy") else 2)
    decision, reasons = decide(scores, replay_row)
    result.update(decision=decision, reasons=reasons, scores=scores, replay=replay_row)
    return finish(state, result, 2 if decision == "reject" else 0)


def cmd_trial_compare(args, _state):
    if run_disabled(os.environ):
        sys.stderr.write(DISABLED_MESSAGE + "\n")
        return 2
    state = _store["resolve_state"](create=False)
    if (state is None or not os.path.isfile(os.path.join(state, "memory.json"))
            or not os.path.isdir(os.path.join(state, "verification-artifacts"))):
        sys.stderr.write("[FAIL] No initialized .mythify workspace found. Run: mythify init\n")
        return 1
    try:
        return compare(args, state)
    except TrialError as exc:
        sys.stderr.write("[FAIL] {0}\n".format(exc))
        return 1


def add_trial_parser(sub):
    family = sub.add_parser("trial", help="Compare a pinned instruction edit with a pinned evaluator.")
    actions = family.add_subparsers(dest="trial_command", metavar="ACTION", required=True)
    p = actions.add_parser(
        "compare", help="Run a pinned evaluator over baseline, candidate, and replay evidence.",
        description=(
            "Validate MANIFEST, stage its pinned inputs, run the pinned evaluator as "
            "python -I through a recorded verification, and print a JSON decision. "
            "Decisions: replay_required (calibration passed, no replay), retain "
            "(qualifying replay), reject (thresholds failed), evaluator_failed. "
            "Exit 0 for replay_required or retain; 2 for reject, an unsuccessful "
            "evaluator run, or MYTHIFY_DISABLE_RUN; 1 for invalid inputs or malformed "
            "evaluator output. Run it from a controller directory outside every "
            "subject workspace. See docs/trials.md."
        ),
    )
    p.add_argument("manifest", help="Trial manifest JSON.")
    p.add_argument("--evaluator", required=True, help="Pinned standalone Python evaluator.")
    p.add_argument("--evaluator-sha256", required=True, help="SHA-256 the evaluator file must have.")
    p.set_defaults(handler=cmd_trial_compare, needs_state=False)
