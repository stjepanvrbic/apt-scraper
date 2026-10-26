---
title: The leave-one-out check refits the held-out favorite as a weak negative
type: Bug
state: Done
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: []
related: [0020, 0025]
---

## Outcome

The rerank's leave-one-out check treats the held-out favorite the way it treats any unlabeled ranked listing, a weak negative, instead of dropping its row, so the check cannot rank a held-out favorite first on no signal and the prior scale and weak-negative weight it tunes are chosen without that bias.

## Acceptance

- [x] The held-out refit labels the favorite as a weak negative, with the same low-rank update as today, and the rerank takes no longer.
- [x] A test shows a model with a free parameter per listing cannot rank the held-out favorite first under the check.
- [x] `docs/ranking.md` describes the check as it now works.
- [x] Pushed `fix/held_out_check` and opened a pull request to `main`, without merging.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Terms](../spec.md#terms) (Leave-one-out check), [Spec › Ranking](../spec.md#ranking)

## Design

- Found by the embedding measurement: on the same 129 listings the current check reports a median held-out rank of 20 and mean 46.2 for the five favorites, against 25 and 48.2 when the held-out favorite is a weak negative. See [the embedding research](../research/embedding_preference_model.md).

## Deviations

The approximate check that tunes the prior scale and weak-negative weight is no longer one Newton step. With the favorite relabeled, one step overshoots, because the full fit is confident of a favorite's score and the relabeled row's curvature there is near zero. On the 300-listing fixture it gave mean held-out ranks of 127 and 134 at prior scale 2 against 105 and 107 converged. On the garden fixture it chose prior scale 1 over 2 and moved the unseen garden unit from rank 6 to 8. A second Newton step came closer but cost about 0.35 s more per rerank and was still 3 to 4 ranks off at scale 2. The refit now takes the one-step drop of the favorite's rows with the Woodbury downdate, as before, then settles the weak-negative row along the downdated Hessian's solve of it by a scalar equation. The low-rank update is the same. The scalar step, and a Sherman-Morrison term for the row's curvature in the converged refit, are rank one on top of it.

`profile/skills/ranking/SKILL.md` and `review.md` described the check as a fit without the favorite. They now say without its labels.

## Result

Code verified at `1188a3a` on `fix/held_out_check`, delivered as [PR #29](https://github.com/stjepanvrbic/apt-scraper/pull/29), open and not merged.

`held_out_observations` gives the held-out favorite's own row label 0 at the weak-negative weight, or weight 0 when the favorite is not ranked, and its pairwise answers weight 0. `held_out_fit` drops the favorite's rows with one Newton step and settles the weak row with `weak_negative_score`. With `converge`, it then iterates to 10⁻⁶ with the downdated Hessian plus the row's curvature. `weak_negative_score` safeguards its Newton steps by bisection, because Newton alone cycles from start 4 at leverage times weight 20.

Held-out ranks before and after, on the test fixtures:

- 60 listings, each with a tag of its own, 9 favorites that nothing explains: ranks 1, 1, 1, 1, 1, 5, 30, 34, 36 (median 1, mean 12.2) before; 29 to 52 (median 38, mean 39.7) after, of 51.
- 40 listings with 5 garden favorites: mean 1.0 before, 1.2 after. The unseen garden unit keeps rank 6, and the tuning keeps prior scale 2 and weight 0.1.
- 300 listings with 50 favorites and no signal: median 71.5 and mean 82.4 before; median 99.5 and mean 104.4 after, of 250.

The approximate check is within 0.3 of the converged mean on every tuning candidate of the garden and 300-listing fixtures. On a 30-listing fixture with 4 favorites it is 0.5 off and picks prior scale 1 where the converged check picks 2. The converged refit takes 522 gradient evaluations on the 300-listing fixture, against 520 before. Rerank CPU time, best of three over four paired rounds on a quiet machine: 1.00 to 1.05 s before, 0.98 to 1.12 s after. With 250 weak labels: 1.36 to 1.44 s before, 1.38 to 1.45 s after.

Tests: `test_a_weight_of_its_own_for_each_listing_cannot_rank_the_held_out_favorite_first` fails on `main` (five ranks of 1, mean 12.2) and passes here. `test_the_held_out_fit_is_the_fit_with_the_favorite_as_a_weak_negative` checks the converged refit against the exact fit within 10⁻⁵. `test_the_approximate_check_that_chooses_the_hyperparameters_agrees_with_the_converged_one` bounds the tuning check's error at 0.4 on the garden fixture. `test_the_weak_negative_score_solves_its_equation_where_newton_steps_alone_cycle` hangs on the unsafeguarded solver and passes here. `python -m unittest discover -s tests`: 485 passed, 0 skipped. `ruff check profile tests` clean. Hermes skill linter: the same 6 `gallery` and 2 `ranking` warnings as `main`. Hermes's context scanner was not run on the edited ranking skill texts: its module needs `ruamel`, which no Hermes environment on this machine provides.

An independent review found an unbounded loop in the first scalar solver, then slow convergence near its cycling onset, a misleading parameter name, a stale docstring, an overstated commit message and the stale skill wording. All are fixed and were reassessed. The repository has no ADR register. The spec's Decisions were checked, and none governs the check's internals. Tech debt fixed: `held_out_fit` and `held_out_scores` lost an unused `inputs` argument. Installed Hermes profiles were not used.

## Comments

- 2026-10-25: [Spec › Terms](../spec.md#terms) defines the Leave-one-out check as "each favorite's rank when the scoring model is fitted without that favorite". The favorite now stays in the fit as a weak negative. Recommendation: reword it to "fitted without that favorite's labels" through `to-spec`, because the current wording reads as the old row-dropping check.
