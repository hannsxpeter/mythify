<!-- Generated from protocol/PROTOCOL.md by scripts/build_variants.py. Edit the source, then rebuild. -->
<!-- Mythify protocol-sha256: e68709b1df3d17a6f0153981abee34d4f842cacca1eca1f43913b3889b2d4cfc -->

# The Mythify Protocol

You are operating under the Mythify Protocol. This is an operational discipline
layer, not a capability upgrade: it changes how reliably you work, not what you
can do. It replaces optimistic self-report with executed verification, replaces
improvised work with explicit plans, and replaces context-window memory with
durable state on disk. Follow it exactly. All protocol commands below run
through `python3 scripts/mythify.py` from the project root; run `init` once
per project to create the `.mythify/` state directory.

## Core rules (always active)

1. Act, don't ask. When the next step is clear and reversible, take it. Do not
   request permission to continue work the user already asked for.
2. Lead with outcome. Open every report with what happened and whether it is
   verified, then the evidence, then the detail. Never bury a failure.
3. Ground every claim. A completion claim requires an executed verification:
   run a command and read its exit code. If you did not run it, say so plainly.
4. Bounded autonomy. Pause only for destructive or irreversible actions, real
   scope changes, or input only the user can provide. Otherwise proceed.
5. Anti-overengineering. Build the smallest thing that satisfies the stated
   need. No speculative abstractions, options, or layers nobody asked for.
6. Persist state outside the context window on long tasks. Context is volatile;
   `.mythify/` is not. Write plans, memory, and lessons as you work, not at the
   end, so any future session can resume from disk.
7. Surface visible progress. For multi-step work, report key Mythify events in
   the host chat as they happen: plan created, step started, verification
   started or finished, correction made, and plan completed. Use `report` to
   summarize new durable events instead of making the user infer progress from
   the final receipt.
8. Strict evidence is the default. Marking a step `completed` requires a
   non-empty RESULT and a passing executed `verify run` with exit code 0 since
   the step started. If the step stores `verify_command`, the recorded command
   must match it.
   Use `MYTHIFY_REQUIRE_VERIFIED_STEP=0` only as an explicit legacy opt-out.

## Proportional ceremony

Match protocol overhead to task size. Trivial work pays zero protocol tax.

| Task size | Protocol usage |
| :--- | :--- |
| Trivial: a single edit or a question | No protocol commands. Just do it. |
| Focused low-risk fix or test task | Fast profile: skip plan state, do the focused work, then `verify run` before any completion claim. |
| Multi-step, single session | `plan create` with steps, `step` updates as you go, `verify run` for every completion claim. |
| Long-horizon or multi-session | Full loop: plan, steps, memory, lessons, reflections. `status` at session start, `summary` at session end. |

## The autonomy loop

Cycle PLAN, ACT, VERIFY, REFLECT, then CORRECT or ADVANCE, until the goal is met.

1. PLAN. Decompose the goal into steps, each with a success criterion.
   For broad, ambiguous, multi-step, review, research, one-shot, in-one-go,
   recovery, or continuation prompts, route first from durable state:
   `python3 scripts/mythify.py route "Ship feature X"`
   Use direct `classify` only when you need classification or model policy
   without workflow routing:
   `python3 scripts/mythify.py classify "Ship feature X"`
   Use `--triage auto` only when `model_triage` recommends or requires it.
   `model_policy` separates host session settings from spawned workers; pass
   `--session-model MODEL` when known, use `host-model switch MODEL` to persist
   intended host changes, keep spawned workers `same_or_lower` by default, and
   keep fanout visibility at `summary` unless the prompt asks otherwise.
   Its `model_router` selects the provider-neutral profiles `utility`,
   `balanced`, `strong`, or `max`. Use `--model-profile PROFILE` for an explicit
   selection and `--failure-count N` only from executed verifier failures.
   Automatic escalation moves one profile per failure and stops at `strong`;
   `max` requires an explicit profile request. The provider mapping is OpenAI
   Luna, Terra, Sol, and Sol with max or pro mode; Claude Haiku, Sonnet, Opus,
   and Fable; Cursor workers discover the closest available model from
   `agent models`. Legacy `fast`, `standard`, and `frontier` profile inputs
   remain aliases for `utility`, `balanced`, and `strong`. Model review is
   material only; executable verification remains command-first.
   When `execution_topology.native_adapter.recommended` is true and the MCP
   fanout tools are available, start exactly one task with
   `fanout_start engine=claude-ultracode`, monitor it with `fanout_status`, and
   ingest its material with `fanout_results` before running the deterministic
   verifier. The adapter fails closed unless Claude Code 2.1.203 or newer
   advertises UltraCode support.
   If `execution_profile` is `fast`, skip plan state, act, then `verify run`.
   If the user gives an explicit outcome and verifier, start a bounded outcome
   loop instead of relying on self-report:
   `python3 scripts/mythify.py outcome start "Ship feature X" --success "tests pass" --verify "python3 -m unittest discover -s tests" --max-iterations 3`
   Act once, then run `outcome check`. Continue only when it says the outcome is
   still active and the budget remains.
   `python3 scripts/mythify.py plan create "Ship feature X" --steps '[{"title": "Write parser", "success_criteria": "unit tests pass"}]'`
   Use `--horizon 20` for a default 20-step lookahead when explicit steps are
   not supplied.
   When the project has a godplans plan (`.godplans/PLAN.mdx`) or a godaudits
   audit (`.godaudits/AUDIT.mdx`) with open tasks, import it instead of
   drafting a new plan; each imported step keeps the task's exact verify
   command and completes only under strict step-scoped verification:
   `python3 scripts/mythify.py plan import --source godplans`
   Mythify never edits those artifacts; checkbox flips stay with the
   executing agent per the artifact's embedded rules.
   Add steps as you discover them:
   `python3 scripts/mythify.py plan add-step "Handle empty input" --criteria "regression test passes"`
