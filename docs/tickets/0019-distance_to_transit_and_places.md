---
title: Distance to the nearest rapid-transit station and to the user's places
type: Feature
state: Todo
priority: 3
assignee:
milestone:
parent: 0015
blocked_by: [0017, 0018, 0020]
related: []
---

## Outcome

Each listing's feature record has its straight-line distance to the nearest rapid-transit station and to each of the user's places, and the scoring model counts them by the location priorities.

## Acceptance

- [ ] Each place is geocoded once when saved, through Nominatim at its published rate of one request per second, with a User-Agent that names this project. A place that cannot be geocoded is reported to the user, who is asked to describe it again.
- [ ] A place is a name and an address. The interview no longer asks for the way of getting there, the longest trip or the visits per week, and the store no longer keeps them.
- [ ] The rapid-transit stations of the user's city come from its GTFS stops once, filtered to rail, metro and bus rapid transit, and are kept in the store.
- [ ] Each listing with coordinates gets its distance to the nearest station and to each place computed in code from coordinates, with no network call, when its feature record is built or a place changes.
- [ ] A listing without coordinates has those features as unknown, and unknown does not count for or against it.
- [ ] Spec › Acceptance "Location" passes in a test against the scoring model.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Ranking](../spec.md#ranking) (location), [Spec › Acceptance](../spec.md#acceptance) (Location, Interview)

## Design

- Distance is the great-circle distance between coordinates. No routing engine, travel time or departure time.
- Which feed is the city's rapid transit is a setting with Chicago's CTA feed as the default; the research report [Geocoding and routing provider](../research/geocoding_and_routing_provider.md) names the sources.
- Nominatim results and the station list live in the store, so a rerank makes no network call.

## Deviations

## Result

## Comments

- 2026-10-25: The owner chose plain distance over routing. The provider research's routing recommendation (r5py, departure times, Metra) is not used.
