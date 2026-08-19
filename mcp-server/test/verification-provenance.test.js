import assert from "node:assert/strict";
import test from "node:test";

import { evidenceMovedSinceRun } from "../src/verification-provenance.js";

test("evidenceMovedSinceRun names only visible movement", () => {
  const clean = { git_commit: "same", worktree_clean: true };
  assert.equal(evidenceMovedSinceRun({}, clean), null);
  assert.equal(
    evidenceMovedSinceRun({ provenance: { git_commit: "same", worktree_clean: true } }, clean),
    null
  );
  assert.equal(
    evidenceMovedSinceRun(
      { provenance: { git_commit: "old", worktree_clean: true } },
      { git_commit: "new", worktree_clean: true }
    ),
    "git_commit_changed_since_run"
  );
  assert.equal(
    evidenceMovedSinceRun(
      { provenance: { git_commit: "same", worktree_clean: true } },
      { git_commit: "same", worktree_clean: false }
    ),
    "worktree_changed_since_run"
  );
  // The dev-loop case stays silent: dirty at run, dirty at completion.
  assert.equal(
    evidenceMovedSinceRun(
      { provenance: { git_commit: "same", worktree_clean: false } },
      { git_commit: "same", worktree_clean: false }
    ),
    null
  );
  assert.equal(
    evidenceMovedSinceRun(
      { provenance: { git_commit: "same", worktree_clean: false, worktree_digest: "one" } },
      { git_commit: "same", worktree_clean: false, worktree_digest: "two" }
    ),
    "worktree_digest_changed_since_run"
  );
  // Off-git runs carry no commit and never claim movement.
  assert.equal(
    evidenceMovedSinceRun(
      { provenance: { git_commit: null, worktree_clean: null } },
      { git_commit: null, worktree_clean: null }
    ),
    null
  );
});
