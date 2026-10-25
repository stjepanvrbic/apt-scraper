# Image and text embeddings with a Gaussian-process preference model

Question: do local CLIP photo embeddings and sentence embeddings of the listing text, used in a Gaussian-process preference model or as a similarity term in the scoring model, rank the user's favorites better than the scoring model alone, and what do they cost on the user's machine?

It informs the owner's decision on option B of the [preference learning research](preference_learning_for_listing_ranking.md): whether the embedding path joins the scoring pipeline, replaces the scoring model, or stays out.

## Status

Measured on 129 real Chicago listings, found by one search with the scoring model's experiment criteria (Lake View and Logan Square, at most 2,600 USD a month, at least 2 bedrooms) on 2026-10-25 and extracted on Sonnet in a throwaway Hermes root. Neither embedding model ranks the alike garden units better than the scoring model alone, and the Gaussian-process model with photos ranks them worse as its amplitude grows. [Comparison on real listings](#comparison-on-real-listings) has the numbers, and the recommendation is that the embedding path stays out.

## What was built

All of it is in `profile/skills/ranking/` and runs in an environment of its own, apart from the scoring model, which keeps to the standard library.

- `scripts/embeddings.py` embeds each listing once per content. Every photo of its feature record goes through CLIP ViT-B/32 (OpenAI weights, `timm/vit_base_patch32_clip_224.openai` at revision `a6f597a3`). The unit vectors are averaged over all photos (`photos`) and over each room type the record gives (`photos:kitchen` and so on). The record's traits and concerns, then the title and description in chunks of 150 words, go through all-MiniLM-L6-v2 (`sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243`), and the chunk vectors are averaged (`text`). The vectors are cached in the listing store's new `embeddings` table (migration 8), keyed by listing, content fingerprint and model, so a listing whose content has not changed is never embedded again. A listing none of whose photos loads, or that has no text, is reported on every run and stays waiting. The step also counts how often CLIP's zero-shot room type agrees with the record's.
- `scripts/embedding_models.py` holds the two models measured against the scoring model. Both use the scoring model's design, labels, label weights and prior.
  - The similarity terms add columns to the scoring model's design: each listing's cosine similarity to its most similar favorite other than itself, for photos (the mean over six photo parts), for text, or both. The leave-one-out check recomputes them without the held-out favorite, as it does nearness to the favorites.
  - The Gaussian-process model puts a latent score on each listing with the scoring model's likelihood. Its prior is the scoring model's prior in function space, a linear kernel over the design with the stated preferences' prior means as its mean function, plus a squared-exponential kernel over the embeddings, whose length is the median distance between listings. The embedding kernel's variance is the square of a relative amplitude, 0.5, 1 or 2, times the prior variance of the structured part, so amplitude 1 weighs the embeddings as much as the structured features and amplitude 2 four times as much. Without the embedding kernel it is the scoring model: a test fits both on 40 listings with favorites, a dismissal and a pairwise answer, and the scores agree within 10⁻⁶.
  - A listing without embeddings carries no information in either model. Its similarity to a favorite is unknown, so its similarity column takes the column mean, and its row of the embedding kernel is zero but for its own variance.
