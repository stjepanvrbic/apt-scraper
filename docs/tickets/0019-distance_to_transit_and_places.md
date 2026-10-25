---
title: Distance to the nearest rapid-transit station and to the user's places
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

Each listing's feature record has its straight-line distance to the nearest rapid-transit station and to each of the user's places, and the scoring model counts them by the location priorities.

## Acceptance

- [x] Each place is geocoded once when saved, through Nominatim at its published rate of one request per second, with a User-Agent that names this project. A place that cannot be geocoded is reported to the user, who is asked to describe it again.
- [x] A place is a name and an address. The interview no longer asks for the way of getting there, the longest trip or the visits per week, and the store no longer keeps them.
- [ ] The rapid-transit stations of the user's city come from its GTFS stops once, filtered to rail, metro and bus rapid transit, and are kept in the store.
- [x] Each listing with coordinates gets its distance to the nearest station and to each place computed in code from coordinates, with no network call, when its feature record is built or a place changes.
- [x] A listing without coordinates has those features as unknown, and unknown does not count for or against it.
- [x] Spec › Acceptance "Location" passes in a test against the scoring model.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Ranking](../spec.md#ranking) (location), [Spec › Acceptance](../spec.md#acceptance) (Location, Interview)

## Design

- Distance is the great-circle distance between coordinates. No routing engine, travel time or departure time.
- Which feed is the city's rapid transit is a setting with Chicago's CTA feed as the default; the research report [Geocoding and routing provider](../research/geocoding_and_routing_provider.md) names the sources.
- Nominatim results and the station list live in the store, so a rerank makes no network call.

## Deviations

- Bus rapid transit does not count. GTFS `route_type` has no value for it, only 3 for every bus, so no bus stop is a station. The filter is rail, metro, light rail and monorail: basic types 0, 1, 2 and 12 and the extended types 100 to 199, 400 to 499 and 900 to 999. CTA has no bus rapid transit, so Chicago loses nothing. The stations acceptance item stays open until the owner accepts this or asks for bus rapid transit routes to be named in a setting.
- The listing features job fetches the stations, not the interview, because the CTA feed is 68.7 MB. A failed fetch is reported by `features.py status` and tried again after 6 hours.
- The distances are stored in `code_features` and computed again for every listing when the places, the `transit_feed` setting or the stations change.
- A place saved before this change has no coordinates. `settings.py status` lists it under `places_without_coordinates` and exits 3, and the agent saves the places again, which looks them up. Extraction and the search do not wait for it.
- Readings of notes and preferences may name `station_distance` and `place:<name>`, so a like such as "Close to the L" maps to the distance.
- Requests are one second apart within one save. Separate saves are not paced against each other, because each follows a model turn.
- Nothing found by Nominatim is not cached: a repeat needs the user to give the same address again, and a cached miss would keep a place unfindable after OpenStreetMap adds it.

## Result

Delivered on branch `feat/distance_features` at `7929944`, in a pull request to `main`, not merged.

- A place is `name` and `address`. Store migration 6 strips `travel_mode`, `longest_trip_minutes` and `visits_per_week` from saved places, and validation rejects them.
- `settings.py save` geocodes each new place address through Nominatim (`geodata.py`), one request a second, User-Agent `apt-scraper (Hermes Agent apartment search)`. Results are kept by address in the `geocodes` table. Nothing found exits 5, Nominatim unreachable exits 6, and both save nothing. The interview skill asks the user to describe the place again, and says back where each place was found with the OpenStreetMap attribution.
- The `transit_feed` setting defaults to CTA's feed. The features job downloads it once per URL and keeps the stations (`stations`, `transit_feed` tables): stops a rail route serves, at their parent station.
- `code_features` has `nearest_station`, `station_distance_km` and `place_distances_km`, great-circle distances from stored coordinates. Unknown without coordinates.
- `scoring.py` adds the columns `station_distance` and `place:<name>`. Their starting weights are −1, −0.5 or −0.25 by the location priority of rapid transit and of places. An unknown distance is the column mean, so it counts neither way.

Verification at `7929944`:

- `python -m unittest discover -s tests`: 432 passed, 0 skipped. Spec › Acceptance "Location" is `DistanceTests` in `tests/test_scoring.py`, with and without favorites, and `test_the_distances_to_stations_and_places_count_by_the_location_priorities` in `tests/test_rerank.py` runs it through a real store. With the place prior set to zero, the acceptance test fails.
- The touched test modules under Hermes's Python 3.14.9: 158 passed at the first candidate.
- `ruff check profile tests`: clean. Hermes's skill linter (`tools/skill_linter.py`): 0 findings for `interview`, `listing-features` and `ranking`; `gallery` keeps its 6 earlier warnings. Skills guard: nothing new in the gallery scripts, `interview` safe. `_scan_context_content` leaves `SOUL.md` and both changed skills unchanged.
- Real services, once, on a throwaway Hermes home that was then removed: Nominatim placed "233 S Wacker Dr, Chicago, IL 60606" at Willis Tower and returned no result for a nonsense address (exit 5, nothing saved). Saving the found place again sent no request. The CTA feed (68,738,293 bytes, published 2026-09-14) gave 143 stations in 4.0 s, the count the research report measured. Listings got Logan Square at 1.31 km, Damen (Blue) at 0.035 km and Midway at 0.026 km.
- Rerank of 300 listings with 50 favorites: 1.39 to 1.41 s on a quiet machine. Under load from another process, `main` took 1.60 to 2.31 s and this branch 1.55 to 2.42 s, so the 2-second timing test can fail on either. The reviewer profiled the change at about 1% of a rerank, from the haversine distance.
- Review: an independent `review-code` pass in two rounds. Fixed from it: two places at one address never counted as geocoded; places without coordinates stopped extraction; feed parse errors escaped the retry; partial geocodes were lost when Nominatim failed partway; the User-Agent named a private repository; the feed URL was resolved in two places; `SOUL.md` read exit 3 as an incomplete interview only.
- No ADR applies: the spec has no decision on location.

Tech debt fixed: one great-circle `distance_km` in `code_features.py` replaces the equirectangular approximation `scoring.py` used for `near_favorites`. Migration 3 no longer computes code features with today's code; migration 6 does, after creating the tables that computation reads.

Found, not fixed: `tests/test_gallery_launcher.py` writes into the real `~/.secrets/apt-scraper/gallery/`, so two suites running at once can fail with "Permission denied" there. The rerank timing test has about 0.6 s of headroom on a quiet machine and fails under load.

## Comments

- 2026-10-25: The owner chose plain distance over routing. The provider research's routing recommendation (r5py, departure times, Metra) is not used.
- 2026-10-25: Questions for the owner. (1) Bus rapid transit has no GTFS route type, so no bus stop counts as a station. Accept that, or name bus rapid transit routes in a setting? Recommendation: accept, since Chicago has none and a route list per city is upkeep with no current user. (2) Nominatim's policy asks for OpenStreetMap attribution where its results are shown. The interview says it back; the gallery's reasons cite place distances without it. Recommendation: add "© OpenStreetMap contributors" to the listing details when the score UI is reworked.
