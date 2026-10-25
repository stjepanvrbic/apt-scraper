---
title: Continuous search of the listing sites
type: Feature
state: In Progress
priority: 1
assignee: agent
milestone:
parent:
blocked_by: [0001, 0002]
related: []
---

## Outcome

Matching listings from every listing site reach the gallery continuously and are marked unavailable when gone.

## Acceptance

- [x] Of the matching listings a manual search finds that were posted more than a day earlier, the agent has found at least 90%.
- [x] Each site's highest-placed listings are read first.
- [x] Re-reading an unchanged listing makes no model call, checked in the Hermes session usage.

## Spec

[Spec › Search](../spec.md)

## Design

Scheduled Hermes job. Fetching, parsing, filtering and change detection run as code, and the model only analyzes new or changed listings. Browser or computer use where plain fetching is blocked.

## Deviations

- The owner chose Zillow as the first listing site and Chicago as the target area. The site layer takes further sites as modules in `profile/skills/search/scripts/sites/`. Only Zillow exists and is verified.
- The Design's browser or computer-use fallback is not built. Zillow's PerimeterX check answers both plain HTTP and headless Chromium, including Hermes's bundled Chromium driven by agent-browser 0.26.0, with HTTP 403 and a "Press & Hold" page before any listing loads. `curl_cffi` with Chrome's TLS handshake gets the full pages from this connection. A headed browser opens windows on the user's desktop, and computer use against the check would mean solving it. On a block the search backs off for 30 minutes, doubling up to a day, and `status` reports it. No proxy, account or bot-check solving is used.
- The model judges only the must-haves in `criteria.other`, once per listing content. Price, rooms, size, move-in date and area are judged in code from facts the search stores as features. A listing without such facts, such as one imported by hand, is judged by the model on every criterion, each written as a phrase. A listing awaiting a judgment stays hidden until judged, so a listing outside the criteria is never shown.
- Following the interview review note, a criteria change is judged in code from the stored features. The store's `update_settings(judge=...)` and `apply_criteria(judge)` apply the stored criteria to every listing inside one write transaction, replacing `update_settings(outside=...)`. Saving criteria and the end of every search run go through them. `settings.py` has no `--outside` option or `listings` command any more, and the model judges only phrases a listing has not been judged on.
- The installers resume the cron job `apt-scraper-search`, next to the contact job, instead of the agent resuming it. A run before the interview is complete searches nothing and makes no model call. SOUL tells the agent to run the job once when the interview completes and to report problems and new listings.
- `curl_cffi` is not in Hermes's Python. The installers build a separate environment under `HERMES_HOME/workspace/search/environment` with `profile/scripts/search-run.py install`, using the Python `hermes pm env` lists, from wheels pinned with their hashes in `profile/skills/search/requirements.txt`. The cron run rebuilds it only when Hermes's Python or the requirements changed. Hermes starts the model whenever a cron script fails (`cron/scheduler_prompt.py`, "Script Error"), so the cron run never fails: a missing environment, a failed rebuild or a search that stopped is recorded for `status` and the run ends without waking the model.
- The search keeps its own records in `search.sqlite3` next to the listing store, so the store schema stays at version 2. The store gains `upsert_listing(listing, matches_criteria=...)` and `apply_criteria`.
- Contacted listings keep their history in view: Contacted and Viewings show a listing with a contact request or a viewing whether or not it meets the criteria, and its details open. Rankings, Favorites and Dismissed still hide it. The gallery test that pinned the old rule now pins this one.
- The contact job test took the only job in `profile/cron/jobs.json`. It now selects its job by id, because the file holds both jobs.

## Result

Delivered in the commit "Continuous search of the listing sites" on `feat/listing_search`, rebased on `main` after contact and viewings.

```
profile/cron/jobs.json                          job apt-scraper-search "Listing search", every hour, deliver local, failures to origin
profile/scripts/search-run.py                   cron script and install command for the search environment
profile/skills/search/requirements.txt          curl_cffi and its dependencies, pinned with hashes
profile/skills/search/SKILL.md                  running, status problems, reporting, judging must-haves
profile/skills/search/scripts/search.py         run, status, pending, judge, reported
profile/skills/search/scripts/matching.py       criteria judged from stored facts and must-have judgments
profile/skills/search/scripts/state.py          card hashes, area lookups, blocks, runs, report marker
profile/skills/search/scripts/client.py         paced curl_cffi session with Chrome's TLS handshake
profile/skills/search/scripts/sites/            site registry, errors, Zillow
profile/SOUL.md                                 Search section after the contact section
profile/skills/interview/                       criteria changes judged in code
profile/skills/gallery/scripts/store.py         update_settings(judge=...), apply_criteria, contacted listings stay open
profile/skills/gallery/scripts/queries.py       Contacted and Viewings regardless of criteria
install.ps1, install.sh                         build the search environment, resume apt-scraper-search
docs/search.md, docs/gallery.md, README.md
tests/test_search_*.py                          matching, Zillow pages, runs, cron script and shipped job
```

Verified on this Windows machine with Hermes 0.22.5, in a separate profile `aptsearch-check` installed from the branch with `hermes profile install --name aptsearch-check`. The owner's `apt-scraper` profile and data were not touched. Nothing opened or focused a browser. The test profile's search job is paused afterwards.

