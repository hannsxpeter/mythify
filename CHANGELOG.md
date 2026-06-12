# Changelog

All notable changes to Mythify are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.0] - 2026-06-12

### Added

- Fanout parallel delegation (MCP only): the orchestrating model declares a
  one-shot task list and the server spawns, sequences, and collects parallel
  sub-workers. Three new MCP tools, `fanout_start`, `fanout_status`, and
  `fanout_results`, bring the server to 15 tools.
- Four worker engines: subscription-billed `claude-cli` workers (no API key
  needed), the `anthropic` and `openai` HTTP APIs, and a generic `command`
  engine that runs any local CLI agent through a shell template.
- Model selection at three levels, most specific wins: per-task `model`
  overrides per-job `model` overrides `MYTHIFY_FANOUT_MODEL`. The same
  precedence applies to engines, so one job can mix models and engines
  across tasks.

## [2.0.0] - 2026-06-12

First published release. Mythify 1.x was an unreleased prototype; 2.0.0 is a
ground-up rebuild around the contracts in [docs/design.md](docs/design.md).

### Added

- Executed verification: `verify run` (CLI) and `verify_run` (MCP) execute a
  command and record the real exit code, duration, and output tails. An exit
  code is evidence; self-report is not.
- Per-project state: each project owns a `.mythify/` directory with named
  plans, key-value memory, lessons, and append-only verification and
  reflection logs. Discovery walks upward from the working directory;
  `MYTHIFY_DIR` overrides.
- Node MCP server (`mcp-server/`) exposing the contract as 12 tools for
  clients without shell access (Claude Desktop and any MCP client).
- Generated protocol variants: `protocol/PROTOCOL.md` is the single source;
  `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` are built by
  `scripts/build_variants.py`, and CI fails on drift.
- Manus-style skill package (`skills/mythify/`), built into
  `dist/mythify.skill` by `scripts/package_skill.py`.
- Test suites: 47 Python tests (including a CLI-to-MCP interop test against
  one shared state directory) and an MCP smoke test that asserts the on-disk
  formats field by field.
- Durability: atomic writes, corrupt-state quarantine
  (`<file>.corrupt-<timestamp>`), and graceful behavior on missing state.
- Claude Desktop and Claude Code integration guide
  ([docs/claude-integrations.md](docs/claude-integrations.md)), including
  running Mythify on smaller models.

### Changed

- State moved from a single shared `~/.mythify/` to per-project `.mythify/`
  directories; only cross-project lessons remain global (`~/.mythify/lessons`).
- Self-attested claims (`verify claim` / `verify_claim`) are now recorded with
  `verified: null` and never count as verified; completion and failure step
  updates require evidence.
- Protocol ceremony is proportional to task size; trivial tasks pay zero
  protocol overhead.

### Removed

- The legacy prototype files (Manus research dumps, the old single-file
  orchestrator, and prebuilt `.skill` archives). The source research report is
  preserved verbatim at [docs/research-report.md](docs/research-report.md).

[Unreleased]: https://github.com/aihxp/mythify/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/aihxp/mythify/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/aihxp/mythify/releases/tag/v2.0.0
