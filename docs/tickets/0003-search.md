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

- [ ] Of the matching listings a manual search finds that were posted more than a day earlier, the agent has found at least 90%.
- [ ] Each site's highest-placed listings are read first.
- [ ] Re-reading an unchanged listing makes no model call, checked in the Hermes session usage.

## Spec

[Spec › Search](../spec.md)

## Design

Scheduled Hermes job. Fetching, parsing, filtering and change detection run as code, and the model only analyzes new or changed listings. Browser or computer use where plain fetching is blocked.

## Deviations

## Result
