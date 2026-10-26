---
title: The extraction subagents' one-line answers have a checked shape
type: Chore
state: Backlog
priority: 4
assignee:
milestone:
parent:
blocked_by: []
related: [0016, 0035]
---

## Outcome

The one line each extraction or judgment subagent answers to the features job agent is validated to a fixed shape before the job agent reads it, so no free text written after reading a listing reaches the job agent's conversation.

## Acceptance

- [ ] The task files ask for a fixed answer form, such as `saved <id>` or `failed <id>: <reason from a fixed list>`, and the job's instructions tell the agent to treat anything else as a failed task.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Invariants](../spec.md#invariants)

## Design

## Deviations

## Result

## Comments

- 2026-10-26: Found while closing the judgment isolation: tags are validated at the store boundary, the subagents' answer lines are not.
