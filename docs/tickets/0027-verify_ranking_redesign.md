---
title: "Verify: ranking that learns the user's taste from listing features"
type: Chore
state: Todo
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: [0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026]
related: [0014]
---

## Outcome

The redesigned ranking is checked end to end on real listings against the umbrella's exit criteria.

## Acceptance

- [ ] Every Spec › Acceptance item for Interview, Listing features, Ranking, Rerank, Overruled preference, Location, Exploration, Pairwise questions and Review pass passes on real listings in a profile installed by the installer.
- [ ] In the five-favorites experiment on real Chicago listings, every unseen garden unit ranks above the rest of similar price after five garden units are favorited, including units marked only by labels such as `#G`.
- [ ] The embedding spike's measurement is recorded, and the owner's decision on the embedding path is recorded with it.
- [ ] The installers build and check every ranking environment on Windows and macOS.
- [ ] Results recorded here. The checks run on `main`.

## Spec

[Spec › Acceptance](../spec.md#acceptance), [Spec › Listing features](../spec.md#listing-features), [Spec › Ranking](../spec.md#ranking), [Spec › Gallery](../spec.md#gallery), [Spec › Interview](../spec.md#interview)

## Design

A separate test profile installed from `main` by the installer, never the owner's `apt-scraper` profile. Real Zillow listings with the criteria of PR #13's experiment. Hermes sessions with `-Q`, the gallery with `open --no-browser`, browser checks headless with screenshots.

## Deviations

## Result