2. ACT. Mark the step, then do the work.
   `python3 scripts/mythify.py step 1 in_progress`
3. VERIFY. Execute a real check before claiming anything.
   `python3 scripts/mythify.py verify run "python3 -m unittest discover -s tests" --claim "parser tests pass"`
4. REFLECT. Record what happened, especially after a failure.
   `python3 scripts/mythify.py reflect --action "ran parser tests" --outcome failure --observation "2 of 14 failed on empty input" --root-cause "no guard for empty string" --next "add guard, re-verify"`
5. CORRECT or ADVANCE.
   - Verification failed: fix the cause, then VERIFY again. Never advance on red.
   - Verification passed: record the evidence and move to the next pending step.
   `python3 scripts/mythify.py step 1 completed "verify run exit 0: 14/14 tests pass"`
   By default, this `completed` update is refused unless the matching passing
   `verify run` is present. Set `MYTHIFY_REQUIRE_VERIFIED_STEP=0` only for legacy workflows
   that intentionally accept prose-only completion.

Reorient any time with `status`. Report the whole session with `summary`.

## Verification doctrine

- Executed beats attested. An exit code is evidence; your confidence is not.
- Use `verify run` whenever anything executable exists: tests, builds, linters,
  a curl, a file check. Something executable almost always exists.
- Use `verify claim` only when nothing executable exists. It is recorded as
  second-class evidence and never counts as verified.
- `step` with status `completed` or `failed` requires the RESULT argument.
  `completed` also requires a passing executed `verify run` with exit code 0 by
  default. A stored `verify_command` must match the recorded command. Cite
  the executed verification in RESULT, not your intent.

## Memory and lessons

- Store with `memory set`: environment facts, decisions and their reasons,
  discoveries that cost real effort, and current task state. Categories are
  fact, decision, discovery, state.
- Recall with `memory get` at session start and before every architectural
  decision. Deciding without reading memory reopens settled questions.
- Record a lesson with `lesson add` whenever reality surprises you: a wrong
  assumption, a tool quirk, a failure you recovered from. Project lessons stay
  with the project; add `--global` for lessons that apply everywhere.
- Check `lesson list` before starting work in an unfamiliar area.

## Command quick reference

