# Release Process

Current release target: `v5.8.0`.

Current release artifacts:

- Version: `5.8.0`
- Runtime: Python 3.9 or newer, standard library only
- Standalone CLI artifact: `dist/mythify-cli-5.8.0.tar.gz` (includes the MCP
  stdio server, `scripts/mythify_mcp.py`)
- Skill artifact: `dist/mythify.skill`
- Checksum manifest: `SHA256SUMS`

## Release Gate

Run these checks on the final commit before publishing:

```bash
python3 scripts/package_cli.py --check-release-tag v5.8.0
python3 -m unittest discover -s tests -v
python3 -m unittest tests.test_install_user tests.test_release_checksums tests.test_mcp_server tests.test_release_version -v
python3 scripts/check_prose_quality.py
python3 scripts/check_runtime_source_size.py
python3 scripts/mythify.py protocol check CLAUDE.md AGENTS.md .cursorrules
git diff --check
```

The readiness command is read-only. `protocol/release-gates.json` is the
authoritative inventory of exact normalized commands. Readiness accepts only
passing executed records for those commands from the same clean Git commit and
Mythify version. It does not rerun gates, tag, publish, or declare the release
safe by itself.

## Build Artifacts

Build local artifacts before creating the GitHub release:

```bash
python3 scripts/package_skill.py
python3 scripts/package_cli.py
mkdir -p dist/release-assets
cp dist/mythify.skill dist/mythify-cli-5.8.0.tar.gz dist/release-assets/
python3 scripts/build_release_checksums.py \
  --output dist/release-assets/SHA256SUMS \
  dist/release-assets/mythify.skill \
  dist/release-assets/mythify-cli-5.8.0.tar.gz
python3 scripts/build_release_checksums.py \
  --check dist/release-assets/SHA256SUMS \
  --directory dist/release-assets
python3 scripts/mythify.py readiness --json
```

The staging directory models GitHub's flat release download layout. The
checksum helper rejects repository-relative names, duplicate basenames,
missing assets, and digest mismatches.

Expected artifacts:

- `dist/release-assets/mythify.skill`
- `dist/release-assets/mythify-cli-5.8.0.tar.gz`
- `dist/release-assets/SHA256SUMS`

## Install Path

The supported user install path is a local checkout or the standalone CLI
archive plus:

```bash
./scripts/install_user.sh --project /absolute/path/to/project
```

The script installs the `mythify`, `mythify-mcp`, and `mythify-uninstall`
launchers under `$HOME/.local/bin` by default, installs the versioned runtime
under `$XDG_DATA_HOME/mythify/VERSION` or `$HOME/.local/share/mythify/VERSION`,
and copies the Mythify chat skills for both runtimes: under
`$CODEX_HOME/skills` (or `$HOME/.codex/skills`) for Codex and
`$CLAUDE_HOME/skills` (or `$HOME/.claude/skills`) for Claude Code. Invoke them
with `$name` in Codex or `/name` in Claude Code. `mythify-mcp` runs
`mythify.py mcp`, the zero-dependency MCP stdio server. With `--project`, the
script prints the MCP command and `MYTHIFY_DIR` value to register with an MCP
client.

Use `--skip-skills` to skip all chat skill installation, `--skills-root PATH`
to choose the Codex skill root, `--skip-claude-skills` to skip only the Claude
Code copy, `--claude-skills-root PATH` to choose the Claude skill root, and
`--install-chat-hook` to install the optional `mythify-chat-report-hook.sh`
helper under `$CODEX_HOME/hooks` or `$HOME/.codex/hooks`. `--skip-mcp` is
accepted for older install commands and has no effect.

## Publish

Push the final version tag only after the final commit is pushed and branch CI
is green:

```bash
git tag v5.8.0
git push origin v5.8.0
```

The tag-triggered release workflow checks out that exact commit, runs every
authoritative test and integrity gate, builds both packages, verifies the flat
checksum manifest, and only then creates the public GitHub release with the
assets attached. Any failed gate prevents release creation.
