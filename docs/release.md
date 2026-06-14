# Release Process

Current release target: `v3.0.1`.

Current package metadata:

- MCP package: `mythify-mcp`
- Version: `3.0.1`
- Node runtime: `>=18`
- Package artifact: `mcp-server/mythify-mcp-3.0.1.tgz`
- Skill artifact: `dist/mythify.skill`

## Release Gate

Run these checks on the final commit before publishing:

```bash
python3 -m unittest discover -s tests -v
npm test --prefix mcp-server
node scripts/check_surface_manifest.mjs
node scripts/check_classification_rules_manifest.mjs
node scripts/build_registry_docs.mjs --check
python3 scripts/mythify.py protocol check CLAUDE.md AGENTS.md .cursorrules
git diff --check
npm audit --prefix mcp-server --audit-level=moderate
python3 scripts/mythify.py readiness --json
```

The readiness command is read-only. It summarizes recorded evidence and project
state, but it does not rerun gates, tag, publish, or declare the release safe by
itself.

## Build Artifacts

Build local artifacts before creating the GitHub release:

```bash
python3 scripts/package_skill.py
(cd mcp-server && npm pack)
```

Expected artifacts:

- `dist/mythify.skill`
- `mcp-server/mythify-mcp-3.0.1.tgz`

The npm tarball must include package-local copies of
`mcp-server/protocol/classification-rules.json`,
`mcp-server/protocol/operation-registry.json`, and
`mcp-server/protocol/surface-manifest.json` because the packaged MCP server
loads these manifests at runtime.

## Install Path

The supported user install path is a local checkout plus:

```bash
./scripts/install_user.sh --project /absolute/path/to/project
```

The script installs `mythify` and `mythify-mcp` launchers under
`$HOME/.local/bin` by default, installs the packaged MCP server under
`$XDG_DATA_HOME/mythify/VERSION` or `$HOME/.local/share/mythify/VERSION`, and
prints the Codex MCP registration command for the selected project.

## Publish

Create the GitHub release only after the final commit is pushed and CI is green:

```bash
gh release create v3.0.1 \
  dist/mythify.skill \
  mcp-server/mythify-mcp-3.0.1.tgz \
  --title "Mythify v3.0.1" \
  --notes-file /tmp/mythify-v3-release-notes.md
```

The release workflow also builds and uploads the same assets on
`release.published`, so manually supplied assets and workflow assets must be
identical or safely overwritten by the workflow.

## Package Distribution Status

The current npm package name is unscoped: `mythify-mcp`. This repository
currently produces a GitHub release package artifact
(`mythify-mcp-3.0.1.tgz`) rather than publishing an npm package to the GitHub
Packages registry. The current product promise is therefore:

- Source checkout plus `scripts/install_user.sh` for user-local installation.
- GitHub release assets for the skill archive and MCP package tarball.
- No `npx` path until the package identity is scoped and a registry publish
  workflow exists.

Add a scoped package name and publish workflow only if registry publishing
becomes a product requirement.
