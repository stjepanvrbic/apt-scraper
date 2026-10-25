---
title: Review pass after each rerank
type: Feature
state: In Progress
priority: 3
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: []
---

## Outcome

After each rerank, the model reviews the top 50 and the state of the whole ranking. It rewrites their reasons, flags conflicts with stated preferences and may move listings, and its moves are kept only when the leave-one-out check after the review is no worse than before.

## Acceptance

- [x] One text-only review per rerank. It reads the top 50 with their feature records, contributions and reasons, the stated preferences, the note readings, and the ranking state: label counts, held-out ranks, feature weights that run against a stated preference, listings waiting for or failing extraction, and the tag list.
- [x] A rewritten reason cites only the listing's feature record. Code rejects one that names a feature the record lacks and keeps the reason it had.
- [x] Each move has a reason. After the moves the leave-one-out check runs again. When it is worse, every move is reverted, and the result records the reverted moves and both values.
- [x] Each listing whose rank conflicts with a stated preference is flagged with that preference.
- [x] What looks wrong in the ranking state, such as two traits that mean the same thing or one weight carried by a single label, is reported in the rerank result and in `status`.
- [x] Tests with controlled review outputs: one that moves favorites down is reverted, one that moves them up stands, and an invalid reason is rejected.
- [x] A live review on real listings, with its token use in Result.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) (review pass), [Spec › Acceptance](../spec.md#acceptance) (Review pass), [Spec › Decisions](../spec.md#decisions) (review pass moves stand only when the check confirms them)

## Design

- The review sees no labels: favorites are not marked, and the favorite part of each reason is added by code after the review. Favorites then test its moves independently.
- Moves are kept as per-listing score adjustments, so they apply to the full fit and to each held-out fit. "Worse" means a higher mean held-out rank of the favorites.
- The review gives a verbalized confidence for each move, which the research found better calibrated than token probabilities.
- The gallery shows the code's order at once and the reviewed order when the review finishes. The review runs as an agent turn started from the rerank, through the gateway client the Notes tab uses or an on-demand Hermes job. The implementer picks one after checking both in Hermes's source.
- Listing text is data, and the review never follows instructions in it.

## Deviations

- The review runs in the Hermes cron job `apt-scraper-review`, every minute, with a script gate (`profile/scripts/review-run.py` → `ranking.py run`) that wakes the agent only when the latest rerank's review waits. I checked both options in Hermes 0.22.5's source. `connect_gateway` in `hermes_cli/gateway_client.py` starts a gateway when none runs. `trigger_job` in `cron/jobs.py` only moves `next_run_at`, and `cron/scheduler_tick.py` skips a job while its previous run is still going. A job gets a fresh session without the conversation's knowledge of the favorites, limits the tools to terminal and file, runs the same way whether the rerank came from the gallery or a conversation, and leaves the gallery only reading `ListingStore.last_rerank()['review']`. The cost is up to a minute before the review starts.
- The agent of the job writes the review itself, on the profile's main model (Sonnet as shipped), instead of handing it to a `delegate_task` subagent, which would run on `delegation.model` and add a hop without isolating anything further.
- The leave-one-out check reads scores the rerank stores with its waiting review (`reviews.snapshot`): the order, the exploration listings, the full-fit scores and each favorite's held-out scores. It does not refit. A refit made every features, contact or search run between the rerank and the save a reason to drop the review, and could not reproduce the rerank's fit exactly. The snapshot is removed when the review ends.
- A reason uses only `CONNECTING_WORDS` outside its bracketed citations, rather than a list of forbidden feature words, which missed words such as "bright" or "laundry". A file with an invalid reason is rejected whole, naming every such reason, so nothing changes and the agent corrects it in the same turn. Every listing keeps its reason until a valid file is saved.
- The review's task carries no count of liked listings per feature and no nearness to the favorites, because both identified favorites. The finding "a large weight carried by a single labeled listing" is computed by code (`state_findings` in `scoring.py`) and reported in the rerank summary's `findings`, outside the task. The review's own findings, such as two traits that mean the same thing, are in the review's result. `status` shows both.
- Without a favorite, nothing can confirm a move, so every move is reverted.
- The schema migration is version 8, after `pairwise_skips` and `distances`.
- A review saved for a rerank that a later one replaced fails with an error that tells the agent to stop, and handing a review that no longer waits does nothing, so a review never lands on the wrong rerank.

## Result

The implementation is the single commit on `feat/review_pass` with subject "Review pass after each rerank", in a pull request to `main`, not merged. The ticket stays In Progress until it merges.

```
profile/skills/gallery/scripts/review.py     the task, format and citation checks, reviewed order, adjustments, held-out ranks on the stored scores
profile/skills/gallery/scripts/rerank.py     rerank returns its review; pending_review, save_review
profile/skills/gallery/scripts/scoring.py    the rerank's check snapshot, state_findings, compose_reason and feature_reason
profile/skills/gallery/scripts/store.py      save_rerank returns the id and stores the snapshot; rerank(id), review_snapshot, hand_review, fail_review, save_review
profile/skills/gallery/scripts/schema.py     migration 8: reviews
profile/skills/ranking/                      review.md task template; ranking.py run and review-save; SKILL.md
profile/scripts/review-run.py, profile/cron/jobs.json, install.ps1, install.sh, profile/SOUL.md, README.md, docs/ranking.md, docs/gallery.md
tests/test_review.py
```

Checks at the delivered commit, on this Windows machine: `python -m unittest discover -s tests` 469 passed, 0 skipped (Python 3.12). `test_review`, `test_rerank`, `test_scoring` and `test_compare` also pass under Hermes's Python 3.14.9. `ruff check profile tests` is clean. Hermes 0.22.5's skill linter reports nothing for `ranking` and `listing-features`, its skills guard says safe, its context scanner passes `SOUL.md`, the skill and the task template, and its cron prompt scanner passes the four job prompts.

Tests with controlled reviews in `tests/test_review.py`: moves that put a favorite lower are all reverted, and the result records them with both check values (mean held-out rank higher after); moves that put a favorite higher stand at their ranks with the exploration listings in place; a file with a reason that cites a value the record lacks or uses another word outside its citations is rejected whole and every listing keeps its reason. Also covered: the label-free task, every format check, the check on the rerank's own scores while labels and listings change, a review of a replaced rerank, reverting without a favorite, the single-label finding, the job's wake gate, its three attempts and its cleanup, the command line and the shipped job.

Review: two rounds by an independent reviewer. The first found label leaks in the task (counts of liked listings per feature, nearness to favorites), reviews dropped as stale by ordinary background jobs, gaps in the forbidden-word check, unhashable ids, and a rerank summary that could come from another rerank. The second found a replaced rerank's review that could be saved onto the new one, a hand-off race, numbers and symbols passing the reason check, and stale template text. All are fixed with tests, as Deviations describes. Kept on purpose: the review agent has the terminal tool, as the search and features agents do, to save and correct its review in one turn. The reviewer agreed and suggested restricting terminal for all three jobs in a separate change.

Live, in a separate Hermes root under `%TEMP%` with this branch's profile installed by `hermes profile install`, Hermes 0.22.5, before the second review round and the last rebase. The root was deleted afterwards and nothing opened a browser.

- A 6-minute search found 48 Zillow listings in Logan Square, Chicago (at most 2,600 USD, at least 2 bedrooms), and one features run extracted all 48 on Sonnet and read the three stated preferences. Five bright listings with in-unit laundry were favorited. The rerank ranked 48 with held-out ranks 1, 2, 4, 11 and 35 (median 4, mean 10.6) and left its review waiting.
- `hermes cron run apt-scraper-review` with the final template, on Sonnet, the shipped main model: the review rewrote all 48 reasons, flagged the dislike "Carpet" on a listing at rank 15 whose summary mentions carpeted bedrooms while no carpet feature is among its contributions, gave 5 findings and proposed no moves. Its first save was rejected for words outside citations ("small", "laundry", "exploration"). A heredoc edit was blocked by Hermes's cron approval rule, and the corrected file saved. A reason as saved: "Best on natural light 5 of 5, with in-unit laundry and window size 3 of 5 as a plus. Most like your favorite 3218 W Fullerton Ave APT 3."
- Token use of that review (`state.db`): 7 API calls, 385 input tokens, 242,612 cache-read, 42,506 cache-write and 5,745 output tokens, 630 of them reasoning, in 54 seconds. The task file was 55 kB. An earlier run with the first template, which let the review skip reasons, rewrote 2: 6 calls, 383 input, 179,070 cache-read, 44,740 cache-write and 2,209 output tokens, in 29 seconds.
- Controlled reviews through `ranking.py review-save` on the same real store: a favorite moved from rank 2 to 40 was reverted (held-out mean 10.6 before, 18.6 after), and a favorite moved from rank 6 to 1 stood (10.6 before, 6.6 after).
- The CLI runs started a gateway for the separate root, which was stopped before deleting it. During the run, the owner's root started its own web server and gateway at 17:35 and 17:37 local time, hosting the profiles `apt-scraper-t0028` and `apt-scraper-test`, which this work never created. Its `gateway-starts.log` went from 14 to 15 lines. Every Hermes command of this work ran with `HERMES_HOME` set to the separate root.

Tech debt fixed: `held_out_ranks` in `scoring.py` took the fit's inputs, though the ranks need only the held-out scores, now `held_out_scores`. `reason` mixed composing a reason with choosing its features, now `feature_reason` and `compose_reason`, which the review reuses.

Not verified:

- The gallery's trigger and display of the review (0022).
- macOS. The change has no platform-specific code.
- A live review that proposes moves. Neither live review moved a listing, so the move path ran live only through the controlled reviews above.
- The live runs predate the second review round and the rebase onto the distance features. The tests cover both.
- `test_spec_rerank_timing_with_300_listings_and_50_favorites` fails intermittently on a loaded machine, on `main` as well (1 of 4 runs during this work). 0030 tracks it.
