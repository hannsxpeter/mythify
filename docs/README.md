# Mythify Documentation

This directory contains current product documentation plus archived historical
decision records.

## Current Docs

- `design.md`: system architecture, command and MCP surface, state model, and
  version notes.
- `start-here.md`: shortest happy path, three concrete workflows, and what to
  ignore at first.
- `release.md`: current release process, package artifacts, and publish checks.
- `adapter-candidates.md`: generated adapter capability registry output.
- `cli-to-model-runtime-migration.md`: migration path from drop-in CLI use to
  MCP and model-runtime integrations.
- `claude-integrations.md`, `codex-integrations.md`, and
  `desktop-tool-calls.md`: host-specific setup notes.
- `host-apply-confirm-proof-watchlist.md`, `host-model-switching-research.md`,
  `local-llm-and-new-host-research.md`, `agents-cli-adk-spike-plan.md`,
  `antigravity-mcp-setup.md`, and `colab-cli-spike-plan.md`: current adapter
  research and guarded future-work notes.

## Archived Docs

- `archive/codeaudit-2026-06-14.md`: completed code audit and remediation
  record.
- `archive/roadmap-completed-2026-06-14.md`: completed roadmap history.
- `archive/release/`: historical v3 release-readiness and release-decision
  notes that have been superseded by `release.md`.

## Drift Rules

- Keep current setup instructions in sync with the required drop-in files:
  protocol variant, `scripts/mythify.py`, adjacent `scripts/mythify_*.py`
  helpers, `protocol/operation-registry.json`,
  `protocol/classification-rules.json`, and `protocol/workflow-router.json`.
- Keep first-run instructions focused on one happy path before listing advanced
  surfaces.
- Keep MCP public surface claims at 41 tools: 38 core tools plus 3 fanout tools.
- Keep release claims aligned to `mcp-server/package.json`,
  `mcp-server/package-lock.json`, `CHANGELOG.md`, and the latest GitHub
  release.
