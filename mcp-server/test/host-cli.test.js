import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  HOST_CLI_IDS,
  formatHostCliProbe,
  formatHostCliRun,
  probeHostCli,
  runHostCliWorker,
} from "../src/host-cli.js";

test("host CLI module exposes stable ids and guarded failures", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "mythify-host-cli-module-"));
  const customBin = path.join(root, "custom-runner");
  fs.writeFileSync(customBin, "#!/usr/bin/env node\nconsole.log('custom');\n", "utf8");
  fs.chmodSync(customBin, 0o755);
  try {
    assert.deepEqual(HOST_CLI_IDS, ["kimi-code", "opencode", "antigravity"]);

    const refusedProbe = probeHostCli({
      host: "opencode",
      bin: customBin,
      timeout_seconds: 1,
    });
    assert.equal(refusedProbe.status, "blocked");
    assert.match(refusedProbe.error, /not allowed/);
    assert.match(formatHostCliProbe(refusedProbe), /Host CLI probe blocked/);

    const refusedRun = runHostCliWorker({
      host: "antigravity",
      bin: "",
      prompt: "Review this.",
      cwd: "",
      default_cwd: root,
    });
    assert.equal(refusedRun.status, "blocked");
    assert.match(refusedRun.error, /requires explicit cwd/);
    assert.match(formatHostCliRun(refusedRun), /Host CLI run blocked/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
