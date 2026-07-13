import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { recoverInterruptedWorktree } from "../src/fanout-worktree-recovery.js";

function fixture() {
  const jobId = "fo-20260713151515-cdef";
  const task = { id: 1 };
  const worktree = {
    isolated: true,
    path: fs.mkdtempSync(path.join(os.tmpdir(), "mythify-fanout-wt-")),
    branch: `mythify/fanout-${jobId}-t1-abcdef`,
  };
  return { jobId, task, worktree };
}

function worktreeList(worktree) {
  return {
    status: 0,
    stdout: `worktree ${fs.realpathSync.native(worktree.path)}\nHEAD 012345\nbranch refs/heads/${worktree.branch}\n\n`,
    stderr: "",
  };
}

test("recovery retains both identifiers when registered worktree removal fails", () => {
  const { jobId, task, worktree } = fixture();
  let call = 0;
  try {
    const recovered = recoverInterruptedWorktree("/repo", jobId, task, worktree, () => {
      call += 1;
      return call === 1
        ? worktreeList(worktree)
        : { status: 1, stdout: "", stderr: "remove refused" };
    });
    assert.equal(recovered.path, worktree.path);
    assert.equal(recovered.branch, worktree.branch);
    assert.equal(recovered.cleaned_on_recovery, false);
    assert.equal(recovered.cleanup_failed, true);
    assert.deepEqual(recovered.recovery_original, {
      path: worktree.path,
      branch: worktree.branch,
    });
    assert.match(recovered.note, /remove refused/);
  } finally {
    fs.rmSync(worktree.path, { recursive: true, force: true });
  }
});

test("recovery retains branch metadata when deletion fails after removal", () => {
  const { jobId, task, worktree } = fixture();
  let call = 0;
  try {
    const recovered = recoverInterruptedWorktree("/repo", jobId, task, worktree, () => {
      call += 1;
      if (call === 1) {
        return worktreeList(worktree);
      }
      return call === 2
        ? { status: 0, stdout: "", stderr: "" }
        : { status: 1, stdout: "", stderr: "branch refused" };
    });
    assert.equal(recovered.path, null);
    assert.equal(recovered.branch, worktree.branch);
    assert.equal(recovered.cleaned_on_recovery, false);
    assert.equal(recovered.cleanup_failed, true);
    assert.deepEqual(recovered.recovery_original, {
      path: worktree.path,
      branch: worktree.branch,
    });
    assert.match(recovered.note, /branch refused/);
  } finally {
    fs.rmSync(worktree.path, { recursive: true, force: true });
  }
});
