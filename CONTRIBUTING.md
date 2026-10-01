# Contributing to Mythify

Thanks for considering a contribution. This document covers the development
workflow, the repository rules that CI enforces, and how to get a pull request
merged.

## Prerequisites

- Python 3.9 or newer (the CLI, the MCP server, and the tests use only the
  standard library).
- No other tooling is required. There is nothing to `pip install`.

## Getting started

```bash
git clone https://github.com/hannsxpeter/mythify.git
cd mythify
```

## Running the tests

Run the suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

It covers the CLI and the MCP stdio server (`tests/test_mcp_server.py` spawns
`python3 scripts/mythify.py mcp`). It must pass before you open a pull request.

## The design contract

[docs/design.md](docs/design.md) is the authoritative contract for every CLI
command and every on-disk format. The MCP server is generated from the CLI
parser and runs the CLI for every tool call, so there is one implementation.

If your change alters any interface or format, update `docs/design.md` in the
same pull request, and update both implementations so they stay in sync. A
behavior change that is not reflected in `docs/design.md` will not be merged.

If the CLI and MCP server both expose a behavior, update both or prove the
asymmetry is intentional in `docs/design.md`. Every shared behavior change must
include at least one parity anchor: a shared manifest or registry update, a
cross-runtime fixture, or an interop assertion. Do not start a broad runtime
unification refactor only to remove duplication; extract a shared artifact only
after real drift or maintenance pressure shows that the smaller contract will
pay for itself.

## Generated files: never edit by hand

`CLAUDE.md`, `AGENTS.md`, and `.cursorrules` at the repository root are
generated. Do not edit them directly; CI rejects any drift between them and
their source.

To change the protocol:

1. Edit `protocol/PROTOCOL.md`.
2. Regenerate the variants:

   ```bash
   python3 scripts/build_variants.py
   ```

3. Commit `protocol/PROTOCOL.md` together with the regenerated `CLAUDE.md`,
   `AGENTS.md`, and `.cursorrules`.

Similarly, `dist/mythify.skill` is a build output (created by
`python3 scripts/package_skill.py` from `skills/mythify/`) and is not committed.

## Writing rules (CI-enforced)

Every file in this repository follows these rules, and the `hygiene` CI job
fails the build on violations:

- ASCII only. No emojis anywhere.
- No em dashes (U+2014) and no en dashes (U+2013). Use commas, colons,
  parentheses, or plain hyphens instead.
- No TODO markers and no placeholder content. Every file ships complete.
- Exception: `docs/research-report.md` is preserved legacy content and is exempt.

Program output uses the ASCII markers `[OK]`, `[FAIL]`, and `[WARN]`.

## Pull requests

- Keep each pull request focused on one change.
- Title the pull request using Conventional Commits, for example `feat: ...`,
  `fix: ...`, `docs: ...`, `test: ...`.
- Fill in the checklist in the pull request template. It mirrors the rules in
  this document.
- CI runs the suite on Python 3.9 and 3.13, the generated-file sync check, the
  prose check, an installer and MCP server smoke test, and the ASCII rules
  check. All jobs must be green.

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
