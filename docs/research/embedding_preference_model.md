# Image and text embeddings with a Gaussian-process preference model

Question: do local CLIP photo embeddings and sentence embeddings of the listing text, used in a Gaussian-process preference model or as a similarity term in the scoring model, rank the user's favorites better than the scoring model alone, and what do they cost on the user's machine?

It informs the owner's decision on option B of the [preference learning research](preference_learning_for_listing_ranking.md): whether the embedding path joins the scoring pipeline, replaces the scoring model, or stays out.

## Status

The code, the measurement script and the costs are done. The ranking comparison on real listings has not run, because no store with the real Chicago listings exists anywhere this work may read. The scoring model's five-favorites experiment ran in a throwaway Hermes root that was deleted after it, and the only other store on this machine is the owner's installed profile, which this work does not touch. Running the comparison needs that store recreated (a search, about 130 extractions) or the owner's own store with feature records. [Running the comparison](#running-the-comparison) gives the one command that produces every number the decision needs. Until it runs, the recommendation below covers costs only.

## What was built

All of it is in `profile/skills/ranking/` and runs in an environment of its own, apart from the scoring model, which keeps to the standard library.

- `scripts/embeddings.py` embeds each listing once per content. Every photo of its feature record goes through CLIP ViT-B/32 (OpenAI weights, `timm/vit_base_patch32_clip_224.openai` at revision `a6f597a3`). The unit vectors are averaged over all photos (`photos`) and over each room type the record gives (`photos:kitchen` and so on). The record's traits and concerns, then the title and description in chunks of 150 words, go through all-MiniLM-L6-v2 (`sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243`), and the chunk vectors are averaged (`text`). The vectors are cached in the listing store's new `embeddings` table (migration 6), keyed by listing, content fingerprint and model, so a listing whose content has not changed is never embedded again. It also counts how often CLIP's zero-shot room type agrees with the record's.
- `scripts/embedding_models.py` holds the two models measured against the scoring model. Both use the scoring model's design, labels, label weights, prior and leave-one-out check.
  - The similarity terms add columns to the scoring model's design: each listing's cosine similarity to its most similar favorite other than itself, for photos (the mean over six photo parts), for text, or both. The leave-one-out check recomputes them without the held-out favorite, as it does nearness to the favorites.
  - The Gaussian-process model puts a latent score on each listing with the scoring model's likelihood. Its prior is the scoring model's prior in function space, a linear kernel over the design with the stated preferences' prior means as its mean function, plus a squared-exponential kernel over the embeddings, whose length is the median distance between listings. The embedding kernel's variance is a relative amplitude, 0.5, 1 or 2, times the prior variance of the structured part, and the leave-one-out check picks the amplitude. Without the embedding kernel it is the scoring model: a test fits both on 40 listings with favorites, a dismissal and a pairwise answer, and the scores agree within 10⁻⁶.
- `scripts/measure_embeddings.py` copies a store read-only, embeds the copy, fits every model and prints the report described below.

The Gaussian-process model is a short numpy implementation of the scoring model's exact likelihood, label weights and pairwise terms included, rather than BoTorch or GPyTorch, so neither is a dependency and the comparison differs from the scoring model only in the kernel.

## The leave-one-out check needed one change for a Gaussian process

The scoring model's check refits without the held-out favorite's row. Every other ranked listing without a label is a weak negative, so the held-out favorite becomes the only listing with no label at all. A linear model barely notices one missing row. A kernel that gives each listing room of its own leaves the held-out favorite at the prior mean while the weak negatives pull every other unlabeled listing below it. On a synthetic store with 50 favorites and no signal behind them, that check gave the Gaussian-process model a held-out median of 1.

The measurement therefore labels the held-out favorite as a weak negative, as an unlabeled ranked listing is, and checks every model this way: the scoring model and the similarity terms are the Gaussian-process model without the embedding kernel, at the prior scale and weak-negative weight their own tuning chose. A test pins it: with a white-noise kernel and favorites drawn at random among 60 listings, the held-out ranks stay near chance (mean 37 of 51), where the row-dropping check gives 12.4. The report keeps the rerank's own check for the scoring model as `rerank_check`. On the synthetic stores the two differ by a few ranks, for example a median of 49 under the rerank's check and 52 under this one with 128 listings and 5 favorites.

## Costs

Measured on this Windows machine (AMD Ryzen 9 9950X3D, 16 cores, 61.6 GB of memory) with Hermes's Python 3.14.9. Embedding cost comes from the 5 real Chicago Zillow listings left in an older test store, 67 photos after duplicate sizes were dropped, downloaded from their stored URLs.

