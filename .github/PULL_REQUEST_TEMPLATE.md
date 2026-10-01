# Summary

Describe what this PR changes and why. If it fixes an issue, link it.

# Checklist

- [ ] Test suite passes locally: `python3 -m unittest discover -s tests -v`
- [ ] If any CLI command, MCP tool, or on-disk format changed, `docs/design.md` is updated to match (it is the authoritative contract)
- [ ] If a command meant for MCP clients was added or removed, `TOOL_ALLOWLIST` in `scripts/mythify_mcp.py` and `tests/test_mcp_server.py` were updated
- [ ] If `protocol/PROTOCOL.md` changed, variants were rebuilt with `python3 scripts/build_variants.py` and the regenerated `CLAUDE.md`, `AGENTS.md`, and `.cursorrules` are committed
- [ ] ASCII only: no emojis, no em dashes, no en dashes in any file or program output
- [ ] PR title follows Conventional Commits (for example `feat: ...`, `fix: ...`, `docs: ...`)
