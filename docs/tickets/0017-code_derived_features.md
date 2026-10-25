---
title: Code-derived listing features and coordinates
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0014]
---

## Outcome

Each listing's feature record has the features code reads reliably: monthly price, size, price per square foot, rooms, unit level from unit labels, days since first seen, photo count and coordinates.

## Acceptance

- [x] Unit level is garden for unit labels such as `#G`, `#G2`, `#Garden` and "Unit Garden", and ground, middle or top where the label or the facts give a floor. The three Chicago garden units PR #13's model missed (Zillow 113953556, 2069930270 and 464925436) read as garden.
- [x] Monthly price and price per square foot come from the cheapest unit, across rental periods. A listing without a size has no price per square foot, not a guess.
- [x] Every Zillow listing whose page gives coordinates stores them, and one without has them as unknown.
- [x] The features are recomputed whenever the listing's content changes, with no model call.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features)

## Design

- Pure functions over the stored listing and the search's `facts`, run when the search saves a listing. PR #13's `unit_terms` in `scoring.py` already finds the cheapest unit's monthly price, size and bedrooms.
- Whether Zillow's pages expose latitude and longitude was not checked by the research. Read it from the page JSON `profile/skills/search/scripts/sites/zillow.py` already parses, and add it to the facts.
- When a unit label states the level, it overrides extraction's reading from photos and text. Otherwise extraction's reading stands.

## Deviations

- Days since first seen is not stored. It changes every day while the content stays the same, so `ListingStore.code_features(today)` counts it from the stored first-seen date to the given day, which keeps a rerank reproducible for a fixed day.
- Garden covers basement and lower-level labels, because 464925436 is labeled `#Basement`.
- A unit on floor 2 or higher is middle or top only when the facts give the building's height (RESO `StoriesTotal`). Otherwise its floor is stored and its level is unknown. None of the 53 live listings had a building height, so live data has garden, ground and unknown levels only.
- A bare number such as `#2` or `#14` is not read as a floor, because in larger buildings it is a unit number. Labels read as floors: a digit with letters (`2F`, `3N`, `6H`), three digits (`205`, `Unit 420`), `2nd Floor`, and penthouse labels for top.
- The search's facts gained unit bathrooms, label and floor, and the listing's coordinates and building height. A Zillow card's signature now includes the reader's version, so each stored listing is read once more after this change to gain these facts.
- The rule that a label's level overrides extraction's is documented in `code_features.py` for the scoring model to apply, since the extraction record's shape belongs to 0016.

## Result

The code-derived features live in their own table, `code_features`, one row per listing with the content fingerprint it was computed from: monthly price and currency, size, price per square foot, bedrooms, bathrooms, unit label, floor and level, photo count, latitude and longitude. `code_features.py` computes them as a pure function of the stored listing and its site facts. The store recomputes them in the same transaction whenever `upsert_listing` saves changed content or `analyze` saves features, so no model call is involved. `ListingStore.code_features(today)` reads them with days since first seen. Migration 3 creates the table and fills it for every stored listing. It depends on no other migration, so it can be ordered after 0016's when both merge. Ticket 0019 can read coordinates with the fingerprint from the same method.

Zillow pages give coordinates as `latitude` and `longitude` on both home pages (`gdpClientCache` property) and building pages (`gdp.building`), checked on 10 live pages. Home pages also carry RESO `entryLevel` and `storiesTotal`, and floor-plan units on building pages carry `floor`, all null on every page checked; they are read when present (RESO Data Dictionary 2.0, EntryLevel and StoriesTotal).

Verification, on this Windows machine:

- Unit tests: `python -m unittest discover -s tests`, 258 run, 0 failed, 0 skipped. `ruff check profile tests` passes.
- Live: a throwaway store under the temp directory, searched with the real search code and client for Logan Square, Chicago (6-minute budget, 50 listings), plus the three garden units read through the search's own read and save path. All 53 listings have code features and coordinates. 113953556 (`#G`), 2069930270 (`#Gn`) and 464925436 (`#Basement`) read as garden, as do 2069548204, 2106063102 and 466125881 (`#G`). Every listing's monthly price equals its cheapest unit's price, and every listing without a size has no price per square foot.
- Migration: a copy of that store reset to version 2 without the table reopened at version 3 with features identical to the original's.

Tech debt fixed: the migration tests asserted schema version 2 literally and now assert the number of migrations.

Not done here: merge through a reviewed pull request.
