---
title: The leave-one-out check refits the held-out favorite as a weak negative
type: Bug
state: Todo
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: []
related: [0020, 0025]
---

## Outcome

The rerank's leave-one-out check treats the held-out favorite the way it treats any unlabeled ranked listing, a weak negative, instead of dropping its row, so the check cannot rank a held-out favorite first on no signal and the prior scale and weak-negative weight it tunes are chosen without that bias.

## Acceptance

- [ ] The held-out refit labels the favorite as a weak negative, with the same low-rank update as today, and the rerank takes no longer.
- [ ] A test shows a model with a free parameter per listing cannot rank the held-out favorite first under the check.
- [ ] `docs/ranking.md` describes the check as it now works.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Terms](../spec.md#terms) (Leave-one-out check), [Spec › Ranking](../spec.md#ranking)

## Design

- Found by the embedding measurement: on the same 129 listings the current check reports a median held-out rank of 20 and mean 46.2 for the five favorites, against 25 and 48.2 when the held-out favorite is a weak negative. See [the embedding research](../research/embedding_preference_model.md).

## Deviations

## Result

## Comments
