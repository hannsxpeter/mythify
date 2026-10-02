# Self-Verification

Mythify treats evidence in two classes. Executed verification runs a real
command and reads the exit code: the machine judges. Attested verification is
a self-report: it is recorded, displayed with a warning, and never counts as
verified. Executed beats attested, always.

## Executed verification

    verify run COMMAND [--claim TEXT] [--timeout N]

The command runs through the shell. Mythify records the exit code, duration,
and output tails, then prints the verdict:

    [OK] VERIFIED: <claim or command> (exit 0, 0.03s)
    [FAIL] UNVERIFIED: <claim or command> (exit 2, 0.10s)

An unverified verdict is followed by `--- stdout (tail) ---` and
`--- stderr (tail) ---` blocks when those streams are non-empty, so the
failure is diagnosable from the record. The CLI itself exits 0 when verified
and 2 when unverified, so callers can branch on it. Default timeout is 300
seconds; a timed-out command is recorded as unverified with exit code -1.

Use `--claim` to state what the command proves; the claim is what appears in
the verdict and the log.

For a plan step that stores a `verify_command`, `plan verify ID` runs that
exact command and records the result against the step, which is what the
strict gate looks for.

## Attested verification

    verify claim CLAIM EVIDENCE

Use this only when nothing executable exists to check the claim. It prints:

    [WARN] ATTESTED: <claim> (self-reported, not machine-checked; prefer verify run)

The stored record has `verified: null`, permanently. An attestation never
upgrades to verified and never satisfies a completion claim when an
executable check was possible.

## When something executable exists

Almost always. Before reaching for `verify claim`, check for:

- A test suite or a single test file covering the change.
- A build, compile, or type-check command.
- A linter or formatter check.
- A curl or HTTP request against a running endpoint.
- A file existence, content grep, or line-count check.
- Running the script or binary and checking its exit code.

If any of these exist, `verify run` is mandatory. "I read the code and it
looks correct" is an attestation, not a verification.

The check must be able to fail. `true`, `exit 0`, and a bare `echo` always
exit 0, and a test run that collected zero tests checked nothing. Mythify
warns when a step or ticket stores such a command, and `status` lists those
passes as attention items. Output from a delegated subagent or worker is
material too: verify the integrated result, not the worker's report.

## The evidence rule for steps

Marking a plan step `completed` or `failed` requires a RESULT argument
describing the proof. Marking a step `completed` also requires a passing
executed `verify run` by default. Pair completion with verification: run
`verify run` first, then cite it in the RESULT. A step closed without a passing
execution behind it is a claim, not a fact. Set
`MYTHIFY_REQUIRE_VERIFIED_STEP=0` only for explicit legacy prose-only
completion.

## Examples

Prove a fix with the failing-then-passing pattern:

    verify run "python3 -m pytest tests/test_parser.py" --claim "parser tests pass"
    [FAIL] UNVERIFIED: parser tests pass (exit 1, 2.41s)
    ... fix the code ...
    verify run "python3 -m pytest tests/test_parser.py" --claim "parser tests pass"
    [OK] VERIFIED: parser tests pass (exit 0, 2.20s)

Check an artifact exists and is well formed:

    verify run "python3 -c \"import json; json.load(open('out/report.json'))\"" --claim "report is valid JSON"

Attest only when execution is impossible:

    verify claim "Stakeholder approved the copy change" "Approval in thread, message of 2026-06-12"

## Honest accounting

`summary` reports executed passed, executed failed, and attested counts as
separate numbers. Keep the attested count low; a session whose evidence is
mostly attestation has weak ground under its claims.
