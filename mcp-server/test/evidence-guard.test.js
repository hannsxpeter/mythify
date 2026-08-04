import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import {
  activeLegacyOptOuts,
  ledgerChainBreaks,
  noopVerifierReason,
  trivialPassReason,
} from "../src/evidence-guard.js";

function executedPass(command, stdoutTail = "", stderrTail = "") {
  return {
    kind: "executed",
    verified: true,
    exit_code: 0,
    command,
    stdout_tail: stdoutTail,
    stderr_tail: stderrTail,
  };
}

test("noopVerifierReason flags always-pass and bare print commands", () => {
  for (const command of ["true", " true ", "TRUE", ":", "exit 0", "echo ok", "printf done"]) {
    assert.notEqual(noopVerifierReason(command), null, command);
  }
});

test("noopVerifierReason leaves real checks alone", () => {
  for (const command of [
    "python3 -m unittest discover -s tests",
    "npm test --prefix mcp-server",
    "test -f dist/mythify.py",
    "echo start && pytest",
    "echo payload | grep expected",
    "",
  ]) {
    assert.equal(noopVerifierReason(command), null, command);
  }
});

test("trivialPassReason flags no-op passes and zero-test green runs", () => {
  assert.notEqual(trivialPassReason(executedPass("true")), null);
  const samples = [
    "Ran 0 tests in 0.000s\n\nNO TESTS RAN",
    "collected 0 items",
    "no tests collected",
    "?   \texample.com/pkg\t[no test files]",
    "  0 passing (2ms)",
  ];
  for (const stdout of samples) {
    assert.notEqual(trivialPassReason(executedPass("run-the-suite", stdout)), null, stdout);
  }
  assert.notEqual(
    trivialPassReason(executedPass("run-the-suite", "", "Ran 0 tests in 0.000s")),
    null
  );
});

test("trivialPassReason ignores substantive passes and non-passing records", () => {
  assert.equal(
    trivialPassReason(
      executedPass("python3 -m unittest discover -s tests", "", "Ran 214 tests in 92.1s\n\nOK")
    ),
    null
  );
  assert.equal(trivialPassReason({ kind: "executed", verified: false, command: "true" }), null);
  assert.equal(trivialPassReason({ kind: "attested", verified: null, command: "true" }), null);
  assert.equal(trivialPassReason(null), null);
});

test("activeLegacyOptOuts names each active opt-out with its effect", () => {
  assert.deepEqual(activeLegacyOptOuts({}), []);
  assert.deepEqual(
    activeLegacyOptOuts({
      MYTHIFY_REQUIRE_VERIFIED_STEP: "1",
      MYTHIFY_REQUIRE_HUMAN_INPUT: "yes",
      MYTHIFY_DISABLE_RUN: "",
    }),
    []
  );
  const active = activeLegacyOptOuts({
    MYTHIFY_REQUIRE_VERIFIED_STEP: "0",
    MYTHIFY_REQUIRE_HUMAN_INPUT: "off",
    MYTHIFY_DISABLE_RUN: "1",
  });
  assert.deepEqual(
    active.map((item) => item.name),
    ["MYTHIFY_REQUIRE_VERIFIED_STEP", "MYTHIFY_REQUIRE_HUMAN_INPUT", "MYTHIFY_DISABLE_RUN"]
  );
  for (const item of active) {
    assert.ok(item.effect);
  }
});

test("ledgerChainBreaks flags edited and deleted chained lines only", () => {
  const chained = (records) => {
    const lines = [];
    for (const record of records) {
      const previous = lines.length > 0 ? lines[lines.length - 1] : "";
      lines.push(
        JSON.stringify({
          ...record,
          prev_sha256: previous
            ? crypto.createHash("sha256").update(previous, "utf8").digest("hex")
            : null,
        })
      );
    }
    return lines;
  };
  const intact = chained([{ claim: "a" }, { claim: "b" }, { claim: "c" }]);
  assert.deepEqual(ledgerChainBreaks(intact.join("\n") + "\n"), []);

  const edited = chained([{ claim: "a" }, { claim: "b" }, { claim: "c" }]);
  edited[1] = edited[1].replace('"b"', '"forged"');
  assert.deepEqual(ledgerChainBreaks(edited.join("\n")), [3]);

  const deleted = chained([{ claim: "a" }, { claim: "b" }, { claim: "c" }]);
  deleted.splice(1, 1);
  assert.deepEqual(ledgerChainBreaks(deleted.join("\n")), [2]);

  const legacy = [JSON.stringify({ claim: "old-a" }), JSON.stringify({ claim: "old-b" })];
  assert.deepEqual(ledgerChainBreaks(legacy.join("\n")), []);

  const compacted = chained([{ claim: "a" }, { claim: "b" }, { claim: "c" }]).slice(1);
  assert.deepEqual(ledgerChainBreaks(compacted.join("\n")), []);

  assert.deepEqual(ledgerChainBreaks(""), []);
});
