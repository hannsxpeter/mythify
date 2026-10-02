# Contributing to Mythify

Thanks for considering a contribution. This document covers the development
rules, the checks a pull request must pass, and how to get it merged.
Maintainer rituals (versions, releases, the drift log) are in
[MAINTAINING.md](MAINTAINING.md).

## One runtime, standard library only

Mythify is one Python program. The CLI (`scripts/mythify.py` and its
`scripts/mythify_*.py` modules), the MCP server (`scripts/mythify_mcp.py`,
which runs the CLI for every tool call), the installer, and the tests use the
Python standard library and nothing else.

- Python 3.9 or newer. Do not use syntax or library calls added after 3.9,
  such as `match` statements, `X | Y` unions evaluated at runtime, or
  `zip(strict=True)`. CI runs the suite on 3.9 and 3.13.
- No third-party packages, no `requirements.txt`, no Node, no npm. There is
  nothing to install before you run the tests.
- Each file under `scripts/` stays under 1500 nonblank lines
  (`python3 scripts/check_runtime_source_size.py`). Split a module by
  responsibility when it grows past that.

```bash
git clone https://github.com/hannsxpeter/mythify.git
cd mythify
python3 -m unittest discover -s tests -v
```

## Tests

Every behavior change ships with a test that fails without it. Put it next to
the code it covers (for example `tests/test_product.py` for
`scripts/mythify_product.py`, `tests/test_mcp_server.py` for the MCP server),
and run the full suite before you open a pull request:

```bash
python3 -m unittest discover -s tests -v
```

Tests must not rewrite tracked files; generate into a temporary copy instead.

## Lint

```bash
python3 scripts/lint.py
```

`scripts/lint.py` is the one maintainer gate for drift: version agreement,
generated files, the protocol byte budget, the text rules below, the prose
check, the model-agnostic rule, standard-library-only imports, the MCP
surface, Markdown links, and file size. It must pass before you open a pull
request. [MAINTAINING.md](MAINTAINING.md) lists each check.

## Generated files: never edit by hand

| File | Source | Regenerate with |
| :--- | :--- | :--- |
| `AGENTS.md`, `CLAUDE.md`, and `PROTOCOL_SOURCE_SHA256` in `scripts/mythify_protocol.py` | `protocol/PROTOCOL.md` | `python3 scripts/build_variants.py` |
| `docs/commands.md` | the argparse tree in `scripts/` | `python3 scripts/build_commands_doc.py` |

`AGENTS.md` is the full protocol copy; `CLAUDE.md` is a pointer that imports
it. Commit the source and the regenerated files together. CI and the lint
fail when they disagree. `dist/` is build output and is not committed.

## Writing rules

Every file in the repository follows these. The lint `text` check fails on
dashes and emoji, and its `prose` check runs
`python3 scripts/check_prose_quality.py` on the docs; review covers the rest.

- ASCII only. No emoji anywhere.
- No em dashes (U+2014) and no en dashes (U+2013). Use commas, colons,
  parentheses, or plain hyphens.
- No TODO markers and no placeholder content. Every file ships complete.
- Concrete prose: name the actor, the action, and the evidence. No promotional
  language.
- Program output uses the ASCII markers `[OK]`, `[FAIL]`, and `[WARN]`.

## No model or vendor names in the product

Mythify works with any host and any model, and it never names, ranks, routes
to, or spawns one. Runtime code, help text, the protocol, the skills, and
product docs name no AI model, provider, or vendor. The exceptions are host
setup file locations in `docs/mcp.md` and the installer, historical records
(`CHANGELOG.md`, `docs/DRIFT.md`, `docs/evidence/`), and the literal file name
`CLAUDE.md`. The lint `model-agnostic` check enforces it; its denylists live
in `scripts/lint.py`, and [MAINTAINING.md](MAINTAINING.md) lists their scope.

## Design changes

A change to a command, a flag, an exit code, an on-disk format, an evidence
rule, or the MCP surface updates [docs/architecture.md](docs/architecture.md)
in the same pull request. A change that alters a design decision adds a dated
entry to its decision log saying what changed and why. New commands follow
"Adding a command" in [MAINTAINING.md](MAINTAINING.md).

## Pull requests

- Keep each pull request focused on one change.
- Title it with Conventional Commits, for example `feat: ...`, `fix: ...`,
  `docs: ...`, `test: ...`; mark breaking changes with `!`.
- Fill in the checklist in the pull request template.
- CI runs the suite on Python 3.9 and 3.13 and the repository hygiene job.
  All jobs must be green.

## Reporting bugs and requesting features

Use the issue templates at
[https://github.com/hannsxpeter/mythify/issues](https://github.com/hannsxpeter/mythify/issues).
For security vulnerabilities, do not open a public issue; follow
[SECURITY.md](SECURITY.md) instead.

## Code of conduct

Participation in this project is governed by the
[code of conduct](CODE_OF_CONDUCT.md).

## License

Mythify is MIT licensed. By contributing, you agree that your contributions are
licensed under the same [MIT license](LICENSE).
