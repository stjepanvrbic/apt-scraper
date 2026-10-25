---
title: Scoring model with explanations and the leave-one-out check
type: Feature
state: Done
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

- [x] One rerank function, used by the gallery and by the agent's `ranking.py rerank`, writes every rank, reason and explanation in one store transaction. Ranks change at no other time, and no job reranks.
- [x] Favorites and contact requests count for their listings, dismissals against them, a note's sentiment labels its listing, and pairwise answers count when they exist.
- [x] Each user note and each stated like or dislike is read once by the model, within an hour, into the record features it names and their direction. A changed preference is read again, an unchanged one never.
- [x] Stated likes and dislikes and note readings set the starting weights. Spec › Acceptance "Overruled preference" passes in a test.
- [x] Each reason names the features that moved the score most and the most similar favorite, citing only the listing's record. The stored explanation lists every feature's contribution, and the contributions add up to the score.
- [x] Each rerank stores and `status` prints every favorite's held-out rank.
- [x] With more than 20 ranked listings, two unlabeled exploration listings drawn from the posterior are in the top 20 and marked in the store. With a fixed seed, a rerank is reproducible, and without exploration it is reproducible with any seed.
- [x] Location counts through the areas of the criteria and a location preference learned from the favorites' coordinates, weighted by the location priorities when they exist.
- [x] Spec › Acceptance "Rerank" timing passes on fixtures of 300 listings and 50 favorites on this Windows machine.
- [x] The five-favorites experiment of Spec › Acceptance, on real Chicago listings including the garden units, is run and its numbers are in Result.
- [x] `docs/ranking.md` describes scoring. Merged to `main` through a reviewed pull request.

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

- The rerank function is `rerank(store, seed=None)` in `profile/skills/gallery/scripts/rerank.py`, over the pure model in `scoring.py`. The gallery's HTTP route and its one-rerank-at-a-time guard stay with 0022, which calls this function. `ListingStore.rank` remains only to seed the sample store.
- Pairwise answers need a place to be stored before they can count, so this ticket adds the `pairwise_answers` table, `ListingStore.answer_pair` and `pairwise_answers`, and `pairwise_questions(store, count)`. The Compare tab, the skip rule and the routes stay with 0023.
- The weak-negative weight and the prior scale are chosen by coordinate search, the weight among 0.05 and 0.1 at scale 1, then the scale among 1, 0.5 and 2, each by the mean held-out rank of a one-Newton-step leave-one-out. The six full leave-one-out grids took 7.4 s on 300 listings and 50 favorites. The reported held-out ranks come from converged refits of the chosen pair. Tuning starts at 3 favorites.
- The likelihood keeps tags and "not shown" columns sparse and uncentered with a nearly flat intercept prior (precision 10⁻⁶), so centering moves only the intercept. Contributions are still `w_j (x_j − mean_j)` and add up to the score.
- Nearness to the favorites is the mean kernel of the other favorites, not the sum. Live, the sum gave every favorite one kernel fewer than any other listing, and the model learned a weight of −0.66 against nearness. A unit test pins that a favorite and a listing at the same place are equally near.
- Days since first seen is not a feature: a favorite's age at rerank time records when it was favorited.
- A contact request weighs 2, a favorite and a note's positive or negative sentiment 1, a pairwise answer 1. An action outranks a note's sentiment, and mixed or neutral notes label nothing. A stated like or dislike moves the prior mean of each feature its reading names by ±1, a note's like or dislike by ±0.5, capped at ±2. Area priorities start their area at 1, 0.5 or 0.25, and nearness to the favorites at the highest area priority.
- A preference reading is keyed by kind and phrase. Extraction judges the stated preferences that are unread or read as naming no feature; later judgments cover only those read as naming no feature. The reading vocabulary is `scoring.reading_vocabulary`.
- The features job hands at most 50 extractions per wake. Live, its first wake with 128 waiting listings hit Hermes 0.22.5's per-turn subagent cap (`tool_loop_guardrails.loop_caps.max_subagents`, 50, `hermes_cli/config_defaults.py`): the 56th `delegate_task` was blocked and the wake ended before the readings. This is a defect of the job from 0016, fixed here because the readings ride its wakes.
- `given_up_extractions` and the extraction read key moved from `features.py` to the store (`ListingStore.given_up_extractions`, `extraction_read`, `READ_ATTEMPTS`), so the ranking does not import a skill script.
- PR #13's `apt-scraper-ranking` job and skill are not on `main` and not in the owner's installed profile (checked read-only: no `cron/jobs.json`, no `ranking` skill), so nothing runs beside the rerank. The new skill reuses the name `ranking`.
- No dependency added. `ranking.py` and `scoring.py` use the standard library only.

