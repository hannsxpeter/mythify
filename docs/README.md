# Mythify documentation

Start with [start-here.md](start-here.md): install, one verified loop, and the
four workflows worth learning. Come back here for reference material.

## Using Mythify

| Doc | What it covers |
| :--- | :--- |
| [start-here.md](start-here.md) | The shortest path from nothing to a working evidence loop. |
| [commands.md](commands.md) | Every command and flag, generated from the CLI's parser. |
| [product-planning.md](product-planning.md) | Product records: problem, user, outcomes, non-goals, bets, risks, and the human gates. |
| [mcp.md](mcp.md) | MCP setup for any host: the launcher, `MYTHIFY_DIR`, config locations, tools, exit codes. |
| [blast-radius.md](blast-radius.md) | Safety cases for changes whose risk sits outside the visible diff. |
| [prose-quality.md](prose-quality.md) | The prose rewrite pass and the mechanical prose check. |

## How it works and what is proven

| Doc | What it covers |
| :--- | :--- |
| [architecture.md](architecture.md) | The runtime, the `.mythify/` state model, and the decisions behind them. |
| [evidence/efficacy-reproduction.md](evidence/efficacy-reproduction.md) | The July 2026 smoke run, its sanitized data, and what it does not show. |
| [DRIFT.md](DRIFT.md) | Drift found between docs and code, and how each case was resolved. |

## Project files at the repository root

| File | What it covers |
| :--- | :--- |
| [ROADMAP.md](../ROADMAP.md) | Where Mythify is going, written as a product plan. |
| [CHANGELOG.md](../CHANGELOG.md) | What changed in each release, with migration notes. |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to propose and test a change. |
| [MAINTAINING.md](../MAINTAINING.md) | Maintainer checks, generated files, and repository rules. |
| [RELEASE-CHECKLIST.md](../RELEASE-CHECKLIST.md) | The steps and checks for cutting a release. |
| [SECURITY.md](../SECURITY.md) | Supported versions and how to report a vulnerability. |

## Assets

`assets/banner.svg` and `assets/loop.svg` are the README images. They are
self-contained SVG with no external fonts or network requests, because GitHub
renders README images through `<img>`, which blocks external resources. Both
ship inside the standalone CLI archive so the packaged README stays intact.

## Keeping docs honest

- Every command and flag a doc names must exist: check it with `--help` before
  writing it down. `python3 scripts/lint.py` checks links, dashes and emoji,
  and the model-agnostic rule.
- MCP tool claims follow `TOOL_ALLOWLIST` in `scripts/mythify_mcp.py`;
  `tools/list` is authoritative.
- `AGENTS.md` and `CLAUDE.md` are generated from `protocol/PROTOCOL.md`. Edit
  the source and rebuild; never edit the copies.
- No claim in `README.md` may be stronger than what
  [evidence/efficacy-reproduction.md](evidence/efficacy-reproduction.md)
  supports. The project's own rule applies to its front page: executed evidence
  beats confident prose.
