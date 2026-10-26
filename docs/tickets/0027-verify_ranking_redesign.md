---
title: "Verify: ranking that learns the user's taste from listing features"
type: Chore
state: In Progress
priority: 2
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0014]
---

## Outcome

The redesigned ranking is checked end to end on real listings against the umbrella's exit criteria.

## Acceptance

- [ ] Every Spec › Acceptance item for Interview, Listing features, Ranking, Rerank, Overruled preference, Location, Exploration, Pairwise questions and Review pass passes on real listings in a profile installed by the installer.
- [ ] In the five-favorites experiment on real Chicago listings, the unseen garden units rank above the rest of similar price after five garden units are favorited at least as well as the scoring model's own experiment did (6 of 15 strictly above every similar-price listing, mean unseen rank 27 of 128), including units marked only by labels such as `#G`, with the review pass run and its effect on the result recorded.
- [ ] The embedding spike's measurement is recorded, and the owner's decision on the embedding path is recorded with it.
- [ ] The installer on Windows ends in the dashboard with the ranking working on the standard library alone; macOS is run by the owner and its output recorded here.
- [ ] Results recorded here. The checks run on `main`.

## Spec

[Spec › Acceptance](../spec.md#acceptance), [Spec › Listing features](../spec.md#listing-features), [Spec › Ranking](../spec.md#ranking), [Spec › Gallery](../spec.md#gallery), [Spec › Interview](../spec.md#interview)

## Design

A separate test profile installed from `main` by the installer, never the owner's `apt-scraper` profile. Real Zillow listings with the criteria of the scoring model's experiment, one search. The store of the embedding spike's run at `%TEMP%pt-embedding-root` may be reused for the garden-unit experiment to spare listing-site traffic. Hermes sessions with `-Q`, the gallery with `open --no-browser`, browser checks headless with screenshots.

## Deviations

## Result

## Comments

- 2026-10-25: The strict "every unseen garden unit" criterion was not met by the scoring model (7 of 15, then 6 of 16 on a second run) and the owner accepted the model; the embedding path was measured and stays out. The acceptance items were adjusted to what was decided.
