---
title: Implicit signals recorded by the gallery
type: Feature
state: In Progress
priority: 4
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0030]
---

## Outcome

The gallery records listing opens, how long a listing stays open and which photos are viewed in the listing store, and each rerank reports whether they would raise the favorites' held-out ranks. The scoring model does not use them.

## Acceptance

- [x] Opening a listing, the time its details stay open and visible, and each photo viewed in the full photo set are recorded through an authenticated HTTP route, batched, in a new store table. Time while the browser tab is hidden does not count.
- [x] The gallery's visible behavior does not change, and nothing is sent anywhere but the local server.
- [x] Each rerank computes the leave-one-out check a second time with the signals as weak labels and reports both values. The ranks come from the model without them.
- [x] Tests at the store and HTTP boundaries, and a headless Playwright test that opening a listing and paging its photos records the events.
- [x] Pushed `feat/implicit_signals` and opened a pull request to `main`, without merging.
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

Schema migration 8 adds `implicit_signals` after the distance-feature migration; authenticated `POST /api/signals` validates and atomically deduplicates batches. Existing Mantine hooks and the lightbox view callback capture opens, visible dwell increments, photo views and closes through the existing fetch layer. Signal writes do not change the gallery revision. Reranks report a separate weak-label leave-one-out result while retaining the ordinary ranks, explanations, exploration and Compare questions.

Ten new Python tests cover storage, migration, authentication, batch validation, diagnostic isolation, held-out leakage and the two-second CPU budget with 300 listings, 50 favorites and 250 weak labels. Four new headless browser tests cover photo paging, hidden time, retries after lost responses, page exit, refreshes and reopened visits. After rebasing onto the distance features, the full Python suite passed 456 tests and the full headless Playwright suite passed 30 tests, including desktop/mobile accessibility and screenshots, with zero skipped tests. Lint, production build and `ruff check profile tests` passed. Independent review and merge remain pending.

The governing requirements are Spec Terms, Ranking and Gallery and the existing Spec Decisions; the repository has no separate ADR register. Adjacent cleanup stabilizes lightbox slides during rerenders. Numerical dot products now use `math.sumprod` to meet the diagnostic timing budget, requiring Python 3.12 or newer outside Hermes; the supplied Hermes runtime is 3.14.9, as the [ranking guide](../ranking.md#implicit-signal-diagnostic) documents. A 40-listing characterization kept ranks, held-out ranks and exploration unchanged, with maximum weight difference 1.39e-17. No new package was added. The owner's installed profiles were not used for verification.

The new signal timing test measures CPU time, following [0030: the rerank timing test fails on a busy machine](0030-rerank_timing_test_under_load.md). The existing timing test is unchanged. Verification uses an isolated temporary home so launcher tests do not access the owner's secrets directory.

Delivered as [PR #23](https://github.com/stjepanvrbic/apt-scraper/pull/23), branch `feat/implicit_signals`. Verified code and test revision: `394f1e11d65d6591ccfb24926e4a04829432be98`. macOS and a connected installed profile were not exercised. Independent review and merge remain pending.