## Result

Delivered as one commit on `feat/scoring_model`, pull request to `main`, not merged. The ticket stays In Progress until review and merge.

```
profile/skills/gallery/scripts/scoring.py   features, prior, Newton fit and Laplace posterior, leave-one-out, explanations, reasons, exploration, pairwise questions
profile/skills/gallery/scripts/rerank.py    rerank(store, seed) and pairwise_questions(store, count)
profile/skills/gallery/scripts/store.py     ranking_inputs, save_rerank, last_rerank, readings, pairwise answers, given_up_extractions
profile/skills/gallery/scripts/schema.py    migration 5: ranking.explanation and exploration, reranks, note_readings, preference_readings, pairwise_answers
profile/skills/gallery/scripts/queries.py   browse returns the exploration mark
profile/skills/ranking/                     skill and ranking.py: rerank, status, explain, questions
profile/skills/listing-features/            readings and read commands, the readings section, at most 50 extractions per wake
profile/cron/jobs.json, profile/SOUL.md, README.md, docs/ranking.md, docs/gallery.md
tests/test_scoring.py, tests/test_rerank.py, tests/test_features_run.py, tests/test_feature_store.py
```

Checks, on this Windows machine at the delivered commit's content: `python -m unittest discover -s tests` 389 passed, 0 skipped (Python 3.12.10). `test_scoring.py`, `test_rerank.py` and `test_features_run.py` also pass under Hermes's Python 3.14.9. `ruff check profile tests` clean. Hermes 0.22.5's skill linter reports nothing for `ranking` and `listing-features`, its skills guard says safe, its context scanner passes `SOUL.md` and both skills, and its cron prompt scanner passes the three job prompts.

Unit tests on constructed listings: Spec › Acceptance "Overruled preference" (dislike "Carpet", five carpeted favorites: the carpet weight turns positive and an unseen carpeted listing's carpet contribution is ≥ 0), "Exploration" (ranks 7 and 14, unlabeled, outside the mean's top 18), a single pairwise answer ranking the chosen listing above the other, the sparse gradient and Hessian against a dense computation, the warm-started leave-one-out against a refit from scratch (weights within 10⁻⁵), reproducibility with a fixed seed and with any seed without exploration, contributions adding up to the score, and the most similar favorite as the nearest under the learned weights. "Rerank" timing: 300 listings with about 110 tags in use and 50 favorites rerank in 1.36 to 1.38 s, store read and write included (7.4 s before the sparse likelihood and the tuning search).

Live, in a separate Hermes root under `%TEMP%` with the branch's profile installed by `hermes profile install`, Hermes 0.22.5, extraction on Sonnet. The owner's root was not touched: its gateway was not running before or after and its `gateway-starts.log` stayed at 14 lines. Nothing opened a browser. The root was deleted afterwards.

