# HumanLayer approaches Mythify should integrate

Research date: 2026-08-17

## Outcome

Mythify should not adopt HumanLayer as a framework or copy its workflow wholesale.
It should integrate four ideas first:

1. Reduce the always-loaded instruction footprint through measured progressive
   disclosure.
2. Add an optional design runway for work where maintainability, interfaces, or
   product behavior are the main risks.
3. Make plans explicitly vertical and phase-aware instead of relying on title
   heuristics and a generic 20-step template.
4. Extend verification backpressure so full diagnostic output remains durable while
   successful output stays compact.

Mythify should then test three more ideas: typed artifact precedence, an advisory
maintainability review, and narrow design alternatives at high-leverage seams.

This is an adaptation agenda, not a replacement agenda. Mythify already has stronger
evidence discipline than the HumanLayer material reviewed here: exit-code verification,
step-scoped evidence, durable workflow state, bounded outcome loops, and a clear rule
that delegated output remains material until the integrated result is verified.

## Scope and method

The research used current primary sources from the [HumanLayer website](https://www.humanlayer.dev/),
the [HumanLayer GitHub organization](https://github.com/humanlayer), official product
documentation, engineering essays, and local inspection of eight repositories:

| Repository | Role in this review | Maturity signal | License signal |
| :--- | :--- | :--- | :--- |
| [humanlayer](https://github.com/humanlayer/humanlayer) | Product, workflow skills, repository conventions, quiet command wrappers | Active product repository | Apache-2.0 |
| [12-factor-agents](https://github.com/humanlayer/12-factor-agents) | Deterministic agent design philosophy | Widely used explanatory guide | Apache-2.0 |
| [skills](https://github.com/humanlayer/skills) | Reusable workflow and control-loop patterns | Small, focused skill registry | MIT |
| [advanced-context-engineering-for-coding-agents](https://github.com/humanlayer/advanced-context-engineering-for-coding-agents) | RPI, context engineering, and software-factory critique | Essay and workshop material | No root license found during inspection |
| [fold](https://github.com/humanlayer/fold) | Provider-neutral agent core, hooks, logs, skills, subagents, compaction | Early but coherent implementation | MIT |
| [agentcontrolplane](https://github.com/humanlayer/agentcontrolplane) | Earlier control-plane architecture and historical instruction style | Alpha and partly superseded in philosophy | Apache-2.0 |
| [rpi-coordination-template](https://github.com/humanlayer/rpi-coordination-template) | Shared multi-repository coordination and conditional instructions | Template repository | No root license found during inspection |
| [mcp-cli](https://github.com/humanlayer/mcp-cli) | CLI-first access to MCP servers | Minimal, early repository | No root license found during inspection |

Repository content was treated as observed implementation, not as a universal
recommendation. Current official documentation was given more weight than old files.
Ideas from repositories without a discovered license should be independently
implemented from the described behavior, not copied.

## Philosophy and observed practice

### 1. Context is a controlled resource

HumanLayer's central claim is that agent quality depends on the context selected for a
particular decision, not on filling the largest available window. Its
[advanced context engineering](https://www.humanlayer.dev/blog/advanced-context-engineering)
material evaluates context by correctness, completeness, size, and trajectory. The
associated RPI pattern uses research, planning, and implementation as separate context
shapes. The [long-context essay](https://www.humanlayer.dev/blog/long-context-isnt-the-answer)
adds an important distinction: a larger context window does not create a proportionally
larger instruction budget.

Observed practice:

- The current `humanlayer` repository keeps its root agent instructions brief and
  points toward commands and deeper artifacts.
- Workflow skills load detailed instructions only when the workflow needs them.
- The [context-forking essay](https://www.humanlayer.dev/blog/context-forking-to-save-time-trouble-and-tokens)
  recommends branching before noisy operations or competing design explorations.
- The [fold repository](https://github.com/humanlayer/fold) implements event logs,
  skills, subagents, hooks, and automatic compaction as separate context controls.

Implication for Mythify: durable state is already strong, but the host bootstrap is
not context-efficient. `AGENTS.md`, `CLAUDE.md`, and `.cursorrules` each contain the
full protocol at about 4,625 words, while `skills/mythify/SKILL.md` is another 2,521
words. A host normally loads only its own instruction file, but that file still carries
the complete command reference into trivial tasks.

### 2. Reliable agents are mostly deterministic software

The [12-factor agents guide](https://github.com/humanlayer/12-factor-agents) argues for
owned prompts, owned context, structured tool outputs, explicit control flow, compact
errors, pause and resume APIs, small focused agents, and reducer-like state transitions.
Its most reusable principle is that an LLM step belongs inside a deterministic system,
not the other way around.

Observed practice:

- `agentcontrolplane` models LLMs, agents, tools, tasks, and tool calls as small core
  objects rather than hiding them behind a large framework.
- `fold` uses a log-based state model and provider-specific tool selection behind a
  common execution core.
- HumanLayer workflows make user checkpoints explicit at product and design decisions.

Implication for Mythify: keep the existing command-first architecture, durable JSON
state, explicit exit-code checks, and host-owned control. These are strong alignments,
not gaps.

### 3. Design review is earlier and more valuable than code review

The current [software factory critique](https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/wsff.md)
argues that passing tests are not a fast oracle for maintainability. It separates four
high-leverage concerns:

- Product design: the user problem, behavior, and success condition.
- System design: contracts, schemas, queues, stores, and service boundaries.
- Program design: call shape, types, method signatures, file changes, and test seams.
- Vertical slices: runnable, visible, or queryable tracer bullets that prove one path.

Observed practice:

- The [workflow phases reference](https://docs.humanlayer.com/explanation/workflow-phases)
  selects the shortest workflow that controls the main risk. It offers one-shot, RPI,
  PRD-oriented, and freeform paths.
- The [skills and workflows guide](https://docs.humanlayer.com/guide/skills-workflows)
  keeps current-state research separate from future-state design and asks structure
  outlines to name vertical results, files, and automated and manual checks.
- The `humanlayer` planning skill requires current and desired states, non-goals,
  implementation phases, and explicit success criteria.
- The `fold` design skills emphasize deep modules, narrow interfaces, seam placement,
  the deletion test, and comparing substantially different interfaces before choosing
  one for a high-leverage boundary.

Implication for Mythify: the current generic planning horizon is broad and useful, but
it mixes understanding, build tasks, and verification into a fixed sequence. It does
not encode product, system, and program design as distinct optional artifacts, and its
phase view often infers phase from titles.

### 4. Research describes reality, design describes intent

HumanLayer gives research a narrow contract: describe the current implementation with
specific code references, without mixing in critique or proposed changes. A later
design artifact owns the desired state. The current workflow documentation also states
that live code wins for current behavior and later artifacts win when desired-state
documents conflict.

Observed practice:

- The `research_codebase` command in `humanlayer` explicitly excludes recommendations.
- Planning commands require named files to be read fully and assumptions to be checked
  against live code.
- Create and iterate skills preserve current decisions in the artifact before the
  context changes.

Implication for Mythify: its research ledger, map decisions, and plans are durable, but
their relationships are implicit. A plan does not declare which research record or map
decision it supersedes, and the artifact chain has no machine-readable precedence.

### 5. The harness is part of the coding agent

The [harness engineering essay](https://www.humanlayer.dev/blog/skill-issue-harness-engineering-for-coding-agents)
treats the model and harness as one system. It favors familiar CLIs where they provide
better composition than a large MCP tool surface, progressive disclosure through
skills, subagents for context isolation, and hooks for deterministic control flow. It
also warns that oversized or automatically generated instruction files can lower
performance.

Observed practice:

- HumanLayer moved some repeated service interaction from a broad MCP surface to a
  small CLI.
- Its skills registry treats reusable procedures like auditable packages.
- Subagents are assigned bounded context and work, not personalities.
- Current root instruction files are concise, while an older `agentcontrolplane`
  instruction file is much larger and contains roleplay and arbitrary code quotas.

The historical inconsistency matters. Current guidance is more credible than the old
file because it reflects a later philosophy and the active product repository. It also
shows that not every artifact in the organization should be copied.

Implication for Mythify: the CLI is already the primary reproducible surface, and the
MCP server mirrors it. The next question is not whether to add more tools. It is whether
at the research baseline, all 50 MCP tools and their descriptions needed to be visible to every host session.

### 6. Verification output should apply context-efficient backpressure

The [backpressure essay](https://www.humanlayer.dev/blog/context-efficient-backpressure)
recommends concise success output, complete failure evidence, fail-fast behavior, and
preserved exit semantics. The `humanlayer` repository applies this through a shell
helper used by Make targets.

Mythify is already close. `verify run` captures stdout and stderr, prints only a compact
success record, prints output tails on failure, enforces time and output bounds, and
records the evidence. The remaining gap is that the full temporary output is deleted.
For a failure larger than the tail, later diagnosis cannot inspect the original output
without rerunning the command.

### 7. Isolation should match the unit of work

The [workspaces guide](https://docs.humanlayer.com/guide/workspaces) uses per-task
worktrees, a shared workspace file, local overrides, and optional multi-repository
layouts. The [subagent model reference](https://docs.humanlayer.com/reference/subagent-models)
separates model choice from reasoning effort and uses cheaper children for bounded
research where appropriate.

Implication for Mythify: fanout isolation, model profiles, and multi-repository suite
coordination already cover the hard mechanics. A small shared and local workspace
configuration could simplify ordinary multi-repository use, but this is not a top
quality gap.

## Mythify crosswalk

| HumanLayer pattern | Mythify today | Decision | Reason |
| :--- | :--- | :--- | :--- |
| Deterministic control around LLM steps | CLI commands, explicit state transitions, bounded loops | Keep | Mythify is already strongly aligned. |
| Executed evidence over model confidence | `verify run`, step-scoped gates, material-only fanout | Keep | This is stricter than the reviewed HumanLayer examples. |
| Proportional workflow choice | `route`, `classify`, fast, plan, map, research, outcome, campaign | Keep | The router already avoids one universal workflow. |
| Frequent intentional context shaping | Durable state exists, full host protocol always loads | Adapt | Preserve durability while shrinking always-loaded instructions. |
| RPI separation | Research, maps, and plans exist but have implicit lineage | Adapt | Add typed artifact links and precedence without forcing RPI on small tasks. |
| Product, system, and program design | Generic plan horizon and heuristic phase grouping | Add selectively | Use only when design risk justifies the ceremony. |
| Vertical tracer-bullet slices | One minimal-slice step, then horizontal happy-path and edge-case steps | Adapt | Make vertical results first-class and verifiable. |
| Quiet success, detailed failure | Compact success and failure tails | Adapt | Persist full bounded output for diagnosis and audit. |
| CLI-first tool composition | Strong CLI plus a 50-tool MCP surface | Keep and measure | Audit tool visibility before adding or removing anything. |
| Design alternatives at important seams | Maps can hold decisions, no explicit interface comparison artifact | Add experimentally | Useful for high-cost boundaries, wasteful as a universal gate. |
| Per-task worktrees and multi-repository workspaces | Fanout isolation and suite coordination | Keep, simplify later | The capability exists, but everyday configuration could improve. |
| Model-judged maintainability | Independent review exists, executable evidence remains authoritative | Add as advisory | Maintainability needs judgment, but judgment must stay second-class. |
| Roleplay personas and arbitrary quotas | Not part of Mythify protocol | Reject | They consume context and do not encode a verifiable engineering constraint. |
| Fully autonomous lights-off factory | Bounded host-supervised loops | Reject | Tests cannot prove product fit or long-term maintainability. |

## Prioritized integrations

### Priority 1: Thin host bootstrap with measured progressive disclosure

Goal: reduce the context spent on instructions before the task begins while preserving
every safety and evidence rule.

Smallest integration:

1. Keep `protocol/PROTOCOL.md` as the canonical, complete contract.
2. Generate a concise host bootstrap containing identity, absolute safety rules,
   proportional ceremony, routing, verification doctrine, and pointers to the skill
   and command reference.
3. Keep a full generated variant for hosts that cannot load skills or referenced files.
4. Select the variant during installation based on proven host capability.
5. Benchmark the thin and full variants with the existing local evaluation harness
   before changing the default.

Why first: instruction pressure affects every task, while the change can be isolated to
generation, installation, and evaluation. The current source and hash discipline can be
retained.

Acceptance checks:

- The thin bootstrap cuts always-loaded words by at least 50 percent.
- Existing safety, destructive-action, verification, and completion-claim scenarios do
  not regress in the evaluation harness.
- `protocol check` proves which source and variant produced every installed file.
- A host without progressive disclosure receives the full protocol automatically.

Risk: a short file can omit a rule that a host cannot discover later. Mitigate with a
capability-gated fallback and behavior benchmarks, not a word-count target alone.

### Priority 2: Optional design runway and explicit vertical slices

Goal: add design attention where maintainability risk is high without imposing a large
workflow on routine changes.

Smallest integration:

1. Extend routing with three plan archetypes: direct, RPI, and design-heavy.
2. Allow a plan step to store an explicit phase enum instead of relying only on title
   classification.
3. Add optional design records for product, system, and program decisions. A map can
   remain the mechanism for unsettled questions.
4. Require design-heavy implementation steps to state a vertical result, affected
   files, automated checks, and manual checks.
5. Keep each slice runnable or observable before the next slice begins.

Why second: HumanLayer's most important quality argument is that architectural mistakes
become expensive before code review sees them. Mythify's current plan horizon includes a
minimal slice, but most implementation steps are organized by technical concern rather
than end-to-end result.

Acceptance checks:

- Router fixtures show small tasks still choose direct or fast execution.
- A design-heavy fixture produces explicit product, system, program, and vertical-slice
  phases.
- `phase` views use stored phase data when present and preserve backward compatibility.
- Every vertical slice has at least one executable or manual observation criterion.
- Plan import and CLI/MCP interop remain compatible with older steps.

Risk: more artifacts can become ceremony and stale prose. Use this path only when the
router identifies product, interface, migration, or maintainability risk.

### Priority 3: Durable, context-efficient verification output

Goal: keep the current compact success path while making failure diagnosis reproducible.

Smallest integration:

1. Preserve the current success line and failure tails.
2. Write bounded, redacted stdout and stderr artifacts under the verification record's
   durable directory.
3. Record artifact paths, byte counts, truncation, redaction, and content digests.
4. Add `--output compact|full` for deliberate inspection without rerunning the command.
5. Let common test adapters extract a test count for the compact success line, but do
   not parse output to decide pass or fail. Exit code remains authoritative.

Acceptance checks:

- A noisy successful command emits one compact status record.
- A noisy failure emits a tail and a valid path to the bounded full artifact.
- Sensitive-output redaction applies before durable storage.
- Output-limit, timeout, signal, and process-tree tests still pass.
- The verifier never uses `head` or another pipe that can replace the tested command's
  exit status.

Risk: durable logs can retain secrets or consume disk. Preserve current limits, redact
before writing, and integrate retention with `logs compact`.

### Priority 4: Typed artifact lineage and precedence

Goal: make it clear which facts and decisions an implementation is meant to follow.

Smallest integration:

- Let research, map, design, plan, outcome, and verification records declare parent
  artifact identifiers.
- Encode a simple precedence contract: live code owns current behavior, approved design
  owns desired behavior, the latest approved slice outline owns implementation order,
  and executable evidence owns completion.
- Surface stale lineage when a parent artifact changes after a child was approved.
- Show the chain in `dashboard`, `harness`, and final reports.

Acceptance checks:

- A plan created from a map records the map identifier and decision versions.
- A changed design marks dependent plans stale without silently rewriting them.
- Current-state research never overrides executable behavior found in live code.
- Old state files remain readable.

Risk: version graphs can become a second project-management system. Keep the first
version to parent identifiers, timestamps, digests, and stale-state warnings.

### Priority 5: Advisory maintainability backpressure

Goal: detect code that passes tests but creates unnecessary coupling, shallow wrappers,
wide interfaces, or hard-to-change seams.

Smallest integration:

- Add an optional review packet for design-heavy or refactor routes.
- Review the integrated diff against local design principles: interface depth,
  locality, seam count, deletion cost, invalid-state exclusion, and test validity.
- Record reviewer findings with `verify claim` or a distinct material review record.
- Keep executable tests, linters, builds, and runtime checks as the only completion
  evidence.
- Grow executable eval scenarios from recurring review failures where possible.

Acceptance checks:

- The review can fail or warn independently of unit tests.
- A model verdict never becomes `verified: true`.
- Findings cite concrete files and code locations.
- Repeated classes of finding produce proposed eval scenarios instead of permanent
  prose-only gates.

Risk: generic architecture advice can reward churn. Scope the review to changed seams
and require evidence of a concrete maintenance cost.

### Priority 6: Tool-surface budget and lazy capability discovery

Goal: ensure the MCP mirror does not spend context on capabilities irrelevant to the
current task.

Smallest integration:

- Measure token cost and selection accuracy for the current 50-tool MCP surface.
- Define capability profiles such as core workflow, execution adapters, lifecycle,
  artifacts, and fanout.
- Expose only core tools initially when the host supports tool discovery, then load a
  profile based on routing.
- Keep the complete CLI surface available for reproducible local workflows.

Acceptance checks:

- A benchmark compares full and profiled tool visibility on selection accuracy,
  completion rate, and token usage.
- Tool discovery does not weaken authorization or mutate state.
- CLI/MCP state interoperability remains unchanged.

Risk: hosts differ in discovery support. This recommendation is measurement-first and
must fall back to the current full surface.

### Priority 7: Experimental design alternatives and context forks

Goal: improve a small number of expensive interface choices before implementation.

Use this only for public APIs, persistence boundaries, cross-runtime contracts, or
hard-to-reverse abstractions. Ask for two or three materially different interfaces,
compare call-site complexity, locality, seam placement, test surface, migration cost,
and deletion cost, then record the chosen design in a map or design artifact.

Do not create several full implementations. The deliverable is a decision artifact and,
where useful, one minimal tracer bullet.

Acceptance checks:

- Alternatives differ in interface shape, not naming.
- Each comparison cites expected call sites and files.
- The selected design states what evidence would reverse the decision.
- The workflow is never selected for a routine local edit.

### Priority 8: Shared and local workspace configuration

Goal: simplify ordinary multi-repository work without changing suite coordination.

Adapt HumanLayer's shared workspace plus local override concept only if real Mythify
users repeatedly configure the same repository group. Keep worktrees isolated by task
and preserve the existing rule that workers cannot silently broaden allowed paths.

This is lower priority because Mythify already has the core isolation and coordination
mechanics.

## Suggested delivery sequence

### Increment A: Measure and reduce context pressure

- Add instruction-footprint metrics to the local eval harness.
- Prototype thin and full generated protocol variants.
- Benchmark both variants.
- Measure the 50-tool MCP surface before changing it.

Exit condition: context use improves without a measurable regression in safety or
verification behavior.

### Increment B: Improve design and plan shape

- Add explicit optional plan phases.
- Add plan archetypes and router fixtures.
- Add vertical-result, automated-check, and manual-check fields.
- Connect maps, designs, and plans with minimal lineage metadata.

Exit condition: a design-heavy fixture produces a reviewable design and runnable
vertical slices, while small-task routing remains unchanged.

### Increment C: Improve diagnostic and maintainability feedback

- Persist bounded redacted verifier artifacts.
- Add optional maintainability review records.
- Convert repeated review failures into executable eval proposals.
- Trial design alternatives on one real cross-runtime seam.

Exit condition: noisy failures are diagnosable without reruns, and maintainability
feedback remains clearly separate from executable proof.

## Do not copy

- Do not require RPI for every task. HumanLayer's current documentation itself chooses
  the shortest workflow that controls the main risk.
- Do not copy roleplay personas, arbitrary line-count targets, mandatory TODO quotas,
  blanket deletion percentages, or commit-frequency rules found in older repository
  instructions.
- Do not treat passing tests as proof of maintainability or product correctness.
- Do not turn every integration into an MCP tool. Prefer the CLI when composition,
  auditability, and local reproducibility are better.
- Do not hard-code model token limits or vendor assumptions into the protocol.
- Do not make every design checkpoint a manual approval. Preserve bounded autonomy and
  reserve human decisions for product intent, expensive interfaces, irreversible
  actions, and real scope changes.
- Do not copy source from a repository without verifying its license and attribution
  requirements. Several reviewed essay and template repositories had no root license.
- Do not adopt a HumanLayer product dependency to obtain these ideas. They can be
  implemented within Mythify's current CLI, MCP mirror, and durable state model.

## Overall recommendation

HumanLayer's most valuable contribution is a quality model: context selection shapes
reasoning, design review catches expensive mistakes earlier than code review, vertical
slices expose architecture under real use, and deterministic backpressure keeps an
agent honest without flooding its context.

Mythify already supplies the stronger execution substrate. The best integration is to
apply those ideas upstream and around that substrate:

1. Make the protocol cheaper to load.
2. Make design intent explicit when the risk warrants it.
3. Make implementation plans vertical and observable.
4. Make failure evidence durable and success evidence compact.
5. Treat maintainability judgment as advisory material that can seed future executable
   checks, never as a substitute for them.

That preserves Mythify's identity while addressing the main failure mode HumanLayer
describes: software that is locally correct, globally plausible, and progressively
harder to change.

## Primary sources

- [HumanLayer product site](https://www.humanlayer.dev/)
- [HumanLayer GitHub organization](https://github.com/humanlayer)
- [Workflow phases](https://docs.humanlayer.com/explanation/workflow-phases)
- [Skills and workflows](https://docs.humanlayer.com/guide/skills-workflows)
- [Workspaces](https://docs.humanlayer.com/guide/workspaces)
- [Subagent models](https://docs.humanlayer.com/reference/subagent-models)
- [Advanced context engineering](https://www.humanlayer.dev/blog/advanced-context-engineering)
- [12-factor agents](https://www.humanlayer.dev/blog/12-factor-agents)
- [Harness engineering](https://www.humanlayer.dev/blog/skill-issue-harness-engineering-for-coding-agents)
- [Context-efficient backpressure](https://www.humanlayer.dev/blog/context-efficient-backpressure)
- [Writing a good CLAUDE.md](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
- [Long context is not the answer](https://www.humanlayer.dev/blog/long-context-isnt-the-answer)
- [Context forking](https://www.humanlayer.dev/blog/context-forking-to-save-time-trouble-and-tokens)
- [Why Software Factories Fail](https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/wsff.md)
- [HumanLayer repository](https://github.com/humanlayer/humanlayer)
- [HumanLayer skills repository](https://github.com/humanlayer/skills)
- [Fold repository](https://github.com/humanlayer/fold)
- [Mythify repository](https://github.com/hannsxpeter/mythify)
