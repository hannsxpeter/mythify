# Memory and Lessons

Context windows end; projects do not. Mythify persists two kinds of knowledge
outside the context window: memory (project-scoped key-value entries) and
lessons (durable write-ups, project or global). Both survive session
boundaries and compaction.

## Memory entries

    memory set KEY VALUE [--category C]
    memory get [QUERY] [--category C]
    memory clear [KEY] [--all]

Each entry has one of four categories (default `fact`):

| Category | Use for | Example |
| :--- | :--- | :--- |
| fact | Stable truths about the project | `api-base` = `https://api.example.com/v2` |
| decision | Choices made and their rationale | `db-choice` = `sqlite, single-writer is fine` |
| discovery | Things learned the hard way | `flaky-test` = `test_sync fails under parallel runs` |
| state | Where work currently stands | `migration-progress` = `12 of 30 tables done` |

Keys are unique: `set` on an existing key overwrites it, which is exactly
right for `state` entries that track a moving frontier. `memory get` does
case-insensitive substring matching over both keys and values, so recall
works from half-remembered fragments. `memory clear` with no arguments
refuses (a guard against wiping state by accident); pass a KEY or `--all`
explicitly.

## What to store, when

Store at the moment of learning, not at session end:

- A decision the next session must not re-litigate: store it as `decision`.
- A surprising behavior, bug cause, or environment quirk: `discovery`.
- Progress on anything spanning sessions: `state`, overwritten as you go.

## Read-before-decide discipline

Recall is cheap; re-deriving is not. Two mandatory recall points:

1. Session start on existing work: run `status`, then `memory get` (no query
   lists everything) before touching anything.
2. Before any architectural decision: `memory get --category decision` and
   `memory get --category discovery`. A decision made blind to a recorded
   discovery is how the same mistake gets made twice.

Recall silently when it is relevant. Fold the recalled fact into the work
instead of announcing that you consulted memory or explaining how the store
works. Never surface an entry the current task did not call for: an unrelated
memory dropped into the thread is noise, not context, and it makes the user
wonder what else you are tracking. Relevance is the only trigger for showing
memory; the mechanics stay out of sight.

## Lessons

    lesson add TITLE DETAIL [--tags a,b] [--global]
    lesson list [--tag TAG] [--scope project|global|all]

Lessons are for transferable insight, written so a future session (or another
agent) can act on them: what happened, why, and what to do instead.

Project lessons (the default) live in the project's `.mythify/lessons/` and
cover anything specific to this codebase. Global lessons (`--global`) live in
`~/.mythify/lessons/` and must hold everywhere: tool behaviors, language
gotchas, protocol-level tactics. When unsure, keep it project-scoped;
a wrong global lesson pollutes every future project.

`lesson list` defaults to scope `all` and labels each lesson `(project)` or
`(global)`. Use `--tag` to filter when the store grows.

## Lessons from reflection

A `lesson` passed to the `reflect` command is automatically recorded as a
project lesson tagged `auto-reflected`. This is the cheapest path from
failure to durable knowledge: reflect honestly after every failure and let
the lesson land in the store without a separate command.

## Memory versus lessons

Memory answers "what is true of this project right now". Lessons answer
"what should anyone in this situation do". A flaky test's name is memory
(`discovery`); "run this suite serially because parallel runs corrupt the
fixture db" is a lesson.
