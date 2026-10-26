---
title: The fit is separate from the leave-one-out check
type: Chore
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

`fit_model` returns the fit alone; the leave-one-out check is a function on that fit which only the rerank calls. Compare questions and the implicit-signal diagnostic take the fit, so a `GET /api/compare` costs a fit, not a rerank.

## Acceptance

- [ ] `questions()` and the diagnostic no longer compute held-out scores; `rank_listings` composes fit and check; the `Model` type carries held-out scores only where a rerank computed them.
- [ ] Measured: `pairwise_questions(store, 1)` on the 300-listing, 50-favorite fixture takes about the fit's time (0.17 s best of three on the review machine), not 0.64 s.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Gallery](../spec.md#gallery)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found by the deep review.
