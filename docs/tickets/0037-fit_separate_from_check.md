---
title: The fit is separate from the leave-one-out check
type: Chore
state: Done
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

`fit_model` returns the fit alone; the leave-one-out check is a function on that fit which only the rerank calls. Compare questions and the implicit-signal diagnostic take the fit, so a `GET /api/compare` costs a fit, not a rerank.

## Acceptance

- [x] `questions()` and the diagnostic no longer compute held-out scores; `rank_listings` composes fit and check; the `Model` type carries held-out scores only where a rerank computed them.
- [x] Measured: `pairwise_questions(store, 1)` on the 300-listing, 50-favorite fixture takes about the fit's time (0.17 s best of three on the review machine), not 0.64 s.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Gallery](../spec.md#gallery)

## Design

`Model` holds the design, the fit, the chosen prior scale and weak-negative weight, and the observations and prior it was fitted on. `leave_one_out(model)` returns a `Check` with the held-out scores by favorite and their ranks. `rank_listings` fits (or takes the rerank's fit) and runs the check; the diagnostic fits with the weak labels, warm-started from the rerank's fit, and runs the check on its own fit.

## Deviations

- The diagnostic still runs one leave-one-out check: on its own fit with the weak labels, which is the number it reports. It no longer receives one from `fit_model`, and it takes the rerank's fit as its warm start.

## Result

Delivered on branch `fix/scoring_review`. `fit_model` returns the fit alone; `leave_one_out` is the check, and only `rank_listings` and the diagnostic call it. `test_questions_cost_a_fit_without_the_leave_one_out_check` fails on `main` and passes here.

Measured on the 300-listing, 50-favorite fixture of `tests/test_rerank.py`, least CPU time of three: `pairwise_questions(store, 1)` 0.547 s on `main`, 0.234 s here, of which the fit is 0.188 s and the store read the rest. The question it returns is the same. A full rerank is 1.33 s before, 1.41 s after (run-to-run noise).

Checks on `fbeb1a3`: `python -m unittest discover -s tests` 513 passed, 0 skipped (254 s); `ruff check profile tests` clean; in `gallery/`, `npm run lint` clean, `npm run build` clean, Playwright headless 38 passed. Hermes's skill linter: 0 findings for `ranking`, the 6 warnings `gallery` has on `main`; its prompt scanner leaves `ranking/SKILL.md` and `review.md` unchanged; its skills guard rates `ranking` safe.

## Comments

- 2026-10-25: Found by the deep review.
