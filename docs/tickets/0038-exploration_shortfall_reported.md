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

- [ ] Spec › Ranking and `docs/ranking.md` say: up to two exploration listings, drawn from the unlabeled listings outside the top 18, fewer when there are fewer candidates.
- [ ] The ranking skill reports the count from the rerank summary instead of a fixed "two"; `ExplorationTests` gains the degenerate case.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Acceptance](../spec.md#acceptance) (Exploration)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found by the deep review: with 21 identical listings and notes on the last three, `explore()` returns no slots and the skill still tells the user two listings are there to learn their taste.
