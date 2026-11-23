<p align="center">
  <img src="assets/hero.svg" alt="Arcade title screen: MYTHIFY, prove done, npx @hxp-tools/mythify. A robot agent hits a ghost called the hallucination with an exit 0 beam." width="100%">
</p>

<p align="center">
  <a href="https://github.com/hannsxpeter/mythify/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/hannsxpeter/mythify/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://www.npmjs.com/package/@hxp-tools/mythify"><img alt="npm" src="https://img.shields.io/npm/v/@hxp-tools/mythify.svg"></a>
  <a href="CHANGELOG.md"><img alt="version" src="https://img.shields.io/badge/version-7.2.1-blue"></a>
  <a href="skills/mythify/SKILL.md"><img alt="agent skills" src="https://img.shields.io/badge/Agent%20Skills-compatible-2f6fed"></a>
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-blue"></a>
</p>

<h3 align="center">Prove done.</h3>

<p align="center"><em>Your agent says the tests pass. Mythify makes it run them.</em></p>

Mythify is evidence for AI coding agents. A step closes only after a real
command exits 0. Plans, checks, and lessons live in `.mythify/` on disk, so the
next session opens where this one stopped. Sibling of
[godpowers](https://github.com/hannsxpeter/godpowers),
[godplans](https://github.com/hannsxpeter/godplans), and
[godaudits](https://github.com/hannsxpeter/godaudits). Part of
[`@hxp-tools`](https://www.npmjs.com/org/hxp-tools).

---

## Insert coin

```bash
npx @hxp-tools/mythify@latest --project .
# or from source:
git clone https://github.com/hannsxpeter/mythify.git
cd mythify
./scripts/install_user.sh --project /path/to/your/project
```

Needs Python 3.9+. Nothing else. The installer puts `mythify`, `mythify-mcp`,
and `mythify-uninstall` in `~/.local/bin` and copies chat skills into the
agent folders it finds. Copy `AGENTS.md` into your project root so the agent
reads the protocol. Host-specific MCP paths live in [docs/mcp.md](docs/mcp.md).

---

## Your first battle

Every step is a fight against an unverified claim. Plan, act, and reflect set
up the hit; only an exit code lands it.

<p align="center">
  <img src="assets/battle.svg" alt="Battle screen. The agent uses plan, act, verify, reflect. Verify returns exit 1 and the claim holds; after a reflection, verify returns exit 0 and step 1 completes." width="100%">
</p>

```bash
mythify plan create "Fix the failing parser test" \
  --steps '[{"title":"Reproduce and fix","success_criteria":"parser tests pass","verify_command":"python3 -m unittest discover -s tests"}]'
mythify step 1 in_progress
# ... do the work ...
mythify plan verify 1
mythify step 1 completed "verify run exit 0: parser tests pass"
```

The last line succeeds only because a real check passed first. A red run keeps
the step open: `mythify reflect`, fix the cause, and verify again.

---

## Bestiary

Six ways agents fake done, and the Mythify rule that beats each one.

<p align="center">
  <img src="assets/bestiary.svg" alt="Bestiary of six monsters: the hallucination, the echo, the shrinking suite, the amnesiac, the mimic, and the hydra, each with the Mythify check that beats it." width="100%">
</p>

| Monster | How it fakes done | Beaten by |
|---|---|---|
| The Hallucination | Says the tests pass without running them | `verify run`: a step closes on a recorded exit 0 |
| The Echo | A check that cannot fail: `true`, `exit 0`, a bare `echo` | Mythify warns on no-op verifiers and `status` flags runs that checked nothing |
| The Shrinking Suite | Goes green by running fewer tests | The test-count floor; zero tests is always refused |
| The Amnesiac | Forgets the plan when the chat resets | `.mythify/` on disk; `mythify status` resumes |
| The Mimic | Answers for the human | `--human-input` in the person's own words |
| The Hydra | A review that never ends | Bounded rounds; round four needs `--rethink` |

---

## Trophy room

Mythify keeps no score. These are milestones, and each one comes with the
command that proves you earned it.

| Trophy | Earn it | Proof |
|---|---|---|
| First Blood | Your first `verify run` exits 0 | `mythify status` |
| Comeback | Red, `reflect`, then green on the same step | `mythify report --since start` |
| Clean Clear | Every step of a plan closed on its own check | `mythify plan show` |
| Lorekeeper | Record a surprise with `lesson add` | `mythify lesson list` |
| Continue? | A new session resumes from `.mythify/` | `mythify status` |
| Campaigner | Advance a plan through the strict gate | `mythify campaign status` |

---

## What you get

| Piece | What it is |
|---|---|
| Plans and steps | Goals that close only on a matching `verify run` |
| Checks and gate | Named project checks; `verify gate` for host hooks |
| Maps | Decisions before plans; human answers stay human |
| Reviews | Findings rounds and blast-radius safety cases |
| Memory | Facts, decisions, and lessons that survive the chat |
| MCP | The same commands as tools via `mythify-mcp` |

---

## Learn more

- [docs/start-here.md](docs/start-here.md): shortest path to a working loop
- [docs/guide.md](docs/guide.md): plans, verification, maps, reviews, memory, routing
- [docs/commands.md](docs/commands.md): every command and flag
- [docs/mcp.md](docs/mcp.md): MCP setup for any host
- [docs/campaigns.md](docs/campaigns.md): continue a plan across sessions
- [docs/trials.md](docs/trials.md): compare a small instruction edit against a baseline
- [docs/architecture.md](docs/architecture.md): state model and runtime
- [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md)

## License

MIT. See [LICENSE](LICENSE).
