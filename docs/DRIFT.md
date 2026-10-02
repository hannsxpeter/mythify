# Drift log

Drift is a place where two parts of Mythify disagree: a doc and the code, a
help string and the handler, a mirror and its source. It matters more than it
looks, because an agent follows whichever text it read last, so two sources
that disagree produce two behaviors.

This log records the drift found in the 2026-10-01 review of Mythify 5.8.0
and while building 6.0.0, and how 6.0.0 resolved each item. Historical rows
name the 5.x files and tools as they were.

How drift is caught from 6.0.0 on:

- `python3 scripts/lint.py` is the maintainer gate. Its checks: `version`
  (the version agrees everywhere it is written), `generated` (AGENTS.md,
  CLAUDE.md, the protocol digest, and docs/commands.md match their
  generators), `protocol-budget` (the protocol spine stays under its byte
  budget), `text` (no en or em dash, no emoji or symbol code points),
  `prose` (the prose checker), `model-agnostic` (no model names or removed
  routing identifiers anywhere, no vendor names in shipped product surfaces),
  `dependencies` (standard library only), `mcp-surface` (every MCP tool maps
  to a parser leaf, and the `mythify` tool lists the rest), `links` (Markdown
  links resolve), and `source-size` (the per-file ceiling).
  `tests/test_lint.py` proves each check can fail.
- Facts are derived from code instead of copied: MCP tools and the escape-hatch
  command list come from the argparse tree, and docs/commands.md is generated
  from it.
- One runtime. There is no second implementation to keep in parity.

To log new drift, add a row to the matching section with the file and line
where it was found, what the text claimed against what the code does, and
the change that resolved it. Drift that is known and not yet fixed goes under
"Open at 6.0.0" until a release resolves it.

## Contents

1. Install and runtime
2. Evidence and exit codes
3. Views and readiness
4. Routing and model policy
5. Protocol, drop-ins, and generated files
6. MCP surface
7. Release and policy docs
8. Removed features
9. Found while building 6.0.0
10. Found by the 6.0.0 lint sweep
11. Open at 6.0.0

## 1. Install and runtime

| Where | Drift | Resolution |
| --- | --- | --- |
| README.md quickstart (5.8) | Said Node 20+ was optional, "no `npm install`", and "zero-dependency Python plus one small optional Node server". The default `install_user.sh --project` required `node`, `npm`, and `tar`, ran `npm pack` and `npm install --omit=dev`, and fetched the MCP SDK and zod from the registry. The server was about 20,000 source lines. | The Node server is deleted. `mythify mcp` is a standard-library Python server, the installer needs only `python3`, and `--skip-mcp` is an accepted no-op. |
| mcp-server/README.md | Told users to `npm install .../mythify-mcp-5.5.0.tgz` while the package was 5.8.0. No check read the file. | `mcp-server/` is deleted. |
| MCP server vs CLI state resolution | Node created `<cwd>/.mythify` lazily on first write; the CLI failed with "No .mythify workspace found"; with `MYTHIFY_DIR` set the CLI created the layout eagerly. The two behaved differently before `init`. | Every MCP call runs the CLI, so there is one resolution rule. |
| MCP `verify_run`, `outcome_check`, `blast_radius_review_prove` | Ran commands in the MCP server's working directory, which a desktop host may set to a minimal directory, not the project root that `MYTHIFY_DIR` implied. | The Python server starts every CLI call in the project root: the parent of `MYTHIFY_DIR`, else the discovered workspace's parent. |
| ci.yml and release.yml | `actions/setup-node` pinned to a 7.0.0 SHA with a `# v6` comment. | Node steps are gone from both workflows. |
| mcp-server/package.json | Two MCP client SDK generations shipped as devDependencies for tests. | No npm package remains. |

## 2. Evidence and exit codes

