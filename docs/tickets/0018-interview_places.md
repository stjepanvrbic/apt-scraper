---
title: Interview captures places and location priorities
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

The interview captures the places the user travels to and what matters about them, and the location priorities, and the user changes them later by telling the agent.

## Acceptance

- [x] The settings gain places, each with a name, an address or landmark precise enough to geocode, the way of getting there (walk, bike, transit or car), the longest accepted trip in minutes and how often the user goes, and location priorities: how much the places, nearness to rapid transit and each area of the criteria matter. The store validates both.
- [x] `missing_settings()` lists them until they are answered. "No places" saves an empty list and counts as answered. A profile whose interview was complete before is asked only for these at its next conversation.
- [x] A real `-Q` session runs the interview and stores both, and a later "I work at <new address> now" changes the stored place. The same change through the Notes tab is saved too.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Interview](../spec.md#interview), [Spec › Terms](../spec.md#terms) (Place, Location priorities), [Spec › Acceptance](../spec.md#acceptance) (Interview)

## Design

- Extend `SETTINGS` validation and `missing_settings` in `profile/skills/gallery/scripts/store.py`, the interview skill's "What to capture" and "Running the interview", `settings.py` and the README's "Your search".
- Importance as low, medium or high, so the user answers in words and the scoring model maps each to a prior strength.
- Places are search settings, used only for ranking. The contact work never shares them with listing agents.
- Geocoding the places belongs to the transit and travel-time ticket, not here.

## Deviations

- Places carry no coordinates. Ticket 0019 (distance to rapid transit and travel time) needs the place as the user named it, and the `address` field holds exactly that. Storing coordinates inside a setting the agent rewrites whole would let a changed address keep the old coordinates, so the geocoding belongs in its own store keyed by address, which 0019 builds.
- The settings format: a place is `name`, `address`, `travel_mode` (walk, bike, transit or car), `longest_trip_minutes` and `visits_per_week`, and names are unique without regard to case. `location_priorities` is `places`, `rapid_transit` and `areas`, an object from each area of `criteria.location` to low, medium or high.
- `location_priorities.places` is needed only when `places` is not empty, and every criteria area needs a priority. Saving criteria drops the priority of each area they no longer name, in the same transaction, and a priority for an area the criteria do not name is rejected.
- The search waits only on the settings it reads, by the owner's decision: before its first search it waits for the whole interview, as Spec › Interview requires, and once a run has searched a listing site it needs only `criteria` and `listing_sites`. A profile that is already searching keeps searching while places or location priorities are missing, and the agent asks for them at the next conversation. `SearchState.missing_settings` holds the rule for the cron launcher, `search.py run` and `search.py status`. Places and location priorities gate only their consumers, the scoring model and ticket 0019, which do not exist yet.
- `ListingStore.missing_settings` takes the setting names a reader uses. SOUL runs the search status at every first message and stays silent while it waits for the interview, and it forbids starting a search only while the search waits for the interview.
- The contact skill never shares places or location priorities with listing agents.
- The live Notes run needs a profile named `apt-scraper` (`chat.py` refuses any other), so verification installed the branch's profile into a separate Hermes root, `HERMES_HOME` set to `apt-places-hermes` in the temporary directory, which Hermes 0.22.5 treats as a custom root (`hermes_constants.py`, `_hermes_root_of`). The owner's Hermes root and profile were not touched.

## Result

The implementation is the single commit on `feat/interview_places`, with subject "Interview captures places and location priorities". The ticket stays In Progress until its pull request merges.

```
profile/skills/gallery/scripts/store.py          places and location priorities validated, area priorities kept in step with the criteria, missing_settings
profile/skills/interview/SKILL.md                what to capture and how to ask for places and location priorities, changing them later
profile/skills/interview/scripts/settings.py     save help lists every setting
profile/SOUL.md                                  places and location priorities among the changes saved in any conversation
profile/skills/contact/SKILL.md                  places and location priorities never shared with listing agents
profile/skills/gallery/scripts/chat.py, profile/skills/gallery/SKILL.md, docs/gallery.md, README.md
profile/skills/search/scripts/state.py, search.py, profile/scripts/search-run.py   the search waits on its own settings once it has searched
profile/skills/search/SKILL.md, docs/search.md
tests/test_interview.py, tests/test_search_run.py, tests/test_search_launcher.py   new tests for places, priorities and the search gate
```

Verified on this Windows machine with Hermes 0.22.5:

- `python -m unittest discover -s tests`: 251 passed, 0 skipped. The search tests also pass under Hermes's venv Python 3.14.9, whose SQLite 3.53.1 has the JSON functions the search gate uses. `test_interview.py` under Hermes's venv Python 3.14.9: 30 passed. `ruff check` on the gallery and interview scripts and the tests passed. Hermes's skill linter reports no findings for `interview` and `contact`.
- The branch's profile was installed with `hermes profile install` into the separate Hermes root above, with the provider plugin, and its test message replied "ready". Every session ran with `-Q --oneshot`. Nothing opened a browser.
- First interview, three turns in one session: the agent saved criteria, preferences, Zillow and availability, then asked for places, transit and the two areas. The third answer saved two places (Work, 233 S Wacker Dr, transit, 45 minutes, 5 a week; First Ascent Avondale, bike, 20 minutes, 2 a week) and priorities places high, rapid transit medium, Logan Square high, Avondale medium. `status` then reported the interview complete.
- A new session, "I work at 1 N State St, Chicago now", saved the Work place with the new address and left the other place and every other setting unchanged.
- Notes: a gallery on port 18767 with its own database received "I switched gyms. I now climb at Movement Wrigleyville, 3516 N Clark St ..." through `/api/chat`. The profile store's places became Work and Movement Wrigleyville (transit, 30 minutes, 2 a week). The gallery's own database held no settings.
- With places and location priorities deleted from the store, `missing` listed only `places`, `location_priorities.rapid_transit` and `location_priorities.areas`. A new session's "Hi" asked only for the places, transit and the areas, and "No places, I work from home ..." saved `places: []` with transit low and both areas medium, completing the interview.
- An already-searching profile, in a second separate root: the store held only the four earlier settings and the search state one finished run that searched Zillow. `search.py status` exited 0 with state `running`, and `search-run.py` passed the interview gate and stopped at the missing search environment, which that root never built, so nothing reached Zillow. A `-Q` "Hi" asked only for the places, transit and the two areas. It also said the search was running with no new listings, although SOUL says to say nothing when the search needs no attention.
- Afterwards the gateways the runs started in both separate roots were stopped and the roots were deleted. The owner's `apt-scraper` profile still has the `main` skills.

Not verified:

- macOS. The change has no platform-specific code.
- Geocoding and travel times, which are ticket 0019's.
- Every live turn included "skip the account connections" and "don't run the search", so the agent's search start after the interview was not exercised. The search environment was not built in the separate root.

Found, not fixed here: Hermes's skill linter warns about `profile/skills/gallery/SKILL.md` (description over 60 characters, missing version, author, license, tags and a "When to Use" section). The change touches one sentence of that file.
