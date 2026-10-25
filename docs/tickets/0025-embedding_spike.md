---
title: "Spike: image and text embeddings with a Gaussian-process preference model"
type: Chore
state: In Progress
priority: 3
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: []
---

## Outcome

Working code computes local CLIP image embeddings and sentence embeddings for every listing and fits a Gaussian-process preference model on them, and a measurement on real listings shows whether it ranks the favorites better than the scoring model alone.

## Acceptance

- [ ] Code computes a CLIP image embedding of every unique photo, averaged per room type, and a sentence embedding of the description and traits, on the CPU, once per listing content, cached in the store.
- [ ] A Gaussian-process preference model over the structured features plus the embeddings fits on the same labels as the scoring model.
- [ ] Measured on the real Chicago listings with the leave-one-out check and the five-favorites experiment, including the garden units: the scoring model alone, the Gaussian-process model, and the scoring model with an embedding term for the most similar favorite. Also measured: CPU time per listing to embed and per fit, memory, install size and weight size.
- [ ] The numbers and a recommendation are in `docs/research/`. The owner decides from them whether the embedding path joins the ranking.
- [ ] The dependencies are chosen, pinned with hashes, and listed for the installers.
- [ ] Delivered as a pull request to `main` with the measurement. The owner decides on merging with the result.

## Spec

None: a measured investigation. Its outcome may lead to a spec change through the owner's decision.

## Design

Option B of the [research](../research/preference_learning_for_listing_ranking.md): `open_clip_torch` with ViT-B-32 (about 605 MB of weights), `sentence-transformers` with all-MiniLM-L6-v2 (about 91 MB), and BoTorch's `PairwiseGP` or a Gaussian-process classifier in GPyTorch. Whatever is cleanest is fine, PyTorch included, in its own virtual environment. CLIP's zero-shot labels can also check extraction's room types.

## Deviations

## Result
