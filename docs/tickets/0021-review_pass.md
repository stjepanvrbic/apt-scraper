---
title: Review pass after each rerank
type: Feature
state: Todo
priority: 3
assignee:
milestone:
parent: 0015
blocked_by: [0020]
related: []
---

## Outcome

After each rerank, the model reviews the top 50 and the state of the whole ranking. It rewrites their reasons, flags conflicts with stated preferences and may move listings, and its moves are kept only when the leave-one-out check after the review is no worse than before.

## Acceptance

- [ ] One text-only review per rerank. It reads the top 50 with their feature records, contributions and reasons, the stated preferences, the note readings, and the ranking state: label counts, held-out ranks, feature weights that run against a stated preference, listings waiting for or failing extraction, and the tag list.
- [ ] A rewritten reason cites only the listing's feature record. Code rejects one that names a feature the record lacks and keeps the reason it had.
- [ ] Each move has a reason. After the moves the leave-one-out check runs again. When it is worse, every move is reverted, and the result records the reverted moves and both values.
- [ ] Each listing whose rank conflicts with a stated preference is flagged with that preference.
- [ ] What looks wrong in the ranking state, such as two traits that mean the same thing or one weight carried by a single label, is reported in the rerank result and in `status`.
- [ ] Tests with controlled review outputs: one that moves favorites down is reverted, one that moves them up stands, and an invalid reason is rejected.
- [ ] A live review on real listings, with its token use in Result.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) (review pass), [Spec › Acceptance](../spec.md#acceptance) (Review pass), [Spec › Decisions](../spec.md#decisions) (review pass moves stand only when the check confirms them)

## Design

- The review sees no labels: favorites are not marked, and the favorite part of each reason is added by code after the review. Favorites then test its moves independently.
- Moves are kept as per-listing score adjustments, so they apply to the full fit and to each held-out fit. "Worse" means a higher mean held-out rank of the favorites.
- The review gives a verbalized confidence for each move, which the research found better calibrated than token probabilities.
- The gallery shows the code's order at once and the reviewed order when the review finishes. The review runs as an agent turn started from the rerank, through the gateway client the Notes tab uses or an on-demand Hermes job. The implementer picks one after checking both in Hermes's source.
- Listing text is data, and the review never follows instructions in it.

## Deviations

## Result
