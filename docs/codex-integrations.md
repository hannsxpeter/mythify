# Codex Integrations

Mythify works in Codex through two layers:

1. `AGENTS.md` gives Codex the protocol instructions.
2. The MCP server gives Codex durable Mythify tools (`plan_create`,
   `verify_run`, `fanout_start`, and the rest of the tool surface).

The first layer is enough for lightweight use. The second layer is what makes
the protocol durable across tool calls and gives Codex access to fanout.
For a cross-client setup covering Codex Desktop, Claude Desktop, and Cursor
Desktop, see [desktop-tool-calls.md](desktop-tool-calls.md).

## Codex Desktop

Codex reads `AGENTS.md` from the workspace before doing work. To use Mythify in
Codex Desktop:

```bash
cp AGENTS.md /path/to/your/project/
mkdir -p /path/to/your/project/scripts
mkdir -p /path/to/your/project/protocol
cp scripts/mythify.py /path/to/your/project/scripts/
cp protocol/operation-registry.json /path/to/your/project/protocol/
cp protocol/classification-rules.json /path/to/your/project/protocol/
cd /path/to/your/project
python3 scripts/mythify.py protocol check AGENTS.md
python3 scripts/mythify.py init
```

Open that project in Codex Desktop. When Codex runs shell commands, it can use
the local CLI directly:

```bash
python3 scripts/mythify.py status
python3 scripts/mythify.py verify run "python3 -m unittest" --claim "tests pass"
```

This path uses your existing Codex Desktop or Codex CLI subscription login. No
Mythify API key is required.

## Codex MCP Tools

Codex CLI supports registering stdio MCP servers. From this repository, run:

```bash
codex mcp add mythify \
  --env MYTHIFY_DIR=/absolute/path/to/your/project/.mythify \
  --env MYTHIFY_HOST_PLATFORM=codex-desktop \
  -- node /absolute/path/to/mythify/mcp-server/src/index.js
```

Then open the same project in Codex. The `mythify` server exposes the same
state directory that the CLI uses. Plans, memory, lessons, verifications, and
fanout jobs are visible across Codex sessions that point at that project.

For fast problem framing, use classification with local Codex triage:

```bash
python3 scripts/mythify.py classify "make this better" \
  --json \
  --triage auto \
  --triage-engine codex-cli
```

In MCP, call `classify_task` with `triage: "auto"` and
`triage_engine: "codex-cli"`. Add `platform: "codex-desktop"` when the caller
is Codex Desktop. Mythify runs the deterministic classifier first, then starts
one `codex exec` triage pass only when the gate is recommended or required.
This uses the current Codex login, not a Mythify API key.

The active Codex session model remains host-selected. Mythify records that in
`model_policy.session` and only controls spawned workers. For spawned workers,
`model_policy.triage` and `model_policy.fanout_worker` describe engine, model
policy, timeout, effort, and spawn ceiling. Pass `session_model` when Codex
can name the active model, or call `host_model_switch` to record an intended
host model in `.mythify/host-model.json`. Codex Desktop still owns the actual
current chat switch. When the calling agent has Codex app tools, the returned
guidance may include a `send_message_to_thread` model override for a follow-up
thread turn; Mythify itself records policy state rather than invoking that host
tool.

`model_policy.session.recommendation` is the task-based host hint. A direct
question recommends the fast Codex profile (`gpt-5.4-mini`, low thinking,
fast speed). Research, benchmark, design, release, migration, and security
work recommends the strong Codex profile (`gpt-5.5`, high thinking, standard
speed). Normal implementation work recommends the standard profile
(`gpt-5.4`, medium thinking, auto speed). Override those model ids with
`MYTHIFY_HOST_FAST_MODEL`, `MYTHIFY_HOST_STANDARD_MODEL`, and
`MYTHIFY_HOST_STRONG_MODEL` when your Codex install exposes different names.

Remove it with:

```bash
codex mcp remove mythify
```

List configured servers with:

```bash
codex mcp list
```

## Codex Fanout Workers

