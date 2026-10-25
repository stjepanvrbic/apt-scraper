---
title: Score and rerank in the gallery, with exploration marks and review results
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: []
---

## Outcome

Rankings has a Score and rerank button that runs a rerank and shows its leave-one-out check, the exploration listings, the review pass's moves with their reasons and a reverted review, and each listing's explanation.

## Acceptance

- [ ] Score and rerank runs a rerank through an authenticated HTTP route and shows the new order. It cannot start a second rerank while one runs, and a failure shows its error.
- [ ] After a rerank, Rankings shows each favorite's held-out rank, then the review's state: running, its moves with their reasons, its flags and findings, or the reverted moves with both check values.
- [ ] Exploration listings carry a visible mark saying they are shown to learn the user's taste.
- [ ] A listing's details show its full explanation: each feature's contribution and the most similar favorite.
- [ ] Listings not ranked yet follow the ranked ones with the reason why: new or changed since the last rerank, waiting for features, or extraction failed.
- [ ] Headless Playwright tests with screenshots at desktop and mobile sizes, against the production build. No owner browser is opened.
- [ ] `docs/gallery.md` updated. Merged to `main` through a reviewed pull request.

## Spec

[Spec › Gallery](../spec.md#gallery), [Spec › Ranking](../spec.md#ranking), [Spec › Acceptance](../spec.md#acceptance) (Rerank, Exploration, Review pass)

## Design

UI ticket. The HTTP routes and their tests come first, against the rerank and review interfaces. The frontend and its visual design are done later by a separate design-focused agent, in the gallery's stack and visual references (`docs/gallery.md`, "Assets and references").

The button could show how many listings and labels changed since the last rerank, so the user knows when one is worth running. The design agent decides.

## Deviations

## Result

- 2026-10-25: Show "© OpenStreetMap contributors" where place distances appear, as the Nominatim usage policy asks.
