# Mythify documentation

Start with [start-here.md](start-here.md): install, one verified loop, and the
three workflows worth learning. Come back here for reference material.

## Using Mythify

| Doc | What it covers |
| :--- | :--- |
| [start-here.md](start-here.md) | The shortest path from nothing to a working evidence loop. |
| [guide.md](guide.md) | Feature guide: plans, verification, maps, outcome loops, reviews, memory, routing. |
| [commands.md](commands.md) | Every command and flag, generated from the CLI's parser. |
| [campaigns.md](campaigns.md) | Continue existing plans across sessions with strict evidence. |
| [trials.md](trials.md) | Compare a small instruction edit against a baseline. |
| [mcp.md](mcp.md) | MCP setup for any host. |

## How it works

| Doc | What it covers |
| :--- | :--- |
| [architecture.md](architecture.md) | The runtime, the `.mythify/` state model, and the decisions behind them. |

## Project files

| File | What it covers |
| :--- | :--- |
| [CHANGELOG.md](../CHANGELOG.md) | What changed in each release. |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to propose and test a change. |
| [SECURITY.md](../SECURITY.md) | Supported versions and how to report a vulnerability. |
| [maintainers/MAINTAINING.md](maintainers/MAINTAINING.md) | Maintainer checks and repository rules. |
| [maintainers/RELEASE-CHECKLIST.md](maintainers/RELEASE-CHECKLIST.md) | The steps for cutting a release. |
| [maintainers/ROADMAP.md](maintainers/ROADMAP.md) | Outcomes, non-goals, and bets. |

## Assets

`../assets/hero.svg`, `quest-map.svg`, and `loop.svg` are the README images.
They are self-contained SVG with no external fonts or network requests, because
GitHub renders README images through `<img>`, which blocks external resources.
They ship inside the standalone CLI archive and the npm package.

## Keeping docs honest

- Every command and flag a doc names must exist: check it with `--help` before
  writing it down. `python3 scripts/lint.py` checks links, dashes and emoji,
  and the model-agnostic rule.
- MCP tool claims follow `TOOL_ALLOWLIST` in `scripts/mythify_mcp.py`;
  `tools/list` is authoritative.
- `AGENTS.md` and `CLAUDE.md` are generated from `protocol/PROTOCOL.md`. Edit
  the source and rebuild; never edit the copies.
- Make no efficacy claim. Mythify enforces evidence; it does not claim to make
  a model smarter or faster.
