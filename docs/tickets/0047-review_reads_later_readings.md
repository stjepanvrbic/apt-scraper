---
title: The review pass reads the preferences of the rerank it reviews
type: Bug
state: In Progress
priority: 4
assignee: agent
milestone:
parent:
blocked_by: []
related: [0021]
---

## Outcome

The review task describes the ranking state of the rerank it reviews, so a preference or note read after that rerank cannot make the review report its weights as running against starting weights the rerank never used.

## Acceptance

- [x] The task's preferences, note readings, starting weights and label counts come from the inputs the rerank used, stored with its waiting review as the check's scores are.
- [x] A test saves a note reading between a rerank and its review and checks that the review task does not contain it.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) ("After each rerank, the review pass reads the top 50 and the state of the whole ranking.").

## Design

`pending_review` in `rerank.py` builds the task with `review_task(store.ranking_inputs(), …)` when the job hands it over, and `review_task` in `review.py` takes the settings, preference readings, note readings and starting weights from those current inputs while the weights are the rerank's. In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#4-gallery-reranks-compare-and-review)), rerank 5 finished at 23:00:16, a note's reading was saved at 23:00:55 and the review task was written at 23:01:11. The review's finding was "The weights of tag:galley kitchen and rating:kitchen_size run against the stated dislikes.", dislikes that came from that note.

## Deviations

- The review's flags are also checked against the rerank's stated preferences, so a preference stated after the rerank cannot be flagged and one removed since can.
- Everything the task and the review's checks read comes from the snapshot, not only the preferences, readings, weights and labels: each top listing's title, record digest, contributions, the values a reason may cite, the code-only mark and the most similar favorite, the waiting and given-up counts, and the tag list with its counts. Only which listings are still ranked with unchanged content comes from the store.
- A review waiting when the store migrates fails with "apt-scraper was updated after this rerank. Rerank to get a review.", because its snapshot lacks the inputs.

## Result

Delivered on branch `fix/e2e_ranking_bugs` at `de6f538`, in a pull request to `main`, not merged. `rerank` stores `review_inputs(inputs)` in the review's snapshot: the stated preferences with their readings, the note readings, the starting weights with the preferences that name them, and the label counts other than favorites. `review_task` and `save_review` read them from the snapshot. The listings, tags and extraction state still came from the store when the task was handed over, until the review fix below.

Test: `test_the_task_describes_the_preferences_readings_and_labels_of_the_rerank_it_reviews` in `tests/test_review.py` saves a note reading, a new dislike with its reading, a pairwise answer, a dismissal and a contact request after the rerank. The task holds none of them, its starting weights are the snapshot's, and a flag on the new dislike is refused.

`python -m unittest discover -s tests`: 554 tests; the only failure was the CPU-budget test `test_rerank_with_signals_meets_the_two_second_cpu_budget` at 2.09 s while the suite loaded the machine. It passes 3 of 3 runs alone, and a 300-listing rerank takes 1.69 to 1.70 s of CPU on both `main` and this branch. Playwright headless, all 40 specs pass. `ruff check profile tests`, `npm run lint`, `tsc` and `npm run build` are clean. Hermes's skill linter and skills guard give the same findings for `listing-features`, `ranking` and `gallery` as on `main`, and its context scanner passes `SOUL.md`, both changed skills and `review.md`. The new tests fail against `main`'s code.

Review fix at `df72c44`, after merging `main`: the task's records, waiting and given-up lists and tag counts, the citable values and a rewritten reason's code-only mark still came from the store, so a listing ranked code-only that got its record after the rerank showed that record, could be cited by it, and lost its code-only mark when its reason was rewritten. `review_inputs` now also stores `reviewed_listing` for each of the top 50 (title, record digest, contributions, reason lists, citable values, code-only mark, most similar favorite), the waiting and given-up lists and the tag counts, from the tag list `ranking_inputs` now reads in the same transaction. `review_task`, `validated_review` and `review_outcome` read only the snapshot, with the store's ranked listings deciding only which are unchanged. Test: `test_a_record_saved_after_the_rerank_stays_out_of_the_task_the_citations_and_the_stored_reason` in `tests/test_review.py` ranks a listing given up on, saves its record with a new trait after the rerank, and checks that the task shows no record and the snapshot's given-up list and tags, that a reason or a finding citing the new trait is refused, and that the rewritten reason keeps `code_only: true`.

`python -m unittest discover -s tests`: 555 tests, all pass, none skipped. Playwright headless, all 40 specs pass. `ruff check profile tests`, `npm run lint`, `tsc` and `npm run build` are clean, and the build leaves the committed `web/` assets unchanged. The new and changed tests fail against `4ccdf64`.
