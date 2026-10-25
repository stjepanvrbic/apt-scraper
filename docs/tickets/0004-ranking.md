---
title: Ranking that learns from favorites and notes
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: [0003, 0008]
related: []
---

## Outcome

Every available listing has a rank and a reason, and the ranking follows the user's favorites, dismissals, notes and preferences.

## Acceptance

- [x] Every available listing shows a rank and a one-line reason.
- [ ] After the tester favorites five listings they consider alike, the unseen listings they judge alike rank above the rest of similar price.
- [x] Dismissing listings lowers similar listings.

## Spec

[Spec › Ranking](../spec.md)

## Design

The model reads a listing only when something about it or about the user's preferences needs judging. Routine re-ranking and unchanged listings cost no model call. Quality comes first, cost second.

Each listing is read once for its profile, in a fixed vocabulary, and again only when its content changes. A note is read once into the same vocabulary. Re-ranking after a favorite, a dismissal or a note scores every listing in code from the stored profiles.

A stated preference is read once. When vocabulary terms express it faithfully, such as "balcony or terrace" for "Balcony", it is scored in code against every stored profile, so it costs one read. When the terms would only approximate it, such as a playground nearby against "near a park", or none fits, such as walking distance to a named station, each shown listing is judged on the phrase itself, from its text and stored profile without the photos, and the reading records why. The trade-off: that costs one text-only read per shown listing for the phrase, in batches of 25, against a mapping that would save those reads but rank listings by something the user did not ask for. The agent takes the per-listing path whenever a mapping would rank wrongly.

## Deviations

- The ranking is its own Hermes cron job, `apt-scraper-ranking` ("Listing ranking", every 2 minutes, toolsets terminal, file and vision), next to the search and contact jobs. Its script `profile/scripts/ranking-run.py` ranks every listing in code on every run and prints `{"wakeAgent": false}` unless a listing, a note or a preference waits for analysis. The installers resume it. The search job is unchanged.
- The model's analysis of a listing is a profile: a one-line summary, the neighborhood, and terms from a fixed vocabulary in four dimensions (photos, description, amenities, location). Code compares listings by these terms, weighted by rarity, and by the monthly price, size and bedrooms the search already stores. A fixed vocabulary keeps the profiles of different listings comparable without an embedding model.
- Photos are looked at with Hermes's `vision_analyze`, up to 3 per listing. On this provider it attaches the image to the main model (`tools/vision_tools.py`, `_vision_analyze_native`), checked live. The agent analyzes one batch per run (6 listings that need a profile, or else 25 that need only preference judgments, up to 25 notes, and the preferences not read yet), so a run's conversation stays small.
- Notes are read once each by the model: their sentiment and the vocabulary terms they liked or disliked. Re-ranking after a note uses that reading and the stored profiles. Only the user's notes are read.
- Learning has two parts. One is similarity to the 3 closest examples (favorites and positive notes, or dismissals and negative notes). The other is the traits at least two examples share, terms more common among them than among all listings. The traits part was added after the first real experiment, where similarity alone left most alike listings low (see Result).
- Owner decisions in review: a stated like or dislike is read once as a phrase and mapped to vocabulary terms when they express it faithfully, the way notes are. A listing has it when its stored profile has one of those terms, so adding "Balcony" costs one agent run, not one read per listing. A phrase the vocabulary expresses only loosely or not at all is judged per listing, from text and stored profile without the photos, so the ranking stays right. Its reading stores the reason, and `status` lists it under `judged_per_listing`. This replaces the first version, which judged every new phrase per listing.
- The run catches every exception, including a store that cannot be opened, a corrupt store file and a lock past the store's timeout, records it for `status` and exits 0, because Hermes wakes the model on a failed cron script.
- `ranking.json` counts how many runs handed each listing content, note and preference to the agent. After 3 runs without a saved analysis it stops handing it over and `status` reports it. A shown listing without a profile is then ranked by its price, size and rooms only, with a reason saying so. A content change starts the count again, and `ranking.py retry` clears it. The skill tells the agent to write the profile from the photos that loaded, or from the text alone, when `vision_analyze` fails, and to leave out a listing it cannot analyze.
- The store is at schema version 3, with `note_readings` and `preference_readings` tables. `analyze` merges the given feature keys into the current ones instead of replacing them, and `rerank` writes the whole ranking in one transaction. The search now writes only its own keys, so the search and the ranking cannot overwrite each other's features.
- A dismissed, unavailable or excluded listing has no rank, and its reason says why. The gallery showed "Awaiting ranking" for any listing without a rank, so its card and drawer now show "Not ranked" with that reason. No new components.
- `profile/SOUL.md` gains a Ranking section. The agent runs `ranking.py status` at the first message and reports a failed ranking run. The job records a failure instead of failing, because Hermes wakes the model whenever a cron script fails.
- The owner replaced Spec › Search's "analyzes a listing once" on `main` with: the model reads a listing only when something about it or about the user's preferences needs judging, and routine re-ranking and unchanged listings cost no model calls. With must-haves in `criteria.other`, a new listing is read once by the search for its must-haves (text only, before it is shown) and once by the ranking for its profile (photos and text, once it is shown), which fits that line. Not exercised here, because the test criteria had no must-haves.

