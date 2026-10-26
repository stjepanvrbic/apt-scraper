---
title: Extraction and scoring share one definition of a labeled listing
type: Bug
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

Whether a listing is labeled (favorite, dismissed, contact request, note, or an endpoint of a pairwise answer) is defined once and used by both the features job and the scoring model, so a compared listing always keeps a usable feature record.

## Acceptance

- [ ] `labeled` lives once, in `scoring.py`, over fields the store's snapshot computes the same way for `ranking_inputs` and the features job; `features.py` uses it for `labeled` and `needs_record`.
- [ ] Test: a paired listing whose content changes while unavailable is extracted, and its pair row stays in the fit.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found by the deep review: `features.py` omits pairwise answers from its labeled set, so a compared listing that becomes unavailable before extraction gets no record and `scoring.py` drops the whole pair.