- `scripts/measure_embeddings.py` copies a store read-only, embeds the copy, fits every model and prints the report described below.
- `tests/embeddings/stand_in_stores.py` builds the stand-in stores of [Stand-in checks](#stand-in-checks).

The Gaussian-process model is a short numpy implementation of the scoring model's exact likelihood, label weights and pairwise terms included, rather than BoTorch or GPyTorch, so neither is a dependency and the comparison differs from the scoring model only in the kernel.

## The leave-one-out check needed one change for a Gaussian process

The scoring model's check refits without the held-out favorite's row. Every other ranked listing without a label is a weak negative, so the held-out favorite becomes the only listing with no label at all. A linear model barely notices one missing row. A kernel that gives each listing room of its own leaves the held-out favorite at the prior mean while the weak negatives pull every other unlabeled listing below it. On a synthetic store with 50 favorites and no signal behind them, that check gave the Gaussian-process model a held-out median of 1.

The measurement therefore labels the held-out favorite as a weak negative, as an unlabeled ranked listing is, and checks every model this way: the scoring model and the similarity terms are the Gaussian-process model without the embedding kernel, at the prior scale and weak-negative weight their own tuning chose. A test pins it with a white-noise kernel and 9 of 60 listings favorited (every seventh), so no feature or kernel explains them. The favorites' mean held-out rank is 37 of 51 under this check and 12.4 under the row-dropping check, and the test requires it to lie between 20.4 and 45.9. The report keeps the rerank's own check for the scoring model as `rerank_check`. On the 128-listing stand-in the weak-negative check gives a median of 95 and the rerank's check 93. On the real listings with the five garden favorites, the rerank's check gives held-out ranks 1, 16, 20, 79 and 115 (median 20, mean 46.2), and the weak-negative check 3, 18, 25, 80 and 115 (median 25, mean 48.2): even for the linear model, the rerank's check is optimistic by a few ranks.

Recommendation for the rerank: label the held-out favorite as a weak negative in the rerank's own check too. A favorite that is held out stands for an unseen listing, and an unseen ranked listing is a weak negative in the fit, so the check should refit it as one. The rerank uses the check to choose its prior scale and weak-negative weight, so an optimistic check also biases that choice, toward settings that rank a label-free listing high. The change keeps the cost: the held-out refit already takes the favorite's rows out of the full fit's Hessian (the Woodbury identity), and relabeling the row instead of dropping it is the same kind of low-rank change.

The Gaussian-process amplitude is selected by this check, so the selected row's check is optimistic by the choice among three. The rows for each fixed amplitude are not selected. The scoring model's prior scale and weak-negative weight are selected by the rerank's own check, as in every rerank.

## Costs

Measured on this Windows machine (AMD Ryzen 9 9950X3D, 16 cores, 61.6 GB of memory) with Hermes's Python 3.14.9. The embedding figures come from the 1,596 photos of the 129 real listings, and the 4-thread figures from the 67 photos of 5 real Chicago listings in an older test store, all downloaded from their stored URLs.

| | |
| --- | --- |
| Download, packages | 221 MB of wheels on Windows, 199 MB on Apple Silicon. PyTorch is 118 MB of it (PyPI JSON, 2026-10-25) |
| Download, models | 696 MB: CLIP 605,143,284 bytes, MiniLM 90,868,376 bytes, plus configuration and tokenizer files (Hugging Face API) |
| Installed environment | 1,064 MB |
| Models on disk | 664 MB |
| Install time | 1 min 56 s for the environment from PyPI, 9 s for the models from Hugging Face, on this connection |
| Loading the models | 3.8 to 4.4 s |
| Embedding, 16 threads (PyTorch's default here) | CLIP alone 60 photos per second. Per listing of 12.4 photos, photos and text: 0.24 s, 3.8 CPU seconds, about 31 s for the 129 |
| Embedding, 4 threads | CLIP alone 32 photos per second. Per listing of 13.4 photos: 0.48 s, 1.9 CPU seconds |
| Downloading photos | 0.2 s per listing on average, 8 at a time |
| Peak memory | 2.03 GB embedding the 129 listings, 1.64 GB for the 5 |
| Fit and leave-one-out, 129 real listings, 5 favorites | Scoring model with its tuning and the weak-negative check 1.5 s. Gaussian-process model 0.4 to 1.5 s per amplitude |
| Fit and leave-one-out, 300 listings, 50 favorites | Scoring model 1.5 s. Gaussian-process model 8 to 13 s per amplitude, about 0.2 s per refit |

Estimates from these figures: 150 listings of 20 photos each, 3,000 photos, take about 1 minute of CLIP at 16 threads and 1 min 35 s at 4 threads, once. A Gaussian-process rerank with its check at 300 listings and 50 favorites takes 8 to 13 s per amplitude, so it misses Spec › Acceptance "Rerank" (2 seconds) unless the check is approximated, as the scoring model's tuning does with one Newton step.

## Running the comparison

Install the environment from the hash-pinned requirements with the Python Hermes runs cron scripts with (`hermes pm env` lists it), then run the measurement on the store:

```powershell
$python = "$env:LOCALAPPDATA\hermes\tools\python-3.14.9+20261021-win32-x64\python.exe"
$environment = "$env:LOCALAPPDATA\apt-scraper-embeddings\environment"
& $python -m venv --clear $environment
& "$environment\Scripts\python.exe" -m pip install --disable-pip-version-check --require-hashes --only-binary :all: --no-deps -r profile\skills\ranking\requirements-embeddings.txt
& "$environment\Scripts\python.exe" profile\skills\ranking\scripts\measure_embeddings.py <store>\listings.sqlite3 <work directory> --models <models directory> --experiment experiment.json
```

On macOS the environment's Python is `bin/python`. PYTHONPATH and PYTHONHOME must be unset, because Hermes may point them at its own packages. The measurement writes to two places: the models directory, where it downloads the models from Hugging Face when they are missing and checks each weight file's SHA-256, and the work directory, which holds the store's copy and the embeddings. It downloads each listing's photos from the URLs its feature record holds and leaves the store itself unchanged.

`experiment.json` is `{"favorites": [five ids], "alike": [ids]}`: the five favorited garden units and the unseen listings the tester judges alike to them. Every id must be a ranked listing of the store, and none may be in both lists. The report replaces the store's favorites with the five, keeps its other labels, and fits each model before and after. For each unseen alike listing, the rest of similar price are the ranked listings neither alike nor favorites within 10% of its monthly price. The report gives, per model, the comparisons won against them, the unseen listings above all of them, the mean ranks, and the five favorites' held-out ranks, the same quantities as the scoring model's experiment. It also gives each model's leave-one-out check on the store's own labels, and the costs above.

## Comparison on real listings

The tester's judgment followed the scoring model's experiment. Of the 129 listings, 21 are garden units: 15 whose unit label says garden or basement (`#G`, `#Gn`, `Unit GRDN`, `Unit GW`, `Unit Garden`, `#Basement`) and 6 whose description calls the listing a garden unit or garden apartment. Twelve other matches of "garden" or "basement" were left out because they describe basement laundry, storage or flex space, or a shared garden. The five favorites are the experiment's five, all found again: 455063308 (`#G`), 441025098 (text only), 2106063102 (`#G`), 2064093099 (text only, unit label `1B`) and 2079514484 (`#G`). The other 16 are unseen. For each, the rest of similar price are the ranked listings neither alike nor favorites within 10% of its monthly price, 635 comparisons in all.

| 129 listings, 5 garden favorites, 16 unseen | Comparisons won, after | Unseen above every similar-price listing | Mean rank of the unseen | Favorites' held-out ranks, median and mean |
| --- | --- | --- | --- | --- |
| Scoring model | 545 of 635 | 6 of 16 | 29.5 | 25, 48.2 |
| With similarity to the most similar favorite, photos | 548 | 7 | 28.8 | 25, 47.4 |
| With similarity to the most similar favorite, text | 545 | 6 | 29.5 | 27, 49.4 |
| With similarity to the most similar favorite, both | 546 | 7 | 29.0 | 25, 47.6 |
| Gaussian process, photos, amplitude 0.5 | 533 | 6 | 29.8 | 23, 49.0 |
| Gaussian process, photos, amplitude 1 | 511 | 6 | 32.7 | 32, 49.4 |
| Gaussian process, photos, amplitude 2 | 464 | 6 | 40.4 | 59, 56.0 |
| Gaussian process, text, amplitude 0.5 | 538 | 6 | 30.7 | 22, 46.2 |
| Gaussian process, text, amplitude 1 | 521 | 6 | 34.4 | 16, 38.8 |
| Gaussian process, text, amplitude 2 | 451 | 3 | 46.9 | 20, 27.0 |
| Gaussian process, both, amplitude 0.5 | 536 | 6 | 30.1 | 22, 47.6 |
| Gaussian process, both, amplitude 1 | 517 | 6 | 32.8 | 22, 44.0 |
| Gaussian process, both, amplitude 2 | 472 | 4 | 43.1 | 30, 40.8 |

Before the favorites, every model wins 62 of the 635 comparisons, puts no unseen garden unit above every similar-price listing, and ranks the unseen at 107.7 on average. After them, the scoring model ranks the five favorites 1 to 5 and the other listings at 73.1 on average. The leave-one-out check selects amplitude 0.5 for photos and 2 for text and for both.

- The similarity term changes almost nothing: one more unseen garden unit above every similar-price listing with photos (7 of 16 against 6), and three more comparisons won. Its learned photo weight is negative (−0.47): among these listings, resembling a favorite's photos counts slightly against a listing once the record's features are in the model.
- The Gaussian-process model with photos does no better at amplitude 0.5 and worse at 1 and 2. In CLIP's embedding the unseen garden units are hardly nearer the favorites than other listings are: their mean cosine similarity to the most similar favorite is 0.883, against 0.872 for the other listings and 0.868 among the favorites themselves.
- The Gaussian-process model with text improves the five favorites' held-out ranks (mean 48.2 to 27.0 at amplitude 2) while it ranks the unseen garden units worse (3 of 16, mean rank 46.9), so the check selects the text variant that ranks the unseen units worst. Text separates garden units a little more than photos do (mean similarity to the most similar favorite 0.722 for the unseen garden units, 0.691 for the other listings, 0.733 among the favorites), and one unseen unit, 465862055, has nearly the words of the favorite 455063308 (similarity 0.89, the same landlord's "LARGE 2 Bed Garden in PRIME" listing). Why the check and the experiment disagree is not settled by this measurement.
- The same six units stay low in the scoring model, the photo similarity term, the photo Gaussian process at amplitude 1 and the text Gaussian process at amplitude 2: 2069930270 (`#Gn`), 466125881 (`#G`), 2090032432 (`Unit Garden`), 113953556 (`#G`), 70459262 (text only) and 466054100 (`#G`) rank between 37 and 119 in all four.

CLIP's zero-shot room type agrees with the extraction's on 63% of the 1,596 photos.

## Stand-in checks

These ran the whole measurement and show that each model learns from a cue it can see. They say nothing about real listings. `tests/embeddings/stand_in_stores.py synthetic` builds stores of random feature records whose photos are the 67 real ones served from a local web server: 20 "garden units" draw theirs from the one listing with 30 photos, every other listing from the other 37.

| 300 listings, 5 garden favorites, 15 unseen | Comparisons won, before and after | Unseen above every similar-price listing | Mean rank of the unseen | Favorites' held-out mean |
| --- | --- | --- | --- | --- |
| Scoring model | 524, 566 of 1,144 | 0 of 15 | 167 | 71 |
| With similarity to the most similar favorite (photos) | 524, 761 | 0 | 119 | 51 |
| Gaussian process, photos, selected amplitude 2 | 578, 1,141 | 13 | 15 | 12 |
| Gaussian process, text, selected amplitude 0.5 | 514, 551 | 0 | 170 | 73 |

The photo cue is planted, so the Gaussian process finding it is expected. The similarity term found less of it, probably because the maximum similarity to five favorites is one column whose weight five labels must learn against nearly 300 weak negatives. Text carries no planted cue here, and the text kernel did not help.

## Recommendation

The embedding path stays out of the scoring pipeline. On real listings, neither the similarity term nor the Gaussian-process model ranks the unseen garden units better than the scoring model alone, beyond one unit of 16 for the photo similarity term. The Gaussian-process model with photos gets worse as the embeddings weigh more, and with text it improves the leave-one-out check while ranking the unseen units worse, so the check would select it for the wrong reason. Replacing the scoring model is ruled out a second time by the 2-second rerank: the Gaussian-process rerank with its check takes 8 to 13 s at 300 listings and 50 favorites, and it gives up the exact per-feature explanations. Joining as the similarity term would cost 1.7 GB on disk, 0.9 GB of downloads and 2 GB of memory per embedding run for a difference within noise.

The units the scoring model misses stay low with the embeddings as well, so embeddings do not recover what the feature record lacks for them. The scoring model's experiment found its misses differ from the favorites in renovation and finish, which the user may in fact care about. The installers need not build the embedding environment, and the code can stay unmerged as the record of this measurement.

## Sources

1. Hugging Face model API, file lists, sizes and SHA-256 of `timm/vit_base_patch32_clip_224.openai` at `a6f597a30f7b82c51704746581f9a4e41421e878` and `sentence-transformers/all-MiniLM-L6-v2` at `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`: https://huggingface.co/api/models/timm/vit_base_patch32_clip_224.openai?blobs=true and https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2?blobs=true, read 2026-10-25.
2. PyPI JSON metadata (versions, wheels, sizes) for every pinned package of `requirements-embeddings.txt`: https://pypi.org/pypi/<name>/<version>/json, read 2026-10-25.
3. The scoring model's five-favorites experiment: `docs/tickets/0020-scoring_model.md`, Result.
4. Measurement reports of this work, on the stand-in stores described above and on the 129 real listings, kept with their store in the throwaway root `%TEMP%\apt-embedding-root` (report `embedding-measurement-report.json`, experiment `experiment.json`, store `profiles\apt-scraper\workspace\gallery\listings.sqlite3`).
