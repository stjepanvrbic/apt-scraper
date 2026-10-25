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
- The Gaussian-process amplitude (0.5, 1 or 2 times the structured part's prior standard deviation) is selected by that check, and the report keeps every fixed amplitude beside the selected one, whose check is optimistic by the choice.
- A listing without embeddings carries no information: its similarity column takes the column mean and its embedding-kernel row is zero but for its own variance.
- The real Chicago store of the scoring model's experiment no longer exists, so the ranking comparison has not run (Comments). Costs were measured on the 67 stored photos of 5 real Chicago listings from an older test store, and the whole measurement ran on synthetic stand-in stores.
- `scoring.fit_model` is split into `fit_model(inputs)` and `fit_design(design, inputs)`, so the similarity terms fit and tune exactly as the scoring model does. Behavior unchanged, the existing tests pass.
- The embedding cache is migration 8 of the listing store (`embeddings` table), after the distance features' `distances`.
- The embedding environment uses Hermes's Python 3.14, as the search environment does. `requirements-embeddings.txt` is one hash-pinned file for Windows x64 and macOS arm64 (macOS 14 or later, which PyTorch 2.14 requires), resolved separately for each and identical but for `colorama` on Windows.

## Result

Delivered on branch `spike/embeddings_gp`, pull request to `main`, not merged. The ticket stays In Progress: the comparison on real listings, its numbers and the owner's decision are open.

```
profile/skills/ranking/scripts/embeddings.py          CLIP per photo, averaged per room type, MiniLM over traits and text, cached per content
profile/skills/ranking/scripts/embedding_models.py    Gaussian-process model and similarity terms over the scoring model's design
profile/skills/ranking/scripts/measure_embeddings.py  costs, leave-one-out and the five-favorites experiment on a read-only store copy
profile/skills/ranking/requirements-embeddings.txt    48 packages pinned with hashes, Windows x64 and macOS arm64, Python 3.14
profile/skills/gallery/scripts/store.py, schema.py    migration 8, embedding_inputs, save_embeddings, embeddings
profile/skills/gallery/scripts/scoring.py             fit_model split into fit_model and fit_design
docs/research/embedding_preference_model.md, docs/ranking.md, docs/gallery.md
tests/test_embedding_store.py, tests/embeddings/
```

Checks: `python -m unittest discover -s tests` (Python 3.12.10) 451 passed, 0 skipped. `tests/embeddings` in the embedding environment (Hermes's Python 3.14.9) 20 passed, 0 skipped, with the real pinned models. `ruff check profile tests` clean. The environment was built from `requirements-embeddings.txt` with `--require-hashes --only-binary :all: --no-deps` on Windows. The macOS resolution was checked with `uv pip compile` for `aarch64-apple-darwin` and needs only hashes the file holds, but no Mac install ran.

Measured on this machine (numbers and conditions in the research): the environment is 1,064 MB from 221 MB of wheels, the models 664 MB from Hugging Face, peak memory 1.64 GB, CLIP 32 photos per second at 4 threads and 55 at 16, 1.9 CPU seconds per listing of 13.4 photos at 4 threads. The Gaussian-process fit with its check takes 0.3 to 0.4 s at 128 listings and 5 favorites, 8 to 13 s at 300 listings and 50 favorites. On the synthetic stores, where garden units share one listing's photos, the Gaussian process with photos at amplitude 2 put 13 of 15 unseen garden units above every similar-price listing, the scoring model 0, and the similarity term 0.

Found on the way: the rerank's leave-one-out check drops the held-out favorite's row, which ranks it first under a flexible kernel with no signal. The difference is small for the linear scoring model (median 93 against 95 under the weak-negative check on 128 synthetic listings), but any nonlinear model needs the weak-negative check. `test_spec_rerank_timing_with_300_listings_and_50_favorites` fails under Hermes's Python 3.14.9 on this machine at 2.1 to 2.6 s on `main` as well; it passes under Python 3.12. Neither is changed here.

Reviewed by an independent reviewer: no defect in the Newton step, the leave-one-out symmetry or the store. Its findings were fixed: neutral handling of missing embeddings, the selected amplitude compared before and after at the same value, the bar taken from the same run, a listing with no text reported, experiment files validated, a line search that finds no lower objective counts as converged only when the full Newton step moves no score by 10⁻⁴ and raises otherwise, research claims corrected, the stand-in builder committed.

Not verified:

- The comparison on the real Chicago listings, the five-favorites experiment and CLIP's room-type agreement with real records.
- The macOS install and run.
- The installers: building this environment is the installer ticket's work.

## Comments

- 2026-10-25: Blocked on data. The store of the scoring model's five-favorites experiment was in a throwaway Hermes root that was deleted, and the owner's installed profile is off limits. Options: (a) recreate it in a throwaway root with the same criteria (Lake View and Logan Square, at most 2,600 USD a month, at least 2 bedrooms), about 25 minutes of search traffic and about 130 Sonnet extractions, keep the root, judge the garden units as the tester did, and run `measure_embeddings.py`; (b) the owner copies a store that already has feature records and labels with SQLite's backup and the measurement runs on the copy. Recommendation (a): it repeats the scoring model's protocol, while the owner's store may lack feature records: its profile had no features job when the scoring model ticket checked it. The listings will differ from the earlier experiment's, so the bar is that run's scoring model.
