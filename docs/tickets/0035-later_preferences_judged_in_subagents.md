---
title: Later preference judgments run in extraction subagents, not the job agent
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

No listing text enters the features job agent's conversation. Preferences stated after a listing's extraction are judged in `delegate_task` subagents from task files, like extraction, and saved with `features.py judge`.

## Acceptance

- [ ] `features.py preferences` writes one task file per batch into `extraction/` and the report lists the `delegate_task` calls; the job agent's turn holds no listing text.
- [ ] The `judge` command and attempt bookkeeping are unchanged; `docs/ranking.md` describes the isolation for both reads.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Invariants](../spec.md#invariants)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found by the deep review: `preferences()` prints up to 2,000 characters of each listing's description into the cron agent's turn, and that agent has terminal, file, vision and delegation tools.