| Where | Drift | Resolution |
| --- | --- | --- |
| CLAUDE.md `verify run` row | "Exit 0 verified, 2 unverified." argparse usage errors also exited 2, so a typo read as a failed check, and the MCP server parsed stderr to tell them apart. | Usage errors exit 64 (`MythifyArgumentParser`). The MCP server treats only exits 0 and 2 as results. |
| scripts/mythify.py docstring, CLAUDE.md `init` row | "Global lessons live in ~/.mythify/lessons and are independent of project state" and init is "safe to re-run". Once `~/.mythify` existed, discovery used it as the workspace for every uninitialized project under HOME, and `init` there did nothing. | Discovery and the MCP root search skip `~/.mythify`. `init` in HOME itself exits 1. `MYTHIFY_DIR` can still name it. |
| protocol/PROTOCOL.md, docs/design.md, README.md | Called a blast-radius review immutable. `lineage attach review NAME` rewrote it, changing its revision digest, so `review show` dropped the proofs linked to the old revision. | `lineage attach` refuses kinds `review` and `verification`. |
| SECURITY.md (5.8) | Said `MYTHIFY_DISABLE_RUN=1` disables command execution. The MCP host CLI runner, host CLI probe, and lifecycle probe spawned processes without checking it. | Those adapters are removed. `verify run`, `plan verify`, `map verify`, `outcome check`, `outcome run`, `review prove`, and `product measure` all refuse under the switch, and MCP tools run the CLI. |

## 3. Views and readiness

| Where | Drift | Resolution |
| --- | --- | --- |
| docs/design.md, `readiness` help, CLAUDE.md | Presented `readiness` as a general release-readiness view. It loaded Mythify's own 14 release gates from the installed script's directory, so every gate read "missing" in a user project, and a fresh `init` read "blocked" because the new `.gitignore` made the tree dirty. | `readiness` and `protocol/release-gates.json` are removed. Maintainers release with RELEASE-CHECKLIST.md and `scripts/lint.py`. |
| docs/start-here.md workflow 4 | Recommended `verify run "mythify readiness --json"` as release evidence. `readiness` always exited 0, so the check could not fail. | Removed with `readiness`. |
| readiness roadmap summary | Took the first `- [` line regardless of its box, so "- [x] No open roadmap items remain." reported "active slice found". | Removed with `readiness`. |
| harness next action | Told CLI users to inspect `fanout_timeline` or `fanout_results`, which were MCP tool names; the CLI never wrote fanout state. | `harness` is removed. Its attention detectors run in `status`, whose next action names CLI commands. |
| phase view | Per-phase "evidence" lines were global counts unrelated to the steps in the phase, and grouping was keyword matching. | Removed. |
| scripts/mythify_views_status.py docstring | Described readiness, timeline, and phase; most of the file was the harness detectors. | Module deleted. The detectors live in `mythify_views.py` under the `status` docstring. |
| CLAUDE.md `status` and `summary` rows | `status` was described as plan, next step, and counts, but also printed the active outcome and map, and had no `--json`. `summary`, the "full session report", omitted outcomes and maps. | `status` help lists everything it shows and adds `--json` and `--recent`. `summary` lists plans, outcomes, maps, and products and adds `--json`. |
| surface-manifest.json front door | Paired CLI `status` with MCP `workflow_status`, which was the dashboard builder with different fields and no map section. | The MCP `status` tool runs CLI `status`. |

## 4. Routing and model policy

