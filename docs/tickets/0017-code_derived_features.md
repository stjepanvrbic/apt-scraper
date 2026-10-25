---
title: Code-derived listing features and coordinates
type: Feature
state: Todo
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: []
related: [0014]
---

## Outcome

Each listing's feature record has the features code reads reliably: monthly price, size, price per square foot, rooms, unit level from unit labels, days since first seen, photo count and coordinates.

## Acceptance

- [ ] Unit level is garden for unit labels such as `#G`, `#G2`, `#Garden` and "Unit Garden", and ground, middle or top where the label or the facts give a floor. The three Chicago garden units PR #13's model missed (Zillow 113953556, 2069930270 and 464925436) read as garden.
- [ ] Monthly price and price per square foot come from the cheapest unit, across rental periods. A listing without a size has no price per square foot, not a guess.
- [ ] Every Zillow listing whose page gives coordinates stores them, and one without has them as unknown.
- [ ] The features are recomputed whenever the listing's content changes, with no model call.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features)

## Design

- Pure functions over the stored listing and the search's `facts`, run when the search saves a listing. PR #13's `unit_terms` in `scoring.py` already finds the cheapest unit's monthly price, size and bedrooms.
- Whether Zillow's pages expose latitude and longitude was not checked by the research. Read it from the page JSON `profile/skills/search/scripts/sites/zillow.py` already parses, and add it to the facts.
- When a unit label states the level, it overrides extraction's reading from photos and text. Otherwise extraction's reading stands.

## Deviations

## Result
