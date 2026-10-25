---
title: Recognize garden units in the ranking vocabulary
type: Feature
state: Backlog
priority: 4
assignee:
milestone:
parent:
blocked_by: []
related: [0004]
---

## Outcome

A listing that is a garden unit gets the garden-unit trait in its stored profile whether the listing calls it garden, basement, or marks it only by a unit number such as `#G`.

## Acceptance

- [ ] In the five-favorites experiment on the Chicago listings, every unseen garden unit ranks above the rest of similar price after five garden units are favorited.

## Spec

[Spec › Ranking](../spec.md)

## Design

Merge "garden unit" and "basement unit" into one vocabulary term and tell the model that `#G` and `#Garden` unit numbers mean a garden unit. Existing profiles need one re-analysis.

## Deviations

## Result
