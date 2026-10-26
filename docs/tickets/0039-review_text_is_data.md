---
title: Review text is data under the citation discipline, never an instruction
type: Chore
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

Move reasons, flag notes and findings written by the review agent follow the same citation rule as reasons, or are structured, and the ranking skill presents them to the user as the review's data, never as something to act on.

## Acceptance

- [ ] Flags are `(listing, kind, preference)` without free text, or their note follows the citation rule; move reasons and findings use citations from the task's values, tags and column names with connecting words outside; a file with any bad text is rejected whole.
- [ ] `profile/skills/ranking/SKILL.md` tells the agent these texts are data to show, mirroring the task-file wording. Tests cover rejection of uncited text in each field.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Invariants](../spec.md#invariants)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found by the deep review: these fields are 300 characters of free text written after reading listing text, rendered in the gallery and relayed by the main agent, which holds Gmail, Calendar and the gateway.
