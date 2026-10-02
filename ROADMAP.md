This roadmap is a Mythify product plan, rendered by `mythify product show --markdown`
from the maintainer's product record. It stays a draft until the maintainer approves
it with `mythify product approve --human-input` in their own words. Every bet carries a
kill criterion and a named decider; only that decider sets its verdict, through
`mythify product decide --human-input`.

# Mythify

- Product: `mythify`
- Status: draft (not approved)
- Stage: growth
- Appetite: One quarter: the 6.x release train through 2026-12-31
- Readiness: ready

## Problem

People who direct AI coding agents get confident completion claims that nobody checked, and the plan, the decisions, and the evidence disappear when the chat ends.

## Primary user

A developer or builder directing an AI coding agent on real work who has been burned by a done that was not done

## Outcomes

| ID | Outcome | Metric | Baseline | Target | Source | Last measurement |
| --- | --- | --- | --- | --- | --- | --- |
| O1 | A completion claim without an executed passing check is refused every time | strict verified-step gate regression tests passing | all passing in 5.8.0 | all passing on every release | decided | passed (exit 0, 2026-10-02T05:46:29+00:00) |
| O2 | Mythify runs with any model and ships no third-party code | model or vendor names and non-standard-library imports in the shipped runtime | about 190 vendor-name lines and 3 npm runtime packages in 5.8.0 | 0 and 0 | decided | passed (exit 0, 2026-10-02T05:46:30+00:00) |
| O3 | Any model can load the whole protocol cheaply, small local models included | bytes of always-loaded protocol text in AGENTS.md | 36,510 bytes in 5.8.0 | at most 12,000 bytes | decided | passed (exit 0, 2026-10-02T05:46:30+00:00) |
| O4 | A new user reaches a first verified step within five minutes | scripted quickstart from install to a completed, verified step | not measured; the 5.x quickstart failed on machines without Node | passes on every release on Python 3.9 and 3.13 | hypothesis | passed (exit 0, 2026-10-02T05:46:32+00:00) |
| O5 | The written trail never contradicts the code | drift findings from the maintainer lint | 112 drift entries recorded in the 6.0 review of 5.8.0 | 0 on every release | decided | passed (exit 0, 2026-10-02T05:46:34+00:00) |

## Non-goals

- **N1** Choosing, ranking, or switching models for the user. Why: Hosts and users know their models; vendor routing tables go stale within months and tie the product to vendors. Revisit when: a host offers a vendor-neutral capability interface that needs no model table.
- **N2** Running or orchestrating agents in parallel. Why: Hosts already provide subagents; Mythify's job is the evidence, not the workers. Revisit when: major hosts drop native delegation.
- **N3** A hosted service, accounts, or telemetry. Why: Evidence belongs in the user's repository, readable without trusting a third party. Revisit when: three separate teams ask for a shared view across repositories.
- **N4** Project management features such as assignees, estimates, and sprints. Why: Trackers already do this; Mythify records proof, not workload. Revisit when: users repeatedly export Mythify plans into a tracker by hand.

## Bets

| Priority | ID | Hypothesis | Outcomes | Status | Verdict | Kill criterion | Decider | Decide by | Plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | B1 | A pre-registered study with any agent CLI shows fewer false completion claims when Mythify is present | O1 | proposed | pending | 30 paired, counterbalanced trials across 3 scenarios show no reduction in unverified completion claims; then the README stops implying an efficacy effect | Maintainer (hannsxpeter) | 2026-12-31 | no plan |
| 2 | B2 | A standard Python package install (one command, still zero dependencies) cuts setup friction | O4 | proposed | pending | packaging needs any third-party runtime dependency, or the quickstart test cannot pass from the package | Maintainer (hannsxpeter) | 2027-03-31 | no plan |
| 3 | B3 | Product plans and status read as a one-page brief for leads who do not read code | O5 | proposed | pending | two leads given product show --markdown cannot say what is verified and what is not within five minutes | Maintainer (hannsxpeter) | 2027-01-31 | no plan |

## Risks

### Value (will the user want it)

- **R1** Users see the gates as ceremony and switch them off. Mitigation: Proportional ceremony: trivial work pays nothing, focused fixes need one verify run. Validation: Track issues and discussions that mention MYTHIFY_REQUIRE_VERIFIED_STEP=0.

### Usability (can the user work it)

- **R2** Too many commands for a first session. Mitigation: 6.0 cuts the CLI from 36 to 22 top-level commands; status, report, and summary cover most days. Validation: tests.test_quickstart passes using only the commands in the README quickstart.

### Feasibility (can we build it)

- **R3** The hand-written MCP server falls behind the MCP specification. Mitigation: Tools delegate to the CLI so they cannot drift from it; both handshake eras are tested. Validation: python3 -m unittest tests.test_mcp_server.

### Viability (does it work for the business)

- **R4** One maintainer cannot keep docs and code aligned across releases. Mitigation: scripts/lint.py and its self-test fail the build on drift. Validation: python3 scripts/lint.py runs in CI on every pull request.

## Traceability

- `outcome_without_bet` O2: Outcome O2 has no live bet, so nothing is planned to move it.
- `outcome_without_bet` O3: Outcome O3 has no live bet, so nothing is planned to move it.
