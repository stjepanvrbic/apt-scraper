---
title: Ranking that learns from favorites and notes
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: [0003, 0008]
related: []
---

## Outcome

Every available listing has a rank and a reason, and the ranking follows the user's favorites, dismissals, notes and preferences.

## Acceptance

- [ ] Every available listing shows a rank and a one-line reason.
- [ ] After the tester favorites five listings they consider alike, the unseen listings they judge alike rank above the rest of similar price.
- [ ] Dismissing listings lowers similar listings.

## Spec

[Spec › Ranking](../spec.md)

## Design

The model scores each listing once against the preferences and stores features. Re-ranking after a favorite or note uses the stored features, not a new model read of every listing.

## Deviations

## Result