| Where | Drift | Resolution |
| --- | --- | --- |
| CLAUDE.md, PROTOCOL.md, README.md | Said `model_router` selected provider-neutral profiles. It wrote concrete vendor model ids into `provider_resolution`. | Model routing, policy, and triage are deleted. The lint `model-agnostic` check scans for model and vendor names; its denylists in `scripts/lint.py` are the only copy. |
| scripts/mythify_model_routing.py docstring | "Provider-neutral execution topology", while it matched and embedded one vendor's workflow adapter engine. | Module deleted. |
| README.md, CLAUDE.md native adapter text | Said the adapter failed closed below a host version. `classify` and `route` recommended it with no platform or version check; only the MCP fanout launch checked the version. | Adapter and fanout removed. `route` reports `parallelism` with `chooser: "host"`. |
| mythify_classification.py, `classify` row | Called classification deterministic, but `classify` output included engine fields that depended on binaries on `PATH` and environment variables. | `classify` is removed. The `classification` block in `route --json` depends only on the prompt and `protocol/classification-rules.json`. |
| `route` help, docs/start-here.md, skills | Listed routes without `map`, and with `research`, `campaign`, and `prompt`. | `route` help, `protocol/workflow-router.json`, and the skills name the same eight ids: direct, plan, map, product, outcome, review, failure_recovery, handoff. |
| mythify_router.py research terms | A second hardcoded research term list beside the manifest's research task type. | The research route is removed. |
| `classify` help for `--platform` and `--speed` | `--platform` listed three of eight accepted values; `--speed` claimed to enable a vendor fast mode that only one triage engine used. | The flags are removed. |
| parser epilog "Labs surfaces" | Listed provider probes, local model runs, host CLI workers, and lifecycle probes, none of which were CLI commands. | The epilog lists real commands only. |

## 5. Protocol, drop-ins, and generated files

| Where | Drift | Resolution |
| --- | --- | --- |
| CONTRIBUTING.md, PR template | Named CLAUDE.md, AGENTS.md, and .cursorrules as the generated files. `build_variants.py` also wrote `protocol/variants/*.thin`, and CI diffed only the three root files. | Thin variants and `.cursorrules` are gone. `build_variants.py --check` covers every file it writes, in CI and in the lint `generated` check. |
| install_user.sh `--protocol-profile auto` | Said `auto` "fails closed to full". It mapped to `full` unconditionally; nothing detected a capability. | The option and loading profiles are removed. |
| docs/design.md layout | Called `loading-profiles.json` generated; it was hand-written input. | Removed. |
| scripts/mythify_protocol.py | Required a hand edit of `RELEASE_GATES_SHA256` on every release, because `release-gates.json` embedded the version. | The manifest and its pin are deleted. |
| CLAUDE.md quick reference | Covered 1 of the 5 `trace` subcommands, 4 of the 7 `research` subcommands, and 6 of the 10 `campaign` subcommands. | Those commands are removed, and docs/commands.md is generated from the parser. |
| protocol/PROTOCOL.md, skills/mythify/SKILL.md | Promised MCP clients "the same contract" and full interop, while many CLI commands had no MCP tool and were not declared CLI-only. | The `mythify` escape-hatch tool runs every command except `mcp`, and its description lists the commands without a typed tool, generated from the parser. |

## 6. MCP surface

| Where | Drift | Resolution |
| --- | --- | --- |
| check_surface_manifest.mjs | Pinned the prose "through exactly 63 tools", so every tool change needed hand edits in docs and the checker. | The checker is deleted. Tool facts come from the parser, and the lint `mcp-surface` check maps each tool to a parser leaf. |
| tests/test_tool_profiles.py, tool-profiles.test.js | Hardcoded copies of the profile and tool counts. | Tool profiles and both tests are deleted. |
| docs/design.md `tool_profile_status` | Reported description bytes only (17,074 bytes for 62 tools) while input schemas added 51,394 bytes, understating context cost about fourfold. | Profiles are removed. The 6.0 listing, schemas included, measures about 30 KB. |
| CLAUDE.md memory guidance vs tool profiles | Told agents to recall memory at session start, but the memory and lesson tools were only in the `full` profile. | `memory_set`, `memory_get`, `lesson_add`, and `lesson_list` are typed tools in the only tool set. |
| README.md tool budgets link | Said per-profile budgets were in docs/design.md, which had none. | Removed with profiles. |

## 7. Release and policy docs

