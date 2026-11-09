# Findings reviews and the high-risk path check

A findings review is the record of an independent reviewer's rounds over a
change. It answers three questions that a blast-radius safety case does not:
what did the reviewer raise, what was done about each point, and does the last
clean pass still describe the code that is about to ship. It is material, not
verification evidence: only an executed `verify run` proves anything.

Findings reviews live beside blast-radius reviews in `.mythify/reviews/`. See
[blast-radius.md](blast-radius.md) for safety cases.

## Rounds

```bash
mythify review round auth-change                  # open round 1
mythify review finding auth-change "Token is logged" --path src/auth.py --severity high
mythify review resolve auth-change F1 --status fixed
mythify review round auth-change                  # round 2, after the fix
mythify review round auth-change --clean          # the reviewer found nothing at the current source
mythify review show auth-change
```

- `review round NAME` creates the review on first use and opens the next round,
  stamped with the git commit and worktree digest it saw. It prints the files
  changed since the previous round (the re-review scope) and the dismissed
  findings the reviewer must not raise again.
- `--clean` records a round in which the reviewer found nothing. The round is
  closed at once and takes no findings.
- A new round is refused (exit 1) while any earlier finding is `open`. Resolve
  each one first.
- Three rounds are the limit. Round 4 and later are refused unless
  `--rethink TEXT` says why the approach still stands after three rounds that
  did not converge. The text is stamped on the round.

## Findings

`review finding NAME SUMMARY` adds `F1`, `F2`, ... to the latest round
(numbered across the whole review). It is refused when the latest round is
clean or no round exists.

`review resolve NAME FINDING_ID --status fixed|dismissed` closes a finding.
A dismissal needs `--reason`. A resolved finding is not reopened.

Repeats are checked on the summary (case, punctuation, and spacing folded away)
together with the path:

- a repeat of a **dismissed** finding is refused (exit 1) and names the
  earlier id and its reason;
- a repeat of a **fixed** finding is recorded with `repeat_of` and a
  `[WARN]`, because the fix may not have held;
- a repeat of a finding that is still open is refused as a duplicate.

## Coverage

`review show NAME` lists each round with its commit, whether it is clean, and
every finding with its status. The last line says what the review covers:

- `covers current source` when the latest round is clean and its stamped
  commit and worktree digest equal the current ones;
- otherwise `N commits and uncommitted changes since round K` (only the parts
  that apply are shown), or a note that the latest round is still open.

`review prove` accepts only blast-radius reviews and refuses a findings review.

`status` lists a review with open findings (an issue), a review at round 3 or
later without a clean pass (a warning to rethink), and, for the most recently
updated review only, a clean round that no longer covers the current source (a
warning). Older clean reviews that went stale are history and stay quiet. `prompt review` includes the
active findings review's round, re-review scope, and dismissed findings.

## The high-risk path check

When a step goes `in_progress`, Mythify stamps `started_commit` and
`started_at` on it. At `step ID completed`, the changed paths are
`git diff --name-only <started_commit>` (the working tree against that commit,
so committed and uncommitted work both count) plus untracked files, minus the
`.mythify/` directory. In a repository with no commits yet, or for a step that
started without a commit, there is no baseline, so every tracked and untracked
file counts as changed. They are rated by the `path_risk` globs in
`protocol/classification-rules.json`. Globs are matched case-insensitively
against the repo-relative path and its basename, and a path takes the highest
tier it matches. `route --json` shows the same rating for your current
changes as `classification.diff_risk`.

When the rating is high, completion needs both:

1. a review that covers the current source: a findings review whose latest
   round is clean at the current commit and digest, or a blast-radius review
   whose merge gate is verified and whose change fingerprint is current;
2. `--human-input TEXT`, the person's own words approving the change.

```bash
mythify step 3 completed "tests pass" --human-input "Sam: approved after reading the diff"
mythify campaign advance --result "tests pass" --human-input "Sam: approved"
```

Never write the human input yourself. Ask the person and record what they said.

A missing piece prints a `[WARN]` naming the high-risk paths and the next
command, and completion proceeds. On a plan with `strict_context` (set by
`campaign start`) it refuses with exit 1, the same split that applies to stale
evidence and to a drop in test count. The step records `risk` (level, paths,
the covering review, whether human input was given), `human_input`, and
`plan show` prints them.

`MYTHIFY_RISK_GATE=0` waives the check even under strict context. The waiver is
warned and stamped on the step as `risk_gate_waived: true`. Use it only when
the user asks. Outside a git repository the changed paths are unknown, so the
check is skipped.

## Through MCP

The commands have no typed MCP tool. Call them through the `mythify` tool with
an argument list, for example `{"args": ["review", "round", "auth-change", "--clean"]}`.
