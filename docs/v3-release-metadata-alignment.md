# v3.0.0 Release Metadata Alignment

Date: 2026-06-14

This slice aligned release metadata to the intended `3.0.0` line. It did not
create a tag, publish a package, or claim final release safety.

## Updated Metadata

- `mcp-server/package.json` version: `3.0.0`
- `mcp-server/package-lock.json` version: `3.0.0`
- `CHANGELOG.md` release heading: `3.0.0`
- `CHANGELOG.md` compare links: `v3.0.0...HEAD` and `v2.5.0...v3.0.0`
- `docs/design.md` current MCP package version: `3.0.0`

## Why

The v3 roadmap work is complete, the public MCP surface is now 36 tools, and
the model-runtime orchestration surface has grown beyond the older 2.x
CLI-first shape. Aligning metadata to `3.0.0` makes the next release candidate
honest before any tag exists.

## Next Safe Action

Run the final release gate on the metadata-aligned commit. Only after that
should a human decide whether to create a `v3.0.0` release-candidate tag.
