---
title: Score and rerank in the gallery, with exploration marks and review results
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: []
---

## Outcome

Rankings has a Score and rerank button that runs a rerank and shows its leave-one-out check, the exploration listings, the review pass's moves with their reasons and a reverted review, and each listing's explanation.

## Acceptance

- [x] Score and rerank runs a rerank through an authenticated HTTP route and shows the new order. It cannot start a second rerank while one runs, and a failure shows its error.
- [x] After a rerank, Rankings shows each favorite's held-out rank, then the review's state: running, its moves with their reasons, its flags and findings, or the reverted moves with both check values.
- [x] Exploration listings carry a visible mark saying they are shown to learn the user's taste.
- [x] A listing's details show its full explanation: each feature's contribution and the most similar favorite.
- [x] Listings not ranked yet follow the ranked ones with the reason why: new or changed since the last rerank, waiting for features, or extraction failed.
- [x] Headless Playwright tests with screenshots at desktop and mobile sizes, against the production build. No owner browser is opened.
- [x] `docs/gallery.md` updated.
- [x] Published `feat/rerank_screen` with a reviewed pull request to `main`, left unmerged for the owner.

## Spec

[Spec › Gallery](../spec.md#gallery), [Spec › Ranking](../spec.md#ranking), [Spec › Acceptance](../spec.md#acceptance) (Rerank, Exploration, Review pass)

## Design

UI ticket. The HTTP routes and their tests come first, against the rerank and review interfaces. The frontend and its visual design are done later by a separate design-focused agent, in the gallery's stack and visual references (`docs/gallery.md`, "Assets and references").

The button could show how many listings and labels changed since the last rerank, so the user knows when one is worth running. The design agent decides.

## Deviations

- Delivery stops at an open pull request, per the owner's implementation request. The ticket stays In Progress; no merge is authorized.
- No migration is needed. The gallery reads the existing rerank and review records. The merged implicit-signals work uses schema version 9.

## Result

- Delivered in [PR #27](https://github.com/stjepanvrbic/apt-scraper/pull/27) from `feat/rerank_screen` to `main`, left open and unmerged. The ticket remains In Progress as requested.
- Verified implementation `9224a7ad415452d3e478d1f3cfc0595849a81d85`. Merged `main` through `fc130e9`; its final documentation-only changes leave the verified application, tests and governing spec unchanged. The governing decisions are the Ranking and Gallery decisions in `docs/spec.md`; no separate ADR register exists.
- Full Python suite: 488 passed, no failures, errors or skips. Full production headless Playwright suite: 38 passed, no skips, including desktop/mobile accessibility, review transitions, retries, concurrent requests, stale listings, explanations, attribution and implicit signals. Ruff, ESLint, TypeScript, Vite build and whitespace checks pass.
- With 300 listings, 50 favorites and a Work-place distance, click-to-visible order and all held-out ranks measured 1,296 ms; scoring took 1.215 s. Earlier pre-merge runs intermittently exceeded two seconds. The threshold was not relaxed; the final merged suites passed.
- Independent standards and spec reassessments found no blocking findings. The standards suggestions were resolved by explicit current/raw read contracts, removing the unused frontend field, narrowing refresh triggers, simplifying failure handling, and using feature keys for place-distance attribution. Its nonblocking coverage note is covered by `test_pending_reasons_follow_current_features_and_never_expose_an_old_explanation` in `tests/test_rerank_server.py`, which checks the HTTP detail's null rank and explanation after content changes.
- Fixed touched debt: stale explanations no longer appear in current listing reads, pagination arrows have accessible names, and the Compare accessibility check waits for the drawer to finish fading in. Historical ranks remain available in the raw export.
- Six production screenshots are included in the PR and under `docs/images/`: Rankings, reverted review and listing explanations, each at desktop and mobile sizes. Visual inspection confirmed the layout and adjacent OpenStreetMap credit.
- Live Hermes execution, session-usage counters and installed profiles were not verified. Tests used isolated homes and stores; no owner browser was opened and the owner's Hermes profiles and secrets were not accessed. No owner question remains.

## Comments

- 2026-10-25: Show "© OpenStreetMap contributors" where place distances appear, as the Nominatim usage policy asks.
