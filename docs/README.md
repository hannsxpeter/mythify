# Mythify Documentation

Start with [start-here.md](start-here.md). It is the shortest path from nothing
to a working evidence loop, and it assumes no prior knowledge of the project.

Come back here when you need the reference material.

## Where to go next

**I want to use Mythify.**
[start-here.md](start-here.md) covers the one happy path, the four workflows
worth learning, and what to safely ignore at first.

**I want the full command and tool surface.**
[design.md](design.md) is the complete reference: system architecture, every CLI
command, every MCP tool, the state model, and version notes.

**I want to know what has actually been measured.**
[evidence/efficacy-reproduction.md](evidence/efficacy-reproduction.md) holds the
reproducible bare-versus-Mythify smoke comparison, its sanitized result, and an
explicit statement of what the result does not show.

**I am cutting a release.**
[release.md](release.md) has the release gate, the package artifacts, and the
publish checks.

## Reference material

- [tool-use-contract.md](tool-use-contract.md): deferred-tool discovery
  discipline. Adapters must load a tool's real schema before calling it, and must
  never invoke from a guessed schema.
- [adapter-candidates.md](adapter-candidates.md): generated adapter capability
  registry output.
- [cli-to-model-runtime-migration.md](cli-to-model-runtime-migration.md): the
  migration path from drop-in CLI use to MCP and model-runtime integrations.
- [artifact-hygiene.md](artifact-hygiene.md): optional external
  watermarks-remover adapter, trust boundaries, finding normalization, and
  guarded cleaning contract.

## Host setup notes

- [claude-integrations.md](claude-integrations.md)
- [codex-integrations.md](codex-integrations.md)
- [desktop-tool-calls.md](desktop-tool-calls.md)
- [antigravity-mcp-setup.md](antigravity-mcp-setup.md)

## Open research and guarded future work

These describe work that is investigated but not promised. Treat them as notes,
not as product commitments.

- [host-apply-confirm-proof-watchlist.md](host-apply-confirm-proof-watchlist.md)
- [host-model-switching-research.md](host-model-switching-research.md)
- [local-llm-and-new-host-research.md](local-llm-and-new-host-research.md)
- [agents-cli-adk-spike-plan.md](agents-cli-adk-spike-plan.md)
- [colab-cli-spike-plan.md](colab-cli-spike-plan.md)
- [research-report.md](research-report.md)

## Archived

- `archive/codeaudit-2026-06-14.md`: completed code audit and remediation record.
- `archive/codeaudit-closed-2026-06-16.md`: the closed remediation tracker that
  previously lived at the repo root as `codeaudit.md`.
- `archive/roadmap-completed-2026-06-14.md`: completed roadmap history.
- `archive/release/`: historical v3 release-readiness and release-decision notes,
  superseded by `release.md`.

## Assets

`assets/banner.svg` and `assets/loop.svg` are the README's visual assets. They
are self-contained SVG with no external font or network dependencies, because
GitHub renders README images through `<img>`, which blocks external resource
loading. Both ship inside the standalone CLI archive so the packaged README is
not broken.

## Drift rules

- Keep current setup instructions in sync with the required drop-in files:
  protocol variant, `scripts/mythify.py`, adjacent `scripts/mythify_*.py`
  helpers, `protocol/operation-registry.json`,
  `protocol/classification-rules.json`, `protocol/model-capabilities.json`, and
  `protocol/workflow-router.json`, plus `protocol/artifact-hygiene.json` when
  the artifact adapter is installed.
- Keep first-run instructions focused on one happy path before listing advanced
  surfaces.
- Keep MCP public surface claims at 60 tools: 57 core tools plus 3 fanout tools.
  `protocol/surface-manifest.json` is authoritative; check it before restating a
  count anywhere.
- Keep `tool-use-contract.md` aligned with the `CLAUDE.md` and `AGENTS.md` MCP
  note and the capability-registry guardrails. It restates their discovery
  discipline; it does not diverge from them.
- Keep release claims aligned to `mcp-server/package.json`,
  `mcp-server/package-lock.json`, `CHANGELOG.md`, and the latest GitHub release.
- Keep marketing claims in `README.md` no stronger than
  `evidence/efficacy-reproduction.md` supports. The project's own rule applies to
  its own front page: executed evidence beats confident prose.
