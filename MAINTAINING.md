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
| `RELEASE-CHECKLIST.md` | ``Current release target: `vX.Y.Z` `` and the asset names | lint `version` (target line); `tests/test_release_version.py` and `tests/test_release_metadata.py` (asset names) |
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
| `AGENTS.md`, `CLAUDE.md`, `PROTOCOL_SOURCE_SHA256` in `scripts/mythify_protocol.py` | `protocol/PROTOCOL.md` and `pointer_copy()` | `python3 scripts/build_variants.py` | `python3 scripts/build_variants.py --check`, run by lint `generated` |
| `docs/commands.md` | the argparse tree | `python3 scripts/build_commands_doc.py` | `python3 scripts/build_commands_doc.py --check`, run by lint `generated` |

Both generators write byte-identical output on every supported Python, so the
3.9 and current-Python CI jobs agree on what is stale.

A protocol change is a contract change: keep `protocol/PROTOCOL.md` under its
byte budget (lint `protocol-budget`), and say in the CHANGELOG what an agent
following the old text would now do differently.

## The lint

```bash
python3 scripts/lint.py                          # every check
python3 scripts/lint.py --check links --check text
python3 scripts/lint.py --json                   # findings as JSON
python3 scripts/lint.py --root /path/to/copy     # lint another tree
```

`scripts/lint.py` is the one maintainer entry point for drift. It exits 0
when clean and 1 with one line per finding, `path:line: check: message`.
It covers the repository's tracked files plus untracked files git does not
ignore, so a new file is linted before its first commit; outside a git work
tree it covers every file under `--root` except `.git/` and `__pycache__/`.
CI's `Repository hygiene` job and the release workflow run it.

| Check | Fails when |
| :--- | :--- |
| `version` | the top `CHANGELOG.md` release heading, the `X.x` row marked Yes in `SECURITY.md`, or the release target in `RELEASE-CHECKLIST.md` disagrees with `VERSION` |
| `generated` | `build_variants.py --check` or `build_commands_doc.py --check` fails |
| `protocol-budget` | `protocol/PROTOCOL.md` or `AGENTS.md` exceeds 12,000 bytes, or a `skills/*/SKILL.md` exceeds 16,000 bytes |
| `text` | a file contains an en dash (U+2013), an em dash (U+2014), U+FE0F, or a code point in U+1F000-1FAFF, U+2600-27BF, or U+2B00-2BFF |
| `prose` | `scripts/check_prose_quality.py` reports a finding |
| `model-agnostic` | a file names a model or a removed model-routing identifier, or a shipped surface names a vendor or host (see below) |
| `dependencies` | a dependency manifest (`package.json`, `requirements*.txt`, a pyproject dependency list, and similar) or `node_modules` is in the tree, or a `scripts/*.py` or `tests/*.py` file imports a module outside the standard library and those two folders |
| `mcp-surface` | a `TOOL_ALLOWLIST` entry is not a parser leaf or repeats, the typed tools fail to build, or the `mythify` tool description does not list exactly the other leaves |
| `links` | a relative Markdown link, reference, or image does not resolve to a file in the tree (anchors are ignored) |
| `source-size` | `scripts/check_runtime_source_size.py` reports a file over the nonblank line ceiling |

`tests/test_lint.py` runs the lint on the repository and, for each check,
copies the tree, injects one violation, and asserts that check reports it.
When a check is wrong, fix the check and extend its injection. Never loosen
a check to make a bad tree pass; a check that fails to fail is worse than a
red build.

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

In practice, the lint `model-agnostic` check enforces two denylists, both
case-insensitive with word boundaries, and both kept only in
`scripts/lint.py` (tests import them):

- Model family names (`MODEL_NAMES`) and the identifiers of the removed
  model-routing layer (`ROUTING_IDENTIFIERS`: its JSON keys, flags, tool
  names, state file, and environment variables) are forbidden in every file
  except `CHANGELOG.md`, `docs/DRIFT.md`, `docs/evidence/`,
  `scripts/lint.py`, and `tests/test_lint.py`. A test that asserts a removed
  key is absent checks keys with `lint.removed_identifier_keys` instead of
  spelling the key out.
- Vendor and host names (`VENDOR_NAMES`) are forbidden in the shipped
  product surfaces (`VENDOR_SCOPE`): `scripts/mythify*.py`,
  `scripts/check_prose_quality.py`, `protocol/`, `skills/`, `AGENTS.md`,
  `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `MAINTAINING.md`,
  `ROADMAP.md`, `RELEASE-CHECKLIST.md`, `CODE_OF_CONDUCT.md`, and every
  `docs/*.md` except `docs/mcp.md`. Lower-case "cursor" is an ordinary word
  there (report cursors), so only the editor's vendor forms match.

Allowed tokens everywhere: the file names `CLAUDE.md` and `.cursorrules`
(both read by `protocol check`), the MIT attribution URLs for the adapted
prose rules and the blast-radius workflow, and the host configuration paths
in the docs/mcp.md table. `docs/mcp.md` and `scripts/install_user.sh` stay
outside the vendor scope because they name host setup locations; keeping
vendor names there to setup locations is review, not lint. A feature that
seems to need a model name is a host feature, not a Mythify feature.

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

Every job in `ci.yml` and `release.yml` runs on `ubuntu-24.04`, not
`ubuntu-latest`. The `Python 3.9` job needs a runner image for which
`actions/setup-python` publishes a 3.9 build; when `ubuntu-latest` moves to
an image without one, that job fails at setup and blocks every release (step
10 of the release checklist). Keep the pin until 3.9 support is dropped, then
move all jobs to the next image together.
