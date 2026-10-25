---
title: Implicit signals recorded by the gallery
type: Feature
state: In Progress
priority: 4
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: []
---

## Outcome

The gallery records listing opens, how long a listing stays open and which photos are viewed in the listing store, and each rerank reports whether they would raise the favorites' held-out ranks. The scoring model does not use them.

## Acceptance

- [x] Opening a listing, the time its details stay open and visible, and each photo viewed in the full photo set are recorded through an authenticated HTTP route, batched, in a new store table. Time while the browser tab is hidden does not count.
- [x] The gallery's visible behavior does not change, and nothing is sent anywhere but the local server.
- [x] Each rerank computes the leave-one-out check a second time with the signals as weak labels and reports both values. The ranks come from the model without them.
- [x] Tests at the store and HTTP boundaries, and a headless Playwright test that opening a listing and paging its photos records the events.
- [ ] Pushed `feat/implicit_signals` and opened a pull request to `main`, without merging.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) (implicit signals), [Spec › Terms](../spec.md#terms) (Implicit signal)

## Design

Not a UI ticket: instrumentation with no visible change, so the implementer does the frontend part.

Candidate weak labels: a listing kept open well above the median time is a weak positive, and one opened and closed within seconds a weak negative. Joachims' rule, that a clicked result is preferred over the skipped results above it, gives pairs from opens in rank order. Whether the scoring model uses them is decided once reports show they help.

## Deviations

The diagnostic evaluates completed visible dwell only. Opens and photo views are retained without preference inference; rank-order click pairs need evidence of which earlier results were actually seen. With no usable weak labels, the second report reuses the identical baseline check instead of refitting it. There is no zoom control in the current gallery, and the accepted capture contract does not include scroll impressions.

Capture is best effort during browser termination or a server outage: the queue holds 200 events, logs failures to the console and retries the same event identities without duplication. Overflow leaves the affected visit out of the diagnostic. Delivery stops at an open pull request, as requested.

## Result

Schema migration 7 adds `implicit_signals`; authenticated `POST /api/signals` validates and atomically deduplicates batches. Existing Mantine hooks and the lightbox view callback capture opens, visible dwell increments, photo views and closes through the existing fetch layer. Signal writes do not change the gallery revision. Reranks report a separate weak-label leave-one-out result while retaining the ordinary ranks, explanations, exploration and Compare questions.

Nine new Python tests cover storage, migration, authentication, batch validation, diagnostic isolation, held-out leakage and the two-second rerank budget with 300 listings, 50 favorites and 250 weak labels. Four new headless browser tests cover photo paging, hidden time, retries after lost responses, page exit, refreshes and reopened visits. The full 30-test Playwright suite passed, including desktop/mobile accessibility and screenshots. Python full-suite rerun is pending; lint, production build and ruff passed. Independent review and merge remain pending.

The governing requirements are Spec Terms, Ranking and Gallery and the existing Spec Decisions; the repository has no separate ADR register. Adjacent cleanup stabilizes lightbox slides during rerenders and seeds the old-schema migration fixture directly instead of invoking the current reranker on an unmigrated database. No new dependency was added. The owner's installed profiles were not used for verification.
