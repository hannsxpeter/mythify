# Mythify documentation

Start with [start-here.md](start-here.md): install, one verified loop, and the
three workflows worth learning. Come back here for reference material.

## Using Mythify

| Doc | What it covers |
| :--- | :--- |
| [start-here.md](start-here.md) | The shortest path from nothing to a working evidence loop. |
| [guide.md](guide.md) | Feature guide: plans, verification, maps, outcome loops, reviews, memory, routing, protocol files. |
| [commands.md](commands.md) | Every command and flag, generated from the CLI's parser. |
| [campaigns.md](campaigns.md) | Continue existing plans across sessions with strict evidence, learning, and read-only prompt watching. |
| [mcp.md](mcp.md) | MCP setup for any host: the launcher, `MYTHIFY_DIR`, config locations, tools, exit codes. |
| [blast-radius.md](blast-radius.md) | Safety cases for changes whose risk sits outside the visible diff. |
| [checks-and-gate.md](checks-and-gate.md) | Named checks for fast, selective verification, and `verify gate` for host stop and pre-commit hooks. |
| [reviews.md](reviews.md) | Findings reviews with bounded rounds, and the high-risk path check at step completion. |
| [prose-quality.md](prose-quality.md) | The prose rewrite pass and the mechanical prose check. |

## How it works

| Doc | What it covers |
| :--- | :--- |
| [architecture.md](architecture.md) | The runtime, the `.mythify/` state model, and the decisions behind them. |

## Project files

| File | What it covers |
| :--- | :--- |
| [CHANGELOG.md](../CHANGELOG.md) | What changed in each release, with migration notes. |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to propose and test a change. |
| [SECURITY.md](../SECURITY.md) | Supported versions and how to report a vulnerability. |
| [maintainers/MAINTAINING.md](maintainers/MAINTAINING.md) | Maintainer checks, generated files, and repository rules. |
| [maintainers/RELEASE-CHECKLIST.md](maintainers/RELEASE-CHECKLIST.md) | The steps and checks for cutting a release. |
| [maintainers/ROADMAP.md](maintainers/ROADMAP.md) | The outcomes, non-goals, and bets Mythify is steered by. |

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
- Make no efficacy claim. Mythify enforces evidence; it does not claim to make
  a model smarter or faster. The project's own rule applies to its front page:
  executed evidence beats confident prose.
