---
title: Scoring model with explanations and the leave-one-out check
type: Feature
state: Todo
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: [0016, 0017]
related: []
---

## Outcome

A rerank fits the scoring model in code on the stored feature records, labels and preferences, ranks and explains every listing with a feature record, reports each favorite's held-out rank, and places two exploration listings in the top 20.

## Acceptance

- [ ] One rerank function, used by the gallery and by the agent's `ranking.py rerank`, writes every rank, reason and explanation in one store transaction. Ranks change at no other time, and no job reranks.
- [ ] Favorites and contact requests count for their listings, dismissals against them, a note's sentiment labels its listing, and pairwise answers count when they exist.
- [ ] Each user note and each stated like or dislike is read once by the model, within an hour, into the record features it names and their direction. A changed preference is read again, an unchanged one never.
- [ ] Stated likes and dislikes and note readings set the starting weights. Spec › Acceptance "Overruled preference" passes in a test.
- [ ] Each reason names the features that moved the score most and the most similar favorite, citing only the listing's record. The stored explanation lists every feature's contribution, and the contributions add up to the score.
- [ ] Each rerank stores and `status` prints every favorite's held-out rank.
- [ ] With more than 20 ranked listings, two unlabeled exploration listings drawn from the posterior are in the top 20 and marked in the store. With a fixed seed, a rerank is reproducible, and without exploration it is reproducible with any seed.
- [ ] Location counts through the areas of the criteria and a location preference learned from the favorites' coordinates, weighted by the location priorities when they exist.
- [ ] Spec › Acceptance "Rerank" timing passes on fixtures of 300 listings and 50 favorites on this Windows machine.
- [ ] The five-favorites experiment of Spec › Acceptance, on real Chicago listings including the garden units, is run and its numbers are in Result.
- [ ] `docs/ranking.md` describes scoring. Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Contracts and invariants](../spec.md#contracts-and-invariants) (reproducible rerank), [Spec › Acceptance](../spec.md#acceptance) (Ranking, Rerank, Overruled preference, Exploration), [Spec › Decisions](../spec.md#decisions) (user-triggered reranks, exploration listings)

## Design

Following option A of the [research](../research/preference_learning_for_listing_ranking.md):

- Bayesian logistic regression. Non-binary features standardized to standard deviation 0.5 (Gelman et al.), a Gaussian prior with means from stated preferences and note readings and zero elsewhere, Newton iterations, and the Laplace posterior for uncertainty and posterior sampling.
- Ratings enter weighted by their confidence. "Not shown" becomes a missing indicator and the mean. Traits are binary features.
- Shown but unlabeled listings are weak negatives, weight about 0.05 to 0.1. That weight and the prior strength are chosen by the leave-one-out check. A contact request weighs more than a favorite. Pairwise answers enter as a logistic term on the two listings' feature difference.
- A contribution is `w_j (x_j - mean_j)`, exact for a linear model. The most similar favorite is the nearest under the learned weights.
- The leave-one-out check refits without each favorite, warm-started from the full fit, and ranks it among the listings that are not favorites. It reports each rank and their median.
- The learned location preference is a smooth density over the favorites' coordinates, as Zillow's location preference work found better than counting by zip code.
- Standard library only, as the research estimates milliseconds per fit. If the timing acceptance fails, profile first.
- Note and preference readings use PR #13's `note_readings` and `preference_readings` tables, now over record features. The model reads run in the extraction job's wakes.
- PR #13's `apt-scraper-ranking` job is not carried over.

## Deviations

## Result