- `python -m unittest discover -s tests`: 240 passed, 0 skipped. `ruff check profile/skills profile/scripts tests` clean. Hermes's skill linter and skills guard: no findings for `search` and `interview`. The guard flags `os.environ` in `profile/scripts/search-run.py`, which is outside the skills.
- Sample criteria: Lake View and Logan Square in Chicago, at most 2,600 USD a month, at least 2 bedrooms, must-have "Dogs allowed", site Zillow. The first run reported "Lakeview" as an area Zillow does not know. Zillow writes "Lake View".
- A `-Q` session "Hi" with the interview complete: the agent reported the connections, resumed the paused job, ran it once, and said so in its reply.
- First full run, triggered with `hermes cron run`: 109 listings saved in 16 minutes, and the cron agent judged all 109 (58 dogs allowed, 45 not, 6 not stated). 64 showed in the gallery. 8 pages failed on shapes the fixtures did not have: `specialFeatures` as a plain list, floor-plan photos as bare links, and `/b/` buildings whose units are `ungroupedUnits`. Each now has a test and reads.
- The gateway's own ticker fired the job at 00:51 (`hermes cron runs`, source `builtin`): 109 unchanged, 19 new, 0 unreadable, in 3.5 minutes. Its agent judged the 19 with 3 API calls.
- Two further runs: 128 unchanged, no listing page read, `{"wakeAgent": false}`. Hermes recorded each run's session in `state.db` with 0 API calls and 0 tokens, against 11 and 3 calls for the runs that judged listings.
- Highest-placed first: a run reads page 1 of every area in Zillow's `priorityscore` order, then page 2, and opens listing pages in card order. A query over 820 results reads its own first page, then the first page of every price band, then the bands' later pages in turn. `tests/test_search_run.py` and `tests/test_search_zillow.py` check the request order. The real runs saved listings in result-page order. The sample queries had 44 and 98 results, so the split was not exercised live.
- Coverage: a separate script queried Zillow for the same criteria at 00:58 with the site's own filters and listed 127 cards. 123 were posted more than a day earlier, by Zillow's age label or days on Zillow. The store held 122 of them, 99.2%. The missing card is 3950 N Lake Shore Dr under building id 2757151036. The agent's runs get the same building under id 2797733629 and hold it and one of its units. The manual query uses the same site and filters a person would, so it is not an independent source.
- Gallery: `gallery.py --database <test store> --port 18791 open --no-browser`, then headless Playwright 1.64.1 on Hermes's Chromium. Rankings showed "64 places" with Zillow photos, and a listing opened to its photos, price, rooms, size and details. Screenshots were taken, and the server was stopped.

After review, on the same test profile:

- The installers' environment step, run for the test profile with the same PowerShell commands: Hermes's `tools/python-3.14.9` from `hermes pm env` built the environment from the hashed wheels, exit 0, with curl_cffi 0.16.3, cffi 2.1.1 and certifi 2026.10.23.
- A cron run with the fixed code: 128 unchanged, 1 new, judged with 3 API calls, no errors, `status` exit 0.
- With the environment moved aside, a cron run made 0 API calls, Hermes recorded it as ok, and `status` exited 3 with "The search environment is missing. Rerun the apt-scraper installer." The environment was put back.
- Reproduced in tests: an amenity list with a blank entry, broken page JSON, a missing result count and a 404 search page are recorded and the run goes on. Criteria narrowed during a run hide every listing outside them when the run ends. A by-hand import of a 9,000 USD one-bedroom stays hidden under a 2,400 USD, 2-room criterion until the model judges each criterion. A contacted listing outside the criteria stays in Contacted and Viewings.
- Frontend checks in `gallery/`: `npm ci`, `npm run lint` and `npm run build` pass, and the build leaves `git status` clean. `npx playwright test`, headless: 16 passed, 0 skipped. The new browser test puts a contacted listing with a booked viewing outside the criteria: it leaves Rankings, shows in Contacted and Viewings, and opens from both. Against `main`'s `queries.py` and `store.py` the same test fails.

Not verified:

- macOS. The scripts have no platform-specific code except the environment's `Scripts/python.exe` against `bin/python`.
- A full installer run. The environment step ran with the installer's commands for the test profile, and `cron resume apt-scraper-search` ran twice, both exit 0. The macOS step reads `python3` from `hermes pm env` and is not run.
- A listing going off the market. No listing left the results during the test runs. Marking unavailable is covered by `tests/test_search_run.py` and the Zillow gone cases in `tests/test_search_zillow.py`.
- Block behavior over days. Apart from the plain HTTP and headless Chromium probes, no request drew a bot check during this work, from 22:54 to 00:58.

Found, not fixed here: Zillow gives the same building different building card ids on different requests, at least for 3950 N Lake Shore Dr, so the gallery can show one building twice.

Also found, not fixed here: `test_gallery_launcher` `test_open_restarts_a_changed_install_and_rotates_its_secret` failed once in three full runs with `PermissionError` on its record under `~/.secrets/apt-scraper/gallery/`, and passed five runs on its own. This change does not touch the gallery launcher.

Tech debt fixed in the touched code: the interview no longer has the model re-judge every stored listing on a criteria change.
