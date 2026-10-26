---
title: A shortfall of exploration listings is reported, not silent
type: Bug
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

When fewer than two unlabeled listings sit outside the top 18, the rerank reports how many exploration listings it placed, the agent says that number, and the spec and ranking guide say what the code does.

## Acceptance

- [x] Spec › Ranking and `docs/ranking.md` say: up to two exploration listings, drawn from the unlabeled listings outside the top 18, fewer when there are fewer candidates.
- [x] The ranking skill reports the count from the rerank summary instead of a fixed "two"; `ExplorationTests` gains the degenerate case.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Acceptance](../spec.md#acceptance) (Exploration)

## Design

`explore()` already placed fewer listings when fewer candidates existed; the spec, the ranking guide, the ranking skill and the README claimed two. Exploration listings are never drawn from the top 18.

## Deviations

- Spec › Acceptance (Exploration) also changed: exactly two only with at least two unlabeled listings outside the top 18, and the degenerate case is a scenario of its own. Spec › Decisions now says up to two. The README said two and now says up to two.

## Result

Delivered on branch `fix/scoring_review`. Spec › Ranking, Acceptance and Decisions, `docs/ranking.md` › Exploration and the README say up to two, drawn from the unlabeled listings outside the top 18, fewer when fewer exist. The ranking skill tells the user the number of entries in the rerank summary's `exploration`. `ExplorationTests` gains the degenerate case (21 identical listings, notes on the last three: none placed, order by id) and the one-candidate case (placed at rank 7). Both pass on `main` too: the code was right and the text was wrong.

Checks on `fbeb1a3`: `python -m unittest discover -s tests` 513 passed, 0 skipped (254 s); `ruff check profile tests` clean; in `gallery/`, `npm run lint` clean, `npm run build` clean, Playwright headless 38 passed. Hermes's skill linter: 0 findings for `ranking`, the 6 warnings `gallery` has on `main`; its prompt scanner leaves `ranking/SKILL.md` and `review.md` unchanged; its skills guard rates `ranking` safe.

## Comments

- 2026-10-25: Found by the deep review: with 21 identical listings and notes on the last three, `explore()` returns no slots and the skill still tells the user two listings are there to learn their taste.
