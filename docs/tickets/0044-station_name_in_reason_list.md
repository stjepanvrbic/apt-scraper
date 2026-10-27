---
title: A reason's station distance reads as one item of its list
type: Bug
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: [0019, 0020]
---

## Outcome

Each value a listing's reason or explanation names reads as one thing, so the station distance never reads as an area or another feature counting against the listing.

## Acceptance

- [x] A reason that names the station distance keeps the station's name with its distance, such as "1.6 km to Logan Square station", and never puts a comma inside a value of the "For it" or "Against it" lists.
- [x] The review pass's citations of that value still match the explanation's value exactly.
- [x] A count is grammatical: "1 bathroom", "2 bathrooms".
- [x] Tests cover both values in `scoring.py`'s reason and explanation.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) ("A listing's reason names the features that moved its score most").

## Design

`scoring.py:865` writes the value as `{distance} km to the nearest station, {station}`, and `scoring.py:919` joins the values against a listing with ", ". In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#4-gallery-reranks-compare-and-review)) the top listing's reason read "Against it: 1.6 km to the nearest station, Logan Square, subway tile.", while its area Logan Square contributed +0.44. The bathrooms value at `scoring.py:874` prints "1 bathrooms".

## Deviations

- The fix is in the reason's construction, not its text: a reason is stored in parts, `{"code_only", "features": {"for": [...], "against": [...]} or {"text": ...}, "similar_favorite"}`, and the gallery renders each list's values as list items. A listing without a rank carries `unranked_reason` instead of a reason. `store.rank` takes the features part.
- Prices and sizes keep their thousands separator ("2,450 USD a month"). Each value is its own list item, so the comma cannot split it, and the review cites these values exactly. No value the code composes from two facts holds a comma any more.
- The migration rebuilds each ranked reason's lists from its stored explanation and keeps a reason a review rewrote as its text. The code-only mark is taken from the text the rerank wrote, not from whether the listing has a record now. Explanation values written before the change ("1 bathrooms", "..., Logan Square") stay until the next rerank.

## Result

Delivered on branch `fix/e2e_ranking_bugs` at `de6f538`, in a pull request to `main`, not merged. `display` in `scoring.py` writes "1.6 km to Logan Square station" (a station named "... Station" is not named twice) and counts bedrooms, bathrooms and photos grammatically. `feature_reason` and `compose_reason` return the reason's parts, which the store keeps as JSON (migration `structured_reasons`), the review writes its text into, and `ListingReason` in `gallery/src/RankingExplanation.tsx` renders as "For it" and "Against it" lists on the card and in the details.

Tests: `ReasonValueTests` in `tests/test_scoring.py` (station value in the explanation and in the against and for lists, a station named as a station, counts in the explanation and the reason, the review citing the value exactly), the migration in `tests/test_rerank.py`, and `gallery/tests/rerank.spec.ts`, which checks the list items on the card and in the details headless with a real station.

`python -m unittest discover -s tests`: 554 tests; the only failure was the CPU-budget test `test_rerank_with_signals_meets_the_two_second_cpu_budget` at 2.09 s while the suite loaded the machine. It passes 3 of 3 runs alone, and a 300-listing rerank takes 1.69 to 1.70 s of CPU on both `main` and this branch. Playwright headless, all 40 specs pass. `ruff check profile tests`, `npm run lint`, `tsc` and `npm run build` are clean. Hermes's skill linter and skills guard give the same findings for `listing-features`, `ranking` and `gallery` as on `main`, and its context scanner passes `SOUL.md`, both changed skills and `review.md`. The new tests fail against `main`'s code.

Review fix at `df72c44`, after merging `main`: the `structured_reasons` migration took `code_only` from the current feature records, so a listing ranked code-only that got its record after the rerank lost the mark while its rank stayed code-only. It now takes the mark from the reason's text (`earlier_marks` in `schema.py`). Test: the migration test in `tests/test_rerank.py` ranks a listing given up on, saves its record after the rerank, and checks that the migrated reason keeps `code_only: true`.

`python -m unittest discover -s tests`, after merging `main` at `46c75fd`: 594 tests, all pass, none skipped. Playwright headless, all 40 specs pass. `ruff check profile tests`, `npm run lint`, `tsc` and `npm run build` are clean, and the build leaves the committed `web/` assets unchanged. The new and changed tests fail against `4ccdf64`.