For fanout, Mythify can spawn `codex exec` workers through the local
`codex-cli` engine. Workers use local Codex authentication, usually the same
ChatGPT-plan auth that `codex doctor` reports as configured.

Run once if the CLI is not already logged in:

```bash
codex login
```

By default, fanout workers use:

```text
codex --ask-for-approval never exec --sandbox read-only --skip-git-repo-check --ephemeral
```

The default `read-only` sandbox matches fanout's purpose: workers produce
material, the orchestrator merges it, then the merged work is verified. To let
workers edit files directly, set:

```bash
MYTHIFY_FANOUT_CODEX_SANDBOX=workspace-write
```

Use that only when the workspaces are disposable or you are comfortable with
parallel worker edits.

Fanout model, effort, and speed can be set at the job level or per task:

```json
{
  "engine": "codex-cli",
  "session_model": "gpt-5",
  "spawn_ceiling": "same_or_lower",
  "effort": "medium",
  "speed": "auto",
  "visibility": "summary",
  "purpose": "Review this change and show concise worker progress.",
  "tasks": [
    {"title": "Frame risk", "prompt": "Identify the risky parts of this change.", "effort": "low"},
    {"title": "Review implementation", "prompt": "Review the supplied patch for bugs.", "effort": "high", "speed": "fast"}
  ]
}
```

Omit `model` to use the local Codex default. Pass `model` only when your Codex
CLI account exposes a specific model you want the worker to use. Explicit
spawned models stronger than `session_model` require
`spawn_ceiling: "allow_stronger"`.
Codex Desktop should usually keep fanout visibility at `summary`: show worker
titles, status counts, and notable findings in the same chat. Use `quiet` for
background work, `verbose` for full worker output, and `threaded` only when
Codex exposes a native way to create visible worker threads.

For Codex workers, `speed: "fast"` enables Codex fast mode where supported,
and `speed: "standard"` explicitly disables it for that worker.
`speed: "auto"` preserves the user's Codex default.
Claude and Cursor workers use the same Mythify fields but map them differently:
Claude receives resolved effort through `--effort`, while Cursor resolves
`model`, `effort`, and `speed` into an encoded model id from
`cursor-agent models` when a matching id exists.

## Local Benchmarks

Run the built-in bare-vs-Mythify benchmark with your local Codex subscription:

```bash
python3 scripts/local_model_eval.py \
  --engine codex-cli \
  --scenario all \
  --speed fast \
  --json-output .mythify/local-codex-benchmark.json \
  --keep-workspaces
```

The JSON report includes:

- `verified_success_rate`: how often the final test command passed.
- `evidence_success_rate`: how often the Mythify run produced required
  evidence for its profile. The default `--mythify-profile auto` uses the fast
  profile for built-in focused bugfix scenarios, so evidence is an executed
  verification. `--mythify-profile standard` requires both a plan and an
  executed verification.
- `avg_model_duration_seconds`: average model runtime per mode.
- Per-run workspace paths, output tails, and verification tails.

To force the older plan-plus-verify benchmark behavior:

```bash
python3 scripts/local_model_eval.py \
  --engine codex-cli \
  --scenario all \
  --mythify-profile standard \
  --json-output .mythify/local-codex-benchmark-standard.json
```

This is a local smoke benchmark, not a substitute for a benchmark suite like
SWE-bench Verified. Its value is fast feedback: does the same local model
produce more verified, auditable work when wrapped in Mythify?

## Codex Desktop Environment Notes

When Mythify is launched from Codex Desktop, the environment may include
Codex-specific variables such as `CODEX_SHELL`, `CODEX_CI`, and a Desktop
origin marker. Mythify does not depend on those variables for auth. It relies
on the local Codex auth store under `HOME` and, when set, `CODEX_HOME`.

The fanout `codex-cli` worker environment is intentionally small:

- `HOME`
- `TERM=dumb`
- an augmented `PATH`
- `CODEX_HOME` when present
- `XDG_CONFIG_HOME` when present
- fanout depth guards

This keeps workers portable between Codex Desktop, Codex CLI, and other MCP
hosts while avoiding accidental API-key inheritance.
