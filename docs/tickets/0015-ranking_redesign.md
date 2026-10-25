---
title: Ranking that learns the user's taste from listing features
type:
state: Todo
priority: 2
assignee:
milestone:
parent:
blocked_by: []
related: [0004]
---

## Motivation

The ranking built in 0004 weights similarity by hand over a fixed vocabulary read from 3 photos per listing. Its weights are not learned, and what the vocabulary or those photos missed cannot be recovered: in its experiment, 3 of 15 alike garden units stayed low because their profiles lacked the garden-unit term. The [preference learning research](../research/preference_learning_for_listing_ranking.md) recommends reading every photo once into a feature record and fitting a preference model in code, and the owner accepted that design (option A) with the decisions below.

## Outcome

Every listing is read once per content into a feature record from all its photos and its text. Score and rerank fits a scoring model on those records and the user's labels in code, explains every rank exactly, and checks itself with the leave-one-out check. A review pass improves reasons and order only where that check confirms it. Pairwise questions, exploration listings and location features feed the scoring model, and a measured spike settles whether image and text embeddings join it.

## Design

- Stage 1, once per listing content: code removes duplicate photos and tiles the rest into contact sheets. One extraction child per listing sees every sheet, may open up to 6 photos at full resolution, and returns a validated feature record. Code adds price per square foot, size, unit level from unit labels and location features. Images never enter the main conversation. Extraction runs on Sonnet, measured against the main model on 20 listings.
- Stage 2, on Score and rerank: a Bayesian logistic scoring model over the stored features, fitted locally. Stated likes and dislikes set prior weights, favorites and dismissals are labels, notes are read once into likes and dislikes on the same features, and a favorite can overrule a stated preference. Explanations are exact feature contributions plus the most similar favorite. The leave-one-out check reports how each favorite ranks when held out. Reranks replace the two-minute ranking job of 0004.
- A review pass after each rerank reads the top 50 and the ranking state. Its moves are kept only when the leave-one-out check after the review is no worse.
- Exploration places two posterior-sampled listings in the top 20. The Compare tab asks pairwise questions, also 5 to 8 for cold start after the interview. The gallery logs implicit signals, unused until the leave-one-out check shows they help.
- Location is layered: areas from the criteria, coordinates and a location preference learned from favorites, distance to rapid transit, and travel time to the places the interview captures. The geocoding and routing provider is chosen by research inside its ticket.
- The embedding path (option B of the research: local CLIP image embeddings, sentence embeddings and a Gaussian-process preference model) is built as a spike with measured results. Its dependencies are whatever is cleanest, PyTorch included, and the installers build and check its environment.
- PR #13 (`feat/learning_ranking`) stays open as reference. Its store changes (merged feature keys, note and preference readings), its never-failing cron script with attempt counts, and its five-favorites experiment setup can be reused.
- Children that change the gallery's visible UI have their frontend done by a separate design-focused agent, as their Design says.

## Spec

- [Spec › Listing features](../spec.md#listing-features), [Spec › Ranking](../spec.md#ranking), [Spec › Gallery](../spec.md#gallery), [Spec › Interview](../spec.md#interview), [Spec › Installation and setup](../spec.md#installation-and-setup)
- [Spec › Contracts and invariants](../spec.md#contracts-and-invariants), [Spec › Acceptance](../spec.md#acceptance), [Spec › Decisions](../spec.md#decisions)

## Exit criteria

- Every Spec › Acceptance item for Interview, Listing features, Ranking, Rerank, Overruled preference, Location, Exploration, Pairwise questions and Review pass passes on real listings in a profile installed by the installer.
- In the five-favorites experiment on real Chicago listings, every unseen garden unit ranks above the rest of similar price after five garden units are favorited, including units marked only by labels such as `#G`.
- The embedding spike's measurement is recorded, and the owner has decided whether the embedding path joins the ranking.
- The installers build and check every ranking environment on Windows and macOS.