| Where | Drift | Resolution |
| --- | --- | --- |
| roadmap.md | "Release gate: pending for `v5.8.0`" after v5.8.0 shipped. `check_surface_manifest.mjs` accepted only that sentence or a "passed" one, so the file stayed stale. | The checker is deleted. ROADMAP.md is the product record rendered by `mythify product show --markdown`. |
| docs/release.md | "Current release target: `v5.8.0`" after the tag, and the gate block omitted the release-asset checksum check that readiness required. | RELEASE-CHECKLIST.md replaces it and runs the checksum build and `--check` before the tag. |
| docs/release.md npm tarball list | Listed the runtime manifests the Node package loaded and missed two it read. | The npm package is gone. |
| docs/design.md versioning | "This is Mythify v5.0.0" while the CLI was 5.8.0; versions 5.1.0 to 5.8.0 were missing. | docs/design.md is replaced by docs/architecture.md, which states no version. The version lives in `scripts/mythify.py`, and the lint `version` check compares the other places it appears. |
| docs/design.md state layout and repo facts | Omitted `designs/`, `reviews/`, and `verification-artifacts/`; quoted a `.gitignore` entry that did not match; quoted a stale skill description; placed functions in the wrong files. | Replaced by docs/architecture.md, whose layout follows `STATE_SUBDIRECTORIES` in the code. |
| SECURITY.md | Listed 5.x, 4.x, 3.x, and 2.x as supported, while only `main` received fixes. | 6.x only. |
| docs/README.md "Drift rules" | Advisory prose with no log and no mapping from a rule to the check that enforces it. | This file, plus `scripts/lint.py`. |

## 8. Removed features

These drifted in 5.8 and left with the feature.

| Where | Drift | Resolution |
| --- | --- | --- |
| `plan create --design` help | "Approved design record this plan implements." Any string was stored, with no existence or approval check, and nothing read the approval. | `design` and `--design` are removed; `product` replaces design records. |
| docs/design.md lineage | Described a "fresh-parent gate" no command declared, and a precedence order nothing enforced. | Lineage is documented as advisory status only. |
| `campaign` routing text | Promised "evidence-gated advancement"; `campaign task N completed` skipped verification. | `campaign` is removed. |
| workspace config docs | Implied local config could not weaken frozen paths or isolation; nothing outside the module read the file. | `workspace` is removed. |
| `eval adopt` output, docs/design.md | Printed a command for `local_model_eval.py`, which no install shipped, and claimed eval evidence survived `logs compact` when only the active log was read. | `eval` and the local harness are removed. |
| docs/artifact-hygiene.md | Listed `explicit_provenance_fields` as actionable; no code read them. | `artifact` is removed. |

## 9. Found while building 6.0.0

| Where | Drift | Resolution |
| --- | --- | --- |
| `protocol check` (first 6.0 cut) | Accepted any CLAUDE.md with an `@AGENTS.md` line and ignored the rest, so a rule written beside the import passed with exit 0. | A pointer must equal `pointer_copy()` apart from trailing whitespace and line endings; anything else is `pointer_drift`. |
| `product measure` (first cut) | A `verify run --parent product:NAME` with an "O1 measured: ..." claim counted as a measurement and could ship a bet. | Only records `product measure` stamps with `product` and `outcome_id`, running the outcome's current measure command, count. |
| MCP allowlist after the merges | The design kept a core typed set; merging the feature tracks brought it back to 63 typed tools. | `TOOL_ALLOWLIST` holds 37 paths; everything else goes through the `mythify` tool. |
| `review prove` no-op check | Refused `true` but accepted `/usr/bin/true`, `true` with arguments, and `: ignored`. | `noop_verifier_reason` flags a path-qualified `true` or `:` and arguments after them. |

## 10. Found by the 6.0.0 lint sweep

Found while writing `scripts/lint.py` and running it on the 6.0.0 tree, or
listed as open in the first draft of this log, and fixed before release.

