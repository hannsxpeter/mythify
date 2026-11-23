#!/usr/bin/env python3
"""Run `mythify trial compare` on a small deterministic fixture.

No model or agent is launched. The raw evidence below is hand-written
illustration data: a baseline with one false completion, a candidate that
fixes it, and a separate held-out replay. The evaluator is pinned by a digest
recorded here ahead of time, and it checks each raw output against the task's
expected contents itself. Run from the repository root:

    python3 docs/examples/trial_demo.py
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CLI = Path(__file__).resolve().parents[2] / "scripts" / "mythify.py"
EVALUATOR_SHA256 = "b266778ec6bfe9a62a086d7f21b9fcc4a7cb0f6c61e830f874fafb6e67b252a6"  # recorded before any run
REVISION = "0123456789abcdef0123456789abcdef01234567"
EVALUATOR = '''import json, sys
manifest = json.load(open(sys.argv[1]))


def load(ref):
    with open(ref["path"]) as handle:
        return json.load(handle)


def score(run):
    raw = load(run["evidence"])
    done = raw["output"] == load(run["task"])["expected"] and raw["check_exit"] == 0
    return {"task_id": run["task_id"], "claimed_complete": raw["claimed"], "verified": done}, raw


out = {side: [score(r)[0] for r in manifest[side]["runs"]] for side in ("baseline", "candidate")}
if "replay" in manifest:
    row, raw = score(manifest["replay"])
    row.update(fresh_checkout=raw["fresh_checkout"], runbook_executed=raw["runbook_ran"])
    out["replay"] = row
json.dump(out, sys.stdout)
'''


def main():
    digest = hashlib.sha256(EVALUATOR.encode()).hexdigest()
    if digest != EVALUATOR_SHA256:
        sys.exit("evaluator sha256 is {0}, not the recorded {1}".format(digest, EVALUATOR_SHA256))
    with tempfile.TemporaryDirectory(prefix="mythify-trial-demo-") as tmp:
        root = Path(tmp).resolve()
        controller, art = root / "controller", root / "art"
        controller.mkdir()
        art.mkdir()
        env = {k: v for k, v in os.environ.items() if k != "MYTHIFY_DIR"}

        def pin(name, content):
            text = content if isinstance(content, str) else json.dumps(content)
            (art / name).write_text(text)
            return {"path": name, "sha256": hashlib.sha256(text.encode()).hexdigest()}

        def run(task_id, name, expected, output, check_exit, workspace, **extra):
            raw = dict(run=name, claimed=True, output=output, check_exit=check_exit, **extra)
            return {"task_id": task_id, "task": pin(task_id + ".json", {"expected": expected}),
                    "evidence": pin(name + ".json", raw), "workspace": str(root / "subjects" / workspace)}

        manifest = {
            "schema_version": 1, "baseline_revision": REVISION,
            "runner": {"model": "demo-model", "environment": "demo-environment"},
            "baseline": {"instructions": pin("base.md", "Run the checks.\nReport the result.\n"),
                         "runs": [run("t1", "base-t1", "42", "41", 1, "base"),
                                  run("t2", "base-t2", "7", "7", 0, "base")]},
            "candidate": {"instructions": pin("cand.md", "Run the checks.\nReport the result and its exit code.\n"),
                          "parent_revision": REVISION, "rationale": "Ask for the exit code.",
                          "runs": [run("t1", "cand-t1", "42", "42", 0, "cand"),
                                   run("t2", "cand-t2", "7", "7", 0, "cand")]},
            "replay": dict(run("r1", "replay-r1", "9", "9", 0, "replay", fresh_checkout=True, runbook_ran=True),
                           runbook=pin("runbook.md", "Check out fresh, run the listed steps.")),
        }
        pin("manifest.json", manifest)
        (art / "evaluator.py").write_text(EVALUATOR)
        subprocess.run([sys.executable, str(CLI), "init"], cwd=controller, env=env, check=True,
                       stdout=subprocess.DEVNULL)
        result = subprocess.run(
            [sys.executable, str(CLI), "trial", "compare", str(art / "manifest.json"),
             "--evaluator", str(art / "evaluator.py"), "--evaluator-sha256", EVALUATOR_SHA256],
            cwd=controller, env=env)
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
