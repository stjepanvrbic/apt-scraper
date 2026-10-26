---
title: "Verify: ranking that learns the user's taste from listing features"
type: Chore
state: Todo
priority: 2
assignee: Stjepan Vrbic
milestone:
parent: 0015
blocked_by: []
related: [0014, 0033]
---

## Outcome

The redesigned ranking is checked end to end on real listings against the umbrella's exit criteria.

## Acceptance

- [ ] Every Spec › Acceptance item for Interview, Listing features, Ranking, Rerank, Overruled preference, Location, Exploration, Pairwise questions and Review pass passes on real listings in a profile installed by the installer.
- [ ] In the five-favorites experiment on real Chicago listings, the unseen garden units rank above the rest of similar price after five garden units are favorited at least as well as the scoring model's own experiment did (6 of 15 strictly above every similar-price listing, mean unseen rank 27 of 128), including units marked only by labels such as `#G`, with the review pass run and its effect on the result recorded.
- [x] The embedding spike's measurement is recorded, and the owner's decision on the embedding path is recorded with it.
- [ ] The installer on Windows ends in the dashboard with the ranking working on the standard library alone; macOS is run by the owner and its output recorded here.
- [x] Results recorded here. The checks run on `main`.

## Spec

[Spec › Acceptance](../spec.md#acceptance), [Spec › Listing features](../spec.md#listing-features), [Spec › Ranking](../spec.md#ranking), [Spec › Gallery](../spec.md#gallery), [Spec › Interview](../spec.md#interview)

## Design

A separate test profile installed from `main` by the installer, never the owner's `apt-scraper` profile. Real Zillow listings with the criteria of the scoring model's experiment, one search. The store of the embedding spike's run at `%TEMP%\apt-embedding-root` may be reused for the garden-unit experiment to spare listing-site traffic. Hermes sessions with `-Q`, the gallery with `open --no-browser`, browser checks headless with screenshots.

## Deviations

- No installer run. `install.ps1` now installs a machine-wide gateway service and a login entry, so the owner runs it. The profile went into a throwaway Hermes root, `HERMES_HOME=%TEMP%\apt-verify-0027-root`, with `hermes profile install ./profile --name <name> --yes`, followed by the installer's post-profile steps: `search-run.py install` with Hermes's Python, `cron resume` of the four jobs, the `delegation.model` check and the provider plugin. The Google Workspace skill, the computer-use driver, the gateway service and the dashboard were left out. The root's own gateway, which Hermes started on demand, ran the jobs and was stopped at the end.
- Two profiles in that root. `apt-verify` holds a copy of the embedding spike's store, with the `embeddings` table dropped and `user_version` set to 7, migrated to 9 when current code opened it. Its search job was paused right after the resume, so it never searched. `apt-verify-fresh` had a live interview and one Zillow search, and its search job was paused right after that search.
- The headless gallery loaded a bundled placeholder image in place of Zillow photos, so the browser checks sent nothing to the listing site. Its connection record went to a home directory inside the root (`USERPROFILE` set for the gallery process only).
- "Searching the unchanged listing again" was checked by saving each listing's stored content again through `upsert_listing`, the search's save path, instead of a second search.
- No real listing has a carpet floor, and the location item needs two listings that differ only in distance. Both ran on copies of the real store with test data added, as Result says.

## Result

Checked on `main` at `cf3ac2f` on this Windows machine, Hermes 0.22.5. The throwaway root is kept at `%TEMP%\apt-verify-0027-root` (`C:\Users\stjep\AppData\Local\Temp\apt-verify-0027-root`): `profiles\apt-verify` (the five-favorites store), `profiles\apt-verify-fresh` (interview and search), `stores\` (the migrated baseline and the copies for the carpet and location checks), `results\` (JSON reports and screenshots) and `scripts\` (the checks). Its gateway is stopped. The owner's root kept its profiles, and its `gateway-starts.log` stayed at 15 lines.

### Spec › Acceptance items

| Item | Result | Evidence |
| --- | --- | --- |
| Interview | Pass | `apt-verify-fresh`, two `-Q` turns. The store holds the places Work (233 S Wacker Dr, found by Nominatim as Willis Tower) and Climbing gym (First Ascent Avondale, 3516 N Spaulding Ave), and the location priorities places high, rapid transit medium, Logan Square high. `missing_settings` is empty. |
| Listing features | Pass | The one search found 87 listings in 10.5 minutes, and three features wakes extracted all 87 on Sonnet. Their 1,166 listed photos became 1,145 unique photos, each with a room type, numbered without gaps. The four main sessions (the interview and three features wakes) hold no image, image attachment or `vision_analyze` call; each of the 87 subagent sessions holds its sheets. Saving the stored content of all 87 again returned `changed: false` for each, the next features run printed "0 listings wait for extraction" and `{"wakeAgent": false}`, and the session count did not change. |
| Ranking | Fail | The strict item, every unseen garden unit above the rest of similar price, holds for 6 of 16. See the experiment below. |
| Rerank | Pass | 300 listings with 50 favorites: `test_spec_rerank_timing_with_300_listings_and_50_favorites` passed under Python 3.12 and Hermes's 3.14.9, and the headless browser test "Score and rerank applies real ranks and every favorite check within two seconds" passed. Live on 129 listings with 5 favorites: 302 ms from the click to the new order and every held-out rank on screen, 0.277 s on the server. The root had no `state.db` before or after the click, so no model call ran; the first sessions are the review job's. |
| Overruled preference | Pass, with test data | No real listing has carpet (59 hardwood, 41 mixed, 28 tile or vinyl, 1 not shown). On a store copy, six real listings got floor carpet and five of them were favorited. The `floor:carpet` weight went from −1.0 to +0.996, and the unseen sixth listing's carpet contribution from −0.953 to +0.949, its rank from 74 to 6. `OverruledPreferenceTests` passes. |
| Location | Pass, with test listings | On a store copy with the place Work at priority high, two copies of one real listing were placed on the circle around its nearest station, toward and away from Work. The one 5.5 km from Work ranked 5 and the one 8.6 km away 86 without favorites, 11 and 41 with the five garden favorites. The far copy was nearer a station (0.36 km against 0.50 km), so only the place distance favored the near one. `DistanceTests` passes. |
| Exploration | Pass | Reranks 2 and 3 on 129 listings each marked ranks 7 and 14, and the gallery showed exactly two exploration labels in the top 20. None of the four listings has a favorite, dismissal, contact request, note or pairwise answer. |
| Pairwise questions | Partly verified | One answer in the Compare tab (headless) chose 3834 W Palmer St #3F, rank 36, over 2862 W Belden Ave #Ch-1, rank 35; at the next rerank they ranked 11 and 74. On the fresh profile, with the interview complete and records present, the invitation is pending: `features.py invite-compare` exits 3 with "not connected. No messaging platform is set up", and `ranking.py questions --count 8` returns 8 pairs. Delivery through a messaging gateway was not verified, because the throwaway root has none. `test_compare_invitation.py` passes. |
| Review pass | Pass | A controlled review of rerank 3, saved with `ranking.py review-save`, moved the top favorite from rank 1 to 45 and another listing from 40 to 2. It was reverted with held-out mean 48.2 before and 72.4 after (median 25 and 80). The gallery showed "Review moves reverted", both moves with their reasons and confidences, and both check values (`results\screenshots\review-reverted.png`). |

Also live: the Score and rerank button and the Compare tab (headless, screenshots in `results\screenshots`), and a note read into a reading. The note "I love the bay windows and the crown molding here. The kitchen feels cramped though." on 3300 W Wabansia Ave APT 2F was read by the next features wake, in 4 API calls, as mixed, liking `tag:bay window` and `tag:crown molding` and disliking `rating:kitchen_size`. At the next rerank those weights moved from 0.017, −0.122 and −0.032 to 0.407, 0.295 and −0.339, and the listing from rank 23 to 12.

### Five-favorites experiment

The spike's store: 129 real listings in Lake View and Logan Square, at most 2,600 USD, at least 2 bedrooms, with the spike's garden-unit judgment (`experiment.json`): the same five favorites and 16 unseen garden units, 635 comparisons with similar-price listings. The favorites were set in the store and the rerank started with the gallery's button. Scoring order is the order by score, without exploration; shown ranks include the two exploration listings.

| | Comparisons won | Unseen above every similar-price listing | Mean rank of the unseen, of 129 | Favorites' held-out ranks |
| --- | --- | --- | --- | --- |
| Before the favorites, scoring order | 62 | 0 | 107.7 | |
| After, scoring order | 545 | 6 | 29.5 | 3, 18, 25, 80, 115 (median 25, mean 48.2) |
| After, shown ranks | 538 | 5 | 31.0 | |
| After the review pass | unchanged | unchanged | unchanged | median 25, mean 48.2 before and after |
| The spike's scoring model, same store | 545 | 6 | 29.5 | mean 48.2 |

- Current `main` reproduces the spike's scoring-model result on this store exactly, with prior scale 2 and weak-negative weight 0.1.
- Units marked only by their label rank above every similar-price listing: 2080475326 (`Unit G`, rank 6), 465862055 (`#G`, 8), 464925436 (`#Basement`, 9) and 464856576 (`#G`, 10). So do 2065604847 (text only, 7) and 402763439 (text only, label `B`, 16). In the shown ranks the exploration listing at rank 14 (2078963383, 2,400 USD) sits above 402763439, which costs it its place.
- The same six stay low as in the spike: 466054100 (`#G`, 39), 70459262 (text, 41), 113953556 (`#G`, 46), 2090032432 (`Unit Garden`, 52), 466125881 (`#G`, 79) and 2069930270 (`#Gn`, 87).
- The review pass ran through the real `apt-scraper-review` job (`hermes cron run`) on the after-favorites rerank. It moved nothing (outcome "no moves"), rewrote all 50 reasons, flagged the like "Lots of daylight" on four garden units, two of them favorites, and the dislike "Carpet" on 2068028186, whose summary mentions carpeted bedrooms, and gave 7 findings, among them that the `rating:ceiling_height` weight of −0.904 favors dim garden units and that `unit_level:garden` at +0.874 has no stated preference behind it. Usage: 4 API calls, 380 input, 7,104 output, 102,310 cache-read and 50,724 cache-write tokens, 46 s. Three later live reviews (after the pairwise answer, after the note, and on the fresh profile) also made no moves. One of them rewrote only 8 of the 50 reasons, though the task asks for every listing.
- Against the bar written in the acceptance item (6 of 15, mean 27 of 128), the count is the same, 6, but of 16 unseen units, and the mean rank is worse, 29.5 (31.0 as shown). That bar combines the scoring model's run on a different set of 128 listings (7 of 15, mean 27.3, ticket 0020) and its rerun on these 129 (6 of 16, mean 29.5). The item stays unticked until the owner says which applies (Comments).

### Embedding spike

The measurement is in [the embedding research](../research/embedding_preference_model.md): neither photo nor text embeddings rank the unseen garden units better than the scoring model (the photo similarity term 7 of 16 against 6, the Gaussian process no better or worse), at 1.7 GB on disk and 2 GB of memory per run. The owner decided on 2026-10-25 that the embedding path stays out (ticket 0025, Comments), so the installers build no embedding environment and the ranking runs on the standard library.

### Checks

- `python -m unittest discover -s tests` (Python 3.12.10), 508 tests. The first run had 2 errors, both from one connection reset in `test_rerank_requires_authentication_same_origin_and_an_empty_json_object` while the search and extraction ran. The second run passed with none skipped, and the module alone passed six times. The reset reproduces: [0033](0033-rejected_post_resets_connection.md).
- Under Hermes's Python 3.14.9: `test_rerank`, `test_scoring`, `test_review`, `test_compare` and `test_implicit_signals` pass. `test_features_run` and `test_compare_invitation` import Pillow, which Hermes's bare Python lacks; they pass under 3.12.
- `npx playwright test` (headless, production build): 38 passed. `ruff check profile tests`: clean.
- Cost of the live runs: the fresh profile's interview, search wake and 87 extractions took 119 sessions and 379 API calls; the five-favorites profile's reviews and note reading took 16 API calls.

### Not verified, for the owner

- The installer. Run `install.ps1` on Windows: it should end in the dashboard's chat for `apt-scraper`, and Score and rerank in the gallery should rank with no package beyond the standard library. Run `install.sh` on a Mac and record its output here.
- Delivery of the pairwise invitation: on a profile with Telegram connected, once the interview is complete and two listings have records, the features job should send the "answer 5 to 8 questions" message once.
- The ranking items in a profile installed by the installer. Everything above ran in a profile installed by `hermes profile install`, with the installer's post-profile steps reproduced.

Tech debt fixed: the Design section's path to the spike's root held a control character in place of `\a`.

## Comments

- 2026-10-25: The strict "every unseen garden unit" criterion was not met by the scoring model (7 of 15, then 6 of 16 on a second run) and the owner accepted the model; the embedding path was measured and stays out. The acceptance items were adjusted to what was decided.
- 2026-10-25: Question for the owner. The five-favorites item's bar, 6 of 15 strictly above and mean rank 27 of 128, combines two runs of the scoring model on different listings. On the spike's 129 listings, current `main` gives exactly the result accepted then: 6 of 16 and mean rank 29.5 in score order, 5 of 16 and 31.0 in the shown ranks, where an exploration listing lands in between. (a) Take the scoring model's result on the same listings, in score order, as the bar: the item passes. (b) Keep the written bar: the item fails on mean rank and needs model work in a ticket of its own. Recommendation (a): the bar is there to show the ranking is no worse than the model the owner accepted, and on identical data it is identical; exploration listings are placed on purpose and are not the model's order. Silence means (a). The installer runs on Windows and macOS and the Telegram delivery of the pairwise invitation are also the owner's.
