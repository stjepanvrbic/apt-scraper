---
title: Distance to rapid transit and travel time to the user's places
type: Feature
state: In Progress
priority: 3
assignee: agent
milestone:
parent: 0015
blocked_by: [0017, 0018, 0020]
related: []
---

## Outcome

Each listing's feature record has its distance to the nearest rapid-transit stop and its travel time to each place by the user's way of getting there, and the scoring model counts them by the location priorities.

## Acceptance

- [ ] The geocoding and routing provider is chosen by research recorded in `docs/research/`: candidates such as OpenStreetMap's Nominatim, OSRM, OpenRouteService, transit feeds (GTFS) and Google Maps Platform, with their rate limits, terms, cost, keys and transit routing for the user's city, and a recommendation. The owner decides on this ticket before the provider is built in.
- [ ] Each place is geocoded once when saved. A place that cannot be geocoded is reported to the user, who is asked to describe it again.
- [ ] Each listing with coordinates gets its transit distance and its travel time to each place once per content and place, within the provider's limits. A rerank makes no provider call.
- [ ] A listing without coordinates, or whose route fails, has those features as unknown, and unknown does not count for or against it.
- [ ] Spec › Acceptance "Location" passes in a test against the scoring model.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Ranking](../spec.md#ranking) (location), [Spec › Scope](../spec.md#scope) (geocoding and routing service), [Spec › Acceptance](../spec.md#acceptance) (Location)

## Design

- Free OpenStreetMap services have rate limits, and Google needs billing. The research weighs these against the number of listings and places a user has, with the results cached in the store.
- A provider key, if any, is the user's own and lives in the profile's secret store, never in the repository.
- Rapid transit means rail, metro and bus rapid transit stops.
- A travel time beyond a place's longest accepted trip counts against the listing by the place's importance, as Spec › Ranking says.

## Deviations

## Result
