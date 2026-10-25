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

- [x] Code computes a CLIP image embedding of every unique photo, averaged per room type, and a sentence embedding of the description and traits, on the CPU, once per listing content, cached in the store.
- [x] A Gaussian-process preference model over the structured features plus the embeddings fits on the same labels as the scoring model.
- [ ] Measured on the real Chicago listings with the leave-one-out check and the five-favorites experiment, including the garden units: the scoring model alone, the Gaussian-process model, and the scoring model with an embedding term for the most similar favorite. Also measured: CPU time per listing to embed and per fit, memory, install size and weight size.
- [ ] The numbers and a recommendation are in `docs/research/`. The owner decides from them whether the embedding path joins the ranking.
- [x] The dependencies are chosen, pinned with hashes, and listed for the installers.
- [ ] Delivered as a pull request to `main` with the measurement. The owner decides on merging with the result.

## Spec

None: a measured investigation. Its outcome may lead to a spec change through the owner's decision.

## Design

Option B of the [research](../research/preference_learning_for_listing_ranking.md): `open_clip_torch` with ViT-B-32 (about 605 MB of weights), `sentence-transformers` with all-MiniLM-L6-v2 (about 91 MB), and BoTorch's `PairwiseGP` or a Gaussian-process classifier in GPyTorch. Whatever is cleanest is fine, PyTorch included, in its own virtual environment. CLIP's zero-shot labels can also check extraction's room types.

## Deviations

- The Gaussian-process model is a numpy implementation of the scoring model's own likelihood (label weights, weak negatives, pairwise terms on differences), with the scoring model's prior as a linear kernel and mean function plus a squared-exponential kernel over the embeddings, instead of BoTorch's `PairwiseGP` or GPyTorch. Without the embedding kernel it reproduces the scoring model's scores within 10⁻⁶, so the comparison isolates the embeddings. BoTorch and GPyTorch are not dependencies.
- The comparison checks every model with one leave-one-out definition: the held-out favorite is refitted as a weak negative, as any unlabeled ranked listing. The rerank's check drops the held-out favorite's row, which a Gaussian process exploits: with 50 favorites and no signal it gave a held-out median of 1. The rerank's own check is reported next to it for the scoring model. The rerank itself is unchanged.
- `scoring.fit_model` is split into `fit_model(inputs)` and `fit_design(design, inputs)`, so the similarity terms fit and tune exactly as the scoring model does. Behavior unchanged, the existing tests pass.
- The embedding cache is migration 6 of the listing store (`embeddings` table). It conflicts in number with any other branch that adds a migration, and the second to merge renumbers.
- The embedding environment uses Hermes's Python 3.14, as the search environment does. `requirements-embeddings.txt` is one hash-pinned file for Windows x64 and macOS arm64 (macOS 14 or later, which PyTorch 2.14 requires), resolved separately for each and identical but for `colorama` on Windows.

## Result
