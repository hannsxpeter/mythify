# Mythify roadmap

Where Mythify is going: the outcomes the project is steered by, what it will
not do, and the bets that could move those outcomes. Every bet has a kill
criterion and a decider.

## Outcomes

- **O1** A completion claim without an executed passing check is refused every time.
  - Metric: strict verified-step gate regression tests passing
  - Baseline: all passing in 5.8.0
  - Target: all passing on every release
- **O2** Mythify runs with any model and ships no third-party code.
  - Metric: model or vendor names and non-standard-library imports in the shipped runtime
  - Baseline: about 190 vendor-name lines and 3 npm runtime packages in 5.8.0
  - Target: 0 and 0
- **O3** Any model can load the whole protocol cheaply, small local models included.
  - Metric: bytes of always-loaded protocol text in AGENTS.md
  - Baseline: 36,510 bytes in 5.8.0
  - Target: at most 12,000 bytes
- **O4** A new user reaches a first verified step within five minutes.
  - Metric: scripted quickstart from install to a completed, verified step
  - Baseline: not measured; the 5.x quickstart failed on machines without Node
  - Target: passes on every release on Python 3.9 and 3.13
- **O5** The written trail never contradicts the code.
  - Metric: drift findings from the maintainer lint
  - Baseline: 112 drift entries recorded in the 6.0 review of 5.8.0
  - Target: 0 on every release

## Non-goals

- **N1** Choosing, ranking, or switching models for the user.
  - Why: Hosts and users know their models; vendor routing tables go stale within months and tie the product to vendors. Printing a routing policy file the user owns, unchanged and as advisory text, is not choosing: Mythify still never picks, ranks, or switches a model.
  - Revisit when: a host offers a vendor-neutral capability interface that needs no model table.
- **N2** Running or orchestrating agents in parallel.
  - Why: Hosts already provide subagents; Mythify's job is the evidence, not the workers.
  - Revisit when: major hosts drop native delegation.
- **N3** A hosted service, accounts, or telemetry.
  - Why: Evidence belongs in the user's repository, readable without trusting a third party.
  - Revisit when: three separate teams ask for a shared view across repositories.
- **N4** Project management features such as assignees, estimates, and sprints.
  - Why: Trackers already do this; Mythify records proof, not workload.
  - Revisit when: users repeatedly export Mythify plans into a tracker by hand.

## Bets

- **B1** A pre-registered study with any agent CLI shows fewer false completion claims when Mythify is present.
  - Moves: O1
  - Kill criterion: 30 paired, counterbalanced trials across 3 scenarios show no reduction in unverified completion claims; then the README stops implying an efficacy effect.
  - Decider: Maintainer (hannsxpeter), decide by 2026-12-31
- **B2** A standard Python package install (one command, still zero dependencies) cuts setup friction.
  - Moves: O4
  - Kill criterion: packaging needs any third-party runtime dependency, or the quickstart test cannot pass from the package.
  - Decider: Maintainer (hannsxpeter), decide by 2027-03-31