## Result

Delivered in the commit "Ranking that learns from favorites and notes" on `feat/learning_ranking`. The ticket stays In Progress. The owner is holding the pull request unmerged while the ranking design is reworked (user-triggered scoring, every photo analyzed, a two-stage feature extraction and scoring pipeline with learned preferences). What follows describes the code on the branch as it stands.

```
profile/skills/ranking/SKILL.md                 analyzing listings, notes and preferences, failures, saving, explaining
profile/skills/ranking/scripts/scoring.py       vocabulary, validation, similarity, shared traits, preference terms, fallback rank, reasons
profile/skills/ranking/scripts/ranking.py       run (never fails), status, pending, analyze, retry, attempt counts
profile/scripts/ranking-run.py                  cron script, hands over to ranking.py run
profile/cron/jobs.json                          job apt-scraper-ranking
profile/skills/gallery/scripts/store.py         analyze merges keys, rerank, note and preference readings
profile/skills/gallery/scripts/schema.py        version 3, note_readings and preference_readings
profile/skills/search/scripts/search.py         writes only facts and must_haves
gallery/src/App.tsx, gallery web build          "Not ranked" with its reason
profile/SOUL.md, install.ps1, install.sh        Ranking section, resume apt-scraper-ranking
docs/ranking.md, docs/gallery.md, README.md
tests/test_ranking_scoring.py, tests/test_ranking_run.py, store and migration tests, one Playwright test
```

Checks: `python -m unittest discover -s tests` 272 passed, 0 skipped. `ruff check profile tests` clean. In `gallery/`, `npm run lint` and `npm run build` pass, and `npx playwright test` (headless) 17 passed, 0 skipped. Hermes's skill linter and skills guard report no findings for `ranking` and `search`.

Verified on this Windows machine with Hermes 0.22.5, in a separate profile `aptrank-check` installed from the branch with `hermes profile install --name aptrank-check`. The owner's `apt-scraper` profile and data were not touched. Nothing opened or focused a browser. The test profile was deleted afterwards.

