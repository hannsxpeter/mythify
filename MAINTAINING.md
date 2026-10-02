# Maintaining Mythify

Maintainer rituals. Contributors should read [CONTRIBUTING.md](CONTRIBUTING.md)
first; this document covers the coordination work that keeps the code, the
generated files, and the docs telling the same story. The design and its
decision log are in [docs/architecture.md](docs/architecture.md).

## The version model

`VERSION` in `scripts/mythify.py` is the single source. The CLI prints it for
`--version`, the MCP server reports it as `serverInfo.version`, every executed
record stamps it as `provenance.mythify_version`, and `scripts/package_cli.py`
reads it to name `dist/mythify-cli-X.Y.Z.tar.gz` and to check the release tag.

These must agree with it when a release is cut. The lint `version` check
compares the three files with `VERSION`; the tag is checked when the release
workflow runs:

| Place | Form | Checked by |
| :--- | :--- | :--- |
| `CHANGELOG.md` | top release heading `## [X.Y.Z] - YYYY-MM-DD`, with the `[Unreleased]` and `[X.Y.Z]` compare links | lint `version`; `tests/test_release_version.py` checks the heading and every compare link |
| `SECURITY.md` | supported line `X.x` | lint `version` |
| `RELEASE-CHECKLIST.md` | ``Current release target: `vX.Y.Z` `` and the asset names | lint `version` |
| release tag | `vX.Y.Z` | `python3 scripts/package_cli.py --check-release-tag vX.Y.Z`, run by the release workflow before anything is built |

`tests/test_release_metadata.py` also pins the version and the release date
in its `VERSION` constant and heading assertion, so a release updates it too.

Semver: a major release removes or renames a command, flag, MCP tool, exit
code, or state field, or changes what an existing one means; a minor release
adds commands, flags, tools, or fields; a patch release fixes behavior or docs
without changing an interface.

## Generated files

Never edit these by hand. Change the source, regenerate, and commit both.

| Generated | Source | Command | Drift check |
| :--- | :--- | :--- | :--- |
| `AGENTS.md`, `CLAUDE.md`, `PROTOCOL_SOURCE_SHA256` in `scripts/mythify_protocol.py` | `protocol/PROTOCOL.md` and `pointer_copy()` | `python3 scripts/build_variants.py` | `python3 scripts/build_variants.py --check` (CI and lint `generated`) |
| `docs/commands.md` | the argparse tree | `python3 scripts/build_commands_doc.py` | lint `generated` |

A protocol change is a contract change: keep `protocol/PROTOCOL.md` under its
byte budget (lint `protocol-budget`), and say in the CHANGELOG what an agent
following the old text would now do differently.

## The lint

```bash
python3 scripts/lint.py
```

`scripts/lint.py` is the one maintainer entry point for drift. It runs these
checks and exits nonzero when any fails:

| Check | Fails when |
| :--- | :--- |
| `version` | a place in the version table disagrees with `VERSION` |
| `generated` | a generated file differs from what its generator would write |
| `protocol-budget` | `protocol/PROTOCOL.md` exceeds its byte budget |
| `text` | a tracked file has a non-ASCII character, an em or en dash, or an emoji |
| `prose` | `scripts/check_prose_quality.py` reports a finding |
| `model-agnostic` | a shipped product surface names an AI model or vendor outside the allowed exceptions |
| `dependencies` | a shipped Python file imports a module outside the standard library or the repository |
| `mcp-surface` | an MCP tool does not map to a parser leaf |
| `links` | a relative Markdown link does not resolve |
| `source-size` | a file under `scripts/` exceeds the nonblank line ceiling |

Its self-test proves each check fails on an injected violation. When a check
is wrong, fix the check and add a self-test case for it. Never loosen a check
to make a bad tree pass; a check that fails to fail is worse than a red build.

## Adding a command

1. Parser. Add the subcommand where its family lives (`scripts/mythify_parser.py`,
   or the grammar module beside its handler, such as
   `mythify_product_parser.py`). Give it `help` and `description`, and set
   `needs_state` (`False` for commands that never touch `.mythify`).
2. Handler. `cmd_<name>(args, state)` in the module that owns the feature
   returns 0, 1, or 2 and never 64; argparse owns usage errors. A handler that
   runs a command goes through `execute_recorded_verification`, refuses under
   `MYTHIFY_DISABLE_RUN=1` with exit 2, and joins the table in
   [SECURITY.md](SECURITY.md).
3. Tests. Cover success, every refusal, and the exit codes. The `--help` test
   reads the command list from the parser, so it covers the new command
   without edits.
4. MCP. Decide whether the command belongs in `TOOL_ALLOWLIST` in
   `scripts/mythify_mcp.py`. Add it only when an agent uses it in most
   sessions; long-running drivers such as `outcome run` stay out. Every
   command outside the list is still reachable through the `mythify` tool,
   whose description lists it automatically. Update
   `tests/test_mcp_server.py` when the typed set changes.
5. Docs. Run `python3 scripts/build_commands_doc.py`. Update
   docs/architecture.md for any new state file, field, exit code, or
   environment variable, add a decision log entry when the command changes a
   design decision, and add a CHANGELOG line under `[Unreleased]`.
6. Run `python3 -m unittest discover -s tests -v` and `python3 scripts/lint.py`.

## The model-agnostic rule

Mythify never names, ranks, routes to, switches, or spawns a model,
provider, or vendor CLI. The host chooses any subagent or none, and delegated
output is material until `verify run` passes on the integrated result.

In practice: runtime code, help text, `protocol/`, the skills, and product
docs name no AI model or vendor. Allowed exceptions are host setup file
locations (docs/mcp.md and the installer's skill-root defaults), historical
records (`CHANGELOG.md`, `docs/DRIFT.md`, `docs/evidence/`), the license
attribution links in docs/blast-radius.md and docs/prose-quality.md, and the
literal file name `CLAUDE.md`. `tests/test_model_agnostic.py`
and the lint `model-agnostic` check enforce it. A feature that seems to need a
model name is a host feature, not a Mythify feature.

## Logging drift

When two sources disagree (a doc and the code, a help string and the
handler), fix the source that is wrong, then add a row to
[docs/DRIFT.md](docs/DRIFT.md): where it was, what it claimed against what
the code does, and the change that resolved it. Prefer a resolution that
derives the fact from code or adds a lint check over one that edits prose.
Drift found and not fixed goes under "Open" in that file until a release
resolves it, and the release's CHANGELOG entry names it.

## Releases

[RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) holds the exact ordered commands:
version, regenerate, full suite on 3.9 and current Python, lint, the release
workflow gates, installer smoke, MCP smoke, packaging, checksums, tag, and the
tag-triggered workflow. Do not tag from a commit whose CI is red.

## Lint or CI regression on main

1. Reproduce locally with `python3 scripts/lint.py` and
   `python3 -m unittest discover -s tests -v`.
2. Fix forward with one revert-or-repair commit; do not stack unrelated
   changes on a red `main`.
3. If the failure only appears on Python 3.9, reproduce it with a 3.9
   interpreter before changing code.

## Dependencies and CI

Mythify has no package dependencies. GitHub Actions in `.github/workflows/`
are pinned by commit SHA with the version in a trailing comment; Dependabot
proposes updates weekly. When you accept one, keep the comment in step with
the SHA.