| | |
| --- | --- |
| Download, packages | 221 MB of wheels on Windows, 199 MB on Apple Silicon. PyTorch is 118 MB of it (PyPI JSON, 2026-10-25) |
| Download, models | 696 MB: CLIP 605,143,284 bytes, MiniLM 90,868,376 bytes, plus configuration and tokenizer files (Hugging Face API) |
| Installed environment | 1,064 MB |
| Models on disk | 664 MB |
| Install time | 1 min 56 s for the environment from PyPI, 9 s for the models from Hugging Face, on this connection |
| Loading the models | 3.8 to 4.4 s |
| Embedding, 16 threads (PyTorch's default here) | 52 photos per second. Per listing of 13.4 photos: 0.26 s, 4.1 CPU seconds |
| Embedding, 4 threads | 29 photos per second. Per listing: 0.47 s, 1.9 CPU seconds |
| Downloading photos | 0.2 to 0.8 s per listing, 8 at a time |
| Peak memory | 1.64 GB, reached while loading the models and unchanged by embedding or fitting |
| Fit and leave-one-out, 128 listings, 5 favorites | Scoring model 0.3 s. Gaussian-process model 0.2 to 0.3 s per amplitude |
| Fit and leave-one-out, 300 listings, 50 favorites | Scoring model 1.5 s. Gaussian-process model 8 to 10 s per amplitude, once 28 s, about 0.17 s per refit |

Estimates from these figures: 150 listings of 20 photos each, 3,000 photos, embed in about 1 minute at 16 threads and 1 min 45 s at 4 threads, once. A Gaussian-process rerank with its check at 300 listings and 50 favorites takes about 9 s per amplitude, so it misses Spec › Acceptance "Rerank" (2 seconds) unless the check is approximated, as the scoring model's tuning does with one Newton step.

## Running the comparison

Install the environment from the hash-pinned requirements with the Python Hermes runs cron scripts with (`hermes pm env` lists it), then run the measurement on the store:

```powershell
$python = 'C:\Users\<user>\AppData\Local\hermes\tools\python-3.14.9+20261021-win32-x64\python.exe'
$environment = "$env:LOCALAPPDATA\apt-scraper-embeddings\environment"
& $python -m venv --clear $environment
& "$environment\Scripts\python.exe" -m pip install --disable-pip-version-check --require-hashes --only-binary :all: --no-deps -r profile\skills\ranking\requirements-embeddings.txt
& "$environment\Scripts\python.exe" profile\skills\ranking\scripts\measure_embeddings.py <store>\listings.sqlite3 <work directory> --models <models directory> --experiment experiment.json
```

On macOS the environment's Python is `bin/python`. PYTHONPATH and PYTHONHOME must be unset, because Hermes may point them at its own packages. The measurement downloads the models from Hugging Face when the models directory lacks them, checks each weight file's SHA-256, and downloads each listing's photos from the URLs its feature record holds. It writes only to the work directory.

`experiment.json` is `{"favorites": [five ids], "alike": [ids]}`: the five favorited garden units and the unseen listings the tester judges alike to them. The report replaces the store's favorites with the five, keeps its other labels, and fits each model before and after. For each unseen alike listing, the rest of similar price are the ranked listings neither alike nor favorites within 10% of its monthly price. The report gives, per model, the comparisons won against them, the unseen listings above all of them, the mean ranks, and the five favorites' held-out ranks, the same quantities as the scoring model's experiment. It also gives each model's leave-one-out check on the store's own labels, and the costs above.

## Stand-in checks

These ran the whole measurement and show that each model learns from a cue it can see. They say nothing about real listings. The synthetic stores have random feature records and give 20 "garden units" photos drawn from one real listing's 30 photos, and every other listing photos from the other 37.

| 300 listings, 5 garden favorites, 15 unseen | Comparisons won before, after | Unseen above every similar-price listing | Mean rank of the unseen | Favorites' held-out mean |
| --- | --- | --- | --- | --- |
| Scoring model | 679, 732 of 1,154 | 0 of 15 | 114 | 129 |
| With similarity to the most similar favorite (photos) | 679, 866 | 1 | 84 | 126 |
| Gaussian process, photos, chosen amplitude 2 | 708, 1,151 | 14 | 14 | 19 |
| Gaussian process, text, chosen amplitude 2 | 689, 693 | 0 | 128 | 119 |

The photo cue is planted, so the Gaussian process finding it is expected. The similarity term found little of it, probably because the maximum similarity to five favorites is one column whose weight five labels must learn against nearly 300 weak negatives. Text carries no planted cue here, and the text kernel did not help.

## Recommendation

From the costs alone, the embedding path is affordable as a step that runs once per listing content: about 1.7 GB on disk, 0.9 GB to download once, 1.6 GB of memory while it runs, and a minute or two of CPU for a full search. It does not fit inside a 2-second rerank at 300 listings and 50 favorites as built, so the Gaussian-process model would need an approximate leave-one-out check before it could replace the scoring model in reranks.

Whether it should join depends on the comparison that has not run. A proposed reading of its result, with the scoring model's own experiment as the bar (7 of 15 unseen garden units above every similar-price listing, 507 of 615 comparisons won):

- If the Gaussian-process model with photos raises the unseen garden units above that bar and the five favorites' held-out ranks improve, the embedding path joins, with the Gaussian-process model scoring and the scoring model's contributions kept for explanations.
- If only the similarity term helps, it joins as two columns of the scoring model, which keeps exact explanations and the 2-second rerank and needs only the embedding step.
- If neither helps, it stays out, and the misses point back at the feature record.

## Sources

1. Hugging Face model API, file lists, sizes and SHA-256 of `timm/vit_base_patch32_clip_224.openai` at `a6f597a30f7b82c51704746581f9a4e41421e878` and `sentence-transformers/all-MiniLM-L6-v2` at `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`: https://huggingface.co/api/models/timm/vit_base_patch32_clip_224.openai?blobs=true and https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2?blobs=true, read 2026-10-25.
2. PyPI JSON metadata (versions, wheels, sizes) for every pinned package of `requirements-embeddings.txt`: https://pypi.org/pypi/<name>/<version>/json, read 2026-10-25.
3. The scoring model's five-favorites experiment and its numbers: `docs/tickets/0020-scoring_model.md`, Result.
4. Measurement reports of this work, on the stand-in stores described above.
