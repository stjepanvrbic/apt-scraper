---
title: Ranking that learns from favorites and notes
type: Feature
state: Canceled
priority: 2
assignee: agent
milestone:
parent:
blocked_by: [0003, 0008]
related: [0015]
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

## Comments

- 2026-10-25: Canceled. The owner redesigned the ranking: a feature record read once per listing content from every photo, and a scoring model fitted in code on each rerank the user asks for, with a review pass, exploration listings and pairwise questions. That replaces this ticket's cron reranking, its 3 photos per listing and its hand-weighted fixed vocabulary, so its brief would mislead. 0015 carries the outcome. PR #13 (`feat/learning_ranking`, https://github.com/stjepanvrbic/apt-scraper/pull/13) stays open and unmerged as the reference baseline: its experiment results, store changes and never-failing job pattern are reused by the redesign.
