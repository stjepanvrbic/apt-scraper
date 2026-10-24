---
title: Continuous search of the listing sites
type: Feature
state: Todo
priority: 1
assignee:
milestone:
parent:
blocked_by: [0001, 0002]
related: []
---

## Outcome

Matching listings from every listing site reach the gallery continuously, merged across sites and marked unavailable when gone.

## Acceptance

- [ ] A fixed search finds at least 90% of a manual search of the same listing sites within a day.
- [ ] Each site's highest-placed listings are read first.
- [ ] An unchanged listing re-read costs zero model tokens, checked in the Hermes session usage.
- [ ] A listing shown on two sites appears once with both links.

## Spec

[Spec › Search](../spec.md)

## Design

Scheduled Hermes job. Fetching, parsing, filtering and change detection run as code, and the model only analyzes new or changed listings. Browser or computer use where plain fetching is blocked.

## Deviations

## Result