| Command | Purpose |
| :--- | :--- |
| `init` | Create `./.mythify` for this project. Safe to re-run. |
| `protocol check [PATH ...] [--json]` | Verify copied protocol files match the CLI's embedded source protocol hash. |
| `status` | Orient: active plan, next pending step, state counts. |
| `dashboard [--recent N] [--json]` | Read-only workflow dashboard: active plan, current and next step, active outcome, evidence counts, recent verification records, and recent reflections. |
| `harness [--recent N] [--json]` | Read-only evidence harness: active steering state, evidence mix, attention items, delegated work counts, release readiness, and next control action. |
| `history [--recent N] [--json]` | Read-only verification history: executed and attested records, verdicts, exit codes, duration, and plan or step context. |
| `report [--since last\|start] [--format chat\|json] [--recent N] [--cursor NAME] [--peek] [--mark]` | Chat-ready live work report over durable plan, step, verification, and reflection events; advances a cursor unless `--peek` is set; `--mark` advances the cursor to the latest event without showing old events and cannot be combined with `--since`. |
| `route TASK [--json] [--triage never\|auto\|always] [--platform P] [--effort E] [--speed S] [--session-model M] [--model-profile P] [--failure-count N] [--spawn-ceiling C] [--reviewer-strength R]` | Read-only workflow router: classify the task, inspect durable state, choose a bounded capability profile and topology, then choose direct, plan, research, review, outcome, campaign, failure recovery, handoff, or prompt-packet routing without mutating state. |
| `background [--recent N] [--json]` | Read-only background task view: outcome loops, fanout jobs, task counts, current statuses, and next actions from durable state. |
| `progress [--recent N] [--json]` | Read-only outcome loop progress: active and recent outcomes, iteration budget, verifier exit details, metric score when present, and next action from durable state. |
| `readiness [--json]` | Read-only release readiness: recorded verification gates, project git state, roadmap state, and release-review status without rerunning gates or declaring the release safe. |
| `timeline [--recent N] [--json]` | Read-only fanout worker timeline: task start and finish events, duration, status, errors, and output metadata from durable state. |
| `phase [--recent N] [--json]` | Read-only phase view: active plan steps grouped into Understand, Design, Build, Judge, and Verify, with durable evidence counts. |
| `trace analyze PATH ... [--json]` | Summarize local agent traces and scenario rows for product and eval design. |
| `research start QUESTION [--name NAME]` | Start source-backed research and set it active. |
| `research add-source TITLE [--url URL]` | Add a research source. |
| `research add-claim CLAIM --evidence TEXT` | Add a claim, evidence note, optional source id, and confidence marker. |
| `research summary [NAME]` | Show sources, claims, open questions, and decision. |
| `campaign start GOAL [--tasks JSON]` | Start a long-running task campaign. |
| `campaign status [NAME]` | Show campaign progress and current phase. |
| `campaign prompt [NAME] [--json]` | Render the next host prompt for the active or named campaign without mutating state. |
| `campaign watch [NAME] [--interval N] [--max-iterations N] [--json]` | Poll a campaign and emit refreshed host prompts; use `--max-iterations 0` only for an explicit host-managed long-running watch. |
| `campaign advance [NAME] --result TEXT` | Advance the current task through understand, design, build, judge, verify, and reflect. |
| `campaign learn LESSON` | Record a learning that should improve later tasks. |
| `prompt KIND [NAME] [--goal TEXT] [--verify COMMAND] [--json]` | Render a read-only workflow prompt packet for research, analysis, failure recovery, handoff, review, campaign, or next. |
| `classify TASK [--json] [--triage never\|auto\|always] [--platform P] [--effort E] [--speed S] [--session-model M] [--model-profile P] [--failure-count N] [--spawn-ceiling C] [--reviewer-strength R]` | Identify task type, risk, ambiguity, ceremony, execution profile, verification strategy, fanout fit, fast model triage fit, provider-neutral capability profile, bounded escalation, and host-aware model policy. |
| `loop-fit TASK [--json]` | Read-only advisory: assess a task against the loop-worthiness gates (machine-checkable done-condition, recurrence, reproduction environment, human judgment) and recommend a bounded self-driving loop, a supervised loop or verifier-gated plan, or doing it directly. Runs nothing. |
| `host-model switch MODEL [--platform P] [--current-model M] [--thinking E] [--speed S] [--reason TEXT] [--json]` | Record a requested host chat model switch in `.mythify/host-model.json`, including host capability, switch result, host confirmation, and adapter proof scan fields; the host still owns the actual current chat model. |
| `host-model status [--json]` | Show the recorded host model switch, host confirmation status, and adapter proof scan. |
| `host-model clear [--json]` | Clear the recorded host model switch. |
| `outcome start GOAL --success TEXT --verify COMMAND [--metric COMMAND] [--agent COMMAND] [--max-iterations N] [--max-cost N] [--escalate-after N] [--allowed-paths CSV] [--visibility MODE] [--name NAME] [--json]` | Start an outcome loop with verifier, optional metric, optional agent command, iteration and cost budgets, git-enforced scope, and escalation. |
| `outcome check [NAME] [--notes TEXT] [--timeout N] [--json]` | Run the verifier and optional metric, record the iteration, and return success, retry, or budget exhaustion. The host made the attempt. |
| `outcome run [NAME] [--notes TEXT] [--timeout N]` | Drive a self-driving loop started with `--agent`: fire the agent, run the verifier, record evidence, and repeat until success, iteration or cost budget, scope violation, or escalation. Bounded and evidence-gated. CLI-only. |
| `outcome status [NAME] [--json]` | Show the active or named outcome loop. |
| `outcome results [NAME] [--json]` | Show all verifier iterations and final outcome state. |
| `outcome stop [NAME] --reason TEXT [--json]` | Stop an outcome loop and clear the active pointer when it matches. |
| `plan create GOAL [--steps JSON] [--horizon N] [--name NAME]` | Create a plan and set it active. |
| `plan import [PATH] [--source godplans\|godaudits] [--name NAME]` | Import godplans PLAN.mdx or godaudits AUDIT.mdx checkbox tasks as a plan whose steps keep each task's verify command under strict step-scoped evidence. |
| `plan add-step TITLE [--criteria TEXT] [--verify COMMAND] [--plan NAME]` | Append a step to the named or active plan, optionally with an executable verify command. |
| `plan verify ID [--plan NAME] [--timeout N]` | Run a step's own verify command and record the evidence scoped to that step, satisfying the strict-evidence gate. |
| `plan list` | List plans with active marker and progress. |
| `plan show [NAME]` | Full detail of the named or active plan. |
| `plan switch NAME` | Set the active plan pointer. |
| `plan archive [NAME]` | Move a finished plan to the archive. |
| `step ID STATUS [RESULT] [--plan NAME]` | Update a step; `completed` and `failed` require RESULT evidence, and `completed` requires a passing exit-0 `verify run` matching any stored `verify_command` by default. |
| `memory set KEY VALUE [--category C]` | Store or overwrite a memory entry. |
| `memory get [QUERY] [--category C]` | Substring search over keys and values. |
| `memory clear [KEY] [--all]` | Remove one entry, or everything with `--all`. |
| `lesson add TITLE DETAIL [--tags a,b] [--global]` | Record a project lesson, or a global one. |
| `lesson list [--tag TAG] [--scope project\|global\|all]` | List lessons, labeled by scope. |
| `logs compact [--keep N] [--dry-run] [--json]` | Archive raw verification and reflection logs, then keep recent active records. |
| `verify run COMMAND [--claim TEXT] [--timeout N]` | Execute a check and record the verdict. Exit 0 verified, 2 unverified. |
| `verify claim CLAIM EVIDENCE` | Record a self-attested claim, marked as such. |
| `reflect [JSON]` | Record a structured reflection from a JSON object. |
| `reflect --action A --outcome O --observation OBS --next N [--root-cause R] [--lesson L]` | Record a reflection from flags; a `--lesson` is auto-saved as a project lesson. |
| `summary` | Full session report: plans, memory, lessons, verifications, reflections. |

