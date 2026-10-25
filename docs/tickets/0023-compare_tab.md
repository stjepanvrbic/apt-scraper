---
title: Compare tab with pairwise questions
type: Feature
state: In Progress
priority: 3
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: []
---

## Outcome

The gallery's Compare tab asks pairwise questions on real listings whose order the scoring model is least sure of, each answer is a label at the next rerank, and after the interview the agent asks the user to answer 5 to 8.

## Acceptance

- [x] Compare sits between Favorites and Contacted and shows one question at a time: both listings with their main photo, price, size, rooms and address. The user picks one or skips, and either listing opens to its details.
- [x] Questions pair available listings whose order the posterior is least sure of. An answered pair is not asked again, and a skipped one not within the next 20 questions.
- [ ] Answers are stored and count at the next rerank. Spec › Acceptance "Pairwise questions" passes.
- [ ] On a fresh test profile, once the first listings have feature records after the interview, the agent asks the user through the messaging gateway to answer 5 to 8 questions in Compare.
- [x] Headless Playwright tests with screenshots, against the production build. No owner browser is opened.
- [x] `docs/gallery.md` updated. Open a reviewed pull request to `main` with screenshots; do not merge.

## Spec

[Spec › Gallery](../spec.md#gallery), [Spec › Ranking](../spec.md#ranking) (pairwise questions), [Spec › Acceptance](../spec.md#acceptance) (Pairwise questions), [Spec › Decisions](../spec.md#decisions) (pairwise questions tab)

## Design

UI ticket. Question selection, the answers' store table, the HTTP routes and the agent's cold-start message come first and are tested at the store and HTTP boundaries. The tab's frontend and visual design are done later by a separate design-focused agent, in the gallery's stack and visual references.

A pair's uncertainty is how close the posterior probability that one ranks above the other is to one half. Before any label, the prior from the stated preferences is the posterior.

## Deviations

- Delivery is an open pull request, not a merge, as requested. The ticket stays In Progress.
- A schema migration adds only `pairwise_skips`, needed to preserve the twenty-question cooldown across restarts. Existing answer storage and scoring remain in use.
- Live messaging delivery is not verified. Tests use a fresh temporary profile and a controlled sender at the existing gateway boundary; they do not access the owner's profile, credentials or messaging account. The full Pairwise questions acceptance remains open until a test gateway receives the invitation.

## Result

The store and authenticated HTTP routes save choices, deduplicate answers, reject conflicting labels, and persist skip cooldowns. Tests verify that an HTTP choice leaves ranks unchanged until rerank, then puts the chosen listing above the other. The first-use invitation waits for a complete interview and two eligible feature records. The cron script checks that the gateway is usable and sends the invitation itself, retries failed delivery, and records successful delivery once. A pending invitation neither sends through a disconnected gateway nor wakes the model. The cron prompt and skill no longer instruct the model to send it.

Python verification: 403 tests passed, zero skipped, including disconnected-gateway and send-once invitation regressions; `ruff check profile tests` passed. Hermes's skill linter reports zero findings for `listing-features`. The migration preserves existing ranks and answers. Frontend verification: production build and lint passed; 26 headless Playwright tests passed, zero skipped, including 10 Compare tests. Axe found no violations at desktop, 390px or 320px widths or in either details drawer. Screenshots are in `docs/images/compare-desktop.png` and `docs/images/compare-mobile.png`. Independent standards and requirements reviews of implementation revision `22e3277732c702b0bd865febc9a85598cb232f75` against `abb3790eede83837f1d2b39ad89644403e0fdedc` found no actionable regressions after fixes. The governing Spec decisions (dedicated Compare tab, requested reranks and gateway questions) were checked unchanged at those revisions; they have no separate ADR identifiers. Delivered on `feat/compare_tab` in [PR #21](https://github.com/stjepanvrbic/apt-scraper/pull/21), open against `main`, not merged.

The existing listing card is shared by browsing and Compare. Mantine cards, responsive grids, buttons, alerts and the details drawer supply the UI. A pre-made [image comparison slider](https://gfazioli.github.io/mantine-compare/) and [promotional comparison cards](https://21st.dev/@olewandowski1/components/comparison-2) were evaluated; neither models choosing an apartment while opening its details. Compare keeps the gallery design and hides ranking cues. The drawer now preserves focus when closed, and six tabs fit a 320px screen.

Review fixes centralize listing eligibility across Rankings, question selection and answer validation, put tab listing behavior in its metadata, and cover cooldown advancement by answers as well as skips.

## Comments

2026-10-25: Live invitation delivery still needs an isolated test profile and messaging gateway. Recommended: provide a dedicated test profile so delivery can be checked without accessing the owner's profile or credentials. Alternatively, the owner can run the invitation check before merge and attach its result. No implementation decision is waiting on the owner.
