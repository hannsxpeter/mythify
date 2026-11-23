"""Pinned evidence trials run a real pinned evaluator through the recorded-run path."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CLI = Path(__file__).resolve().parents[1] / "scripts" / "mythify.py"
DEMO = Path(__file__).resolve().parents[1] / "docs" / "examples" / "trial_demo.py"
sys.path.insert(0, str(CLI.parent))
import mythify_trials  # noqa: E402
SHA = "a" * 40

HEADER = (
    "import json, os, sys\n"
    "assert sys.flags.isolated == 1\n"
    "manifest = json.load(open(sys.argv[1]))\n"
)
BODY = '''
def row(run):
    path = run["evidence"]["path"]
    assert os.path.isabs(path) and os.path.basename(os.path.dirname(path)) == "files"
    with open(path) as handle:
        evidence = json.load(handle)
    return {"task_id": run["task_id"], "claimed_complete": evidence["claimed"],
            "verified": evidence["verified"]}, evidence
assert manifest["baseline"]["runs"][0]["task"]["path"] == manifest["candidate"]["runs"][0]["task"]["path"]
out = {"baseline": [row(r)[0] for r in manifest["baseline"]["runs"]],
       "candidate": [row(r)[0] for r in manifest["candidate"]["runs"]]}
if "replay" in manifest:
    entry, evidence = row(manifest["replay"])
    entry.update(fresh_checkout=evidence["fresh"], runbook_executed=evidence["runbook"])
    out["replay"] = entry
json.dump(out, sys.stdout)
'''
EVALUATOR = HEADER + BODY
TAMPER = (
    'target = manifest["baseline"]["runs"][0]["evidence"]["path"]\n'
    "os.chmod(target, 0o600)\n"
    'open(target, "a").write(" ")\n'
)
BASE_TEXT = "line a\nline b\nline c\nline d\nline e\n"
CAND_TEXT = "line a\nline b\nline c\nline d\nline e with exit codes\n"
BASE_ROWS = [(True, False), (True, True)]
CAND_ROWS = [(True, True), (True, True)]
GOOD_REPLAY = (True, True, True, True)


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def printing(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return "import sys\nsys.stdout.write({0!r})\n".format(text)


class TrialTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.ctl, self.art, self.ws = (self.root / name for name in ("ctl", "art", "ws"))
        for path in (self.ctl, self.art):
            path.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(
            ("MYTHIFY_DIR", "MYTHIFY_DISABLE_RUN", "MYTHIFY_VERIFY"))}
        self.env["HOME"] = str(self.root)
        self.run_cli("init", check=True)
        self.counter = 0

    def run_cli(self, *args, env=None, check=False):
        result = subprocess.run([sys.executable, str(CLI), *args], cwd=self.ctl,
                                env=env or self.env, capture_output=True, text=True, timeout=60)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def file(self, name, text):
        (self.art / name).write_bytes(text.encode())
        return {"path": name, "sha256": sha(text)}

    def evidence(self, name, claimed, verified, fresh=True, runbook=True):
        return self.file(name + ".json", json.dumps({
            "claimed": claimed, "verified": verified, "fresh": fresh, "runbook": runbook, "run": name}))

    def runs(self, side, rows):
        return [{"task_id": "t%d" % i, "task": self.file("task%d.md" % i, "spec %d" % i),
                 "evidence": self.evidence("%s%d" % (side, i), *row),
                 "workspace": str(self.ws / side)} for i, row in enumerate(rows)]

    def manifest(self, baseline=BASE_ROWS, candidate=CAND_ROWS, replay=GOOD_REPLAY,
                 texts=(BASE_TEXT, CAND_TEXT), mutate=None):
        data = {
            "schema_version": 1, "baseline_revision": SHA,
            "runner": {"model": "model-x", "environment": "env-x"},
            "baseline": {"instructions": self.file("base.md", texts[0]),
                         "runs": self.runs("base", baseline)},
            "candidate": {"instructions": self.file("cand.md", texts[1]), "parent_revision": SHA,
                          "rationale": "Ask for exit codes.", "predicted_gain": "fewer false claims",
                          "runs": self.runs("cand", candidate)},
        }
        if replay is not None:
            data["replay"] = {
                "task_id": "r1", "task": self.file("replay.md", "replay spec"),
                "evidence": self.evidence("replay", *replay), "workspace": str(self.ws / "replay"),
                "runbook": self.file("runbook.md", "Check out fresh, run the listed steps."),
            }
        if mutate:
            mutate(data)
        return data

    def cli_args(self, data=None, evaluator=EVALUATOR, pin=None, raw=None):
        data = self.manifest() if data is None else data
        path = self.art / "manifest.json"
        path.write_text(raw if raw is not None else json.dumps(data))
        evaluator_path = self.art / "evaluator.py"
        evaluator_path.write_text(evaluator)
        return ["trial", "compare", str(path), "--evaluator", str(evaluator_path),
                "--evaluator-sha256", pin or sha(evaluator)]

    def compare(self, data=None, evaluator=EVALUATOR, pin=None, env=None, raw=None):
        return self.run_cli(*self.cli_args(data, evaluator, pin, raw), env=env)

    def run_patched(self, prelude, args, env=None):
        """Run the real CLI entry point in a child after PRELUDE (patches), bounded."""
        code = "import sys\nsys.path.insert(0, {0!r})\n{1}\nimport mythify\nsys.exit(mythify.main(sys.argv[1:]))\n".format(
            str(CLI.parent), prelude)
        return subprocess.run([sys.executable, "-c", code, *args], cwd=self.ctl, env=env or self.env,
                              capture_output=True, text=True, timeout=60)

    def ledger(self):
        path = self.ctl / ".mythify" / "verifications.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def receipts(self):
        return sorted((self.ctl / ".mythify").rglob("trial.json"))

    def decided(self, result, code, decision):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["decision"], decision, out)
        relative = "verification-artifacts/{0}/trial.json".format(out["verification_id"])
        self.assertEqual(out["receipt_path"], relative)
        self.assertEqual(json.loads((self.ctl / ".mythify" / relative).read_text()), out)
        return out

    def refused(self, result, text=""):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(text, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual((self.ledger(), self.receipts()), ([], []))

    def test_qualifying_replay_retains_and_binds_the_local_record(self):
        result = self.compare()
        out = self.decided(result, 0, "retain")
        self.assertEqual(out["scores"], {
            "baseline": {"tasks": 2, "verified_completions": 1, "false_completions": 1},
            "candidate": {"tasks": 2, "verified_completions": 2, "false_completions": 0}})
        self.assertRegex(out["verification_id"], r"^v-\d{14}-[0-9a-f]{12}$")
        self.assertEqual(out["parent"]["revision"], SHA)
        self.assertEqual(out["binding"]["source_manifest_sha256"],
                         sha((self.art / "manifest.json").read_bytes()))
        self.assertEqual(out["pins"]["evaluator_sha256"], sha(EVALUATOR))
        self.assertEqual(out["pins"]["replay_runbook_sha256"], sha("Check out fresh, run the listed steps."))
        record, = self.ledger()
        self.assertEqual((record["id"], record["kind"], record["verified"], record["plan"]),
                         (out["verification_id"], "executed", True, None))
        self.assertIn(" -I ", record["command"])
        self.assertEqual(out["pins"]["replay_task_sha256"], sha("replay spec"))
        self.assertEqual(out["pins"]["replay_evidence_sha256"],
                         sha(json.dumps({"claimed": True, "verified": True, "fresh": True,
                                         "runbook": True, "run": "replay"})))
        self.assertEqual(out["policy"], {"forbidden_terms": [], "max_edit_lines": 6, "max_edit_bytes": 1024})
        self.assertEqual(record["trial"]["pins"], out["pins"])
        self.assertEqual(record["trial"]["policy"], out["policy"])
        self.assertEqual(record["trial"]["binding"], out["binding"])

    def test_valid_calibration_without_replay_requires_replay(self):
        out = self.decided(self.compare(self.manifest(replay=None)), 0, "replay_required")
        self.assertIsNone(out["pins"]["replay_runbook_sha256"])
        self.assertEqual(len(self.ledger()), 1)

    def test_thresholds_reject_ties_and_coverage_drops(self):
        cases = {
            "tie": ([(True, False), (True, True)], [(True, False), (True, True)]),
            "more false": ([(True, True), (True, False)], [(True, False), (True, False)]),
            "fewer verified": ([(True, True), (True, False)], [(False, True), (False, False)]),
        }
        for name, (baseline, candidate) in cases.items():
            with self.subTest(name):
                out = self.decided(self.compare(self.manifest(baseline, candidate)), 2, "reject")
                self.assertTrue(out["reasons"])

    def test_supplied_replay_must_be_complete_fresh_and_executed(self):
        for replay in ((False, True, True, True), (True, False, True, True),
                       (True, True, False, True), (True, True, True, False)):
            with self.subTest(replay=replay):
                out = self.decided(self.compare(self.manifest(replay=replay)), 2, "reject")
                self.assertTrue(any("replay" in reason for reason in out["reasons"]))

    def test_wrong_pins_refuse_before_any_run(self):
        self.refused(self.compare(pin="0" * 64), "evaluator")
        self.refused(self.compare(pin="xyz"), "evaluator-sha256")
        for name, mutate in {
            "evidence": lambda d: d["candidate"]["runs"][0]["evidence"].update(sha256="1" * 64),
            "instructions": lambda d: d["candidate"]["instructions"].update(sha256="1" * 64),
            "runbook": lambda d: d["replay"]["runbook"].update(sha256="1" * 64),
            "task": lambda d: [d[side]["runs"][1]["task"].update(sha256="1" * 64)
                               for side in ("baseline", "candidate")],
        }.items():
            with self.subTest(name):
                self.refused(self.compare(self.manifest(mutate=mutate)), "pinned sha256")

    def test_evaluator_drift_after_pinning_is_refused(self):
        data = self.manifest()
        pin = sha(EVALUATOR)
        self.refused(self.compare(data, evaluator=EVALUATOR + "# changed\n", pin=pin), "evaluator")

    def test_edit_budget_and_shortcut_screen(self):
        five = "".join("line %s\n" % c for c in "abcde")
        cases = {
            "no change": (five, five),
            "whitespace only": ("line a", "line a\n"),
            "too many lines": (five, "".join("new %s\n" % c for c in "abcd") + "line e\n"),
            "too many bytes": (five, five.replace("line e", "x" * 1100)),
            "hardcode": (five, five + "Hardcode the result.\n"),
            "benchmark": (five, five + "Use the Benchmark answer key.\n"),
            "grader": (five, five + "Ask the grader first.\n"),
            "skip tests": (five, five + "SKIP tests when slow.\n"),
            "task id": (five, five + "Handle T1 first.\n"),
            "line separators": (
                "".join("line %d\n" % i for i in range(201)),
                "".join("line %d " % i for i in range(201)).replace("line 5 ", "line five ")),
        }
        for name, texts in cases.items():
            with self.subTest(name):
                self.refused(self.compare(self.manifest(texts=texts)))

    def test_forbidden_terms_are_screened_case_insensitively(self):
        data = self.manifest(texts=(BASE_TEXT, BASE_TEXT + "Mention SecretWord here.\n"),
                             mutate=lambda d: d.update(forbidden_terms=["secretword"]))
        self.refused(self.compare(data), "secretword")

    def test_invalid_manifests_are_refused_before_execution(self):
        def swap(side, key, value):
            return lambda d: d[side].update({key: value})
        cases = {
            "short revision": lambda d: d.update(baseline_revision="abc123"),
            "upper revision": lambda d: d.update(baseline_revision="A" * 40),
            "parent mismatch": lambda d: d["candidate"].update(parent_revision="b" * 40),
            "schema version": lambda d: d.update(schema_version=2),
            "bool schema version": lambda d: d.update(schema_version=True),
            "unknown key": lambda d: d.update(extra=1),
            "empty model": lambda d: d["runner"].update(model=" "),
            "empty rationale": lambda d: d["candidate"].update(rationale=""),
            "empty gain": lambda d: d["candidate"].update(predicted_gain=""),
            "empty task id": lambda d: d["baseline"]["runs"][0].update(task_id=""),
            "duplicate task ids": lambda d: d["baseline"]["runs"][1].update(task_id="t0"),
            "missing task": lambda d: d["candidate"]["runs"].pop(),
            "extra task": lambda d: d["candidate"]["runs"].append(
                dict(d["candidate"]["runs"][0], task_id="t9")),
            "task spec differs": lambda d: d["candidate"]["runs"][0].update(
                task=dict(d["baseline"]["runs"][1]["task"])),
            "shared evidence": lambda d: d["candidate"]["runs"][0].update(
                evidence=d["baseline"]["runs"][0]["evidence"]),
            "relative workspace": lambda d: d["baseline"]["runs"][0].update(workspace="ws"),
            "no runs": swap("baseline", "runs", []),
            "replay reuses task id": lambda d: d["replay"].update(task_id="t0"),
            "replay reuses task spec": lambda d: d["replay"].update(
                task=d["baseline"]["runs"][0]["task"]),
            "replay reuses evidence": lambda d: d["replay"].update(
                evidence=d["candidate"]["runs"][0]["evidence"]),
            "replay overlaps workspace": lambda d: d["replay"].update(
                workspace=str(self.ws / "base" / "inner")),
            "replay without runbook": lambda d: d["replay"].pop("runbook"),
            "null replay": lambda d: d.update(replay=None),
            "bad forbidden terms": lambda d: d.update(forbidden_terms=[""]),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.refused(self.compare(self.manifest(mutate=mutate)))

    def test_json_ambiguity_is_refused(self):
        text = json.dumps(self.manifest())
        self.refused(self.compare(raw=text.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')),
                     "duplicate")
        self.refused(self.compare(raw=text[:-1] + ', "x": NaN}'), "non-finite")
        self.refused(self.compare(raw="[]"))
        self.refused(self.compare(raw="x" * 300000), "exceeds")

    def test_controller_paths_stay_outside_workspaces(self):
        link = self.root / "alias"
        link.symlink_to(self.ctl)
        for name, workspace in {"cwd inside": str(self.ctl), "symlink alias": str(link),
                                "parent of cwd": str(self.root)}.items():
            with self.subTest(name):
                data = self.manifest(replay=None, mutate=lambda d: d["baseline"]["runs"][0].update(
                    workspace=workspace))
                self.refused(self.compare(data), "outside")
        inside = self.manifest(mutate=lambda d: d["replay"].update(workspace=str(self.art)))
        self.refused(self.compare(inside), "outside")

    def test_workspaces_may_coincide_or_not_exist(self):
        shared = str(self.ws / "shared")
        def same(d):
            for side in ("baseline", "candidate"):
                for run in d[side]["runs"]:
                    run["workspace"] = shared
        self.decided(self.compare(self.manifest(mutate=same)), 0, "retain")
        self.assertFalse(Path(shared).exists())

    def test_unsuccessful_evaluator_runs_are_recorded_with_a_status(self):
        cases = {"nonzero_exit": "import sys\nsys.exit(3)\n", "no_output": "pass\n",
                 "truncated": "import sys\nsys.stdout.write(' ' * 300000 + '{}')\n"}
        for status, source in cases.items():
            with self.subTest(status):
                before = len(self.ledger())
                out = self.decided(self.compare(evaluator=source), 2, "evaluator_failed")
                self.assertEqual(out["status"], status)
                self.assertEqual(self.ledger()[before]["id"], out["verification_id"])
        self.assertFalse(self.ledger()[0]["verified"])

    def test_malformed_evaluator_output_is_refused(self):
        rows = lambda side: [{"task_id": "t%d" % i, "claimed_complete": True, "verified": True}
                             for i in range(2)]
        good = {"baseline": rows("b"), "candidate": rows("c")}
        replay = {"task_id": "r1", "claimed_complete": True, "verified": True,
                  "fresh_checkout": True, "runbook_executed": True}
        def edit(fn, **extra):
            out = json.loads(json.dumps(dict(good, **extra)))
            fn(out)
            return out
        cases = {
            "not json": "not json",
            "duplicate keys": '{"baseline": [], "baseline": []}',
            "array": [],
            "missing row": edit(lambda o: o["baseline"].pop()),
            "extra row": edit(lambda o: o["candidate"].append(
                {"task_id": "t9", "claimed_complete": True, "verified": True})),
            "duplicate row": edit(lambda o: o["candidate"].__setitem__(1, dict(o["candidate"][0]))),
            "string bool": edit(lambda o: o["baseline"][0].update(verified="true")),
            "int bool": edit(lambda o: o["baseline"][0].update(claimed_complete=1)),
            "extra key": edit(lambda o: o["baseline"][0].update(score=1)),
            "missing candidate": {"baseline": good["baseline"]},
        }
        for name, payload in cases.items():
            with self.subTest(name):
                out = self.decided(self.compare(evaluator=printing(payload)), 1, "evaluator_failed")
                self.assertEqual(out["status"], "malformed")
        out = self.decided(self.compare(self.manifest(replay=None), evaluator=printing(dict(good, replay=replay))),
                           1, "evaluator_failed")
        self.assertEqual(out["status"], "malformed")
        data = self.manifest()
        with_replay = dict(good, replay=dict(replay, task_id="other"))
        out = self.decided(self.compare(data, evaluator=printing(with_replay)), 1, "evaluator_failed")
        self.assertEqual(out["status"], "malformed")
        out = self.decided(self.compare(data, evaluator=printing(good)), 1, "evaluator_failed")
        self.assertEqual(out["status"], "malformed")

    def test_staged_input_drift_during_the_run_is_refused(self):
        out = self.decided(self.compare(evaluator=HEADER + TAMPER + BODY), 1, "evaluator_failed")
        self.assertEqual(out["status"], "evidence_drift")
        self.assertEqual(len(self.ledger()), 1)

    def test_disabled_execution_runs_and_records_nothing(self):
        env = dict(self.env, MYTHIFY_DISABLE_RUN="1")
        result = self.compare(env=env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("MYTHIFY_DISABLE_RUN", result.stderr)
        self.assertEqual((result.stdout, self.ledger(), self.receipts()), ("", [], []))

    def test_state_is_resolved_without_creating_it(self):
        missing = self.root / "nostate"
        for name, extra in {"disabled": {"MYTHIFY_DISABLE_RUN": "1"}, "enabled": {}}.items():
            with self.subTest(name):
                result = self.compare(env=dict(self.env, MYTHIFY_DIR=str(missing), **extra))
                self.assertEqual(result.returncode, 2 if extra else 1, result.stdout + result.stderr)
                self.assertFalse(missing.exists())

    def test_artifact_store_must_stay_inside_state(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        store = self.ctl / ".mythify" / "verification-artifacts"
        store.rmdir()
        store.symlink_to(elsewhere)
        self.refused(self.compare(), "verification-artifacts")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_alias_names_for_a_workspace_are_refused_by_identity(self):
        same = lambda a, b: a.lower() == b.lower()
        with mock.patch("os.path.exists", return_value=True), mock.patch("os.path.samefile", same):
            for path in ("/x/Docs", "/x/Docs/sub"):
                with self.assertRaises(mythify_trials.TrialError):
                    mythify_trials.require_outside(["/x/docs"], {"state": path})
            self.assertTrue(mythify_trials.inside("/x/DOCS/a/b", "/x/docs"))
            mythify_trials.require_outside(["/x/docs"], {"state": "/x/other"})
        self.assertFalse(mythify_trials.inside("/gone/a", "/gone/b"))

    def test_invalid_utf8_stdout_is_not_a_lossless_capture(self):
        lossy = HEADER + BODY.replace("json.dump(out, sys.stdout)",
                                      "sys.stdout.buffer.write(json.dumps(out).encode() + b'\\xff')")
        out = self.decided(self.compare(evaluator=lossy), 1, "evaluator_failed")
        self.assertEqual(out["status"], "lossy")
        wide = HEADER + BODY.replace(
            "json.dump(out, sys.stdout)",
            "sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode('utf-8'))")
        def rename(d):
            for side in ("baseline", "candidate"):
                d[side]["runs"][0]["task_id"] = "té"
        self.decided(self.compare(self.manifest(mutate=rename), evaluator=wide), 0, "retain")

    def test_staged_input_changes_are_found_with_bounded_rereads(self):
        target = 'target = manifest["baseline"]["runs"][0]["evidence"]["path"]\n'
        other = 'manifest["candidate"]["runs"][0]["evidence"]["path"]'
        changes = {
            "removed": "os.remove(target)\n",
            "fifo": "os.remove(target)\nos.mkfifo(target)\n",
            "symlink": "os.remove(target)\nos.symlink(%s, target)\n" % other,
            "huge": "os.chmod(target, 0o600)\nopen(target, 'a').write('x' * 5000000)\n",
            "evaluator": 'os.chmod(sys.argv[0], 0o600)\nopen(sys.argv[0], "a").write("#")\n',
        }
        for name, change in changes.items():
            with self.subTest(name):
                out = self.decided(self.compare(evaluator=HEADER + target + change + "print('{}')\n"),
                                   1, "evaluator_failed")
                self.assertEqual(out["status"], "evidence_drift")

    def test_unpersistable_receipt_is_never_claimed(self):
        prelude = (
            "import mythify_trials\n"
            "def broken(path, data):\n"
            "    raise OSError('disk full')\n"
            "mythify_trials._store['write_json'] = broken\n")
        result = self.run_patched("import mythify\n" + prelude, self.cli_args())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("persist", result.stderr)
        self.assertEqual((result.stdout, self.receipts()), ("", []))
        self.assertEqual(len(self.ledger()), 1)

    def test_runner_clipped_stdout_is_truncated_but_stderr_clipping_is_not(self):
        env = dict(self.env, MYTHIFY_VERIFY_MAX_OUTPUT_BYTES="64")
        out = self.decided(self.compare(evaluator="print('x' * 300)\n", env=env), 2, "evaluator_failed")
        self.assertEqual(out["status"], "truncated")
        noisy = "import sys\nsys.stderr.write('x' * 300)\n"
        out = self.decided(self.compare(evaluator=noisy, env=env), 2, "evaluator_failed")
        self.assertEqual(out["status"], "nonzero_exit")

    def test_trial_never_probes_or_allocates_in_a_subject_temp_dir(self):
        subject = self.ws / "base"
        subject.mkdir(parents=True)
        env = dict(self.env, TMPDIR=str(subject))
        prelude = (
            "import tempfile\n"
            "def refuse(*args, **kwargs):\n"
            "    raise AssertionError('default temporary parent used')\n"
            "tempfile.gettempdir = tempfile._get_default_tempdir = refuse\n")
        result = self.run_patched(prelude, self.cli_args(), env=env)
        self.assertEqual(self.decided(result, 0, "retain")["decision"], "retain")
        self.assertEqual(list(subject.iterdir()), [])
        self.assertEqual(list((self.ctl / ".mythify").glob(".mythify-trial-*")), [])

    def test_existing_but_uninitialized_state_is_refused_untouched(self):
        bare = self.root / "bare"
        bare.mkdir()
        result = self.compare(env=dict(self.env, MYTHIFY_DIR=str(bare)))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("initialized", result.stderr)
        self.assertEqual(list(bare.iterdir()), [])

    def test_runnable_example_retains_from_the_repository_root(self):
        env = {k: v for k, v in self.env.items() if k != "MYTHIFY_DIR"}
        result = subprocess.run([sys.executable, str(DEMO)], cwd=CLI.parents[1], env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual((out["decision"], out["scores"]["candidate"]["false_completions"],
                          out["scores"]["baseline"]["false_completions"]), ("retain", 0, 1))

    def test_help_documents_the_contract(self):
        result = self.run_cli("trial", "compare", "--help", check=True)
        for text in ("--evaluator", "--evaluator-sha256", "replay_required", "retain"):
            self.assertIn(text, result.stdout)


if __name__ == "__main__":
    unittest.main()