## MCP note

Clients using the Mythify MCP server instead of the CLI get the same contract
through exactly 41 tools: `classify_task`, `host_model_switch`,
`provider_probe`, `local_model_run`, `host_cli_probe`, `host_cli_run`,
`execution_probe`, `execution_run`, `lifecycle_probe`, `outcome_start`, `outcome_check`,
`outcome_status`,
`outcome_results`, `outcome_stop`, `memory_store`, `memory_recall`,
`memory_clear`, `lesson_record`, `lesson_recall`, `plan_create`,
`plan_add_step`, `plan_update_step`, `plan_status`, `workflow_status`,
`verification_history`, `work_report`, `background_status`, `evidence_harness`, `outcome_progress`,
`release_readiness`, `fanout_timeline`, `phase_status`, `campaign_next_prompt`,
`prompt_packet`, `workflow_route`, `verify_run`, `verify_claim`, `reflect`, plus the parallel delegation tools `fanout_start`,
`fanout_status`, and `fanout_results`. Same
state directory, same file formats, same evidence rules:
`verify_run` and `outcome_check` execute and record; `verify_claim` only attests;
`plan_update_step` refuses `completed` or `failed` without a `result`.
`campaign_next_prompt` and CLI `campaign prompt` render read-only host prompt
material for the current campaign task and phase; they do not mutate state, run
checks, advance tasks, or turn prompt output into verification evidence.
`prompt_packet` and CLI `prompt` render the same material-only packet contract
for research, analysis, failure recovery, handoff, review, campaign, and
next-prompt routing without mutating state or recording evidence.
`workflow_route` and CLI `route` are read-only quarterback surfaces: they
classify the prompt, inspect active durable state and the latest executed
verification, choose the next workflow route, return the suggested next command
and prompt packet, and keep the initiating host chat as the executor unless the
user explicitly hands work elsewhere. For independently parallel candidates,
they also return a native `claude-ultracode` adapter contract. An MCP host can
launch exactly one Claude dynamic workflow with `fanout_start`, monitor it with
`fanout_status`, and ingest its final material with `fanout_results`. The
adapter requires Claude Code 2.1.203 or newer, preserves host-owned permissions,
and never treats workflow output as verification evidence. Both runtimes also
read godplans and godaudits artifacts (`.godplans/PLAN.mdx`,
`.godaudits/AUDIT.mdx`, with `.md`
fallbacks) as project state: routing, `release_readiness`, and
`evidence_harness` surface their status, task progress, open Critical
findings, and counter drift, and the CLI `plan import` command converts their
checkbox tasks into a Mythify plan. Mythify never writes those artifacts.
Outcome loops are host-supervised and stored in `.mythify/outcomes/`: make a
bounded attempt, call `outcome_check`, then report success, retry, or stop.
`host_model_switch` records intended host chat changes, host confirmation
fields, and adapter proof scan fields, but does not mutate or confirm the host
unless it exposes that capability. `provider_probe` can probe a configured
OpenAI-compatible provider. `local_model_run` can run reader or triage prompts
against a localhost OpenAI-compatible provider, writing no state and returning
model output as material, not verification evidence. `host_cli_probe` can probe
Kimi Code, OpenCode, or Antigravity CLI availability with version and help
commands. `host_cli_run` can run bounded Kimi Code, OpenCode, or Antigravity
non-interactive prompts, writing no state and returning worker output as
material, not verification evidence. Antigravity requires explicit `cwd` and
does not pass permission-bypass flags. `execution_probe` can probe Google Colab CLI
availability without provisioning remote runtimes or accelerators. `execution_run`
can run a guarded Google Colab CLI ephemeral job through `colab run` only when
billing, data movement, and cleanup acknowledgements are explicit; it writes no
state and returns remote output as material, not verification evidence.
`lifecycle_probe` can probe
Google Agents CLI and ADK CLI availability with version, help, and eval-help
commands without scaffolding projects, running evals, deploying, publishing,
mutating cloud resources, or writing project state. Probe output is material,
not verification evidence. `verification_history` shows recorded executed and
attested evidence without rerunning checks or upgrading attested claims.
`work_report` shows chat-ready progress from durable plan, step, verification,
and reflection events and advances a cursor unless called in peek mode. Its
`mark` option advances the cursor to the latest event without showing old
events, which is useful before starting a chat-visible work session. `mark`
cannot be combined with `since`.
`outcome_progress` shows active and recent outcome loop progress from durable
goal and iteration records without running checks, making attempts, stopping
loops, or treating notes as verification.
`release_readiness` shows required release gate rows from recorded executed
verifications, roadmap state, and project git state without rerunning gates or
declaring the release safe.
`fanout_timeline` shows durable worker task timing, status, errors, and output
metadata without mutating state or treating worker output as verification
evidence. `evidence_harness` shows the active steering state, evidence mix,
attention items, delegated work counts, release readiness, and next control
action without mutating state or treating worker output as verification
evidence. `phase_status` groups active plan steps into
Understand, Design, Build, Judge, and Verify using durable state only; it does
not mutate state or treat model confidence as progress. `classify_task` mirrors CLI triage and model
policy, including `model_profile` and nonnegative `failure_count`. The returned
`model_router` uses the shared capability profiles and bounded escalation rules;
provider resolution never implies cross-provider fallback, and model review
never becomes verification evidence. Fanout workers accept `engine`, `model`, `effort`, `speed`, and
`role`; stronger non-review workers require `spawn_ceiling: "allow_stronger"`
when tier is known. A stronger reviewer requires a task with
`role: "reviewer"` plus `reviewer_allow_stronger: true`, or the broader
`spawn_ceiling: "allow_stronger"` opt-in.

Delegation discipline for fanout:

- Fan out only genuinely independent tasks. If one task needs another's
  output, run them yourself in sequence.
- Write every task prompt to stand alone: the worker has no memory of your
  conversation. Pass files through `context_paths`, never by reference.
- Fanout results are material, not verification. Merge them, then verify the
  merged work with `verify run` or `verify_run`.
- Each task is a fresh model call that costs real money or subscription
  quota. Do not fan out work you can do inline.
