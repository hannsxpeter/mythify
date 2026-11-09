# Named checks and the verify gate

Two small features make verification fast and hard to skip. A named check
stores a command once and runs it by name, optionally on only the files that
changed. `verify gate` answers, for a host's stop or pre-commit hook, whether
any step is in flight without evidence.

## Named checks

```bash
mythify verify check fast --set "python3 -m unittest tests.test_parser" --description "parser only"
mythify verify check full --set "python3 -m unittest discover -s tests"
mythify verify check                  # list the defined checks
mythify verify check fast             # run one, record it, exit 0 or 2
```

Checks live in `.mythify/checks.json`. A name is lowercase letters, digits, and
hyphens. `--set` stores the command and runs nothing; it refuses a command that
cannot fail (`true`, `exit 0`, `echo ok`, `pytest || true`), because a check
that cannot fail proves nothing. Running goes through the same runner as
`verify run`: the record carries the output, the commit and worktree
fingerprint, and a test count, plus `check` (the name) and `check_template`
(the stored command). `--reuse`, `--timeout`, `--claim`, and `--json` work as
they do elsewhere, and `MYTHIFY_DISABLE_RUN=1` refuses with exit 2.

### Run only what changed

Put `{files}` in the command to run it on the changed files:

```bash
mythify verify check lint --set "ruff check {files}"
mythify verify check lint                       # changed files: working tree against HEAD, plus untracked
mythify verify check lint --files src/a.py "src/b c.py"
```

`{files}` becomes the shell-quoted list, so a name with spaces or shell
characters stays one argument. Deleted files and `.mythify/` are left out. With
no changed files, Mythify prints `[OK] No changed files for check lint; nothing
to run.`, records nothing, and exits 0. Outside git, pass `--files`.

### Checks as step verifiers

A step's verify command may be `@NAME`:

```bash
mythify plan add-step "Parser fixed" --verify @fast
mythify plan verify 1        # runs the fast check with this step's context
mythify step 1 completed "fast check passed"
```

`plan verify` resolves `@fast` and records the run for the step. A
`verify check fast` run while the step is in progress counts for it too. The
step gate accepts any passing run of the check made since the step started,
because a `{files}` command expands differently each time; a run of a different
check, or a plain `verify run` of the same text, does not count, and a later
failing run of the check cancels an earlier pass. `plan create` and `plan
add-step` warn when `@NAME` is not defined yet. Prompt packets and `status` list
the defined checks.

## Wiring the gate into a host hook

`mythify verify gate` is read-only and runs no command. For each in-progress
step of the active plan (or `--plan NAME`) it applies the same rules as
`step ID completed`:

- the step needs a passing executed run since it started, and a later failing
  run of the same command cancels it;
- evidence that predates the current commit or worktree does not pass;
- on a strict-context plan, a test count below the step's baseline does not
  pass without a recorded reason, and a high-risk change without a covering
  review and the person's words does not pass.

Exit 0 means nothing is in flight without evidence, or there is no active
plan. Exit 2 means unverified work is in flight. Each failing step prints two
lines on stderr: the plan and step, and the next command, usually `mythify plan
verify ID --plan SLUG`. `--json` prints `{"ok", "plan", "steps": [{"id",
"title", "ok", "reasons", "next"}]}` on stdout.

Any host that runs a command when an agent stops, or before a commit, can use
it. A non-zero exit means "unverified work in flight":

```bash
# A stop or pre-commit hook: block while a step has no current evidence.
mythify verify gate || exit 1
```

A hook cannot supply the person's approval, so a strict-context step that
touches high-risk paths keeps the gate at exit 2 until `step ID completed
--human-input TEXT` records it. The gate ignores `MYTHIFY_REQUIRE_VERIFIED_STEP=0`:
a hook asks whether evidence exists, not whether the session waived the rule.
