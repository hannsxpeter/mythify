# Prose quality

Mythify tightens user-facing writing with two separate controls: an advisory
rewrite pass and a small mechanical check. Neither control claims to detect
human authorship.

## Advisory rewrite pass

The packaged skill applies the guidance in
[`skills/mythify/references/communication-quality.md`](../skills/mythify/references/communication-quality.md)
before final chat responses and authored documentation. Every packet that
`mythify prompt` renders carries the same compact instruction, and MCP
clients receive the same packets because the MCP server runs the CLI.

The pass removes generic or promotional language, replaces vague claims with
named actions or evidence, and preserves exact technical terms. Quotations,
logs, commands, legal text, license notices, and generated output remain exact
when fidelity matters.

## Mechanical check

Run:

```bash
python3 scripts/check_prose_quality.py
```

The checker reads `protocol/prose-quality.json` and scans the maintained
Markdown paths listed there. It rejects the project's forbidden dash
characters, decorative symbols in configured Unicode ranges, and a short list
of canned phrases, matched without regard to case. Paths listed under
`excluded_paths` in the manifest, such as committed evaluation evidence in
`docs/evidence`, are outside the default scope. Pass explicit Markdown files
or directories to inspect them separately.

Exit codes are:

- `0`: no configured mechanical violation was found.
- `1`: one or more configured violations were found.
- `2`: the checker could not load or inspect its inputs.

CI, the release workflow, and the `prose` check in `scripts/lint.py` run this
command. Its result proves only that the configured mechanical patterns are
absent. Voice, clarity, originality, and human authorship remain judgment
calls.

## Source and license

The rewrite process selectively adapts ideas from the
[pstack `unslop` skill](https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md),
which is available under the MIT License. Mythify keeps its own domain terms
and does not treat a vocabulary list as proof of writing quality.