- Listings: the real search with the test criteria (Lake View and Logan Square, at most 2,600 USD a month, at least 2 bedrooms, no must-haves) saved and showed 129 Zillow listings. Preferences: likes "Lots of daylight" and "In-unit laundry", dislike "Carpet".
- Analysis: 22 runs of the job through `hermes cron run` analyzed all 129 listings, 6 per run, about 45 seconds each. Each run looked at the photos with `vision_analyze` and saved with one `analyze`. `status` then reported 129 ranked and 0 awaiting. Every shown listing had a rank and a reason, and so did all 125 after four dismissals.
- Re-ranking without the model: Hermes recorded a session for every run in `state.db`. The runs fired by the Hermes ticker after the favorites (04:03:54) and after the dismissals (04:12:54) have 0 API calls, 0 tool calls and 0 tokens. Their cron output says the script gate returned `wakeAgent=false` and the agent was skipped. The ranks changed in those runs.
- Acceptance experiment. As the tester, I judged the garden-level units alike, from their text and a look at some of their photos (low soffited ceilings, small high windows): 20 listings from 1,400 to 2,500 USD. I favorited five (455063308, 441025098, 2106063102, 2064093099, 2090032432). For each of the 15 unseen garden units, the rest of similar price is the other shown listings within 10% of its monthly price.
  - Before the favorites, garden units won 229 of 551 comparisons with similar-price listings. 1 of 15 ranked above all of its similar-price listings. Mean rank 83.8, against 62.1 for the rest.
  - With similarity alone: 318 of 551, 3 of 15, mean rank 67.3 against 67.5. Not met.
  - With shared traits, the final code: 470 of 551 (85%), 12 of 15 above all of their similar-price listings, mean rank 28.7 against 72.8. The five favorites are #1 to #5.
  - The 3 that stay low (113953556 at #73, 2069930270 at #83, 464925436 at #120) have no "garden unit" term in their stored profile. The model tagged one "basement unit" and did not read the `#G` and `#Gn` unit numbers of the other two as garden units. The strict criterion holds for 12 of 15 unseen alike listings, so the acceptance item stays unticked.
- Dismissals: dismissing two of three near-identical Pine Grove listings, one of two "best 2 bed deal" listings and one of two Southport rehabs moved the remaining twins from #50 to #125, #43 to #109 and #114 to #120, among the 125 shown.
- Note and preference, with the first preference design: a positive viewing note on 464375775 was read once (positive, liking "exposed brick" and "balcony or terrace") and moved that listing from #60 to #12. Adding the like "Balcony" through the interview's `settings.py save` made the job judge that phrase for the 125 shown listings in 5 runs, text only. The review replaced this with preference terms, verified below. The 27 judged to have a balcony moved from mean rank 77.5 to 53.6. Other exposed-brick listings barely moved (85.0 to 83.3), because exposed brick is also a trait of the dismissed Pine Grove listings.
- Usage of the 27 runs that woke the agent, from `state.db`: 112 API calls, 426 tool calls, 134,860 output tokens, 3,534,275 cache-read and 1,063,798 cache-write input tokens.
- Gallery: `gallery.py --database <test store> --port 18797 open --no-browser`, then headless Playwright. Rankings showed "125 places", the five favorites at #1 to #5, each card with its rank and reason. A screenshot was taken and the server stopped.
- Acceptance item 1 holds in steady state. A shown listing waiting for its first profile shows "Awaiting ranking" until a run analyzes it, and one that the analysis gave up on shows a rank by price, size and rooms.

After review, verified in a second test profile `aptpref-check` with 14 real Zillow listings (Logan Square, at most 1,900 USD, at least 2 bedrooms), deleted afterwards. No gateway was started: in Hermes 0.22.5, `hermes cron run` of an agent job and every chat entry point reach the agent through `connect_gateway`, which starts a gateway (`cron/scheduler_gateway_gate.py`). Each job run was therefore reproduced as the job's script with Hermes's cron Python, then the prompt from `cron/scheduler_prompt.py` `_build_job_prompt` run by `hermes_cli.oneshot.run_oneshot`, which builds the agent in-process, with `HERMES_DISABLE_LAZY_INSTALLS=1` so Hermes's bootstrap does not relaunch the process (`hermes_cli/venv_sync.py`, `prepare_launch`). `gateway-starts.log` stayed at 11 entries and no gateway process ran.

- Run path: a corrupt store file made the cron script print the failure and `{"wakeAgent": false}` and exit 0, and `status` exited 3 naming the `DatabaseError`. A write lock held for 15 seconds delayed the run 13 seconds, then it succeeded. A lock held past the store's timeout and a store that cannot be opened are covered by unit tests with the exception raised.
- Attempt limit: the first agent runs crashed before doing anything (the relaunch above). After 3 runs the 14 listings were given up and ranked by price, size and rooms with that reason, the 2 preferences were reported, `status` exited 3 naming them, and the gate stayed false. `retry` handed them over again.
- Analysis: 3 runs analyzed the 14 listings and read the 2 preferences, 5, 4 and 5 API calls. `status` reported 14 ranked and no problem.
- Adding the like "Balcony" through `settings.py save`: one run, 3 API calls (read the report, write the file, run `analyze`), 371 output tokens, the reading `["balcony or terrace"]`, no listing judgment stored, and the next run gated. Adding "Hardwood floors" the same way: one run, 3 API calls. The 7 listings with that term moved from mean rank 6.0 to 4.0 and the other 7 from 9.0 to 11.0.
- A like the vocabulary cannot express, "Within a 20 minute walk of the Logan Square Blue Line station": one run read it with no terms and the reason "No term covers walking distance to a named station.", one text-only run judged all 14 listings on it, 3 API calls and 2,812 output tokens, and the next run gated. `status` lists it under `judged_per_listing` with that reason.

Not verified:

- The acceptance item as stated, see the experiment: 12 of 15. The experiment ran with the first preference design, under which the stated likes and dislikes were judged per listing. It was not repeated with preference terms, because its 129-listing test profile was deleted and re-analyzing costs about 22 runs.
- macOS and a full installer run. The resume step ran as `hermes cron resume` on the test profile.
- Must-haves together with the ranking.
- A store locked past its timeout, live. The reproduction was stopped before it ran. Unit tests raise `database is locked` from the store and check the run exits 0 and `status` reports it.
- Favorites and dismissals were saved with the store's `feedback`, the method the gallery's feedback route calls, not by clicking in the gallery. The click path is covered by the existing Playwright tests.

Found, not fixed here:

- Zillow photo lists often hold the same photo twice in two sizes (`-r_b.jpg` and `-o_a.jpg` with one key), so the ranking sometimes looks at the same photo twice.
- The first `hermes cron resume` or `cron run` on the test profile started a host gateway (`gateway run --quiet --idle-exit`) serving every profile, including `apt-scraper`. `apt-scraper` and `default` have no cron jobs, so nothing ran there. Only the ticker's heartbeat files in their `cron` folders changed. The gateway was stopped afterwards, and both profiles show "stopped" as before.

- Deleting a test profile in Hermes 0.22.5 left its session identity purge pending until `hermes profile purge-identity <name>` ran, and the deleted name cannot be installed again.

Tech debt fixed in the touched code: `listed([])` raised instead of returning an empty list of names, so a reason's "similar overall" fallback never worked. It now returns an empty text. The search replaced a listing's whole features after reading them, so a concurrent writer's keys could be lost. `analyze` now merges keys and the search writes only its own.
