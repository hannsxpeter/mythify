# Communication quality

Mythify applies a final rewrite pass to user-facing prose. The goal is useful,
specific writing, not writing that merely avoids a list of model habits.

## Where it applies

Use this pass for chat responses, documentation, research summaries, release
notes, pull request descriptions, commit messages, and generated prompt
packets. Preserve verbatim quotations, logs, commands, legal text, license
notices, and machine-generated output when exact text matters.

## Rewrite pass

1. Remove boilerplate, puffery, promotional language, vague attribution,
   stacked hedging, canned pleasantries, and conclusions that add no fact.
2. Replace feelings about the work with the actor, action, evidence,
   measurement, or instruction the reader needs.
3. Delete or rewrite any sentence that could describe another project without
   changing a word.
4. Prefer active voice. Split sentences that make the reader backtrack.
5. Preserve precise domain terms. A familiar technical word is better than a
   vague synonym.
6. Read the result once for rhythm and point of view. Tight prose still needs
   to sound like a person addressing a specific reader.

## Judgment boundary

Mechanical checks may reject prohibited characters, decorative symbols, and
known filler phrases. They cannot verify voice, originality, human authorship,
or whether a contextual style choice is good. Keep those judgments material
and never use them to satisfy an executable completion gate.

## Source

This guidance selectively adapts the process and pattern families from Lauren
Tan's pstack `unslop` skill:
https://github.com/cursor/plugins/blob/main/pstack/skills/unslop/SKILL.md.
The pstack plugin is available under the MIT License. Mythify does not import
its blanket vocabulary rules because terms such as `harness`, `surface`, and
`artifact` have precise meanings here.
