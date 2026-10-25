---
title: Implicit signals recorded by the gallery
type: Feature
state: Todo
priority: 4
assignee:
milestone:
parent: 0015
blocked_by: [0020]
related: []
---

## Outcome

The gallery records listing opens, how long a listing stays open and which photos are viewed in the listing store, and each rerank reports whether they would raise the favorites' held-out ranks. The scoring model does not use them.

## Acceptance

- [ ] Opening a listing, the time its details stay open and visible, and each photo viewed in the full photo set are recorded through an authenticated HTTP route, batched, in a new store table. Time while the browser tab is hidden does not count.
- [ ] The gallery's visible behavior does not change, and nothing is sent anywhere but the local server.
- [ ] Each rerank computes the leave-one-out check a second time with the signals as weak labels and reports both values. The ranks come from the model without them.
- [ ] Tests at the store and HTTP boundaries, and a headless Playwright test that opening a listing and paging its photos records the events.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) (implicit signals), [Spec › Terms](../spec.md#terms) (Implicit signal)

## Design

Not a UI ticket: instrumentation with no visible change, so the implementer does the frontend part.

Candidate weak labels: a listing kept open well above the median time is a weak positive, and one opened and closed within seconds a weak negative. Joachims' rule, that a clicked result is preferred over the skipped results above it, gives pairs from opens in rank order. Whether the scoring model uses them is decided once reports show they help.

## Deviations

## Result
