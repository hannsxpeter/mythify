# Release Version Alignment Decision

Date: 2026-06-14

This decision reviewed the release version state after the v3 roadmap work and
the v3.0 release-candidate tag decision. It did not tag, publish, or change
package metadata.

## Decision

Use `3.0.0` as the intended next release version.

The next executable slice should align release metadata to `3.0.0` before any
release-candidate tag:

- Update `mcp-server/package.json`.
- Update `mcp-server/package-lock.json`.
- Move the relevant `CHANGELOG.md` unreleased entries under a `3.0.0` release
  heading.
- Update release-facing docs that still describe the current server version as
  `2.5.0`.
- Rerun the full release gate on the final commit.

## Evidence

Executed version-state mapping found:

- `mcp-server/package.json` reports version `2.5.0`.
- `mcp-server/package-lock.json` still reports version `2.4.0`.
- The latest release tag is `v2.5.0`.
- `CHANGELOG.md` has an `Unreleased` section above `2.5.0`.
- The v3.0 roadmap section is complete.
- The protocol and README describe the current MCP surface as 36 tools.
- Release readiness remains `ready_for_release_review` from recorded gates.

## Rationale

`2.5.x` is not a good next target because the lockfile is already behind the
package metadata and the current unreleased work is broader than a patch.

`2.6.0` would be plausible for purely additive feature work, but the completed
v3 roadmap work changes the public orchestration surface substantially: local
model support, hosted provider fanout guardrails, host CLI worker lanes,
execution substrate probes, lifecycle probes, registry-generated docs, and
release-readiness surfaces are now part of the contract.

`3.0.0` is the cleanest next release line because it matches the roadmap
milestone and gives downstream users a clear signal that the model-runtime
orchestration surface is no longer the older CLI-only shape.

## Guardrail

This is a version decision only. It is not a release tag, publish approval, or
claim that the final release artifact is safe. Tagging and publishing still
require explicit user intent and a clean final release gate after metadata is
aligned.
