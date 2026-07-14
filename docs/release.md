# Release Process

Current release target: `v5.0.0`.

Current package metadata:

- MCP package: `mythify-mcp`
- Version: `5.0.0`
- Node runtime: `>=20`
- Package artifact: `mcp-server/mythify-mcp-5.0.0.tgz`
- Standalone CLI artifact: `dist/mythify-cli-5.0.0.tar.gz`
- Skill artifact: `dist/mythify.skill`
- Checksum manifest: `SHA256SUMS`

## Release Gate

Run these checks on the final commit before publishing:

```bash
npm ci --prefix mcp-server
python3 scripts/package_cli.py --check-release-tag v5.0.0
python3 -m unittest discover -s tests -v
npm test --prefix mcp-server
python3 -m unittest tests.test_interop -v
python3 -m unittest tests.test_install_user tests.test_release_checksums tests.test_mcp_package tests.test_release_version -v
node scripts/check_surface_manifest.mjs
node scripts/check_classification_rules_manifest.mjs
node scripts/build_registry_docs.mjs --check
python3 scripts/check_runtime_source_size.py
python3 scripts/mythify.py protocol check CLAUDE.md AGENTS.md .cursorrules
git diff --check
npm audit --prefix mcp-server --audit-level=moderate
```

The readiness command is read-only. `protocol/release-gates.json` is the
authoritative inventory of exact normalized commands, and its packaged MCP
mirror must be identical. Readiness accepts only passing executed records for
those commands from the same clean Git commit and Mythify version. It does not
rerun gates, tag, publish, or declare the release safe by itself.

## Build Artifacts

Build local artifacts before creating the GitHub release:

```bash
python3 scripts/package_skill.py
python3 scripts/package_cli.py
(cd mcp-server && npm pack)
mkdir -p dist/release-assets
cp dist/mythify.skill dist/mythify-cli-5.0.0.tar.gz \
  mcp-server/mythify-mcp-5.0.0.tgz dist/release-assets/
python3 scripts/build_release_checksums.py \
  --output dist/release-assets/SHA256SUMS \
  dist/release-assets/mythify.skill \
  dist/release-assets/mythify-cli-5.0.0.tar.gz \
  dist/release-assets/mythify-mcp-5.0.0.tgz
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
- `dist/release-assets/mythify-cli-5.0.0.tar.gz`
- `dist/release-assets/mythify-mcp-5.0.0.tgz`
- `dist/release-assets/SHA256SUMS`

The npm tarball must include package-local copies of
`mcp-server/protocol/classification-rules.json`,
`mcp-server/protocol/model-capabilities.json`,
`mcp-server/protocol/operation-registry.json`,
`mcp-server/protocol/workflow-router.json`, and
`mcp-server/protocol/surface-manifest.json` because the packaged MCP server
loads these manifests at runtime.

It must also include `README.md` and `LICENSE`, and must exclude the package's
development-only `test/` tree. Users install the release asset locally with
`npm install /path/to/mythify-mcp-5.0.0.tgz`; there is no registry publish.

## Install Path

The supported user install path is a local checkout plus:

```bash
./scripts/install_user.sh --project /absolute/path/to/project
```

The script installs `mythify` and `mythify-mcp` launchers under
`$HOME/.local/bin` by default, installs the packaged MCP server under
`$XDG_DATA_HOME/mythify/VERSION` or `$HOME/.local/share/mythify/VERSION`, and
copies the Mythify chat skills for both runtimes: under `$CODEX_HOME/skills`
(or `$HOME/.codex/skills`) for Codex and `$CLAUDE_HOME/skills` (or
`$HOME/.claude/skills`) for Claude Code. Invoke them with `$name` in Codex or
`/name` in Claude Code. It also prints the Codex MCP registration command for
the selected project.

Use `--skip-skills` to skip all chat skill installation, `--skills-root PATH`
to choose the Codex skill root, `--skip-claude-skills` to skip only the Claude
Code copy, `--claude-skills-root PATH` to choose the Claude skill root, and
`--install-chat-hook` to install the optional `mythify-chat-report-hook.sh`
helper under `$CODEX_HOME/hooks` or `$HOME/.codex/hooks`.

## Publish

Push the final version tag only after the final commit is pushed and branch CI
is green:

```bash
git tag v5.0.0
git push origin v5.0.0
```

The tag-triggered release workflow checks out that exact commit, runs every
authoritative test and integrity gate, builds all three packages, verifies the
flat checksum manifest, and only then creates the public GitHub release with
the assets attached. Any failed gate prevents release creation.

## Package Distribution Status

The current npm package name is unscoped: `mythify-mcp`. This repository
currently produces a GitHub release package artifact
(`mythify-mcp-5.0.0.tgz`) rather than publishing an npm package to the GitHub
Packages registry. The current product promise is therefore:

- Source checkout plus `scripts/install_user.sh` for user-local installation.
- Standalone CLI archive plus `scripts/install_user.sh --skip-mcp` for a
  checkout-independent CLI installation.
- GitHub release assets for the skill archive, standalone CLI archive, MCP
  package tarball, and checksums.
- No `npx` path until the package identity is scoped and a registry publish
  workflow exists.

Add a scoped package name and publish workflow only if registry publishing
becomes a product requirement.