- Search: the experiment criteria of PR #13 (Lake View and Logan Square, at most 2,600 USD a month, at least 2 bedrooms) found and showed 128 Zillow listings in one 25-minute run.
- Extraction: a `hermes cron run` and two scheduled wakes made all 128 records. The first was cut at 55 by the subagent cap, which led to the per-wake limit above. The second, on the fixed code, ran 50 extractions and then read the three stated preferences: "Lots of daylight" as `rating:natural_light` and `rating:window_size` liked, "In-unit laundry" as `flag:in_unit_laundry`, "Carpet" as `floor:carpet` disliked. No live listing has a carpet floor.
- A user note ("Loved the exposed brick and the light, but the kitchen is tiny and the street is loud.") was read 8 minutes after it was written, in a wake of 4 API calls and 552 output tokens: mixed, likes `tag:exposed brick` and `rating:natural_light`, dislikes `rating:kitchen_size`, `tag:street noise`, `tag:busy street`.
- A rerank of the 128 listings took 0.18 s without favorites and 0.31 to 0.34 s with five, and left Hermes's session and API call counts unchanged (131 sessions, 564 calls).

The five-favorites experiment. As the tester I judged the garden units alike: 20 listings whose unit label says garden or basement (14) or whose description calls it a garden unit or garden apartment (6); "basement" mentions about laundry or storage were left out. I favorited five (455063308 `#G`, 441025098 text only, 2106063102 `#G`, 2064093099 text only with unit label `1B`, 2079514484 `#G`), leaving unseen the three PR #13 missed (113953556 `#G`, 2069930270 `#Gn`, 464925436 `#Basement`). For each unseen garden unit, the rest of similar price is the other ranked listings within 10% of its monthly price.

| | Before the favorites | After |
| --- | --- | --- |
| Comparisons won against similar-price listings | 47 of 615 (8%) | 507 of 615 (82%) |
| Unseen garden units above every similar-price listing | 0 of 15 | 7 of 15 |
| Mean rank of the unseen garden units, of 128 | 108.2 | 27.3 |
| Mean rank of the other listings | 56.3 | 72.5 |

- The favorites rank 1 to 5. 113953556 ranks 45, 464925436 18 and 2069930270 106. Its explanation: garden unit +0.74, against it renovation recency 5 of 5 and kitchen finish 5 of 5, modern style and small windows, where the favorites are older units. The strict criterion, every unseen garden unit above all of similar price, holds for 7 of 15 and is not met.
- Learned weights: `unit_level:garden` +1.00, `rating:ceiling_height` −1.34, `flag:in_unit_laundry` +0.55, `rating:natural_light` +0.18 (prior +1), `rating:window_size` +0.28 (prior +1), `near_favorites` −0.32. The search chose prior scale 2 and weak-negative weight 0.1.
- Leave-one-out after the five favorites: 2064093099 6, 2079514484 3, 2106063102 60, 441025098 59, 455063308 60 among 123 listings that are not favorites; median 59, mean 37.6. After the note and one pairwise answer: 5, 4, 39, 55, 71; median 39, mean 34.8.
- Exploration: ranks 7 and 14 (457633824, 2071235917), neither labeled.
- Pairwise: the first of `ranking.py questions` paired 2097377676 (rank 30) and 2061240996 (rank 31) at probability 0.50001. Answering 2061240996 moved it to 11 and the other to 81 at the next rerank.

Tech debt fixed: the features job's wakes exceeded Hermes's subagent cap (above). `features.py`'s given-up extractions lived in a skill script the ranking would have had to import. The migration test asserted schema version 4 literally and now asserts the number of migrations.

Not verified:

- The gallery's Score and rerank button and its HTTP route (0022), and the Compare tab (0023).
- macOS. The change has no platform-specific code.
- "Overruled preference" on live data: no live listing has carpet. The unit test covers it.
- Travel time and transit distance (0019): `raw_values` and `prior_means` in `scoring.py` are where their columns and starting weights go.

Open questions for the owner:

1. The strict five-favorites criterion holds for 7 of 15. (a) Accept the scoring model as it is and let the review pass (0021) and the embedding spike (0025) address the rest, as planned. (b) Tune the model on this experiment now. Recommendation (a): tuning on the one experiment that judges it would overfit it, and the misses are listings whose records differ from the favorites in renovation and finish, which the user may in fact care about.
2. A pairwise answer weighs as much as a favorite, and one answer moved a listing from 30 to 81. (a) Keep weight 1. (b) Lower it to 0.5. Recommendation (a) until the Compare tab gives answers to measure with the leave-one-out check.
