# Tool-Use Contract

A discovery-discipline guardrail for any agent or adapter that calls tools on
top of Mythify. It adds no machinery: it states one rule that keeps tool calls
honest, and points at the doctrine it inherits from.

## The rule

1. Deferred tools must be discovered before use. When a tool is named but its
   schema is not loaded, treat it as not-yet-callable until you have fetched its
   real definition.
2. Never invoke a tool from a guessed or remembered schema. Argument shapes
   drift between versions; a schema you did not just load is an assumption, not
   a fact.
3. An adapter must load a tool's real schema through discovery (tool search or
   the host's tool-listing surface) before the first call, then call against
   that loaded shape.

## Why

A guessed schema fails one of two ways. The loud failure is a validation error:
the call is rejected and you retry blind. The quiet failure is worse: the call
is accepted with plausible-but-wrong arguments, and the tool does something you
did not intend. Discovery removes the guess. The listing surface returns the
authoritative parameter shape, so the first call is made against the real
contract, not a memory of it.

## In practice

- On seeing a tool name with no loaded schema, run the discovery step first,
  before composing any call.
- Batch related discoveries into one request when the surface allows it, rather
  than one round-trip per tool.
- If a tool will not resolve through discovery, treat it as unavailable and say
  so. Do not fall back to calling it blind.
- A discovered schema is an input to your next action, not evidence that the
  action succeeded. Like worker output, it is material, not verification: prove
  the call worked with `verify run`, not with the fact that you found the tool.

## See also

- The MCP note in `CLAUDE.md` / `AGENTS.md`: delegation discipline (workers have
  no memory, pass context explicitly, results are material not verification).
- `docs/design.md`, "Experience surface tiers" and the capability-registry
  guardrails (`metadata_only`, `material_not_verification`,
  `explicit_enable_required`, `no_implicit_cross_provider_fallback`).
- `docs/desktop-tool-calls.md`: how hosts wire up MCP tool calls.