| Where | Drift | Resolution |
| --- | --- | --- |
| scripts/mythify_parser.py `plan` help | "Manage plans: create, import, add-step, list, show, switch, archive." omitted `verify`. | The help and description list `verify`. docs/commands.md is generated from the parser, so the reference cannot omit a subcommand. |
| scripts/mythify_parser.py `outcome start --allowed-paths` | Said the CLI loop enforces scope and "a check fails if files change outside the scope". Only `outcome run` fails an iteration; `outcome check` adds the out-of-scope files to `next_action` and passes. | The help names which mode enforces and which reports. |
| README.md outcome loops, `outcome start --escalate-after` help | Said the loop stops after `--escalate-after` failures in a row and at a change outside `--allowed-paths`, beside an `outcome check` example. Only `outcome run` counts consecutive failures and stops on scope; `outcome check` does neither. | README.md and the help say which mode does what. |
| scripts/mythify_map_parser.py `map verify`, scripts/mythify_maps.py docstrings | Said the evidence is scoped to the ticket. The resolve gate accepts any passing executed run of the ticket's command recorded since the claim and does not read `ticket_id`. | The help and docstrings describe the gate as it runs: command-scoped, after the claim anchor. `map resolve` help already said so. |
| protocol/workflow-router.json | Carried per-route `priority` numbers and an `output_fields` list that no code read; the selection order lives in `select_workflow_route` and does not follow the numbers. Its description said it was shared with an MCP server. | Both keys are removed, the manifest is version 3, and the description names its one reader. |
| tests/test_model_agnostic.py, tests/test_protocol_variants.py, tests/test_install_user.py, tests/test_routes.py, tests/test_mythify.py | Five copies of the model and vendor denylist that disagreed: the runtime scan had no `mistral`, `qwen`, or `deepseek`; the spine scan had no `colab` or `llama`; the skills scan had no `fable` or provider profile names but alone banned `fanout`; route and classification tests spelled out removed keys. | One table in `scripts/lint.py` (`MODEL_NAMES`, `ROUTING_IDENTIFIERS`, `VENDOR_NAMES`). The tests import it, `tests/test_model_agnostic.py` is folded into `tests/test_lint.py`, and absence checks use `lint.removed_identifier_keys`. |
| .github/workflows/ci.yml ASCII step, CONTRIBUTING.md | The inline step exempted `docs/research-report.md`, which had no banned characters and is deleted, and CONTRIBUTING.md said the hygiene job rejected all non-ASCII text while the step banned only dashes, U+FE0F, and three symbol ranges. | The step is the lint `text` check, with no exemptions, and CONTRIBUTING.md and MAINTAINING.md name the exact code points it bans. |
| protocol/prose-quality.json | Excluded `docs/archive` and `docs/research-report.md`, both deleted, and did not scan MAINTAINING.md, RELEASE-CHECKLIST.md, or ROADMAP.md. | The dead exclusions are gone and the three maintainer docs are scanned. |
| README.md, docs/start-here.md vs scripts/package_cli.py | Told users to copy AGENTS.md into their project, but the CLI archive shipped neither AGENTS.md nor CLAUDE.md, and its README linked docs the archive did not contain. | The archive ships AGENTS.md, CLAUDE.md, and every doc a shipped doc links to; tests/test_install_user.py checks that each link resolves inside the archive. |
| `review blast-radius`, `review prove`, `review show`, `lineage attach`, `lineage status` arguments | Fourteen arguments had no help, so their MCP schemas and the generated reference showed bare names. | Each has help text; docs/commands.md shows it. |
| MAINTAINING.md, CONTRIBUTING.md lint tables (first draft) | Written from the plan before `scripts/lint.py` existed: `text` was described as rejecting any non-ASCII character, `protocol-budget` as covering only PROTOCOL.md, and `tests/test_model_agnostic.py` as an enforcer. | The tables describe the checks as implemented, including the `--check`, `--json`, and `--root` options. |

## 11. Open at 6.0.0

None known. New drift found after release goes here until a release resolves
it.
