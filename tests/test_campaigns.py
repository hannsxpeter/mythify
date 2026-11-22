"""Campaigns reuse plans and their evidence gates through the real CLI."""

import json
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "scripts" / "mythify.py"


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.state = self.project / ".mythify"
        self.env = dict(os.environ)
        self.env.pop("MYTHIFY_DIR", None)
        self.env.pop("MYTHIFY_REQUIRE_VERIFIED_STEP", None)
        self.env["HOME"] = self.tmp.name
        self.ok("init")
        self.verifier = shlex.quote(sys.executable) + " -c 'assert 2 + 2 == 4'"

    def run_cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(CLI), *args], cwd=self.project,
            env=env or self.env, capture_output=True, text=True, timeout=30,
        )

    def ok(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def plan(self, name="existing"):
        return json.loads((self.state / "plans" / (name + ".json")).read_text())

    def snapshot(self):
        return {str(p.relative_to(self.state)): p.read_bytes()
                for p in self.state.rglob("*") if p.is_file()}

    def create_plan(self):
        self.ok("plan", "create", "Existing goal", "--name", "existing", "--steps",
                json.dumps([{"title": "First", "verify_command": self.verifier},
                            {"title": "Second", "verify_command": self.verifier}]))

    def git(self, *args):
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", *args],
            cwd=self.project, env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def init_git(self):
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        XDG_CONFIG_HOME=self.tmp.name)
        self.git("init", "-q")
        for name in ("owned/first.txt", "owned/second.txt", "owned/trusted.txt", "outside.txt"):
            path = self.project / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("original\n")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Initial")

    def start_scoped(self, tasks=None):
        args = ["campaign", "start", "Scoped", "--ordered", "--allowed-paths", "owned",
                "--frozen-paths", "owned/trusted.txt"]
        if tasks:
            args += ["--tasks", json.dumps(tasks)]
        else:
            args += ["--verify", self.verifier]
        self.ok(*args)

    def refused(self, expected, *args, code=1):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertIn(expected, result.stderr)
        return result

    def cli_script(self, setup):
        source = "import sys\nsys.path.insert(0, {!r})\nimport mythify\n".format(str(CLI.parent))
        return source + setup + "\nraise SystemExit(mythify.main(sys.argv[1:]))"

    @contextmanager
    def paused_command(self, patch, *args):
        pause = (
            "def pause():\n"
            "    print('ready', flush=True)\n"
            "    if input() != 'continue':\n"
            "        raise RuntimeError('expected test release')\n"
        )
        script = self.cli_script(pause + patch)
        with subprocess.Popen(
            [sys.executable, "-c", script, *args], cwd=self.project, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as child:
            ready = queue.Queue()
            reader = threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
            reader.start()
            try:
                try:
                    self.assertEqual(ready.get(timeout=10), "ready\n")
                except queue.Empty:
                    self.fail("Timed out waiting for the test command to reach its pause")
                yield child
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=10)
                reader.join(timeout=10)
                self.assertFalse(reader.is_alive(), "Timed out waiting for the test command's stdout")

    def resume_command(self, child):
        stdout, stderr = child.communicate("continue\n", timeout=30)
        return child.returncode, stdout, stderr

    def test_ordered_campaign_refuses_later_and_skipped_steps(self):
        self.create_plan()
        self.ok("campaign", "start", "Finish", "--ordered")
        self.ok("step", "1", "in_progress")
        before = self.snapshot()
        self.refused("first unfinished step is 1", "step", "2", "in_progress")
        self.refused("first unfinished step is 1", "plan", "verify", "2")
        self.assertEqual(self.snapshot(), before)
        self.ok("step", "1", "skipped")
        self.refused("first unfinished step is 1", "plan", "verify", "2")
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "First check passed")
        self.ok("plan", "verify", "2")
        self.ok("step", "2", "completed", "Second check passed")
        self.assertEqual([s["status"] for s in self.plan()["steps"]], ["completed", "completed"])

    def test_ordered_start_refuses_an_existing_out_of_order_plan(self):
        self.create_plan()
        self.ok("step", "2", "in_progress")
        before = self.snapshot()
        self.refused("out of order", "campaign", "start", "Finish", "--ordered")
        self.assertEqual(self.snapshot(), before)

    def test_scope_requires_clean_committed_git_worktree(self):
        before = self.snapshot()
        self.refused("scope", "campaign", "start", "Scoped", "--allowed-paths", "owned")
        self.assertEqual(self.snapshot(), before)
        self.init_git()
        (self.project / "owned/first.txt").write_text("dirty before start")
        before = self.snapshot()
        self.refused("clean Git worktree", "campaign", "start", "Scoped", "--allowed-paths", "owned")
        self.assertEqual(self.snapshot(), before)

    def test_green_generic_run_cannot_complete_with_out_of_scope_changes(self):
        self.init_git()
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        (self.project / "owned/first.txt").write_text("allowed change")
        extra = self.project / "extra.txt"
        extra.write_text("outside scope")
        self.assertIn("[OK] VERIFIED", self.ok("verify", "run", self.verifier).stdout)
        before = self.snapshot()
        self.refused("extra.txt", "campaign", "advance", "--result", "Green")
        self.refused("extra.txt", "step", "1", "completed", "Green")
        self.refused("extra.txt", "plan", "verify", "1")
        self.refused("extra.txt", "step", "1", "in_progress")
        self.refused("extra.txt", "verify", "gate", code=2)
        self.assertEqual(self.snapshot(), before)
        extra.unlink()
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Allowed edit verified")
        self.assertEqual((self.project / "owned/first.txt").read_text(), "allowed change")
        self.ok("verify", "gate")

    def test_scope_checkpoint_before_open_and_across_steps_without_commits(self):
        self.init_git()
        self.start_scoped([
            {"title": "First", "verify_command": self.verifier, "allowed_paths": ["owned/first.txt"]},
            {"title": "Second", "verify_command": self.verifier, "allowed_paths": ["owned/second.txt"]},
        ])
        self.ok("verify", "gate")
        first = self.project / "owned/first.txt"
        first.write_text("early edit")
        self.refused("owned/first.txt", "verify", "gate", code=2)
        self.refused("owned/first.txt", "plan", "verify", "1")
        first.write_text("original\n")
        self.ok("step", "1", "in_progress")
        first.write_text("accepted")
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "First edit verified")
        self.ok("verify", "gate")
        self.ok("step", "2", "in_progress")
        first.write_text("changed again")
        self.refused("owned/first.txt", "plan", "verify", "2")
        first.write_text("accepted")
        (self.project / "owned/second.txt").write_text("second edit")
        self.ok("plan", "verify", "2")
        self.ok("campaign", "advance", "--result", "Second edit verified")

    def test_idle_scope_allows_staging_and_committing_accepted_content(self):
        self.init_git()
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        path = "owned/first.txt"
        (self.project / path).write_text("accepted\n")
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Edit verified")
        self.ok("verify", "gate")
        before = self.snapshot()

        self.git("add", path)
        self.assertEqual(self.git("show", ":" + path), "accepted\n")
        self.ok("verify", "gate")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Accepted")
        self.assertEqual(self.git("show", "HEAD:" + path), "accepted\n")
        self.ok("verify", "gate")
        self.assertEqual(self.snapshot(), before)

    def test_idle_scope_refuses_other_staged_modes_and_staged_or_committed_blobs(self):
        self.init_git()
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        name = "owned/first.txt"
        path = self.project / name
        path.write_text("accepted\n")
        path.chmod(0o644)
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Edit verified")

        self.git("add", "--chmod=+x", name)
        self.refused(name, "verify", "gate", code=2)
        path.write_text("different bytes\n")
        self.git("add", "--chmod=-x", name)
        path.write_text("accepted\n")
        self.refused(name, "verify", "gate", code=2)
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Different")
        self.assertEqual(self.git("show", "HEAD:" + name), "different bytes\n")
        self.git("add", name)
        self.assertEqual(self.git("show", ":" + name), "accepted\n")
        self.refused(name, "verify", "gate", code=2)

    def test_step_scope_allows_staging_and_committing_previous_step_content(self):
        self.init_git()
        first, second = "owned/first.txt", "owned/second.txt"
        self.start_scoped([
            {"title": "First", "verify_command": self.verifier, "allowed_paths": [first]},
            {"title": "Second", "verify_command": self.verifier, "allowed_paths": [second]},
        ])
        self.ok("step", "1", "in_progress")
        (self.project / first).write_text("accepted\n")
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "First edit verified")
        self.ok("step", "2", "in_progress")
        (self.project / second).write_text("next edit\n")

        self.git("add", first)
        self.ok("plan", "verify", "2")
        self.ok("verify", "gate")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "First edit")
        self.assertEqual(self.git("show", "HEAD:" + first), "accepted\n")
        self.ok("plan", "verify", "2")
        self.ok("verify", "gate")
        self.ok("campaign", "advance", "--result", "Second edit verified")

    def test_scope_allows_staging_and_committing_accepted_deletion_and_rename(self):
        self.init_git()
        first, deleted, moved, next_path = (
            "owned/first.txt", "owned/second.txt", "owned/moved.txt", "owned/next.txt",
        )
        self.start_scoped([
            {"title": "Remove and rename", "verify_command": self.verifier},
            {"title": "Next", "verify_command": self.verifier, "allowed_paths": [next_path]},
        ])
        self.ok("step", "1", "in_progress")
        (self.project / deleted).unlink()
        (self.project / first).rename(self.project / moved)
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Removal and rename verified")
        self.ok("verify", "gate")

        self.git("add", "-u", "--", deleted)
        self.ok("verify", "gate")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Remove")
        self.assertEqual(self.git("ls-tree", "--name-only", "HEAD", "--", deleted), "")
        self.ok("verify", "gate")

        self.ok("step", "2", "in_progress")
        self.git("add", "-A", "--", first, moved)
        self.ok("plan", "verify", "2")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Rename")
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-status", "-M", "-r", "HEAD"),
                         "R100\t" + first + "\t" + moved + "\n")
        outside = self.project / "outside.txt"
        outside.unlink()
        self.refused("outside.txt", "plan", "verify", "2")
        outside.write_text("original\n")
        self.ok("plan", "verify", "2")
        self.ok("verify", "gate")
        self.ok("campaign", "advance", "--result", "Next step verified")

    def test_scope_sees_both_sides_of_rename_and_staged_only_changes(self):
        self.init_git()
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        self.git("mv", "outside.txt", "owned/moved.txt")
        self.refused("outside.txt", "verify", "gate", code=2)
        self.git("mv", "owned/moved.txt", "outside.txt")
        outside = self.project / "outside.txt"
        outside.write_text("index only")
        self.git("add", "outside.txt")
        outside.write_text("original\n")
        self.refused("outside.txt", "plan", "verify", "1")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Outside")
        self.git("add", "outside.txt")
        self.refused("outside.txt", "plan", "verify", "1")

    def test_scope_rechecks_after_a_passing_verifier_touches_frozen_input(self):
        self.init_git()
        command = shlex.quote(sys.executable) + " -c 'open(\"owned/trusted.txt\", \"w\").write(\"changed\")'"
        self.ok("verify", "check", "heldout", "--set", command)
        self.start_scoped([{"title": "Verify", "verify_command": "@heldout"}])
        self.refused("owned/trusted.txt", "plan", "verify", "1")
        self.assertEqual((self.project / "owned/trusted.txt").read_text(), "changed")
        self.refused("owned/trusted.txt", "campaign", "advance", "--result", "Verifier exited zero")
        self.refused("owned/trusted.txt", "verify", "gate", code=2)
        (self.project / "owned/trusted.txt").write_text("original\n")
        self.refused("Verified evidence required", "campaign", "advance", "--result", "Verifier exited zero")

    def test_frozen_directory_covers_visible_untracked_files_and_excludes_ignored_files(self):
        self.init_git()
        with (self.project / ".gitignore").open("a") as ignores:
            ignores.write("\nowned/ignored.tmp\n")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Ignore fixture")
        self.ok("campaign", "start", "Hold directory frozen", "--frozen-paths", "owned",
                "--verify", self.verifier)
        self.ok("step", "1", "in_progress")
        (self.project / "owned/ignored.tmp").write_text("ignored")
        visible = self.project / "owned/visible.txt"
        visible.write_text("visible")
        self.refused("Frozen campaign paths changed: 'owned/visible.txt'", "plan", "verify", "1")
        visible.unlink()
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Frozen sources verified")

    def test_scoped_verifiers_refuse_allowed_source_changed_during_execution(self):
        self.init_git()
        path = self.project / "owned/first.txt"
        code = ("from pathlib import Path; p = Path('owned/first.txt'); "
                "assert p.read_text() == 'accepted'; p.write_text('rewritten')")
        verifier = shlex.quote(sys.executable) + " -c " + shlex.quote(code)
        self.ok("verify", "check", "rewrite", "--set", verifier)
        self.start_scoped([{"title": "Verify", "verify_command": verifier}])
        self.ok("step", "1", "in_progress")
        for args, exit_status in (
            (("plan", "verify", "1"), 1),
            (("verify", "run", verifier), 2),
            (("verify", "check", "rewrite"), 2),
        ):
            with self.subTest(command=args[:2]):
                path.write_text("accepted")
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, exit_status, result.stdout + result.stderr)
                self.assertIn("Campaign source changed during verification", result.stdout + result.stderr)
                self.assertEqual(path.read_text(), "rewritten")
                record = json.loads((self.state / "verifications.jsonl").read_text().splitlines()[-1])
                self.assertFalse(record["verified"])
                self.assertEqual(record["exit_code"], 0)
                self.assertIn("Campaign source changed during verification", record["campaign_refusal"])
                self.refused("Verified evidence required", "campaign", "advance", "--result", "Rewrote source")

    def test_scoped_verification_records_and_reuses_stable_source(self):
        self.init_git()
        code = "from pathlib import Path; assert Path('owned/first.txt').read_text() == 'accepted'"
        verifier = shlex.quote(sys.executable) + " -c " + shlex.quote(code)
        self.start_scoped([{"title": "Verify stable source", "verify_command": verifier}])
        self.ok("step", "1", "in_progress")
        (self.project / "owned/first.txt").write_text("accepted")
        self.ok("plan", "verify", "1")
        self.assertIn("Reused passing run", self.ok("plan", "verify", "1", "--reuse").stdout)
        original, reused = [json.loads(line) for line in (self.state / "verifications.jsonl").read_text().splitlines()]
        self.assertTrue(original["verified"] and reused["verified"])
        self.assertEqual(reused["reused_from"], original["id"])
        self.assertEqual(reused["provenance"], original["provenance"])
        self.ok("campaign", "advance", "--result", "Stable source verified")

    def test_scope_implies_order_and_refuses_replacing_policy_on_resume(self):
        self.init_git()
        self.ok("campaign", "start", "Scoped", "--allowed-paths", "owned", "--tasks",
                json.dumps([{"title": "First"}, {"title": "Second"}]))
        before = self.snapshot()
        self.refused("first unfinished step is 1", "step", "2", "in_progress")
        self.refused("different order or scope", "campaign", "start", "Scoped",
                     "--allowed-paths", "outside.txt")
        self.ok("campaign", "start", "Scoped", "--allowed-paths", "./owned/")
        self.assertEqual(self.snapshot(), before)

    def test_scope_refuses_an_unreadable_checkpoint(self):
        self.init_git()
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        git_dir, moved = self.project / ".git", Path(self.tmp.name) / "git-backup"
        git_dir.rename(moved)
        try:
            self.refused("Cannot inspect campaign scope", "verify", "gate")
            self.refused("Cannot inspect campaign scope", "plan", "verify", "1")
        finally:
            moved.rename(git_dir)
        plan = self.plan("scoped")
        plan["campaign"]["scope"]["checkpoint"]["head"] = []
        (self.state / "plans" / "scoped.json").write_text(json.dumps(plan))
        before = self.snapshot()
        for args in (("plan", "verify", "1"), ("verify", "gate"),
                     ("campaign", "advance", "--result", "Checked")):
            self.refused("scope metadata is unreadable", *args)
        self.assertEqual(self.snapshot(), before)

    def test_status_and_prompts_explain_a_blocked_campaign_policy(self):
        self.init_git()
        self.start_scoped([{"title": "First"}, {"title": "Second"}])
        plan_path = self.state / "plans" / "scoped.json"
        original = plan_path.read_text()
        for fault, reason in (("missing", "checkpoint"), ("null", "checkpoint"), ("order", "out of order")):
            with self.subTest(fault=fault):
                plan = json.loads(original)
                if fault == "missing":
                    del plan["campaign"]["scope"]["checkpoint"]
                elif fault == "null":
                    plan["campaign"]["scope"]["checkpoint"] = None
                else:
                    plan["steps"][1]["status"] = "in_progress"
                plan_path.write_text(json.dumps(plan))
                before = self.snapshot()
                campaign = json.loads(self.ok("campaign", "status", "--json").stdout)
                status = json.loads(self.ok("status", "--json").stdout)
                self.assertEqual(campaign["status"], "blocked")
                self.assertEqual(campaign["total_steps"], 2)
                self.assertIn(reason, campaign["blocked_reason"])
                self.assertEqual(status["active_plan"]["campaign"], campaign)
                self.assertEqual(status["status"], "needs_attention")
                for args in (("status",), ("campaign", "status")):
                    self.assertIn(campaign["blocked_reason"], self.ok(*args).stdout)
                for args in (("campaign", "prompt"), ("prompt", "campaign"), ("prompt", "next")):
                    prompt = self.ok(*args).stdout
                    self.assertIn(campaign["blocked_reason"], prompt)
                    self.assertNotIn("Start: mythify step", prompt)
                    self.assertNotIn("Advance: mythify campaign", prompt)
                for args in (("step", "1", "in_progress"), ("verify", "gate"),
                             ("campaign", "advance", "--result", "Checked")):
                    self.refused(reason, *args)
                self.assertEqual(self.snapshot(), before)

    def test_scope_refuses_changed_external_ignores(self):
        self.init_git()
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        exclude = self.project / ".git/info/exclude"
        original = exclude.read_bytes()
        exclude.write_bytes(original + b"\n/hidden.txt\n")
        (self.project / "hidden.txt").write_text("outside scope")
        self.refused("external ignore rules changed", "verify", "gate")
        exclude.write_bytes(original)
        self.refused("hidden.txt", "verify", "gate", code=2)

    def test_scope_stays_within_inspection_budgets(self):
        self.init_git()
        before = self.snapshot()
        for constant, limit, message in (
            ("MAX_ENTRIES", 3, "Git path limit"), ("MAX_BYTES", 5, "file byte limit"),
        ):
            with self.subTest(constant=constant):
                script = self.cli_script("import mythify_campaign_scope as scope\nscope.{} = {}"
                                         .format(constant, limit))
                result = subprocess.run(
                    [sys.executable, "-c", script, "campaign", "start", "Scoped", "--allowed-paths", "owned"],
                    cwd=self.project, env=self.env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_scoped_gate_hashes_raw_bytes_without_running_git_clean_filters(self):
        self.init_git()
        (self.project / ".gitattributes").write_text("owned/first.txt filter=scopeprobe\n")
        self.git("add", ".gitattributes")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Attributes")
        marker = Path(self.tmp.name) / "filter-ran"
        code = "import pathlib, sys; pathlib.Path({!r}).touch(); sys.stdout.buffer.write(sys.stdin.buffer.read())"
        command = shlex.quote(sys.executable) + " -c " + shlex.quote(code.format(str(marker)))
        self.git("config", "filter.scopeprobe.clean", command)
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        (self.project / "owned/first.txt").write_text("allowed raw edit")
        self.ok("plan", "verify", "1")
        self.ok("verify", "gate")
        self.ok("campaign", "advance", "--result", "Raw edit verified")
        self.assertFalse(marker.exists())

    def test_scope_tracks_symlink_targets_without_walking_them(self):
        self.init_git()
        self.start_scoped()
        self.ok("step", "1", "in_progress")
        outside = Path(self.tmp.name) / "other"
        outside.mkdir()
        (outside / "record.txt").write_text("external")
        (outside / ".git").mkdir()
        link = self.project / "owned" / "new-link"
        link.symlink_to(outside, target_is_directory=True)
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Link verified")
        link.unlink()
        link.symlink_to(outside / "record.txt")
        self.refused("owned/new-link", "verify", "gate", code=2)

    def test_concurrent_campaign_start_cannot_overwrite_scope(self):
        self.init_git()
        patch = (
            "import mythify_campaigns as campaigns\n"
            "original = campaigns.scope_snapshot\n"
            "def snapshot(*args, **kwargs):\n"
            "    result = original(*args, **kwargs)\n"
            "    pause()\n"
            "    return result\n"
            "campaigns.scope_snapshot = snapshot"
        )
        with self.paused_command(patch, "campaign", "start", "Scoped", "--allowed-paths", "owned") as child:
            script = self.cli_script(
                "import mythify_plans, mythify_io\n"
                "mythify_plans.jsonl_file_lock = lambda path: mythify_io.jsonl_file_lock(path, timeout=0)"
            )
            other = subprocess.run(
                [sys.executable, "-c", script, "campaign", "start", "Scoped", "--allowed-paths", "outside.txt"],
                cwd=self.project, env=self.env,
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(other.returncode, 1, other.stdout + other.stderr)
            self.assertIn("Another process is updating the plan store", other.stderr)
            self.assertFalse((self.state / "plans/scoped.json").exists())
            code, stdout, stderr = self.resume_command(child)
            self.assertEqual(code, 0, stdout + stderr)
        before = self.snapshot()
        self.refused("different order or scope", "campaign", "start", "Scoped",
                     "--allowed-paths", "outside.txt")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.plan("scoped")["campaign"]["scope"]["allowed_paths"], ["owned"])

    def test_verifier_cannot_publish_into_a_reopened_step_or_overwrite_learning(self):
        self.init_git()
        self.start_scoped()
        patch = (
            "original = mythify.run_shell_capture\n"
            "def capture(*args, **kwargs):\n"
            "    pause()\n"
            "    return original(*args, **kwargs)\n"
            "mythify.run_shell_capture = capture"
        )
        with self.paused_command(patch, "plan", "verify", "1") as child:
            self.ok("campaign", "learn", "Keep the selected scope")
            self.ok("step", "1", "skipped")
            self.ok("plan", "verify", "1")
            before = self.plan("scoped")
            code, stdout, stderr = self.resume_command(child)
            self.assertEqual(code, 1, stdout + stderr)
            self.assertIn("Ordered campaign step changed", stderr)
        self.assertEqual(self.plan("scoped"), before)
        self.ok("campaign", "advance", "--result", "New attempt verified")
        lessons = self.plan("scoped")["campaign"]["lessons"]
        self.assertEqual([item["text"] for item in lessons], ["Keep the selected scope"])

    def test_start_reuses_existing_plan_and_preserves_progress_and_evidence(self):
        self.create_plan()
        self.ok("step", "1", "in_progress")
        self.ok("plan", "verify", "1")
        self.ok("step", "1", "completed", "First check passed")
        self.ok("step", "2", "in_progress")
        before = self.snapshot()
        original = self.plan()
        self.ok("campaign", "start", "Finish existing work", "--success", "Both steps pass")
        updated = self.plan()
        self.assertEqual(updated["steps"], original["steps"])
        self.assertEqual(updated["verification_anchor"], original["verification_anchor"])
        self.assertEqual(updated["goal"], original["goal"])
        self.assertTrue(updated["strict_context"])
        self.assertEqual(len(list((self.state / "plans").glob("*.json"))), 1)
        self.assertFalse((self.state / "campaigns").exists())
        after = self.snapshot()
        for path, content in before.items():
            if path != "plans/existing.json":
                self.assertEqual(after[path], content, path)
        stable = self.snapshot()
        self.ok("campaign", "start", "Finish existing work")
        self.assertEqual(self.snapshot(), stable)

    def test_advance_requires_started_step_and_scoped_verification(self):
        self.create_plan()
        self.ok("campaign", "start", "Finish")
        before = self.snapshot()
        result = self.run_cli("campaign", "advance", "--result", "looks done")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.snapshot(), before)
        self.ok("step", "1", "in_progress")
        result = self.run_cli("campaign", "advance", "--result", "looks done")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Verified evidence required", result.stderr)
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Stored verifier passed")
        self.assertEqual(self.plan()["steps"][0]["status"], "completed")
        self.assertEqual(self.plan()["steps"][1]["status"], "pending")

    def test_failed_latest_check_and_waiver_cannot_advance(self):
        self.create_plan()
        self.ok("campaign", "start", "Finish")
        self.ok("step", "1", "in_progress")
        env = dict(self.env, MYTHIFY_REQUIRE_VERIFIED_STEP="0")
        result = self.run_cli("campaign", "advance", "--result", "waive", env=env)
        self.assertEqual(result.returncode, 1)
        # Same command passes, then fails after its input changes.
        check = self.project / "check.py"
        check.write_text("raise SystemExit(0)\n")
        command = shlex.quote(sys.executable) + " check.py"
        self.ok("plan", "create", "Gate", "--steps", json.dumps([
            {"title": "Gate", "verify_command": command}]))
        self.ok("campaign", "start", "Gate")
        self.ok("step", "1", "in_progress")
        self.ok("plan", "verify", "1")
        check.write_text("raise SystemExit(1)\n")
        self.assertEqual(self.run_cli("plan", "verify", "1").returncode, 2)
        self.assertEqual(self.run_cli("campaign", "advance", "--result", "old pass").returncode, 1)

    def test_named_prompt_and_status_are_read_only_and_resume_in_new_process(self):
        self.ok("campaign", "start", "Deliver", "--name", "delivery", "--success", "Checks pass",
                "--tasks", json.dumps([{"title": "Implement", "verify_command": self.verifier}]))
        self.ok("campaign", "learn", "Keep the existing verifier", "--apply-next")
        self.ok("plan", "create", "Other")
        before = self.snapshot()
        status = json.loads(self.ok("campaign", "status", "delivery", "--json").stdout)
        packet = json.loads(self.ok("campaign", "prompt", "delivery", "--json").stdout)
        self.assertEqual(status["plan"], "delivery")
        self.assertEqual(status["status"], "active")
        self.assertIn("Keep the existing verifier", packet["next_prompt"])
        self.assertIn("--plan delivery", packet["next_prompt"])
        resumed = self.ok("campaign", "start", "Deliver", "--plan", "delivery")
        self.assertIn("Next: mythify campaign prompt delivery", resumed.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_complete_campaign_has_no_next_step_and_skips_are_not_completion(self):
        self.ok("campaign", "start", "Deliver", "--verify", self.verifier)
        self.ok("step", "1", "in_progress")
        self.ok("plan", "verify", "1")
        self.ok("campaign", "advance", "--result", "Passed")
        view = json.loads(self.ok("campaign", "status", "--json").stdout)
        self.assertEqual(view["status"], "completed")
        self.assertIsNone(view["current_step"])
        before = self.snapshot()
        self.assertEqual(self.run_cli("campaign", "advance", "--result", "again").returncode, 1)
        self.assertEqual(self.snapshot(), before)
        self.ok("step", "1", "skipped")
        view = json.loads(self.ok("campaign", "status", "--json").stdout)
        self.assertEqual(view["status"], "blocked")

    def test_invalid_tasks_and_conflicting_start_leave_state_unchanged(self):
        for tasks in ('{}', '[{}]', '[{"title": ""}]', '[{"title": 4}]'):
            before = self.snapshot()
            self.assertEqual(self.run_cli("campaign", "start", "New", "--tasks", tasks).returncode, 1)
            self.assertEqual(self.snapshot(), before)
        self.create_plan()
        before = self.snapshot()
        self.assertEqual(self.run_cli("campaign", "start", "New", "--tasks", '[{"title":"replace"}]').returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_prompt_packet_next_and_route_resume_campaign(self):
        self.ok("campaign", "start", "Deliver", "--verify", self.verifier)
        before = self.snapshot()
        packet = json.loads(self.ok("prompt", "next", "--json").stdout)
        self.assertEqual(packet["selected_kind"], "campaign")
        explicit = json.loads(self.ok("prompt", "campaign", "--json").stdout)
        self.assertEqual(explicit["source"]["id"], "deliver")
        for task in ("continue", "keep going until done", "full send"):
            route = json.loads(self.ok("route", task, "--json").stdout)
            self.assertIn("mythify campaign prompt", json.dumps(route))
        self.assertEqual(self.snapshot(), before)

    def test_advance_rejects_evidence_for_another_plan(self):
        self.create_plan()
        self.ok("campaign", "start", "Finish")
        self.ok("step", "1", "in_progress")
        self.ok("plan", "create", "Other", "--steps", json.dumps([
            {"title": "Unrelated", "verify_command": self.verifier}]))
        self.ok("plan", "verify", "1")
        before = self.snapshot()
        result = self.run_cli("campaign", "advance", "existing", "--result", "Other check passed")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_manual_verifier_works_for_an_existing_plan_without_stored_command(self):
        self.ok("plan", "create", "Manual", "--steps", '[{"title":"Check manually"}]')
        self.ok("campaign", "start", "Manual")
        self.ok("step", "1", "in_progress")
        self.ok("verify", "run", self.verifier)
        self.ok("campaign", "advance", "--result", "Manual check passed")

    def test_watch_is_read_only_and_rejects_invalid_limits(self):
        self.ok("campaign", "start", "Deliver")
        before = self.snapshot()
        result = self.ok("campaign", "watch", "--interval", "0.001", "--max-iterations", "2")
        self.assertEqual(result.stdout.count("Campaign: deliver"), 1)
        for args in (("--interval", "0"), ("--interval", "nan"),
                     ("--interval", "inf"), ("--max-iterations", "-1")):
            self.assertEqual(self.run_cli("campaign", "watch", *args).returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_watch_reloads_changed_prompts_without_writing_state(self):
        self.ok("campaign", "start", "Deliver")
        process = subprocess.Popen(
            [sys.executable, str(CLI), "campaign", "watch", "--interval", "0.02",
             "--max-iterations", "60"], cwd=self.project, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            first = []
            while True:
                line = process.stdout.readline()
                self.assertTrue(line, "Watcher exited before first prompt")
                first.append(line)
                if line.startswith("Guardrail:"):
                    break
            self.ok("campaign", "learn", "Changed frontier", "--apply-next")
            before = self.snapshot()
            output, errors = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, errors)
            self.assertEqual(output.count("Campaign: deliver"), 1)
            self.assertEqual(output.count("Changed frontier"), 1)
            self.assertNotIn("Changed frontier", "".join(first))
            self.assertEqual(self.snapshot(), before)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_malformed_campaign_metadata_does_not_break_orientation(self):
        self.create_plan()
        path = self.state / "plans" / "existing.json"
        for metadata in ({"schema_version": 1}, {"schema_version": 1, "goal": "g", "lessons": [None]}):
            plan = self.plan()
            plan["campaign"] = metadata
            path.write_text(json.dumps(plan))
            before = self.snapshot()
            self.ok("status")
            self.ok("prompt", "next")
            self.assertEqual(self.run_cli("campaign", "prompt").returncode, 1)
            self.assertEqual(self.run_cli("campaign", "start", "Finish").returncode, 1)
            self.assertEqual(self.snapshot(), before)

    def test_learning_handles_optional_lessons_field(self):
        self.ok("campaign", "start", "Deliver")
        path = self.state / "plans" / "deliver.json"
        plan = json.loads(path.read_text())
        del plan["campaign"]["lessons"]
        path.write_text(json.dumps(plan))
        self.ok("campaign", "status")
        self.ok("campaign", "learn", "Keep progress")
        self.assertIn("Keep progress", self.ok("campaign", "prompt").stdout)

    def test_active_pointer_cannot_escape_plan_store(self):
        self.ok("campaign", "start", "Deliver")
        outside = self.project / "outside.json"
        outside.write_bytes((self.state / "plans" / "deliver.json").read_bytes())
        original = outside.read_bytes()
        pointer = self.state / "plans" / "active"
        for name in ("../../outside", str(outside.with_suffix("")), "../plans/deliver"):
            pointer.write_text(name + "\n")
            before = self.snapshot()
            for args in (("learn", "Tampered"), ("start", "Other"), ("prompt",), ("status",)):
                result = self.run_cli("campaign", *args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(outside.read_bytes(), original)
                self.assertEqual(self.snapshot(), before)

    def test_campaign_refuses_plan_and_parent_symlinks_outside_state(self):
        self.ok("campaign", "start", "Deliver")
        path = self.state / "plans" / "deliver.json"
        outside = self.project / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
        original = outside.read_bytes()
        for args in (("learn", "Tampered"), ("start", "Deliver", "--plan", "deliver"),
                     ("prompt", "deliver"), ("status", "deliver")):
            result = self.run_cli("campaign", *args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(outside.read_bytes(), original)
            self.assertTrue(path.is_symlink())
        path.unlink()
        outside.rename(path)
        plans = self.state / "plans"
        outside_plans = self.project / "outside-plans"
        plans.rename(outside_plans)
        plans.symlink_to(outside_plans, target_is_directory=True)
        before = {p.name: p.read_bytes() for p in outside_plans.glob("*") if p.is_file()}
        for args in (("learn", "Tampered"), ("start", "Other"), ("prompt",), ("status",)):
            result = self.run_cli("campaign", *args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        after = {p.name: p.read_bytes() for p in outside_plans.glob("*") if p.is_file()}
        self.assertEqual(after, before)
        (outside_plans / "archive").rmdir()
        env = dict(self.env, MYTHIFY_DIR=str(self.state))
        result = self.run_cli("campaign", "status", env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((outside_plans / "archive").exists())

    def test_pointer_and_archive_symlinks_cannot_escape_store(self):
        self.ok("campaign", "start", "Deliver")
        pointer = self.state / "plans" / "active"
        outside_pointer = self.project / "outside-pointer"
        outside_pointer.write_text("deliver\n")
        pointer.unlink()
        pointer.symlink_to(outside_pointer)
        self.assertEqual(self.run_cli("campaign", "learn", "Tampered").returncode, 1)
        self.assertEqual(outside_pointer.read_text(), "deliver\n")
        pointer.unlink()
        pointer.write_text("deliver\n")
        archive = self.state / "plans" / "archive"
        archive.rmdir()
        outside_archive = self.project / "outside-archive"
        outside_archive.mkdir()
        archive.symlink_to(outside_archive, target_is_directory=True)
        for args in (("plan", "archive", "deliver"), ("plan", "create", "Other")):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(outside_archive.iterdir()), [])
        self.assertTrue((self.state / "plans" / "deliver.json").is_file())

    def test_campaign_inspection_preserves_corrupt_plan_bytes(self):
        self.ok("campaign", "start", "Deliver")
        path = self.state / "plans" / "deliver.json"
        for bad in (b"{broken", bytes([255, 254])):
            path.write_bytes(bad)
            before = self.snapshot()
            for args in (("campaign", "status"), ("campaign", "prompt"),
                         ("campaign", "watch"), ("prompt", "campaign")):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.snapshot(), before)
            for args in (("prompt", "next"), ("prompt", "handoff"), ("status",)):
                result = self.ok(*args)
                self.assertIn("file left unchanged", result.stderr)
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
